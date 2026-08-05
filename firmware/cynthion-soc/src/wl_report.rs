//! The console for the workload and monotonic binaries, and the only place in
//! them that formats.
//!
//! `src/bin/workload_bare.rs`, `src/bin/workload_rtic.rs` and
//! `src/bin/mono_rtic.rs` all carry a `#[riscv_rt::core_interrupt]`, which makes
//! them handler modules: they may not contain `write!`, `writeln!`, `fmt::Write`
//! or the name `Uart` (`scripts/soc_irq_log_check.py`). That is the right rule —
//! `Uart::put` spins on `LSR.THRE`, and a handler that spins on a
//! level-sensitive source presents as a dead CPU with a running clock — and this
//! is how those three obey it, the same way `src/usb_report.rs` does for the USB
//! spikes and `src/events.rs` does for the shell.
//!
//! Every function here runs in normal context: `#[idle]`, or `main` before the
//! run starts. None is reachable from a handler, and the argument is ownership
//! rather than discipline — the `Uart` is inside [`Console`], `Console` is a
//! local of `#[idle]`, and a handler has nowhere to be handed one from.
//!
//! Unlike `src/usb_report.rs` this wraps `src/uart.rs`'s real `Uart` rather than
//! a private 16550, because these binaries include `uart.rs` anyway: their PLIC
//! front ends need `UartRx`, the receive-only half with no transmit method.

use core::fmt::Write;

use crate::uart::Uart;

/// `uart::report_errors` calls `crate::log!`, and `src/bin/mono_rtic.rs` cannot
/// have `src/log.rs`: that module stamps every line from `timer::millis`, and
/// `src/timer.rs` cannot be in that binary at all -- it installs a
/// `MachineTimer` handler, and the monotonic there owns that vector and the one
/// comparator behind it. So: the same macro without the stamp.
///
/// The two workload binaries include `src/log.rs` and get the stamped one.
#[cfg(feature = "rticmono")]
#[macro_export]
macro_rules! log {
    ($uart:expr, $($arg:tt)*) => {
        {
            let _ = ::core::writeln!($uart, "{}", ::core::format_args!($($arg)*));
        }
    };
}

/// The transmit half, owned by whoever is printing.
pub struct Console(Uart);

impl Console {
    pub fn new(base: usize) -> Self {
        let mut uart = Uart::new(base);
        uart.init();
        Console(uart)
    }

    pub fn banner(&mut self, what: &str) {
        let _ = writeln!(self.0, "Cynthion RISC-V SoC: {}", what);
    }

    pub fn usage(&mut self) {
        let _ = self.0.write_str("usage: usb <events>\n");
    }

    /// Claims against completions. A claim that is never completed gates its
    /// source off for the rest of the session, so the two are counted rather
    /// than argued -- see `src/plic.rs`.
    pub fn plic(&mut self, model: &str, claims: u32, completes: u32) {
        let _ = writeln!(
            self.0,
            "  {}    claims {} completes {}",
            model, claims, completes
        );
    }

    /// What RTIC's `#[shared]` resource holds, beside what the PLIC did.
    pub fn shared(&mut self, events: u32, defers: u32) {
        let _ = writeln!(
            self.0,
            "  rtic    shared events {} defers {}",
            events, defers
        );
    }

    /// The 1 ms tick's own worst case. `late` is the detector that would see a
    /// long `critical_section` if there were one.
    pub fn tick(&mut self, ticks: u32, cost_us: u32, late_us: u32) {
        let _ = writeln!(
            self.0,
            "  tick    ticks {} worst cost {} us  worst late {} us",
            ticks, cost_us, late_us
        );
    }

    /// `(count, instructions each)` for each stage of an RTIC dispatch.
    pub fn probe(&mut self, pend: (u32, u32), trap: (u32, u32), lock: (u32, u32)) {
        let _ = writeln!(
            self.0,
            "  probe   pend {} x {} instr  trap->task {} x {} instr  \
             lock {} x {} instr",
            pend.0, pend.1, trap.0, trap.1, lock.0, lock.1
        );
    }

    /// Critical sections: how many, how long in total, the mean, the worst, and
    /// the measured cost of an EMPTY one, which is this build's instrument
    /// floor and what the other three should be read against.
    pub fn critical_sections(&mut self, taken: u32, total: u32, worst: u32, floor: u32) {
        let _ = writeln!(
            self.0,
            "  cs      taken {} total {} instr  mean {} worst {} floor {} instr",
            taken,
            total,
            total / taken.max(1),
            worst,
            floor
        );
    }

    /// `usb <n>` on the superloop binary: `workload::command` is the run and the
    /// scheduler both.
    #[cfg(feature = "wlbare")]
    pub fn run(&mut self, rest: &[u8]) {
        crate::workload::command(&mut self.0, rest);
    }

    /// The end of a run on the RTIC binary, where `#[idle]` waited rather than
    /// scheduled.
    #[cfg(feature = "rticwl")]
    pub fn finish(&mut self, started: (u32, u32)) {
        crate::workload::finish(&mut self.0, started);
    }

    /// The monotonic's report. Lateness against an ABSOLUTE grid, so a period
    /// that overran cannot push the next one out; `early` is a defect and not
    /// jitter, which is why it is a line of its own.
    #[cfg(feature = "rticmono")]
    pub fn monotonic(
        &mut self,
        periods: (u32, u32),
        period_us: u32,
        late: (u32, u32, u32),
        early: u32,
        past: (u32, u32),
        clock_hz: u32,
    ) {
        let _ = writeln!(
            self.0,
            "mono   periods {} of {} at {} us on an absolute grid",
            periods.0, periods.1, period_us
        );
        let _ = writeln!(
            self.0,
            "  late    worst {} us  mean {} us  total {} ticks",
            late.0, late.1, late.2
        );
        let _ = writeln!(self.0, "  early   worst {} ticks (any is a defect)", early);
        let _ = writeln!(
            self.0,
            "  past    one deadline set {} us behind now, fired {} ticks late",
            past.0, past.1
        );
        let _ = writeln!(self.0, "  clock   {} Hz", clock_hz);
    }
}
