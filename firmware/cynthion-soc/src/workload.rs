//! A synthetic USB device-emulation load, shaped on moondancer's measured one.
//!
//! Issue #115's last comment rejects the baseline every earlier measurement
//! used: an idle shell at 0.10-0.23% busy. This core is being built as a USB
//! controller, and the workload that decides the concurrency question is device
//! emulation -- bursty, latency-sensitive, arriving at the host's convenience.
//! This module is that workload, as faithfully as a board-free build can make
//! it, and section "How this differs" says exactly where it lies.
//!
//! Everything here is behind `--features workload` and nothing in `src/main.rs`
//! reaches it without that feature, so the shipping image is unchanged.
//!
//! ## What moondancer actually does per event
//!
//! moondancer is a workspace member of `greatscottgadgets/cynthion`, at
//! `firmware/moondancer`, and not a repo of its own. Paths below are relative to
//! that `firmware/` directory; the gateware ones are `greatscottgadgets/luna-soc`
//! under `luna_soc/gateware/core/`. Neither is checked out in this tree, so every
//! line number below was read from the mirror rather than copied from a summary.
//!
//! * **In the handler**, on `USB0_EP_CONTROL`: acknowledge, read the endpoint
//!   number, then drain the 8-byte setup FIFO **one byte at a time over MMIO**
//!   -- `while ... have().bit() { buffer[n] = ...data().read().byte().bits() }`,
//!   `lunasoc-hal/src/usb.rs:380-392` -- build a `SetupPacket` from it, and
//!   enqueue an `InterruptEvent` into a **64-slot** `Queue`
//!   (`moondancer/src/bin/moondancer.rs:28`, handler at
//!   `moondancer/src/util.rs:50-70`).
//! * **A full queue is fatal.** `dispatch_event` drains it into the log and then
//!   `loop { nop }` -- in interrupt context, forever
//!   (`moondancer/src/bin/moondancer.rs:30-46`). The queue is an assertion, not
//!   a buffer.
//! * **In the main loop**, `Moondancer::dispatch_event`
//!   (`moondancer/src/gcp/moondancer.rs:82`) copies the packet on, and anything
//!   it cannot answer locally goes to the host over a second USB port. Each
//!   command zeroes a **1 KiB** buffer (`LIBGREAT_MAX_COMMAND_SIZE = 1024`,
//!   `libgreat/src/gcp.rs:15`) at `moondancer/src/bin/moondancer.rs:460`, and
//!   the verbs zero their own at `gcp/moondancer.rs:560` and `:821` -- one to
//!   three kilobytes of memset per command, independent of payload.
//! * **On the write path**, `write_with_packet_size`
//!   (`lunasoc-hal/src/usb.rs:487`) pushes the response into the endpoint FIFO
//!   with **one 8-bit MMIO store per byte**.
//! * A 512-byte bulk payload is copied repeatedly between the FIFO, a
//!   `rx_buffer` and the packet buffer (`gcp/moondancer.rs:147-162`) before it
//!   reaches the 1 KiB response buffer.
//!
//! Upstream's own numbers, in comments beside the code that produced them:
//! `moondancer/examples/bulk_speed_test.rs:390` reads 5.03 MB/s through the
//! byte-at-a-time FIFO loop, `:394` reads 6.39 MB/s with no memory access
//! behind it, and `:414-416` reads ~4.04 MB/s for the shape actually shipped.
//! At 60 MHz, 5.03 MB/s is **11.9 cycles per MMIO byte store**, so a 512-byte
//! packet is **~5,000-6,000 cycles, 85-100 us** -- about 80% of a 125 us
//! high-speed microframe, which is why the part tops out near one packet per
//! microframe.
//!
//! ## The deadline, which is not the one people assume
//!
//! **Packet level is soft.** luna-soc's IN endpoint NAKs every IN token while
//! the CPU is still filling the FIFO -- *"We'll wait for it to do so, and NAK
//! any packets that arrive"*, `usb2/ep_in.py:279-292` -- and the OUT endpoint
//! NAKs when it is not primed (`nak_receives = token.is_out & ~ready_to_receive
//! & ~stalled`, `usb2/ep_out.py:277`). USB 2.0 §8.5.3.3 makes that legitimate
//! flow control, so a late CPU costs a retry, not a protocol error.
//!
//! **Transfer level is hard, and generous.** USB 2.0 §9.2.6.4: the first data
//! packet of a control read within **500 ms**, the status stage within **50 ms**
//! of the last data packet, and a request with no data stage complete within
//! **50 ms**. §9.2.6.3: `SetAddress` complete within **50 ms**, then 2 ms of
//! recovery. §9.2.6.1: 10 ms of reset recovery. Nothing in this firmware is
//! anywhere near those.
//!
//! **There is exactly one hard, silent window, and it is in the gateware.** The
//! control endpoint's setup FIFO is 8 bytes deep and is *cleared by the arrival
//! of the next SETUP token* -- `clear_fifo = new_setup | reset_requested` at
//! `usb2/ep_control.py:130`, feeding `ResetInserter(clear_fifo)` around a
//! `depth=8` FIFO at `:135`. Miss it and the previous setup packet is destroyed
//! with the host already ACKed, and the firmware reads zero bytes
//! (`moondancer/src/util.rs:62-63`). That endpoint also cannot NAK: it *"always
//! ACKs packets, and does not allow for any flow control; as a USB device must
//! always be ready to accept control packets. [USB2.0: 8.6.1]"*,
//! `ep_control.py:30-34`. So the deadline
//! that matters is **one host control-transfer inter-arrival**: at high speed a
//! short control transfer occupies about three microframes, so **~375 us**.
//!
//! That is the number this module measures against. It is three orders of
//! magnitude tighter than §9.2.6.4 and three times looser than a microframe,
//! and a model built against either of those is modelling the wrong thing.
//!
//! ## How this differs from the real workload
//!
//! Stated plainly, because none of it is hidden by the numbers:
//!
//! * **The arrival source is a 16550, not a USB device controller.** One
//!   received byte is one USB event, through a real PLIC source into the real
//!   handler. The bytes are injected by the 1 ms tick through local loopback
//!   ([`tick`]) rather than by a host, which buys reproducibility -- the two
//!   models under test see the identical arrival sequence -- and gives up the
//!   host's own jitter. Arrivals are bursty at 1 ms granularity and not
//!   microframe-aligned.
//! * **The FIFO is the 16550's scratch register.** SCR is eight bits of MMIO
//!   with no side effect of any kind, on the SoC and on QEMU's `virt` alike, so
//!   a byte-at-a-time loop over it is a real bus transaction per byte -- which
//!   is the property that makes moondancer's FIFO loop cost what it costs.
//! * **No second USB port.** moondancer spends three to six interrupts on its
//!   host link for every one on the port under test. That multiplier is left
//!   out; it would make every figure here worse for the superloop, not better.
//! * **The Type-C deferral stands in for the one long job.** On the board it is
//!   a millisecond of I2C on a shared controller. Under QEMU there is no I2C, so
//!   [`SERVICE_US`] is spun on the CLINT instead, and the source is `virt`'s
//!   goldfish RTC alarm -- a second PLIC line that is level-sensitive, that the
//!   handler cannot clear cheaply, and that the guest can schedule. That is the
//!   same obligation shape as a FUSB302B.
//! * **QEMU retires one instruction per cycle.** The board measures IPC 0.302 at
//!   `opt-level = "z"`. Every instruction count here is real; every *cycle*
//!   count is optimistic by about 3.3x, and cache behaviour is modelled
//!   separately by `scripts/soc_icache_model.py` rather than measured.

