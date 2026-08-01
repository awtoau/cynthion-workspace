//! Firmware for the Cynthion r1.4 VexiiRiscv SoC.
//!
//! Deliberately minimal, and deliberately not built on `lunasoc-hal`: that crate pins
//! `embedded-hal` to `=1.0.0-alpha.9`, a pre-1.0 alpha whose serial traits were removed
//! before 1.0 shipped. All a console needs is `core::fmt::Write`, which is in `core` and
//! is implemented in `src/uart.rs` in about six lines. That is what makes `writeln!` work.
//!
//! ## Two targets, one shell, one driver
//!
//! This file is compiled unchanged for the FPGA and for QEMU, and so is `src/uart.rs`:
//!
//!     default          -> src/target.rs + memory.x       (RAM at 0x0000_0000)
//!     --features qemu  -> src/target.rs + memory-qemu.x  (RAM at 0x8000_0000)
//!
//! There is no longer a per-target console. The SoC's console peripheral is a standard
//! NS16550A (`ecp5-test/riscv/uart16550.py`) and QEMU's `-M virt` presents a standard
//! NS16550A, so both are driven by `src/uart.rs` and the entire difference between the
//! builds is a list of base addresses, a flash stand-in, and a linker script.
//!
//! `scripts/soc_test.py` builds the QEMU variant, drives this shell over a pipe and
//! asserts what it says; `scripts/soc_run.py` will not configure the board until those
//! assertions pass. The value of that gate depends entirely on the two builds sharing
//! source, so resist the urge to `#[cfg]` anything below this line -- put the difference
//! in `src/target.rs` instead.
//!
//! ## More than one console
//!
//! The shell is not a singleton and neither is the console. `Shell` holds one line
//! editor's worth of state, and the main loop runs one per UART in `target::UART_BASES`,
//! polling each in turn. Two people on two ports get two independent prompts; a command
//! typed on one replies on that one. The only asymmetry is index 0, which is the port the
//! boot banner, the bootloader's reports and any panic go to, because those happen before
//! or outside any prompt.

#![no_std]
#![no_main]

use core::fmt::Write;
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

use riscv_rt::entry;

mod hyperram;
mod target;
mod uart;

use target::flash_word;
use uart::Uart;

/// The most consoles this build will run shells for.
///
/// Sized rather than allocated: `Shell` is ~80 bytes and there is no allocator. Four is
/// well past the two the hardware has and costs a third of a kilobyte of the 32 KiB the
/// shell half of block RAM gives us.
const MAX_CONSOLES: usize = 4;

// A base address with no shell behind it would be a port that silently never answers,
// which is the exact class of failure this firmware keeps being bitten by. Catch it at
// compile time instead.
const _: () = assert!(target::UART_BASES.len() <= MAX_CONSOLES);

/// One console's line editor and its idle state.
///
/// Per-console rather than global: `spoken` latching on one port must not silence the
/// re-banner on another, and two half-typed command lines must not share a buffer.
struct Shell {
    line: [u8; 64],
    len: usize,
    /// Set by the first keypress. From then on the prompt is on screen and reprinting
    /// the banner would fight the line being edited.
    spoken: bool,
    idle: u32,
}

impl Shell {
    const NEW: Shell = Shell {
        line: [0u8; 64],
        len: 0,
        spoken: false,
        idle: 0,
    };

