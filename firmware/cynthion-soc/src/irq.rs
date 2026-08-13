//! The machine external interrupt handler, and the receive rings behind it.
//!
//! Each UART raises a source when a byte lands; the handler moves it into a
//! per-console ring; the shell takes bytes out. Nothing in this file is
//! target-specific -- `src/intc.rs` presents the board's controller and QEMU's
//! PLIC as the same four operations, so `scripts/soc_test.py` exercises this
//! handler rather than a stand-in.
//!
//! ## The livelock this is written to avoid
//!
//! A 16550's interrupt is a LEVEL: high while a byte is waiting and IER.ERBFI is
//! set. **A handler that returns without clearing the condition is re-entered
//! before the interrupted code executes one instruction, forever** -- a board
//! that looks hung, with a running clock and a working peripheral.
//!
//! Two ways to clear it, and this handler uses both:
//!
//!   * drain the byte -- the normal case
//!   * if the ring is full, **mask the source** (`disable_rx_interrupt`) and let
//!     the consumer run. [`pop`] re-arms it.
//!
//! **Never read a byte with nowhere to put it.** RBR pops the FIFO on read, so
//! the room is checked first.
//!
//! ## Nothing in this file may print
//!
//! A handler that writes to a console spins on `LSR.THRE` inside an interrupt,
//! which on a level-sensitive source is a hang that presents as a dead
//! CPU. So the receive path uses [`UartRx`] -- the receive and interrupt-control
//! half of a 16550, with no transmit method and no `core::fmt::Write`, so
//! `write!` here does not compile. What a handler wants to say goes through
//! `src/events.rs` as a code and two words for the main loop to format.
//!
//! Rust's privacy cannot stop a sibling module naming `Uart`, so the rest is
//! enforced by grep: `scripts/soc_irq_log_check.py`, the `irqlog` check.
//!
//! ## Concurrency, on one hart with no preemption
//!
//! One producer (this handler), one consumer (the shell). The handler cannot
//! preempt itself -- `mstatus.MIE` is cleared by hardware on trap entry and this
//! firmware never re-enables it inside a handler -- so the ring needs no lock,
//! only that the two indices are read and written atomically and in order.
//! `AtomicU8`/`AtomicUsize` with acquire/release gives that, and on riscv32imac
//! it compiles to ordinary loads and stores with `fence`s.
//!
//! Interrupt-driven rather than polled because RTIC cannot be layered on a polled
//! main loop; see `docs/architecture.md`.

use core::sync::atomic::{AtomicU32, AtomicU8, AtomicUsize, Ordering};

use riscv::interrupt::Interrupt;

use crate::events;
use crate::intc::Intc;
use crate::target;
use crate::uart::UartRx;
use crate::MAX_CONSOLES;

/// Bytes buffered per console between the handler and the shell.
///
/// Not a latency budget -- the handler runs within microseconds of a byte
/// arriving whatever this is. It is the amount of typing that may arrive while
/// the shell is busy printing a reply, and `put()` is a bounded spin that can
/// last several milliseconds if nothing is draining the transmit side. At USB
/// bulk rates a paste can outrun that, so this is sized for a paste rather than
/// for a keystroke.
///
/// A power of two, so the index wrap is a mask rather than a division; 256 bytes
/// per console, two consoles, is half a kilobyte of the 63 KiB the image region of
/// block RAM gives us.
///
/// Overrunning it is not a failure: the handler masks the source and the bytes
/// wait in the 16550's own FIFO and the transport behind it. Nothing is dropped.
const RING: usize = 256;

