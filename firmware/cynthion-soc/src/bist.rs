//! Drive the HyperRAM BIST engine from the CPU. See #226.
//!
//! # Why the CPU drives this
//!
//! Every HyperRAM figure this project has recorded was taken with at least one
//! broken instrument. Two of the four were only fixed on 2026-08-06: a negative
//! control that armed *after* the engine started, and a JTAG register readback
//! that slips a bit below a `sync`/TCK ratio of about four. So nothing measured
//! before then discriminates, and the numbers have to be re-established from
//! zero rather than confirmed.
//!
//! This is the rig for that. The engine is a CSR peripheral, not a memory
//! window, so the measurement path has no `JTAGRegisterInterface` in it and no
//! decoder, cache, arbiter or `RegisteredResponse` bubble between the engine and
//! the part. The unfinished SoC DQS write path is therefore not in the way.
//!
//! # The rule this module enforces
//!
//! **A cell that passes without its negative control having fired is recorded as
//! NO RESULT, not as a pass.** A comparator that never fires and a part that
//! never errs produce identical output, and this project has mistaken one for
//! the other more than once. [`Cell::verdict`] is the only way to read a result
//! and it cannot return `Pass` without both halves.
//!
//! # Register window
//!
//! One 32-bit CSR per engine register at `4 * address`, so adding a register in
//! the gateware does not move the others. The addresses are the engine's own
//! (`hyperram_ceiling_top.py`), deliberately not renumbered: two rigs sharing a
//! numbering is what lets a number from one be compared with a number from the
//! other.
//!
//! Each address appears **twice**, because no `amaranth_soc` CSR field is both
//! CPU-writable and gateware-driven, and which one an address needs is not known
//! until the engine elaborates -- after the bank must exist. So:
//!
//! ```text
//! base + 0x000 + 4*N    parameter N, the CPU writes
//! base + 0x100 + 4*N    result N, the engine drives
//! ```
//!
//! [`Bist::write`] therefore targets the low window and [`Bist::read`] the high
//! one, which is not an arbitrary convention: every register this file writes is
//! an engine parameter and every one it reads is an engine result. Reading a
//! parameter's number out of the result window yields zero rather than an error,
//! so [`Bist::present`] is the guard -- `ID` is a result, and if the offset were
//! wrong it would not match `APPLET_ID` and no measurement would be taken.
//! `BistCsrTransport::RESULT_WINDOW` is the same constant on the gateware side.

#![allow(dead_code)]

use crate::uart::Uart;
use core::fmt::Write;

/// Engine register numbers, from `gateware/probes/hyperram/hyperram_ceiling_top.py`.
///
/// Kept as the engine's numbers rather than re-indexed: the JTAG applet and this
/// rig address the same window, and a divergence here would make their results
/// incomparable without anything failing to build.
pub mod reg {
    pub const ID: usize = 1;
    pub const STATUS: usize = 2;
    pub const WRITE_CYCLES: usize = 3;
    pub const READ_CYCLES: usize = 4;
    pub const ERRORS: usize = 7;
    pub const WORDS: usize = 8;
    pub const DIE: usize = 9;
    pub const CLOCK: usize = 10;
    pub const CONFIG: usize = 11;
    pub const BAD_INDEX: usize = 12;
    pub const BAD_GOT: usize = 13;
    pub const BAD_WANT: usize = 14;
    pub const CONTROL: usize = 15;
    pub const READCLKSEL: usize = 16;
    pub const DEVICE_CR0: usize = 19;
    pub const DEVICE_CR1: usize = 20;
    pub const PASS_LIMIT: usize = 21;
}

/// `REG_CONTROL` bits, from `BISTHarness`.
pub mod control {
    pub const GO: u32 = 1 << 0;
    pub const NEGATIVE: u32 = 1 << 1;
}

/// `REG_STATUS` bits, from `BISTHarness`.
pub mod status {
    pub const BUSY: u32 = 1 << 0;
    pub const DONE: u32 = 1 << 1;
    pub const ERROR: u32 = 1 << 2;
    pub const NEGATIVE: u32 = 1 << 3;
}

/// The applet's own identifier, "HRC1". Read back before anything else: a rig
/// that is not there reads as zeroes, and zero errors from a peripheral that
/// does not exist is the most flattering wrong answer available.
pub const APPLET_ID: u32 = 0x4852_4331;

/// What one cell of the matrix was run with.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Axes {
    /// `CR0[14:12]`, the part's output drive. 0..=7, 19..115 ohms.
    pub drive: u8,
    /// `CR1[6]`: false selects the differential clock the board is wired for.
    pub single_ended_clock: bool,
    /// `READCLKSEL` 0..=7, which shifts the DQSBUFM read pulse by T/4 a step.
    pub readclksel: u8,
}