use core::cell::UnsafeCell;
use core::fmt::Write;
use core::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use crate::clock;
use crate::metrics;
use crate::target;
use crate::uart::Uart;

/// Bytes in a USB setup packet. USB 2.0 §9.3.
const SETUP_BYTES: usize = 8;

/// A high-speed bulk packet. luna-soc's OUT FIFO is exactly one of them --
/// `SyncFIFOBuffered(width=8, depth=self._max_packet_size)`,
/// `usb2/ep_out.py:265`, and the IN FIFO the same at `ep_in.py:169`.
const PACKET: usize = 512;

/// libgreat's response buffer, zeroed per command. `LIBGREAT_MAX_COMMAND_SIZE`
/// is 1024 (`libgreat/src/gcp.rs:15`).
const GCP_BUF: usize = 1024;

/// Slots in the event queue. moondancer's is 64
/// (`moondancer/src/bin/moondancer.rs:28`).
const SLOTS: usize = 64;

/// How long the deferred job takes, in microseconds.
///
/// A FUSB302B is cleared by reading three read-to-clear registers over I2C at
/// 80 kHz -- about a millisecond, and `src/irq.rs`'s `defer_type_c` exists
/// entirely because of it. There is no I2C under QEMU, so this is spun on the
/// CLINT instead. 1000 us, because that is what the board does.
const SERVICE_US: u32 = 1000;

