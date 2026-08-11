//! The BIST rig's arithmetic, with no register access in it.
//!
//! Split out so it can be tested on this machine (#337) --
//! `firmware/cynthion-soc-tests` includes this file verbatim. **Nothing here may
//! name `crate::` or touch MMIO**, or the split stops meaning anything.
//!
//! The reason it exists: `cr0_word`'s clear mask was `0x70f0` where it should
//! have been `0x70f8`, so `CR0[3]` kept its power-on 1 and variable latency was
//! structurally unreachable. 2048 cells of `bist all` reported `fix` results
//! under the `var` label for months (#335). Three lines of arithmetic, inside a
//! method that writes to hardware, in a file that already had a test module.

/// `CR0[15]`: 1 is normal operation, 0 is Deep Power Down. Forced set.
const CR0_FULL_POWER: u32 = 1 << 15;
/// `CR1[5]`: 1 is Hybrid Sleep. Forced clear.
const CR1_HYBRID_SLEEP: u32 = 1 << 5;

/// What one cell of the matrix was run with.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Axes {
    /// `CR0[14:12]`, the part's output drive. 0..=7, 19..115 ohms.
    pub drive: u8,
    /// `CR1[6]`: false selects the differential clock the board is wired for.
    pub single_ended_clock: bool,
    /// `READCLKSEL` 0..=7, which shifts the DQSBUFM read pulse by T/4 a step.
    pub readclksel: u8,
    /// `CR0[7:4]`, the part's initial latency code, and `CR0[3]` fixed/variable.
    ///
    /// A read that starts one clock early or late lands one device word off, so
    /// this is one candidate for a rotated compare pair. It is not the only one:
    /// the measured read eye turns the same residue into zero errors two capture
    /// phases away, which is why `latency` sweeps both axes (#421).
    pub latency: u8,
    pub fixed_latency: bool,
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

/// The verdict a row PRINTS and the tally COUNTS, from one place.
///
/// Folds shortness in: a cell that compared fewer words than it was asked for is
/// NO RESULT, not a pass -- one sweep row read `words 17` of 512 and scored PASS
/// beside rows that compared all of them. A short FAILURE stays a failure and
/// the row says `SHORT` beside it.
pub fn scored(cell: &Cell, expected: u32) -> Verdict {
    let short = cell.words < expected || cell.control_words < expected;
    match cell.verdict() {
        Verdict::Pass if short => Verdict::NoResult,
        other => other,
    }
}

/// Order for choosing which capture phase's row stands for a cell. Lower wins:
/// a pass beats every failure, fewer errors beats more, NO RESULT is last.
pub fn rank(cell: &Cell, expected: u32) -> (u8, u32) {
    match scored(cell, expected) {
        Verdict::Pass => (0, 0),
        Verdict::Fail(errors) => (1, errors),
        Verdict::NoResult => (2, 0),
    }
}

/// The centre of the widest run of passing phases in `passed`, bit N per phase.
///
/// Centre rather than first pass: it is the phase with margin either side. Runs
/// do NOT wrap -- a window over the ends reads as two runs and the wider one is
/// still inside it.
pub fn eye_centre(passed: u8, walked: u8) -> Option<u8> {
    let (mut best_start, mut best_len) = (0u8, 0u8);
    let (mut start, mut len) = (0u8, 0u8);
    for phase in 0..walked.min(8) {
        if passed >> phase & 1 == 0 {
            len = 0;
            continue;
        }
        if len == 0 {
            start = phase;
        }
        len += 1;
        if len > best_len {
            (best_start, best_len) = (start, len);
        }
    }
    (best_len > 0).then(|| best_start + (best_len - 1) / 2)
}

/// Which capture phases a row was chosen from, printed under it (#421).
///
/// A row naming one `sel` is a statement about that phase unless this says what
/// else was tried. `pick` is the phase the row above was taken at.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Census {
    pub walked: u8,
    /// Bit N set: phase N passed.
    pub passed: u8,
    pub pick: u8,
}

