//! Peripheral bring-up: the ORDER, and the SHAPE of what each step reports.
//!
//! Issue #315. **Only a power cycle resets the external chips** -- neither a CPU
//! reset (`jr _reset_vector`) nor an FPGA reconfigure does -- so nothing may
//! assume a part is at its power-on defaults, and firmware is what establishes
//! the state instead.
//!
//! ## `_init()` establishes NON-DESTRUCTIVELY; a separate command destroys
//!
//! The rule this file exists to hold, and it applies to every part:
//!
//! | | runs | may destroy | called by |
//! |---|---|---|---|
//! | `<peripheral>_init()` | every boot, and on demand | **nothing** | [`bringup`], and `init` |
//! | evidence gathered inside it | with it | nothing | itself, to learn what it is looking at |
//! | `hyperram clear`, `pac1954 reset` | never automatically | **yes, and says so** | an operator |
//!
//! Rule 5 below makes `_init()` something people run casually, from the shell,
//! on a board mid-experiment. That is only safe if it cannot lose anything. The
//! PAC1954's `REFRESH` (`0x00`) on every 50 ms poll was the counter-example: a
//! routine operation quietly resetting the accumulators, for as long as that
//! code existed (#275/#276).
//!
//! ## The five rules
//!
//! 1. **Ordered.** CPU facilities, then the console, then the bus, then the
//!    parts on it, then interrupt sources -- `fusb302b_init` must have cleared
//!    both parts' interrupt registers before their PLIC sources are claimed.
//! 2. **The UART is the exception**: it cannot log its own initialisation, so it
//!    goes first and REPORTS afterwards, from the registers.
//! 3. **Inside `#[init]`, before RTIC starts.** `devices` does not exist as a
//!    shared resource until `#[init]` returns, so no task and no shell command
//!    can run against a half-established board. The cost is that `#[init]`
//!    cannot be preempted -- which is why every wait below is bounded, with a
//!    stated derivation and a logged expiry (#295). A one-shot RTIC task would
//!    buy preemptibility nothing needs and hand `#[idle]` the first turn of the
//!    shell against uninitialised parts.
//! 4. **Each step verifies**: writes, then reads back, then reports what it
//!    read. An init that only writes is what let a HyperRAM `CR0` write go
//!    nowhere for as long as that code existed.
//! 5. **Idempotent and re-runnable**: `init`, or `init <peripheral>`.
//!
//! ## The steps are calls, not bodies
//!
//! Each `_init()` belongs to its driver, because the driver owns the constants
//! that decide its verdict (#303: drivers are the API, and one that cannot
//! initialise itself is not a complete one). What lives here is the order and
//! the report shape.
//!
//! ## The boot report is RETAINED
//!
//! The CPU starts the instant the FPGA is configured and the host takes ~0.5 s
//! to enumerate and bind a tty, so everything printed at boot goes at a console
//! nobody has attached to yet. Only the banner was reprinted on attach
//! (`shell/console.rs`), and it went to console 0 alone.
//!
//! So each line is formatted ONCE into a fixed buffer and pushed at the console
//! that is listening now; [`replay`] flushes the buffer on the first received
//! byte, on whichever console someone actually attached to, alongside the banner
//! that already waits there.
//!
//! `src/events.rs`'s shape, not its code: timestamps captured at push rather
//! than at drain (`src/log.rs`), and a drop counter that reports itself. A
//! second record ring would cost more than a byte buffer and buy nothing -- the
//! records here are already text by the time anyone reads them.

use core::fmt::{self, Write};
use core::sync::atomic::{AtomicUsize, Ordering};

use crate::clock::{self, Hz};
use crate::uart::Uart;
use crate::{
    bench, info, log, plic, power, sched, selftest, shell, target, timer, ulpi,
    vbus, Devices,
};
// Only the non-BIST `hyperram_init` reaches this; under `hyperram-bist` that
// body is compiled out and an unconditional import is a warning.
#[cfg(not(feature = "hyperram-bist"))]
use crate::hyperram;

/// Bytes of boot report kept for a console that attaches late.
///
/// Sixteen lines of stamp, name, verdict and a detail with room to explain a
/// failure. Overflow is COUNTED and reported rather than silently truncating,
/// which is the property `src/events.rs`'s drop counter has.
///
/// Block RAM, and `.bss`, so raising it costs image space directly. It is spent
/// rather than conserved: a report that truncates is a report whose last line --
/// the one the newest check writes -- is the one nobody sees.
const RETAINED: usize = 2400;

