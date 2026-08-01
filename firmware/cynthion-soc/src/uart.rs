//! An NS16550A driver, parameterised by base address.
//!
//! One type, any number of instances, and -- the point of the exercise -- the
//! same code on the FPGA and under QEMU. `-M virt` presents an `ns16550a` at
//! 0x10000000 and `ecp5-test/riscv/uart16550.py` presents the same register map
//! at whatever address the SoC's decoder puts it. Nothing below is conditional
//! on the target, so `scripts/soc_test.py` exercises the driver the board runs
//! rather than a second implementation that merely agrees with it.
//!
//! This replaces a bespoke two-register console whose receive path had a read
//! with a side effect one byte away from the register firmware polls. See the
//! module docstring in `ecp5-test/riscv/uart16550.py` for what that cost. The
//! discipline the driver inherits is: **poll LSR, act on RBR/THR, and never read
//! anything else to find out whether you may.**

use core::ptr::{read_volatile, write_volatile};

/// Receive buffer (read) and transmit holding (write). Reading pops a byte.
const RBR_THR: usize = 0;
/// Interrupt enable.
const IER: usize = 1;
/// FIFO control (write).
const FCR: usize = 2;
/// Line control. Bit 7 is DLAB.
const LCR: usize = 3;
/// Line status. Read-only, no side effects, and four bytes clear of RBR.
const LSR: usize = 5;

/// Data ready: the receive FIFO holds at least one byte.
const LSR_DR: u8 = 1 << 0;
/// Transmit holding register empty: there is room to write.
const LSR_THRE: u8 = 1 << 5;

/// 8 data bits, no parity, one stop bit -- and, critically, DLAB clear.
///
/// With DLAB set, offset 0 is the baud divisor latch rather than the data
/// register, so every character written would reprogram a baud rate instead of
/// being transmitted. Both the SoC peripheral and QEMU reset LCR to 0, so this
/// is belt and braces; a wrong value here looks exactly like "the firmware
/// produces no output", which is the failure this whole layer exists to
/// distinguish from a real one.
const LCR_8N1: u8 = 0x03;

/// Enable the FIFOs and clear both of them.
///
/// Bit 0 enables, bit 1 clears receive, bit 2 clears transmit. Clearing at start
/// up matters on the board: the gateware's FIFOs survive a `j _start` reboot
/// (only the CPU restarts, not the peripheral), so a `reset` command issued
/// mid-line would otherwise leave the remainder of that line in the receive FIFO
/// to be interpreted as the first command of the new session.
const FCR_ENABLE_AND_CLEAR: u8 = 0x07;

/// A 16550 at a fixed base address.
///
/// `Copy`, and deliberately: an instance is nothing but an address, so a caller
/// may materialise one wherever it needs to speak without threading a handle
/// through every function. That is what lets the panic handler print without
/// borrowing the console that panicked.
#[derive(Clone, Copy)]
pub struct Uart {
    base: usize,
}

impl Uart {
    /// A handle on the 16550 at `base`.
    ///
    /// # Safety
    ///
    /// Not marked unsafe, because constructing one does nothing. Every access
    /// below is individually `unsafe` and individually justified; a handle on a
    /// wrong address is a wrong program, not an unsound one, and making this
    /// `unsafe` would put an `unsafe` block around every call site for no gain.
    pub const fn new(base: usize) -> Self {
        Uart { base }
    }

    fn reg(&self, offset: usize) -> *mut u8 {
        (self.base + offset) as *mut u8
    }

