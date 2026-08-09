//! The PAC1954-1 power monitor: four rails, polled, reported when they change.
//!
//! The part is a four-channel bus-voltage and shunt-current monitor at I2C
//! `0x10` on the `power_monitor` bus. The conversion arithmetic and the channel
//! map already existed in Python (`gateware/probes/power_monitor/registers.py`, driven
//! over JTAG by `scripts/power_probe.py`); this is the same thing where the CPU
//! can reach it, which is what makes continuous monitoring possible -- the JTAG
//! path needs an Apollo debug session and therefore cannot run while anything
//! else is using the debugger.
//!
//! ## Every sample set comes from one instant
//!
//! `REFRESH` (a Send Byte to register `0x00`) latches VBUS, VSENSE and the
//! accumulators for all four channels together. Without it the eight measurement
//! registers hold whatever each was last latched at, so channel 1's voltage and
//! channel 4's current can be milliseconds apart -- and the failure is invisible,
//! because every individual number is plausible. Reading all eight in ONE
//! auto-incremented transaction is the other half of that guarantee: it makes it
//! impossible for a REFRESH to land halfway through a sample set.
//!
//! **Each poll reads the set the PREVIOUS poll asked for, then asks for the
//! next.** That is not an optimisation; it is what the part requires. The
//! registers "will be stable within 1 ms from sending the REFRESH command"
//! (DS20006539B 5.2), and reading inside that window does not return stale data
//! -- it returns *no* data: the part acknowledges its address and then NACKs the
//! register pointer. This driver did REFRESH-then-read first and the console
//! filled with `no acknowledge (register pointer)`. Reading last poll's sample
//! turns a zero-millisecond margin into a fifty-millisecond one at no cost.
//!
//! ## One owner of the REFRESH cycle
//!
//! REFRESH-then-read is a protocol that spans two transactions with a window in
//! the middle, and [`Monitor::service`] owns it. Nothing else in this firmware
//! may read the part: the transaction is private to this module and `service` is
//! its only caller, so there is no second REFRESH to collide with the first.
//!
//! **Who calls `service` is the concurrency model and nothing else** (#245).
//! The default build is the superloop and reaches it through [`Monitor::poll`],
//! which is the interval check at the top of the main loop; `--features rtic`
//! deletes that function and reaches the same body from a periodic task the 1 ms
//! tick releases. One owner either way, and the same 32 bytes of state, which is
//! what makes the jitter figures the `rtic` command prints comparable.
//!
//! **Nothing else may read this part.** A second reader lands inside the 1 ms REFRESH
//! window and reports a bus fault on a working bus. Full argument, including the measured
//! collision rate: `docs/architecture.md#20-multi-transaction-device-protocols`.
//!
//! Staleness is the cost, and staleness is the kind of wrongness that looks right. So:
//!
//!   * the sample carries the instant of the REFRESH that **latched** it, not the read
//!     that fetched it -- the read is one interval later, so timestamping it would
//!     understate age by a full poll
//!   * `power` prints that age, so a stopped poller reads as a number climbing past
//!     50 ms rather than as four plausible voltages
//!
//! ## What gets printed, and what does not
//!
//! A poll every 50 ms is twenty lines a second per channel if every sample is
//! reported, which is not a log -- it is a wall of text that hides the one line
//! that mattered. So a sample is only announced when it differs from the last
//! ANNOUNCED value by at least [`CHANGE_UA`], and each port has a floor below
//! which it is called disconnected and says nothing at all. The measured ADC
//! offset on an unplugged rail is 0.76-0.92 mA (see
//! `docs/chips/pac1954-power-monitor.md`), and without a floor that noise walks
//! across a threshold and emits events from a port with nothing plugged into it.
//!
//! Comparing against the last *announced* value rather than the last sample is
//! deliberate. Against the last sample, a rail ramping at 90 mA per poll would
//! never announce anything however far it travelled; against the last announced
//! value, every 100 mA of travel produces exactly one line.

use core::fmt::Write;

use crate::bus::{self, Bus, BUS_POWER_MONITOR};
use crate::clock::{self, Instant};
use crate::uart::Uart;

/// The address the PAC1954 on r1.4 is strapped to.
///
/// Set by a resistor from ADDRSEL to ground and latched at power-up, so it
/// cannot be changed at runtime. Confirmed on this board: `0x10` answers and
/// `0x11`-`0x1e` are silent (`docs/chips/pac1954-power-monitor.md`). The `i2c`
/// shell command still scans, so a board strapped differently reports what it
/// found rather than being quietly unmonitored.
pub const ADDRESS: u8 = 0x10;

/// Send Byte: latch VBUS, VSENSE and the accumulators together.
/// `REFRESH_V`, and NOT `REFRESH` (`0x00`).
///
/// DS20006539B, register table: `REFRESH_V (1FH)` -- "Refreshes VBUS and VSENSE
/// data only". Section 5.1 puts it plainly: `REFRESH` reads accumulator data
/// **and resets the accumulators**; `REFRESH_V` reads voltage, current and power
/// **without** resetting them.
///
/// This driver reads VBUS and VSENSE and never touches an accumulator. It sent
/// `REFRESH` anyway, twenty times a second, since it was written -- so the
/// accumulators and the accumulator count were wiped before they could
/// accumulate anything, and the part's true-average-power feature could never
/// have been used. Nothing was broken, because nothing read them; nothing COULD
/// read them, and the reason was one byte. #275.
const REG_REFRESH_V: u8 = 0x1f;

// ---- the limit ALERT (#270) -----------------------------------------------
//
// Five limit types, all per channel, all with a hardware debounce, and all three
// control registers share ONE 24-bit layout:
//
//     bit 23..16   CH1OC CH2OC CH3OC CH4OC  CH1UC CH2UC CH3UC CH4UC
//     bit 15..8    CH1OV CH2OV CH3OV CH4OV  CH1UV CH2UV CH3UV CH4UV
//     bit  7..0    CH1OP CH2OP CH3OP CH4OP  ACC_OVF ACC_COUNT ALERT_CC --
//
// `ALERT_STATUS` reports, `GPIO_ALERT2` routes to the pin, `ALERT_ENABLE` is the
// master. One bit-position function serves all three.

/// Control. Read-modify-written, never written blind: bits 9-8 are
/// `SLOW_ALERT1`, and #273 established that field is load-bearing -- it was
/// holding the converter at 8 SPS. POR is `0x0700`: `SAMPLE_MODE` 1024 SPS,
/// `GPIO_ALERT2` = GPIO input, `SLOW_ALERT1` = SLOW.
const REG_CTRL: u8 = 0x01;
/// `GPIO_ALERT2[1:0]`, bits 11-10. `00` makes the pin an ALERT output.
const CTRL_GPIO_ALERT2_SHIFT: u32 = 10;

/// Which limits have fired. **Read-to-clear**, which is what services the level
/// -- the pin is latched low until this is read, exactly like a FUSB302B.
const REG_ALERT_STATUS: u8 = 0x26;
/// Which limits are routed to the GPIO/ALERT2 pin.
const REG_GPIO_ALERT2: u8 = 0x28;
/// Which limits are armed at all. Cleared before any limit is written and
/// restored after -- register 7-29: "Disable ALERTs in Register 7-34 before
/// changing the value to avoid false triggers."
const REG_ALERT_ENABLE: u8 = 0x49;