/// Room past the retained report for a line the SHELL asked for.
///
/// `init` typed by hand renders through the same one `writeln!` -- one format
/// site instead of two, in an image where each costs a few hundred bytes -- but
/// must not append to a report that describes the boot. So it formats above the
/// retained prefix and commits nothing.
const SCRATCH: usize = 160;

const REPORT_BYTES: usize = RETAINED + SCRATCH;

struct Retained(core::cell::UnsafeCell<[u8; REPORT_BYTES]>);

// SAFETY: one writer. The sequence runs to completion inside `#[init]` with
// interrupts masked, on one core. Readers take a shared slice of the prefix
// `USED` names, which the writer only ever extends.
unsafe impl Sync for Retained {}

static REPORT: Retained = Retained(core::cell::UnsafeCell::new([0; REPORT_BYTES]));
static USED: AtomicUsize = AtomicUsize::new(0);
static LOST: AtomicUsize = AtomicUsize::new(0);

/// Append into the retained buffer, counting what did not fit.
struct Append {
    at: usize,
    limit: usize,
    lost: usize,
}

impl Write for Append {
    fn write_str(&mut self, text: &str) -> fmt::Result {
        // SAFETY: see `Retained`. The borrow ends with this call.
        let buffer = unsafe { &mut *REPORT.0.get() };
        for &byte in text.as_bytes() {
            if self.at >= self.limit {
                self.lost += 1;
                continue;
            }
            buffer[self.at] = byte;
            self.at += 1;
        }
        Ok(())
    }
}

/// Where one step's line goes, and whether this is the boot sequence.
struct Out<'a> {
    uart: &'a mut Uart,
    /// True for the boot sequence: retain the report, and perform the writes
    /// that are only safe when nothing is using the peripheral yet.
    boot: bool,
}

impl Out<'_> {
    /// One line of the report: what came up, and what it came up AS.
    ///
    /// **The detail is read back from what was configured, not restated as a
    /// literal.** A line that prints the number the code was written with
    /// reports the source, not the machine, and is exactly the kind of claim
    /// this project keeps having to withdraw.
    ///
    /// `status` is a short verdict -- `ok`, `ABSENT`, `WARN`, `FAIL` -- and any
    /// explanation belongs in `detail`. It comes SECOND because
    /// `core::fmt::Arguments` ignores width and padding: a `{:52}` on the detail
    /// silently does nothing, while two `&str` fields pad properly and the
    /// verdicts form a column that can be scanned for the one that is not `ok`.
    ///
    /// An absent peripheral prints `ABSENT` and stays in the list. A missing
    /// line reads as a subsystem nobody thought about; a present one reading
    /// `ABSENT` reads as a board without it, which is the truth on the emulator.
    fn line(&mut self, what: &str, status: &str, detail: fmt::Arguments) {
        // Formatted once, into the buffer, then the same bytes go at whoever is
        // already listening. The stamp is captured HERE rather than at replay,
        // for the reason `src/log.rs` gives: a line drained later must still
        // report when it happened.
        let (start, limit) = match self.boot {
            true => (USED.load(Ordering::Relaxed), RETAINED),
            false => (RETAINED, REPORT_BYTES),
        };
        let mut append = Append { at: start, limit, lost: 0 };
        let _ = writeln!(
            append,
            "{} init  {:9} {:7} {}",
            log::now(),
            what,
            status,
            detail
        );
        if self.boot {
            USED.store(append.at, Ordering::Relaxed);
            LOST.fetch_add(append.lost, Ordering::Relaxed);
        }
        // SAFETY: see `Retained`; the append above has finished with it.
        let buffer: &[u8; REPORT_BYTES] = unsafe { &*REPORT.0.get() };
        let fresh = &buffer[start..append.at];
        let _ = self.uart.write_str(core::str::from_utf8(fresh).unwrap_or("?"));
    }
}

