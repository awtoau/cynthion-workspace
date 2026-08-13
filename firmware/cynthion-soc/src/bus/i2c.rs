//! I2C controller: bytes on wires, nothing about which bus.
//!
//! - Drives `gateware/soc/peripherals/i2c_master.py` (OpenCores I2C master register
//!   map). Nothing here is bus-specific except `pac195x` at the bottom, which knows
//!   how to ask a Microchip PAC195x for its name.
//! - Private to [`crate::bus`], the only thing allowed to construct an [`I2c`] and
//!   pair every transfer with a mux select. A driver reaching this module directly
//!   could start a transfer without saying which of the board's three buses it
//!   meant -- see the module comment in `bus.rs`.
//!
//! ## Every wait is bounded
//!
//! - A poll that can spin forever is indistinguishable from a dead core -- has cost
//!   real days in this tree (`uart.rs`, `hyperram.rs`). `wait` gives up.
//! - Bound: [`I2c::wait_limit`], derived per command from the prescale the core
//!   actually holds -- 150 turns at the prescale 9 this build runs. A flat
//!   `200_000` is a hang with a number attached, not a safety factor (#355).
//! - Protects against the peripheral not being there at all, not a slow bus: the bit
//!   engine's states are all a fixed number of slots long and cannot hang. Turns "the
//!   shell stopped responding" into "i2c: timeout".
//! - `Uart::put` still carries a flat `200_000` with the same stated reason. Not
//!   fixed here; it belongs to the tree-wide timeout audit, #295.
//!
//! ## The completion interrupt: enabled, and what it is for
//!
//! - `CTR.IEN` set, source 3 claimed by [`I2c::init`]. A peripheral claims its
//!   own source rather than being remembered by a list elsewhere: a source wired in
//!   `gateware/soc/top.py` but absent from that list reads as `enabled 00000036`,
//!   bit 3 missing, with nothing saying so (#246).
//! - **This driver still spins on `SR.TIP`.** `TIP` ("transfer in progress") and `IF`
//!   (completion flag, raises the line) are different bits, neither waits on the
//!   other. A shell command reading a register has nothing else to do while it
//!   waits, so the spin is not the thing to remove first.
//! - Interrupt today gives EVIDENCE -- a source that fires and is counted rather than
//!   enabled and silent, per #246. Enables the conversion that matters next: under
//!   RTIC the REFRESH task holds `Devices` for ~2 ms per PAC1954 read; a task that
//!   started a transfer, returned, and got re-pended by this source would hold it for
//!   neither. That's #247, and needs this bit set first.
//!
//! ## `wfi` was tried here and rejected -- #266
//!
//! - VexiiRiscv implements `WFI` as a trap (`TrapReason.WFI`, `EnvPlugin.scala:144`),
//!   so each wake costs a pipeline flush and an I-cache refill; the spin does not.
//! - What WOULD pay is not being in the wait at all: a resumable driver, #247.
//!
//! ## The handler must clear it AT THE PERIPHERAL
//!
//! - Source 3 is a LEVEL: `self.irq.eq(irq_flag & ien)` in
//!   `gateware/soc/peripherals/i2c_master.py`; `irq_flag` clears only via `CR.IACK`.
//!   Acknowledging while the peripheral still asserts re-delivers it
//!   immediately -- the livelock `irq.rs` documents.
//! - [`I2c::acknowledge_interrupt`] is one MMIO write, so unlike the FUSB302B (three
//!   read-to-clear registers over I2C, ~1 ms) it's short enough to do in the handler.
//!   That's the whole difference between the two sources' treatment -- a fact about
//!   the clear, not the peripheral.

use core::ptr::{read_volatile, write_volatile};
use core::sync::atomic::{AtomicU32, Ordering};

const PRER_LO: usize = 0;
const PRER_HI: usize = 1;
const CTR: usize = 2;
/// TXR on write, RXR on read. Both plain registers; reading RXR pops nothing.
const DATA: usize = 3;
/// CR on write, SR on read. The only address here with a side effect, and only
/// on write.
const CMD_STATUS: usize = 4;

