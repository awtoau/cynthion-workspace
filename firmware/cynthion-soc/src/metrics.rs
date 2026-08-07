//! Where the cycles go: busy against idle, instructions retired, and the
//! interval the power poll is actually achieving.
//!
//! Three sources, all free:
//!
//!   * `mcycle` and `minstret`, sampled once per turn of the main loop
//!   * a flag any module sets to say that turn did something
//!   * `rdtime` at the moment `power::Monitor::poll` decides to run
//!
//!     what                 from                              printed as
//!     -------------------  --------------------------------  --------------
//!     busy fraction        mcycle, split by the flag          `busy N.NN%`
//!     ipc                  minstret / mcycle                  `ipc 0.NNN`
//!     mean turn            window cycles / window turns       cycles
//!     worst turn           max of one turn's cycles           cycles
//!     poll gap             rdtime between consecutive polls   ms
//!
//! ## "% CPU" here means useful work against spinning
//!
//! There is no scheduler and nothing is ever descheduled, so a utilisation
//! figure taken the usual way reads 100% forever. The split that means something
//! is whether a turn of the main loop did anything or only asked: a turn is
//! **busy** if [`busy`] was called during it, and **idle** otherwise. The idle
//! turns are the ones that polled `irq::pop`, found nothing, and came back.
//!
//! Marked busy by, and only by:
//!
//!     irq::pop              a byte reached the shell
//!     power::Monitor::poll  the 50 ms interval elapsed (via `polled`)
//!     events::drain         a deferred record was printed
//!     typec::service        a controller was cleared
//!     uart::report_errors   a lost byte was reported
//!
//! **The 1 ms tick is deliberately not one of them.** Its cycles land in the
//! idle column, because the question this figure exists to answer is whether an
//! RTOS would fit -- and an RTOS brings its own tick. The amount is not
//! unaccounted for: `time` prints the handler's worst cost and its rate.
//!
//! Cycles spent in the machine external handler ARE counted busy, one turn
//! later: the handler fills a ring, the turn that follows takes the byte out
//! through `irq::pop`, and attribution happens at the next [`turn`] over the
//! whole preceding interval.
//!
//! ## A window, so the arithmetic stays 32 bits
//!
//! `busy`, `cycles`, `instret` and `turns` accumulate together and are halved
//! together when the cycle count reaches [`HALVE_AT`]. Halving preserves every
//! ratio exactly at the instant it happens, so what is reported is a lifetime
//! figure that weights the recent past more -- with a time constant of about 18
//! seconds at 60 MHz.
//!
//! The alternative was a 64-bit accumulator and a 64-bit divide, which on rv32
//! is a call to `__udivdi3`: 912 bytes of compiler-builtins, measured, and the
//! reason `time` prints a raw `mtime` rather than milliseconds. A window makes
//! every divide here a single `divu`.
//!
//! ## Collection does not distort what it measures
//!
//! Per turn: two `csrr`s, one subtraction each, one branch and four stores. A
//! `csrr` is a register read and not a bus transaction. Formatting -- which is
//! the expensive part, and the thing that would swamp the measurement if it ran
//! every turn -- happens once, in [`command`], when someone types `stats`.
//!
//! That command's own cost is charged busy, which is correct: it was typed.
//!
//! ## These CSRs exist on this core, checked rather than assumed
//!
//! `mcycle` (0xb00), `minstret` (0xb02) and their high halves are decoded by the
//! generated core -- `COMB_CSR_PerformanceCounterPlugin_logic_csrFilter` in
//! `VexiiRiscv.v` -- because `--with-rdtime` in `gateware/soc/cpu/cpu.py`
//! adds `zicntr`, and `zicntr` is what instantiates `PerformanceCounterPlugin`.
//! One flag gates `rdtime` and these together, so `info`'s `NO RDTIME` line is
//! the warning for both. An undecoded CSR read traps rather than reading zero.
//!
//! **`mhpmcounter3..6` and `mhpmevent3..6` are REAL, and this comment used to say
//! they were not.** `gateware/soc/cpu/cpu.py:123` passes
//! `--performance-counters 4`, so the plugin allocates four counters with
//! writable event selectors rather than the WARL-zero dummies it produces at
//! `additionalCounterCount = 0`. The stale claim survived the flag being added
//! (#173) and is corrected here because it was the reason nothing outside
//! `src/bench.rs` read one.
//!
//! What they cost is timing, not space: the design stopped closing at 72 MHz
//! when they were added, which is why `SYNC_MHZ` is 60. `src/bench.rs` owns the
//! event ids and the selector writes; `src/sched.rs` reads
//! `STALLED_CYCLES_FRONTEND` and `STALLED_CYCLES_BACKEND` through it for the
//! `rtic` command, which is the #115 comparison this module's `busy` fraction
//! cannot make on its own -- a turn can be busy and stalled at the same time.
//!
//! Only the low 32 bits are read, for the reason `src/clock.rs` gives for
//! `rdtime`: a 64-bit read on rv32 is a retry loop, and every interval here is
//! shorter than the 71.6 s the low word takes to wrap at 60 MHz.

