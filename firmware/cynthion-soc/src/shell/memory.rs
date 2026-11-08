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
    if matches!(region, Region::Flash)
        && matches!(verb, b"regs" | b"wren" | b"erase" | b"program" | b"test" | b"cal" | b"reset")
    {
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
                regs(uart, &spi);
            }
        }
        Region::Hyperram => crate::shell::hr::command(uart, b"status"),
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
            let _ = writeln!(uart, "flash jedec needs the command port, absent here");
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

/// `flash regs|wren|erase|program|test|cal|reset` -- the command port (#593).
fn controller(uart: &mut Uart, verb: &[u8], arg: &[u8]) {
    let Some(spi) = Flash::take() else {
        let _ = writeln!(uart, "flash: no command port on this target");
        return;
    };
    match verb {
        b"regs" => regs(uart, &spi),
        b"wren" => {
            let r = spi.write_enable();
            let _ = writeln!(uart, "flash wren: {}", ok_or(r.err()));
        }
        b"erase" => erase(uart, &spi, arg),
        b"program" => program(uart, &spi, arg),
        b"test" => test(uart, &spi, arg),
        b"cal" => cal(uart, &spi),
        b"reset" => {
            let r = spi.reset();
            let _ = writeln!(uart, "flash reset: {}", ok_or(r.err()));
        }
        _ => {}
    }
}

fn ok_or(e: Option<flash::Error>) -> &'static str {
    match e {
        None => "ok",
        Some(flash::Error::Code(c)) => flash::CODE_NAMES.get(c as usize).unwrap_or(&"?"),
        Some(flash::Error::Timeout) => "TIMEOUT",
        Some(flash::Error::Outside) => "outside the test region",
        Some(flash::Error::NotResident) => "poll loop not in block RAM",
    }
}

/// Status registers, the peripheral's own state and its sticky errors.
fn regs(uart: &mut Uart, spi: &Flash) {
    use cynthion_soc_pac::flash::offset;
    match spi.status_regs() {
        Ok(sr) => {
            let _ = write!(uart, "flash sr1 {:02x} sr2 {:02x} sr3 {:02x}",
                           sr & 0xff, (sr >> 8) & 0xff, (sr >> 16) & 0xff);
        }
        Err(why) => {
            let _ = write!(uart, "flash status unavailable: {}", why);
        }
    }
    let fl = spi.r32(offset::FILL_LAST);
    let _ = writeln!(uart, "  status {:04x} errors {:04x} fill_last {} fill_max {} ck {}",
                     spi.status(), spi.take_errors(), fl & 0xffff, fl >> 16,
                     spi.r32(offset::CK_COUNT));
}

/// `flash cal` -- rerun capture calibration (the wait runs from block RAM).
fn cal(uart: &mut Uart, spi: &Flash) {
    use cynthion_soc_pac::flash::offset;
    let r = spi.calibrate();
    let c = spi.r32(offset::CAL);
    let _ = writeln!(uart, "flash cal {}: EBh eye {} centre lat {} phase {} xfer {} valid {}",
                     ok_or(r.err()), (c >> 16) & 0xff, (c >> 5) & 0xf, c & 31, (c >> 24) & 1,
                     c >> 31);
}

/// `flash erase <hex> [64k]` -- one sector or block inside the test region.
fn erase(uart: &mut Uart, spi: &Flash, arg: &[u8]) {
    let (a, rest) = split(arg);
    let Some(addr) = parse_hex(a) else {
        let _ = writeln!(uart, "usage: flash erase <hex in {:06x}..{:06x}> [64k]",
                         flash::TEST_LO, flash::TEST_HI);
        return;
    };
    match spi.erase(addr, rest == b"64k") {
        Ok(busy) => {
            let _ = writeln!(uart, "flash erase {:06x} ok, busy {} us", addr, flash::sync_us(busy));
        }
        Err(why) => {
            let _ = writeln!(uart, "flash erase {:06x} failed: {}", addr, why);
        }
    }
}

/// `flash program <hex addr> <hex seed>` -- one 256 B page of the seeded pattern.
fn program(uart: &mut Uart, spi: &Flash, arg: &[u8]) {
    let (a, rest) = split(arg);
    let (Some(addr), Some(seed)) = (parse_hex(a), parse_hex(rest)) else {
        let _ = writeln!(uart, "usage: flash program <hex page address> <hex seed>");
        return;
    };
    let at = addr & !0xff;
    let mut page = [0u8; 256];
    fill(&mut page, at, seed);
    match spi.program(at, &page) {
        Ok(busy) => {
            let _ = writeln!(uart, "flash program {:06x} ok, busy {} us", at, flash::sync_us(busy));
        }
        Err(why) => {
            let _ = writeln!(uart, "flash program {:06x} failed: {}", at, why);
        }
    }
}

