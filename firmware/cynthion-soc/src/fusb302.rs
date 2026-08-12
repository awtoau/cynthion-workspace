//! The two FUSB302B Type-C controllers, and the one bus they take turns on.
//!
//! - **Both answer to I2C address `0x22`**, why the board gives them separate
//!   pin-sets and why there's a mux at all: two devices at one fixed address
//!   can't be told apart on one wire. A transaction here is always two steps --
//!   select the bus, then talk -- the select stands in for an address.
//!   `docs/chips/fusb302b-type-c.md` has the part's register map; external
//!   device, so its registers are not in the generated PAC.
//!
//! ## What is configured, and what is deliberately not
//!
//! Enough for the controller to interrupt on a state change, nothing that
//! changes what the port presents to whatever is plugged in:
//!
//! - `POWER` -- bandgap, wake, measure block, receiver. Not the internal
//!   oscillator, only needed to *send* PD messages.
//! - `MASK` -- unmask `I_BC_LVL` and `I_VBUSOK`, mask the rest.
//! - `MASKA`/`MASKB` -- everything masked. Carry PD and hard-reset events;
//!   nothing here acts on one yet, and unmasking would produce interrupts with
//!   no handler -- a storm, not a curiosity, on a shared level-sensitive line.
//! - `SWITCHES0` -- `MEAS_CC1` only. [`state`] toggles the measure select to
//!   `MEAS_CC2` and back to read the other band; that select routes a pin to an
//!   internal comparator and drives nothing, so the port is unchanged
//!   electrically either way.
//! - `CONTROL0` -- clear `INT_MASK`, what lets the `INT` pin assert at all.
//!
//! **The CC pull-downs (`PDWN1`/`PDWN2`) are NOT enabled -- the one decision
//! here worth arguing about.** They'd give full attach detection and are the
//! only bit in this sequence that changes the port electrically: presenting Rd
//! tells a source this port is a sink. One of these two controllers is on AUX,
//! carrying the USB console this firmware answers on -- so getting it wrong
//! loses the console, on a board whose Type-C CC lines have never been driven
//! by anything in this tree. `MEAS_CC1` routes CC1 to the internal comparator
//! and drives nothing, so `BC_LVL` still reports the voltage a source's Rp puts
//! on CC and `VBUSOK` still reports power appearing/disappearing -- a state
//! change on both ports, obtained without asserting anything onto a connector.
//! Enabling the pull-downs is a one-line change with a known consequence; this
//! comment is what the next person should read before making it.
//!
//! ## The interrupt, and why the handler does not touch this file
//!
//! - **One PLIC source per `int` line**, not an OR of the two -- see
//!   `gateware/soc/peripherals/i2c_mux.py` (where the sources are wired) and
//!   `docs/architecture.md` decision 8 (why).
//! - Not cosmetic: a SHARED level obliges whatever services it to clear *every*
//!   asserting device before the source is live again, or the line stays high,
//!   the interrupt re-fires immediately, and the CPU makes no progress -- a
//!   hang. One source per device removes the obligation rather than documenting
//!   it; the PLIC had 27 spare sources, so sharing would have bought nothing.
//! - Each line is still a LEVEL, so the trap in `docs/chips/fusb302b-type-c.md`
//!   applies per port: a source whose device has not been read stays asserted.
//! - Clearing means reading the device's `INTERRUPT` registers -- an I2C
//!   transaction over the same controller the foreground uses for the power
//!   monitor, ~80 us at the 1 MHz the bus runs (`bus::I2C_SCL_HZ`, #272).
//!   Doing that inside a handler would be two things this firmware refuses: a
//!   long spin in interrupt context, and a second master on a peripheral with
//!   no lock. So the handler MASKS the source and records the event
//!   (`src/irq.rs`, `src/events.rs`), and [`service`] -- called from the main
//!   loop -- clears every asserting device and re-enables it. The storm cannot
//!   happen because the source is off for the whole window in which the line is
//!   still asserted.

use core::fmt;
use core::fmt::Write as _;

