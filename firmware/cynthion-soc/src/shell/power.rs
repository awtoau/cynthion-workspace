//! The `power` command family: status table, rate, limits, samples, bracket.
//!
//! Separate from `power.rs`, which is the driver. The split is the same one
//! `bench`/`memory` already use -- a driver that a command renders, not a driver
//! that prints.
//!
//! Moved out of `main.rs` unchanged (#296).

use core::fmt::Write;

use crate::shell::parse::{
    as_str, parse_decimal, parse_limit, parse_port, parse_signed, trim, FixedWriter,
};
use crate::uart::Uart;
use crate::shell::console::board_absent;
use crate::{ clock, power, target, Devices};

/// `power`, or `power floor <port> <mA>`.
///
/// **Prints the poller's cached sample and touches no bus.** The 50 ms poll
/// already reads every channel and keeps the values to compute the 100 mA delta,
/// so the data is here; issuing a second read would only add a caller that can
/// land inside the poll's REFRESH window, which is issue #123 and is what the
/// deleted 2 ms retry was papering over.
///
/// All four rails regardless of the change threshold, which is unchanged: the
/// threshold keeps the background log readable, and a command that inherited it
/// could not answer "what is it now".
///
/// Ports are named, never numbered, for the same reason the LEDs are: the PAC's
/// channel order is not the port order anyone would guess (channel 1 is
/// TARGET_A), and "channel 3" in a bug report means nothing to the person
/// holding the board.
/// `power detect [on|off]` -- plug detection in the part, not in the poll (#285).
///
/// The poll is off by default (#286), and connection state used to come from it.
/// This arms a current limit at the floor instead, so the part reports a crossing
/// against every sample at 1024 SPS -- faster than the 50 ms poll it replaces.
///
/// Takes one current limit per port, in whichever direction the port can move,
/// which is why it is opt-in: a protection threshold on the same direction and
/// channel would be overwritten.
fn detect_command(uart: &mut Uart, arg: &[u8], devices: &mut Devices) {
    let Some(bus) = devices.bus.as_mut() else {
        return board_absent(uart);
    };
    match arg {
        b"on" | b"off" => {
            let on = arg == b"on";
            if on {
                if let Err(error) = devices.power.alert_pin_enable(bus) {
                    let _ = writeln!(uart, "detect: {}", error.as_str());
                    return;
                }
            }
            if let Err(error) = devices.power.detect_set(bus, on) {
                let _ = writeln!(uart, "detect: {}", error.as_str());
                return;
            }
        }
        b"" => {}
        _ => {
            let _ = writeln!(uart, "usage: pac1954 detect [on|off]");
            return;
        }
    }
    if devices.power.detecting() {
        let _ = writeln!(
            uart,
            "detect   on   a current limit at each port's floor; the part \
             compares every sample"
        );
    } else {
        let _ = writeln!(
            uart,
            "detect   off  connect and disconnect are noticed only while the \
             poll runs"
        );
    }
}

/// `power rate [<ms>|off]` -- how often the rails are sampled (#286).
///
/// Reports the rate, the measured cost of one cycle, and whether the two are
/// compatible. A rate the poll cannot achieve would free-run and the transcript
/// would claim a rate that is not happening.
///
/// **The ALERT does not depend on this.** Limits are compared against every
/// sample inside the part, so excursions are reported with the poll off.
pub(crate) fn rate_command(uart: &mut Uart, arg: &[u8]) {
    if !arg.is_empty() {
        let asked = if arg == b"off" {
            Some(power::RATE_OFF)
        } else {
            parse_decimal(arg)
        };
        let Some(asked) = asked else {
            let _ = writeln!(
                uart,
                "usage: pac1954 rate [<ms>|off]   min {} ms",
                power::MIN_INTERVAL_MS
            );
            return;
        };
        // Report what was ACTUALLY set when it differs from what was asked. A
        // clamp nobody is told about is a transcript claiming a rate that is
        // not running.
        let set = power::set_interval_ms(asked);
        if set != asked {
            let _ = writeln!(
                uart,
                "clamped {} -> {} ms: the part samples at 1024 SPS (976 us), so \
                 nothing faster exists to read",
                asked, set
            );
        }
    }

    let rate = power::interval_ms();
    match rate {
        power::RATE_OFF => {
            let _ = writeln!(
                uart,
                "rate     off      the ALERT still reports excursions -- it \
                 compares every sample in the part"
            );
        }
        ms => {
            let _ = writeln!(uart, "rate     {} ms", ms);
        }
    }

    let (worst, last) = power::cycle_ticks();
    if worst == 0 {
        let _ = writeln!(uart, "cycle    not yet measured  needs one poll");
    } else {
        let _ = writeln!(
            uart,
            "cycle    worst {} us  last {} us   the I2C of ONE dispatch",
            clock::to_micros(worst),
            clock::to_micros(last),
        );
        // A cycle is TWO dispatches with the part's 1 ms settling between them
        // (DS20006539B 5.2), so a complete measurement can never take less than
        // 1 ms however fast the bus is. That, not the I2C above, is what bounds
        // the rate.
        let worst_us = clock::to_micros(worst);
        if rate != power::RATE_OFF && rate == power::MIN_INTERVAL_MS {
            let _ = writeln!(
                uart,
                "         at {} ms the cycle is back-to-back: 1 ms settling \
                 plus {} us of bus, no idle",
                rate, worst_us
            );
        }
    }
}

