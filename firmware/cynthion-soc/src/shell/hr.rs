//! `hr` -- everything HyperRAM-specific, under one verb.
//!
//! Moved out of `main.rs` unchanged (#296). `hyperram.rs` and `bench.rs` are the
//! drivers.

use core::fmt::Write;

use crate::shell::parse::{parse_hex, trim};
use crate::uart::Uart;
use crate::{bench, hyperram};

/// Everything HyperRAM-specific, under one verb.
///
///     hr status   the DQS read path's self-report
///     hr read <hex>  one word over the staging port
///     hr sel <n>  bits 2:0 READCLKSEL, bit 3 read-window phase
///     hr sweep    try every READCLKSEL and say which ones read correctly
///     hr test     round-trip one word through the staging port
///     hr cross    do the window and the staging port agree?
///     hr bench    the same walk as `bench hyperram`
///     hr id       HyperBus has no identify
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    if rest.is_empty() {
        return crate::shell::list_family(uart, "hyperram");
    }
    match rest {
        b"status" => {
            let (locked, ready, seen, bursts) = bench::dqs_status();
            let _ = writeln!(
                uart,
                "dqs: dll {} {}, burstdet {} ({} bursts)",
                if locked { "locked" } else { "UNLOCKED" },
                if ready { "ready" } else { "NOT-READY" },
                if seen { "seen" } else { "NEVER" },
                bursts
            );
        }
        b"test" => {
            // Round-trip one word so the HyperRAM path can be checked without
            // staging a whole image.
            hyperram::write_header(0, 0);
            match hyperram::staged() {
                Ok(_) => {
                    let _ = writeln!(
                        uart,
                        "hyperram round-trip BAD: zero length should be rejected"
                    );
                }
                // `Length` specifically: the magic was written and read back,
                // which is the round trip this checks. `NoMagic` or `Silent`
                // would mean the word did not survive.
                Err(hyperram::Reject::Length) => {
                    let _ = writeln!(uart, "hyperram write+read ok");
                }
                Err(_) => {
                    let _ = writeln!(uart, "hyperram round-trip BAD: the magic did not read back");
                }
            }
            hyperram::invalidate();
        }
        b"cross" => {
            let result = bench::hyper_cross_check();
            // Printed either way. On a pass it is the evidence that a DQS read
            // used its strobe rather than a latency count that landed right by
            // luck; on a failure it says which layer to look at.
            let (locked, ready, seen, bursts) = bench::dqs_status();
            let _ = writeln!(
                uart,
                "dqs: dll {} {}, burstdet {} ({} bursts)",
                if locked { "locked" } else { "UNLOCKED" },
                if ready { "ready" } else { "NOT-READY" },
                if seen { "seen" } else { "NEVER" },
                bursts
            );
            let (wrote_w, win_w, stg_w) = result.window_written;
            let (wrote_s, win_s, stg_s) = result.staged_written;
            let _ = writeln!(
                uart,
                "  window wrote {:08x}: window {:08x} staging {:08x}",
                wrote_w, win_w, stg_w
            );
            let _ = writeln!(
                uart,
                "  staging wrote {:08x}: window {:08x} staging {:08x}",
                wrote_s, win_s, stg_s
            );
            let (good, bitmap, want, got) = bench::hyper_line_write_check();
            let _ = writeln!(
                uart,
                "  line write: {}/16 correct, bad {:016b} want {:08x} got {:08x}, ck-stalled {} cycles",
                good, bitmap, want, got, bench::stalls()
            );
            if result.ok() {
                let _ = writeln!(uart, "hyperram ports agree");
            } else {
                let _ = writeln!(uart, "hyperram ports DISAGREE");
            }
        }
        // THE DESTRUCTIVE VERB, and it is never called by `hyperram::init`.
        // Separating the two is the rule: an init that quietly destroyed data
        // would be a data-loss bug waiting for the first person who ran it
        // expecting a health check. See `src/init.rs`.
        b"clear" => {
            hyperram::clear();
            let _ = writeln!(
                uart,
                "hyperram cleared: staging magic and power canary gone, any staged image \
                 is invalid.\n  RESET# is not reachable from firmware in this bitstream, \
                 so the array itself is untouched (#315)."
            );
        }
        b"bench" => bench::command(uart, b"hyperram"),
        b"id" => crate::shell::memory::command(uart, crate::memory::Region::Hyperram, b"id"),
        _ if rest.starts_with(b"read") => crate::shell::memory::command(uart, crate::memory::Region::Hyperram, rest),
        _ if rest.starts_with(b"sel") => match parse_hex(trim(&rest[3..])) {
            Some(n) if n < 16 => {
                bench::set_readclksel(n as u8);
                let _ = writeln!(uart, "readclksel {}", n);
            }
            _ => {
                let _ = writeln!(uart, "usage: hr sel <0-3f>  (2:0 tap, 3 phase, 5:4 read stall)");
            }
        },
        _ if rest.starts_with(b"ramp") => {
            // A 0-255 byte ramp: every byte names its own position, so a
            // displacement, a duplication or a swapped pair is READ off the dump
            // rather than inferred from four bytes of a single word.
            //
            // `hr ramp` VERIFIES what is already there -- use it after staging
            // the ramp over JTAG, which writes through a path that shares none
            // of this SoC's write logic, so a failure is then unambiguously a
            // READ fault. `hr ramp w` writes it through the memory window first,
            // which tests the write path against the same known pattern.
            const RAMP_AT: usize = 0x4000;      // bytes into the window
            const RAMP_LEN: usize = 256;
            let base = cynthion_soc_pac::base::HYPERRAM + RAMP_AT;
            let writing = trim(&rest[4..]) == b"w";

            if writing {
                for i in (0..RAMP_LEN).step_by(4) {
                    let word = (i as u32)
                        | ((i as u32 + 1) << 8)
                        | ((i as u32 + 2) << 16)
                        | ((i as u32 + 3) << 24);
                    // SAFETY: 4-byte aligned, inside the decoded 8 MiB window.
                    unsafe { core::ptr::write_volatile((base + i) as *mut u32, word) };
                }
                bench::evict_pub();
            }

            let mut wrong = 0;
            let mut first_bad = RAMP_LEN;
            let mut got = [0u8; 16];
            for i in 0..RAMP_LEN {
                // SAFETY: as above; byte reads inside the same window.
                let byte = unsafe { core::ptr::read_volatile((base + i) as *const u8) };
                if i < 16 {
                    got[i] = byte;
                }
                if byte != i as u8 {
                    wrong += 1;
                    if first_bad == RAMP_LEN {
                        first_bad = i;
                    }
                }
            }

            let _ = writeln!(uart, "ramp {} at +{:x}, {} bytes",
                             if writing { "written and verified" } else { "verified" },
                             RAMP_AT, RAMP_LEN);
            let _ = write!(uart, "  first 16 want 00..0f got");
            for byte in got.iter() {
                let _ = write!(uart, " {:02x}", byte);
            }
            let _ = writeln!(uart, "");
            if wrong == 0 {
                let _ = writeln!(uart, "  {}/{} correct -- the path is clean", RAMP_LEN, RAMP_LEN);
            } else {
                let _ = writeln!(uart, "  {}/{} wrong, first at +{:x}",
                                 wrong, RAMP_LEN, first_bad);
            }
        }
        b"sweep" => {
            // One bitstream, eight settings. The tap that captures returning
            // data is a property of the board and CK, and the built-in default
            // is upstream's untested guess.
            for setting in 0..64u8 {
                bench::set_readclksel(setting);
                let (good, bitmap, want, got) = bench::hyper_line_write_check();
                let (_, _, seen, bursts) = bench::dqs_status();
                let _ = writeln!(
                    uart,
                    "  tap {} phase {} rd-stall {}: {:2}/16, bad {:016b}, burstdet {} ({}), want {:08x} got {:08x}",
                    setting & 7, (setting >> 3) & 1, setting >> 4, good, bitmap,
                    if seen { "y" } else { "n" }, bursts, want, got
                );
            }
        }
        _ => {
            let _ = writeln!(
                uart,
                "usage: hr status|read <hex>|sel <n>|sweep|test|cross|bench|id"
            );
        }
    }
}