use crate::bus::{
    self, Bus, BUS_AUX_C, BUS_TARGET_C, LINE_AUX_FAULT, LINE_AUX_INT, LINE_TARGET_FAULT,
    LINE_TARGET_INT,
};

/// Both controllers, and nothing else, live here.
pub const ADDRESS: u8 = 0x22;

/// Identity and silicon revision. `0x91` is version 9 revision 1: FUSB302B
/// revision B, which is what both of these read.
const REG_DEVICE_ID: u8 = 0x01;
/// CC pull-up/pull-down enables, VCONN, and the measure-block select.
const REG_SWITCHES0: u8 = 0x02;
/// Interrupt masking, transmit, toggle, retries. Only CONTROL0 is written here.
const REG_CONTROL0: u8 = 0x06;
/// Autonomous CC role and orientation polling.
const REG_CONTROL2: u8 = 0x08;
/// The interrupt mask for the `INTERRUPT` register.
const REG_MASK: u8 = 0x0a;
/// Per-block power enables.
const REG_POWER: u8 = 0x0b;
/// Software and PD reset.
const REG_RESET: u8 = 0x0c;
/// Further interrupt masks, for `INTERRUPTA` and `INTERRUPTB`.
const REG_MASKA: u8 = 0x0e;
const REG_MASKB: u8 = 0x0f;
/// Read-to-clear. Reading these is what drops the `INT` line.
const REG_INTERRUPTA: u8 = 0x3e;
const REG_INTERRUPTB: u8 = 0x3f;
/// Autonomous toggle state, including the detected source-side CC pin.
const REG_STATUS1A: u8 = 0x3d;
const REG_INTERRUPT: u8 = 0x42;
/// Comparator result, BC_LVL, VBUSOK.
const REG_STATUS0: u8 = 0x40;

/// `RESET.SW_RES`: every register back to its power-on value.
const RESET_SW: u8 = 1 << 0;

/// `POWER`: bandgap and wake, the measure block, and the receiver.
///
/// Bit 3, the internal oscillator, is left off: it is needed to transmit PD
/// messages and nothing here transmits. Running it would cost current on a
/// board whose idle draw is a measured number this firmware reports.
const POWER_BLOCKS: u8 = 0b0000_0111;

/// `SWITCHES0`: route CC1 to the measure block, and drive nothing.
///
/// `MEAS_CC1` is bit 2. `PDWN1`/`PDWN2` (bits 0 and 1) are deliberately clear --
/// see the module comment.
const SWITCHES0_MEASURE_CC1: u8 = 1 << 2;

/// `SWITCHES0`: route CC2 to the measure block instead. `MEAS_CC2` is bit 3.
///
/// The other half of orientation. There is one comparator, so the two pins are
/// read one after the other rather than together, and [`state`] is the only
/// thing that moves this bit.
const SWITCHES0_MEASURE_CC2: u8 = 1 << 3;

/// Both measure selects, so a sweep can clear them before setting one and put
/// back exactly what it found.
///
/// The datasheet permits both at once and the result is then the OR of the two
/// pins, which is a reading that cannot be attributed to either. One at a time
/// is the only way to get two numbers out of one comparator.
const SWITCHES0_MEASURE: u8 = SWITCHES0_MEASURE_CC1 | SWITCHES0_MEASURE_CC2;

/// Present Rp on both receptacle pins while autonomous source polling resolves
/// orientation. The sink pull-down bits are clear: TARGET-C is deliberately
/// changing to the opposite role, while AUX remains measure-only and untouched.
const SWITCHES0_SOURCE: u8 = (1 << 7) | (1 << 6);

/// `MASK`: unmask `I_BC_LVL` (bit 0) and `I_VBUSOK` (bit 7), mask everything
/// else. A 1 in this register MASKS, so the value is the complement.
const MASK_WANTED: u8 = !(0b1000_0001);

/// `MASK.M_BC_LVL`, bit 0. A 1 masks.
///
/// Set across the orientation sweep in [`state`], because moving the measure
/// select changes `BC_LVL` and the part reports that as an interrupt -- one the
/// poll caused by looking. See the comment there.
const MASK_BC_LVL: u8 = 1 << 0;

