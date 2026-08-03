//! HyperRAM staging buffer: where a new firmware image lives across a reboot.
//!
//! Block RAM cannot stage its own replacement -- the image is executing from it, so
//! anything it wrote there would be overwriting live code. HyperRAM is external and
//! survives a CPU reset, which is the whole reason it sits in this path.
//!
//! The flow:
//!
//!   1. an image arrives here, over USB bulk (`load`) or over JTAG with the CPU held in
//!      reset (`scripts/soc_jtag_stage.py`), and a CRC32 is computed as it goes in
//!   2. a header follows it -- length, CRC, then the magic last
//!   3. the board reboots into `firmware/cynthion-boot`
//!   4. the bootloader finds the magic, re-reads the image, checks the CRC, copies it
//!      into the image region and jumps there
//!   5. anything wrong at any step: jump to the image region anyway, and run whatever
//!      the bitstream put there
//!
//! The CRC is checked on the way OUT, not just computed on the way in. Verifying what
//! was actually stored is the only check that covers the HyperRAM round trip itself; a
//! checksum computed only on arrival would confirm USB delivered the bytes and say
//! nothing about whether they survived.

//! ## This file is compiled into two crates
//!
//! `cynthion-soc` -- the image, which writes -- and `cynthion-boot`, the bootloader,
//! which reads. The bootloader includes it with `#[path]` rather than copying it,
//! because the header ordering, the magic, the length bound and the CRC polynomial have
//! to be the same on both sides or a staged image is unreadable by the thing that
//! staged it. Sharing the file makes that true by construction rather than by review.
//!
//! Only the three primitives at the bottom -- `seek`, `write_word`, `read_word` -- know
//! where the words actually go. Everything above them (the header layout, `staged()`,
//! `Crc32`, the magic, the bounds checks) is shared by both targets, so the QEMU test
//! exercises the real staging logic against a RAM stand-in rather than a
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

/// Largest image we will accept: the length of the image region it must fit into.
///
/// 63 KiB -- all of block RAM but the kilobyte the bootloader keeps. A const rather
/// than a linker symbol because the QEMU backend below sizes an array from it, and
/// because `staged()` bounds against it in a crate whose linker script does not name
/// the region. `scripts/soc_generate_pac.py --check` is what holds it to the five other
/// places that state the same split.
pub const MAX_IMAGE: u32 = 63 * 1024;

/// Point the address register at the first word of the image area.
pub fn seek_image() {
    seek(IMAGE_WORD);
}

/// Point it at an arbitrary word.
///
/// Staging only ever needs `seek_image` and the header offsets, so this exists
/// for `src/bench.rs`, which walks an area well above the image and must be able
/// to jump about inside it. Nothing else should be addressing this part directly.
pub fn seek_word(word: u32) {
    seek(word);
}

fn write_u32(word_addr: u32, value: u32) {
    seek(word_addr);
    write_word(value as u16);
    write_word((value >> 16) as u16);
}

/// `pub` for `src/memory.rs`, which reads one word for the shell's `hyperram read`
/// and must assemble the halves exactly as the header above is assembled -- a
/// second copy of the lo/hi convention could disagree with the bootloader about
/// which half is which and nothing would catch it.
pub fn read_u32(word_addr: u32) -> u32 {
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

/// Why there is nothing to boot.
///
/// A reason rather than a bare `None`, and it changes no behaviour: the bootloader
/// treats all three identically and jumps to the image region either way. What it buys
/// is that the boot status word and the sideband byte can say WHICH, so a board that
/// came up on the bitstream's image can be asked why without a console.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Reject {
    /// No magic. Either nothing was ever staged, or `--clear` removed it.
    NoMagic,
    /// Magic present, length zero or past the image region: a corrupt header.
    Length,
    /// Every word read back as 0xffff, which is what the CSR port returns when the
    /// HyperRAM controller never answers. Distinguishable from `NoMagic` only because
    /// erased memory reads as all ones and `MAGIC` deliberately is not.
    Silent,
}

/// A staged image's length and CRC, or why there is not one.
pub fn staged() -> Result<(u32, u32), Reject> {
    let magic = read_u32(HDR_MAGIC);
    if magic != MAGIC {
        return Err(if magic == 0xffff_ffff {
            Reject::Silent
        } else {
            Reject::NoMagic
        });
    }
    let length = read_u32(HDR_LENGTH);
    // A length past the region is a corrupt header, not a big image. Copying it would
    // run off the end of block RAM, and the bootloader would be told to write past
    // where anything answers.
    if length == 0 || length > MAX_IMAGE {
        return Err(Reject::Length);
    }
    Ok((length, read_u32(HDR_CRC)))
}

