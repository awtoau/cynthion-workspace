//! Drive the HyperRAM BIST engine from the CPU. See #226.
//!
//! # Why the CPU drives this
//!
//! Every HyperRAM figure this project has recorded was taken with at least one
//! broken instrument -- five overlapping faults fixed between 5 and 7 August
//! 2026. Nothing measured before then discriminates, so the numbers have to be
//! re-established from zero rather than confirmed.
//!
//! This is the rig for that. The engine is a CSR peripheral, not a memory
//! window, so the measurement path has no `JTAGRegisterInterface` in it (#204)
//! and no decoder, cache, arbiter or `RegisteredResponse` bubble between the
//! engine and the part. The unfinished SoC DQS write path is therefore not in
//! the way.
//!
//! # The rule this module enforces
//!
//! **A cell that passes without its negative control having fired is recorded as
//! NO RESULT, not as a pass.** A comparator that never fires and a part that
//! never errs produce identical output, and this project has mistaken one for
//! the other more than once. [`Cell::verdict`] is the only way to read a result
//! and it cannot return `Pass` without both halves.
//!
//! # Register window
//!
//! The addresses are the engine's own (`hyperram_ceiling_top.py`), deliberately
//! not renumbered: two rigs sharing a numbering is what lets a number from one
//! be compared with a number from the other.
//!
//! Each address appears **twice**, because no `amaranth_soc` CSR field is both
//! CPU-writable and gateware-driven, and which one an address needs is not known
//! until the engine elaborates -- after the bank must exist. So:
//!
//! ```text
//! base + 0x000 + 4*N    parameter N, the CPU writes
//! base + 0x100 + 4*N    result N, the engine drives
//! ```
//!
//! [`Bist::write`] targets the low window and [`Bist::read`] the high one, which
//! is not an arbitrary convention: every register this file writes is an engine
//! parameter and every one it reads is an engine result. Reading a parameter's
//! number out of the result window yields zero rather than an error, so
//! [`Bist::present`] is the guard -- `ID` is a result, and if the offset were
//! wrong it would not match [`APPLET_ID`] and no measurement would be taken.

#![allow(dead_code)]

use crate::clock::Hz;
use crate::uart::Uart;
use core::fmt::Write;

/// The arithmetic, with no register access in it, so it can be tested on the
/// host (#337). Re-exported rather than hidden: `Axes` and `Verdict` are this
/// module's vocabulary and callers should not have to know where they live.
pub mod pure;

pub use pure::{Axes, Cell, Census, Readback, Tally, Verdict};
use pure::{cr0_word, cr1_word, eye_centre, latency_clocks, latency_code_legal,
           poll_limit, rank, scored, HEADING, LEGEND};

/// Engine register numbers, from `gateware/probes/hyperram/hyperram_ceiling_top.py`.
pub mod reg {
    pub const ID: usize = 1;
    pub const STATUS: usize = 2;
    pub const WRITE_CYCLES: usize = 3;
    pub const READ_CYCLES: usize = 4;
    pub const ERRORS: usize = 7;
    pub const WORDS: usize = 8;
    pub const DIE: usize = 9;
    pub const CLOCK: usize = 10;
    pub const CONFIG: usize = 11;
    pub const BAD_INDEX: usize = 12;
    pub const BAD_GOT: usize = 13;
    pub const BAD_WANT: usize = 14;
    pub const CONTROL: usize = 15;
    pub const READCLKSEL: usize = 16;
    pub const ACTUAL: usize = 17;
    pub const GOLDEN: usize = 18;
    pub const DEVICE_CR0: usize = 19;
    pub const DEVICE_CR1: usize = 20;
    pub const PASS_LIMIT: usize = 21;
    /// The engine FSM's state, so a stall says WHERE rather than only that it
    /// stalled. The engine's own comment: without it a hung sweep is a silent
    /// poll loop, and the cell index does not say which state.
    pub const FSM_STATE: usize = 28;
    /// The READ PATH's state: the controller handshake in bits 0..3 -- `idle`,
    /// `recovery elapsed`, `start`, `stalled` (sticky) -- and the readback trust
    /// rules in bits 8..10, see [`pure::trust`].
    ///
    /// Not an FSM state: in the engine the controller's state number was
    /// declared and never bound, so it read zero and looked like "state 0". Bits
    /// 0..1 are the two halves of what `READ_RECOVER` waits on, which is the
    /// state the engine parks in; reading them separately says WHICH half is
    /// false, and a state number never could.
    pub const CTRL_STATE: usize = 29;
    /// CR0 AS THE PART REPORTS IT, read back after configuring.
    ///
    /// The one register that can say whether a configuration write landed. Until
    /// this existed the engine only ever WROTE the part's registers, so a write
    /// that went nowhere was indistinguishable from one that worked -- and a
    /// latency sweep passed on codes 2 and 6 where the datasheet says 0, 1, 2
    /// and 15 are legal and 3..13 are RESERVED. 2 is the power-on default and 6
    /// is that default with one bit flipped.
    pub const DEVICE_READBACK: usize = 30;
    /// CR1 as the part reports it. Winbond's application note (rev P01, s6.5.5):
    /// a part without differential-clock support silently DISCARDS a write to
    /// `CR1[6]`, so the readback is a capability probe and not only a liveness
    /// check. Without it "the dif/se axis has no effect" and "the dif/se axis
    /// never moved" are the same measurement. (#334, #336)
    pub const DEVICE_READBACK_CR1: usize = 31;
}