/// `MASKA`/`MASKB`: mask everything. Nothing here acts on a PD or hard-reset
/// event yet, and an unmasked interrupt with no handler is a storm on a shared
/// level-sensitive line.
const MASK_ALL: u8 = 0xff;
/// Source polling needs its completion interrupt; every unrelated event stays
/// masked so the level-sensitive line cannot storm on work we do not service.
const MASK_SOURCE: u8 = MASK_ALL & !(1 << 6);

/// `CONTROL0`: everything at its reset value except `INT_MASK` (bit 5), which
/// is CLEARED so the `INT` pin may assert. Its reset value is 1 -- masked -- so
/// a part that has never been configured never interrupts, which is exactly
/// what was observed on this board before now.
const CONTROL0_INTERRUPTS_ON: u8 = 0b0000_0000;

/// `CONTROL2.MODE=SRC`, `TOGGLE=1`: poll both CC pins and settle on the one
/// carrying Rd, rather than assuming cable orientation.
const CONTROL2_SOURCE_TOGGLE: u8 = 0x07;

/// `CONTROL2.TOGGLE`, bit 0: the autonomous polling state machine is running.
///
/// Read by [`state`], and the reason it is read: while this is set the part's
/// own logic owns `SWITCHES0` -- it is what is flipping the pull-ups and the
/// measure select between CC1 and CC2 to find the cable. A sweep that wrote that
/// register underneath it would be a second writer, and the damage is not a bad
/// reading but a disturbed search. So when this bit is set the sweep is skipped
/// and orientation comes from `STATUS1A.TOGSS`, which is the answer the part
/// worked out itself.
const CONTROL2_TOGGLE: u8 = 1 << 0;

/// `STATUS0` bit 7: VBUS is above the vSafe5V threshold.
const STATUS0_VBUSOK: u8 = 1 << 7;
/// `STATUS0` bits 1:0: the CC voltage band the comparator reports.
const STATUS0_BC_LVL: u8 = 0b11;

/// Which controller, by the bus it is on.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Port {
    Target,
    Aux,
}

/// Current advertised by Rp. These are the `CONTROL0.HOST_CUR` field values,
/// not amperes the passthrough has measured or guaranteed.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum HostCurrent {
    Default = 1,
    A1_5 = 2,
    A3 = 3,
}

impl Port {
    pub const ALL: [Port; 2] = [Port::Target, Port::Aux];

    pub fn name(&self) -> &'static str {
        match self {
            Port::Target => "target",
            Port::Aux => "aux",
        }
    }

    /// The mux select value that reaches this controller.
    fn bus(&self) -> u8 {
        match self {
            Port::Target => BUS_TARGET_C,
            Port::Aux => BUS_AUX_C,
        }
    }

    /// This controller's bit in the mux's LINES register.
    fn int_bit(&self) -> u8 {
        match self {
            Port::Target => LINE_TARGET_INT,
            Port::Aux => LINE_AUX_INT,
        }
    }

    fn fault_bit(&self) -> u8 {
        match self {
            Port::Target => LINE_TARGET_FAULT,
            Port::Aux => LINE_AUX_FAULT,
        }
    }
}

/// Which CC pin the cable landed on, as far as this driver can tell.
///
/// **Unvalidated on hardware.** Deriving this needs no board, but confirming it
/// needs one cable inserted both ways round -- the reading is a claim about a
/// physical connector and nothing simulated can check it. Until that has been
/// done, treat a `Cc1`/`Cc2` here as "the comparator saw a band on that pin",
/// which is what it literally is, and not as a proven orientation.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Orientation {
    /// Neither pin was measured. The controller's own toggle machine owns
    /// `SWITCHES0` and has not settled, so this driver has nothing to go on --
    /// distinct from having looked and found nothing.
    Unknown,
    /// Both pins measured, neither shows a band. Nothing attached, or attached
    /// with no source presenting Rp.
    Unattached,
    Cc1,
    Cc2,
    /// A band on both pins. Not a contradiction: a debug or audio accessory
    /// presents on both, and so does a source whose Rp reaches both pins through
    /// a fault. Reported as itself rather than resolved to a guess.
    Both,
}