/// Per-channel limit registers. Channel `n` (0-3) is `base + n`.
const REG_OC_LIMIT: u8 = 0x30;
const REG_UC_LIMIT: u8 = 0x34;
const REG_OV_LIMIT: u8 = 0x3c;
const REG_UV_LIMIT: u8 = 0x40;

/// Consecutive samples over the limit before the ALERT asserts, two bits per
/// channel, one register per limit type. `00`=1, `01`=4, `10`=8, `11`=16 -- at
/// 1024 SPS that is 0.98, 3.9, 7.8 or 15.6 ms of SUSTAINED excursion.
///
/// This is what makes a tight bracket usable: a device's inrush is milliseconds,
/// so 16 samples is a fault rather than an event.
const REG_OC_NSAMPLES: u8 = 0x44;
const REG_UC_NSAMPLES: u8 = 0x45;
const REG_OV_NSAMPLES: u8 = 0x47;
const REG_UV_NSAMPLES: u8 = 0x48;

/// The five limit kinds, in the order their bits appear in the 24-bit word.
#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(usize)]
pub enum Limit {
    OverCurrent,
    UnderCurrent,
    OverVoltage,
    UnderVoltage,
}

impl Limit {
    /// The bit for `channel` (0-3) in `ALERT_STATUS`, `GPIO_ALERT2` and
    /// `ALERT_ENABLE` -- one function, because all three share a layout. CH1 is
    /// the HIGH bit of each group, so the channel index subtracts.
    pub fn bit(self, channel: usize) -> u32 {
        let top = match self {
            Limit::OverCurrent => 23,
            Limit::UnderCurrent => 19,
            Limit::OverVoltage => 15,
            Limit::UnderVoltage => 11,
        };
        1 << (top - channel as u32)
    }

    pub fn limit_register(self, channel: usize) -> u8 {
        let base = match self {
            Limit::OverCurrent => REG_OC_LIMIT,
            Limit::UnderCurrent => REG_UC_LIMIT,
            Limit::OverVoltage => REG_OV_LIMIT,
            Limit::UnderVoltage => REG_UV_LIMIT,
        };
        base + channel as u8
    }

    pub fn nsamples_register(self) -> u8 {
        match self {
            Limit::OverCurrent => REG_OC_NSAMPLES,
            Limit::UnderCurrent => REG_UC_NSAMPLES,
            Limit::OverVoltage => REG_OV_NSAMPLES,
            Limit::UnderVoltage => REG_UV_NSAMPLES,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Limit::OverCurrent => "oc",
            Limit::UnderCurrent => "uc",
            Limit::OverVoltage => "ov",
            Limit::UnderVoltage => "uv",
        }
    }

    /// Is this a current limit? Current limits are compared against VSENSE and
    /// voltage limits against VBUS, and the two have different scales -- getting
    /// it wrong programs a threshold 320 times off.
    pub fn is_current(self) -> bool {
        matches!(self, Limit::OverCurrent | Limit::UnderCurrent)
    }

    pub const ALL: [Limit; 4] = [
        Limit::OverCurrent,
        Limit::UnderCurrent,
        Limit::OverVoltage,
        Limit::UnderVoltage,
    ];
}

/// VSENSE range for all four channels. `0x55` selects bipolar +/-100 mV for
/// each two-bit CFG_VSn field; the low byte leaves every VBUS range unipolar.
const REG_NEG_PWR_FSR: u8 = 0x1d;
const NEG_PWR_FSR_BIPOLAR: [u8; 2] = [0x55, 0x00];

/// VBUS1. VBUS1-4 are `0x07`-`0x0a` and VSENSE1-4 are `0x0b`-`0x0e`, so one
/// 16-byte read from here covers every measurement this driver wants. The part
/// auto-increments its address pointer within a read (DS20006539B), which is the
/// same behaviour that silently shifted every value by one register during
/// bring-up when a 1-byte register was read with `size=2` -- so the length here
/// has to match the span exactly.
const REG_VBUS1: u8 = 0x07;

/// Four channels, two 16-bit registers each, one transaction.
const MEASUREMENT_BYTES: usize = 16;

/// Which physical port each PAC channel measures.
///
/// **This is not the intuitive ordering: channel 1 is TARGET_A, not CONTROL.**
/// Derived from the clean `r1.4.0` schematic tag and then confirmed physically by
/// unplugging AUX and watching AUX -- and only AUX -- go dead, with its current
/// appearing on CONTROL. See `docs/chips/pac1954-power-monitor.md`.
pub const PORTS: [&str; 4] = ["target_a", "target_c", "aux", "control"];

/// How far a channel must move before it is worth a line, in microamps.
///
/// 100 mA, from the issue this implements. For scale: the whole board idles at
/// about 65 mA and a bus-powered device that enumerates draws 100 mA by
/// specification, so this is "something was plugged in or started up", not
/// "something twitched".
pub const CHANGE_UA: u32 = 100_000;

/// How far past the observed excursion a tripped limit is moved, in millivolts.
///
/// **A limit that trips and stays where it is, storms.** The condition is still
/// true on the next conversion, so it re-asserts, and the board spends its time
/// servicing one event -- measured at 88% CPU with a bracket inside the ADC
/// noise, with the periodic task 53 ms late on a 50 ms period.
///
/// So a trip moves the threshold past what it saw. The next alert is then a
/// LARGER excursion than the last, which turns a storm into a sequence of
/// "new worst" reports and converges on its own.
///
/// 500 mV: bigger than any noise or ripple this board shows, small enough that
/// a real fault still produces several steps rather than one.
pub const ALERT_BACKOFF_MV: i32 = 500;

/// The same, in milliamps.
pub const ALERT_BACKOFF_MA: i32 = 500;

/// Below this, a port is reported as disconnected and says nothing.
///
/// 10 mA. An unplugged rail measures 0.76-0.92 mA on this board -- ADC offset
/// near zero, not leakage -- and the smallest connected draw seen is 29 mA, so
/// 10 mA sits an order of magnitude above the noise and a factor of three below
/// anything real. Per port and changeable at runtime (`power floor`), because a
/// port with a deliberately tiny load is a legitimate thing to want to watch.
pub const DEFAULT_FLOOR_UA: u32 = 10_000;

/// Default sampling period, in milliseconds. Runtime-settable -- see
/// [`set_interval_ms`].
pub const DEFAULT_INTERVAL_MS: u32 = 50;

/// [`set_interval_ms`] takes this to mean "do not poll at all".
///
/// Not merely a very long period: the tick short-circuits on it, so an off poll
/// costs nothing per tick. Excursions are still reported -- the ALERT is a
/// hardware comparison against every sample inside the part and does not depend
/// on this at all (#285).
pub const RATE_OFF: u32 = 0;

/// The floor a rate may be set to, in milliseconds.
///
/// The PAC1954 samples at 1024 SPS in normal mode -- **976 us** -- and 1024 is
/// the fastest of its 8/64/256/1024 options. So 1 ms collects one fresh sample
/// per poll and nothing faster exists to collect; below this the same sample is
/// read twice and only bus time is spent.
///
/// A spike shorter than 976 us is not sampled by the part at all, so NO poll
/// rate finds one. The ALERT is the only thing that sees between samples.
pub const MIN_INTERVAL_MS: u32 = 1;

/// How often the rails are sampled. `RATE_OFF` disables the poll.
static INTERVAL_MS: core::sync::atomic::AtomicU32 =
    core::sync::atomic::AtomicU32::new(DEFAULT_INTERVAL_MS);

pub fn interval_ms() -> u32 {
    INTERVAL_MS.load(core::sync::atomic::Ordering::Relaxed)
}