impl core::fmt::Display for Census {
    /// `scripts/bist_rows.py`'s `CENSUS` reads this; `tests/test_bist_row_
    /// parsers.py` holds it. `none passed` carries no pass list ON PURPOSE, so a
    /// caller cannot read the pick as a phase that works.
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(f, "      sel  {} walked  ", self.walked)?;
        if self.passed == 0 {
            return write!(f, "none passed  pick {} -- the fewest errors", self.pick);
        }
        write!(f, "pass ")?;
        let mut first = true;
        for phase in 0..self.walked.min(8) {
            if self.passed >> phase & 1 != 0 {
                write!(f, "{}{}", if first { "" } else { "," }, phase)?;
                first = false;
            }
        }
        write!(f, "  pick {} -- the widest window's centre", self.pick)
    }
}

/// A sweep's three counts, and there is no fourth.
///
/// **NO RESULT is not a pass and not a failure.** Collapsing the three is the
/// failure this rig exists to avoid, so every sweep ends with all of them.
#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub struct Tally {
    pub pass: u32,
    pub fail: u32,
    pub no_result: u32,
}

impl Tally {
    pub fn add(&mut self, verdict: Verdict) {
        match verdict {
            Verdict::Pass => self.pass += 1,
            Verdict::Fail(_) => self.fail += 1,
            Verdict::NoResult => self.no_result += 1,
        }
    }

    /// Cells actually RUN, which is what `of N` reports. A sweep that stopped
    /// early must not claim the count it planned.
    pub fn total(&self) -> u32 {
        self.pass + self.fail + self.no_result
    }
}

impl core::fmt::Display for Tally {
    /// `scripts/hyperram_matrix_diff.py` and `hyperram_register_path.py` parse
    /// this exact wording; `tests/test_bist_row_parsers.py` holds them to it.
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(f, "{} pass, {} fail, {} no result of {}",
               self.pass, self.fail, self.no_result, self.total())
    }
}

/// `REG_CTRL_STATE` bits 8..10: whether the engine's CR0/CR1 readback stands up.
///
/// The readback shares its capture window with the data path, so a cell run at a
/// phase outside the window returns a held or slid word -- and the report was
/// printed as fact, once announcing a part in Deep Power Down that was awake
/// (#366). Each rule is one residue seen on the board (#358).
pub mod trust {
    /// CR0 read twice with CR1 between agreed. A path sliding by one transaction
    /// cannot.
    pub const REREAD: u32 = 1 << 8;
    /// CR0 and CR1 came back different. A path holding one word answers alike.
    pub const DISTINCT: u32 = 1 << 9;
    /// Both words carry their 16 bits in both halves, as a register read does.
    /// A byte-slipped capture does not.
    pub const HALVES: u32 = 1 << 10;
}

/// What may be said about the part's configuration, given the trust bits.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Readback {
    /// Nothing has been read back yet, so there is no claim to make either way.
    NotLatched,
    /// The words stand alone.
    Reported { cr0: u16, cr1: u16 },
    /// Withheld, and why. **It carries no number**: a value printed beside a
    /// warning that it is wrong is read as the value, and every claim derived
    /// from it -- the latency code, the drive strength, "the part is asleep" --
    /// is as wrong as the word it came from.
    Withheld(&'static str),
}

