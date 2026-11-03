//! `hr` -- the HyperRAM peripheral (#588): status, calibration, tests, bytes, errors.
//!
//! Register map and codes: `docs/chips/hyperram/soc-peripheral.md`. Every wait is
//! bounded in `hyperram::backend`; a timeout prints as code `ff`.

use core::fmt::Write;

use crate::shell::parse::{parse_hex, trim};
use crate::uart::Uart;

#[cfg(not(feature = "qemu"))]
use crate::hyperram::backend as hw;
#[cfg(not(feature = "qemu"))]
use cynthion_soc_pac::hyperram::offset;

/// Sticky error bits, in `ERRORS` order (`peripherals/hyperram.py`).
pub(crate) const ERROR_NAMES: [&str; 13] = [
    "op", "wdog", "underflow", "stream_ovf", "stream_unf", "handshake", "pll_lost",
    "pll_nolock", "ck_bad", "ck_dead", "cal_none", "reg_mismatch", "check",
];

/// Operation codes, by value; unnamed values print as hex.
fn code_name(code: u8) -> &'static str {
    match code {
        0 => "ok",
        1 => "busy",
        2 => "arg",
        3 => "not-ready",
        4 => "uncalibrated",
        7 => "watchdog",
        8 => "handshake",
        9 => "reg-mismatch",
        10 => "check",
        11 => "abort",
        12 => "no-clock",
        13 => "no-clean-tap",
        0xff => "timeout",
        _ => "?",
    }
}

fn words(rest: &[u8]) -> ([&[u8]; 6], usize) {
    let mut out: [&[u8]; 6] = [&[]; 6];
    let mut n = 0;
    for w in rest.split(|&c| c == b' ').filter(|w| !w.is_empty()) {
        if n == out.len() {
            break;
        }
        out[n] = w;
        n += 1;
    }
    (out, n)
}

#[cfg(feature = "qemu")]
pub(crate) fn command(uart: &mut Uart, _rest: &[u8]) {
    let _ = writeln!(uart, "hr: no HyperRAM peripheral on this target");
}

