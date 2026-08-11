//! The shell: the command table, the dispatcher, and every command.
//!
//! ALL of it lives under `shell/`, so dropping the text shell is deleting a
//! directory and one `mod` line. That is the point of the boundary (#303): the
//! drivers are an API, this is one front end over it, and a binary command
//! stream from the host is meant to be another.
//!
//! Moved out of `main.rs` unchanged (#296).
//!
//! `run` is 23 KB in one function -- the largest in the binary by an order of
//! magnitude -- because it is 74 string comparisons in one match. Splitting
//! dispatch per family so a command pulls in only its own code is where a
//! `.text` win would come from, and `.text` is this design's binding constraint.
//! That is a measurement, not a move, and is deliberately not done here.

pub(crate) mod bist;
pub(crate) mod console;
pub(crate) mod cpu;
pub(crate) mod editor;

pub(crate) use console::board_absent;
pub(crate) mod hardware;
// The `hr` verb and the `load`/`parse_hex`/`staging` helpers below are compiled
// out under `hyperram-bist` -- see the dispatch arms. `cynthion-boot` carries the
// same allow for the same reason (#226).
#[cfg_attr(feature = "hyperram-bist", allow(dead_code))]
pub(crate) mod hr;
pub(crate) mod i2c;
pub(crate) mod led;
pub(crate) mod memory;
pub(crate) mod parse;
pub(crate) mod phy;
pub(crate) mod power;
pub(crate) mod rejoin;
pub(crate) mod sideband;
pub(crate) mod typec;
pub(crate) mod usb;
pub(crate) mod vbus;

use core::fmt::Write;

