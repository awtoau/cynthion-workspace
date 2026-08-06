//! Which I2C bus the one controller is driving, and who is asking for it.
//!
//! Two registers in front of `gateware/soc/peripherals/i2c_mux.py`. The board has three
//! physically separate I2C buses because both FUSB302Bs answer to `0x22` and
//! cannot be told apart on one wire, so the select here does the job an address
//! would otherwise do -- and every transaction has to set it, because there is
//! nothing in a reply that says which bus it came from.
//!
//! Both reads below are pure. `LINES` is a wire from four synchronised pads and
//! `SELECT` is a latch; neither clears anything, and the FUSB302B's own
//! interrupt registers -- which ARE read-to-clear -- are cleared over the bus by
//! the driver in `src/fusb302.rs`, which is where that belongs.

use core::ptr::{read_volatile, write_volatile};

/// Which pin-set the controller drives.
const SELECT: usize = 0;
/// The four Type-C signals, read-only.
const LINES: usize = 1;

/// The FUSB302B on TARGET-C.
pub const BUS_TARGET_C: u8 = 0;
/// The FUSB302B on AUX-C.
pub const BUS_AUX_C: u8 = 1;
/// The PAC1954. The reset value, so the power monitor works before anything
/// touches the select.
pub const BUS_POWER_MONITOR: u8 = 2;

pub const LINE_TARGET_INT: u8 = 0;
pub const LINE_AUX_INT: u8 = 1;
pub const LINE_TARGET_FAULT: u8 = 2;
pub const LINE_AUX_FAULT: u8 = 3;

/// The bus mux at a fixed base address.
#[derive(Clone, Copy)]
pub struct Mux {
    base: usize,
}

impl Mux {
    pub const fn new(base: usize) -> Self {
        Mux { base }
    }

    fn reg(&self, offset: usize) -> *mut u8 {
        (self.base + offset) as *mut u8
    }

    /// Where this register block is. Reported by the `typec` command, so that
    /// what is printed is the address the driver is actually using rather than a
    /// second reading of `target::BOARD` that could disagree with it.
    pub fn base(&self) -> usize {
        self.base
    }

    /// Point the controller at a bus.
    ///
    /// Reachable only from [`crate::bus::Bus`], which calls it immediately before
    /// every transfer and never remembers what it wrote. That is the point of
    /// this module being private: a driver cannot select a bus and then talk to
    /// it in two separate steps, so the window in which something else moves the
    /// select does not exist.
    ///
    /// Cheap enough to do every time -- one uncached byte store against a bus
    /// transfer of milliseconds. Caching it would be an optimisation worth
    /// nothing and a second copy of the truth, and the failure it enables is not
    /// an error but a plausible answer from the wrong chip, since both Type-C
    /// controllers answer the same address with the same identity byte.
    ///
    /// The gateware holds the change until the controller is idle, so this
    /// cannot move the bus underneath a transfer even if it is called at the
    /// wrong moment.
    pub fn select(&self, bus: u8) {
        // SAFETY: a write to a plain read/write CSR in the uncached peripheral
        // region.
        unsafe { write_volatile(self.reg(SELECT), bus & 0x03) }
    }

    /// Which bus the controller is pointed at.
    pub fn selected(&self) -> u8 {
        // SAFETY: a read of a latch. No side effect.
        unsafe { read_volatile(self.reg(SELECT)) & 0x03 }
    }

    /// The four Type-C signals: `int` for each controller, then `fault`.
    ///
    /// Read once and passed around, rather than read per device. The two `int`
    /// lines are OR-ed into one interrupt, so a handler that read this register
    /// twice could see one device assert between the reads and act on a picture
    /// that never existed.
    pub fn lines(&self) -> u8 {
        // SAFETY: a read of four wires from FFSynchronizers. Pure: reading it
        // twice gives the same answer and it clears nothing. What clears the
        // FUSB302B's interrupt is reading the FUSB302B.
        unsafe { read_volatile(self.reg(LINES)) }
    }
}
