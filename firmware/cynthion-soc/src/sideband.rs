//! What the FPGA_ADV sideband link reports.
//!
//! One byte of CSR, driving `ecp5-test/riscv/sideband_csr.py`. The link itself
//! is a UART and a CRC on pin T6, answering the Apollo microcontroller when USB
//! and the CPU's consoles cannot -- see `ecp5-test/sideband_debug.py`.
//!
//! The point of this register is that until it is written, the FABRIC decides
//! what the link says: whether a byte ever reached the USB endpoint, whether
//! any bus master ever reported an error. Those answers are worth having
//! precisely when the CPU is the thing under suspicion, so the firmware does not
//! get them by default. Setting `OWN` is the firmware saying it knows better,
//! and clearing it hands the link back.

use core::ptr::{read_volatile, write_volatile};

const CTRL: usize = 0;

pub const STATE_MASK: u8 = 0b0000_0011;
pub const EVENTS: u8 = 0b0000_0100;
pub const ERROR: u8 = 0b0000_1000;
pub const RECONFIGURED: u8 = 0b0001_0000;
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
}
