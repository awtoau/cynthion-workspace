//! `usbpd` source engine + `fusb302b` PHY on the synchronous shell (#633).
//!
//! - One poll executor: the session future lives in a static slot and is polled from the
//!   main loop with a no-op waker; nothing sleeps, so a waker has nothing to do.
//! - `Bus` is reached through a raw pointer set for the span of one poll.
//! - Raw sends (queries, destructive verbs) run INSIDE the session: `engine.run()` is
//!   dropped at a clean await, the PHY is used directly, then `run()` resumes.
//! - Time is the 1 kHz tick `timer::millis()`.

use core::cell::UnsafeCell;
use core::future::{poll_fn, Future};
use core::mem::{align_of, size_of_val, MaybeUninit};
use core::pin::Pin;
use core::ptr;
use core::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

use embassy_futures::select::{select, Either};
use embassy_time_driver::Driver as TimeDriverTrait;
use embedded_hal_async::i2c::{ErrorType, I2c, Operation};
use fusb302b::{Fusb302b, InterruptPin, Sop};
use usbpd::protocol_layer::message::data::request::PowerSource;
use usbpd::protocol_layer::message::data::source_capabilities::{
    FixedSupply, PowerDataObject, SourceCapabilities,
};
use usbpd::source::device_policy_manager::{
    CapabilityResponse, DevicePolicyManager, DrpDevicePolicyManager, EprDevicePolicyManager,
    SourceDpm,
};
use usbpd::source::policy_engine::Source;
use usbpd::timers::Timer;
use usbpd_traits::{Driver, DriverRxError, DriverTxError};

use crate::bus::{self, Bus, BUS_TARGET_C, LINE_TARGET_INT};
use crate::pd_decode::{self as dec, Frame};
use crate::timer;

pub const LOG: usize = 24;
pub const REPLIES: usize = 4;
/// Frames in flight: header (2) + 7 objects.
const WIRE: usize = 2 + dec::MAX_PAYLOAD;

struct Cell<T>(UnsafeCell<T>);
// SAFETY: one hart; every access is from the main loop (never a handler).
unsafe impl<T> Sync for Cell<T> {}

static BUS: Cell<*mut Bus> = Cell(UnsafeCell::new(ptr::null_mut()));
static WRAPS: Cell<(u32, u32)> = Cell(UnsafeCell::new((0, 0)));

// ---- time ----

struct TimeDriver;

impl TimeDriverTrait for TimeDriver {
    fn now(&self) -> u64 {
        let ms = timer::millis();
        // SAFETY: see `Cell`.
        let (last, high) = unsafe { &mut *WRAPS.0.get() };
        if ms < *last {
            *high += 1;
        }
        *last = ms;
        ((*high as u64) << 32) | ms as u64
    }

    // The main loop re-polls every pass, so a deadline is seen on the next poll.
    fn schedule_wake(&self, _at: u64, _waker: &Waker) {}
}

embassy_time_driver::time_driver_impl!(static DRIVER: TimeDriver = TimeDriver);

pub struct SysTimer;

impl Timer for SysTimer {
    fn after_millis(milliseconds: u64) -> impl Future<Output = ()> {
        embassy_time::Timer::after_millis(milliseconds)
    }
}

// ---- I2C and INT_N ----

pub struct BusI2c;

pub struct I2cError(pub bus::Error);

impl core::fmt::Debug for I2cError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str("i2c")
    }
}

impl embedded_hal_async::i2c::Error for I2cError {
    fn kind(&self) -> embedded_hal_async::i2c::ErrorKind {
        embedded_hal_async::i2c::ErrorKind::Other
    }
}

impl ErrorType for BusI2c {
    type Error = I2cError;
}

fn bus_ref<'a>() -> Option<&'a mut Bus> {
    // SAFETY: set by `Pd::poll` for exactly the span of one poll.
    unsafe { (*BUS.0.get()).as_mut() }
}

