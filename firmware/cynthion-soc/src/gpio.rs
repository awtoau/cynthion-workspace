//! The board's GPIO block: six LEDs, the power monitor's PWRDN, and the button.
//!
//! The peripheral is `amaranth_soc.gpio.Peripheral`, upstream and unmodified.
//! This is only the register access and the pin names.
//!
//! ## The LEDs are named by colour
//!
//! There are six in a row and their index is an implementation detail of the
//! platform file. `Led::Green` is a thing a person holding the board can check;
//! "LED 3" is not, and getting it wrong is invisible in code review. Every
//! public entry point here takes a colour.
//!
//! Active-low is not this driver's business either. The platform declares them
//! with `invert=True`, so `PinsN` inverts at the pad and a 1 in the Output
//! register lights the LED.
//!
//! ## The ownership bit
//!
//! Each LED has two possible drivers: this peripheral, and the fabric's own
//! diagnostic (bus activity, heartbeat, USB state -- see the LED comment in
//! `gateware/soc/vexii_hello_soc.py`). The gateware muxes between them on the
//! GPIO pin's output enable, so a pin in its RESET mode leaves the fabric
//! driving and a pin put in push-pull takes over.
//!
//! That ordering matters and is the reason there is no separate "override"
//! register: a bitstream whose firmware never runs, or hangs before it reaches
//! `main`, still lights the LEDs with the fabric's account of itself. Those six
//! diagnostics are the reason the LEDs were wired up at all, and the CPU
//! borrowing one should not cost them.
//!
//! `Led::release` hands a pin back.
//!
//! ## Wide registers commit on their last byte
//!
//! Mode and SetClr are sixteen bits across two byte addresses. `csr.Multiplexer`
//! accumulates the low bytes in a shadow and raises the register's write strobe
//! only when the HIGHEST address in its range is written
//! (`amaranth_soc/csr/bus.py`); a read likewise latches the whole register into
//! a shadow when the LOWEST address is read. So:
//!
//!   * write the low byte first and the high byte last, always;
//!   * read the low byte first, or the high byte is whatever the shadow held.
//!
//! A driver that writes only the low byte of Mode has written nothing at all,
//! and the symptom is an LED command that returns cleanly and does nothing.

use core::ptr::{read_volatile, write_volatile};

/// Mode: two bits per pin, low byte first. 0b00 input-only, 0b01 push-pull.
const MODE: usize = 0;
/// Input: one bit per pin, read-only.
const INPUT: usize = 2;
/// Output: one bit per pin.
const OUTPUT: usize = 3;
/// SetClr: two bits per pin, `set` at 2n and `clr` at 2n+1. Write-only.
const SETCLR: usize = 4;

const MODE_INPUT_ONLY: u16 = 0b00;
const MODE_PUSH_PULL: u16 = 0b01;

/// The six LEDs, in the order they sit on the board.
///
/// The discriminants are the GPIO pin numbers and match `GPIO_RED` and friends
/// in `gateware/soc/vexii_hello_soc.py`. Nothing outside this module should
/// need them.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Led {
    Red = 0,
    Orange = 1,
    Yellow = 2,
    Green = 3,
    Blue = 4,
    Violet = 5,
}

/// Every LED, in board order, for a shell that wants to list them.
pub const LEDS: [(Led, &str); 6] = [
    (Led::Red, "red"),
    (Led::Orange, "orange"),
    (Led::Yellow, "yellow"),
    (Led::Green, "green"),
    (Led::Blue, "blue"),
    (Led::Violet, "violet"),
];

/// Parse a colour name. Returns `None` for anything else, including an index --
/// accepting "3" here would undo the whole point of naming them.
pub fn led_by_name(name: &[u8]) -> Option<Led> {
    LEDS.iter()
        .find(|(_, text)| text.as_bytes() == name)
        .map(|(led, _)| *led)
}

/// The power monitor's PWRDN, active low at the pad. A 1 here powers the
/// PAC1954 DOWN, so leaving this pin released is what keeps the I2C bus useful.
pub const PIN_PWRDN: usize = 6;

/// The USER button, `PinsN` on M14, so a 1 means pressed. The only real input in
/// this block.
pub const PIN_BUTTON: usize = 7;

/// Who is driving a pin.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Owner {
    /// The gateware's diagnostic. The reset state.
    Fabric,
    /// This peripheral.
    Cpu,
}

#[derive(Clone, Copy)]
pub struct Gpio {
    base: usize,
}

impl Gpio {
    /// A handle on the GPIO block at `base`. Constructing one does nothing; see
    /// the same note on `Uart::new`.
    pub const fn new(base: usize) -> Self {
        Gpio { base }
    }