/// Print the retained boot report. Called on a console's first keypress, right
/// after the banner it shares that moment with.
pub(crate) fn replay(uart: &mut Uart) {
    let used = USED.load(Ordering::Relaxed);
    if used == 0 {
        return;
    }
    // SAFETY: see `Retained`. Everything below `used` was written at boot and
    // is not written again.
    let buffer: &[u8; REPORT_BYTES] = unsafe { &*REPORT.0.get() };
    let _ = uart.write_str(core::str::from_utf8(&buffer[..used]).unwrap_or("?"));
    let lost = LOST.load(Ordering::Relaxed);
    if lost != 0 {
        let _ = writeln!(uart, "init  report    WARN    {} bytes of it did not fit", lost);
    }
}

// ---------------------------------------------------------------------------
// The sequence
// ---------------------------------------------------------------------------

/// Everything on a bus or a pin, established in order and each one reported.
///
/// Called from `main::boot` with `boot: true`, before `#[init]` returns, and
/// from the `init` command with `boot: false`.
pub(crate) fn bringup(console: &mut Uart, devices: &mut Devices, boot: bool) {
    let mut out = Out { uart: console, boot };
    uart_init(&mut out);
    i2c_init(&mut out, devices);
    pac1954_init(&mut out, devices);
    fusb302b_init(&mut out, devices);
    hyperram_init(&mut out);
    w25q32_init(&mut out);
    usb3343_init(&mut out);
    vbus_init(&mut out);
    facilities(&mut out);
}

/// One peripheral by name, for `init <peripheral>`.
///
/// Returns false for a name nothing here answers to, so the caller can say so
/// rather than silently doing nothing.
fn one(out: &mut Out, name: &[u8], devices: &mut Devices) -> bool {
    match name {
        b"uart" => uart_init(out),
        b"i2c" => i2c_init(out, devices),
        b"pac1954" => pac1954_init(out, devices),
        b"fusb302b" => fusb302b_init(out, devices),
        b"hyperram" => hyperram_init(out),
        b"w25q32" | b"flash" => w25q32_init(out),
        b"usb3343" => usb3343_init(out),
        b"vbus" => vbus_init(out),
        _ => return false,
    }
    true
}

/// `init` and `init <peripheral>`.
pub(crate) fn command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    let name = shell::parse::trim(rest);
    if name.is_empty() {
        return bringup(uart, devices, false);
    }
    let mut out = Out { uart, boot: false };
    if !one(&mut out, name, devices) {
        let _ = writeln!(out.uart, "no such peripheral; try `help`");
    }
}

// ---------------------------------------------------------------------------
// One step per part, named for the part
// ---------------------------------------------------------------------------

fn uart_init(out: &mut Out) {
    let report = crate::uart::init(out.boot);
    if out.boot {
        // The banner is the first thing this image says, and reaching it is
        // already a report on the boot: the bootloader ran, found nothing
        // staged or nothing that checked out, and handed over to the image the
        // bitstream placed. Here rather than in `main::boot`, because the step
        // that establishes the console is what knows it can carry this.
        shell::console::banner(out.uart);
    }
    out.line(
        "uart",
        if report.established() { "ok" } else { "WARN" },
        format_args!(
            "{} port(s), lcr {:02x} iir {:02x} ier {:02x} read back, rx through the irq ring{}",
            report.ports,
            report.lcr,
            report.iir,
            report.ier,
            if report.established() { "" } else { " -- not 8N1 with usable FIFOs" }
        ),
    );
}

fn i2c_init(out: &mut Out, devices: &mut Devices) {
    let Some(bus) = devices.bus.as_mut() else {
        // Listed, not skipped: a boot report that omitted the parts on the bus
        // would read as a boot that had not got to them yet.
        for what in ["i2c", "pac1954", "fusb302b"] {
            out.line(what, "ABSENT", format_args!("no i2c on this target"));
        }
        return;
    };
    let report = bus.init();
    out.line(
        "i2c",
        if report.established() { "ok" } else { "FAIL" },
        format_args!(
            "{} scl, prer {} read back at {} sync ({}), bus released with 9 clocks + stop{}",
            Hz(report.scl_hz),
            report.prescale,
            Hz(report.sync_hz),
            if report.sync_measured { "counted" } else { "assumed" },
            if report.established() { "" } else { " -- the core did not take the prescale" }
        ),
    );
}

