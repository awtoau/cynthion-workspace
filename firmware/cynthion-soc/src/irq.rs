//! The machine external interrupt handler, and the receive rings behind it.
//!
//! This is where the console stopped being polled. The shell used to sit in a
//! loop reading LSR on every UART in turn; now each UART raises a PLIC source
//! when a byte lands, the handler below moves it into a ring, and the shell
//! takes bytes out of the ring. The shell's behaviour is unchanged from the
//! outside -- same prompt, same echo, same idle re-banner.
//!
//! ## Why bother, when polling worked
//!
//! Because RTIC cannot be layered on a polled main loop. RTIC's model is that
//! hardware raises an interrupt, the runtime dispatches to a task, and priorities
//! decide who preempts whom; every part of that needs an interrupt controller
//! reporting which source fired. That is what `src/plic.rs` and
//! `ecp5-test/riscv/vexii_plic.py` now provide, and this file is the minimum
//! that proves the path works end to end.
//!
//! ## Nothing in this file may print
//!
//! This is the module the machine external handler lives in, and a handler that
//! writes to a console spins on `LSR.THRE` inside an interrupt -- on a
//! level-sensitive shared source that is a hang, and it presents as a dead CPU.
//! So the receive path uses [`UartRx`], which is the receive and
//! interrupt-control half of a 16550 and has no transmit method and no
//! `core::fmt::Write`: `write!` here does not compile. What a handler says
//! instead goes through `src/events.rs`, which records a code and two words and
//! lets the main loop do the formatting.
//!
//! `scripts/soc_irq_log_check.py` (the `irqlog` check) enforces the rest by
//! grep, because Rust's privacy cannot stop a sibling module naming `Uart`.
//!
//! ## No `#[cfg]` here either
//!
//! Nothing in this file is target-specific. The PLIC is standard on both the SoC
//! and QEMU's `-M virt`, so the only differences -- where it is, and which source
//! number each UART is on -- are constants in `src/target.rs`, exactly as the
//! UART base addresses already were. `scripts/soc_test.py` therefore exercises
//! this handler, not a stand-in for it.
//!
//! ## The livelock this is written to avoid
//!
//! A 16550's interrupt is a LEVEL: high for as long as a byte is waiting and
//! IER.ERBFI is set. If the handler returns without clearing the condition, the
//! interrupt is taken again before the interrupted code executes a single
//! instruction, forever. The CPU is then fully occupied and the board looks
//! hung, with a clock that is running and a peripheral that is working.
//!
//! There are exactly two ways for the handler to clear the condition, and it
//! uses both:
//!
//!   * drain the byte -- which is the normal case, and
//!   * if the ring has no room, **mask the source** (`disable_rx_interrupt`) and
//!     let the consumer run. [`pop`] re-arms it.
//!
//! What it must never do is read a byte it has nowhere to put. That byte is gone
//! from the FIFO the instant RBR is read, so the room is checked first.
//!
//! ## Concurrency, on one hart with no preemption
//!
//! One producer (this handler) and one consumer (the shell). The handler cannot
//! preempt itself -- `mstatus.MIE` is cleared by hardware on trap entry and this
//! firmware never re-enables it inside a handler -- so the ring needs no lock,
//! only that the two indices are read and written atomically and in order.
//! `AtomicU8`/`AtomicUsize` with acquire/release gives that, and on riscv32imac
//! it compiles to ordinary loads and stores with `fence`s.

use core::sync::atomic::{AtomicBool, AtomicU8, AtomicU32, AtomicUsize,
                         Ordering};

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
/// per console, two consoles, is half a kilobyte of the 32 KiB the shell half of
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
            Some(byte) => ring.push(byte),
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
        } else if Some(source) == target::TYPE_C_IRQ {
            defer_type_c(&plic);
        }
        // ALWAYS, including for a source with no handler. A claim that is never
        // completed gates that source off for the rest of the session, with
        // `pending` reading zero and the peripheral asserting into the void.
        plic.complete(source);
    }
}