/// What one cell produced, with the evidence that it means anything.
#[derive(Clone, Copy)]
pub struct Cell {
    pub axes: Axes,
    /// Errors on the real pass.
    pub errors: u32,
    /// Words compared on the real pass. Zero means the engine did not run.
    pub words: u32,
    /// Errors on the deliberately-wrong pass. Must be non-zero for a verdict.
    pub control_errors: u32,
    /// Words compared on the control pass.
    pub control_words: u32,
}

/// The only thing a cell can honestly be.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// Zero errors, and the control fired. The only reportable success.
    Pass,
    /// Errors on the real pass, and the control fired.
    Fail(u32),
    /// The control did NOT fire, so the comparator is not shown to work here.
    /// Not a pass and not a failure: no result at all.
    NoResult,
}

impl Cell {
    /// The verdict, which cannot be `Pass` without the control having fired.
    ///
    /// This is the whole reason the type exists. Reading `errors == 0` directly
    /// is how "zero errors at every rung" got recorded from a comparator that
    /// was never armed.
    pub fn verdict(&self) -> Verdict {
        // The control must have run AND must have failed. A control that
        // reports zero errors against a value the part cannot return means the
        // comparison is not happening.
        if self.control_words == 0 || self.control_errors == 0 {
            return Verdict::NoResult;
        }
        if self.words == 0 {
            return Verdict::NoResult;
        }
        if self.errors == 0 {
            Verdict::Pass
        } else {
            Verdict::Fail(self.errors)
        }
    }
}

/// Where the BIST peripheral sits, matching `HYPERRAM_BIST_BASE` in
/// `gateware/soc/top.py`.
///
/// A literal rather than a PAC constant, and deliberately: the PAC is generated
/// from whichever variant the gateware flag selects, and only one of the two can
/// be committed. Taking this from the PAC would mean committing the measurement
/// variant's map, which would leave the shipping image checking itself against a
/// map it does not have. `tests/test_bist_constants.py` asserts this equals the
/// gateware's, so the two cannot drift silently.
pub const BASE: usize = 0xf000_0700;

/// The BIST peripheral's register window.
pub struct Bist {
    base: usize,
}

impl Bist {
    /// # Safety
    /// `base` must be the BIST peripheral's CSR base from the generated PAC.
    pub const unsafe fn new(base: usize) -> Self {
        Self { base }
    }

    /// Byte offset of the result window. Mirrors
    /// `BistCsrTransport.RESULT_WINDOW` in `gateware/soc/peripherals/bist_csr.py`.
    const RESULT_WINDOW: usize = 0x100;

    /// A result the engine drives. See the module docs for why this is not the
    /// same address as the parameter of the same number.
    pub fn read(&self, reg: usize) -> u32 {
        let addr = (self.base + Self::RESULT_WINDOW + 4 * reg) as *const u32;
        unsafe { core::ptr::read_volatile(addr) }
    }

    /// A parameter the CPU sets and the engine reads.
    pub fn write(&self, reg: usize, value: u32) {
        let addr = (self.base + 4 * reg) as *mut u32;
        unsafe { core::ptr::write_volatile(addr, value) }
    }

    /// Is the engine present and answering? Checked before any measurement.
    pub fn present(&self) -> bool {
        self.read(reg::ID) == APPLET_ID
    }

    /// Set the axis values. Held still for the whole pass, which is what makes
    /// the domain crossing safe without a FIFO.
    pub fn configure(&self, axes: &Axes) {
        self.write(reg::READCLKSEL, axes.readclksel as u32 & 0x7);
        // Bit 16 is "apply this"; without it the part keeps its power-on
        // configuration. The gateware sweep once left this clear and swept a
        // drive axis that therefore did nothing -- eight rows that should have
        // been identical, and were not.
        self.write(reg::DEVICE_CR0, (1 << 16) | 0x8f2f | ((axes.drive as u32 & 0x7) << 12));
        self.write(reg::DEVICE_CR1,
                   (1 << 16) | if axes.single_ended_clock { 0xffc1 } else { 0xff81 });
    }

    /// Run one pass and wait for it. `negative` builds the deliberately-wrong
    /// comparison, which must report every word wrong.
    ///
    /// Returns `(errors, words)`. `words == 0` means the engine never ran and
    /// the caller must not treat the zero errors as a result.
    pub fn run(&self, negative: bool, passes: u32) -> (u32, u32) {
        let (errors, words, _) = self.run_verbose(negative, passes);
        (errors, words)
    }

