//! The named PD verbs (#633): every name is unique, each builds the header the spec
//! tables give, and the disruptive/illegal flags match USB PD R3.1 Tables 6-5/6-6/6-76.

use cynthion_soc_tests::pd_decode::*;
use cynthion_soc_tests::pd_verbs::*;

fn build_named(name: &str) -> Frame {
    build(find(name).unwrap(), None)
}

#[test]
fn names_are_unique() {
    for (i, a) in VERBS.iter().enumerate() {
        for b in &VERBS[i + 1..] {
            assert_ne!(a.name, b.name);
        }
    }
}

#[test]
fn control_queries_use_the_spec_type_numbers() {
    for (name, kind) in [("sink-cap", 8), ("status", 18), ("revision", 24), ("sink-cap-ext", 22),
        ("country-codes", 21), ("ping", 5), ("gotomin", 2), ("soft-reset", 13),
        ("data-reset", 14), ("dr-swap", 9), ("pr-swap", 10), ("vconn-swap", 11)] {
        let f = build_named(name);
        assert_eq!((f.kind(), f.objects(), f.extended()), (kind, 0, false), "{name}");
        assert_eq!(f.name(), find(name).map(|_| f.name()).unwrap());
    }
}

#[test]
fn extended_queries_carry_a_chunked_extended_header() {
    let f = build_named("mfr-info");
    assert!(f.extended());
    assert_eq!((f.kind(), f.objects()), (6, 1));
    // Chunked, data size 2.
    assert_eq!(f.ext_header(), Some((true, 0, false, 2)));
    assert_eq!(f.name(), "Get_Manufacturer_Info");
    let b = build_named("battery-status");
    assert_eq!((b.kind(), b.ext_header()), (4, Some((true, 0, false, 1))));
}

#[test]
fn vdm_discovery_is_structured_version_2_request() {
    let id = build_named("identity");
    assert_eq!(id.kind(), VENDOR_DEFINED);
    assert_eq!(id.object(0), 0xff00_a001);
    assert_eq!(build_named("svids").object(0), 0xff00_a002);
    let cable = build_named("identity-cable");
    assert_eq!(cable.sop, SOP_PRIME);
    // modes takes the SVID to query.
    let modes = build(find("modes").unwrap(), Some(0x04e8));
    assert_eq!(modes.object(0), 0x04e8_a003);
}

#[test]
fn legality_flags_follow_table_6_76() {
    // Sink-or-DRP / sink-only messages a source shall not send.
    for name in ["source-cap", "source-cap-ext", "pps-status", "source-info-get"] {
        assert!(find(name).unwrap().illegal, "{name}");
    }
    for name in ["sink-cap", "status", "revision", "identity"] {
        let v = find(name).unwrap();
        assert!(!v.illegal && !v.destructive, "{name}");
    }
}

#[test]
fn resets_swaps_and_modes_are_disruptive() {
    for name in ["soft-reset", "hard-reset", "data-reset", "dr-swap", "pr-swap", "vconn-swap",
        "gotomin", "enter-usb", "bist-carrier"] {
        assert!(find(name).unwrap().destructive, "{name}");
    }
}

#[test]
fn reply_budgets_are_the_spec_timers_times_1_25() {
    // tSenderResponse max 33 ms -> 40; tVDMSenderResponse max 30 ms -> 38.
    assert_eq!(timeout_ms(find("status").unwrap()), 40);
    assert_eq!(timeout_ms(find("identity").unwrap()), 38);
    // Accept (40) then PS_RDY within tVCONNSourceOn 50 ms x1.25.
    assert_eq!(timeout_ms(find("vconn-swap").unwrap()), 105);
}

#[test]
fn hex_parser() {
    let mut out = [0u8; 4];
    assert_eq!(parse_hex(b"4f01", &mut out), Some(2));
    assert_eq!(&out[..2], &[0x4f, 0x01]);
    assert_eq!(parse_hex(b"4f0", &mut out), None);
    assert_eq!(parse_hex(b"zz", &mut out), None);
    assert_eq!(parse_hex(b"0000000000", &mut out), None);
}
