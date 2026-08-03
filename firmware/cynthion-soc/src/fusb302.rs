//! The two FUSB302B Type-C controllers, and the one bus they take turns on.
//!
//! **Both answer to I2C address `0x22`**, which is why the board gives them
//! separate pin-sets and why there is a mux at all: two devices at one fixed
//! address cannot be told apart on one wire. So a transaction here is always
//! two steps -- select the bus, then talk -- and the select is what stands in
//! for an address. `docs/chips/fusb302b-type-c.md` has the part's register map;
//! it is an external device, so its registers are not in the generated PAC.
//!
//! ## What is configured, and what is deliberately not
//!
//! Enough for the controller to interrupt on a state change, and nothing that
//! changes what the port presents to whatever is plugged into it:
//!
//!   * `POWER` -- bandgap and wake, the measure block, and the receiver. Not the
//!     internal oscillator, which is only needed to *send* PD messages.
//!   * `MASK`  -- unmask `I_BC_LVL` and `I_VBUSOK`, mask the rest.
//!   * `MASKA`/`MASKB` -- everything masked. They carry PD and hard-reset
//!     events, and nothing here acts on one yet; unmasking them would produce
//!     interrupts with no handler, which on a shared level-sensitive line is a
//!     storm rather than a curiosity.
//!   * `SWITCHES0` -- `MEAS_CC1` only.
//!   * `CONTROL0` -- clear `INT_MASK`, which is what lets the `INT` pin assert
//!     at all.
//!
//! **The CC pull-downs (`PDWN1`/`PDWN2`) are NOT enabled, and that is the one
//! decision here worth arguing about.** They would give full attach detection,
//! and they are the only bit in this sequence that changes the port
//! electrically: presenting Rd tells a source this port is a sink. One of these
//! two controllers is on AUX, which is the port carrying the USB console this
//! firmware answers on -- so the failure mode of getting it wrong is losing the
//! console, on a board whose Type-C CC lines have never been driven by anything
//! in this tree. `MEAS_CC1` routes CC1 to the internal comparator and drives
//! nothing, so `BC_LVL` still reports the voltage a source's Rp puts on CC and
//! `VBUSOK` still reports power appearing and disappearing. That is a state
//! change on both ports, obtained without asserting anything onto a connector.
//!
//! Enabling the pull-downs is a one-line change with a known consequence, and
//! this comment is what the next person should read before making it.
//!
//! ## The interrupt, and why the handler does not touch this file
//!
//! **One PLIC source per `int` line**, not an OR of the two -- see
//! `ecp5-test/riscv/i2c_mux.py`, which says so where the sources are wired, and
//! `docs/decisions.md` decision 8 for why. This comment used to claim the
//! opposite, while citing the file that contradicts it.
//!
//! The distinction is not cosmetic. A SHARED level obliges whatever services it
//! to clear *every* asserting device before the source is live again, or the line
//! stays high, the interrupt re-fires immediately, and the CPU makes no progress
//! -- a hang. One source per device removes that obligation rather than
//! documenting it, and the PLIC had 27 spare sources, so sharing would have
//! bought nothing.
//!
//! Each line is still a LEVEL, so the trap in `docs/chips/fusb302b-type-c.md`
//! applies per port: a source whose device has not been read stays asserted.
//!
//! Clearing means reading the device's `INTERRUPT` registers, which is an I2C
//! transaction of about a millisecond at 80 kHz over the same controller the
//! foreground uses for the power monitor. Doing that inside a handler would be
//! two things this firmware refuses: a long spin in interrupt context, and a
//! second master on a peripheral with no lock. So the handler MASKS the source
//! and records the event (`src/irq.rs`, `src/events.rs`), and [`service`] --
//! called from the main loop -- clears every asserting device and re-enables it.
//! The storm cannot happen because the source is off for the whole window in
//! which the line is still asserted.

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

/// Present Rp on both receptacle pins while autonomous source polling resolves
/// orientation. The sink pull-down bits are clear: TARGET-C is deliberately
/// changing to the opposite role, while AUX remains measure-only and untouched.
const SWITCHES0_SOURCE: u8 = (1 << 7) | (1 << 6);

/// `MASK`: unmask `I_BC_LVL` (bit 0) and `I_VBUSOK` (bit 7), mask everything
/// else. A 1 in this register MASKS, so the value is the complement.
const MASK_WANTED: u8 = !(0b1000_0001);

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

/// What one controller reports about its port.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct State {
    /// `DEVICE_ID`. `0x91` on both of these.
    pub device_id: u8,
    /// VBUS is present on this port.
    pub vbus: bool,
    /// The CC voltage band the comparator reports: 0 nothing, 1 vRd-USB, 2
    /// vRd-1.5, 3 vRd-3.0. Meaningful only with the measure block routed to a
    /// CC pin, which [`configure`] does.
    pub bc_lvl: u8,
    /// `STATUS1A.TOGSS`: 1/2 mean source polling settled on CC1/CC2.
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

/// Read and discard all three interrupt registers.
///
/// **This is what drops the `INT` line**, and all three have to be read: they
/// are read-to-clear, the line is the OR of them inside the part, and leaving
/// one set leaves the pin asserted. On a shared level-sensitive source that is
/// the storm the module comment describes, so "read every one, always" is the
/// rule rather than "read the one you expect".
///
/// **`typec::Controllers::service` is the only caller, and that is a rule.** The
/// three registers are read-to-clear, so any second reader takes the event away
/// from the thing that services it, and the symptom is an interrupt whose cause
/// nobody can name -- the same shape of fault as reading the PAC1954 out from
/// under its poller, and harder to see. `state()` below is what a reporter wants,
/// and it touches nothing that clears.
pub fn clear(bus: &mut Bus, port: Port) -> Result<(), bus::Error> {
    read(bus, port, REG_INTERRUPT)?;
    read(bus, port, REG_INTERRUPTA)?;
    read(bus, port, REG_INTERRUPTB)?;
    Ok(())
}

/// What one controller currently says about its port.
///
/// Safe for anyone to call: `DEVICE_ID` and `STATUS0` are plain registers with no
/// read side effect and no window around them, so unlike [`clear`] this does not
/// need a single owner. A part with an interrupt pending still has it pending
/// afterwards.
pub fn state(bus: &mut Bus, port: Port) -> Result<State, bus::Error> {
    let device_id = read(bus, port, REG_DEVICE_ID)?;
    let status0 = read(bus, port, REG_STATUS0)?;
    let status1a = read(bus, port, REG_STATUS1A)?;
    Ok(State {
        device_id,
        vbus: status0 & STATUS0_VBUSOK != 0,
        bc_lvl: status0 & STATUS0_BC_LVL,
        source_cc: (status1a >> 3) & 0b111,
    })
}

impl State {
    /// One line's worth of description, for the shell and the change log.
    pub fn cc(&self) -> &'static str {
        match (self.source_cc, self.bc_lvl) {
            (1, _) => "source on CC1",
            (2, _) => "source on CC2",
            (_, 0) => "nothing on CC",
            (_, 1) => "vRd-USB (default current)",
            (_, 2) => "vRd-1.5A",
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
