//! The resident bootloader: the only thing at 0x0, and the only thing that never moves.
//!
//! - reads the staging header out of HyperRAM -- magic, length, CRC32
//! - checksums the staged image on the way OUT, which is the check that covers the
//!   HyperRAM round trip rather than only the transfer that filled it
//! - copies it into the image region, then `fence` + `fence.i`
//! - jumps to the image region, always
//!
//! ## One failure path
//!
//! Every way this can go wrong ends in the same instruction: jump to the image region
//! and run whatever is there.
//!
//! | what happened                        | what this does |
//! |--------------------------------------|----------------|
//! | no magic in HyperRAM                 | jump           |
//! | magic present, CRC does not match    | jump           |
//! | length of 0, or past the image region| jump           |
//! | HyperRAM never answers               | jump           |
//! | a good image, copied                 | jump           |
//!
//! - No branch on the reason, no error code carried forward, no retry, no flag left
//!   behind for the image to interpret.
//! - A second failure path would be a bug in this file, not a feature of it.
//!
//! ## What "whatever is there" actually is
//!
//! - The image region is block RAM, which survives a CPU reset -- so the fallback is
//!   the region **as it stands**, not "the bitstream's image" in general:
//!
//! | since the last FPGA configure  | what a fallback runs      |
//! |---------------------------------|----------------------------|
//! | nothing was staged and copied  | the bitstream's own image |
//! | an image was staged and copied | that image                |
//!
//! - Configuring the FPGA restores the bitstream's pair: block RAM init is written by
//!   configuration and nothing else. Something always answers at the image origin from
//!   power-on onwards, but it is not always the same thing.
//! - Matters for `scripts/soc_jtag_stage.py --clear`: it removes the header, but a
//!   board that already copied an image keeps running it -- clearing the header does
//!   not restore the old bytes. Reconfiguring does, in about a second; `--clear` says
//!   so.
//! - The alternative -- a bootloader that keeps a pristine copy to restore from -- is a
//!   second image region and a policy about when to use it. That is the "clever" this
//!   file exists not to be.
//!
//! ## Two breadcrumbs, and no console
//!
//! - No UART driver here: `core::fmt` costs kilobytes, this image is under half of one,
//!   and a formatted message would describe a distinction the control flow deliberately
//!   does not make. Two stores instead:
//!
//! | breadcrumb    | where                     | read by                        |
//! |----------------|----------------------------|---------------------------------|
//! | status word   | `_boot_status`, block RAM | the image (`info`), or gdb     |
//! | sideband byte | `BOARD_SIDEBAND` CSR      | Apollo, with no CPU of its own |
//!
//! - The sideband is what works when nothing else does: pin T6 reaches the Apollo
//!   microcontroller without USB, console or JTAG, reporting whatever was last written.
//! - Neither is a branch: `boot` computes a `Status`, `enter_image` stores it on the
//!   way past; the jump is unconditional and identical whatever the value is.
//!
//! ## It does not manage the header
//!
//! - A rejected image is left exactly as found. Clearing it is a policy decision
//!   (retry, or give up) and policy belongs in the image: `scripts/soc_jtag_stage.py
//!   --clear` is where it lives.
//!
//! ## No formatting, and nothing that wants an allocator
//!
//! - No `write!`, `unwrap`, `expect`, `assert!` or index anywhere in this file; `nm` on
//!   the built image finds six symbols and no `core::fmt`.
//! - Largest size decision here: formatting machinery costs kilobytes, this image is
//!   492 bytes.
//! - `panic_immediate_abort` would be the next lever and is moot -- no panic machinery
//!   left to remove, and this project builds on stable.
//!
//! ## Measured size
//!
//! | symbol / section | bytes |
//! |------------------|-------|
//! | `pass`           | 188   |
//! | `boot`           | 132   |
//! | `hyperram::read_u32` | 48 |
//! | `hyperram::backend::read_pair` | 48 |
//! | `enter_image`    | 64    |
//! | `_start`         | 12    |
//! | `.rodata`, `.data`, `.bss` | 0, and `memory.x` asserts the last two |
//! | **image total**  | **492** |
//!
//! 1 KiB is allocated. Re-measure with `./scripts/soc_boot_size.py` (also covers the
//! profile ablations behind these numbers); `Cargo.toml` holds what each setting is
//! worth.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::ptr::write_volatile;

