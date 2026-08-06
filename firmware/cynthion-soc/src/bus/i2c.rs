//! The I2C controller itself: bytes on wires, and nothing about which bus.
//!
//! Drives `gateware/soc/i2c_master.py`, which is the OpenCores I2C master
//! register map. Nothing here is specific to what is on the bus except
//! `pac195x`, at the bottom, which knows how to ask a Microchip PAC195x for its
//! name.
//!
//! Private to [`crate::bus`], which is the one thing allowed to construct an
//! [`I2c`] and which pairs every transfer here with a mux select. A driver that
//! reached this module directly could start a transfer without saying which of
//! the board's three buses it meant -- see the module comment there.
//!
//! ## Every wait is bounded
//!
//! A poll that can spin forever is indistinguishable from a dead core, and that
//! confusion has cost real days in this tree (`uart.rs`, `hyperram.rs`). So
//! `wait` gives up.
//!
//! The bound: at 80 kHz a byte and its acknowledge are nine bit periods, or
//! 112 us, and the longest single command this driver issues -- START, byte,
//! acknowledge, STOP -- is about twelve, so 150 us, which is 9000 cycles of a
//! 60 MHz CPU. One turn of the poll loop is a handful of instructions plus an
//! uncached bus read, so a few thousand turns would already cover it. 200_000 is
//! two orders of magnitude of headroom, and is the same bound `Uart::put` uses
//! for the same reason.
//!
//! What it is protecting against is not a slow bus. The bit engine's states are
//! all a fixed number of slots long, so it cannot hang; this catches the case
//! where the peripheral is not there at all, and turns "the shell stopped
//! responding" into "i2c: timeout".
//!
//! ## Polled, not interrupt-driven
//!
//! The gateware raises an interrupt on completion and the SoC wires it to PLIC
//! source 3, but `CTR.IEN` is left at its reset value of zero and this driver
//! spins on `SR.TIP`. A shell command that reads a register has nothing else to
//! do while it waits, so an interrupt would buy it nothing and cost a handler
//! that has to be right about a level-sensitive source -- see the livelock
//! contract in `irq.rs`. The wire exists so that a future driver, one filling
//! the sideband's power payload from a timer, does not need a bitstream change.

use core::ptr::{read_volatile, write_volatile};

const PRER_LO: usize = 0;
const PRER_HI: usize = 1;
const CTR: usize = 2;
/// TXR on write, RXR on read. Both plain registers; reading RXR pops nothing.
const DATA: usize = 3;
/// CR on write, SR on read. The only address here with a side effect, and only
/// on write.
const CMD_STATUS: usize = 4;

const CTR_EN: u8 = 0x80;

const CR_STA: u8 = 0x80;
const CR_STO: u8 = 0x40;
const CR_RD: u8 = 0x20;
const CR_WR: u8 = 0x10;
/// Send a NACK after this read. The name is the OpenCores name and its sense is
/// the surprising one: a master NACKs to say "that was the last byte".
const CR_ACK: u8 = 0x08;
/// Clear the interrupt flag, and move nothing on the bus. A command with none
/// of START, STOP, READ or WRITE set does only this.
const CR_IACK: u8 = 0x01;

const SR_TIP: u8 = 0x02;
const SR_AL: u8 = 0x20;
/// The acknowledge the slave sent. Zero means it acknowledged.
const SR_RXACK: u8 = 0x80;

const TIMEOUT: u32 = 200_000;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// The peripheral never dropped TIP. It is probably not there.
    Timeout,
    /// Something else was holding SDA down. On a single-master bus that is a
    /// wedged slave, not a competitor.
    ArbitrationLost,
    /// Nothing acknowledged the device address at the START of a transfer.
    Nack,
    /// The device acknowledged its address and then not the register pointer.
    NackRegister,
    /// The device acknowledged the write half of a register read and then not
    /// its own address at the repeated START.
    ///
    /// Distinct from `Nack` because it means something quite different: the
    /// device is present and listening, and it declined to turn the bus around.
    /// A part that is busy internally -- the PAC195x for 1 ms after a REFRESH --
    /// fails exactly here and nowhere else, and lumping it in with "nothing
    /// answered" sends the next person looking for a wiring fault.
    NackRestart,
}

impl Error {
    pub fn as_str(&self) -> &'static str {
        match self {
            Error::Timeout => "timeout",
            Error::ArbitrationLost => "arbitration lost (SDA held low)",
            Error::Nack => "no acknowledge (address)",
            Error::NackRegister => "no acknowledge (register pointer)",
            Error::NackRestart => "no acknowledge (repeated start)",
        }
    }
}

#[derive(Clone, Copy)]
pub struct I2c {
    base: usize,
}

impl I2c {
    pub const fn new(base: usize) -> Self {
        I2c { base }
    }

    fn reg(&self, offset: usize) -> *mut u8 {
        (self.base + offset) as *mut u8
    }