/// One console's receive ring, plus the counters the `irq` shell command prints.
struct Ring {
    /// The bytes. `AtomicU8` rather than an `UnsafeCell<[u8; N]>` so this needs
    /// no `unsafe impl Sync` and no unsafe block; on this target the generated
    /// code is the same `lbu`/`sb` either way.
    data: [AtomicU8; RING],
    /// Next index the handler will write. Only the handler advances it.
    head: AtomicUsize,
    /// Next index the shell will read. Only the shell advances it.
    tail: AtomicUsize,
    /// How many times this source has been serviced. Printed by `irq`, and the
    /// only direct evidence from the shell that interrupts are happening at all
    /// rather than something having quietly fallen back to polling.
    interrupts: AtomicU32,
    /// How many times the ring filled and the source had to be masked. Nonzero
    /// is not an error; persistently growing means the shell is not draining.
    stalls: AtomicU32,
}

impl Ring {
    const fn new() -> Self {
        Ring {
            data: [const { AtomicU8::new(0) }; RING],
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
            interrupts: AtomicU32::new(0),
            stalls: AtomicU32::new(0),
        }
    }

    /// Is there room for one more byte?
    ///
    /// The ring holds RING-1 bytes, not RING: head == tail has to mean empty,
    /// so the completely-full state is not representable. One wasted byte buys a
    /// pair of indices that need no separate count and therefore no third thing
    /// for the two sides to agree about.
    fn has_room(&self) -> bool {
        let head = self.head.load(Ordering::Relaxed);
        (head + 1) % RING != self.tail.load(Ordering::Acquire)
    }

    /// Handler side. The caller must have checked [`Ring::has_room`].
    fn push(&self, byte: u8) {
        let head = self.head.load(Ordering::Relaxed);
        self.data[head].store(byte, Ordering::Relaxed);
        // Release: the byte must be visible before the index that publishes it,
        // or the consumer can read the slot before it was written.
        self.head.store((head + 1) % RING, Ordering::Release);
    }

    /// Shell side.
    fn pop(&self) -> Option<u8> {
        let tail = self.tail.load(Ordering::Relaxed);
        // Acquire, pairing with the release in `push`.
        if tail == self.head.load(Ordering::Acquire) {
            return None;
        }
        let byte = self.data[tail].load(Ordering::Relaxed);
        self.tail.store((tail + 1) % RING, Ordering::Release);
        Some(byte)
    }
}

/// One ring per console, indexed the same way `target::UART_BASES` is.
///
/// `static` rather than passed around: the handler takes no arguments and has
/// nowhere to be handed a reference from.
static RINGS: [Ring; MAX_CONSOLES] = [const { Ring::new() }; MAX_CONSOLES];

/// Which console a source number belongs to.
///
/// Linear search over at most two entries. A source with no console is
/// acknowledged with no handler run -- the alternative is a pending bit nothing
/// ever clears, which raises the CPU line forever.
fn console_of(source: u32) -> Option<usize> {
    let mut index = 0;
    while index < target::UART_IRQS.len() {
        if target::UART_IRQS[index] == source {
            return Some(index);
        }
        index += 1;
    }
    None
}

/// Which Type-C port a source number belongs to.
///
/// The same shape as [`console_of`], over the slice that is empty under QEMU --
/// so a target with no Type-C hardware simply never matches and needs no
/// `#[cfg]` here.
fn type_c_of(source: u32) -> Option<usize> {
    let mut index = 0;
    while index < target::TYPE_C_IRQS.len() {
        if target::TYPE_C_IRQS[index] == source {
            return Some(index);
        }
        index += 1;
    }
    None
}

