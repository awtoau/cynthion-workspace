//! A payload image, loaded into RAM by the resident shell and jumped to.
//!
//! This is the fast-iteration path: editing this file and running
//! `scripts/soc_payload.py` takes seconds, where changing the resident shell needs a
//! ~60 s bitstream rebuild because that image is baked into the gateware as block RAM
//! init.
//!
//! ## Rules for anything written here
//!
//! - **No `riscv-rt`.** Its `_start` is linked for `0x0` and would collide with the
//!   shell, which is still resident and still owns the low half of RAM.
//! - **No zero-initialised statics.** Nothing zeroes `.bss` -- that is normally
//!   `riscv-rt`'s job. Keep state in locals.
//! - **Do not write below `0x8000`.** That is the live shell and the live stack.
//!
//! Returning from `payload_main` returns to the shell's prompt, because the shell
//! called us with a normal `jalr` and `ra` still points into it.

#![no_std]
#![no_main]

use core::fmt::Write;
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

/// Same console peripheral the shell uses -- the NS16550A in
/// `ecp5-test/riscv/uart16550.py`. Hardcoded for the same reason and with the same
/// eventual fix: a generated PAC.
///
/// Not shared with `cynthion-soc`'s `src/uart.rs`: that would make this crate depend on
/// the resident shell's crate, and this one deliberately depends on nothing so that a
/// payload can be built and loaded while the shell is being changed. Six lines of
/// duplication buys that independence; if it grows past this, make a shared crate rather
/// than a dependency.
const CONSOLE_BASE: usize = 0xf000_0000;
const CONSOLE_THR: *mut u8 = CONSOLE_BASE as *mut u8;
/// Line status at +5, four bytes clear of THR. Bit 5 is THRE: room to send.
const CONSOLE_LSR: *const u8 = (CONSOLE_BASE + 5) as *const u8;
const LSR_THRE: u8 = 1 << 5;

const FLASH_BASE: usize = 0x1000_0000;

struct Console;

impl Console {
    fn put(&mut self, byte: u8) {
        // SAFETY: fixed peripheral addresses from the SoC memory map, uncached region.
        unsafe {
            while read_volatile(CONSOLE_LSR) & LSR_THRE == 0 {}
            write_volatile(CONSOLE_THR, byte);
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

/// Entry stub, placed at the very base of the payload slot by `memory.x`.
///
/// The shell jumps to the slot's ADDRESS -- it has no symbol table for this image -- so
/// whatever sits at `0x8000` is what runs. `.start` is placed first for exactly that
/// reason, and `naked` guarantees the compiler emits no prologue ahead of the jump.
#[unsafe(no_mangle)]
#[unsafe(link_section = ".start")]
#[unsafe(naked)]
pub unsafe extern "C" fn _payload_entry() -> ! {
    core::arch::naked_asm!("j {main}", main = sym payload_main)
}

/// The actual payload. Edit freely; this is the fast loop.
#[unsafe(no_mangle)]
pub extern "C" fn payload_main() -> ! {
    let mut console = Console;

    let _ = writeln!(console, "\r\npayload running at {:08x}",
                     _payload_entry as *const () as usize);

    // Prove this is genuinely executing loaded code rather than something stale: read
    // flash, which only works if the memory map and caches are live.
    let word = unsafe { read_volatile(FLASH_BASE as *const u32) };
    let _ = writeln!(console, "flash @0 {:08x} {}", word,
                     if word == 0x6150_00ff { "ok" } else { "BAD" });

    let _ = writeln!(console, "payload done; `reset` for the shell");

    // No return: the shell's `go` used a plain jump, so there is no reliable `ra` to
    // return through. `reset` restarts the shell.
    loop {}
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    let mut console = Console;
    let _ = writeln!(console, "\r\n*** PAYLOAD PANIC: {}", info);
    loop {}
}
