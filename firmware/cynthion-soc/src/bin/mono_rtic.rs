//! A CLINT monotonic for RTIC, written and measured.
//!
//! Issue 4 of the six: **implementation of a CLINT monotonic.**
//! `docs/rtic.md` §9 and `docs/rtic.md` §8 both record that
//! `rtic-monotonics` 2.2.1 "has SysTick, STM32 and Silabs and nothing for
//! RISC-V". **That is wrong, and this binary is the correction.** The crate ships
//! `esp32c3.rs` and `esp32c6.rs`, both RISC-V, both `TimerQueueBackend`
//! implementations over an ordinary compare register. What is missing is not
//! RISC-V support but a **CLINT** backend, and `TimerQueueBackend` is five
//! methods:
//!
//!     now()                the 64-bit `mtime`, high-low-high
//!     set_compare(t)       the three-store `mtimecmp` sequence
//!     clear_compare_flag() nothing: the CLINT has no flag
//!     pend_interrupt()     `mtimecmp = 0`, a real hardware pend
//!     timer_queue()        a `static`
//!
//! All five are below, and `src/timer.rs` already contains the hard parts of two
//! of them for its own 1 ms tick. The sorted queue, the insert path, the
//! re-arm-on-earlier-insert and the deadline-in-the-past case -- everything
//! `docs/rtic.md` §3 lists as the bulk of the work -- are
//! `rtic_time::TimerQueue`'s, not ours.
//!
//! ## What it costs to have one, and what it takes away
//!
//! **The CLINT has one comparator.** `Mono::start()` takes `mtimecmp`, so
//! `src/timer.rs`'s 1 ms tick cannot also have it. That is not a defect of the
//! monotonic -- a monotonic is exactly the thing that multiplexes many deadlines
//! onto one comparator -- but it does mean adopting it is not additive: every
//! periodic job in the firmware moves onto the queue at once, including the tick
//! that stamps every log line.
//!
//! ## What it proves
//!
//! `scripts/soc_rtic_monotonic.py` runs it: 100 periods on a 5 ms absolute grid,
//! `delay_until` rather than `delay`, so a period that overran cannot push the
//! next one out and the figure reported is lateness against an absolute
//! deadline -- the same discipline `src/timer.rs` states for adding rather than
//! reloading. The deliberately-late case is exercised too: one period spends
//! longer than its own interval before asking for the next deadline, so
//! `set_compare` is called with an instant already in the past and the queue has
//! to fire it immediately rather than wait 58 minutes for `mtime` to wrap.
//!
//! Built only by `--features rticmono`.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::AtomicU32;

// **`src/timer.rs` cannot be in this binary at all** -- it installs a
// `MachineTimer` handler, and the monotonic below owns that vector and the one
// comparator behind it. `symbol MachineTimer is already defined` is the linker
// saying exactly that, and it is the sharpest evidence there is that adopting a
// monotonic is not additive. `src/log.rs` goes with it, because it stamps every
// line from `timer::millis`; `src/wl_report.rs` carries an unstamped `log!` for
// `uart::report_errors` instead, and everything that formats.
#[allow(dead_code)]
#[path = "../clock.rs"]
mod clock;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;
#[allow(dead_code)]
#[path = "../uart.rs"]
mod uart;
#[allow(dead_code)]
#[path = "../wl_report.rs"]
mod wl_report;

pub const MAX_CONSOLES: usize = 4;

/// See `src/bin/workload_rtic.rs`: `uart::report_errors` wants these two and
/// this binary has no turn accounting to give it.
mod metrics {
    pub fn busy() {}
}

/// The results, so that `#[idle]` can print what the async task measured.
static PERIODS: AtomicU32 = AtomicU32::new(0);
static WORST_LATE: AtomicU32 = AtomicU32::new(0);
static TOTAL_LATE: AtomicU32 = AtomicU32::new(0);
static WORST_EARLY: AtomicU32 = AtomicU32::new(0);
static LATE_FIRE: AtomicU32 = AtomicU32::new(0);
static DONE: AtomicU32 = AtomicU32::new(0);

/// Periods to run, and the grid they run on.
///
/// 100 x 5 ms is half a second of virtual time and the same grid the #115
/// workload's deferral runs on, so the two are read against each other. Long
/// enough that a per-period drift of one tick would be visible as 100 of them in
/// the total, which is the point of reporting the sum as well as the maximum.
const PERIODS_WANTED: u32 = 100;
const PERIOD_US: u32 = 5_000;

/// One period is deliberately overrun, so that the next deadline is already in
/// the past when it is set.
///
/// The 50th, so it is neither the first (which would test the start-up path
/// instead) nor the last (which would leave nothing after it to show that the
/// queue recovered). It spins for one and a half periods; `set_compare` is then
/// called with an instant `PERIOD_US / 2` behind `now`.
const OVERRUN_AT: u32 = 50;

mod device {
    pub mod interrupt {
        pub use riscv::interrupt::Interrupt as CoreInterrupt;

