//! What the dispatcher is achieving, and what it costs.
//!
//! Issue #245, under #115. This firmware has one concurrency model and it is
//! `#[rtic::app]` in `src/rtic_app.rs`. It used to have two, chosen at compile
//! time, with the superloop shipping and RTIC behind a feature; the superloop is
//! gone and the measurements that decided it are in `docs/rtic.md`.
//!
//! ## Why this module exists at all
//!
//! Because "is the dispatcher doing its job" is a question about the SHIPPING
//! firmware and there was no way to ask it. #115 names three instruments that
//! already existed and went unread:
//!
//!     metrics::polled()        the REFRESH cycle's achieved interval
//!     the PLIC's counters      irqs, stalls, buffered and lost, per source
//!     mhpmcounter3/4           STALLED_CYCLES_FRONTEND and _BACKEND
//!
//! The `rtic` command below prints all three. It reports the model first, and
//! that line survives the superloop's removal deliberately: a transcript that
//! does not say what produced it is a transcript nobody can compare against
//! another, and the day a second model appears every figure taken before it
//! stops comparing.
//!
//! ## Gap and lateness are different questions
//!
//! Both are printed and neither substitutes for the other.
//!
//! **Gap** is the interval between two consecutive runs, from `src/metrics.rs`.
//! It says what period the REFRESH cycle is actually achieving. A task that is
//! late every time but consistently late has a perfect gap.
//!
//! **Lateness** is how long a run waited after it became due, recorded by
//! [`released`]. It is what a dispatcher can fix. A run becomes due on the 1 ms
//! tick grid and waits for the SLIC to dispatch it.
//!
//! ## What still limits it
//!
//! If the shell's `&mut Devices` is held across a long command then the task
//! blocks on the same resource the command holds, and no dispatcher can fix
//! that. `#[idle]` takes the lock per step rather than around the loop for
//! exactly this reason, and a whole command is still inside one because `run()`
//! takes `&mut Devices`. That is a fact about where the sharing is; this command
//! reports it rather than hiding it.

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

/// The dispatcher. There is one.
///
/// Kept as a constant rather than deleted with the superloop, because the `rtic`
/// command prints it and a transcript that does not say what produced it is a
/// transcript nobody can compare against another. It is also what a future
/// second model would have to change, and having to change one constant is the
/// cheapest possible reminder that transcripts from before it do not compare.
pub const MODEL: &str = "rtic";

/// The PAC1954's REFRESH cycle: `power::Monitor::service`, every
/// [`power::interval_ms`].
pub const POWER: usize = 0;

/// The limit ALERT: `power::Monitor::service_alert`, released by the pin (#285).
///
/// Aperiodic. It used to be serviced INSIDE the REFRESH cycle, which made alert
/// latency equal to the poll period -- so backing the poll off would have made
/// excursions slower to report, and #286 wants it off by default.
pub const POWER_ALERT: usize = 1;

/// The heartbeat: toggle the orange LED, every [`HEARTBEAT_PERIOD_MS`] (#411).
///
/// A TASK, not a line in `#[idle]`, and that is the whole of what the LED
/// claims. A blink driven from a loop says only that a loop is turning, which a
/// core spinning in a poll does too -- #409 is exactly that shape. A blink
/// driven from a dispatched task says the SCHEDULER IS STILL SCHEDULING, and
/// that is what "the OS is alive" means.
pub const HEARTBEAT: usize = 2;

/// How many tasks this module accounts for.
///
/// The consoles are not one: the machine-external handler fills a ring at
/// hardware priority, above every SLIC source. #247 sweeps the rest.
const TASKS: usize = 3;

/// The same count, for anything outside this module that reports it. Derived
/// rather than transcribed: the boot line said "1 task" while three were
/// declared, and a boot line that miscounts the scheduler is read at exactly the
/// moment someone is deciding whether to trust the machine.
pub const TASK_COUNT: usize = TASKS;

/// What each task is, for the report. Parallel to the ids above.
struct Task {
    name: &'static str,
    /// Its RTIC priority.
    priority: u8,
    /// Periodic tasks have a period to be judged against; aperiodic ones do not,
    /// and the gap and lateness lines are meaningless for them -- "late" needs a
    /// time it was DUE, and an alert is due when the pin says so.
    periodic: bool,
}