/// Whether the CR0/CR1 readback may be reported, and what to say when it may not.
///
/// `write_cycles` is the engine's own counter: its first increment is
/// `WRITE_START`, which every run reaches through the config path, so zero means
/// nothing has been read back off the part at all.
pub fn readback(write_cycles: u32, ctrl_state: u32, cr0: u32, cr1: u32) -> Readback {
    if write_cycles == 0 {
        return Readback::NotLatched;
    }
    // First failing rule names itself. The order is most to least specific about
    // what the read path did.
    if ctrl_state & trust::REREAD == 0 {
        return Readback::Withheld(
            "CR0 read twice gave two answers -- the read path slid, so both \
             words belong to other transactions");
    }
    if ctrl_state & trust::DISTINCT == 0 {
        return Readback::Withheld(
            "CR0 and CR1 read back identical -- the read path is handing back \
             one held word, not the registers");
    }
    if ctrl_state & trust::HALVES == 0 {
        return Readback::Withheld(
            "a register read arrives in both halves of the word and these do \
             not match -- the capture is slipped by whole bytes");
    }
    Readback::Reported { cr0: cr0 as u16, cr1: cr1 as u16 }
}

/// CR0 for these axes, without the apply bit.
///
/// Every field the sweep varies must be CLEARED before it is set, or the
/// power-on value shows through. `0x70f8` covers drive `[14:12]`, latency
/// `[7:4]` and fixed `[3]`. It was `0x70f0` and so omitted bit 3 (#335).
///
/// `CR0_FULL_POWER` is forced here rather than at the call site: `CR0[15] = 0`
/// is Deep Power Down and a part put there outlives the bitstream, since nothing
/// on this board power-cycles it.
pub fn cr0_word(axes: &Axes) -> u32 {
    (0x8f2f & !0x70f8u32)
        | ((axes.drive as u32 & 0x7) << 12)
        | ((axes.latency as u32 & 0xf) << 4)
        | (if axes.fixed_latency { 1 << 3 } else { 0 })
        | CR0_FULL_POWER
}

/// CR1 for these axes, without the apply bit. `CR1[5] = 1` is Hybrid Sleep and
/// is forced clear for the same reason `CR0[15]` is forced set.
pub fn cr1_word(axes: &Axes) -> u32 {
    (if axes.single_ended_clock { 0xffc1 } else { 0xff81 }) & !CR1_HYBRID_SLEEP
}

/// `CR0[7:4]` as CK clocks: `clocks = 5 + sext4(code)`, so the field is SPARSE
/// and not monotonic -- 14 and 15 are the two SHORTEST at 3 and 4 clocks, then
/// 0, 1, 2 are 5, 6, 7.
///
/// Defined for every code, so a RESERVED one still reports what the part was
/// asked for. [`latency_code_legal`] is the separate question.
pub fn latency_clocks(code: u8) -> u32 {
    let code = (code & 0xf) as i32;
    (5 + if code >= 14 { code - 16 } else { code }) as u32
}

/// Codes the datasheet allows: 0, 1, 2, 14, 15. **3..=13 are RESERVED** (Table
/// 8, W956A8MBYA rev A01-006 p. 21), and a sweep runs them anyway -- knowing
/// which rows were out of spec is the point.
pub fn latency_code_legal(code: u8) -> bool {
    matches!(code & 0xf, 0 | 1 | 2 | 14 | 15)
}

/// How many poll spins one pass of `passes` bursts may legitimately take.
///
/// **Waits for**: the engine's `done`, one write burst plus one read burst per
/// pass.
///
/// **Expected duration, and where it comes from**: a burst is 128 words, one
/// word per `hr` cycle, and a pass does two of them -- 256 `hr` cycles -- plus
/// command, latency and tCSHI, call it 512 to be safe about the fixed part. The
/// slowest rung this rig builds is CK 100 on the DQS path, where `hr` is 50 MHz,
/// against a CPU at 60 MHz: 512 x 60/50 = 614 CPU cycles per pass. One spin is
/// an uncached CSR read plus a mask and a branch; at ~10 cycles that is ~62
/// spins per pass.
///
/// **Multiplier**: `SPINS_PER_PASS` is 80, about 1.3x that, and the whole thing
/// is per-pass rather than a fixed ceiling so it tracks the parameter it depends
/// on. `FLOOR` covers DLL lock and engine start-up once.
///
/// **On expiry**: `Poll::TimedOut` carries the limit and the spins taken, `words`
/// is forced to zero so [`Cell::verdict`] reads NoResult, and the caller prints
/// all of it. A wedged engine can never be reported as a pass.
///
/// The retired branch used a flat 2,000,000 here. At ~30x it made a wedged cell
/// slow enough that a 128-cell sweep was indistinguishable from a lockup, which
/// is how the wedge was first reported.
pub fn poll_limit(passes: u32) -> u32 {
    const SPINS_PER_PASS: u32 = 80;
    const FLOOR: u32 = 2_000;
    passes.saturating_mul(SPINS_PER_PASS).saturating_add(FLOOR)
}