        #[derive(Clone, Copy)]
        pub enum Hart {
            H0 = 0,
        }
    }

    pub struct Msip(*mut u32);

    impl Msip {
        pub fn pend(&self) {
            // SAFETY: the CLINT window. Bit 0 is the only bit implemented.
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

    #[allow(clippy::upper_case_acronyms)]
    pub struct CLINT;

    impl CLINT {
        pub fn mswi() -> Mswi {
            Mswi
        }
    }
}

/// The monotonic itself.
///
/// `src/timer.rs` is the reference for every register access here and its module
/// comment is the reason for the shapes: the high-low-high retry in [`mtime`]
/// because a 64-bit read on rv32 is two loads that can straddle a carry, and the
/// three stores in [`set_mtimecmp`] because two stores cannot be atomic and the
/// intermediate value is a real deadline the comparator will act on.
mod mono {
    use super::target;
    use rtic_monotonics::{TimerQueueBackend, TimerQueueBasedMonotonic};
    use rtic_time::timer_queue::TimerQueue;

    const MTIME_LO: usize = 0xbff8;
    const MTIME_HI: usize = 0xbffc;
    const MTIMECMP_LO: usize = 0x4000;
    const MTIMECMP_HI: usize = 0x4004;

    fn read(offset: usize) -> u32 {
        // SAFETY: the CLINT window, uncached on the SoC and a device on `virt`.
        unsafe { core::ptr::read_volatile((target::CLINT_BASE + offset) as *const u32) }
    }

    fn write(offset: usize, value: u32) {
        // SAFETY: as above.
        unsafe { core::ptr::write_volatile((target::CLINT_BASE + offset) as *mut u32, value) }
    }

    pub struct ClintBackend;

    static TIMER_QUEUE: TimerQueue<ClintBackend> = TimerQueue::new();

    impl ClintBackend {
        /// Take `mtimecmp` and start the queue.
        ///
        /// `src/timer.rs::start` cannot also run in this binary: there is one
        /// comparator and this is now its owner.
        pub fn start() {
            // Out of reach until the queue asks for a deadline, so that a reset
            // value of zero -- which is QEMU's -- does not fire immediately.
            write(MTIMECMP_LO, u32::MAX);
            write(MTIMECMP_HI, u32::MAX);
            TIMER_QUEUE.initialize(ClintBackend {});
            // SAFETY: the handler is installed at link time and the deadline is
            // programmed out of reach above.
            unsafe {
                riscv::interrupt::enable_interrupt(riscv::interrupt::Interrupt::MachineTimer)
            };
        }
    }

    impl TimerQueueBackend for ClintBackend {
        type Ticks = u64;

        fn now() -> u64 {
            loop {
                let high = read(MTIME_HI);
                let low = read(MTIME_LO);
                if read(MTIME_HI) == high {
                    return ((high as u64) << 32) | low as u64;
                }
            }
        }

        fn set_compare(instant: u64) {
            // LOW FIRST, to a value no deadline can match, so that no
            // combination of the old high half and the new one is a time the
            // comparator will fire on. `src/timer.rs:197` and its comment.
            write(MTIMECMP_LO, u32::MAX);
            write(MTIMECMP_HI, (instant >> 32) as u32);
            write(MTIMECMP_LO, instant as u32);
        }

        /// Nothing. The CLINT's interrupt is a LEVEL -- `mtime >= mtimecmp`,
        /// with no acknowledge register -- so the only thing that lowers it is
        /// moving the deadline, which `on_monotonic_interrupt` does through
        /// `set_compare` or `disable_timer` immediately after this returns.
        fn clear_compare_flag() {}

        /// A deadline of zero is always in the past, so the comparator asserts
        /// on the next cycle. The ESP32-C3 backend calls its own handler
        /// directly because its part cannot do this; the CLINT can.
        fn pend_interrupt() {
            write(MTIMECMP_LO, 0);
            write(MTIMECMP_HI, 0);
        }

        /// The queue is empty: push the deadline out of reach rather than clear
        /// `mie.MTIE`, so that `now()` keeps working and nothing has to be
        /// re-enabled on the next insert.
        fn disable_timer() {
            write(MTIMECMP_LO, u32::MAX);
            write(MTIMECMP_HI, u32::MAX);
        }

        fn timer_queue() -> &'static TimerQueue<ClintBackend> {
            &TIMER_QUEUE
        }
    }

    /// The monotonic RTIC's `delay`/`delay_until` come from.
    ///
    /// Written out rather than through `rtic_monotonics`' per-part macro,
    /// because the macro exists to name a fixed frequency and this one is the
    /// gateware's: `target::TIME_HZ` is `SYNC_HZ` on the board and 10 MHz under
    /// QEMU, and both are compile-time constants.
    pub struct Mono;