/// `REG_CONTROL` bits, from `BISTHarness`.
pub mod control {
    pub const GO: u32 = 1 << 0;
    pub const NEGATIVE: u32 = 1 << 1;
}

/// `REG_STATUS` bits, from `BISTHarness`.
pub mod status {
    pub const BUSY: u32 = 1 << 0;
    pub const DONE: u32 = 1 << 1;
    pub const ERROR: u32 = 1 << 2;
    pub const NEGATIVE: u32 = 1 << 3;
}

/// The applet's own identifier, "HRC1". Read back before anything else: a rig
/// that is not there reads as zeroes, and zero errors from a peripheral that
/// does not exist is the most flattering wrong answer available.
pub const APPLET_ID: u32 = 0x4852_4331;

/// Words per burst, from `BURST_WORDS` in the engine. The poll bound below is
/// derived from it, so the two must agree.
const BURST_WORDS: u32 = 128;

/// Values the `sel` axis walks. Live only on a DQS build -- see [`Bist::distinct`].
const SEL_VALUES: u32 = 8;


/// Where the BIST peripheral sits, matching `HYPERRAM_BIST_BASE` in
/// `gateware/soc/top.py`.
///
/// A literal rather than a PAC constant, and deliberately: the PAC is generated
/// from whichever variant the gateware flag selects, and only one of the two can
/// be committed. Taking this from the PAC would mean committing the measurement
/// variant's map, which would leave the shipping image checking itself against a
/// map it does not have. `tests/test_bist_constants.py` asserts this equals the
/// gateware's, so the two cannot drift silently.
pub const BASE: usize = 0xf000_0800;

/// The CK rung selector's CSR base, and the same literal-not-PAC reasoning as
/// `BASE` above. Mirrors `HYPERRAM_CK_BASE` in `gateware/soc/top.py`.
pub const CK_BASE: usize = 0xf000_0a00;

/// Which HyperRAM CK a two-rung bitstream drives. See #228, #313.
///
/// The gateware has exposed this since `HyperRAMClockSelect` landed; nothing on
/// the CPU could reach it, so a two-rung build could only ever be measured at
/// rung 0 and the second rung was dead weight in the bitstream.
///
/// Register map is `gateware/soc/peripherals/hyperram_ck.py`: `ctrl.sel` at
/// +0x00, `status.locked`/`status.rungs` at +0x04, the two rung frequencies in
/// kHz at +0x08 and +0x0c. kHz because every reachable rung is a whole number of
/// them, so nothing rounds.
pub struct Ck {
    base: usize,
}

impl Ck {
    /// # Safety
    /// `base` must be the CK selector's CSR base.
    pub const unsafe fn new(base: usize) -> Self {
        Self { base }
    }

    fn read(&self, offset: usize) -> u32 {
        // SAFETY: `offset` is one of the four registers the map defines.
        unsafe { core::ptr::read_volatile((self.base + offset) as *const u32) }
    }

    /// How many rungs this bitstream carries. 1 or 2 -- `DCSC` is a 2:1 mux and
    /// the two DCS bels do not cascade, so 2 is the silicon's limit, not ours.
    pub fn rungs(&self) -> u32 {
        (self.read(0x04) >> 4) & 0xF
    }

    /// Is the PLL locked? Both rungs are outputs of one VCO, so this covers both.
    pub fn locked(&self) -> bool {
        self.read(0x04) & 1 != 0
    }

    /// Which rung is live.
    pub fn selected(&self) -> u32 {
        self.read(0x00) & 1
    }

    /// This rung's CK in kHz. Rung 1 reads 0 on a one-rung build.
    pub fn khz(&self, rung: u32) -> u32 {
        self.read(if rung == 0 { 0x08 } else { 0x0c })
    }

    /// Select a rung. Glitchless in `DCSC` with both clocks running, and no
    /// settling wait is needed -- see the peripheral's own docs for why the
    /// engine does not have to be idle across the change.
    pub fn select(&self, rung: u32) {
        // SAFETY: `ctrl` is a writable register at the base.
        unsafe {
            core::ptr::write_volatile(self.base as *mut u32, rung & 1);
        }
    }
}

/// Why a poll stopped.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Poll {
    Done,
    /// The bound expired. Carries the bound and the spins actually taken, so
    /// the report can say whether it was close or nowhere near.
    TimedOut { limit: u32, spins: u32 },
}

/// One cell, with the engine's evidence registers sampled at the time.
///
/// A sweep that walks several cells before choosing which row to print (#421)
/// would otherwise attach the LAST cell's first-bad word and FSM state to it.
#[derive(Clone, Copy)]
pub struct Measured {
    pub cell: Cell,
    status: [u32; 2],
    poll: [Poll; 2],
    /// `(index, got, want)`, sampled only when the pass had errors.
    bad: Option<(u32, u32, u32)>,
    /// The two FSM states, twice, sampled only when a poll expired.
    fsm: Option<[u32; 4]>,
}

/// The BIST peripheral's register window.
pub struct Bist {
    base: usize,
}

impl Bist {
    /// # Safety
    /// `base` must be the BIST peripheral's CSR base.
    pub const unsafe fn new(base: usize) -> Self {
        Self { base }
    }

