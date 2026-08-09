//! An NS16550A driver, parameterised by base address.
//!
//! One type, any number of instances, and the same code on the FPGA and under
//! QEMU: `-M virt` presents an `ns16550a` at 0x10000000 and
//! `gateware/soc/peripherals/uart16550.py` presents the same register map at whatever
//! address the SoC's decoder gives it. Nothing below is conditional on the
//! target, so `scripts/soc_test.py` exercises the driver the board runs.
//!
//! **The discipline: poll LSR, act on RBR/THR, and never read anything else to
//! find out whether you may.** RBR at +0 pops the FIFO; LSR at +5 is in a
//! different 32-bit word and cannot be aliased onto it. See the module
//! docstring in `gateware/soc/peripherals/uart16550.py`.
//!
//! ## A read of LSR destroys what it returns
//!
//! LSR reports overrun, parity, framing and break in bits 1..4, and the read
//! that reports them CLEARS them -- on this peripheral, on QEMU's, and on the
//! part both copy. So a poll loop that reads LSR and throws the value away
//! throws away the only report of a lost byte there will ever be.
//!
//! [`read_lsr`] is therefore the only place this driver reads it, and it folds
//! the error bits into [`take_errors`] before returning. Both LSR readers -- the
//! transmit spin in [`Uart::put`] and the receive check in [`UartRx::get`] --
//! go through it. A new one that does not is a silent regression, and it looks
//! exactly like a healthy console.
//!
//! Which reads change state, on the peripheral and on QEMU alike:
//!
//! | read | changes |
//! |---|---|
//! | RBR at +0, DLAB clear | pops the receive FIFO |
//! | IIR at +2 | clears the transmit-empty interrupt while IIR reports it |
//! | LSR at +5 | clears OE, PE, FE, BI and the bit 7 summary |
//!
//! This driver reads RBR and LSR and never reads IIR: it enables only
//! `IER.ERBFI`, so the one interrupt it can take needs no identifying and is
//! cleared by draining the FIFO. See `src/irq.rs`.

use core::ptr::{read_volatile, write_volatile};
use core::sync::atomic::{AtomicU32, AtomicU8, Ordering};

use crate::target;
use crate::MAX_CONSOLES;

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
/// Overrun: a byte was destroyed before anything read it.
const LSR_OE: u8 = 1 << 1;
/// Framing: a character arrived without a stop bit and was dropped.
const LSR_FE: u8 = 1 << 3;
/// Transmit holding register empty: the transmit FIFO is EMPTY.
///
/// Not "there is room". The standard promises that a driver seeing this set may
/// write a whole FIFO -- 16 bytes -- without looking again, which is what Linux's
/// 8250 does. This driver writes one byte at a time and so does not take the
/// promise up; the constant is still the standard's bit.
const LSR_THRE: u8 = 1 << 5;

/// The bits a read of LSR clears: OE, PE, FE, BI, and the bit 7 summary.
///
/// Named as a mask rather than checked bit by bit because the point is that
/// [`read_lsr`] must keep ALL of them: a reader that looks at overrun alone
/// still destroys the other three.
const LSR_ERRORS: u8 = 0x9e;

/// IER bit 0, ERBFI: interrupt while the receive FIFO holds a byte.
///
/// The ONLY IER bit this firmware ever sets, and the choice is the shell's
/// shape rather than the peripheral's.
///
/// Bit 1 (ETBEI, transmit holding register empty) would be usable: reading IIR
/// clears that interrupt here exactly as it does on a real part, so the standard
/// take-interrupt, read-IIR, write, return sequence terminates. What is missing
/// is anything to write from -- the shell formats straight into `Uart::put` with
/// no transmit ring behind it, so an interrupt saying "the FIFO is empty" would
/// have nothing to hand it. Bit 2 (ELSI, receiver line status) is likewise left
/// clear: an overrun is picked up by the next LSR read either way, and an
/// interrupt would only arrive sooner at somewhere that cannot print.
///
/// So transmit stays a bounded spin in `put()`. It costs nothing worth having:
/// the FIFO plus the gateware's elastic buffer absorb a line of output, and a
/// console that occasionally spins for a few microseconds is not a problem
/// anyone has.
pub const IER_ERBFI: u8 = 1 << 0;