#[cfg(not(feature = "qemu"))]
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    let (w, n) = words(trim(rest));
    if n == 0 {
        return crate::shell::list_family(uart, "hr");
    }
    let arg = |i: usize| if i < n { parse_hex(w[i]) } else { None };
    match w[0] {
        b"status" => status(uart),
        b"calibrate" => {
            let code = hw::run(hw::OP_CALIBRATE, hw::CAL_TICKS);
            report(uart, "calibrate", code);
            calibration(uart);
        }
        b"test" => {
            let mode = match if n > 1 { w[1] } else { b"wr" } {
                b"w" => 1,
                b"r" => 2,
                b"wr" => 3,
                _ => {
                    let _ = writeln!(uart, "usage: hr test [w|r|wr] [bytes] [addr] [seed]");
                    return;
                }
            };
            test(uart, mode, arg(2).unwrap_or(0x80_0000), arg(3).unwrap_or(0),
                 arg(4).unwrap_or(0x5eed) as u16, 0);
        }
        b"fill" => match (arg(1), arg(2)) {
            (Some(addr), Some(len)) => {
                let mode = match if n > 3 { w[3] } else { b"const" } {
                    b"ramp" => 1,
                    b"pat" => 2,
                    _ => 0,
                };
                hw::w32(offset::HR_ADDR, addr);
                hw::w32(offset::LEN, len);
                hw::w8(offset::MODE, mode);
                hw::w32(offset::FILL_VALUE, arg(4).unwrap_or(0));
                let code = hw::run(hw::OP_FILL, hw::op_ticks(len));
                report(uart, "fill", code);
                counters(uart, len);
            }
            _ => {
                let _ = writeln!(uart, "usage: hr fill <addr> <len> [const|ramp|pat] [value]");
            }
        },
        b"read" => match arg(1) {
            Some(addr) => {
                let len = arg(2).unwrap_or(16).clamp(1, 256);
                if !hw::copy(hw::OP_COPY_IN, addr, 0, len) {
                    return report(uart, "read", ((hw::status() >> 8) & 0xf) as u8);
                }
                for i in 0..len {
                    if i % 16 == 0 {
                        let _ = write!(uart, "{}{:06x}:", if i == 0 { "" } else { "\n" }, addr + i);
                    }
                    let _ = write!(uart, " {:02x}", hw::win_get(i));
                }
                let _ = writeln!(uart);
            }
            None => {
                let _ = writeln!(uart, "usage: hr read <addr> [len]");
            }
        },
        b"write" => match arg(1) {
            Some(addr) if n > 2 => {
                let mut count = 0;
                for i in 2..n {
                    match parse_hex(w[i]) {
                        Some(b) if b <= 0xff => {
                            hw::win_put(count, b as u8);
                            count += 1;
                        }
                        _ => {
                            let _ = writeln!(uart, "bytes are hex 00..ff");
                            return;
                        }
                    }
                }
                let ok = hw::copy(hw::OP_COPY_OUT, addr, 0, count);
                let code = if ok { 0 } else { ((hw::status() >> 8) & 0xf) as u8 };
                report(uart, "write", code);
            }
            _ => {
                let _ = writeln!(uart, "usage: hr write <addr> <byte> [byte...]");
            }
        },
        b"reg" => {
            let sel = match if n > 1 { w[1] } else { b"cr0" } {
                b"id0" => 0,
                b"id1" => 1,
                b"cr0" => 2,
                b"cr1" => 3,
                _ => {
                    let _ = writeln!(uart, "usage: hr reg <id0|id1|cr0|cr1> [value]");
                    return;
                }
            };
            hw::w8(offset::REG_SEL, sel);
            let code = match arg(2) {
                Some(value) => {
                    hw::w16(offset::REG_WDATA, value as u16);
                    hw::run(hw::OP_REG_WRITE, hw::op_ticks(4))
                }
                None => hw::run(hw::OP_REG_READ, hw::op_ticks(2)),
            };
            report(uart, "reg", code);
            let _ = writeln!(uart, "  value {:04x}", hw::r16(offset::REG_RDATA));
        }
        b"delay" => match arg(1) {
            Some(tap) if tap < 128 => {
                hw::w8(offset::DELAY, tap as u8);
                let code = hw::run(hw::OP_SET_DELAY, hw::op_ticks(2) + 1500);
                report(uart, "delay", code);
            }
            _ => {
                let _ = writeln!(uart, "usage: hr delay <tap 0..7f>");
            }
        },
        b"errors" => errors(uart, true),
        b"inject" => match if n > 1 { w[1] } else { b"" } {
            b"hang" => {
                // The engine ignores `done`; the transaction watchdog must fire.
                hw::w32(offset::HR_ADDR, 0x7f_0000);
                hw::w32(offset::LEN, 64);
                hw::w8(offset::MODE, 0x10 | 2);
                let code = hw::run(hw::OP_FILL, hw::op_ticks(64));
                hw::w8(offset::MODE, 0);
                report(uart, "inject hang", code);
                let _ = writeln!(uart, "  state {:04x}", hw::r16(offset::STATE));
                errors(uart, true);
            }
            b"xor" => {
                // A clean write, then the same read with the checker's want inverted:
                // every word must fail, or the checker cannot see a failure.
                test(uart, 1, 0x1000, 0x7f_0000, 0x5eed, 0);
                test(uart, 2, 0x1000, 0x7f_0000, 0x5eed, 1);
            }
            _ => {
                let _ = writeln!(uart, "usage: hr inject <hang|xor>");
            }
        },
        b"abort" => {
            hw::w8(offset::CTRL, 1);
            let _ = writeln!(uart, "abort sent; engine and core reset, calibration reruns");
        }
        _ => crate::shell::list_family(uart, "hr"),
    }
}

#[cfg(not(feature = "qemu"))]
fn report(uart: &mut Uart, what: &str, code: u8) {
    let _ = writeln!(uart, "{} {} (code {:x})", what, code_name(code), code);
}

