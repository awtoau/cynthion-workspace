//! What an interrupt handler is allowed to say, and when it gets said.
//!
//! **A handler must never print.** `Uart::put` waits for `LSR.THRE`, so writing
//! to a console from a handler blocks it for as long as the host takes to drain
//! a FIFO. On a level-sensitive shared source that is not a delay, it is a hang:
//! the line is still asserted, the interrupt is taken again the moment the
//! handler returns, and the result presents as a dead CPU with a running clock.
//! This project has mistaken that for dead gateware more than once.
//!
//! ## How the rule is enforced
//!
//! Mostly by ownership, and deliberately so. `main` owns the `Uart` values and
//! passes them down by `&mut`; a handler is a free function with no arguments
//! and nothing to be handed one from. There is no global `print!`, no logging
//! singleton and no `static mut` anywhere in this crate, so the only route to a
//! console from handler context is to construct a `Uart` from a base address --
//! which is why `src/irq.rs` uses [`crate::uart::UartRx`], a receive-only view
//! with no transmit method and no `core::fmt::Write`, and imports nothing else.
//!
//! What ownership cannot do is stop someone importing `Uart` into a handler
//! module tomorrow. Rust's privacy is per-module-tree, so a private item in the
//! crate root is still reachable from every child module; there is no way to
//! make `Uart` unnameable from `irq.rs` without splitting the crate in two. So
//! the remainder is a grep: `scripts/soc_irq_log_check.py` finds every module
//! containing an interrupt handler and fails if one of them mentions `write!`,
//! `writeln!`, `fmt::Write` or `Uart`. It runs as the `irqlog` check in
//! `scripts/check.py`.
//!
//! ## The pattern that makes the rule followable
//!
//! A rule with no alternative gets worked around rather than followed, so a
//! handler CAN log -- it just cannot be the thing that formats and transmits.
//! [`push`] records a code and two words in a ring; the main loop drains it with
//! [`drain`] and does the formatting there, on a console it owns.
//!
//! Formatting is deferred rather than done in the handler because
//! `core::fmt` is not cheap: a `writeln!` with two arguments is a dispatch
//! through `Arguments`, a decimal or hex conversion per value and a call per
//! fragment. A code and two `u32`s is three stores.
//!
//! ## Wait-free, bounded, and lossy on purpose
//!
//! [`push`] never spins and never waits. If the ring is full the record is
//! DROPPED and a counter incremented -- a storm must degrade to lost lines, not
//! to a stalled handler, because a stalled handler on this SoC is the failure
//! this whole file exists to prevent.
//!
//! **The drop count is reported.** [`dropped`] is printed by the `irq` shell
//! command and by [`drain`] itself the first time it notices a loss. Silently
//! losing log lines is how a fault becomes invisible, and a queue that quietly
//! discards under exactly the conditions you most want to see is worse than no
//! queue at all.
//!
//! ## One producer at a time, on one hart
//!
//! The handler is the natural producer and normal context is the natural
//! consumer, which would make this a plain SPSC ring like the receive rings in
//! `src/irq.rs`. But [`log_from_irq!`] is meant to be usable from ANY context --
//! a rule that only works in half the program is a rule people learn to ignore
//! -- and a push from normal context can be interrupted halfway through by a
//! push from a handler.
//!
//! So [`push`] clears `mstatus.MIE` for the length of the copy and restores it.
//! Three or four instructions, no loop, still wait-free. **In a handler this
//! costs nothing and changes nothing**: the hardware already cleared MIE on trap
//! entry and this firmware never sets it inside a handler, so the save/restore
//! writes back the zero it read. The alternative -- a compare-exchange loop to
//! reserve a slot -- would be lock-free rather than wait-free and would still
//! need the payload published separately.
//!
//! Nothing here is target-specific. It is compiled unchanged for the FPGA and
//! for QEMU, and `scripts/soc_test.py` exercises fill, wrap and drop counting
//! through the `log` shell command.

use core::fmt::Write;
use core::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

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

/// A shared Type-C interrupt line asserted. `a` is a bitmap of which
/// controllers were asserting, `b` is unused.
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

/// One record. Three plain words, no allocation, no formatting.
///
/// Parallel atomics rather than a struct behind an `UnsafeCell`, for the same
/// reason `src/irq.rs` uses `[AtomicU8; N]`: it needs no `unsafe impl Sync` and
/// no unsafe block, and on this target it compiles to the same loads and stores
/// either way.
struct Slot {
    code: AtomicU32,
    a: AtomicU32,
    b: AtomicU32,
}

impl Slot {
    const fn new() -> Self {
        Slot {
            code: AtomicU32::new(NONE),
            a: AtomicU32::new(0),
            b: AtomicU32::new(0),
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
        TAIL.store((tail + 1) % RING, Ordering::Release);
        report(uart, code, a, b);
    }

    // Losses, once each. Reported here rather than only by the `irq` command
    // because a drop that nobody types a command to discover is a drop nobody
    // knows about, and the whole reason the ring is lossy is that the
    // alternative was worse -- which is only true if the loss is visible.
    let dropped = DROPPED.load(Ordering::Relaxed);
    let reported = REPORTED.load(Ordering::Relaxed);
    if dropped != reported {
        REPORTED.store(dropped, Ordering::Relaxed);
        let _ = writeln!(uart, "irq log: {} event(s) LOST -- the ring filled \
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
fn report(uart: &mut Uart, code: u32, a: u32, b: u32) {
    match code {
        TYPE_C_INT => {
            let _ = writeln!(uart, "type-c: int asserted, controllers {:02x}", a);
        }
        TYPE_C_FAULT => {
            let _ = writeln!(uart, "type-c: FAULT asserted, controllers {:02x}", a);
        }
        TEST => {
            let _ = writeln!(uart, "log test {}", a);
        }
        _ => {
            let _ = writeln!(uart, "irq log: unknown code {} {:08x} {:08x}",
                             code, a, b);
        }
    }
}