    /// Where this register block is. Reported by the `i2c` command; see
    /// [`crate::bus::Bus::i2c_base`].
    pub fn base(&self) -> usize {
        self.base
    }

    /// Set the prescale and enable the core. Idempotent.
    ///
    /// The prescale is written with the core DISABLED. The bit engine's slot
    /// timer reloads from PRER, so changing it under a running transfer would
    /// move an edge that has already been set up -- and I2C's failure mode for a
    /// mistimed edge is a device that answers most of the time.
    pub fn init(&self, prescale: u16) {
        // SAFETY: writes to read/write CSRs in the uncached peripheral region.
        unsafe {
            write_volatile(self.reg(CTR), 0);
            write_volatile(self.reg(PRER_LO), prescale as u8);
            write_volatile(self.reg(PRER_HI), (prescale >> 8) as u8);
            write_volatile(self.reg(CTR), CTR_EN);
        }
        // Clear any completion left over from a previous run.
        //
        // `reset` and `go` restart this firmware with `j _start`, which resets
        // the CPU and nothing else -- the peripherals keep whatever state they
        // were in. IF set from the last command before a reboot would be
        // reported by the next thing that looked at SR, and would raise an
        // interrupt the moment anything set IEN. `irq::init()` calls
        // `plic.complete()` for exactly the same reason and after exactly the
        // same bug.
        self.acknowledge_interrupt();
    }

    fn status(&self) -> u8 {
        // SAFETY: a read of SR, which is a wire from flags and has no side
        // effect. In particular it does not clear IF -- that needs a write.
        unsafe { read_volatile(self.reg(CMD_STATUS)) }
    }

    /// Issue one command and wait for it to finish. Returns SR.
    fn command(&self, command: u8) -> Result<u8, Error> {
        // SAFETY: a write to CR, the peripheral's one side-effecting address.
        unsafe { write_volatile(self.reg(CMD_STATUS), command) };

        let mut spins = 0u32;
        loop {
            let status = self.status();
            if status & SR_TIP == 0 {
                if status & SR_AL != 0 {
                    return Err(Error::ArbitrationLost);
                }
                return Ok(status);
            }
            spins += 1;
            if spins > TIMEOUT {
                return Err(Error::Timeout);
            }
        }
    }

    fn put(&self, byte: u8) {
        // SAFETY: a write to TXR, a plain holding register.
        unsafe { write_volatile(self.reg(DATA), byte) };
    }

    fn take(&self) -> u8 {
        // SAFETY: a read of RXR. It is a register, not a FIFO head: reading it
        // twice gives the same byte and advances nothing.
        unsafe { read_volatile(self.reg(DATA)) }
    }

    /// Put the bus back in a state the next transfer can start from.
    ///
    /// Called on every error path. Without it, a transfer abandoned between its
    /// START and its STOP leaves the peripheral holding SCL low and every
    /// subsequent probe fails -- so one absent device would make the rest of a
    /// scan look absent too, which is exactly the wrong answer to give.
    fn release(&self) {
        let _ = self.command(CR_STO);
    }

    /// Does anything answer at this seven-bit address?
    ///
    /// The probe is a zero-length write: START, the address with the read/write
    /// bit clear, STOP. Whether the device acknowledged its address is the whole
    /// result. A zero-length *read* would be the other convention and is worse
    /// here -- some parts treat a read of an unspecified register as a command.
    pub fn probe(&self, address: u8) -> Result<bool, Error> {
        let status = self.command_with(CR_STA | CR_WR | CR_STO, address << 1)?;
        Ok(status & SR_RXACK == 0)
    }

    fn command_with(&self, command: u8, byte: u8) -> Result<u8, Error> {
        self.put(byte);
        match self.command(command) {
            Ok(status) => Ok(status),
            Err(error) => {
                self.release();
                Err(error)
            }
        }
    }

    /// Read `out.len()` bytes from a device's registers, starting at `register`.
    ///
    /// The standard sequence, and the reason the controller has to support a
    /// repeated START: write the register pointer without releasing the bus,
    /// then START again with the read bit set. A STOP in between would let the
    /// device forget the pointer, and the read would return register 0 -- which
    /// on most parts is a plausible-looking value.
    pub fn read_registers(&self, address: u8, register: u8, out: &mut [u8]) -> Result<(), Error> {
        if out.is_empty() {
            return Ok(());
        }

        let result = self.read_registers_inner(address, register, out);
        if result.is_err() {
            self.release();
        }
        result
    }

