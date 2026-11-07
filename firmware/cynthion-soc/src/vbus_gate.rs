//! The VBUS gate's decisions, as pure functions (#552).
//!
//! No `crate::` paths, so `firmware/cynthion-soc-tests` runs these on the host.
//! Register access and the I2C sequence stay in `power.rs` / `vbus.rs`.
//!
//! The PAC1954 compares every sample (1024 SPS) against per-channel UV/OV limits
//! and latches a flag; the gate asks the part instead of dating a polled sample.

/// The most that is passed through to TARGET A, in millivolts.
///
/// Equals the board's own input OV threshold (5.5 V). Not a Cynthion protection
/// limit: the passthrough network is rated to 20 V.
pub const LIMIT_MV: u32 = 5_500;

/// Below this the switch is outside its documented working range.
pub const FLOOR_MV: u32 = 4_400;

/// The accepted rail window for one close: 88% to 110% of the nominal volts
/// (5 V gives exactly [`FLOOR_MV`]..[`LIMIT_MV`]).
#[derive(Copy, Clone, PartialEq, Eq, Debug)]
pub struct Window {
    pub floor_mv: u32,
    pub limit_mv: u32,
}

impl Window {
    pub const V5: Window = Window { floor_mv: FLOOR_MV, limit_mv: LIMIT_MV };

    /// Both ends of a change from `from_mv` to `to_mv`: low floor, high limit.
    pub const fn spanning(from_mv: u32, to_mv: u32) -> Window {
        let a = Window::for_nominal(from_mv);
        let b = Window::for_nominal(to_mv);
        Window {
            floor_mv: if a.floor_mv < b.floor_mv { a.floor_mv } else { b.floor_mv },
            limit_mv: if a.limit_mv > b.limit_mv { a.limit_mv } else { b.limit_mv },
        }
    }

    pub const fn for_nominal(nominal_mv: u32) -> Window {
        Window { floor_mv: nominal_mv / 100 * 88, limit_mv: nominal_mv / 100 * 110 }
    }
}

/// `ALERT_STATUS` / `ALERT_ENABLE` / `GPIO_ALERT2` share one layout: CH1 is the
/// high bit of each group (CH1OV = bit 15, CH1UV = bit 11).
pub const fn ov_bit(channel: usize) -> u32 {
    1 << (15 - channel as u32)
}

pub const fn uv_bit(channel: usize) -> u32 {
    1 << (11 - channel as u32)
}

/// Both limit bits for one channel.
pub const fn limit_bits(channel: usize) -> u32 {
    ov_bit(channel) | uv_bit(channel)
}

/// Why a close was refused.
#[derive(PartialEq, Eq)]
pub enum Refusal {
    /// The part's UV flag is set. Millivolts as read on demand.
    Uv(u32),
    /// The part's OV flag is set.
    Ov(u32),
    /// Measured volts above [`LIMIT_MV`], flag not set.
    TooHigh(u32),
    /// Measured volts below [`FLOOR_MV`], flag not set.
    TooLow(u32),
    /// The part did not answer, or there is no bus on this target.
    Unreadable,
    /// `CTRL.SAMPLE_MODE` is not continuous 1024 SPS, so no limit is evaluated.
    NotSampling,
}

/// `CTRL.SAMPLE_MODE` (bits 15-12): 0 is 1024 SPS adaptive, the POR value and
/// what `power::CTRL_ESTABLISHED` writes. Anything else is not the mode the
/// limits were designed against (sleep, single-shot, slower rates).
pub const fn sampling_continuously(ctrl: u16) -> bool {
    ctrl >> 12 == 0
}

/// Close allowed? `flags` is `ALERT_STATUS` as read after the limits had at
/// least one conversion to act on; `millivolts` is the on-demand VBUS read.
///
/// Flags first: the part compared every sample, the read is one instant.
pub fn decide(channel: usize, millivolts: u32, flags: u32) -> Result<u32, Refusal> {
    decide_in(Window::V5, channel, millivolts, flags)
}

/// [`decide`] against the window of the voltage this close expects.
pub fn decide_in(window: Window, channel: usize, millivolts: u32, flags: u32) -> Result<u32, Refusal> {
    if flags & ov_bit(channel) != 0 {
        return Err(Refusal::Ov(millivolts));
    }
    if flags & uv_bit(channel) != 0 {
        return Err(Refusal::Uv(millivolts));
    }
    if millivolts > window.limit_mv {
        return Err(Refusal::TooHigh(millivolts));
    }
    if millivolts < window.floor_mv {
        return Err(Refusal::TooLow(millivolts));
    }
    Ok(millivolts)
}

