//! `cpu` -- what the core is doing, and whether it still computes.
//!
//! `check` and `irq` were top-level verbs. Both are properties of the CPU, so
//! they read as `cpu check` and `cpu irq` -- the device-major shape the memories
//! and the power monitor already use.

use core::fmt::Write;
use core::ptr::read_volatile;

use crate::shell::parse::{parse_decimal, parse_hex, trim};
use crate::target::flash_word;
use crate::uart::Uart;
use crate::{clock, heartbeat, log, metrics, sched, target};

/// `cpu status|stats|check|irq|wedge`.
pub(crate) fn command(uart: &mut Uart, rest: &[u8]) {
    let rest = trim(rest);
    let (verb, args) = match rest.iter().position(|&byte| byte == b' ') {
        Some(at) => (&rest[..at], trim(&rest[at + 1..])),
        None => (rest, &rest[..0]),
    };
    match verb {
        b"status" => sched::heartbeat_report(uart),
        b"stats" => metrics::command(uart),
        b"irq" => {
            sched::sources(uart);
            sched::log_health(uart);
        }
        b"check" => check(uart),
        b"wedge" => wedge(uart, args),
        b"fault" => fault(uart, args),
        b"" => {
            let _ = uart.write_str("usage: cpu status|stats|check|irq|wedge|fault\n");
        }
        _ => {
            let _ = uart.write_str("unknown: try `cpu status|stats|check|irq|wedge|fault`\n");
        }
    }
}

/// The address `cpu fault` reads when none is given.
///
/// Inside the CPU's `f0000000+10000000` I/O region -- so the core issues the
/// cycle rather than trapping on the PMA -- and inside no decoder window on
/// either variant: the board block ends at f0000680 and the BIST engine, when
/// present, starts at f0000800.
const FAULT_ADDR: u32 = 0xf000_0700;

/// `cpu fault [hex]` -- read an address nothing decodes, on purpose.
///
/// **The negative control for #409.** Before the gateware answered ERR for an
/// unclaimed address, this command did not print its second line and the shell
/// never came back: no ack, no err, the core stalled in the load. That is the
/// defect, and it is the only way to show the fix is a fix.
fn fault(uart: &mut Uart, args: &[u8]) {
    let addr = match parse_hex(args) {
        Some(value) => value & !3,
        None if args.is_empty() => FAULT_ADDR,
        None => {
            let _ = writeln!(uart, "usage: cpu fault [hex address, default {:08x}]", FAULT_ADDR);
            return;
        }
    };
    let _ = writeln!(uart, "reading {:08x}; the bus must fault, not hang", addr);

    // SAFETY: a 32-bit aligned volatile read of an I/O address. It is expected
    // to fault, and the fault is the point -- `src/fault.rs` reports it and
    // steps over this instruction.
    let word = unsafe { read_volatile(addr as *const u32) };

    let _ = writeln!(
        uart,
        "  {:08x} answered {:08x} -- {} trap(s) taken",
        addr,
        word,
        crate::fault::taken()
    );

    // The FABRIC's account beside the CPU's, which is what makes this a control
    // rather than a demonstration: `unclaimed` moving proves the terminator
    // fired, and not that the firmware happened to print a line.
    if let Some(id) = crate::info::fabric::status() {
        let _ = writeln!(
            uart,
            "  bus  {} unclaimed, {} timed out, worst wait {} cycles",
            id.bus_fault & 0xff,
            (id.bus_fault >> 8) & 0xff,
            id.bus_fault >> 16
        );
    }
}

/// How long `cpu wedge` will hold the scheduler, in milliseconds.
///
///   waits for   nothing -- a fixed interval, spun out against `rdtime`, which
///               counts `sync` cycles in the fabric and is unaffected by
///               `mstatus.MIE`
///   duration    the argument, default 400 ms = four heartbeat periods, so the
///               lamp misses four toggles rather than one marginal one
///   ceiling     2000 ms, twenty periods, so a typo cannot strand the board
///   on expiry   interrupts are restored and the samples below are printed
const WEDGE_DEFAULT_MS: u32 = 4 * sched::HEARTBEAT_PERIOD_MS;
const WEDGE_MAX_MS: u32 = 2_000;

