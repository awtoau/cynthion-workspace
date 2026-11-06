//! Driver for the `usb_host` window: role, bus reset, control and bulk transfers,
//! enumeration and the AOA handshake. The shell (`shell/usb.rs`) is syntax only.
//!
//! - Register map: `docs/chips/usb3343-ulpi-phy.md#role-mux-and-byte-stream-on-target_phy-usb_host-window`.
//! - Sequence proven in simulation only: `scripts/soc/usb_host_shim_sim.py` group `aoa`.
//! - Every wait is `wait_until` or `stage`: a limit, an op name, elapsed on expiry.
//! - No engine (QEMU, `target::BOARD` = None): every call returns `Error::NoEngine`.

use core::cell::Cell;
use core::fmt;
use core::ptr::{read_volatile, write_volatile};

use crate::clock;
use crate::usb_desc;

const ROLE: usize = 0x00;
const CTRL: usize = 0x01;
const ROLE_STATUS: usize = 0x04;
const LINK: usize = 0x05;
const EVENTS: usize = 0x06;
const ADDR: usize = 0x08;
const EP: usize = 0x09;
const RXPID: usize = 0x07;
const RESP: usize = 0x0a;
const RXLEN_LO: usize = 0x0c;
const RXLEN_HI: usize = 0x0d;
const RX_DATA: usize = 0x10;
const RX_TAG: usize = 0x11;
const RX_LEVEL: usize = 0x12;
const TX_DATA: usize = 0x14;
const TX_LAST: usize = 0x15;
const TX_LEVEL: usize = 0x17;
const TR_CTRL: usize = 0x18;
const TR_STATE: usize = 0x19;
const TR_CNT_LO: usize = 0x1a;
const TR_CNT_HI: usize = 0x1b;
const TR_IDX_LO: usize = 0x1c;
const TR_IDX_HI: usize = 0x1d;
const TR_SEL: usize = 0x1e;
const TR_DATA: usize = 0x1f;

const CTRL_BUS_RESET: u8 = 1 << 0;
const CTRL_START: u8 = 1 << 1;

const LINK_RESET_ACTIVE: u8 = 1 << 5;
const LINK_IDLE: u8 = 1 << 6;
const LINK_RESET_DONE: u8 = 1 << 7;

const EV_DONE: u8 = 1 << 0;
const EV_TX_OVERFLOW: u8 = 1 << 3;
const EV_BUSY_DROP: u8 = 1 << 6;
const EV_TIMEOUT: u8 = 1 << 7;

pub const ROLE_OFF: u8 = 0;
pub const ROLE_REGS: u8 = 1;
pub const ROLE_HOST: u8 = 2;
pub const ROLE_RAW: u8 = 3;

const TY_SETUP: u8 = 0;
const TY_IN: u8 = 1;
const TY_OUT: u8 = 2;

const RESP_ACK: u8 = 1;
const RESP_NAK: u8 = 2;
const RESP_STALL: u8 = 3;

/// DATA0 / DATA1 PID bytes, as `RXPID` reads them.
const DATA_PID: [u8; 2] = [0xc3, 0x4b];

/// Shim `SWITCH_LIMIT` = 1.25 x (PHY_PAD_RESET + PHY_PREP) = 90_160 usb cycles = 1.503 ms; x1.25.
/// Expiry: `Timeout("role switch")`.
const ROLE_US: u32 = 1_880;
/// Bus reset waits for the reset controller's DISCONNECTED state to see J again: a phone in
/// accessory mode drops off the bus and re-attaches (0.1 s to over 1 s seen, #571), so the
/// bound is the re-attach bound, `ATTACH_US`. Expiry: `Timeout("bus reset")`.
const ENTER_RESET_US: u32 = ATTACH_US;
const RESET_US: u32 = ATTACH_US;
/// `reset_done` clears ~6 sync cycles (0.1 us) after the strobe; 2 us covers the read quantum.
const RESET_CLEAR_US: u32 = 2;
/// Root-port reset recovery, TRSTRCY (USB 2.0 7.1.7.5). A fixed delay, not a bound.
const RESET_RECOVERY_MS: u32 = 10;
/// SetAddress recovery (USB 2.0 9.2.6.3). A fixed delay, not a bound.
const ADDR_RECOVERY_MS: u32 = 2;
/// One transfer: shim `XFER_LIMIT` 76_000 usb cycles = 1.267 ms; x1.25.
const XFER_US: u32 = 1_600;
/// Control request without data stage / status stage: 50 ms (USB 2.0 9.2.6.4).
const SETUP_US: u32 = 50_000;
const STATUS_US: u32 = 50_000;
/// Control data stage, per packet: 500 ms (USB 2.0 9.2.6.4).
const DATA_US: u32 = 500_000;
/// Bulk from the console: no spec bound; 100 ms is the longest a typed command may sit.
pub const BULK_US: u32 = 100_000;
/// Whole pump/echo: echo moves 2 x 1 MiB at the 0.5 MB/s floor (#565) = 4.2 s; x1.25 = 5.2 s.
/// Expiry: `Timeout` naming op, limit, elapsed.
const PUMP_US: u32 = 5_200_000;
pub const PUMP_MAX: u32 = 1 << 20;
/// Echo transfer size: the bench app reads 16384-byte buffers (#565); a read ends on a full one.
const ECHO_CHUNK: u32 = 16384;
/// RX FIFO refill is ~0.1 us per byte (4 usb cycles of CDC); 2 us covers one read.
const FIFO_US: u32 = 2;
/// Most bytes one transaction can leave: SIE FIFO 512 + shim RX FIFO 128.
/// Reached: stop draining (a FIFO that never empties is not ours to wait on).
const RX_DRAIN_MAX: u16 = 640;
/// Error retries per transaction: xHCI endpoint context CERR = 3.
const TRIES: u8 = 3;
/// Phone re-enumerating after START: 4.0 s and over 5 s on the Pixel 3 XL
/// (`scripts/tools/aoa_probe.py`); 8 s is ~1.25x a 6.5 s worst case.
const ATTACH_US: u32 = 8_000_000;
/// Gap between re-attach polls: the probe's 100 ms (a bus reset alone is 50 ms).
const ATTACH_GAP_MS: u32 = 100;

