//! The machine external interrupt handler, and the receive rings behind it.
//!
//! Each UART raises a PLIC source when a byte lands; the handler moves it into a
//! per-console ring; the shell takes bytes out. Nothing in this file is
//! target-specific -- the PLIC is standard on the SoC and on QEMU's `-M virt`, so
//! `scripts/soc_test.py` exercises this handler rather than a stand-in.
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
use crate::plic::Plic;
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

/// Which console a PLIC source number belongs to.
///
/// Linear search over at most two entries. A source with no console -- which
/// cannot happen with the map this SoC has, but could after someone adds a timer
/// -- is claimed and completed with no handler run, which is the right thing:
/// the alternative is a claim that is never completed, and that leaves the source
/// silently dead forever.
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

/// Which Type-C port a PLIC source number belongs to.
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
/// Loops until the PLIC has nothing left, rather than servicing one source and
/// returning. Both are correct -- the line is still high on return, so we would
/// simply be re-entered -- but re-entering costs a full trap frame save and
/// restore per source, and looping does not.
#[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
fn machine_external() {
    let plic = Plic::new(target::PLIC_BASE);
    while let Some(source) = plic.claim() {
        if let Some(index) = console_of(source) {
            service(index);
        } else if let Some(port) = type_c_of(source) {
            defer_type_c(&plic, source, port);
        } else if is_i2c(source) {
            service_i2c();
        }
        // The #115 workload's stand-in for a Type-C controller: a second
        // level-sensitive source whose service is too long for a handler. Same
        // deferral, same order, and under `preempt` the same `pend`.
        #[cfg(feature = "workload")]
        if source == crate::workload::source::SOURCE {
            defer_workload(&plic, source);
        }
        // ALWAYS, including for a source with no handler. A claim that is never
        // completed gates that source off for the rest of the session, with
        // `pending` reading zero and the peripheral asserting into the void.
        plic.complete(source);
    }

    // The tail of the handler is where a preemptive dispatcher belongs: every
    // source has been claimed and every task it wants has been pended, so this
    // runs them in priority order with `mstatus.MIE` back on. See
    // `src/dispatch.rs` for why `mepc` has to be saved around that.
    #[cfg(feature = "preempt")]
    crate::dispatch::run();
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
/// over I2C at 80 kHz -- about a millisecond, on the controller the power task
/// is also using -- which is why that one is masked and deferred and this one is
/// not. Same rule, opposite answer, because the rule is about how long the clear
/// takes.
///
/// The driver's own `command()` still spins on `SR.TIP`, which is a different
/// bit and is not touched here. Both writing `IACK` is idempotent, so a handler
/// and a driver acknowledging the same completion is not a race.
fn service_i2c() {
    I2C_INTERRUPTS.store(I2C_INTERRUPTS.load(Ordering::Relaxed).saturating_add(1),
                         Ordering::Relaxed);
    // Through `bus`, which owns every construction of a controller -- see
    // `bus::acknowledge_i2c_interrupt` for why this one operation needs no
    // ownership, and `scripts/soc_i2c_owner_sim.py` for the rule.
    crate::bus::acknowledge_i2c_interrupt();
}

/// How many I2C completions have been delivered.
///
/// Zero here after traffic means the source is masked, `CTR.IEN` is clear, or
/// the gateware is not driving it -- three different faults that used to be
/// indistinguishable because none of them produced a number.
static I2C_INTERRUPTS: AtomicU32 = AtomicU32::new(0);

/// The count, for `irq` and `rtic` to print.
pub fn i2c_interrupts() -> u32 {
    I2C_INTERRUPTS.load(Ordering::Relaxed)
}

/// [`defer_type_c`], for the workload's stand-in source.
///
/// The same three steps in the same order -- complete, disable, record -- and
/// the comment on `defer_type_c` is the reason for all three.
#[cfg(feature = "workload")]
fn defer_workload(plic: &Plic, source: u32) {
    plic.complete(source);
    plic.disable(source);
    crate::workload::defer(crate::clock::Instant::ZERO.elapsed(crate::clock::now()));
}

/// A level-sensitive Type-C line, handled by NOT handling it here.
///
/// Clearing a FUSB302B means reading three read-to-clear registers over I2C --
/// about a millisecond at 80 kHz, on the single controller the power monitor's
/// poll is also using. Doing that here would be a long spin inside an interrupt
/// AND a second master on a peripheral with no lock, either of which is worse
/// than the problem. **That is why the deferral survives per-device sources**:
/// what made a handler unable to clear was the I2C, not the sharing.
///
/// So: mask THIS source, record which port, return. The line stays asserted and
/// the PLIC keeps its pending bit; nothing is lost. `type_c::service` in normal
/// context clears that one device and re-enables that one source.
///
/// What per-device sources changed is the shape of the obligation. A shared
/// level had to be cleared on *every* asserting device before it could be
/// re-enabled, and missing one was a storm that presents as a hung CPU. Here
/// there is one device behind the source, so there is nothing to miss, and a
/// deferred TARGET no longer masks AUX's ability to interrupt.
///
/// `log_from_irq!` records; it does not print. See `src/events.rs` for why a
/// handler that printed would hang this board. The record now carries the port
/// bitmap, which the shared source could not supply without a register read.
fn defer_type_c(plic: &Plic, source: u32, port: usize) {
    // Complete BEFORE masking, and never the other way round.
    //
    // The PLIC ignores a completion for a source the context is not enabled for
    // -- the spec's rule, implemented in `gateware/soc/cpu/plic.py` and
    // warned about in that file's own comment. Disabling first therefore threw
    // the completion away, left `claimed` set for good, and since
    // `pending[i] = sources[i] & ~claimed[i]`, gated the source off permanently.
    //
    // The symptom was one interrupt per port per boot. The device kept asserting
    // -- `typec` showed `int 1` and the gateware `lines 01` -- while the PLIC
    // read `pending 00000000` and no handler ever ran again. Measured on the
    // board: an attach was serviced, the matching detach was not.
    //
    // The caller completes again after this returns, which is harmless: the bit
    // is already clear, and the second write lands while the source is disabled
    // and so is ignored anyway.
    plic.complete(source);
    plic.disable(source);
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

/// Re-enable one port's source, after that device has been cleared.
///
/// Called by normal context and only there, per port, because the mask is per
/// port. If the device is still asserting when this runs the interrupt fires
/// again immediately -- which is correct, there is still work -- and the
/// deferral repeats rather than spinning, because the handler masks again on the
/// way in.
pub fn resume_type_c(port: usize) {
    if let Some(&source) = target::TYPE_C_IRQS.get(port) {
        Plic::new(target::PLIC_BASE).enable(source);
    }
}

/// Deferrals recorded for Type-C port `port`, for the `irq` command.
pub fn type_c_interrupts(port: usize) -> u32 {
    TYPE_C_INTERRUPTS[port].load(Ordering::Relaxed)
}

/// Configure the PLIC and the UARTs, then let interrupts happen.
///
/// Call once, after every `Uart::init()` and after the bootloader has decided
/// not to jump anywhere. Order matters throughout and each step says why.
/// The interrupt CONTROLLER, and nothing that is plugged into it.
///
/// **Machine before peripherals.** This is the PLIC's own configuration -- the
/// threshold, and `mstatus.MIE` -- and it deliberately enables no source. A
/// source is claimed by the peripheral behind it, in [`claim`], once that
/// peripheral is in a state where an interrupt from it would mean something.
///
/// It used to be one function that enabled the consoles and the Type-C
/// controllers from two lists held here. That arrangement is why the I2C source
/// has never fired: it is wired in `gateware/soc/top.py`, and adding it meant
/// remembering to add it to a third list in a file that is not the I2C driver.
/// A peripheral that claims its own source cannot be forgotten by a list it is
/// not in. See #246.
///
/// Safe to call before any peripheral exists: a threshold of 0 with nothing
/// enabled cannot deliver anything.
pub fn init() {
    let plic = Plic::new(target::PLIC_BASE);

    // 0 lets anything with a nonzero priority through. Nothing here is latency
    // critical enough to justify starving the other console.
    plic.set_threshold(0);

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

/// A peripheral claims its PLIC source, now that it is ready to be interrupted.
///
/// Called by the peripheral's own bring-up, not from a list here. The order
/// matters and it is the peripheral that knows it: the FUSB302Bs must have had
/// their interrupt registers cleared first, or the first thing delivered is a
/// plug event from the previous session.
///
/// `priority` is 1 for everything on this board -- the lowest that is not
/// "never". Nothing here is latency critical enough to starve anything else, and
/// equal priorities leave the PLIC's tie-break to decide, which is lowest source
/// number first.
pub fn claim(source: u32, priority: u32) {
    let plic = Plic::new(target::PLIC_BASE);
    plic.set_priority(source, priority);
    plic.enable(source);

    // Release any claim left in flight by a `j _start` reboot. The CPU restarts;
    // the PLIC does not. A source claimed by the previous session and never
    // completed stays gated forever, and the symptom is a console that
    // enumerates, banners, and then ignores every keystroke.
    //
    // Harmless when there is nothing to release: completing a source that was
    // not claimed clears a bit that was already clear.
    plic.complete(source);
}

/// The consoles claim their sources and start asking.
///
/// One call rather than a `claim` per port at the call site, because the
/// receive-interrupt enable in the 16550 has to follow the PLIC enable: a UART
/// asking before the PLIC will deliver is a byte that lands in the ring with
/// nothing scheduled to come and get it.
pub fn claim_consoles() {
    for &source in target::UART_IRQS {
        claim(source, 1);
    }
    // The UARTs start asking only now that there is something to answer them.
    for &base in target::UART_BASES {
        UartRx::new(base).enable_rx_interrupt();
    }
}

/// The Type-C controllers claim their sources.
///
/// **Only valid after `type_c.start`**, which clears both parts' interrupt
/// registers. Before that, the first delivery is a state change from the
/// previous session and the report it produces describes a cable that may no
/// longer be there.
pub fn claim_type_c() {
    for &source in target::TYPE_C_IRQS {
        claim(source, 1);
    }
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
