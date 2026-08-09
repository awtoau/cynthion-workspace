//! `usb <port>` -- the board as four USB ports, not as five chips.
//!
//! The chip commands (`pac1954`, `fusb302b`, `usb3343`, `vbus`) each answer for
//! one part across every port. That is the right shape when the part is what is
//! suspect. It is the wrong shape for every other question, because a port is
//! what a cable plugs into and what a person is actually asking about:
//!
//!     usb aux            everything about AUX -- rail, switch, cc, phy
//!     usb target_c off   open its switch and stop its interrupts
//!
//! **The inverse index of `board`.** `board` walks the tree once and shows every
//! port shallowly; this shows one port completely. Same drivers, same numbers,
//! transposed.
//!
//! ## In English, not in register fields
//!
//! `cc both`, `vRd-3.0A` and `vbus present` are the part's vocabulary. A port
//! view says what they MEAN -- who is source, who is sink, what the cable
//! advertises -- because the reader is asking about a connector and not about
//! CC1/CC2 comparator bands.

use core::fmt::Write;

use crate::fusb302::Port;
use crate::shell::parse::trim;
use crate::uart::Uart;
use crate::{target, typec::Controllers, vbus, Devices};

/// The four rails, in `power::PORTS` order, and what each one is.
///
/// The index IS the PAC1954 channel, so the order is the part's rather than the
/// intuitive one -- channel 0 is TARGET_A. `power::PORTS` is the single
/// definition of the names; this adds only what the part cannot know.
///
/// `Port` is the FUSB302B's, and CONTROL and TARGET_A have none: CONTROL has no
/// controller on the board at all, and TARGET_A is USB-A, which has no CC pins
/// to have a controller for.
const PORTS: [(&str, Option<Port>, Option<vbus::Source>, &str); 4] = [
    ("target_a", None, None, "the USB-A side of the passthrough; no CC, no PD"),
    ("target_c", Some(Port::Target), Some(vbus::Source::TargetC),
     "the port under test; nothing in this SoC drives it"),
    ("aux", Some(Port::Aux), Some(vbus::Source::Aux),
     "the USB console; this text is leaving through it"),
    ("control", None, Some(vbus::Source::Control),
     "the host port, and Apollo's; no fusb302b, so CC is invisible"),
];

/// `usb`, `usb <port>`, `usb <port> status|off`.
pub(crate) fn command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    let rest = trim(rest);
    if rest.is_empty() {
        return list(uart);
    }

    let (name, verb) = match rest.iter().position(|&b| b == b' ') {
        Some(at) => (&rest[..at], trim(&rest[at + 1..])),
        None => (rest, &rest[..0]),
    };

    let Some(index) = PORTS.iter().position(|(port, ..)| port.as_bytes() == name)
    else {
        let _ = writeln!(uart, "no port of that name; they are:");
        return list(uart);
    };

    match verb {
        b"" | b"status" => status(uart, index, devices),
        verb => match VERBS.iter().find(|(name, ..)| name.as_bytes() == verb) {
            Some((name, what, missing)) => planned(uart, index, name, what, missing),
            None => {
                let _ = writeln!(uart, "usage: usb {} [status|off|on|device|host|reset]",
                                 PORTS[index].0);
            }
        },
    }
}

/// The verbs that are NOT built yet, and what each one is waiting on.
///
/// Registered rather than omitted, and each says which driver gap blocks it.
/// A verb that is missing from the table is indistinguishable from one that was
/// never planned, and `usage:` lines are where intent goes to be forgotten.
///
/// **None of them touch hardware.** A half-done `off` is worse than none: the
/// first version opened EVERY VBUS switch, because there is no per-switch open
/// in the driver -- so a command scoped to one port silently cut the other two.
const VERBS: [(&str, &str, &str); 5] = [
    ("off", "stop this port sourcing, and stop it interrupting",
     "no per-switch open in `vbus.rs`, and no Type-C interrupt mask"),
    ("on", "source the board from this port",
     "`vbus.rs` refuses combinations by policy; a per-port form needs that policy"),
    ("device", "put this port in device mode",
     "no role control -- the ULPI window is read-only in this SoC"),
    ("host", "put this port in host mode",
     "no role control -- the ULPI window is read-only in this SoC"),
    ("reset", "pulse this port's PHY reset",
     "only TARGET has a RESETB this SoC drives; see `usb3343 reset`"),
];

/// A verb that exists in the vocabulary and not yet in the firmware.
///
/// It names the gap. "not implemented" tells a reader to go and find out what
/// is missing; this tells them, so the next person to pick it up starts from the
/// driver rather than from the shell.
fn planned(uart: &mut Uart, index: usize, verb: &str, what: &str, missing: &str) {
    let _ = writeln!(uart, "usb {} {} -- NOT IMPLEMENTED", PORTS[index].0, verb);
    let _ = writeln!(uart, "  intent    {}", what);
    let _ = writeln!(uart, "  blocked   {}", missing);
}