/// The staging layout, compiled from the shell's copy rather than transcribed.
///
/// The header ordering, the magic, the bounds check and the CRC polynomial all have to
/// be the same on both sides or a staged image is unreadable by the thing that staged
/// it. Sharing the file is what makes that true by construction. Most of what it
/// exports writes; this image only reads.
#[path = "../../cynthion-soc/src/hyperram.rs"]
#[allow(dead_code)]
mod hyperram;

unsafe extern "C" {
    /// Base of the image region, from `memory.x`. Taken from the linker so the split
    /// is stated in one place.
    static _image_start: u8;
    /// One word at the top of BOOT, below the stack. See `memory.x`.
    static _boot_status: u8;
}

/// Which path this boot took. A record, never a condition.
///
/// Stored in the status word and, coarsened, in the sideband byte. Nothing reads these
/// back here and nothing branches on them: every value ends in the same jump.
#[derive(Clone, Copy)]
#[repr(u32)]
enum Status {
    /// A staged image checked out and was copied. It is what is running.
    Ran = 0,
    /// No magic in HyperRAM: nothing staged, or `--clear` removed it. The ordinary
    /// power-on case.
    NoMagic = 1,
    /// A staged image was found and its CRC did not match what HyperRAM gave back.
    Crc = 2,
    /// Magic present, length zero or past the image region: a corrupt header.
    Length = 3,
    /// HyperRAM never answered. Every word read back as all ones.
    Silent = 4,
    /// This image panicked, which nothing in it can currently do.
    Panicked = 5,
    /// A staged image was found and verified, and was NOT installed, because the
    /// image this bootloader hands to lives in flash.
    ///
    /// Staging writes to `_image_start`, and `_image_start` is now a memory-mapped
    /// SPI window. A store there does not install anything -- flash is not written
    /// by storing to its read window -- so the copy would have been a silent no-op
    /// at best, and the CPU would then have jumped to whatever flash already held.
    /// That is precisely the "runs firmware that is not the firmware you loaded"
    /// failure this project has paid for three times, so it is reported rather
    /// than attempted.
    ///
    /// Not an error. It means: your staged image was fine, and it is not what is
    /// running. Program flash instead -- `./dev.py run` does, in seconds.
    FlashResident = 6,
}

/// Whether the image region is the memory-mapped flash window rather than block RAM.
///
/// Derived from the linker's `_image_start`, so it follows `memory.x` rather than
/// restating it. Const-evaluable in principle but written as a function because
/// `_image_start` is an extern symbol and its address is not a constant.
fn image_is_flash() -> bool {
    let start = (&raw const _image_start) as usize;
    (0x1000_0000..0x1040_0000).contains(&start)
}

/// High 24 bits of the status word: "BOT", so an uninitialised or stale block RAM word
/// cannot be mistaken for a report. `info` in the shell checks it before believing the
/// low byte.
const STATUS_MARK: u32 = 0x424f_5400;

/// The FPGA_ADV sideband register, per `gateware/soc/peripherals/sideband_csr.py`.
///
/// From the generated memory map rather than transcribed, which is what
/// `scripts/check.py`'s `socmap` check enforces across this whole tree.
const SIDEBAND: *mut u8 = cynthion_soc_pac::base::BOARD_SIDEBAND as *mut u8;

/// Take the link from the fabric's hardwired bits. Resets clear, so a design that never
/// reaches this still answers with the fabric's own account of itself.
const SIDEBAND_OWN: u8 = 0b1000_0000;
/// Bit 3, `error`: set when the bootloader fell back for a reason that is a fault.
const SIDEBAND_ERROR: u8 = 0b0000_1000;

/// Reset vector. `_start` is at 0x0 because `memory.x` places `.start` there and the
/// CPU's `reset_addr` is `RAM_BASE`.
///
/// `naked` so the compiler emits nothing ahead of this: a prologue would run before
/// `sp` was set, storing to whatever address the reset value of `sp` happened to name.
#[unsafe(no_mangle)]
#[unsafe(link_section = ".start")]
#[unsafe(naked)]
unsafe extern "C" fn _start() -> ! {
    core::arch::naked_asm!(
        "la sp, _boot_stack_top",
        "j {boot}",
        boot = sym boot,
    )
}

