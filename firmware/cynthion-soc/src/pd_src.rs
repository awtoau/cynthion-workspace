//! USB PD source policy engine, SPR fixed supplies only (#633).
//!
//! Pure: events in, one [`Out`] back, time passed in as milliseconds. No
//! `crate::` paths except `pd_msg`, so the host tests run it. `src/pd.rs` is
//! the FUSB302B glue. States follow USB PD R3.1 V1.6 §8.3.3.2 (PE_SRC_*);
//! not implemented: swaps, VDM, PPS, EPR, collision avoidance (Rp SinkTxNG/Ok).

use crate::pd_msg::*;

// ---- timers, ms. Spec values are PD R3.1 V1.6 Table 6-63 / 7-22 ----

/// tTypeCSendSourceCap, 100-200 ms (nom 150), §6.6.3.1: gap between
/// Source_Capabilities while no sink has answered.
pub const T_SEND_CAP: u32 = 150;
/// tSenderResponse, 27-33 ms, §6.6.2: Request must follow our caps' GoodCRC.
/// Expiry: Hard Reset (§8.3.3.2.3). 33 = the spec max; the 1 ms tick adds <=1.
pub const T_SENDER_RESPONSE: u32 = 33;
/// tSrcTransition, 25-35 ms, Table 7-22: Accept -> PS_RDY, so the sink can
/// quiesce. Nominal 30.
pub const T_SRC_TRANSITION: u32 = 30;
/// tPSHardReset, 25-35 ms (nom 30), §6.6.11.2: after Hard Reset signalling,
/// before VBUS is removed.
pub const T_PS_HARD_RESET: u32 = 30;
/// tSrcRecover, 660-1000 ms, Table 7-22: VBUS off before it is re-applied.
pub const T_SRC_RECOVER: u32 = 800;
/// Chip-side send bound, not a spec timer: 3 transmissions (nRetryCount 2) of a
/// <=40-byte BMC frame at 300 kbit/s (~1.3 ms) plus 3 x tReceive 1.1 ms = 7.2 ms,
/// x1.25 = 9; rounded to the 1 ms tick. Expiry: treated as a failed send.
pub const T_TX_GUARD: u32 = 10;
/// nCapsCount, §6.7.4: Source_Capabilities sent before giving up on a partner.
pub const N_CAPS: u8 = 50;
/// nHardResetCount, §6.7.3.
pub const N_HARD_RESET: u8 = 2;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum State {
    Off,
    WaitVbus,
    /// A message is on the wire; [`Engine::next_ok`] / [`Engine::next_fail`] follow.
    Sending,
    /// Send Source_Capabilities on the next tick.
    NeedCaps,
    /// PE_SRC_Discovery: waiting tTypeCSendSourceCap.
    Discovery,
    /// Caps acknowledged; waiting for the Request (SenderResponseTimer).
    WaitRequest,
    /// Accept sent; waiting tSrcTransition before PS_RDY.
    Transition,
    Ready,
    /// Soft_Reset sent; waiting for Accept.
    SoftResetWait,
    /// Hard Reset signalling requested; waiting for the chip to say it was sent.
    HardSending,
    HardWait,
    Recover,
    /// Partner never answered (nCapsCount / nHardResetCount spent).
    NoPd,
}

impl State {
    pub fn name(self) -> &'static str {
        match self {
            State::Off => "off",
            State::WaitVbus => "wait-vbus",
            State::Sending => "sending",
            State::NeedCaps => "need-caps",
            State::Discovery => "discovery",
            State::WaitRequest => "wait-request",
            State::Transition => "transition",
            State::Ready => "ready",
            State::SoftResetWait => "soft-reset",
            State::HardSending => "hard-reset",
            State::HardWait => "hard-reset-wait",
            State::Recover => "recover",
            State::NoPd => "no-pd",
        }
    }
}

pub enum Event {
    /// The sink's Rd is attached on the comm pin.
    Arm,
    Disarm,
    /// VBUS is at vSafe5V.
    VbusOk,
    Rx(Message),
    /// The chip saw a GoodCRC for our message.
    TxSent,
    /// Retries exhausted, or the send guard expired.
    TxFailed,
    HardResetRx,
    HardResetSent,
    Tick,
}

