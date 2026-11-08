//! `pd target_c ...`: syntax only; `crate::pd` holds the logic.

use core::fmt::Write;

use crate::pd::Refusal;
use crate::pd_decode as dec;
use crate::shell::parse::{parse_hex, trim};
use crate::uart::Uart;
use crate::Devices;

const USAGE: &str = "usage: pd target_c status|caps|log|live|regs|list\n       \
    pd proxy on confirm|off|status|log\n       \
    pd target_c send <name>[!] [svid] [--force] [--illegal]\n       \
    pd target_c raw <hex> [sop|sop1|sop2]";

fn words<'a>(text: &'a [u8], out: &mut [&'a [u8]; 6]) -> usize {
    let mut n = 0;
    for word in text.split(|&b| b == b' ').filter(|w| !w.is_empty()) {
        if n < out.len() {
            out[n] = word;
            n += 1;
        }
    }
    n
}

pub(crate) fn command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    let rest = trim(rest);
    if rest.is_empty() {
        return crate::shell::list_family(uart, "pd");
    }
    let mut w: [&[u8]; 6] = [&[]; 6];
    let n = words(rest, &mut w);
    if w[0] == b"proxy" {
        return proxy(uart, &w[1..n], devices);
    }
    if w[0] != b"target_c" {
        let _ = writeln!(uart, "only target_c has a PD controller we drive");
        return;
    }
    let verb = if n > 1 { w[1] } else { &b"status"[..] };
    match verb {
        b"status" => devices.type_c.pd.status(uart),
        b"caps" => devices.type_c.pd.caps(uart),
        b"live" => {
            devices.type_c.pd.live = !devices.type_c.pd.live;
            let _ = writeln!(uart, "live {}", devices.type_c.pd.live as u8);
        }
        b"log" => devices.type_c.pd.dump_log(uart),
        b"list" => {
            for v in crate::pd_verbs::VERBS {
                let _ = writeln!(
                    uart,
                    "{}{}{}",
                    v.name,
                    if v.destructive { " !" } else { "" },
                    if v.illegal { " (illegal for a source)" } else { "" }
                );
            }
        }
        b"regs" => {
            if let Some(bus) = devices.bus.as_mut() {
                let mut r = [0u8; 26];
                match crate::fusb302::pd_registers(bus, &mut r) {
                    Ok(()) => {
                        let _ = writeln!(
                            uart,
                            "01..0f {:02x?}\n3c {:02x} 3d {:02x} 40 {:02x} 41 {:02x}",
                            &r[..15], r[15], r[16], r[17], r[18]
                        );
                    }
                    Err(error) => {
                        let _ = writeln!(uart, "{}", error.as_str());
                    }
                }
            }
        }
        b"send" | b"raw" if n > 2 => send(uart, devices, &w[..n]),
        _ => {
            let _ = writeln!(uart, "{}", USAGE);
        }
    }
}

fn proxy(uart: &mut Uart, w: &[&[u8]], devices: &mut Devices) {
    let pd = &mut devices.type_c.pd;
    match w.first().copied().unwrap_or(&b"status"[..]) {
        b"on" => {
            let Some(bus) = devices.bus.as_mut() else {
                let _ = writeln!(uart, "no I2C bus");
                return;
            };
            let confirmed = w.get(1).copied() == Some(&b"confirm"[..]);
            match pd.proxy_on(bus, confirmed) {
                Ok(()) => {
                    let _ = writeln!(uart, "pd proxy on");
                }
                Err(why) => {
                    let _ = writeln!(uart, "pd proxy refused: {}", why);
                }
            }
        }
        b"off" => {
            if let Some(bus) = devices.bus.as_mut() {
                pd.proxy_off(bus, &mut devices.power);
            }
            let _ = writeln!(uart, "pd proxy off");
        }
        b"status" => pd.bridge.status(uart),
        b"log" => pd.dump_proxy_log(uart),
        _ => {
            let _ = writeln!(uart, "{}", USAGE);
        }
    }
}

fn send(uart: &mut Uart, devices: &mut Devices, w: &[&[u8]]) {
    let Some(bus) = devices.bus.as_mut() else {
        let _ = writeln!(uart, "no I2C bus");
        return;
    };
    let pd = &mut devices.type_c.pd;
    let power = &mut devices.power;
    let mut force = false;
    let mut illegal = false;
    let mut name = w[2];
    if name.ends_with(b"!") {
        force = true;
        name = &name[..name.len() - 1];
    }
    let mut arg = None;
    let mut sop = dec::SOP;
    for flag in &w[3..] {
        match *flag {
            b"--force" => force = true,
            b"--illegal" => illegal = true,
            b"sop" => sop = dec::SOP,
            b"sop1" => sop = dec::SOP_PRIME,
            b"sop2" => sop = dec::SOP_DOUBLE_PRIME,
            other => arg = parse_hex(other).map(|v| v as u16),
        }
    }
    let result = if w[1] == b"raw" {
        let mut bytes = [0u8; 2 + dec::MAX_PAYLOAD];
        match crate::pd_verbs::parse_hex(name, &mut bytes) {
            Some(len) => pd.send_raw(uart, bus, power, &bytes[..len], sop),
            None => Err(Refusal::Unknown),
        }
    } else {
        let name = core::str::from_utf8(name).unwrap_or("");
        pd.send_named(uart, bus, power, name, arg, force, illegal)
    };
    if let Err(refusal) = result {
        let why = match refusal {
            Refusal::Unknown => "unknown message name or bad hex (pd target_c list)",
            Refusal::NeedsForce => "disruptive: add ! to the name or --force",
            Refusal::NeedsIllegal => "a source shall not send this: add --illegal",
            Refusal::NotRunning => "PD session not running (usb target_c power on)",
            Refusal::Busy => "exchange did not complete",
        };
        let _ = writeln!(uart, "pdx refused {}", why);
    }
}