/// The deadline this workload is measured against. See the module comment: it
/// is one host control-transfer inter-arrival, not a microframe and not
/// §9.2.6.4.
pub const DEADLINE_US: u32 = 375;

/// One arrival, as the handler recorded it.
///
/// `at` is the low half of `mtime`, which is what the latency is computed
/// against. A whole `Instant` would be 64 bits and there is no 64-bit atomic on
/// riscv32imac -- see `src/timer.rs`'s `MILLIS` for the same argument.
struct Slot {
    at: AtomicU32,
    kind: AtomicU32,
}

static QUEUE: [Slot; SLOTS] = [const {
    Slot {
        at: AtomicU32::new(0),
        kind: AtomicU32::new(0),
    }
}; SLOTS];

static HEAD: AtomicUsize = AtomicUsize::new(0);
static TAIL: AtomicUsize = AtomicUsize::new(0);

/// Events the handler enqueued, events the task completed, and events dropped
/// because the queue was full.
///
/// Dropping rather than what upstream does: `bin/moondancer.rs:30-46` answers a
/// full queue by logging every entry and then `loop { nop }` **in interrupt
/// context**, which makes the queue a fatal assertion rather than a buffer. A
/// counter is the more useful instrument here, and `dropped` being nonzero is
/// what says a measurement was taken past saturation.
static ARRIVED: AtomicU32 = AtomicU32::new(0);
static COMPLETED: AtomicU32 = AtomicU32::new(0);
static DROPPED: AtomicU32 = AtomicU32::new(0);

/// Worst and total arrival-to-completion latency, in `mtime` ticks.
static WORST: AtomicU32 = AtomicU32::new(0);
static TOTAL: AtomicU32 = AtomicU32::new(0);
/// Events that missed [`DEADLINE_US`].
static MISSED: AtomicU32 = AtomicU32::new(0);

/// Instructions retired inside [`handle`], summed over the run.
///
/// Separate from the run's total because the total is just elapsed virtual time
/// -- this CPU never halts, so `minstret` over the window equals the window.
/// What the per-event cost actually is needs the work bracketed, and two `csrr`s
/// per event is what that costs.
static WORK: AtomicU32 = AtomicU32::new(0);

/// The deferred job: assertions, and worst assertion-to-completion latency.
static DEFERRED: AtomicU32 = AtomicU32::new(0);
static DEFERRED_AT: AtomicU32 = AtomicU32::new(0);
static DEFERRED_WORST: AtomicU32 = AtomicU32::new(0);
static DEFERRED_PENDING: AtomicU32 = AtomicU32::new(0);
/// When the previous assertion arrived, and the smallest gap between two --
/// the check that the source really is firing on the grid it was armed on.
static DEFERRED_LAST: AtomicU32 = AtomicU32::new(0);
static DEFERRED_GAP: AtomicU32 = AtomicU32::new(u32::MAX);

/// Is the workload running? Nothing below costs anything until the `usb`
/// command sets it, so a build with this feature on still behaves like the
/// shell until asked.
static ACTIVE: AtomicU32 = AtomicU32::new(0);

