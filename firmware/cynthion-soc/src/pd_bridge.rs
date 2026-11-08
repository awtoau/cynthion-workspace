//! Driver for the PD proxy (#633): feeds the `pd_proxy` state machine from the two PD
//! engines and the rail, and carries out its actions. No policy lives here.
//!
//! - AUX runs the `usbpd` sink engine (Rd on), TARGET_C the source engine in proxy mode.
//! - Events come from the engines' frame log; AUX VBUS from the PAC1954 sample.

use core::fmt::Write;

use crate::bus::Bus;
use crate::fusb302::{self, HostCurrent};
use crate::pd_lib::{self, DECIDE_ACCEPT, DECIDE_REJECT, DECIDE_WAIT};
use crate::pd_proxy::{self as px, Action, Event, Phase, Proxy, Rails};
use crate::uart::Uart;
use crate::vbus_gate::AUX as AUX_CH;
use crate::vbus_gate::TARGET_C as TARGET_C_CH;
use crate::{power, timer, vbus};

/// Attach poll for the phone's CC, ms: no faster than a person can plug (as `pd.rs`).
const PHONE_POLL_MS: u32 = 50;
/// Phone CC open on two reads this far apart = detach: tCCDebounce 100-200 ms (min).
const DETACH_DEBOUNCE_MS: u32 = 100;
/// AUX VBUS below this while a session runs = source gone: vSinkDisconnect (PD R3.1 7.1.2 ~3.67 V).
const AUX_GONE_MV: u32 = 3_670;
/// AUX VBUS at or above this = a source is there (the 5 V window floor).
const AUX_HERE_MV: u32 = 4_400;
/// After a failed or ended AUX session, ms before trying again: bounds I2C and Rd churn.
const AUX_RETRY_MS: u32 = 1_000;

const LOG: usize = 24;

#[derive(Clone, Copy)]
struct Note {
    at: u32,
    text: &'static str,
    value: u32,
}

pub struct Bridge {
    pub active: bool,
    proxy: Proxy,
    aux: pd_lib::Pd,
    phone_cc: u8,
    phone_up: bool,
    next_phone_poll: u32,
    open_since: u32,
    aux_retry_at: u32,
    pub aux_bytes: usize,
    log: [Note; LOG],
    logged: u32,
}

const EMPTY: Note = Note { at: 0, text: "", value: 0 };

impl Bridge {
    pub const fn new() -> Bridge {
        Bridge {
            active: false,
            proxy: Proxy::new(px::CONFIG_9V),
            aux: pd_lib::Pd::new(pd_lib::AUX),
            phone_cc: 0,
            phone_up: false,
            next_phone_poll: 0,
            open_since: 0,
            aux_retry_at: 0,
            aux_bytes: 0,
            log: [EMPTY; LOG],
            logged: 0,
        }
    }

    fn note(&mut self, text: &'static str, value: u32) {
        self.log[self.logged as usize % LOG] = Note { at: timer::millis(), text, value };
        self.logged = self.logged.wrapping_add(1);
    }

