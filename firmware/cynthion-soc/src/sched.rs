//! Which dispatcher this image was built with, and what it is achieving.
//!
//! Issue #245, under #115. The firmware has two concurrency models and the
//! shipping one is chosen at compile time:
//!
//!     default            the superloop in `src/main.rs`
//!     --features rtic    the `#[rtic::app]` in `src/rtic_app.rs`
//!
//! **The default is the superloop and must stay that way.** The hardware image
//! is the product, and a feature that had to be remembered on every build would
//! eventually be forgotten on one -- the same argument `Cargo.toml` makes for
//! the `qemu` gate.
//!
//! ## Why this module exists at all
//!
//! Because "is RTIC better" is a question about the SHIPPING firmware and there
//! was no way to ask it. `docs/rtic.md`'s figures come from
//! `src/workload.rs`, a synthetic load built to be measured, and it says so at
//! the top. #115 asks for the real thing instead, and names the three
//! instruments that already exist and go unread:
//!
//!     metrics::polled()        the REFRESH cycle's achieved interval
//!     the PLIC's counters      irqs, stalls, buffered and lost, per source
//!     mhpmcounter3/4           STALLED_CYCLES_FRONTEND and _BACKEND
//!
//! The `rtic` command below prints all three, in the same shape under both
//! models, so the two transcripts subtract. It reports the model it was built
//! with first, because a table that does not say which dispatcher produced it is
//! a table nobody can compare against another.
//!
//! ## Gap and lateness are different questions
//!
//! Both are printed and neither substitutes for the other.
//!
//! **Gap** is the interval between two consecutive runs, from
//! `src/metrics.rs`. It says what period the REFRESH cycle is actually
//! achieving. A poller that is late every time but consistently late has a
//! perfect gap.
//!
//! **Lateness** is how long a run waited after it became due, recorded by
//! [`released`]. It is what a dispatcher can fix. Under the superloop a run
//! becomes due one interval after the last one and waits for the loop to come
//! round; under RTIC it becomes due on the 1 ms tick grid and waits for the SLIC
//! to dispatch it. Same definition, two schedulers, so the numbers subtract.
//!
//! ## "No better" is a result
//!
//! #245 says so explicitly and it is worth repeating where the numbers are
//! produced: this command exists to record an answer, not to produce a
//! favourable one. If the shell's `&mut Devices` is held across a long command
//! then the RTIC task blocks on the same resource the superloop's turn does, and
//! the lateness will not move. That is a fact about where the sharing is, and it
//! is more useful than a tuned figure.

use core::fmt::Write;
use core::sync::atomic::{AtomicU32, Ordering};

use crate::bench;
use crate::clock;
use crate::events;
use crate::fusb302;
use crate::irq;
use crate::metrics;
use crate::plic::Plic;
use crate::power;
use crate::target;
use crate::uart::{self, Uart};

/// Which dispatcher this image was built with.
///
/// `cfg!` rather than `#[cfg]`, so both spellings are type-checked in both
/// builds and the string cannot go stale on the branch nobody is compiling.
pub const MODEL: &str = if cfg!(feature = "rtic") {
    "rtic"
} else {
    "superloop"
};

/// The PAC1954's REFRESH cycle: `power::Monitor::service`, every
/// [`power::INTERVAL_MS`].
pub const POWER: usize = 0;

/// How many tasks this module accounts for.
///
/// One, and that is the whole of #245: the power monitor is the FIRST peripheral
/// onto RTIC, chosen because it already measures its own jitter. The consoles
/// are still the machine-external handler filling a ring under both models --
/// unchanged, deliberately, so that this measurement has exactly one variable in
/// it. #247 sweeps the rest.
const TASKS: usize = 1;

/// What each task is, for the report. Parallel to the ids above.
struct Task {
    name: &'static str,
    /// The period it is supposed to run at, in milliseconds.
    period_ms: u32,
    /// Its RTIC priority. Meaningless under the superloop, which has no
    /// priorities at all, and printed as `-` there rather than as a number that
    /// would read as a property of a build that does not have one.
    priority: u8,
}

const TABLE: [Task; TASKS] = [Task {
    name: "power_refresh",
    period_ms: power::INTERVAL_MS,
    priority: POWER_PRIORITY,
}];

/// The RTIC priority the power task runs at.
///
/// 1, the lowest that is not idle. A REFRESH cycle is 50 ms of period and a
/// couple of milliseconds of I2C; nothing about it is more urgent than a
/// keystroke, and the console's bytes are collected by the machine-external
/// handler at hardware priority anyway -- above every SLIC source, so this
/// number cannot starve them.
///
/// Here rather than in `src/rtic_app.rs` so that the superloop build can print
/// the priority the RTIC build would use without compiling the app.
pub const POWER_PRIORITY: u8 = 1;

/// How many times each task has run.
static RUNS: [AtomicU32; TASKS] = [const { AtomicU32::new(0) }; TASKS];

/// How many times each task has been released and not yet run.
///
/// Under RTIC this is the tick pending the task; under the superloop nothing
/// pends anything, so it stays equal to [`RUNS`] and the difference is the
/// diagnostic: a pend count that outruns the run count is a task being released
/// again before it got to run, which the SLIC coalesces into one dispatch.
static RELEASES: [AtomicU32; TASKS] = [const { AtomicU32::new(0) }; TASKS];

