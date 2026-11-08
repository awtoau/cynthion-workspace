//! PD proxy state machine (#633): AUX is a PD sink, TARGET_C a PD source to the phone.
//!
//! Pure: events in, actions out, no I/O. Rail: AUX -> `aux_vbus_en` -> TARGET A node ->
//! `target_c_vbus_en` -> TARGET_C. Switches close once at 5 V and never move after that;
//! a voltage change is a new AUX contract, PS_RDY to the phone follows AUX's PS_RDY.
//! Spec refs: USB PD R3.1 Table 6-68 (timers), 8.3.3.2 (source), 8.3.3.3 (sink).

use crate::vbus_gate::Window;

pub const MAX_PDOS: usize = 7;

// ---- timers (spec value; what it waits for; on expiry) ----

/// tSenderResponse 24-30 ms: AUX Accept after our Request. Expiry: Hard Reset both ports.
pub const T_SENDER_RESPONSE_MS: u32 = 30;
/// Our own answer budget to the phone: its tSenderResponse (24-30 ms) minus one TX with
/// retries (about 10 ms). Expiry: Wait to the phone.
pub const PHONE_ANSWER_BUDGET_MS: u32 = 20;
/// tPSTransition 450-550 ms (sink): AUX PS_RDY after Accept. The phone's own timer starts
/// later, so ours at the spec minimum fires first. Expiry: Hard Reset both ports.
pub const T_PS_TRANSITION_MS: u32 = 450;
/// tSrcTransition 25-35 ms: Accept to PS_RDY to the phone when no AUX change is needed.
pub const T_SRC_TRANSITION_MS: u32 = 30;
/// tTypeCSinkWaitCap 310-620 ms: AUX Source_Capabilities after attach. Expiry: Hard Reset AUX.
pub const T_SINK_WAIT_CAP_MS: u32 = 620;
/// tSafe0V 650 ms (max): TARGET A to 0.8 V after the path opens.
pub const T_SAFE0V_MS: u32 = 650;
/// tSrcRecover 660-1000 ms (max): AUX back to 5 V after its Hard Reset.
pub const T_SRC_RECOVER_MS: u32 = 1_000;
/// After a Hard Reset: AUX VBUS to 0, recover, caps. Expiry: AUX is treated as detached.
pub const RESET_RECOVERY_MS: u32 = T_SAFE0V_MS + T_SRC_RECOVER_MS + T_SINK_WAIT_CAP_MS;

// ---- PDO / RDO fields (R3.1 6.4.1, 6.4.2) ----

const fn is_fixed(pdo: u32) -> bool {
    pdo >> 30 == 0
}

pub const fn pdo_mv(pdo: u32) -> u32 {
    (pdo >> 10 & 0x3ff) * 50
}

pub const fn pdo_ma(pdo: u32) -> u32 {
    (pdo & 0x3ff) * 10
}

/// Fixed-supply RDO: position, operating and maximum current, USB-comm-capable.
pub const fn rdo(position: u32, op_ma: u32, max_ma: u32) -> u32 {
    position << 28 | 1 << 25 | (op_ma / 10) << 10 | max_ma / 10
}

pub const fn rdo_position(rdo: u32) -> u32 {
    rdo >> 28 & 7
}

pub const fn rdo_op_ma(rdo: u32) -> u32 {
    (rdo >> 10 & 0x3ff) * 10
}

/// Dual-role power (bit 29) and dual-role data (bit 25) are cleared on the mirrored
/// first PDO: the proxy honours no swaps.
const PDO1_CLEAR: u32 = 1 << 29 | 1 << 25;

/// Highest fixed voltage at or below `max_mv` that the sink also lists; 5 V when the
/// sink's list is unknown (empty). Returns `(position, mv, ma)` of the source PDO; the
/// current is the source's own maximum.
pub fn pick_aux(source: &[u32], sink: &[u32], max_mv: u32) -> Option<(u32, u32, u32)> {
    let mut best: Option<(u32, u32, u32)> = None;
    for (index, &pdo) in source.iter().enumerate() {
        if !is_fixed(pdo) {
            continue;
        }
        let mv = pdo_mv(pdo);
        let listed = if sink.is_empty() {
            mv == 5_000
        } else {
            sink.iter().any(|&s| is_fixed(s) && pdo_mv(s) == mv)
        };
        if mv > max_mv || !listed || pdo_ma(pdo) == 0 {
            continue;
        }
        if best.map_or(true, |(_, best_mv, _)| mv > best_mv) {
            best = Some((index as u32 + 1, mv, pdo_ma(pdo)));
        }
    }
    best
}

// ---- configuration and inputs ----