/// Set the sampling period. Clamped to [`MIN_INTERVAL_MS`]; returns what was set.
///
/// Clamped rather than refused, and it says so at the call site: a rate the poll
/// cannot honour would free-run, and a transcript reporting a rate that is not
/// being achieved is the plausible-wrong-answer failure this driver keeps
/// removing.
pub fn set_interval_ms(ms: u32) -> u32 {
    let ms = if ms == RATE_OFF {
        RATE_OFF
    } else {
        ms.max(MIN_INTERVAL_MS)
    };
    INTERVAL_MS.store(ms, core::sync::atomic::Ordering::Relaxed);
    ms
}

/// Longest and most recent complete REFRESH cycle, in timer ticks.
///
/// The question `sched` could not answer: it measures the GAP between polls,
/// never the DURATION of one, so whether a requested rate is achievable was
/// unknown. A rate faster than one cycle cannot be honoured however it is
/// configured.
static WORST_CYCLE: core::sync::atomic::AtomicU32 =
    core::sync::atomic::AtomicU32::new(0);
static LAST_CYCLE: core::sync::atomic::AtomicU32 =
    core::sync::atomic::AtomicU32::new(0);

/// `(worst, last)` complete cycle, in ticks. Zero until one has completed.
pub fn cycle_ticks() -> (u32, u32) {
    (
        WORST_CYCLE.load(core::sync::atomic::Ordering::Relaxed),
        LAST_CYCLE.load(core::sync::atomic::Ordering::Relaxed),
    )
}

fn record_cycle(ticks: u32) {
    LAST_CYCLE.store(ticks, core::sync::atomic::Ordering::Relaxed);
    WORST_CYCLE.fetch_max(ticks, core::sync::atomic::Ordering::Relaxed);
}

/// Past this, the sample's age is reported as a bound and not as a number.
///
/// `clock::now()` is the low 32 bits of the `time` CSR, which wraps every 71.6
/// seconds at 60 MHz. An age computed across a wrap is a small number -- a
/// poller stopped for two minutes would report "12 ms old", which is precisely
/// the plausible-wrong-answer failure this whole change exists to remove. 60
/// seconds is comfortably inside one wrap on both targets and three orders of
/// magnitude past the 50 ms a working poll takes, so nothing healthy reaches it.
pub const AGE_LIMIT_MS: u32 = 60_000;

/// One channel's reading, in the units the shell prints.
#[derive(Clone, Copy)]
pub struct Reading {
    pub bus_mv: u32,
    pub current_ua: i32,
}

/// Every channel from one instant, and which instant that was.
#[derive(Clone, Copy)]
pub struct Sample {
    pub readings: [Reading; 4],
    /// The REFRESH that latched these values -- NOT the read that fetched them.
    ///
    /// Each poll reads the set the PREVIOUS poll asked for, so the fetch happens
    /// one interval after the measurement. Timestamping the fetch would
    /// understate the age by exactly the interval this driver is trying to make
    /// visible, and would do it silently.
    pub latched: Instant,
}

/// How old the cached sample is, as far as it can truthfully be stated.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Age {
    /// Milliseconds since the REFRESH that latched it. 50-100 ms in steady
    /// state: one interval for the sample to be fetched, plus however long ago
    /// the last poll ran.
    Millis(u32),
    /// Past [`AGE_LIMIT_MS`], where a number would wrap and start lying.
    Older,
    /// Nothing sampled yet. True for the first two polls after reset -- one to
    /// issue a REFRESH, one to read what it latched -- and true forever if the
    /// part never answers.
    Never,
}

/// VBUS is a 0-32 V full-scale unsigned 16-bit reading.
///
/// 32 V / 65536 = 488.28125 uV per LSB, so millivolts are `raw * 32000 / 65536`,
/// which reduces to `raw * 125 / 256` EXACTLY -- no rounding constant, no
/// floating point, and no soft-float library linked into a 64 KiB block RAM. The
/// widest intermediate is 65535 * 125 = 8_191_875, which fits a `u32` with room
/// to spare.
///
/// VBUS stays unipolar when VSENSE changes to bipolar; the low byte written to
/// `NEG_PWR_FSR` is zero.
/// Millivolts to a VBUS code. The inverse of [`bus_mv`], and it has to stay so:
/// a limit programmed on a different scale from the measurement is a threshold
/// that trips at the wrong value and looks correct in the register.
///
/// Saturates rather than wrapping. A request above full scale becomes a limit
/// that cannot trip, which is wrong but obvious; a wrapped one becomes a limit
/// that trips constantly and looks deliberate.
pub fn mv_to_code(millivolts: u32) -> u16 {
    let code = (millivolts as u64 * 256) / 125;
    code.min(u16::MAX as u64) as u16
}

/// Millivolts to an OV/UV **limit** code, which is NOT the measurement scale.
///
/// DS20006539B section 5.16.4:
///
/// > These limits are specified with **two's complement values independent of
/// > whether Unipolar/Bipolar or Unidirectional/Bidirectional modes** are set
/// > for VBUS and VSENSE measurements.
///
/// So a limit is signed over +/-32 V -- 32768 codes for the positive half, or
/// **976.6 uV each** -- while a VBUS measurement in our unipolar mode is
/// unsigned over 0..32 V, **488.3 uV each**. The two scales differ by exactly
/// two, and using the measurement scale for a limit programs twice the voltage
/// asked for.
///
/// That is why every voltage limit silently failed to fire: `ov 5000` wrote the
/// code for 10 V, which no rail on this board reaches, so the comparator was
/// correct and the threshold was nonsense. It cost a threshold sweep, a channel
/// comparison and a wrong conclusion recorded in a commit message before the
/// sentence above was read properly.
///
/// **The current limits need no equivalent.** VSENSE is already bipolar
/// (`NEG_PWR_FSR = 0x5500`), so measurement and limit share a scale and
/// `ua_to_code` serves both -- which is exactly why OC and UC worked from the
/// first attempt while OV and UV never did.
pub fn mv_to_limit_code(millivolts: i32) -> u16 {
    let code = (millivolts as i64 * 128) / 125;
    code.clamp(i16::MIN as i64, i16::MAX as i64) as i16 as u16
}

/// An OV/UV limit code back to millivolts. The inverse of
/// [`mv_to_limit_code`], and signed, because the register is.
pub fn limit_code_to_mv(raw: u16) -> i32 {
    (raw as i16 as i32 * 125) / 128
}

/// Microamps to a VSENSE code, signed. The inverse of [`current_ua`].
///
/// The limits are compared against the same signed VSENSE the measurement
/// reads, so a negative limit is meaningful -- the switch tree is bidirectional
/// and a port can sink.
pub fn ua_to_code(microamps: i32) -> u16 {
    let magnitude = ((microamps.unsigned_abs() as u64) << 9) / 78125;
    let magnitude = magnitude.min(i16::MAX as u64) as i16;
    (if microamps < 0 { -magnitude } else { magnitude }) as u16
}

pub fn bus_mv(raw: u16) -> u32 {
    raw as u32 * 125 / 256
}

/// VSENSE is -100 to +100 mV across a 20 mOhm shunt.
///
/// DS20006539B section 5.9 and Table 5-2 use a signed 16-bit code and a 2^15
/// denominator in bipolar +/-FSR mode: 5,000,000 uA / 32768 = 78125/512 uA per
/// LSB. Magnitude-first arithmetic keeps equal positive and negative codes
/// symmetric. The nominal full-scale range remains -5 A to +5 A; +/-50 mV
/// FSR/2 is the separate +/-2.5 A mode and is not selected here.
pub fn current_ua(raw: u16) -> i32 {
    let code = raw as i16 as i32;
    let magnitude = ((code.unsigned_abs() as u64 * 78125) >> 9) as i32;
    if code < 0 {
        -magnitude
    } else {
        magnitude
    }
}