const TABLE: [Task; TASKS] = [
    Task {
        name: "power_refresh",
        priority: POWER_PRIORITY,
        periodic: true,
    },
    Task {
        name: "power_alert",
        priority: POWER_ALERT_PRIORITY,
        periodic: false,
    },
    Task {
        name: "heartbeat",
        priority: HEARTBEAT_PRIORITY,
        periodic: true,
    },
];

/// The period a task is asked to hold, or `None` if it is aperiodic.
///
/// A function rather than a field, because the REFRESH period is settable at
/// runtime (#286) and a `const` table would report the value it was compiled
/// with.
fn period_ms(task: usize) -> Option<u32> {
    match task {
        POWER => match power::interval_ms() {
            power::RATE_OFF => None,
            ms => Some(ms),
        },
        HEARTBEAT => Some(HEARTBEAT_PERIOD_MS),
        _ => None,
    }
}

/// How often the heartbeat task toggles the orange LED, in milliseconds.
///
/// 250, so the LED's own period is 500 ms -- **2 Hz**, unmistakably a blink
/// rather than a flicker, and slow enough that a phone camera or a stopwatch
/// can time it. Two toggles per period is what makes a stuck output
/// distinguishable from a healthy one; a level, however carefully computed,
/// cannot be.
///
/// The cost is 4 dispatches a second of one GPIO write.
pub const HEARTBEAT_PERIOD_MS: u32 = 250;

/// The RTIC priority the heartbeat task runs at.
///
/// **THE LOWEST, deliberately.** It was 3, above the `devices` ceiling, so that
/// no shell command could stop the blink. That bought a lamp saying "the timer
/// fires" -- nearly always true, and so nearly never news.
///
/// At the bottom it says the stronger thing: **everything above it is also
/// getting done, and there is slack left over.** A board that has stopped
/// keeping up stops blinking, and that is the condition worth seeing.
///
/// It also gains the failure this lamp was built for. At 3 it blinked straight
/// through #409 -- a verb spinning at idle priority with interrupts enabled
/// leaves everything above it intact. At the bottom that spin holds the CPU and
/// the blink stops.
///
/// The cost is real and is the point: a long command that holds `devices` WILL
/// pause the lamp. That is not a false alarm. It is the board reporting it has
/// no slack, which is the same fact said a different way.
pub const HEARTBEAT_PRIORITY: u8 = 1;

/// The RTIC priority the power task runs at.
///
/// 1, the lowest that is not idle. A REFRESH cycle is 50 ms of period and a
/// couple of milliseconds of I2C; nothing about it is more urgent than a
/// keystroke, and the console's bytes are collected by the machine-external
/// handler at hardware priority anyway -- above every SLIC source, so this
/// number cannot starve them.
///
/// Here rather than in `src/rtic_app.rs` so that this module can report it
/// without depending on the app, which depends on this module.
pub const POWER_PRIORITY: u8 = 1;

/// The RTIC priority the ALERT task runs at.
///
/// 2, ABOVE the REFRESH task. An excursion is the one thing on this peripheral
/// that is time-critical: the part latches it and the rail may already be back
/// in range, so the report is all there is.
///
/// It cannot corrupt a REFRESH cycle in progress. Both tasks use `devices`, so
/// RTIC's ceiling for that resource is 2, and the REFRESH task's `lock` raises
/// the threshold to 2 for as long as it holds it -- the alert waits for the
/// cycle rather than preempting into a half-finished I2C transaction.
pub const POWER_ALERT_PRIORITY: u8 = 2;

/// How many times each task has run.
static RUNS: [AtomicU32; TASKS] = [const { AtomicU32::new(0) }; TASKS];

/// How many times each task has been released and not yet run.
///
/// The tick pending the task. The difference from [`RUNS`] is the diagnostic: a
/// pend count that outruns the run count is a task being released again before
/// it got to run, which the SLIC coalesces into one dispatch.
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