pub(crate) fn command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    let rest = trim(rest);

    // BEFORE the board check, deliberately. The rate is a property of the tick,
    // not of the bus: `rtic_app::tick` reads it whether or not a monitor answers,
    // and the scheduler checks in `soc_test.py` run under QEMU where there is no
    // board at all. Behind the guard it returned "no board peripherals" on a
    // target whose scheduler was running perfectly.
    if rest.starts_with(b"rate") {
        return rate_command(uart, trim(&rest[b"rate".len()..]));
    }

    if devices.bus.is_none() {
        return board_absent(uart);
    }

    // `power` bare lists the family; `power status` is the reading. The table
    // used to be what a bare `power` printed, which made it the only family
    // whose bare form did work rather than describing itself.
    if rest.is_empty() {
        return crate::shell::list_family(uart, "pac1954");
    }
    // `status` falls through to the table below, which is where it already
    // lived -- it was simply reached by typing nothing.
    let rest: &[u8] = if rest == b"status" { b"" } else { rest };
    if rest.starts_with(b"detect") {
        return detect_command(uart, trim(&rest[b"detect".len()..]), devices);
    }
    // THE DESTRUCTIVE VERB. `PWRDN#` is this part's only hardware reset and it
    // loses the accumulators; `init pac1954` is the non-destructive one. See
    // `power::Monitor::reset`.
    if rest == b"reset" {
        return pac1954_reset(uart, devices);
    }
    if rest.starts_with(b"floor") {
        let rest = trim(&rest[b"floor".len()..]);
        let (name, value) = match rest.iter().position(|&b| b == b' ') {
            Some(i) => (&rest[..i], trim(&rest[i + 1..])),
            None => (rest, &rest[..0]),
        };
        let channel = match power::PORTS.iter().position(|&p| p.as_bytes() == name) {
            Some(channel) => channel,
            None => {
                let _ = writeln!(
                    uart,
                    "no port of that name; they are \
                                        target_a, target_c, aux, control"
                );
                return;
            }
        };
        match parse_decimal(value) {
            // A floor is a magnitude. The bipolar full-scale current is 5 A,
            // so anything higher can never be crossed.
            Some(milliamps) if milliamps <= 5000 => {
                devices.power.set_floor(channel, milliamps * 1000)
            }
            _ => {
                let _ = writeln!(
                    uart,
                    "usage: pac1954 floor <port> <mA>  \
                                        (0..5000)"
                );
                return;
            }
        }
    } else if rest.starts_with(b"alert") || rest.starts_with(b"limit")
        || rest.starts_with(b"samples") || rest.starts_with(b"bracket")
    {
        return power_alert_command(uart, rest, devices);
    } else if !rest.is_empty() {
        let _ = writeln!(uart, "usage: pac1954 [floor <port> <mA>]");
        let _ = writeln!(uart, "       power alert [on|off]");
        let _ = writeln!(uart, "       power limit <ov|oc|uv|uc> <port> <mV|mA>");
        let _ = writeln!(uart, "       power samples <ov|oc|uv|uc> <port> <1|4|8|16>");
        let _ = writeln!(uart, "       power bracket <port> <+/-mA> <+/-mV>");
        return;
    }

    // The header carries the age, because every number under it is that old.
    //
    // Not decoration. A poller that has stopped leaves four voltages that are
    // individually plausible and jointly a lie, and there is nothing in them to
    // say so -- which is the same shape of failure as a stale bus select. The
    // age is the only thing on this screen that can contradict them.
    // POWERED DOWN FIRST, because every number below it is meaningless when it
    // is set -- the part is not converting. It was a row under `led`, which is
    // where nobody reading a power table would look for it.
    if let Some(board) = crate::target::BOARD {
        if crate::gpio::Gpio::new(board.gpio).power_monitor_down() {
            let _ = writeln!(
                uart,
                "  PWRDN asserted -- the part is powered down, readings below are stale"
            );
        }
    }

    let rate = power::interval_ms();
    let _ = write!(uart, "power @{:02x}  poll ", power::ADDRESS);
    match rate {
        power::RATE_OFF => {
            let _ = write!(uart, "off");
        }
        ms => {
            let _ = write!(uart, "{} ms", ms);
        }
    }
    let _ = write!(uart, "  change {} mA  ", power::CHANGE_UA / 1000);

    // An age that keeps growing is a FAULT when the poll is running and the
    // expected state when it is off. Saying which costs one branch and stops an
    // off poll reading as a stuck one -- the reading below is simply the last
    // one taken, and that is a different statement from "the poller died".
    match (devices.power.age(), rate) {
        (power::Age::Millis(ms), power::RATE_OFF) => {
            let _ = writeln!(uart, "last sample {} ms ago (poll off)", ms);
        }
        (power::Age::Millis(ms), _) => {
            let _ = writeln!(uart, "sampled {} ms ago", ms);
        }
        (power::Age::Older, power::RATE_OFF) => {
            let _ = writeln!(
                uart,
                "last sample over {} s ago -- expected, the poll is off",
                power::AGE_LIMIT_MS / 1000
            );
        }
        (power::Age::Older, _) => {
            let _ = writeln!(
                uart,
                "sampled OVER {} s ago -- the poll has stopped",
                power::AGE_LIMIT_MS / 1000
            );
        }
        (power::Age::Never, _) => {
            let _ = writeln!(uart, "NO SAMPLE YET");
        }
    }

    // ONE row per port, with the reading and every setting that governs it.
    //
    // The measurement, its four limits, the debounce, the floor and whether the
    // alert is armed all describe the same port, and they used to be in three
    // places: `power` for the reading, a second block for the floor, and
    // `power alert` for the limits. A reader asking "what is aux doing and what
    // will it tell me about" had to hold three tables in their head.
    //
    // Limits are read FROM THE PART. `ALERT_STATUS` deliberately is not -- it is
    // read-to-clear and belongs to the service path, so `fired` comes from what
    // that path recorded. Reading it here is the defect this table was built
    // after.
    // NO BUS. `power` prints what the poller and the setters recorded, which is
    // the #123 rule and what `soc_i2c_owner_sim.py` asserts: a second reader
    // lands inside the 1 ms window after a refresh and reports a fault on a
    // working bus. The limits are still the part's -- they are written by the
    // code that programs them, including the auto-backoff -- they are simply
    // not fetched by a display command.
    //
    // `power alert` is the authoritative view and does read the part.
    let enabled = devices.power.alert_enable_cached();
    let fired = devices.power.alert_history();

    // The header uses the SAME width specifiers as the rows below. Written out
    // by hand it drifted by one column the first time a field changed width,
    // and a misaligned table is read wrong rather than read as broken.
    let _ = writeln!(
        uart,
        "  {:9}  {:>6} {:>7} {:>7}   {:>6} {:>7} {:>7}  {:>8}  {:>5}  {:>5}  {:>6}",
        "port", "volts", "uv", "ov", "amps", "uc", "oc", "debounce", "armed",
        "fired", "floor"
    );

    let sample = devices.power.latest();
    for channel in 0..4 {
        let floor = devices.power.floor(channel);

        // Each limit, as the part holds it. A zero limit is not a threshold
        // anybody set -- it is the POR value -- so it prints as `--` rather than
        // as `0.000`, which would read as a limit of zero volts.
        let mut cell = [[0u8; 9]; 4];
        let mut armed = [b'-'; 4];
        let mut hit = [b'-'; 4];
        // Under before over, so each pair reads low-to-high like the range it
        // describes. The flag columns below index into this order and the
        // header names it, so the three cannot disagree.
        for (slot, limit) in [
            power::Limit::UnderVoltage,
            power::Limit::OverVoltage,
            power::Limit::UnderCurrent,
            power::Limit::OverCurrent,
        ]
        .iter()
        .enumerate()
        {
            let raw = devices.power.alert_limit_cached(*limit, channel);
            // `limit_code_to_mv`, not `bus_mv`: limit registers are two's
            // complement at 976.6 uV/code, the VBUS measurement is unipolar at
            // 488.3. This column showed every voltage threshold at half.
            let value = if limit.is_current() {
                power::current_ua(raw) / 1000
            } else {
                power::limit_code_to_mv(raw)
            };
            let text = &mut cell[slot];
            if raw == 0 {
                text[..2].copy_from_slice(b"--");
            } else {
                let mut buffer = FixedWriter::new(text);
                let _ = write!(
                    buffer,
                    "{}{}.{:03}",
                    if value < 0 { "-" } else { "" },
                    (value / 1000).abs(),
                    (value % 1000).unsigned_abs()
                );
            }
            // POSITIONAL, not initials. The first letter of each limit name
            // gives `o u o u` for ov/uv/oc/uc, which says nothing about which
            // slot is which -- the header already fixes the order, so a slot
            // only has to say yes or no.
            let bit = limit.bit(channel);
            if enabled & bit != 0 {
                armed[slot] = b'*';
            }
            if fired & bit != 0 {
                hit[slot] = b'!';
            }
        }

        // The debounce, which is per limit but is one value in practice. Shown
        // as the OV one with a `*` when the four disagree, rather than four more
        // columns for a number nobody sets differently per limit.
        let samples = devices
            .power
            .alert_samples_cached(power::Limit::UnderVoltage, channel);

        match sample {
            Some(sample) => {
                let reading = sample.readings[channel];
                // AMPS, because the header says amps. `current_ua / 1000` is
                // milliamps and printing it under an "amps" heading is a
                // thousandfold error that looks like a plausible current.
                let magnitude = reading.current_ua.unsigned_abs();
                let _ = write!(
                    uart,
                    "  {:9}  {:2}.{:03} {:>7} {:>7}   {}{}.{:03}",
                    power::PORTS[channel],
                    reading.bus_mv / 1000,
                    reading.bus_mv % 1000,
                    as_str(&cell[0]),
                    as_str(&cell[1]),
                    if reading.current_ua < 0 { "-" } else { " " },
                    // ROUNDED, not truncated. -762 uA truncates to `-0.000`,
                    // which is a minus sign in front of nothing and reads as a
                    // formatting bug rather than as a current below the
                    // display's resolution.
                    (magnitude + 500) / 1_000_000,
                    (((magnitude + 500) / 1000) % 1000),
                );
            }
            None => {
                let _ = write!(
                    uart,
                    "  {:9}      -- {:>7} {:>7}       --",
                    power::PORTS[channel],
                    as_str(&cell[0]),
                    as_str(&cell[1])
                );
            }
        }
        let _ = writeln!(
            uart,
            " {:>7} {:>7}  {:>8}  {:>5}  {:>5}  {:>2}.{:03}",
            as_str(&cell[2]),
            as_str(&cell[3]),
            samples,
            as_str(&armed),
            as_str(&hit),
            floor / 1_000_000,
            (floor / 1000) % 1000
        );
    }
    if sample.is_none() {
        // Two polls after reset there is genuinely nothing to say -- one to
        // issue a REFRESH_V and one to read what it latched.
        let _ = writeln!(
            uart,
            "  no sample yet; last phase {}",
            devices.power.phase()
        );
    }
    let _ = writeln!(uart);
    let _ = writeln!(
        uart,
        "  uv ov        under- and over-VOLTAGE limits, in volts\n\
         \x20 uc oc        under- and over-CURRENT limits, in amps\n\
         \x20 debounce     consecutive out-of-range samples before ALERT asserts \
         (1, 4, 8 or 16)\n\
         \x20 armed/fired  one flag per limit\n\
         \x20 floor        below this current a port reads as disconnected\n\
         \x20 `--`         no limit set"
    );
    let _ = writeln!(uart);

    if devices.power.failures > 0 {
        let _ = writeln!(
            uart,
            "  {} failed poll(s) since the last good one",
            devices.power.failures
        );
    }
}