/// Scratch the per-event work copies through, standing in for moondancer's
/// `rx_buffer`, `Packet.buffer` and the two 1 KiB libgreat buffers.
///
/// Plain `[u8; N]` behind an `UnsafeCell` rather than `[AtomicU8; N]`: the whole
/// point of these buffers is to be memset and memcpy'd at the rate the real ones
/// are, and an atomic array compiles to a byte loop where a `[u8]` becomes a
/// call to `memset`. And NOT `static mut`, which `scripts/soc_irq_log_check.py`
/// rejects on sight -- rightly, since one of the three writers below runs in a
/// task the dispatcher may enter from a handler.
struct Buffers {
    response: UnsafeCell<[u8; GCP_BUF]>,
    stage: UnsafeCell<[u8; PACKET]>,
    rx: UnsafeCell<[u8; PACKET]>,
}

/// SAFETY: one hart, and every reader and writer is either the `usb` command's
/// own loop or the single dispatcher task that replaces it. The dispatcher's
/// threshold makes that task non-reentrant (`src/dispatch.rs`), so there is
/// never a second holder.
unsafe impl Sync for Buffers {}

static BUFFERS: Buffers = Buffers {
    response: UnsafeCell::new([0; GCP_BUF]),
    stage: UnsafeCell::new([0; PACKET]),
    rx: UnsafeCell::new([0; PACKET]),
};

/// The 16550 scratch register, used as a stand-in packet FIFO.
///
/// SCR is the one byte of a 16550 that does nothing at all: it is not read to
/// clear, it does not pop a FIFO, and it does not gate an interrupt. So a
/// byte-at-a-time loop over it costs one real bus transaction per byte -- which
/// is the whole of what makes moondancer's FIFO loop 11.9 cycles a byte -- while
/// being safe to do inside a handler on a live console.
const SCR: usize = 7;

/// Transmit holding register, and the modem control register whose bit 4 is
/// local loopback.
const THR: usize = 0;
const MCR: usize = 4;
const MCR_LOOP: u8 = 0x10;

/// Arrivals injected per 1 ms tick.
///
/// moondancer sustains 8,000-10,000 usb0 interrupts a second at its measured
/// 4-5 MB/s (`examples/bulk_speed_test.rs:414-416`), and at ~90 us of work per
/// packet that is 70-80% of the CPU -- the real thing genuinely runs near
/// saturation. Four is half that rate, chosen so the event queue does not
/// overflow: at saturation the latency figure measures the backlog rather than
/// the scheduling, and the scheduling is the question.
///
/// Delivered as a burst rather than spread, which is the shape the issue asks
/// for: a host does not arrive politely spaced.
const BURST: usize = 4;

/// The `time` CSR as a bare count.
///
/// `clock::Instant` keeps its counter private, and rightly: only differences
/// mean anything. The queue below has to store one, so it is taken as an
/// interval from the counter's own zero, which is the same number.
fn ticks() -> u32 {
    clock::Instant::ZERO.elapsed(clock::now())
}

/// Counter ticks in `us` microseconds. `clock::millis` is the millisecond form;
/// nothing outside this file schedules finer, so it lives here.
const fn micros(us: u32) -> u32 {
    ((target::TIME_HZ as u64 * us as u64) / 1_000_000) as u32
}

fn fifo_read(base: usize) -> u8 {
    // SAFETY: a byte inside a 16550 that has no side effect on read. See above.
    unsafe { core::ptr::read_volatile((base + SCR) as *const u8) }
}

fn fifo_write(base: usize, byte: u8) {
    // SAFETY: as above.
    unsafe { core::ptr::write_volatile((base + SCR) as *mut u8, byte) }
}

/// Inject a burst of arrivals. Called from the 1 ms tick.
///
/// **In the timer handler on purpose.** An arrival generator that lived in the
/// main loop would be stopped by exactly the thing under test -- a long turn --
/// and would hide the defect it is here to measure. The 16550 is in local
/// loopback for the duration, so a byte written to THR comes back through RSR,
/// raises the same PLIC source a real byte would, and reaches the same handler.
pub fn tick() {
    if ACTIVE.load(Ordering::Relaxed) == 0 {
        return;
    }
    let base = target::UART_BASES[0];
    let mut i = 0;
    while i < BURST {
        // SAFETY: THR on a 16550 whose DLAB is clear. In loopback this reaches
        // no wire.
        unsafe { core::ptr::write_volatile((base + THR) as *mut u8, i as u8) };
        i += 1;
    }
}

