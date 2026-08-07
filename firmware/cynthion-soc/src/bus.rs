//! The one I2C controller, the mux in front of it, and the rule that there is
//! only ever one of each.
//!
//! The board has THREE physically separate I2C buses and ONE controller to drive
//! them, because both FUSB302Bs answer to address `0x22` and cannot be told apart
//! on one wire (`gateware/soc/peripherals/i2c_mux.py`). A register in the mux says which
//! pin-set the controller is wired to, and nothing in a reply says which bus it
//! came from -- both Type-C controllers even return the same identity byte,
//! `0x91`. So a stale select does not produce an error. It produces a plausible
//! answer from the wrong chip, which is the class of fault that is found months
//! later or not at all.
//!
//! ## The select is a parameter, not a mode
//!
//! Every method here that moves a byte takes the bus as its first argument and
//! writes the select itself, immediately before the transfer. There is no
//! `select()` on this type, so "talk to a device without saying which bus" is not
//! a sentence this API can form -- the omission that would cause the fault above
//! is not available to make. One uncached byte store against a transfer of
//! milliseconds: caching it would save nothing and would create a second copy of
//! the truth.
//!
//! The gateware holds a select change until the controller is idle, so this
//! cannot move the bus underneath a transfer even if it were called at the wrong
//! moment.
//!
//! ## One owner, enforced rather than agreed
//!
//! `i2c` and `mux` are PRIVATE submodules. `Bus` is the only thing in this
//! firmware that can construct an [`i2c::I2c`] or a [`mux::Mux`], so a second
//! driver holding its own handle to the same controller is not a mistake anyone
//! can make here; it does not compile. Everything a caller needs -- the error
//! type, the bus numbers, the PAC195x identity registers -- is re-exported below,
//! so nothing pays for that in convenience.
//!
//! Devices go one step further: a part whose protocol spans more than one
//! transaction has exactly one owner, and everybody else reads what that owner
//! cached.
//!
//! | device  | protocol spanning transactions          | its one owner              |
//! |---------|-----------------------------------------|----------------------------|
//! | PAC1954 | REFRESH, then read what it latched      | `power::Monitor::poll`     |
//! | FUSB302B| configure; read all three INTERRUPT regs| `typec::Controllers`       |
//!
//! That is what issue #123 is about. The PAC1954 is unavailable for 1 ms after a
//! REFRESH and answers a read inside that window by acknowledging its address and
//! then NACKing the register pointer, so a second caller reading the part on its
//! own account collides with the poller's window about 2% of the time. With one
//! owner the collision is not rare, it is impossible.
//!
//! ## `&mut self`, and why there is no lock
//!
//! Every transaction takes `&mut self`, so the compiler proves that only one
//! caller has the bus at a time, at no cost in code.
//!
//! There is deliberately no mutex and no critical section. Nothing in this
//! firmware performs I2C from interrupt context and that is a design rule, not an
//! accident: the Type-C handler MASKS its source and defers its ~1 ms of I2C to
//! the main loop (`src/irq.rs`, `src/typec.rs`), precisely so that a long spin
//! never happens inside a handler. A lock added today would guard against a
//! preemption that cannot occur, and would say -- in the code, permanently --
//! that this system has concurrent bus users when it does not. That is a worse
//! lie than no lock at all.
//!
//! What the shape here buys instead is that the day something does want the bus
//! from a handler, the change is a critical section inside these four methods --
//! one place, which already holds exclusive access -- rather than an audit of
//! every call site to find who else might be mid-transfer.

mod i2c;
mod mux;

/// Clear the I2C controller's completion flag. For the interrupt handler, and
/// for nothing else.
///
/// **Here rather than in `irq.rs`, because one owner of the bus is enforced by
/// these modules being private to `bus` and not by everyone remembering.**
/// `scripts/soc_i2c_owner_sim.py` asserts that nothing outside this file
/// constructs an `I2c`, and it caught the first version of this, which did.
///
/// It does NOT take a `&mut Bus`, and that is the point. Source 3 is a level:
/// `irq.eq(irq_flag & ien)` in the gateware, with `irq_flag` cleared only by a
/// write of `CR.IACK`. Completing at the PLIC while the peripheral still asserts
/// re-delivers immediately. The handler therefore has to clear it while a driver
/// in normal context owns the transaction -- so this is deliberately the one
/// operation on the controller that needs no ownership: a single store, of a
/// command with none of START, STOP, READ or WRITE set, which the gateware
/// checks for and which moves nothing on the wire.
///
/// A transfer in flight is untouched by it. `IF` and `TIP` are different bits,
/// and `command()` waits on `TIP`.
pub fn acknowledge_i2c_interrupt() {
    if let Some(board) = crate::target::BOARD {
        i2c::I2c::new(board.i2c).acknowledge_interrupt();
    }
}