/// The worst lateness seen, in counter ticks. A maximum, never halved.
static WORST_LATE: [AtomicU32; TASKS] = [const { AtomicU32::new(0) }; TASKS];

/// Lateness summed, and the count it is summed over, for a mean.
///
/// A worst case alone cannot tell a dispatcher that is usually prompt and
/// occasionally terrible from one that is always mediocre, and those are
/// different faults. Saturating rather than wrapping: at 60 MHz and 50 ms a
/// `u32` of summed ticks lasts about an hour of pathological lateness, and
/// pinning at full scale is a visibly wrong number rather than a plausible one.
static TOTAL_LATE: [AtomicU32; TASKS] = [const { AtomicU32::new(0) }; TASKS];

/// Relaxed everywhere, for the reason `src/metrics.rs` gives at length: one
/// hart, and the only writer that is not the main loop is a task the main loop
/// cannot observe mid-update. Atomics rather than `static mut`, which is banned
/// crate-wide -- see `scripts/soc_irq_log_check.py`.
const RELAXED: Ordering = Ordering::Relaxed;

/// Point the CPU's performance counters at the events worth watching.
///
/// Called once from the boot path, under both models. It is two CSR writes and
/// it must happen before anything reads them, because the counters free-run from
/// reset and a selector written late means the count so far was of a different
/// event.
///
/// `src/bench.rs` owns the selection: it writes all four, and `bench hyperram`
/// re-selects the same four before its own walk, so the two commands cannot
/// disagree about what counter 3 is counting.
pub fn init() {
    bench::hpm::select();
}

/// A task became due. Called by whatever decides that -- the 1 ms tick under
/// RTIC, and nothing under the superloop, where the decision and the run are the
/// same instant.
pub fn pended(task: usize) {
    RELEASES[task].store(RELEASES[task].load(RELAXED).saturating_add(1), RELAXED);
}

/// A task is running now, `late` ticks after it was due.
///
/// Called from the task body under both models, with the SAME definition of
/// late, which is the only reason the two transcripts can be subtracted. See the
/// module comment.
pub fn released(task: usize, late: u32) {
    RUNS[task].store(RUNS[task].load(RELAXED).saturating_add(1), RELAXED);

    // Discarded past a bound, exactly as `metrics::polled` discards a gap past
    // one, and for the same reason: the counter's low word wraps every 71.6 s at
    // 60 MHz, so a subtraction across a wrap yields a small number and would
    // report a stalled dispatcher as a prompt one. A task that really was that
    // late is a task that stopped, and `power`'s age line is what says so.
    if late >= clock::millis(power::AGE_LIMIT_MS) {
        return;
    }
    WORST_LATE[task].fetch_max(late, RELAXED);
    TOTAL_LATE[task].store(TOTAL_LATE[task].load(RELAXED).saturating_add(late), RELAXED);

    // Under the superloop nothing pends, so the release IS the run. Counted here
    // rather than left at zero, so the `pends` column means the same thing in
    // both transcripts instead of being blank in one of them.
    if !cfg!(feature = "rtic") {
        pended(task);
    }
}

/// The PLIC and its five sources, as the `irq` command prints them.
///
/// Extracted so `irq` and `rtic` render the same lines from one place. They ask
/// different questions of the same counters -- `irq` asks whether the console is
/// interrupt-driven, `rtic` asks what the dispatcher cost -- and two renderers
/// would eventually answer them in two formats that could not be diffed.
///
/// Every register read here is side-effect free. In particular it does NOT read
/// the claim register: that would take an interrupt away from the handler and
/// never complete it, killing the console from a diagnostic command. See
/// `Plic::claim`.
pub fn sources(uart: &mut Uart) {
    let plic = Plic::new(target::PLIC_BASE);
    let _ = writeln!(
        uart,
        "plic  @{:08x} pending {:08x} enabled {:08x}",
        target::PLIC_BASE,
        plic.pending(),
        plic.enabled()
    );
    for console in 0..target::UART_BASES.len() {
        let (interrupts, stalls, buffered) = irq::stats(console);
        // `lost` counts LSR reads that found an error bit set -- an overrun or a
        // framing error, both of which mean input that never reached the shell.
        // Zero is the only good value; a number that climbs while nothing is
        // typed is a noisy line.
        let _ = writeln!(
            uart,
            "  {} src {} irqs {} stalls {} buffered {} lost {}",
            console,
            target::UART_IRQS[console],
            interrupts,
            stalls,
            buffered,
            uart::error_reads(console)
        );
    }
    // The Type-C sources, one per FUSB302B rather than one for both.
    //
    // Separately visible is half the point of giving them a source each: a
    // TARGET count that climbs while AUX stays at zero says which connector
    // something is happening on, and a shared source could only ever have shown
    // the sum. The `enabled` word above is the other half -- a port whose bit is
    // clear there is one the handler has masked and `typec` has not finished
    // servicing.
    for (port, &source) in target::TYPE_C_IRQS.iter().enumerate() {
        let _ = writeln!(
            uart,
            "  type-c {:6} src {} irqs {}",
            fusb302::Port::ALL[port].name(),
            source,
            irq::type_c_interrupts(port)
        );
    }
}