/// `cpu wedge [ms]` -- stop the scheduler on purpose, and watch the lamp stop.
///
/// **The negative control, and the deliverable of #411.** A blink nobody has
/// seen stop is not evidence of anything; five of this board's six lamps were
/// latches that could not go dark, which is why a wedged board and a healthy one
/// looked identical.
///
/// It is a REAL stall, not a simulated one: `mstatus.MIE` cleared, so the CLINT
/// does not deliver, `tick` does not run, nothing is pended, the SLIC dispatches
/// nothing and the task does not toggle. Bounded and self-recovering.
///
/// **It carries its own control.** The same interval is sampled twice -- once
/// with the scheduler running and once without -- and the run only passes if the
/// lamp moved in the first and did not move in the second. A test that only
/// watches the lamp stop cannot tell "the scheduler stopped" from "this sampler
/// is broken".
///
/// The samples are taken BY THE STALLED CPU. The GPIO Input register is fed from
/// the value on the LED net, so this is the pin rather than a copy of what
/// firmware last wrote -- and it is the only way the evidence reaches a
/// transcript, since nobody reading one can see the board.
fn wedge(uart: &mut Uart, args: &[u8]) {
    if target::BOARD.is_none() {
        return crate::shell::console::board_absent(uart);
    }
    let millis = match parse_decimal(args) {
        Some(value) if value > WEDGE_MAX_MS => {
            let _ = writeln!(uart, "at most {} ms", WEDGE_MAX_MS);
            return;
        }
        Some(value) => value,
        None if args.is_empty() => WEDGE_DEFAULT_MS,
        None => {
            let _ = writeln!(uart, "usage: cpu wedge [ms, up to {}]", WEDGE_MAX_MS);
            return;
        }
    };
    let window = clock::millis(millis);

    // THE CONTROL, first and with everything running: the lamp must move.
    let healthy = transitions(window);

    // Then the same interval with the scheduler stopped.
    // SAFETY: interrupts are re-enabled unconditionally at the end of the block,
    // after a spin bounded by `rdtime`. Nothing inside can block or fault.
    let stalled = unsafe {
        riscv::interrupt::disable();
        let seen = transitions(window);
        riscv::interrupt::enable();
        seen
    };

    let _ = writeln!(
        uart,
        "cpu wedge {} ms  ({} heartbeat periods)",
        millis,
        millis / sched::HEARTBEAT_PERIOD_MS
    );
    let _ = writeln!(
        uart,
        "  running   {} lamp transition(s)  <- the control",
        healthy
    );
    let _ = writeln!(
        uart,
        "  stalled   {} lamp transition(s)  \
                            interrupts off: no tick, no dispatch",
        stalled
    );
    let _ = writeln!(
        uart,
        "  verdict   {}",
        if healthy > 0 && stalled == 0 {
            "PASS -- the lamp blinks while the scheduler runs and stops when it \
             does not"
        } else if healthy == 0 {
            "FAIL -- the lamp did not move even with the scheduler running; the \
             blink is not there to be watched"
        } else {
            "FAIL -- the lamp kept moving with interrupts off, so it is not \
             reporting the scheduler"
        }
    );
}

/// How many times the lamp changed state over `window` counter ticks.
///
/// Counting transitions rather than sampling two levels: a level pair can miss a
/// whole blink period and read as frozen, and it cannot tell a stopped lamp from
/// one caught at the same phase twice.
fn transitions(window: u32) -> u32 {
    let began = clock::now();
    let mut last = heartbeat::lit();
    let mut seen = 0;
    while began.elapsed(clock::now()) < window {
        let now = heartbeat::lit();
        if now != last {
            seen += 1;
            last = now;
        }
    }
    seen
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
