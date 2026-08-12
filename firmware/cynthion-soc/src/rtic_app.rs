//! The shell's RTIC dispatcher, built only by `--features rtic`.
//!
//! Issue #245, under #115: the PAC1954's REFRESH cycle stops being a poll at the
//! top of the main loop and becomes a periodic task the 1 ms tick releases.
//!
//! **This is not the spike.** `src/bin/rtic.rs` is, and it stays: two tasks that
//! count, in their own binary, holding the answer to "does RTIC accept this
//! machine at all". What is here runs the SHELL -- the same `Devices`, the same
//! `Shell`, the same `run()`, the same commands -- so that the jitter figures the
//! `rtic` command prints are figures about the firmware that ships rather than
//! about a stand-in. That distinction is the whole of #115's "do not gate this on
//! a benchmark".
//!
//! ## This is the entry point
//!
//! `#[rtic::app]` emits this firmware's `#[no_mangle] fn main`. There is no
//! `#[entry]` anywhere else and no superloop to fall back to -- two entry points
//! are mutually exclusive by the linker, and #245 settled the choice.
//!
//! ## What lives here, and what does not
//!
//! Almost nothing lives here. `main::boot` runs the prologue, `#[idle]` calls
//! `main::housekeeping` and `main::consoles`, and `power_refresh` calls
//! `power::Monitor::service`. This file is the dispatch and the resource
//! declarations; the work is where it always was.
//!
//! The consoles are NOT tasks. Bytes arrive through `src/irq.rs`'s
//! machine-external handler into a ring -- hardware priority, above every SLIC
//! source, and nothing a task could improve on. #247 is the sweep of what should
//! become tasks; the consoles are not on it.
//!
//! ## RTIC does not use the PLIC, and this file does not pretend otherwise
//!
//! RTIC 2.3's RISC-V backends are `riscv-slic` backends: a SOFTWARE interrupt
//! controller, dispatched out of the machine software interrupt, which the SoC
//! wires from the CLINT's `msip` (`gateware/soc/top.py:1165`). RTIC never claims,
//! never completes, and never touches a PLIC priority or threshold.
//!
//! So the PLIC front end stays exactly where it is -- `src/irq.rs`, unchanged --
//! and the one task here is released by the timer rather than by a hardware
//! source. That is not a compromise: a 50 ms period is a schedule, not an event,
//! and there is no PAC1954 interrupt line on this board to bind to. The I2C
//! controller does have a source (IRQ 3), and #246 is where that goes; see the
//! note on `power_refresh`.

use crate::target;

/// The device the CLINT backend names.
///
/// riscv-slic's generated `__riscv_slic_swi_pend` is written against
/// `riscv-peripheral`'s CLINT type and reaches for `CLINT::mswi().msip(Hart::H0)`.
/// That is four accessors over one word, so it is written out here instead of
/// taking the dependency -- the same argument `src/main.rs` makes for having no
/// HAL.
///
/// Duplicated from `src/bin/rtic.rs` rather than shared, because that file
/// reaches its modules by `#[path]` and cannot say `use crate::`: this crate has
/// no `[lib]`. Twenty lines of MMIO accessor against adding a library target and
/// rewriting every `mod` in the shell.
pub mod device {
    pub mod interrupt {
        // riscv 0.16 calls this enum `Interrupt`; riscv-slic emits
        // `CoreInterrupt`, which is what riscv 0.13 and later PACs generate.
        pub use riscv::interrupt::Interrupt as CoreInterrupt;

        #[derive(Clone, Copy)]
        pub enum Hart {
            H0 = 0,
        }
    }

    /// `msip` for hart 0, at CLINT offset 0. `src/timer.rs` documents the rest
    /// of the register map and deliberately omits this one, because until now
    /// nothing raised a software interrupt at us.
    pub struct Msip(*mut u32);

    impl Msip {
        pub fn pend(&self) {
            // SAFETY: the CLINT window, uncached on the SoC and a device under
            // QEMU. Bit 0 is the only bit `cpu/clint.py` implements.
            unsafe { core::ptr::write_volatile(self.0, 1) }
        }

        pub fn unpend(&self) {
            // SAFETY: as above.
            unsafe { core::ptr::write_volatile(self.0, 0) }
        }
    }

    pub struct Mswi;

    impl Mswi {
        pub fn msip(&self, hart: interrupt::Hart) -> Msip {
            Msip((super::target::CLINT_BASE + 4 * hart as usize) as *mut u32)
        }
    }