fn pac1954_init(out: &mut Out, devices: &mut Devices) {
    let Some(bus) = devices.bus.as_mut() else {
        return;
    };
    match devices.power.init(bus) {
        Ok(report) => out.line(
            "pac1954",
            if report.established() { "ok" } else { "WARN" },
            format_args!(
                "4 channels, fsr {:04x} ctrl {:04x} read back, refresh every {} ms{}",
                report.fsr,
                report.ctrl,
                power::interval_ms(),
                if report.established() { "" } else { " -- the part is not holding what it was given" }
            ),
        ),
        Err(error) => out.line(
            "pac1954",
            "WARN",
            format_args!("{}; the poller retries", error.as_str()),
        ),
    }
}

fn fusb302b_init(out: &mut Out, devices: &mut Devices) {
    let Some(bus) = devices.bus.as_mut() else {
        return;
    };
    // The per-port lines this prints go straight at the console rather than
    // into the retained report: they are `log!` records about cables, not a
    // verdict about a peripheral, and the ring is for the latter.
    let report = devices.type_c.init(out.uart, bus);
    out.line(
        "fusb302b",
        if report.configured { "ok" } else { "WARN" },
        format_args!(
            "{} controller(s), sw_res then interrupt on state change{}",
            report.ports,
            if report.configured { "" } else { " -- a port did not answer" }
        ),
    );
}

/// The BIST variant has NO BootRAM, and a read of an address in no declared
/// region never gets an ack -- the CPU stalls on the load and the board is
/// silent from reset, with no banner and no shell. `soc_run.py` already
/// compiles the same probe out of `cynthion-boot` for this reason; the shell
/// gained the call at boot afterwards and did not inherit the guard (#226).
#[cfg(feature = "hyperram-bist")]
fn hyperram_init(out: &mut Out) {
    out.line("hyperram", "ABSENT",
             format_args!("the BIST engine owns the part; no BootRAM window here"));
}

#[cfg(not(feature = "hyperram-bist"))]
fn hyperram_init(out: &mut Out) {
    let report = hyperram::init(info::build::GIT_WORD);
    if !report.alive {
        return out.line(
            "hyperram",
            "FAIL",
            format_args!(
                "no round trip after {} CS# pulses -- the part is not answering",
                report.pulses
            ),
        );
    }
    out.line(
        "hyperram",
        if report.kept { "ok" } else { "WARN" },
        format_args!(
            "awake after {} CS# pulse(s), round trip ok, {}",
            report.pulses,
            if !report.kept {
                "POWER WAS LOST: defaults in force, re-stage any image"
            } else if report.stamp == info::build::GIT_WORD {
                "power kept since this build wrote the canary"
            } else {
                "power kept, canary written by another build"
            }
        ),
    );
}

fn w25q32_init(out: &mut Out) {
    let report = crate::memory::w25q32_init();
    out.line(
        "w25q32",
        if report.established() { "ok" } else { "WARN" },
        format_args!(
            "offset 0 reads {:08x}{}",
            report.word,
            if report.established() {
                ", a bitstream header: not in continuous read"
            } else {
                " -- NOT a bitstream header; the part may be in continuous read, \
                 which this path cannot exit (#315)"
            }
        ),
    );
}

fn usb3343_init(out: &mut Out) {
    let Some(board) = target::BOARD else {
        return out.line("usb3343", "ABSENT", format_args!("no ulpi window on this target"));
    };
    match ulpi::Ulpi::new(board.ulpi).init() {
        Ok(phy) => {
            let identified =
                phy.vendor == ulpi::usb3343::VENDOR_ID && phy.product == ulpi::usb3343::PRODUCT_ID;
            out.line(
                "usb3343",
                if identified && phy.reached { "ok" } else { "WARN" },
                format_args!(
                    "target: vendor {:04x} product {:04x}, resetb {}; aux and control have no CSR",
                    phy.vendor,
                    phy.product,
                    if phy.reached { "reached the pad" } else { "DID NOT REACH THE PAD (#241)" }
                ),
            );
        }
        Err(error) => out.line("usb3343", "WARN", format_args!("target: {}", error.as_str())),
    }
}

fn vbus_init(out: &mut Out) {
    if target::BOARD.is_none() {
        return out.line("vbus", "ABSENT", format_args!("no vbus register on this target"));
    }
    let report = vbus::init();
    out.line(
        "vbus",
        if report.established() { "ok" } else { "FAIL" },
        format_args!(
            "all four switches open, register reads {:02x}{}",
            report.state,
            if report.established() { "" } else { " -- a switch did not open" }
        ),
    );
}