impl Orientation {
    /// The word the shell and the board tree print.
    pub fn name(&self) -> &'static str {
        match self {
            Orientation::Unknown => "?",
            Orientation::Unattached => "none",
            Orientation::Cc1 => "cc1",
            Orientation::Cc2 => "cc2",
            Orientation::Both => "both",
        }
    }
}

/// What one controller reports about its port.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct State {
    /// `DEVICE_ID`. `0x91` on both of these.
    pub device_id: u8,
    /// VBUS is present on this port.
    pub vbus: bool,
    /// The CC voltage band on CC1: 0 nothing, 1 vRd-USB, 2 vRd-1.5, 3 vRd-3.0.
    ///
    /// `None` when the sweep was skipped because the part's toggle machine owns
    /// `SWITCHES0` -- see [`CONTROL2_TOGGLE`]. A band of 0 and an absent reading
    /// are different facts and a driver that conflated them would report "nothing
    /// on CC" for a port it never looked at.
    pub cc1: Option<u8>,
    /// The same band on CC2, read by moving the measure select. Without this,
    /// a cable whose CC landed on CC2 is indistinguishable from no cable.
    pub cc2: Option<u8>,
    /// `STATUS1A.TOGSS`: 1/2 mean source polling settled on CC1/CC2, 3/4 the
    /// same as a sink.
    pub source_cc: u8,
}

/// Read one register from the controller on this port's bus.
///
/// The bus is named on the call and written by [`Bus`] immediately before the
/// transfer -- there is no way to reach the controller without saying which one,
/// which is the point of that type. A driver that remembered which bus it left
/// selected would be right until something else -- the power monitor's 50 ms
/// poll -- moved it, and the failure is not an error but a plausible answer from
/// the wrong chip. Both FUSB302Bs answer 0x22 and both report `0x91`, so reading
/// the wrong one looks exactly like reading the right one.
fn read(bus: &mut Bus, port: Port, register: u8) -> Result<u8, bus::Error> {
    let mut value = [0u8; 1];
    bus.read_registers(port.bus(), ADDRESS, register, &mut value)?;
    Ok(value[0])
}

fn write(bus: &mut Bus, port: Port, register: u8, value: u8) -> Result<(), bus::Error> {
    bus.write_register(port.bus(), ADDRESS, register, value)
}

/// Put one controller into a state where it interrupts on a change.
///
/// The order matters and each step says why. A software reset first, so this
/// starts from the register values the datasheet documents rather than from
/// whatever a previous bitstream left; then the blocks that do the measuring;
/// then the masks; and `CONTROL0` last, because it is the switch that lets the
/// pin assert and there is no reason to have it on while the rest is being set
/// up -- an interrupt raised half way through this sequence would be serviced
/// against a half-configured part.
pub fn configure(bus: &mut Bus, port: Port) -> Result<(), bus::Error> {
    write(bus, port, REG_RESET, RESET_SW)?;
    write(bus, port, REG_POWER, POWER_BLOCKS)?;
    write(bus, port, REG_SWITCHES0, SWITCHES0_MEASURE_CC1)?;
    write(bus, port, REG_MASK, MASK_WANTED)?;
    write(bus, port, REG_MASKA, MASK_ALL)?;
    write(bus, port, REG_MASKB, MASK_ALL)?;

    // Clear anything the reset or the configuration itself latched, BEFORE the
    // pin is allowed to assert. Otherwise the first thing this port does is
    // raise an interrupt about its own setup.
    clear(bus, port)?;

    write(bus, port, REG_CONTROL0, CONTROL0_INTERRUPTS_ON)
}

/// Present Rp on TARGET-C and let the controller resolve cable orientation.
///
/// There is intentionally no `Port` argument: AUX powers the board and carries
/// the console, so a caller cannot accidentally change its electrical role.
pub fn source_target(bus: &mut Bus, current: HostCurrent) -> Result<(), bus::Error> {
    let port = Port::Target;
    write(bus, port, REG_RESET, RESET_SW)?;
    write(bus, port, REG_POWER, POWER_BLOCKS)?;
    write(bus, port, REG_SWITCHES0, SWITCHES0_SOURCE)?;
    write(bus, port, REG_MASK, MASK_WANTED)?;
    write(bus, port, REG_MASKA, MASK_SOURCE)?;
    write(bus, port, REG_MASKB, MASK_ALL)?;
    clear(bus, port)?;
    write(bus, port, REG_CONTROL0, (current as u8) << 2)?;
    write(bus, port, REG_CONTROL2, CONTROL2_SOURCE_TOGGLE)
}

