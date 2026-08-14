//! `bram`, `flash`, `hyperram` -- read a word from a named region.
//!
//! `crate::memory` is the driver: the regions, their bounds and the reads.


use core::fmt::Write;

use crate::flash::{self, Flash};
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

    // The controller's verbs are flash-only: `bram sfdp` is not a sentence, and
    // the region check keeps them out of the other two regions' usage lines.
    if matches!(region, Region::Flash) && matches!(verb, b"sfdp" | b"erase" | b"program") {
        return controller(uart, verb, arg);
    }

    match verb {
        b"read" => read(uart, region, arg),
        b"status" => info(uart, region),
        // DEVICE-MAJOR. `bench bram` was a verb-major name over a device-major
        // reality: every other thing you can do to a memory is spelled
        // `<region> <verb>`, and `hyperram bench` already existed as an alias.
        // One shape, so the help listing and TAB tell the same story.
        b"bench" => crate::bench::command(uart, region.name().as_bytes()),
        _ => usage(uart, region),
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

/// Everything the part will tell you about itself, in ONE command.
///
/// **`status` is the only verb for this.** Identity, geometry and configuration
/// were three commands -- `info`, `id`, `regs` -- and a reader had to know all
/// three existed to get the picture. Where it is, how big, what it says it is,
/// how it is configured, and whether it is answering: one question.
///
/// Every value is READ BACK from the part or from the generated map. Nothing
/// here restates a constant the code was written with, which is the claim this
/// project keeps having to withdraw.
fn info(uart: &mut Uart, region: Region) {
    let bytes = region.size();
    match region.base() {
        Some(base) => {
            let _ = write!(uart, "  {:9} @{:08x}  ", region.name(), base);
        }
        None => {
            let _ = write!(uart, "  {:9} {}  ", region.name(),
                           match region {
                               Region::Hyperram => "via the CSR staging port",
                               _ => "no window on this target",
                           });
        }
    }
    if bytes >= 1024 * 1024 {
        let _ = writeln!(uart, "{} MiB ({} bytes)", bytes / (1024 * 1024), bytes);
    } else {
        let _ = writeln!(uart, "{} KiB ({} bytes)", bytes / 1024, bytes);
    }

    match region {
        Region::Flash => {
            id(uart, region);
            if let Some(spi) = Flash::take() {
                status(uart, &spi);
            }
        }
        Region::Hyperram => crate::shell::hr::registers(uart),
        Region::Bram => {
            let _ = writeln!(uart, "  {:9} fabric block RAM: no identity and no \
                                    configuration to read", "id");
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

    // The REAL id first, over the controller: `0x9f` is a register read, not an
    // address read, so no memory map can express it (#442).
    match Flash::take() {
        None => {
            let _ = writeln!(uart, "flash jedec needs the SPI controller, absent here");
        }
        Some(spi) => match spi.jedec_id() {
            Err(why) => {
                let _ = writeln!(uart, "flash jedec unavailable: {}", why);
            }
            Ok(id) => {
                let _ = write!(uart, "flash jedec {:06x}  {}", id, maker(id >> 16));
                match capacity_kib(id) {
                    Some(kib) => {
                        let _ = writeln!(uart, ", {} KiB declared", kib);
                    }
                    None => {
                        let _ = writeln!(uart, ", capacity byte {:02x} is not a density",
                                         id & 0xff);
                    }
                }
            }
        },
    }

    // And then the memory map's account: the first word -- 615000ff on a
    // programmed part, because offset 0 holds the bitstream -- and how much of
    // the part the window decodes.
    let _ = writeln!(
        uart,
        "flash @0 {:08x}, {} KiB",
        target::flash_word(0),
        target::FLASH_SIZE / 1024
    );
}

/// JEDEC bank-1 manufacturer. Only the one this board carries is named; a
/// table of 200 would be code for a question nobody asks twice.
fn maker(manufacturer: u32) -> &'static str {
    match manufacturer {
        0xef => "winbond",
        _ => "unknown maker",
    }
}

/// KiB from the capacity byte, which is `log2(bytes)`. `None` for a byte that
/// is not one -- 00 and ff are what a silent path returns, and reporting either
/// as a size would dress up a dead read as an answer.
fn capacity_kib(id: u32) -> Option<u32> {
    match id & 0xff {
        exponent @ 10..=31 => Some(1 << (exponent - 10)),
        _ => None,
    }
}

/// `flash sfdp|status|erase|program` -- the verbs only the controller can serve.
fn controller(uart: &mut Uart, verb: &[u8], arg: &[u8]) {
    let Some(spi) = Flash::take() else {
        let _ = writeln!(uart, "flash: no SPI controller on this target");
        return;
    };
    match verb {
        b"sfdp" => sfdp(uart, &spi),
        b"status" => status(uart, &spi),
        b"erase" => erase(uart, &spi, arg),
        b"program" => program(uart, &spi, arg),
        _ => {}
    }
}

/// The SFDP header: signature, revision, and how many parameter headers follow.
///
/// SFDP is what declares the 4 MiB that every address above it aliases back
/// into, so it is the one account of the part's size that is not a constant in
/// this tree.
fn sfdp(uart: &mut Uart, spi: &Flash) {
    let mut header = [0u8; 8];
    match spi.sfdp(0, &mut header) {
        Err(why) => {
            let _ = writeln!(uart, "flash sfdp unavailable: {}", why);
        }
        Ok(()) => {
            let _ = writeln!(
                uart,
                "flash sfdp {:02x}{:02x}{:02x}{:02x} rev {}.{}, {} parameter header(s)  {}",
                header[0], header[1], header[2], header[3],
                header[5], header[4], header[6] as u32 + 1,
                if &header[..4] == b"SFDP" { "signature ok" } else { "NOT SFDP" }
            );
        }
    }
}

fn status(uart: &mut Uart, spi: &Flash) {
    match spi.status1() {
        Err(why) => {
            let _ = writeln!(uart, "flash status unavailable: {}", why);
        }
        Ok(sr1) => {
            let _ = writeln!(uart, "flash sr1 {:02x}  busy {} wel {}", sr1,
                             (sr1 & flash::SR1_BUSY != 0) as u8,
                             (sr1 & flash::SR1_WEL != 0) as u8);
        }
    }
}

/// `flash erase <hex>` -- one 4 KiB sector, and only the reserved one.
///
/// The offset must be typed. The driver refuses anything outside the sector,
/// so this cannot reach the bitstream at 0 or the image at 0xb0000; requiring
/// the argument is about the operation being deliberate rather than a TAB away.
fn erase(uart: &mut Uart, spi: &Flash, arg: &[u8]) {
    let Some(offset) = parse_hex(arg) else {
        let _ = writeln!(uart, "usage: flash erase <hex offset in the {:06x} sector>",
                         flash::SCRATCH);
        return;
    };
    // Announced BEFORE it starts, because the shell does not come back from it:
    // the erase lands, and the CPU does not survive the 45 ms the part spends
    // answering nothing but its status register. Reconfigure to get it back;
    // the sector is erased when you do. #463.
    let _ = writeln!(uart, "flash erase {:06x} starting -- the console will not \
                            return; reconfigure after (#463)", offset);
    match spi.sector_erase(offset) {
        Err(why) => {
            let _ = writeln!(uart, "flash erase {:06x} failed: {}", offset, why);
        }
        Ok(cycles) => {
            let us = flash::micros(cycles);
            let _ = write!(uart, "flash erase {:06x}  4 KiB in {} us", offset, us);
            // tSE is 45 ms typical. An erase two orders under that erased
            // nothing, which is the shape of the fault the C bring-up firmware
            // found on this path -- report it rather than believe the timing.
            if us < flash::ERASE_FLOOR_US {
                let _ = write!(uart, "  IMPLAUSIBLY FAST (tSE typ 45 ms)");
            }
            readback(uart, spi, offset, 0xffff_ffff);
        }
    }
}

/// `flash program <hex>` -- eight words of a seeded ramp into the reserved
/// sector, verified over the controller so the D-cache cannot answer for it.
fn program(uart: &mut Uart, spi: &Flash, arg: &[u8]) {
    let Some(seed) = parse_hex(arg) else {
        let _ = writeln!(uart, "usage: flash program <hex seed>");
        return;
    };
    let mut words = [0u32; 8];
    for (index, word) in words.iter_mut().enumerate() {
        *word = seed.wrapping_add(index as u32);
    }
    // As `erase`: the program lands and the console does not come back (#463).
    let _ = writeln!(uart, "flash program {:06x} starting -- the console will not \
                            return; reconfigure after (#463)", flash::SCRATCH);
    match spi.page_program(flash::SCRATCH, &words) {
        Err(why) => {
            let _ = writeln!(uart, "flash program failed: {}", why);
        }
        Ok(cycles) => {
            let _ = write!(uart, "flash program {:06x}  {} words in {} us",
                           flash::SCRATCH, words.len(), flash::micros(cycles));
            readback(uart, spi, flash::SCRATCH, seed);
        }
    }
}

/// One word back over the CONTROLLER, and whether it is what was asked for.
/// Ends the line either way.
fn readback(uart: &mut Uart, spi: &Flash, offset: u32, expected: u32) {
    match spi.read_word(offset) {
        Err(why) => {
            let _ = writeln!(uart, "; readback failed: {}", why);
        }
        Ok(word) if word == expected => {
            let _ = writeln!(uart, "; reads {:08x}", word);
        }
        Ok(word) => {
            let _ = writeln!(uart, "; reads {:08x}, WANTED {:08x}", word, expected);
        }
    }
}

/// How to call it, named for the region that was actually typed: a person who typed
/// `hyperram` and got told about `flash` would reasonably wonder which one answered.
fn usage(uart: &mut Uart, region: Region) {
    let _ = writeln!(
        uart,
        "usage: {} info|read <hex offset>{}",
        region.name(),
        if region.no_id().is_none() {
            ", or `flash id|sfdp|status|erase|program`"
        } else {
            ""
        }
    );
}