/// Called from the machine external handler, once per arriving byte.
///
/// This is moondancer's `USB0_EP_CONTROL` handler: drain the setup FIFO
/// byte-at-a-time before anything else can overwrite it, then enqueue. The
/// drain is here rather than in the task **because the gateware window is
/// here** -- `ep_control.py:128-135` wipes the FIFO on the next SETUP token, so
/// a task that got to it later would find nothing.
/// `true` if the byte was the workload's and must not reach the shell's ring.
pub fn arrival(base: usize, byte: u8) -> bool {
    if ACTIVE.load(Ordering::Relaxed) == 0 {
        return false;
    }
    let at = ticks();

    // The 8-byte setup drain. One MMIO read per byte, as upstream.
    let mut setup = [0u8; SETUP_BYTES];
    let mut i = 0;
    while i < SETUP_BYTES {
        setup[i] = fifo_read(base);
        i += 1;
    }

    let head = HEAD.load(Ordering::Relaxed);
    let next = (head + 1) % SLOTS;
    if next == TAIL.load(Ordering::Acquire) {
        DROPPED.fetch_add(1, Ordering::Relaxed);
        return true;
    }
    QUEUE[head].at.store(at, Ordering::Relaxed);
    // The byte decides which request this is, exactly as `bRequest` would.
    // Both halves of what arrived: the byte that raised the interrupt, and the
    // first byte out of the setup FIFO. `bmRequestType` is what a real one would
    // dispatch on.
    QUEUE[head]
        .kind
        .store((byte as u32) << 8 | setup[0] as u32, Ordering::Relaxed);
    HEAD.store(next, Ordering::Release);
    ARRIVED.fetch_add(1, Ordering::Relaxed);

    #[cfg(feature = "preempt")]
    crate::dispatch::pend(crate::dispatch::TASK_USB);

    true
}

/// One event's worth of device-emulation work.
///
/// The shape is moondancer's, with the multiplier for its second USB port left
/// out: two 1 KiB buffers zeroed, the payload copied twice between staging
/// buffers, and the response pushed out one MMIO store per byte.
fn handle(kind: u32) {
    let base = target::UART_BASES[0];

    // SAFETY: see `Buffers`. `static` rather than a stack frame so the memset
    // cost is the real one.
    let response = unsafe { &mut *BUFFERS.response.get() };
    let stage = unsafe { &mut *BUFFERS.stage.get() };
    let rx = unsafe { &mut *BUFFERS.rx.get() };

    // libgreat zeroes its response buffer per command, twice or three times.
    response.fill(0);
    response[..PACKET].fill(kind as u8);

    // FIFO -> rx_buffer -> Packet.buffer -> response, which is four of the
    // copies `gcp/moondancer.rs:167-186` makes.
    rx.copy_from_slice(&response[..PACKET]);
    stage.copy_from_slice(rx);
    response[..PACKET].copy_from_slice(stage);

    // The write path: one 8-bit MMIO store per byte, upstream's 11.9 cycles.
    let mut i = 0;
    while i < PACKET {
        fifo_write(base, response[i]);
        i += 1;
    }
}

/// Take one event off the queue and do its work. `true` if there was one.
fn step() -> bool {
    let tail = TAIL.load(Ordering::Relaxed);
    if tail == HEAD.load(Ordering::Acquire) {
        return false;
    }
    let at = QUEUE[tail].at.load(Ordering::Relaxed);
    let kind = QUEUE[tail].kind.load(Ordering::Relaxed);
    TAIL.store((tail + 1) % SLOTS, Ordering::Release);

    let before = metrics::minstret();
    handle(kind);
    WORK.fetch_add(metrics::minstret().wrapping_sub(before), Ordering::Relaxed);

    let took = clock::Instant::at(at).elapsed(clock::now());
    WORST.fetch_max(took, Ordering::Relaxed);
    TOTAL.fetch_add(took / 100, Ordering::Relaxed);
    if took > micros(DEADLINE_US) {
        MISSED.fetch_add(1, Ordering::Relaxed);
    }
    COMPLETED.fetch_add(1, Ordering::Relaxed);
    true
}