#[derive(Default)]
pub struct Out {
    pub send: Option<Message>,
    /// Send Hard Reset signalling.
    pub hard_reset: bool,
    /// Switch VBUS: `Some(false)` off, `Some(true)` back on.
    pub vbus: Option<bool>,
}

pub struct Engine {
    pub state: State,
    deadline: u32,
    tx_id: u8,
    rx_id: Option<u8>,
    caps: [u32; 1],
    caps_count: u8,
    hard_resets: u8,
    next_ok: State,
    next_fail: State,
    /// The contract in force, once Accept has been sent.
    pub contract: Option<Rdo>,
}

impl Engine {
    pub const fn new() -> Engine {
        Engine {
            state: State::Off,
            deadline: 0,
            tx_id: 0,
            rx_id: None,
            caps: [fixed_pdo(5000, 900)],
            caps_count: 0,
            hard_resets: 0,
            next_ok: State::Off,
            next_fail: State::Off,
            contract: None,
        }
    }

    pub fn caps(&self) -> &[u32] {
        &self.caps
    }

    /// Advertise `ma` at 5 V. Takes effect on the next Source_Capabilities.
    pub fn set_current(&mut self, ma: u32) {
        self.caps[0] = fixed_pdo(5000, ma);
    }

    fn due(&self, now: u32) -> bool {
        now.wrapping_sub(self.deadline) as i32 >= 0
    }

    fn enter(&mut self, state: State, now: u32) {
        self.state = state;
        let wait = match state {
            State::WaitRequest | State::SoftResetWait => T_SENDER_RESPONSE,
            State::Transition => T_SRC_TRANSITION,
            State::Discovery => T_SEND_CAP,
            State::HardWait => T_PS_HARD_RESET,
            State::Recover => T_SRC_RECOVER,
            _ => 0,
        };
        self.deadline = now.wrapping_add(wait);
    }

    fn reset_protocol(&mut self) {
        self.tx_id = 0;
        self.rx_id = None;
    }

    fn send(&mut self, now: u32, out: &mut Out, kind: u8, objects: &[u32], ok: State, fail: State) {
        out.send = Some(Message::source(kind, self.tx_id, objects));
        self.next_ok = ok;
        self.next_fail = fail;
        self.state = State::Sending;
        self.deadline = now.wrapping_add(T_TX_GUARD);
    }

    fn send_caps(&mut self, now: u32, out: &mut Out) {
        self.caps_count += 1;
        let caps = self.caps;
        self.send(now, out, SOURCE_CAP, &caps, State::WaitRequest, State::Discovery);
    }

    fn hard_reset(&mut self, now: u32, out: &mut Out) {
        if self.hard_resets >= N_HARD_RESET {
            self.state = State::NoPd;
            return;
        }
        self.hard_resets += 1;
        self.contract = None;
        out.hard_reset = true;
        self.enter(State::HardSending, now);
        // The chip reports HardResetSent within a frame time; bound it like a send.
        self.deadline = now.wrapping_add(T_TX_GUARD);
    }

    fn soft_reset_begin(&mut self, now: u32, out: &mut Out) {
        self.reset_protocol();
        self.send(now, out, SOFT_RESET, &[], State::SoftResetWait, State::HardSending);
    }

    fn negotiate(&mut self, now: u32, out: &mut Out, rdo: u32) {
        let rdo = Rdo(rdo);
        if request_ok(rdo, &self.caps) {
            self.contract = Some(rdo);
            self.send(now, out, ACCEPT, &[], State::Transition, State::SoftResetWait);
        } else {
            let again = if self.contract.is_some() { State::Ready } else { State::NeedCaps };
            self.send(now, out, REJECT, &[], again, State::SoftResetWait);
        }
    }