    // The spelling is not ours to choose: riscv-slic's macro emits `#pac::CLINT`.
    #[allow(clippy::upper_case_acronyms)]
    pub struct CLINT;

    impl CLINT {
        pub fn mswi() -> Mswi {
            Mswi
        }
    }
}

/// The 1 ms tick's hook: release the REFRESH task when its period is up.
///
/// Re-exported from inside the app module, which is where it has to live --
/// `slic::SoftwareInterrupt` is generated there by `rtic::export::codegen!` and
/// is not nameable from outside it.
///
/// `src/timer.rs` is the only caller and calls it from the machine timer
/// handler, which is exactly right: the release must not be decided by the loop
/// it exists to be independent of. That was the defect in the poll -- a turn that
/// ran long moved the next REFRESH with it.
pub use app::{pend_power_alert, tick};

// `device = device`, a bare name, and it has to be: riscv-slic's generated
// `pub mod slic` says `use super::device;` -- so the argument names something
// reachable one level up from the app module, not a path from the crate root.
// The `use super::device` below is what puts it there.
#[rtic::app(device = device, peripherals = false, backend = H0)]
mod app {
    use core::sync::atomic::{AtomicU32, Ordering};

    use super::device;
    use crate::clock::{self, Instant};
    use crate::sched;
    use crate::shell::console::Shell;
    use crate::{metrics, timer, Devices, MAX_CONSOLES};

    /// The board, and every driver's state. ONE of these, shared between the
    /// task and `#[idle]`.
    ///
    /// The same `Devices` the superloop owns by `&mut`, with the same invariant
    /// -- one I2C controller, one owner per device -- enforced by a priority
    /// ceiling instead of by the borrow checker. RTIC computes the ceiling from
    /// the two users below: the task at [`sched::POWER_PRIORITY`] and `#[idle]`
    /// at 0, so the ceiling is the task's own priority and `idle`'s `lock`
    /// raises the SLIC threshold to it.
    ///
    /// **This is where "no better" would come from, and it is not hidden.** For
    /// as long as `idle` holds the lock, the task cannot run; a shell command
    /// that takes a `&mut Devices` and spends a second in it delays the REFRESH
    /// by a second under RTIC exactly as it does under the superloop. What the
    /// lock buys is that the delay is bounded by the command rather than by the
    /// whole turn -- see `idle` for what is deliberately outside it.
    #[shared]
    struct Shared {
        devices: Devices,
    }

    /// The line editors, one per console. `#[local]` to `idle`, which is their
    /// only user -- the power task never touches a console's editing state, only
    /// the wire.
    #[local]
    struct Local {
        shells: [Shell; MAX_CONSOLES],
    }

    /// Ticks at the moment the task was last released, so the task can say how
    /// long it waited.
    ///
    /// A `static` rather than a `#[local]` resource because the writer is the
    /// machine timer handler, which is not an RTIC context and has no `Context`
    /// to be handed one from. One writer, one reader, one hart: relaxed is
    /// enough, for the reason `src/metrics.rs` sets out.
    static RELEASED_AT: AtomicU32 = AtomicU32::new(0);

    /// Milliseconds counted by the tick since the last release.
    ///
    /// Counting ticks rather than comparing `rdtime` against a deadline, because
    /// the tick is already on an absolute grid -- `src/timer.rs` adds a period to
    /// `mtimecmp` and never reloads from now -- so a count of ticks does not
    /// drift and needs no second time source to check itself against.
    static SINCE_MS: AtomicU32 = AtomicU32::new(0);

    /// The same pair for the heartbeat task (#411), kept separate so the two
    /// periods cannot interact: the power interval is settable at runtime and
    /// may be OFF, and whether the board looks alive must not inherit either
    /// property.
    static HEARTBEAT_SINCE_MS: AtomicU32 = AtomicU32::new(0);
    static HEARTBEAT_AT: AtomicU32 = AtomicU32::new(0);

