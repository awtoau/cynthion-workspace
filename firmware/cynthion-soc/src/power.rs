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
const REG_REFRESH: u8 = 0x00;

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

/// Below this, a port is reported as disconnected and says nothing.
///
/// 10 mA. An unplugged rail measures 0.76-0.92 mA on this board -- ADC offset
/// near zero, not leakage -- and the smallest connected draw seen is 29 mA, so
/// 10 mA sits an order of magnitude above the noise and a factor of three below
/// anything real. Per port and changeable at runtime (`power floor`), because a
/// port with a deliberately tiny load is a legitimate thing to want to watch.
pub const DEFAULT_FLOOR_UA: u32 = 10_000;

/// How often the rails are sampled.
///
/// 50 ms, from the issue. It is fast enough that a plug event is reported while
/// the person who caused it is still watching, and slow enough to be free: one
/// poll is a 1-byte write plus a 19-byte read at 80 kHz, about 2.3 ms of bus
/// time, so the bus is idle 95% of the time and the shell never waits behind it.
/// The part's own conversion rate is 1024 SPS, so 20 Hz is not asking for values
/// it has not produced.
pub const INTERVAL_MS: u32 = 50;

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
fn bus_mv(raw: u16) -> u32 {
    raw as u32 * 125 / 256
}

/// VSENSE is -100 to +100 mV across a 20 mOhm shunt.
///
/// DS20006539B section 5.9 and Table 5-2 use a signed 16-bit code and a 2^15
/// denominator in bipolar +/-FSR mode: 5,000,000 uA / 32768 = 78125/512 uA per
/// LSB. Magnitude-first arithmetic keeps equal positive and negative codes
/// symmetric. The nominal full-scale range remains -5 A to +5 A; +/-50 mV
/// FSR/2 is the separate +/-2.5 A mode and is not selected here.
fn current_ua(raw: u16) -> i32 {
    let code = raw as i16 as i32;
    let magnitude = ((code.unsigned_abs() as u64 * 78125) >> 9) as i32;
    if code < 0 {
        -magnitude
    } else {
        magnitude
    }
}

/// The sole call site for the PAC1954's multi-transaction REFRESH cycle.
fn refresh(bus: &mut Bus) -> Result<(), bus::Error> {
    bus.send_byte(BUS_POWER_MONITOR, ADDRESS, REG_REFRESH)
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
            // configuration. Discard it; the normal cycle below issues the
            // second REFRESH whose result is decoded as signed.
            return Ok(None);
        }
        // READ FIRST, THEN REFRESH -- and that order is the whole trick.
        //
        // The datasheet (DS20006539B 5.2) says the readable registers "will be
        // stable within 1 ms from sending the REFRESH command". Reading inside
        // that window is not merely early: this part answers its address and
        // then NACKs the register pointer, which is what "no acknowledge
        // (register pointer)" meant when this driver did REFRESH-then-read.
        //
        // Waiting 1 ms would work and would cost a millisecond of spinning
        // inside a poll that runs twenty times a second. Reading the sample the
        // PREVIOUS poll asked for costs nothing and gives 50 ms of margin
        // instead of zero. Every sample set is still internally coherent -- all
        // eight registers were latched by one REFRESH -- it is simply one
        // interval old, which is exactly what a 50 ms poll means anyway.
        //
        // The consequence to know about: the very first read after reset
        // returns whatever the registers hold before any REFRESH has been
        // issued. That read is discarded below, and the same fact is what makes
        // the age reported by `power` meaningful.
        self.phase = "read";
        let mut raw = [0u8; MEASUREMENT_BYTES];
        bus.read_registers(BUS_POWER_MONITOR, ADDRESS, REG_VBUS1, &mut raw)?;

        // WHEN these bytes were measured: the previous REFRESH, read out before
        // this pass overwrites it. A sample timestamped with the read that
        // fetched it would claim to be one interval younger than it is, which is
        // the sort of small consistent lie nothing ever notices.
        let latched = self.refresh_at;

        // Ask for the next set. One transaction, all four channels, so nothing
        // can arrive from two different sample instants.
        self.phase = "refresh";
        refresh(bus)?;
        // After the Send Byte returns, not before it: the part latches when it
        // receives the command, and the transfer is ~0.2 ms of the 50 ms
        // interval either way.
        self.refresh_at = Some(clock::now());

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

    /// Sample if the interval has elapsed, and report anything worth reporting.
    ///
    /// **The superloop's half of the REFRESH cycle, and only the superloop's.**
    /// It is the poll issue #245 is about: every turn of the main loop reads the
    /// clock, compares, and returns. Under `--features rtic` the comparison is
    /// not made here at all -- `src/timer.rs` makes it on the 1 ms grid and pends
    /// a task -- so this function does not exist in that build and a stray caller
    /// is a compile error rather than two schedulers for one device.
    ///
    /// Called from the main loop with the primary console. This is normal
    /// context, not a handler -- it may print, and it may spin on the I2C bus,
    /// because nothing is waiting on it.
    #[cfg(not(feature = "rtic"))]
    pub fn poll(&mut self, uart: &mut Uart, bus: Option<&mut Bus>) {
        let now = clock::now();
        let elapsed = self.last.elapsed(now);
        if elapsed < clock::millis(INTERVAL_MS) {
            return;
        }

        // How far past its release this run is, which is the figure the `rtic`
        // command compares between the two models. Under the superloop the
        // release was one interval after the last run, so everything beyond the
        // interval is lateness -- and it is charged to whatever held the loop.
        //
        // Note what this does NOT do: `self.last = now` rather than
        // `self.last + INTERVAL`, so the lateness is not carried into the next
        // period. The poller drifts instead of catching up, which is the
        // opposite of what `src/timer.rs` does with `mtimecmp` and is the reason
        // the achieved period and the lateness are two separate numbers.
        crate::sched::released(crate::sched::POWER, elapsed - clock::millis(INTERVAL_MS));

        self.service(uart, bus);
    }

    /// One REFRESH cycle, unconditionally: the work, with no decision about when.
    ///
    /// **The sole owner of the PAC1954's REFRESH cycle.** Everything else asks
    /// [`Monitor::latest`], which touches nothing.
    ///
    /// Split out of [`Monitor::poll`] for #245. The two callers are the two
    /// dispatchers -- the superloop above, and `power_refresh` in
    /// `src/rtic_app.rs` -- and the point of the split is that they share this
    /// body exactly, so a jitter figure taken under one is comparable with the
    /// other. Anything that moved into the caller would be a difference between
    /// the models that is not the model.
    ///
    /// Still normal context under both, and still allowed to print and to spin
    /// on I2C: under RTIC this is a task at a priority, not a handler.
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

        let sample = match self.transfer(bus) {
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