    pub fn event(&mut self, now: u32, event: Event) -> Out {
        let mut out = Out::default();
        match event {
            Event::Arm => {
                self.hard_resets = 0;
                self.caps_count = 0;
                self.contract = None;
                self.reset_protocol();
                self.enter(State::WaitVbus, now);
            }
            Event::Disarm => self.state = State::Off,
            Event::VbusOk => {
                if self.state == State::WaitVbus {
                    self.send_caps(now, &mut out);
                }
            }
            Event::TxSent => {
                if self.state == State::Sending {
                    self.tx_id = (self.tx_id + 1) & 7;
                    if self.next_ok == State::WaitRequest {
                        // Caps acknowledged: a PD sink is there (§8.3.3.2.3).
                        self.caps_count = 0;
                        self.hard_resets = 0;
                    }
                    let next = self.next_ok;
                    self.enter(next, now);
                }
            }
            Event::TxFailed => {
                if self.state == State::Sending {
                    match self.next_fail {
                        State::Discovery if self.caps_count >= N_CAPS => self.state = State::NoPd,
                        State::Discovery => self.enter(State::Discovery, now),
                        State::SoftResetWait => self.soft_reset_begin(now, &mut out),
                        _ => self.hard_reset(now, &mut out),
                    }
                }
            }
            Event::HardResetRx => self.hard_reset_received(now),
            Event::HardResetSent => {
                if self.state == State::HardSending {
                    self.enter(State::HardWait, now);
                }
            }
            Event::Rx(message) => self.rx(now, &mut out, message),
            Event::Tick => self.tick(now, &mut out),
        }
        out
    }

    fn hard_reset_received(&mut self, now: u32) {
        if matches!(self.state, State::Off | State::NoPd) {
            return;
        }
        self.contract = None;
        self.enter(State::HardWait, now);
    }

    fn tick(&mut self, now: u32, out: &mut Out) {
        match self.state {
            State::NeedCaps => self.send_caps(now, out),
            _ if !self.due(now) => {}
            State::Sending => {
                // Guard expired with no TXSENT / RETRYFAIL: treat as failed.
                *out = self.event(now, Event::TxFailed);
            }
            State::Discovery => self.send_caps(now, out),
            State::WaitRequest | State::SoftResetWait => self.hard_reset(now, out),
            State::Transition => {
                self.send(now, out, PS_RDY, &[], State::Ready, State::SoftResetWait);
            }
            State::HardSending => self.enter(State::HardWait, now),
            State::HardWait => {
                out.vbus = Some(false);
                self.enter(State::Recover, now);
            }
            State::Recover => {
                out.vbus = Some(true);
                self.caps_count = 0;
                self.reset_protocol();
                self.enter(State::WaitVbus, now);
            }
            _ => {}
        }
    }

    fn rx(&mut self, now: u32, out: &mut Out, message: Message) {
        if matches!(self.state, State::Off | State::WaitVbus | State::NoPd) {
            return;
        }
        if message.kind() == GOOD_CRC && message.is_control() {
            return;
        }
        if self.rx_id == Some(message.id()) && message.kind() != SOFT_RESET {
            return; // our GoodCRC was lost and the sink retried
        }
        self.rx_id = Some(message.id());
        let control = message.is_control();
        let kind = message.kind();

        if control && kind == SOFT_RESET {
            self.reset_protocol();
            self.rx_id = Some(message.id());
            self.send(now, out, ACCEPT, &[], State::NeedCaps, State::HardSending);
            return;
        }
        match (self.state, control, kind) {
            (State::WaitRequest | State::Ready, false, REQUEST) => {
                self.negotiate(now, out, message.data[0])
            }
            (State::SoftResetWait, true, ACCEPT) => self.enter(State::NeedCaps, now),
            (State::Ready, true, GET_SOURCE_CAP) => {
                let caps = self.caps;
                self.send(now, out, SOURCE_CAP, &caps, State::Ready, State::SoftResetWait);
            }
            (State::Ready, true, PING) => {}
            // Anything else in an AMS we don't implement: refuse politely.
            (State::Ready, _, _) => {
                self.send(now, out, NOT_SUPPORTED, &[], State::Ready, State::SoftResetWait)
            }
            _ => {}
        }
    }
}
