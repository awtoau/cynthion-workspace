//! The two fixed-width formatters, with no clock behind them.
//!
//! Split from `log.rs` so `firmware/cynthion-soc-tests` can include it (#337):
//! `now()` and `set_wall` read `crate::timer`, these do not. **Nothing here may
//! name `crate::`.**
//!
//! Both are FIXED WIDTH, and that is the whole point -- the stamp column is what
//! makes intervals readable off a log without arithmetic, and one ragged line
//! breaks the column for the reader, not for the parser.

use core::fmt;

/// Seconds the six-digit field can show before it repeats: 11.57 days.
///
/// The counter behind it wraps at 49.7 days (see `MILLIS` in `src/timer.rs`), so
/// this is what wraps first and it is the only wrap a reader will ever meet.
const SECONDS_SPAN: u32 = 1_000_000;

/// A millisecond count, formatted as `ssssss.mmm`.
///
/// Constructed from a stored value by `src/events.rs` and from the clock by
/// `log::now`. Holding the number rather than formatting eagerly is what lets a
/// deferred entry carry the time it was pushed at.
#[derive(Clone, Copy)]
pub struct Stamp(u32);

impl Stamp {
    /// A stamp on a millisecond count taken earlier.
    pub const fn at(millis: u32) -> Stamp {
        Stamp(millis)
    }

    /// The raw millisecond count, for `src/events.rs` to store.
    pub const fn millis(&self) -> u32 {
        self.0
    }
}

impl fmt::Display for Stamp {
    /// Exactly ten characters, always.
    ///
    /// `{:06}` and `{:03}` rather than arithmetic on the digits: the width and
    /// the zero padding are the whole point, and a hand-rolled version is one
    /// more place for a line to come out ragged. The modulo is what keeps the
    /// seconds field six digits after 11.57 days rather than pushing the column
    /// sideways.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{:06}.{:03}",
            (self.0 / 1000) % SECONDS_SPAN,
            self.0 % 1000
        )
    }
}

/// The time of day, as `HH:MM:SS`, from a Unix second count.
///
/// Seconds within the day only -- no date. Getting to a date needs the civil
/// calendar, and getting to a LOCAL date needs a timezone this board has no way
/// to learn. This is `% 86400` and three `u32` divides, which is what the prompt
/// can afford; `src/shell.rs`'s `time` command explains what a 64-bit divide
/// costs on rv32.
pub struct Clock(pub u32);

impl fmt::Display for Clock {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let day = self.0 % 86_400;
        write!(f, "{:02}:{:02}:{:02}", day / 3600, (day / 60) % 60, day % 60)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The docs say "exactly ten characters, always", and the BIST headings
    /// indent by that number. Nothing checked it until now.
    #[test]
    fn a_stamp_is_ten_characters_at_every_input() {
        for millis in [0, 1, 999, 1_000, 59_999, 86_399_999, u32::MAX / 2, u32::MAX] {
            let text = std::format!("{}", Stamp::at(millis));
            assert_eq!(text.len(), 10, "{millis} -> {text}");
        }
    }

    #[test]
    fn the_stamp_is_seconds_and_milliseconds() {
        assert_eq!(std::format!("{}", Stamp::at(0)), "000000.000");
        assert_eq!(std::format!("{}", Stamp::at(1)), "000000.001");
        assert_eq!(std::format!("{}", Stamp::at(12_481)), "000012.481");
        // The six-digit field wraps at 11.57 days rather than widening; the
        // counter behind it wraps later, so this is the only wrap a reader meets.
        assert_eq!(std::format!("{}", Stamp::at(999_999_999)), "999999.999");
        assert_eq!(std::format!("{}", Stamp::at(1_000_000_000)), "000000.000");
    }

    /// `millis` is what `src/events.rs` stores, so it must survive the round
    /// trip untouched -- a deferred line carries the time it was PUSHED at.
    #[test]
    fn a_stamp_keeps_the_count_it_was_made_from() {
        for millis in [0, 1, u32::MAX] {
            assert_eq!(Stamp::at(millis).millis(), millis);
        }
    }

    #[test]
    fn a_clock_is_eight_characters_and_never_reaches_24() {
        for seconds in [0u32, 1, 3_599, 86_399, 86_400, 1_754_800_000, u32::MAX] {
            let text = std::format!("{}", Clock(seconds));
            assert_eq!(text.len(), 8, "{seconds} -> {text}");
            let hour: u32 = text[..2].parse().unwrap();
            assert!(hour < 24, "{seconds} -> {text}");
        }
        assert_eq!(std::format!("{}", Clock(0)), "00:00:00");
        assert_eq!(std::format!("{}", Clock(86_399)), "23:59:59");
        // Seconds within the day only -- the date is deliberately not here.
        assert_eq!(std::format!("{}", Clock(86_400)), "00:00:00");
    }
}
