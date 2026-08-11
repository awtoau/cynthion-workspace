//! Invariants that span two modules, which neither can state on its own.
//!
//! A module included here may not name `crate::`, so a rule holding one module
//! against another has nowhere to live inside them. This file is where those go.

use cynthion_soc_tests::bist_pure::{field_ends, Tally, Verdict, FIELD_WIDTHS, HEADING};
use cynthion_soc_tests::log_format::Stamp;

/// `report`'s row format, as one `writeln!` with the stamp as field 0.
///
/// Kept in step with `bist::report` by the tests below: the widths come from
/// `FIELD_WIDTHS` and the heading has to land on them.
fn row(stamp: u32, verdict: &str) -> String {
    format!(
        "{}  {:3}  {:4}  {:5}  {:3}  {:3}  {:8}  {:8}  {:8}  {}",
        Stamp::at(stamp), 15, "var", 7, "dif", 6, 8u32, 1024u32, 1024u32, verdict
    )
}

/// The stamp is a COLUMN, not an indent, and its width is field 0's.
///
/// The failure it replaces: the stamp written by its own `write!` between the
/// caller's prefix and the columns, landing mid-row.
#[test]
fn the_time_column_is_exactly_a_stamp_wide() {
    assert_eq!(FIELD_WIDTHS[0], format!("{}", Stamp::at(12_481)).len());
}

/// The whole heading, sitting over a whole row.
#[test]
fn the_heading_line_covers_the_row_it_labels() {
    let printed = row(12_481, "PASS");
    // Both end where the last unpadded field starts, so every column between
    // them lines up. `bist_pure` checks each heading word against its own field.
    assert_eq!(HEADING.len() - "verdict".len(), printed.len() - "PASS".len());
}

/// Every value sits under the word that names it. Read the columns back out of
/// a rendered row by the heading's own offsets -- a shifted field fails here
/// rather than on a board a month later.
#[test]
fn each_value_lands_in_the_column_its_heading_names() {
    let printed = row(38_640, "fail");
    let ends = field_ends();
    let want = ["000038.640", "15", "var", "7", "dif", "6", "8", "1024", "1024"];
    for (i, value) in want.iter().enumerate() {
        let field = &printed[ends[i] - FIELD_WIDTHS[i]..ends[i]];
        assert_eq!(field.trim(), *value, "field {i} of the row");
        assert_eq!(HEADING[ends[i] - FIELD_WIDTHS[i]..ends[i]].trim().is_empty(),
                   false, "field {i} of the heading is blank");
    }
    assert_eq!(&printed[ends[8] + 2..], "fail");
}

/// A row's every field is on ONE line. Three writes per row is what put the
/// timestamp between column 2 and column 3.
#[test]
fn a_row_is_one_line() {
    assert!(!row(38_640, "NO RESULT -- control did not fire").contains('\n'));
}

/// The summary every sweep ends with, and the three counts stay apart.
#[test]
fn a_sweep_summary_reports_all_three_outcomes() {
    let mut tally = Tally::default();
    tally.add(Verdict::Pass);
    tally.add(Verdict::Fail(8));
    tally.add(Verdict::NoResult);
    assert_eq!(format!("{tally}"), "1 pass, 1 fail, 1 no result of 3");
}