/// The ONE heading, over the one row shape every sweep prints.
///
/// - The stamp is column 0, not an indent: three writes per row put it between
///   column 2 and column 3, mid-row.
/// - Every axis is named, including the two the reader had to know from the
///   source -- `mode` is CR0[3] fixed/variable and `clk` is CR1[6] dif/se.
///
/// Widths must match [`FIELD_WIDTHS`] exactly. Integers right-align, `&str`
/// left-aligns, which is why `mode` and `clk` are exactly their field's width
/// and everything numeric sits right.
pub const HEADING: &str =
    "      time  lat  mode  drive  clk  sel    errors     words   control  verdict";

/// What each column IS, printed once above the heading so a row can be read
/// without this file. Register fields as the datasheet numbers them, Table 8
/// (W956A8MBYA rev A01-006 p. 21).
pub const LEGEND: [&str; 4] = [
    "  time   ms since boot            lat    CR0[7:4] latency code",
    "  mode   CR0[3] fixed/variable    drive  CR0[14:12] output drive",
    "  clk    CR1[6] differential/single-ended    sel  READCLKSEL capture phase",
    "  errors/words  the real pass    control  the negative control's errors",
];

/// `report`'s row format as data: nine fields, two spaces between each, then
/// the verdict. A format string has to be a literal, so this is the
/// machine-readable half of
/// `{}  {:3}  {:4}  {:5}  {:3}  {:3}  {:8}  {:8}  {:8}  {}`.
///
/// Field 0 is the stamp, which is exactly ten characters at every input --
/// `log::format` asserts that, and this width depends on it.
pub const FIELD_WIDTHS: [usize; 9] = [10, 3, 4, 5, 3, 3, 8, 8, 8];

/// Where each field of a row ends. The verdict starts two columns after the last.
pub const fn field_ends() -> [usize; 9] {
    let mut ends = [0usize; 9];
    let (mut i, mut at) = (0, 0);
    while i < FIELD_WIDTHS.len() {
        at += FIELD_WIDTHS[i];
        ends[i] = at;
        at += 2;
        i += 1;
    }
    ends
}

#[cfg(test)]
mod tests {
    use super::*;

