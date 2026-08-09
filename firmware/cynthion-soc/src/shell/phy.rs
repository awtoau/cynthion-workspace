//! `phy` and `phy reset` -- the USB3343 on TARGET.
//!
//! Moved out of `main.rs` unchanged (#296). `ulpi.rs` is the driver.

use core::fmt::Write;

use crate::uart::Uart;
use crate::shell::console::board_absent;
use crate::{ target, ulpi};

/// `phy` -- identity and state of the USB3343 on TARGET.
///
/// **How to tell a live PHY from an absent one:** the identity registers are
/// necessary and not sufficient. A bus that returns a constant, a PHY held in
/// reset, or a window whose data lines are stuck can all produce a number that
/// looks like an answer, and `0x0424`/`0x0009` are only two of the eight bytes
/// the bus can carry. So this ALSO walks a single bit across the scratch
/// register: eight writes, eight read-backs, each value seen once. That fails on
/// a stuck line, on a shorted pair and on a constant, and it is the same test
/// `scripts/phy_probe.py` and the shipped `cynthion selftest` make.
///
/// A PHY that is not there does not read as zeros -- it never releases `dir`,
/// so the gateware's 68 us timeout fires and this says so, which is a different
/// message from "answered, wrongly".
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let phy = ulpi::Ulpi::new(board.ulpi);

    if rest.is_empty() {
        return crate::shell::list_family(uart, "phy");
    }
    if rest == b"reset" {
        return board_phy_reset(uart, &phy);
    }

    // A named read, so one failure reports which register it was on rather than
    // leaving the caller to count lines.
    let read = |uart: &mut Uart, name: &str, address: u8| -> Option<u8> {
        match phy.read(address) {
            Ok(value) => {
                let _ = writeln!(uart, "  {:16} {:02x}  {:02x}", name, address, value);
                Some(value)
            }
            Err(error) => {
                let _ = writeln!(uart, "  {:16} {:02x}  {}", name, address, error.as_str());
                None
            }
        }
    };

    let _ = writeln!(uart, "ulpi  @{:08x}  target_phy", board.ulpi);
    let _ = writeln!(uart, "  register         at  value");

    // Read but NOT printed one line each. The four bytes only mean anything
    // assembled, and the `vendor ... product ... USB3343 ok` line below says it
    // in the form a reader wants. Four rows of hex were four chances to read a
    // byte and conclude something about a 16-bit value.
    let vendor_low = phy.read(ulpi::usb3343::REG_VENDOR_ID_LOW).ok();
    let vendor_high = phy.read(ulpi::usb3343::REG_VENDOR_ID_LOW + 1).ok();
    let product_low = phy.read(ulpi::usb3343::REG_PRODUCT_ID_LOW).ok();
    let product_high = phy.read(ulpi::usb3343::REG_PRODUCT_ID_LOW + 1).ok();
    read(
        uart,
        "function control",
        ulpi::usb3343::REG_FUNCTION_CONTROL,
    );
    read(uart, "otg control", ulpi::usb3343::REG_OTG_CONTROL);
    let debug = read(uart, "debug", ulpi::usb3343::REG_DEBUG);

    match (vendor_low, vendor_high, product_low, product_high) {
        (Some(vl), Some(vh), Some(pl), Some(ph)) => {
            let vendor = ((vh as u16) << 8) | vl as u16;
            let product = ((ph as u16) << 8) | pl as u16;
            let _ = writeln!(
                uart,
                "  vendor {:04x} product {:04x} {}",
                vendor,
                product,
                if vendor == ulpi::usb3343::VENDOR_ID && product == ulpi::usb3343::PRODUCT_ID {
                    "USB3343 ok"
                } else {
                    "NOT a USB3343"
                }
            );
        }
        _ => {
            let _ = writeln!(uart, "  identity incomplete; the PHY did not answer");
            return;
        }
    }

    if let Some(debug) = debug {
        // LineState is D+ in bit 0 and D- in bit 1, straight from the receiver.
        // With nothing plugged into TARGET both are low, which is SE0 -- so `00`
        // here is the expected reading on an idle port and not a fault.
        let _ = writeln!(uart, "  linestate dp {} dm {}", debug & 1, (debug >> 1) & 1);
    }

    // The walking bit. Eight patterns, each with exactly one bit set, so every
    // data line is driven high on its own and read back on its own.
    let mut lines_ok = 0u8;
    let mut failed = false;
    for bit in 0..8 {
        let pattern = 1u8 << bit;
        if phy.write(ulpi::usb3343::REG_SCRATCH, pattern).is_err() {
            failed = true;
            break;
        }
        match phy.read(ulpi::usb3343::REG_SCRATCH) {
            Ok(value) if value == pattern => lines_ok |= pattern,
            Ok(_) => {}
            Err(_) => {
                failed = true;
                break;
            }
        }
    }
    if failed {
        let _ = writeln!(uart, "  scratch walk did not complete");
    } else {
        let _ = writeln!(
            uart,
            "  scratch walk {:02x}  {}",
            lines_ok,
            if lines_ok == 0xff {
                "all 8 data lines ok"
            } else {
                "A DATA LINE IS STUCK"
            }
        );
    }
}