#[cfg_attr(feature = "hyperram-bist", allow(unused_imports))]
use self::parse::{parse_decimal, parse_hex, trim};
use crate::uart::Uart;
// The `usb` arm below. Moved here from `src/main.rs` by #296, where the module
// was in scope because that file declares it; nothing built the shell with this
// feature on, so it went unnoticed until #362 built every feature set.
#[cfg(feature = "workload")]
use crate::workload;
#[cfg_attr(feature = "hyperram-bist", allow(unused_imports))]
use crate::{
    bench, board, clock, events, info, log,
    reboot, sched, scratch_responds, selftest, staging, target, timer,
    Devices,
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
/// # `<device> <verb>`, and a bare verb is not a command
///
/// The device is named first and the verb qualifies it: `cpu stats`, `flash
/// read`, `hyperram bench`, `pac1954 limit`. A bare `stats`, `check`, `irq`,
/// `map` or `bench` does not say what it is about, and two of them wanted the
/// same word. **One-level entries are board-wide actions only** -- `reset`,
/// `board`, `selftest`, `time`, `load`, `help` -- never a verb belonging to a
/// device.
///
/// Two columns, and the first is padded to `HELP_WIDTH` so no name runs into its
/// description. `{:w$}` would pull in `core::fmt`'s width machinery for one call
/// site; the padding is done by hand below for the same reason the rest of this
/// firmware avoids it.
pub(crate) const HELP: &[(&str, &str)] = &[
    ("bist", "the HyperRAM BIST engine, off the Wishbone"),
    ("bist status", "is the engine there, and what was it built as"),
    ("bist smoke", "four cells: can the rig both pass and detect a fault"),
    ("bist cell <d> <clk> <sel>", "one cell, by hand"),
    ("bist sweep [passes]", "drive x clock x readclksel, a row per cell"),
    ("bist trace [passes]", "the sweep, narrating each cell before it runs"),
    ("board", "every connector, rail and controller"),
    ("bram", "block RAM at address zero"),
    ("bram info", "base and size, from the generated map"),
    ("bram read <hex>", "one word of block RAM"),
    ("bram bench", "time a walk over block RAM"),
    ("cpu", "the RISC-V core"),
    ("cpu stats", "cycles, instructions, busy fraction"),
    ("cpu check", "smoke test: add/multiply, flash reads, time format"),
    ("cpu irq", "interrupt counts, per source"),
    ("cpu log [n|tags]", "the deferred event ring the handlers push to"),
    ("cpu status", "is the OS alive: heartbeat dispatches and their lateness"),
    ("cpu wedge [ms]", "stop the scheduler on purpose; watch the lamp stop"),
    ("cpu fault [hex]", "read an address nothing decodes; it must fault, not hang"),
    ("flash", "the memory-mapped W25Q32 config flash"),
    ("flash info", "base and size, from the generated map"),
    ("flash id", "the first flash word, and the size"),
    ("flash read <hex>", "one word of flash, by offset"),
    ("flash bench", "time a walk over the flash window"),
    ("fusb302b [port]", "the FUSB302B controllers"),
    ("help, ?", "this list"),
    // HR AND VBUS HAD ONE ROW EACH -- `hr <cmd>`, "see `hr`" -- with their real
    // subcommands living in a usage string inside the command. That was survivable
    // while `help` was the only reader. It is not now: this table IS the
    // completion source, so a subcommand missing from here cannot be TAB-completed
    // and cannot be discovered without running the command wrongly first.
    ("hyperram", "HyperRAM, and its read window"),
    ("hyperram info", "base and size, from the generated map"),
    ("hyperram status", "the DQS read path's self-report"),
    ("hyperram read <hex>", "one word over the staging port"),
    ("hyperram sel <n>", "READCLKSEL: 2:0 tap, 3 phase, 5:4 read stall"),
    ("hyperram sweep", "try every READCLKSEL and say which read correctly"),
    ("hyperram test", "round-trip one word through the staging port"),
    ("hyperram cross", "do the window and the staging port agree?"),
    ("hyperram ramp [w]", "verify a 0-255 byte ramp; `w` writes it first"),
    ("hyperram bench", "the same walk as `bench hyperram`"),
    ("hyperram id", "HyperBus has no identify"),
    ("hyperram clear", "DESTRUCTIVE: invalidates any staged image"),
    ("i2c", "the three I2C buses behind the mux"),
    ("i2c status", "the controller: base, prescale, selected bus"),
    ("i2c scan [bus]", "scan a bus behind the mux"),
    ("i2c soak <bus> <prer> <n>", "hammer one bus at one rate, count failures"),
    ("info", "this build and this board"),
    ("info map", "every peripheral window, from the generated map"),
    ("info pmod", "connector pins: ball, resource, free or claimed"),
    ("info ports", "the consoles: type, FIFO depth, and which answer"),
    ("info button", "the USER button: open or closed"),
    // AFTER the whole `info` family, not inside it. `list_all` walks runs of
    // rows sharing a first word, so a row wedged between two `info` rows splits
    // the family in two and the first half prints with no subcommands. The
    // table being alphabetical is what keeps every family contiguous.
    //
    // ONE COMMAND, not a verb on every family: `init pac1954` is one arm and
    // two rows; `pac1954 init` and its siblings would be one of each per
    // family, in an image whose binding constraint is `.text`.
    ("init", "establish every peripheral again, in order"),
    ("init <peripheral>", "one of uart i2c pac1954 fusb302b hyperram w25q32 usb3343 vbus"),
    ("led [colour]", "the six LEDs"),
    ("load <hex>", "stage <hex> bytes of firmware, then boot it"),
    ("pac1954", "the four PAC1954 channels"),
    ("pac1954 status", "the reading, the limits and the floors"),
    ("pac1954 floor <port> <mA>", "the current below which a port reads absent"),
    ("pac1954 alert", "the limit ALERTs: armed, routed, fired"),
    ("pac1954 rate [ms|off]", "how often the rails are sampled"),
    ("pac1954 detect [on|off]", "plug detection via the ALERT, not the poll"),
    ("pac1954 limit <k> <port> <n>", "ov/oc/uv/uc threshold, in mV or mA"),
    ("pac1954 samples <k> <port> <n>", "consecutive samples before it asserts"),
    ("pac1954 bracket <port> <mA> <mV>", "limits around the present reading"),
    ("pac1954 hispeed [on|off]", "SLOW bit 0: the 3.4 MHz Pulse Gobbler filter"),
    ("pac1954 reset", "DESTRUCTIVE: PWRDN#, which loses the accumulators"),
    ("reset", "jump to the reset vector"),
    ("rtic", "the dispatcher: model, task jitter, stalls"),
    ("selftest", "run every self-check"),
    ("sideband", "FPGA_ADV: one-wire UART to the SAMD11, on T6"),
    ("time", "uptime, from mtime"),
    ("time set <epoch>", "tell the board the wall clock; no RTC, lost on reset"),
    ("usb", "each port as one thing: rail, switch, cable, phy"),
    ("usb aux", "the usb console; this text leaves through it"),
    ("usb control", "the host port, and apollo's"),
    ("usb target_a", "the USB-A side of the passthrough"),
    ("usb target_c", "the port under test"),
    ("usb3343", "the USB PHYs"),
    ("usb3343 status", "identity, line state and the data-line walk"),
    ("usb3343 reset", "pulse TARGET's RESETB, and prove it reached"),
    ("vbus", "the VBUS switches"),
    ("vbus status", "which switches are closed, and what is sourcing"),
    ("vbus off", "open every switch"),
    ("vbus control", "source from CONTROL"),
    ("vbus both", "source from both"),
    ("vbus charge [1.5|3]", "advertise a host current on TARGET"),
];

/// Width of the first column. One more than the longest entry above, so every
/// description starts in the same place and none of them touch the name.
///
/// It said 20 while the longest entry was 30, so the four longest names ran
/// straight into their descriptions: `power limit <k> <port> <n>ov/oc/uv/uc
/// threshold`. The padding loop is `name.len()..HELP_WIDTH`, which is simply
/// empty when the name is longer -- no panic, no warning, just a missing space.
/// The assert below is what makes the comment true rather than aspirational.
pub(crate) const HELP_WIDTH: usize = 33;

const _: () = {
    let mut i = 0;
    while i < HELP.len() {
        assert!(HELP[i].0.len() < HELP_WIDTH, "HELP_WIDTH is too small");
        i += 1;
    }
};

/// `help` and `?`, for any caller that is not the line editor.
///
/// The editor intercepts both words and never reaches `run`, so this is what
/// #303's binary front end and any editor-less console get. It renders through
/// [`list_all`] rather than repeating it -- there were two copies, and the one
/// the crate never called was the one still being maintained.
pub(crate) fn help(uart: &mut Uart) {
    list_all(&mut |text| {
        let _ = uart.write_str(text);
    });
}

/// The root listing: ONE LINE PER FAMILY, not per row.
///
/// `pac1954` alone has seven rows and `hyperram` nine, so a listing per row is a
/// wall rather than an index. The detail is one TAB away.
///
/// Name alone in the first column, subcommands after the summary: `hyperram`'s
/// nine beside the name pushed the column out and wrapped the line, losing the
/// alignment the column exists for to the thing it was listing.
///
/// `out` rather than a writer, because the two callers have incompatible ones --
/// `Uart` and the crate's `Writer` -- and both translate `\n` to CRLF
/// themselves. One implementation, two sinks.
pub(crate) fn list_all(out: &mut dyn FnMut(&str)) {
    let mut index = 0;
    while index < HELP.len() {
        let (entry, mut summary) = HELP[index];
        let name = first_word(entry);

        // How many rows share this first word, and where the family ends.
        let mut end = index;
        while end < HELP.len() && first_word(HELP[end].0) == name {
            end += 1;
        }

        // THE FAMILY'S OWN summary, not its first subcommand's. Taking the first
        // row described `hyperram` as "the DQS read path's self-report", which is
        // `hyperram status` -- a true sentence about the wrong thing, which is
        // the worst kind of wrong in an index.
        //
        // The bare row is the one with no keyword after the name. Every family
        // has one; the two that did not (`flash`, `hyperram`) were given one
        // rather than having a summary invented here.
        for (row, text) in &HELP[index..end] {
            if second_word(row).is_empty() {
                summary = text;
                break;
            }
        }

        // Pad by hand. `write!("{:w$}")` instantiates core::fmt's fill-and-align
        // path, which is several hundred bytes of code for one call site in an
        // image that has spent this session fighting for block RAM.
        out(name);
        for _ in name.len()..HELP_WIDTH {
            out(" ");
        }
        out(summary);

        // Subcommands after the summary, second word only. A row that is just
        // the bare command contributes nothing -- `pac1954` is not a subcommand
        // of itself.
        let mut first = true;
        for (row, _) in &HELP[index..end] {
            let sub = second_word(row);
            if sub.is_empty() {
                continue;
            }
            out(if first { "  [" } else { "|" });
            first = false;
            out(sub);
        }
        if !first {
            out("]");
        }
        out("\n");

        index = end;
    }
}

/// The command name: everything before the first space.
pub(crate) fn first_word(entry: &str) -> &str {
    // An alias row -- `help, ?` -- is ONE entry, not a family. Splitting it at
    // the space printed `help,` and lost the `?`, so the listing no longer named
    // the second way to ask for it.
    if entry.as_bytes().contains(&b',') {
        return entry;
    }
    match entry.find(' ') {
        Some(at) => &entry[..at],
        None => entry,
    }
}

/// The subcommand: the second word, empty if there is none or if it is an
/// argument placeholder rather than a keyword.
///
/// `power alert` gives `alert`; `power floor <port> <mA>` gives `floor`;
/// `bench [region]` and `led [n]` give nothing, because a bracketed or angled
/// token is what the command TAKES, not a word you type.
pub(crate) fn second_word(entry: &str) -> &str {
    // `help, ?` is two ALIASES, not a command and a subcommand. Without this it
    // renders as `help, [?]`, which reads as a subcommand nobody can type.
    if entry.as_bytes().contains(&b',') {
        return "";
    }
    let rest = match entry.find(' ') {
        Some(at) => &entry[at + 1..],
        None => return "",
    };
    let word = match rest.find(' ') {
        Some(at) => &rest[..at],
        None => rest,
    };
    match word.as_bytes().first() {
        Some(b'<') | Some(b'[') | None => "",
        _ => word,
    }
}

/// The words of a `HELP` entry, or of a typed line.
///
/// Empty pieces are dropped, so a double space or a trailing one never becomes a
/// word that nothing can match.
fn words(text: &str) -> impl Iterator<Item = &str> + Clone + '_ {
    text.split(' ').filter(|word| !word.is_empty())
}

