//! Names and limits for the four USB VBUS rails measured by the PAC1954.
//!
//! All four sense points are USB VBUS. The nominal and limit come from
//! `docs/chips/pac1954-power-monitor.md`: 5.000 V, within 4.750--5.250 V.
//! The verdict concerns sustained voltage at the PAC1954 sense point; it says
//! nothing about a transient or voltage at the FPGA die.

/// One PAC1954 channel as an electrical quantity rather than an ADC count.
#[derive(Clone, Copy)]
pub struct Rail {
    pub name: &'static str,
    pub nominal_mv: u32,
    pub tolerance_mv: u32,
}

/// PAC channel order; it is deliberately not connector order.
pub const RAILS: [Rail; 4] = [
    Rail {
        name: "target_a_vbus",
        nominal_mv: 5_000,
        tolerance_mv: 250,
    },
    Rail {
        name: "target_c_vbus",
        nominal_mv: 5_000,
        tolerance_mv: 250,
    },
    Rail {
        name: "aux_vbus",
        nominal_mv: 5_000,
        tolerance_mv: 250,
    },
    Rail {
        name: "control_vbus",
        nominal_mv: 5_000,
        tolerance_mv: 250,
    },
];

/// Inclusive limits keep exact USB boundary values valid.
pub fn within_tolerance(rail: Rail, measured_mv: u32) -> bool {
    let lower = rail.nominal_mv.saturating_sub(rail.tolerance_mv);
    let upper = rail.nominal_mv.saturating_add(rail.tolerance_mv);
    measured_mv >= lower && measured_mv <= upper
}

#[cfg(test)]
mod tests {
    use super::{within_tolerance, RAILS};

    #[test]
    fn synthetic_samples_discriminate() {
        let rail = RAILS[3];
        assert!(within_tolerance(rail, 5_000));
        assert!(!within_tolerance(rail, 4_749));
    }

    /// The USB limits themselves must be valid: `>=`/`<=`, not `>`/`<`.
    #[test]
    fn the_boundaries_are_inclusive_on_every_rail() {
        for rail in RAILS {
            assert!(within_tolerance(rail, 4_750), "{} lower", rail.name);
            assert!(within_tolerance(rail, 5_250), "{} upper", rail.name);
            assert!(!within_tolerance(rail, 4_749), "{} below", rail.name);
            assert!(!within_tolerance(rail, 5_251), "{} above", rail.name);
        }
    }

    /// A dead rail reads 0 mV, and `saturating_sub` must not turn that into a
    /// pass by wrapping the lower bound.
    #[test]
    fn zero_millivolts_is_never_in_tolerance() {
        for rail in RAILS {
            assert!(!within_tolerance(rail, 0), "{}", rail.name);
        }
    }

    /// Channel order is not connector order on this part; the table says so and
    /// nothing else records it.
    #[test]
    fn the_channel_names_are_in_pac_order() {
        let names: [&str; 4] = core::array::from_fn(|i| RAILS[i].name);
        assert_eq!(
            names,
            ["target_a_vbus", "target_c_vbus", "aux_vbus", "control_vbus"]
        );
    }
}
