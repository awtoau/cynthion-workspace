//! The board's [`Driver`]: each op calls the function the shell calls.
//!
//! No text is produced here and no unit is converted here: values come from the
//! driver functions as mV, uA, ms or enums (#270).

use super::dispatch::*;
use super::proto::*;
use crate::fusb302::{HostCurrent, Orientation, Port, State};
use crate::gpio::{self, Led};
use crate::power::{self, Age, Limit};
use crate::vbus::{self, PowerError, Refusal, Source};
use crate::vbus_gate::PowerRefusal;
use crate::{portctl, target, Devices};

/// Wire LED index to LED. The wire order is fixed here, not by `Led::ALL`.
const WIRE_LEDS: [Led; LEDS] =
    [Led::Red, Led::Orange, Led::Yellow, Led::Green, Led::Blue, Led::Violet];

const NO_BOARD: Fail = Fail::unavailable(1);
const NO_SAMPLE: Fail = Fail::unavailable(2);
const READ_FAILED: Fail = Fail::unavailable(3);

pub struct Board<'a> {
    pub devices: &'a mut Devices,
    pub console: usize,
}

fn orientation(o: Orientation) -> u8 {
    match o {
        Orientation::Unknown => 0,
        Orientation::Unattached => 1,
        Orientation::Cc1 => 2,
        Orientation::Cc2 => 3,
        Orientation::Both => 4,
    }
}

fn cc_port(state: State, serviced: u32) -> CcPort {
    CcPort {
        ok: true,
        device_id: state.device_id,
        vbus: state.vbus,
        cc1: state.cc1.unwrap_or(CC_UNMEASURED),
        cc2: state.cc2.unwrap_or(CC_UNMEASURED),
        source_cc: state.source_cc,
        orientation: orientation(state.orientation()),
        serviced,
    }
}

fn refusal(r: Refusal) -> Fail {
    match r {
        Refusal::TooHigh(mv) => Fail::refused(1, mv),
        Refusal::TooLow(mv) => Fail::refused(2, mv),
        Refusal::Uv(mv) => Fail::refused(3, mv),
        Refusal::Ov(mv) => Fail::refused(4, mv),
        Refusal::Unreadable => Fail::refused(5, 0),
        Refusal::NotSampling => Fail::refused(6, 0),
    }
}

fn power_error(e: PowerError) -> Fail {
    match e {
        PowerError::Gate(r) => refusal(r),
        PowerError::Port(PowerRefusal::NotOutput) => Fail::refused(7, 0),
        PowerError::Port(PowerRefusal::Other(_)) => Fail::refused(8, 0),
        PowerError::Cc => Fail::refused(9, 0),
    }
}

fn source_code(s: Source) -> u8 {
    match s {
        Source::Control => 0,
        Source::Aux => 1,
        Source::TargetC => 2,
    }
}

impl Board<'_> {
    fn pins(&self) -> Result<gpio::Gpio, Fail> {
        target::BOARD.map(|board| gpio::Gpio::new(board.gpio)).ok_or(NO_BOARD)
    }

    fn leds(&self) -> Result<Leds, Fail> {
        let pins = self.pins()?;
        let bit = |f: &dyn Fn(Led) -> bool| bits(LEDS, |n| f(WIRE_LEDS[n]));
        Ok(Leds {
            lit: bit(&|led| pins.led_lit(led)),
            fabric: bit(&|led| pins.led_owner(led) == gpio::Owner::Fabric),
            button: pins.button(),
        })
    }
}

