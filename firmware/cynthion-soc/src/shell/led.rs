//! `led` -- and the colour names, which are shell vocabulary, not driver state.
//!
//! `gpio.rs` knows `Led::Red`. That "red" is spelled "red" is a fact about the
//! shell's grammar, and it moved here with the command that uses it (#296): a
//! driver carrying display strings cannot be used by something that does not
//! display, and #171 will want everything the shell touches in one place.
//!
//! Not a size argument -- LTO drops unreferenced statics either way.

use core::fmt::Write;

use crate::gpio::{self, Led};
use crate::shell::parse::trim;
use crate::uart::Uart;
use crate::shell::console::board_absent;
use crate::{ target};

/// Every LED, in board order, for a shell that wants to list them.
pub(crate) const LEDS: [(Led, &str); 6] = [
    (Led::Red, "red"),
    (Led::Orange, "orange"),
    (Led::Yellow, "yellow"),
    (Led::Green, "green"),
    (Led::Blue, "blue"),
    (Led::Violet, "violet"),
];

/// Parse a colour name. Returns `None` for anything else, including an index --
/// accepting "3" here would undo the whole point of naming them.
pub(crate) fn led_by_name(name: &[u8]) -> Option<Led> {
    LEDS.iter()
        .find(|(_, text)| text.as_bytes() == name)
        .map(|(led, _)| *led)
}

/// `led`, `led <colour>`, `led <colour> on|off|fabric`.
///
/// Colours only -- see the module comment in `src/gpio.rs` for why an index is
/// not accepted here.
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let pins = gpio::Gpio::new(board.gpio);

    // Split the argument into a colour and an optional state.
    let rest = trim(rest);
    let (name, state) = match rest.iter().position(|&b| b == b' ') {
        Some(i) => (&rest[..i], trim(&rest[i + 1..])),
        None => (rest, &rest[..0]),
    };

    if !name.is_empty() {
        let led = match led_by_name(name) {
            Some(led) => led,
            None => {
                let _ = writeln!(
                    uart,
                    "no LED of that colour; they are red, \
                                        orange, yellow, green, blue, violet"
                );
                return;
            }
        };
        match state {
            b"on" => pins.set_led(led, true),
            b"off" => pins.set_led(led, false),
            b"fabric" => pins.release_led(led),
            b"" => {}
            _ => {
                let _ = writeln!(uart, "usage: led <colour> [on|off|fabric]");
                return;
            }
        }
    }

    // Always list afterwards, so a command that set something shows the result
    // rather than reporting success and leaving the state to be guessed at.
    // This is the only way to confirm an LED from a terminal: nobody reading
    // this output can see the board.
    for (led, colour) in LEDS {
        let owner = match pins.led_owner(led) {
            gpio::Owner::Cpu => "cpu",
            gpio::Owner::Fabric => "fabric",
        };
        let _ = writeln!(
            uart,
            "  {:7} {:3}  driven by {}",
            colour,
            if pins.led_lit(led) { "on" } else { "off" },
            owner
        );
    }
}

/// `info button` -- the USER button, as a contact rather than as a verb.
///
/// It was two extra rows under `led`, along with the power monitor's power-down
/// state. Neither is an LED, and a command that lists six LEDs and then two
/// other things has stopped being about anything. The power-down state went to
/// `pac1954 status`, next to the part it powers down.
///
/// **Open or closed, then what that means.** The pin is what the shell can
/// actually see: `PIN_BUTTON` high is the contact closed. "Pressed" is an
/// inference about a person, and reporting it alone leaves nothing to check the
/// wiring against when the button is stuck.
pub(crate) fn button_command(uart: &mut Uart) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let pins = gpio::Gpio::new(board.gpio);
    let closed = pins.button();
    let _ = writeln!(
        uart,
        "  USER    {:6}  {}",
        if closed { "closed" } else { "open" },
        if closed { "pressed" } else { "released" }
    );
}

