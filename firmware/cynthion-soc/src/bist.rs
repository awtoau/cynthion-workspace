//! Drive the HyperRAM BIST engine from the CPU. See #226.
//!
//! # Why the CPU drives this
//!
//! Every HyperRAM figure this project has recorded was taken with at least one
//! broken instrument -- five overlapping faults fixed between 5 and 7 August
//! 2026. Nothing measured before then discriminates, so the numbers have to be
//! re-established from zero rather than confirmed.
//!
//! This is the rig for that. The engine is a CSR peripheral, not a memory
//! window, so the measurement path has no `JTAGRegisterInterface` in it (#204)
//! and no decoder, cache, arbiter or `RegisteredResponse` bubble between the
//! engine and the part. The unfinished SoC DQS write path is therefore not in
//! the way.
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
//! The addresses are the engine's own (`hyperram_ceiling_top.py`), deliberately
//! not renumbered: two rigs sharing a numbering is what lets a number from one
//! be compared with a number from the other.
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
//! [`Bist::write`] targets the low window and [`Bist::read`] the high one, which
//! is not an arbitrary convention: every register this file writes is an engine
//! parameter and every one it reads is an engine result. Reading a parameter's
//! number out of the result window yields zero rather than an error, so
//! [`Bist::present`] is the guard -- `ID` is a result, and if the offset were
//! wrong it would not match [`APPLET_ID`] and no measurement would be taken.

#![allow(dead_code)]

use crate::uart::Uart;
use core::fmt::Write;

/// Engine register numbers, from `gateware/probes/hyperram/hyperram_ceiling_top.py`.
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
    pub const ACTUAL: usize = 17;
    pub const GOLDEN: usize = 18;
    pub const DEVICE_CR0: usize = 19;
    pub const DEVICE_CR1: usize = 20;
    pub const PASS_LIMIT: usize = 21;
    /// The engine FSM's state, so a stall says WHERE rather than only that it
    /// stalled. The engine's own comment: without it a hung sweep is a silent
    /// poll loop, and the cell index does not say which state.
    pub const FSM_STATE: usize = 28;
    /// The controller HANDSHAKE, as three bits: `idle`, `recovery elapsed`,
    /// `start`. Not an FSM state -- see the engine, where the state number was
    /// declared and never bound, so it read zero and looked like "state 0".
    ///
    /// These are the two halves of what `READ_RECOVER` waits on, which is the
    /// state the engine parks in. Reading them separately says WHICH half is
    /// false; a state number never could.
    pub const CTRL_STATE: usize = 29;
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

/// Words per burst, from `BURST_WORDS` in the engine. The poll bound below is
/// derived from it, so the two must agree.
const BURST_WORDS: u32 = 128;

/// Where the BIST peripheral sits, matching `HYPERRAM_BIST_BASE` in
/// `gateware/soc/top.py`.
///
/// A literal rather than a PAC constant, and deliberately: the PAC is generated
/// from whichever variant the gateware flag selects, and only one of the two can
/// be committed. Taking this from the PAC would mean committing the measurement
/// variant's map, which would leave the shipping image checking itself against a
/// map it does not have. `tests/test_bist_constants.py` asserts this equals the
/// gateware's, so the two cannot drift silently.
pub const BASE: usize = 0xf000_0800;

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

/// The three verdicts a cell can carry, and there is no fourth.
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
        // The control must have run AND must have failed. A control reporting
        // zero errors against a value the part cannot return means the
        // comparison is not happening.
        if self.control_words == 0 || self.control_errors == 0 || self.words == 0 {
            return Verdict::NoResult;
        }
        if self.errors == 0 {
            Verdict::Pass
        } else {
            Verdict::Fail(self.errors)
        }
    }
}

/// Why a poll stopped.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Poll {
    Done,
    /// The bound expired. Carries the bound and the spins actually taken, so
    /// the report can say whether it was close or nowhere near.
    TimedOut { limit: u32, spins: u32 },
}

/// The BIST peripheral's register window.
pub struct Bist {
    base: usize,
}