    /// As `run`, plus whether the poll timed out and the last STATUS seen.
    ///
    /// Returns `(errors, words, status)`. On a timeout `words` is zero, which
    /// `verdict()` reads as NoResult -- a wedged engine can never be reported as
    /// a pass, however its counters happen to read.
    pub fn run_verbose(&self, negative: bool, passes: u32) -> (u32, u32, u32) {
        self.write(reg::PASS_LIMIT, passes);
        // Arm the control BEFORE `go`. Arming after the engine has started is
        // exactly the defect that made every pre-2026-08-06 zero meaningless:
        // the comparison had already happened by the time the mode changed.
        let mode = if negative { control::NEGATIVE } else { 0 };
        self.write(reg::CONTROL, mode);
        self.write(reg::CONTROL, mode | control::GO);
        self.write(reg::CONTROL, mode);

        // Poll, bounded. The bound has to be DERIVED, not picked: at
        // 200_000_000 spins a wedged engine took long enough per cell that a
        // 128-cell sweep was indistinguishable from a lockup, which is how this
        // was first reported.
        //
        // A pass is a burst of a few thousand words. Even allowing a very
        // generous 1e6 `hr` cycles for one -- 20 ms at the slowest rung this rig
        // builds, `hr` 50 MHz for CK 100 -- and a CPU at 30 MHz, that is about
        // 600k CPU cycles. This spin is a CSR read plus a mask and a branch,
        // call it 10 cycles, so a legitimate pass finishes inside ~60k spins.
        //
        // 2e6 is ~30x that margin and still gives up in under a second, so a
        // wedge is REPORTED rather than waited on. If a future rung genuinely
        // needs longer, raise it with the arithmetic, not by rounding up.
        const LIMIT: u32 = 2_000_000;
        let mut spins: u32 = 0;
        let mut last = self.read(reg::STATUS);
        while last & status::DONE == 0 {
            spins = spins.wrapping_add(1);
            if spins > LIMIT {
                // Zero words, deliberately: `verdict()` turns that into
                // NoResult. The status goes back so the caller can say WHY.
                return (0, 0, last);
            }
            last = self.read(reg::STATUS);
        }
        (self.read(reg::ERRORS), self.read(reg::WORDS), last)
    }

    /// One cell: the real pass and its control, in that order.
    pub fn cell(&self, axes: Axes, passes: u32) -> Cell {
        let (errors, words) = self.run(false, passes);
        let (control_errors, control_words) = self.run(true, passes);
        Cell { axes, errors, words, control_errors, control_words }
    }

    /// As `cell`, reporting the STATUS each half ended on.
    pub fn cell_verbose(&self, axes: Axes, passes: u32) -> (Cell, u32, u32) {
        let (errors, words, st_real) = self.run_verbose(false, passes);
        let (control_errors, control_words, st_ctrl) = self.run_verbose(true, passes);
        (Cell { axes, errors, words, control_errors, control_words }, st_real, st_ctrl)
    }

    /// Decode STATUS for a human. Why a cell produced nothing is usually here.
    pub fn describe_status(&self, uart: &mut Uart, label: &str, st: u32) {
        let _ = writeln!(
            uart, "      {}: status {:#06x} busy={} done={} error={} negative={}",
            label, st,
            (st & status::BUSY != 0) as u8,
            (st & status::DONE != 0) as u8,
            (st & status::ERROR != 0) as u8,
            (st & status::NEGATIVE != 0) as u8);
    }
}

/// Sweep drive x clock x readclksel and print a row per cell.
///
/// Printed as it goes rather than collected: the gateware sweep dies at cell 0
/// with no way to see why (#210), and a row per cell means a hang names the cell
/// it hung on.
pub fn sweep(uart: &mut Uart, bist: &Bist, passes: u32) {
    sweep_verbose(uart, bist, passes, false)
}

