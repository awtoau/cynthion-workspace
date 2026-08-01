//! HyperRAM staging buffer: where a new firmware image lives across a reboot.
//!
//! Block RAM cannot stage its own replacement -- the shell is executing from it, so
//! anything it wrote there would be overwriting live code. HyperRAM is external and
//! survives a CPU reset, which is the whole reason it sits in this path.
//!
//! The flow:
//!
//!   1. shell receives an image over USB bulk and writes it here, computing CRC32
//!   2. shell writes a header (magic, length, CRC) and reboots
//!   3. boot code finds the magic, re-reads the image, and checks the CRC
//!   4. CRC good  -> copy into block RAM and jump
//!      CRC bad   -> drop it, invalidate the header, and fall through to the shell
//!
//! The CRC is checked on the way OUT, not just computed on the way in. Verifying what
//! was actually stored is the only check that covers the HyperRAM round trip itself; a
//! checksum computed only on arrival would confirm USB delivered the bytes and say
//! nothing about whether they survived.

//! Only the three primitives at the bottom of this file -- `seek`, `write_word`,
//! `read_word` -- know where the words actually go. Everything above them (the header
//! layout, `staged()`, `Crc32`, the magic, the bounds checks) is shared by both targets,
//! so the QEMU test exercises the real staging logic against a RAM stand-in rather than a
//! re-implementation of it. The console needs no such split any more: both targets have a
//! real 16550, so `src/uart.rs` is shared outright and only its base address differs.

/// Identifies a staged image. Arbitrary, but must not be 0x0000_0000 or 0xffff_ffff:
/// those are what uninitialised and erased memory read as, and a magic that matches
/// empty memory would make the bootloader run garbage on a cold boot.
pub const MAGIC: u32 = 0x4359_4e42; // "CYNB"

/// Header layout, in 16-bit HyperRAM words.
const HDR_MAGIC: u32 = 0;
const HDR_LENGTH: u32 = 2;
const HDR_CRC: u32 = 4;

/// First word of the image itself, leaving room for the header to grow.
pub const IMAGE_WORD: u32 = 16;

/// Largest image we will accept, bounded by the block RAM slot it must fit into.
pub const MAX_IMAGE: u32 = 32 * 1024;

/// Point the address register at the first word of the image area.
pub fn seek_image() {
    seek(IMAGE_WORD);
}

fn write_u32(word_addr: u32, value: u32) {
    seek(word_addr);
    write_word(value as u16);
    write_word((value >> 16) as u16);
}

fn read_u32(word_addr: u32) -> u32 {
    seek(word_addr);
    let lo = read_word() as u32;
    let hi = read_word() as u32;
    lo | (hi << 16)
}

/// CRC-32 (IEEE 802.3), computed a byte at a time without a lookup table.
///
/// Bitwise rather than table-driven on purpose: a 1 KiB table would cost more block RAM
/// than the whole check is worth, and this runs once per image at boot, not in a hot
/// loop.
pub struct Crc32(u32);

impl Crc32 {
    pub fn new() -> Self {
        Crc32(0xffff_ffff)
    }

    pub fn push(&mut self, byte: u8) {
        self.0 ^= byte as u32;
        for _ in 0..8 {
            let mask = (self.0 & 1).wrapping_neg();
            self.0 = (self.0 >> 1) ^ (0xedb8_8320 & mask);
        }
    }

    pub fn finish(&self) -> u32 {
        !self.0
    }
}

/// Record a staged image so the bootloader will find it after a reset.
pub fn write_header(length: u32, crc: u32) {
    // Magic LAST. The header is only meaningful once length and CRC are already stored,
    // and writing it first would leave a window where a reset mid-update boots against
    // a header describing the previous image.
    write_u32(HDR_LENGTH, length);
    write_u32(HDR_CRC, crc);
    write_u32(HDR_MAGIC, MAGIC);
}

/// Clear the magic so a rejected image is not retried on every boot.
pub fn invalidate() {
    write_u32(HDR_MAGIC, 0);
}

/// A staged image's length and CRC, if the header looks valid.
pub fn staged() -> Option<(u32, u32)> {
    if read_u32(HDR_MAGIC) != MAGIC {
        return None;
    }
    let length = read_u32(HDR_LENGTH);
    // A length past the slot is a corrupt header, not a big image. Copying it would run
    // off the end of block RAM and into the shell that is doing the copying.
    if length == 0 || length > MAX_IMAGE {
        return None;
    }
    Some((length, read_u32(HDR_CRC)))
}

use backend::seek;
pub use backend::{read_word, write_word};

/// The HyperRAM CSR port on the FPGA, per `HyperRAMBoot` in
/// `ecp5-test/riscv/vexii_bootram.py`.
#[cfg(not(feature = "qemu"))]
mod backend {
    use core::ptr::{read_volatile, write_volatile};

    /// Matches `BOOTRAM_BASE` in `ecp5-test/riscv/vexii_hello_soc.py`.
    const BASE: usize = 0xf000_0400;