    /// One millisecond passed. Release the REFRESH task if its period is up.
    ///
    /// Called from `timer::tick`, in the machine timer handler. It may not print
    /// and does not: `sched::pended` is two atomic stores and
    /// `rtic::export::pend` is one MMIO store to `msip`. The rule is
    /// `src/events.rs`'s and it applies here for the same reason.
    ///
    /// If a release lands while the task is still waiting to run, the SLIC
    /// coalesces the two into one dispatch -- the pend is a bit, not a queue.
    /// That is the right behaviour for a sampler (the newest reading is the one
    /// wanted) and the `pends` against `runs` columns in the `rtic` command are
    /// what make it visible rather than silent.
    pub fn tick() {
        // THE HEARTBEAT RELEASE COMES FIRST, and before every early return
        // below. A release the power branch can skip would make whether the
        // board looks alive depend on the PAC1954 poll rate, which is settable
        // at runtime and may be OFF (#286).
        let beat = HEARTBEAT_SINCE_MS.load(Ordering::Relaxed) + timer::PERIOD_MS;
        if beat >= sched::HEARTBEAT_PERIOD_MS {
            // Subtract rather than zero, so a tick that arrives late does not
            // shorten the following interval -- the same absolute-grid argument
            // `src/timer.rs` makes about `mtimecmp`.
            HEARTBEAT_SINCE_MS.store(beat - sched::HEARTBEAT_PERIOD_MS, Ordering::Relaxed);
            HEARTBEAT_AT.store(Instant::ZERO.elapsed(clock::now()), Ordering::Relaxed);
            sched::pended(sched::HEARTBEAT);
            rtic::export::pend(slic::SoftwareInterrupt::Heartbeat);
        } else {
            HEARTBEAT_SINCE_MS.store(beat, Ordering::Relaxed);
        }

        let since = SINCE_MS.load(Ordering::Relaxed) + timer::PERIOD_MS;

        // A PAC1954 cycle is TWO dispatches, not one, and the gap between them
        // is what the datasheet asks for (#275).
        //
        //     t = 0      REFRESH_V   latch the most recent conversion
        //     t = 1 ms   read        the registers are stable by now
        //
        // DS20006539B section 5.2: the readable registers "will be stable
        // within 1 ms from sending the REFRESH command", and a command inside
        // that window "will be ignored and NACKed". So the second dispatch is
        // released on the very next tick, which is exactly the 1 ms the part
        // wants -- the tick period and the part's settling time happen to be
        // the same number, and `PERIOD_MS` is asserted against it below.
        //
        // The alternative -- reading the PREVIOUS cycle's latch 50 ms later --
        // respects the window by a wide margin and makes every reading one
        // whole interval stale.
        if crate::power::refresh_pending() {
            SINCE_MS.store(since, Ordering::Relaxed);
            sched::pended(sched::POWER);
            rtic::export::pend(slic::SoftwareInterrupt::PowerRefresh);
            return;
        }

        // The rate is runtime-settable, and OFF is a supported setting (#286).
        // An off poll costs one relaxed load per tick and releases nothing;
        // excursions are still reported, because the ALERT is a comparison
        // inside the part against every sample and does not depend on this.
        // One cycle at boot even with the poll off, so `power` has something to
        // show. Without it the table would read NO SAMPLE YET for ever and an
        // idle board would look like a dead one.
        let interval = crate::power::interval_ms();

        // While OFF, nothing is being timed, so nothing accumulates. Storing
        // `since` here instead left a backlog that turning the poll on then
        // drained as a burst -- five releases a millisecond apart, and a "worst
        // gap" of 20 ms against a 50 ms period, which reads as a broken tick.
        if interval == crate::power::RATE_OFF && crate::power::primed() {
            SINCE_MS.store(0, Ordering::Relaxed);
            return;
        }

        if !crate::power::primed() {
            SINCE_MS.store(since, Ordering::Relaxed);
            RELEASED_AT.store(Instant::ZERO.elapsed(clock::now()), Ordering::Relaxed);
            sched::pended(sched::POWER);
            rtic::export::pend(slic::SoftwareInterrupt::PowerRefresh);
            return;
        }
        if since < interval {
            SINCE_MS.store(since, Ordering::Relaxed);
            return;
        }
        // Subtract the period rather than storing zero, so a tick that arrives
        // late does not shorten the following interval. The same argument
        // `src/timer.rs` makes about `mtimecmp`, one level up.
        //
        // `saturating_sub`, because the interval can now SHRINK at runtime: a
        // rate moved from 1000 ms to 1 ms leaves `since` far past the new
        // period, and the wrapped result would be a ~49-day wait for the next
        // release.
        SINCE_MS.store(since.saturating_sub(interval), Ordering::Relaxed);

        RELEASED_AT.store(Instant::ZERO.elapsed(clock::now()), Ordering::Relaxed);
        sched::pended(sched::POWER);
        rtic::export::pend(slic::SoftwareInterrupt::PowerRefresh);
    }