/// 8 data bits, no parity, one stop bit -- and, critically, DLAB clear.
///
/// With DLAB set, offset 0 is the baud divisor latch rather than the data
/// register, so every character written would reprogram a baud rate instead of
/// being transmitted. Both the SoC peripheral and QEMU reset LCR to 0, so this
/// is belt and braces; a wrong value here looks exactly like "the firmware
/// produces no output", which is the failure this whole layer exists to
/// distinguish from a real one.
pub const LCR_8N1: u8 = 0x03;

/// IIR bits 7:6, set only when the FIFOs are enabled AND usable.
///
/// The same probe Linux's `autoconfig` makes: it separates a 16550A from a
/// 16550 whose FIFO was broken and from a 16450 with none. FCR is write-only
/// and IIR shares its address, so this is the only way to read back what
/// [`Uart::init`] established.
pub const IIR_FIFO_OK: u8 = 0xc0;

/// Enable the FIFOs and clear both of them.
///
/// Bit 0 enables, bit 1 clears receive, bit 2 clears transmit. Clearing at start
/// up matters on the board: the gateware's FIFOs survive a `j _start` reboot
/// (only the CPU restarts, not the peripheral), so a `reset` command issued
/// mid-line would otherwise leave the remainder of that line in the receive FIFO
/// to be interpreted as the first command of the new session.
const FCR_ENABLE_AND_CLEAR: u8 = 0x07;

/// The error bits seen on each console and not yet printed, OR-ed together.
///
/// A bitmask rather than a queued record, because the report has to survive the
/// conditions that produce it. An event pushed into `src/events.rs` is dropped
/// when that ring fills, and the ring fills under exactly the load that causes
/// an overrun -- so the one message worth having would be the one lost. Bits
/// OR-ed into a word cannot be lost, only coalesced.
///
/// Indexed the same way `target::UART_BASES` is. Written from any context,
/// including the interrupt handler, which is why it is atomic and why nothing
/// here prints.
static ERRORS: [AtomicU8; MAX_CONSOLES] = [const { AtomicU8::new(0) }; MAX_CONSOLES];

/// How many LSR reads have seen an error bit, per console, since boot. Never
/// reset, and printed by the `irq` shell command: a count that keeps climbing is
/// a line that keeps losing bytes, which a coalesced bitmask alone would hide.
static ERROR_READS: [AtomicU32; MAX_CONSOLES] = [const { AtomicU32::new(0) }; MAX_CONSOLES];

/// Read LSR once, keeping the error bits the read destroyed.
///
/// Every LSR read in this driver goes through here. See the module comment for
/// why there may not be another.
fn read_lsr(base: usize) -> u8 {
    // SAFETY: a fixed peripheral address in an uncached region; volatile because
    // the value changes underneath the compiler and because the read has a side
    // effect that must happen exactly once.
    let lsr = unsafe { read_volatile((base + LSR) as *const u8) };

    let bits = lsr & LSR_ERRORS;
    if bits != 0 {
        // Linear search over at most two entries, and only on the rare path
        // where something went wrong. It keeps `Uart::new(base)` -- which the
        // panic handler and every shell command use -- free of an index it
        // would have to be told and could be told wrongly.
        let mut index = 0;
        while index < target::UART_BASES.len() {
            if target::UART_BASES[index] == base {
                ERRORS[index].fetch_or(bits, Ordering::Relaxed);
                ERROR_READS[index].fetch_add(1, Ordering::Relaxed);
                return lsr;
            }
            index += 1;
        }
    }
    lsr
}

/// Error bits seen on console `index` since the last call, and cleared by it.
pub fn take_errors(index: usize) -> u8 {
    ERRORS[index].swap(0, Ordering::Relaxed)
}

/// How many LSR reads have seen an error on console `index` since boot.
pub fn error_reads(index: usize) -> u32 {
    ERROR_READS[index].load(Ordering::Relaxed)
}

/// Print anything the consoles have lost, on the console the caller owns.
///
/// Called from the main loop, alongside `events::drain`, and for the same
/// reason: an overrun is discovered inside an interrupt handler, which may not
/// print. Reported once per occurrence rather than once per pass, because a
/// line that says the same thing forever is a line nobody reads.
///
/// It prints on the PRIMARY console whichever port lost the byte. The second
/// port's transmit pin is JTAG TMS and this firmware never speaks there unbidden
/// -- see `target::NEVER_SPEAKS_FIRST` -- and a port that is dropping input is the last
/// one to announce it on.
pub fn report_errors(uart: &mut Uart) {
    use core::fmt::Write;

    let mut index = 0;
    while index < target::UART_BASES.len() {
        let bits = take_errors(index);
        if bits != 0 {
            crate::metrics::busy();
            crate::log!(
                uart,
                "uart {}: LSR {:02x}{}{} -- input lost before the CPU read it \
                 ({} so far)",
                index,
                bits,
                if bits & LSR_OE != 0 { " overrun" } else { "" },
                if bits & LSR_FE != 0 { " framing" } else { "" },
                error_reads(index)
            );
        }
        index += 1;
    }
}