/// Is a REFRESH_V outstanding, waiting for its 1 ms before the read?
///
/// Read by `rtic_app::tick`, which releases the task again on the very next
/// tick when this is true. A `static` because the tick handler has no `Monitor`
/// -- the one that matters is inside a shared resource it must not lock.
///
/// One writer (the task) and one reader (the tick), on one hart, so relaxed is
/// enough for the reason `src/metrics.rs` sets out at length.
static REFRESH_PENDING: core::sync::atomic::AtomicBool =
    core::sync::atomic::AtomicBool::new(false);

pub fn refresh_pending() -> bool {
    REFRESH_PENDING.load(core::sync::atomic::Ordering::Relaxed)
}

/// The sole call site for the PAC1954's multi-transaction REFRESH cycle.
fn refresh(bus: &mut Bus) -> Result<(), bus::Error> {
    bus.send_byte(BUS_POWER_MONITOR, ADDRESS, REG_REFRESH_V)
}

/// What the monitor decided about one channel this poll.
#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    /// Below the floor. Nothing plugged in, or nothing drawing.
    Disconnected,
    /// Above the floor, last announced at this current in microamps.
    Connected(i32),
}

/// The poller's state: when it last ran, and what it last said about each rail.
pub struct Monitor {
    last: Instant,
    /// `None` until the first successful poll, so the first sample of a
    /// connected port is announced rather than compared against a zero that was
    /// never measured.
    state: [Option<State>; 4],
    floor: [u32; 4],
    /// Set once, when the part has been found and a whole poll has completed.
    /// Until then `poll` retries: a board whose I2C comes up late should start
    /// working on its own rather than needing a command typed at it.
    live: bool,
    /// Set only after all four VSENSE channels were programmed bipolar and a
    /// REFRESH activated the new range.
    configured: bool,
    /// The limits, debounce and enable mask as last written or read.
    ///
    /// **`power` must reach no bus.** That is the #123 rule -- the command
    /// prints the poller's cached sample so a second reader cannot land inside
    /// the 1 ms window after a refresh, where the part NACKs the register
    /// pointer and a working bus reports a fault. `soc_i2c_owner_sim.py`
    /// asserts it, and it caught the status table reading limit registers to
    /// display them.
    ///
    /// So the cache is written by the code that changes the part -- every
    /// setter below, and the auto-backoff -- and the display reads this. The
    /// values are still the part's; they are just not fetched by a command.
    alert_limits: [[u16; 4]; 4],
    alert_samples: [[u8; 4]; 4],
    alert_enable: u32,
    /// Every limit bit that has fired since boot, accumulated.
    ///
    /// **`ALERT_STATUS` is read-to-clear and belongs to the service path.** A
    /// command that reads it to display it STEALS the event from the handler --
    /// which is the same one-owner rule the REFRESH cycle lives under, and I
    /// broke it with `power alert`.
    ///
    /// The symptom was an alert that fired intermittently for no reason: an OV
    /// limit swept 1000, 1500, 2000, 2500, 3000 mV against a 5.13 V rail
    /// reported fired, fired, NOT, fired, NOT. Identical conditions, different
    /// answers, because the command and the 20 Hz service loop were racing for
    /// a register that empties on the first read. It looked like OV was broken.
    /// OV was never broken.
    ///
    /// So the service path records here, and the command reports THIS.
    alert_fired: u32,
    /// Has a REFRESH_V been sent whose data has not been read yet?
    ///
    /// The whole of the two-dispatch cycle (#275). `service` sends the command
    /// and returns; the tick releases it again 1 ms later and it reads. The
    /// datasheet asks for exactly that gap -- "stable within 1 ms", and a
    /// command inside the window "ignored and NACKed" -- and the tick period
    /// happens to be the same 1 ms, so the second dispatch lands where the part
    /// wants it rather than where a timer was tuned to.
    ///
    /// It replaces reading the PREVIOUS cycle's latch fifty milliseconds later,
    /// which respected the window by a wide margin and made every reading one
    /// whole interval stale.
    refreshing: bool,
    /// When the last REFRESH was issued, or `None` before the first one.
    ///
    /// Two jobs, and they are the same fact. It is the timestamp the NEXT poll's
    /// readings belong to, since a REFRESH latches what the following read
    /// fetches. And `None` is what makes the very first read discardable: before
    /// any REFRESH the measurement registers hold whatever they held at power-up
    /// (DS20006539B 5.2 -- the part only updates them on command), so that read
    /// is not a measurement of anything and must neither be announced nor cached.
    /// Announcing it would put a fabricated connect event on the console at every
    /// boot; caching it would let `power` print it.
    refresh_at: Option<Instant>,
    /// The most recent real sample, which is what `power` prints.
    ///
    /// The whole of issue #123: one owner reads the part, everybody else reads
    /// this. `None` until a REFRESH has been issued AND the sample it latched has
    /// been fetched, which is two polls -- 100 ms after reset, or never on a
    /// board whose monitor does not answer.
    sample: Option<Sample>,
    /// Consecutive failed polls. Printed by `power`, so "the monitor says
    /// nothing" can be told apart from "the monitor cannot reach the part",
    /// which are the same silence from outside.
    pub failures: u32,
    /// Which half of a poll was in flight when it went wrong.
    ///
    /// A bus error says what happened and not where. REFRESH and the
    /// measurement read are two transactions with different shapes -- a Send
    /// Byte and a repeated-START block read -- and they fail for different
    /// reasons, so a report that does not say which one is a report that has to
    /// be reproduced with a logic analyser before it means anything.
    phase: &'static str,
}

impl Monitor {
    pub const fn new() -> Self {
        Monitor {
            // Zero rather than `now()`, because `new()` is a `const` initialiser
            // for a `static` and nothing may run before `main`. The effect is
            // that the first poll happens up to one interval early, once.
            last: Instant::ZERO,
            state: [None; 4],
            floor: [DEFAULT_FLOOR_UA; 4],
            live: false,
            configured: false,
            alert_limits: [[0; 4]; 4],
            alert_samples: [[1; 4]; 4],
            alert_enable: 0,
            alert_fired: 0,
            refreshing: false,
            refresh_at: None,
            sample: None,
            failures: 0,
            phase: "idle",
        }
    }