use core::fmt::Write;
use core::sync::atomic::{AtomicU32, Ordering};

use crate::clock::{self, Instant};
use crate::power;
use crate::uart::Uart;

/// Halve the window when its cycle count reaches this.
///
/// 2^30, which is 17.9 s at 60 MHz and the whole of the headroom a `u32` has
/// left for one turn's delta -- a `load` transfer is one turn and can run for
/// minutes. The add saturates rather than wrapping, so the worst a turn that
/// long can do is pin the window at full scale for one halving.
const HALVE_AT: u32 = 1 << 30;

/// `mcycle` at the last [`turn`], and `minstret` beside it.
static LAST_CYCLE: AtomicU32 = AtomicU32::new(0);
static LAST_INSTR: AtomicU32 = AtomicU32::new(0);

/// The window: cycles, of which busy, instructions retired, and turns.
///
/// Halved together, so every ratio between them survives the halving.
static WINDOW_CYCLES: AtomicU32 = AtomicU32::new(0);
static WINDOW_BUSY: AtomicU32 = AtomicU32::new(0);
static WINDOW_INSTR: AtomicU32 = AtomicU32::new(0);
static WINDOW_TURNS: AtomicU32 = AtomicU32::new(0);

/// Longest single turn since boot, in cycles. Never halved: it is a maximum,
/// not a rate, and it is how long the 50 ms poller can be kept waiting.
static WORST_TURN: AtomicU32 = AtomicU32::new(0);

/// Set by [`busy`], consumed by [`turn`]. Nonzero means the turn now ending did
/// something.
static WORKED: AtomicU32 = AtomicU32::new(0);

/// Has [`turn`] run once? The first call has no previous sample to subtract
/// from and establishes the baseline only.
static STARTED: AtomicU32 = AtomicU32::new(0);

/// The power poll: how many, when the last one was, and the worst gap between
/// two, in counter ticks.
static POLLS: AtomicU32 = AtomicU32::new(0);
static LAST_POLL: AtomicU32 = AtomicU32::new(0);
static WORST_GAP: AtomicU32 = AtomicU32::new(0);

/// Relaxed everywhere, and that is not a shortcut.
///
/// Every one of these is written by the main loop and read by a command the main
/// loop dispatches, on one hart with no preemption between them. No interrupt
/// handler touches any of them -- `irq::pop` and `events::drain` are normal
/// context -- so there is nothing to order against and no pairing to get wrong.
/// Atomics rather than plain statics because a `static mut` is banned crate-wide;
/// see `scripts/soc_irq_log_check.py`.
const RELAXED: Ordering = Ordering::Relaxed;

