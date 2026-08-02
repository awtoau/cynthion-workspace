//! What an interrupt handler is allowed to say, and when it gets said.
//!
//! [`push`] records a code, two words and the time in a ring; the main loop
//! drains it with [`drain`] and formats there, on a console it owns.
//!
//! The time is captured by [`push`] and not by [`drain`], which is the one
//! thing about this ring that is easy to get backwards -- see `src/log.rs`.
//!
//! ## A handler must never print
//!
//! `Uart::put` waits for `LSR.THRE`, so writing to a console from a handler
//! blocks it for as long as the host takes to drain a FIFO. **On a
//! level-sensitive shared source that is not a delay, it is a hang** -- the line
//! is still asserted, the interrupt is retaken the moment the handler returns,
//! and it presents as a dead CPU with a running clock. This project has mistaken
//! that for dead gateware more than once.
//!
//! Formatting is deferred for a second reason: `core::fmt` is not cheap. A
//! `writeln!` with two arguments is a dispatch through `Arguments`, a conversion
//! per value and a call per fragment. A code and two `u32`s is three stores.
//!
//! ## How the rule is enforced
//!
//!   * **Ownership.** `main` owns the `Uart` values and passes them down by
//!     `&mut`; a handler is a free function with nothing to be handed one from.
//!     No global `print!`, no logging singleton, no `static mut` in this crate.
//!     `src/irq.rs` takes [`crate::uart::UartRx`], which has no transmit method
//!     and no `core::fmt::Write`, so `write!` there does not compile.
//!   * **Grep, for the rest.** Rust's privacy is per-module-tree, so a private
//!     item in the crate root is nameable from every child module.
//!     `scripts/soc_irq_log_check.py` fails any module containing a handler that
//!     mentions `write!`, `writeln!`, `fmt::Write` or `Uart`. It is the `irqlog`
//!     check in `scripts/check.py`.
//!
//! A rule with no alternative gets worked around rather than followed, which is
//! why a handler CAN log -- it just cannot be what formats and transmits.
//!
//! ## Wait-free, bounded, lossy on purpose
//!
//!   * [`push`] never spins and never waits. A full ring DROPS the record and
//!     increments a counter: a storm must degrade to lost lines, not to a
//!     stalled handler.
//!   * **The drop count is reported** by the `irq` shell command and by
//!     [`drain`] the first time it notices a loss. A queue that quietly discards
//!     under exactly the conditions you most want to see is worse than no queue.
//!
//! ## One producer at a time, on one hart
//!
//! [`log_from_irq!`] is meant to be usable from ANY context -- a rule that works
//! in half the program is a rule people learn to ignore -- and a push from normal
//! context can be interrupted halfway by a push from a handler. So [`push`]
//! clears `mstatus.MIE` for the length of the copy and restores it: three or four
//! instructions, no loop, still wait-free.
//!
//! **In a handler this costs nothing and changes nothing**: the hardware already
//! cleared MIE on trap entry and this firmware never sets it inside a handler, so
//! the save/restore writes back the zero it read. A compare-exchange loop to
//! reserve a slot would be lock-free rather than wait-free and would still need
//! the payload published separately.
//!
//! Nothing here is target-specific. It is compiled unchanged for the FPGA and
//! for QEMU, and `scripts/soc_test.py` exercises fill, wrap and drop counting
//! through the `log` shell command.

use core::fmt::Write;
use core::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use crate::log;
use crate::uart::Uart;

/// Records held between a handler and the main loop.
///
/// Sixteen, which is 192 bytes of the 32 KiB the shell half of block RAM gives
/// us. The main loop drains on every pass -- microseconds apart -- so this is
/// not a latency budget: it is how many events may arrive in one burst while
/// the loop is inside a command that is printing. A shell command can spin for
/// milliseconds in `Uart::put`, and the interrupt this was written for is a
/// shared Type-C line that can assert twice in a row, so 16 is generous rather
/// than tight. Overrunning it is not a failure, it is a counted loss.
const RING: usize = 16;

/// A code with no arguments worth printing.
pub const NONE: u32 = 0;