#[cfg(not(feature = "qemu"))]
fn status(uart: &mut Uart) {
    let s = hw::status();
    let ck_khz = hw::r32(offset::CK_KHZ);
    let count = hw::r32(offset::CK_COUNT);
    // CK_COUNT counts hr cycles over 2^19 cycles of the 60 MHz oscillator.
    let khz = (count as u64 * 60_000) >> 19;
    let _ = writeln!(
        uart,
        "hyperram id {:08x} ck {} kHz requested, {} kHz measured\n  \
         ready {} locked {} calibrated {} busy {} hr_busy {}  last op {} {}",
        hw::r32(offset::ID), ck_khz, khz, (s >> 1) & 1, (s >> 2) & 1, (s >> 3) & 1,
        s & 1, (s >> 5) & 1, s >> 12, code_name(((s >> 8) & 0xf) as u8)
    );
    calibration(uart);
    errors(uart, false);
}

#[cfg(not(feature = "qemu"))]
fn calibration(uart: &mut Uart) {
    let cal = hw::r32(offset::CAL);
    let _ = write!(uart, "  cal tap {} width {} valid {}\n  taps ", cal & 0x7f,
                   (cal >> 8) & 0xff, (cal >> 16) & 1);
    for i in 0..4 {
        let map = hw::r32(offset::CAL_MAP0 + 4 * i);
        for bit in 0..32 {
            let _ = uart.write_str(if map >> bit & 1 == 1 { "#" } else { "." });
        }
    }
    let _ = writeln!(uart);
}

#[cfg(not(feature = "qemu"))]
fn errors(uart: &mut Uart, clear: bool) {
    let e = hw::r16(offset::ERRORS);
    let _ = write!(uart, "  errors {:04x}", e);
    for (bit, name) in ERROR_NAMES.iter().enumerate() {
        if e >> bit & 1 == 1 {
            let _ = write!(uart, " {}", name);
        }
    }
    let _ = writeln!(uart);
    if clear {
        hw::w16(offset::ERRORS, e);
        hw::w8(offset::EVENTS, 3);
    }
}

#[cfg(not(feature = "qemu"))]
fn counters(uart: &mut Uart, bytes: u32) {
    let cycles = hw::r32(offset::CYCLES);
    let ck_khz = hw::r32(offset::CK_KHZ) as u64;
    // bytes per hr cycle x CK = bytes/s; tenths of MB/s
    let tenths = if cycles == 0 { 0 } else { bytes as u64 * ck_khz / cycles as u64 / 100 };
    let _ = writeln!(
        uart,
        "  words {} xacts {} cycles {} = {}.{} MB/s",
        hw::r32(offset::WORDS), hw::r32(offset::XACTS), cycles, tenths / 10, tenths % 10
    );
}

/// TEST over [addr, addr+bytes): `mode` 1 write, 2 read+check, 3 both per burst.
#[cfg(not(feature = "qemu"))]
fn test(uart: &mut Uart, mode: u8, bytes: u32, addr: u32, seed: u16, xor: u16) {
    hw::w32(offset::HR_ADDR, addr);
    hw::w32(offset::LEN, bytes);
    hw::w8(offset::MODE, mode << 2);
    hw::w16(offset::SEED, seed);
    hw::w16(offset::XOR, xor);
    hw::w16(offset::BURST, 0);
    let moved = if mode == 3 { 2 * bytes } else { bytes };
    let code = hw::run(hw::OP_TEST, hw::op_ticks(moved));
    hw::w16(offset::XOR, 0);
    report(uart, "test", code);
    counters(uart, moved);
    let _ = writeln!(
        uart,
        "  checked {} errors {} missing {} first-bad {:08x} got/want {:08x} bits {:04x}",
        hw::r32(offset::CHECKED), hw::r32(offset::ERRS), hw::r32(offset::MISSING),
        hw::r32(offset::BAD_INDEX), hw::r32(offset::BAD_GOT_WANT),
        hw::r16(offset::BAD_BITS)
    );
}