    /// Byte offset of the result window. Mirrors
    /// `BistCsrTransport.RESULT_WINDOW` in `gateware/soc/peripherals/bist_csr.py`.
    const RESULT_WINDOW: usize = 0x100;

    /// A result the engine drives. See the module docs for why this is not the
    /// same address as the parameter of the same number.
    pub fn read(&self, reg: usize) -> u32 {
        let addr = (self.base + Self::RESULT_WINDOW + 4 * reg) as *const u32;
        unsafe { core::ptr::read_volatile(addr) }
    }

    /// A parameter the CPU sets and the engine reads.
    pub fn write(&self, reg: usize, value: u32) {
        let addr = (self.base + 4 * reg) as *mut u32;
        unsafe { core::ptr::write_volatile(addr, value) }
    }

    /// Is the engine present and answering? Checked before any measurement.
    pub fn present(&self) -> bool {
        self.read(reg::ID) == APPLET_ID
    }

    /// Which PHY this bitstream carries. `REG_CONFIG` bit 0 in the engine.
    pub fn dqs(&self) -> bool {
        self.read(reg::CONFIG) & 1 != 0
    }

    /// How many of `cells` are DISTINCT configurations, and the repeat count.
    ///
    /// `readclksel` reaches the PHY only on a DQS build; on a non-DQS one the
    /// register is written and read by nothing, so eight consecutive cells are
    /// one configuration measured eight times (#343). Proven from the elaborated
    /// design by `scripts/hyperram_axis_wiring.py`, not from a flat result set.
    pub fn distinct(&self, cells: u32) -> (u32, u32) {
        if self.dqs() { (cells, 1) } else { (cells / SEL_VALUES, SEL_VALUES) }
    }

    /// The line every sweep prints when its cell count overstates what it varied.
    fn say_repeats(&self, uart: &mut Uart, cells: u32) {
        let (distinct, repeats) = self.distinct(cells);
        if repeats > 1 {
            let _ = writeln!(
                uart, "  {} DISTINCT configurations x {} repeats: `sel` reaches \
                       nothing without the DQS PHY (#343)", distinct, repeats);
        }
    }

    /// Set the axis values. Held still for the whole pass, which is what makes
    /// the domain crossing safe without a FIFO.
    ///
    /// Bit 16 is "apply this"; without it the part keeps its power-on
    /// configuration. The gateware sweep once left it clear and swept a drive
    /// axis that therefore did nothing -- eight rows that should have been
    /// identical, and were not. The words themselves are `pure::cr0_word` /
    /// `cr1_word`, which is where the forced full-power bits live.
    pub fn configure(&self, axes: &Axes) {
        const APPLY: u32 = 1 << 16;
        self.write(reg::READCLKSEL, axes.readclksel as u32 & 0x7);
        self.write(reg::DEVICE_CR0, APPLY | cr0_word(axes));
        self.write(reg::DEVICE_CR1, APPLY | cr1_word(axes));
    }

    /// Run one pass and wait for it. `negative` builds the deliberately-wrong
    /// comparison, which must report every word wrong.
    ///
    /// Returns `(errors, words, status, poll)`. On a timeout `words` is zero,
    /// which `verdict()` reads as NoResult.
    pub fn run(&self, negative: bool, passes: u32) -> (u32, u32, u32, Poll) {
        self.write(reg::PASS_LIMIT, passes);
        // Arm the control BEFORE `go`. Arming after the engine has started is
        // exactly the defect that made every pre-2026-08-06 zero meaningless:
        // the comparison had already happened by the time the mode changed.
        let mode = if negative { control::NEGATIVE } else { 0 };
        self.write(reg::CONTROL, mode);
        self.write(reg::CONTROL, mode | control::GO);
        self.write(reg::CONTROL, mode);

        let limit = poll_limit(passes);
        let mut spins: u32 = 0;
        let mut last = self.read(reg::STATUS);
        while last & status::DONE == 0 {
            spins = spins.wrapping_add(1);
            if spins > limit {
                return (0, 0, last, Poll::TimedOut { limit, spins });
            }
            last = self.read(reg::STATUS);
        }
        (self.read(reg::ERRORS), self.read(reg::WORDS), last, Poll::Done)
    }

    /// One cell: the real pass and its control, in that order.
    pub fn cell(&self, axes: Axes, passes: u32) -> (Cell, [u32; 2], [Poll; 2]) {
        self.cell_with_control(axes, passes, None)
    }

    /// One cell, optionally reusing a control taken earlier in the sweep.
    ///
    /// The control proves the COMPARATOR fires, which is a property of the rig
    /// and not of the axes -- so running it per cell doubled every sweep to
    /// re-establish one bit. `carry` is a control measured once at the start;
    /// there is no failure mode where the comparator works for cell 3 and not
    /// cell 4 that a per-cell run would catch and a per-sweep one would not.
    ///
    /// A SHORT control is still caught per cell, from `control_words`, which is
    /// the defect #424 was: a cell whose control compared fewer words than the
    /// real pass was scored PASS.
    pub fn cell_with_control(&self, axes: Axes, passes: u32,
                             carry: Option<(u32, u32, u32, Poll)>)
        -> (Cell, [u32; 2], [Poll; 2])
    {
        let (errors, words, st_real, p_real) = self.run(false, passes);
        let (control_errors, control_words, st_ctrl, p_ctrl) = match carry {
            Some((ce, cw, st, p)) => (ce, cw, st, p),
            None => self.run(true, passes),
        };
        (
            Cell { axes, errors, words, control_errors, control_words },
            [st_real, st_ctrl],
            [p_real, p_ctrl],
        )
    }

