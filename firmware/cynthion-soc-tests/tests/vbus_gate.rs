//! The VBUS gate's decisions (#552): close allowed or refused, what an ALERT
//! does, and which channel carries the limits.

use cynthion_soc_tests::vbus_gate::*;

// PAC channel indices the gate uses: target_c 1, aux 2, control 3.
const AUX: usize = 2;
const CONTROL: usize = 3;

#[test]
fn bits_match_the_part_layout() {
    // CH1OV is bit 15, CH1UV is bit 11; CH1 is the high bit of each group.
    assert_eq!(ov_bit(0), 1 << 15);
    assert_eq!(ov_bit(3), 1 << 12);
    assert_eq!(uv_bit(0), 1 << 11);
    assert_eq!(uv_bit(3), 1 << 8);
    assert_eq!(limit_bits(AUX), ov_bit(AUX) | uv_bit(AUX));
}

#[test]
fn in_range_with_no_flag_closes() {
    assert!(decide(CONTROL, 5_000, 0) == Ok(5_000));
    assert!(decide(CONTROL, FLOOR_MV, 0) == Ok(FLOOR_MV));
    assert!(decide(CONTROL, LIMIT_MV, 0) == Ok(LIMIT_MV));
}

#[test]
fn uv_flag_refuses_even_if_the_read_looks_fine() {
    assert!(decide(AUX, 5_000, uv_bit(AUX)) == Err(Refusal::Uv(5_000)));
    assert!(decide(AUX, 3_980, uv_bit(AUX)) == Err(Refusal::Uv(3_980)));
}

#[test]
fn ov_flag_refuses_and_wins_over_uv() {
    assert!(decide(AUX, 5_000, ov_bit(AUX)) == Err(Refusal::Ov(5_000)));
    assert!(decide(AUX, 5_000, ov_bit(AUX) | uv_bit(AUX)) == Err(Refusal::Ov(5_000)));
}

#[test]
fn another_channels_flag_is_not_ours() {
    assert!(decide(AUX, 5_000, limit_bits(CONTROL)) == Ok(5_000));
    // OC / UC / OP bits are not UV/OV.
    assert!(decide(AUX, 5_000, 0xFF_0000 | 0xFF) == Ok(5_000));
}

#[test]
fn out_of_range_read_without_a_flag_still_refuses() {
    assert!(decide(AUX, LIMIT_MV + 1, 0) == Err(Refusal::TooHigh(LIMIT_MV + 1)));
    assert!(decide(AUX, FLOOR_MV - 1, 0) == Err(Refusal::TooLow(FLOOR_MV - 1)));
}

#[test]
fn only_continuous_1024_sps_counts_as_sampling() {
    assert!(sampling_continuously(0x0700)); // CTRL_ESTABLISHED
    assert!(!sampling_continuously(0x1700));
    assert!(!sampling_continuously(0xF700));
}

#[test]
fn limits_are_armed_for_the_closing_source_only() {
    for closing in [None, Some(1), Some(2), Some(3)] {
        for channel in 0..4 {
            assert_eq!(channel_armed(channel, closing), closing == Some(channel));
        }
    }
}

#[test]
fn alert_on_the_armed_source_with_a_switch_closed_opens_all() {
    let a = alert_action(ov_bit(AUX), Some(AUX), true);
    assert!(a.open && a.disarm && a.gate_bits == ov_bit(AUX));
    let a = alert_action(uv_bit(AUX), Some(AUX), true);
    assert!(a.open && a.disarm && a.gate_bits == uv_bit(AUX));
}

#[test]
fn alert_while_open_disarms_but_opens_nothing() {
    let a = alert_action(uv_bit(AUX), Some(AUX), false);
    assert!(!a.open && a.disarm);
}

#[test]
fn alerts_that_are_not_the_gates_are_left_alone() {
    // Unarmed gate, another channel, or a current limit: no action, no bits taken.
    for (fired, armed) in [
        (uv_bit(AUX), None),
        (uv_bit(CONTROL), Some(AUX)),
        (1u32 << 23 | 1 << 19, Some(AUX)), // OC / UC
        (0, Some(AUX)),
    ] {
        let a = alert_action(fired, armed, true);
        assert!(!a.open && !a.disarm && a.gate_bits == 0);
    }
}

#[test]
fn gate_bits_are_split_from_a_mixed_word() {
    let mixed = ov_bit(AUX) | (1 << 23);
    let a = alert_action(mixed, Some(AUX), true);
    assert!(a.gate_bits == ov_bit(AUX));
    assert!(mixed & !a.gate_bits == 1 << 23);
}

// ---- port power plans (#625): register byte in, verdict out ----

const EN: u8 = 1 << BIT_ENABLE;
const C: u8 = 1 << BIT_CONTROL;
const A: u8 = 1 << BIT_AUX;
const T: u8 = 1 << BIT_TARGET_C;