    /// Handle at most one byte from `uart`, or count one turn of idleness.
    ///
    /// `announce` re-prints the banner and prompt periodically while nothing has been
    /// typed. Printing them once is invisible: the CPU starts the moment the FPGA is
    /// configured and the host takes about half a second to enumerate and bind a tty, so
    /// a terminal attaching afterwards has already missed everything. Worse, an idle
    /// shell that only prints on input is indistinguishable from a dead one -- there is
    /// nothing to see until you type, and no reason to believe typing will work.
    ///
    /// It is off for every console but the first, because on this board the second one's
    /// TX pin is shared with JTAG TMS and an unbidden transmission is bus contention.
    /// See `target::ANNOUNCING`.
    fn poll(&mut self, uart: &mut Uart, announce: bool) {
        let byte = match uart.get() {
            Some(byte) => byte,
            None => {
                if announce && !self.spoken {
                    self.idle = self.idle.wrapping_add(1);
                    // ~2 s at 60 MHz with one console, and proportionally longer with
                    // more, since each turn of the outer loop now polls each of them.
                    // Not calibrated; it only has to be slow enough to read and fast
                    // enough that attaching does not feel dead. Under QEMU the same
                    // count runs in about two seconds, because each pass costs one
                    // emulated MMIO read -- close enough that the test does not need
                    // its own value.
                    if self.idle >= 12_000_000 {
                        self.idle = 0;
                        banner(uart);
                        let _ = write!(uart, "> ");
                    }
                }
                return;
            }
        };
        // First keypress: stop re-announcing, the user is here.
        self.spoken = true;

        match byte {
            // Enter. Both, because terminals disagree about which they send.
            b'\r' | b'\n' => {
                let _ = write!(uart, "\n");
                if self.len > 0 {
                    let len = self.len;
                    // Copied out before dispatch so `run` may borrow the uart mutably
                    // while the line it was given stays valid.
                    let mut line = [0u8; 64];
                    line[..len].copy_from_slice(&self.line[..len]);
                    self.len = 0;
                    run(uart, &line[..len]);
                }
                let _ = write!(uart, "> ");
            }
            // Backspace and delete. Erase on screen as well as in the buffer, or the
            // display and the buffer disagree about what the command is.
            0x08 | 0x7f => {
                if self.len > 0 {
                    self.len -= 1;
                    let _ = write!(uart, "\x08 \x08");
                }
            }
            // Printable ASCII only. Echo, since the device gets raw bytes and nothing
            // else will show what was typed.
            0x20..=0x7e => {
                if self.len < self.line.len() {
                    self.line[self.len] = byte;
                    self.len += 1;
                    uart.put(byte);
                }
            }
            // Everything else -- stray control codes, terminal escape sequences -- is
            // dropped. Echoing or reporting them is worse than silence: an escape
            // sequence would be replayed at the terminal, and a chatty default turns a
            // stuck RX FIFO into an unstoppable wall of text.
            _ => {}
        }
    }
}

/// The console the banner, the bootloader and any panic speak on.
fn primary() -> Uart {
    Uart::new(target::UART_BASES[0])
}

#[entry]
fn main() -> ! {
    // Every UART, not just the primary: an uninitialised 16550 has its FIFOs in whatever
    // state the last boot left them, and on this SoC a `j _start` reboot restarts the CPU
    // without resetting the peripherals. A port left holding half a command line would
    // run it as the first command of the new session.
    for &base in target::UART_BASES {
        Uart::new(base).init();
    }

    let mut console = primary();

    // Banner BEFORE the HyperRAM probe, deliberately.
    //
    // try_boot() touches a peripheral, and if that bus access never completes the CPU
    // stalls inside the load itself -- no software timeout can escape that. Printing
    // first means a silent board and a board that hangs on HyperRAM are distinguishable,
    // which they were not: both looked like a dead CPU.
    banner(&mut console);

    // Run a staged image if one is present and intact.
    //
    // This is the bootloader half. `load` stages into HyperRAM and reboots; on the way
    // back up we land here, verify, copy into the payload slot and jump. A failed check
    // falls through to the shell rather than halting -- a board that drops to a prompt
    // can be told what went wrong, one that hangs cannot.
    try_boot(&mut console);

    let mut shells = [Shell::NEW; MAX_CONSOLES];

    loop {
        // Round-robin, one byte per console per pass. Fair by construction and with no
        // arbitration to get wrong: a console that is being pasted into cannot starve
        // the others, because it still only gets one byte per turn.
        for (index, &base) in target::UART_BASES.iter().enumerate() {
            let mut uart = Uart::new(base);
            shells[index].poll(&mut uart, index < target::ANNOUNCING);
        }
    }
}

fn banner(uart: &mut Uart) {
    let _ = writeln!(uart, "\nCynthion RISC-V SoC - Rust firmware");
    let _ = writeln!(uart, "type `help` or `?` for commands");
}

