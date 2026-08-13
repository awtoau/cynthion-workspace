//! The one I2C controller, the mux in front of it, and the rule that there is
//! only ever one of each.
//!
//! - Board has THREE physically separate I2C buses, ONE controller to drive
//!   them: both FUSB302Bs answer to address `0x22` and can't be told apart on
//!   one wire (`gateware/soc/peripherals/i2c_mux.py`). A mux register says
//!   which pin-set the controller is wired to; nothing in a reply says which
//!   bus it came from -- both Type-C controllers even return the same identity
//!   byte, `0x91`. A stale select produces a plausible answer from the wrong
//!   chip, not an error -- the class of fault found months later or not at all.
//!
//! ## The select is a parameter, not a mode
//!
//! - Every method here that moves a byte takes the bus as its first argument
//!   and writes the select itself, immediately before the transfer. No
//!   `select()` on this type, so "talk to a device without saying which bus"
//!   isn't a sentence this API can form. One uncached byte store against a
//!   transfer of milliseconds: caching it would save nothing and create a
//!   second copy of the truth.
//! - Gateware holds a select change until the controller is idle, so this
//!   can't move the bus underneath a transfer even called at the wrong moment.
//!
//! ## One owner, enforced rather than agreed
//!
//! - `i2c` and `mux` are PRIVATE submodules. `Bus` is the only thing that can
//!   construct an [`i2c::I2c`] or a [`mux::Mux`], so a second driver holding
//!   its own handle to the same controller does not compile. Error type, bus
//!   numbers, PAC195x identity registers all re-exported below, so nothing
//!   pays for that in convenience.
//! - Devices go one step further: a part whose protocol spans more than one
//!   transaction has exactly one owner, everybody else reads what that owner
//!   cached:
//!
//! | device   | protocol spanning transactions           | its one owner          |
//! |----------|--------------------------------------------|--------------------------|
//! | PAC1954  | REFRESH, then read what it latched          | `power::Monitor::poll` |
//! | FUSB302B | configure; read all three INTERRUPT regs    | `typec::Controllers`   |
//!
//! - Issue #123: the PAC1954 is unavailable for 1 ms after a REFRESH and
//!   answers a read inside that window by acknowledging its address then
//!   NACKing the register pointer, so a second caller reading the part on its
//!   own account collides with the poller's window ~2% of the time (1 ms in a
//!   50 ms poll interval). With one owner the collision is impossible, not
//!   rare.
//!
//! ## `&mut self`, and why there is no lock
//!
//! - Every transaction takes `&mut self`, so the compiler proves only one
//!   caller has the bus at a time, at no cost in code.
//! - Deliberately no mutex, no critical section. Nothing in this firmware
//!   performs I2C from interrupt context, by design: the Type-C handler MASKS
//!   its source and defers its I2C (now ~80 us at the bus's 1 MHz, was ~1 ms at
//!   the earlier 80 kHz, #269) to the main loop (`src/irq.rs`, `src/typec.rs`),
//!   precisely so a long spin never happens inside a handler. A lock added
//!   today would guard against a preemption that cannot occur, and would say --
//!   in the code, permanently -- that this system has concurrent bus users
//!   when it does not. A worse lie than no lock at all.
//! - What the shape here buys instead: the day something does want the bus
//!   from a handler, the change is a critical section inside these four
//!   methods -- one place, already holding exclusive access -- rather than an
//!   audit of every call site to find who else might be mid-transfer.

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
/// write of `CR.IACK`. Acknowledging while the peripheral still asserts
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

// `last_timeout` is a pure read of a record, so it does not breach the rule that
// only this module may reach the controller: it starts no transfer.
pub use i2c::{last_timeout, pac195x, Error};
pub use mux::{
    BUS_AUX_C, BUS_POWER_MONITOR, BUS_TARGET_C, LINE_AUX_FAULT, LINE_AUX_INT, LINE_TARGET_FAULT,
    LINE_TARGET_INT,
};

use i2c::I2c;
use mux::Mux;

/// What the bus is aimed at. `f_SCL = f_sync / (5 * (PRER + 1))`.
pub const I2C_SCL_HZ: u32 = 1_000_000;
/// Sync cycles per bit period, the bit engine's own arithmetic.
const CYCLES_PER_PERIOD: u32 = 5;

