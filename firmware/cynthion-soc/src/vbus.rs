//! The VBUS distribution switches: passing host power through to a target.
//!
//! The gateware side is `gateware/soc/peripherals/vbus_csr.py`, one register whose bit
//! numbering is upstream's. This module holds the policy, because the gateware
//! deliberately holds none.
//!
//! ## What the board already does, so this does not have to
//!
//! Cynthion protects itself. Overvoltage shuts either power *input* off
//! automatically above 5.5 V -- `D17`, a 5.6 V zener, no firmware in the loop --
//! input and passthrough are separate functions, and the passthrough network is
//! rated for the full 20 V PD Standard Power Range. **Nothing this module can
//! command damages Cynthion.**
//!
//! ## What is left at risk, and is therefore this module's job
//!
//! The device on the other end. TARGET A is a USB-A connector with no CC line
//! and no way to refuse a voltage; a PD source on TARGET-C sitting at 20 V would
//! reach it through the shared rail. GSG name the power monitors as the intended
//! mechanism for exactly this, slower than the internal protection and the only
//! option on the passthrough path.
//!
//! So `close` consults the PAC1954 before connecting anything, and refuses on the
//! part's own UV/OV flags. See `vbus_gate.rs` (#552).
//!
//! ## Sources
//!
//! `docs/hardware.md` collects these; the primary ones are GSG's Cynthion
//! Hardware Design Update and mossmann in greatscottgadgets/cynthion#184.



use crate::bus::Bus;
use crate::power;

/// Bit positions, matching `gateware/soc/peripherals/vbus_csr.py` and upstream's
/// analyzer state word. Bits 0-2 are upstream's own fields and are not ours.
const BIT_TARGET_C: u8 = 3;
const BIT_CONTROL: u8 = 4;
const BIT_AUX: u8 = 5;
const BIT_DISCHARGE: u8 = 6;
const BIT_ENABLE: u8 = 7;

/// Which port supplies the shared rail.
#[derive(Copy, Clone, PartialEq, Eq)]
pub enum Source {
    /// The port that cannot present anything but 5 V: CONTROL has no FUSB302B,
    /// so no PD contract can raise it. Prefer this one.
    Control,
    /// Powers the board and has a PD controller.
    Aux,
    /// Has a PD controller and is the one that can legitimately sit at 20 V.
    TargetC,
}

impl Source {
    fn bit(self) -> u8 {
        match self {
            Source::Control => BIT_CONTROL,
            Source::Aux => BIT_AUX,
            Source::TargetC => BIT_TARGET_C,
        }
    }

    /// The PAC1954 channel this port is measured on.
    ///
    /// The ordering is the part's, not the intuitive one -- channel 0 is
    /// TARGET_A. `power::PORTS` is the single definition and this indexes it
    /// rather than restating the mapping.
    fn channel(self) -> usize {
        match self {
            Source::TargetC => 1,
            Source::Aux => 2,
            Source::Control => 3,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Source::Control => "control",
            Source::Aux => "aux",
            Source::TargetC => "target_c",
        }
    }

    /// Bytes, not `&str`, and that is a size decision rather than a taste.
    ///
    /// The console line arrives as `&[u8]` and every other parser in this
    /// firmware -- `memory::Region::parse`, `gpio::led_by_name`, `fusb302` --
    /// takes it that way. This one took `&str`, so its one caller had to run
    /// `core::str::from_utf8` first, and that pulled UTF-8 validation and
    /// `<str>::trim` into the image; `<str>::trim` in turn pulled
    /// `core::unicode::white_space::WHITESPACE_MAP`. Measured at **1,152 bytes
    /// of `.text` and 256 of `.rodata`** for the privilege of comparing four
    /// ASCII words -- see `docs/soc-size-review.md`.
    pub fn parse(text: &[u8]) -> Option<Source> {
        match text {
            b"control" => Some(Source::Control),
            b"aux" => Some(Source::Aux),
            b"target_c" | b"targetc" => Some(Source::TargetC),
            _ => None,
        }
    }
}

pub use crate::vbus_gate::Refusal;
use crate::vbus_gate::{decide, FLOOR_MV};

// INPUT SELECTION IS GONE, and it never worked. `control_vbus_in_en` (K13) and
// `aux_vbus_in_en` (L13) reach no pin: neither appears among the 126 `LOCATE`
// lines of the shipping design's own constraint file. `prefer_control`,
// `allow_all_inputs` and `vbus input` all wrote a scratch register and returned
// success. The load split they claimed to fix is an open question again (#305).

fn read() -> u8 {
    // SAFETY: fixed peripheral address, `main=0` region, so uncached.
    unsafe { core::ptr::read_volatile(cynthion_soc_pac::base::BOARD_VBUS as *const u8) }
}

fn write(value: u8) {
    // SAFETY: as above.
    unsafe { core::ptr::write_volatile(cynthion_soc_pac::base::BOARD_VBUS as *mut u8, value) };
}