/// Boot is over: report what it cost, then start the measurement again.
///
/// Called once from `src/main.rs`, after the boot sequence and before the loop.
///
/// **Startup is not a sample of steady state, and leaving it in the totals makes
/// every later number wrong.** Configuring the PAC1954, probing two FUSB302Bs
/// and waiting for USB all hold the bus for milliseconds at a time and starve
/// the periodic task while they do. That produced a `late worst` of 12.6 ms on a
/// task whose steady-state worst is under 100 us -- a real event, but one that
/// happened once, before the system was running, and that then stood as the
/// headline for the rest of the session.
///
/// The startup figures are PRINTED before they are cleared. They are a real
/// measurement of a real thing -- how long the board takes to become useful --
/// and discarding them silently would replace a misleading number with a missing
/// one.
pub fn boot_complete(uart: &mut Uart) {
    for (index, task) in TABLE.iter().enumerate() {
        let runs = RUNS[index].load(RELAXED);
        let worst = WORST_LATE[index].load(RELAXED);
        if runs != 0 {
            let _ = writeln!(
                uart,
                "sched    startup: {} ran {} time(s), worst {} us late -- \
                 cleared, steady state starts now",
                task.name,
                runs,
                clock::to_micros(worst)
            );
        }
        RUNS[index].store(0, RELAXED);
        RELEASES[index].store(0, RELAXED);
        WORST_LATE[index].store(0, RELAXED);
        TOTAL_LATE[index].store(0, RELAXED);
    }
    metrics::restart();
}

/// `cpu status` -- is the OS alive, and how promptly (#411).
///
/// **The same statics the `rtic` table reads, formatted for one question.** Not
/// a second copy of the arithmetic: a number reachable by two names that can
/// drift apart is worse than one reachable only by the less obvious name. `rtic`
/// is the dispatcher's whole report, every task and both its gap and lateness
/// columns; this is the one row a person wants when they are asking whether the
/// board is up.
///
/// The figures are STEADY STATE, not boot. [`boot_complete`] clears every
/// counter after the prologue, so what prints here began at the first turn of
/// the loop -- which is what makes the lateness a jitter measurement rather than
/// the 12.6 ms of one-off bring-up that #322 is about.
pub fn heartbeat_report(uart: &mut Uart) {
    let runs = RUNS[HEARTBEAT].load(RELAXED);
    let pends = RELEASES[HEARTBEAT].load(RELAXED);
    let worst = WORST_LATE[HEARTBEAT].load(RELAXED);
    let mean = TOTAL_LATE[HEARTBEAT]
        .load(RELAXED)
        .checked_div(runs)
        .unwrap_or(0);

    let _ = writeln!(
        uart,
        "os       {}  the {} LED toggles per dispatch, every {} ms",
        if runs == 0 {
            "NOT YET DISPATCHED -- nothing has toggled the lamp"
        } else {
            "alive"
        },
        HEARTBEAT_LAMP,
        HEARTBEAT_PERIOD_MS
    );
    let _ = writeln!(
        uart,
        "  runs    {}  pends {}{}",
        runs,
        pends,
        if pends != runs { "  COALESCED" } else { "" }
    );
    // LATE, not GAP, and the two are different questions -- a dispatcher that is
    // late every time by the same amount holds a perfect period. `rtic` prints
    // both columns; this prints the one that says whether a dispatch is prompt.
    let _ = writeln!(
        uart,
        "  late    worst {} us  mean {} us   dispatch delay, steady state \
         (boot cleared)",
        clock::to_micros(worst),
        clock::to_micros(mean)
    );
    // WHAT A BLINK DOES NOT SAY. Stated where the number is read, because the
    // number invites exactly the wrong conclusion.
    let _ = writeln!(
        uart,
        "  means   the scheduler dispatches; NOT that any verb is progressing \
         -- see `cpu wedge`"
    );
}

/// The colour the heartbeat task drives, for the report above. Named by colour
/// and only by colour -- `src/gpio.rs` says why.
const HEARTBEAT_LAMP: &str = "orange";

/// A task became due. Called by the 1 ms tick, which is what decides that.
pub fn pended(task: usize) {
    RELEASES[task].store(RELEASES[task].load(RELAXED).saturating_add(1), RELAXED);
}