/// `rtic` -- which dispatcher this is, and whether it is doing better.
///
/// Four blocks, and each one is a thing #115 asked for:
///
///     model    which dispatcher, from cfg!(feature = "rtic")
///     task     runs, achieved period, and lateness against the period
///     plic     the per-source counters, unchanged from `irq`
///     stalls   mhpmcounter3/4 against mcycle
///
/// Printed on request and never on a timer: formatting is the expensive part
/// and this console shares its wire with JTAG.
pub fn command(uart: &mut Uart) {
    let _ = writeln!(
        uart,
        "model    {}  ({} task{})",
        MODEL,
        TASKS,
        if TASKS == 1 { "" } else { "s" }
    );

    // The achieved period comes from `src/metrics.rs`, which counts it inside
    // `power::Monitor::service` -- the body BOTH dispatchers call. So this line
    // is measuring the same code under both models and only the arrival of it
    // differs, which is the comparison.
    let (polls, worst_gap) = metrics::poll_stats();
    for (index, task) in TABLE.iter().enumerate() {
        let runs = RUNS[index].load(RELAXED);
        let releases = RELEASES[index].load(RELAXED);
        let worst = WORST_LATE[index].load(RELAXED);
        let total = TOTAL_LATE[index].load(RELAXED);

        let _ = write!(
            uart,
            "task     {} prio {} period {} ms  runs {}  pends {}",
            task.name,
            // A priority the superloop does not have. See `POWER_PRIORITY`.
            if cfg!(feature = "rtic") {
                task.priority as i32
            } else {
                -1
            },
            task.period_ms,
            runs,
            releases
        );
        if !cfg!(feature = "rtic") {
            let _ = write!(uart, " (= runs)");
        }
        let _ = writeln!(uart);

        // Ticks, not milliseconds, for the lateness. `clock::to_millis`
        // truncates, and every interesting answer here is under a millisecond --
        // printing it in milliseconds would render the whole comparison as `0`.
        // The counter rate is on the `time` command's own line and in
        // `target::TIME_HZ`.
        // `checked_div`, so a task that has never run prints 0 rather than
        // dividing by its own run count. That happens on every boot: the first
        // `rtic` typed inside the first period has nothing to average.
        let mean = total.checked_div(runs).unwrap_or(0);
        let _ = writeln!(
            uart,
            "         late worst {} ticks  mean {} ticks  gap worst {} ms over {} polls",
            worst,
            mean,
            clock::to_millis(worst_gap),
            polls
        );
    }

    sources(uart);

    // The two counters #115 names, read at last. `select` ran at boot, so these
    // are lifetime totals of the same events over the same cycles `mcycle`
    // counted -- and the ratio is the useful part: a dispatcher that idles in a
    // tighter loop moves the frontend number, one that waits on MMIO moves the
    // backend one.
    //
    // Both zero means the counters are not implemented on this target, which is
    // QEMU: `-M virt` decodes `mhpmcounter3..31` as hardwired zero, so the CSR
    // read is legal and the answer is not a measurement. Reported as `--`
    // rather than as a suspiciously perfect score.
    let (frontend, backend) = bench::hpm::stalls();
    let cycles = metrics::mcycle();
    if frontend == 0 && backend == 0 {
        let _ = writeln!(
            uart,
            "stalls   frontend -- backend --  (no performance counters on this target)"
        );
        return;
    }
    let _ = writeln!(
        uart,
        "stalls   frontend {} backend {}  of {} cycles",
        frontend, backend, cycles
    );
    // Per mille, by dividing the denominator down first -- the same arithmetic
    // `src/metrics.rs` explains: multiplying up overflows a `u32` at any
    // interesting cycle count, and doing it in `u64` is the `__udivdi3` call
    // this firmware is arranged to avoid.
    //
    // A `match` rather than an `if`, so the case with no answer is written out:
    // fewer than a thousand cycles since reset makes the unit zero, which is the
    // first few microseconds of every boot and is a missing line rather than a
    // divide by zero.
    match cycles / 1000 {
        0 => {}
        unit => {
            let _ = writeln!(
                uart,
                "         {} / {} per 1000 cycles",
                frontend / unit,
                backend / unit
            );
        }
    }
}

/// The deferred log's health, which `irq` prints and `rtic` does not.
///
/// Here beside [`sources`] because they are read together and a caller that
/// wanted one usually wants the other. A handler may not print, so it records;
/// if the ring fills, records are dropped rather than the handler stalling, and
/// this is where that shows up. A nonzero count is not a failure by itself -- it
/// means a burst outran the shell -- but a count that keeps climbing means
/// events are being lost continuously, which is the state in which a fault
/// becomes invisible. See `src/events.rs`.
pub fn log_health(uart: &mut Uart) {
    let _ = writeln!(
        uart,
        "  log  waiting {} dropped {}",
        events::waiting(),
        events::dropped()
    );
}
