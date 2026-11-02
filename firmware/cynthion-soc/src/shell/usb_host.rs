//! `usb <port> role|reset|desc|enum|ctrl|setup|in|out|tx|rx|addr|aoa` -- syntax over `crate::usb_host`.
//!
//! No transfer logic here: parse, call the driver, print its `Error` as it displays.

use core::fmt::Write;

use crate::shell::parse::{parse_decimal, parse_hex, trim};
use crate::uart::Uart;
use crate::usb_host::{self, Error, Host};

/// Verbs this module owns; `usb.rs` routes on them.
pub(crate) const VERBS: [&[u8]; 13] = [
    b"trace", b"role", b"reset", b"desc", b"enum", b"ctrl", b"setup", b"in", b"out", b"tx", b"rx", b"addr", b"aoa",
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
        b"enum" => match parse_decimal(args).filter(|&a| (1..128).contains(&a)) {
            Some(a) => match host.enumerate(a as u8) {
                Ok(id) => {
                    let _ = writeln!(uart, "{:04x}:{:04x}", id.vid, id.pid);
                }
                Err(e) => fail(uart, e),
            },
            None => {
                let _ = writeln!(uart, "usage: usb <port> enum <addr 1-127>");
            }
        },
        // SETUP (8 bytes) then optional OUT data (up to 56 bytes); IN data prints like `in`.
        b"ctrl" => match hex_bytes(args, &mut buf) {
            Some(n) if n >= 8 => {
                let mut packet = [0u8; 8];
                packet.copy_from_slice(&buf[..8]);
                let mut out = [0u8; 56];
                out[..n - 8].copy_from_slice(&buf[8..n]);
                let mut data = [0u8; 64];
                match host.control(&packet, &out[..n - 8], &mut data) {
                    Ok(got) if packet[0] & 0x80 != 0 => {
                        let _ = writeln!(uart, "{} bytes", got);
                        dump(uart, &data[..got]);
                    }
                    Ok(_) => {
                        let _ = writeln!(uart, "ack");
                    }
                    Err(e) => fail(uart, e),
                }
            }
            _ => {
                let _ = writeln!(uart, "usage: usb <port> ctrl <8 setup bytes> [out bytes]");
            }
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
        _ => {}
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
            let speed = ["HS", "FS", "LS", "?"][(s.link >> 2) as usize & 3];
            let _ = writeln!(
                uart,
                "role {}  switching {}  timeout {}  link {:02x} ls {} {} up {} rx {} tx {}",
                name, s.switching as u8, s.timeout as u8, s.link, s.link & 3, speed,
                (s.link >> 7) as u8, s.rx_level, s.tx_level);
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
