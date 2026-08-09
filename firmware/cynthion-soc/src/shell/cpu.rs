//! `cpu` -- what the core is doing, and whether it still computes.
//!
//! `check` and `irq` were top-level verbs. Both are properties of the CPU, so
//! they read as `cpu check` and `cpu irq` -- the device-major shape the memories
//! and the power monitor already use.

use core::fmt::Write;
use core::ptr::read_volatile;

use crate::target::flash_word;
use crate::uart::Uart;
use crate::{log, metrics, sched};

/// `cpu stats|check|irq`.
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    match rest {
        b"stats" => metrics::command(uart),
        b"irq" => {
            sched::sources(uart);
            sched::log_health(uart);
        }
        b"check" => check(uart),
        b"" => {
            let _ = uart.write_str("usage: cpu stats|check|irq\n");
        }
        _ => {
            let _ = uart.write_str("unknown: try `cpu stats|check|irq`\n");
        }
    }
}

/// The bring-up smoke test: does this CPU compute, can it reach flash, does the
/// clock formatter hold at its boundaries.
fn check(uart: &mut Uart) {

            let a: u32 = 0x1234_5678;
            let b: u32 = 0x9abc_def0;
            // SAFETY: our own stack slots. `read_volatile` is what makes this a
            // measurement of the CPU: without it the compiler folds both
            // operations at build time and the command proves nothing about the
            // silicon it is running on.
            let (a, b) = unsafe { (read_volatile(&a), read_volatile(&b)) };
            let sum = a.wrapping_add(b);
            let prod = a.wrapping_mul(3);
            let f0 = flash_word(0);
            let f40 = flash_word(0x40);

            let _ = writeln!(
                uart,
                "sum   {:08x} {}",
                sum,
                if sum == 0xacf1_3568 { "ok" } else { "BAD" }
            );
            let _ = writeln!(
                uart,
                "prod  {:08x} {}",
                prod,
                if prod == 0x369d_0368 { "ok" } else { "BAD" }
            );
            let _ = writeln!(
                uart,
                "@0    {:08x} {}",
                f0,
                if f0 == 0x6150_00ff { "ok" } else { "BAD" }
            );
            let _ = writeln!(
                uart,
                "@40   {:08x} {}",
                f40,
                if f40 == 0x2a55_8800 { "ok" } else { "BAD" }
            );

            // The timestamp format, at the values where it can go wrong.
            //
            //   0              zero pads to the full width, not "0.0"
            //   1              the milliseconds field pads, not "000000.1"
            //   999            the last value before a carry into seconds
            //   1_000          the carry itself
            //   61_000         two digits of seconds, still six columns wide
            //   999_999_999    the largest the six-digit field can hold
            //   1_000_000_000  one past it -- wraps the column, does not widen it
            //
            // The last is the one worth having: without the modulo in
            // `log::Stamp`, a machine up for 11.57 days starts printing a
            // seven-digit field and every line after it is misaligned.
            //
            // PRINTED rather than compared here, and `scripts/soc_test.py` holds
            // the expected string. Comparing in firmware needed a `core::fmt`
            // sink over a byte slice and seven `&str`s to check against, and
            // this build has 32 KiB for everything -- the same reason the `sum`
            // and `prod` values above are asserted by the test rather than by
            // the shell. What the firmware must supply is the bytes its own
            // formatter produces, and that is exactly what this is.
            let _ = write!(uart, "stamp");
            for millis in [0u32, 1, 999, 1_000, 61_000, 999_999_999, 1_000_000_000] {
                let _ = write!(uart, " {}", log::Stamp::at(millis));
            }
            let _ = writeln!(uart);
        }
