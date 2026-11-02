//! `usb <port> dp [serve [n]|nak <n>]` -- syntax over `crate::datapath`.

use core::fmt::Write;

use crate::datapath::{DataPath, Error};
use crate::shell::parse::{parse_decimal, trim};
use crate::uart::Uart;
use crate::usb_host::Host;

/// `serve` returns after this long with no block queued. An operator bound, not a
/// hardware one: long enough to start the PC tool after typing the command.
const SERVE_IDLE_US: u32 = 5_000_000;

const STATUS_NAMES: [&str; 7] =
    ["ok", "stall", "nak_limit", "error", "bad_header", "rejected", "overrun"];

fn fail(uart: &mut Uart, error: Error) {
    let _ = writeln!(uart, "{}", error);
}

pub(crate) fn command(uart: &mut Uart, dp: &mut DataPath, host: &Host, args: &[u8]) {
    let args = trim(args);
    let (word, rest) = match args.iter().position(|&b| b == b' ') {
        Some(at) => (&args[..at], trim(&args[at + 1..])),
        None => (args, &args[..0]),
    };
    match word {
        b"" => match dp.status() {
            Ok(s) => {
                let _ = writeln!(uart, "status {:02x} queued {} busy {} sending {} nak_limit {}",
                                 s.status, (s.status >> 3) & 7, s.status & 2 != 0,
                                 s.status & 4 != 0, s.nak_limit);
            }
            Err(e) => fail(uart, e),
        },
        b"nak" => match parse_decimal(rest).filter(|&n| n <= 0xffff) {
            Some(n) => match dp.set_nak_limit(n as u16) {
                Ok(()) => {
                    let _ = writeln!(uart, "nak_limit {}", n);
                }
                Err(e) => fail(uart, e),
            },
            None => {
                let _ = writeln!(uart, "usage: usb <port> dp nak <0..65535>");
            }
        },
        b"serve" => {
            let max = if rest.is_empty() { Some(u32::MAX) } else { parse_decimal(rest) };
            let Some(max) = max else {
                let _ = writeln!(uart, "usage: usb <port> dp serve [count]");
                return;
            };
            let result = dp.serve(host, max, SERVE_IDLE_US, |s| {
                let d = &s.desc;
                let _ = writeln!(uart, "tag {:02x} ep {} {} len {} -> {} moved {} naks {} blocks {}",
                                 d.tag, d.ep(), if d.is_in() { "in" } else { "out" }, d.len,
                                 STATUS_NAMES.get(s.status as usize).unwrap_or(&"?"),
                                 s.moved, s.naks, s.blocks);
            });
            match result {
                Ok(n) => {
                    let _ = writeln!(uart, "served {}", n);
                }
                Err(e) => fail(uart, e),
            }
        }
        _ => {
            let _ = writeln!(uart, "usage: usb <port> dp [serve [count]|nak <n>]");
        }
    }
}
