//! `usb <port> role|reset|desc|setup|in|out|tx|rx|addr|aoa|pump|echo` -- syntax over `crate::usb_host`.
//!
//! No transfer logic here: parse, call the driver, print its `Error` as it displays.

use core::fmt::Write;

use crate::shell::parse::{parse_decimal, parse_hex, trim};
use crate::uart::Uart;
use crate::usb_host::{self, Error, Host};

/// Verbs this module owns; `usb.rs` routes on them.
pub(crate) const VERBS: [&[u8]; 13] = [
    b"trace", b"role", b"reset", b"desc", b"setup", b"in", b"out", b"tx", b"rx", b"addr", b"aoa",
    b"pump", b"echo",
];

fn fail(uart: &mut Uart, error: Error) {
    let _ = writeln!(uart, "{}", error);
}

/// Up to 64 hex bytes from `text`; `None` on a bad token or none at all.
fn hex_bytes(text: &[u8], out: &mut [u8; 64]) -> Option<usize> {
    let mut n = 0;
    for token in text.split(|&b| b == b' ').filter(|t| !t.is_empty()) {
        let value = parse_hex(token)?;
        if value > 0xff || n == out.len() {
            return None;
        }
        out[n] = value as u8;
        n += 1;
    }
    if n == 0 { None } else { Some(n) }
}

fn dump(uart: &mut Uart, bytes: &[u8]) {
    for byte in bytes {
        let _ = write!(uart, "{:02x} ", byte);
    }
    let _ = writeln!(uart);
}

pub(crate) fn command(uart: &mut Uart, host: &mut Host, verb: &[u8], args: &[u8]) {
    let args = trim(args);
    let mut buf = [0u8; 64];
    match verb {
        b"role" => role(uart, host, args),
        b"reset" => match host.reset() {
            Ok(()) => {
                let _ = writeln!(uart, "reset done");
            }
            Err(e) => fail(uart, e),
        },
        b"desc" => match host.device_descriptor() {
            Ok(d) => {
                let _ = writeln!(uart, "{:04x}:{:04x}", d[8] as u16 | (d[9] as u16) << 8,
                                 d[10] as u16 | (d[11] as u16) << 8);
                dump(uart, &d);
            }
            Err(e) => fail(uart, e),
        },
        b"setup" => match hex_bytes(args, &mut buf) {
            Some(8) => {
                let mut packet = [0u8; 8];
                packet.copy_from_slice(&buf[..8]);
                match host.setup(&packet) {
                    Ok(()) => {
                        let _ = writeln!(uart, "ack");
                    }
                    Err(e) => fail(uart, e),
                }
            }
            _ => {
                let _ = writeln!(uart, "usage: usb <port> setup <8 hex bytes>");
            }
        },
        b"in" => {
            let (ep, len) = match args.iter().position(|&b| b == b' ') {
                Some(at) => (parse_decimal(&args[..at]), parse_decimal(&args[at + 1..])),
                None => (None, None),
            };
            let (Some(ep), Some(len)) = (ep.filter(|&e| e < 16), len.filter(|&l| l <= 64)) else {
                let _ = writeln!(uart, "usage: usb <port> in <ep 0-15> <len 0-64>");
                return;
            };
            match host.bulk_in(ep as u8, &mut buf[..len as usize], usb_host::BULK_US) {
                Ok(n) => {
                    let _ = writeln!(uart, "{} bytes", n);
                    dump(uart, &buf[..n]);
                }
                Err(e) => fail(uart, e),
            }
        }
        b"out" => {
            let at = args.iter().position(|&b| b == b' ').unwrap_or(args.len());
            let ep = parse_decimal(&args[..at]).filter(|&e| e < 16);
            let n = hex_bytes(&args[at..], &mut buf);
            let (Some(ep), Some(n)) = (ep, n) else {
                let _ = writeln!(uart, "usage: usb <port> out <ep 0-15> <hex bytes>");
                return;
            };
            match host.bulk_out(ep as u8, &buf[..n], usb_host::BULK_US) {
                Ok(()) => {
                    let _ = writeln!(uart, "ack");
                }
                Err(e) => fail(uart, e),
            }
        }
        b"tx" => match hex_bytes(args, &mut buf) {
            Some(n) => match host.raw_tx(&buf[..n]) {
                Ok(()) => {
                    let _ = writeln!(uart, "queued {}", n);
                }
                Err(e) => fail(uart, e),
            },
            None => {
                let _ = writeln!(uart, "usage: usb <port> tx <hex bytes> (raw role)");
            }
        },
        b"rx" => rx(uart, host),
        b"trace" => trace(uart, host, args),
        b"addr" => {
            let result = match parse_decimal(args).filter(|&a| a < 128) {
                Some(a) => host.set_addr(a as u8).and_then(|()| host.addr()),
                None => host.addr(),
            };
            match result {
                Ok(a) => {
                    let _ = writeln!(uart, "addr {}", a);
                }
                Err(e) => fail(uart, e),
            }
        }
        b"aoa" => match host.aoa(&usb_host::AOA_DJI) {
            Ok(id) => {
                let _ = writeln!(uart, "accessory {:04x}:{:04x} bulk in {} out {}", id.vid, id.pid,
                                 host.ep_in, host.ep_out);
            }
            Err(e) => fail(uart, e),
        },
        b"pump" => pump(uart, host, args),
        b"echo" => echo(uart, host, args),
        _ => {}
    }
}