    /// The same prologue the superloop runs, in the same order.
    ///
    /// `main::boot` is shared source, so a board that comes up differently under
    /// the two models is a difference this arrangement cannot produce.
    ///
    /// It enables interrupts globally on the way through (`irq::init`), which
    /// RTIC's generated `main` would otherwise do after this returns. That is
    /// harmless and checked: the machine-external handler touches only `static`
    /// rings initialised before `main`, and the machine timer handler's pend
    /// lands on a SLIC bit that is read once the SLIC is enabled. Nothing here
    /// can be preempted into uninitialised state.
    #[init]
    fn init(_cx: init::Context) -> (Shared, Local) {
        let devices = crate::boot();
        (
            Shared { devices },
            Local {
                shells: [Shell::NEW; MAX_CONSOLES],
            },
        )
    }

    /// The shell, at priority 0.
    ///
    /// The superloop's body with the power poll removed, and nothing else
    /// removed. `metrics::turn()` still closes each turn, so `cpu stats` means
    /// the same thing under both models.
    ///
    /// **The lock is taken per step, not around the loop.** Holding it across
    /// the whole body would make this an exact re-implementation of the
    /// superloop, priority ceiling and all, and would guarantee the answer "no
    /// better" by construction rather than by measurement. Per step, the task
    /// can be dispatched between `housekeeping` and `consoles`, and during the
    /// long tail of every turn in which the shell is doing nothing at all.
    ///
    /// What is still inside a lock is a whole command, because `run()` takes
    /// `&mut Devices`. A `bench` walk therefore blocks the REFRESH for its
    /// duration under both models. That is a fact about where the sharing is and
    /// the `rtic` command reports it rather than hiding it.
    #[idle(shared = [devices], local = [shells])]
    fn idle(mut cx: idle::Context) -> ! {
        // Here rather than in `#[init]`, and at the same point the superloop
        // does it: after boot, before the first turn. `#[init]` runs with
        // interrupts masked, so the task cannot have been dispatched yet and
        // there would be nothing to report -- and reporting the startup cost is
        // half the point of the call. See `sched::boot_complete`.
        sched::boot_complete(&mut crate::primary());

        loop {
            metrics::turn();

            let mut console = crate::primary();
            cx.shared
                .devices
                .lock(|devices| crate::housekeeping(&mut console, devices));
            cx.shared
                .devices
                .lock(|devices| crate::shell::console::consoles(cx.local.shells, devices));
        }
    }

    /// The PAC1954's REFRESH cycle, released by the tick.
    ///
    /// The task #245 asks for. Its body is `power::Monitor::service` -- the same
    /// function the superloop calls, deliberately, so the only thing that
    /// differs between the two measurements is how it got here.
    ///
    /// It reports on the PRIMARY console only, as the poll did. The second
    /// port's TX pin is JTAG TMS and this firmware never transmits there
    /// unbidden -- see `target::NEVER_SPEAKS_FIRST`.
    ///
    /// **The I2C transaction is still a spin, and #245 asks for it not to be.**
    /// It is left as one here on purpose: IRQ 3 is wired in the gateware
    /// (`top.py:849`) but `irq::init` never enables it, so the source has never
    /// asserted and nothing knows whether it is level or edge or how it is
    /// cleared. Establishing that is #246's job and needs the board, which this
    /// change does not touch. A task that spins for two milliseconds at priority
    /// 1 is preemptible and delays only `idle`; the same spin inside a handler
    /// would not have been.
    #[task(binds = PowerRefresh, priority = 1, shared = [devices])]
    fn power_refresh(mut cx: power_refresh::Context) {
        let released = Instant::at(RELEASED_AT.load(Ordering::Relaxed));
        sched::released(sched::POWER, released.elapsed(clock::now()));

        let mut console = crate::primary();
        cx.shared.devices.lock(|devices| {
            let bus = devices.bus.as_mut();
            devices.power.service(&mut console, bus);
        });
    }