/// Read all three interrupt registers, and RETURN what they said.
///
/// **This is what drops the `INT` line**, and all three have to be read: they
/// are read-to-clear, the line is the OR of them inside the part, and leaving
/// one set leaves the pin asserted. On a level-sensitive source that is the storm
/// the module comment describes, so "read every one, always" is the rule rather
/// than "read the one you expect".
///
/// Reading them is compulsory, and they are the only record of WHY the line
/// asserted -- the registers cannot be
/// read a second time to find out, because the first read is what cleared them.
/// So the values come back as [`Interrupts`], which knows their names, and
/// `typec::Controllers::service` logs one line naming the cause. An interrupt
/// count that moves with no cause beside it is a number; a count with `I_BC_LVL`
/// beside it is evidence.
///
/// **`typec::Controllers::service` is the only caller, and that is a rule.** The
/// registers are read-to-clear, so any second reader takes the event away from
/// the thing that services it, and the symptom is an interrupt whose cause nobody
/// can name -- the same shape of fault as reading the PAC1954 out from under its
/// poller, and harder to see. `state()` below is what a reporter wants, and it
/// touches nothing that clears.
pub fn clear(bus: &mut Bus, port: Port) -> Result<Interrupts, bus::Error> {
    Ok(Interrupts {
        interrupt: read(bus, port, REG_INTERRUPT)?,
        interrupta: read(bus, port, REG_INTERRUPTA)?,
        interruptb: read(bus, port, REG_INTERRUPTB)?,
    })
}

/// `INTERRUPT` bit names, bit 0 first. FUSB302B datasheet page 25.
const INTERRUPT_NAMES: [&str; 8] = [
    "I_BC_LVL",
    "I_COLLISION",
    "I_WAKE",
    "I_ALERT",
    "I_CRC_CHK",
    "I_COMP_CHNG",
    "I_ACTIVITY",
    "I_VBUSOK",
];

/// `INTERRUPTA` bit names, bit 0 first.
const INTERRUPTA_NAMES: [&str; 8] = [
    "I_HARDRST",
    "I_SOFTRST",
    "I_TXSENT",
    "I_HARDSENT",
    "I_RETRYFAIL",
    "I_SOFTFAIL",
    "I_TOGDONE",
    "I_OCP_TEMP",
];

/// `INTERRUPTB` bit names. Only bit 0 is defined; bits 1-7 are reserved and are
/// printed as `3f.N` if one ever appears, because a reserved bit that is set is
/// worth seeing rather than dropping.
const INTERRUPTB_NAMES: [&str; 1] = ["I_GCRCSENT"];

/// The three read-to-clear interrupt registers, as one read of a part found them.
///
/// Every bit here has a name in the datasheet, and the whole value of the type is
/// that the name survives as far as the log line. It is [`Copy`] and 24 bits of
/// payload so it can travel through the deferred event ring as one `u32` -- see
/// [`Interrupts::payload`].
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Interrupts {
    pub interrupt: u8,
    pub interrupta: u8,
    pub interruptb: u8,
}

impl Interrupts {
    /// Pack a port index and the three registers into one event payload.
    ///
    /// One `u32`, so the record stays the fixed-size code-and-value pair
    /// `src/events.rs` insists on. The port goes in the top byte because the
    /// line has to say which connector it is about, and the alternative -- a
    /// record per port code -- would double the codes to carry one bit.
    pub const fn payload(&self, port: usize) -> u32 {
        ((port as u32 & 0xff) << 24)
            | ((self.interrupt as u32) << 16)
            | ((self.interrupta as u32) << 8)
            | self.interruptb as u32
    }

