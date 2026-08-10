//! `vbus` -- the distribution switches, as the shell sees it. `vbus.rs` is the
//! driver.

use core::fmt::Write;

use crate::shell::parse::trim;
use crate::uart::Uart;
use crate::vbus::*;
use crate::shell::console::board_absent;
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

    if argument.is_empty() {
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
        open_all();
        if let Some(bus) = devices.bus.as_mut() {
            let _ = fusb302::configure(bus, fusb302::Port::Target);
        }
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
        let bus = match devices.bus.as_mut() {
            Some(bus) => bus,
            None => return board_absent(uart),
        };
        if fusb302::source_target(bus, current).is_err() {
            let _ = writeln!(uart, "?");
            return;
        }
        match charge_target_c(&devices.power) {
            Ok(mv) => {
                let _ = writeln!(uart, "on {} mV", mv);
            }
            Err(error) => {
                let _ = fusb302::configure(bus, fusb302::Port::Target);
                vbus_refusal(uart, error);
            }
        }
        return;
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
        Some(source) => match close(source, &devices.power) {
            Ok(mv) => {
                let _ = writeln!(uart, "on {} mV", mv);
            }
            Err(error) => vbus_refusal(uart, error),
        },
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
        Refusal::Stale => {
            let _ = writeln!(uart, "no: stale");
        }
    }
}

