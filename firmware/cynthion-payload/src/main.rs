//! A payload image: what the bootloader runs instead of the shell.
//!
//! This is the fast-iteration path. Staging this over JTAG or the console takes
//! seconds, where changing what the bitstream carries needs a ~60 s rebuild because
//! those images are baked into the gateware as block RAM init.
//!
//! It REPLACES the shell. The bootloader at 0x0 copies one image into the image region
//! and jumps to it, so there is exactly one running at a time and no resident code to
//! return to.
//!
//! ## Rules for anything written here
//!
//! - **No `riscv-rt`.** It would bring a runtime `memory.x` already replaces.
//! - **No zero-initialised statics.** Nothing zeroes `.bss` -- that is normally
//!   `riscv-rt`'s job. Keep state in locals.
//! - **Do not return.** There is nowhere to return to; the bootloader jumped here.
//! - **Do not write below `0x400`.** That is the bootloader, and it is what recovers
//!   the board.

#![no_std]
#![no_main]

use core::fmt::Write;
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

/// Same console peripheral the shell uses -- the NS16550A in
/// `gateware/soc/peripherals/uart16550.py`. Hardcoded for the same reason and with the same
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

/// Entry stub, placed at the very base of the image region by `memory.x`.
///
/// The bootloader jumps to the region's ADDRESS -- it has no symbol table for this
/// image -- so whatever sits at `0x400` is what runs. `.start` is placed first for
/// exactly that reason, and `naked` guarantees the compiler emits no prologue ahead of
/// the jump.
///
/// `sp` first, and nothing before it. The bootloader hands over with `sp` still inside
/// its own kilobyte at the bottom of block RAM; a prologue running at that point would
/// push onto the code that had just jumped here.
#[unsafe(no_mangle)]
#[unsafe(link_section = ".start")]
#[unsafe(naked)]
pub unsafe extern "C" fn _payload_entry() -> ! {
    core::arch::naked_asm!(
        "la sp, _stack_start",
        "j {main}",
        main = sym payload_main,
    )
}

/// The actual payload. Edit freely; this is the fast loop.
#[unsafe(no_mangle)]
pub extern "C" fn payload_main() -> ! {
    let mut console = Console;

    let _ = writeln!(
        console,
        "\r\npayload running at {:08x}",
        _payload_entry as *const () as usize
    );

    // Prove this is genuinely executing loaded code rather than something stale: read
    // flash, which only works if the memory map and caches are live.
    let word = unsafe { read_volatile(FLASH_BASE as *const u32) };
    let _ = writeln!(
        console,
        "flash @0 {:08x} {}",
        word,
        if word == 0x6150_00ff { "ok" } else { "BAD" }
    );

    let _ = writeln!(
        console,
        "payload done; \
                              `./scripts/soc_jtag_stage.py --clear` for the shell"
    );

    // No return: the bootloader jumped here, and there is no shell left underneath to
    // return to -- this image is where that one was. A reset comes straight back here,
    // because the header in HyperRAM still names this image; clearing it is what makes
    // the bootloader fall back to the bitstream's own.
    loop {}
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    let mut console = Console;
    let _ = writeln!(console, "\r\n*** PAYLOAD PANIC: {}", info);
    loop {}
}