#[derive(Copy, Clone)]
pub struct Config {
    /// Highest fixed voltage offered to the phone. TARGET A (USB-A) sits at this voltage.
    pub max_mv: u32,
    /// Current ceiling of the rail path (connector and switch), not the source's.
    pub max_ma: u32,
}

pub const CONFIG_9V: Config = Config { max_mv: 9_000, max_ma: 3_000 };

/// What the glue measured/read just before an event; used to refuse a tie.
#[derive(Copy, Clone, PartialEq, Eq)]
pub struct Rails {
    /// `control_vbus_en` is closed: the host's 5 V is on the node.
    pub control_closed: bool,
    pub target_c_mv: u32,
    pub aux_mv: u32,
}

/// Why the path was not closed.
#[derive(Copy, Clone, PartialEq, Eq, Debug)]
pub enum Refusal {
    /// CONTROL feeds the node: closing AUX would tie two live sources.
    ControlClosed,
    /// TARGET_C already carries a voltage: something else is live there.
    TargetCLive(u32),
    /// AUX VBUS is outside the 5 V window.
    AuxOutOfWindow(u32),
    /// AUX offers no usable fixed 5 V PDO.
    NoFiveVolts,
}

/// Vbus below this on TARGET_C counts as dead (vSafe0V is 0.8 V; sink detect is 2.0 V).
const DEAD_MV: u32 = 800;

pub fn check_path(rails: Rails) -> Result<(), Refusal> {
    if rails.control_closed {
        return Err(Refusal::ControlClosed);
    }
    if rails.target_c_mv > DEAD_MV {
        return Err(Refusal::TargetCLive(rails.target_c_mv));
    }
    let w = Window::V5;
    if rails.aux_mv < w.floor_mv || rails.aux_mv > w.limit_mv {
        return Err(Refusal::AuxOutOfWindow(rails.aux_mv));
    }
    Ok(())
}