    /// Begin proxying. The caller has checked the preconditions it can see.
    pub fn on(&mut self, bus: &mut Bus, phone: &mut pd_lib::Pd) -> Result<(), &'static str> {
        fusb302::source_target(bus, HostCurrent::A1_5).map_err(|_| "TARGET_C CC setup failed")?;
        phone.stop();
        phone.set_proxy(true);
        self.proxy = Proxy::new(px::CONFIG_9V);
        self.phone_cc = 0;
        self.phone_up = false;
        self.aux_retry_at = 0;
        self.logged = 0;
        self.active = true;
        self.note("proxy on", 0);
        Ok(())
    }

    pub fn off(&mut self, bus: &mut Bus, monitor: &mut power::Monitor, phone: &mut pd_lib::Pd) {
        let out = self.proxy.off(timer::millis());
        self.run(out.as_slice(), bus, monitor, phone, false);
        self.aux.stop();
        phone.stop();
        phone.set_proxy(false);
        vbus::open_all();
        monitor.vbus_disarm(bus);
        let _ = fusb302::configure(bus, fusb302::Port::Aux);
        let _ = fusb302::configure(bus, fusb302::Port::Target);
        crate::irq::resume_type_c(crate::typec::index(fusb302::Port::Aux));
        crate::irq::resume_type_c(crate::typec::index(fusb302::Port::Target));
        self.active = false;
        self.note("proxy off", 0);
    }

    fn rails(&mut self, bus: &mut Bus, monitor: &mut power::Monitor) {
        let sample = monitor.read_now(bus).ok();
        let mv = |channel: usize| sample.map_or(u32::MAX, |s| s.readings[channel].bus_mv);
        self.proxy.set_rails(Rails {
            control_closed: vbus::is_closed(vbus::Source::Control),
            target_c_mv: mv(TARGET_C_CH),
            aux_mv: if sample.is_some() { mv(AUX_CH) } else { 0 },
        });
    }

    fn deliver(
        &mut self,
        event: Event,
        bus: &mut Bus,
        monitor: &mut power::Monitor,
        phone: &mut pd_lib::Pd,
    ) {
        let from_caps = matches!(event, Event::AuxCaps(_));
        // The only events that can close the path read the rails first.
        if matches!(event, Event::PhoneAttached | Event::AuxPsRdy) {
            self.rails(bus, monitor);
        }
        let out = self.proxy.step(timer::millis(), event);
        self.run(out.as_slice(), bus, monitor, phone, from_caps);
    }

    fn run(
        &mut self,
        actions: &[Action],
        bus: &mut Bus,
        monitor: &mut power::Monitor,
        phone: &mut pd_lib::Pd,
        from_caps: bool,
    ) {
        let mut start_phone = false;
        for &action in actions {
            match action {
                Action::Arm(window) => vbus::arm_aux(monitor, bus, window),
                Action::Disarm => monitor.vbus_disarm(bus),
                Action::ClosePath => {
                    let ok = fusb302::pd_start(bus, self.phone_cc).is_ok()
                        && vbus::close_proxy(monitor, Some(&mut *bus)).is_ok();
                    if ok {
                        start_phone = true;
                        self.note("path closed", 5_000);
                    } else {
                        self.note("path refused by the gate", 0);
                        vbus::open_all();
                    }
                }
                Action::OpenAndDischarge => {
                    vbus::discharge();
                    phone.stop();
                    self.phone_up = false;
                    self.phone_cc = 0;
                    let _ = fusb302::source_target(bus, HostCurrent::A1_5);
                    self.note("path open, TARGET A discharged", 0);
                }
                Action::AuxRequest(rdo) => {
                    // The sink engine sends the 5 V Request itself on the capabilities.
                    if !from_caps {
                        let caps = self.aux.caps();
                        let index = px::rdo_position(rdo) as usize;
                        if index >= 1 && index <= caps.len() {
                            self.aux.want_mv(px::pdo_mv(caps[index - 1]));
                        }
                        self.aux.request(rdo);
                        self.note("aux request", px::rdo_position(rdo));
                    }
                }
                Action::AuxHardReset => {
                    self.aux.hard_reset();
                    self.note("aux hard reset", 0);
                }
                Action::PhoneOffer => {
                    phone.set_offer(self.proxy.offered());
                    if phone.running() {
                        phone.reoffer();
                    }
                    self.note("phone offer", self.proxy.offered().len() as u32);
                }
                Action::PhoneAccept => phone.decide(DECIDE_ACCEPT),
                Action::PhoneReject => phone.decide(DECIDE_REJECT),
                Action::PhoneWait => phone.decide(DECIDE_WAIT),
                Action::PhonePsRdy => phone.release_ps_rdy(),
                Action::PhoneHardReset => {
                    if phone.running() {
                        phone.hard_reset();
                    }
                    self.note("phone hard reset", 0);
                }
                Action::Refused(why) => {
                    self.note(
                        match why {
                            px::Refusal::ControlClosed => "refused: CONTROL feeds the rail",
                            px::Refusal::TargetCLive(_) => "refused: TARGET_C already live",
                            px::Refusal::AuxOutOfWindow(_) => "refused: AUX outside 5 V window",
                            px::Refusal::NoFiveVolts => "refused: AUX offers no 5 V PDO",
                        },
                        0,
                    );
                }
            }
        }
        if start_phone {
            phone.set_proxy(true);
            phone.set_offer(self.proxy.offered());
            let _ = phone.start(3_000);
        }
    }

    /// Every main-loop pass while active.
    pub fn tick(&mut self, _uart: &mut Uart, bus: &mut Bus, monitor: &mut power::Monitor, phone: &mut pd_lib::Pd) {
        let now = timer::millis();
        self.aux.poll(bus);
        phone.poll(bus);

        let aux_mv = monitor.latest().map_or(0, |s| s.readings[AUX_CH].bus_mv);
        let resetting = self.proxy.phase == Phase::Resetting;

        // AUX: session start on VBUS, end on its loss or on the engine ending.
        if !self.aux.running() {
            if self.proxy.phase != Phase::Idle && self.proxy.phase != Phase::Discharging {
                self.note("aux session ended", 0);
                self.deliver(Event::AuxDetached, bus, monitor, phone);
                self.aux_retry_at = now.wrapping_add(AUX_RETRY_MS);
            }
            if aux_mv >= AUX_HERE_MV && (now.wrapping_sub(self.aux_retry_at) as i32) >= 0 && self.proxy.phase == Phase::Idle {
                self.aux_bytes = self.aux.start_sink();
                self.deliver(Event::AuxAttached, bus, monitor, phone);
            }
        } else if aux_mv < AUX_GONE_MV && !resetting && monitor.latest().is_some() {
            self.aux.stop();
            self.deliver(Event::AuxDetached, bus, monitor, phone);
        }

        let mut caps = [0u32; px::MAX_PDOS];
        while let Some((code, value)) = self.aux.take_event() {
            match code {
                pd_lib::EV_CAPS => {
                    let list = self.aux.caps();
                    let n = list.len().min(px::MAX_PDOS);
                    caps[..n].copy_from_slice(&list[..n]);
                    self.deliver(Event::AuxCaps(&caps[..n]), bus, monitor, phone);
                }
                pd_lib::EV_ACCEPT => self.deliver(Event::AuxAccept, bus, monitor, phone),
                pd_lib::EV_REJECT => self.deliver(Event::AuxReject, bus, monitor, phone),
                pd_lib::EV_WAIT => self.deliver(Event::AuxWait, bus, monitor, phone),
                pd_lib::EV_PS_RDY => self.deliver(Event::AuxPsRdy, bus, monitor, phone),
                pd_lib::EV_HARD_RESET => self.deliver(Event::AuxHardReset, bus, monitor, phone),
                _ => {
                    let _ = value;
                }
            }
        }
        while let Some((code, value)) = phone.take_event() {
            match code {
                pd_lib::EV_REQUEST => self.deliver(Event::PhoneRequest(value), bus, monitor, phone),
                pd_lib::EV_HARD_RESET => self.deliver(Event::PhoneHardReset, bus, monitor, phone),
                _ => {}
            }
        }

        // The contract AUX holds is what a re-Request must ask for; a reset is over
        // once the proxy is bridged again.
        self.aux.want_mv(self.proxy.aux_mv);
        if self.proxy.phase == Phase::Bridged {
            phone.reset_done();
        }

        // Protection tripped (UV/OV alert opened the switches): same as AUX gone.
        if self.proxy.path_closed() && !vbus::any_closed() {
            self.note("gate opened the path", 0);
            self.aux.stop();
            self.deliver(Event::AuxDetached, bus, monitor, phone);
        }

        self.phone_side(now, bus, monitor, phone);
        self.deliver(Event::Tick, bus, monitor, phone);
    }

    fn phone_side(&mut self, now: u32, bus: &mut Bus, monitor: &mut power::Monitor, phone: &mut pd_lib::Pd) {
        if (now.wrapping_sub(self.next_phone_poll) as i32) < 0 {
            return;
        }
        self.next_phone_poll = now.wrapping_add(PHONE_POLL_MS);
        if !self.phone_up && !phone.running() {
            if let Ok(cc @ 1..=2) = fusb302::source_cc(bus) {
                self.phone_cc = cc;
                self.phone_up = true;
                self.open_since = 0;
                self.deliver(Event::PhoneAttached, bus, monitor, phone);
            }
        } else if self.proxy.path_closed() && phone.running() {
            // Source role: an open measured CC means the sink is gone.
            if fusb302::target_cc_open(bus).unwrap_or(false) {
                if self.open_since == 0 {
                    self.open_since = now | 1;
                } else if now.wrapping_sub(self.open_since) >= DETACH_DEBOUNCE_MS {
                    self.open_since = 0;
                    self.deliver(Event::PhoneDetached, bus, monitor, phone);
                }
            } else {
                self.open_since = 0;
            }
        }
    }

    pub fn status(&self, uart: &mut Uart) {
        let _ = writeln!(
            uart,
            "pd proxy  {}  {}  aux {} mV  path {}  aux engine {} B",
            if self.active { "on" } else { "off" },
            self.proxy.phase_name(),
            self.proxy.aux_mv,
            if self.proxy.path_closed() { "closed" } else { "open" },
            self.aux_bytes
        );
        match self.proxy.phone_contract {
            Some((position, mv, ma)) => {
                let _ = writeln!(uart, "  phone contract  pos{} {} mV {} mA", position, mv, ma);
            }
            None => {
                let _ = writeln!(uart, "  phone contract  none");
            }
        }
        for (n, pdo) in self.proxy.offered().iter().enumerate() {
            let _ = writeln!(uart, "  offer {}  {:08x}  {} mV {} mA", n + 1, pdo, px::pdo_mv(*pdo), px::pdo_ma(*pdo));
        }
        if let Some(why) = self.proxy.refusal {
            let _ = writeln!(uart, "  refused  {:?}", why);
        }
    }

    pub fn dump_log(&self, uart: &mut Uart, phone: &pd_lib::Pd) {
        let notes = (self.logged as usize).min(LOG) as u32;
        for n in self.logged.wrapping_sub(notes)..self.logged {
            let note = self.log[n as usize % LOG];
            let _ = writeln!(uart, "{:06}.{:03} proxy {} {}", note.at / 1000, note.at % 1000, note.text, note.value);
        }
        self.dump_port(uart, "aux", &self.aux);
        self.dump_port(uart, "phone", phone);
    }

    fn dump_port(&self, uart: &mut Uart, name: &str, lib: &pd_lib::Pd) {
        let logged = lib.logged();
        let count = (logged as usize).min(pd_lib::LOG) as u32;
        for n in logged.wrapping_sub(count)..logged {
            let entry = lib.entry(n);
            let _ = writeln!(
                uart,
                "{:06}.{:03} {} {} {}",
                entry.at / 1000,
                entry.at % 1000,
                name,
                entry.dir as char,
                crate::pd_decode::Decoded { frame: &entry.frame, caps: &[] }
            );
        }
    }
}
