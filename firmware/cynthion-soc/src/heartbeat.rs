//! The orange LED, toggled by a periodic RTIC task. If it stops, the OS is dead.
//!
//! One task, one LED, no gateware. #411.
//!
//! ## Why orange
//!
//! The fabric's map (`gateware/soc/top.py`) drives orange from `ever_fetched` --
//! "the instruction bus has moved at least once". It latches within microseconds
//! of any boot and has told nobody anything since, which is why #411 named it as
//! the one to repurpose.
//!
//! **And nothing is displaced.** The GPIO peripheral's reset mode is INPUT_ONLY,
//! so the fabric keeps orange until firmware puts the pin in push-pull. A board
//! whose firmware never runs still shows `ever_fetched` there, exactly as
//! before; a board whose firmware runs shows something strictly stronger, since
//! a blink cannot happen without the fetch that `ever_fetched` reported.
//!
//! It pairs with green, which the fabric flashes off the solved PLL clock:
//!
//!     green flashing, orange flashing  both are alive
//!     green flashing, orange FROZEN    the OS stopped; the FPGA is fine
//!     green frozen                     the FPGA or its clock is gone
//!
//! ## Toggle, not a level
//!
//! A level -- however carefully computed -- cannot distinguish a healthy board
//! from a stuck output, and five of this board's six lamps were exactly that
//! before #411. Motion is the whole signal: two toggles per LED period, at 100
//! ms each, is 5 Hz. Unmistakably a blink, and slow enough to time.
//!
//! ## What a blink proves, and what it does not
//!
//! It is a SOFTWARE task through RTIC's SLIC dispatcher, so a blink means the
//! core is fetching, the CLINT fired, the release arithmetic ran, the pend
//! reached `msip`, the dispatcher selected this source, and nothing was masking
//! priority 3.
//!
//! It does NOT mean the shell is well. A verb spinning at idle priority with
//! interrupts enabled -- #409 -- leaves every one of those intact and the LED
//! blinks straight through it. That is the right answer to the question this
//! lamp asks; it is the wrong lamp to ask about a wedged command.

use crate::gpio::{Gpio, Led};
use crate::target;

/// The lamp. Named by colour, never by index -- see `src/gpio.rs`.
const LAMP: Led = Led::Orange;

/// Flip it, and take the pin from the fabric the first time.
///
/// `set_led` writes the level through SetClr and puts the pin in push-pull, so
/// the handover needs no separate claim and no new register: push-pull IS the
/// claim (`gateware/soc/top.py`, the LED comment).
///
/// The level is read back from the Input register, which the gateware feeds from
/// the value on the LED net. So the toggle is against what the pin is actually
/// doing rather than against a copy this module keeps -- and a first call
/// inherits whatever the fabric had there instead of forcing a phase.
pub fn toggle() {
    if let Some(board) = target::BOARD {
        let pins = Gpio::new(board.gpio);
        pins.set_led(LAMP, !pins.led_lit(LAMP));
    }
}

/// Whether the lamp is lit, for the shell's own negative control.
///
/// Reads the net, not the Output register, so it reports what the LED is doing
/// whichever side is driving it.
pub fn lit() -> bool {
    match target::BOARD {
        Some(board) => Gpio::new(board.gpio).led_lit(LAMP),
        None => false,
    }
}