    /// The inverse, for `events::report` to render at drain time.
    pub const fn from_payload(value: u32) -> Interrupts {
        Interrupts {
            interrupt: (value >> 16) as u8,
            interrupta: (value >> 8) as u8,
            interruptb: value as u8,
        }
    }

    /// Which port a payload was about.
    pub const fn port_of(value: u32) -> usize {
        (value >> 24) as usize
    }
}

impl fmt::Display for Interrupts {
    /// The set bits by name, then all three values in hex.
    ///
    /// The hex is not redundant with the names. A reserved bit has no name, a
    /// name table can be wrong, and the raw registers are what a datasheet page
    /// can be checked against -- so the decode is offered alongside the evidence
    /// for it rather than in place of it. Three bytes is a cheap price for a log
    /// line that cannot mislead about what was actually read.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut named = false;
        for (value, names, register) in [
            (self.interrupt, &INTERRUPT_NAMES[..], REG_INTERRUPT),
            (self.interrupta, &INTERRUPTA_NAMES[..], REG_INTERRUPTA),
            (self.interruptb, &INTERRUPTB_NAMES[..], REG_INTERRUPTB),
        ] {
            for bit in 0..8 {
                if value & (1 << bit) == 0 {
                    continue;
                }
                if named {
                    f.write_char(' ')?;
                }
                named = true;
                match names.get(bit) {
                    Some(name) => f.write_str(name)?,
                    None => write!(f, "{:02x}.{}", register, bit)?,
                }
            }
        }
        if !named {
            // Not an error and not impossible: a masked event still latches in
            // these registers without ever raising the pin, and the part can
            // clear its own condition between asserting and being read. Said out
            // loud, because a blank where a cause should be reads as a bug in
            // this decoder.
            f.write_str("nothing latched")?;
        }
        write!(
            f,
            " ({:02x} {:02x} {:02x})",
            self.interrupt, self.interrupta, self.interruptb
        )
    }
}

/// What one controller currently says about its port, both CC pins included.
///
/// **The orientation this produces is unvalidated on hardware.** The sweep below
/// is written, reviewed and simulated (`scripts/soc_typec_sim.py`); what it has
/// never had is a cable inserted one way and then the other on a board. That is
/// the only test that can distinguish a correct reading from a plausible one, and
/// until it has been run `State::orientation` is a claim rather than a fact.
///
/// Still safe for anyone to call, but for a narrower reason than before. It now
/// WRITES `SWITCHES0`, because one comparator cannot see two pins at once:
///
///   * The measure select is the only field touched. The register is read first
///     and put back byte for byte, so `PDWN1`/`PDWN2` and the `PU_EN` bits the
///     source role sets are carried through untouched -- this cannot change what
///     the port presents to a connector, which is the one property the module
///     comment refuses to give up.
///   * A bus error part way through leaves the select on CC2. That is a routing
///     bit into a comparator and drives nothing, so the port is still electrically
///     as it was; the next successful call restores it.
///   * When `CONTROL2.TOGGLE` is set the part's own logic is driving this
///     register, so the sweep is skipped entirely and both bands come back
///     `None`. Orientation then comes from `TOGSS`, which is that logic's answer.
///
/// The measure block is given no explicit settling delay, and **whether it needs
/// one is unverified** (#295).
///
/// What the bus provides: each write and the read after it are separate I2C
/// transactions, three and four bytes, so at the 1 MHz the bus runs
/// (`bus::I2C_SCL_HZ`, #272) roughly tens of microseconds pass between the
/// select moving and `STATUS0` being sampled.
///
/// What the part requires: **the datasheet does not say.** It specifies no
/// settling, response or acquisition time for the measure block; `MDAC` appears
/// only as a voltage step size. So the original justification -- that hundreds
/// of microseconds was ample -- was a comparison against nothing, and the cut to
/// tens is neither safe nor unsafe on any evidence here.
///
/// **No delay is added, deliberately.** A number with no reason is exactly what
/// this project's rules forbid, and inventing one would replace an unverified
/// claim with an unverifiable one. The way to settle it is to measure: read the
/// same CC band twice, once immediately after moving the select and once after a
/// pause, and see whether they differ. If they never do at 1 MHz, the code is
/// right and can say so with evidence.
///
/// The read-only registers are unchanged in character: `DEVICE_ID`, `STATUS0`,
/// `STATUS1A` and `CONTROL2` have no read side effect, so unlike [`clear`] this
/// still takes nothing away from the thing that services an interrupt.
/// Whether [`state`] should move the measure select to read the second CC band.
///
/// **The sweep is neither free nor invisible to the part.** It writes `SWITCHES0`
/// three times, and moving the select changes what the comparator sees, which the
/// FUSB302B reports as `I_BC_LVL` -- an interrupt caused entirely by looking. It
/// also costs five I2C transactions on a bus shared with the power monitor, at
/// the bus's current 1 MHz.
///
/// Orientation only changes when a cable moves, so it is read on ATTACH and
/// carried forward rather than re-derived every poll. Doing it per poll produced
/// roughly 130 self-inflicted interrupts a second on this board.
#[derive(Copy, Clone, PartialEq, Eq)]
pub enum Bands {
    /// Read `STATUS0` with whatever select is already set. `cc1`/`cc2` come back
    /// `None` -- "not measured", which a caller carries forward from its last
    /// reading rather than treating as a change.
    Skip,
    /// Sweep both selects: the first look at a port, and after an interrupt says
    /// something moved.
    Read,
}

