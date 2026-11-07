//! PD proxy state machine (#633): scripted AUX source and scripted phone, no hardware.
//! PDO/RDO layouts: USB PD R3.1 6.4.1 / 6.4.2. Phone sink list: the S20's measured
//! Sink_Capabilities (5 V/3 A, 9 V/2 A).

use cynthion_soc_tests::pd_proxy::*;
use cynthion_soc_tests::vbus_gate::Window;

const fn fixed(mv: u32, ma: u32) -> u32 {
    (mv / 50) << 10 | ma / 10
}

// Charger flags on PDO1: dual-role power + dual-role data + comm capable.
const CHARGER: [u32; 5] = [
    fixed(5_000, 3_000) | 1 << 29 | 1 << 25 | 1 << 26,
    fixed(9_000, 3_000),
    fixed(12_000, 3_000),
    fixed(15_000, 3_000),
    fixed(20_000, 3_250),
];
const S20_SINK: [u32; 2] = [fixed(5_000, 3_000), fixed(9_000, 2_000)];
const SAFE_RAILS: Rails = Rails { control_closed: false, target_c_mv: 0, aux_mv: 5_000 };

struct Bench {
    p: Proxy,
    now: u32,
    log: Vec<Action>,
}

impl Bench {
    fn new() -> Bench {
        let mut p = Proxy::new(CONFIG_9V);
        p.set_rails(SAFE_RAILS);
        Bench { p, now: 1_000, log: vec![] }
    }

    fn ev(&mut self, event: Event) -> Vec<Action> {
        let out = self.p.step(self.now, event).as_slice().to_vec();
        self.log.extend_from_slice(&out);
        out
    }

    fn advance(&mut self, ms: u32) -> Vec<Action> {
        self.now = self.now.wrapping_add(ms);
        self.ev(Event::Tick)
    }

    /// AUX attaches and settles at 5 V; phone attaches; path closes.
    fn bridged(&mut self) {
        self.ev(Event::AuxAttached);
        let out = self.ev(Event::AuxCaps(&CHARGER));
        assert_eq!(out, vec![Action::AuxRequest(rdo(1, 3_000, 3_000))]);
        self.ev(Event::AuxAccept);
        assert!(self.ev(Event::AuxPsRdy).is_empty());
        assert_eq!(self.p.phase, Phase::AuxReady);
        self.ev(Event::PhoneSinkCaps(&S20_SINK));
        let out = self.ev(Event::PhoneAttached);
        assert_eq!(out, vec![Action::Arm(Window::V5), Action::ClosePath, Action::PhoneOffer]);
        assert_eq!(self.p.phase, Phase::Bridged);
    }
}

#[test]
fn rdo_and_pdo_fields() {
    let pdo = fixed(9_000, 2_000);
    assert_eq!((pdo_mv(pdo), pdo_ma(pdo)), (9_000, 2_000));
    // S20 capture from #633: pos1 1500 mA.
    assert_eq!(rdo_position(0x13025896), 1);
    assert_eq!(rdo_op_ma(0x13025896), 1_500);
    assert_eq!(rdo(2, 2_000, 2_000), 2 << 28 | 1 << 25 | 200 << 10 | 200);
}

#[test]
fn pick_is_highest_fixed_the_sink_also_lists() {
    // 12/15/20 V are not in the S20's list, so 9 V wins even with no ceiling.
    assert_eq!(pick_aux(&CHARGER, &S20_SINK, 20_000), Some((2, 9_000, 3_000)));
    assert_eq!(pick_aux(&CHARGER, &S20_SINK, 8_999), Some((1, 5_000, 3_000)));
    // Sink list unknown: 5 V only.
    assert_eq!(pick_aux(&CHARGER, &[], 20_000), Some((1, 5_000, 3_000)));
    // Variable/PPS entries never match: top bits set.
    let pps = [fixed(5_000, 3_000), 3 << 30 | 9_000 / 100];
    assert_eq!(pick_aux(&pps, &S20_SINK, 20_000), Some((1, 5_000, 3_000)));
    assert_eq!(pick_aux(&[], &S20_SINK, 9_000), None);
}