/// A task is running now, `late` ticks after it was due.
///
/// Called from the task body. `late` is measured from the instant the tick
/// recorded when it pended, so it is the wait for dispatch and nothing else.
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
///
/// `pri` is read back from the PLIC, not from `plic::priority`. A level that is
/// not what the table says is a claim order that is not what the table says, and
/// nothing else on this console could show it. #344.
pub fn sources(uart: &mut Uart) {
    let plic = Plic::new(target::PLIC_BASE);
    let _ = writeln!(
        uart,
        "plic  @{:08x} pending {:08x} enabled {:08x} threshold {}",
        target::PLIC_BASE,
        plic.pending(),
        plic.enabled(),
        plic.threshold()
    );
    for console in 0..target::UART_BASES.len() {
        let (interrupts, stalls, buffered) = irq::stats(console);
        // `lost` counts LSR reads that found an error bit set -- an overrun or a
        // framing error, both of which mean input that never reached the shell.
        // Zero is the only good value; a number that climbs while nothing is
        // typed is a noisy line.
        let _ = writeln!(
            uart,
            "  {} src {} pri {} irqs {} stalls {} buffered {} lost {}",
            console,
            target::UART_IRQS[console],
            plic.priority(target::UART_IRQS[console]),
            interrupts,
            stalls,
            buffered,
            uart::error_reads(console)
        );
    }
    // The I2C completion. Printed even when the count is zero, and especially
    // then: zero after traffic means the source is masked, `CTR.IEN` is clear,
    // or the gateware is not driving it, and those used to be indistinguishable
    // because none of them produced a number. #246.
    if let Some(board) = target::BOARD {
        let count = irq::i2c_interrupts();
        let _ = writeln!(
            uart,
            "  i2c           src {} pri {} irqs {}{}",
            cynthion_soc_pac::base::BOARD_I2C_IRQ,
            plic.priority(cynthion_soc_pac::base::BOARD_I2C_IRQ),
            count,
            if count == 0 { "  -- never fired" } else { "" }
        );
        let _ = board;
    }

    // The PAC1954's limit ALERT. Printed at zero too: zero after a bracket has
    // been armed means the pin is not reaching the PLIC, the part is not
    // asserting, or the source is masked -- three faults that look identical
    // without a number. #270.
    if target::BOARD.is_some() {
        let count = irq::power_alert_interrupts();
        let _ = writeln!(
            uart,
            "  power alert   src {} pri {} irqs {}{}",
            cynthion_soc_pac::base::BOARD_I2C_MUX_POWER_ALERT_IRQ,
            plic.priority(cynthion_soc_pac::base::BOARD_I2C_MUX_POWER_ALERT_IRQ),
            count,
            if count == 0 { "  -- never fired" } else { "" }
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
            "  type-c {:6} src {} pri {} irqs {}",
            fusb302::Port::ALL[port].name(),
            source,
            plic.priority(source),
            irq::type_c_interrupts(port)
        );
    }
}