/// An entry with its aliases stripped: `help, ?` completes as `help`.
fn entry_head(entry: &str) -> &str {
    match entry.find(',') {
        Some(at) => &entry[..at],
        None => entry,
    }
}

/// What can follow `line`, at whatever depth `line` has reached.
///
/// **One rule, no levels.** A `HELP` entry is a list of words; so is the typed
/// line, whose last word may be partial. A row matches when every complete typed
/// word equals the row's word in that position, and what is offered is the row's
/// word at the partial position. The same code answers `po` with `power` and
/// `i2c st` with `status`, and would answer a third level without being told
/// about it.
///
/// Placeholders are not candidates: `<hex>` and `[bus]` are what a command
/// TAKES, not words anyone types.
///
/// Returns how many matched, which can exceed `out.len()`; `out` holds the first
/// of them.
pub(crate) fn complete(line: &str, out: &mut [&'static str]) -> usize {
    let typed = words(line).count();
    let (depth, partial) = if line.ends_with(' ') || typed == 0 {
        (typed, "")
    } else {
        (typed - 1, words(line).last().unwrap_or(""))
    };

    let mut found = 0;
    for &(entry, _) in HELP {
        let mut rest = words(entry_head(entry));
        if !words(line).take(depth).all(|word| rest.next() == Some(word)) {
            continue;
        }
        let Some(candidate) = rest.next() else {
            continue;
        };
        if !candidate.starts_with(partial) {
            continue;
        }
        // A placeholder is the end of what completion can say.
        if matches!(candidate.as_bytes()[0], b'<' | b'[') {
            continue;
        }
        // Several rows share a word -- `power alert`, `power rate` all offer
        // `power` at depth 0. One entry each.
        if out[..found.min(out.len())].contains(&candidate) {
            continue;
        }
        if found < out.len() {
            out[found] = candidate;
        }
        found += 1;
    }
    found
}

/// Every `HELP` row that extends `line` by at least one word, with its full
/// argument syntax and summary.
///
/// The listing half of [`complete`], and matched the same way. `i2c` gives its
/// three subcommand rows and not its own bare row -- the question at that point
/// is what can follow, and the command itself cannot.
pub(crate) fn rows_under(
    line: &str,
    out: &mut [(&'static str, &'static str)],
) -> usize {
    let depth = words(line).count();
    let mut found = 0;
    for &(entry, summary) in HELP {
        let mut rest = words(entry_head(entry));
        if !words(line).take(depth).all(|word| rest.next() == Some(word)) {
            continue;
        }
        if rest.next().is_none() {
            continue;
        }
        if found < out.len() {
            out[found] = (entry, summary);
        }
        found += 1;
    }
    found
}

/// The longest prefix every name shares, as a slice of the first one.
///
/// What TAB can insert without choosing between candidates.
pub(crate) fn shared_prefix(names: &[&'static str]) -> &'static str {
    let Some(&first) = names.first() else {
        return "";
    };
    let mut take = first.len();
    for name in &names[1..] {
        let common = first
            .bytes()
            .zip(name.bytes())
            .take_while(|(a, b)| a == b)
            .count();
        if common < take {
            take = common;
        }
    }
    &first[..take]
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
        // Which dispatcher this image was built with, and what it is achieving:
        // the #115 comparison, on the shipping firmware rather than on a
        // synthetic workload. See `src/sched.rs`.
        b"rtic" => sched::command(uart),
        // NO RTC ON THIS BOARD. `time set <epoch>` is a person telling it, and a
        // reset loses it -- which is why the prompt falls back to the uptime
        // stamp rather than showing a date it cannot support.
        b"time" if trim(rest).starts_with(b"set") => {
            match parse_decimal(trim(&trim(rest)[b"set".len()..])) {
                Some(epoch) => {
                    log::set_wall(epoch);
                    let _ = writeln!(
                        uart,
                        "  wall    {} UTC  from the host; a reset loses it",
                        log::Clock(epoch)
                    );
                }
                None => {
                    let _ = writeln!(uart, "usage: time set <unix seconds>");
                }
            }
        }
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
            // So this is the ONE frequency still printed in raw Hz (#333): the
            // field is the divisor a machine reads, `soc_test.py:1485` matches
            // it as `at (\d+) Hz`, and `clock::Hz` would render `60 MHz` and
            // fail that match. Changing it means changing the parser with it.
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
        b"cpu" if trim(rest) == b"log" || trim(rest).starts_with(b"log ") => {
            let arg = trim(&trim(rest)[b"log".len()..]);
            log_command(index, uart, arg, devices)
        }
        b"cpu" => cpu::command(uart, trim(rest)),
        #[cfg(feature = "workload")]
        b"usb" => workload::command(uart, trim(rest)),
        b"bench" => bench::command(uart, trim(rest)),
        b"info" => match trim(rest) {
            b"map" => hardware::map_command(uart),
            b"pmod" => hardware::pmod_command(uart),
            b"ports" => ports_command(uart),
            b"button" => led::button_command(uart),
            _ => info::command(uart),
        },
        // ESTABLISH, on demand. `_init()` is non-destructive by contract, which
        // is what makes it safe to type on a board mid-experiment. See
        // `src/init.rs`.
        b"init" => crate::init::command(uart, rest, devices),
        b"selftest" => selftest::command(uart, &devices.power),
        // Registered on every target, unlike its neighbours below: it reads no
        // bus at all, so a boardless build renders the same tree with every leaf
        // reporting what it does not have -- which is what `scripts/soc_test.py`
        // drives. See `src/board.rs`.
        b"bist" => bist::command(uart, rest),
        b"board" => board::tree(uart, &devices.power, &devices.type_c),
        b"led" => led::command(uart, rest),
        b"i2c" => i2c::command(uart, rest, devices),
        // THE PART NUMBER IS THE COMMAND. `power`, `phy` and `typec` named a
        // function; the board has one specific part doing each, and the
        // datasheet is what anyone debugging it has open. `pac1954 limit` and
        // DS20006539B use the same word for the same register.
        //
        // The generic spelling still dispatches -- it is in every script and
        // every issue written so far -- but it is not in `HELP`, so it does not
        // complete and is not offered. One name to learn, one name that works.
        b"pac1954" | b"power" => power::command(uart, rest, devices),
        b"usb" => usb::command(uart, rest, devices),
        b"usb3343" | b"phy" => phy::command(uart, trim(rest)),
        // Split here rather than inside the command, because "there is no board"
        // is a fact about this build and not about the Type-C controllers. The
        // command then takes a `&mut Bus` it can use unconditionally.
        b"fusb302b" | b"typec" => match devices.bus.as_mut() {
            Some(bus) => self::typec::command(uart, rest, &mut devices.type_c, bus),
            None => board_absent(uart),
        },
        b"vbus" => vbus::command(uart, rest, devices),
        // One record per payload tag, so the drain-time decoding of every tag is
        // exercised on the shipping build. A guard arm rather than a branch
        // inside the one below, so the two cases do not share an indent: this
        // file is merged from several branches at once.
        //
        // The codes and the sample values live in `src/events.rs`, next to the
        // renderer they test; this arm only names the command.
        b"sideband" => sideband::command(uart, rest),
        // The bring-up smoke test: does this CPU compute, can it reach flash,
        // does the clock formatter hold at its boundaries. Four lines, each `ok`
        // or `BAD`, against values that cannot be produced by accident.
        //
        // Distinct from `selftest`, which asks the PERIPHERALS whether they are
        // healthy. This asks whether the core and the flash window work at all,
        // and it is the thing to run first when a board is behaving strangely --
        // every other command's output is only worth reading if this passes.
        // `load` and `hr` reach the part through the BootRAM CSR window, and the
        // BIST variant decodes neither that nor HYPERRAM_PROBE -- the engine owns
        // the pins. A load to an undecoded address never acks, so the CPU stalls
        // mid-command and the board goes silent with no way to ask why. Same
        // defect as the boot-time probe in `init.rs`, same guard (#226).
        #[cfg(not(feature = "hyperram-bist"))]
        b"load" => match parse_hex(rest) {
            Some(len) => staging::load(index, uart, len),
            None => {
                let _ = writeln!(uart, "usage: load <hex byte count>");
            }
        },
        #[cfg(not(feature = "hyperram-bist"))]
        b"hr" => hr::command(uart, trim(rest)),
        #[cfg(feature = "hyperram-bist")]
        b"load" | b"hr" => {
            let _ = writeln!(uart, "hyperram ABSENT: the BIST engine owns the part; use `bist`");
        }
        b"reset" => {
            let _ = writeln!(uart, "restarting");
            reboot();
        }
        // `bram`, `flash` and `hyperram` are dispatched by asking the module that
        // owns the region names, rather than by three arms here. Naming them in
        // this match as well would make it a second list of the same memories, and
        // `src/bench.rs` -- which takes the same three words -- would then have a
        // third. One `parse` and this arm is the whole vocabulary.
        _ => match crate::memory::Region::parse(cmd) {
            Some(region) => self::memory::command(uart, region, rest),
            None => {
                let _ = writeln!(uart, "unknown command; try `help`");
            }
        },
    }
}


/// `info ports` -- what the consoles ARE, not merely whether they answer.
///
/// The type and the FIFO depth are DERIVED, not typed in here. `IIR` bits 7:6
/// report whether the FIFOs are enabled and usable, which is what distinguishes
/// a 16550A from a 16550 whose FIFO was broken and from a plain 16450 with none
/// -- the same probe Linux's `autoconfig` uses. `scratch_responds` already
/// answers "is anything there"; this answers "what is it".
fn ports_command(uart: &mut Uart) {
    let _ = writeln!(
        uart,
        "  {} console(s), {} bytes of FIFO each when enabled",
        target::UART_BASES.len(),
        crate::uart::FIFO_DEPTH
    );

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

/// `cpu log [n|tags]` -- the deferred event ring the interrupt handlers push to.
fn log_command(index: usize, uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    let _ = (index, devices);
    if rest == b"tags" {

            let pushed = events::push_tag_samples();
            let _ = writeln!(
                uart,
                "log pushed {} tag samples, waiting {} dropped {}",
                pushed,
                events::waiting(),
                events::dropped()
            );
                return;
    }

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

/// Print every `HELP` row in a family, for a bare command that has subcommands.
///
/// The same rows `<name>` + TAB shows. A bare command that does nothing but
/// describe itself is better than one that guesses which subcommand was meant.
pub(crate) fn list_family(uart: &mut Uart, name: &str) {
    for (entry, summary) in HELP {
        if first_word(entry) != name {
            continue;
        }
        let _ = uart.write_str("  ");
        let _ = uart.write_str(entry);
        for _ in entry.len()..HELP_WIDTH {
            let _ = uart.write_str(" ");
        }
        let _ = uart.write_str(summary);
        let _ = uart.write_str("\n");
    }
}
