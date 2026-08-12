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
//! Only the three primitives at the bottom -- `seek`, `write_pair`, `read_pair` -- know
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

/// `pub` for `src/bench.rs`'s cross-port check, which must write through THIS
/// port and read through the memory window.
pub fn write_u32_pub(word_addr: u32, value: u32) {
    write_u32(word_addr, value);
}

fn write_u32(word_addr: u32, value: u32) {
    seek(word_addr);
    write_pair(value);
}

/// `pub` for `src/memory.rs`, which reads one pair for the shell's `hyperram read`.
///
/// The port is 32 bits wide, so this is a single transfer with no lo/hi assembly
/// convention for a second copy to disagree with. `word_addr` counts the part's
/// 16-bit words and must be EVEN.
pub fn read_u32(word_addr: u32) -> u32 {
    seek(word_addr);
    read_pair()
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
    /// Every word read back as all ones, which is what the CSR port returns when the
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

/// Spare header words. `IMAGE_WORD` is 16 and the header itself uses 0, 2 and
/// 4, so everything written here is untouched by a staged image and untouched
/// by the bootloader.
#[cfg(feature = "image")]
const CANARY_WORD: u32 = 6;
#[cfg(feature = "image")]
const STAMP_WORD: u32 = 8;
#[cfg(feature = "image")]
const SCRATCH_WORD: u32 = 10;

/// The power-loss canary: present means the part kept power since some image
/// wrote it, absent means it did not.
///
/// Deliberately not `0x0000_0000` (uninitialised), not `0xffff_ffff` (what the
/// staging port returns when the controller never answers) and not [`MAGIC`],
/// which would make a staged image look like a canary.
#[cfg(feature = "image")]
const CANARY: u32 = 0x4852_5057; // "HRPW"

/// Two patterns, not one: a single value could match a port returning the last
/// thing it saw, and these are complements, so a stuck lane fails one of them.
#[cfg(feature = "image")]
const ROUND_TRIP: [u32; 2] = [0x5aa5_0f0f, 0xa55a_f0f0];

/// `hyperram clear` -- **DESTRUCTIVE**, and never called by [`init`].
///
/// It invalidates any staged image: the magic goes, so the bootloader falls
/// through to whatever the bitstream placed, and the canary goes with it, so
/// the next [`init`] reports the array as unestablished rather than trusting
/// contents this deliberately abandoned.
///
/// **What it cannot do here, and says rather than claiming otherwise**: assert
/// `RESET#`. That pin is tied de-asserted in the shipping SoC
/// (`gateware/soc/bootram.py:706,723`), so the full hardware reset -- `tRP`
/// 200 ns low, `tRPH` 400 ns before CS# -- has no path from firmware at all. It
/// would destroy the array outright (§11.3.6), which is exactly why it belongs
/// behind an operator's command and not behind an init.
#[cfg(feature = "image")]
pub fn clear() {
    invalidate();
    write_u32(CANARY_WORD, 0);
    write_u32(STAMP_WORD, 0);
}

// The power-state half: the CS# wake ladder (#315).
//
// `image` only, and the feature exists for one reason: this file is compiled
// into `firmware/cynthion-boot` as well, and that crate has no clock to derive
// `tEXTDPD` from and 492 bytes to live in. See `Cargo.toml`.
#[cfg(feature = "image")]
use crate::clock;

/// CS# pulses the wake ladder will spend before giving up.
///
/// **Waits for**: the part to leave Deep Power Down or Hybrid Sleep.
/// **Expected duration, and where it comes from**: W956A8MBYA A01-006 -- one
/// CS# low-then-high is the documented DPD exit (`tCSDPD` 200 ns..3 us) and CS#
/// low alone clears Hybrid Sleep, after which `tEXTDPD` <= 150 us must pass
/// before the next transaction. One pulse should do it.
/// **Multiplier**: four, so one pulse landing across a refresh is not the whole
/// answer; the whole ladder failing costs 4 x 150 us = 600 us.
/// **On expiry**: `alive` is false, the report says so and names the pulse
/// count, and nothing downstream treats the part as established.
#[cfg(feature = "image")]
const WAKE_PULSES: u32 = 4;

/// `tEXTDPD`: the settling time after CS# rises out of Deep Power Down.
#[cfg(feature = "image")]
const EXIT_US: u32 = 150;

/// What [`init`] found.
#[cfg(feature = "image")]
pub struct Init {
    /// CS# pulses spent before the part answered, or [`WAKE_PULSES`].
    pub pulses: u32,
    /// The round trip worked, so the path and the part are both there.
    pub alive: bool,
    /// The canary was present: this part has not been power-cycled since an
    /// image wrote it, so its contents are what that image left.
    pub kept: bool,
    /// The build stamp found beside the canary, whatever it was.
    pub stamp: u32,
}

/// `hyperram_init()` -- wake it, prove the path, and record whether it kept
/// power. **It never asserts `RESET#`.**
///
/// §11.3.6: *"The host system should assume DRAM array data is lost after
/// hardware reset"*, and the staging path deliberately depends on contents
/// surviving a CPU reset -- which is the whole reason HyperRAM is in it. So the
/// ladder is the non-destructive one: a CS# low-then-high edge, which is the
/// documented Deep Power Down exit, which Hybrid Sleep clears itself on, and
/// which preserves contents. One staging transaction is one CS# pulse.
///
/// A DQS build wrote `CR0 = 0x0000` on every register write, and `CR0[15] = 0`
/// is Deep Power Down. Every subsequent reconfigure left the part asleep,
/// because nothing power-cycles it and no path asserts `RESET#` (#226).
///
/// The canary answers a question nothing could ask before: **was this part
/// power-cycled?** Present with this build's `stamp` -- it kept power and its
/// contents are ours. Present with another -- it kept power across a different
/// image's run. Absent -- it lost power, the defaults genuinely are in force,
/// and any staged image must be re-staged rather than believed. Today the
/// bootloader assumes a staged image is valid with no evidence the RAM kept it.
///
/// `stamp` is passed IN: a staging driver has no business reading build
/// metadata, and what it needs is a word that changes when the image does.
///
/// Non-destructive: everything written is spare header space above the CRC and
/// below `IMAGE_WORD`, so a staged image is untouched. Idempotent: the canary
/// is read before it is written and rewriting it changes nothing.
#[cfg(feature = "image")]
pub fn init(stamp: u32) -> Init {
    let mut pulses = 0;
    let mut alive = false;
    while pulses < WAKE_PULSES {
        pulses += 1;
        // The transaction IS the pulse, and its result is not used: a part in
        // Deep Power Down answers nothing, which is the state being left.
        let _ = read_u32(SCRATCH_WORD);
        clock::wait(clock::micros(EXIT_US));
        alive = ROUND_TRIP.iter().all(|&pattern| {
            write_u32(SCRATCH_WORD, pattern);
            read_u32(SCRATCH_WORD) == pattern
        });
        if alive {
            break;
        }
    }
    if !alive {
        return Init { pulses, alive, kept: false, stamp: 0 };
    }

    let found = read_u32(CANARY_WORD);
    let was = read_u32(STAMP_WORD);
    // Written AFTER they are read, and unconditionally: this is what the next
    // boot reads, and rewriting the same value is what makes the step
    // idempotent.
    write_u32(CANARY_WORD, CANARY);
    write_u32(STAMP_WORD, stamp);
    Init { pulses, alive, kept: found == CANARY, stamp: was }
}

use backend::seek;
pub use backend::{claim, clear_refused, owner_is_bist, read_pair, refused,
                  write_pair};

/// The HyperRAM CSR port on the FPGA, per `HyperRAMBoot` in
/// `gateware/soc/bootram.py`.
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
    // BYTE pointers, and a 32-bit register is read and written a byte at a time.
    //
    // amaranth-soc gives a multi-byte register a shadow that is latched when one
    // designated byte is accessed; the other bytes come from that shadow. A single
    // 32-bit `read_volatile` does not drive that handshake the way the bridge
    // expects, and the result is a value from the PREVIOUS access with its bytes
    // shifted one position -- measured, non-deterministically, on the board.
    //
    // Writes are not affected the same way (`ADDR` has always been written as a
    // u32 and staging works), but they go byte-wise here too so the two directions
    // cannot drift apart.
    const DATA: *const u8 = (BASE + offset::DATA) as *const u8;
    const WDATA: *mut u8 = (BASE + offset::WDATA) as *mut u8;

    /// How long to wait for a transfer before giving up, in spin turns.
    ///
    /// The bound matters more than the value: this code runs BEFORE the console
    /// banner, so an unbounded spin gives a board silent from power-on with no
    /// way to ask it why -- which is exactly what happened once.
    ///
    /// **Derived, not chosen.** `gateware/soc/bootram.py` measures 156 cycles per
    /// 16-bit word, so a 32-bit pair is 312. One turn of this loop is one uncached
    /// MMIO read, ~11.9 cycles (`workload.rs`), so a healthy transfer is about
    /// **26 turns**.
    ///
    /// 2,600 is **100x** that. Deliberately not the 1.25x the rules ask for: this
    /// runs before anything can report a false trip, and a premature timeout here
    /// drops a word into a staged image, which surfaces later as a CRC failure
    /// pointing at the wrong thing. 100x still gives up in ~31 us, fast enough
    /// to notice a dead peripheral, with room for a first access after reset.
    const TIMEOUT: u32 = 2_600;

    // NO STATICS IN THIS FILE. `cynthion-boot` includes it with
    // `#[path = "../../cynthion-soc/src/hyperram.rs"]` and has no .bss zeroing;
    // its linker asserts `cynthion-boot has .bss; nothing zeroes it here`. A
    // counter added here failed the bootloader build immediately, which is the
    // guard doing its job. So these primitives REPORT a timeout and the SoC
    // counts -- see `hyperram::timeouts`.

    /// The mode register, `hyperram_ck` (`gateware/soc/peripherals/hyperram_ck.py`).
    ///
    ///     ctrl   +0x00  bit 0 sel, bit 1 bist, bit 2 refused_clear
    ///     status +0x04  bit 0 locked, bit 1 mode, bit 2 refused
    const MODE_CTRL: *mut u32 = cynthion_soc_pac::base::HYPERRAM_CK as *mut u32;
    const MODE_STATUS: *const u32 =
        (cynthion_soc_pac::base::HYPERRAM_CK + 4) as *const u32;

    /// Spins waiting for the mux to follow a mode write.
    ///
    /// **Waits for** an idle controller: the mux hands the part over between
    /// transactions, and tCSM bounds one at 4 us -- 240 CPU cycles at 60 MHz,
    /// or ~20 turns of this loop at the ~11.9 cycles an uncached MMIO read
    /// costs. 64 is 3x that, covering the `sync`/`hr` round trip and a slower
    /// CPU rung. On expiry `claim` returns false and the caller reports; the
    /// part is then still owned by the other half.
    const MODE_SPINS: u32 = 64;

    /// Hand the part to the BIST engine, or take it back for staging.
    ///
    /// Returns whether the mux followed. A mode write during a pass is not
    /// refused by anything here -- see the peripheral's own docs.
    pub fn claim(bist: bool) -> bool {
        // SAFETY: uncached peripheral registers.
        unsafe {
            let ctrl = read_volatile(MODE_CTRL as *const u32);
            write_volatile(MODE_CTRL, (ctrl & !2) | ((bist as u32) << 1));
            for _ in 0..MODE_SPINS {
                if (read_volatile(MODE_STATUS) >> 1) & 1 == bist as u32 {
                    return true;
                }
            }
        }
        false
    }

    /// Which half the mux is on, as the gateware reports it.
    pub fn owner_is_bist() -> bool {
        // SAFETY: as above.
        unsafe { (read_volatile(MODE_STATUS) >> 1) & 1 != 0 }
    }

    /// Sticky: a staging transaction was asked for while the engine had the
    /// part. Answered with all ones rather than left outstanding, so this bit
    /// is the only thing that says the data was never the part's.
    pub fn refused() -> bool {
        // SAFETY: as above.
        unsafe { (read_volatile(MODE_STATUS) >> 2) & 1 != 0 }
    }

    pub fn clear_refused() {
        // SAFETY: as above. Bit 2 of `ctrl` is write-one-to-clear and holds no
        // state, so the read-modify-write cannot lose the mode.
        unsafe {
            let ctrl = read_volatile(MODE_CTRL as *const u32);
            write_volatile(MODE_CTRL, ctrl | 4);
        }
    }

    /// Set the word address for the next transfer.
    pub fn seek(word: u32) {
        // SAFETY: fixed peripheral address in a `main=0` (uncached) region.
        unsafe { write_volatile(ADDR, word) };
    }

    /// Store one 32-bit pair and advance by two words. The address auto-increments in
    /// gateware, so a sequential write is one store per pair with no address bookkeeping.
    pub fn write_pair(value: u32) -> bool {
        // SAFETY: uncached peripheral registers. `busy` clears when the transfer
        // completes; spinning is correct because a HyperRAM word takes well under a
        // microsecond and there is nothing else for this CPU to do.
        unsafe {
            write_volatile(WDATA, value as u8);
            write_volatile(WDATA.add(1), (value >> 8) as u8);
            write_volatile(WDATA.add(2), (value >> 16) as u8);
            // The transfer starts on the LAST byte, so this one goes last.
            write_volatile(WDATA.add(3), (value >> 24) as u8);
            let mut spins = 0u32;
            while read_volatile(STATUS) & 1 == 0 {
                spins += 1;
                if spins > TIMEOUT {
                    // The word is dropped, and the caller is told. Otherwise a
                    // staged image simply has a hole and the failure appears
                    // later as a bad CRC, blaming the transfer that read it.
                    return false;
                }
            }
        }
        true
    }

    /// Fetch one 32-bit pair and advance by two words.
    pub fn read_pair() -> u32 {
        // SAFETY: as above. The valid flag is cleared by the gateware when the fetch
        // starts, so this cannot observe the previous word and return early.
        unsafe {
            write_volatile(CTRL, 1);
            let mut spins = 0u32;
            while read_volatile(STATUS) & 1 == 0 {
                spins += 1;
                if spins > TIMEOUT {
                    // All-ones reads as "no image" to `staged()`, so a dead peripheral
                    // makes the board fall through to the shell rather than hang.
                    //
                    // NOT counted here -- no statics in this file, see above.
                    // The value is indistinguishable from erased memory, so
                    // "nothing was staged" and "the peripheral never answered"
                    // still look alike to `staged()`. That ambiguity is the
                    // remaining half of this defect and wants a caller that can
                    // tell them apart; `hr test` is the place to add it.
                    return 0xffff_ffff;
                }
            }
            // Lowest byte first -- the order the shadow is built in.
            (read_volatile(DATA) as u32)
                | ((read_volatile(DATA.add(1)) as u32) << 8)
                | ((read_volatile(DATA.add(2)) as u32) << 16)
                | ((read_volatile(DATA.add(3)) as u32) << 24)
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

    /// No mux without gateware: the array is always the staging buffer.
    pub fn claim(bist: bool) -> bool {
        !bist
    }

    pub fn owner_is_bist() -> bool {
        false
    }

    pub fn refused() -> bool {
        false
    }

    pub fn clear_refused() {}

    /// The array stays 16-bit, because that is the part's word and the header
    /// offsets are in those units. A pair is two entries, low word first --
    /// matching the wire, where HyperBus sends the lower address first.
    pub fn write_pair(value: u32) -> bool {
        // Out-of-range writes are dropped rather than wrapping. Wrapping would corrupt
        // the header from an over-long image and make a bounds bug look like a CRC
        // failure; on the board the gateware simply addresses past the image area.
        let index = CURSOR.load(Ordering::Relaxed);
        if index < WORDS {
            STORE[index].store(value as u16, Ordering::Relaxed);
        }
        if index + 1 < WORDS {
            STORE[index + 1].store((value >> 16) as u16, Ordering::Relaxed);
        }
        CURSOR.store(index + 2, Ordering::Relaxed);
        // Never times out: there is no peripheral to wait for.
        true
    }

    pub fn read_pair() -> u32 {
        // All ones past the end matches what the SoC backend returns when the peripheral
        // never answers, which `staged()` already treats as "no image".
        let index = CURSOR.load(Ordering::Relaxed);
        CURSOR.store(index + 2, Ordering::Relaxed);
        if index + 1 < WORDS {
            (STORE[index].load(Ordering::Relaxed) as u32)
                | ((STORE[index + 1].load(Ordering::Relaxed) as u32) << 16)
        } else {
            0xffff_ffff
        }
    }
}
