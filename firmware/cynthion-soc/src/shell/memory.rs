//! `bram`, `flash`, `hyperram` -- read a word from a named region.
//!
//! `crate::memory` is the driver: the regions, their bounds and the reads.

use core::fmt::Write;

use crate::memory::*;
use crate::target;
use crate::shell::parse::{parse_hex, trim};
use crate::uart::Uart;

/// `flash id`, and `bram|flash|hyperram read <hex>`.
pub fn command(uart: &mut Uart, region: Region, rest: &[u8]) {
    let rest = trim(rest);
    let (verb, arg) = match rest.iter().position(|&b| b == b' ') {
        Some(i) => (&rest[..i], trim(&rest[i + 1..])),
        None => (rest, &rest[..0]),
    };

    match verb {
        b"read" => read(uart, region, arg),
        b"id" => id(uart, region),
        b"info" => info(uart, region),
        // DEVICE-MAJOR. `bench bram` was a verb-major name over a device-major
        // reality: every other thing you can do to a memory is spelled
        // `<region> <verb>`, and `hyperram bench` already existed as an alias.
        // One shape, so the help listing and TAB tell the same story.
        b"bench" => crate::bench::command(uart, region.name().as_bytes()),
        _ => usage(uart, region),
    }
}

/// `<region> info` -- where it is and how big, DERIVED.
///
/// The size was in the help text: "the 64 KiB block RAM at address zero", "the
/// 8 MiB HyperRAM". A number typed into a summary is a copy of something the
/// gateware decides, and nothing checks it -- change `BRAM_SIZE` and the help
/// keeps claiming the old figure. `Region::size` already reads `src/target.rs`,
/// which on the board comes from the SoC's own memory map.
///
/// KiB or MiB by magnitude rather than one fixed unit: 8388608 bytes is a
/// number nobody reads as 8 MiB.
fn info(uart: &mut Uart, region: Region) {
    let bytes = region.size();
    match region.base() {
        Some(base) => {
            let _ = write!(uart, "  {:9} @{:08x}  ", region.name(), base);
        }
        None => {
            let _ = write!(
                uart,
                "  {:9} {}  ",
                region.name(),
                match region {
                    Region::Hyperram => "via the CSR staging port",
                    _ => "no window on this target",
                }
            );
        }
    }
    if bytes >= 1024 * 1024 {
        let _ = write!(uart, "{} MiB", bytes / (1024 * 1024));
    } else {
        let _ = write!(uart, "{} KiB", bytes / 1024);
    }
    let _ = writeln!(uart, "  ({} bytes)", bytes);
    if let Some(why) = region.no_id() {
        let _ = writeln!(uart, "  {:9} {}", "id", why);
    }
}

/// One word, from whichever region was named. One parser, one bound, one line out.
fn read(uart: &mut Uart, region: Region, arg: &[u8]) {
    let Some(offset) = parse_hex(arg) else {
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

