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
    /// **The axis most likely to explain a one-word slip.** A read that starts
    /// one clock early or late lands one device word off, and no capture phase
    /// can correct that -- it is not a timing margin, it is an off-by-one. Left
    /// at the power-on 0010b/fixed for every measurement so far.
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

/// Column headings, composed rather than sliced.
///
/// Two things the old single-string version got wrong. `report` prints
/// `log::now()` and two spaces BEFORE the first field, so a heading without the
/// same indent puts every column 12 characters out. And the latency variants
/// built their heading as `&HEADING[13..]`, which cuts mid-word and produced a
/// stray `el` in `lat  mode  el   errors`.
///
/// Widths must match [`FIELD_WIDTHS`] exactly. Integers right-align, `&str`
/// left-aligns, which is why `clk` sits left and everything numeric sits right.
pub const STAMP_PAD: &str = "            ";
pub const HEADING_AXES: &str = "drive  clk  sel";
pub const HEADING_COUNTS: &str = "    errors     words   control  verdict";

/// `report`'s row format as data: six fields, two spaces between each, then the
/// verdict. A format string has to be a literal, so this is the machine-readable
/// half of `{:5}  {:3}  {:3}  {:8}  {:8}  {:8}  {}` and the headings above are
/// checked against it.
pub const FIELD_WIDTHS: [usize; 6] = [5, 3, 3, 8, 8, 8];

/// Where each field of a row ends, counting from the first character after the
/// timestamp. The verdict starts two columns after the last.
pub const fn field_ends() -> [usize; 6] {
    let mut ends = [0usize; 6];
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
    /// The two failures this replaces: a heading with no `STAMP_PAD` put every
    /// column 12 characters left of its data, and `&HEADING[13..]` cut a word in
    /// half and printed `lat  mode  el   errors`.
    #[test]
    fn the_headings_sit_over_the_columns_they_name() {
        let heading = [HEADING_AXES, HEADING_COUNTS].concat();
        let ends = field_ends();

        // Numeric fields right-align and `clk` is exactly its width, so every
        // heading word ENDS at its field's last column.
        for (word, end) in ["drive", "clk", "sel", "errors", "words", "control"]
            .iter()
            .zip(ends)
        {
            assert_eq!(&heading[end - word.len()..end], *word, "{word} at {end}");
        }
        // The verdict is the one unpadded field, two columns after the last.
        assert_eq!(&heading[ends[5] + 2..], "verdict");

        // Nothing but spaces in between, so a stray character cannot shift a
        // later column while every word still ends where it should.
        assert_eq!(heading.len(), ends[5] + 2 + "verdict".len());
        assert!(heading.chars().all(|c| c == ' ' || c.is_ascii_alphabetic()));
    }

    /// The stamp indent is `log::Stamp`'s ten characters plus `report`'s two
    /// spaces. `tests/headings.rs` in the host crate holds it against the real
    /// formatter; here it is only the width.
    #[test]
    fn the_stamp_pad_is_twelve_blank_columns() {
        assert_eq!(STAMP_PAD.len(), 12);
        assert!(STAMP_PAD.bytes().all(|b| b == b' '));
    }
}
