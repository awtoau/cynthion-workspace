//! Firmware for the Cynthion r1.4 VexiiRiscv SoC.
//!
//! Deliberately minimal, and deliberately not built on `lunasoc-hal`: that crate pins
//! `embedded-hal` to `=1.0.0-alpha.9`, a pre-1.0 alpha whose serial traits were removed
//! before 1.0 shipped. All a console needs is `core::fmt::Write`, which is in `core` and
//! is implemented below in about six lines. That is what makes `writeln!` work.
//!
//! Register addresses are hardcoded here for now. They should come from a generated PAC
//! (`scripts/soc_generate_pac.py`), which is the point of doing this in Rust at all --
//! the C firmware transcribes offsets from the gateware by hand, and that is exactly the
//! class of error that had firmware sending `0x9f << 24` because a comment asserted the
//! PHY did not left-justify, when it does.

#![no_std]
#![no_main]

use core::fmt::Write;
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

use riscv_rt::entry;

/// Console peripheral, matching `CONSOLE_BASE` in `ecp5-test/riscv/vexii_hello_soc.py`.
const CONSOLE_BASE: usize = 0xf000_0000;
const CONSOLE_DATA: *mut u8 = CONSOLE_BASE as *mut u8;
const CONSOLE_READY: *const u8 = (CONSOLE_BASE + 1) as *const u8;

/// The SoC console: a byte sink that reaches the host over USB CDC-ACM.
///
/// Behind it is an `AsyncFIFOBuffered` crossing from `sync` to `usb`, then LUNA's
/// `USBSerialDevice`. There is no UART anywhere -- no baud rate, no start bits. The
/// 115200 a host sets is a legacy field the device ignores.
struct Console;

impl Console {
    /// Writes one byte, waiting for FIFO space.
    ///
    /// `ready` is the FIFO's write-side space, high when it can accept a byte. Spinning
    /// here is correct: the FIFO drains at USB speed and a full one means the host is
    /// behind, not that anything is wrong.
    fn put(&mut self, byte: u8) {
        // SAFETY: CONSOLE_READY and CONSOLE_DATA are fixed peripheral addresses declared
        // in the SoC's memory map, in a `main=0` region so accesses are uncached.
        unsafe {
            while read_volatile(CONSOLE_READY) == 0 {}
            write_volatile(CONSOLE_DATA, byte);
        }
    }
}

impl Write for Console {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        for byte in s.as_bytes() {
            self.put(*byte);
        }
        Ok(())
    }
}

#[entry]
fn main() -> ! {
    let mut console = Console;

    let _ = writeln!(console, "\r\nRISC-V on Cynthion: Rust, block RAM, USB console.");

    // The same values the C firmware prints, so the two are directly comparable.
    //
    // `read_volatile` on locals stops the compiler folding these at build time: a
    // constant printed by a CPU that never executed proves nothing, and this is the
    // check that distinguishes a running core from a stored answer.
    let a: u32 = 0x1234_5678;
    let b: u32 = 0x9abc_def0;
    // SAFETY: reading our own stack slots; volatile only to defeat constant folding.
    let (a, b) = unsafe { (read_volatile(&a), read_volatile(&b)) };

    let _ = writeln!(console, "sum  {:08x}", a.wrapping_add(b));
    let _ = writeln!(console, "prod {:08x}", a.wrapping_mul(3));

    // Print the banner, then go quiet.
    //
    // An earlier version printed a tick every iteration with no delay, which made the
    // console unreadable -- ~600,000 lines before anyone could look at it. The C
    // firmware paced itself with a one-second busy-wait for exactly this reason.
    //
    // Neither is right. Liveness belongs on the LEDs (green heartbeat), not in a
    // scrolling log, and a console that has said everything it has to say should stop
    // talking. It still emits one line a second so a reader attaching late learns the
    // CPU is alive, but nothing beyond that.
    //
    // The delay is a busy-wait because there is no timer peripheral yet. It is not
    // calibrated: at 60 MHz roughly 6M iterations of a volatile loop is about a second,
    // and it only has to be slow enough to read.
    let mut tick: u32 = 0;
    loop {
        let _ = writeln!(console, "alive {:08x}", tick);
        tick = tick.wrapping_add(1);

        let mut spin: u32 = 0;
        while spin < 6_000_000 {
            // SAFETY: reading a local; volatile stops the loop being optimised away.
            spin = unsafe { read_volatile(&spin) }.wrapping_add(1);
            unsafe { write_volatile(&mut spin as *mut u32, spin) };
        }
    }
}

/// There is nowhere to report a panic except the console, and no way to recover.
///
/// Printing rather than silently spinning matters: a panicking CPU and a hung one look
/// identical from the host, and that ambiguity has cost real time on this project.
#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    let mut console = Console;
    let _ = writeln!(console, "\r\n*** PANIC: {}", info);
    loop {}
}