#[test]
fn offer_mirrors_fixed_pdos_within_voltage_and_phone_support() {
    let mut b = Bench::new();
    b.ev(Event::AuxAttached);
    b.ev(Event::AuxCaps(&CHARGER));
    // Phone list unknown: every fixed PDO to 9 V (12 V+ above config.max_mv).
    assert_eq!(b.p.offered().len(), 2);
    b.ev(Event::PhoneSinkCaps(&S20_SINK));
    let offer = b.p.offered().to_vec();
    assert_eq!(offer.len(), 2);
    assert_eq!((pdo_mv(offer[0]), pdo_mv(offer[1])), (5_000, 9_000));
    // PDO1 loses dual-role power/data, keeps comm-capable.
    assert_eq!(offer[0] & (1 << 29 | 1 << 25), 0);
    assert_ne!(offer[0] & 1 << 26, 0);
}

#[test]
fn offer_current_is_capped_to_the_rail() {
    let mut p = Proxy::new(Config { max_mv: 9_000, max_ma: 2_000 });
    p.set_rails(SAFE_RAILS);
    p.step(0, Event::AuxAttached);
    p.step(0, Event::AuxCaps(&CHARGER));
    assert!(p.offered().iter().all(|&o| pdo_ma(o) == 2_000));
}

#[test]
fn attach_order_aux_5v_first_then_the_phone_closes_the_path() {
    let mut b = Bench::new();
    b.bridged();
    assert!(b.p.path_closed());
    assert_eq!(b.p.aux_mv, 5_000);
    assert!(!b.log.contains(&Action::OpenAndDischarge));
}

#[test]
fn phone_first_waits_for_aux() {
    let mut b = Bench::new();
    assert!(b.ev(Event::PhoneAttached).is_empty());
    b.ev(Event::AuxAttached);
    b.ev(Event::AuxCaps(&CHARGER));
    b.ev(Event::AuxAccept);
    let out = b.ev(Event::AuxPsRdy);
    assert_eq!(out, vec![Action::Arm(Window::V5), Action::ClosePath, Action::PhoneOffer]);
}

#[test]
fn two_live_sources_refuse_the_close() {
    for (rails, why) in [
        (Rails { control_closed: true, ..SAFE_RAILS }, Refusal::ControlClosed),
        (Rails { target_c_mv: 4_900, ..SAFE_RAILS }, Refusal::TargetCLive(4_900)),
        (Rails { aux_mv: 9_000, ..SAFE_RAILS }, Refusal::AuxOutOfWindow(9_000)),
    ] {
        assert_eq!(check_path(rails), Err(why));
        let mut b = Bench::new();
        b.p.set_rails(rails);
        b.ev(Event::AuxAttached);
        b.ev(Event::AuxCaps(&CHARGER));
        b.ev(Event::AuxAccept);
        b.ev(Event::AuxPsRdy);
        let out = b.ev(Event::PhoneAttached);
        assert_eq!(out, vec![Action::Refused(why)]);
        assert!(!b.p.path_closed());
        assert_eq!(b.p.phase, Phase::Refused);
        assert!(!b.log.contains(&Action::ClosePath));
    }
    assert_eq!(check_path(SAFE_RAILS), Ok(()));
}

#[test]
fn no_five_volt_pdo_is_refused() {
    let mut b = Bench::new();
    b.ev(Event::AuxAttached);
    let out = b.ev(Event::AuxCaps(&[fixed(9_000, 3_000)]));
    assert_eq!(out, vec![Action::Refused(Refusal::NoFiveVolts)]);
}

#[test]
fn nine_volt_request_goes_aux_first_then_ps_rdy_to_the_phone() {
    let mut b = Bench::new();
    b.bridged();
    b.now += 100;
    // Phone asks pos 2 (9 V), 2 A.
    let out = b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
    assert_eq!(
        out,
        vec![Action::Arm(Window::spanning(5_000, 9_000)), Action::AuxRequest(rdo(2, 3_000, 3_000))]
    );
    // Nothing is told to the phone until AUX answers.
    b.now += 7;
    assert_eq!(b.ev(Event::AuxAccept), vec![Action::PhoneAccept]);
    // No PS_RDY to the phone before AUX's.
    assert!(b.advance(100).is_empty());
    b.now += 60;
    let out = b.ev(Event::AuxPsRdy);
    assert_eq!(out, vec![Action::Arm(Window::for_nominal(9_000)), Action::PhonePsRdy]);
    assert_eq!(b.p.aux_mv, 9_000);
    assert_eq!(b.p.phone_contract, Some((2, 9_000, 2_000)));
    // The switches never moved after the close.
    assert_eq!(b.log.iter().filter(|a| **a == Action::ClosePath).count(), 1);
    assert!(!b.log.contains(&Action::OpenAndDischarge));
}