use backend::seek;
pub use backend::{read_word, write_word};

/// The HyperRAM CSR port on the FPGA, per `HyperRAMBoot` in
/// `ecp5-test/riscv/vexii_bootram.py`.
#[cfg(not(feature = "qemu"))]
mod backend {
    use core::ptr::{read_volatile, write_volatile};
    use cynthion_soc_pac::bootram::offset;

    /// Read out of the SoC's memory map rather than transcribed from
    /// `BOOTRAM_BASE` -- see the module comment in `src/target.rs`. This module
    /// is already `not(feature = "qemu")`, so naming a hardware-only address here
    /// costs the QEMU build nothing.
    ///
    /// The accesses below stay hand-rolled: they are byte accesses to a multi-byte
    /// CSR whose low byte latches a shadow and whose high byte commits, which is
    /// not what a generated accessor would emit. Their addresses are generated.
    const BASE: usize = cynthion_soc_pac::base::BOOTRAM;

    const ADDR: *mut u32 = (BASE + offset::ADDR) as *mut u32;
    const CTRL: *mut u8 = (BASE + offset::CTRL) as *mut u8;
    const STATUS: *const u8 = (BASE + offset::STATUS) as *const u8;
    const DATA_LO: *const u8 = (BASE + offset::DATA_LO) as *const u8;
    const DATA_HI: *const u8 = (BASE + offset::DATA_HI) as *const u8;
    const WDATA: *mut u16 = (BASE + offset::WDATA) as *mut u16;

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
/// this buys is that `load`, `hrtest` and everything the bootloader reads -- header
/// ordering, the magic, the CRC computed on read-back, the length bounds -- run under
/// QEMU against the same code the board runs.
///
/// What it deliberately does NOT model is the one property that makes HyperRAM the right
/// part: surviving a CPU reset. This array is in `.bss`, so `reset` and the reboot at the
/// end of `load` zero it. There is also no bootloader image on this machine and nothing
/// at 0 to fall back into. A staged image therefore never boots under QEMU; testing the
/// round trip, the fallback and a failed CRC needs the board.
#[cfg(feature = "qemu")]
mod backend {
    use core::sync::atomic::{AtomicU16, AtomicUsize, Ordering};

    /// Header words plus the largest image `MAX_IMAGE` allows, rounded up. Sized from the
    /// same constants the shared code bounds against, so the two cannot drift.
    const WORDS: usize = super::IMAGE_WORD as usize + (super::MAX_IMAGE as usize + 1) / 2;

    // `AtomicU16`/`AtomicUsize` rather than `static mut`, and the reason changed
    // under this code. When it was written this binary had no interrupt
    // handlers, so a raw mutable static had no second accessor to race with. It
    // has one now (`src/irq.rs`), and while nothing in that handler touches
    // HyperRAM today, "nothing does yet" is not a property the type system can
    // hold on to. Atomics need no `unsafe`, no `critical-section` dependency and
    // no reasoning about who might arrive later; on this target they compile to
    // the same loads and stores. `scripts/soc_irq_log_check.py` is what found
    // this, which is most of the argument for having it.
    static STORE: [AtomicU16; WORDS] = [const { AtomicU16::new(0) }; WORDS];
    static CURSOR: AtomicUsize = AtomicUsize::new(0);

    pub fn seek(word: u32) {
        CURSOR.store(word as usize, Ordering::Relaxed);
    }

    pub fn write_word(value: u16) {
        // Out-of-range writes are dropped rather than wrapping. Wrapping would corrupt
        // the header from an over-long image and make a bounds bug look like a CRC
        // failure; on the board the gateware simply addresses past the image area.
        let index = CURSOR.load(Ordering::Relaxed);
        if index < WORDS {
            STORE[index].store(value, Ordering::Relaxed);
        }
        CURSOR.store(index + 1, Ordering::Relaxed);
    }

    pub fn read_word() -> u16 {
        // 0xffff past the end matches what the SoC backend returns when the peripheral
        // never answers, which `staged()` already treats as "no image".
        let index = CURSOR.load(Ordering::Relaxed);
        CURSOR.store(index + 1, Ordering::Relaxed);
        if index < WORDS {
            STORE[index].load(Ordering::Relaxed)
        } else {
            0xffff
        }
    }
}