/// The low half of `mcycle`. Public so `src/bench.rs` times with the same
/// counter this module accounts with, rather than reading it a second way.
pub fn mcycle() -> u32 {
    let value: u32;
    // SAFETY: a read of an implemented, side-effect-free machine counter. The
    // module comment names where it is decoded in the generated core.
    unsafe {
        core::arch::asm!("csrr {0}, mcycle", out(reg) value,
                         options(nomem, nostack));
    }
    value
}

/// The low half of `minstret`. Public for the reason `mcycle` is.
pub fn minstret() -> u32 {
    let value: u32;
    // SAFETY: as `mcycle`; the same plugin decodes both.
    unsafe {
        core::arch::asm!("csrr {0}, minstret", out(reg) value,
                         options(nomem, nostack));
    }
    value
}

/// This turn did something. Call from anywhere; costs one store.
pub fn busy() {
    WORKED.store(1, RELAXED);
}

/// Close the turn that just ended and open the next one.
///
/// Called once, at the top of the main loop, so the interval it measures is a
/// whole pass with nothing outside it -- including the epilogue after the last
/// console was polled, which a call at the bottom would lose.
pub fn turn() {
    let cycles = mcycle();
    let instret = minstret();
    let elapsed = cycles.wrapping_sub(LAST_CYCLE.load(RELAXED));
    let retired = instret.wrapping_sub(LAST_INSTR.load(RELAXED));
    LAST_CYCLE.store(cycles, RELAXED);
    LAST_INSTR.store(instret, RELAXED);

    // The first call has nothing to subtract from: the baseline it just stored
    // is the measurement's zero, and the difference against a static's initial
    // zero would be the whole of `mcycle` since reset.
    if STARTED.load(RELAXED) == 0 {
        STARTED.store(1, RELAXED);
        return;
    }

    if WINDOW_CYCLES.load(RELAXED) >= HALVE_AT {
        for counter in [&WINDOW_CYCLES, &WINDOW_BUSY, &WINDOW_INSTR, &WINDOW_TURNS] {
            counter.store(counter.load(RELAXED) >> 1, RELAXED);
        }
    }

    WINDOW_CYCLES.store(WINDOW_CYCLES.load(RELAXED).saturating_add(elapsed), RELAXED);
    WINDOW_INSTR.store(WINDOW_INSTR.load(RELAXED).saturating_add(retired), RELAXED);
    WINDOW_TURNS.store(WINDOW_TURNS.load(RELAXED).saturating_add(1), RELAXED);
    if WORKED.swap(0, RELAXED) != 0 {
        WINDOW_BUSY.store(WINDOW_BUSY.load(RELAXED).saturating_add(elapsed), RELAXED);
    }
    WORST_TURN.fetch_max(elapsed, RELAXED);
}

/// The power monitor's 50 ms interval elapsed and a poll is about to run.
///
/// Called from `power::Monitor::poll` after its interval check and before the
/// bus, so it happens on every target -- `scripts/soc_test.py` drives the same
/// arithmetic under QEMU, where there is no PAC1954 to read.
///
/// Marks the turn busy as well as timing it, because a poll is exactly the kind
/// of work this is counting.
pub fn polled() {
    busy();

    let now = clock::now();
    let polls = POLLS.load(RELAXED);
    POLLS.store(polls.saturating_add(1), RELAXED);
    let previous = Instant::at(LAST_POLL.load(RELAXED));
    LAST_POLL.store(now_ticks(now), RELAXED);

    // The first poll has no predecessor, and `Instant::ZERO` is not one.
    if polls == 0 {
        return;
    }

    // Discarded past the bound `src/power.rs` already reasons about: the low
    // word wraps every 71.6 s at 60 MHz, so a longer gap subtracts to a small
    // number and would report a stopped poller as a healthy one. A poller that
    // stalled that far is what `power`'s age line is for; this is for one that
    // is drifting from 50 ms while still running.
    let gap = previous.elapsed(now);
    if gap < clock::millis(power::AGE_LIMIT_MS) {
        WORST_GAP.fetch_max(gap, RELAXED);
    }
}

