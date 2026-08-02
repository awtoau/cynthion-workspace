//! USB3343 PHY registers, over the ULPI register window on TARGET.
//!
//! The three PHYs on this board are parallel ULPI: no bus address, no I2C, an
//! 8-bit data bus with `dir`/`nxt`/`stp` handshaking, and registers reached by
//! putting a command byte on that bus during an idle turn. The transaction lives
//! in gateware (`ecp5-test/riscv/ulpi_window.py`); this is the four-register CSR
//! interface in front of it.
//!
//! ## Only TARGET
//!
//! There is one window and it is on `target_phy`. `aux_phy` carries the USB
//! console this firmware answers on, and a second master issuing register
//! commands there would corrupt the link the answer travels over -- a diagnostic
//! that breaks the thing it is reporting through. `control_phy` is shared with
//! Apollo. TARGET is the port nothing drives, which is exactly why it is the one
//! that can be probed from a running system.
//!
//! ## Every wait is bounded, in both halves
//!
//! The gateware times out after 68 us if the PHY never releases the bus, and
//! reports it in `STATUS.timeout`. This driver ALSO bounds its own poll of
//! `STATUS.busy`, because the two failures are different: the gateware's timeout
//! catches an absent PHY, and this one catches an absent *peripheral* -- an
//! address that decodes to nothing reads as zero, so `busy` is never set and the
//! loop would exit at once with a fabricated result rather than hanging. So
//! the bound here is what stops a firmware built against a bitstream that lacks
//! this peripheral from reporting plausible rubbish.

use core::ptr::{read_volatile, write_volatile};

/// The 6-bit ULPI register address to act on.
const ADDRESS: usize = 0;
/// Byte to write; on read, the byte the last transaction returned.
const DATA: usize = 1;
/// Write-only: bit 0 starts a read, bit 1 starts a write.
const CONTROL: usize = 2;
/// Read-only: bit 0 busy, bit 1 the last transaction timed out.
const STATUS: usize = 3;

const CONTROL_READ: u8 = 1 << 0;
const CONTROL_WRITE: u8 = 1 << 1;

const STATUS_BUSY: u8 = 1 << 0;
const STATUS_TIMEOUT: u8 = 1 << 1;

/// Turns of the poll loop before this driver gives up on the peripheral.
///
/// The gateware's own bound is 4096 cycles of the 60 MHz ULPI clock, 68 us, and
/// it always toggles the acknowledge on expiry -- so `busy` cannot stay set for
/// longer than that plus a domain crossing. One turn of the loop below is an
/// uncached CSR read, which is a handful of CPU cycles at minimum, so 10_000
/// turns covers 68 us with two orders of magnitude to spare. Reaching it means
/// the peripheral is not there at all, which is a bitstream/firmware mismatch
/// rather than a PHY problem, and this is what makes the two distinguishable.
const SPINS: u32 = 10_000;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// The PHY never released the bus. It is absent, held in reset, or driving
    /// continuously.
    PhyTimeout,
    /// `busy` never cleared. The window is not at this address.
    NoPeripheral,
}

impl Error {
    pub fn as_str(&self) -> &'static str {
        match self {
            Error::PhyTimeout => "no answer from the PHY",
            Error::NoPeripheral => "no ULPI window at this address",
        }
    }
}

/// The ULPI register window at a fixed base address.
#[derive(Clone, Copy)]
pub struct Ulpi {
    base: usize,
}

impl Ulpi {
    pub const fn new(base: usize) -> Self {
        Ulpi { base }
    }

    fn reg(&self, offset: usize) -> *mut u8 {
        (self.base + offset) as *mut u8
    }

    fn status(&self) -> u8 {
        // SAFETY: a read of STATUS, which is two wires from flags in gateware.
        // No side effect: reading it twice gives the same answer, and in
        // particular it does not clear `timeout` -- that is cleared by the start
        // of the next transaction, so a poll loop cannot lose it.
        unsafe { read_volatile(self.reg(STATUS)) }
    }

    /// Wait for the window to go idle, then report whether the PHY answered.
    fn settle(&self) -> Result<(), Error> {
        let mut spins = 0u32;
        loop {
            let status = self.status();
            if status & STATUS_BUSY == 0 {
                if status & STATUS_TIMEOUT != 0 {
                    return Err(Error::PhyTimeout);
                }
                return Ok(());
            }
            spins += 1;
            if spins > SPINS {
                return Err(Error::NoPeripheral);
            }
        }
    }

    /// Read one PHY register.
    pub fn read(&self, address: u8) -> Result<u8, Error> {
        // SAFETY: writes to plain read/write CSRs, then a write to the one
        // side-effecting address in this peripheral. CONTROL is write-only, so
        // there is nothing here a speculative read could trigger.
        unsafe {
            write_volatile(self.reg(ADDRESS), address & 0x3f);
            write_volatile(self.reg(CONTROL), CONTROL_READ);
        }
        self.settle()?;
        // SAFETY: DATA is a latch on the `sync` side, written when the
        // handshake completed -- which `settle` has just established. Reading it
        // pops nothing.
        Ok(unsafe { read_volatile(self.reg(DATA)) })
    }

    /// Write one PHY register.
    ///
    /// DATA has to be staged BEFORE CONTROL, because CONTROL is the strobe: the
    /// gateware latches the request on the write to CONTROL and looks at nothing
    /// afterwards. The reverse order writes whatever was in DATA from the last
    /// read, which on a scratch-register test is the value being tested against
    /// and therefore passes.
    pub fn write(&self, address: u8, value: u8) -> Result<(), Error> {
        // SAFETY: as above.
        unsafe {
            write_volatile(self.reg(ADDRESS), address & 0x3f);
            write_volatile(self.reg(DATA), value);
            write_volatile(self.reg(CONTROL), CONTROL_WRITE);
        }
        self.settle()
    }
}

/// The USB3343 registers this firmware reads, and what they are for.
///
/// These are ULPI registers, not SoC registers: they are not in our memory map
/// and nothing generates them. The map is in
/// `docs/chips/usb3343-ulpi-phy.md`.
pub mod usb3343 {
    /// Vendor ID, low byte then high. `0x0424` is Microchip/SMSC.
    pub const REG_VENDOR_ID_LOW: u8 = 0x00;
    /// Product ID, low byte then high. `0x0009` is the USB3343.
    pub const REG_PRODUCT_ID_LOW: u8 = 0x02;
    /// Function control: transceiver select, term select, op mode, reset.
    pub const REG_FUNCTION_CONTROL: u8 = 0x04;
    /// OTG control: the D+/D- pull-up and pull-down enables.
    pub const REG_OTG_CONTROL: u8 = 0x0a;
    /// Debug. Bit 0 is LineState0, bit 1 LineState1 -- what the PHY currently
    /// sees on D+/D-.
    ///
    /// Read rather than `0x14` (Interrupt Latch), which is CLEAR-ON-READ. A
    /// diagnostic must not consume the event it is reporting on; that is the
    /// rule this SoC's own register maps are built around, and it applies just
    /// as much to somebody else's part.
    pub const REG_DEBUG: u8 = 0x15;
    /// Scratch. Eight bits that do nothing else, which is what makes a
    /// walking-bit test possible.
    pub const REG_SCRATCH: u8 = 0x16;

    /// What a present, working USB3343 answers with. Confirmed on all three
    /// PHYs of this board by `scripts/phy_probe.py`, and asserted by the
    /// shipped `cynthion selftest`.
    pub const VENDOR_ID: u16 = 0x0424;
    pub const PRODUCT_ID: u16 = 0x0009;
}