/// Move everything the UART has into the ring, or mask it if there is no room.
fn service(index: usize) {
    let ring = &RINGS[index];
    let mut uart = UartRx::new(target::UART_BASES[index]);

    ring.interrupts.fetch_add(1, Ordering::Relaxed);

    loop {
        // Room BEFORE the read, never after. `Uart::get` pops the 16550's FIFO,
        // so a byte read with nowhere to put it is a byte destroyed -- and in
        // the middle of a `load` transfer that is a corrupt image caught only by
        // the CRC at the end.
        if !ring.has_room() {
            // Stop asking. The bytes stay in the 16550's FIFO and in the
            // transport behind it; the level drops, the handler returns, and
            // the shell finally gets to run. `pop` turns it back on.
            uart.disable_rx_interrupt();
            ring.stalls.fetch_add(1, Ordering::Relaxed);
            return;
        }
        match uart.get() {
            Some(byte) => {
                // One arriving byte is one USB event, for the #115 measurement.
                // The setup-FIFO drain happens HERE, in the handler, because
                // that is where the gateware window is -- see `src/workload.rs`.
                // A byte the workload took does NOT also reach the shell: the
                // ring would fill, the source would be masked, and the run would
                // deadlock waiting for arrivals it had itself switched off.
                #[cfg(feature = "workload")]
                if crate::workload::arrival(target::UART_BASES[index], byte) {
                    continue;
                }
                ring.push(byte);
            }
            None => return,
        }
    }
}

/// The machine external interrupt.
///
/// riscv-rt dispatches here through `_dispatch_core_interrupt` in direct trap
/// mode; the symbol is installed by the attribute, and without it this would
/// reach `DefaultHandler`, which aborts.
///
/// Loops until nothing is ready rather than servicing one source and returning.
/// Both are correct -- the line is still high on return, so we would simply be
/// re-entered -- but re-entering costs a full trap frame save and restore per
/// source. [`Intc::next_ready`] re-reads, so a source that arrives mid-handler
/// is picked up here too.
///
/// It terminates because every branch of [`dispatch`] either acknowledges its
/// source or masks it.
#[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
fn machine_external() {
    let intc = Intc::new(target::INTC_BASE);
    while let Some(source) = intc.next_ready() {
        dispatch(&intc, source);
    }

    // The tail of the handler is where a preemptive dispatcher belongs: every
    // source has been serviced and every task it wants has been pended, so this
    // runs them in priority order with `mstatus.MIE` back on. See
    // `src/dispatch.rs` for why `mepc` has to be saved around that.
    #[cfg(feature = "preempt")]
    crate::dispatch::run();
}

/// One source, serviced or deferred.
///
/// **Everything acknowledges, including a source with no handler.** A pending
/// bit nothing clears raises the CPU line forever, and the loop above would
/// never leave -- so an unknown source is cleared and dropped rather than
/// skipped. The one exception is a deferral, which masks instead: a level whose
/// line is still asserted cannot be acknowledged at all.
fn dispatch(intc: &Intc, source: u32) {
    if let Some(index) = console_of(source) {
        // Drain FIRST. The clear is ignored while the 16550 still asserts, so
        // acknowledging before draining does nothing at all.
        service(index);
        intc.clear(source);
    } else if let Some(port) = type_c_of(source) {
        defer_type_c(intc, source, port);
    } else if is_i2c(source) {
        service_i2c();
        intc.clear(source);
    } else if is_power_alert(source) {
        defer_power_alert(intc, source);
    } else if let Some(port) = fault_of(source) {
        record_fault(intc, source, port);
    } else if is_button(source) {
        record_button(intc, source);
    } else if is_pll_loss(source) {
        record_pll_loss(intc, source);
    } else {
        // The #115 workload's stand-in for a Type-C controller: a second
        // level-sensitive source whose service is too long for a handler.
        #[cfg(feature = "workload")]
        if source == crate::workload::source::SOURCE {
            defer_workload(intc, source);
            return;
        }
        intc.clear(source);
    }
}

/// Is this the PAC1954's limit ALERT?
fn is_power_alert(source: u32) -> bool {
    target::BOARD.is_some() && source == cynthion_soc_pac::base::BOARD_I2C_MUX_POWER_ALERT_IRQ
}