    /// The limit ALERT, released by the pin rather than by a period (#285).
    ///
    /// Servicing INSIDE the REFRESH cycle would make alert latency the poll
    /// period, which #286 wants settable and off by default. Reporting an
    /// excursion up to a second late, or never, is not a property the alert
    /// should inherit from an unrelated setting.
    ///
    /// The handler cannot do this itself: clearing `ALERT_STATUS` is an I2C
    /// transaction and `devices` is a shared resource it has no `Context` to
    /// lock. So the handler masks the source and pends this, which runs in
    /// normal context and can.
    ///
    /// Priority 2, above the REFRESH task. It cannot preempt a cycle in
    /// progress: both use `devices`, so RTIC's ceiling for that resource is 2
    /// and the REFRESH task's `lock` holds the threshold there until it is done.
    #[task(binds = PowerAlert, priority = 2, shared = [devices])]
    fn power_alert(mut cx: power_alert::Context) {
        sched::released(sched::POWER_ALERT, 0);

        let mut console = crate::primary();
        cx.shared.devices.lock(|devices| {
            if let Some(bus) = devices.bus.as_mut() {
                devices.power.service_alert(&mut console, bus);
            }
        });
    }

    const _: () = assert!(sched::POWER_ALERT_PRIORITY == 2);

    /// Toggle the orange LED. The board's one live sign that the OS is up (#411).
    ///
    /// A SOFTWARE task, dispatched through the SLIC, not a hardware task bound
    /// to the timer vector. Reaching this body means all of: the core is
    /// fetching, the CLINT fired, `tick` ran and did its arithmetic, the pend
    /// reached `msip`, and the SLIC selected this source. A hardware task on the
    /// timer IRQ would prove the first two and stop there.
    ///
    /// **THE LOWEST PRIORITY, deliberately.** It ran at 3 -- above the `devices`
    /// ceiling -- so that no shell command could stop the blink. That made the
    /// lamp say "the timer fires", which is nearly always true and so nearly
    /// never news.
    ///
    /// At the bottom it says something stronger: **everything above it is also
    /// getting done, and there is slack left over.** A board that has stopped
    /// keeping up stops blinking, which is the condition worth seeing.
    ///
    /// It also gains the failure this lamp was built for. At priority 3 it
    /// blinked straight through #409, because a verb spinning at idle priority
    /// with interrupts enabled leaves everything above it intact. At the bottom
    /// that spin holds the CPU and the blink stops.
    ///
    /// The cost is real and is the point: a shell command that holds the CPU
    /// long enough WILL pause the lamp. That is not a false alarm -- it is the
    /// board reporting that it has no slack, which is the same fact.
    ///
    /// RTIC's software tasks here are released by `tick`'s counter, not by a
    /// monotonic queue, so nothing below exercises a deadline queue. Not an
    /// omission that could be tidied up: the CLINT has ONE comparator,
    /// `Mono::start()` claims `mtimecmp`, and `src/timer.rs` already owns it --
    /// adopting a monotonic moves every periodic job at once, including the tick
    /// that stamps every log line. `src/bin/mono_rtic.rs` is that work.
    #[task(binds = Heartbeat, priority = 1)]
    fn heartbeat(_cx: heartbeat::Context) {
        let released = Instant::at(HEARTBEAT_AT.load(Ordering::Relaxed));
        sched::released(sched::HEARTBEAT, released.elapsed(clock::now()));
        crate::heartbeat::toggle();
    }

    const _: () = assert!(sched::HEARTBEAT_PRIORITY == 1);

    /// Release the ALERT task. Called from the machine-external handler.
    ///
    /// One MMIO store to `msip`, which is what makes it legal there -- the rule
    /// is `src/events.rs`'s and applies for the same reason.
    pub fn pend_power_alert() {
        sched::pended(sched::POWER_ALERT);
        rtic::export::pend(slic::SoftwareInterrupt::PowerAlert);
    }

    // THE PRIORITY THE TASK ABOVE RUNS AT is written as a literal, because
    // RTIC's grammar takes an integer there and not a path. This is what keeps
    // the literal and the constant the `rtic` command PRINTS from drifting
    // apart: they are the same number or this does not compile.
    const _: () = assert!(sched::POWER_PRIORITY == 1);

    // THE MACHINE EXTERNAL INTERRUPT is not an RTIC task and cannot be.
    //
    // `src/irq.rs` owns it under both models and this build links that one --
    // the attribute there installs the symbol, and there is exactly one. It is
    // mentioned here only so the reader does not go looking for the
    // `#[task(binds = ...)]` that would be its natural home and is not possible:
    // RTIC's SLIC has no PLIC backend, so a hardware source needs the claim loop
    // that already exists.
    //
    // The consequence worth stating: console bytes are collected at HARDWARE
    // priority, above every SLIC source, under both models. Nothing in this file
    // can delay them.
}
