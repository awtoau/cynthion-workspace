//! One word out of one memory: `flash id`, and `read` on any of the three regions.
//!
//! The shell used to have `id` and `read <hex>`, both flash-only and neither saying
//! so, while `bench` had already learned to take a region name. One command family
//! was region-aware and the other silently assumed a region, which left a reader of
//! `read 40` having to know which of three memories it meant. This module is the
//! other half of `bench`'s vocabulary: the same three words in front of the same
//! verb.
//!
//!     flash id
//!     flash read <hex>
//!     hyperram read <hex>
//!     bram read <hex>
//!
//! `Region` is parsed HERE and used by `src/bench.rs` as well, and that sharing is
//! the point rather than tidiness. Two spellings for one memory would be worse than
//! either alone, and one enum makes `bench flash` and `flash read` agree by
//! construction instead of by review.
//!
//! ## What each region can answer
//!
//!     region     a word comes from        bounded by               identify
//!     bram       a load from the window   target::BRAM_SIZE        nothing to identify
//!     flash      target::flash_word       target::FLASH_SIZE       the first word
//!     hyperram   the CSR staging port     target::HYPERRAM_SIZE    no JEDEC on HyperBus
//!
//! `flash id` is the only identify that exists, and the other two say why rather
//! than being registered as commands that print nothing. HyperBus has no JEDEC
//! sequence at all -- the part answers reads and writes and identifies itself
//! through a separate configuration space this SoC's controller does not drive --
//! and block RAM is fabric, which has no identity to read back.
//!
//! ## There is deliberately no `write`
//!
//! Reading any of the three is safe. Writing is not, and differently so for each:
//! block RAM holds the image that is executing, flash needs an erase before a word
//! can change, and HyperRAM above the header is where a staged image waits to
//! boot. A write form is a separate command with its own argument about safety,
//! and folding it in here would make the safe half wait for the dangerous one.

use core::fmt::Write;

use crate::bench;
use crate::hyperram;
use crate::target;
use crate::uart::Uart;

/// The three memories on this board, and the only place a word becomes one of them.
#[derive(Clone, Copy)]
pub enum Region {
    Bram,
    Flash,
    Hyperram,
}

impl Region {
    /// `bram`, `flash` or `hyperram`; `None` for anything else.
    ///
    /// `src/main.rs` calls this from the arm that would otherwise say `unknown
    /// command`, so the three region names are NOT listed in the dispatcher --
    /// this match is the only list of them, and `bench` asks the same question of
    /// its own argument.
    pub fn parse(word: &[u8]) -> Option<Region> {
        match word {
            b"bram" => Some(Region::Bram),
            b"flash" => Some(Region::Flash),
            b"hyperram" => Some(Region::Hyperram),
            _ => None,
        }
    }

    /// The word it was parsed from. Every line printed below starts with it, so a
    /// scrollback of several reads still says which memory each came from.
    pub fn name(self) -> &'static str {
        match self {
            Region::Bram => "bram",
            Region::Flash => "flash",
            Region::Hyperram => "hyperram",
        }
    }

    /// How many bytes the region holds, which is what an offset is checked against.
    ///
    /// Every one of these comes from `src/target.rs` and therefore, on the board,
    /// from the SoC's own memory map. A literal here would be a fourth copy of a
    /// number the gateware already decides.
    fn size(self) -> usize {
        match self {
            Region::Bram => target::BRAM_SIZE,
            Region::Flash => target::FLASH_SIZE,
            Region::Hyperram => target::HYPERRAM_SIZE,
        }
    }

    /// Why this region has no `id`, or `None` for the one that has.
    fn no_id(self) -> Option<&'static str> {
        match self {
            Region::Bram => Some("block RAM is fabric, with no identity to read"),
            Region::Flash => None,
            Region::Hyperram => Some("HyperBus carries no JEDEC id"),
        }
    }

    /// One 32-bit word at `offset`, or `None` if the region did not answer.
    ///
    /// `offset` must already be word aligned and inside `size()`; `read` below is
    /// what enforces both, once, for all three regions.
    fn word(self, offset: usize) -> Option<u32> {
        match self {
            // SAFETY: inside the block RAM window, which is ordinary memory on both
            // targets. Volatile so that reading the same address twice actually
            // reads it twice -- the interesting words here are ones something else
            // wrote, like the bootloader's status at 0x3fc.
            Region::Bram => Some(unsafe {
                core::ptr::read_volatile((target::BRAM_BASE + offset) as *const u32)
            }),
            Region::Flash => Some(target::flash_word(offset)),
            Region::Hyperram => {
                // Over the CSR staging port, not the memory window at 0x20000000,
                // and the difference matters here. Every spin in
                // `hyperram::read_pair` is bounded, so a part or a controller that
                // never answers costs milliseconds and returns 0xffff; a load from
                // the memory window would stall the bus with nothing in this
                // firmware able to give up on it. A shell that hangs is worse than
                // one that says it got nothing.
                //
                // The two halves are assembled by the same `read_u32` the staging
                // header is read with, so this cannot disagree with the bootloader
                // about which half is which. Its address is in 16-bit words,
                // because the part is 16 bits wide.
                let word = hyperram::read_u32(offset as u32 / 2);

                // 0xffff_ffff is what the guard returns on a timeout AND what a
                // never-written part reads as, so the value alone cannot tell a
                // dead port from empty memory. The probe -- one word out and back
                // through bench's scratch area, well above anything staged -- is
                // what separates them, and it runs only in the ambiguous case, so
                // a read that answered stays a read and writes nothing.
                if word == 0xffff_ffff && !bench::hyper_present() {
                    None
                } else {
                    Some(word)
                }
            }
        }
    }
}

