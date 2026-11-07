//! `pd target_c [status|caps|log]`: syntax only; `crate::pd` holds the logic.

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
        b"log" => pd.dump_log(uart),
        _ => {
            let _ = writeln!(uart, "usage: pd target_c [status|caps|log]");
        }
    }
}