const REQ_GET_DESCRIPTOR: u8 = 6;
const REQ_SET_ADDRESS: u8 = 5;
const REQ_SET_CONFIGURATION: u8 = 9;
const REQ_CLEAR_FEATURE: u8 = 1;
const AOA_GET_PROTOCOL: u8 = 51;
const AOA_SEND_STRING: u8 = 52;
const AOA_START: u8 = 53;

/// Address given to whatever enumerates (the bus is reset between phone and accessory).
const DEVICE_ADDR: u8 = 1;
pub const GOOGLE_VID: u16 = 0x18d1;
pub const AOA_PIDS: [u16; 2] = [0x2d00, 0x2d01];

/// AOA identity: manufacturer, model, description, version, URI, serial.
/// The DJI RC's, matching awto-dji `accessory_filter.xml` and `aoa_probe.py`.
pub const AOA_DJI: [&[u8]; 6] =
    [b"DJI", b"T600", b"DJI", b"v1.2.3.4", b"www.dji.com", b"12345678"];

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Error {
    NoEngine,
    Timeout { op: &'static str, limit_us: u32, elapsed_us: u32 },
    /// Role switch refused or timed out in the shim (`ROLE_STATUS.timeout`).
    RoleSwitch,
    NotHost,
    NotRaw,
    /// Link busy or in reset when a transfer was due.
    NotIdle,
    /// The shim dropped or aborted the transfer; the `EVENTS` byte.
    Dropped(u8),
    Stall,
    /// A handshake other than ACK/NAK/STALL; the `RESP` code.
    Resp(u8),
    /// Unusable descriptor, or a string/packet too long.
    Bad(&'static str),
    /// Device answered 51 with protocol 0.
    NoAoa,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {
            Error::NoEngine => f.write_str("no host engine on this target"),
            Error::Timeout { op, limit_us, elapsed_us } => {
                write!(f, "timeout: {} limit {} us elapsed {} us", op, limit_us, elapsed_us)
            }
            Error::RoleSwitch => f.write_str("role switch failed (ROLE_STATUS.timeout)"),
            Error::NotHost => f.write_str("role is not host"),
            Error::NotRaw => f.write_str("role is not raw"),
            Error::NotIdle => f.write_str("link busy or in reset"),
            Error::Dropped(ev) => write!(f, "transfer dropped (events {:02x})", ev),
            Error::Stall => f.write_str("STALL"),
            Error::Resp(code) => write!(f, "handshake {}", code),
            Error::Bad(what) => f.write_str(what),
            Error::NoAoa => f.write_str("device does not speak AOA (protocol 0)"),
        }
    }
}

#[derive(Clone, Copy)]
pub struct DeviceId {
    pub vid: u16,
    pub pid: u16,
}

impl DeviceId {
    pub fn is_accessory(&self) -> bool {
        self.vid == GOOGLE_VID && AOA_PIDS.contains(&self.pid)
    }
}

/// What the shim reports, for `usb <port> role`.
pub struct Status {
    pub role: u8,
    pub switching: bool,
    pub timeout: bool,
    pub link: u8,
    pub rx_level: u8,
    pub tx_level: u8,
}

/// Result of a pump or echo run; `err` is why it stopped early.
pub struct Pump {
    pub bytes_out: u32,
    pub bytes_in: u32,
    pub packets: u32,
    pub naks: u32,
    pub ticks: u32,
    /// Byte sum of everything received.
    pub sum: u32,
    /// Ticks in transactions (token to handshake) and in draining the RX FIFO.
    pub xact_ticks: u32,
    pub fifo_ticks: u32,
    /// First wrong received byte: (offset, got, expected).
    pub bad: Option<(u32, u8, u8)>,
    pub err: Option<Error>,
}

/// What `pump_send` puts on the wire: the ramp, or AOAT frames the app's sink verifies.
pub enum Gen {
    Ramp(u8),
    Aoat(crate::aoat::Aoat),
}

impl Gen {
    fn fill(&mut self, buf: &mut [u8], off: u32) {
        match self {
            Gen::Ramp(seed) => {
                for (i, b) in buf.iter_mut().enumerate() {
                    *b = ramp(*seed, off + i as u32);
                }
            }
            Gen::Aoat(a) => a.fill(buf),
        }
    }
}

/// Test stream: byte `off` = seed + off (aoa_testkit P_RAMP, counter at seed 0).
fn ramp(seed: u8, off: u32) -> u8 {
    seed.wrapping_add(off as u8)
}

#[derive(Clone, Copy)]
struct Regs(usize);

impl Regs {
    fn rd(self, offset: usize) -> u8 {
        // SAFETY: byte read of a `usb_host` CSR; only RX_DATA has a side effect
        // (pop) and it is read only after RX_TAG says there is an entry.
        unsafe { read_volatile((self.0 + offset) as *const u8) }
    }

    fn wr(self, offset: usize, value: u8) {
        // SAFETY: byte write; a 32-bit store would strobe the whole word.
        unsafe { write_volatile((self.0 + offset) as *mut u8, value) }
    }
}

fn wait_until(op: &'static str, limit_us: u32, mut done: impl FnMut() -> bool) -> Result<(), Error> {
    let started = clock::now();
    let limit = clock::micros(limit_us);
    loop {
        if done() {
            return Ok(());
        }
        let elapsed = started.elapsed(clock::now());
        if elapsed > limit {
            return Err(Error::Timeout { op, limit_us, elapsed_us: clock::to_micros(elapsed) });
        }
    }
}

/// Empty the RX FIFO of a response whose payload is not read, so the next IN starts clean.
/// `settle`: bytes may still be crossing from the SIE; each straggler gets `FIFO_US`.
fn discard_rx(r: Regs, settle: bool) {
    for _ in 0..RX_DRAIN_MAX {
        let empty = r.rd(RX_TAG) & 2 == 0;
        if empty && (!settle || wait_until("rx drain", FIFO_US, || r.rd(RX_TAG) & 2 != 0).is_err()) {
            return;
        }
        r.rd(RX_DATA);
    }
}

fn setup_packet(bm: u8, request: u8, value: u16, index: u16, length: u16) -> [u8; 8] {
    [bm, request, value as u8, (value >> 8) as u8, index as u8, (index >> 8) as u8,
     length as u8, (length >> 8) as u8]
}

/// One host engine: the register window plus what the bus state implies.
pub struct Host {
    base: Option<usize>,
    addr: u8,
    mps0: u8,
    tog_in: u16,
    tog_out: u16,
    pub ep_in: u8,
    pub ep_out: u8,
    /// wMaxPacketSize of `ep_in` / `ep_out`; 0 until `find_endpoints`.
    mps_in: u16,
    mps_out: u16,
    naks: Cell<u32>,
    /// Ticks spent in `transact` and in `read_rx`, summed by `stage`; a pump zeroes it.
    prof: Cell<(u32, u32)>,
}

impl Host {
    pub const fn new(base: Option<usize>) -> Self {
        Host { base, addr: 0, mps0: 8, tog_in: 0, tog_out: 0, ep_in: 0, ep_out: 0, mps_in: 0, mps_out: 0,
               naks: Cell::new(0),
               prof: Cell::new((0, 0)) }
    }

    pub fn addr(&self) -> Result<u8, Error> {
        self.regs().map(|_| self.addr)
    }

    pub fn set_addr(&mut self, addr: u8) -> Result<(), Error> {
        self.regs()?;
        self.addr = addr & 0x7f;
        Ok(())
    }

    fn regs(&self) -> Result<Regs, Error> {
        self.base.map(Regs).ok_or(Error::NoEngine)
    }

    fn host(&self) -> Result<Regs, Error> {
        let r = self.regs()?;
        if r.rd(ROLE_STATUS) & 3 != ROLE_HOST {
            return Err(Error::NotHost);
        }
        Ok(r)
    }

    fn forget_bus(&mut self) {
        self.addr = 0;
        self.mps0 = 8;
        self.tog_in = 0;
        self.tog_out = 0;
    }

    pub fn status(&self) -> Result<Status, Error> {
        let r = self.regs()?;
        let s = r.rd(ROLE_STATUS);
        Ok(Status { role: s & 3, switching: s & 4 != 0, timeout: s & 8 != 0,
                    link: r.rd(LINK), rx_level: r.rd(RX_LEVEL), tx_level: r.rd(TX_LEVEL) })
    }

    pub fn set_role(&mut self, role: u8) -> Result<(), Error> {
        let r = self.regs()?;
        // A write while a switch is pending is dropped by the shim.
        wait_until("role switch", ROLE_US, || r.rd(ROLE_STATUS) & 4 == 0)?;
        r.wr(ROLE, role);
        wait_until("role switch", ROLE_US, || r.rd(ROLE_STATUS) & 4 == 0)?;
        let s = r.rd(ROLE_STATUS);
        if s & 8 != 0 || s & 3 != role {
            return Err(Error::RoleSwitch);
        }
        self.forget_bus();
        if role == ROLE_HOST {
            // Entering host starts the SIE's own reset sequence.
            wait_until("bus reset after role host", ENTER_RESET_US,
                       || r.rd(LINK) & LINK_RESET_DONE != 0)?;
            clock::wait(clock::millis(RESET_RECOVERY_MS));
        }
        Ok(())
    }

    /// Bus reset; a device that is absent ends as `Timeout("bus reset")`.
    pub fn reset(&mut self) -> Result<(), Error> {
        let r = self.host()?;
        r.wr(CTRL, CTRL_BUS_RESET);
        wait_until("bus reset start", RESET_CLEAR_US, || r.rd(LINK) & LINK_RESET_DONE == 0)?;
        wait_until("bus reset", RESET_US, || r.rd(LINK) & LINK_RESET_DONE != 0)?;
        clock::wait(clock::millis(RESET_RECOVERY_MS));
        self.forget_bus();
        Ok(())
    }

    /// One transaction. Bytes are staged only when the engine can start at once,
    /// else a dropped start would leave them in the SIE FIFO for the next one.
    fn transact(&self, r: Regs, ty: u8, ep: u8, pid: u8, out: &[u8]) -> Result<(u8, u16), Error> {
        let link = r.rd(LINK);
        if link & LINK_IDLE == 0 || link & LINK_RESET_ACTIVE != 0 {
            return Err(Error::NotIdle);
        }
        for &byte in out {
            r.wr(TX_DATA, byte);
        }
        r.wr(ADDR, self.addr);
        r.wr(EP, ep | ty << 4 | pid << 6);
        r.wr(EVENTS, 0xff);
        r.wr(CTRL, CTRL_START);
        wait_until("transfer", XFER_US, || r.rd(EVENTS) & EV_DONE != 0)?;
        let events = r.rd(EVENTS);
        if events & (EV_BUSY_DROP | EV_TIMEOUT | EV_TX_OVERFLOW) != 0 {
            return Err(Error::Dropped(events));
        }
        let len = r.rd(RXLEN_LO) as u16 | (r.rd(RXLEN_HI) as u16) << 8;
        Ok((r.rd(RESP) & 7, len))
    }

    /// Pop `len` payload bytes into `buf`; bytes beyond `buf` are discarded.
    fn read_rx(&self, r: Regs, buf: &mut [u8], len: u16) -> Result<usize, Error> {
        for i in 0..len as usize {
            // Ready is the common case: no clock read unless the FIFO is empty.
            if r.rd(RX_TAG) & 2 == 0 {
                wait_until("rx fifo", FIFO_US, || r.rd(RX_TAG) & 2 != 0)?;
            }
            let byte = r.rd(RX_DATA);
            if let Some(slot) = buf.get_mut(i) {
                *slot = byte;
            }
        }
        Ok(len as usize)
    }

    /// Repeat a transaction while the device NAKs or resends, until `limit_us`.
    /// Errors (timeout, CRC) get `TRIES` attempts. Returns bytes read for IN.
    fn stage(&self, r: Regs, ty: u8, ep: u8, pid: u8, out: &[u8], inbuf: &mut [u8],
             op: &'static str, limit_us: u32) -> Result<usize, Error> {
        let started = clock::now();
        let limit = clock::micros(limit_us);
        let mut errors = 0;
        loop {
            let t0 = clock::now();
            let (resp, len) = match self.transact(r, ty, ep, pid, out) {
                Ok(v) => v,
                Err(e) => {
                    if ty == TY_IN {
                        discard_rx(r, true);
                    }
                    return Err(e);
                }
            };
            let t1 = clock::now();
            let (x, f) = self.prof.get();
            self.prof.set((x.wrapping_add(t0.elapsed(t1)), f));
            if ty == TY_IN && resp != RESP_ACK {
                // NAK/STALL carry no payload; CRC, overflow and timeout may leave some.
                discard_rx(r, !matches!(resp, RESP_NAK | RESP_STALL));
            }
            match resp {
                // Wrong DATA PID: a resend after our ACK was lost. The SIE ACKed it; drop it,
                // keep the toggle, ask again (USB 2.0 8.6.4).
                RESP_ACK if ty == TY_IN && r.rd(RXPID) != DATA_PID[pid as usize & 1] => {
                    self.read_rx(r, &mut [], len)?;
                }
                RESP_ACK => {
                    if ty != TY_IN {
                        return Ok(0);
                    }
                    let n = self.read_rx(r, inbuf, len);
                    let (x, f) = self.prof.get();
                    self.prof.set((x, f.wrapping_add(t1.elapsed(clock::now()))));
                    return n;
                }
                RESP_NAK => self.naks.set(self.naks.get() + 1),
                RESP_STALL => return Err(Error::Stall),
                code => {
                    errors += 1;
                    if errors >= TRIES {
                        return Err(Error::Resp(code));
                    }
                }
            }
            let elapsed = started.elapsed(clock::now());
            if elapsed > limit {
                return Err(Error::Timeout { op, limit_us, elapsed_us: clock::to_micros(elapsed) });
            }
        }
    }

    /// SETUP stage only, at the current address.
    pub fn setup(&mut self, setup: &[u8; 8]) -> Result<(), Error> {
        let r = self.host()?;
        self.stage(r, TY_SETUP, 0, 0, setup, &mut [], "setup", SETUP_US).map(|_| ())
    }

    /// Setup + data + status. Returns bytes read (IN) or 0.
    pub fn control(&mut self, setup: &[u8; 8], out: &[u8], inbuf: &mut [u8]) -> Result<usize, Error> {
        let r = self.host()?;
        self.stage(r, TY_SETUP, 0, 0, setup, &mut [], "control setup", SETUP_US)?;
        let (mps, mut toggle, mut n) = (self.mps0 as usize, 1, 0);
        let length = setup[6] as usize | (setup[7] as usize) << 8;
        let want = length.min(inbuf.len());
        // No data stage when wLength is 0, whatever the direction bit: the status stage is IN.
        if setup[0] & 0x80 != 0 && length != 0 {
            // `stage` returns the packet's true length; bytes past the caller's buffer are popped
            // and dropped, so the data stage still completes before the status stage (8.5.3).
            let (mut done, mut last) = (0, mps);
            while done < length && last == mps {
                last = self.stage(r, TY_IN, 0, toggle, &[], &mut inbuf[n..want], "control data in", DATA_US)?;
                n += last.min(want - n);
                done += last;
                toggle ^= 1;
            }
            self.stage(r, TY_OUT, 0, 1, &[], &mut [], "control status", STATUS_US)?;
        } else {
            for chunk in out.chunks(mps) {
                self.stage(r, TY_OUT, 0, toggle, chunk, &mut [], "control data out", DATA_US)?;
                toggle ^= 1;
            }
            self.stage(r, TY_IN, 0, 1, &[], &mut [], "control status", STATUS_US)?;
        }
        if setup[0] == 0 && setup[1] == REQ_SET_CONFIGURATION {
            // The device restarts its toggles at DATA0 (USB 2.0 9.1.1.5).
            self.tog_in = 0;
            self.tog_out = 0;
        }
        if setup[0] == 0x02 && setup[1] == REQ_CLEAR_FEATURE && setup[2] == 0 && setup[3] == 0 {
            // CLEAR_FEATURE(ENDPOINT_HALT) restarts that endpoint's toggle (9.4.5).
            let bit = 1u16 << (setup[4] & 15);
            if setup[4] & 0x80 != 0 { self.tog_in &= !bit } else { self.tog_out &= !bit }
        }
        Ok(n)
    }

    /// Bulk/interrupt IN on endpoint `ep`; NAK retried until `limit_us`.
    /// Returns the packet's length; bytes past `buf` are dropped (the toggle still advances).
    pub fn bulk_in(&mut self, ep: u8, buf: &mut [u8], limit_us: u32) -> Result<usize, Error> {
        let r = self.host()?;
        let bit = 1 << (ep & 15);
        let n = self.stage(r, TY_IN, ep & 15, (self.tog_in & bit != 0) as u8, &[], buf,
                           "bulk in", limit_us)?;
        self.tog_in ^= bit;
        Ok(n)
    }

    /// Bulk/interrupt OUT on endpoint `ep`, one packet; NAK retried until `limit_us`.
    pub fn bulk_out(&mut self, ep: u8, data: &[u8], limit_us: u32) -> Result<(), Error> {
        let r = self.host()?;
        let bit = 1 << (ep & 15);
        self.stage(r, TY_OUT, ep & 15, (self.tog_out & bit != 0) as u8, data, &mut [],
                   "bulk out", limit_us)?;
        self.tog_out ^= bit;
        Ok(())
    }

    /// Bulk max packet of the found pair (`find_endpoints`): `(in, out)`.
    fn bulk_mps(&self) -> Result<(usize, usize), Error> {
        self.host()?;
        if self.mps_in == 0 || self.mps_out == 0 {
            return Err(Error::Bad("no bulk pair found yet (aoa)"));
        }
        Ok((self.mps_in as usize, self.mps_out as usize))
    }

    /// Microseconds left in the run; `Timeout` once `PUMP_US` is spent.
    fn pump_left(started: clock::Instant, op: &'static str) -> Result<u32, Error> {
        let spent = clock::to_micros(started.elapsed(clock::now()));
        match PUMP_US.checked_sub(spent) {
            Some(left) if left > 0 => Ok(left),
            _ => Err(Error::Timeout { op, limit_us: PUMP_US, elapsed_us: spent }),
        }
    }

    fn pump_send(&mut self, ep: u8, p: &mut Pump, len: u32, gen: &mut Gen, started: clock::Instant)
                 -> Result<(), Error> {
        let mps = self.bulk_mps()?.1;
        let mut buf = [0u8; 512];
        let mut n = (len - p.bytes_out).min(mps as u32) as usize;
        if let Gen::Aoat(a) = gen {
            if a.xfer {
                n = n.min(a.frame_left() as usize);
            }
        }
        gen.fill(&mut buf[..n], p.bytes_out);
        self.bulk_out(ep, &buf[..n], Self::pump_left(started, "pump out")?)?;
        p.bytes_out += n as u32;
        p.packets += 1;
        if let Gen::Aoat(a) = gen {
            if a.xfer_zlp && a.frame_left() == a.frame_len() && a.frame_len() % mps as u32 == 0 {
                self.bulk_out(ep, &[], Self::pump_left(started, "pump out zlp")?)?;
                p.packets += 1;
            }
        }
        Ok(())
    }

    fn pump_recv(&mut self, ep: u8, p: &mut Pump, seed: Option<u8>, started: clock::Instant)
                 -> Result<(), Error> {
        let mps = self.bulk_mps()?.0;
        let mut buf = [0u8; 512];
        let n = self.bulk_in(ep, &mut buf[..mps], Self::pump_left(started, "pump in")?)?.min(mps);
        let mut sum = p.sum;
        for &b in &buf[..n] {
            sum = sum.wrapping_add(b as u32);
        }
        p.sum = sum;
        if let (Some(s), None) = (seed, p.bad) {
            for (i, &b) in buf[..n].iter().enumerate() {
                let at = p.bytes_in + i as u32;
                if b != ramp(s, at) {
                    p.bad = Some((at, b, ramp(s, at)));
                    break;
                }
            }
        }
        p.bytes_in += n as u32;
        p.packets += 1;
        Ok(())
    }

    fn pump_new() -> Pump {
        Pump { bytes_out: 0, bytes_in: 0, packets: 0, naks: 0, ticks: 0, sum: 0, xact_ticks: 0, fifo_ticks: 0,
               bad: None, err: None }
    }

    fn pump_end(&self, mut p: Pump, naks0: u32, started: clock::Instant, r: Result<(), Error>) -> Pump {
        p.naks = self.naks.get() - naks0;
        (p.xact_ticks, p.fifo_ticks) = self.prof.get();
        p.ticks = started.elapsed(clock::now());
        p.err = r.err();
        p
    }

    /// `len` bytes of the ramp as consecutive bulk OUT packets; `zlp` ends a whole-packet
    /// multiple (or an empty transfer) with a zero-length packet.
    pub fn pump_out(&mut self, ep: u8, len: u32, seed: u8, zlp: bool) -> Result<Pump, Error> {
        self.pump_stream(ep, len, Gen::Ramp(seed), zlp)
    }

    /// `pump_out` of whole AOAT frames (`len` a multiple of `frame`), verified by the app's sink.
    pub fn pump_out_aoat(&mut self, ep: u8, len: u32, frame: u32, seed: u32, test: u16, xfer: bool,
                         zlp: bool) -> Result<Pump, Error> {
        if frame < crate::aoat::MIN_FRAME || len % frame != 0 {
            return Err(Error::Bad("aoat: frame under 24 B or len not whole frames"));
        }
        let mut aoat = crate::aoat::Aoat::new(frame, len / frame, seed, test);
        aoat.xfer = xfer;
        aoat.xfer_zlp = xfer && zlp;
        self.pump_stream(ep, len, Gen::Aoat(aoat), zlp && !xfer)
    }

    fn pump_stream(&mut self, ep: u8, len: u32, mut gen: Gen, zlp: bool) -> Result<Pump, Error> {
        let mps = self.bulk_mps()?.1 as u32;
        if len > PUMP_MAX {
            return Err(Error::Bad("len over 1 MiB"));
        }
        self.prof.set((0, 0));
        let (naks0, started, mut p) = (self.naks.get(), clock::now(), Self::pump_new());
        let mut r = Ok(());
        while r.is_ok() && p.bytes_out < len {
            r = self.pump_send(ep, &mut p, len, &mut gen, started);
        }
        if r.is_ok() && zlp && len % mps == 0 {
            r = Self::pump_left(started, "pump out").and_then(|left| self.bulk_out(ep, &[], left));
            p.packets += r.is_ok() as u32;
        }
        Ok(self.pump_end(p, naks0, started, r))
    }

    /// Read `len` bytes of bulk IN (whole packets, short ones included); the ramp is
    /// checked when `seed` is given.
    pub fn pump_in(&mut self, ep: u8, len: u32, seed: Option<u8>) -> Result<Pump, Error> {
        self.bulk_mps()?;
        if len > PUMP_MAX {
            return Err(Error::Bad("len over 1 MiB"));
        }
        self.prof.set((0, 0));
        let (naks0, started, mut p) = (self.naks.get(), clock::now(), Self::pump_new());
        let mut r = Ok(());
        while r.is_ok() && p.bytes_in < len {
            r = self.pump_recv(ep, &mut p, seed, started);
        }
        Ok(self.pump_end(p, naks0, started, r))
    }

    /// OUT `ECHO_CHUNK` bytes at a time, then IN until they are back; the ramp is checked.
    /// The far read ends on a short packet: a whole-packet chunk under `ECHO_CHUNK` gets a
    /// ZLP out, and the ZLP the far write adds comes back in (#565).
    pub fn echo(&mut self, ep: u8, len: u32, seed: u8) -> Result<Pump, Error> {
        let (mps_in, mps_out) = self.bulk_mps()?;
        let (mps_in, mps_out) = (mps_in as u32, mps_out as u32);
        if len > PUMP_MAX {
            return Err(Error::Bad("len over 1 MiB"));
        }
        let mut gen = Gen::Ramp(seed);
        self.prof.set((0, 0));
        let (naks0, started, mut p) = (self.naks.get(), clock::now(), Self::pump_new());
        let mut r = Ok(());
        while r.is_ok() && p.bytes_in < len {
            let chunk = (len - p.bytes_in).min(ECHO_CHUNK);
            let end = p.bytes_in + chunk;
            while r.is_ok() && p.bytes_out < end {
                r = self.pump_send(ep, &mut p, end, &mut gen, started);
            }
            if r.is_ok() && chunk % mps_out == 0 && chunk < ECHO_CHUNK {
                r = Self::pump_left(started, "echo zlp").and_then(|left| self.bulk_out(ep, &[], left));
                p.packets += r.is_ok() as u32;
            }
            while r.is_ok() && p.bytes_in < end {
                r = self.pump_recv(ep, &mut p, Some(seed), started);
            }
            if r.is_ok() && chunk % mps_in == 0 {
                r = self.pump_recv(ep, &mut p, Some(seed), started);
            }
        }
        Ok(self.pump_end(p, naks0, started, r))
    }

    /// GET_DESCRIPTOR(device) at the current address.
    pub fn device_descriptor(&mut self) -> Result<[u8; 18], Error> {
        let mut d = [0u8; 18];
        let n = self.control(&setup_packet(0x80, REQ_GET_DESCRIPTOR, 0x0100, 0, 18), &[], &mut d)?;
        if n < 18 || d[1] != 1 {
            return Err(Error::Bad("short or wrong device descriptor"));
        }
        Ok(d)
    }

    /// `enumerate`, retried while the phone is still re-attaching after a bus reset (Android drops
    /// and re-presents the gadget, 0.1-1 s measured, #571). Bounded by `ATTACH_US`.
    fn enumerate_waiting(&mut self, addr: u8) -> Result<DeviceId, Error> {
        let started = clock::now();
        loop {
            match self.enumerate(addr) {
                Err(Error::Resp(4) | Error::NotIdle | Error::Timeout { .. }) => {
                    let elapsed = started.elapsed(clock::now());
                    if elapsed > clock::micros(ATTACH_US) {
                        return Err(Error::Timeout { op: "device wait", limit_us: ATTACH_US,
                                                    elapsed_us: clock::to_micros(elapsed) });
                    }
                    clock::wait(clock::millis(ATTACH_GAP_MS));
                    let _ = self.reset();
                }
                other => return other,
            }
        }
    }

    /// Addr 0: read 8 bytes for bMaxPacketSize0, SET_ADDRESS, read the descriptor.
    pub fn enumerate(&mut self, addr: u8) -> Result<DeviceId, Error> {
        let mut d = [0u8; 8];
        self.control(&setup_packet(0x80, REQ_GET_DESCRIPTOR, 0x0100, 0, 8), &[], &mut d)?;
        if !matches!(d[7], 8 | 16 | 32 | 64) {
            return Err(Error::Bad("bad bMaxPacketSize0"));
        }
        self.mps0 = d[7];
        self.control(&setup_packet(0x00, REQ_SET_ADDRESS, addr as u16, 0, 0), &[], &mut [])?;
        self.addr = addr;
        clock::wait(clock::millis(ADDR_RECOVERY_MS));
        let d = self.device_descriptor()?;
        Ok(DeviceId { vid: d[8] as u16 | (d[9] as u16) << 8, pid: d[10] as u16 | (d[11] as u16) << 8 })
    }

    /// SET_CONFIGURATION; data toggles restart at DATA0 (USB 2.0 9.1.1.5).
    pub fn set_configuration(&mut self, config: u8) -> Result<(), Error> {
        self.control(&setup_packet(0x00, REQ_SET_CONFIGURATION, config as u16, 0, 0), &[], &mut [])?;
        self.tog_in = 0;
        self.tog_out = 0;
        Ok(())
    }

    /// Find the first bulk IN and OUT endpoints: numbers in `ep_in` / `ep_out`, and their
    /// wMaxPacketSize, which the pumps use as the packet size.
    pub fn find_endpoints(&mut self) -> Result<(), Error> {
        let mut c = [0u8; usb_desc::CONFIG_MAX];
        let get = |len| setup_packet(0x80, REQ_GET_DESCRIPTOR, 0x0200, 0, len);
        let n = self.control(&get(usb_desc::CONFIG_HEADER as u16), &[], &mut c)?;
        let total = usb_desc::total_length(&c[..n]).ok_or(Error::Bad("bad config descriptor header"))?;
        let n = self.control(&get(total), &[], &mut c)?;
        let pair = usb_desc::bulk_pair(&c[..n]).ok_or(Error::Bad("no bulk IN/OUT pair"))?;
        if !(1..=512).contains(&pair.mps_in) || !(1..=512).contains(&pair.mps_out) {
            return Err(Error::Bad("bulk wMaxPacketSize 0 or over the 512 B SIE FIFO"));
        }
        (self.ep_in, self.mps_in, self.ep_out, self.mps_out) =
            (pair.ep_in, pair.mps_in, pair.ep_out, pair.mps_out);
        Ok(())
    }

    /// The whole AOA handshake from host role with the link up: enumerate, 51,
    /// 52 x6, 53, bus reset until the phone is back as 18d1:2d00/2d01, configure,
    /// find the bulk pair. Returns the accessory-mode id.
    pub fn aoa(&mut self, strings: &[&[u8]; 6]) -> Result<DeviceId, Error> {
        self.host()?;
        let mut id = self.enumerate_waiting(DEVICE_ADDR)?;
        if !id.is_accessory() {
            let mut version = [0u8; 2];
            self.control(&setup_packet(0xc0, AOA_GET_PROTOCOL, 0, 0, 2), &[], &mut version)?;
            if version[0] == 0 && version[1] == 0 {
                return Err(Error::NoAoa);
            }
            for (index, text) in strings.iter().enumerate() {
                let mut buf = [0u8; 64];
                if text.len() >= buf.len() {
                    return Err(Error::Bad("AOA string too long"));
                }
                buf[..text.len()].copy_from_slice(text);
                let len = text.len() + 1;
                self.control(&setup_packet(0x40, AOA_SEND_STRING, 0, index as u16, len as u16),
                             &buf[..len], &mut [])?;
            }
            // The phone detaches during START; an error here is the normal case.
            let _ = self.control(&setup_packet(0x40, AOA_START, 0, 0, 0), &[], &mut []);
            let started = clock::now();
            loop {
                clock::wait(clock::millis(ATTACH_GAP_MS));
                if self.reset().is_ok() {
                    if let Ok(found) = self.enumerate(DEVICE_ADDR) {
                        if found.is_accessory() {
                            id = found;
                            break;
                        }
                    }
                }
                let elapsed = started.elapsed(clock::now());
                if elapsed > clock::micros(ATTACH_US) {
                    return Err(Error::Timeout { op: "aoa re-attach", limit_us: ATTACH_US,
                                                elapsed_us: clock::to_micros(elapsed) });
                }
            }
        }
        self.set_configuration(1)?;
        self.find_endpoints()?;
        Ok(id)
    }

    /// Raw role: queue `bytes` as one ULPI packet (last byte ends it with STP).
    pub fn raw_tx(&mut self, bytes: &[u8]) -> Result<(), Error> {
        let r = self.regs()?;
        if r.rd(ROLE_STATUS) & 3 != ROLE_RAW {
            return Err(Error::NotRaw);
        }
        let Some((last, body)) = bytes.split_last() else {
            return Err(Error::Bad("nothing to send"));
        };
        if bytes.len() > 128 {
            return Err(Error::Bad("raw packet over the 128-entry TX FIFO"));
        }
        for &byte in body {
            r.wr(TX_DATA, byte);
        }
        r.wr(TX_LAST, *last);
        Ok(())
    }

    /// Pop one RX FIFO entry: `(is_rxcmd, byte)`, or `None` when empty.
    pub fn rx_pop(&mut self) -> Result<Option<(bool, u8)>, Error> {
        let r = self.regs()?;
        let tag = r.rd(RX_TAG);
        if tag & 2 == 0 {
            return Ok(None);
        }
        Ok(Some((tag & 1 != 0, r.rd(RX_DATA))))
    }

    /// Debug trace (CYNTHION_USB_TRACE build): write TR_CTRL.
    pub fn trace_ctrl(&mut self, v: u8) -> Result<(), Error> {
        self.regs()?.wr(TR_CTRL, v & 15);
        Ok(())
    }

    /// `(state, present, entries)`.
    pub fn trace_status(&self) -> Result<(u8, bool, u16), Error> {
        let r = self.regs()?;
        let s = r.rd(TR_STATE);
        Ok((s & 3, s & 8 != 0, r.rd(TR_CNT_LO) as u16 | (r.rd(TR_CNT_HI) as u16) << 8))
    }

    /// One 41-bit trace entry as 6 little-endian bytes. Load wait: the usb-domain
    /// latch takes ~8 cycles (0.15 us); 5 us is 30x.
    pub fn trace_entry(&self, i: u16) -> Result<[u8; 6], Error> {
        let r = self.regs()?;
        r.wr(TR_IDX_LO, i as u8);
        r.wr(TR_IDX_HI, (i >> 8) as u8);
        wait_until("trace load", 5, || r.rd(TR_STATE) & 4 == 0)?;
        let mut out = [0u8; 6];
        for (k, b) in out.iter_mut().enumerate() {
            r.wr(TR_SEL, k as u8);
            *b = r.rd(TR_DATA);
        }
        Ok(out)
    }
}