pub fn state(bus: &mut Bus, port: Port, bands: Bands) -> Result<State, bus::Error> {
    let device_id = read(bus, port, REG_DEVICE_ID)?;
    let control2 = read(bus, port, REG_CONTROL2)?;
    let switches0 = read(bus, port, REG_SWITCHES0)?;

    // TOGGLE means the part resolves orientation itself and owns `SWITCHES0`;
    // `Skip` means the caller already has bands and does not want them re-read.
    // Either way there is no sweep, and nothing interrupts itself.
    let (status0, cc1, cc2) = if control2 & CONTROL2_TOGGLE != 0 || bands == Bands::Skip {
        (read(bus, port, REG_STATUS0)?, None, None)
    } else {
        // Everything except the measure select, so each half of the sweep is the
        // register as found with one field replaced.
        let base = switches0 & !SWITCHES0_MEASURE;

        // MASK `I_BC_LVL` ACROSS THE SWEEP, or the poll interrupts itself.
        //
        // Moving the measure select changes which pin the comparator sees, which
        // changes `BC_LVL`, which the part reports as `I_BC_LVL` -- a real
        // interrupt caused entirely by us looking. Every `state()` call raised
        // two, `service` answered each by calling `state()`, and the board
        // produced roughly 130 interrupts a second with `serviced` climbing
        // without limit. Measured on hardware; simulation cannot show it, because
        // the model has no reason to raise an interrupt on a select it was told
        // to change.
        //
        // Masking is not enough on its own: `INTERRUPT` still LATCHES while
        // masked -- MASK gates the pin, not the register -- so the bit is read
        // and discarded below before unmasking, and the line stays quiet.
        let mask = read(bus, port, REG_MASK)?;
        write(bus, port, REG_MASK, mask | MASK_BC_LVL)?;

        write(bus, port, REG_SWITCHES0, base | SWITCHES0_MEASURE_CC1)?;
        let status0 = read(bus, port, REG_STATUS0)?;
        write(bus, port, REG_SWITCHES0, base | SWITCHES0_MEASURE_CC2)?;
        let status0_cc2 = read(bus, port, REG_STATUS0)?;
        // Back to what was found, not to `MEAS_CC1`: a caller that had neither
        // select on gets neither back.
        write(bus, port, REG_SWITCHES0, switches0)?;

        // Read-to-clear, discarding: this one is ours. A genuine BC_LVL change
        // that happened during the sweep is lost, and that is the right trade --
        // the band is read directly two lines above, so the state is captured
        // even when the edge announcing it is not.
        let _ = read(bus, port, REG_INTERRUPT)?;
        write(bus, port, REG_MASK, mask)?;

        (
            status0,
            Some(status0 & STATUS0_BC_LVL),
            Some(status0_cc2 & STATUS0_BC_LVL),
        )
    };

    let status1a = read(bus, port, REG_STATUS1A)?;
    Ok(State {
        device_id,
        // From the CC1 half, arbitrarily: `VBUSOK` is a comparator on VBUS and
        // has nothing to do with where the measure select points.
        vbus: status0 & STATUS0_VBUSOK != 0,
        cc1,
        cc2,
        source_cc: (status1a >> 3) & 0b111,
    })
}