/// Drain everything queued. The superloop calls this once per turn.
fn drain() {
    while step() {}
}

/// The per-event work as a dispatcher task. Same body; the difference under
/// test is *where it runs*, not what it does.
#[cfg(feature = "preempt")]
pub fn usb_task() {
    drain();
}

/// The deferred job, recorded by the handler.
///
/// The port of `irq::defer_type_c`: the handler masks the source and says a
/// port is waiting. What changes between the two versions is who runs the
/// service afterwards.
pub fn defer(at: u32) {
    let last = DEFERRED_LAST.swap(at, Ordering::Relaxed);
    if last != 0 {
        DEFERRED_GAP.fetch_min(at.wrapping_sub(last), Ordering::Relaxed);
    }
    DEFERRED_AT.store(at, Ordering::Relaxed);
    DEFERRED.fetch_add(1, Ordering::Relaxed);
    DEFERRED_PENDING.store(1, Ordering::Release);
    #[cfg(feature = "preempt")]
    crate::dispatch::pend(crate::dispatch::TASK_TYPE_C);
}

/// Clear the device and re-enable the source: `typec::service`, synthesised.
fn service_body() {
    // A millisecond of I2C, with no I2C. `clock::now()` is `rdtime`, a register
    // read, so this spins on the CLINT and not on a bus.
    let started = clock::now();
    let budget = micros(SERVICE_US);
    while started.elapsed(clock::now()) < budget {
        core::hint::spin_loop();
    }

    let took = clock::Instant::at(DEFERRED_AT.load(Ordering::Relaxed)).elapsed(clock::now());
    DEFERRED_WORST.fetch_max(took, Ordering::Relaxed);

    source::rearm();
}

/// Version A: the deferred job, run from the main loop. `typec::service`'s
/// place in `main.rs`'s turn.
#[cfg(not(feature = "preempt"))]
pub fn service() {
    if DEFERRED_PENDING.swap(0, Ordering::Acquire) == 0 {
        return;
    }
    service_body();
}

/// Version B: the same job as a dispatcher task, run with `mstatus.MIE` set.
#[cfg(feature = "preempt")]
pub fn type_c_task() {
    if DEFERRED_PENDING.swap(0, Ordering::Acquire) == 0 {
        return;
    }
    service_body();
}

/// The interrupt source the deferred job hangs off.
///
/// On the board it is a FUSB302B on its own PLIC line. Under QEMU it is
/// `virt`'s goldfish RTC alarm at 0x101000 on source 11 -- level-sensitive,
/// cleared only by an MMIO write, and schedulable by the guest, which is the
/// same obligation shape and the only spare source `virt` offers without
/// bringing up a virtio driver.
pub mod source {
    use super::*;

    #[cfg(feature = "qemu")]
    mod rtc {
        pub const BASE: usize = 0x0010_1000;
        pub const IRQ: u32 = 11;
        pub const TIME_LOW: usize = 0x00;
        pub const TIME_HIGH: usize = 0x04;
        pub const ALARM_LOW: usize = 0x08;
        pub const ALARM_HIGH: usize = 0x0c;
        pub const IRQ_ENABLED: usize = 0x10;
        pub const CLEAR_INTERRUPT: usize = 0x1c;
    }

    /// Which PLIC source the deferred job's device is on.
    #[cfg(feature = "qemu")]
    pub const SOURCE: u32 = rtc::IRQ;
    #[cfg(not(feature = "qemu"))]
    pub const SOURCE: u32 = if target::TYPE_C_IRQS.is_empty() {
        0
    } else {
        target::TYPE_C_IRQS[0]
    };