/// A shared, level-sensitive Type-C line, handled by NOT handling it here.
///
/// The two FUSB302Bs' `int` lines are OR-ed into one source, and clearing one
/// means reading three read-to-clear registers over I2C -- about a millisecond
/// at 80 kHz, on the single controller the power monitor's poll is also using.
/// Doing that here would be a long spin inside an interrupt AND a second master
/// on a peripheral with no lock, either of which is worse than the problem.
///
/// So: mask the source, record the event, return. The line stays asserted and
/// the PLIC keeps its pending bit; nothing is lost. `type_c::service` in normal
/// context clears every asserting device and re-enables the source, which is
/// where the "clear EVERY device" rule in `docs/chips/fusb302b-type-c.md`
/// actually lives. The storm that rule warns about cannot happen, because the
/// source is off for the entire window in which the line is still high.
///
/// `log_from_irq!` records; it does not print. See `src/events.rs` for why a
/// handler that printed would hang this board.
fn defer_type_c(plic: &Plic) {
    plic.disable(TYPE_C_SOURCE.load(Ordering::Relaxed));
    PENDING_TYPE_C.store(true, Ordering::Release);
    // The mux's LINES register would say which controller asserted, but reading
    // it needs a base address this module has no business knowing and the
    // answer is re-read by the service routine anyway -- between here and there
    // the other controller may assert too, and acting on the older picture is
    // exactly how one gets left set.
    let _ = crate::log_from_irq!(events::TYPE_C_INT);
}

/// Set by the handler, cleared by [`take_type_c`]. `Release`/`Acquire` so the
/// mask is visible before the flag that publishes it.
static PENDING_TYPE_C: AtomicBool = AtomicBool::new(false);

/// The source number the handler masks, so `defer_type_c` needs no target
/// import at the point of use. Written once by `init`.
static TYPE_C_SOURCE: AtomicU32 = AtomicU32::new(0);

/// Has the Type-C source fired and not yet been serviced?
///
/// Consuming, so the main loop services once per assertion rather than on every
/// pass. The source stays masked until [`resume_type_c`] is called, which is
/// what makes the sequence safe to be slow.
pub fn take_type_c() -> bool {
    PENDING_TYPE_C.swap(false, Ordering::Acquire)
}

/// Re-enable the Type-C source, after every asserting device has been cleared.
///
/// Called by normal context and only there. If a device is still asserting when
/// this runs, the interrupt fires again immediately -- which is correct, there
/// is still work -- and the deferral repeats rather than spinning, because the
/// handler masks again on the way in.
pub fn resume_type_c() {
    let source = TYPE_C_SOURCE.load(Ordering::Relaxed);
    if source != 0 {
        Plic::new(target::PLIC_BASE).enable(source);
    }
}

/// Configure the PLIC and the UARTs, then let interrupts happen.
///
/// Call once, after every `Uart::init()` and after the bootloader has decided
/// not to jump anywhere. Order matters throughout and each step says why.
pub fn init() {
    let plic = Plic::new(target::PLIC_BASE);

    // 0 lets anything with a nonzero priority through. Nothing here is latency
    // critical enough to justify starving the other console.
    plic.set_threshold(0);

    for &source in target::UART_IRQS {
        // Priority 1, the lowest that is not "never". Equal for both consoles,
        // so the PLIC's tie-break decides -- lowest source number first, which
        // is the USB console. See IRQ_CONSOLE in vexii_hello_soc.py.
        plic.set_priority(source, 1);
        plic.enable(source);

        // Release any claim left in flight by a `j _start` reboot. The CPU
        // restarts; the PLIC does not. A source claimed by the previous session
        // and never completed stays gated forever, and the symptom is a console
        // that enumerates, banners, and then ignores every keystroke.
        //
        // Harmless when there is nothing to release: completing a source that
        // was not claimed clears a bit that was already clear.
        plic.complete(source);
    }

    // The Type-C source, if this target has one. Same priority as the consoles:
    // nothing here is more urgent than a keystroke, and a plug event that waits
    // a few microseconds is a plug event nobody notices waited.
    if let Some(source) = target::TYPE_C_IRQ {
        TYPE_C_SOURCE.store(source, Ordering::Relaxed);
        plic.set_priority(source, 1);
        plic.enable(source);
        plic.complete(source);
    }

    // The UARTs start asking only now that there is something to answer them.
    for &base in target::UART_BASES {
        UartRx::new(base).enable_rx_interrupt();
    }

    // SAFETY: the handler above is installed at link time, the trap vector was
    // set by riscv-rt's `_setup_interrupts`, and every source is configured. The
    // rings are `static` and initialised before `main` runs, so there is no
    // window in which the handler can touch uninitialised state.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
        riscv::interrupt::enable();
    }
}

/// Stop interrupts reaching this firmware's handler.
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
}

/// One byte from console `index`, or `None`. Never blocks.
///
/// Replaces `Uart::get()` everywhere in the shell. The UART is still the source
/// of the byte; this is where it arrives.
pub fn pop(index: usize) -> Option<u8> {
    let byte = RINGS[index].pop()?;

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