pub enum Event<'a> {
    Tick,
    /// AUX shows a source (Rp seen, VBUS present).
    AuxAttached,
    AuxCaps(&'a [u32]),
    AuxAccept,
    AuxReject,
    AuxWait,
    AuxPsRdy,
    AuxHardReset,
    AuxDetached,
    /// A sink is on TARGET_C (CC seen).
    PhoneAttached,
    PhoneRequest(u32),
    PhoneHardReset,
    PhoneDetached,
    /// The phone's Sink_Capabilities, when known.
    PhoneSinkCaps(&'a [u32]),
}

#[derive(Copy, Clone, PartialEq, Eq, Debug)]
pub enum Action {
    /// Arm the PAC1954 limits on AUX to this window.
    Arm(Window),
    Disarm,
    /// Close `aux_vbus_en` and `target_c_vbus_en` (CONTROL stays open).
    ClosePath,
    /// Open the path and drain TARGET A.
    OpenAndDischarge,
    /// Send Request to AUX with this RDO.
    AuxRequest(u32),
    AuxHardReset,
    /// (Re)send Source_Capabilities to the phone from [`Proxy::offered`].
    PhoneOffer,
    PhoneAccept,
    PhoneReject,
    PhoneWait,
    PhonePsRdy,
    PhoneHardReset,
    Refused(Refusal),
}

pub struct Out {
    items: [Action; 4],
    len: usize,
}

impl Out {
    const fn new() -> Out {
        Out { items: [Action::Disarm; 4], len: 0 }
    }

    fn push(&mut self, action: Action) {
        if self.len < self.items.len() {
            self.items[self.len] = action;
            self.len += 1;
        }
    }

    pub fn as_slice(&self) -> &[Action] {
        &self.items[..self.len]
    }
}

#[derive(Copy, Clone, PartialEq, Eq, Debug)]
pub enum Phase {
    Idle,
    /// AUX attached, waiting for its Source_Capabilities.
    AwaitCaps,
    /// Request(5 V) sent, waiting Accept.
    Aux5Request,
    Aux5Transition,
    /// AUX at 5 V, path open, no phone yet.
    AuxReady,
    /// Path closed, AUX and phone consistent.
    Bridged,
    /// Same-voltage phone Request accepted, PS_RDY after tSrcTransition.
    PhoneSettle,
    /// Request(N) sent to AUX, waiting Accept.
    AuxRequest,
    /// AUX accepted, waiting PS_RDY.
    AuxTransition,
    /// Hard Reset propagated; waiting for AUX to come back.
    Resetting,
    Discharging,
    /// Path refused; nothing moves until detach or off.
    Refused,
}

pub struct Proxy {
    cfg: Config,
    pub phase: Phase,
    deadline: u32,
    /// AUX contract voltage; 5000 until a change completes.
    pub aux_mv: u32,
    aux_caps: [u32; MAX_PDOS],
    aux_n: usize,
    phone_caps: [u32; MAX_PDOS],
    phone_n: usize,
    offered: [u32; MAX_PDOS],
    /// AUX position behind each offered object.
    map: [u8; MAX_PDOS],
    offered_n: usize,
    phone_present: bool,
    path_closed: bool,
    /// Voltage the pending AUX change is for.
    target_mv: u32,
    waited: bool,
    pending: Option<(u32, u32, u32)>,
    /// `(position, mv, ma)` the phone holds.
    pub phone_contract: Option<(u32, u32, u32)>,
    rails: Rails,
    pub refusal: Option<Refusal>,
}

fn before(now: u32, deadline: u32) -> bool {
    (now.wrapping_sub(deadline) as i32) < 0
}

impl Proxy {
    pub const fn new(cfg: Config) -> Proxy {
        Proxy {
            cfg,
            phase: Phase::Idle,
            deadline: 0,
            aux_mv: 5_000,
            aux_caps: [0; MAX_PDOS],
            aux_n: 0,
            phone_caps: [0; MAX_PDOS],
            phone_n: 0,
            offered: [0; MAX_PDOS],
            map: [0; MAX_PDOS],
            offered_n: 0,
            phone_present: false,
            path_closed: false,
            target_mv: 0,
            waited: false,
            pending: None,
            phone_contract: None,
            rails: Rails { control_closed: false, target_c_mv: 0, aux_mv: 0 },
            refusal: None,
        }
    }

    pub fn set_rails(&mut self, rails: Rails) {
        self.rails = rails;
    }

    /// The Source_Capabilities for the phone: AUX's fixed PDOs, capped to the config.
    pub fn offered(&self) -> &[u32] {
        &self.offered[..self.offered_n]
    }

    pub fn path_closed(&self) -> bool {
        self.path_closed
    }

    fn rebuild_offer(&mut self) {
        self.offered_n = 0;
        for index in 0..self.aux_n {
            let pdo = self.aux_caps[index];
            let mv = pdo_mv(pdo);
            let known = self.phone_n == 0 || self.phone_caps[..self.phone_n].iter().any(|&s| is_fixed(s) && pdo_mv(s) == mv);
            if !is_fixed(pdo) || mv > self.cfg.max_mv || pdo_ma(pdo) == 0 || !(known || mv == 5_000) {
                continue;
            }
            let ma = pdo_ma(pdo).min(self.cfg.max_ma);
            let mut out = pdo & !0x3ff | ma / 10;
            if self.offered_n == 0 {
                out &= !PDO1_CLEAR;
            }
            self.offered[self.offered_n] = out;
            self.map[self.offered_n] = index as u8 + 1;
            self.offered_n += 1;
        }
    }

    fn begin_reset(&mut self, now: u32, out: &mut Out) {
        out.push(Action::Disarm);
        self.phase = Phase::Resetting;
        self.deadline = now.wrapping_add(RESET_RECOVERY_MS);
        self.phone_contract = None;
        self.aux_mv = 5_000;
    }

    fn both_reset(&mut self, now: u32, out: &mut Out) {
        out.push(Action::AuxHardReset);
        out.push(Action::PhoneHardReset);
        self.begin_reset(now, out);
    }

    /// Close the path if AUX is at 5 V and a phone is waiting.
    fn try_close(&mut self, out: &mut Out) {
        if self.path_closed || !self.phone_present || self.phase != Phase::AuxReady {
            return;
        }
        match check_path(self.rails) {
            Ok(()) => {
                out.push(Action::Arm(Window::V5));
                out.push(Action::ClosePath);
                out.push(Action::PhoneOffer);
                self.path_closed = true;
                self.phase = Phase::Bridged;
            }
            Err(refusal) => {
                out.push(Action::Refused(refusal));
                self.refusal = Some(refusal);
                self.phase = Phase::Refused;
            }
        }
    }

    fn request_five(&mut self, now: u32, out: &mut Out) {
        match pick_aux(&self.aux_caps[..self.aux_n], &[], 5_000) {
            Some((position, _, ma)) => {
                out.push(Action::AuxRequest(rdo(position, ma, ma)));
                self.phase = Phase::Aux5Request;
                self.deadline = now.wrapping_add(T_SENDER_RESPONSE_MS);
            }
            None => {
                out.push(Action::Refused(Refusal::NoFiveVolts));
                self.refusal = Some(Refusal::NoFiveVolts);
                self.phase = Phase::Refused;
            }
        }
    }

    pub fn step(&mut self, now: u32, event: Event) -> Out {
        let mut out = Out::new();
        match event {
            Event::Tick => self.tick(now, &mut out),
            Event::AuxAttached => {
                if self.phase == Phase::Idle {
                    self.phase = Phase::AwaitCaps;
                    self.deadline = now.wrapping_add(T_SINK_WAIT_CAP_MS);
                }
            }
            Event::AuxCaps(caps) => {
                self.aux_n = caps.len().min(MAX_PDOS);
                self.aux_caps[..self.aux_n].copy_from_slice(&caps[..self.aux_n]);
                self.rebuild_offer();
                match self.phase {
                    Phase::AwaitCaps | Phase::Resetting => self.request_five(now, &mut out),
                    // New capabilities mid-session (source changed its list): the offer
                    // follows; the phone is told, the AUX contract is untouched.
                    Phase::Bridged => out.push(Action::PhoneOffer),
                    _ => {}
                }
            }
            Event::AuxAccept => match self.phase {
                Phase::Aux5Request => {
                    self.phase = Phase::Aux5Transition;
                    self.deadline = now.wrapping_add(T_PS_TRANSITION_MS);
                }
                Phase::AuxRequest => {
                    if !self.waited {
                        out.push(Action::PhoneAccept);
                    }
                    self.phase = Phase::AuxTransition;
                    self.deadline = now.wrapping_add(T_PS_TRANSITION_MS);
                }
                // A retried Request (after Wait) accepted late: the rail will move, the
                // phone was already told Wait and gets new capabilities afterwards.
                Phase::Bridged if self.target_mv != 0 => {
                    out.push(Action::Arm(Window::spanning(self.aux_mv, self.target_mv)));
                    self.waited = true;
                    self.phase = Phase::AuxTransition;
                    self.deadline = now.wrapping_add(T_PS_TRANSITION_MS);
                }
                _ => {}
            },
            Event::AuxReject | Event::AuxWait => {
                let is_wait = matches!(event, Event::AuxWait);
                match self.phase {
                    Phase::Aux5Request => self.both_reset(now, &mut out),
                    Phase::AuxRequest => {
                        if !self.waited {
                            out.push(if is_wait { Action::PhoneWait } else { Action::PhoneReject });
                        }
                        out.push(Action::Arm(Window::for_nominal(self.aux_mv)));
                        self.phase = Phase::Bridged;
                        if !is_wait {
                            self.target_mv = 0;
                        }
                    }
                    _ => {}
                }
            }
            Event::AuxPsRdy => match self.phase {
                Phase::Aux5Transition => {
                    self.aux_mv = 5_000;
                    if self.path_closed {
                        // Back after a Hard Reset: the switches never moved.
                        out.push(Action::Arm(Window::V5));
                        out.push(Action::PhoneOffer);
                        self.phase = Phase::Bridged;
                    } else {
                        self.phase = Phase::AuxReady;
                        self.try_close(&mut out);
                    }
                }
                Phase::AuxTransition => {
                    self.aux_mv = self.target_mv;
                    self.target_mv = 0;
                    out.push(Action::Arm(Window::for_nominal(self.aux_mv)));
                    if self.waited {
                        out.push(Action::PhoneOffer);
                        self.phone_contract = None;
                    } else {
                        out.push(Action::PhonePsRdy);
                        self.phone_contract = self.pending;
                    }
                    self.waited = false;
                    self.phase = Phase::Bridged;
                }
                _ => {}
            },
            Event::AuxHardReset => {
                if self.phase != Phase::Idle && self.phase != Phase::Discharging {
                    out.push(Action::PhoneHardReset);
                    self.begin_reset(now, &mut out);
                }
            }
            Event::AuxDetached => self.detach(now, &mut out),
            Event::PhoneAttached => {
                self.phone_present = true;
                self.try_close(&mut out);
            }
            Event::PhoneDetached => {
                self.phone_present = false;
                self.phone_contract = None;
                if self.path_closed {
                    out.push(Action::Disarm);
                    out.push(Action::OpenAndDischarge);
                    self.path_closed = false;
                    self.phase = Phase::Discharging;
                    self.deadline = now.wrapping_add(T_SAFE0V_MS);
                }
            }
            Event::PhoneHardReset => {
                if self.path_closed && self.phase != Phase::Resetting {
                    out.push(Action::AuxHardReset);
                    self.begin_reset(now, &mut out);
                }
            }
            Event::PhoneSinkCaps(caps) => {
                self.phone_n = caps.len().min(MAX_PDOS);
                self.phone_caps[..self.phone_n].copy_from_slice(&caps[..self.phone_n]);
                self.rebuild_offer();
            }
            Event::PhoneRequest(request) => self.phone_request(now, request, &mut out),
        }
        out
    }

    fn detach(&mut self, now: u32, out: &mut Out) {
        if self.phase == Phase::Idle {
            return;
        }
        if self.path_closed {
            out.push(Action::Disarm);
            out.push(Action::PhoneHardReset);
            out.push(Action::OpenAndDischarge);
            self.phase = Phase::Discharging;
            self.deadline = now.wrapping_add(T_SAFE0V_MS);
        } else {
            self.phase = Phase::Idle;
        }
        self.path_closed = false;
        self.phone_contract = None;
        self.aux_n = 0;
        self.offered_n = 0;
        self.aux_mv = 5_000;
        self.target_mv = 0;
        self.refusal = None;
    }

    fn phone_request(&mut self, now: u32, request: u32, out: &mut Out) {
        match self.phase {
            Phase::Bridged => {}
            // The phone retries after tSinkRequest (100 ms) once AUX is back.
            Phase::Resetting => {
                out.push(Action::PhoneWait);
                return;
            }
            Phase::Discharging | Phase::Idle | Phase::Refused => return,
            _ => {
                out.push(Action::PhoneWait);
                return;
            }
        }
        let position = rdo_position(request) as usize;
        if position == 0 || position > self.offered_n || rdo_op_ma(request) > pdo_ma(self.offered[position - 1]) {
            out.push(Action::PhoneReject);
            return;
        }
        let pdo = self.offered[position - 1];
        let mv = pdo_mv(pdo);
        if mv == self.aux_mv {
            out.push(Action::PhoneAccept);
            self.pending = Some((position as u32, mv, rdo_op_ma(request)));
            self.phase = Phase::PhoneSettle;
            self.deadline = now.wrapping_add(T_SRC_TRANSITION_MS);
            return;
        }
        // Rail has to move: AUX first. The window spans old..new until PS_RDY.
        let aux_pos = self.map[position - 1] as u32;
        self.target_mv = mv;
        self.waited = false;
        out.push(Action::Arm(Window::spanning(self.aux_mv, mv)));
        out.push(Action::AuxRequest(rdo(aux_pos, pdo_ma(self.aux_caps[aux_pos as usize - 1]), pdo_ma(self.aux_caps[aux_pos as usize - 1]))));
        self.pending = Some((position as u32, mv, rdo_op_ma(request)));
        self.phase = Phase::AuxRequest;
        self.deadline = now.wrapping_add(PHONE_ANSWER_BUDGET_MS);
    }

    fn tick(&mut self, now: u32, out: &mut Out) {
        if before(now, self.deadline) {
            return;
        }
        match self.phase {
            Phase::AwaitCaps => {
                out.push(Action::AuxHardReset);
                self.begin_reset(now, out);
            }
            Phase::Aux5Request | Phase::Aux5Transition => self.both_reset(now, out),
            Phase::PhoneSettle => {
                out.push(Action::PhonePsRdy);
                self.phone_contract = self.pending;
                self.phase = Phase::Bridged;
            }
            // Budget for the phone's answer: tell it to wait, keep waiting for AUX
            // until tSenderResponse.
            Phase::AuxRequest => {
                if !self.waited {
                    out.push(Action::PhoneWait);
                    self.waited = true;
                    self.deadline = now.wrapping_add(T_SENDER_RESPONSE_MS - PHONE_ANSWER_BUDGET_MS);
                } else {
                    self.both_reset(now, out);
                }
            }
            Phase::AuxTransition => self.both_reset(now, out),
            Phase::Resetting => self.detach(now, out),
            Phase::Discharging => {
                self.phase = Phase::Idle;
            }
            _ => {}
        }
    }

    pub fn off(&mut self, now: u32) -> Out {
        let mut out = Out::new();
        self.detach(now, &mut out);
        if self.phase == Phase::Discharging {
            self.deadline = now.wrapping_add(T_SAFE0V_MS);
        }
        out
    }

    pub fn phase_name(&self) -> &'static str {
        match self.phase {
            Phase::Idle => "idle",
            Phase::AwaitCaps => "await-caps",
            Phase::Aux5Request => "aux-request-5v",
            Phase::Aux5Transition => "aux-transition-5v",
            Phase::AuxReady => "aux-ready",
            Phase::Bridged => "bridged",
            Phase::PhoneSettle => "phone-settle",
            Phase::AuxRequest => "aux-request",
            Phase::AuxTransition => "aux-transition",
            Phase::Resetting => "resetting",
            Phase::Discharging => "discharging",
            Phase::Refused => "refused",
        }
    }
}