    /// Which half of the last poll was in flight. See the field.
    pub fn phase(&self) -> &'static str {
        self.phase
    }

    pub fn floor(&self, channel: usize) -> u32 {
        self.floor[channel]
    }

    pub fn set_floor(&mut self, channel: usize, microamps: u32) {
        self.floor[channel] = microamps;
        // Forget what was last announced about that port. Otherwise lowering a
        // floor onto a port that is already drawing leaves it "disconnected"
        // until it moves by 100 mA, which reads as a floor that did not take.
        self.state[channel] = None;
    }

    /// The most recent sample, or `None` if there has not been one.
    ///
    /// What `power` prints. It reaches no bus, so it cannot collide with the
    /// REFRESH cycle this type owns, and it costs a copy of 32 bytes.
    ///
    /// `None` and [`Age::Never`] are the same condition, always: a sample exists
    /// exactly when there is an instant to date it from.
    pub fn latest(&self) -> Option<Sample> {
        self.sample
    }

    /// How old [`Monitor::latest`] is, as far as the counter can say truthfully.
    pub fn age(&self) -> Age {
        age_of(self.sample.map(|sample| sample.latched))
    }

    /// Select signed +/-100 mV VSENSE on every channel and activate it.
    /// A failed boot-time attempt is retried by [`Monitor::poll`].
    pub fn configure(&mut self, bus: &mut Bus) -> Result<(), bus::Error> {
        self.phase = "configure";
        bus.write_registers(
            BUS_POWER_MONITOR,
            ADDRESS,
            REG_NEG_PWR_FSR,
            &NEG_PWR_FSR_BIPOLAR,
        )?;

        // RESET THE ALERT STATE, because the part outlives the firmware.
        //
        // Nothing here is cleared by a firmware reload -- only a power cycle or
        // an explicit write. So a fresh boot inherited whatever the previous
        // session left, and the report could not say so.
        //
        // Two symptoms, both seen:
        //
        //   * an `armed` flag beside a threshold reading `--`: a limit armed
        //     with a ZERO threshold. A zero over-voltage trips permanently, a
        //     zero under-voltage never trips.
        //   * the reverse -- a real threshold in the part that the table shows
        //     as `--`, because the firmware's cache starts empty and only a
        //     write fills it. The display under-reported the part.
        //
        // Reading them back at boot instead would fix the second symptom and
        // leave the first, and it would make a fresh boot's behaviour depend on
        // a session nobody remembers. The firmware owns this part's state, so
        // configure establishes it rather than discovering it.
        //
        // Costs 18 register writes, once, on a bus that is otherwise idle.
        // `power alert on` re-arms whatever has a threshold, so a deliberate
        // configuration is one command away.
        self.write24(bus, REG_ALERT_ENABLE, 0)?;
        self.alert_enable = 0;
        self.write24(bus, REG_GPIO_ALERT2, 0)?;
        for limit in Limit::ALL {
            for channel in 0..4 {
                self.write16(bus, limit.limit_register(channel), 0)?;
                self.alert_limits[limit as usize][channel] = 0;
            }
        }

        refresh(bus)?;
        self.configured = true;
        self.refresh_at = None;
        Ok(())
    }

    /// One REFRESH cycle: fetch what the last one latched, and ask for the next.
    ///
    /// PRIVATE, and `poll` is its only caller. That is the fix for issue #123
    /// stated in the type system rather than in a comment: a second caller cannot
    /// exist, so there is no second REFRESH to land inside this one's window.
    ///
    /// `Ok(None)` is the first pass after reset, whose read predates every
    /// REFRESH and is therefore not a measurement. See `refresh_at`.
    fn transfer(&mut self, bus: &mut Bus) -> Result<Option<Sample>, bus::Error> {
        if !self.configured {
            self.configure(bus)?;
            // The first set after changing range can reflect the prior active
            // configuration. Discard it; the cycle below issues the REFRESH_V
            // whose result is decoded as signed.
            return Ok(None);
        }

        // ---- dispatch A: ask, and return ---------------------------------
        //
        // A Send Byte and nothing else -- about 30 us at 1 MHz. The task holds
        // `Devices` for that and hands it back, where the old single-shot
        // transaction held it for the whole ~2 ms.
        if !self.refreshing {
            self.phase = "refresh";
            refresh(bus)?;
            // AFTER the Send Byte returns, not before: the part latches when it
            // receives the command.
            self.refresh_at = Some(clock::now());
            self.refreshing = true;
            REFRESH_PENDING.store(true, core::sync::atomic::Ordering::Relaxed);
            return Ok(None);
        }

        // ---- dispatch B: read what it latched -----------------------------
        //
        // One 16-byte auto-incremented read, ~170 us at 1 MHz, covering VBUS1-4
        // and VSENSE1-4. One transaction, so nothing can arrive from two
        // different sample instants.
        //
        // Cleared BEFORE the read rather than after it. A bus error here must
        // not leave the cycle stuck waiting for data that is never coming --
        // the next dispatch should issue a fresh REFRESH_V, which is also how
        // the part recovers from a NACK.
        self.refreshing = false;
        REFRESH_PENDING.store(false, core::sync::atomic::Ordering::Relaxed);
        self.phase = "read";
        let mut raw = [0u8; MEASUREMENT_BYTES];
        bus.read_registers(BUS_POWER_MONITOR, ADDRESS, REG_VBUS1, &mut raw)?;

        // WHEN these bytes were measured: the REFRESH_V one dispatch ago, which
        // is 1 ms rather than the 50 ms the previous arrangement carried. A
        // sample timestamped with the read that fetched it would understate its
        // own age, which is the sort of small consistent lie nothing notices.
        let latched = self.refresh_at;

        let mut readings = [Reading {
            bus_mv: 0,
            current_ua: 0,
        }; 4];
        for channel in 0..4 {
            // Big-endian, high byte first, as every 16-bit register on this part
            // is. Getting this backwards produces values that look like noise on
            // a small reading and like a fault on a large one.
            let vbus = u16::from_be_bytes([raw[channel * 2], raw[channel * 2 + 1]]);
            let vsense = u16::from_be_bytes([raw[8 + channel * 2], raw[8 + channel * 2 + 1]]);
            readings[channel] = Reading {
                bus_mv: bus_mv(vbus),
                current_ua: current_ua(vsense),
            };
        }
        Ok(latched.map(|latched| Sample { readings, latched }))
    }


    // ---- the limit ALERT (#270) -------------------------------------------

    /// Read a 24-bit register. The part sends them big-endian, like every
    /// other multi-byte register here.
    fn read24(&self, bus: &mut Bus, register: u8) -> Result<u32, bus::Error> {
        let mut raw = [0u8; 3];
        bus.read_registers(BUS_POWER_MONITOR, ADDRESS, register, &mut raw)?;
        Ok(((raw[0] as u32) << 16) | ((raw[1] as u32) << 8) | raw[2] as u32)
    }

    fn write24(&self, bus: &mut Bus, register: u8, value: u32) -> Result<(), bus::Error> {
        let raw = [(value >> 16) as u8, (value >> 8) as u8, value as u8];
        bus.write_registers(BUS_POWER_MONITOR, ADDRESS, register, &raw)
    }

    fn read16(&self, bus: &mut Bus, register: u8) -> Result<u16, bus::Error> {
        let mut raw = [0u8; 2];
        bus.read_registers(BUS_POWER_MONITOR, ADDRESS, register, &mut raw)?;
        Ok(u16::from_be_bytes(raw))
    }

    fn write16(&self, bus: &mut Bus, register: u8, value: u16) -> Result<(), bus::Error> {
        bus.write_registers(BUS_POWER_MONITOR, ADDRESS, register, &value.to_be_bytes())
    }

    /// Make GPIO/ALERT2 an ALERT pin, without disturbing anything else in CTRL.
    ///
    /// **Read-modify-write, never a blind write.** Bits 9-8 are `SLOW_ALERT1`
    /// and #273 established that field is load-bearing: it selects the SLOW
    /// function, and losing it would put the converter back to 8 SPS. Bits 15-12
    /// are `SAMPLE_MODE`, whose POR default is the 1024 SPS we want. This
    /// touches bits 11-10 and nothing else.
    ///
    /// Changes to `CTRL` take effect on the next refresh, which the poll issues
    /// 50 ms later at the latest.
    pub fn alert_pin_enable(&self, bus: &mut Bus) -> Result<(), bus::Error> {
        let ctrl = self.read16(bus, REG_CTRL)?;
        let wanted = ctrl & !(0b11 << CTRL_GPIO_ALERT2_SHIFT);
        if wanted != ctrl {
            self.write16(bus, REG_CTRL, wanted)?;
        }

        // The peripheral claims its own PLIC source, rather than being
        // remembered by a list in a file that is not its driver. That is the
        // rule #264 established after the I2C source spent the life of the
        // project wired and masked because nobody added it to a third list.
        //
        // Here rather than in `configure`: the source is only meaningful once
        // the pin is an ALERT pin, and claiming it before that would enable a
        // source whose pad is still a GPIO input floating on a pull-up.
        crate::irq::claim(
            cynthion_soc_pac::base::BOARD_I2C_MUX_POWER_ALERT_IRQ,
            1,
        );
        Ok(())
    }

    /// Everything currently armed, routed and fired.
    ///
    /// Read back FROM THE PART rather than from any cached idea of it. That is
    /// the difference between "the write was issued" and "the write took", and
    /// this part gives a specific reason to care: a limit written while alerts
    /// are enabled is a write whose effect is not what the caller intended.
    ///
    /// `ALERT_STATUS` is read-to-clear, so this CLEARS whatever it reports --
    /// which is exactly what services the latched pin, and why it is not called
    /// from anywhere that merely wants to look.
    pub fn alert_service(&self, bus: &mut Bus) -> Result<u32, bus::Error> {
        self.read24(bus, REG_ALERT_STATUS)
    }

    /// Every limit that has fired since boot, as the service path recorded it.
    ///
    /// Reads no bus. This is what a display command asks; `alert_service` is
    /// what the service path calls, and calling it from anywhere else empties
    /// the register underneath the handler.
    /// The cached limit, debounce and enable mask. No bus.
    pub fn alert_limit_cached(&self, limit: Limit, channel: usize) -> u16 {
        self.alert_limits[limit as usize][channel]
    }

    pub fn alert_samples_cached(&self, limit: Limit, channel: usize) -> u32 {
        self.alert_samples[limit as usize][channel] as u32
    }

    pub fn alert_enable_cached(&self) -> u32 {
        self.alert_enable
    }

    pub fn alert_history(&self) -> u32 {
        self.alert_fired
    }

    /// Forget what has fired, so the next report is about what happens next.
    pub fn alert_forget(&mut self) {
        self.alert_fired = 0;
    }

    pub fn alert_enabled(&self, bus: &mut Bus) -> Result<u32, bus::Error> {
        self.read24(bus, REG_ALERT_ENABLE)
    }

    pub fn alert_routed(&self, bus: &mut Bus) -> Result<u32, bus::Error> {
        self.read24(bus, REG_GPIO_ALERT2)
    }

    /// Set one limit, with the disable-write-restore the datasheet requires.
    ///
    /// Register 7-29: "Disable ALERTs in Register 7-34 before changing the value
    /// to avoid false triggers." So the ordering is this function's problem and
    /// not the caller's -- `power limit oc aux 3500` is safe to type at any
    /// moment, and cannot leave alerts disabled if the write fails, because the
    /// restore runs on the error path too.
    ///
    /// `raw` is a device code, not milliamps. The caller converts, because the
    /// scale differs between a current limit and a voltage one and this function
    /// should not have to know which.
    pub fn alert_set_limit(
        &mut self,
        bus: &mut Bus,
        limit: Limit,
        channel: usize,
        raw: u16,
    ) -> Result<(), bus::Error> {
        let enabled = self.read24(bus, REG_ALERT_ENABLE)?;
        self.write24(bus, REG_ALERT_ENABLE, 0)?;
        let result = self.write16(bus, limit.limit_register(channel), raw);
        if result.is_ok() {
            self.alert_limits[limit as usize][channel] = raw;
        }
        // Restored whatever happened above. An early return here would leave
        // the part with every alert disabled and nothing saying so.
        let restored = self.write24(bus, REG_ALERT_ENABLE, enabled);
        result.and(restored)
    }

    /// Arm one limit on one channel, and route it to the pin.
    ///
    /// Both registers, because they are separate gates and the datasheet is
    /// explicit that routing without enabling does nothing: "ALERTs must be
    /// enabled in Register 7-34 before you can route them to a pin."
    pub fn alert_arm(
        &mut self,
        bus: &mut Bus,
        limit: Limit,
        channel: usize,
        on: bool,
    ) -> Result<(), bus::Error> {
        let bit = limit.bit(channel);
        for register in [REG_ALERT_ENABLE, REG_GPIO_ALERT2] {
            let current = self.read24(bus, register)?;
            let wanted = if on { current | bit } else { current & !bit };
            if wanted != current {
                self.write24(bus, register, wanted)?;
            }
            if register == REG_ALERT_ENABLE {
                self.alert_enable = wanted;
            }
        }
        Ok(())
    }

    /// Consecutive samples over the limit before it asserts: 1, 4, 8 or 16.
    ///
    /// Two bits per channel, CH1 highest, one register per limit type. Anything
    /// that is not one of the four is rounded DOWN to the next legal value --
    /// more sensitive rather than less, because this is a detector and a
    /// detector that is quietly less sensitive than asked for is the failure
    /// this whole issue is about.
    pub fn alert_set_nsamples(
        &mut self,
        bus: &mut Bus,
        limit: Limit,
        channel: usize,
        samples: u32,
    ) -> Result<u32, bus::Error> {
        let code: u16 = match samples {
            0..=3 => 0,
            4..=7 => 1,
            8..=15 => 2,
            _ => 3,
        };
        let shift = 6 - 2 * channel as u32;
        let register = limit.nsamples_register();
        let mut raw = [0u8; 1];
        bus.read_registers(BUS_POWER_MONITOR, ADDRESS, register, &mut raw)?;
        let wanted = (raw[0] as u16 & !(0b11 << shift)) | (code << shift);

        // Disable-write-restore, the same as `alert_set_limit`, and it was
        // MISSING here. Register 7-29 carries the warning on the Nsamples
        // register itself -- "Disable ALERTs in Register 7-34 before changing
        // the value to avoid false triggers" -- and this wrote it live.
        //
        // Found while chasing an OV limit that would not fire. It fires; the
        // sequence that appeared to prove otherwise had set the sample count
        // immediately before, with alerts enabled. Whether that is the whole
        // explanation is not established, but writing a debounce underneath a
        // live comparator is a defect on its own terms.
        let enabled = self.read24(bus, REG_ALERT_ENABLE)?;
        self.write24(bus, REG_ALERT_ENABLE, 0)?;
        let result =
            bus.write_registers(BUS_POWER_MONITOR, ADDRESS, register, &[wanted as u8]);
        let restored = self.write24(bus, REG_ALERT_ENABLE, enabled);
        result.and(restored)?;
        let actual = match code {
            0 => 1,
            1 => 4,
            2 => 8,
            _ => 16,
        };
        self.alert_samples[limit as usize][channel] = actual as u8;
        Ok(actual)
    }

    pub fn alert_nsamples(
        &self,
        bus: &mut Bus,
        limit: Limit,
        channel: usize,
    ) -> Result<u32, bus::Error> {
        let mut raw = [0u8; 1];
        bus.read_registers(BUS_POWER_MONITOR, ADDRESS, limit.nsamples_register(), &mut raw)?;
        Ok(match (raw[0] >> (6 - 2 * channel)) & 0b11 {
            0 => 1,
            1 => 4,
            2 => 8,
            _ => 16,
        })
    }

    /// Read one limit register back as a device code.
    pub fn alert_limit(
        &self,
        bus: &mut Bus,
        limit: Limit,
        channel: usize,
    ) -> Result<u16, bus::Error> {
        self.read16(bus, limit.limit_register(channel))
    }

    /// Clear the ALERT at the part, say what fired, and re-arm the source.
    ///
    /// Normal context, called from [`Monitor::service`] when the handler has
    /// deferred one. The read of `ALERT_STATUS` is what drops the latched pin --
    /// the register is read-to-clear -- so the order is: read, report, re-enable.
    ///
    /// **Re-enabled even when the read fails.** A bus error here would otherwise
    /// leave the source masked for the rest of the session, which is a silence
    /// indistinguishable from a rail that never left its limits again. If the
    /// part is still asserting, the interrupt fires again immediately and this
    /// repeats -- which is correct, there is still a condition.
    /// Service a latched ALERT: report what tripped, move the limit, re-enable.
    ///
    /// The body of `rtic_app::power_alert`, released by the pin. Public because
    /// the task is in another module; there is exactly one caller.
    ///
    /// Returns early unless the handler actually deferred one. RTIC dispatches
    /// on a bit, so a coalesced release can arrive with nothing to do, and
    /// reading `ALERT_STATUS` speculatively would be a bus transaction that
    /// CLEARS the register -- consuming the next real event.
    pub fn service_alert(&mut self, uart: &mut Uart, bus: &mut Bus) {
        if !crate::irq::take_power_alert() {
            return;
        }
        let mut moved_any = false;
        match self.alert_service(bus) {
            Ok(fired) => {
                // Recorded before anything else, because this is the only read
                // of it that will ever happen -- the register is empty now.
                self.alert_fired |= fired;
                // WHAT tripped, WHAT the limit was, and WHAT the rail is doing
                // -- not merely that something happened.
                //
                // "oc on aux" is a notification. It has to be reproduced before
                // it means anything, and reproducing an excursion that has
                // already passed is exactly the thing that cannot be done. The
                // limit comes from the part, the reading from the last sample,
                // and the excursion is the subtraction.
                for limit in Limit::ALL {
                    for channel in 0..4 {
                        if fired & limit.bit(channel) == 0 {
                            continue;
                        }
                        let threshold = self
                            .alert_limit(bus, limit, channel)
                            .map(|raw| {
                                if limit.is_current() {
                                    current_ua(raw) / 1000
                                } else {
                                    limit_code_to_mv(raw)
                                }
                            })
                            .unwrap_or(0);
                        // The rail as of the last REFRESH, which is at most one
                        // interval old. NOT the value at the instant the limit
                        // tripped -- the part does not keep that, and by the
                        // time normal context runs the excursion may be over.
                        // Saying "now" rather than implying "then" is the whole
                        // point; a report that quietly claims to be the trigger
                        // sample would be the plausible-wrong-answer failure
                        // this firmware keeps removing.
                        let now = self.sample.map(|sample| {
                            let reading = sample.readings[channel];
                            if limit.is_current() {
                                reading.current_ua / 1000
                            } else {
                                reading.bus_mv as i32
                            }
                        });
                        // Volts and amps, formatted the way the `power` table
                        // formats them. A limit reported in millivolts beside a
                        // reading reported in volts is two units for one
                        // quantity, and the reader has to convert to compare
                        // the two numbers the line exists to compare.
                        let over = matches!(limit, Limit::OverCurrent | Limit::OverVoltage);
                        let step = if limit.is_current() {
                            ALERT_BACKOFF_MA
                        } else {
                            ALERT_BACKOFF_MV
                        };

                        let (moved, excursion) = match now {
                            // Seen out of range: step past the value itself, so
                            // one move clears any size of excursion.
                            Some(now) if (over && now > threshold) || (!over && now < threshold) => {
                                (if over { now + step } else { now - step },
                                 Some(if over { now - threshold } else { threshold - now }))
                            }
                            // Latched, but back in range by the time normal
                            // context looked. **This must still move**, and the
                            // first version did not -- which is why the log
                            // filled with `back in range` at 9000 a second. The
                            // excursion is unknown, so step from the threshold
                            // rather than from the reading. It takes more steps
                            // and it converges.
                            _ => (if over { threshold + step } else { threshold - step }, None),
                        };

                        let raw = if limit.is_current() {
                            ua_to_code(moved.saturating_mul(1000))
                        } else {
                            mv_to_limit_code(moved)
                        };
                        let rearmed = self.alert_set_limit(bus, limit, channel, raw).is_ok();
                        moved_any |= rearmed;
                        let settled = if rearmed { moved } else { threshold };

                        if limit.is_current() {
                            match excursion {
                                Some(by) => crate::log!(
                                    uart,
                                    "power: {} {} {}.{:03} A, saw {}.{:03} A -- {} by {}.{:03} A, limit -> {}.{:03}",
                                    PORTS[channel], limit.name(),
                                    threshold / 1000, (threshold % 1000).unsigned_abs(),
                                    now.unwrap_or(0) / 1000, (now.unwrap_or(0) % 1000).unsigned_abs(),
                                    if over { "over" } else { "under" },
                                    by / 1000, (by % 1000).unsigned_abs(),
                                    settled / 1000, (settled % 1000).unsigned_abs()
                                ),
                                None => crate::log!(
                                    uart,
                                    "power: {} {} {}.{:03} A tripped, back in range -- limit -> {}.{:03}",
                                    PORTS[channel], limit.name(),
                                    threshold / 1000, (threshold % 1000).unsigned_abs(),
                                    settled / 1000, (settled % 1000).unsigned_abs()
                                ),
                            }
                        } else {
                            match excursion {
                                Some(by) => crate::log!(
                                    uart,
                                    "power: {} {} {}.{:03} V, saw {}.{:03} V -- {} by {}.{:03} V, limit -> {}.{:03}",
                                    PORTS[channel], limit.name(),
                                    threshold / 1000, (threshold % 1000).unsigned_abs(),
                                    now.unwrap_or(0) / 1000, (now.unwrap_or(0) % 1000).unsigned_abs(),
                                    if over { "over" } else { "under" },
                                    by / 1000, (by % 1000).unsigned_abs(),
                                    settled / 1000, (settled % 1000).unsigned_abs()
                                ),
                                None => crate::log!(
                                    uart,
                                    "power: {} {} {}.{:03} V tripped, back in range -- limit -> {}.{:03}",
                                    PORTS[channel], limit.name(),
                                    threshold / 1000, (threshold % 1000).unsigned_abs(),
                                    settled / 1000, (settled % 1000).unsigned_abs()
                                ),
                            }
                        }
                    }
                }
                if fired == 0 {
                    // The pin asserted and the status word says nothing did it.
                    // Worth a line: it means the alert came from a source this
                    // firmware has not armed -- an accumulator overflow, or the
                    // conversion-complete pulse (#278) -- or that something else
                    // read the register first, which would be a second owner.
                    crate::log!(uart, "power: alert with an empty status word");
                }
            }
            Err(error) => {
                self.failures = self.failures.saturating_add(1);
                crate::log!(uart, "power: alert unreadable: {}", error.as_str());
            }
        }
        // Discard whatever latched WHILE the limits were being moved.
        //
        // The back-off writes a new threshold, but the part may already have
        // latched a trip against the old one -- the comparison runs at 1024 SPS
        // and the write takes an I2C transaction. That stale latch arrives as a
        // second alert for one event, and because the threshold has already
        // moved past the reading it reports as "tripped, back in range" and
        // steps the limit a second time:
        //
        //     power: aux ov 1.799 V, saw 5.137 V -- over by 3.338 V, limit -> 5.637
        //     power: aux ov 5.636 V tripped, back in range -- limit -> 6.136
        //
        // One read, discarded. If the condition is genuinely still true the very
        // next conversion re-asserts and the next service reports it properly --
        // section 5.16.8: "the ALERT function will reassert, if the next
        // converted sample detects the limit is exceeded".
        if moved_any {
            let _ = self.alert_service(bus);
        }
        crate::irq::resume_power_alert();
    }

    /// One REFRESH cycle: the work, with no decision about when.
    ///
    /// **The sole owner of the PAC1954's REFRESH cycle.** Everything else asks
    /// [`Monitor::latest`], which touches nothing.
    ///
    /// There is one caller and it is `power_refresh` in `src/rtic_app.rs`. The
    /// interval check that used to live beside this, in a `poll` the main loop
    /// ran on every turn, is gone with the superloop (#245): when this runs is
    /// the tick's decision now, and this function does not have an opinion about
    /// it.
    ///
    /// Normal context, and still allowed to print and to spin on I2C -- this is
    /// a task at a priority, not a handler.
    ///
    /// `bus` is an `Option` rather than this function reaching for
    /// `target::BOARD` itself, so that a target with no board is a missing
    /// resource rather than a `#[cfg]` -- and so the clock is still read on every
    /// target. See below.
    pub fn service(&mut self, uart: &mut Uart, bus: Option<&mut Bus>) {
        // When this run happened, which is what the NEXT one measures its
        // interval against. Set here rather than in either caller, so the two
        // dispatchers cannot disagree about when a poll counts as having
        // started.
        self.last = clock::now();

        // The interval elapsed, so this turn is busy -- and the gap since the
        // last poll is the jitter figure `stats` reports. Above the bus check
        // for the same reason the clock read is: it happens on every target.
        crate::metrics::polled();

        // The clock is read on EVERY target, and only the bus access is skipped
        // where there is no bus. That is deliberate: `scripts/soc_test.py` runs
        // this loop under QEMU, so the `time` CSR read above is exercised by the
        // gate rather than only on hardware. A CSR that trapped would be an
        // illegal-instruction exception in the main loop, which the gate would
        // catch in seconds instead of a reconfigure finding it in minutes.
        let bus = match bus {
            Some(bus) => bus,
            None => return,
        };

        // The ALERT is NOT serviced here any more. It has its own task, released
        // by the pin (#285) -- see `rtic_app::power_alert`. Servicing it inside
        // this cycle made alert latency equal to the poll period, which #286
        // makes settable and defaults to off.

        // How long one dispatch's bus work actually takes -- the number that
        // decides whether a requested rate is achievable (#286). `sched`
        // measures the GAP between polls and never the DURATION of one, so
        // until now nothing could say.
        //
        // Around `transfer` only: the clock read and the metrics above happen on
        // every target, and the question is what the I2C costs.
        let started = clock::now();
        let outcome = self.transfer(bus);
        record_cycle(started.elapsed(clock::now()));

        let sample = match outcome {
            Ok(sample) => sample,
            Err(error) => {
                self.failures = self.failures.saturating_add(1);
                // Announced once, on the transition from working to not. A bus
                // fault repeats every 50 ms and a monitor that said so every
                // time would bury the shell -- which is the same argument as
                // the change threshold, applied to failure.
                if self.live {
                    self.live = false;
                    crate::log!(
                        uart,
                        "power: monitor unreachable: {} during {}",
                        error.as_str(),
                        self.phase
                    );
                }
                return;
            }
        };

        if !self.live {
            self.live = true;
            self.failures = 0;
            crate::log!(uart, "power: monitor responding");
        }

        // The first pass has read the registers as they were before any REFRESH.
        // Not a measurement, so it is neither announced nor cached. See
        // `refresh_at`.
        let sample = match sample {
            Some(sample) => sample,
            None => return,
        };

        // Publish BEFORE deciding what to announce. The announcement rules are
        // about what is worth a console line; `power` asks a different question
        // and must get the answer whether or not this sample was newsworthy.
        self.sample = Some(sample);
        let readings = sample.readings;

        for channel in 0..4 {
            let current = readings[channel].current_ua;
            // A floor suppresses near-zero offset, not one direction of flow.
            // A real negative current is connected by the same magnitude rule
            // as an equal positive current.
            let state = if current.unsigned_abs() < self.floor[channel] {
                State::Disconnected
            } else {
                State::Connected(current)
            };

            let announce = match (self.state[channel], state) {
                // Nothing said about this port yet: say what it is, unless it is
                // simply absent, which is not news.
                (None, State::Connected(_)) => true,
                (None, State::Disconnected) => false,
                // Crossing the floor either way is a connect or a disconnect,
                // and both are events regardless of how small the step was.
                (Some(State::Disconnected), State::Connected(_)) => true,
                (Some(State::Connected(_)), State::Disconnected) => true,
                // Both connected: the threshold decides.
                (Some(State::Connected(announced)), State::Connected(current)) => {
                    current.abs_diff(announced) >= CHANGE_UA
                }
                (Some(State::Disconnected), State::Disconnected) => false,
            };

            if announce {
                // A change nobody asked to see, so it is a log line and carries
                // the time. The `power` command's rows do not -- see the table
                // in `src/log.rs`.
                report(
                    uart,
                    &crate::log::now(),
                    channel,
                    &readings[channel],
                    state != State::Disconnected,
                );
                self.state[channel] = Some(state);
            }
        }
    }
}

