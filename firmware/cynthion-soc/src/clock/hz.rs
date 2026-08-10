//! A frequency in the unit its magnitude calls for (#333).
//!
//! Its own file because `clock.rs` reads `crate::target` and the PAC, and this
//! is arithmetic and `core::fmt` -- so `firmware/cynthion-soc-tests` can include
//! it and run the tests below (#337). **Nothing here may name `crate::`.**

/// A frequency, printed in the unit its magnitude calls for (#333).
///
/// - `>= 1 MHz` -> `60 MHz`, `85.714 MHz`
/// - `>= 1 kHz` -> `400 kHz`, `80 kHz`
/// - below     -> `20 Hz`, which is the power poll rate and belongs there
///
/// **Lossless.** The fraction is the remainder printed at the unit's full width
/// with trailing zeros trimmed, never rounded: the CK rungs include 85.7143 and
/// 94.2857 MHz and a sweep exists to tell those from their neighbours, and an
/// I2C rate is `f_sync / (5 * (PRER + 1))`, which divides evenly on almost
/// nothing. No `f32` anywhere -- there is no float formatting in `core`.
pub struct Hz(pub u32);

impl Hz {
    /// From kHz, which is how the CK selector reports its rungs.
    ///
    /// `u32` overflows above 4.29 GHz; nothing on this board is within an order
    /// of magnitude of that.
    pub const fn khz(khz: u32) -> Hz {
        Hz(khz * 1000)
    }
}

impl core::fmt::Display for Hz {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        let (unit, per, mut width) = match self.0 {
            hz if hz >= 1_000_000 => ("MHz", 1_000_000, 6usize),
            hz if hz >= 1_000 => ("kHz", 1_000, 3usize),
            hz => return write!(f, "{} Hz", hz),
        };
        let mut frac = self.0 % per;
        if frac == 0 {
            return write!(f, "{} {}", self.0 / per, unit);
        }
        // Trim on the value, not on a digit buffer: no indexing, so no bounds
        // check and no panic path in a formatter every boot line goes through.
        while frac % 10 == 0 {
            frac /= 10;
            width -= 1;
        }
        write!(f, "{}.{:0width$} {}", self.0 / per, frac, unit, width = width)
    }
}

/// The formatter, on the values that reach it.
#[cfg(test)]
mod tests {
    use super::Hz;

    fn show(hz: u32) -> std::string::String {
        std::format!("{}", Hz(hz))
    }

    #[test]
    fn whole_units_carry_no_decimal_point() {
        assert_eq!(show(60_000_000), "60 MHz");
        assert_eq!(show(1_000_000), "1 MHz");
        assert_eq!(show(400_000), "400 kHz");
        assert_eq!(show(80_000), "80 kHz");
    }

    /// The rungs `bist ck` reports, which a sweep exists to tell apart.
    #[test]
    fn adjacent_ck_rungs_stay_apart() {
        assert_eq!(show(Hz::khz(85_714).0), "85.714 MHz");
        assert_eq!(show(Hz::khz(85_715).0), "85.715 MHz");
        assert_eq!(show(Hz::khz(94_285).0), "94.285 MHz");
    }

    /// `f_sync / (5 * (PRER + 1))` for a prescale that does not divide evenly.
    #[test]
    fn a_divider_result_is_not_rounded_away() {
        assert_eq!(show(60_000_000 / 45), "1.333333 MHz");
        assert_eq!(show(60_000_000 / 55), "1.090909 MHz");
        assert_eq!(show(1_500_000), "1.5 MHz");
        assert_eq!(show(100_500), "100.5 kHz");
    }

    #[test]
    fn below_a_kilohertz_stays_in_hertz() {
        assert_eq!(show(20), "20 Hz");
        assert_eq!(show(999), "999 Hz");
        assert_eq!(show(0), "0 Hz");
        assert_eq!(show(1_000), "1 kHz");
    }
}