const CTR_EN: u8 = 0x80;
/// Interrupt enable. The gateware drives source 3 from `irq_flag & ien`, so
/// this bit is the difference between a source that is wired and a source that
/// can fire.
const CTR_IEN: u8 = 0x40;

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
/// A START has gone out and no STOP has followed it yet.
const SR_BUSY: u8 = 0x40;
const SR_AL: u8 = 0x20;
/// The acknowledge the slave sent. Zero means it acknowledged.
const SR_RXACK: u8 = 0x80;

/// The longest single command in bit periods: START, eight bits, ack, STOP.
const LONGEST_COMMAND_PERIODS: u32 = 12;
/// Sync cycles per bit period, `f_SCL = f_sync / (5 * (PRER + 1))`.
const CYCLES_PER_PERIOD: u32 = 5;
/// Cycles of the tightest possible turn of the spin in [`I2c::command`]: an
/// uncached MMIO load, a mask, a compare and a branch. Assumed low on purpose --
/// a faster loop needs MORE turns to cover the same wait.
const CYCLES_PER_TURN: u32 = 5;

/// The last expiry, for `i2c status` to name. A silent expiry is worse than no
/// timeout, and this driver has no console handle to log from -- so it records
/// rather than prints, and something with a `Uart` reads it back.
static TIMEOUT_COMMAND: AtomicU32 = AtomicU32::new(NO_TIMEOUT);
static TIMEOUT_LIMIT: AtomicU32 = AtomicU32::new(0);
static TIMEOUT_TURNS: AtomicU32 = AtomicU32::new(0);
/// No command byte can be this, so it cannot be confused with a real record.
const NO_TIMEOUT: u32 = u32::MAX;

fn record_timeout(command: u8, limit: u32, turns: u32) {
    TIMEOUT_LIMIT.store(limit, Ordering::Relaxed);
    TIMEOUT_TURNS.store(turns, Ordering::Relaxed);
    // Last, so a reader that sees a command byte sees the other two settled.
    TIMEOUT_COMMAND.store(command as u32, Ordering::Relaxed);
}