/// PRER for `scl_hz` at `sync_hz`, rounded so the rate never OVERSHOOTS.
///
/// Mirrors `prescale_for` in `gateware/soc/peripherals/i2c_master.py`.
pub fn prescale_for(sync_hz: u32, scl_hz: u32) -> u16 {
    let divisor = CYCLES_PER_PERIOD * scl_hz;
    (sync_hz.div_ceil(divisor).max(1) - 1) as u16
}

/// The sync clock to size the prescale from, and whether it was COUNTED.
///
/// `target::TIME_HZ` is what the build assumed; the clock monitor counts what
/// the silicon does, against the one oscillator that cannot be wrong. The two
/// disagree on any variant that moved `SYNC_MHZ` -- the BIST build runs 50 MHz
/// against a constant of 60, so a prescale derived from the constant sets
/// 833 kHz and reports it as 1 MHz (#272).
pub fn sync_hz() -> (u32, bool) {
    match crate::clock::measured() {
        Some(m) if m.locked && m.khz != 0 => (m.khz * 1000, true),
        _ => (crate::target::TIME_HZ, false),
    }
}

/// What [`Bus::init`] established and read back.
pub struct Init {
    /// PRER as the controller holds it, not as the build asked for it.
    pub prescale: u16,
    pub wanted: u16,
    pub scl_hz: u32,
    /// The sync clock the prescale was derived from, and its source.
    pub sync_hz: u32,
    pub sync_measured: bool,
}

impl Init {
    pub fn established(&self) -> bool {
        self.prescale == self.wanted
    }
}

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

    /// `i2c_init()` -- the CONTROLLER and the WIRE, which are two resets.
    ///
    /// Idempotent, and re-runnable: called at boot, and again by the `i2c` scan
    /// command, because a scan is also how a wedged bus gets recovered. Not
    /// called per transaction -- it writes CTR and clears the completion flag,
    /// and the power monitor's poll runs twenty times a second.
    ///
    /// **`I2c::init` resets the controller; `I2c::recover` resets the bus.**
    /// Only the first existed. A slave left mid-transaction by a CPU reset --
    /// which resets neither the peripheral nor the parts on it -- goes on
    /// holding SDA low regardless of what is written to CTR, and nine clocks
    /// plus a STOP is what releases it. Unconditional because it is harmless on
    /// a healthy bus and because a recovery path that only runs when something
    /// is already broken is a path nothing exercises.
    ///
    /// Non-destructive: no device is addressed and nothing is written to one.
    pub fn init(&mut self) -> Init {
        // Re-derived on every call, so a boot that ran before the clock
        // monitor's first window still picks the counted rate on the next
        // `init i2c` rather than keeping the build's guess for ever.
        let (sync_hz, sync_measured) = sync_hz();
        self.prescale = prescale_for(sync_hz, I2C_SCL_HZ);
        self.i2c.init(self.prescale);
        self.i2c.recover();
        let prescale = self.i2c.prescale();
        Init {
            prescale,
            wanted: self.prescale,
            // Derived the way the CONTROLLER derives it, from the prescale it
            // is actually holding: `f_SCL = f_sync / (5 * (PRER + 1))`. The
            // divider is an integer, so this is what the bus gets rather than
            // what was asked for.
            scl_hz: sync_hz / (CYCLES_PER_PERIOD * (prescale as u32 + 1)),
            sync_hz,
            sync_measured,
        }
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

    /// Reprogram the bit rate, for finding where the bus stops working.
    ///
    /// **This exists because the rate ceiling cannot be computed.** It depends
    /// on SDA's rise time, which depends on bus capacitance, which is a
    /// property of the copper and is not in any datasheet. The arithmetic can
    /// say a rate is out of spec; only the board can say whether it works.
    ///
    /// `I2c::init` writes PRER with the core disabled, which is the datasheet's
    /// requirement -- the bit engine's slot timer reloads from it, so changing
    /// it under a running transfer moves an edge that has already been set up.
    /// Going through `init` rather than poking the register is what keeps that
    /// true.
    ///
    /// A rate that does not work does not announce itself. The failure is a
    /// device that answers MOST of the time, so anything using this must soak
    /// rather than probe once.
    pub fn set_prescale(&mut self, prescale: u16) {
        self.prescale = prescale;
        self.i2c.init(prescale);
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
