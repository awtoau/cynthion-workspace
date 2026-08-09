//! What the FPGA_ADV sideband link reports.
//!
//! One byte of CSR, driving `gateware/soc/peripherals/sideband_csr.py`. The link itself
//! is a UART and a CRC on pin T6, answering the Apollo microcontroller when USB
//! and the CPU's consoles cannot -- see `gateware/probes/sideband/sideband_debug.py`.
//!
//! The point of this register is that until it is written, the FABRIC decides
//! what the link says: whether a byte ever reached the USB endpoint, whether
//! any bus master ever reported an error. Those answers are worth having
//! precisely when the CPU is the thing under suspicion, so the firmware does not
//! get them by default. Setting `OWN` is the firmware saying it knows better,
//! and clearing it hands the link back.
//!
//! `ADVERTISE` is the other half, and is not part of that handover: it asks
//! Apollo for the CONTROL port by putting the frame Apollo matches on the same
//! wire (`gateware/probes/sideband/sideband_advertise.py`). Every bitstream here is AUX-only, so
//! this is the only way the SoC can claim CONTROL at all.

use core::fmt::Write;

use crate::parse::{parse_hex, trim};
use crate::uart::Uart;
use crate::{board_absent, target};

use core::ptr::{read_volatile, write_volatile};

const CTRL: usize = 0;
const TX: usize = 1;
const RX: usize = 2;
const RXCNT: usize = 3;

pub const STATE_MASK: u8 = 0b0000_0011;
pub const EVENTS: u8 = 0b0000_0100;
pub const ERROR: u8 = 0b0000_1000;
pub const RECONFIGURED: u8 = 0b0001_0000;
/// Ask Apollo for the CONTROL port. Resets clear, and outside `OWN` -- it is an
/// action rather than a reported value, so the fabric has nothing to override.
/// Clearing it hands the port back one Apollo timeout (300 ms) later.
pub const ADVERTISE: u8 = 0b0010_0000;
/// Take the link from the fabric. Resets clear.
pub const OWN: u8 = 0b1000_0000;

#[derive(Clone, Copy)]
pub struct Sideband {
    base: usize,
}

impl Sideband {
    pub const fn new(base: usize) -> Self {
        Sideband { base }
    }

    /// What is in the register. Plain storage; reading it changes nothing and
    /// does not tell you what the fabric would be reporting instead.
    pub fn read(&self) -> u8 {
        // SAFETY: a read of a read/write CSR in the uncached peripheral region.
        unsafe { read_volatile((self.base + CTRL) as *const u8) }
    }

    pub fn write(&self, value: u8) {
        // SAFETY: a write to a read/write CSR.
        unsafe { write_volatile((self.base + CTRL) as *mut u8, value) };
    }

    /// The byte a `PING` returns. Plain storage, outside `OWN`.
    pub fn message(&self) -> u8 {
        // SAFETY: a read of a read/write CSR in the uncached peripheral region.
        unsafe { read_volatile((self.base + TX) as *const u8) }
    }

    pub fn set_message(&self, value: u8) {
        // SAFETY: a write to a read/write CSR.
        unsafe { write_volatile((self.base + TX) as *mut u8, value) };
    }

    /// The last seven-bit value Apollo sent, and how many have arrived.
    ///
    /// Returned together because neither is useful alone: the count is what
    /// distinguishes "Apollo sent the same byte again" from "Apollo has sent
    /// nothing", and reading either changes neither. Keep the count and compare.
    /// It wraps at 256, which is correct for a comparison against your own last
    /// value and is why it does not saturate.
    pub fn received(&self) -> (u8, u8) {
        // SAFETY: two reads of read-only CSRs in the uncached peripheral region.
        unsafe {
            (
                read_volatile((self.base + RX) as *const u8),
                read_volatile((self.base + RXCNT) as *const u8),
            )
        }
    }
}
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