#[test]
fn same_voltage_request_waits_t_src_transition_and_leaves_aux_alone() {
    let mut b = Bench::new();
    b.bridged();
    let out = b.ev(Event::PhoneRequest(rdo(1, 1_500, 1_500)));
    assert_eq!(out, vec![Action::PhoneAccept]);
    assert!(b.advance(T_SRC_TRANSITION_MS - 1).is_empty());
    assert_eq!(b.advance(1), vec![Action::PhonePsRdy]);
    assert_eq!(b.p.phone_contract, Some((1, 5_000, 1_500)));
}

#[test]
fn bad_requests_are_rejected() {
    let mut b = Bench::new();
    b.bridged();
    // Position 3 does not exist (two objects offered), position 0 neither.
    assert_eq!(b.ev(Event::PhoneRequest(rdo(3, 500, 500))), vec![Action::PhoneReject]);
    assert_eq!(b.ev(Event::PhoneRequest(rdo(0, 500, 500))), vec![Action::PhoneReject]);
    // More than the PDO's current (3 A offered at 5 V).
    assert_eq!(b.ev(Event::PhoneRequest(rdo(1, 3_500, 3_500))), vec![Action::PhoneReject]);
}

#[test]
fn aux_reject_and_wait_are_mirrored_and_rearm_the_old_window() {
    for (event, mirrored) in [(Event::AuxReject, Action::PhoneReject), (Event::AuxWait, Action::PhoneWait)] {
        let mut b = Bench::new();
        b.bridged();
        b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
        let out = b.ev(event);
        assert_eq!(out, vec![mirrored, Action::Arm(Window::for_nominal(5_000))]);
        assert_eq!(b.p.phase, Phase::Bridged);
        assert_eq!(b.p.aux_mv, 5_000);
    }
}

#[test]
fn slow_aux_answer_gets_the_phone_a_wait_then_new_caps() {
    let mut b = Bench::new();
    b.bridged();
    b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
    // Budget used up before AUX answered: Wait to the phone.
    assert!(b.advance(PHONE_ANSWER_BUDGET_MS - 1).is_empty());
    assert_eq!(b.advance(1), vec![Action::PhoneWait]);
    // AUX accepts inside tSenderResponse: no Accept to the phone any more.
    assert!(b.ev(Event::AuxAccept).is_empty());
    let out = b.ev(Event::AuxPsRdy);
    assert_eq!(out, vec![Action::Arm(Window::for_nominal(9_000)), Action::PhoneOffer]);
    // The phone re-requests 9 V against the 9 V rail: same-voltage path.
    assert_eq!(b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000))), vec![Action::PhoneAccept]);
}

#[test]
fn aux_silence_past_t_sender_response_hard_resets_both() {
    let mut b = Bench::new();
    b.bridged();
    b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
    b.advance(PHONE_ANSWER_BUDGET_MS);
    assert!(b.advance(T_SENDER_RESPONSE_MS - PHONE_ANSWER_BUDGET_MS - 1).is_empty());
    let out = b.advance(1);
    assert_eq!(out, vec![Action::AuxHardReset, Action::PhoneHardReset, Action::Disarm]);
    assert_eq!(b.p.phase, Phase::Resetting);
}

#[test]
fn aux_ps_rdy_timeout_hard_resets_both() {
    let mut b = Bench::new();
    b.bridged();
    b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
    b.ev(Event::AuxAccept);
    assert!(b.advance(T_PS_TRANSITION_MS - 1).is_empty());
    assert_eq!(b.advance(1), vec![Action::AuxHardReset, Action::PhoneHardReset, Action::Disarm]);
}

#[test]
fn phone_hard_reset_goes_to_aux_and_the_switches_stay() {
    let mut b = Bench::new();
    b.bridged();
    let out = b.ev(Event::PhoneHardReset);
    assert_eq!(out, vec![Action::AuxHardReset, Action::Disarm]);
    assert_eq!(b.p.phase, Phase::Resetting);
    assert!(b.p.path_closed());
    // AUX comes back at 5 V with new caps: request 5 V, re-arm, offer again.
    b.ev(Event::AuxCaps(&CHARGER));
    b.ev(Event::AuxAccept);
    let out = b.ev(Event::AuxPsRdy);
    assert_eq!(out, vec![Action::Arm(Window::V5), Action::PhoneOffer]);
    assert_eq!(b.p.phase, Phase::Bridged);
    assert!(!b.log.contains(&Action::OpenAndDischarge));
}