/// A 16550 at a fixed base address.
///
/// `Copy`, and deliberately: an instance is nothing but an address, so a caller
/// may materialise one wherever it needs to speak without threading a handle
/// through every function. That is what lets the panic handler print without
/// borrowing the console that panicked.
/// The 16550's FIFO depth, in bytes.
///
/// 16 is the 16550A's, and it is what "write a whole FIFO without looking again"
/// already assumes -- named here so `info ports` reports the number the driver
/// acts on rather than one written down twice.
pub const FIFO_DEPTH: usize = 16;

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
            // Interrupts off first, and they stay off until `irq::init()` has
            // a handler, a PLIC and the CPU's `mie`/`mstatus` set up. An
            // external interrupt raised before that reaches riscv-rt's
            // `DefaultHandler`, which is an abort.
            //
            // This also matters on the way back round: a `j _start` reboot
            // restarts the CPU without resetting the peripherals, so a UART
            // left with ERBFI set would interrupt during init if the FIFOs
            // still held anything.
            write_volatile(self.reg(IER), 0);
            write_volatile(self.reg(LCR), LCR_8N1);
            write_volatile(self.reg(FCR), FCR_ENABLE_AND_CLEAR);
        }
    }

    /// What the peripheral says it is configured as: LCR, IIR, IER.
    ///
    /// Read back rather than restated, so [`init`] reports the machine instead
    /// of the constants above it. FCR is write-only and IIR shares its address,
    /// so the FIFO state comes from IIR bits 7:6.
    pub fn settings(&self) -> (u8, u8, u8) {
        // SAFETY: three reads of read-only or read/write registers, none of
        // which pops the receive FIFO or clears anything. IIR's read DOES
        // acknowledge a transmit-empty interrupt, which this driver never
        // enables -- see `IER_ERBFI`.
        unsafe {
            (
                read_volatile(self.reg(LCR)),
                read_volatile(self.reg(FCR)),
                read_volatile(self.reg(IER)),
            )
        }
    }

    /// Writes one byte, waiting for the transmit FIFO to be empty.
    ///
    /// Bounded, not an infinite spin.
    ///
    /// Waiting for EMPTY rather than for room, because that is what LSR.THRE
    /// means -- the standard's promise is that a driver seeing it set may write a
    /// whole FIFO, so the bit cannot also mean "one slot free". This driver takes
    /// the promise up one byte at a time, which costs a poll per byte and leaves
    /// the FIFO holding at most one: the elastic buffer behind it is what absorbs
    /// a line of output, and that is where buffering was deliberately put.
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
            // longer than the microsecond a byte needs to reach the elastic
            // buffer behind a USB endpoint that is being serviced. That is why
            // the bound did not move when THRE became "empty" rather than "has
            // room": what it is waiting on is the transport, not the FIFO depth,
            // and the transport is what stops. Reaching it therefore means
            // nothing is draining at all, and the only useful response to that
            // is to carry on and let the next byte try again.
            let mut spins = 0u32;
            // `read_lsr`, not a bare read: this loop is the busiest LSR reader in
            // the firmware, and a bare read here would clear an overrun the
            // receive path had not noticed yet -- a transmit spin quietly eating
            // the report of lost input.
            while read_lsr(self.base) & LSR_THRE == 0 {
                spins += 1;
                if spins > 200_000 {
                    return;
                }
            }
            write_volatile(self.reg(RBR_THR), byte);
        }
    }
}

/// The receive and interrupt-control half of a 16550, and nothing else.
///
/// This exists so that `src/irq.rs` -- the one module an interrupt handler lives
/// in -- has no way to transmit. It has no `put`, no `core::fmt::Write`, and
/// therefore no `write!`; a handler that reaches for one gets a compile error
/// rather than a spin on `LSR.THRE` inside a level-sensitive interrupt, which is
/// a hang that presents as a dead CPU. `src/events.rs` is what a handler uses
/// instead, and its module comment has the full argument.
///
/// It is a separate type rather than a shared reference because the point is
/// what is ABSENT: a `Uart` behind any kind of reference still has `put` on it.
/// Rust's privacy is per-module-tree and cannot hide `Uart` from a sibling
/// module, so the remaining half of the enforcement is the `irqlog` grep in
/// `scripts/check.py`.
///
/// The receive side is here and NOT on `Uart`, rather than on both: two ways to
/// pop the same FIFO is two things to keep in step, and the split is only worth
/// anything if it is the only route.
#[derive(Clone, Copy)]
pub struct UartRx {
    base: usize,
}