    /// How often the deferred job is provoked, in microseconds.
    ///
    /// A plug event is rare; a Type-C sweep is every 50 ms. This is faster than
    /// either on purpose -- the question is what a long job does to a
    /// latency-sensitive one, and at 50 ms almost no event would overlap one.
    /// 5 ms means roughly one in five events meets a service in progress.
    pub const PERIOD_US: u32 = 5_000;

    #[cfg(feature = "qemu")]
    fn reg(offset: usize) -> *mut u32 {
        (rtc::BASE + offset) as *mut u32
    }

    /// Arm the next assertion.
    #[cfg(feature = "qemu")]
    pub fn arm() {
        // SAFETY: the goldfish RTC's window, an ordinary device on `virt`.
        // TIME_LOW must be read first: it latches the high half.
        unsafe {
            let low = core::ptr::read_volatile(reg(rtc::TIME_LOW)) as u64;
            let high = core::ptr::read_volatile(reg(rtc::TIME_HIGH)) as u64;
            // The counter is nanoseconds of virtual time.
            // The next point on an absolute grid, not `now + period`.
            // `src/timer.rs` gives the reason -- reloading from now adds the
            // service duration to every interval -- and here it has a second:
            // the two models under test must see the same schedule, and a
            // schedule that reloads from the end of the service does not give
            // them one.
            let period = PERIOD_US as u64 * 1000;
            let now = (high << 32) | low;
            let at = (now / period + 1) * period;
            core::ptr::write_volatile(reg(rtc::ALARM_HIGH), (at >> 32) as u32);
            core::ptr::write_volatile(reg(rtc::ALARM_LOW), at as u32);
            core::ptr::write_volatile(reg(rtc::IRQ_ENABLED), 1);
        }
    }

    #[cfg(not(feature = "qemu"))]
    pub fn arm() {}

    /// Clear the device, then let the source through again.
    ///
    /// The order is `src/irq.rs`'s and for its reason: the completion goes in
    /// before the disable there, and the re-enable comes after the device is
    /// quiet here.
    pub fn rearm() {
        #[cfg(feature = "qemu")]
        // SAFETY: as above. Writing CLEAR_INTERRUPT lowers the line.
        unsafe {
            core::ptr::write_volatile(reg(rtc::CLEAR_INTERRUPT), 1);
        }
        arm();
        crate::plic::Plic::new(target::PLIC_BASE).enable(SOURCE);
    }

    /// Start provoking the deferred job.
    pub fn start() {
        let plic = crate::plic::Plic::new(target::PLIC_BASE);
        plic.set_priority(SOURCE, 1);
        plic.complete(SOURCE);
        arm();
        plic.enable(SOURCE);
    }

    /// Stop, and leave the line low.
    pub fn stop() {
        crate::plic::Plic::new(target::PLIC_BASE).disable(SOURCE);
        #[cfg(feature = "qemu")]
        // SAFETY: as above.
        unsafe {
            core::ptr::write_volatile(reg(rtc::IRQ_ENABLED), 0);
            core::ptr::write_volatile(reg(rtc::CLEAR_INTERRUPT), 1);
        }
    }
}

/// `usb <n>` -- run the workload until `n` events have been completed.
///
/// Arrivals come from bytes typed at this console, which is what makes them
/// arrive at the host's convenience rather than ours.
pub fn command(uart: &mut Uart, rest: &[u8]) {
    let want = parse(rest).unwrap_or(0);
    if want == 0 {
        let _ = uart.write_str("usage: usb <events>\n");
        return;
    }

    reset();
    let base = target::UART_BASES[0];
    // Loopback for the duration. Nothing may print between here and the
    // restore below: it would come straight back as an arrival.
    // SAFETY: MCR on a 16550. Bit 4 is local loopback and touches nothing else.
    unsafe { core::ptr::write_volatile((base + MCR) as *mut u8, MCR_LOOP) };
    let cycles = metrics::mcycle();
    let instret = metrics::minstret();
    ACTIVE.store(1, Ordering::Release);
    source::start();

    // The main loop, in miniature. Under `preempt` the two jobs below are
    // dispatcher tasks and this loop only waits; without it, this loop IS the
    // scheduler and every latency it produces is the unbounded turn.
    while COMPLETED.load(Ordering::Relaxed) < want {
        #[cfg(not(feature = "preempt"))]
        {
            service();
            drain();
        }
        #[cfg(feature = "preempt")]
        core::hint::spin_loop();
    }

    source::stop();
    ACTIVE.store(0, Ordering::Release);
    // SAFETY: as above. Back on the wire, and the console works again.
    unsafe { core::ptr::write_volatile((base + MCR) as *mut u8, 0) };
    let cycles = metrics::mcycle().wrapping_sub(cycles);
    let instret = metrics::minstret().wrapping_sub(instret);

    report(uart, cycles, instret);
}