/// A Type-C `int` line asserted. `a` is the port's bit -- 1 for TARGET, 2 for
/// AUX -- and `b` is unused.
///
/// Exact rather than a guess, because each controller has its own PLIC source:
/// the handler knows which one from the claim, with no register read. Two
/// records, not one with two bits, if both assert.
pub const TYPE_C_INT: u32 = 1;

/// A Type-C `fault` line asserted. `a` is the bitmap.
///
/// Distinct from `TYPE_C_INT` because it means something different, and it is
/// the one worth noticing without having to read a register first.
pub const TYPE_C_FAULT: u32 = 2;

/// A test record, pushed by the `log` shell command.
///
/// Here rather than in a test file because the ring is `no_std` firmware with no
/// unit-test harness: the only way to exercise fill, wrap and drop counting on
/// the code that actually ships is to make the shipping build able to push one.
/// `a` is the sequence number the command gave it.
pub const TEST: u32 = 3;

/// One record. Four plain words, no allocation, no formatting.
///
/// Parallel atomics rather than a struct behind an `UnsafeCell`, for the same
/// reason `src/irq.rs` uses `[AtomicU8; N]`: it needs no `unsafe impl Sync` and
/// no unsafe block, and on this target it compiles to the same loads and stores
/// either way.
struct Slot {
    code: AtomicU32,
    a: AtomicU32,
    b: AtomicU32,
    /// When this record was PUSHED, in milliseconds since the tick started.
    ///
    /// The fourth word, and the reason it is here rather than being read by
    /// [`drain`]: a record drained a hundred milliseconds after the event would
    /// otherwise report the drain. See the module comment in `src/log.rs` --
    /// the events worth timing are exactly the ones this ring carries.
    at: AtomicU32,
}

impl Slot {
    const fn new() -> Self {
        Slot {
            code: AtomicU32::new(NONE),
            a: AtomicU32::new(0),
            b: AtomicU32::new(0),
            at: AtomicU32::new(0),
        }
    }
}

static SLOTS: [Slot; RING] = [const { Slot::new() }; RING];

/// Next slot to be written. Advanced only by [`push`].
static HEAD: AtomicUsize = AtomicUsize::new(0);
/// Next slot to be read. Advanced only by [`drain`].
static TAIL: AtomicUsize = AtomicUsize::new(0);
/// Records lost because the ring was full. Never reset.
static DROPPED: AtomicU32 = AtomicU32::new(0);
/// How many drops the console has already been told about, so a loss is
/// reported once rather than on every pass of the main loop.
static REPORTED: AtomicU32 = AtomicU32::new(0);

/// Record an event. Safe from any context, including an interrupt handler.
///
/// Never blocks, never allocates, never formats and never waits. Returns
/// `false` if the ring was full and the record was dropped, which callers are
/// free to ignore -- the count is reported by [`drain`] either way.
pub fn push(code: u32, a: u32, b: u32) -> bool {
    // Interrupts off for the length of the copy. See the module comment: this
    // is what lets normal context push into the same ring a handler pushes
    // into, and it is a no-op when called from a handler.
    //
    // `riscv::interrupt::free` reads `mstatus`, clears MIE, runs the closure and
    // restores what it read -- so it cannot enable interrupts that were off.
    riscv::interrupt::free(|| {
        let head = HEAD.load(Ordering::Relaxed);
        let next = (head + 1) % RING;
        // The ring holds RING-1 records, not RING: head == tail must mean
        // empty, so the completely-full state is not representable. One wasted
        // slot buys a pair of indices with no separate count for the two sides
        // to disagree about -- the same trade `src/irq.rs` makes.
        if next == TAIL.load(Ordering::Acquire) {
            DROPPED.fetch_add(1, Ordering::Relaxed);
            return false;
        }
        SLOTS[head].code.store(code, Ordering::Relaxed);
        SLOTS[head].a.store(a, Ordering::Relaxed);
        SLOTS[head].b.store(b, Ordering::Relaxed);
        // HERE, not in `drain`. One atomic load of a counter the tick handler
        // maintains -- no MMIO, no division, nothing that could make a push
        // expensive enough to think about.
        SLOTS[head].at.store(log::now().millis(), Ordering::Relaxed);
        // Release: the payload must be visible before the index that publishes
        // it, or the consumer can read a slot before it was written.
        HEAD.store(next, Ordering::Release);
        true
    })
}

