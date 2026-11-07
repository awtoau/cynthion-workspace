//! The PD source policy engine against a scripted sink (#633).

use cynthion_soc_tests::pd_msg::*;
use cynthion_soc_tests::pd_src::*;

const PR_SWAP: u8 = 10;

fn request(id: u8, pos: u32, op_ma: u32, max_ma: u32) -> Message {
    // A sink's header: power role sink, data role UFP, rev 3.
    let header = (REQUEST as u16) | (2 << 6) | ((id as u16) << 9) | (1 << 12);
    let rdo = (pos << 28) | (op_ma / 10) << 10 | (max_ma / 10);
    Message { header, data: [rdo, 0, 0, 0, 0, 0, 0] }
}

fn control(kind: u8, id: u8) -> Message {
    Message { header: (kind as u16) | (2 << 6) | ((id as u16) << 9), data: [0; 7] }
}

fn sent(out: Out) -> Message {
    out.send.expect("engine should send")
}

/// Drive to Ready; returns the engine and the time.
fn contract() -> (Engine, u32) {
    let mut e = Engine::new();
    let mut t = 100;
    e.event(t, Event::Arm);
    assert!(e.state == State::WaitVbus);
    let caps = sent(e.event(t, Event::VbusOk));
    assert_eq!(caps.kind(), SOURCE_CAP);
    assert_eq!(caps.id(), 0);
    e.event(t + 1, Event::TxSent);
    assert!(e.state == State::WaitRequest);
    t += 5;
    let accept = sent(e.event(t, Event::Rx(request(0, 1, 500, 500))));
    assert_eq!(accept.kind(), ACCEPT);
    assert_eq!(accept.id(), 1); // one GoodCRC (for the caps) has been counted
    e.event(t + 1, Event::TxSent);
    assert!(e.state == State::Transition);
    assert!(e.event(t + 1 + T_SRC_TRANSITION - 1, Event::Tick).send.is_none());
    let ready = sent(e.event(t + 1 + T_SRC_TRANSITION, Event::Tick));
    assert_eq!(ready.kind(), PS_RDY);
    assert_eq!(ready.id(), 2);
    e.event(t + 40, Event::TxSent);
    assert!(e.state == State::Ready);
    (e, t + 40)
}

#[test]
fn full_negotiation() {
    let (e, _) = contract();
    assert_eq!(e.contract.unwrap().position(), 1);
}

#[test]
fn no_goodcrc_keeps_sending_caps_then_gives_up() {
    let mut e = Engine::new();
    let mut t = 0;
    e.event(t, Event::Arm);
    let mut out = e.event(t, Event::VbusOk);
    for n in 1..=N_CAPS {
        assert_eq!(sent(out).kind(), SOURCE_CAP, "attempt {n}");
        e.event(t, Event::TxFailed);
        if n == N_CAPS {
            break;
        }
        assert!(e.state == State::Discovery);
        assert!(e.event(t + T_SEND_CAP - 1, Event::Tick).send.is_none());
        t += T_SEND_CAP;
        out = e.event(t, Event::Tick);
    }
    assert!(e.state == State::NoPd);
}

#[test]
fn no_request_in_sender_response_hard_resets_and_recovers() {
    let mut e = Engine::new();
    e.event(0, Event::Arm);
    e.event(0, Event::VbusOk);
    e.event(2, Event::TxSent);
    assert!(!e.event(2 + T_SENDER_RESPONSE - 1, Event::Tick).hard_reset);
    assert!(e.event(2 + T_SENDER_RESPONSE, Event::Tick).hard_reset);
    let t = 2 + T_SENDER_RESPONSE;
    e.event(t + 2, Event::HardResetSent);
    assert!(e.state == State::HardWait);
    assert!(e.event(t + 2 + T_PS_HARD_RESET - 1, Event::Tick).vbus.is_none());
    let t = t + 2 + T_PS_HARD_RESET;
    assert_eq!(e.event(t, Event::Tick).vbus, Some(false));
    assert!(e.event(t + T_SRC_RECOVER - 1, Event::Tick).vbus.is_none());
    assert_eq!(e.event(t + T_SRC_RECOVER, Event::Tick).vbus, Some(true));
    assert!(e.state == State::WaitVbus);
    // Protocol layer was reset: caps go out as id 0 again.
    assert_eq!(sent(e.event(t + T_SRC_RECOVER + 1, Event::VbusOk)).id(), 0);
}