/// How old a cached value is, for anything in this firmware that keeps one.
///
/// Here rather than in `src/clock.rs`, because what makes the answer truthful is
/// [`AGE_LIMIT_MS`] -- a bound chosen against the counter's 71.6 second wrap --
/// and that belongs with the interval it was reasoned about. `src/board.rs` uses
/// it for the Type-C controllers' cached state, which is dated the same way and
/// must be reported in the same words.
pub fn age_of(at: Option<Instant>) -> Age {
    let at = match at {
        Some(at) => at,
        None => return Age::Never,
    };
    let ticks = at.elapsed(clock::now());
    if ticks >= clock::millis(AGE_LIMIT_MS) {
        Age::Older
    } else {
        Age::Millis(clock::to_millis(ticks))
    }
}

/// One line about one channel, in the same shape whether it came from a change
/// event or from the `power` command -- so a log line and a manual reading can
/// be compared without translating between two formats.
///
/// `lead` is what goes in front: a timestamp when the monitor noticed a change
/// on its own, and a space when the reader asked. One parameter rather than two
/// functions, because the columns after it must stay identical -- two format
/// strings for one table is how they drift apart.
pub fn report(
    uart: &mut Uart,
    lead: &dyn core::fmt::Display,
    channel: usize,
    reading: &Reading,
    connected: bool,
) {
    let magnitude = reading.current_ua.unsigned_abs();
    let _ = writeln!(
        uart,
        "{} {:8} {:2}.{:03} V  {}{}.{:03} mA  {}",
        lead,
        PORTS[channel],
        reading.bus_mv / 1000,
        reading.bus_mv % 1000,
        if reading.current_ua < 0 { "-" } else { " " },
        magnitude / 1000,
        magnitude % 1000,
        if connected {
            "connected"
        } else {
            "disconnected"
        }
    );
}