    fn reg(&self, offset: usize) -> *mut u8 {
        (self.base + offset) as *mut u8
    }

    /// The Mode register, low byte first because the low byte is what latches
    /// the shadow.
    fn mode(&self) -> u16 {
        // SAFETY: two byte reads of a read/write CSR in the uncached peripheral
        // region. Neither has a side effect; the ordering is the multiplexer's
        // shadow requirement, not a hazard.
        unsafe {
            let low = read_volatile(self.reg(MODE)) as u16;
            let high = read_volatile(self.reg(MODE + 1)) as u16;
            low | (high << 8)
        }
    }

    /// Write Mode. The high byte commits.
    fn set_mode(&self, mode: u16) {
        // SAFETY: writes to a read/write CSR. The high byte must be last or the
        // register does not take the value at all.
        unsafe {
            write_volatile(self.reg(MODE), mode as u8);
            write_volatile(self.reg(MODE + 1), (mode >> 8) as u8);
        }
    }

    /// The Output register: what the CPU is driving, whether or not it owns the
    /// pin.
    pub fn output(&self) -> u8 {
        // SAFETY: a read of a read/write CSR, no side effect.
        unsafe { read_volatile(self.reg(OUTPUT)) }
    }

    /// The Input register.
    ///
    /// For the LEDs and PWRDN the gateware feeds this from the value on the net,
    /// so it reports what the pin is doing whichever side is driving it. That is
    /// not a measurement -- nothing on an ECP5 reads an output pad back -- and
    /// the pin's active-low inversion happens below this point, so a 1 means
    /// "lit" rather than "high". Only the button is a genuine input.
    pub fn input(&self) -> u8 {
        // SAFETY: a read of a read-only CSR, no side effect.
        unsafe { read_volatile(self.reg(INPUT)) }
    }

    /// Set or clear one pin's output bit, atomically.
    ///
    /// Through SetClr rather than a read-modify-write of Output, so this is safe
    /// against anything else touching a different pin in between -- which today
    /// is nothing, and tomorrow is whatever a timer or an interrupt handler
    /// decides to blink.
    fn write_pin(&self, pin: usize, on: bool) {
        // set is bit 2n, clr is bit 2n+1; byte 0 covers pins 0..3.
        let bit = 2 * pin + if on { 0 } else { 1 };
        let (byte, shift) = (bit / 8, bit % 8);
        // SAFETY: writes to a write-only CSR whose fields are self-clearing
        // strobes. Byte 1 is written last because it is the highest address in
        // the register and therefore the one that commits.
        unsafe {
            let low = if byte == 0 { 1u8 << shift } else { 0 };
            let high = if byte == 1 { 1u8 << shift } else { 0 };
            write_volatile(self.reg(SETCLR), low);
            write_volatile(self.reg(SETCLR + 1), high);
        }
    }

    fn set_owner(&self, pin: usize, owner: Owner) {
        let mode = match owner {
            Owner::Cpu => MODE_PUSH_PULL,
            Owner::Fabric => MODE_INPUT_ONLY,
        };
        let shift = 2 * pin;
        self.set_mode((self.mode() & !(0b11 << shift)) | (mode << shift));
    }

    fn owner(&self, pin: usize) -> Owner {
        if (self.mode() >> (2 * pin)) & 0b11 == MODE_PUSH_PULL {
            Owner::Cpu
        } else {
            Owner::Fabric
        }
    }

    /// Light or extinguish an LED, taking it from the fabric if it has to.
    pub fn set_led(&self, led: Led, on: bool) {
        let pin = led as usize;
        self.write_pin(pin, on);
        self.set_owner(pin, Owner::Cpu);
    }

    /// Give an LED back to the gateware's diagnostic.
    pub fn release_led(&self, led: Led) {
        self.set_owner(led as usize, Owner::Fabric);
    }

    /// Who is driving this LED.
    pub fn led_owner(&self, led: Led) -> Owner {
        self.owner(led as usize)
    }

    /// Whether this LED is lit, according to the net.
    pub fn led_lit(&self, led: Led) -> bool {
        self.input() & (1 << (led as usize)) != 0
    }

    /// Whether the USER button is pressed.
    pub fn button(&self) -> bool {
        self.input() & (1 << PIN_BUTTON) != 0
    }

    /// Whether the CPU has forced the power monitor into power-down.
    ///
    /// False out of reset and false unless something here asks for it, which is
    /// what leaves the PAC1954 available to the I2C bus.
    pub fn power_monitor_down(&self) -> bool {
        self.owner(PIN_PWRDN) == Owner::Cpu && self.output() & (1 << PIN_PWRDN) != 0
    }
}
