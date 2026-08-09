//! The command table and the dispatcher.
//!
//! Moved out of `main.rs` unchanged (#296).
//!
//! `run` is 23 KB in one function -- the largest in the binary by an order of
//! magnitude -- because it is 74 string comparisons in one match. Splitting
//! dispatch per family so a command pulls in only its own code is where a
//! `.text` win would come from, and `.text` is this design's binding constraint.
//! That is a measurement, not a move, and is deliberately not done here.

use core::fmt::Write;
use core::ptr::read_volatile;

use crate::parse::{parse_decimal, parse_hex, trim};
use crate::uart::Uart;
use crate::target::flash_word;
use crate::{
    bench, board, board_absent, led_cmd, sideband_cmd, vbus_cmd, clock, events, hardware,
    hr_cmd, i2c_cmd, info, log, memory, metrics, phy_cmd, power_cmd, reboot,
    sched, scratch_responds, staging, selftest, target, timer, typec, Devices,
};

/// Every command, with its argument syntax and what it does.
///
/// A table rather than one long string, for three reasons that the string version
/// demonstrated by failing at all of them. It could not show a command's
/// ARGUMENTS, so `read`, `bench`, `log` and `load` all appeared to take none and
/// there was nowhere to learn otherwise. It could not be sorted, so the order was
/// whatever the match arms happened to be in. And it drifted: `vbus` and `hrtest`
/// were dispatchable and unlisted, while the listing is the only place anyone
/// looks.
///
/// **Kept in alphabetical order**, which is the order it prints -- sorting at
/// runtime would cost code to save nothing, since the table is a constant.
///
/// Two columns, and the first is padded to `HELP_WIDTH` so no name runs into its
/// description. `{:w$}` would pull in `core::fmt`'s width machinery for one call
/// site; the padding is done by hand below for the same reason the rest of this
/// firmware avoids it.
pub(crate) const HELP: &[(&str, &str)] = &[
    ("bench [region]", "time bram, flash or hyperram"),
    ("board", "every connector, rail and controller"),
    ("bram read <hex>", "one word of block RAM"),
    (
        "check",
        "smoke test: CPU add/multiply, flash reads, time format",
    ),
    ("cpu stats", "cycles, instructions, busy fraction"),
    ("flash id", "the first flash word, and the size"),
    ("flash read <hex>", "one word of flash, by offset"),
    ("help, ?", "this list"),
    ("hr <cmd>", "hyperram: see `hr`"),
    ("hyperram read <hex>", "one word over the staging port"),
    ("i2c [bus]", "scan a bus behind the mux"),
    ("i2c soak <bus> <prer> <n>", "hammer one bus at one rate, count failures"),
    ("info", "image, memory, boot, cpu, gateware"),
    ("irq", "interrupt counts, per source"),
    ("led [n]", "the six LEDs"),
    ("load <hex>", "stage <hex> bytes of firmware, then boot it"),
    ("log [n|tags]", "the deferred event log"),
    ("map", "every peripheral window, from the generated map"),
    ("phy", "the USB PHYs"),
    ("phy reset", "pulse TARGET's RESETB, and prove it reached"),
    ("pmod", "connector pins: ball, resource, free or claimed"),
    ("ports", "which UARTs answer"),
    ("power [floor]", "the four PAC1954 channels"),
    ("power alert", "the limit ALERTs: armed, routed, fired"),
    ("power limit <k> <port> <n>", "ov/oc/uv/uc threshold, in mV or mA"),
    ("power samples <k> <port> <n>", "consecutive samples before it asserts"),
    ("power bracket <port> <mA> <mV>", "limits around the present reading"),
    ("reset", "jump to the reset vector"),
    ("rtic", "the dispatcher: model, task jitter, stalls"),
    ("selftest", "run every self-check"),
    ("sideband", "the sideband link"),
    ("time", "uptime, from mtime"),
    ("typec [port]", "the FUSB302B controllers"),
    ("vbus <cmd>", "the VBUS distribution switches"),
];

/// Width of the first column. One more than the longest entry above, so every
/// description starts in the same place and none of them touch the name.
pub(crate) const HELP_WIDTH: usize = 20;

pub(crate) fn help(uart: &mut Uart) {
    for (name, summary) in HELP {
        let _ = uart.write_str("  ");
        let _ = uart.write_str(name);
        // Pad by hand. `write!("{:w$}")` instantiates core::fmt's fill-and-align
        // path, which is several hundred bytes of code for one call site in an
        // image that has spent this session fighting for block RAM.
        for _ in name.len()..HELP_WIDTH {
            let _ = uart.write_str(" ");
        }
        let _ = uart.write_str(summary);
        let _ = uart.write_str("\n");
    }
}

