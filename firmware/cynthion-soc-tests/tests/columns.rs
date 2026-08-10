//! Invariants that span two modules, which neither can state on its own.
//!
//! A module included here may not name `crate::`, so a rule holding one module
//! against another has nowhere to live inside them. This file is where those go.

use cynthion_soc_tests::bist_pure::{HEADING_AXES, HEADING_COUNTS, STAMP_PAD};
use cynthion_soc_tests::log_format::Stamp;

/// `bist`'s heading indent is `log::Stamp` plus the two spaces `report` writes
/// after it. Asserted against the real formatter, not against 12.
///
/// The failure it replaces: a heading with no indent at all, which put every
/// column of a 4096-cell sweep ten characters left of its data.
#[test]
fn the_heading_indent_is_a_stamp_wide() {
    let stamp = format!("{}", Stamp::at(12_481));
    assert_eq!(STAMP_PAD.len(), stamp.len() + 2);
}

/// The whole line, assembled the way `bist::one` assembles it, sitting over a
/// row assembled the way `report` prints one.
#[test]
fn the_heading_line_covers_the_row_it_labels() {
    let heading = format!("{}{}{}", STAMP_PAD, HEADING_AXES, HEADING_COUNTS);
    // Same widths and separators as `report`'s format string, which
    // `FIELD_WIDTHS` is the machine-readable copy of.
    let row = format!(
        "{}  {:5}  {:3}  {:3}  {:8}  {:8}  {:8}  {}",
        Stamp::at(12_481), 3, "dif", 0, 0u32, 8192u32, 8192u32, "PASS"
    );
    // Both end where the last unpadded field starts, so every column between
    // them lines up. `bist_pure` checks each heading word against its own field.
    assert_eq!(heading.len() - "verdict".len(), row.len() - "PASS".len());
}