/// The CPU's own facilities, which report rather than establish.
///
/// Not `_init()`s: `sched::init`, `timer::start` and `irq::init` ran in
/// `main::boot` before any of this, because everything above needs a clock and
/// a counter to be measured against.
fn facilities(out: &mut Out) {
    // WHICH sources, read back from the PLIC rather than counted from the lists
    // that were walked. The mask is the hardware's answer, and it is the only
    // thing here that can disagree with the code above -- `enabled 00000036`
    // with bit 3 clear is how the masked I2C source was found (#246).
    let enabled = plic::Plic::new(target::PLIC_BASE).enabled();
    let i2c_masked =
        target::BOARD.is_some() && enabled & (1 << cynthion_soc_pac::base::BOARD_I2C_IRQ) == 0;
    out.line(
        "plic",
        if i2c_masked { "WARN" } else { "ok" },
        format_args!(
            "enabled {:08x}: {} console(s), {} type-c{}",
            enabled,
            target::UART_IRQS.len(),
            target::TYPE_C_IRQS.len(),
            if i2c_masked {
                " -- i2c source MASKED, the bus is polled rather than woken (#246)"
            } else {
                ""
            }
        ),
    );
    out.line(
        "timer",
        if timer::running() { "ok" } else { "FAIL" },
        format_args!(
            "{} ms tick on mtimecmp, {} counter",
            timer::PERIOD_MS,
            Hz(target::TIME_HZ)
        ),
    );
    let (fe, be) = bench::hpm::stalls();
    out.line(
        "hpm",
        if fe == 0 && be == 0 { "ABSENT" } else { "ok" },
        format_args!(
            "4 counters selected: frontend/backend stalls, cache{}",
            if fe == 0 && be == 0 { " -- read as hardwired zero here" } else { "" }
        ),
    );
    // The count comes from the task table, not from a literal. It said "1 task"
    // while three were declared, which is the failure mode a hand-maintained
    // count always reaches -- and a boot line that miscounts the scheduler is
    // read at exactly the moment someone is deciding whether to trust it.
    out.line(
        "sched",
        "ok",
        format_args!(
            "{}, {} tasks: heartbeat every {} ms, power_refresh every {} ms",
            sched::MODEL,
            sched::TASK_COUNT,
            sched::HEARTBEAT_PERIOD_MS,
            power::interval_ms()
        ),
    );
    isa(out);
    clocks(out);
    die(out);
}

/// The instruction classes this image was COMPILED with, EXECUTED.
///
/// Two accounts disagree about this core and neither is checkable on its own:
/// `misa` reads `rv32im` however the core was generated, and
/// `target::CPU_HAS_*` says what it was asked for. Running the instructions is
/// what settles it.
///
/// **The generated flags gate the run, not the other way round.** A core with
/// no `A` does not answer `lr.w` wrongly, it raises an illegal instruction, and
/// this firmware aborts on one. So a class the core was not generated with is
/// reported unrun; only a class it claims is exercised.
///
/// Cost: no MMIO and no wait. The four sequences are ~120 instructions plus at
/// most eight lr/sc attempts -- microseconds at any `sync` this board runs.
fn isa(out: &mut Out) {
    // What the COMPILER emitted for, from its own cfg rather than a list anyone
    // typed: `riscv32imac-unknown-none-elf` gives m, a and c, and changing the
    // triple moves this with it.
    let want_m = cfg!(target_feature = "m");
    let want_a = cfg!(target_feature = "a");
    let want_c = cfg!(target_feature = "c");

    // What the core was GENERATED with: `target::CPU_HAS_*`, from the PAC that
    // `soc_generate_pac.py` writes out of `cpu/cpu.py`'s own flag list.
    let missing = if want_m && !target::CPU_HAS_M {
        "M"
    } else if want_a && !target::CPU_HAS_A {
        "A"
    } else if want_c && !target::CPU_HAS_C {
        "C"
    } else {
        ""
    };
    if !missing.is_empty() {
        return out.line(
            "isa",
            "FAIL",
            format_args!(
                "{} needs {}, and this build's core was generated without it \
                 -- those instructions trap, not fail; NOT RUN",
                info::build::TARGET,
                missing
            ),
        );
    }

    let mut ok = selftest::classes::alu();
    if want_m {
        ok &= selftest::classes::muldiv();
    }
    if want_c {
        let (value, bytes) = selftest::classes::compressed();
        ok &= value == 128 && bytes == 6;
    }
    if want_a {
        ok &= selftest::classes::atomics();
    }
    out.line(
        "isa",
        if ok { "ok" } else { "FAIL" },
        format_args!(
            "{}: alu{}{}{} executed{}",
            info::build::TARGET,
            if want_m { ", mul/div/rem" } else { "" },
            if want_c { ", compressed" } else { "" },
            if want_a { ", lr/sc and amo" } else { "" },
            if ok {
                ""
            } else {
                " -- A CLASS RETURNED THE WRONG ANSWER; `selftest` says which"
            }
        ),
    );
}