pub use i2c::{pac195x, Error};
pub use mux::{
    BUS_AUX_C, BUS_POWER_MONITOR, BUS_TARGET_C, LINE_AUX_FAULT, LINE_AUX_INT, LINE_TARGET_FAULT,
    LINE_TARGET_INT,
};

use i2c::I2c;
use mux::Mux;

/// The board's I2C controller and the mux that points it at a bus.
///
/// Constructed once, in `Devices`, and passed by `&mut` to whoever is using it.
/// Not a `static`: a bus reachable from anywhere is a bus an interrupt handler
/// can reach, which is the same argument `src/irq.rs` makes about consoles.
pub struct Bus {
    i2c: I2c,
    mux: Mux,
    /// Kept so [`Bus::init`] needs no argument and cannot be called with the
    /// wrong one. The value comes from `target::Board`, where the reason for it
    /// is written down.
    prescale: u16,
}

impl Bus {
    pub const fn new(i2c_base: usize, mux_base: usize, prescale: u16) -> Self {
        Bus {
            i2c: I2c::new(i2c_base),
            mux: Mux::new(mux_base),
            prescale,
        }
    }

    /// Set the prescale and enable the controller. Idempotent.
    ///
    /// Called once at boot, and again by the `i2c` scan command -- a scan is also
    /// how a wedged bus gets recovered, and re-initialising is what unwedges it.
    /// Not called per transaction: it writes CTR and clears the completion flag,
    /// and the power monitor's poll runs twenty times a second.
    pub fn init(&mut self) {
        self.i2c.init(self.prescale);
    }

    /// Does anything answer at this seven-bit address, on this bus?
    pub fn probe(&mut self, bus: u8, address: u8) -> Result<bool, Error> {
        self.mux.select(bus);
        self.i2c.probe(address)
    }

    /// Read `out.len()` bytes from a device's registers, starting at `register`.
    pub fn read_registers(
        &mut self,
        bus: u8,
        address: u8,
        register: u8,
        out: &mut [u8],
    ) -> Result<(), Error> {
        self.mux.select(bus);
        self.i2c.read_registers(address, register, out)
    }

    /// Write one byte to a device register.
    pub fn write_register(
        &mut self,
        bus: u8,
        address: u8,
        register: u8,
        value: u8,
    ) -> Result<(), Error> {
        self.mux.select(bus);
        self.i2c.write_register(address, register, value)
    }

    /// Write consecutive bytes to one device register pointer.
    pub fn write_registers(
        &mut self,
        bus: u8,
        address: u8,
        register: u8,
        values: &[u8],
    ) -> Result<(), Error> {
        self.mux.select(bus);
        self.i2c.write_registers(address, register, values)
    }

    /// SMBus Send Byte: one byte with no register pointer in front of it.
    ///
    /// The PAC1954's REFRESH is one of these. See [`i2c::I2c::send_byte`] for why
    /// it cannot be written as a register write with a dummy payload.
    pub fn send_byte(&mut self, bus: u8, address: u8, byte: u8) -> Result<(), Error> {
        self.mux.select(bus);
        self.i2c.send_byte(address, byte)
    }

    /// The four Type-C signals: `int` for each controller, then `fault`.
    ///
    /// `&self`, because it moves nothing on any bus -- it is a read of four wires
    /// through synchronisers, and it is pure. Reading it does NOT clear anything;
    /// what clears an FUSB302B's interrupt is reading the FUSB302B, over the bus,
    /// from its one owner.
    pub fn lines(&self) -> u8 {
        self.mux.lines()
    }

    // What the `i2c` and `typec` commands print about the hardware itself. These
    // report what this object is actually driving, rather than a second reading
    // of `target::BOARD`, so a shell line cannot describe a peripheral the
    // driver is not using.

    pub fn i2c_base(&self) -> usize {
        self.i2c.base()
    }

    pub fn mux_base(&self) -> usize {
        self.mux.base()
    }

    pub fn prescale(&self) -> u16 {
        self.prescale
    }

    /// Which bus the controller is pointed at right now.
    ///
    /// For reporting only. Nothing here reads this to decide whether to select,
    /// because deciding not to select is the whole fault this module prevents.
    pub fn selected(&self) -> u8 {
        self.mux.selected()
    }
}
