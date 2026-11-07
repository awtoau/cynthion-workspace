//! The op dispatcher: argument decode, range checks, result encode. Pure.
//!
//! [`Driver`] is the typed driver layer: one method per op, taking validated
//! numbers and returning numbers and enums (units are the driver's, #270). The
//! board's implementation calls the same functions the shell calls.

use super::proto::*;

/// Why an op did not produce a result.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Fail {
    pub status: u8,
    pub reason: u8,
    pub value: u32,
}

impl Fail {
    pub const fn refused(reason: u8, value: u32) -> Self {
        Fail { status: ST_REFUSED, reason, value }
    }
    pub const fn unavailable(reason: u8) -> Self {
        Fail { status: ST_UNAVAILABLE, reason, value: 0 }
    }
    pub const fn bad_args() -> Self {
        Fail { status: ST_BAD_ARGS, reason: 0, value: 0 }
    }
}

pub struct Info {
    pub fw_git: u32,
    pub time_hz: u32,
    pub uptime_ms: u32,
    pub console: u8,
    pub consoles: u8,
}

#[derive(Clone, Copy, Default)]
pub struct Channel {
    pub mv: u32,
    pub ua: i32,
    pub floor_ua: u32,
    pub drawing: bool,
}

pub struct PowerRead {
    /// `u32::MAX` when older than the driver's age limit.
    pub age_ms: u32,
    pub channels: [Channel; CHANNELS],
}

pub struct Limits {
    pub ov_mv: i32,
    pub uv_mv: i32,
    pub oc_ua: i32,
    pub uc_ua: i32,
    /// Bit 0 ov, 1 uv, 2 oc, 3 uc.
    pub armed: u8,
    pub fired: u8,
}

pub struct VbusStatus {
    pub raw: u8,
    /// Bit 0 control, 1 aux, 2 target_c.
    pub closed: u8,
    /// Bit n = channel n driven.
    pub powered: u8,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum VbusAction {
    Off,
    On { current_ma: u16 },
    Source,
    OpenAll,
}

pub struct VbusDone {
    /// 0 control, 1 aux, 2 target_c.
    pub source: u8,
    pub mv: u32,
    /// Bit 0 fell back to aux, bit 1 already on.
    pub flags: u8,
}

pub struct Leds {
    pub lit: u8,
    pub fabric: u8,
    pub button: bool,
}

#[derive(Clone, Copy, Default)]
pub struct CcPort {
    pub ok: bool,
    pub device_id: u8,
    pub vbus: bool,
    /// `CC_UNMEASURED` when not measured.
    pub cc1: u8,
    pub cc2: u8,
    pub source_cc: u8,
    /// 0 unknown, 1 unattached, 2 cc1, 3 cc2, 4 both.
    pub orientation: u8,
    pub serviced: u32,
}

pub const CC_UNMEASURED: u8 = 0xFF;

pub struct TypeC {
    pub configured: bool,
    pub lines: u8,
    pub ports: [CcPort; 2],
}

pub struct PdStatus {
    pub armed: bool,
    pub cc: u8,
    pub running: bool,
    pub ready: bool,
    pub sent: u32,
    pub received: u32,
    pub errors: u32,
    pub hardresets: u32,
    pub contract_pos: u8,
    pub contract_ma: u16,
    pub session_bytes: u32,
}

pub struct UsbPort {
    pub sample: bool,
    pub mv: u32,
    pub ua: i32,
    pub drawing: bool,
    /// 0 none, 1 open, 2 closed.
    pub switch: u8,
    /// The cached Type-C state, when the port has a controller and it answered.
    pub cable: Option<CcPort>,
}

pub struct HrStatus {
    pub id: u32,
    pub ck_khz_req: u32,
    pub ck_khz_meas: u32,
    pub ready: bool,
    pub locked: bool,
    pub calibrated: bool,
    pub busy: bool,
    pub hr_busy: bool,
    pub last_op: u8,
    pub last_code: u8,
    pub errors: u16,
    pub state: u16,
    pub cal_tap: u8,
    pub cal_width: u8,
    pub cal_valid: bool,
}

pub struct Selftest {
    pub passed: u8,
    pub failed: u8,
    pub skipped: u8,
    pub n: u8,
    /// (item index, outcome 0 pass / 1 fail / 2 skip)
    pub items: [(u8, u8); SELFTEST_MAX_ITEMS],
}

/// One method per op. Arguments are range-checked by the dispatcher first.
pub trait Driver {
    fn features(&self) -> u32;
    fn info(&mut self) -> Info;
    fn power_read(&mut self) -> Result<PowerRead, Fail>;
    fn power_limits(&mut self, channel: u8) -> Result<Limits, Fail>;
    fn vbus_status(&mut self) -> VbusStatus;
    fn vbus_set(&mut self, port: u8, action: VbusAction) -> Result<VbusDone, Fail>;
    fn led_get(&mut self) -> Result<Leds, Fail>;
    fn led_set(&mut self, led: u8, state: u8) -> Result<Leds, Fail>;
    fn typec_status(&mut self) -> Result<TypeC, Fail>;
    fn pd_status(&mut self) -> PdStatus;
    fn usb_port(&mut self, port: u8) -> Result<UsbPort, Fail>;
    fn hr_status(&mut self) -> Result<HrStatus, Fail>;
    fn selftest(&mut self) -> Selftest;
}

struct Reader<'a> {
    b: &'a [u8],
    n: usize,
}