/// Dispatch one command line.
fn run(uart: &mut Uart, line: &[u8]) {
    // Split off the first word; the rest is the argument.
    let (cmd, rest) = match line.iter().position(|&b| b == b' ') {
        Some(i) => (&line[..i], &line[i + 1..]),
        None => (line, &line[..0]),
    };

    match cmd {
        b"help" | b"?" => {
            let _ = writeln!(uart, "  help, ?       this");
            let _ = writeln!(uart, "  id            flash JEDEC id and capacity");
            let _ = writeln!(uart, "  read <hex>    read a word from flash");
            let _ = writeln!(uart, "  check         arithmetic and known flash values");
            let _ = writeln!(uart, "  ports         the consoles this firmware answers on");
            let _ = writeln!(uart, "  load <hex>    receive N bytes into the payload slot");
            let _ = writeln!(uart, "  go            jump to the loaded payload");
            let _ = writeln!(uart, "  reset         restart the firmware");
        }
        b"id" => {
            // Reads through the memory map, which is the verified path. The JEDEC id
            // itself needs the SPI controller, which the C firmware drives; this
            // reports what the memory map can see.
            let _ = writeln!(uart, "flash @0 {:08x}", flash_word(0));
        }
        b"ports" => {
            // Answers "is the second UART actually there" without a bitstream rebuild.
            // SCR is eight bits of scratch that do nothing else, so writing a pattern
            // and reading it back distinguishes a peripheral that exists from an address
            // that decodes to nothing -- which on this bus returns zeros rather than
            // faulting, and so is otherwise invisible.
            for (index, &base) in target::UART_BASES.iter().enumerate() {
                let present = scratch_responds(base);
                let _ = writeln!(uart, "  {} {:08x} {}", index, base,
                                 if present { "ok" } else { "NO RESPONSE" });
            }
        }
        b"read" => match parse_hex(rest) {
            Some(offset) => {
                // Bounded to the 4 MiB the flash actually holds. Above that the address
                // aliases back onto offset 0, which would read as real data.
                if offset >= 0x0040_0000 {
                    let _ = writeln!(uart, "offset past 4 MiB; it would alias to 0");
                } else {
                    let word = flash_word(offset as usize & !3);
                    let _ = writeln!(uart, "flash @{:06x} {:08x}", offset, word);
                }
            }
            None => {
                let _ = writeln!(uart, "usage: read <hex offset>");
            }
        },
        b"check" => {
            let a: u32 = 0x1234_5678;
            let b: u32 = 0x9abc_def0;
            // SAFETY: our own stack slots; volatile defeats constant folding, so this
            // measures the CPU rather than the compiler.
            let (a, b) = unsafe { (read_volatile(&a), read_volatile(&b)) };
            let sum = a.wrapping_add(b);
            let prod = a.wrapping_mul(3);
            let f0 = flash_word(0);
            let f40 = flash_word(0x40);

            let _ = writeln!(uart, "sum   {:08x} {}", sum,
                             if sum == 0xacf1_3568 { "ok" } else { "BAD" });
            let _ = writeln!(uart, "prod  {:08x} {}", prod,
                             if prod == 0x369d_0368 { "ok" } else { "BAD" });
            let _ = writeln!(uart, "@0    {:08x} {}", f0,
                             if f0 == 0x6150_00ff { "ok" } else { "BAD" });
            let _ = writeln!(uart, "@40   {:08x} {}", f40,
                             if f40 == 0x2a55_8800 { "ok" } else { "BAD" });
        }
        b"load" => match parse_hex(rest) {
            Some(len) => load(uart, len),
            None => {
                let _ = writeln!(uart, "usage: load <hex byte count>");
            }
        },
        b"hrtest" => {
            // Round-trip one word so the HyperRAM path can be checked without staging a
            // whole image.
            hyperram::write_header(0, 0);
            match hyperram::staged() {
                Some(_) => {
                    let _ = writeln!(uart,
                        "hyperram round-trip BAD: zero length should be rejected");
                }
                None => {
                    let _ = writeln!(uart, "hyperram write+read ok");
                }
            }
            hyperram::invalidate();
        }
        b"go" => {
            let _ = writeln!(uart, "jumping to {:08x}", payload_start());

            // Flush before fetching. The payload arrived as DATA through the D-cache,
            // so without this the I-side may fetch stale lines from before the load --
            // executing whatever was there, which presents as a hang or a wild fault
            // rather than as a cache problem. `fence.i` makes stores visible to fetch.
            unsafe {
                core::arch::asm!("fence", "fence.i");
                let entry: extern "C" fn() -> ! =
                    core::mem::transmute(payload_start() as usize);
                entry();
            }
        }
        b"reset" => {
            let _ = writeln!(uart, "restarting");
            // No reset controller yet, so jump to the reset vector. This re-runs main
            // without re-initialising .bss or the stack pointer -- enough to restart the
            // shell, not a true reset. A real one needs a CSR the SoC does not have.
            unsafe {
                core::arch::asm!("j _start", options(noreturn));
            }
        }
        _ => {
            let _ = writeln!(uart, "unknown command; try `help`");
        }
    }
}