/// The PAC1954's ALERT, handled by NOT handling it here.
///
/// Clearing this means reading `ALERT_STATUS` over I2C -- a three-byte register
/// read at the far end of a mux, on the single controller the REFRESH cycle is
/// also using. Doing that in a handler would be a long spin inside an interrupt
/// AND a second master on a peripheral with one owner.
///
/// Contrast `service_i2c` above, which clears IN the handler: its clear is a
/// single MMIO store that moves nothing on the wire. The rule is how long the
/// clear takes, and these are its two answers.
///
/// **An EDGE source, so no mask.** Acknowledging is unconditional and there is
/// no further edge until the part releases the pin, so the handler is not
/// re-entered while `power::service` does the I2C. A second assertion during
/// that window is a second event and gets a second deferral, which is right.
fn defer_power_alert(intc: &Intc, source: u32) {
    intc.clear(source);
    POWER_ALERT_INTERRUPTS.fetch_add(1, Ordering::Relaxed);
    // Release: the acknowledgement must be visible before the flag that
    // publishes it.
    PENDING_POWER_ALERT.store(1, Ordering::Release);
    // Release the task that can actually clear the part. One MMIO store to
    // `msip`; the flag above stays because `power alert` reports it and because
    // the task reads it to distinguish a real release from a spurious dispatch.
    crate::rtic_app::pend_power_alert();
}

/// Deferrals recorded, for `irq` and `power alert`.
static POWER_ALERT_INTERRUPTS: AtomicU32 = AtomicU32::new(0);

/// Set by the handler, taken by normal context.
static PENDING_POWER_ALERT: AtomicU32 = AtomicU32::new(0);

/// Has the ALERT fired since this was last asked? Clears on read.
pub fn take_power_alert() -> bool {
    PENDING_POWER_ALERT.swap(0, Ordering::Acquire) != 0
}

pub fn power_alert_interrupts() -> u32 {
    POWER_ALERT_INTERRUPTS.load(Ordering::Relaxed)
}

/// Is this the I2C controller's completion source?
///
/// `None` on a target with no board, which is how the emulator gets a handler
/// that cannot mistake one of its own sources for this one.
fn is_i2c(source: u32) -> bool {
    target::BOARD.is_some() && source == cynthion_soc_pac::base::BOARD_I2C_IRQ
}

/// The I2C completion, cleared HERE rather than deferred.
///
/// The opposite treatment from `defer_type_c` one function down, and the reason
/// is the clear and not the peripheral. Source 3 is a level --
/// `irq.eq(irq_flag & ien)` in `peripherals/i2c_master.py` -- and `irq_flag`
/// goes down only on a write of `CR.IACK`. Completing at the PLIC while the
/// peripheral still asserts re-delivers immediately, so something has to clear
/// it before the `plic.complete` at the bottom of the claim loop.
///
/// That clear is ONE MMIO WRITE. A FUSB302B's is three read-to-clear registers
/// over I2C -- ~120 us at the bus's 1 MHz (#272), on the controller the power
/// task is also using -- which is why that one is masked and deferred and this
/// one is not. Same rule, opposite answer, because the rule is about how long
/// the clear takes.
///
/// The driver's own `command()` still spins on `SR.TIP`, which is a different
/// bit and is not touched here. Both writing `IACK` is idempotent, so a handler
/// and a driver acknowledging the same completion is not a race.
fn service_i2c() {
    I2C_INTERRUPTS.store(
        I2C_INTERRUPTS.load(Ordering::Relaxed).saturating_add(1),
        Ordering::Relaxed,
    );
    // Through `bus`, which owns every construction of a controller -- see
    // `bus::acknowledge_i2c_interrupt` for why this one operation needs no
    // ownership, and `scripts/soc_i2c_owner_sim.py` for the rule.
    crate::bus::acknowledge_i2c_interrupt();
}

/// How many I2C completions have been delivered.
///
/// Zero here after traffic means the source is masked, `CTR.IEN` is clear, or
/// the gateware is not driving it -- three faults that no other number separates.
static I2C_INTERRUPTS: AtomicU32 = AtomicU32::new(0);