    impl TimerQueueBasedMonotonic for Mono {
        type Backend = ClintBackend;
        type Instant = fugit::Instant<u64, 1, { target::TIME_HZ }>;
        type Duration = fugit::Duration<u64, 1, { target::TIME_HZ }>;
    }
}

/// The monotonic's interrupt. One line, and it is the whole wiring.
#[riscv_rt::core_interrupt(riscv::interrupt::Interrupt::MachineTimer)]
fn machine_timer() {
    use rtic_monotonics::TimerQueueBackend;
    // SAFETY: called from the monotonic timer's own interrupt, which is what
    // `on_monotonic_interrupt` requires.
    unsafe { mono::ClintBackend::timer_queue().on_monotonic_interrupt() };
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

#[rtic::app(device = device, peripherals = false, backend = H0)]
mod app {
    use super::{
        clock, device, mono, target, wl_report, DONE, LATE_FIRE, OVERRUN_AT, PERIODS,
        PERIODS_WANTED, PERIOD_US, TOTAL_LATE, WORST_EARLY, WORST_LATE,
    };
    use core::sync::atomic::Ordering;
    use rtic_monotonics::Monotonic;

    #[shared]
    struct Shared {}

    #[local]
    struct Local {
        console: wl_report::Console,
    }

    #[init]
    fn init(_cx: init::Context) -> (Shared, Local) {
        let mut console = wl_report::Console::new(target::UART_BASES[0]);
        console.banner("CLINT monotonic on RTIC");

        mono::ClintBackend::start();
        periodic::spawn().ok();

        (Shared {}, Local { console })
    }

    /// A periodic task on an absolute grid.
    ///
    /// `delay_until`, not `delay`: a period that overran must not push the next
    /// one out, which is the property `src/timer.rs` gets by adding to the
    /// deadline rather than reloading from now. It is also what makes the
    /// lateness figure a measurement of the monotonic rather than of the body.
    ///
    /// An async software task, which the SLIC backend supports without being
    /// told: `rtic-macros`'s `pre_init_preprocessing` REJECTS an explicit
    /// `dispatchers` argument and synthesises one SLIC source per software-task
    /// priority instead.
    #[task(priority = 1)]
    async fn periodic(_cx: periodic::Context) {
        let period = <mono::Mono as Monotonic>::Duration::from_ticks(
            (target::TIME_HZ as u64 * PERIOD_US as u64) / 1_000_000,
        );
        let mut deadline = mono::Mono::now() + period;

        for index in 0..PERIODS_WANTED {
            mono::Mono::delay_until(deadline).await;

            let now = mono::Mono::now();
            let want = deadline.ticks();
            let got = now.ticks();
            // The period after the overrun is late BY CONSTRUCTION -- its
            // deadline had already passed when it was asked for -- so it is
            // reported by itself as `past` rather than folded into the jitter it
            // would otherwise dominate.
            let deliberate = index == OVERRUN_AT + 1;
            if got >= want {
                let late = (got - want) as u32;
                if !deliberate {
                    WORST_LATE.fetch_max(late, Ordering::Relaxed);
                    TOTAL_LATE.fetch_add(late, Ordering::Relaxed);
                }
            } else {
                // A monotonic that returns early is a defect, not a jitter
                // figure, so it is counted separately rather than folded in.
                WORST_EARLY.fetch_max((want - got) as u32, Ordering::Relaxed);
            }
            PERIODS.fetch_add(1, Ordering::Relaxed);

            deadline += period;

            if index == OVERRUN_AT {
                // Spend past the next deadline on purpose, so that the
                // `delay_until` after this one is asked for an instant that has
                // already gone. `LATE_FIRE` records how far behind it was.
                let until = mono::Mono::now().ticks()
                    + (target::TIME_HZ as u64 * (PERIOD_US as u64 + PERIOD_US as u64 / 2))
                        / 1_000_000;
                while mono::Mono::now().ticks() < until {
                    core::hint::spin_loop();
                }
                LATE_FIRE.store(
                    (mono::Mono::now().ticks() - deadline.ticks()) as u32,
                    Ordering::Relaxed,
                );
            }
        }
        DONE.store(1, Ordering::Release);
    }

    #[idle(local = [console])]
    fn idle(cx: idle::Context) -> ! {
        let console = cx.local.console;
        while DONE.load(Ordering::Acquire) == 0 {
            core::hint::spin_loop();
        }

        let per_us = target::TIME_HZ / 1_000_000;
        let periods = PERIODS.load(Ordering::Relaxed).max(1);
        console.monotonic(
            (periods, PERIODS_WANTED),
            PERIOD_US,
            (
                WORST_LATE.load(Ordering::Relaxed) / per_us,
                TOTAL_LATE.load(Ordering::Relaxed) / periods / per_us,
                TOTAL_LATE.load(Ordering::Relaxed),
            ),
            WORST_EARLY.load(Ordering::Relaxed),
            (PERIOD_US / 2, LATE_FIRE.load(Ordering::Relaxed)),
            target::TIME_HZ,
        );
        loop {
            core::hint::spin_loop();
        }
    }

    // `clock` and `mono` are both used above; this keeps the unused-import
    // warning honest without an `#[allow]`.
    #[allow(dead_code)]
    fn _uses(_: clock::Instant) {}
}