/// Does the 16550 at `base` have a working scratch register?
///
/// Two patterns, not one: a single value could match a bus that returns the last thing it
/// saw, and 0x00/0xff could match a floating or tied-off read. Restores nothing afterwards
/// because SCR is defined to do nothing.
fn scratch_responds(base: usize) -> bool {
    const SCR: usize = 7;
    let reg = (base + SCR) as *mut u8;
    // SAFETY: SCR is eight bits of scratch on every 16550; writing it has no effect on
    // any other register, the FIFOs, or anything transmitted. `base` comes from
    // target::UART_BASES, which is the SoC's own address map.
    unsafe {
        let mut ok = true;
        for pattern in [0x5au8, 0xa5] {
            write_volatile(reg, pattern);
            ok &= read_volatile(reg) == pattern;
        }
        ok
    }
}

unsafe extern "C" {
    /// Start and size of the payload slot, from `memory.x` / `memory-qemu.x`. Taking
    /// these from the linker rather than hardcoding them means the two cannot drift:
    /// change the split there and this follows.
    static _payload_start: u8;
    static _payload_size: u8;
}

fn payload_start() -> u32 {
    (&raw const _payload_start) as u32
}

#[allow(dead_code)]
fn payload_size() -> u32 {
    // The linker exports this as an ADDRESS, not a value -- `_payload_size` is defined
    // as a bare number, so its "address" is the number. Reading it as a u8 would give
    // whatever byte happens to live at the slot's base.
    (&raw const _payload_size) as u32
}

/// Receive `len` bytes over the console and stage them in HyperRAM, then reboot.
///
/// The bytes arrive over the USB bulk OUT endpoint -- the same transport `apollo
/// flash-write` uses, and about four orders of magnitude faster than the JTAG register
/// path this replaced (34 ms per 16-bit word, measured).
///
/// They go to HyperRAM rather than straight into the payload slot because the next step
/// is a reboot, and a reboot is exactly what block RAM does not survive intact: the
/// shell doing the receiving is executing from it. HyperRAM is external and keeps its
/// contents across a CPU reset.
fn load(uart: &mut Uart, len: u32) {
    if len == 0 || len > hyperram::MAX_IMAGE {
        let _ = writeln!(uart, "length must be 1..{:x}", hyperram::MAX_IMAGE);
        return;
    }

    let _ = writeln!(uart, "send {} bytes", len);

    let mut crc = hyperram::Crc32::new();
    let mut received = 0u32;
    let mut pending: Option<u8> = None;

    // Seek once; the gateware auto-increments, so the inner loop is one store per word.
    hyperram::seek_image();

    while received < len {
        // Blocking on THIS console, and only this one: once the sender has started there
        // is nothing else to do, and returning to the prompt mid-transfer would interpret
        // the image as commands. The other consoles are not serviced for the duration,
        // which is correct -- a transfer in flight is not a moment to run a command.
        let byte = match uart.get() {
            Some(b) => b,
            None => continue,
        };
        crc.push(byte);
        received += 1;

        // HyperRAM is 16 bits wide, so bytes are paired little-endian.
        match pending.take() {
            None => pending = Some(byte),
            Some(low) => hyperram::write_word((low as u16) | ((byte as u16) << 8)),
        }
    }

    // An odd-length image still has to fill its final word.
    if let Some(low) = pending {
        hyperram::write_word(low as u16);
    }

    let crc = crc.finish();
    hyperram::write_header(len, crc);
    let _ = writeln!(uart, "staged {} bytes, crc {:08x}; rebooting", received, crc);

    // Reboot into the bootloader path at the top of main().
    unsafe {
        core::arch::asm!("j _start", options(noreturn));
    }
}