/// The four ports and what each one is for.
fn list(uart: &mut Uart) {
    for (name, controller, source, what) in PORTS {
        let _ = writeln!(
            uart,
            "  {:9} {}{}",
            name,
            what,
            match (controller, source) {
                (Some(_), Some(_)) => "",
                (None, Some(_)) => "",
                // Nothing to switch and nothing to negotiate: the only thing
                // this port has is a rail, and saying so here saves a reader
                // asking it for a switch that does not exist.
                _ => "  [rail only]",
            }
        );
    }
}

/// Everything this SoC can see about one port.
///
/// Four rows at most, and a port that lacks a thing says so rather than omitting
/// the row -- an absent row reads as "not checked", which is the opposite of
/// what is true here.
fn status(uart: &mut Uart, index: usize, devices: &mut Devices) {
    let (name, controller, source, what) = PORTS[index];
    let _ = writeln!(uart, "{}  {}", name, what);

    // --- the rail ------------------------------------------------------
    let sample = devices.power.latest();
    let _ = write!(uart, "  power     ");
    match sample.as_ref() {
        Some(sample) => {
            let millivolts = sample.readings[index].bus_mv;
            let milliamps = sample.readings[index].current_ua / 1000;
            let floor = devices.power.floor(index) / 1000;
            // ABSENT, not "0 mA". Below the floor the current is indistinguishable
            // from the part's own offset, so the reading is not evidence of a
            // connected device -- and printing a small number implies it is.
            if milliamps < floor as i32 {
                let _ = writeln!(
                    uart,
                    "{}.{:03} V   nothing drawing (under the {} mA floor)",
                    millivolts / 1000,
                    millivolts % 1000,
                    floor
                );
            } else {
                let _ = writeln!(
                    uart,
                    "{}.{:03} V   {} mA",
                    millivolts / 1000,
                    millivolts % 1000,
                    milliamps
                );
            }
        }
        None => {
            let _ = writeln!(uart, "no sample yet; `pac1954 rate 50` starts the poll");
        }
    }

    // --- the VBUS switch ----------------------------------------------
    let _ = write!(uart, "  switch    ");
    match source {
        Some(source) => {
            if vbus::is_closed(source) {
                let _ = writeln!(uart, "CLOSED -- this port is SOURCING the board");
            } else {
                let _ = writeln!(uart, "open -- this port is not feeding the board");
            }
        }
        None => {
            let _ = writeln!(uart, "none -- TARGET_A cannot source, it is the far end");
        }
    }

    // --- the Type-C controller, in English ----------------------------
    let _ = write!(uart, "  cable     ");
    match controller {
        None if name == "control" => {
            let _ = writeln!(
                uart,
                "unknown -- no fusb302b on CONTROL, so CC and PD are invisible"
            );
        }
        None => {
            let _ = writeln!(uart, "USB-A: no CC pins, so nothing to negotiate");
        }
        Some(port) => cable(uart, port, &devices.type_c),
    }

    // --- the PHY -------------------------------------------------------
    // Only TARGET has a register window in this SoC; the other two PHYs are
    // Apollo's and the console's, and neither is addressable from here.
    let _ = write!(uart, "  phy       ");
    match name {
        "target_c" | "target_a" => {
            let _ = writeln!(uart, "usb3343, readable -- `usb3343 status`");
        }
        "aux" => {
            let _ = writeln!(uart, "usb3343, THE CONSOLE'S -- no ulpi window here");
        }
        _ => {
            let _ = writeln!(uart, "usb3343, APOLLO'S -- no ulpi window here");
        }
    }
}

/// What the FUSB302B knows, said as a sentence rather than as bands.
fn cable(uart: &mut Uart, port: Port, type_c: &Controllers) {
    let Some((state, _at)) = type_c.cached(port) else {
        if target::BOARD.is_none() {
            let _ = writeln!(uart, "no i2c bus on this target");
        } else {
            let _ = writeln!(uart, "the controller has not answered; `fusb302b init` retries");
        }
        return;
    };

    // The verdict first, then the evidence. `vbus present` plus an Rd band is
    // "something is feeding this port"; the bands alone leave the reader to
    // work that out every time.
    if state.vbus {
        let _ = write!(uart, "connected, VBUS live");
    } else {
        let _ = write!(uart, "nothing connected, VBUS absent");
    }
    let _ = writeln!(uart, "  ({}, cc {})", state.cc(), state.orientation().name());
}