/// `<ep 0-15> <len> [seed] [zlp]` words after the direction word.
fn pump_args(args: &[u8]) -> Option<(u8, u32, Option<u8>, bool)> {
    let mut words = args.split(|&b| b == b' ').filter(|w| !w.is_empty());
    let ep = parse_decimal(words.next()?).filter(|&e| e < 16)? as u8;
    let len = parse_decimal(words.next()?)?;
    let (mut seed, mut zlp) = (None, false);
    for word in words {
        if word == b"zlp" {
            zlp = true;
        } else {
            seed = Some(parse_decimal(word).filter(|&s| s < 256)? as u8);
        }
    }
    Some((ep, len, seed, zlp))
}

fn report(uart: &mut Uart, what: &str, run: Result<usb_host::Pump, Error>) {
    let p = match run {
        Ok(p) => p,
        Err(e) => return fail(uart, e),
    };
    let us = crate::clock::to_micros(p.ticks).max(1);
    let bytes = (p.bytes_out + p.bytes_in) as u64;
    let rate = bytes * 100 / us as u64;
    let _ = writeln!(uart, "{} out {} in {} bytes {} packets {} naks {} elapsed {} us {}.{:02} MB/s sum {:08x}",
                     what, p.bytes_out, p.bytes_in, bytes, p.packets, p.naks, us, rate / 100,
                     rate % 100, p.sum);
    if let Some((at, got, want)) = p.bad {
        let _ = writeln!(uart, "mismatch at {} got {:02x} want {:02x}", at, got, want);
    }
    if let Some(e) = p.err {
        let _ = writeln!(uart, "stopped: {}", e);
    }
}

fn pump(uart: &mut Uart, host: &mut Host, args: &[u8]) {
    let (dir, rest) = match args.iter().position(|&b| b == b' ') {
        Some(at) => (&args[..at], &args[at + 1..]),
        None => (args, &args[..0]),
    };
    match (dir, pump_args(rest)) {
        (b"out", Some((ep, len, seed, zlp))) => {
            report(uart, "pump out", host.pump_out(ep, len, seed.unwrap_or(1), zlp))
        }
        (b"in", Some((ep, len, seed, _))) => report(uart, "pump in", host.pump_in(ep, len, seed)),
        _ => {
            let _ = writeln!(uart, "usage: usb <port> pump out <ep> <len> [seed] [zlp] | in <ep> <len> [seed]");
        }
    }
}

fn echo(uart: &mut Uart, host: &mut Host, args: &[u8]) {
    match pump_args(args) {
        Some((ep, len, seed, _)) => report(uart, "echo", host.echo(ep, len, seed.unwrap_or(1))),
        None => {
            let _ = writeln!(uart, "usage: usb <port> echo <ep> <len> [seed]");
        }
    }
}