impl State {
    /// Which CC pin the cable is on, as far as a measure-only driver can tell.
    ///
    /// **Unvalidated on hardware** -- see [`Orientation`] and [`state`]. The
    /// derivation is the whole of what is known:
    ///
    ///   * `TOGSS` wins when it has settled. The part resolved orientation with
    ///     its own state machine and that is a better answer than two bands.
    ///   * Otherwise, exactly one pin showing a band is that pin. This is the
    ///     case the driver was blind to: with only CC1 ever routed, a cable on
    ///     CC2 read as `nothing on CC` and matched the TARGET hardware log
    ///     exactly.
    ///   * Both pins, or neither, are reported as themselves.
    pub fn orientation(&self) -> Orientation {
        // TOGSS: 1 source on CC1, 2 source on CC2, 3 sink on CC1, 4 sink on CC2.
        match self.source_cc {
            1 | 3 => return Orientation::Cc1,
            2 | 4 => return Orientation::Cc2,
            _ => {}
        }
        match (self.cc1, self.cc2) {
            (Some(0), Some(0)) => Orientation::Unattached,
            (Some(_), Some(0)) => Orientation::Cc1,
            (Some(0), Some(_)) => Orientation::Cc2,
            (Some(_), Some(_)) => Orientation::Both,
            // Nothing measured and no toggle result: not "nothing attached".
            _ => Orientation::Unknown,
        }
    }

    /// The band on whichever pin the orientation picked.
    ///
    /// CC1 for anything that is not specifically CC2, including `Both` -- with a
    /// band on each pin the two are the same reading twice over, and picking the
    /// lower-numbered one keeps the answer stable rather than making it depend on
    /// which comparison ran first.
    fn band(&self) -> u8 {
        let pin = match self.orientation() {
            Orientation::Cc2 => self.cc2,
            _ => self.cc1,
        };
        pin.unwrap_or(0)
    }

    /// The two bands as characters, CC1 then CC2, `-` for a pin the sweep
    /// skipped.
    ///
    /// The evidence behind [`State::orientation`], for the command that has room
    /// to print both. A verdict of `cc2` next to `0/2` is checkable; the verdict
    /// on its own has to be trusted.
    pub fn bands(&self) -> (char, char) {
        fn digit(band: Option<u8>) -> char {
            match band {
                // 0..=3, so one digit, and no `core::fmt` integer path for it.
                Some(band) => (b'0' + (band & 0b11)) as char,
                None => '-',
            }
        }
        (digit(self.cc1), digit(self.cc2))
    }

    /// One line's worth of description, for the shell and the change log.
    ///
    /// `nothing on CC` is a statement about BOTH pins -- one about CC1 alone
    /// reads as one about the port. A port whose bands were never sampled says
    /// so instead.
    pub fn cc(&self) -> &'static str {
        match (self.source_cc, self.orientation(), self.band()) {
            (1, _, _) => "source on CC1",
            (2, _, _) => "source on CC2",
            (_, Orientation::Unknown, _) => "CC not measured",
            (_, _, 0) => "nothing on CC",
            (_, _, 1) => "vRd-USB (default current)",
            (_, _, 2) => "vRd-1.5A",
            _ => "vRd-3.0A",
        }
    }
}

/// Is this port's `int` line asserting, per the mux's LINES register?
pub fn asserting(lines: u8, port: Port) -> bool {
    lines & (1 << port.int_bit()) != 0
}

/// Is this port's `fault` line asserting?
///
/// Kept distinct from `int` all the way through, because it means something
/// different and is the one worth noticing without a register read first.
pub fn faulting(lines: u8, port: Port) -> bool {
    lines & (1 << port.fault_bit()) != 0
}