impl UartRx {
    pub const fn new(base: usize) -> Self {
        UartRx { base }
    }

    fn reg(&self, offset: usize) -> *mut u8 {
        (self.base + offset) as *mut u8
    }

    /// Ask for an interrupt whenever a byte is waiting.
    ///
    /// Separate from `Uart::init()` because ordering matters: every UART must be
    /// quiet before the PLIC is configured and `mstatus.MIE` is set, and only
    /// then may any of them start asking. `irq::init()` does both halves in that
    /// order.
    ///
    /// Writes the whole register rather than or-ing a bit in, because the other
    /// three IER bits must stay clear -- see `IER_ERBFI` for what setting bit 1
    /// would do -- and a read-modify-write from outside a critical section could
    /// resurrect one the handler had just cleared.
    pub fn enable_rx_interrupt(&mut self) {
        // SAFETY: a fixed peripheral address; write only.
        unsafe { write_volatile(self.reg(IER), IER_ERBFI) }
    }

    /// Stop asking for interrupts on this port.
    ///
    /// The receive FIFO keeps whatever is in it and LSR still reports it; only
    /// the interrupt line drops. That is what makes this usable as flow control:
    /// when the software ring fills, the handler calls this, returns, and lets
    /// the consumer run. Without it a level-sensitive source with a full ring
    /// re-enters the handler forever and the CPU makes no progress at all --
    /// which looks exactly like a hung board.
    pub fn disable_rx_interrupt(&mut self) {
        // SAFETY: as above.
        unsafe { write_volatile(self.reg(IER), 0) }
    }

    /// One byte if any has been received, else `None`. Never blocks.
    pub fn get(&mut self) -> Option<u8> {
        // SAFETY: fixed peripheral addresses; volatile because these are devices
        // whose values change underneath the compiler.
        //
        // DR must be checked first and RBR must not be touched otherwise:
        // reading RBR pops the FIFO whether or not it held anything, and reading
        // an empty one returns the previous byte rather than blocking. That is
        // the whole reason LSR lives at +5 -- a different 32-bit word from RBR at
        // +0 -- so that no widening, prefetch or replay of this poll can reach
        // the data register.
        //
        // `read_lsr` keeps the error bits this read clears. On the port that is
        // a real serial line they are the only evidence a byte was lost, and
        // this is the read most likely to be the one that sees them -- the
        // handler runs the instant a byte lands.
        unsafe {
            if read_lsr(self.base) & LSR_DR == 0 {
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

/// What `uart_init()` established and read back.
pub struct Init {
    pub ports: usize,
    pub lcr: u8,
    pub iir: u8,
    pub ier: u8,
}

impl Init {
    /// 8N1 with DLAB clear, and FIFOs that are enabled and usable.
    pub fn established(&self) -> bool {
        self.lcr == LCR_8N1 && self.iir & IIR_FIFO_OK == IIR_FIFO_OK
    }
}

/// `uart_init()` -- every console, and the one report nothing else can make.
///
/// The console is the channel every other `_init()` reports on, so it goes
/// first and is the one peripheral whose failure is silent by construction.
/// It cannot log its own initialisation; it can only report afterwards, from
/// the registers rather than from the constants above.
///
/// **`establish` is destructive to the FIFOs by design, and only at boot.** A
/// `j _start` reboot restarts the CPU and not the peripherals, so a port left
/// holding half a command line would run it as the first command of the new
/// session (#239). Doing that from the shell would discard the reply being
/// written, so a re-run reads back and touches nothing.
///
/// Every port, not just the primary: an uninitialised 16550 has its FIFOs in
/// whatever state the last boot left them.
pub fn init(establish: bool) -> Init {
    if establish {
        for &base in target::UART_BASES {
            Uart::new(base).init();
        }
        // The peripheral claims its own PLIC sources, rather than being
        // remembered by a list in a file that is not its driver (#264).
        crate::irq::claim_consoles();
    }
    let (lcr, iir, ier) = Uart::new(target::UART_BASES[0]).settings();
    Init { ports: target::UART_BASES.len(), lcr, iir, ier }
}