/// The command byte, the limit it exceeded and the turns it spent, or `None`.
pub fn last_timeout() -> Option<(u8, u32, u32)> {
    match TIMEOUT_COMMAND.load(Ordering::Relaxed) {
        NO_TIMEOUT => None,
        command => Some((
            command as u8,
            TIMEOUT_LIMIT.load(Ordering::Relaxed),
            TIMEOUT_TURNS.load(Ordering::Relaxed),
        )),
    }
}

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
            write_volatile(self.reg(CTR), CTR_EN | CTR_IEN);
        }
        // Clear any completion left over from a previous run.
        //
        // `reset` and `go` restart this firmware with `j _start`, which resets
        // the CPU and nothing else -- the peripherals keep whatever state they
        // were in. IF set from the last command before a reboot would be
        // reported by the next thing that looked at SR, and now that IEN is set
        // above it would assert the line before anything was ready to clear it.
        // `irq::claim_type_c` clears the pending bit for exactly the same
        // reason and after exactly the same bug.
        //
        // BEFORE the source is enabled, and that ordering is load-bearing: this
        // is the FUSB302B rule applied to a second peripheral -- clear whatever
        // the previous session left asserting, then enable delivery.
        self.acknowledge_interrupt();
        let intc = crate::intc::Intc::new(crate::target::INTC_BASE);
        intc.clear(cynthion_soc_pac::base::BOARD_I2C_IRQ);
        intc.enable(cynthion_soc_pac::base::BOARD_I2C_IRQ);
    }

    /// PRER as the core holds it, low byte first.
    ///
    /// The rate a boot report quotes has to come from here and not from the
    /// prescale the build was written with: the two disagree exactly when
    /// something went wrong, which is the case worth reporting.
    pub fn prescale(&self) -> u16 {
        // SAFETY: two reads of read/write CSRs, neither with a side effect.
        unsafe {
            (read_volatile(self.reg(PRER_LO)) as u16)
                | ((read_volatile(self.reg(PRER_HI)) as u16) << 8)
        }
    }

    /// Release a slave that is holding SDA low, and return the bus to idle.
    ///
    /// **`init` resets the CONTROLLER; this resets the BUS.** A slave that lost
    /// its master mid-byte goes on driving SDA until it has clocked the rest of
    /// that byte out, and no write to CTR moves a wire. `scl` is `dir="o"`
    /// push-pull (`gateware/soc/top.py`), so the master owns the clock outright
    /// and can do this unilaterally.
    ///
    /// Nine clocks: eight data plus the acknowledge slot, which is the longest
    /// a slave can still be holding for. Then a STOP, which is what any slave
    /// listening treats as the end of a transaction.
    ///
    /// ONE command, not two. A bare `CR_STO` is issued from IDLE, where SCL is
    /// high, and the engine's STOP state drops SCL and pulls SDA low in the same
    /// slot -- so SDA can fall on a high clock, which is a START (#355). Folding
    /// the STOP into the read reaches the STOP state from RACK, where SCL is
    /// already low. ~10 us at 1 MHz.
    pub fn recover(&self) {
        let _ = self.command(CR_RD | CR_ACK | CR_STO);
    }

    fn status(&self) -> u8 {
        // SAFETY: a read of SR, which is a wire from flags and has no side
        // effect. In particular it does not clear IF -- that needs a write.
        unsafe { read_volatile(self.reg(CMD_STATUS)) }
    }

    /// Turns of the spin in [`I2c::command`] before the bus is given up on.
    ///
    /// - **Waits for** one bit-engine command to drop `SR.TIP`.
    /// - **Expected worst case** is the longest command this driver issues, 12 bit
    ///   periods, each `5 * (PRER + 1)` sync cycles -- the engine's own arithmetic,
    ///   read from the prescale the core holds rather than from the rate the build
    ///   was written with. 600 cycles at the configured prescale of 9 (#272).
    /// - **Multiplier 1.25x**, over a turn assumed as short as [`CYCLES_PER_TURN`]:
    ///   150 turns at prescale 9. Assumed SHORT on purpose -- a turn that
    ///   compiles tighter needs more turns to cover the same wait, so the low
    ///   assumption is the safe one.
    /// - **On expiry** [`I2c::command`] records the command byte, this limit and the
    ///   turns spent, and returns [`Error::Timeout`]. `i2c status` prints the record.
    ///
    /// Invariant to the sync clock: the CPU and SCL both derive from it, so a slower
    /// part makes the wait longer and the turns proportionally slower. It is the
    /// PRESCALE that moves this, which is why `i2c soak` cannot outrun it.
    fn wait_limit(&self) -> u32 {
        let cycles = LONGEST_COMMAND_PERIODS * CYCLES_PER_PERIOD * (self.prescale() as u32 + 1);
        // x1.25 over cycles/CYCLES_PER_TURN, folded: 5 turns per 4 cycles.
        cycles / CYCLES_PER_TURN * 5 / 4
    }

    /// Issue one command and wait for it to finish. Returns SR.
    fn command(&self, command: u8) -> Result<u8, Error> {
        let limit = self.wait_limit();
        // SAFETY: a write to CR, the peripheral's one side-effecting address.
        unsafe { write_volatile(self.reg(CMD_STATUS), command) };

        let mut waits = 0u32;
        loop {
            let status = self.status();
            if status & SR_TIP == 0 {
                if status & SR_AL != 0 {
                    return Err(Error::ArbitrationLost);
                }
                return Ok(status);
            }
            waits += 1;
            if waits > limit {
                record_timeout(command, limit, waits);
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
    ///
    /// Conditional on BUSY: with no START outstanding there is nothing to
    /// release, and a STOP issued from IDLE puts a START on the wire instead
    /// (#355). Every error path called this, so the recovery cost the next
    /// transfer on that bus.
    fn release(&self) {
        if self.status() & SR_BUSY != 0 {
            let _ = self.command(CR_STO);
        }
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