/// `rtic` -- which dispatcher this is, and whether it is doing better.
///
/// Four blocks, and each one is a thing #115 asked for:
///
///     model    the dispatcher, so a transcript says what produced it
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

        let period = period_ms(index);
        let _ = write!(uart, "task     {} prio {} ", task.name, task.priority);
        match (task.periodic, period) {
            (false, _) => {
                let _ = write!(uart, "aperiodic       ");
            }
            (true, None) => {
                let _ = write!(uart, "period OFF      ");
            }
            (true, Some(ms)) => {
                let _ = write!(uart, "period {} ms  ", ms);
            }
        }
        let _ = write!(uart, "runs {}  pends {}", runs, releases);
        // `pends` above `runs` is a task being released again before it got to
        // run, which the SLIC coalesces into one dispatch. Equal is healthy.
        if releases != runs {
            let _ = write!(uart, "  COALESCED {}", releases - runs);
        }
        let _ = writeln!(uart);

        // Ticks AND microseconds. Ticks alone were the primary unit because
        // `to_millis` truncates and every interesting answer here is well under
        // a millisecond -- but a bare tick count sat on the same line as a
        // `gap worst 50 ms`, and two numbers in different units on one line get
        // compared. They were not comparable, and the comparison said the
        // lateness was 8 periods when the gap said it was none.
        //
        // `checked_div`, so a task that has never run prints 0 rather than
        // dividing by its own run count. That happens on every boot: the first
        // `rtic` typed inside the first period has nothing to average.
        let mean = total.checked_div(runs).unwrap_or(0);
        // `late worst N us` and `gap worst N ms` are the tokens
        // `scripts/soc_test.py` and `scripts/soc_probe.py` parse, so the wording
        // around them can change and the checks keep working. What follows each
        // is the sentence saying WHICH question it answers -- the two were on one
        // line in different units once, and got compared.
        let _ = writeln!(
            uart,
            "  late   worst {} us  mean {} us     how long after it was DUE a \
             run started",
            clock::to_micros(worst),
            clock::to_micros(mean),
        );

        // The interval between consecutive runs, which is a DIFFERENT question.
        // A dispatcher can be reliably late and still hold the period exactly;
        // one that drifts holds neither. `+0` here with a nonzero lateness above
        // is the normal, healthy reading and not a contradiction.
        // A gap is between TWO runs, so one run has no gap and no run has less
        // than that. Printed as unmeasured rather than as `0 ms  -50 ms`, which
        // is what it said when `rtic` was typed inside the first period: an
        // interval of zero against a period of fifty, which reads as a
        // catastrophically fast poller rather than as an absent measurement.
        // Same defect as the lateness measured from a zero instant, one line
        // over.
        // Only a periodic task has a gap worth judging. An alert arrives when
        // the pin says so, and an interval between two of them measures the
        // rails, not the dispatcher.
        match period {
            // `poll_stats` is the POWER poll's and only its. Printing it beside
            // another task's period would put one task's interval under
            // another's heading, which is a plausible wrong number -- worse here
            // than an absent one.
            _ if index != POWER => {
                let _ = writeln!(
                    uart,
                    "  gap    not timed here -- {}",
                    if index == HEARTBEAT {
                        "the LED is the readout; `cpu wedge` is the control (#411)"
                    } else {
                        "released by the ALERT pin, not a period"
                    }
                );
            }
            None => {
                let _ = writeln!(
                    uart,
                    "  gap    n/a   released by {}",
                    if task.periodic {
                        "nothing -- the poll is OFF"
                    } else {
                        "the ALERT pin, not a period"
                    }
                );
            }
            Some(asked) if polls < 2 => {
                let _ = writeln!(
                    uart,
                    "  gap    not yet measured  asked {} ms   needs two runs, \
                     has {}",
                    asked, polls
                );
            }
            Some(asked) => {
                let achieved = clock::to_millis(worst_gap);
                let _ = writeln!(
                    uart,
                    "  gap    worst {} ms  asked {} ms  {:+} ms   the INTERVAL \
                     between runs, over {} of them",
                    achieved,
                    asked,
                    achieved as i32 - asked as i32,
                    polls
                );
            }
        }

        // Ticks last, because they are what was measured and microseconds are
        // what a reader wants. Printed at all so a figure under a microsecond is
        // still visible instead of rendering as `0`.
        let _ = writeln!(
            uart,
            "         {} / {} ticks of the {} MHz counter behind both",
            worst,
            mean,
            target::TIME_HZ / 1_000_000,
        );
    }

    sources(uart);

    // The two counters #115 names. The ratio is the useful part: a dispatcher
    // that idles in a tighter loop moves the frontend number, one that waits on
    // MMIO moves the backend one.
    //
    // From `metrics`, NOT from three CSR reads here. `mhpmcounter3/4` and
    // `mcycle` are 64-bit counters on an RV32 core, so a read gets the low word
    // and it wraps every 71.6 s at 60 MHz. Divided against each other after a
    // wrap they gave `3366 / 952 per 1000 cycles` on the board -- more stalled
    // cycles than cycles, which is not a large error but an impossible one.
    // `metrics::turn` takes the deltas per loop pass, where a wrap cannot
    // happen, and halves all three together so the ratio survives.
    //
    // Both zero means the counters are not implemented on this target, which is
    // QEMU: `-M virt` decodes `mhpmcounter3..31` as hardwired zero, so the CSR
    // read is legal and the answer is not a measurement. Reported as `--`
    // rather than as a suspiciously perfect score.
    let (frontend, backend, cycles) = metrics::stall_window();
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
