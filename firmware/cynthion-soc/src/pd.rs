//! USB PD on TARGET-C: the FUSB302B glue around `pd_src::Engine` (#633).
//!
//! - Armed by `usb target_c power on`; the part's source toggle picks the CC pin,
//!   then PD is switched on for that pin and the engine runs.
//! - Interrupts come through `typec::Controllers::service`, which hands the
//!   decoded cause here; everything else is the polled [`Pd::tick`].
//! - Every message is logged with the board's millisecond stamp and decoded.

use core::fmt::Write;

use crate::bus::Bus;
use crate::fusb302::{self, HostCurrent, Interrupts};
use crate::pd_msg::{Decoded, Message};
use crate::pd_src::{Engine, Event, Out, State};
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

const LOG: usize = 16;
/// Attach poll, ms: the part settles in tens of ms; this is I2C load while armed
/// with nothing plugged in, so no faster than a person can plug.
const ATTACH_POLL_MS: u32 = 50;
/// VBUS poll while waiting for the rail, ms. The rail rises in under 275 ms
/// (tSrcTurnOn, Table 7-12); 10 ms resolves it against tFirstSourceCap 250 ms.
const VBUS_POLL_MS: u32 = 10;

#[derive(Clone, Copy)]
struct Entry {
    at: u32,
    /// `b'<'` received, `b'>'` sent.
    dir: u8,
    message: Message,
}

pub struct Pd {
    engine: Engine,
    armed: bool,
    /// 1 or 2 once PD is running on that CC pin; 0 while waiting for attach.
    cc: u8,
    next_poll: u32,
    pending_vbus: Option<bool>,
    /// Print each message and state change as it happens. Off by default: a
    /// console nobody is reading backs up and stalls the main loop, and with it
    /// the PD timers (measured: 150 ms resends became 5 s). The ring has them.
    pub live: bool,
    log: [Entry; LOG],
    logged: u32,
    pub sent: u32,
    pub received: u32,
    pub send_errors: u32,
}

fn current_ma(current: HostCurrent) -> u32 {
    match current {
        HostCurrent::Default => 900,
        HostCurrent::A1_5 => 1500,
        HostCurrent::A3 => 3000,
    }
}

impl Pd {
    pub const fn new() -> Pd {
        Pd {
            engine: Engine::new(),
            armed: false,
            cc: 0,
            next_poll: 0,
            pending_vbus: None,
            live: false,
            log: [Entry { at: 0, dir: 0, message: Message::EMPTY }; LOG],
            logged: 0,
            sent: 0,
            received: 0,
            send_errors: 0,
        }
    }

    /// PD owns the part's registers; `typec` must not sweep them.
    pub fn running(&self) -> bool {
        self.cc != 0
    }

    pub fn arm(&mut self, current: HostCurrent) {
        self.armed = true;
        self.cc = 0;
        self.engine.set_current(current_ma(current));
        self.engine.event(timer::millis(), Event::Disarm);
    }

    pub fn set_probe(&mut self, level: u8) {
        self.engine.set_probe(level);
    }

    pub fn disarm(&mut self) {
        self.armed = false;
        self.cc = 0;
        self.engine.event(timer::millis(), Event::Disarm);
    }

    fn record(&mut self, dir: u8, message: &Message, uart: &mut Uart) {
        let now = timer::millis();
        if message.is_control() && message.kind() == crate::pd_msg::GOOD_CRC {
            return;
        }
        self.log[self.logged as usize % LOG] = Entry { at: now, dir, message: *message };
        self.logged = self.logged.wrapping_add(1);
        if !self.live {
            return;
        }
        say!(
            self, uart,
            "pd target_c {} {}",
            if dir == b'<' { "rx" } else { "tx" },
            Decoded { message, caps: self.engine.all_caps() }
        );
    }

    /// Feed one event and carry out what the engine asks for.
    fn feed(&mut self, uart: &mut Uart, bus: &mut Bus, event: Event) {
        let before = self.engine.state;
        let out = self.engine.event(timer::millis(), event);
        self.apply(uart, bus, out);
        if self.engine.state != before {
            say!(self, uart, "pd target_c {} -> {}", before.name(), self.engine.state.name());
        }
    }

    fn apply(&mut self, uart: &mut Uart, bus: &mut Bus, out: Out) {
        if let Some(message) = out.send {
            self.record(b'>', &message, uart);
            match fusb302::pd_send(bus, &message) {
                Ok(()) => self.sent += 1,
                Err(error) => {
                    self.send_errors += 1;
                    say!(self, uart, "pd target_c send failed: {}", error.as_str());
                    let retry = self.engine.event(timer::millis(), Event::TxFailed);
                    // One level only: a failed send of the escalation is left to
                    // the engine's guard timer.
                    if let Some(message) = retry.send {
                        let _ = fusb302::pd_send(bus, &message);
                    }
                    if retry.hard_reset {
                        let _ = fusb302::pd_hard_reset(bus);
                    }
                }
            }
        }
        if out.hard_reset {
            say!(self, uart, "pd target_c hard reset signalling");
            let _ = fusb302::pd_hard_reset(bus);
        }
        if out.vbus.is_some() {
            self.pending_vbus = out.vbus;
        }
    }