fn reset() {
    for slot in &QUEUE {
        slot.at.store(0, Ordering::Relaxed);
        slot.kind.store(0, Ordering::Relaxed);
    }
    HEAD.store(0, Ordering::Relaxed);
    TAIL.store(0, Ordering::Relaxed);
    for counter in [
        &ARRIVED,
        &COMPLETED,
        &DROPPED,
        &WORST,
        &TOTAL,
        &MISSED,
        &WORK,
        &DEFERRED,
        &DEFERRED_WORST,
        &DEFERRED_PENDING,
        &DEFERRED_LAST,
    ] {
        counter.store(0, Ordering::Relaxed);
    }
    DEFERRED_GAP.store(u32::MAX, Ordering::Relaxed);
}

fn report(uart: &mut Uart, cycles: u32, instret: u32) {
    let completed = COMPLETED.load(Ordering::Relaxed).max(1);
    let worst = WORST.load(Ordering::Relaxed);
    let mean = TOTAL.load(Ordering::Relaxed) * 100 / completed;
    let per_us = target::TIME_HZ / 1_000_000;

    let _ = writeln!(
        uart,
        "usb    model {} deadline {} us",
        if cfg!(feature = "preempt") {
            "preempt"
        } else {
            "superloop"
        },
        DEADLINE_US
    );
    let _ = writeln!(
        uart,
        "  events  arrived {} completed {} dropped {} missed {}",
        ARRIVED.load(Ordering::Relaxed),
        COMPLETED.load(Ordering::Relaxed),
        DROPPED.load(Ordering::Relaxed),
        MISSED.load(Ordering::Relaxed)
    );
    let _ = writeln!(
        uart,
        "  latency worst {} us  mean {} us",
        worst / per_us,
        mean / per_us
    );
    let _ = writeln!(
        uart,
        "  defer   asserts {} worst {} us  service {} us  closest {} us",
        DEFERRED.load(Ordering::Relaxed),
        DEFERRED_WORST.load(Ordering::Relaxed) / per_us,
        SERVICE_US,
        DEFERRED_GAP.load(Ordering::Relaxed) / per_us
    );
    let work = WORK.load(Ordering::Relaxed);
    let _ = writeln!(
        uart,
        "  cost    window {} ns  instret {}  work {}  busy {}%",
        cycles,
        instret,
        work,
        work / (instret / 100).max(1)
    );
    let _ = writeln!(
        uart,
        "  event   work {} instr  {} us at 62.5 MHz",
        work / completed,
        work / completed * 16 / 1000
    );
    #[cfg(feature = "preempt")]
    {
        let (dispatches, depth, overhead) = crate::dispatch::stats();
        let _ = writeln!(
            uart,
            "  disp    dispatches {} depth {} overhead {} instr = {} each",
            dispatches,
            depth,
            overhead,
            overhead / dispatches.max(1)
        );
    }
}

fn parse(text: &[u8]) -> Option<u32> {
    let mut value: u32 = 0;
    let mut any = false;
    for &byte in text {
        if byte == b' ' {
            continue;
        }
        let digit = byte.checked_sub(b'0')?;
        if digit > 9 {
            return None;
        }
        value = value.checked_mul(10)?.checked_add(digit as u32)?;
        any = true;
    }
    if any {
        Some(value)
    } else {
        None
    }
}