    fn read_registers_inner(&self, address: u8, register: u8, out: &mut [u8]) -> Result<(), Error> {
        self.put(address << 1);
        if self.command(CR_STA | CR_WR)? & SR_RXACK != 0 {
            return Err(Error::Nack);
        }

        self.put(register);
        if self.command(CR_WR)? & SR_RXACK != 0 {
            return Err(Error::NackRegister);
        }

        self.put((address << 1) | 1);
        if self.command(CR_STA | CR_WR)? & SR_RXACK != 0 {
            return Err(Error::NackRestart);
        }

        let last = out.len() - 1;
        for (index, slot) in out.iter_mut().enumerate() {
            // The LAST byte is NACKed and followed by a STOP. That is how a
            // master says "no more"; acknowledging it instead leaves the device
            // driving the bus for a byte nobody wanted.
            let command = if index == last {
                CR_RD | CR_ACK | CR_STO
            } else {
                CR_RD
            };
            self.command(command)?;
            *slot = self.take();
        }
        Ok(())
    }

    /// Send one byte to a device with no register pointer in front of it.
    ///
    /// This is SMBus's "Send Byte", and the PAC1954's `REFRESH` is one: the
    /// command IS the register address, and there is no payload. Writing it as
    /// a register write with a dummy data byte would work on most parts and not
    /// on this one -- the byte after the pointer would land in whatever
    /// register the auto-increment moved on to.
    pub fn send_byte(&self, address: u8, byte: u8) -> Result<(), Error> {
        let result = self.send_byte_inner(address, byte);
        if result.is_err() {
            self.release();
        }
        result
    }

    fn send_byte_inner(&self, address: u8, byte: u8) -> Result<(), Error> {
        self.put(address << 1);
        if self.command(CR_STA | CR_WR)? & SR_RXACK != 0 {
            return Err(Error::Nack);
        }
        self.put(byte);
        if self.command(CR_WR | CR_STO)? & SR_RXACK != 0 {
            return Err(Error::Nack);
        }
        Ok(())
    }

    /// Write one byte to a device register.
    ///
    /// START, address+W, register, value, STOP -- no repeated START, because
    /// nothing turns around. The acknowledge is checked after every byte rather
    /// than only after the address: a part that runs out of write buffer NACKs
    /// mid-transfer, and a driver that only looked at the address would report
    /// a configuration write as successful when the value never landed.
    pub fn write_register(&self, address: u8, register: u8, value: u8) -> Result<(), Error> {
        self.write_registers(address, register, &[value])
    }

    /// Write consecutive bytes to one register pointer.
    pub fn write_registers(&self, address: u8, register: u8, values: &[u8]) -> Result<(), Error> {
        let result = self.write_registers_inner(address, register, values);
        if result.is_err() {
            self.release();
        }
        result
    }

    fn write_registers_inner(&self, address: u8, register: u8, values: &[u8]) -> Result<(), Error> {
        self.put(address << 1);
        if self.command(CR_STA | CR_WR)? & SR_RXACK != 0 {
            return Err(Error::Nack);
        }
        self.put(register);
        if self.command(CR_WR)? & SR_RXACK != 0 {
            return Err(Error::Nack);
        }
        let last = values.len() - 1;
        for (index, &value) in values.iter().enumerate() {
            self.put(value);
            let command = CR_WR | if index == last { CR_STO } else { 0 };
            if self.command(command)? & SR_RXACK != 0 {
                return Err(Error::Nack);
            }
        }
        Ok(())
    }

    /// Clear IF and drop the interrupt line, without moving anything on the bus.
    ///
    /// A command with none of START, STOP, READ or WRITE set is an acknowledge
    /// and nothing else -- the gateware checks for exactly that.
    pub fn acknowledge_interrupt(&self) {
        // SAFETY: a write to CR.
        unsafe { write_volatile(self.reg(CMD_STATUS), CR_IACK) };
    }
}

/// What a Microchip PAC195x calls itself.
///
/// Three read-only registers at the top of the map (DS20006539B, registers
/// 7-31..7-33). Reading them back with sensible values is the check that proves
/// the link before any measurement from this part is believed -- a bus that
/// returns 0x00 or 0xff for everything looks like data until you ask it
/// something whose answer you already know.
pub mod pac195x {
    pub const REG_PRODUCT_ID: u8 = 0xfd;
    pub const REG_MANUFACTURER_ID: u8 = 0xfe;
    pub const REG_REVISION_ID: u8 = 0xff;

    /// Microchip's manufacturer id. Anything else means the part is not what
    /// this decoder thinks it is.
    pub const MANUFACTURER_MICROCHIP: u8 = 0x54;

    /// Product ids for the family, from Table 7-1 of the datasheet.
    ///
    /// The board carries a PAC1954-1 (`0x7b`), confirmed on r1.4 silicon on
    /// 2026-07-28 -- see `gateware/probes/power_monitor/registers.py`. The rest are
    /// here so a different board, or a different strap, reports what it is
    /// rather than "unknown".
    pub fn product_name(id: u8) -> &'static str {
        match id {
            0x78 => "PAC1951-1",
            0x79 => "PAC1952-1",
            0x7a => "PAC1953-1",
            0x7b => "PAC1954-1",
            0x68 => "PAC1951-2",
            0x69 => "PAC1952-2",
            _ => "unknown",
        }
    }
}