    /// The control pass alone, for a sweep that takes it once.
    pub fn control_pass(&self, passes: u32) -> (u32, u32, u32, Poll) {
        self.run(true, passes)
    }

    /// One cell AND its evidence, sampled before the next cell overwrites it.
    /// The unit every sweep runs; see [`Measured`].
    pub fn measured(&self, axes: Axes, passes: u32,
                    carry: Option<(u32, u32, u32, Poll)>) -> Measured
    {
        let (cell, status, poll) = self.cell_with_control(axes, passes, carry);
        Measured {
            cell,
            status,
            poll,
            bad: (cell.errors != 0).then(|| (self.read(reg::BAD_INDEX),
                                             self.read(reg::BAD_GOT),
                                             self.read(reg::BAD_WANT))),
            fsm: poll.iter().any(|p| matches!(p, Poll::TimedOut { .. }))
                .then(|| self.sample_fsm()),
        }
    }

    /// Decode STATUS for a human. Why a cell produced nothing is usually here.
    pub fn describe_status(&self, uart: &mut Uart, label: &str, st: u32) {
        print_status(uart, label, st);
    }

    /// The two FSM states, which is where a stall actually is.
    ///
    /// Read TWICE with the second read after the first, so a state that is
    /// moving can be told from one that is parked. A single sample cannot
    /// distinguish "wedged in state 3" from "cycling through state 3", and those
    /// want opposite investigations.
    pub fn describe_fsm(&self, uart: &mut Uart) {
        print_fsm(uart, self.sample_fsm());
    }

    /// Both states, twice, so a stall can be printed after later cells have run.
    fn sample_fsm(&self) -> [u32; 4] {
        [self.read(reg::FSM_STATE), self.read(reg::CTRL_STATE),
         self.read(reg::FSM_STATE), self.read(reg::CTRL_STATE)]
    }

    /// What the engine says about itself, before anything is measured.
    pub fn describe(&self, uart: &mut Uart) {
        let _ = writeln!(uart, "  id      {:#010x} {}", self.read(reg::ID),
                         if self.present() { "HRC1" } else { "NOT THE ENGINE" });
        let _ = writeln!(uart, "  clock   {} kHz as built  config {:#x}  {}",
                         self.read(reg::CLOCK), self.read(reg::CONFIG),
                         if self.dqs() { "DQS PHY" }
                         else { "non-DQS PHY -- `sel` reaches nothing (#343)" });
        self.describe_status(uart, "at rest", self.read(reg::STATUS));

        // NOTHING IS LATCHED UNTIL A CELL HAS RUN.
        //
        // `CONFIG_VERIFY` in `hyperram_ceiling_top.py` is what reads CR0 and
        // CR1 back off the part, and only a commanded run reaches it. On a cold
        // board both registers hold their reset value -- 0x0000 -- and the
        // block below used to decode that into `latency code 0 variable
        // drive 0` and then attribute it to a broken register read path. Both
        // are claims about a part nothing has spoken to.
        //
        // The discriminator is the ENGINE's `write_cycles`, not a flag this
        // firmware keeps: it is only ever incremented, never cleared, and its
        // first increment is `WRITE_START`, which every run reaches through the
        // config path. So it stays right across a reconfigure this firmware did
        // not initiate, which a session flag would not.
        //
        // What it cannot separate: a sweep driven from the gateware leaves the
        // host's apply bits clear, and `CONFIG_CR1` then jumps straight to
        // `WRITE_START` without verifying -- the counter advances with nothing
        // latched. `configure` below always sets both apply bits, so reaching
        // that state needs a sweep started by something other than this shell.
        //
        // AND THE READBACK CAPTURES THROUGH THE CELL'S OWN PHASE, so at a phase
        // outside the window it is a word from another transaction. `readback`
        // decides; a withheld report carries no number, so nothing below can
        // decode one. (#366)
        let write_cycles = self.read(reg::WRITE_CYCLES);
        match pure::readback(write_cycles, self.read(reg::CTRL_STATE),
                             self.read(reg::DEVICE_READBACK),
                             self.read(reg::DEVICE_READBACK_CR1)) {
            Readback::NotLatched => {
                let _ = writeln!(uart,
                    "  CR0/CR1 NOT LATCHED -- no cell has run since this bitstream \
                     was configured, so nothing has been read back off the part");
            }
            Readback::Withheld(why) => {
                let _ = writeln!(uart,
                    "  CR0/CR1 WITHHELD -- {}. Re-run at a capture phase inside \
                     the proven window, then read this again", why);
            }
            Readback::Reported { cr0, cr1 } => {
                // WHAT THE PART SAYS, against what it was told. Datasheet default
                // is 0x8F2F: latency code 2 (7 clocks), fixed, drive 34 ohms.
                //
                // The code decoded, because the field is SPARSE: `5 + sext4(code)`,
                // so 14 and 15 are the two SHORTEST at 3 and 4 clocks and sit below
                // code 0. Reading the raw code as if it were monotonic is a mistake
                // the sweeps invite.
                let code = ((cr0 >> 4) & 0xf) as u8;
                let _ = writeln!(
                    uart, "  CR0     part reports {:#06x}  latency code {} = {} clocks{}  \
                           {}  drive {}",
                    cr0, code, latency_clocks(code),
                    if latency_code_legal(code) { "" } else { " RESERVED" },
                    if cr0 & 8 != 0 { "fixed" } else { "variable" },
                    (cr0 >> 12) & 0x7);
                // CR1[6] IS A CAPABILITY, not a setting. The vendor's application
                // note (rev P01, s6.5.5) says a part without differential-clock
                // support silently discards a write to this bit -- so the readback
                // is the only thing that separates "the axis has no effect" from
                // "the axis never moved", which are the same measurement without
                // it (#334, #336).
                let _ = writeln!(
                    uart, "  CR1     part reports {:#06x}  clock {}  hybrid sleep {}",
                    cr1,
                    if cr1 & (1 << 6) != 0 { "single-ended" } else { "differential" },
                    if cr1 & (1 << 5) != 0 { "ON -- the part is asleep" } else { "off" });
                if cr1 == 0 || cr1 == 0xffff {
                    let _ = writeln!(uart,
                        "  CR1     READBACK IS {:#06x} -- so the dif/se axis cannot be \
                         judged in either direction", cr1);
                }
                if cr0 == 0 || cr0 == 0xffff {
                    let _ = writeln!(uart,
                        "  CR0     READBACK IS {:#06x} after a run -- the register read \
                         path is not working, so nothing here says the write landed", cr0);
                }
            }
        }
        self.describe_fsm(uart);
        // Counters the engine drives from `hr`. If `hr` is not running at all,
        // every one of these is frozen -- which is the first thing to rule out,
        // because `clock` above is a constant baked in at elaboration and says
        // nothing about whether the PLL locked.
        let _ = writeln!(uart, "  cycles  write {}  read {}  words {}  errors {}",
                         write_cycles, self.read(reg::READ_CYCLES),
                         self.read(reg::WORDS), self.read(reg::ERRORS));
        let _ = writeln!(uart, "  compare actual {:#010x}  golden {:#010x}",
                         self.read(reg::ACTUAL), self.read(reg::GOLDEN));
    }
}

