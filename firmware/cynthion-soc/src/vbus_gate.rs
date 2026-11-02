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
    if flags & ov_bit(channel) != 0 {
        return Err(Refusal::Ov(millivolts));
    }
    if flags & uv_bit(channel) != 0 {
        return Err(Refusal::Uv(millivolts));
    }
    if millivolts > LIMIT_MV {
        return Err(Refusal::TooHigh(millivolts));
    }
    if millivolts < FLOOR_MV {
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
