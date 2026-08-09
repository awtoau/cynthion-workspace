//! USB3343 PHY registers, over the ULPI register window on TARGET.
//!
//! The three PHYs on this board are parallel ULPI: no bus address, no I2C, an
//! 8-bit data bus with `dir`/`nxt`/`stp` handshaking, and registers reached by
//! putting a command byte on that bus during an idle turn. The transaction lives
//! in gateware (`gateware/soc/peripherals/ulpi_window.py`); this is the four-register CSR
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
/// Write-only: bit 0 starts a read, bit 1 starts a write, bit 2 resets the PHY.
const CONTROL: usize = 2;
/// Read-only: bit 0 busy, bit 1 the last transaction timed out, bit 2 a PHY
/// reset is running.
const STATUS: usize = 3;

const CONTROL_READ: u8 = 1 << 0;
const CONTROL_WRITE: u8 = 1 << 1;
const CONTROL_PHY_RESET: u8 = 1 << 2;

const STATUS_BUSY: u8 = 1 << 0;
const STATUS_TIMEOUT: u8 = 1 << 1;
const STATUS_RESETTING: u8 = 1 << 2;

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

/// Turns of the poll loop while a PHY reset runs.
///
/// A separate bound from `SPINS`, and it has to be: the reset sequence is
/// 128 + 72000 cycles of the 60 MHz ULPI clock -- 1.202 ms -- which is four
/// orders of magnitude longer than a register transaction. Polled against
/// `SPINS` a perfectly good reset would be reported as an absent peripheral.
///
/// One turn is an uncached CSR read, which is several CPU cycles at the very
/// least, so at the highest clock this SoC has ever been built at (130 MHz)
/// 1.202 ms is under 160_000 cycles and this covers it several times over.
/// Reaching it means the peripheral is not there, same as `SPINS`.
const RESET_SPINS: u32 = 2_000_000;

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

/// What [`Ulpi::establish`] found. See it for why `reached` is the field that
/// matters.
pub struct Established {
    pub reached: bool,
    pub vendor: u16,
    pub product: u16,
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

    /// Two consecutive registers as one little-endian value: vendor, product.
    fn read16(&self, address: u8) -> Result<u16, Error> {
        Ok(self.read(address)? as u16 | ((self.read(address + 1)? as u16) << 8))
    }

    /// Establish TARGET's PHY from any prior state, and prove the pad moved.
    ///
    /// The reset IS the establishment here: every ULPI register goes back to
    /// its default and nothing in this firmware configures any of them
    /// afterwards, so there is nothing to re-apply. TARGET only -- AUX carries
    /// the console a reply travels over and CONTROL is Apollo's; the gateware
    /// wires this bit to TARGET's pad alone.
    ///
    /// `reached` is the load-bearing field. A PHY answers its identity whether
    /// or not RESETB is connected, because its own power-on reset ran at cold
    /// boot -- which is how both driven reset pads stayed tied de-asserted
    /// through #236 with nothing saying so. The marker is what distinguishes
    /// them: USB334x rev 1.2 §5.6.2 resets the registers, and Table 7.1 gives
    /// Scratch's default as `00h`.
    pub fn establish(&self) -> Result<Established, Error> {
        const MARKER: u8 = 0x5a;
        self.write(usb3343::REG_SCRATCH, MARKER)?;
        // A PHY that never took the marker reads 00 afterwards whatever the
        // reset did, so without this the whole check is vacuous.
        let took = self.read(usb3343::REG_SCRATCH)? == MARKER;
        self.reset_phy()?;
        let cleared = self.read(usb3343::REG_SCRATCH)? == 0x00;
        Ok(Established {
            reached: took && cleared,
            vendor: self.read16(usb3343::REG_VENDOR_ID_LOW)?,
            product: self.read16(usb3343::REG_PRODUCT_ID_LOW)?,
        })
    }

    /// Pulse the TARGET PHY's RESETB and wait out its preparation time.
    ///
    /// This is the only way back for a PHY that has glitched. Before #241 there
    /// was none: both driven reset pads were tied de-asserted, so recovery meant
    /// reconfiguring the FPGA -- which the firmware running on that FPGA cannot
    /// do. Afterwards every ULPI register is at its power-up default, so
    /// anything that had configured the PHY has to do it again.
    ///
    /// TARGET only. AUX carries the console this reply travels over and CONTROL
    /// is shared with Apollo; the gateware wires this bit to TARGET's pad alone,
    /// so there is nothing here that could reset the wrong one.
    ///
    /// The durations are the datasheet's, and they are the gateware's: RESETB
    /// low for 2.133 us, then 1.200 ms of TPREP. `STATUS.resetting` is set for
    /// all of it, so this polls one bit rather than counting out a delay that
    /// would have to track the CPU clock. It returns when the PHY is ready to be
    /// spoken to, not when the pulse ends.
    pub fn reset_phy(&self) -> Result<(), Error> {
        // SAFETY: a write to CONTROL, the one side-effecting address here, and
        // write-only so speculation cannot reach it.
        unsafe {
            write_volatile(self.reg(CONTROL), CONTROL_PHY_RESET);
        }
        let mut spins = 0u32;
        loop {
            if self.status() & STATUS_RESETTING == 0 {
                return Ok(());
            }
            spins += 1;
            if spins > RESET_SPINS {
                return Err(Error::NoPeripheral);
            }
        }
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
