//! The whole board as one tree: every port's rail, PD controller and PHY.
//!
//! `power`, `typec` and `phy` each answer about one subsystem, and a port is all
//! three at once -- so answering "is anything plugged into TARGET, is it powered,
//! and is its PHY alive" means running three commands and correlating them by
//! hand. This prints one branch per connector, with the rail that feeds it and
//! the parts that watch it underneath.
//!
//! ## It reads no bus
//!
//! Every number here is one a poller or an interrupt already cached, and each
//! branch carries the age of its own. That is the rule from issue #123 applied to
//! the command most likely to break it: this one touches every device on the
//! board, so a live sweep would be a dozen I2C transactions issued against a
//! controller whose REFRESH cycle has exactly one owner.
//!
//! | value                        | where it comes from                       | age |
//! |------------------------------|-------------------------------------------|-----|
//! | rail volts and milliamps     | `power::Monitor::poll`, every 50 ms       | yes |
//! | device id, vbus, cc          | `typec::Controllers`, boot and interrupts | yes |
//! | TARGET phy identity, linestate | the ULPI window, read here             | live |
//! | which parts each port has    | how the board is built                    | n/a |
//!
//! **The two ages mean opposite things, which is why the tree says which is
//! which.** The rails are POLLED, so an old sample means the poller stopped. The
//! controllers INTERRUPT on a change, so an old confirmation means nothing has
//! changed -- a value hours old is still current.
//!
//! The one live read is TARGET's ULPI window, because "is the PHY there now" is
//! the one question a cache cannot answer. It is safe where a live I2C read would
//! not be: it is not the shared bus, the window has one user (issue #125 is about
//! AUX and CONTROL, whose PHYs belong to a USB stack and have no window here at
//! all), and the gateware bounds the transaction at 68 us.
//!
//! ## Absent is not zero
//!
//! An unplugged rail measures 0.76-0.92 mA of ADC offset and an absent PHY does
//! not read as zeros, so neither is ever printed as a number:
//!
//!   * below [`VSAFE0_MV`] the rail is `--` and `off`, with the millivolts it
//!     actually measured in brackets
//!   * below that port's own floor the current is `--` and `no load`
//!   * a PHY that never releases `dir` prints the gateware's timeout
//!   * a value nothing has cached prints `--` and says so, never a zero nobody
//!     measured
//!
//! ## CONTROL is not a third copy of the same port
//!
//! It has a Type-C connector and no FUSB302B -- the board fits those to AUX and
//! TARGET only -- so its VBUS and CC are not visible from this CPU at all, and
//! its PHY belongs to Apollo. Its branch is a row shorter than the others and
//! says why, rather than leaving three identical-looking headings with two of the
//! values missing.

use core::fmt::Write;

use crate::fusb302::Port;
use crate::power::{self, Age, Sample};
use crate::target;
use crate::typec::Controllers;
use crate::uart::Uart;
use crate::ulpi::{self, Ulpi};

/// Below this a rail is off rather than idle, in millivolts.
///
/// 800 mV is vSafe0V from the USB PD specification -- the level a source must
/// have discharged VBUS below before the port counts as unpowered. vSafe5V
/// starts at 4750 mV, and a rail between the two is in transition and belongs to
/// neither, so the tree prints the volts it measured and reserves `off` for the
/// bottom of the range.
pub const VSAFE0_MV: u32 = 800;

/// Which PAC1954 channel each connector's rail is on.
///
/// **The part's channel order is not the board's**: channel 1 is TARGET_A and
/// channel 4 is CONTROL. Named here and checked against `power::PORTS` below, so
/// a branch cannot quietly print the wrong rail if that list is ever reordered.
const TARGET_A: usize = 0;
const TARGET_C: usize = 1;
const AUX: usize = 2;
const CONTROL: usize = 3;

const _: () = assert!(matches!(power::PORTS[TARGET_A].as_bytes(), b"target_a"));
const _: () = assert!(matches!(power::PORTS[TARGET_C].as_bytes(), b"target_c"));
const _: () = assert!(matches!(power::PORTS[AUX].as_bytes(), b"aux"));
const _: () = assert!(matches!(power::PORTS[CONTROL].as_bytes(), b"control"));

/// The indent under a branch that has more branches below it, and under the last
/// one. Two strings rather than a box-drawing routine: the tree is three fixed
/// branches, and a general one would be more code than the thing it draws.
const STEM: &str = "|    ";
const LAST: &str = "     ";

