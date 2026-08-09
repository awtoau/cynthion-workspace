//! `sideband` -- the link, as the shell sees it. `sideband.rs` is the driver.

use core::fmt::Write;

use crate::parse::{parse_hex, trim};
use crate::uart::Uart;
use crate::sideband::*;
use crate::{board_absent, target};

/// `sideband`, `sideband <ctrl>`, or `sideband <ctrl> <tx>`.
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let link = Sideband::new(board.sideband);

    let rest = trim(rest);
    if !rest.is_empty() {
        // Split on the first space: the control register, then optionally the
        // byte a PING returns. Two arguments rather than two commands because
        // they are read back together and are usually set together.
        let split = rest.iter().position(|&byte| byte == b' ');
        let (first, second) = match split {
            Some(at) => (&rest[..at], trim(&rest[at + 1..])),
            None => (rest, &b""[..]),
        };
        match (parse_hex(first), second.is_empty()) {
            (Some(value), true) => link.write(value as u8),
            (Some(value), false) => match parse_hex(second) {
                Some(message) => {
                    link.write(value as u8);
                    link.set_message(message as u8);
                }
                None => return sideband_usage(uart),
            },
            (None, _) => return sideband_usage(uart),
        }
    }

    let value = link.read();
    let _ = writeln!(uart, "sideband @{:08x} ctrl {:02x}", board.sideband, value);
    if value & OWN != 0 {
        let _ = writeln!(
            uart,
            "  reporting state {} events {} error {} \
                                reconfigured {}",
            value & STATE_MASK,
            (value & EVENTS != 0) as u8,
            (value & ERROR != 0) as u8,
            (value & RECONFIGURED != 0) as u8
        );
    } else {
        // Do NOT decode the payload bits here. With OWN clear they are stored
        // and ignored, and printing them under a heading that reads like a
        // report would say the link is announcing something it is not -- which
        // is the one lie a diagnostic for a debug link must not tell.
        let _ = writeln!(
            uart,
            "  reporting the fabric's own state; these bits \
                                are stored and unused"
        );
    }
    // Printed either way: neither the port request nor the byte channel is part
    // of the payload, so OWN says nothing about them.
    let _ = writeln!(
        uart,
        "  CONTROL port {}",
        if value & ADVERTISE != 0 {
            "REQUESTED"
        } else {
            "not requested"
        }
    );
    let (received, count) = link.received();
    let _ = writeln!(
        uart,
        "  message out {:02x}, in {:02x} after {} byte(s)",
        link.message(),
        received,
        count
    );
}

pub(crate) fn sideband_usage(uart: &mut Uart) {
    let _ = writeln!(uart, "usage: sideband [ctrl [tx]]");
    let _ = writeln!(
        uart,
        "  ctrl bit 7 takes the link from the fabric, \
                            bit 5 asks for the CONTROL port"
    );
    let _ = writeln!(uart, "  tx   the byte a PING returns");
}