impl Bist {
    /// # Safety
    /// `base` must be the BIST peripheral's CSR base.
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
        // configuration. The gateware sweep once left it clear and swept a
        // drive axis that therefore did nothing -- eight rows that should have
        // been identical, and were not.
        self.write(reg::DEVICE_CR0,
                   (1 << 16) | 0x8f2f | ((axes.drive as u32 & 0x7) << 12));
        self.write(reg::DEVICE_CR1,
                   (1 << 16) | if axes.single_ended_clock { 0xffc1 } else { 0xff81 });
    }

    /// How many poll spins one pass of `passes` bursts may legitimately take.
    ///
    /// **Waits for**: the engine's `done`, one write burst plus one read burst
    /// per pass.
    ///
    /// **Expected duration, and where it comes from**: a burst is
    /// `BURST_WORDS` = 128 words, one word per `hr` cycle, and a pass does two
    /// of them -- 256 `hr` cycles -- plus command, latency and tCSHI, call it
    /// 512 to be safe about the fixed part. The slowest rung this rig builds is
    /// CK 100 on the DQS path, where `hr` is 50 MHz, against a CPU at 60 MHz:
    /// 512 x 60/50 = 614 CPU cycles per pass. One spin is an uncached CSR read
    /// plus a mask and a branch; at ~10 cycles that is ~62 spins per pass.
    ///
    /// **Multiplier**: `SPINS_PER_PASS` is 80, about 1.3x that, and the whole
    /// thing is per-pass rather than a fixed ceiling so it tracks the parameter
    /// it depends on. `FLOOR` covers DLL lock and engine start-up once.
    ///
    /// **On expiry**: [`Poll::TimedOut`] carries the limit and the spins taken,
    /// `words` is forced to zero so [`Cell::verdict`] reads NoResult, and the
    /// caller prints all of it. A wedged engine can never be reported as a pass.
    ///
    /// The retired branch used a flat 2,000,000 here. At ~30x it made a wedged
    /// cell slow enough that a 128-cell sweep was indistinguishable from a
    /// lockup, which is how the wedge was first reported.
    fn poll_limit(passes: u32) -> u32 {
        const SPINS_PER_PASS: u32 = 80;
        const FLOOR: u32 = 2_000;
        passes.saturating_mul(SPINS_PER_PASS).saturating_add(FLOOR)
    }

    /// Run one pass and wait for it. `negative` builds the deliberately-wrong
    /// comparison, which must report every word wrong.
    ///
    /// Returns `(errors, words, status, poll)`. On a timeout `words` is zero,
    /// which `verdict()` reads as NoResult.
    pub fn run(&self, negative: bool, passes: u32) -> (u32, u32, u32, Poll) {
        self.write(reg::PASS_LIMIT, passes);
        // Arm the control BEFORE `go`. Arming after the engine has started is
        // exactly the defect that made every pre-2026-08-06 zero meaningless:
        // the comparison had already happened by the time the mode changed.
        let mode = if negative { control::NEGATIVE } else { 0 };
        self.write(reg::CONTROL, mode);
        self.write(reg::CONTROL, mode | control::GO);
        self.write(reg::CONTROL, mode);

        let limit = Self::poll_limit(passes);
        let mut spins: u32 = 0;
        let mut last = self.read(reg::STATUS);
        while last & status::DONE == 0 {
            spins = spins.wrapping_add(1);
            if spins > limit {
                return (0, 0, last, Poll::TimedOut { limit, spins });
            }
            last = self.read(reg::STATUS);
        }
        (self.read(reg::ERRORS), self.read(reg::WORDS), last, Poll::Done)
    }

    /// One cell: the real pass and its control, in that order.
    pub fn cell(&self, axes: Axes, passes: u32) -> (Cell, [u32; 2], [Poll; 2]) {
        let (errors, words, st_real, p_real) = self.run(false, passes);
        let (control_errors, control_words, st_ctrl, p_ctrl) = self.run(true, passes);
        (
            Cell { axes, errors, words, control_errors, control_words },
            [st_real, st_ctrl],
            [p_real, p_ctrl],
        )
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

    /// The two FSM states, which is where a stall actually is.
    ///
    /// Read TWICE with the second read after the first, so a state that is
    /// moving can be told from one that is parked. A single sample cannot
    /// distinguish "wedged in state 3" from "cycling through state 3", and those
    /// want opposite investigations.
    pub fn describe_fsm(&self, uart: &mut Uart) {
        let (engine_a, ctrl_a) = (self.read(reg::FSM_STATE), self.read(reg::CTRL_STATE));
        let (engine_b, ctrl_b) = (self.read(reg::FSM_STATE), self.read(reg::CTRL_STATE));
        let _ = writeln!(
            uart, "  fsm     engine {} -> {} {}",
            engine_a, engine_b,
            if engine_a == engine_b { "(parked)" } else { "(moving)" });
        // The two halves of READ_RECOVER's exit condition, named. Whichever is
        // 0 is the one holding the engine there.
        let _ = writeln!(
            uart, "  ctrl    idle={} recovered={} start={}  (a=%{:03b} b=%{:03b})",
            (ctrl_b & 1) as u8, ((ctrl_b >> 1) & 1) as u8, ((ctrl_b >> 2) & 1) as u8,
            ctrl_a & 0b111, ctrl_b & 0b111);
        // STICKY, and not cleared by `go`. A run that had to be rescued from a
        // stall is not a clean run, and this must survive into the next reading
        // -- a rig that recovers silently is a rig that reports a number taken
        // after something went wrong.
        if ctrl_b & 0b1000 != 0 {
            let _ = writeln!(
                uart, "  STALLED the controller failed to return to idle and \
                       the engine was reset (sticky; power-cycle to clear)");
        }
        let _ = (ctrl_a, ctrl_b);
    }

    /// What the engine says about itself, before anything is measured.
    pub fn describe(&self, uart: &mut Uart) {
        let _ = writeln!(uart, "  id      {:#010x} {}", self.read(reg::ID),
                         if self.present() { "HRC1" } else { "NOT THE ENGINE" });
        let _ = writeln!(uart, "  clock   {} kHz as built  config {:#x}",
                         self.read(reg::CLOCK), self.read(reg::CONFIG));
        self.describe_status(uart, "at rest", self.read(reg::STATUS));
        self.describe_fsm(uart);
        // Counters the engine drives from `hr`. If `hr` is not running at all,
        // every one of these is frozen -- which is the first thing to rule out,
        // because `clock` above is a constant baked in at elaboration and says
        // nothing about whether the PLL locked.
        let _ = writeln!(uart, "  cycles  write {}  read {}  words {}  errors {}",
                         self.read(reg::WRITE_CYCLES), self.read(reg::READ_CYCLES),
                         self.read(reg::WORDS), self.read(reg::ERRORS));
        let _ = writeln!(uart, "  compare actual {:#010x}  golden {:#010x}",
                         self.read(reg::ACTUAL), self.read(reg::GOLDEN));
    }
}