#[test]
fn closed_mask_needs_the_gate_bit() {
    assert_eq!(closed_mask(0), 0);
    assert_eq!(closed_mask(C | A | T), 0); // switch bits with enable off: all open
    assert_eq!(closed_mask(EN | C), C);
    assert_eq!(closed_mask(EN | 1 << BIT_DISCHARGE), 0); // discharge is not a source
}

#[test]
fn target_a_on_from_open_closes_and_a_live_control_or_aux_is_left_alone() {
    assert!(on_plan(TARGET_A, 0) == Ok(OnPlan::Close));
    assert!(on_plan(TARGET_A, EN | C) == Ok(OnPlan::AlreadyOn));
    assert!(on_plan(TARGET_A, EN | A) == Ok(OnPlan::AlreadyOn));
}

#[test]
fn target_a_on_refuses_when_target_c_or_two_sources_are_closed() {
    assert!(on_plan(TARGET_A, EN | T) == Err(PowerRefusal::Other(T)));
    assert!(on_plan(TARGET_A, EN | C | T) == Err(PowerRefusal::Other(C | T)));
    assert!(on_plan(TARGET_A, EN | C | A) == Err(PowerRefusal::Other(C | A)));
}

#[test]
fn target_c_on_allows_open_or_its_own_charge_path_only() {
    assert!(on_plan(TARGET_C, 0) == Ok(OnPlan::Close));
    assert!(on_plan(TARGET_C, EN | C | T) == Ok(OnPlan::Close)); // Rp change in place
    assert!(on_plan(TARGET_C, EN | A) == Err(PowerRefusal::Other(A)));
    assert!(on_plan(TARGET_C, EN | C) == Err(PowerRefusal::Other(C)));
}

#[test]
fn inputs_have_no_power_verbs() {
    for port in [AUX, CONTROL] {
        assert!(on_plan(port, 0) == Err(PowerRefusal::NotOutput));
        assert!(off_plan(port, EN | C) == Err(PowerRefusal::NotOutput));
    }
}

#[test]
fn target_a_off_opens_only_its_own_sources() {
    assert!(off_plan(TARGET_A, 0) == Ok(()));
    assert!(off_plan(TARGET_A, EN | C) == Ok(()));
    assert!(off_plan(TARGET_A, EN | A) == Ok(()));
    assert!(off_plan(TARGET_A, EN | T) == Err(PowerRefusal::Other(T)));
    assert!(off_plan(TARGET_A, EN | C | T) == Err(PowerRefusal::Other(T)));
}

#[test]
fn target_c_off_covers_its_charge_path_and_refuses_aux() {
    assert!(off_plan(TARGET_C, EN | C | T) == Ok(()));
    assert!(off_plan(TARGET_C, EN | T) == Ok(()));
    assert!(off_plan(TARGET_C, EN | A) == Err(PowerRefusal::Other(A)));
    assert!(off_plan(TARGET_C, EN | A | T) == Err(PowerRefusal::Other(A)));
}

#[test]
fn powered_follows_the_ports_own_view() {
    assert!(!powered(TARGET_A, 0));
    assert!(powered(TARGET_A, EN | C));
    assert!(powered(TARGET_A, EN | A));
    assert!(powered(TARGET_A, EN | T)); // something is driving the rail
    assert!(!powered(TARGET_C, EN | C));
    assert!(powered(TARGET_C, EN | C | T));
    assert!(powered(CONTROL, EN | C) && !powered(CONTROL, EN | A));
    assert!(powered(AUX, EN | A) && !powered(AUX, 0));
}

#[test]
fn window_for_5v_is_the_fixed_window() {
    let w = Window::for_nominal(5_000);
    assert_eq!((w.floor_mv, w.limit_mv), (FLOOR_MV, LIMIT_MV));
    assert!(decide_in(w, AUX, 4_400, 0) == decide(AUX, 4_400, 0));
    assert!(decide_in(w, AUX, 5_501, 0) == decide(AUX, 5_501, 0));
}

#[test]
fn window_follows_a_9v_contract() {
    let w = Window::for_nominal(9_000);
    assert!(decide_in(w, AUX, 9_000, 0) == Ok(9_000));
    assert!(decide_in(w, AUX, 8_550, 0) == Ok(8_550));
    assert!(decide_in(w, AUX, w.limit_mv + 1, 0) == Err(Refusal::TooHigh(w.limit_mv + 1)));
    assert!(decide_in(w, AUX, w.floor_mv - 1, 0) == Err(Refusal::TooLow(w.floor_mv - 1)));
    // 5 V rail is out of a 9 V window, and 9 V out of the 5 V one.
    assert!(decide_in(w, AUX, 5_000, 0) == Err(Refusal::TooLow(5_000)));
    assert!(decide(AUX, 9_000, 0) == Err(Refusal::TooHigh(9_000)));
}

#[test]
fn window_limit_stays_under_the_next_pdo() {
    assert!(Window::for_nominal(9_000).limit_mv < 10_000);
    assert!(Window::for_nominal(5_000).limit_mv < 9_000);
}