    const ADDR: *mut u32 = BASE as *mut u32;
    const CTRL: *mut u8 = (BASE + 0x08) as *mut u8;
    const STATUS: *const u8 = (BASE + 0x09) as *const u8;
    const DATA_LO: *const u8 = (BASE + 0x0a) as *const u8;
    const DATA_HI: *const u8 = (BASE + 0x0b) as *const u8;
    const WDATA: *mut u16 = (BASE + 0x0c) as *mut u16;

    /// How long to wait for a transfer before giving up.
    ///
    /// A HyperRAM word takes well under a microsecond, so anything approaching this means
    /// the peripheral is not responding. The bound matters more than the value: this code
    /// runs BEFORE the console banner, so an unbounded spin gives a board that is silent
    /// from power-on with no way to ask it why -- which is exactly what happened.
    const TIMEOUT: u32 = 100_000;

    /// Set the word address for the next transfer.
    pub fn seek(word: u32) {
        // SAFETY: fixed peripheral address in a `main=0` (uncached) region.
        unsafe { write_volatile(ADDR, word) };
    }

    /// Store one 16-bit word and advance. The address auto-increments in gateware, so a
    /// sequential write is one store per word with no address bookkeeping.
    pub fn write_word(value: u16) {
        // SAFETY: uncached peripheral registers. `busy` clears when the transfer
        // completes; spinning is correct because a HyperRAM word takes well under a
        // microsecond and there is nothing else for this CPU to do.
        unsafe {
            write_volatile(WDATA, value);
            let mut spins = 0u32;
            while read_volatile(STATUS) & 1 == 0 {
                spins += 1;
                if spins > TIMEOUT {
                    return;
                }
            }
        }
    }

    /// Fetch one 16-bit word and advance.
    pub fn read_word() -> u16 {
        // SAFETY: as above. The valid flag is cleared by the gateware when the fetch
        // starts, so this cannot observe the previous word and return early.
        unsafe {
            write_volatile(CTRL, 1);
            let mut spins = 0u32;
            while read_volatile(STATUS) & 1 == 0 {
                spins += 1;
                if spins > TIMEOUT {
                    // 0xffff reads as "no image" to `staged()`, so a dead peripheral
                    // makes the board fall through to the shell rather than hang.
                    return 0xffff;
                }
            }
            (read_volatile(DATA_LO) as u16) | ((read_volatile(DATA_HI) as u16) << 8)
        }
    }
}

/// A plain RAM array standing in for the HyperRAM part, for the QEMU target.
///
/// `virt` has no HyperRAM and emulating one would be emulating the thing under test. What
/// this buys is that `load`, `hrtest` and the whole `try_boot` path -- header ordering,
/// the magic, the CRC computed on read-back, the length bounds -- run under QEMU against
/// the same code the board runs.
///
/// What it deliberately does NOT model is the one property that makes HyperRAM the right
/// part: surviving a CPU reset. This array is in `.bss`, so `reset` and the reboot at the
/// end of `load` zero it. A staged image therefore never boots under QEMU; testing the
/// staging round trip needs the board.
#[cfg(feature = "qemu")]
mod backend {
    /// Header words plus the largest image `MAX_IMAGE` allows, rounded up. Sized from the
    /// same constants the shared code bounds against, so the two cannot drift.
    const WORDS: usize =
        super::IMAGE_WORD as usize + (super::MAX_IMAGE as usize + 1) / 2;

    // Raw `static mut` rather than a `Cell`/`Mutex`: this is a single-hart no_std binary
    // with no interrupt handlers, so there is no second accessor to synchronise against,
    // and the alternative pulls `critical-section` in for nothing. Accessed only through
    // `&raw mut` so no reference to a mutable static is ever created.
    static mut STORE: [u16; WORDS] = [0; WORDS];
    static mut CURSOR: usize = 0;

    fn slot(index: usize) -> *mut u16 {
        unsafe { (&raw mut STORE).cast::<u16>().add(index) }
    }

    pub fn seek(word: u32) {
        unsafe { *(&raw mut CURSOR) = word as usize };
    }

    pub fn write_word(value: u16) {
        // Out-of-range writes are dropped rather than wrapping. Wrapping would corrupt
        // the header from an over-long image and make a bounds bug look like a CRC
        // failure; on the board the gateware simply addresses past the image area.
        unsafe {
            let index = *(&raw const CURSOR);
            if index < WORDS {
                slot(index).write_volatile(value);
            }
            *(&raw mut CURSOR) = index + 1;
        }
    }

    pub fn read_word() -> u16 {
        // 0xffff past the end matches what the SoC backend returns when the peripheral
        // never answers, which `staged()` already treats as "no image".
        unsafe {
            let index = *(&raw const CURSOR);
            *(&raw mut CURSOR) = index + 1;
            if index < WORDS {
                slot(index).read_volatile()
            } else {
                0xffff
            }
        }
    }
}