/// Dispatch one command line.
///
/// `index` is which console this arrived on, needed by `load` so a transfer reads from the
/// right receive ring.
pub(crate) fn run(index: usize, uart: &mut Uart, line: &[u8], devices: &mut Devices) {
    // Split off the first word; the rest is the argument.
    let (cmd, rest) = match line.iter().position(|&b| b == b' ') {
        Some(i) => (&line[..i], &line[i + 1..]),
        None => (line, &line[..0]),
    };

    match cmd {
        b"help" | b"?" => help(uart),
        b"ports" => {
            // Answers "is the second UART actually there" without a bitstream rebuild.
            // SCR is eight bits of scratch that do nothing else, so writing a pattern
            // and reading it back distinguishes a peripheral that exists from an address
            // that decodes to nothing -- which on this bus returns zeros rather than
            // faulting, and so is otherwise invisible.
            for (index, &base) in target::UART_BASES.iter().enumerate() {
                let present = scratch_responds(base);
                let _ = writeln!(
                    uart,
                    "  {} {:08x} {}",
                    index,
                    base,
                    if present { "ok" } else { "NO RESPONSE" }
                );
            }
        }
        b"irq" => {
            // The evidence that this shell is interrupt-driven and not quietly polling.
            //
            // A count that climbs as you type is the whole proof: the byte reached the
            // handler, the handler reached the ring, and the shell reached the ring. If
            // the interrupt path were broken there would be nothing to read here and no
            // prompt to type it at, so the useful failure is the subtler one -- a count
            // that stays at zero for the *other* console, or `pending` stuck with a bit
            // set, which is a claim that was never completed.
            //
            // The PLIC block itself is rendered by `src/sched.rs`, because the
            // `rtic` command prints the same counters and two renderers would
            // eventually answer the same question in two formats that could not
            // be diffed.
            sched::sources(uart);
            sched::log_health(uart);
        }
        // Which dispatcher this image was built with, and what it is achieving:
        // the #115 comparison, on the shipping firmware rather than on a
        // synthetic workload. See `src/sched.rs`.
        b"rtic" => sched::command(uart),
        b"time" => {
            // The tick, and the evidence that it is a tick rather than a
            // counter someone reads.
            //
            // `uptime` comes from the tick handler's own count; `counter` comes
            // from `rdtime`, which nothing periodic touches. **The two are
            // independent measurements of the same interval**, so agreement is
            // the whole assertion: a tick that stopped, or that is firing at
            // the wrong rate, shows up as the two diverging, and nothing else
            // in this shell can distinguish those from a slow clock.
            //
            // `cost` is the worst time the handler has ever spent, `late` the
            // worst gap between a deadline and the handler starting, both in
            // counter ticks and both since boot. See `src/timer.rs`; `late`
            // growing without bound is the failure worth watching for, because
            // it means something is holding interrupts off for longer than a
            // period.
            let (ticks, cost, late) = timer::stats();

            // The whole 64-bit counter, in hex, and NOT converted to
            // milliseconds here.
            //
            // Converting would be a 64-bit divide by a value only known at run
            // time, and on rv32 that is a call to `__udivdi3` -- 912 bytes of
            // compiler-builtins, measured, which is the difference between this
            // firmware fitting in its 32 KiB half of block RAM and not. The
            // reader that needs milliseconds is `scripts/soc_test.py`, which has
            // `at {} Hz` on the same line and a language where the division is
            // free.
            //
            // The low word alone would have divided in one instruction and
            // wrapped every 71.6 s at 60 MHz (see `src/clock.rs`), which is
            // shorter than the intervals this line exists to be compared over.
            let mtime = timer::mtime();
            let _ = writeln!(
                uart,
                "  uptime  {}  ticks {}  period {} ms  {}",
                log::now(),
                ticks,
                timer::PERIOD_MS,
                if timer::running() {
                    "running"
                } else {
                    "STOPPED"
                }
            );
            let _ = writeln!(
                uart,
                "  clint   @{:08x}  mtime {:08x}:{:08x} at {} Hz",
                target::CLINT_BASE,
                (mtime >> 32) as u32,
                mtime as u32,
                target::TIME_HZ
            );
            let _ = writeln!(
                uart,
                "  cost    worst {} ticks  late worst {} ticks",
                cost, late
            );
        }
        // `cpu stats` rather than a bare `stats`, matching what every other
        // command family here now does: the thing being asked about is named
        // first, so `flash read`, `hyperram read` and `cpu stats` all read the
        // same way. A bare `stats` did not say what it was counting.
        b"map" => hardware::map_command(uart),
        b"pmod" => hardware::pmod_command(uart),
        b"cpu" => match trim(rest) {
            b"stats" => metrics::command(uart),
            b"" => {
                let _ = uart.write_str("usage: cpu stats\n");
            }
            _ => {
                let _ = uart.write_str("unknown: try `cpu stats`\n");
            }
        },
        #[cfg(feature = "workload")]
        b"usb" => workload::command(uart, trim(rest)),
        b"bench" => bench::command(uart, trim(rest)),
        b"info" => info::command(uart),
        b"selftest" => selftest::command(uart, &devices.power),
        // Registered on every target, unlike its neighbours below: it reads no
        // bus at all, so a boardless build renders the same tree with every leaf
        // reporting what it does not have -- which is what `scripts/soc_test.py`
        // drives. See `src/board.rs`.
        b"board" => board::tree(uart, &devices.power, &devices.type_c),
        b"led" => led_cmd::command(uart, rest),
        b"i2c" => i2c_cmd::command(uart, rest, devices),
        b"power" => power_cmd::command(uart, rest, devices),
        b"phy" => phy_cmd::command(uart, trim(rest)),
        // Split here rather than inside the command, because "there is no board"
        // is a fact about this build and not about the Type-C controllers. The
        // command then takes a `&mut Bus` it can use unconditionally.
        b"typec" => match devices.bus.as_mut() {
            Some(bus) => typec::command(uart, rest, &mut devices.type_c, bus),
            None => board_absent(uart),
        },
        b"vbus" => vbus_cmd::command(uart, rest, devices),
        // One record per payload tag, so the drain-time decoding of every tag is
        // exercised on the shipping build. A guard arm rather than a branch
        // inside the one below, so the two cases do not share an indent: this
        // file is merged from several branches at once.
        //
        // The codes and the sample values live in `src/events.rs`, next to the
        // renderer they test; this arm only names the command.
        b"log" if rest == b"tags" => {
            let pushed = events::push_tag_samples();
            let _ = writeln!(
                uart,
                "log pushed {} tag samples, waiting {} dropped {}",
                pushed,
                events::waiting(),
                events::dropped()
            );
        }
        b"log" => {
            // Pushes through the SAME `events::push` an interrupt handler uses,
            // from normal context, which is exactly what makes it a test of the
            // ring rather than of a copy of it: `push` clears `mstatus.MIE` for
            // the length of the copy precisely so that both contexts may use it.
            //
            // Registered on every target, because the ring is pure logic with no
            // hardware behind it -- so `scripts/soc_test.py` can drive fill, wrap
            // and drop counting under QEMU against the code that ships.
            let count = parse_decimal(rest).unwrap_or(1);
            let mut pushed = 0u32;
            for index in 0..count {
                // One millisecond between pushes, and this spacing is the test.
                //
                // Nothing drains the ring until this command returns -- the
                // main loop is what calls `events::drain`, and it is currently
                // several frames below us -- so every record is pushed before
                // any is printed, and the printing takes microseconds. The
                // stamps therefore come out either a millisecond apart, which
                // can only be the push times, or all equal, which can only be
                // the drain time. `scripts/soc_test.py` asserts the former.
                //
                // Waiting on `clock::now()` rather than on the tick, so this
                // works before `timer::start` has run and cannot spin forever
                // on a machine whose tick is broken -- which is one of the
                // things the test is here to catch.
                let until = clock::now();
                while until.elapsed(clock::now()) < clock::millis(1) {}

                if crate::log_from_irq!(events::TEST, index) {
                    pushed += 1;
                }
            }
            let _ = writeln!(
                uart,
                "log pushed {} of {}, waiting {} dropped {}",
                pushed,
                count,
                events::waiting(),
                events::dropped()
            );
        }
        b"sideband" => sideband_cmd::command(uart, rest),
        // The bring-up smoke test: does this CPU compute, can it reach flash,
        // does the clock formatter hold at its boundaries. Four lines, each `ok`
        // or `BAD`, against values that cannot be produced by accident.
        //
        // Distinct from `selftest`, which asks the PERIPHERALS whether they are
        // healthy. This asks whether the core and the flash window work at all,
        // and it is the thing to run first when a board is behaving strangely --
        // every other command's output is only worth reading if this passes.
        b"check" => {
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
        b"load" => match parse_hex(rest) {
            Some(len) => staging::load(index, uart, len),
            None => {
                let _ = writeln!(uart, "usage: load <hex byte count>");
            }
        },
        b"hr" => hr_cmd::command(uart, trim(rest)),
        b"reset" => {
            let _ = writeln!(uart, "restarting");
            reboot();
        }
        // `bram`, `flash` and `hyperram` are dispatched by asking the module that
        // owns the region names, rather than by three arms here. Naming them in
        // this match as well would make it a second list of the same memories, and
        // `src/bench.rs` -- which takes the same three words -- would then have a
        // third. One `parse` and this arm is the whole vocabulary.
        _ => match memory::Region::parse(cmd) {
            Some(region) => memory::command(uart, region, rest),
            None => {
                let _ = writeln!(uart, "unknown command; try `help`");
            }
        },
    }
}

