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


use crate::bench;
use crate::hyperram;
use crate::target;

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
    pub(crate) fn size(self) -> usize {
        match self {
            Region::Bram => target::BRAM_SIZE,
            Region::Flash => target::FLASH_SIZE,
            Region::Hyperram => target::HYPERRAM_SIZE,
        }
    }

    /// Where the region starts. Same rule as [`size`](Self::size): from
    /// `src/target.rs`, so it is the map the gateware built rather than a
    /// literal that has to be kept in step with it.
    /// `None` for HyperRAM: it IS decoded at a window, but nothing in this
    /// firmware reaches it that way -- every access goes over the bounded CSR
    /// staging port. Reporting the window would name an address no command
    /// here uses.
    pub(crate) fn base(self) -> Option<usize> {
        match self {
            Region::Bram => Some(0),
            // From `FLASH_WINDOW` rather than `FLASH_BASE`, which exists only on
            // the target that has one -- naming the constant broke the QEMU
            // build outright (d58f224). `None` here is the truth on `virt`:
            // there is no flash window, and `target::flash_word` answers from a
            // stand-in with no address to report.
            Region::Flash => target::FLASH_WINDOW.map(|(base, _)| base),
            Region::Hyperram => None,
        }
    }

    /// Why this region has no `id`, or `None` for the one that has.
    pub(crate) fn no_id(self) -> Option<&'static str> {
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
    pub(crate) fn word(self, offset: usize) -> Option<u32> {
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

/// The first word of an ECP5 bitstream, which is what offset 0 of this part
/// holds. `flash id`, `cpu check` and `selftest` compare against it for the
/// same reason: it is a value the flash cannot return by accident.
const BITSTREAM_HEADER: u32 = 0x6150_00ff;

/// What [`w25q32_init`] read.
pub struct FlashInit {
    pub word: u32,
}

impl FlashInit {
    pub fn established(&self) -> bool {
        self.word == BITSTREAM_HEADER
    }
}

/// `w25q32_init()` -- **a read, and only a read**.
///
/// No erase, no program, no status-register write, ever. The bitstream lives at
/// offset 0 and this image at 0xb0000, and there is nothing on this part a
/// write could establish that is worth the risk of writing it.
///
/// What it establishes is therefore evidence rather than state, and the
/// evidence is about Continuous Read -- the one mode that survives a
/// reconfigure. A part left in it answers an opcode with an address phase and
/// returns plausible data from the wrong offset, so offset 0 stops reading as a
/// bitstream header. That is the whole of what is checkable from here.
///
/// **Exiting it is not implemented on the SoC path.** That needs a read with
/// the opcode omitted and mode byte `0xff`, which
/// `gateware/probes/qspi/qspi_gateware.py` can issue and the reader in
/// `gateware/soc/peripherals/flash.py` cannot (#315).
pub fn w25q32_init() -> FlashInit {
    FlashInit { word: target::flash_word(0) }
}