    /// Put the peripheral in the state the rest of this driver assumes.
    ///
    /// Idempotent, so calling it again from a panic handler or after a reboot is
    /// safe. It does not touch the divisor latches: there is no baud rate on the
    /// SoC's instance (it is a byte pipe to a USB endpoint), and QEMU's chardev
    /// ignores the one on `virt`. Writing a divisor would be pretending to
    /// configure something.
    pub fn init(&mut self) {
        // SAFETY: `base` is a peripheral address in an uncached region on the
        // SoC (`main=0` PMA) and a device address under QEMU. All writes.
        unsafe {
            // Interrupts off first. Nothing installs a handler, and an
            // unexpected external interrupt on a core with the default trap
            // vector is an unrecoverable jump.
            write_volatile(self.reg(IER), 0);
            write_volatile(self.reg(LCR), LCR_8N1);
            write_volatile(self.reg(FCR), FCR_ENABLE_AND_CLEAR);
        }
    }

    /// Writes one byte, waiting for room in the transmit FIFO.
    ///
    /// Bounded, not an infinite spin.
    ///
    /// An unbounded wait here blocks the CPU INSIDE the banner whenever the
    /// transmit FIFO fills before something drains it -- which is the normal case
    /// at boot, since the firmware starts the instant the FPGA is configured and
    /// USB takes ~0.5 s to enumerate. The symptom is a board that prints nothing,
    /// never re-banners and never echoes, with a healthy clock and a CPU that
    /// demonstrably reaches the I/O bus: indistinguishable from a dead core, and
    /// it cost a day.
    ///
    /// Dropping a byte is strictly better than wedging: a console exists to
    /// report, and one that hangs to preserve a character reports nothing at all.
    pub fn put(&mut self, byte: u8) {
        // SAFETY: fixed peripheral addresses; volatile because these are devices
        // whose values change underneath the compiler.
        unsafe {
            // 200,000 turns of a loop whose body is one uncached MMIO read --
            // several milliseconds at 60 MHz, and several orders of magnitude
            // longer than the microsecond a 16-byte FIFO needs to drain into a
            // USB endpoint that is being serviced. Reaching it therefore means
            // nothing is draining at all, and the only useful response to that
            // is to carry on and let the next byte try again.
            let mut spins = 0u32;
            while read_volatile(self.reg(LSR)) & LSR_THRE == 0 {
                spins += 1;
                if spins > 200_000 {
                    return;
                }
            }
            write_volatile(self.reg(RBR_THR), byte);
        }
    }

    /// One byte if any has been received, else `None`. Never blocks.
    pub fn get(&mut self) -> Option<u8> {
        // SAFETY: as above.
        //
        // DR must be checked first and RBR must not be touched otherwise:
        // reading RBR pops the FIFO whether or not it held anything, and reading
        // an empty one returns the previous byte rather than blocking. That is
        // the whole reason LSR lives at +5 -- a different 32-bit word from RBR at
        // +0 -- so that no widening, prefetch or replay of this poll can reach
        // the data register.
        unsafe {
            if read_volatile(self.reg(LSR)) & LSR_DR == 0 {
                None
            } else {
                Some(read_volatile(self.reg(RBR_THR)))
            }
        }
    }
}

impl core::fmt::Write for Uart {
    /// Translates LF to CRLF on the way out.
    ///
    /// This is a raw byte pipe -- CDC-ACM with no line discipline anywhere, so
    /// nothing between here and the terminal turns a newline into a carriage
    /// return plus a line feed. `writeln!` emits a bare `\n`, which moves the
    /// cursor DOWN without moving it back to column zero, so successive lines
    /// march diagonally off the right of the screen and the prompt never appears
    /// where it should. It reads as "the shell is ignoring Enter" when in fact
    /// every keystroke was handled correctly.
    ///
    /// Doing it here rather than writing `\r\n` at each call site is deliberate:
    /// this is a property of the device, so every `writeln!` in the firmware --
    /// including ones not yet written -- is covered by construction. Call sites
    /// should emit `\n` and leave it alone; an explicit `\r\n` above this layer
    /// now comes out as `\r\r\n`.
    ///
    /// `soc_test.py` asserts that no bare LF ever reaches the wire, because this
    /// fix has been made and lost once already.
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        for &byte in s.as_bytes() {
            if byte == b'\n' {
                self.put(b'\r');
            }
            self.put(byte);
        }
        Ok(())
    }
}