    /// A TARGET interrupt, already read and cleared by `typec::service`.
    pub fn interrupt(&mut self, uart: &mut Uart, bus: &mut Bus, cause: Interrupts) {
        if !self.running() {
            return;
        }
        if cause.tx_sent() {
            self.feed(uart, bus, Event::TxSent);
        }
        if cause.retry_failed() {
            say!(self, uart, "pd target_c no GoodCRC after retries");
            self.feed(uart, bus, Event::TxFailed);
        }
        if cause.hard_reset_sent() {
            self.feed(uart, bus, Event::HardResetSent);
        }
        if cause.hard_reset_received() {
            say!(self, uart, "pd target_c hard reset received");
            let _ = fusb302::pd_reset(bus);
            self.feed(uart, bus, Event::HardResetRx);
        }
        // Drain whatever arrived, whichever bit announced it.
        for _ in 0..8 {
            match fusb302::pd_receive(bus) {
                Ok(Some(message)) => {
                    self.received += 1;
                    self.record(b'<', &message, uart);
                    self.feed(uart, bus, Event::Rx(message));
                }
                Ok(None) => break,
                Err(error) => {
                    say!(self, uart, "pd target_c rx failed: {}", error.as_str());
                    break;
                }
            }
        }
    }

    /// Timers, attach detection, and VBUS switching. Every main-loop pass.
    pub fn tick(&mut self, uart: &mut Uart, bus: &mut Bus, monitor: &mut power::Monitor) {
        if !self.armed {
            return;
        }
        let now = timer::millis();
        if self.cc == 0 {
            if (now.wrapping_sub(self.next_poll) as i32) < 0 {
                return;
            }
            self.next_poll = now.wrapping_add(ATTACH_POLL_MS);
            if let Ok(cc @ 1..=2) = fusb302::source_cc(bus) {
                match fusb302::pd_start(bus, cc) {
                    Ok(()) => {
                        self.cc = cc;
                        say!(self, uart, "pd target_c sink on cc{}", cc);
                        self.feed(uart, bus, Event::Arm);
                    }
                    Err(error) => say!(self, uart, "pd target_c start: {}", error.as_str()),
                }
            }
            return;
        }
        if self.engine.state == State::WaitVbus
            && (now.wrapping_sub(self.next_poll) as i32) >= 0
        {
            self.next_poll = now.wrapping_add(VBUS_POLL_MS);
            if let Ok(true) = fusb302::target_vbus(bus) {
                self.feed(uart, bus, Event::VbusOk);
            }
        }
        self.feed_tick(uart, bus);
        match self.pending_vbus.take() {
            Some(false) => {
                vbus::open_all();
                monitor.vbus_disarm(bus);
                say!(self, uart, "pd target_c vbus off");
            }
            Some(true) => match vbus::charge_target_c(monitor, Some(&mut *bus)) {
                Ok(mv) => say!(self, uart, "pd target_c vbus on {} mV", mv),
                Err(_) => {
                    say!(self, uart, "pd target_c vbus on refused");
                    self.disarm();
                }
            },
            None => {}
        }
    }

    fn feed_tick(&mut self, uart: &mut Uart, bus: &mut Bus) {
        // Quiet unless something happened: `feed` logs state changes only.
        let before = self.engine.state;
        let out = self.engine.event(timer::millis(), Event::Tick);
        self.apply(uart, bus, out);
        if self.engine.state != before {
            say!(self, uart, "pd target_c {} -> {}", before.name(), self.engine.state.name());
        }
    }

    pub fn status(&self, uart: &mut Uart) {
        let _ = writeln!(
            uart,
            "pd target_c  {}  cc{}  state {}  tx {} rx {} senderr {}",
            if self.armed { "armed" } else { "off" },
            self.cc,
            self.engine.state.name(),
            self.sent,
            self.received,
            self.send_errors
        );
        match self.engine.contract {
            Some(rdo) => {
                let (op, max) = rdo.fixed_ma();
                let _ = writeln!(
                    uart,
                    "  contract  pos{} op {} mA max {} mA  mismatch {}",
                    rdo.position(),
                    op,
                    max,
                    rdo.mismatch() as u8
                );
            }
            None => {
                let _ = writeln!(uart, "  contract  none");
            }
        }
    }

    pub fn caps(&self, uart: &mut Uart) {
        for (index, &object) in self.engine.caps().iter().enumerate() {
            let _ = writeln!(uart, "  pdo{} {:08x}", index + 1, object);
        }
    }

    pub fn dump_log(&self, uart: &mut Uart) {
        let count = (self.logged as usize).min(LOG);
        let start = self.logged as usize - count;
        for n in start..self.logged as usize {
            let entry = &self.log[n % LOG];
            let _ = writeln!(
                uart,
                "{:06}.{:03} {} {}",
                entry.at / 1000,
                entry.at % 1000,
                entry.dir as char,
                Decoded { message: &entry.message, caps: self.engine.all_caps() }
            );
        }
    }
}
