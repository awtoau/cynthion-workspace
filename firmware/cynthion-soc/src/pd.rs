//! USB PD on TARGET-C: glue around the `usbpd` source engine (`pd_lib`) (#633).
//!
//! - Armed by `usb target_c power on`; the part's source toggle picks the CC pin,
//!   PD is switched on for that pin, VBUS comes up, then the engine runs.
//! - The engine owns the part while running: `typec` leaves TARGET alone.
//! - Every frame is in the ring (`log`); `live` also prints them as they happen.

use core::fmt::Write;

mod send;
pub use send::Refusal;

use crate::bus::Bus;
use crate::fusb302::{self, HostCurrent};
use crate::pd_decode::{Decoded, Frame};
use crate::pd_lib::{self, Op, Outcome};
use crate::uart::Uart;
use crate::{power, timer, vbus};

/// `log!` only while [`Pd::live`].
macro_rules! say {
    ($pd:expr, $uart:expr, $($arg:tt)*) => {
        if $pd.live {
            crate::log!($uart, $($arg)*);
        }
    };
}

/// Attach poll, ms: the part settles in tens of ms; this is I2C load while armed
/// with nothing plugged in, so no faster than a person can plug.
const ATTACH_POLL_MS: u32 = 50;
/// Unanswered-capabilities bound, ms: nCapsCount 50 x 150 ms = 7.5 s (spec 6.7.3), x1.33.
const NO_SINK_MS: u32 = 10_000;

pub struct Pd {
    lib: pd_lib::Pd,
    pub bridge: crate::pd_bridge::Bridge,
    armed: bool,
    current: HostCurrent,
    /// 1 or 2 once PD is running on that CC pin; 0 while waiting for attach.
    cc: u8,
    next_poll: u32,
    /// Print each frame and state change as it happens. Off by default: a console
    /// nobody is reading backs up and stalls the main loop, and with it the PD
    /// timers (#635).
    pub live: bool,
    printed: u32,
    session_bytes: usize,
    session_started: u32,
}

fn current_ma(current: HostCurrent) -> u16 {
    match current {
        HostCurrent::Default => 900,
        HostCurrent::A1_5 => 1500,
        HostCurrent::A3 => 3000,
    }
}

impl Pd {
    pub const fn new() -> Pd {
        Pd {
            lib: pd_lib::Pd::new(pd_lib::TARGET),
            bridge: crate::pd_bridge::Bridge::new(),
            armed: false,
            current: HostCurrent::Default,
            cc: 0,
            next_poll: 0,
            live: false,
            printed: 0,
            session_bytes: 0,
            session_started: 0,
        }
    }

    /// PD owns the part's registers; `typec` must not sweep them.
    pub fn running(&self) -> bool {
        self.cc != 0 || self.bridge.active
    }

    pub fn bridge_active(&self) -> bool {
        self.bridge.active
    }

    pub fn arm(&mut self, current: HostCurrent) {
        self.lib.stop();
        self.lib.set_proxy(false);
        self.armed = true;
        self.cc = 0;
        self.current = current;
    }