/// `board` -- every port's rail, PD controller and PHY, in one tree.
///
/// Takes what it prints by `&`, not a `&mut Bus`: there is nothing here it could
/// use one for, and a signature that cannot reach the bus is the same argument
/// `src/bus.rs` makes about ownership, made one level up.
pub fn tree(uart: &mut Uart, power: &power::Monitor, type_c: &Controllers) {
    let sample = power.latest();
    let sample = sample.as_ref();

    // Every fixed run of lines goes out as ONE `write_str`, not as a `writeln!`
    // each. Both reach the same console, but a `writeln!` with no arguments
    // still builds a `fmt::Arguments` and calls through `core::fmt::write`, and
    // the pieces table for each one is bytes of `.rodata` on a firmware whose
    // 32 KiB half was down to two spare bytes before this command existed. The
    // newlines inside these blocks are translated to CRLF by `Uart`'s
    // `write_str`, exactly as `writeln!`'s would be.
    let _ = uart.write_str(match target::BOARD {
        Some(_) => "board  cynthion r1.4  three usb ports, four rails, \
                    three phys\n",
        // The whole tree still prints, with every leaf saying what it does not
        // have. That is what makes `scripts/soc_test.py` evidence about this
        // command: the rendering under QEMU is the rendering on the board, fed
        // nothing instead of fed a sample.
        None => "board  NONE on this target -- the tree below is what a \
                 boardless build can see\n",
    });
    // The age is on the header, because every rail below it is that old. A
    // poller that has stopped leaves four individually plausible voltages and
    // nothing else on this screen can contradict them.
    let _ = writeln!(uart, "power  pac1954 @{:02x} on the power bus, polled every \
                            {} ms  [{}]",
                     power::ADDRESS, power::INTERVAL_MS,
                     Since("sampled", power.age()));

    // CONTROL: a connector, a rail, and a PHY that is not ours.
    let _ = uart.write_str("|\n+- CONTROL   the host port, and apollo's\n");
    rail(uart, STEM, CONTROL, sample, power.floor(CONTROL));

    // CONTROL's two remaining rows and AUX's heading, which are one constant
    // run: there is no Type-C row on CONTROL at all, and its absence is the
    // asymmetry worth showing.
    let _ = uart.write_str(
        "|    connector  type-c, NO fusb302b -- vbus and cc are not visible \
         from here\n\
         |    phy        usb3343, APOLLO'S -- no ulpi register window in this \
         soc\n\
         |\n\
         +- AUX       the usb console; this text is leaving through it\n");
    rail(uart, STEM, AUX, sample, power.floor(AUX));
    let _ = uart.write_str("|    connector  type-c, fusb302b on the aux bus\n");
    controller(uart, STEM, type_c, Port::Aux);

    // TARGET carries two connectors on one branch, because they are two ends of
    // one passthrough and the questions asked of them are the same questions.
    let _ = uart.write_str(
        "|    phy        usb3343, THE CONSOLE'S -- no ulpi register window in \
         this soc\n\
         |\n\
         +- TARGET    the port under test; nothing here drives it\n");
    rail(uart, LAST, TARGET_C, sample, power.floor(TARGET_C));
    rail(uart, LAST, TARGET_A, sample, power.floor(TARGET_A));
    let _ = uart.write_str("     connector  type-c, fusb302b on the target bus\n");
    controller(uart, LAST, type_c, Port::Target);
    phy(uart, LAST);

    // The key to the two ages above. Without it both read as staleness, and only
    // one of them is.
    let _ = uart.write_str(
        "ages   rails are POLLED, so an old sample means the poller stopped;\n\
         \x20      controllers INTERRUPT, so an old one means nothing changed.\n");
}

/// One rail: volts, milliamps, and what it means when either is missing.
fn rail(uart: &mut Uart, stem: &str, channel: usize, sample: Option<&Sample>,
        floor: u32) {
    let _ = write!(uart, "{}rail       {:8} ", stem, power::PORTS[channel]);

    let reading = match sample {
        Some(sample) => &sample.readings[channel],
        // Two polls after reset there is genuinely nothing, and on a target with
        // no monitor there never will be. Four fabricated zeros would be four
        // measurements that were never made.
        None => {
            let _ = writeln!(uart, "   --  V      --  mA   NOT SAMPLED");
            return;
        }
    };

    if reading.bus_mv < VSAFE0_MV {
        // The millivolts are still shown, in brackets, because "off" is a
        // judgement and the reading behind it is the evidence.
        let _ = writeln!(uart, "   --  V      --  mA   off ({} mV)",
                         reading.bus_mv);
        return;
    }
    let _ = write!(uart, "{:2}.{:03} V ", reading.bus_mv / 1000,
                   reading.bus_mv % 1000);
    if reading.current_ua < floor {
        // An unplugged rail measures 0.76-0.92 mA of ADC offset on this board,
        // so a small number here is noise wearing the shape of a measurement.
        let _ = writeln!(uart, "     --  mA   no load (under {} mA)",
                         floor / 1000);
    } else {
        let _ = writeln!(uart, "{:4}.{:03} mA", reading.current_ua / 1000,
                         reading.current_ua % 1000);
    }
}