#[test]
fn request_above_offer_is_rejected_and_caps_resent() {
    let mut e = Engine::new();
    e.event(0, Event::Arm);
    e.event(0, Event::VbusOk);
    e.event(1, Event::TxSent);
    let reject = sent(e.event(5, Event::Rx(request(0, 1, 2000, 2000))));
    assert_eq!(reject.kind(), REJECT);
    e.event(6, Event::TxSent);
    assert!(e.state == State::NeedCaps);
    assert_eq!(sent(e.event(7, Event::Tick)).kind(), SOURCE_CAP);
    assert!(e.contract.is_none());
}

#[test]
fn request_for_a_missing_object_is_rejected() {
    let mut e = Engine::new();
    e.event(0, Event::Arm);
    e.event(0, Event::VbusOk);
    e.event(1, Event::TxSent);
    assert_eq!(sent(e.event(5, Event::Rx(request(0, 2, 100, 100)))).kind(), REJECT);
}

#[test]
fn duplicate_message_id_is_ignored() {
    let (mut e, t) = contract();
    // Sink repeats Get_Source_Cap id 1 after a lost GoodCRC.
    let first = e.event(t + 1, Event::Rx(control(GET_SOURCE_CAP, 1)));
    assert_eq!(sent(first).kind(), SOURCE_CAP);
    e.event(t + 2, Event::TxSent);
    assert!(e.event(t + 3, Event::Rx(control(GET_SOURCE_CAP, 1))).send.is_none());
}

#[test]
fn get_source_cap_in_ready_answers_with_caps() {
    let (mut e, t) = contract();
    let out = e.event(t + 1, Event::Rx(control(GET_SOURCE_CAP, 1)));
    assert_eq!(sent(out).kind(), SOURCE_CAP);
    e.event(t + 2, Event::TxSent);
    assert!(e.state == State::Ready);
}

#[test]
fn unsupported_message_gets_not_supported() {
    let (mut e, t) = contract();
    assert_eq!(sent(e.event(t + 1, Event::Rx(control(PR_SWAP, 1)))).kind(), NOT_SUPPORTED);
}

#[test]
fn soft_reset_from_sink_accepts_and_renegotiates() {
    let (mut e, t) = contract();
    let accept = sent(e.event(t + 1, Event::Rx(control(SOFT_RESET, 2))));
    assert_eq!(accept.kind(), ACCEPT);
    assert_eq!(accept.id(), 0);
    e.event(t + 2, Event::TxSent);
    let caps = sent(e.event(t + 3, Event::Tick));
    assert_eq!(caps.kind(), SOURCE_CAP);
    assert_eq!(caps.id(), 1);
}

#[test]
fn failed_send_in_contract_soft_resets_then_hard_resets() {
    let (mut e, t) = contract();
    e.event(t + 1, Event::Rx(control(GET_SOURCE_CAP, 1)));
    let soft = sent(e.event(t + 2, Event::TxFailed));
    assert_eq!(soft.kind(), SOFT_RESET);
    assert_eq!(soft.id(), 0);
    // Soft_Reset itself unacknowledged: Hard Reset.
    assert!(e.event(t + 3, Event::TxFailed).hard_reset);
}

#[test]
fn lost_txsent_interrupt_is_bounded_by_the_guard() {
    let mut e = Engine::new();
    e.event(0, Event::Arm);
    e.event(0, Event::VbusOk);
    assert!(e.event(T_TX_GUARD - 1, Event::Tick).send.is_none());
    assert!(e.state == State::Sending);
    e.event(T_TX_GUARD, Event::Tick);
    assert!(e.state == State::Discovery);
}

#[test]
fn received_hard_reset_cycles_vbus() {
    let (mut e, t) = contract();
    e.event(t + 1, Event::HardResetRx);
    assert!(e.contract.is_none());
    assert_eq!(e.event(t + 1 + T_PS_HARD_RESET, Event::Tick).vbus, Some(false));
}

#[test]
fn timers_survive_the_counter_wrapping() {
    let mut e = Engine::new();
    let t = u32::MAX - 10;
    e.event(t, Event::Arm);
    e.event(t, Event::VbusOk);
    e.event(t.wrapping_add(1), Event::TxSent);
    let after = t.wrapping_add(1 + T_SENDER_RESPONSE);
    assert!(e.event(after, Event::Tick).hard_reset);
}