impl Driver for Board<'_> {
    fn features(&self) -> u32 {
        (if target::BOARD.is_some() { FEAT_BOARD } else { 0 })
            | (if cfg!(feature = "qemu") { 0 } else { FEAT_HYPERRAM })
    }

    fn info(&mut self) -> Info {
        Info {
            fw_git: crate::info::build::GIT_WORD,
            time_hz: target::TIME_HZ,
            uptime_ms: crate::timer::millis(),
            console: self.console as u8,
            consoles: target::UART_BASES.len() as u8,
        }
    }

    fn power_read(&mut self) -> Result<PowerRead, Fail> {
        if target::BOARD.is_none() {
            return Err(NO_BOARD);
        }
        let d = &mut *self.devices;
        let sample = d.power.current(d.bus.as_mut()).ok_or(
            if power::interval_ms() == power::RATE_OFF { READ_FAILED } else { NO_SAMPLE },
        )?;
        let age_ms = match d.power.age() {
            Age::Millis(ms) => ms,
            Age::Older | Age::Never => u32::MAX,
        };
        let mut channels = [Channel::default(); CHANNELS];
        for (n, channel) in channels.iter_mut().enumerate() {
            let reading = sample.readings[n];
            *channel = Channel {
                mv: reading.bus_mv,
                ua: reading.current_ua,
                floor_ua: d.power.floor(n),
                drawing: d.power.drawing(n, &reading),
            };
        }
        Ok(PowerRead { age_ms, channels })
    }

    fn power_limits(&mut self, channel: u8) -> Result<Limits, Fail> {
        let (p, ch) = (&self.devices.power, channel as usize);
        let enabled = p.alert_enable_cached();
        let armed = |limit: Limit| enabled & limit.bit(ch) != 0;
        let fired = p.alert_history();
        let hit = |limit: Limit| fired & limit.bit(ch) != 0;
        let order = [Limit::OverVoltage, Limit::UnderVoltage, Limit::OverCurrent, Limit::UnderCurrent];
        Ok(Limits {
            ov_mv: power::limit_code_to_mv(p.alert_limit_cached(Limit::OverVoltage, ch)),
            uv_mv: power::limit_code_to_mv(p.alert_limit_cached(Limit::UnderVoltage, ch)),
            oc_ua: power::current_ua(p.alert_limit_cached(Limit::OverCurrent, ch)),
            uc_ua: power::current_ua(p.alert_limit_cached(Limit::UnderCurrent, ch)),
            armed: bits(4, |n| armed(order[n])),
            fired: bits(4, |n| hit(order[n])),
        })
    }

    fn vbus_status(&mut self) -> VbusStatus {
        VbusStatus {
            raw: vbus::state(),
            closed: bits(3, |n| {
                vbus::is_closed([Source::Control, Source::Aux, Source::TargetC][n])
            }),
            powered: bits(CHANNELS, vbus::power_state),
        }
    }

    fn vbus_set(&mut self, port: u8, action: VbusAction) -> Result<VbusDone, Fail> {
        let port = port as usize;
        let d = &mut *self.devices;
        let none = VbusDone { source: 0xFF, mv: 0, flags: 0 };
        match action {
            VbusAction::OpenAll => {
                portctl::open_all(d);
                Ok(none)
            }
            VbusAction::Off => portctl::power_off(d, port).map(|()| none).map_err(power_error),
            _ if d.bus.is_none() => Err(NO_BOARD),
            VbusAction::On { current_ma } => {
                let current = match current_ma {
                    1500 => HostCurrent::A1_5,
                    3000 => HostCurrent::A3,
                    _ => HostCurrent::Default,
                };
                let on = portctl::power_on(d, port, current).map_err(power_error)?;
                Ok(VbusDone {
                    source: source_code(on.source),
                    mv: on.millivolts.unwrap_or(0),
                    flags: on.fell_back as u8 | ((on.millivolts.is_none() as u8) << 1),
                })
            }
            VbusAction::Source => {
                let source = portctl::source(port).ok_or(Fail::refused(7, 0))?;
                let mv = vbus::close(source, &mut d.power, d.bus.as_mut()).map_err(refusal)?;
                Ok(VbusDone { source: source_code(source), mv, flags: 0 })
            }
        }
    }

    fn led_get(&mut self) -> Result<Leds, Fail> {
        self.leds()
    }

    fn led_set(&mut self, led: u8, state: u8) -> Result<Leds, Fail> {
        let pins = self.pins()?;
        let led = WIRE_LEDS[led as usize];
        match state {
            0 => pins.set_led(led, false),
            1 => pins.set_led(led, true),
            _ => pins.release_led(led),
        }
        self.leds()
    }

    fn typec_status(&mut self) -> Result<TypeC, Fail> {
        let d = &mut *self.devices;
        let bus = d.bus.as_mut().ok_or(NO_BOARD)?;
        let mut ports = [CcPort { cc1: CC_UNMEASURED, cc2: CC_UNMEASURED, ..CcPort::default() }; 2];
        for (slot, port) in ports.iter_mut().zip(Port::ALL) {
            if let Ok(state) = d.type_c.live(bus, port) {
                *slot = cc_port(state, d.type_c.serviced[crate::typec::index(port)]);
            }
        }
        Ok(TypeC { configured: d.type_c.configured, lines: bus.lines(), ports })
    }

    fn pd_status(&mut self) -> PdStatus {
        let s = self.devices.type_c.pd.snapshot();
        let (contract_pos, contract_ma) = s.contract.unwrap_or((0, 0));
        PdStatus {
            armed: s.armed,
            cc: s.cc,
            running: s.running,
            ready: s.ready,
            sent: s.sent,
            received: s.received,
            errors: s.errors,
            hardresets: s.hardresets,
            contract_pos,
            contract_ma,
            session_bytes: s.session_bytes,
        }
    }

    fn usb_port(&mut self, port: u8) -> Result<UsbPort, Fail> {
        let port = port as usize;
        let d = &mut *self.devices;
        let sample = d.power.current(d.bus.as_mut());
        let reading = sample.map(|s| s.readings[port]);
        let switch = match portctl::source(port) {
            None => 0,
            Some(source) if vbus::is_closed(source) => 2,
            Some(_) => 1,
        };
        let cable = portctl::controller(port).and_then(|p| d.type_c.cached(p)).map(|(state, _)| {
            cc_port(state, 0)
        });
        Ok(UsbPort {
            sample: reading.is_some(),
            mv: reading.map_or(0, |r| r.bus_mv),
            ua: reading.map_or(0, |r| r.current_ua),
            drawing: reading.is_some_and(|r| d.power.drawing(port, &r)),
            switch,
            cable,
        })
    }

    #[cfg(not(feature = "qemu"))]
    fn hr_status(&mut self) -> Result<HrStatus, Fail> {
        let s = crate::hyperram::backend::snapshot();
        Ok(HrStatus {
            id: s.id,
            ck_khz_req: s.ck_khz_req,
            ck_khz_meas: s.ck_khz_meas,
            ready: s.ready,
            locked: s.locked,
            calibrated: s.calibrated,
            busy: s.busy,
            hr_busy: s.hr_busy,
            last_op: s.last_op,
            last_code: s.last_code,
            errors: s.errors,
            state: s.state,
            cal_tap: s.cal_tap,
            cal_width: s.cal_width,
            cal_valid: s.cal_valid,
        })
    }

    #[cfg(feature = "qemu")]
    fn hr_status(&mut self) -> Result<HrStatus, Fail> {
        Err(NO_BOARD)
    }

    fn selftest(&mut self) -> Selftest {
        let mut uart = crate::primary_for(self.console);
        let r = crate::selftest::run(&mut uart, &self.devices.power, false);
        Selftest {
            passed: r.passed as u8,
            failed: r.failed as u8,
            skipped: r.skipped as u8,
            n: r.n.min(SELFTEST_MAX_ITEMS) as u8,
            items: r.items,
        }
    }
}