/// The count, for `irq` and `rtic` to print.
pub fn i2c_interrupts() -> u32 {
    I2C_INTERRUPTS.load(Ordering::Relaxed)
}

/// [`defer_type_c`], for the workload's stand-in source.
///
/// A level, so the same two steps: mask, record. `workload::source::rearm`
/// clears the device, acknowledges and unmasks.
#[cfg(feature = "workload")]
fn defer_workload(intc: &Intc, source: u32) {
    intc.disable(source);
    crate::workload::defer(crate::clock::Instant::ZERO.elapsed(crate::clock::now()));
}

/// A level-sensitive Type-C line, handled by NOT handling it here.
///
/// Clearing a FUSB302B means reading three read-to-clear registers over I2C --
/// ~120 us at the bus's 1 MHz (#272), on the single controller the power
/// monitor's poll is also using. Doing that here would be a long spin inside an
/// AND a second master on a peripheral with no lock, either of which is worse
/// than the problem. **That is why the deferral survives per-device sources**:
/// what made a handler unable to clear was the I2C, not the sharing.
///
/// So: mask THIS source, record which port, return. The line stays asserted and
/// the pending bit stays set; nothing is lost. `type_c::service` in normal
/// context clears that one device, then acknowledges and unmasks that one
/// source.
///
/// Per-device sources are the shape of the obligation: a shared level must be
/// cleared on *every* asserting device before it can be re-enabled, and missing
/// one is a storm that presents as a hung CPU. One device behind the source
/// leaves nothing to miss, and a deferred TARGET does not mask AUX.
///
/// `log_from_irq!` records; it does not print. See `src/events.rs` for why a
/// handler that printed would hang this board. The record carries the port
/// bitmap, which a shared source could not supply without a register read.
fn defer_type_c(intc: &Intc, source: u32, port: usize) {
    // No acknowledgement here: the FUSB302B is still asserting, so the clear
    // would be ignored. `resume_type_c` does it after the I2C clear.
    intc.disable(source);
    TYPE_C_INTERRUPTS[port].fetch_add(1, Ordering::Relaxed);
    // Release: the mask must be visible before the bit that publishes it.
    PENDING_TYPE_C.fetch_or(1 << port, Ordering::Release);
    let _ = crate::log_from_irq!(events::TYPE_C_INT, 1 << port);
}

/// How many Type-C ports this firmware can track.
///
/// Two on the board, none under QEMU. Fixed rather than derived so the counters
/// below are a `static` array; the assertion catches a third controller being
/// wired without this following.
pub const MAX_TYPE_C: usize = 2;

const _: () = assert!(target::TYPE_C_IRQS.len() <= MAX_TYPE_C);

/// Which ports the handler has deferred, one bit each, cleared by
/// [`take_type_c`].
///
/// A bitmap rather than a flag per port because the main loop wants one atomic
/// swap: reading two flags leaves a window in which the second is set after the
/// first was read and cleared, and a port that is pending but not serviced stays
/// masked forever.
static PENDING_TYPE_C: AtomicU32 = AtomicU32::new(0);

/// Interrupts deferred per port, for the `irq` command. Separately visible is
/// the diagnostic half of the per-device sources: a TARGET count that climbs
/// while AUX stays at zero is a fact about the board, not about the firmware.
static TYPE_C_INTERRUPTS: [AtomicU32; MAX_TYPE_C] = [const { AtomicU32::new(0) }; MAX_TYPE_C];

/// Which Type-C ports have fired and not yet been serviced, as a bitmap.
///
/// Consuming, so the main loop services once per assertion rather than on every
/// pass. Each port's source stays masked until [`resume_type_c`] is called for
/// it, which is what makes the sequence safe to be slow.
pub fn take_type_c() -> u32 {
    PENDING_TYPE_C.swap(0, Ordering::Acquire)
}