/// `(polls, worst gap in ticks)` -- what `stats` prints about the poller, for
/// anything else that wants the same two numbers.
///
/// `src/sched.rs` is the caller: the `rtic` command reports the achieved period
/// beside the release lateness, and the two answer different questions. The gap
/// says what interval the REFRESH cycle is actually running at; the lateness
/// says how long it waited after it was due. A dispatcher can fix the second
/// without moving the first.
pub fn poll_stats() -> (u32, u32) {
    (POLLS.load(RELAXED), WORST_GAP.load(RELAXED))
}

/// An `Instant` back as the number it holds.
///
/// `Instant` is deliberately opaque -- only differences mean anything -- and
/// this is the one place that has to store one in a `static`, where there is no
/// `Instant` to initialise from before `main` runs.
fn now_ticks(at: Instant) -> u32 {
    Instant::ZERO.elapsed(at)
}

/// `numerator / denominator`, scaled to parts per `scale`, in 32-bit arithmetic.
///
/// The denominator is divided down first. Multiplying up would overflow a `u32`
/// at any interesting window size, and doing it in `u64` is the `__udivdi3` call
/// this whole module is arranged to avoid.
///
/// `None` when the denominator is too small to scale by, which is the first few
/// turns after boot and nothing else.
fn parts(numerator: u32, denominator: u32, scale: u32) -> Option<u32> {
    let unit = denominator / scale;
    if unit == 0 {
        return None;
    }
    Some(numerator / unit)
}

/// `stats` -- where the cycles went.
///
/// Three lines, and the reason it is three rather than a table: this is printed
/// on request from a console that shares its wire with JTAG, and every line
/// costs the shell a spin on `LSR.THRE`.
pub fn command(uart: &mut Uart) {
    let cycles = WINDOW_CYCLES.load(RELAXED);
    let busy = WINDOW_BUSY.load(RELAXED);
    let instret = WINDOW_INSTR.load(RELAXED);
    let turns = WINDOW_TURNS.load(RELAXED);

    let _ = write!(uart, "cycles   window {}  ", cycles);
    match parts(busy, cycles, 10_000) {
        // Hundredths of a per cent, because the interesting answer is a small
        // one: a shell with nobody typing at it is legitimately near zero, and
        // a whole per cent of resolution would print every such state as `0%`.
        Some(basis) => {
            let _ = write!(uart, "busy {}.{:02}%", basis / 100, basis % 100);
        }
        None => {
            let _ = write!(uart, "busy --");
        }
    }
    match parts(instret, cycles, 1000) {
        Some(per) => {
            let _ = writeln!(uart, "  ipc {}.{:03}", per / 1000, per % 1000);
        }
        None => {
            let _ = writeln!(uart, "  ipc --");
        }
    }

    // The mean says what a turn normally costs; the worst says how long the
    // poller below can be kept waiting by one. A `load` transfer is one turn,
    // so a large worst is not by itself a fault -- a large worst with nothing
    // having been loaded is.
    let _ = write!(uart, "loop     turns {}", turns);
    match parts(cycles, turns, 1) {
        Some(mean) => {
            let _ = write!(uart, "  mean {} cycles", mean);
        }
        None => {
            let _ = write!(uart, "  mean -- cycles");
        }
    }
    let _ = writeln!(uart, "  worst {} cycles", WORST_TURN.load(RELAXED));

    // The jitter the issue asked for: the largest gap between two consecutive
    // polls, against the interval they are supposed to be at. A poller that has
    // stopped shows up in `power`'s age line; this is what shows one degrading
    // before it does.
    let _ = writeln!(
        uart,
        "poll     every {} ms  polls {}  worst gap {} ms",
        power::INTERVAL_MS,
        POLLS.load(RELAXED),
        clock::to_millis(WORST_GAP.load(RELAXED))
    );
}