/// `power alert`, `power limit`, `power samples`, `power bracket` -- the ALERT
/// configuration, all of it settable and readable from here (#270).
///
/// **These are not firmware constants, deliberately.** The right thresholds are
/// not knowable in advance: whether 3.5 A trips on a real device, or a bracket
/// sits inside the ADC noise, is found by trying it. A constant means a rebuild
/// and a reflash per attempt, and a number nobody is sure about stays unchanged.
///
/// Every read comes FROM THE PART. `power limit` prints what the device holds,
/// not what firmware last wrote -- the difference between "the write was issued"
/// and "the write took", which matters here because a limit written while alerts
/// are enabled is a write whose effect is not what was asked for.
pub(crate) fn power_alert_command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    // Split borrow: the bus and the monitor are separate fields of `Devices`,
    // and the setters below write the monitor's cache while using the bus. An
    // immutable alias to the monitor beside a mutable bus does not compile, and
    // should not -- the cache is state this command changes.
    let Devices { bus, power: monitor, .. } = devices;
    let bus = match bus.as_mut() {
        Some(bus) => bus,
        None => return board_absent(uart),
    };

    let mut field = rest.split(|&b| b == b' ').filter(|f| !f.is_empty());
    let verb = field.next().unwrap_or(b"");

    // ---- power alert [on|off] -------------------------------------------
    if verb == b"alert" {
        let arg = field.next();

        if arg == Some(b"clear".as_slice()) {
            devices.power.alert_forget();
            let _ = writeln!(uart, "alert history cleared");
            return;
        }

        if arg == Some(b"on".as_slice()) || arg == Some(b"off".as_slice()) {
            let want_on = arg == Some(b"on".as_slice());
            if want_on {
                if let Err(error) = monitor.alert_pin_enable(bus) {
                    let _ = writeln!(uart, "alert pin: {}", error.as_str());
                    return;
                }
            }
            // Arm only the limits that have actually been SET.
            //
            // Arming all sixteen would arm the twelve still at their POR zero,
            // and a zero limit is not "no limit" -- it is a threshold every
            // rail is permanently on the wrong side of. The first version did
            // that and produced `target_a oc limit 0 mA now 1 mA over by`, which
            // is a true statement about a limit nobody set.
            //
            // Disarming, by contrast, disarms everything: turning it off should
            // not depend on what happens to be configured.
            let mut count = 0;
            for limit in power::Limit::ALL {
                for channel in 0..4 {
                    let configured = monitor
                        .alert_limit(bus, limit, channel)
                        .map(|raw| raw != 0)
                        .unwrap_or(false);
                    let on = want_on && configured;
                    if want_on && !configured {
                        // Explicitly disarm it, so `power alert on` after a
                        // limit is cleared does not leave a stale arm behind.
                        let _ = monitor.alert_arm(bus, limit, channel, false);
                        continue;
                    }
                    if let Err(error) = monitor.alert_arm(bus, limit, channel, on) {
                        let _ = writeln!(uart, "alert {}: {}", limit.name(), error.as_str());
                        return;
                    }
                    if on {
                        count += 1;
                    }
                }
            }
            if want_on {
                let _ = writeln!(
                    uart,
                    "alerts armed: {} limit(s) with a threshold set",
                    count
                );
                if count == 0 {
                    let _ = writeln!(
                        uart,
                        "  nothing to arm -- set one with `power limit` or \
                         `power bracket` first"
                    );
                }
            } else {
                let _ = writeln!(uart, "alerts disarmed");
            }
            return;
        }

        // Bare `power alert`: the whole picture.
        //
        // `enable` and `routed` are read from the part -- they are plain R/W
        // registers and reading them costs nothing. **`ALERT_STATUS` is NOT**,
        // because it is read-to-clear and belongs to the service path. Reading
        // it here stole events from the handler and made the alert look
        // intermittent; `alert_history` is what the service path recorded.
        let (enabled, routed) = match (monitor.alert_enabled(bus), monitor.alert_routed(bus)) {
            (Ok(e), Ok(r)) => (e, r),
            _ => {
                let _ = writeln!(uart, "alert: the monitor did not answer");
                return;
            }
        };
        let fired = monitor.alert_history();
        let _ = writeln!(
            uart,
            "alert  enable {:06x}  routed {:06x}  fired {:06x}  (since boot)",
            enabled, routed, fired
        );
        // No per-port table here. `power` shows every limit, its debounce and
        // whether it is armed, one row per port -- repeating it under a second
        // command is two places to read the same thing and two places for it to
        // go stale.
        //
        // What this command adds is the RAW masks and the authoritative read:
        // `power` shows the cache, this reads `ALERT_ENABLE` and `GPIO_ALERT2`
        // from the part, so the two disagreeing is itself the finding.
        let _ = writeln!(
            uart,
            "  bit order, high to low: oc1-4 uc1-4  ov1-4 uv1-4  op1-4 accovf acccount cc"
        );
        let _ = writeln!(uart, "  `power` has the per-port limits; this is the raw state");
        let _ = writeln!(uart, "  power alert clear   forget what has fired");
        let _ = writeln!(uart, "  power alert on|off  arm every limit that has a threshold");
        return;
    }

    // ---- power limit <kind> <port> <value> --------------------------------
    if verb == b"limit" {
        let (limit, channel, value) = match (
            field.next().and_then(parse_limit),
            field.next().and_then(parse_port),
            field.next().and_then(parse_signed),
        ) {
            (Some(l), Some(c), Some(v)) => (l, c, v),
            _ => {
                let _ = writeln!(
                    uart,
                    "usage: pac1954 limit <ov|oc|uv|uc> <port> <mV|mA>"
                );
                return;
            }
        };
        let raw = if limit.is_current() {
            power::ua_to_code(value.saturating_mul(1000))
        } else {
            // The LIMIT scale, not the measurement scale -- they differ by two.
            power::mv_to_limit_code(value)
        };
        if let Err(error) = monitor.alert_set_limit(bus, limit, channel, raw) {
            let _ = writeln!(uart, "limit: {}", error.as_str());
            return;
        }
        // What was ACTUALLY programmed, read back and converted. A request
        // rarely lands exactly, and a threshold rounded silently and echoed as
        // the requested value is the lie this read-back exists to prevent.
        //
        // `limit_code_to_mv`, NOT `bus_mv`. The limit registers are two's
        // complement at 976.6 uV/code; the VBUS measurement is unipolar at
        // 488.3. Converting a limit with the measurement scale reported every
        // voltage threshold at HALF what was programmed -- the write above says
        // "they differ by two" and this line used the other one. The limit was
        // correct in the part throughout; only the echo was wrong, which is the
        // worst shape for it: it looks exactly like the scale bug that was fixed
        // in #270 and sends the reader back to already-correct code.
        let back = monitor.alert_limit(bus, limit, channel).unwrap_or(0);
        let _ = writeln!(
            uart,
            "{} {} = {} {} (asked {}, code {:04x})",
            limit.name(),
            power::PORTS[channel],
            if limit.is_current() {
                power::current_ua(back) / 1000
            } else {
                power::limit_code_to_mv(back)
            },
            if limit.is_current() { "mA" } else { "mV" },
            value,
            back
        );
        return;
    }

    // ---- power samples <kind> <port> <n> ----------------------------------
    if verb == b"samples" {
        let (limit, channel, want) = match (
            field.next().and_then(parse_limit),
            field.next().and_then(parse_port),
            field.next().and_then(parse_decimal),
        ) {
            (Some(l), Some(c), Some(n)) => (l, c, n),
            _ => {
                let _ = writeln!(
                    uart,
                    "usage: pac1954 samples <ov|oc|uv|uc> <port> <1|4|8|16>"
                );
                return;
            }
        };
        match monitor.alert_set_nsamples(bus, limit, channel, want) {
            Ok(actual) => {
                let _ = writeln!(
                    uart,
                    "{} {} samples = {} (asked {}), {} ms at 1024 SPS",
                    limit.name(),
                    power::PORTS[channel],
                    actual,
                    want,
                    actual * 1000 / 1024
                );
            }
            Err(error) => {
                let _ = writeln!(uart, "samples: {}", error.as_str());
            }
        }
        return;
    }

    // ---- power bracket <port> <+/-mA> <+/-mV> -----------------------------
    //
    // The change detector. The part has no "value changed" alert; a tight
    // bracket around the present reading IS one, and this is the command that
    // makes finding the noise floor a matter of typing rather than rebuilding.
    if verb == b"bracket" {
        let (channel, d_ma, d_mv) = match (
            field.next().and_then(parse_port),
            field.next().and_then(parse_decimal),
            field.next().and_then(parse_decimal),
        ) {
            (Some(c), Some(a), Some(v)) => (c, a as i32, v as i32),
            _ => {
                let _ = writeln!(uart, "usage: pac1954 bracket <port> <+/-mA> <+/-mV>");
                return;
            }
        };
        let sample = match monitor.latest() {
            Some(sample) => sample,
            None => {
                let _ = writeln!(uart, "bracket: no sample yet");
                return;
            }
        };
        let reading = sample.readings[channel];
        let ma = reading.current_ua / 1000;
        let mv = reading.bus_mv as i32;

        let plan = [
            (power::Limit::OverCurrent, power::ua_to_code((ma + d_ma) * 1000)),
            (power::Limit::UnderCurrent, power::ua_to_code((ma - d_ma) * 1000)),
            (power::Limit::OverVoltage, power::mv_to_limit_code(mv + d_mv)),
            (power::Limit::UnderVoltage, power::mv_to_limit_code(mv - d_mv)),
        ];
        for (limit, raw) in plan {
            if monitor.alert_set_limit(bus, limit, channel, raw).is_err()
                || monitor.alert_set_nsamples(bus, limit, channel, 1).is_err()
                || monitor.alert_arm(bus, limit, channel, true).is_err()
            {
                let _ = writeln!(uart, "bracket: the monitor did not answer");
                return;
            }
        }
        if let Err(error) = monitor.alert_pin_enable(bus) {
            let _ = writeln!(uart, "alert pin: {}", error.as_str());
            return;
        }
        let _ = writeln!(
            uart,
            "bracket {}  current {}..{} mA  voltage {}..{} mV  samples 1",
            power::PORTS[channel],
            ma - d_ma,
            ma + d_ma,
            mv - d_mv,
            mv + d_mv
        );
        let _ = writeln!(uart, "  any excursion asserts ALERT on src {}",
                         cynthion_soc_pac::base::BOARD_I2C_MUX_POWER_ALERT_IRQ);
        return;
    }
}


