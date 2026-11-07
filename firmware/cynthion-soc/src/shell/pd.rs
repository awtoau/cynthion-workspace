//! `pd target_c [status|caps|log|regs]`: syntax only; `crate::pd` holds the logic.

use core::fmt::Write;

use crate::shell::parse::trim;
use crate::uart::Uart;
use crate::Devices;

pub(crate) fn command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    let rest = trim(rest);
    if rest.is_empty() {
        return crate::shell::list_family(uart, "pd");
    }
    let (port, verb) = match rest.iter().position(|&b| b == b' ') {
        Some(at) => (&rest[..at], trim(&rest[at + 1..])),
        None => (rest, &rest[..0]),
    };
    if port != b"target_c" {
        let _ = writeln!(uart, "only target_c has a PD controller we drive");
        return;
    }
    let pd = &devices.type_c.pd;
    match verb {
        b"" | b"status" => pd.status(uart),
        b"caps" => pd.caps(uart),
        b"probe" | b"probe 1" | b"probe 2" | b"probe off" => {
            let level = match verb {
                b"probe off" => 0,
                b"probe 2" => 2,
                _ => 1,
            };
            devices.type_c.pd.set_probe(level);
            let _ = writeln!(uart, "probe {} (next power on)", level);
        }
        b"live" => {
            devices.type_c.pd.live = !devices.type_c.pd.live;
            let _ = writeln!(uart, "live {}", devices.type_c.pd.live as u8);
        }
        b"log" => pd.dump_log(uart),
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
        _ => {
            let _ = writeln!(uart, "usage: pd target_c [status|caps|log]");
        }
    }
}