/// One STATUS word, decoded. Takes the word, not the engine, so a row printed
/// after later cells have run still describes its own pass.
fn print_status(uart: &mut Uart, label: &str, st: u32) {
    let _ = writeln!(
        uart, "      {}: status {:#06x} busy={} done={} error={} negative={}",
        label, st,
        (st & status::BUSY != 0) as u8,
        (st & status::DONE != 0) as u8,
        (st & status::ERROR != 0) as u8,
        (st & status::NEGATIVE != 0) as u8);
}

/// [`Bist::sample_fsm`]'s four words, decoded.
fn print_fsm(uart: &mut Uart, words: [u32; 4]) {
    let [engine_a, ctrl_a, engine_b, ctrl_b] = words;
    let _ = writeln!(
        uart, "  fsm     engine {} -> {} {}",
        engine_a, engine_b,
        if engine_a == engine_b { "(parked)" } else { "(moving)" });
    // The two halves of READ_RECOVER's exit condition, named. Whichever is
    // 0 is the one holding the engine there.
    let _ = writeln!(
        uart, "  ctrl    idle={} recovered={} start={}  (a=%{:03b} b=%{:03b})",
        (ctrl_b & 1) as u8, ((ctrl_b >> 1) & 1) as u8, ((ctrl_b >> 2) & 1) as u8,
        ctrl_a & 0b111, ctrl_b & 0b111);
    // STICKY, and not cleared by `go`. A run that had to be rescued from a
    // stall is not a clean run, and this must survive into the next reading
    // -- a rig that recovers silently is a rig that reports a number taken
    // after something went wrong.
    if ctrl_b & 0b1000 != 0 {
        let _ = writeln!(
            uart, "  STALLED the controller failed to return to idle and \
                   the engine was reset (sticky; power-cycle to clear)");
    }
}

/// The `sel` census as a row SUFFIX. `None` prints nothing, for the sweeps that
/// do not walk a phase.
struct Sel(Option<Census>);

impl core::fmt::Display for Sel {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self.0 {
            Some(census) => write!(f, "{census}"),
            None => Ok(()),
        }
    }
}

/// A failed cell's evidence, as a SUFFIX to its row rather than lines under it.
///
/// Everything here is empty on a clean pass, so a passing sweep is unchanged.
struct Evidence<'a>(&'a Measured);

impl core::fmt::Display for Evidence<'_> {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        let m = self.0;
        // Which half expired, how far it got and against what. A silent expiry
        // is worse than no bound at all.
        for (label, p) in [("real", m.poll[0]), ("control", m.poll[1])] {
            if let Poll::TimedOut { limit, spins } = p {
                write!(f, "  {label} TIMEOUT {spins}/{limit} spins")?;
            }
        }
        // WHERE it stalled. A parked engine never asked for a transaction; a
        // parked controller asked and got no answer. `STALL` is sticky (#409).
        if let Some([engine_a, ctrl_a, engine_b, ctrl_b]) = m.fsm {
            write!(f, "  fsm {engine_a}>{engine_b}{}  ctrl %{:03b}>%{:03b}",
                   if engine_a == engine_b { " parked" } else { "" },
                   ctrl_a & 0b111, ctrl_b & 0b111)?;
            if ctrl_b & 0b1000 != 0 {
                write!(f, " STALLED-STICKY")?;
            }
        }
        // The first mismatch and both values: one bad word and a million bad
        // words are different faults, and the pattern is invertible so a value
        // decodes back to the address it belongs to.
        if let Some((index, got, want)) = m.bad {
            write!(f, "  bad[{index:#x}] got {got:#010x} want {want:#010x}")?;
            // 16-bit rotation == one device word of slip (#186). NOT proof the
            // phase is right: the eye has this residue at `sel 0` (#421).
            if got.rotate_left(16) == want || got == want.rotate_left(16) {
                write!(f, " ROT16(#186)")?;
            }
        }
        Ok(())
    }
}