/// Acknowledge and unmask one port's source, after that device has been cleared.
///
/// Normal context only, per port, because the mask is per port. If the device is
/// still asserting the acknowledgement is ignored and the interrupt fires again
/// on the unmask -- which is correct, there is still work, and the deferral
/// repeats rather than spinning because the handler masks again on the way in.
pub fn resume_type_c(port: usize) {
    if let Some(&source) = target::TYPE_C_IRQS.get(port) {
        let intc = Intc::new(target::INTC_BASE);
        intc.clear(source);
        intc.enable(source);
    }
}

/// Deferrals recorded for Type-C port `port`, for the `irq` command.
pub fn type_c_interrupts(port: usize) -> u32 {
    TYPE_C_INTERRUPTS[port].load(Ordering::Relaxed)
}

/// Configure the controller, then let interrupts happen.
///
/// Call once, after every `Uart::init()` and after the bootloader has decided
/// not to jump anywhere. The interrupt CONTROLLER, and nothing plugged into it:
/// it deliberately enables no source. A source is claimed by the peripheral
/// behind it, once that peripheral is in a state where an interrupt from it
/// would mean something.
///
/// NOT a list of sources held here. A source wired in `gateware/soc/top.py`
/// would then need adding to a list in a file that is not its driver, which is
/// why the I2C source never fired (#246).
///
/// Safe to call before any peripheral exists: nothing is enabled, so nothing
/// can be delivered.
pub fn init() {
    Intc::new(target::INTC_BASE).init();

    // SAFETY: the handler is installed at link time, the trap vector was set by
    // riscv-rt's `_setup_interrupts`, and the rings are `static` and initialised
    // before `main` runs. NO SOURCE IS ENABLED YET, so enabling delivery here
    // cannot deliver anything -- which is what makes it safe to do this before
    // the peripherals rather than after them.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
        riscv::interrupt::enable();
    }
}

/// The Type-C controllers claim their sources.
///
/// **Only valid after `type_c.start`**, which clears both parts' interrupt
/// registers. Before that, the first delivery is a state change from the
/// previous session and the report it produces describes a cable that may no
/// longer be there.
pub fn claim_type_c() {
    let intc = Intc::new(target::INTC_BASE);
    for &source in target::TYPE_C_IRQS {
        // Acknowledge before enabling: a `j _start` reboot leaves the previous
        // session's pending bit set, the CPU being the only thing restarted.
        intc.clear(source);
        intc.enable(source);
    }
}

/// The board's edge sources, claimed together.
///
/// **Not one per driver, unlike every level source**, because none of these has
/// a driver: a fault, a button press and a PLL dropping lock are counted and
/// reported, and nothing is configured to make them happen. There is no
/// peripheral bring-up they could hang off.
///
/// Called after the Type-C bring-up, so an assertion from the previous session
/// is acknowledged rather than delivered.
pub fn claim_edges() {
    let intc = Intc::new(target::INTC_BASE);
    for &source in EDGE_SOURCES {
        intc.clear(source);
        intc.enable(source);
    }
}

/// Every edge source with no driver of its own.
#[cfg(not(feature = "qemu"))]
const EDGE_SOURCES: &[u32] = &[
    cynthion_soc_pac::base::BOARD_I2C_MUX_TARGET_FAULT_IRQ,
    cynthion_soc_pac::base::BOARD_I2C_MUX_AUX_FAULT_IRQ,
    cynthion_soc_pac::base::BOARD_GPIO_BUTTON_IRQ,
    cynthion_soc_pac::base::BOARD_CLOCKS_PLL_LOSS_IRQ,
];

/// `virt` has none of this hardware and nothing raises these.
#[cfg(feature = "qemu")]
const EDGE_SOURCES: &[u32] = &[];

/// Which DPO2036 a source number belongs to, in `fusb302::Port::ALL` order.
fn fault_of(source: u32) -> Option<usize> {
    let mut index = 0;
    while index < FAULT_IRQS.len() {
        if FAULT_IRQS[index] == source {
            return Some(index);
        }
        index += 1;
    }
    None
}