/// Read `length` bytes back out of the staging area, checksumming every one and, when
/// `dest` is given, storing it.
///
/// One function for both passes on purpose. The verify pass and the copy pass have to
/// read the same bytes the same way, and two loops that were meant to agree are two
/// loops that can stop agreeing.
fn pass(length: u32, dest: Option<*mut u8>) -> u32 {
    let mut crc = hyperram::Crc32::new();
    hyperram::seek_image();

    let mut done: u32 = 0;
    while done < length {
        // The staging port moves a 32-bit pair and the address auto-increments in
        // gateware, so a sequential read is one fetch per four bytes with no address
        // bookkeeping.
        let word = hyperram::read_pair();
        for shift in [0u32, 8, 16, 24] {
            if done < length {
                let byte = (word >> shift) as u8;
                crc.push(byte);
                if let Some(dest) = dest {
                    // SAFETY: `done` is bounded by `length`, which `staged()` has
                    // already limited to the image region's size.
                    unsafe { write_volatile(dest.add(done as usize), byte) };
                }
                done += 1;
            }
        }
    }

    crc.finish()
}

/// Verify, copy, jump.
///
/// Two passes over HyperRAM, not one, and the second pass is what makes the fallback
/// real: a single pass that checksummed while copying would have already overwritten
/// the image region by the time it discovered the CRC was wrong, leaving a half-written
/// image as the only thing to fall back to.
///
/// The extra read is one more pass over the staged bytes, and only on a boot that has
/// an image staged. `docs/architecture.md` section 15 measures the CPU-side HyperRAM port
/// at roughly 8 ms for 32 KiB at 60 MHz, so the largest image this will accept costs
/// about 16 ms more than a single pass would.
fn boot() -> ! {
    let status = match hyperram::staged() {
        Err(hyperram::Reject::NoMagic) => Status::NoMagic,
        Err(hyperram::Reject::Length) => Status::Length,
        Err(hyperram::Reject::Silent) => Status::Silent,
        Ok((length, expected)) => {
            if pass(length, None) != expected {
                Status::Crc
            } else if image_is_flash() {
                // Verified, and deliberately not installed. See `FlashResident`:
                // the destination is a read-only SPI window, so the copy cannot
                // do what its name says and jumping afterwards would run whatever
                // flash already held while reporting a successful install.
                Status::FlashResident
            } else {
                pass(length, Some((&raw const _image_start) as *mut u8));
                Status::Ran
            }
        }
    };

    // Everything above this point is optional. This is not.
    enter_image(status)
}

/// Record what happened, then hand over to the image region. The only jump in this
/// program, and the only way out of it.
///
/// The two stores are the whole of the diagnostics. They happen on every path with the
/// same instructions in the same order, so there is nothing here that can be reached on
/// one outcome and not another.
///
/// `fence` then `fence.i`: the image arrived as DATA through the write-back D-cache, so
/// without the flush the I-side may fetch lines left over from whatever was there.
/// Executed on the fallback path too, where they cost two instructions and remove the
/// question of whether the path that skipped them was the one that needed them. They
/// also order the status word, which is a plain block RAM store the image reads back.
fn enter_image(status: Status) -> ! {
    let code = status as u32;

    // SAFETY: two fixed addresses -- a reserved word at the top of this image's own
    // region, and a read/write CSR in the uncached peripheral window.
    unsafe {
        write_volatile((&raw const _boot_status) as *mut u32, STATUS_MARK | code);

        // The sideband carries the coarse version, because the register has two bits of
        // `state` and one of `error` and those mean something to Apollo already. `state`
        // is the code truncated to what fits; `error` says whether falling back was a
        // fault (a bad CRC, a corrupt header, a silent part) or the ordinary case of
        // nothing being staged.
        let fault = matches!(
            status,
            Status::Crc | Status::Length | Status::Silent | Status::Panicked
        );
        write_volatile(
            SIDEBAND,
            SIDEBAND_OWN | (code as u8 & 0b11) | if fault { SIDEBAND_ERROR } else { 0 },
        );
    }

    unsafe {
        core::arch::asm!(
            "fence",
            "fence.i",
            "jr {entry}",
            entry = in(reg) (&raw const _image_start) as usize,
            options(noreturn),
        )
    }
}

/// Unreachable, and required anyway.
///
/// Nothing here indexes, divides, unwraps or formats, so no panic site exists to reach
/// this -- `nm` on the built image finds six symbols and none of them is a panic. It
/// is written as a fallback rather than a `loop {}` so that the promise the rest of the
/// file makes holds without exception: every way this can go wrong ends in the same
/// instruction. A spin would be a second failure path, reachable only by a bug, and the
/// board would look dead rather than come up on the image the bitstream placed.
#[panic_handler]
fn panic(_: &PanicInfo) -> ! {
    enter_image(Status::Panicked)
}