/// Print one cell's row, and the evidence when it produced nothing. Returns the
/// verdict the row states, so the caller's tally cannot disagree with it.
///
/// `expected` is how many words the cell was ASKED to compare; [`pure::scored`]
/// is where shortness is folded into the verdict.
fn report(uart: &mut Uart, m: &Measured, expected: u32, verbose: bool,
          census: Option<Census>) -> Verdict {
    let (cell, st, poll) = (&m.cell, m.status, m.poll);
    let short = cell.words < expected || cell.control_words < expected;
    let timed_out = matches!(poll[0], Poll::TimedOut { .. })
        || matches!(poll[1], Poll::TimedOut { .. });
    let outcome = scored(cell, expected);
    let verdict = match outcome {
        Verdict::Pass => "PASS",
        Verdict::Fail(_) if short => "fail (SHORT -- fewer words than asked)",
        Verdict::Fail(_) => "fail",
        // A timeout reaches NoResult by the same door as a control that did not
        // fire, and the two want completely different next steps -- so they must
        // not print the same text.
        Verdict::NoResult if timed_out => "NO RESULT -- engine never completed (timeout)",
        Verdict::NoResult if short => "NO RESULT -- short cell, fewer words than asked",
        Verdict::NoResult => "NO RESULT -- control did not fire",
    };
    // ONE `writeln!`, EVERY field in it including the evidence. A row that
    // wraps is a row `bist_rows.py` and a reader both have to reassemble. #423.
    //
    // The stamp is per-row so per-cell duration is in the data.
    let _ = writeln!(
        uart, "{}  {:3}  {:4}  {:5}  {:3}  {:3}  {:8}  {:8}  {:8}  {}{}{}",
        crate::log::now(),
        cell.axes.latency,
        if cell.axes.fixed_latency { "fix" } else { "var" },
        cell.axes.drive,
        if cell.axes.single_ended_clock { "se" } else { "dif" },
        cell.axes.readclksel,
        cell.errors, cell.words, cell.control_errors, verdict, Evidence(m),
        Sel(census));

    if verbose {
        print_status(uart, "real   ", st[0]);
        print_status(uart, "control", st[1]);
    }
    outcome
}

/// What every table carries above it: the speeds and the heading.
///
/// The column legend is NOT here. It is four lines of static text that never
/// changes, and reprinting it above every four-row sweep costs more attention
/// than it repays -- `bist legend` prints it when it is wanted.
fn preamble(uart: &mut Uart, bist: &Bist) {
    say_speeds(uart, bist);
    let _ = writeln!(uart, "{}", HEADING);
}

/// The column legend, on request.
pub fn legend(uart: &mut Uart) {
    for line in LEGEND {
        let _ = writeln!(uart, "{}", line);
    }
}

/// The CK the rows below were taken at, and the sync clock. A table whose
/// frequency is not on it cannot be compared with another table.
fn say_speeds(uart: &mut Uart, bist: &Bist) {
    // SAFETY: `CK_BASE` is the selector's CSR base, and this path exists only in
    // the engine-bearing build, where that window is decoded (#409).
    let ck = unsafe { Ck::new(CK_BASE) };
    match ck.rungs() {
        // No selector: `REG_CLOCK` is the constant baked in at elaboration, so
        // it says what was ASKED for and nothing about whether the PLL locked.
        0 => {
            let _ = writeln!(uart, "  CK {} as built, no rung selector",
                             Hz::khz(bist.read(reg::CLOCK)));
        }
        rungs => {
            let live = ck.selected();
            let _ = writeln!(uart, "  CK {}  rung {} of {}  PLL {}",
                             Hz::khz(ck.khz(live)), live, rungs,
                             if ck.locked() { "locked" }
                             else { "NOT LOCKED -- no row below means anything" });
        }
    }
    // `sync` as the fabric COUNTS it, not as the design asked: a PLL with its
    // feedback unconnected reported the constant while `sync` was not running.
    match crate::clock::measured() {
        Some(m) => {
            let _ = writeln!(uart, "  sync {} counted  PLL {}", Hz::khz(m.khz),
                             if m.locked { "locked" } else { "NOT LOCKED" });
        }
        None => {
            let _ = writeln!(uart, "  sync not counted yet -- the {} us window \
                                    has not completed", crate::clock::WINDOW_US);
        }
    }
}

/// One cell, by hand. The unit a sweep is made of, so nothing is exercised in a
/// sweep that was not first exercised alone.
pub fn one(uart: &mut Uart, bist: &Bist, axes: Axes, passes: u32) {
    if !gate(uart, bist) {
        return;
    }
    preamble(uart, bist);
    bist.configure(&axes);
    let measured = bist.measured(axes, passes, None);
    report(uart, &measured, passes * BURST_WORDS, true, None);
}