#[cfg(not(feature = "qemu"))]
const FAULT_IRQS: &[u32] = &[
    cynthion_soc_pac::base::BOARD_I2C_MUX_TARGET_FAULT_IRQ,
    cynthion_soc_pac::base::BOARD_I2C_MUX_AUX_FAULT_IRQ,
];

#[cfg(feature = "qemu")]
const FAULT_IRQS: &[u32] = &[];

const _: () = assert!(FAULT_IRQS.len() <= MAX_TYPE_C);

/// A DPO2036 asserted `FAULTB`: an over-voltage on that port's CC/SBU pins.
///
/// Count it, set the port's STICKY flag, release the task. Nothing here tries to
/// make the fault go away: the part protects itself and recovers after 26-38 ms
/// on its own, and the task's job is to record and judge, not to clear the
/// hardware condition. #506, #507.
///
/// **Sticky is the whole point.** The task runs milliseconds later, by which
/// time the part may have recovered and `LINES` reads clean -- so a task that
/// looked at the level would conclude nothing had happened. The flag survives
/// the recovery and only the task clears it.
fn record_fault(intc: &Intc, source: u32, port: usize) {
    intc.clear(source);
    FAULT_INTERRUPTS[port].fetch_add(1, Ordering::Relaxed);
    // Release: the flag must be visible before the pend that publishes it.
    FAULTED.fetch_or(1 << port, Ordering::Release);
    let _ = crate::log_from_irq!(events::TYPE_C_FAULT, 1 << port);
    crate::rtic_app::pend_type_c_fault();
}

static FAULT_INTERRUPTS: [AtomicU32; MAX_TYPE_C] = [const { AtomicU32::new(0) }; MAX_TYPE_C];

/// Which ports have faulted and not yet been accounted for, one bit each.
///
/// Set by the handler, cleared only by [`clear_faulted`] in task context. Not
/// consuming on read: the task reads it, does the work, and clears exactly what
/// it accounted for -- a swap would lose a fault that arrived mid-service.
static FAULTED: AtomicU32 = AtomicU32::new(0);

/// Which ports have faulted since the task last accounted for them.
pub fn faulted() -> u32 {
    FAULTED.load(Ordering::Acquire)
}

/// Task context only, after the fault has been recorded and reported.
pub fn clear_faulted(ports: u32) {
    FAULTED.fetch_and(!ports, Ordering::Release);
}

/// Faults recorded for port `port`, for the `irq` and `typec` commands.
pub fn fault_interrupts(port: usize) -> u32 {
    FAULT_INTERRUPTS[port].load(Ordering::Relaxed)
}

/// Is this the USER button?
fn is_button(source: u32) -> bool {
    target::BOARD.is_some() && source == cynthion_soc_pac::base::BOARD_GPIO_BUTTON_IRQ
}

/// The USER button went down.
///
/// **Not debounced in fabric**, so one press arrives as several interrupts and
/// the count climbs by more than one. Software settles it: a press is a human
/// event and the consumer has milliseconds of slack.
fn record_button(intc: &Intc, source: u32) {
    intc.clear(source);
    let count = BUTTON_INTERRUPTS.fetch_add(1, Ordering::Relaxed) + 1;
    let _ = crate::log_from_irq!(events::BUTTON, count as u64);
}

static BUTTON_INTERRUPTS: AtomicU32 = AtomicU32::new(0);

/// Presses recorded, for `irq`. Bounces included -- see [`record_button`].
pub fn button_interrupts() -> u32 {
    BUTTON_INTERRUPTS.load(Ordering::Relaxed)
}

/// Is this the PLL losing lock?
fn is_pll_loss(source: u32) -> bool {
    target::BOARD.is_some() && source == cynthion_soc_pac::base::BOARD_CLOCKS_PLL_LOSS_IRQ
}