/// What `sync` is COUNTED at, beside what this firmware assumes it is.
///
/// Every interval in this image is derived from `target::TIME_HZ`, and until now
/// the boot report printed that constant and nothing else -- the shape of fault
/// this project keeps finding. `info` has had the measured figure since #226,
/// but only for someone who typed it.
///
/// Cost: two register reads, plus a bounded wait for the first window.
/// `clock_monitor.py` averages over 1 ms of the 60 MHz oscillator, so nothing
/// can be read before one has elapsed; the budget is 1.25x that, and boot
/// usually reaches here well past it, in which case the wait is zero. On expiry
/// the line says the window did not complete, which is a `usb` domain -- the
/// discrete oscillator -- that is not running.
fn clocks(out: &mut Out) {
    if target::BOARD.is_none() {
        return out.line("clocks", "ABSENT", format_args!("no clock monitor on this target"));
    }
    let budget = clock::micros(clock::WINDOW_US + clock::WINDOW_US / 4);
    let began = clock::now();
    let mut measured = clock::measured();
    while measured.is_none() && began.elapsed(clock::now()) < budget {
        measured = clock::measured();
    }
    let Some(measured) = measured else {
        return out.line(
            "clocks",
            "FAIL",
            format_args!(
                "no {} us window completed in {} us -- the 60 MHz oscillator \
                 domain is not running",
                clock::WINDOW_US,
                clock::to_micros(began.elapsed(clock::now()))
            ),
        );
    };
    let agrees = info::within_one_percent(measured.khz, target::TIME_HZ / 1000);
    out.line(
        "clocks",
        if agrees && measured.locked { "ok" } else { "FAIL" },
        format_args!(
            "sync counted at {} against the {} this image assumes, pll {}",
            Hz::khz(measured.khz),
            Hz(target::TIME_HZ),
            if measured.locked { "locked" } else { "UNLOCKED" }
        ),
    );
}

/// The die's own temperature, and the one thing on this board that depends on it.
///
/// Not cosmetic. The HyperRAM's tCSM is **4 us below 85 C and 1 us above** --
/// Winbond's `Config-AC.v`, behind `` `define LA_85C `` -- and every CS# cap in
/// this design is derived from the 4 us figure alone. Above the knee the
/// watchdog that is supposed to end a burst inside tCSM is four times too loose,
/// and a sustained matrix sweep is precisely the workload that heats the die.
///
/// Cost: one register read. The conversion free-runs in fabric every 8.7 ms
/// (`fabric_status.py`), so nothing here waits for one.
fn die(out: &mut Out) {
    let Some(id) = info::fabric::status() else {
        return out.line("die", "ABSENT", format_args!("no fabric status on this target"));
    };
    let Some((sign, degrees)) = id.celsius() else {
        return out.line(
            "die",
            if id.die & info::fabric::DIE_PRESENT == 0 { "ABSENT" } else { "WARN" },
            format_args!(
                "{}",
                if id.die & info::fabric::DIE_PRESENT == 0 {
                    "no DTR in this bitstream"
                } else {
                    "the DTR has not completed a conversion -- \
                     started every 8.7 ms, so this is the block not answering"
                }
            ),
        );
    };
    let hot = sign.is_empty() && degrees >= info::fabric::TCSM_KNEE_C;
    out.line(
        "die",
        if hot { "WARN" } else { "ok" },
        format_args!(
            "{}{} C (dtr code {}), tCSM {}{}",
            sign,
            degrees,
            id.die & 0x3f,
            if hot { "1000" } else { "4000" },
            if hot {
                " ns above 85 C -- EVERY CS# CAP HERE IS DERIVED FROM 4000 ns \
                 AND IS 4x TOO LOOSE"
            } else {
                " ns, which is what the CS# caps are derived from"
            }
        ),
    );
}