/// The rig's own smoke test: four cells, one CK, four values of a LIVE axis.
///
/// From `docs/chips/hyperram/bist-plan.md`, and it is a test **of the rig**, not
/// of the part. The result that validates it is NOT four passes:
///
///   * at least one `PASS` -- the rig can report success
///   * at least one `fail` -- the rig can DETECT failure, without which a pass
///     is worthless
///   * the control fired in all four -- zero errors means something everywhere
///
/// Four passes is a failed smoke test: either every phase is genuinely good,
/// which is implausible, or the rig cannot see a fault. Four NO RESULTs is a
/// wedged engine, not a bad part.
pub fn smoke(uart: &mut Uart, bist: &Bist, passes: u32) {
    if !gate(uart, bist) {
        return;
    }
    // FOUR CELLS THAT DIFFER. The capture phase reaches nothing without the DQS
    // PHY (#343), so there the four cells walk the latency code instead: 2 and 6
    // pass on this part, 0 and 15 are legal and fail. Four repeats can only meet
    // this test's criterion by being marginal, which made it unfalsifiable.
    let dqs = bist.dqs();
    let values: [u8; 4] = if dqs { [0, 1, 2, 3] } else { [2, 6, 0, 15] };
    let _ = writeln!(uart, "  four cells, one drive, four {}.",
                     if dqs { "capture phases" } else { "latency codes" });
    let _ = writeln!(uart, "  wanted: at least one PASS *and* at least one fail.");
    let _ = writeln!(uart, "  four passes means the rig cannot see a fault.");
    preamble(uart, bist);

    let mut tally = Tally::default();
    for value in values {
        let axes = Axes {
            drive: 3, single_ended_clock: false, fixed_latency: true,
            readclksel: if dqs { value } else { 0 },
            latency: if dqs { 2 } else { value },
        };
        bist.configure(&axes);
        let measured = bist.measured(axes, passes, None);
        tally.add(report(uart, &measured, passes * BURST_WORDS, false, None));
    }

    let _ = writeln!(uart, "  {}", tally);
    let _ = writeln!(uart, "  rig: {}", match (tally.pass, tally.fail, tally.no_result) {
        (_, _, n) if n == tally.total() => "WEDGED -- fix the rig, this says nothing about the part",
        (_, _, n) if n > 0 => "INCOMPLETE -- some cells produced no result",
        (0, _, _) => "cannot report success -- no cell passed",
        (_, 0, _) => "NOT VALIDATED -- nothing failed, so a pass is not evidence",
        _ => "validated -- it can both pass and detect a fault",
    });
}

/// The FULL runtime cross product: latency x fixed/var x drive x clock x phase.
///
/// 16 x 2 x 8 x 2 x 8 = 4,096 cells. At the measured ~2 ms per cell that is
/// about 8 seconds, so there is no reason to sweep these axes SEPARATELY -- and
/// sweeping them separately is what has been done until now, which cannot see an
/// INTERACTION. Latency was swept at one phase and phase at one latency; if the
/// two interact, every reading so far was blind to it.
///
/// Prints only rows that are not a clean PASS, plus a tally. 4,096 lines at
/// 115200 baud is 40 seconds of console for a result that is one number.
pub fn all(uart: &mut Uart, bist: &Bist, passes: u32) {
    if !gate(uart, bist) {
        return;
    }
    let _ = writeln!(uart, "  4096 cells: latency x fix/var x drive x clock x phase");
    bist.say_repeats(uart, 4096);
    let _ = writeln!(uart, "  printing only what is NOT a clean pass");
    preamble(uart, bist);

    let expected = passes * BURST_WORDS;
    // ONE control for the sweep, not one per cell. It proves the comparator
    // fires, which is a property of the rig -- so this halves the sweep and
    // loses nothing. A SHORT control is still caught per cell (#424).
    let carry = bist.control_pass(passes);
    let mut tally = Tally::default();
    for latency in 0u8..16 {
        for fixed_latency in [true, false] {
            for drive in 0u8..8 {
                for single_ended_clock in [false, true] {
                    for readclksel in 0u8..8 {
                        let axes = Axes { drive, single_ended_clock, readclksel,
                                          latency, fixed_latency };
                        bist.configure(&axes);
                        let measured = bist.measured(axes, passes, Some(carry));
                        // A clean pass is counted and NOT printed: 4096 rows at
                        // 115200 baud is 40 seconds of console for one number.
                        if matches!(scored(&measured.cell, expected), Verdict::Pass) {
                            tally.pass += 1;
                            continue;
                        }
                        tally.add(report(uart, &measured, expected, false, None));
                    }
                }
            }
        }
    }
    let _ = writeln!(uart, "  {}", tally);
    // AFTER the summary the host parses, and repeated: a tally of 4096 read
    // without this line overstates what was measured by eight times.
    bist.say_repeats(uart, 4096);
}

