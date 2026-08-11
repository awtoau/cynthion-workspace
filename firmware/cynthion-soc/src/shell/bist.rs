//! `bist` -- the HyperRAM BIST engine, one cell at a time. See #226.
//!
//! One cell is one command, and a sweep is the same command in a loop, so
//! nothing is exercised in a sweep that was not first exercised alone. The last
//! attempt put the loop in gateware; it died at cell 0 and there was no way to
//! ask it anything (#210).
//!
//! The driver is `src/bist.rs`; this only parses words.
//!
//! ## The verb is gated on the VARIANT, not on a probe (#409)
//!
//! `bist` was dispatched on every build, including the shipping
//! `bist0-*` bitstreams, whose gateware has no engine and no window at
//! `bist::BASE`. `describe()` read it anyway, nothing decoded it, nothing
//! acknowledged, and the board was gone.
//!
//! `Bist::present()` is not the fix and cannot be: it answers by READING the
//! engine, which is the access that hangs. The variant is known at build time --
//! `gateware/soc/variant.py`, `CYNTHION_HYPERRAM_BIST`, which `soc_run.py`
//! turns into this cargo feature -- so the verb that touches the bus only
//! exists where the bus has something to touch.

// Everything below `command` is reachable only from the engine-bearing build.
#![cfg_attr(not(feature = "hyperram-bist"), allow(dead_code, unused_imports))]

use core::fmt::Write;

use super::parse::{parse_decimal, trim};
use crate::bist::{self, Axes, Bist};
use crate::clock::Hz;
use crate::uart::Uart;

/// Passes per cell when none is given.
///
/// One pass is a 128-word burst each way. 64 of them is a few thousand words --
/// enough that a marginal setting shows up rather than being missed by a single
/// lucky burst, and short enough that a 128-cell sweep is not an afternoon.
const DEFAULT_PASSES: u32 = 64;

/// `bist` on a bitstream with no engine: one sentence, and NO bus access.
#[cfg(not(feature = "hyperram-bist"))]
pub(crate) fn command(uart: &mut Uart, _rest: &[u8]) {
    let _ = writeln!(
        uart,
        "bist: this bitstream has no engine -- built without CYNTHION_HYPERRAM_BIST"
    );
}

#[cfg(feature = "hyperram-bist")]
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    let rest = trim(rest);
    // SAFETY: `bist::BASE` is the peripheral's CSR base, held equal to the
    // gateware's by `tests/test_bist_constants.py`.
    let engine = unsafe { Bist::new(bist::BASE) };

    let (word, args) = match rest.iter().position(|&b| b == b' ') {
        Some(i) => (&rest[..i], trim(&rest[i + 1..])),
        None => (rest, &rest[..0]),
    };

    match word {
        b"" | b"status" => engine.describe(uart),
        b"smoke" => bist::smoke(uart, &engine, passes(args)),
        b"latency" => bist::latency(uart, &engine, passes(args)),
        b"all" => bist::all(uart, &engine, passes(args)),
        b"sweep" => bist::sweep(uart, &engine, passes(args), false),
        b"trace" => bist::sweep(uart, &engine, passes(args), true),
        b"ck" => ck(uart, args),
        b"cell" => match axes(args) {
            Some(axes) => bist::one(uart, &engine, axes, DEFAULT_PASSES),
            None => {
                let _ = writeln!(
                    uart,
                    "usage: bist cell <drive 0-7> <dif|se> <sel 0-7> [latency 0-15] [fix|var]"
                );
            }
        },
        _ => crate::shell::list_family(uart, "bist"),
    }
}

/// `bist ck [0|1]` -- report the rungs this bitstream carries, or select one.
///
/// Through `clock::Hz`, which is lossless: the reachable rungs include 85.7143
/// and 94.2857 MHz, and a sweep exists to tell those apart from their
/// neighbours (#333). The rung column is ragged as a result -- a whole megahertz
/// prints as `60 MHz`, not `60.000` -- and both host regexes
/// (`hyperram_ck_sweep.py`, `hyperram_matrix_diff.py`) already read
/// `([\d.]+)\s+MHz`.
fn ck(uart: &mut Uart, args: &[u8]) {
    // SAFETY: `bist::CK_BASE` is the selector's CSR base.
    let ck = unsafe { bist::Ck::new(bist::CK_BASE) };
    let rungs = ck.rungs();
    if rungs == 0 {
        let _ = writeln!(uart, "ck: no selector in this bitstream");
        return;
    }

    if let Some(want) = parse_decimal(args) {
        if want >= rungs {
            let _ = writeln!(uart, "ck: this bitstream carries {} rung(s)", rungs);
            return;
        }
        ck.select(want);
    }

    let live = ck.selected();
    for rung in 0..rungs {
        let _ = writeln!(
            uart,
            "  rung {} {}{}",
            rung,
            Hz::khz(ck.khz(rung)),
            if rung == live { "  <- live" } else { "" }
        );
    }
    // Both rungs come off one VCO, so a lost lock is not per-rung -- but it is
    // the one condition under which every number above is meaningless.
    if !ck.locked() {
        let _ = writeln!(uart, "  PLL NOT LOCKED -- no measurement here means anything");
    }
}

/// `[passes]`, or the default.
fn passes(args: &[u8]) -> u32 {
    match parse_decimal(args) {
        // Zero passes would run nothing and report zero errors over zero words,
        // which `verdict()` calls NoResult -- correct, but a confusing way to
        // learn that the argument was rejected.
        Some(0) | None => DEFAULT_PASSES,
        Some(n) => n,
    }
}

/// `<drive> <dif|se> <sel> [latency] [fix|var]`.
fn axes(args: &[u8]) -> Option<Axes> {
    let mut words = args.split(|&b| b == b' ').filter(|w| !w.is_empty());
    let drive = parse_decimal(words.next()?)?;
    let clock = words.next()?;
    let readclksel = parse_decimal(words.next()?)?;
    // Latency is optional: omitted means the power-on 0010b/fixed, which is what
    // every reading before this used.
    let latency = words.next().and_then(parse_decimal).unwrap_or(2);
    // `fixed_latency` was HARDCODED true here, so no single cell could ever
    // command CR0[3] = 0 -- only the sweeps varied it, and they do not report the
    // readback. That is why the fix/var axis shows "no effect" with nothing
    // proving the axis moved at all (#334).
    let fixed_latency = match words.next() {
        None | Some(b"fix") => true,
        Some(b"var") => false,
        Some(_) => return None,
    };
    if drive > 7 || readclksel > 7 {
        return None;
    }
    let single_ended_clock = match clock {
        b"se" => true,
        b"dif" => false,
        _ => return None,
    };
    Some(Axes {
        drive: drive as u8,
        single_ended_clock,
        readclksel: readclksel as u8,
        latency: latency as u8,
        fixed_latency,
    })
}