/// Is `channel` armed while `closing` is the source being closed (or closed)?
///
/// Only the source: an open switch's rail is unconnected and would read UV.
pub const fn channel_armed(channel: usize, closing: Option<usize>) -> bool {
    match closing {
        Some(source) => source == channel,
        None => false,
    }
}

/// What the alert service does with a fired status word.
#[derive(PartialEq, Eq)]
pub struct AlertAction {
    /// Run `vbus::open_all()`.
    pub open: bool,
    /// Disarm the limits: the rail is no longer ours to judge, and an armed UV
    /// on an unconnected rail would storm.
    pub disarm: bool,
    /// The bits that belong to the gate, to be kept out of the generic back-off
    /// (which would move our thresholds).
    pub gate_bits: u32,
}

/// `armed` is the channel the gate armed, if any; `closed` is whether any
/// switch is closed. Fired bits on other channels or kinds are not the gate's.
pub fn alert_action(fired: u32, armed: Option<usize>, closed: bool) -> AlertAction {
    let gate_bits = match armed {
        Some(channel) => fired & limit_bits(channel),
        None => 0,
    };
    AlertAction {
        open: gate_bits != 0 && closed,
        disarm: gate_bits != 0,
        gate_bits,
    }
}

// ---- port power plans (#625) ----
//
// Pure: the register byte in, a verdict out. `vbus.rs` does the I2C and the write.

/// Register bit positions, matching `gateware/soc/peripherals/vbus_csr.py`.
pub const BIT_TARGET_C: u8 = 3;
pub const BIT_CONTROL: u8 = 4;
pub const BIT_AUX: u8 = 5;
pub const BIT_DISCHARGE: u8 = 6;
pub const BIT_ENABLE: u8 = 7;

/// Ports, as `power::PORTS` indexes them (the PAC1954 channel).
pub const TARGET_A: usize = 0;
pub const TARGET_C: usize = 1;
pub const AUX: usize = 2;
pub const CONTROL: usize = 3;

const SOURCES: u8 = 1 << BIT_TARGET_C | 1 << BIT_CONTROL | 1 << BIT_AUX;

/// The source switches that are closed, in register positions.
pub const fn closed_mask(reg: u8) -> u8 {
    if reg & (1 << BIT_ENABLE) == 0 { 0 } else { reg & SOURCES }
}

/// Switches `port`'s power verbs may close and open. TARGET_A is the shared
/// rail's far end, so CONTROL or AUX feed it; TARGET_C's charge path is CONTROL
/// through the rail plus its own switch.
const fn own(port: usize) -> u8 {
    match port {
        TARGET_A => 1 << BIT_CONTROL | 1 << BIT_AUX,
        TARGET_C => 1 << BIT_TARGET_C | 1 << BIT_CONTROL,
        _ => 0,
    }
}

#[derive(PartialEq, Eq)]
pub enum PowerRefusal {
    /// AUX and CONTROL are inputs: they have no power verbs.
    NotOutput,
    /// The closed-switch mask (register bit positions) that is not this port's.
    Other(u8),
}

#[derive(PartialEq, Eq)]
pub enum OnPlan {
    /// Already fed by CONTROL or AUX; re-closing would drop VBUS under the device.
    AlreadyOn,
    Close,
}

pub const fn on_plan(port: usize, reg: u8) -> Result<OnPlan, PowerRefusal> {
    let closed = closed_mask(reg);
    match port {
        TARGET_A => {
            if closed == 0 {
                Ok(OnPlan::Close)
            } else if closed == 1 << BIT_CONTROL || closed == 1 << BIT_AUX {
                Ok(OnPlan::AlreadyOn)
            } else {
                Err(PowerRefusal::Other(closed))
            }
        }
        TARGET_C => {
            if closed == 0 || closed == own(TARGET_C) {
                Ok(OnPlan::Close)
            } else {
                Err(PowerRefusal::Other(closed))
            }
        }
        _ => Err(PowerRefusal::NotOutput),
    }
}

/// Off is allowed when every closed switch is this port's own.
pub const fn off_plan(port: usize, reg: u8) -> Result<(), PowerRefusal> {
    if port != TARGET_A && port != TARGET_C {
        return Err(PowerRefusal::NotOutput);
    }
    let foreign = closed_mask(reg) & !own(port);
    if foreign == 0 { Ok(()) } else { Err(PowerRefusal::Other(foreign)) }
}

/// Is `port` driven according to the register? Not a measurement.
pub const fn powered(port: usize, reg: u8) -> bool {
    let closed = closed_mask(reg);
    match port {
        TARGET_A => closed != 0,
        TARGET_C => closed & (1 << BIT_TARGET_C) != 0,
        AUX => closed & (1 << BIT_AUX) != 0,
        _ => closed & (1 << BIT_CONTROL) != 0,
    }
}