impl Reader<'_> {
    fn u8(&mut self) -> u8 {
        let v = self.b.get(self.n).copied().unwrap_or(0);
        self.n += 1;
        v
    }
    fn u16(&mut self) -> u16 {
        u16::from_le_bytes([self.u8(), self.u8()])
    }
    fn u32(&mut self) -> u32 {
        u32::from_le_bytes([self.u8(), self.u8(), self.u8(), self.u8()])
    }
    /// All of the arguments, and no more.
    fn exact(&self, want: usize) -> bool {
        self.b.len() == want
    }
}

struct Writer<'a> {
    b: &'a mut [u8],
    n: usize,
}

impl Writer<'_> {
    fn u8(&mut self, v: u8) {
        if let Some(slot) = self.b.get_mut(self.n) {
            *slot = v;
        }
        self.n += 1;
    }
    fn u16(&mut self, v: u16) {
        v.to_le_bytes().iter().for_each(|&b| self.u8(b));
    }
    fn u32(&mut self, v: u32) {
        v.to_le_bytes().iter().for_each(|&b| self.u8(b));
    }
    fn i32(&mut self, v: i32) {
        self.u32(v as u32);
    }
}

/// Run one request. `req` is the REQ body (opcode and arguments); the response
/// body (`op status` and result or failure tail) is written to `out`, whose length
/// is the board's response limit. Returns the response length.
pub fn dispatch(d: &mut dyn Driver, req: &[u8], out: &mut [u8]) -> usize {
    let op = u16::from_le_bytes([req[0], req[1]]);
    let mut r = Reader { b: &req[2..], n: 0 };
    let mut w = Writer { b: out, n: 0 };
    w.u16(op);
    w.u8(ST_OK);

    let outcome = run(d, op, &mut r, &mut w);
    if let Err(fail) = outcome {
        w.n = 2;
        w.u8(fail.status);
        w.u8(fail.reason);
        w.u32(fail.value);
    } else if w.n > w.b.len() {
        w.n = 2;
        w.u8(ST_IO);
        w.u8(0xFF);
        w.u32(0);
    }
    w.n
}