    /// `pd proxy on`: AUX becomes a PD sink (Rd on), TARGET_C a PD source.
    pub fn proxy_on(&mut self, bus: &mut Bus, confirmed: bool) -> Result<(), &'static str> {
        if !confirmed {
            return Err("AUX will present Rd and run PD toward what is on it; the console is on AUX: add 'confirm'");
        }
        if self.bridge.active {
            return Err("already on");
        }
        if self.armed || self.cc != 0 {
            return Err("TARGET_C PD is armed: usb target_c power off first");
        }
        if vbus::any_closed() {
            return Err("a VBUS switch is closed: usb target_c power off / vbus off first");
        }
        self.bridge.on(bus, &mut self.lib)
    }

    pub fn proxy_off(&mut self, bus: &mut Bus, monitor: &mut power::Monitor) {
        if self.bridge.active {
            self.bridge.off(bus, monitor, &mut self.lib);
        }
    }

    pub fn disarm(&mut self) {
        self.lib.stop();
        self.armed = false;
        if self.cc != 0 {
            self.cc = 0;
            crate::irq::resume_type_c(crate::typec::index(fusb302::Port::Target));
        }
    }

    fn print_new(&mut self, uart: &mut Uart) {
        let logged = self.lib.logged();
        if logged.wrapping_sub(self.printed) > pd_lib::LOG as u32 {
            self.printed = logged.wrapping_sub(pd_lib::LOG as u32);
        }
        while self.printed != logged {
            let entry = self.lib.entry(self.printed);
            self.printed = self.printed.wrapping_add(1);
            if self.live {
                crate::log!(
                    uart,
                    "pd target_c {} {}",
                    if entry.dir == b'<' { "rx" } else { "tx" },
                    Decoded { frame: &entry.frame, caps: &[self.lib.caps_pdo()] }
                );
            }
        }
    }

    /// One poll of the engine and the VBUS actions it asks for.
    fn service(&mut self, uart: &mut Uart, bus: &mut Bus, monitor: &mut power::Monitor) {
        self.lib.poll(bus);
        match self.lib.take_vbus() {
            Some(false) => {
                // Drain TARGET A: a sink holds VBUS up for far longer than tSafe0V.
                vbus::discharge();
                monitor.vbus_disarm(bus);
                self.lib.vbus_done();
                say!(self, uart, "pd target_c vbus off");
            }
            Some(true) => {
                match vbus::charge_target_c(monitor, Some(&mut *bus)) {
                    Ok(mv) => say!(self, uart, "pd target_c vbus on {} mV", mv),
                    Err(_) => {
                        say!(self, uart, "pd target_c vbus on refused");
                        self.disarm();
                    }
                }
                self.lib.vbus_done();
            }
            None => {}
        }
        self.print_new(uart);
    }

    /// Attach detection, then the engine. Every main-loop pass.
    pub fn tick(&mut self, uart: &mut Uart, bus: &mut Bus, monitor: &mut power::Monitor) {
        if self.bridge.active {
            self.bridge.tick(uart, bus, monitor, &mut self.lib);
            return;
        }
        if !self.armed {
            return;
        }
        if self.cc == 0 {
            let now = timer::millis();
            if (now.wrapping_sub(self.next_poll) as i32) < 0 {
                return;
            }
            self.next_poll = now.wrapping_add(ATTACH_POLL_MS);
            if let Ok(cc @ 1..=2) = fusb302::source_cc(bus) {
                self.begin(uart, bus, monitor, cc);
            }
            return;
        }
        self.service(uart, bus, monitor);
        // nCapsCount 50 x tTypeCSendSourceCap 150 ms = 7.5 s of unanswered capabilities
        // puts the engine in Disabled for good; x1.33 and out: start over from attach.
        if self.lib.contract().is_some() {
            self.session_started = timer::millis();
        }
        if self.lib.running()
            && self.lib.contract().is_none()
            && timer::millis().wrapping_sub(self.session_started) > NO_SINK_MS
        {
            self.lib.stop();
            self.lib.ended = "no sink answered the capabilities";
        }
        if !self.lib.running() {
            say!(self, uart, "pd target_c session ended: {}", self.lib.ended);
            // Back to attach detection: the part toggles again, VBUS off.
            vbus::open_all();
            monitor.vbus_disarm(bus);
            self.cc = 0;
            crate::irq::resume_type_c(crate::typec::index(fusb302::Port::Target));
            let _ = fusb302::source_target(bus, self.current);
        }
    }

    fn begin(&mut self, uart: &mut Uart, bus: &mut Bus, monitor: &mut power::Monitor, cc: u8) {
        if let Err(error) = fusb302::pd_start(bus, cc) {
            say!(self, uart, "pd target_c start: {}", error.as_str());
            return;
        }
        match vbus::charge_target_c(monitor, Some(&mut *bus)) {
            Ok(mv) => say!(self, uart, "pd target_c cc{} vbus on {} mV", cc, mv),
            Err(_) => {
                say!(self, uart, "pd target_c vbus on refused");
                self.disarm();
                return;
            }
        }
        // VBUSOK must be seen before the engine starts: the PHY waits for it.
        self.session_bytes = self.lib.start(current_ma(self.current));
        self.cc = cc;
        self.session_started = timer::millis();
    }

    /// Run one raw exchange to completion (blocking the main loop for at most
    /// `limit_ms`, which the caller derives from the exchange's own timers).
    #[allow(clippy::too_many_arguments)]
    pub fn raw(
        &mut self,
        uart: &mut Uart,
        bus: &mut Bus,
        monitor: &mut power::Monitor,
        op: Op,
        sop: u8,
        frame: Frame,
        timeout_ms: u32,
        timer_name: &'static str,
        no_reply: bool,
        want: usize,
        limit_ms: u32,
    ) -> Option<&'static pd_lib::Raw> {
        if !self.lib.running() {
            return None;
        }
        if !self.lib.post(op, sop, frame, timeout_ms, timer_name, no_reply, want) {
            return None;
        }
        let start = timer::millis();
        loop {
            self.service(uart, bus, monitor);
            if let Some(result) = self.lib.take_result() {
                return Some(result);
            }
            if timer::millis().wrapping_sub(start) > limit_ms {
                crate::log!(uart, "pd target_c raw exchange overran its {} ms limit", limit_ms);
                return None;
            }
        }
    }

    pub fn status(&self, uart: &mut Uart) {
        let (sent, received, errors, resets) = self.lib.counters();
        let _ = writeln!(
            uart,
            "pd target_c  {}  cc{}  session {}  ready {}  tx {} rx {} senderr {} hardreset {}",
            if self.armed { "armed" } else { "off" },
            self.cc,
            if self.lib.running() { "running" } else { "stopped" },
            self.lib.ready() as u8,
            sent,
            received,
            errors,
            resets
        );
        match self.lib.contract() {
            Some((position, ma)) => {
                let _ = writeln!(uart, "  contract  pos{} {} mA at 5000 mV", position, ma);
            }
            None => {
                let _ = writeln!(uart, "  contract  none");
            }
        }
        if !self.lib.ended.is_empty() {
            let _ = writeln!(uart, "  last session ended: {}", self.lib.ended);
        }
        let _ = writeln!(uart, "  engine future {} B", self.session_bytes);
    }

    pub fn dump_proxy_log(&self, uart: &mut Uart) {
        self.bridge.dump_log(uart, &self.lib);
    }

    pub fn caps(&self, uart: &mut Uart) {
        let _ = writeln!(uart, "  pdo1 {:08x}", self.lib.caps_pdo());
    }

    pub fn dump_log(&self, uart: &mut Uart) {
        let logged = self.lib.logged();
        let count = (logged as usize).min(pd_lib::LOG) as u32;
        for n in logged.wrapping_sub(count)..logged {
            let entry = self.lib.entry(n);
            let _ = writeln!(
                uart,
                "{:06}.{:03} {} {}",
                entry.at / 1000,
                entry.at % 1000,
                entry.dir as char,
                Decoded { frame: &entry.frame, caps: &[self.lib.caps_pdo()] }
            );
        }
    }
}

/// How a raw exchange ended, for the shell and the exerciser to parse.
pub fn outcome_text(outcome: Outcome) -> (&'static str, &'static str) {
    match outcome {
        Outcome::Reply => ("reply", ""),
        Outcome::NoReply(timer) => ("no-reply", timer),
        Outcome::TxFailed(why) => ("tx-failed", why),
        Outcome::HardResetRx => ("hard-reset-received", ""),
        Outcome::NotReady => ("not-ready", "engine not idle in Ready"),
        Outcome::Sent => ("sent", "GoodCRC; no reply expected"),
    }
}