/// `pac1954 reset` -- **DESTRUCTIVE**, and deliberately its own command.
///
/// `PWRDN#` is this part's only hardware reset and it loses the accumulators,
/// which are the only thing that can see an event between two 50 ms samples.
/// `init pac1954` is the non-destructive establishment; this is what you reach
/// for when the part is genuinely wedged, and making it a separate verb is what
/// makes it safe to have at all. See `power::Monitor::reset`.
fn pac1954_reset(uart: &mut Uart, devices: &mut Devices) {
    let Some(board) = target::BOARD else {
        return board_absent(uart);
    };
    let gpio = crate::gpio::Gpio::new(board.gpio);
    let Some(bus) = devices.bus.as_mut() else {
        return board_absent(uart);
    };
    let _ = writeln!(uart, "pac1954 reset: PWRDN#, so the accumulators are lost");
    match devices.power.reset(&gpio, bus) {
        Ok(report) => {
            let _ = writeln!(
                uart,
                "  re-established: fsr {:04x} ctrl {:04x} read back  {}",
                report.fsr,
                report.ctrl,
                if report.established() { "ok" } else { "NOT WHAT IT WAS GIVEN" }
            );
        }
        Err(error) => {
            let _ = writeln!(uart, "  did not come back: {}", error.as_str());
        }
    }
}