/// `phy reset` -- pulse TARGET's RESETB, and prove that it reached the pin.
///
/// The proof matters more than the reset. Between the `soc-clocks` work and
/// #241 both driven ULPI reset pads were tied de-asserted, and NOTHING SAID SO:
/// the PHY answers its identity registers either way, because its own power-on
/// reset had already run at cold boot. A command that pulsed a wire and printed
/// "done" would have passed on the broken bitstream.
///
/// So the check is a register the reset is specified to clear. The USB334x
/// datasheet Rev 1.2 section 5.6.2: cycling RESETB low for at least 1 us
/// "reset[s] the ULPI registers to their default state (and reset[s] all
/// internal state machines)", and Table 7.1 gives the Scratch register's default
/// as 00h. Write 0x5a, reset, read:
///
///     0x00  the pad moved, the PHY saw it
///     0x5a  the pad did not move, or is not connected to RESETB
///
/// The value survives on the broken build and is cleared on the fixed one, so
/// this command distinguishes them without a scope.
pub(crate) fn board_phy_reset(uart: &mut Uart, phy: &ulpi::Ulpi) {
    const MARKER: u8 = 0x5a;

    let _ = writeln!(uart, "phy reset  target_phy");

    // 1. Leave a mark the reset is specified to erase.
    if let Err(error) = phy.write(ulpi::usb3343::REG_SCRATCH, MARKER) {
        let _ = writeln!(uart, "  scratch write   {}", error.as_str());
        return;
    }
    match phy.read(ulpi::usb3343::REG_SCRATCH) {
        Ok(MARKER) => {
            let _ = writeln!(uart, "  scratch set     {:02x}", MARKER);
        }
        // Without this the whole test is vacuous: a scratch register that never
        // held the marker reads 0x00 afterwards whatever the reset did.
        Ok(other) => {
            let _ = writeln!(
                uart,
                "  scratch set     {:02x}  NOT {:02x} -- the PHY did not take the \
                 marker, so this test cannot tell you anything",
                other, MARKER
            );
            return;
        }
        Err(error) => {
            let _ = writeln!(uart, "  scratch read    {}", error.as_str());
            return;
        }
    }

    // 2. RESETB low for 2.133 us, then 1.200 ms of the PHY's preparation time.
    // Both are counted in gateware against the 60.000 MHz oscillator; this
    // returns when the PHY is ready, not when the pulse ends.
    let _ = writeln!(uart, "  resetb          low 2.133 us, then 1.200 ms tprep");
    if let Err(error) = phy.reset_phy() {
        let _ = writeln!(uart, "  reset           {}", error.as_str());
        return;
    }

    // 3. And the PHY must still be there afterwards. A reset that left it
    // wedged, or a preparation time cut short, shows up here as a timeout.
    let vendor = match phy.read(ulpi::usb3343::REG_VENDOR_ID_LOW) {
        Ok(value) => value,
        Err(error) => {
            let _ = writeln!(
                uart,
                "  after reset     {}  -- the PHY did not come back",
                error.as_str()
            );
            return;
        }
    };

    match phy.read(ulpi::usb3343::REG_SCRATCH) {
        Ok(0x00) => {
            let _ = writeln!(
                uart,
                "  scratch now     00  RESET REACHED THE PHY (vendor {:02x})",
                vendor
            );
        }
        Ok(MARKER) => {
            let _ = writeln!(
                uart,
                "  scratch now     {:02x}  RESET DID NOT REACH THE PHY -- the pad \
                 never moved (#241)",
                MARKER
            );
        }
        Ok(other) => {
            let _ = writeln!(
                uart,
                "  scratch now     {:02x}  neither 00 nor {:02x}; the window is \
                 returning something else entirely",
                other, MARKER
            );
        }
        Err(error) => {
            let _ = writeln!(uart, "  scratch read    {}", error.as_str());
        }
    }
}

