//! The PAC1954-1 power monitor: four rails, polled, reported when they change.
//!
//! The part is a four-channel bus-voltage and shunt-current monitor at I2C
//! `0x10` on the `power_monitor` bus. The conversion arithmetic and the channel
//! map already existed in Python (`ecp5-test/power_monitor/registers.py`, driven
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

use crate::clock::{self, Instant};
use crate::i2c::{self, I2c};
use crate::mux::{self, Mux};
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

/// One channel's reading, in the units the shell prints.
#[derive(Clone, Copy)]
pub struct Reading {
    pub bus_mv: u32,
    pub current_ua: u32,
}

/// VBUS is a 0-32 V full-scale unsigned 16-bit reading.
///
/// 32 V / 65536 = 488.28125 uV per LSB, so millivolts are `raw * 32000 / 65536`,
/// which reduces to `raw * 125 / 256` EXACTLY -- no rounding constant, no
/// floating point, and no soft-float library linked into a 64 KiB block RAM. The
/// widest intermediate is 65535 * 125 = 8_191_875, which fits a `u32` with room
/// to spare.
///
/// Unsigned is correct only while `NEG_PWR_FSR` (`0x1d`) is at its power-on
/// default of zero, which all four channels were measured to be. Bipolar mode
/// would halve the range and make these two's complement.
fn bus_mv(raw: u16) -> u32 {
    raw as u32 * 125 / 256
}

/// VSENSE is 0-100 mV full scale across a 20 mOhm shunt.
///
/// 0.1 V / 65536 / 0.02 Ohm = 76.2939453125 uA per LSB, and that is `78125 /
/// 1024` exactly, so microamps are `raw * 78125 / 1024` with no approximation.
/// The multiply is done in `u64` because 65535 * 78125 = 5_119_921_875, which
/// overflows a `u32` -- and would do so only at high current, which is precisely
/// when a wrong number matters. Full scale is 5 A.
fn current_ua(raw: u16) -> u32 {
    ((raw as u64 * 78125) >> 10) as u32
}