/// Record an event from any context, including an interrupt handler.
///
/// The macro exists so that the compile error a `writeln!` in a handler
/// produces has an obvious answer. It is a thin wrapper over [`push`]; the
/// value is the name.
#[macro_export]
macro_rules! log_from_irq {
    ($code:expr) => { $crate::events::push($code, 0, 0) };
    ($code:expr, $a:expr) => { $crate::events::push($code, $a, 0) };
    ($code:expr, $a:expr, $b:expr) => { $crate::events::push($code, $a, $b) };
}

/// How many records have been dropped since boot.
pub fn dropped() -> u32 {
    DROPPED.load(Ordering::Relaxed)
}

/// How many records are waiting to be printed.
pub fn waiting() -> usize {
    let head = HEAD.load(Ordering::Relaxed);
    let tail = TAIL.load(Ordering::Relaxed);
    (head + RING - tail) % RING
}

/// Print everything waiting, and any losses since the last time.
///
/// Called from the main loop with the primary console. This is where the
/// formatting happens, in normal context, on a `Uart` the caller owns -- which
/// is the whole arrangement: a handler cannot reach a `Uart`, and this function
/// cannot be called without one.
pub fn drain(uart: &mut Uart) {
    loop {
        let tail = TAIL.load(Ordering::Relaxed);
        // Acquire, pairing with the release in `push`.
        if tail == HEAD.load(Ordering::Acquire) {
            break;
        }
        let code = SLOTS[tail].code.load(Ordering::Relaxed);
        let a = SLOTS[tail].a.load(Ordering::Relaxed);
        let b = SLOTS[tail].b.load(Ordering::Relaxed);
        let at = log::Stamp::at(SLOTS[tail].at.load(Ordering::Relaxed));
        TAIL.store((tail + 1) % RING, Ordering::Release);
        report(uart, at, code, a, b);
    }

    // Losses, once each. Reported here rather than only by the `irq` command
    // because a drop that nobody types a command to discover is a drop nobody
    // knows about, and the whole reason the ring is lossy is that the
    // alternative was worse -- which is only true if the loss is visible.
    let dropped = DROPPED.load(Ordering::Relaxed);
    let reported = REPORTED.load(Ordering::Relaxed);
    if dropped != reported {
        REPORTED.store(dropped, Ordering::Relaxed);
        // Stamped NOW rather than at a push, because this line is not about a
        // record -- it is about the ring, and the moment it describes is the
        // moment the loss was noticed. The lost records' own times are the gap
        // in the column either side of it.
        crate::log!(uart, "irq log: {} event(s) LOST -- the ring filled \
                           faster than the shell drained it",
                    dropped - reported);
    }
}

/// One line for one record.
///
/// All the formatting for every event code lives here, in one `match`, in
/// normal context. That is deliberate: a code whose arguments are formatted at
/// the push site would be formatting inside a handler again, one indirection
/// further away where nobody would notice.
///
/// `at` is when the record was PUSHED. Every arm uses it and none of them reads
/// the clock; see `src/log.rs` for why that distinction is the point of the
/// field.
fn report(uart: &mut Uart, at: log::Stamp, code: u32, a: u32, b: u32) {
    match code {
        TYPE_C_INT => {
            crate::log_at!(uart, at, "type-c: int asserted, port {:02x}", a);
        }
        TYPE_C_FAULT => {
            crate::log_at!(uart, at, "type-c: FAULT asserted, controllers {:02x}", a);
        }
        TEST => {
            crate::log_at!(uart, at, "log test {}", a);
        }
        _ => {
            crate::log_at!(uart, at, "irq log: unknown code {} {:08x} {:08x}",
                           code, a, b);
        }
    }
}