/// Print one cell's row, and the evidence when it produced nothing.
fn report(uart: &mut Uart, bist: &Bist, cell: &Cell, st: [u32; 2], poll: [Poll; 2],
          verbose: bool) {
    let verdict = match cell.verdict() {
        Verdict::Pass => "PASS",
        Verdict::Fail(_) => "fail",
        // A timeout reaches NoResult by the same door as a control that did not
        // fire, and the two want completely different next steps -- so they must
        // not print the same text.
        Verdict::NoResult
            if matches!(poll[0], Poll::TimedOut { .. })
                || matches!(poll[1], Poll::TimedOut { .. }) =>
            "NO RESULT -- engine never completed (timeout)",
        Verdict::NoResult => "NO RESULT -- control did not fire",
    };
    let _ = writeln!(
        uart, "{:5}  {:3}  {:3}  {:8}  {:8}  {:8}  {}",
        cell.axes.drive,
        if cell.axes.single_ended_clock { "se" } else { "dif" },
        cell.axes.readclksel,
        cell.errors, cell.words, cell.control_errors, verdict);

    // A silent expiry is worse than no bound at all: say which half, what the
    // limit was and how far it got.
    let mut stalled = false;
    for (label, p) in [("real", poll[0]), ("control", poll[1])] {
        if let Poll::TimedOut { limit, spins } = p {
            let _ = writeln!(
                uart, "      {} pass TIMED OUT after {} spins, limit {} \
                       -- engine never raised done",
                label, spins, limit);
            stalled = true;
        }
    }
    // WHERE it stalled, not merely that it did. A parked engine state and a
    // parked controller state are different faults: the first says the engine
    // never asked for a transaction, the second says it asked and got no answer.
    if stalled {
        bist.describe_fsm(uart);
    }
    if verbose {
        bist.describe_status(uart, "real   ", st[0]);
        bist.describe_status(uart, "control", st[1]);
    }
}

const HEADING: &str = "drive  clk  sel   errors     words   control  verdict";

/// One cell, by hand. The unit a sweep is made of, so nothing is exercised in a
/// sweep that was not first exercised alone.
pub fn one(uart: &mut Uart, bist: &Bist, axes: Axes, passes: u32) {
    if !gate(uart, bist) {
        return;
    }
    let _ = writeln!(uart, "{}", HEADING);
    bist.configure(&axes);
    let (cell, st, poll) = bist.cell(axes, passes);
    report(uart, bist, &cell, st, poll, true);
}