/// What the monitor decided about one channel this poll.
#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    /// Below the floor. Nothing plugged in, or nothing drawing.
    Disconnected,
    /// Above the floor, last announced at this current in microamps.
    Connected(u32),
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
    /// Cleared until one REFRESH has been issued and its sample read back.
    ///
    /// The first read after reset returns whatever the measurement registers
    /// held before any REFRESH -- the part only updates them on command
    /// (DS20006539B 5.2), so before the first one they are not a measurement of
    /// anything. Announcing that would put a fabricated connect event on the
    /// console at every boot.
    primed: bool,
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
            primed: false,
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

    /// Sample now, whatever the interval says. Used by the `power` command.
    ///
    /// Returns the four readings in channel order, or the bus error that stopped
    /// it. Does not touch the announcement state: reading on demand must not
    /// swallow the change event that would otherwise have been printed.
    pub fn read(&mut self, bus: &I2c, mux: &Mux)
        -> Result<[Reading; 4], i2c::Error>
    {
        match self.read_once(bus, mux) {
            // The one collision this driver can have with itself.
            //
            // The part is unavailable for 1 ms after a REFRESH (DS20006539B
            // 5.2) and answers a read inside that window by acknowledging its
            // address and then NACKing the register pointer. The background
            // poll issues a REFRESH every 50 ms, so a read requested from the
            // shell has about a 2% chance of arriving in it -- an intermittent
            // "no acknowledge" on a bus that is working perfectly, which is
            // precisely the class of report that costs a day.
            //
            // Waiting it out is the whole fix. 2 ms rather than the datasheet's
            // 1 because the window opened before this call did and the cost of
            // being generous is a millisecond in a command a human typed. A
            // second failure is a real bus fault and is reported as one.
            Err(i2c::Error::NackRegister) => {
                let started = clock::now();
                while started.elapsed(clock::now()) < clock::millis(2) {}
                self.read_once(bus, mux)
            }
            other => other,
        }
    }

    /// One attempt. See [`Monitor::read`] for why there are two.
    fn read_once(&mut self, bus: &I2c, mux: &Mux)
        -> Result<[Reading; 4], i2c::Error>
    {
        // Point the one controller at this bus, every time and without
        // remembering. Two other devices share it -- both FUSB302Bs, both at
        // 0x22 -- so a stale select does not produce an error, it produces a
        // plausible answer from the wrong chip. One uncached byte store against
        // a transfer of milliseconds.
        mux.select(mux::BUS_POWER_MONITOR);

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
        // issued. `poll` handles that by issuing one and reporting nothing on
        // its first pass.
        self.phase = "read";
        let mut raw = [0u8; MEASUREMENT_BYTES];
        bus.read_registers(ADDRESS, REG_VBUS1, &mut raw)?;

        // Ask for the next set. One transaction, all four channels, so nothing
        // can arrive from two different sample instants.
        self.phase = "refresh";
        bus.send_byte(ADDRESS, REG_REFRESH)?;

        let mut readings = [Reading { bus_mv: 0, current_ua: 0 }; 4];
        for channel in 0..4 {
            // Big-endian, high byte first, as every 16-bit register on this part
            // is. Getting this backwards produces values that look like noise on
            // a small reading and like a fault on a large one.
            let vbus = u16::from_be_bytes([raw[channel * 2], raw[channel * 2 + 1]]);
            let vsense = u16::from_be_bytes([raw[8 + channel * 2],
                                             raw[8 + channel * 2 + 1]]);
            readings[channel] = Reading {
                bus_mv: bus_mv(vbus),
                current_ua: current_ua(vsense),
            };
        }
        Ok(readings)
    }

    /// Sample if the interval has elapsed, and report anything worth reporting.
    ///
    /// Called from the main loop with the primary console. This is normal
    /// context, not a handler -- it may print, and it may spin on the I2C bus,
    /// because nothing is waiting on it. An interrupt-driven version would have
    /// to defer both, and buys nothing: a 50 ms period is not a latency anyone
    /// can perceive.
    pub fn poll(&mut self, uart: &mut Uart) {
        let now = clock::now();
        if self.last.elapsed(now) < clock::millis(INTERVAL_MS) {
            return;
        }
        self.last = now;

        // The clock is read on EVERY target, and only the bus access is skipped
        // where there is no bus. That is deliberate: `scripts/soc_test.py` runs
        // this loop under QEMU, so the `time` CSR read above is exercised by the
        // gate rather than only on hardware. A CSR that trapped would be an
        // illegal-instruction exception in the main loop, which the gate would
        // catch in seconds instead of a reconfigure finding it in minutes.
        let board = match crate::target::BOARD {
            Some(board) => board,
            None => return,
        };
        let bus = I2c::new(board.i2c);
        let mux = Mux::new(board.i2c_mux);

        let readings = match self.read(&bus, &mux) {
            Ok(readings) => readings,
            Err(error) => {
                self.failures = self.failures.saturating_add(1);
                // Announced once, on the transition from working to not. A bus
                // fault repeats every 50 ms and a monitor that said so every
                // time would bury the shell -- which is the same argument as
                // the change threshold, applied to failure.
                if self.live {
                    self.live = false;
                    let _ = writeln!(uart, "power: monitor unreachable: {} \
                                     during {}", error.as_str(), self.phase);
                }
                return;
            }
        };

        if !self.live {
            self.live = true;
            self.failures = 0;
            let _ = writeln!(uart, "power: monitor responding");
        }

        // Discard the first sample. See `primed`.
        if !self.primed {
            self.primed = true;
            return;
        }

        for channel in 0..4 {
            let current = readings[channel].current_ua;
            let state = if current < self.floor[channel] {
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
                (Some(State::Connected(announced)), State::Connected(current)) =>
                    current.abs_diff(announced) >= CHANGE_UA,
                (Some(State::Disconnected), State::Disconnected) => false,
            };

            if announce {
                report(uart, channel, &readings[channel],
                       state != State::Disconnected);
                self.state[channel] = Some(state);
            }
        }
    }
}

/// One line about one channel, in the same shape whether it came from a change
/// event or from the `power` command -- so a log line and a manual reading can
/// be compared without translating between two formats.
pub fn report(uart: &mut Uart, channel: usize, reading: &Reading, connected: bool) {
    let _ = writeln!(uart, "  {:8} {:2}.{:03} V  {:5}.{:03} mA  {}",
                     PORTS[channel],
                     reading.bus_mv / 1000, reading.bus_mv % 1000,
                     reading.current_ua / 1000, reading.current_ua % 1000,
                     if connected { "connected" } else { "disconnected" });
}