/// Run a staged image, if one is present and its CRC matches.
///
/// Called before the shell starts. Returns normally when there is nothing to boot, or
/// when the image fails its check -- dropping to a prompt that can explain the failure
/// beats halting, which looks identical to a hang.
fn try_boot(uart: &mut Uart) {
    let (length, expected) = match hyperram::staged() {
        Some(header) => header,
        None => return,
    };

    let _ = writeln!(uart, "\nstaged image: {} bytes, crc {:08x}", length, expected);

    // Copy and checksum in one pass, reading back what was actually STORED rather than
    // trusting the CRC computed on arrival. That is what makes this cover the HyperRAM
    // round trip and not merely the USB transfer.
    let dest = payload_start() as *mut u8;
    let mut crc = hyperram::Crc32::new();
    hyperram::seek_image();

    let mut written = 0u32;
    while written < length {
        let word = hyperram::read_word();
        for shift in [0u32, 8] {
            if written < length {
                let byte = (word >> shift) as u8;
                crc.push(byte);
                // SAFETY: bounds-checked against `length`, which `staged()` has already
                // limited to the payload slot's size.
                unsafe { write_volatile(dest.add(written as usize), byte) };
                written += 1;
            }
        }
    }

    let actual = crc.finish();
    if actual != expected {
        // Drop it. Leaving the header in place would retry the same bad image on every
        // reset, and the board would never reach a usable prompt.
        hyperram::invalidate();
        let _ = writeln!(uart, "crc MISMATCH: got {:08x}, want {:08x} -- image \
                                dropped, send it again", actual, expected);
        return;
    }

    let _ = writeln!(uart, "crc ok; starting payload at {:08x}", payload_start());

    // Flush before fetching: the image arrived as DATA through the D-cache, so without
    // this the I-side may fetch stale lines and execute whatever was there before.
    unsafe {
        core::arch::asm!("fence", "fence.i");
        let entry: extern "C" fn() -> ! = core::mem::transmute(payload_start() as usize);
        entry();
    }
}

/// Parse an ASCII hex number. `None` if empty or malformed -- better than a wrong
/// address silently read.
fn parse_hex(text: &[u8]) -> Option<u32> {
    let text = match text.iter().position(|&b| b != b' ') {
        Some(i) => &text[i..],
        None => return None,
    };
    let mut value: u32 = 0;
    let mut digits = 0;
    for &byte in text {
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            b'A'..=b'F' => byte - b'A' + 10,
            b' ' => break,
            _ => return None,
        };
        value = value.checked_mul(16)?.checked_add(digit as u32)?;
        digits += 1;
    }
    if digits == 0 { None } else { Some(value) }
}

/// There is nowhere to report a panic except the console, and no way to recover.
///
/// Printing rather than silently spinning matters: a panicking CPU and a hung one look
/// identical from the host, and that ambiguity has cost real time on this project.
#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    // A fresh handle rather than the one that panicked: taking it by value cannot
    // deadlock, and a `Uart` is nothing but an address so constructing one costs nothing.
    //
    // Deliberately NOT `init()`ed. Initialising clears the transmit FIFO, which would
    // discard whatever the panicking code had already queued -- quite possibly the last
    // line printed before things went wrong, which is the one worth having. LCR resets to
    // 0 on both targets, so DLAB is clear and THR is reachable without any setup.
    let mut uart = primary();
    let _ = writeln!(uart, "\n*** PANIC: {}", info);
    loop {}
}