    fn axes() -> Axes {
        Axes { drive: 0, single_ended_clock: false, readclksel: 0,
               latency: 2, fixed_latency: true }
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

    const ALL_TRUST: u32 = trust::REREAD | trust::DISTINCT | trust::HALVES;

    /// THE BOARD'S OWN NUMBERS, #366. After a cell at a failing capture phase
    /// `bist status` printed `CR1 = 0xbf2f` -- the PREVIOUS CR0 -- and read
    /// CR1[5] out of it as "the part is asleep". The part was awake.
    #[test]
    fn a_slid_readback_is_withheld_rather_than_decoded() {
        let slid = readback(1, ALL_TRUST & !trust::REREAD, 0x4006_4006, 0xbf2f_bf2f);
        assert!(matches!(slid, Readback::Withheld(_)));
        // And nothing derived survives: no word comes out of a withheld report,
        // so no caller can decode CR1[5] out of one.
        assert!(!matches!(slid, Readback::Reported { .. }));
    }

    /// Each rule alone must be able to withhold. A trust word that only ever
    /// reports when every bit is wrong would pass the test above and nothing else.
    #[test]
    fn every_trust_rule_can_withhold_on_its_own() {
        for rule in [trust::REREAD, trust::DISTINCT, trust::HALVES] {
            assert!(matches!(readback(1, ALL_TRUST & !rule, 0x8f2f_8f2f, 0xff81_ff81),
                             Readback::Withheld(_)),
                    "rule {rule:#x} cannot withhold on its own");
        }
    }

    /// The control: a good readback must still be reported, or "withheld" is
    /// just silence and the instrument has lost its measurement.
    #[test]
    fn a_readback_that_passes_every_rule_is_reported() {
        assert!(matches!(readback(1, ALL_TRUST, 0x8f2f_8f2f, 0xff81_ff81),
                         Readback::Reported { cr0: 0x8f2f, cr1: 0xff81 }));
    }

    /// Before any run there is nothing to report and nothing to distrust; the
    /// two must not print the same thing.
    #[test]
    fn nothing_latched_is_not_the_same_as_untrustworthy() {
        assert!(matches!(readback(0, 0, 0, 0), Readback::NotLatched));
        assert!(matches!(readback(0, ALL_TRUST, 0x8f2f_8f2f, 0xff81_ff81),
                         Readback::NotLatched));
    }

    /// The bound must track the parameter it depends on, not be a flat ceiling.
    #[test]
    fn the_poll_bound_scales_with_passes() {
        assert!(poll_limit(256) > poll_limit(16));
        // And it must not overflow into a tiny value at absurd inputs.
        assert!(poll_limit(u32::MAX) > poll_limit(1));
    }

    /// Every field the sweep varies must be reachable in BOTH directions.
    ///
    /// `CR0[3]` was not: the clear mask omitted bit 3, so `var` ORed zero onto a
    /// bit the power-on value had already set. 2048 cells of `bist all` and 16
    /// rows of `bist latency` reported `fix` results labelled `var` (#335).
    #[test]
    fn every_swept_cr0_field_can_be_cleared_and_set() {
        let axes = |drive, latency, fixed_latency| Axes {
            drive, single_ended_clock: false, readclksel: 0, latency, fixed_latency,
        };

        // The measured pair from the board, and the value the fix must produce.
        assert_eq!(cr0_word(&axes(3, 2, true)), 0xbf2f);
        assert_eq!(cr0_word(&axes(3, 2, false)), 0xbf27);

        // Each field, both extremes, so a mask that drops a bit fails here
        // rather than on the board a month later.
        for fixed_latency in [true, false] {
            for drive in 0..=7u8 {
                for latency in 0..=15u8 {
                    let word = cr0_word(&axes(drive, latency, fixed_latency));
                    assert_eq!((word >> 12) & 0x7, drive as u32, "drive {drive}");
                    assert_eq!((word >> 4) & 0xf, latency as u32, "latency {latency}");
                    assert_eq!((word >> 3) & 1, fixed_latency as u32,
                               "fixed {fixed_latency} drive {drive} lat {latency}");
                    // CR0[15] must survive every combination: 0 is Deep Power
                    // Down, and a part put there outlives the bitstream.
                    assert_eq!((word >> 15) & 1, 1, "CR0[15] cleared");
                }
            }
        }
    }

    /// The dif/se axis must move `CR1[6]` and nothing else, and must never ask
    /// for Hybrid Sleep.
    #[test]
    fn cr1_moves_only_the_clock_bit_and_never_sleeps() {
        let axes = |single_ended_clock| Axes {
            drive: 3, single_ended_clock, readclksel: 0, latency: 2,
            fixed_latency: true,
        };
        for single_ended_clock in [true, false] {
            let word = cr1_word(&axes(single_ended_clock));
            assert_eq!((word >> 6) & 1, single_ended_clock as u32);
            assert_eq!((word >> 5) & 1, 0, "CR1[5] hybrid sleep set");
        }
        // Exactly one bit apart, so the axis cannot be moving something else too.
        assert_eq!(cr1_word(&axes(true)) ^ cr1_word(&axes(false)), 1 << 6);
    }

    /// The latency field is SPARSE and not monotonic: `5 + sext4(code)`, so 14
    /// and 15 are the two shortest settings and sit below code 0.
    #[test]
    fn the_latency_map_is_the_datasheets() {
        for (code, clocks) in [(14u8, 3u32), (15, 4), (0, 5), (1, 6), (2, 7)] {
            assert!(latency_code_legal(code), "code {code}");
            assert_eq!(latency_clocks(code), clocks, "code {code}");
        }
        // 3..=13 are RESERVED, and the sweep runs them anyway -- so the
        // arithmetic must still hold rather than the function refusing.
        for code in 3..=13u8 {
            assert!(!latency_code_legal(code), "code {code}");
            assert_eq!(latency_clocks(code), 5 + code as u32, "code {code}");
        }
        // The whole field, and nothing above it: only the low four bits count.
        for code in 0..=255u8 {
            assert_eq!(latency_clocks(code), latency_clocks(code & 0xf));
            assert!((3..=18).contains(&latency_clocks(code)));
        }
    }

    /// Every heading word must occupy exactly its field's columns.
    ///
    /// The failures this replaces: a heading with no stamp column put every
    /// column 12 characters left of its data, and `&HEADING[13..]` cut a word in
    /// half and printed `lat  mode  el   errors`.
    #[test]
    fn the_headings_sit_over_the_columns_they_name() {
        let ends = field_ends();

        // Numeric fields right-align, and `mode`/`clk` are exactly their own
        // width, so every heading word ENDS at its field's last column.
        for (word, end) in ["time", "lat", "mode", "drive", "clk", "sel",
                            "errors", "words", "control"].iter().zip(ends)
        {
            assert_eq!(&HEADING[end - word.len()..end], *word, "{word} at {end}");
        }
        // The verdict is the one unpadded field, two columns after the last.
        assert_eq!(&HEADING[ends[8] + 2..], "verdict");

        // Nothing but spaces in between, so a stray character cannot shift a
        // later column while every word still ends where it should.
        assert_eq!(HEADING.len(), ends[8] + 2 + "verdict".len());
        assert!(HEADING.chars().all(|c| c == ' ' || c.is_ascii_alphabetic()));
    }

    /// EVERY column is named, or the reader is back in the source. `mode` and
    /// `clk` are the two that were unlabelled.
    #[test]
    fn the_legend_names_every_column_in_the_heading() {
        let legend = LEGEND.concat();
        for word in ["time", "lat", "mode", "drive", "clk", "sel", "errors",
                     "words", "control"] {
            assert!(legend.contains(word), "{word} is not in the legend");
        }
        // A console line wider than the terminal wraps and destroys the table
        // it is explaining.
        for line in LEGEND {
            assert!(line.len() <= 80, "{} columns: {line}", line.len());
        }
        assert!(HEADING.len() <= 80, "{} columns", HEADING.len());
    }

    /// A short cell is NO RESULT, never a pass -- and the tally must bucket it
    /// exactly as the row prints it.
    #[test]
    fn a_short_cell_is_no_result_rather_than_a_pass() {
        assert!(matches!(scored(&cell(0, 4096, 4096, 4096), 4096), Verdict::Pass));
        assert!(matches!(scored(&cell(0, 17, 4096, 4096), 4096), Verdict::NoResult));
        // A short CONTROL is equally disqualifying: the comparator is not shown
        // to have run over the words the real pass was judged on.
        assert!(matches!(scored(&cell(0, 4096, 4096, 17), 4096), Verdict::NoResult));
        // Errors are errors however few words carried them.
        assert!(matches!(scored(&cell(9, 17, 4096, 4096), 4096), Verdict::Fail(9)));
    }

    /// The eye MEASURED on the board 2026-08-11 (#421): phases 1, 2, 3 clean,
    /// 0 and 4..7 not. The centre is the phase with margin either side.
    #[test]
    fn the_centre_of_the_measured_eye_is_the_middle_of_its_window() {
        assert_eq!(eye_centre(0b0000_1110, 8), Some(2));
        // One phase is a window of one, and the ends are reachable.
        assert_eq!(eye_centre(0b0000_0001, 8), Some(0));
        assert_eq!(eye_centre(0b1000_0000, 8), Some(7));
        // Nothing passed: there is no centre to report, and no phase to use.
        assert_eq!(eye_centre(0, 8), None);
        // Two runs: the wider one, and its centre -- never a phase between them.
        assert_eq!(eye_centre(0b0011_1001, 8), Some(4));
        // A non-DQS build walks one phase, where `sel` reaches nothing (#343).
        assert_eq!(eye_centre(0b0000_0001, 1), Some(0));
        assert_eq!(eye_centre(0b1111_1110, 1), None);
    }

    /// A pass at any phase beats every failure, so a code that works somewhere
    /// can never be reported by a phase where it did not.
    #[test]
    fn a_pass_outranks_every_failure_and_no_result_is_last() {
        let at = |errors, control_errors| {
            Cell { axes: axes(), errors, words: 4096, control_errors,
                   control_words: 4096 }
        };
        assert!(rank(&at(0, 4096), 4096) < rank(&at(1, 4096), 4096));
        assert!(rank(&at(1, 4096), 4096) < rank(&at(4096, 4096), 4096));
        assert!(rank(&at(4096, 4096), 4096) < rank(&at(0, 0), 4096));
        // A short cell is NO RESULT however few errors it counted.
        assert!(rank(&at(4096, 4096), 4096)
                < rank(&Cell { words: 17, ..at(0, 4096) }, 4096));
    }

    /// The line a host reads to learn which phases were tried. A pick with no
    /// pass list must be unreadable as a working phase.
    #[test]
    fn the_census_names_every_passing_phase_and_the_one_picked() {
        let line = |walked, passed, pick| {
            std::format!("{}", Census { walked, passed, pick })
        };
        assert_eq!(line(8, 0b0000_1110, 2),
                   "      sel  8 walked  pass 1,2,3  pick 2 -- the widest window's centre");
        assert_eq!(line(8, 0, 5),
                   "      sel  8 walked  none passed  pick 5 -- the fewest errors");
        // Widest case: still one line, and still inside a terminal.
        assert!(line(8, 0xff, 3).len() <= 80, "{}", line(8, 0xff, 3));
        // A row is never mistaken for one: the census has no timestamp column.
        assert!(!line(8, 0b0000_1110, 2).contains("PASS"));
    }

    /// The three counts stay separate, and `of N` is what RAN.
    #[test]
    fn the_tally_keeps_no_result_out_of_both_other_buckets() {
        let mut tally = Tally::default();
        for verdict in [Verdict::Pass, Verdict::Pass, Verdict::Fail(3),
                        Verdict::NoResult] {
            tally.add(verdict);
        }
        assert_eq!((tally.pass, tally.fail, tally.no_result), (2, 1, 1));
        assert_eq!(tally.total(), 4);
        assert_eq!(std::format!("{tally}"), "2 pass, 1 fail, 1 no result of 4");
    }

    /// The wording `scripts/hyperram_matrix_diff.py`'s `SUMMARY` regex reads.
    /// A summary it cannot parse makes a finished 4096-cell run unsaveable.
    #[test]
    fn an_empty_tally_still_reports_all_four_numbers() {
        assert_eq!(std::format!("{}", Tally::default()),
                   "0 pass, 0 fail, 0 no result of 0");
    }
}