fn run(d: &mut dyn Driver, op: u16, r: &mut Reader, w: &mut Writer) -> Result<(), Fail> {
    let bad = Fail::bad_args();
    match op {
        OP_SYS_PING => {
            if !r.exact(4) {
                return Err(bad);
            }
            w.u32(r.u32());
        }
        OP_SYS_CAPABILITIES => {
            if !r.exact(2) {
                return Err(bad);
            }
            let first = (r.u16() as usize).min(OPS.len());
            let n = (OPS.len() - first).min(CAPS_PAGE);
            w.u8(VERSION);
            w.u16(MAX_REQ_BODY as u16);
            w.u16(MAX_RSP_BODY as u16);
            w.u32(d.features());
            w.u16(OPS.len() as u16);
            w.u8(n as u8);
            for &code in &OPS[first..first + n] {
                w.u16(code);
            }
        }
        OP_SYS_INFO => {
            if !r.exact(0) {
                return Err(bad);
            }
            let i = d.info();
            w.u32(i.fw_git);
            w.u32(i.time_hz);
            w.u32(i.uptime_ms);
            w.u8(i.console);
            w.u8(i.consoles);
        }
        OP_POWER_READ => {
            if !r.exact(0) {
                return Err(bad);
            }
            let p = d.power_read()?;
            w.u32(p.age_ms);
            w.u8(CHANNELS as u8);
            for c in &p.channels {
                w.u32(c.mv);
                w.i32(c.ua);
                w.u32(c.floor_ua);
                w.u8(c.drawing as u8);
            }
        }
        OP_POWER_LIMITS => {
            if !r.exact(1) {
                return Err(bad);
            }
            let channel = r.u8();
            if channel as usize >= CHANNELS {
                return Err(bad);
            }
            let l = d.power_limits(channel)?;
            w.i32(l.ov_mv);
            w.i32(l.uv_mv);
            w.i32(l.oc_ua);
            w.i32(l.uc_ua);
            w.u8(l.armed);
            w.u8(l.fired);
        }
        OP_VBUS_STATUS => {
            if !r.exact(0) {
                return Err(bad);
            }
            let v = d.vbus_status();
            w.u8(v.raw);
            w.u8(v.closed);
            w.u8(v.powered);
        }
        OP_VBUS_SET => {
            if !r.exact(4) {
                return Err(bad);
            }
            let (port, action, current_ma) = (r.u8(), r.u8(), r.u16());
            let action = match action {
                0 => VbusAction::Off,
                1 => VbusAction::On { current_ma },
                2 => VbusAction::Source,
                3 => VbusAction::OpenAll,
                _ => return Err(bad),
            };
            // Rp current exists on target_c (channel 1) only, and only when powering on.
            let rp_ok = matches!(current_ma, 0 | 1500 | 3000)
                && (current_ma == 0 || (port == 1 && matches!(action, VbusAction::On { .. })));
            if port as usize >= CHANNELS || !rp_ok {
                return Err(bad);
            }
            let v = d.vbus_set(port, action)?;
            w.u8(v.source);
            w.u32(v.mv);
            w.u8(v.flags);
        }
        OP_LED_GET => {
            if !r.exact(0) {
                return Err(bad);
            }
            leds(w, d.led_get()?, true);
        }
        OP_LED_SET => {
            if !r.exact(2) {
                return Err(bad);
            }
            let (led, state) = (r.u8(), r.u8());
            if led as usize >= LEDS || state > 2 {
                return Err(bad);
            }
            leds(w, d.led_set(led, state)?, false);
        }
        OP_TYPEC_STATUS => {
            if !r.exact(0) {
                return Err(bad);
            }
            let t = d.typec_status()?;
            w.u8(t.configured as u8);
            w.u8(t.lines);
            for p in &t.ports {
                cc_port(w, p);
            }
        }
        OP_PD_STATUS => {
            if !r.exact(0) {
                return Err(bad);
            }
            let p = d.pd_status();
            w.u8(p.armed as u8);
            w.u8(p.cc);
            w.u8(p.running as u8);
            w.u8(p.ready as u8);
            w.u32(p.sent);
            w.u32(p.received);
            w.u32(p.errors);
            w.u32(p.hardresets);
            w.u8(p.contract_pos);
            w.u16(p.contract_ma);
            w.u32(p.session_bytes);
        }
        OP_USB_PORT => {
            if !r.exact(1) {
                return Err(bad);
            }
            let port = r.u8();
            if port as usize >= CHANNELS {
                return Err(bad);
            }
            let u = d.usb_port(port)?;
            w.u8(u.sample as u8);
            w.u32(u.mv);
            w.i32(u.ua);
            w.u8(u.drawing as u8);
            w.u8(u.switch);
            w.u8(u.cable.is_some() as u8);
            let c = u.cable.unwrap_or(CcPort { cc1: CC_UNMEASURED, cc2: CC_UNMEASURED, ..CcPort::default() });
            w.u8(c.vbus as u8);
            w.u8(c.cc1);
            w.u8(c.cc2);
            w.u8(c.source_cc);
            w.u8(c.orientation);
        }
        OP_HR_STATUS => {
            if !r.exact(0) {
                return Err(bad);
            }
            let h = d.hr_status()?;
            w.u32(h.id);
            w.u32(h.ck_khz_req);
            w.u32(h.ck_khz_meas);
            for flag in [h.ready, h.locked, h.calibrated, h.busy, h.hr_busy] {
                w.u8(flag as u8);
            }
            w.u8(h.last_op);
            w.u8(h.last_code);
            w.u16(h.errors);
            w.u16(h.state);
            w.u8(h.cal_tap);
            w.u8(h.cal_width);
            w.u8(h.cal_valid as u8);
        }
        OP_SELFTEST_RUN => {
            if !r.exact(0) {
                return Err(bad);
            }
            let s = d.selftest();
            w.u8(s.passed);
            w.u8(s.failed);
            w.u8(s.skipped);
            w.u8(s.n);
            for &(item, outcome) in &s.items[..s.n as usize] {
                w.u8(item);
                w.u8(outcome);
            }
        }
        _ => return Err(Fail { status: ST_UNKNOWN_OP, reason: 0, value: 0 }),
    }
    Ok(())
}

fn leds(w: &mut Writer, l: Leds, button: bool) {
    w.u8(l.lit);
    w.u8(l.fabric);
    if button {
        w.u8(l.button as u8);
    }
}

fn cc_port(w: &mut Writer, p: &CcPort) {
    w.u8(p.ok as u8);
    w.u8(p.device_id);
    w.u8(p.vbus as u8);
    w.u8(p.cc1);
    w.u8(p.cc2);
    w.u8(p.source_cc);
    w.u8(p.orientation);
    w.u32(p.serviced);
}

/// Bit helper for drivers: bit n = `f(n)`.
pub fn bits(count: usize, f: impl Fn(usize) -> bool) -> u8 {
    (0..count).fold(0, |m, i| m | ((f(i) as u8) << i))
}