fn role(uart: &mut Uart, host: &mut Host, args: &[u8]) {
    let wanted = match args {
        b"" => None,
        b"off" => Some(usb_host::ROLE_OFF),
        b"regs" => Some(usb_host::ROLE_REGS),
        b"host" => Some(usb_host::ROLE_HOST),
        b"raw" => Some(usb_host::ROLE_RAW),
        _ => {
            let _ = writeln!(uart, "usage: usb <port> role [off|regs|host|raw]");
            return;
        }
    };
    if let Some(role) = wanted {
        if let Err(e) = host.set_role(role) {
            return fail(uart, e);
        }
    }
    match host.status() {
        Ok(s) => {
            let name = ["off", "regs", "host", "raw"][s.role as usize & 3];
            let speed = ["HS", "FS", "LS", "none"][(s.link >> 2) as usize & 3];
            let line = ["SE0", "J", "K", "SE1"][s.link as usize & 3];
            let link = if s.link >> 7 != 0 { "up" } else { "down" };
            let yes_no = |b: bool| if b { "yes" } else { "no" };
            let _ = writeln!(
                uart,
                "role {} | speed {} | line {} | link {} | switching {} | timeout {} | rx {} tx {} | raw {:02x}",
                name, speed, line, link, yes_no(s.switching), yes_no(s.timeout),
                s.rx_level, s.tx_level, s.link);
        }
        Err(e) => fail(uart, e),
    }
}

/// Drain the RX FIFO: `c:xx` is an RXCMD entry, bare `xx` a data byte.
fn rx(uart: &mut Uart, host: &mut Host) {
    let mut count = 0;
    loop {
        match host.rx_pop() {
            Ok(Some((cmd, byte))) => {
                let _ = write!(uart, "{}{:02x} ", if cmd { "c:" } else { "" }, byte);
                count += 1;
                if count == 128 {
                    break;
                }
            }
            Ok(None) => break,
            Err(e) => return fail(uart, e),
        }
    }
    let _ = writeln!(uart, "[{}]", count);
}

/// `trace <mode hex 0-f>` arms (1 incl. reset, 2 drop repeats, 4 now, 8 stop);
/// `trace dump [first] [n]` prints entries: idx delta data flags ls.
fn trace(uart: &mut Uart, host: &mut Host, args: &[u8]) {
    if let Some(rest) = args.strip_prefix(b"dump") {
        let rest = trim(rest);
        let at = rest.iter().position(|&b| b == b' ').unwrap_or(rest.len());
        let first = parse_decimal(&rest[..at]).unwrap_or(0) as u16;
        let n = parse_decimal(trim(&rest[at..])).unwrap_or(48) as u16;
        match host.trace_status() {
            Ok((state, present, count)) => {
                let _ = writeln!(uart, "trace state {} present {} entries {}", state, present as u8, count);
                for i in first..count.min(first.saturating_add(n)) {
                    match host.trace_entry(i) {
                        Ok(e) => {
                            let w = e[0] as u64 | (e[1] as u64) << 8 | (e[2] as u64) << 16
                                | (e[3] as u64) << 24 | (e[4] as u64) << 32 | (e[5] as u64) << 40;
                            let _ = writeln!(uart, "{} +{} d{:02x} f{:02x} ls{} q{:02x}", i,
                                             (w >> 18) & 0x3ffff, w & 0xff, (w >> 8) & 0xff,
                                             (w >> 16) & 3, (w >> 36) & 0x1f);
                        }
                        Err(e) => return fail(uart, e),
                    }
                }
            }
            Err(e) => fail(uart, e),
        }
        return;
    }
    if args == b"reset" {
        // Arm now (incl. reset period, drop repeats), then reset: the capture spans it.
        let t0 = crate::clock::now();
        let r = host.trace_ctrl(0x7).and_then(|()| host.reset());
        let us = crate::clock::to_micros(t0.elapsed(crate::clock::now()));
        match r {
            Ok(()) => {
                let _ = writeln!(uart, "reset done in {} us (incl. 10 ms recovery), trace armed", us);
            }
            Err(e) => fail(uart, e),
        }
        return;
    }
    match parse_hex(args) {
        Some(v) if v < 16 => match host.trace_ctrl(v as u8) {
            Ok(()) => {
                let _ = writeln!(uart, "trace mode {:x}", v);
            }
            Err(e) => fail(uart, e),
        },
        _ => {
            let _ = writeln!(uart, "usage: usb <port> trace <mode 0-f> | dump [first] [n]");
        }
    }
}