/// The rig's own smoke test: four cells, one CK, four capture phases.
///
/// From `docs/chips/hyperram/bist-plan.md`, and it is a test **of the rig**, not
/// of the part. The result that validates it is NOT four passes:
///
///   * at least one `PASS` -- the rig can report success
///   * at least one `fail` -- the rig can DETECT failure, without which a pass
///     is worthless
///   * the control fired in all four -- zero errors means something everywhere
///
/// Four passes is a failed smoke test: either every phase is genuinely good,
/// which is implausible, or the rig cannot see a fault. Four NO RESULTs is a
/// wedged engine, not a bad part.
pub fn smoke(uart: &mut Uart, bist: &Bist, passes: u32) {
    if !gate(uart, bist) {
        return;
    }
    let _ = writeln!(uart, "  four cells, one drive, four capture phases.");
    let _ = writeln!(uart, "  wanted: at least one PASS *and* at least one fail.");
    let _ = writeln!(uart, "  four passes means the rig cannot see a fault.");
    let _ = writeln!(uart, "{}", HEADING);

    let (mut passed, mut failed, mut nothing) = (0u32, 0u32, 0u32);
    for readclksel in 0u8..4 {
        let axes = Axes { drive: 3, single_ended_clock: false, readclksel };
        bist.configure(&axes);
        let (cell, st, poll) = bist.cell(axes, passes);
        match cell.verdict() {
            Verdict::Pass => passed += 1,
            Verdict::Fail(_) => failed += 1,
            Verdict::NoResult => nothing += 1,
        }
        report(uart, bist, &cell, st, poll, false);
    }

    let _ = writeln!(uart, "  {} pass, {} fail, {} no result", passed, failed, nothing);
    let _ = writeln!(uart, "  rig: {}", match (passed, failed, nothing) {
        (_, _, n) if n == 4 => "WEDGED -- fix the rig, this says nothing about the part",
        (_, _, n) if n > 0 => "INCOMPLETE -- some cells produced no result",
        (0, _, _) => "cannot report success -- no cell passed",
        (_, 0, _) => "NOT VALIDATED -- nothing failed, so a pass is not evidence",
        _ => "validated -- it can both pass and detect a fault",
    });
}

/// Sweep drive x clock x readclksel and print a row per cell.
///
/// Printed as it goes rather than collected: the gateware sweep died at cell 0
/// with no way to see why (#210), and a row per cell means a hang names the cell
/// it hung on.
pub fn sweep(uart: &mut Uart, bist: &Bist, passes: u32, verbose: bool) {
    if !gate(uart, bist) {
        return;
    }
    let _ = writeln!(uart, "  {} passes per cell, 128 cells", passes);
    let _ = writeln!(uart, "{}", HEADING);

    for drive in 0u8..8 {
        for single_ended_clock in [false, true] {
            for readclksel in 0u8..8 {
                let axes = Axes { drive, single_ended_clock, readclksel };
                bist.configure(&axes);
                // BEFORE the pass, so a cell that never returns is named by the
                // last line printed rather than leaving a blank terminal.
                if verbose {
                    let _ = writeln!(uart, "  cell drive {} {} sel {} -- running",
                                     drive,
                                     if single_ended_clock { "se" } else { "dif" },
                                     readclksel);
                }
                let (cell, st, poll) = bist.cell(axes, passes);
                report(uart, bist, &cell, st, poll, verbose);
            }
        }
    }
    let _ = writeln!(uart, "  sweep complete");
}

/// Refuse to measure through an engine that is not answering.
fn gate(uart: &mut Uart, bist: &Bist) -> bool {
    if bist.present() {
        return true;
    }
    let _ = writeln!(uart,
                     "  no engine at {:#010x} (id {:#010x}, want {:#010x})",
                     BASE, bist.read(reg::ID), APPLET_ID);
    let _ = writeln!(uart,
                     "  this image was built without CYNTHION_HYPERRAM_BIST=1");
    false
}

// NOTE: nothing in this repo runs `cargo test`, and this crate cannot host-test
// as it stands -- it is `#![no_std]` with its own panic handler. The tests below
// therefore do not run anywhere today; `power_rails.rs` has had the same problem
// for longer. They are kept because the property they state is the one this
// module exists to enforce.
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
    #[test]
    fn zero_errors_without_a_fired_control_is_not_a_pass() {
        assert!(matches!(cell(0, 4096, 0, 4096).verdict(), Verdict::NoResult));
        assert!(matches!(cell(0, 4096, 0, 0).verdict(), Verdict::NoResult));
    }

    #[test]
    fn a_pass_needs_both_halves() {
        assert!(matches!(cell(0, 4096, 4096, 4096).verdict(), Verdict::Pass));
    }

    #[test]
    fn errors_are_a_failure_only_when_the_control_fired() {
        assert!(matches!(cell(17, 4096, 4096, 4096).verdict(), Verdict::Fail(17)));
        assert!(matches!(cell(17, 4096, 0, 4096).verdict(), Verdict::NoResult));
    }

    #[test]
    fn an_engine_that_never_ran_is_not_a_pass() {
        assert!(matches!(cell(0, 0, 4096, 4096).verdict(), Verdict::NoResult));
    }

    /// The bound must track the parameter it depends on, not be a flat ceiling.
    #[test]
    fn the_poll_bound_scales_with_passes() {
        assert!(Bist::poll_limit(256) > Bist::poll_limit(16));
        // And it must not overflow into a tiny value at absurd inputs.
        assert!(Bist::poll_limit(u32::MAX) > Bist::poll_limit(1));
    }
}
