//! `vbus` -- the distribution switches, as the shell sees it. `vbus.rs` is the
//! driver.

use core::fmt::Write;

use crate::shell::parse::trim;
use crate::uart::Uart;
use crate::vbus::*;
use crate::shell::console::board_absent;
use crate::vbus_gate::{PowerRefusal, TARGET_C};
use crate::{ fusb302, Devices};

/// `vbus` -- pass host power through to a target, or report the switches.
///
/// Terse on purpose. Every string here is `.rodata` in a 63 KiB image whose
/// stack is whatever is left above `.bss`, and a chatty command in this firmware
/// is paid for in stack depth -- see the ASSERT in `memory.x`, which exists
/// because this exact command overran it.
pub(crate) fn command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    // `trim(rest)`, not `core::str::from_utf8(rest).unwrap_or("").trim()`.
    //
    // This was the only place in the firmware that touched `core::str`, and it
    // cost 1,152 bytes of `.text` and 256 of `.rodata`: UTF-8 validation,
    // `<str>::trim`, and the Unicode whitespace table `<str>::trim` consults --
    // to compare four ASCII words on a line the 16550 delivered as bytes.
    // `crate::trim` is 88 bytes and is what every other command already uses.
    let argument = trim(rest);

    if argument.is_empty() || argument == b"status" {
        let _ = writeln!(
            uart,
            "vbus {:02x}  c{} a{} t{}",
            state(),
            is_closed(Source::Control) as u8,
            is_closed(Source::Aux) as u8,
            is_closed(Source::TargetC) as u8,
        );
        return;
    }
    if argument == b"off" {
        crate::portctl::open_all(devices);
        let _ = writeln!(uart, "open");
        return;
    }
    let charge = match argument {
        b"charge" => Some(fusb302::HostCurrent::Default),
        b"charge 1.5" => Some(fusb302::HostCurrent::A1_5),
        b"charge 3" => Some(fusb302::HostCurrent::A3),
        _ => None,
    };
    if let Some(current) = charge {
        return power_on(uart, TARGET_C, current, devices);
    }
    if argument.starts_with(b"input") {
        // Kept as a message rather than dropped to `?`: the verb was documented
        // and used, and "unknown command" would read as a typo (#305).
        let _ = writeln!(uart, "vbus input: removed -- the register reached no pin (#305)");
        return;
    }
    match Source::parse(argument) {
        None => {
            let _ = writeln!(uart, "?");
        }
        Some(source) => match close(source, &mut devices.power, devices.bus.as_mut()) {
            Ok(mv) => {
                let _ = writeln!(uart, "on {} mV", mv);
            }
            Err(error) => vbus_refusal(uart, error),
        },
    }
}

/// `usb <port> power on` and `vbus charge`: the driver acts, this reports.
pub(crate) fn power_on(
    uart: &mut Uart,
    port: usize,
    current: fusb302::HostCurrent,
    devices: &mut Devices,
) {
    if devices.bus.is_none() {
        return board_absent(uart);
    }
    match crate::portctl::power_on(devices, port, current) {
        Ok(on) => {
            let _ = write!(uart, "on {}", on.source.name());
            match on.millivolts {
                Some(mv) => {
                    let _ = write!(uart, " {} mV", mv);
                }
                None => {
                    let _ = write!(uart, " (already)");
                }
            }
            if on.fell_back {
                let _ = write!(uart, " (control refused)");
            }
            let _ = writeln!(uart);
        }
        Err(error) => power_error(uart, error),
    }
}

/// `usb <port> power off`.
pub(crate) fn power_off(uart: &mut Uart, port: usize, devices: &mut Devices) {
    match crate::portctl::power_off(devices, port) {
        Ok(()) => {
            let _ = writeln!(uart, "off");
        }
        Err(error) => power_error(uart, error),
    }
}

/// `usb <port> power`: the register's view, not a measurement.
pub(crate) fn power_status(uart: &mut Uart, port: usize) {
    let _ = writeln!(
        uart,
        "{}  c{} a{} t{}",
        if power_state(port) { "on" } else { "off" },
        is_closed(Source::Control) as u8,
        is_closed(Source::Aux) as u8,
        is_closed(Source::TargetC) as u8,
    );
}

fn power_error(uart: &mut Uart, error: PowerError) {
    match error {
        PowerError::Gate(refusal) => vbus_refusal(uart, refusal),
        PowerError::Cc => {
            let _ = writeln!(uart, "?");
        }
        PowerError::Port(PowerRefusal::NotOutput) => {
            let _ = writeln!(uart, "no: input port");
        }
        PowerError::Port(PowerRefusal::Other(_)) => {
            let _ = writeln!(uart, "no: another source is closed; `vbus off`");
        }
    }
}

pub(crate) fn vbus_refusal(uart: &mut Uart, refusal: Refusal) {
    match refusal {
        Refusal::TooHigh(mv) => {
            let _ = writeln!(uart, "no: {} mV high", mv);
        }
        Refusal::TooLow(mv) => {
            let _ = writeln!(uart, "no: {} mV low", mv);
        }
        Refusal::Uv(mv) => {
            let _ = writeln!(uart, "no: uv {} mV", mv);
        }
        Refusal::Ov(mv) => {
            let _ = writeln!(uart, "no: ov {} mV", mv);
        }
        Refusal::Unreadable => {
            let _ = writeln!(uart, "no: pac1954 unreadable");
        }
        Refusal::NotSampling => {
            let _ = writeln!(uart, "no: pac1954 not sampling");
        }
    }
}