#[test]
fn aux_hard_reset_goes_to_the_phone() {
    let mut b = Bench::new();
    b.bridged();
    b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
    b.ev(Event::AuxAccept);
    b.ev(Event::AuxPsRdy);
    assert_eq!(b.p.aux_mv, 9_000);
    let out = b.ev(Event::AuxHardReset);
    assert_eq!(out, vec![Action::PhoneHardReset, Action::Disarm]);
    // The rail is a 5 V rail again after the reset.
    assert_eq!(b.p.aux_mv, 5_000);
    assert_eq!(b.p.phone_contract, None);
}

#[test]
fn aux_never_returning_after_a_reset_is_a_detach() {
    let mut b = Bench::new();
    b.bridged();
    b.ev(Event::AuxHardReset);
    assert!(b.advance(RESET_RECOVERY_MS - 1).is_empty());
    let out = b.advance(1);
    assert_eq!(out, vec![Action::Disarm, Action::PhoneHardReset, Action::OpenAndDischarge]);
}

#[test]
fn aux_detach_discharges_and_hard_resets_the_phone_in_that_order() {
    let mut b = Bench::new();
    b.bridged();
    let out = b.ev(Event::AuxDetached);
    assert_eq!(out, vec![Action::Disarm, Action::PhoneHardReset, Action::OpenAndDischarge]);
    assert_eq!(b.p.phase, Phase::Discharging);
    assert!(!b.p.path_closed());
    assert!(b.advance(T_SAFE0V_MS - 1).is_empty());
    assert!(b.advance(1).is_empty());
    assert_eq!(b.p.phase, Phase::Idle);
}

#[test]
fn aux_detach_with_the_path_open_touches_nothing() {
    let mut b = Bench::new();
    b.ev(Event::AuxAttached);
    b.ev(Event::AuxCaps(&CHARGER));
    b.ev(Event::AuxAccept);
    b.ev(Event::AuxPsRdy);
    assert!(b.ev(Event::AuxDetached).is_empty());
    assert_eq!(b.p.phase, Phase::Idle);
}

#[test]
fn phone_detach_opens_and_discharges_but_keeps_aux() {
    let mut b = Bench::new();
    b.bridged();
    let out = b.ev(Event::PhoneDetached);
    assert_eq!(out, vec![Action::Disarm, Action::OpenAndDischarge]);
    b.advance(T_SAFE0V_MS);
    assert_eq!(b.p.phase, Phase::Idle);
}

#[test]
fn caps_never_arriving_hard_resets_aux() {
    let mut b = Bench::new();
    b.ev(Event::AuxAttached);
    assert!(b.advance(T_SINK_WAIT_CAP_MS - 1).is_empty());
    assert_eq!(b.advance(1), vec![Action::AuxHardReset, Action::Disarm]);
}

#[test]
fn requests_mid_transition_get_wait() {
    let mut b = Bench::new();
    b.bridged();
    b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
    assert_eq!(b.ev(Event::PhoneRequest(rdo(1, 500, 500))), vec![Action::PhoneWait]);
}

#[test]
fn late_aux_accept_after_wait_forces_new_caps_not_a_silent_rail_move() {
    let mut b = Bench::new();
    b.bridged();
    b.ev(Event::PhoneRequest(rdo(2, 2_000, 2_000)));
    b.ev(Event::AuxWait);
    // The library retries the same Request after tSinkRequest and AUX accepts it.
    let out = b.ev(Event::AuxAccept);
    assert_eq!(out, vec![Action::Arm(Window::spanning(5_000, 9_000))]);
    let out = b.ev(Event::AuxPsRdy);
    assert_eq!(out, vec![Action::Arm(Window::for_nominal(9_000)), Action::PhoneOffer]);
}

#[test]
fn timers_survive_millisecond_counter_wrap() {
    let mut b = Bench::new();
    b.now = u32::MAX - 10;
    b.ev(Event::AuxAttached);
    assert!(b.advance(T_SINK_WAIT_CAP_MS - 1).is_empty());
    assert_eq!(b.advance(1), vec![Action::AuxHardReset, Action::Disarm]);
}