/// `flash id`, and `bram|flash|hyperram read <hex>`.
pub fn command(uart: &mut Uart, region: Region, rest: &[u8]) {
    let rest = crate::trim(rest);
    let (verb, arg) = match rest.iter().position(|&b| b == b' ') {
        Some(i) => (&rest[..i], crate::trim(&rest[i + 1..])),
        None => (rest, &rest[..0]),
    };

    match verb {
        b"read" => read(uart, region, arg),
        b"id" => id(uart, region),
        _ => usage(uart, region),
    }
}

/// One word, from whichever region was named. One parser, one bound, one line out.
fn read(uart: &mut Uart, region: Region, arg: &[u8]) {
    let Some(offset) = crate::parse_hex(arg) else {
        return usage(uart, region);
    };

    // Aligned DOWN rather than refused, because `3fe` and `3fc` name the same
    // 32-bit word and refusing the first would be pedantry. The offset that comes
    // back is the aligned one, so the reply says which word was actually read
    // rather than echoing what was typed.
    let offset = offset as usize & !3;

    if offset >= region.size() {
        // The bound is load-bearing, not decoration, and flash is why. Above 4 MiB
        // the flash address aliases back onto offset 0, so an unchecked read past
        // the end SUCCEEDS and returns the bitstream header -- a wrong answer that
        // looks exactly like a right one. Block RAM and the HyperRAM port fail more
        // visibly, but there is no reason for three behaviours here.
        let _ = writeln!(
            uart,
            "{} @{:x} is past the end; the region holds {:x} bytes",
            region.name(),
            offset,
            region.size()
        );
        return;
    }

    match region.word(offset) {
        Some(word) => {
            let _ = writeln!(uart, "{} @{:06x} {:08x}", region.name(), offset, word);
        }
        // The same sentence `bench hyperram` prints when the port is silent, for
        // the same reason and from the same probe.
        None => {
            let _ = writeln!(uart, "{} did not answer", region.name());
        }
    }
}

/// `flash id` -- and, for the other two, why there is no such thing.
fn id(uart: &mut Uart, region: Region) {
    if let Some(reason) = region.no_id() {
        let _ = writeln!(
            uart,
            "{} has no id: {}; `flash id` is the only one",
            region.name(),
            reason
        );
        return;
    }

    // Read through the memory map, which is the path everything else here uses and
    // the one the D-cache and the mmap FSM are on. The JEDEC id proper needs the
    // SPI controller driven by hand, which is the C firmware's job; what this can
    // say is the first word -- 615000ff on a programmed part, because offset 0
    // holds the bitstream -- and how much of the part the window decodes.
    let _ = writeln!(
        uart,
        "flash @0 {:08x}, {} KiB",
        target::flash_word(0),
        target::FLASH_SIZE / 1024
    );
}

/// How to call it, named for the region that was actually typed: a person who typed
/// `hyperram` and got told about `flash` would reasonably wonder which one answered.
fn usage(uart: &mut Uart, region: Region) {
    let _ = writeln!(
        uart,
        "usage: {} read <hex offset>{}",
        region.name(),
        if region.no_id().is_none() {
            ", or `flash id`"
        } else {
            ""
        }
    );
}