/// The sweep, optionally narrating every cell.
///
/// `verbose` prints the axes BEFORE the pass runs and decodes STATUS after each
/// half. Without it the row is printed only once both halves have returned, so a
/// cell that never completes names nothing -- which is how `hr sweep` came to
/// read as a lockup rather than as a wedged engine on a particular setting.
pub fn sweep_verbose(uart: &mut Uart, bist: &Bist, passes: u32, verbose: bool) {
    if !bist.present() {
        let _ = writeln!(uart, "bist: no engine at this address (id {:#010x})",
                         bist.read(reg::ID));
        return;
    }

    let _ = writeln!(uart, "bist: ck {} kHz, config {:#x}, id {:#010x}",
                     bist.read(reg::CLOCK), bist.read(reg::CONFIG),
                     bist.read(reg::ID));
    bist.describe_status(uart, "at rest", bist.read(reg::STATUS));
    let _ = writeln!(uart, "bist: {} passes per cell, 128 cells", passes);
    let _ = writeln!(uart, "drive  clk  sel   errors     words   control  verdict");

    for drive in 0u8..8 {
        for single_ended in [false, true] {
            for readclksel in 0u8..8 {
                let axes = Axes { drive, single_ended_clock: single_ended, readclksel };
                bist.configure(&axes);

                // BEFORE the pass, so a cell that never returns is named by the
                // last line printed rather than leaving a blank terminal.
                if verbose {
                    let _ = writeln!(uart, "  cell drive {} {} sel {} -- running",
                                     drive,
                                     if single_ended { "se" } else { "dif" },
                                     readclksel);
                }

                let (cell, st_real, st_ctrl) = bist.cell_verbose(axes, passes);
                if verbose {
                    bist.describe_status(uart, "real   ", st_real);
                    bist.describe_status(uart, "control", st_ctrl);
                }
                let verdict = match cell.verdict() {
                    Verdict::Pass => "PASS",
                    Verdict::Fail(_) => "fail",
                    // A timeout is reported as itself. It reaches NoResult by
                    // the same door as a control that did not fire, and the two
                    // want completely different next steps.
                    Verdict::NoResult if cell.words == 0 && cell.control_words == 0 =>
                        "NO RESULT -- engine never completed (timeout)",
                    Verdict::NoResult => "NO RESULT -- control did not fire",
                };
                let _ = writeln!(
                    uart, "{:5}  {:3}  {:3}  {:8}  {:8}  {:8}  {}",
                    drive,
                    if single_ended { "se" } else { "dif" },
                    readclksel,
                    cell.errors, cell.words, cell.control_errors, verdict);
            }
        }
    }
    let _ = writeln!(uart, "bist: sweep complete");
}


// NOTE: nothing in this repo runs `cargo test`, and this crate cannot host-test
// as it stands -- it is `#![no_std]` with its own panic handler, so
// `cargo test --target x86_64-unknown-linux-gnu` fails on a duplicate
// `panic_impl` lang item and a missing `println!`. The tests below therefore do
// not run anywhere today; `power_rails.rs` has had the same problem for longer.
//
// They are kept because the property they state is the one this module exists to
// enforce, and because making them run is a small crate change (a `[lib]` target
// over the pure logic) rather than a rewrite. See the issue.
#[cfg(test)]
mod tests {
    use super::*;

    fn axes() -> Axes {
        Axes { drive: 0, single_ended_clock: false, readclksel: 0 }
    }

    fn cell(errors: u32, words: u32, control_errors: u32, control_words: u32) -> Cell {
        Cell { axes: axes(), errors, words, control_errors, control_words }
    }

    /// The property this whole rig exists to enforce.
    ///
    /// "Zero errors" and "the comparator never ran" produce the same number, and
    /// this project has recorded the second as the first more than once -- a
    /// ceiling sweep with a control that armed after the engine started, a
    /// stress run whose pattern repeated every 256 words, a READCLKSEL sweep
    /// taken while the controller read a CK early. Every one reported clean.
    #[test]
    fn zero_errors_without_a_fired_control_is_not_a_pass() {
        // The control did not fire: the comparator is not shown to work here,
        // so the clean real pass means nothing.
        assert!(matches!(cell(0, 4096, 0, 4096).verdict(), Verdict::NoResult));
        // The control did not even run.
        assert!(matches!(cell(0, 4096, 0, 0).verdict(), Verdict::NoResult));
    }

    #[test]
    fn a_pass_needs_both_halves() {
        // Clean real pass AND a control that reported every word wrong.
        assert!(matches!(cell(0, 4096, 4096, 4096).verdict(), Verdict::Pass));
    }

    #[test]
    fn errors_are_a_failure_only_when_the_control_fired() {
        assert!(matches!(cell(17, 4096, 4096, 4096).verdict(), Verdict::Fail(17)));
        // Errors on both passes with no control evidence is still no result:
        // the comparator might be reporting everything wrong unconditionally.
        assert!(matches!(cell(17, 4096, 0, 4096).verdict(), Verdict::NoResult));
    }

    #[test]
    fn an_engine_that_never_ran_is_not_a_pass() {
        // A timed-out poll returns (0, 0). Zero errors over zero words is the
        // most flattering wrong answer available.
        assert!(matches!(cell(0, 0, 4096, 4096).verdict(), Verdict::NoResult));
    }
}