/// Arm `source`'s limits, read it on demand, and let the part's flags decide.
///
/// The basis is the PAC1954's own UV/OV flags (it compares every sample) plus
/// one on-demand VBUS read, not a polled sample (#552). Returns the whole sample
/// so `charge_target_c` can look at a second channel from the same instant.
fn gate(
    source: Source,
    monitor: &mut power::Monitor,
    bus: Option<&mut Bus>,
) -> Result<(u32, power::Sample), Refusal> {
    let bus = bus.ok_or(Refusal::Unreadable)?;
    let (sample, flags) = monitor.vbus_read(bus, source.channel())?;
    let millivolts = sample.readings[source.channel()].bus_mv;
    match decide(source.channel(), millivolts, flags) {
        Ok(mv) => Ok((mv, sample)),
        Err(refusal) => {
            // Refused: whatever was closed before stays closed, so its limits
            // must be the armed ones, not the ones just tried.
            monitor.vbus_restore(bus, closed_source().map(Source::channel));
            Err(refusal)
        }
    }
}

/// Is any switch closed (the gate bit is on)?
pub fn any_closed() -> bool {
    read() & (1 << BIT_ENABLE) != 0
}

/// The source whose switch is closed, if any.
fn closed_source() -> Option<Source> {
    [Source::Control, Source::Aux, Source::TargetC]
        .into_iter()
        .find(|&source| is_closed(source))
}

/// Open every switch, unconditionally.
///
/// Clearing `enable` opens all four in the same cycle -- the gate is
/// combinational precisely so this is one write and cannot half-succeed.
pub fn open_all() {
    write(0);
}

/// What [`init`] read back. Zero is every switch open; nothing else is.
pub struct Init {
    pub state: u8,
}

impl Init {
    pub fn established(&self) -> bool {
        self.state == 0
    }
}

/// `vbus_init()` -- open every switch, and read the register back.
///
/// **This cuts power to anything attached to TARGET**, which is correct at boot
/// and is why it is not in any health-check path.
///
/// It is needed because a CPU reset is narrower than it looks. A reconfigure
/// clears this register; `jr _reset_vector` does not, so `vbus control` then
/// `load` or `reset` leaves a switch closed across the whole of the next boot,
/// unopened and unreported (#315).
///
/// The read back is the only evidence the write landed.
pub fn init() -> Init {
    open_all();
    Init { state: read() }
}

/// The raw register, for `board` and `vbus` to report.
pub fn state() -> u8 {
    read()
}

pub fn is_closed(source: Source) -> bool {
    let value = read();
    value & (1 << BIT_ENABLE) != 0 && value & (1 << source.bit()) != 0
}

/// Connect `source` to the shared rail, if its measured voltage allows.
///
/// Returns the measured millivolts on success.
pub fn close(
    source: Source,
    monitor: &mut power::Monitor,
    bus: Option<&mut Bus>,
) -> Result<u32, Refusal> {
    let (millivolts, _) = gate(source, monitor, bus)?;

    // ONE SOURCE AT A TIME, and this write is what enforces it.
    //
    // The whole register is replaced rather than read-modify-written, so closing
    // CONTROL opens AUX and TARGET-C in the same cycle. That is policy, not an
    // accident of the implementation.
    //
    // Two switches closed is safe when it is one supply reaching two
    // destinations -- TARGET A is the common node, so that is what the second
    // switch buys, and it is the case the hardware is designed for. It is NOT
    // safe when both ports are themselves live: connecting TARGET-C while AUX is
    // also sourcing ties two supplies together through the shared rail.
    //
    // Distinguishing those needs to know which ports are actually supplying,
    // which is a measurement rather than a register state. Until this reads all
    // four channels and reasons about them, the safe subset is one source, and
    // that is what this does. A future "pass through to a second port" must
    // check the other ports for VBUS before it closes anything.
    //
    // Setting the gate in the same write also means there is no window in which
    // `enable` is on over a stale set of switch bits.
    write((1 << BIT_ENABLE) | (1 << source.bit()));
    Ok(millivolts)
}

/// Route CONTROL through the shared rail to TARGET-C.
///
/// TARGET-C must not already be powered by another source. Repeating this while
/// our exact path is active is allowed so the Rp level can be changed in place.
pub fn charge_target_c(
    monitor: &mut power::Monitor,
    bus: Option<&mut Bus>,
) -> Result<u32, Refusal> {
    let (millivolts, sample) = gate(Source::Control, monitor, bus)?;
    let wanted = (1 << BIT_ENABLE) | (1 << BIT_CONTROL) | (1 << BIT_TARGET_C);
    if read() != wanted {
        let target_mv = sample.readings[Source::TargetC.channel()].bus_mv;
        if target_mv >= FLOOR_MV {
            return Err(Refusal::TooHigh(target_mv));
        }
    }
    write(wanted);
    Ok(millivolts)
}

/// Drain TARGET A.
///
/// A target should see VBUS fall rather than float, so removal looks like
/// removal. Opens the passthroughs first: discharging while a source is still
/// connected would be draining a live supply.
pub fn discharge() {
    write((1 << BIT_ENABLE) | (1 << BIT_DISCHARGE));
}