impl I2c for BusI2c {
    async fn transaction(
        &mut self,
        address: u8,
        operations: &mut [Operation<'_>],
    ) -> Result<(), Self::Error> {
        let bus = bus_ref().ok_or(I2cError(bus::Error::Nack))?;
        let mut register = None;
        for operation in operations {
            match operation {
                Operation::Write(data) => match data.split_first() {
                    Some((&reg, [])) => register = Some(reg),
                    Some((&reg, rest)) => bus
                        .write_registers(BUS_TARGET_C, address, reg, rest)
                        .map_err(I2cError)?,
                    None => {}
                },
                Operation::Read(data) => {
                    let reg = register.take().unwrap_or(0);
                    bus.read_registers(BUS_TARGET_C, address, reg, data)
                        .map_err(I2cError)?;
                }
            }
        }
        Ok(())
    }
}

/// Ready while INT_N is low; otherwise pending until the next main-loop poll.
pub struct IntPin;

impl InterruptPin for IntPin {
    async fn wait_for_interrupt(&mut self) {
        poll_fn(|_| match bus_ref() {
            Some(bus) if bus.lines() & (1 << LINE_TARGET_INT) != 0 => Poll::Ready(()),
            _ => Poll::Pending,
        })
        .await
    }
}

type Phy = Fusb302b<BusI2c, I2cError, (), IntPin>;

// ---- shared state ----

#[derive(Clone, Copy)]
pub struct Entry {
    pub at: u32,
    /// `b'<'` received, `b'>'` sent.
    pub dir: u8,
    pub frame: Frame,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Op {
    /// One message, collect replies until the timer.
    Send,
    SoftReset,
    HardReset,
    DataReset,
    DrSwap,
    PrSwap,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// At least one reply frame.
    Reply,
    /// Nothing arrived before the named timer.
    NoReply(&'static str),
    TxFailed(&'static str),
    HardResetRx,
    /// Engine not in Ready (no contract, mid-negotiation, or traffic in flight).
    NotReady,
    /// Sent and acknowledged; the verb expects no reply (Ping, Alert, ...).
    Sent,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum RawState {
    Idle,
    Pending,
    Running,
    Done,
}

pub struct Raw {
    state: RawState,
    pub op: Op,
    pub sop: u8,
    pub frame: Frame,
    pub timeout_ms: u32,
    pub timer_name: &'static str,
    /// The verb expects no reply: success is the PHY's GoodCRC.
    pub no_reply: bool,
    pub want: usize,
    pub outcome: Outcome,
    pub started: u32,
    pub tx_ms: u32,
    pub sent: Frame,
    pub replies: [Frame; REPLIES],
    pub reply_ms: [u32; REPLIES],
    pub n_replies: usize,
    pub note: &'static str,
}

struct Core {
    phy: Option<Phy>,
    milliamps: u16,
    wire_id: u8,
    rev: u8,
    ufp: bool,
    ready: bool,
    last_frame_ms: u32,
    contract: Option<(u8, u16)>,
    log: [Entry; LOG],
    logged: u32,
    sent: u32,
    received: u32,
    send_errors: u32,
    vbus_cmd: u8,
    hard_resets: u32,
    restart: bool,
    raw: Raw,
}

const VBUS_NONE: u8 = 0;
const VBUS_OFF: u8 = 1;
const VBUS_ON: u8 = 2;

const EMPTY_ENTRY: Entry = Entry { at: 0, dir: 0, frame: Frame::EMPTY };

static CORE: Cell<Core> = Cell(UnsafeCell::new(Core {
    phy: None,
    milliamps: 0,
    wire_id: 0,
    rev: 0,
    ufp: false,
    ready: false,
    last_frame_ms: 0,
    contract: None,
    log: [EMPTY_ENTRY; LOG],
    logged: 0,
    sent: 0,
    received: 0,
    send_errors: 0,
    vbus_cmd: VBUS_NONE,
    hard_resets: 0,
    restart: false,
    raw: Raw {
        state: RawState::Idle,
        op: Op::Send,
        sop: dec::SOP,
        frame: Frame::EMPTY,
        timeout_ms: 0,
        timer_name: "",
        no_reply: false,
        want: 0,
        outcome: Outcome::Reply,
        started: 0,
        tx_ms: 0,
        sent: Frame::EMPTY,
        replies: [Frame::EMPTY; REPLIES],
        reply_ms: [0; REPLIES],
        n_replies: 0,
        note: "",
    },
}));

fn core<'a>() -> &'a mut Core {
    // SAFETY: see `Cell`. Borrows are short and never held across an await that
    // another borrow could overlap: the PE and the raw path never run together.
    unsafe { &mut *CORE.0.get() }
}

fn phy<'a>() -> &'a mut Phy {
    core().phy.as_mut().expect("phy attached before the engine runs")
}

fn log_frame(dir: u8, frame: Frame) {
    let c = core();
    let now = timer::millis();
    c.last_frame_ms = now;
    c.log[c.logged as usize % LOG] = Entry { at: now, dir, frame };
    c.logged = c.logged.wrapping_add(1);
    if dir == b'<' {
        c.received += 1;
        c.rev = frame.rev().clamp(1, 2);
        // A Soft_Reset restarts both message-ID counters.
        if frame.is(13) {
            c.wire_id = 0;
        }
    }
}

fn sop_kind(sop: Sop) -> u8 {
    match sop {
        Sop::Sop => dec::SOP,
        Sop::SopPrime => dec::SOP_PRIME,
        Sop::SopDoublePrime => dec::SOP_DOUBLE_PRIME,
    }
}

// ---- the PHY as the engine sees it ----

/// Logs every frame, rewrites the engine's MessageID onto the shared wire counter.
pub struct Shared;

impl Driver for Shared {
    const HAS_AUTO_GOOD_CRC: bool = true;
    const HAS_AUTO_RETRY: bool = true;

    async fn wait_for_vbus(&mut self) {
        phy().wait_for_vbus().await
    }

    async fn receive(&mut self, buffer: &mut [u8]) -> Result<usize, DriverRxError> {
        loop {
            let (length, sop) = match phy().receive_sop(buffer).await {
                Ok(frame) => frame,
                Err(DriverRxError::HardReset) => {
                    core().wire_id = 0;
                    return Err(DriverRxError::HardReset);
                }
                Err(error) => return Err(error),
            };
            if let Some(frame) = Frame::from_wire(sop_kind(sop), &buffer[..length]) {
                log_frame(b'<', frame);
            }
            core().ready = false;
            if sop == Sop::Sop {
                return Ok(length);
            }
        }
    }

    async fn transmit(&mut self, data: &[u8]) -> Result<(), DriverTxError> {
        let mut wire = [0u8; WIRE];
        let n = data.len().min(WIRE);
        wire[..n].copy_from_slice(&data[..n]);
        let c = core();
        // Soft_Reset (control type 13, no objects) restarts the counter.
        if n >= 2 && wire[0] & 0x1f == 13 && wire[1] >> 4 & 7 == 0 && wire[1] & 0x80 == 0 {
            c.wire_id = 0;
        }
        if n >= 2 {
            wire[1] = (wire[1] & !0x0e) | (c.wire_id << 1);
        }
        if let Some(frame) = Frame::from_wire(dec::SOP, &wire[..n]) {
            log_frame(b'>', frame);
        }
        match phy().transmit_sop(Sop::Sop, &wire[..n]).await {
            Ok(()) => {
                let c = core();
                c.sent += 1;
                c.wire_id = (c.wire_id + 1) & 7;
                Ok(())
            }
            Err(error) => {
                core().send_errors += 1;
                Err(error)
            }
        }
    }

    async fn transmit_hard_reset(&mut self) -> Result<(), DriverTxError> {
        core().wire_id = 0;
        phy().transmit_hard_reset().await
    }
}

// ---- policy ----

pub struct Dpm;

fn capabilities(milliamps: u16) -> SourceCapabilities {
    let mut pdos = heapless9::Vec::new();
    let supply = FixedSupply::v_safe_5v(milliamps / 10).with_usb_communications_capable(true);
    let _ = pdos.push(PowerDataObject::FixedSupply(supply));
    SourceCapabilities::new_with_pdos(pdos)
}

async fn vbus_command(command: u8) {
    core().vbus_cmd = command;
    poll_fn(|_| {
        if core().vbus_cmd == VBUS_NONE {
            Poll::Ready(())
        } else {
            Poll::Pending
        }
    })
    .await
}

impl DevicePolicyManager for Dpm {
    fn source_capabilities(&mut self) -> SourceCapabilities {
        core().ready = false;
        capabilities(core().milliamps)
    }

    async fn evaluate_request(&mut self, request: &PowerSource) -> CapabilityResponse {
        core().ready = false;
        match request {
            PowerSource::FixedVariableSupply(rdo)
                if rdo.object_position() == 1
                    && rdo.raw_operating_current() <= core().milliamps / 10 =>
            {
                core().contract = Some((1, rdo.raw_operating_current() * 10));
                CapabilityResponse::Accept
            }
            _ => CapabilityResponse::Reject,
        }
    }

    // vSafe5V is the only supply: a contract changes no rail. PS_RDY still waits
    // tSrcTransition (25-35 ms, Table 6-68); 30 ms as the midpoint.
    async fn transition_power(&mut self, _request: &PowerSource) -> Result<(), ()> {
        embassy_time::Timer::after_millis(30).await;
        Ok(())
    }

    // Spec 7.1.5: VBUS to vSafe0V, tSrcRecover (0.66-1.0 s), back to vSafe5V.
    async fn hard_reset(&mut self) -> Result<(), ()> {
        let c = core();
        c.ready = false;
        c.contract = None;
        c.wire_id = 0;
        c.hard_resets += 1;
        vbus_command(VBUS_OFF).await;
        embassy_time::Timer::after_millis(800).await;
        vbus_command(VBUS_ON).await;
        Ok(())
    }

    // The Ready state awaits this; reaching it means the engine is idle in Ready.
    async fn get_event(&mut self) -> usbpd::source::device_policy_manager::Event {
        core().ready = true;
        core::future::pending().await
    }
}
impl EprDevicePolicyManager for Dpm {}
impl DrpDevicePolicyManager for Dpm {}
impl SourceDpm for Dpm {}

type Engine = Source<Shared, SysTimer, Dpm>;

// ---- raw exchanges ----

/// Quiet window before a raw send, ms: the engine is mid-exchange if a frame moved
/// within it (tSenderResponse 30 ms, spec Table 6-68).
const QUIET_MS: u32 = 35;

fn raw_pending() -> impl Future<Output = ()> {
    poll_fn(|_| {
        let c = core();
        if c.raw.state != RawState::Pending {
            return Poll::Pending;
        }
        let quiet = timer::millis().wrapping_sub(c.last_frame_ms) > QUIET_MS;
        if c.ready && quiet {
            Poll::Ready(())
        } else {
            c.raw.outcome = Outcome::NotReady;
            c.raw.state = RawState::Done;
            Poll::Pending
        }
    })
}

async fn send_frame(frame: &Frame, sop: u8) -> Result<(), DriverTxError> {
    let mut wire = [0u8; WIRE];
    let n = frame.to_wire(&mut wire);
    let kind = match sop {
        dec::SOP_PRIME => Sop::SopPrime,
        dec::SOP_DOUBLE_PRIME => Sop::SopDoublePrime,
        _ => Sop::Sop,
    };
    let mut logged = *frame;
    logged.sop = sop;
    log_frame(b'>', logged);
    core().raw.sent = logged;
    let result = phy().transmit_sop(kind, &wire[..n]).await;
    match result {
        Ok(()) => {
            core().sent += 1;
            Ok(())
        }
        Err(error) => {
            core().send_errors += 1;
            Err(error)
        }
    }
}

fn next_header(c: &Core, template: u16) -> u16 {
    // Template carries type, extended bit, object count; roles, rev and ID are ours.
    let mut h = template & !((7 << 9) | (3 << 6) | (1 << 5) | (1 << 8));
    h |= (c.wire_id as u16) << 9 | (c.rev.max(1) as u16) << 6 | 1 << 8;
    if !c.ufp {
        h |= 1 << 5;
    }
    h
}

/// Collect replies until `deadline`, or until `want` have arrived.
async fn collect(deadline: u32, want: usize) {
    let mut buffer = [0u8; WIRE];
    loop {
        if core().raw.n_replies >= want {
            return;
        }
        match phy().receive_sop(&mut buffer).await {
            Ok((length, sop)) => {
                if let Some(frame) = Frame::from_wire(sop_kind(sop), &buffer[..length]) {
                    log_frame(b'<', frame);
                    let c = core();
                    if c.raw.n_replies < REPLIES {
                        let n = c.raw.n_replies;
                        c.raw.replies[n] = frame;
                        c.raw.reply_ms[n] = timer::millis().wrapping_sub(c.raw.started);
                        c.raw.n_replies += 1;
                    }
                }
            }
            Err(DriverRxError::HardReset) => {
                core().raw.outcome = Outcome::HardResetRx;
                return;
            }
            Err(DriverRxError::Discarded) => {}
        }
        if (timer::millis().wrapping_sub(deadline) as i32) >= 0 {
            return;
        }
    }
}

/// Send one message and gather its replies. Returns false on a transmit failure.
async fn exchange(frame: Frame, sop: u8, timeout_ms: u32, want: usize) -> bool {
    let c = core();
    let mut f = frame;
    f.header = next_header(c, frame.header);
    c.raw.started = timer::millis();
    let result = send_frame(&f, sop).await;
    let c = core();
    c.raw.tx_ms = timer::millis().wrapping_sub(c.raw.started);
    match result {
        Ok(()) => {
            c.wire_id = (c.wire_id + 1) & 7;
            if c.raw.no_reply {
                return true;
            }
            let deadline = timer::millis().wrapping_add(timeout_ms);
            collect(deadline, want).await;
            true
        }
        Err(DriverTxError::HardReset) => {
            c.raw.outcome = Outcome::HardResetRx;
            false
        }
        Err(DriverTxError::Discarded) => {
            c.raw.outcome = Outcome::TxFailed("no GoodCRC after 2 retries (nRetryCount)");
            false
        }
    }
}

fn control(kind: u8) -> Frame {
    let mut f = Frame::EMPTY;
    f.header = kind as u16;
    f
}

fn last_reply_is(kind: u8) -> bool {
    let r = &core().raw;
    r.n_replies > 0 && r.replies[r.n_replies - 1].is(kind)
}

/// A second message in the same exchange; its replies append to the first's.
async fn exchange_more(frame: Frame, sop: u8, timeout_ms: u32) {
    let c = core();
    let mut f = frame;
    f.header = next_header(c, frame.header);
    if send_frame(&f, sop).await.is_ok() {
        core().wire_id = (core().wire_id + 1) & 7;
        let want = core().raw.n_replies + 1;
        collect(timer::millis().wrapping_add(timeout_ms), want).await;
    }
}

async fn hard_reset_now(keep_outcome: bool) {
    core().contract = None;
    let ok = phy().transmit_hard_reset().await.is_ok();
    let c = core();
    c.wire_id = 0;
    c.hard_resets += 1;
    if !keep_outcome {
        c.raw.outcome = if ok { Outcome::Sent } else { Outcome::TxFailed("hard reset signalling") };
    }
    if ok {
        vbus_command(VBUS_OFF).await;
        embassy_time::Timer::after_millis(800).await;
        vbus_command(VBUS_ON).await;
    }
    core().restart = true;
}

fn finish_reply() {
    let r = &mut core().raw;
    if r.outcome == Outcome::HardResetRx || matches!(r.outcome, Outcome::TxFailed(_)) {
        return;
    }
    r.outcome = if r.n_replies > 0 {
        Outcome::Reply
    } else if r.no_reply {
        Outcome::Sent
    } else {
        Outcome::NoReply(r.timer_name)
    };
}

/// Request chunks 1.. of a chunked extended reply (6.2.1.2.1), 40 ms each
/// (tChunkSenderResponse 24-30 ms, x1.25 + one poll).
async fn chunks() {
    loop {
        let (kind, next, more) = {
            let r = &core().raw;
            if r.n_replies == 0 || r.n_replies >= REPLIES {
                return;
            }
            let last = &r.replies[r.n_replies - 1];
            let Some((true, number, false, size)) = last.ext_header() else {
                return;
            };
            let carried = (number as u16 + 1) * 26;
            (last.kind(), number + 1, size > carried)
        };
        if !more {
            return;
        }
        let mut f = Frame::EMPTY;
        f.header = kind as u16 | 0x8000 | (1 << 12);
        // Extended header: chunked, chunk number, request-chunk, size 0; two pad bytes.
        let ext = 0x8000u16 | ((next as u16) << 11) | 0x400;
        f.payload[..2].copy_from_slice(&ext.to_le_bytes());
        f.len = 4;
        let want = core().raw.n_replies + 1;
        f.header = next_header(core(), f.header);
        if send_frame(&f, dec::SOP).await.is_err() {
            return;
        }
        core().wire_id = (core().wire_id + 1) & 7;
        collect(timer::millis().wrapping_add(40), want).await;
        if core().raw.n_replies < want {
            return;
        }
    }
}

async fn execute() {
    let (op, frame, sop, timeout_ms, want) = {
        let r = &mut core().raw;
        r.state = RawState::Running;
        r.n_replies = 0;
        r.outcome = Outcome::Reply;
        r.note = "";
        (r.op, r.frame, r.sop, r.timeout_ms, r.want)
    };
    match op {
        Op::Send => {
            if exchange(frame, sop, timeout_ms, want).await {
                chunks().await;
                finish_reply();
            }
        }
        Op::SoftReset => {
            if exchange(control(13), sop, timeout_ms, 1).await {
                if last_reply_is(dec::ACCEPT) {
                    core().wire_id = 0;
                    core().restart = true;
                    core().raw.note = "engine restarted: Source_Capabilities follow";
                }
                finish_reply();
            }
        }
        Op::DataReset => {
            if exchange(control(14), sop, timeout_ms, 1).await {
                finish_reply();
                if last_reply_is(dec::ACCEPT) {
                    // Spec 6.3.14: the DFP follows with Data_Reset_Complete.
                    exchange_more(control(15), sop, 40).await;
                    core().raw.note = "Accept; Data_Reset_Complete sent";
                }
            }
        }
        Op::DrSwap => {
            if exchange(control(9), sop, timeout_ms, 1).await {
                finish_reply();
                if last_reply_is(dec::ACCEPT) {
                    core().raw.note = "Accept: roles not followed, hard reset to restore";
                    hard_reset_now(true).await;
                }
            }
        }
        Op::PrSwap => {
            if exchange(control(10), sop, timeout_ms, 1).await {
                finish_reply();
                if last_reply_is(dec::ACCEPT) {
                    core().raw.note = "Accept: not honoured (no sink path), hard reset to recover";
                    hard_reset_now(true).await;
                }
            }
        }
        Op::HardReset => hard_reset_now(false).await,
    }
    let r = &mut core().raw;
    if r.state == RawState::Running {
        r.state = RawState::Done;
    }
}

// ---- session ----

async fn session(milliamps: u16) -> &'static str {
    core().milliamps = milliamps;
    if core().phy.is_none() {
        match Fusb302b::attach_configured(BusI2c, IntPin).await {
            Ok(phy) => core().phy = Some(phy),
            Err(_) => return "phy attach failed",
        }
    }
    loop {
        core().restart = false;
        core().ready = false;
        let mut engine: Engine = Source::new(Shared, Dpm, false);
        loop {
            match select(engine.run(), raw_pending()).await {
                Either::First(Ok(_)) => return "engine exited",
                Either::First(Err(usbpd::source::policy_engine::Error::PortPartnerUnresponsive)) => {
                    return "port partner unresponsive"
                }
                Either::First(Err(_)) => return "engine error",
                Either::Second(()) => {
                    execute().await;
                    if core().restart {
                        break;
                    }
                }
            }
        }
    }
}

const SLOT: usize = 3584;

#[repr(align(8))]
struct Slot(UnsafeCell<MaybeUninit<[u8; SLOT]>>);
// SAFETY: see `Cell`.
unsafe impl Sync for Slot {}
static SLOT_BYTES: Slot = Slot(UnsafeCell::new(MaybeUninit::uninit()));

fn noop_waker() -> Waker {
    fn clone(_: *const ()) -> RawWaker {
        RawWaker::new(ptr::null(), &VTABLE)
    }
    fn nop(_: *const ()) {}
    static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, nop, nop, nop);
    // SAFETY: the vtable functions do nothing.
    unsafe { Waker::from_raw(RawWaker::new(ptr::null(), &VTABLE)) }
}

type Held = *mut dyn Future<Output = &'static str>;

pub struct Pd {
    future: Option<SendHeld>,
    /// Why the last session ended.
    pub ended: &'static str,
}

struct SendHeld(Held);
// SAFETY: one hart; only the main loop (inside `Devices`) touches it.
unsafe impl Send for SendHeld {}

impl Pd {
    pub const fn new() -> Pd {
        Pd { future: None, ended: "" }
    }

    pub fn running(&self) -> bool {
        self.future.is_some()
    }

    /// Start a session offering `milliamps` at 5 V. Returns the future's size.
    pub fn start(&mut self, milliamps: u16) -> usize {
        let future = session(milliamps);
        const { assert!(align_of::<usize>() <= 8) };
        let used = size_of_val(&future);
        assert!(used <= SLOT, "session future exceeds SLOT");
        self.future = None;
        let c = core();
        c.phy = None;
        c.contract = None;
        c.wire_id = 0;
        c.vbus_cmd = VBUS_NONE;
        c.raw.state = RawState::Idle;
        // SAFETY: the slot is only written here, after the previous occupant was dropped.
        let place = unsafe {
            let at = SLOT_BYTES.0.get() as *mut _;
            ptr::write(at, future);
            at as Held
        };
        self.future = Some(SendHeld(place));
        used
    }

    pub fn stop(&mut self) {
        self.future = None;
        let c = core();
        c.phy = None;
        c.ready = false;
        c.contract = None;
    }

    /// Poll the session once. A finished session leaves its reason in `ended`.
    pub fn poll(&mut self, bus: &mut Bus) {
        let Some(SendHeld(place)) = self.future.as_ref() else {
            return;
        };
        // SAFETY: points into SLOT_BYTES, written by `start` and dropped only by
        // `stop`/`start`/completion, never moved.
        let mut future = unsafe { Pin::new_unchecked(&mut **place) };
        // SAFETY: cleared again before returning.
        unsafe { *BUS.0.get() = bus };
        let waker = noop_waker();
        let result = future.as_mut().poll(&mut Context::from_waker(&waker));
        // SAFETY: as above.
        unsafe { *BUS.0.get() = ptr::null_mut() };
        if let Poll::Ready(reason) = result {
            self.ended = reason;
            self.future = None;
        }
    }

    /// A VBUS action the engine asked for: `Some(false)` off, `Some(true)` on.
    pub fn take_vbus(&mut self) -> Option<bool> {
        match core().vbus_cmd {
            VBUS_OFF => Some(false),
            VBUS_ON => Some(true),
            _ => None,
        }
    }

    pub fn vbus_done(&mut self) {
        core().vbus_cmd = VBUS_NONE;
    }

    pub fn contract(&self) -> Option<(u8, u16)> {
        core().contract
    }
    pub fn ready(&self) -> bool {
        core().ready
    }
    /// `(sent, received, send errors, hard resets)`
    pub fn counters(&self) -> (u32, u32, u32, u32) {
        let c = core();
        (c.sent, c.received, c.send_errors, c.hard_resets)
    }
    pub fn logged(&self) -> u32 {
        core().logged
    }
    pub fn entry(&self, n: u32) -> Entry {
        core().log[n as usize % LOG]
    }
    pub fn caps_pdo(&self) -> u32 {
        dec::fixed_pdo(5000, core().milliamps as u32, dec::PDO_USB_COMM)
    }

    /// Post a raw exchange; the session picks it up on a later poll.
    pub fn post(
        &mut self,
        op: Op,
        sop: u8,
        frame: Frame,
        timeout_ms: u32,
        timer_name: &'static str,
        no_reply: bool,
        want: usize,
    ) -> bool {
        let r = &mut core().raw;
        if r.state == RawState::Pending || r.state == RawState::Running {
            return false;
        }
        r.op = op;
        r.sop = sop;
        r.frame = frame;
        r.timeout_ms = timeout_ms;
        r.timer_name = timer_name;
        r.no_reply = no_reply;
        r.want = want;
        r.n_replies = 0;
        r.outcome = Outcome::NotReady;
        r.state = RawState::Pending;
        true
    }

    /// The finished exchange, once.
    pub fn take_result(&mut self) -> Option<&'static Raw> {
        let r = &mut core().raw;
        if r.state == RawState::Done {
            r.state = RawState::Idle;
            Some(&*r)
        } else {
            None
        }
    }
}