/// Sweep the part's LATENCY, at EVERY capture phase, one row per code.
///
/// `CR0[7:4]` is the initial latency code and `CR0[3]` selects fixed or
/// variable. Every reading this project has taken used the power-on 0010b with
/// fixed latency, because the CR0 write hardcoded 0x8F2F and replaced only the
/// drive field.
///
/// **The phase is walked, never assumed (#421).** A row pinned to one `sel` is a
/// statement about that phase whenever the phase is outside the read eye, and
/// the eye has moved between builds -- a whole 32-row sweep once reported
/// 896/1024 at every code from `sel 0` alone. So each code is run at all eight
/// phases and reported at the one that did best, with a census line under it
/// naming what else was tried.
///
/// On a non-DQS build `sel` reaches nothing (#343), so there is one phase to
/// walk and the sweep says so rather than measuring the same cell eight times.
pub fn latency(uart: &mut Uart, bist: &Bist, passes: u32) {
    if !gate(uart, bist) {
        return;
    }
    let walked = if bist.dqs() { SEL_VALUES as u8 } else { 1 };
    let _ = writeln!(uart, "  {} cells: CR0[7:4] latency code x fix/var \
                            x {} capture phase(s), at drive 3",
                     32 * walked as u32, walked);
    let _ = writeln!(uart, "  one row per code at its best phase; `sel` ON it \
                            says what else was tried");
    if walked == 1 {
        let _ = writeln!(uart, "  `sel` reaches nothing without the DQS PHY \
                                (#343), so the phase is not an axis here");
    }
    preamble(uart, bist);

    // ONE control for the sweep, not one per cell. It proves the comparator
    // fires, which is a property of the rig -- so this halves the sweep and
    // loses nothing. A SHORT control is still caught per cell (#424).
    let carry = bist.control_pass(passes);
    let expected = passes * BURST_WORDS;
    let mut tally = Tally::default();
    for latency in 0u8..16 {
        for fixed_latency in [true, false] {
            let mut runs: [Option<Measured>; SEL_VALUES as usize] = [None; 8];
            let mut passed = 0u8;
            for readclksel in 0..walked {
                let axes = Axes { drive: 3, single_ended_clock: false,
                                  readclksel, latency, fixed_latency };
                bist.configure(&axes);
                let measured = bist.measured(axes, passes, Some(carry));
                if matches!(scored(&measured.cell, expected), Verdict::Pass) {
                    passed |= 1 << readclksel;
                }
                runs[readclksel as usize] = Some(measured);
            }
            let pick = pick_phase(&runs, passed, walked, expected);
            // SAFETY of the unwrap: every phase below `walked` was just run, and
            // `pick_phase` only ever returns one of them.
            if let Some(measured) = runs[pick as usize] {
                tally.add(report(uart, &measured, expected, false,
                                 Some(Census { walked, passed, pick })));
            }
        }
    }
    let _ = writeln!(uart, "  {}", tally);
}

/// Which phase's row stands for a code: the eye's centre when anything passed,
/// else the least-bad cell -- so a fail row is the code's best case, not a
/// phase's worst.
fn pick_phase(runs: &[Option<Measured>], passed: u8, walked: u8, expected: u32) -> u8 {
    if let Some(centre) = eye_centre(passed, walked) {
        return centre;
    }
    let mut pick = 0u8;
    for phase in 1..walked {
        let better = match (runs[phase as usize], runs[pick as usize]) {
            (Some(a), Some(b)) => rank(&a.cell, expected) < rank(&b.cell, expected),
            (Some(_), None) => true,
            _ => false,
        };
        if better {
            pick = phase;
        }
    }
    pick
}

/// Sweep drive x clock x readclksel and print a row per cell.
///
/// Printed as it goes rather than collected: the gateware sweep died at cell 0
/// with no way to see why (#210), and a row per cell means a hang names the cell
/// it hung on.
pub fn sweep(uart: &mut Uart, bist: &Bist, passes: u32, verbose: bool) {
    if !gate(uart, bist) {
        return;
    }
    let _ = writeln!(uart, "  {} passes per cell, 128 cells", passes);
    bist.say_repeats(uart, 128);
    preamble(uart, bist);

    // ONE control for the sweep, not one per cell. It proves the comparator
    // fires, which is a property of the rig -- so this halves the sweep and
    // loses nothing. A SHORT control is still caught per cell (#424).
    let carry = bist.control_pass(passes);
    let mut tally = Tally::default();
    for drive in 0u8..8 {
        for single_ended_clock in [false, true] {
            for readclksel in 0u8..8 {
                let axes = Axes { drive, single_ended_clock, readclksel, latency: 2, fixed_latency: true };
                bist.configure(&axes);
                // BEFORE the pass, so a cell that never returns is named by the
                // last line printed rather than leaving a blank terminal.
                if verbose {
                    let _ = writeln!(uart, "  cell drive {} {} sel {} -- running",
                                     drive,
                                     if single_ended_clock { "se" } else { "dif" },
                                     readclksel);
                }
                let measured = bist.measured(axes, passes, Some(carry));
                tally.add(report(uart, &measured, passes * BURST_WORDS, verbose, None));
            }
        }
    }
    let _ = writeln!(uart, "  {}", tally);
}

/// Refuse to measure through an engine that is not answering.
fn gate(uart: &mut Uart, bist: &Bist) -> bool {
    if bist.present() {
        return true;
    }
    let _ = writeln!(uart,
                     "  no engine at {:#010x} (id {:#010x}, want {:#010x})",
                     BASE, bist.read(reg::ID), APPLET_ID);
    let _ = writeln!(uart,
                     "  this image was built without CYNTHION_HYPERRAM_BIST=1");
    false
}

