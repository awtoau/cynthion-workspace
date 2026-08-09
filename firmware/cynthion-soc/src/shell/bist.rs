//! `bist` -- the HyperRAM BIST engine, one cell at a time. See #226.
//!
//! One cell is one command, and a sweep is the same command in a loop, so
//! nothing is exercised in a sweep that was not first exercised alone. The last
//! attempt put the loop in gateware; it died at cell 0 and there was no way to
//! ask it anything (#210).
//!
//! The driver is `src/bist.rs`; this only parses words.

use core::fmt::Write;

use super::parse::{parse_decimal, trim};
use crate::bist::{self, Axes, Bist};
use crate::uart::Uart;

/// Passes per cell when none is given.
///
/// One pass is a 128-word burst each way. 64 of them is a few thousand words --
/// enough that a marginal setting shows up rather than being missed by a single
/// lucky burst, and short enough that a 128-cell sweep is not an afternoon.
const DEFAULT_PASSES: u32 = 64;

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
        b"cell" => match axes(args) {
            Some(axes) => bist::one(uart, &engine, axes, DEFAULT_PASSES),
            None => {
                let _ = writeln!(uart, "usage: bist cell <drive 0-7> <dif|se> <sel 0-7>");
            }
        },
        _ => crate::shell::list_family(uart, "bist"),
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

/// `<drive> <dif|se> <sel>`.
fn axes(args: &[u8]) -> Option<Axes> {
    let mut words = args.split(|&b| b == b' ').filter(|w| !w.is_empty());
    let drive = parse_decimal(words.next()?)?;
    let clock = words.next()?;
    let readclksel = parse_decimal(words.next()?)?;
    // Latency is optional: omitted means the power-on 0010b/fixed, which is what
    // every reading before this used.
    let latency = words.next().and_then(parse_decimal).unwrap_or(2);
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
        fixed_latency: true,
    })
}