/// One port's FUSB302B, from what the interrupt path last confirmed.
///
/// Cached, not read: `typec` is the command that asks the part what it is now,
/// and this is the command that does not touch a bus. The cache is refreshed at
/// boot and on every interrupt, and the controllers are configured to interrupt
/// on exactly the two things printed here -- VBUS appearing and CC moving -- so
/// an old confirmation is a port that has not changed, not a stale reading.
fn controller(uart: &mut Uart, stem: &str, type_c: &Controllers, port: Port) {
    let _ = write!(uart, "{}typec      ", stem);
    match type_c.cached(port) {
        Some((state, at)) => {
            let _ = write!(uart, "vbus {:7}  {:25}  [{}]",
                           if state.vbus { "present" } else { "absent" },
                           state.cc(),
                           Since("confirmed", power::age_of(Some(at))));
            // The identity only when it is wrong. `0x91` is version 9 revision 1
            // -- FUSB302B revision B, what both parts on this board read -- and
            // anything else means the select reached a different chip. `typec`
            // prints it either way; a tree prints the exception.
            if state.device_id != 0x91 {
                let _ = write!(uart, "  device {:02x}, NOT a fusb302b",
                               state.device_id);
            }
            let _ = writeln!(uart);
        }
        None if target::BOARD.is_none() => {
            let _ = writeln!(uart, "--  ABSENT: no i2c bus on this target");
        }
        None => {
            let _ = writeln!(uart, "--  NO READING: the controller has not \
                                    answered; `typec init` retries");
        }
    }
}

/// The TARGET PHY, read live over the ULPI window.
///
/// The only live access this command makes, and the module comment says why it
/// is the one that has to be. Read-only: the walking-bit test in `phy` writes
/// the scratch register, and a status view must not change what it reports on.
fn phy(uart: &mut Uart, stem: &str) {
    let _ = write!(uart, "{}phy        ", stem);
    let board = match target::BOARD {
        Some(board) => board,
        None => {
            let _ = writeln!(uart, "--  ABSENT: no ulpi window on this target");
            return;
        }
    };
    let window = Ulpi::new(board.ulpi);
    let vendor = identity(&window, ulpi::usb3343::REG_VENDOR_ID_LOW);
    let product = identity(&window, ulpi::usb3343::REG_PRODUCT_ID_LOW);
    let debug = window.read(ulpi::usb3343::REG_DEBUG);

    match (vendor, product, debug) {
        (Ok(vendor), Ok(product), Ok(debug)) => {
            let _ = writeln!(uart, "{:04x}:{:04x} {:9}  linestate {:8} [live]",
                             vendor, product,
                             if vendor == ulpi::usb3343::VENDOR_ID
                                 && product == ulpi::usb3343::PRODUCT_ID {
                                 "usb3343"
                             } else {
                                 "UNKNOWN"
                             },
                             line_state(debug));
        }
        // An absent PHY never releases `dir` and the gateware's 68 us timeout
        // fires, which is a different report from "answered, wrongly" and must
        // stay that way.
        (Err(error), _, _) | (_, Err(error), _) | (_, _, Err(error)) => {
            let _ = writeln!(uart, "--  ABSENT or silent: {}  [live]",
                             error.as_str());
        }
    }
}

/// Two consecutive ULPI registers as one little-endian 16-bit id.
fn identity(window: &Ulpi, low: u8) -> Result<u16, ulpi::Error> {
    let low_byte = window.read(low)?;
    let high_byte = window.read(low + 1)?;
    Ok(((high_byte as u16) << 8) | low_byte as u16)
}

/// What the PHY sees on D+/D-, from the two LineState bits of `DEBUG`.
///
/// `se0` on an idle port with nothing attached is the expected reading and not a
/// fault, which is why it is spelled out rather than printed as two bits.
fn line_state(debug: u8) -> &'static str {
    match debug & 0b11 {
        0b00 => "se0",
        0b01 => "dp high",
        0b10 => "dm high",
        _ => "se1",
    }
}

/// An age with the verb that makes it a sentence: `sampled 61 ms ago`.
///
/// Milliseconds below ten seconds and whole seconds above, because a five-figure
/// millisecond count is a number nobody reads as a duration. `NEVER` is a word,
/// not a number, for the same reason the rails print `--`.
struct Since(&'static str, Age);

impl core::fmt::Display for Since {
    fn fmt(&self, f: &mut core::fmt::Formatter) -> core::fmt::Result {
        match self.1 {
            Age::Millis(ms) if ms < 10_000 => write!(f, "{} {} ms ago", self.0, ms),
            Age::Millis(ms) => write!(f, "{} {} s ago", self.0, ms / 1000),
            Age::Older => write!(f, "{} OVER {} s ago", self.0,
                                 power::AGE_LIMIT_MS / 1000),
            Age::Never => write!(f, "NEVER {}", self.0),
        }
    }
}