/// The PLL dropped lock.
///
/// Nothing here can fix it -- the CPU runs on the clock that went away, and if
/// `sync` itself stopped this code is not executing. It is for the report: a
/// build that lost lock and recovered looks identical to one that never did,
/// and `ClockMonitor`'s bit only says whether it is locked NOW.
fn record_pll_loss(intc: &Intc, source: u32) {
    intc.clear(source);
    PLL_LOSS_INTERRUPTS.fetch_add(1, Ordering::Relaxed);
    let _ = crate::log_from_irq!(events::PLL_LOSS, 0);
}

static PLL_LOSS_INTERRUPTS: AtomicU32 = AtomicU32::new(0);

/// Losses of lock recorded, for `irq` and `clocks`.
pub fn pll_loss_interrupts() -> u32 {
    PLL_LOSS_INTERRUPTS.load(Ordering::Relaxed)
}

/// Stop interrupts reaching this firmware's handlers -- the external one below
/// and the timer in `src/timer.rs`.
///
/// Called before jumping to a loaded payload, which has its own trap vector, its
/// own idea of where the rings are, and quite possibly neither. Taking an
/// interrupt into a handler belonging to the code you just left is a fault
/// somewhere unrelated, minutes later, with nothing pointing back here.
///
/// Not needed before `j _start`: riscv-rt's `_abs_start` writes `mie` and `mip`
/// to zero as its first act. It is done here anyway, because "the runtime
/// happens to do it" is a fact about a dependency's assembly rather than a
/// property of this firmware.
pub fn shutdown() {
    riscv::interrupt::disable();
    for &base in target::UART_BASES {
        UartRx::new(base).disable_rx_interrupt();
    }
    // The timer is one of this firmware's handlers too, and it is the one a
    // payload is most likely to walk into: `mstatus.MIE` above stops everything
    // while it stays clear, but a payload that enables interrupts for its own
    // purposes inherits a deadline that is already in the past and takes a timer
    // interrupt into a vector that knows nothing about it. `timer::shutdown`
    // pushes the deadline out of reach as well as clearing the bit.
    //
    // Here rather than at each of the three call sites, so the pairing cannot be
    // half-remembered by the fourth.
    crate::timer::shutdown();
}

/// One byte from console `index`, or `None`. Never blocks.
///
/// Replaces `Uart::get()` everywhere in the shell. The UART is still the source
/// of the byte; this is where it arrives.
pub fn pop(index: usize) -> Option<u8> {
    let byte = RINGS[index].pop()?;

    // A byte reached the shell, so this turn of the main loop did something.
    // Here rather than at the call sites because there are three of them and
    // this is the one place all of them go through -- including `load`, whose
    // transfer loop is one very long turn. See `src/metrics.rs`.
    crate::metrics::busy();

    // Re-arm, on every byte, unconditionally.
    //
    // The handler masks this port when the ring fills. Re-enabling here needs no
    // flag, no critical section and no reasoning about who won a race: the ring
    // can only become un-full through this function, so an enable always follows
    // the moment room appears. If the handler masks again immediately afterwards
    // it is because the ring is full again, which is exactly right.
    //
    // The cost is one uncached byte store per byte consumed, which is invisible
    // next to the MMIO read that fetched it.
    UartRx::new(target::UART_BASES[index]).enable_rx_interrupt();

    Some(byte)
}

/// `(interrupts, stalls, buffered)` for console `index`, for the `irq` command.
pub fn stats(index: usize) -> (u32, u32, usize) {
    let ring = &RINGS[index];
    let head = ring.head.load(Ordering::Relaxed);
    let tail = ring.tail.load(Ordering::Relaxed);
    (
        ring.interrupts.load(Ordering::Relaxed),
        ring.stalls.load(Ordering::Relaxed),
        (head + RING - tail) % RING,
    )
}