/// The byte due at flash address `a` for `seed`: every address bit folds in, low lines
/// toggle byte to byte, each seed byte xors a quarter of the bytes.
fn pattern(a: u32, seed: u32) -> u8 {
    let b = (a >> 1) ^ (a >> 9) ^ ((a >> 17) << 2);
    let b = if a & 1 != 0 { !b } else { b };
    (b ^ (seed >> (8 * (a & 3)))) as u8
}

fn fill(out: &mut [u8], addr: u32, seed: u32) {
    for (i, b) in out.iter_mut().enumerate() {
        *b = pattern(addr + i as u32, seed);
    }
}

/// Wrong bytes in `len` at `addr`, over the command port (`seed` None: blank).
fn check(spi: &Flash, addr: u32, len: u32, seed: Option<u32>) -> Result<u32, flash::Error> {
    let mut chunk = [0u8; 256];
    let mut bad = 0;
    let mut at = addr;
    while at < addr + len {
        spi.read(at, &mut chunk)?;
        for (i, b) in chunk.iter().enumerate() {
            let want = seed.map_or(0xff, |s| pattern(at + i as u32, s));
            bad += (*b != want) as u32;
        }
        at += chunk.len() as u32;
    }
    Ok(bad)
}

fn show(r: Result<u32, flash::Error>) -> i64 {
    match r {
        Ok(v) => v as i64,
        Err(_) => -1,
    }
}

/// `flash test [hex addr] [hex seed]` -- erase one 64 KiB block of the test region,
/// check blank, program its 256 pages, verify over the command port and over XIP, and a
/// stale-seed control that must fail.
fn test(uart: &mut Uart, spi: &Flash, arg: &[u8]) {
    let (a, rest) = split(arg);
    let addr = parse_hex(a).unwrap_or(flash::TEST_LO) & !(flash::BLOCK - 1);
    let seed = parse_hex(rest).unwrap_or(0x5937_a1c4);
    let n = flash::BLOCK;
    let _ = writeln!(uart, "flash test {:06x}+{:x} seed {:08x}", addr, n, seed);
    let erase = match spi.erase(addr, true) {
        Ok(busy) => busy,
        Err(why) => {
            let _ = writeln!(uart, "  erase failed: {}", why);
            return;
        }
    };
    let blank = check(spi, addr, n, None);
    let mut page = [0u8; 256];
    let (mut busy_sum, mut busy_max) = (0u32, 0u32);
    let mut at = addr;
    while at < addr + n {
        fill(&mut page, at, seed);
        match spi.program(at, &page) {
            Ok(busy) => {
                busy_sum += busy;
                busy_max = busy_max.max(busy);
            }
            Err(why) => {
                let _ = writeln!(uart, "  program {:06x} failed: {}", at, why);
                return;
            }
        }
        at += flash::PAGE;
    }
    let good = check(spi, addr, n, Some(seed));
    let stale = check(spi, addr, n, Some(!seed));
    // XIP: evict the 4 KiB direct-mapped D-cache with 8 KiB of other flash, then compare.
    let mut evict = 0u32;
    for i in (0..8192).step_by(4) {
        evict = evict.wrapping_add(target::flash_word(i));
    }
    let mut xip_bad = 0u32;
    for i in (0..n).step_by(4) {
        let w = target::flash_word((addr + i) as usize);
        for k in 0..4 {
            xip_bad += (((w >> (8 * k)) as u8) != pattern(addr + i + k, seed)) as u32;
        }
    }
    let _ = writeln!(uart, "  erase 64K {} us; blank errors {}", flash::sync_us(erase), show(blank));
    let _ = writeln!(uart, "  program 256 pages: avg {} us, max {} us",
                     flash::sync_us(busy_sum / 256), flash::sync_us(busy_max));
    let _ = writeln!(uart, "  verify (EBh command READ): {} of {} bytes wrong", show(good), n);
    let _ = writeln!(uart, "  verify (XIP): {} of {} bytes wrong (evict {:08x})", xip_bad, n,
                     evict);
    let _ = writeln!(uart, "  control (stale seed): {} of {} bytes wrong (must be most)",
                     show(stale), n);
    let pass = matches!(blank, Ok(0)) && matches!(good, Ok(0)) && xip_bad == 0
        && matches!(stale, Ok(s) if s > n / 2);
    let _ = writeln!(uart, "flash test {}", if pass { "PASS" } else { "FAIL" });
}

fn split(arg: &[u8]) -> (&[u8], &[u8]) {
    match arg.iter().position(|&b| b == b' ') {
        Some(i) => (&arg[..i], trim(&arg[i + 1..])),
        None => (arg, &arg[..0]),
    }
}

/// How to call it, named for the region that was actually typed: a person who typed
/// `hyperram` and got told about `flash` would reasonably wonder which one answered.
fn usage(uart: &mut Uart, region: Region) {
    let _ = writeln!(
        uart,
        "usage: {} status|read <hex offset>|bench{}",
        region.name(),
        if region.no_id().is_none() {
            ", or `flash regs|wren|erase|program|test|cal|reset`"
        } else {
            ""
        }
    );
}

