//! Firmware for the Cynthion r1.4 VexiiRiscv SoC: a `no_std` shell over the board.
//!
//! ## This is an image, not the resident half
//!
//! - `firmware/cynthion-boot` owns 0x0, the CPU's reset vector: 492 bytes that read a
//!   staged image out of HyperRAM, check its CRC, copy it here and jump. This crate is
//!   what it jumps to -- one of the two images the bitstream carries, replaceable in
//!   seconds by staging another over it.
//! - This file may grow: it does not have to survive a bad load, so a new command costs
//!   space in the 63 KiB image region, not the resident one. `load` stages and calls
//!   `reboot()`; nothing here copies an image into place, since this code is already
//!   executing from where an image lands.
//! - `riscv-rt` provides entry and trap vector; no HAL crate under that. Drivers here
//!   are small enough to read in one sitting; addresses are generated from the gateware
//!   into `cynthion_soc_pac` and checked by the `socmap` step of `scripts/check.py` -- a
//!   HAL would sit between this firmware and a memory map it already has from the
//!   machine that defines it. Console is `core::fmt::Write` over a standard 16550 in
//!   `src/uart.rs`, ~6 lines, which is what makes `writeln!` work.
//!
//! ## One bus, one owner per device
//!
//! - Board's parts hang off one I2C controller behind a three-way mux; `src/bus.rs`
//!   owns both. Only module that can construct a controller; every transfer names the
//!   bus it wants; each device whose protocol spans transactions has exactly one driver
//!   running it -- `power::Monitor::poll` for the PAC1954's REFRESH cycle,
//!   `typec::Controllers` for the FUSB302Bs' read-to-clear interrupt registers.
//! - Commands report what those drivers cached, not the parts directly: `power` prints
//!   a sample and its age, touches nothing. `Devices` below holds the one `Bus` and
//!   lends it by `&mut`; see `src/bus.rs` for why that's structure, not a lock.
//!
//! ## Two targets, one shell, one driver
//!
//! This file and `src/uart.rs` compile unchanged for FPGA and QEMU:
//!
//! | build              | uses                          | image at      |
//! |--------------------|--------------------------------|---------------|
//! | default            | `src/target.rs` + `memory.x`   | `0x0000_0400` |
//! | `--features qemu`  | `src/target.rs` + `memory-qemu.x` | `0x8000_0000` |
//!
//! - One console driver serves both: the SoC's console peripheral
//!   (`gateware/soc/peripherals/uart16550.py`) and QEMU's `-M virt` are both a standard
//!   NS16550A, so `src/uart.rs` drives each unchanged -- the whole difference is base
//!   addresses, a flash stand-in and a linker script.
//! - `scripts/soc_test.py` builds the QEMU variant, drives this shell over a pipe and
//!   asserts what it says; `scripts/soc_run.py` won't configure the board until those
//!   assertions pass. That gate's value depends on the two builds sharing source --
//!   resist `#[cfg]` below this line, put the difference in `src/target.rs`.
//!
//! ## One dispatcher
//!
//! - `#[rtic::app]` in `src/rtic_app.rs` emits this firmware's `#[no_mangle] fn main`.
//!   No `#[entry]` in this file.
//! - Used to be two, chosen at compile time (superloop shipping, RTIC behind a
//!   feature), to make the #245 comparison possible with exactly one variable. Decided
//!   on hardware; losing path removed rather than kept as a dead branch nobody dares
//!   touch. Measurements: `docs/rtic.md`.
//! - Everything below the dispatcher is unchanged: `boot`, `housekeeping`, `consoles`,
//!   `Devices`, `Shell`, `run` and every command. `#[idle]` calls the same three
//!   functions in the same order the superloop's `loop {}` did.
//!
//! ## More than one console
//!
//! - Shell is not a singleton, neither is the console: `Shell` holds one line editor's
//!   worth of state, main loop runs one per UART in `target::UART_BASES`, taking a byte
//!   from each in turn. Two people on two ports get two independent prompts; a command
//!   typed on one replies on that one.
//! - Only asymmetry: index 0 is where the boot banner and any panic go, since those
//!   happen before or outside any prompt.
//!
//! ## Received bytes arrive by interrupt, not by polling
//!
//! - Each UART raises a PLIC source when a byte lands; the handler in `src/irq.rs`
//!   moves it into a per-console ring; the loop below takes bytes out with `irq::pop`.
//!   Shell reads identically from a user's point of view -- what changed is the byte
//!   was already collected before the loop asked, so a console busy printing no longer
//!   has to be back at `uart.get()` in time.
//! - Transmit is still a bounded spin in `Uart::put`, deliberately -- see `IER_ERBFI`
//!   in `src/uart.rs` for why enabling the transmit-empty interrupt on this peripheral
//!   would be a storm, not a service.

#![no_std]
#![no_main]

use core::fmt::Write;
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

mod bench;
mod board;
mod bus;
mod clock;
// The concurrency measurement for #115: a synthetic USB device-emulation load
// and a preemptive dispatcher for it to run under. Both off by default, and
// nothing below reaches either without the feature, so the shipping image is
// byte-identical either way -- `scripts/soc_workload.py --sizes` checks it.
#[cfg(feature = "preempt")]
mod dispatch;
mod events;
mod fusb302;
mod gpio;
mod hardware;
mod hr_cmd;
mod hyperram;
mod i2c_cmd;
mod info;
mod irq;
mod log;
mod memory;
mod metrics;
mod parse;
mod phy_cmd;
mod plic;
mod power;
mod power_cmd;
mod power_rails;
// THE dispatcher. `#[rtic::app]` emits this firmware's `#[no_mangle] fn main`,
// so there is no `#[entry]` anywhere in this file and no second loop for one to
// attach to. #245.
mod rtic_app;
mod sched;
mod selftest;
mod staging;
mod shell;
mod sideband;
mod target;
mod timer;
mod typec;
mod uart;
mod ulpi;
mod vbus;
#[cfg(feature = "workload")]
mod workload;

use bus::Bus;
use parse::{
    as_str, parse_decimal, parse_hex, parse_limit, parse_port, parse_signed, trim,
    FixedWriter,
};
use clock::Instant;
use target::flash_word;
use uart::Uart;

/// How long an idle console waits before printing the banner again.
///
/// Two seconds: slow enough to read, fast enough that attaching to a quiet board
/// does not feel like attaching to a dead one. A shell that only prints on input
/// is indistinguishable from one that has stopped -- there is nothing to see
/// until you type, and no reason to believe typing will work.
///
/// Milliseconds, measured against `rdtime`, and NOT a count of loop turns. It
/// was a count, and a turn is not a unit of time: under `--features rtic` a turn
/// costs about four times as much, so the same number took four times as long
/// and the QEMU suite reported the shell as silent.
const BANNER_INTERVAL_MS: u32 = 2_000;

/// The most consoles this build will run shells for.
///
/// Sized rather than allocated: `Shell` is ~80 bytes and there is no allocator. Four is
/// well past the two the hardware has and costs a third of a kilobyte of the 63 KiB the
/// image region of block RAM gives us.
///
/// `src/irq.rs` allocates one receive ring per slot, so this is now the dominant term in
/// the firmware's static footprint: four rings of 256 bytes. Raising it costs a quarter of
/// a kilobyte a console.
pub const MAX_CONSOLES: usize = 4;

// A base address with no shell behind it would be a port that silently never answers,
// which is the exact class of failure this firmware keeps being bitten by. Catch it at
// compile time instead.
const _: () = assert!(target::UART_BASES.len() <= MAX_CONSOLES);

/// The board state the shell carries between commands.
///
/// Owned by `main` and passed down by `&mut`, not held in a `static`. That is
/// not a style preference: a `static mut` reachable from any module is exactly
/// the thing that lets an interrupt handler print, and this firmware makes that
/// impossible by construction rather than by convention. See `src/irq.rs` and
/// the `irqlog` check in `scripts/check.py`.
///
/// Empty on a target with no board -- `target::BOARD` is `None` under QEMU, so
/// every field here is state about hardware that is not there, kept anyway so
/// the commands that report it compile and run on both targets.
/// `pub` and not private: `#[rtic::app]` names this type in the signature of
/// `pub` items it generates inside its own module, and a less-visible type
/// cannot appear in one -- `pub(crate)` is not enough, the compiler wants the
/// same visibility. It costs nothing here: this crate is a binary with no
/// `[lib]`, so there is no outside for anything to be public to.
pub struct Devices {
    /// The board's one I2C controller and the mux in front of it, or `None` on a
    /// target that has no board.
    ///
    /// ONE of these exists, here, and every driver that talks to a device
    /// borrows it. That is the arrangement issue #123 asked for: `src/bus.rs`
    /// is the only module that can construct a controller, so a second one
    /// cannot be made, and `&mut` proves at compile time that two callers are
    /// never mid-transfer at once.
    bus: Option<Bus>,
    power: power::Monitor,
    type_c: typec::Controllers,
}

impl Devices {
    const fn new() -> Self {
        Devices {
            bus: match target::BOARD {
                Some(board) => Some(Bus::new(board.i2c, board.i2c_mux, board.i2c_prescale)),
                None => None,
            },
            power: power::Monitor::new(),
            type_c: typec::Controllers::new(),
        }
    }
}

/// One console's line editor and its idle state.
///
/// Per-console rather than global: `spoken` latching on one port must not silence the
/// re-banner on another, and two half-typed command lines must not share a buffer.
/// `pub` for the reason [`Devices`] is: RTIC's `#[local]` resources land in
/// generated signatures too.
pub struct Shell {
    line: [u8; 64],
    len: usize,
    /// Set by the first keypress. From then on the prompt is on screen and reprinting
    /// the banner would fight the line being edited.
    spoken: bool,
    /// When the banner was last printed. `None` until the first idle poll, which
    /// is what stops the first one measuring against a zero instant -- the same
    /// origin defect the scheduler had.
    ///
    /// A TIMESTAMP, not a turn count. This was `idle: u32`, compared against
    /// 12,000,000 turns and described as "~2 s at 60 MHz". A turn is not a unit
    /// of time: under `--features rtic` `#[idle]` takes two SLIC locks per pass
    /// and each turn costs about four times as much, so the same count took
    /// about eight seconds and the shell looked silent. It also scaled with the
    /// number of consoles and with `SYNC_MHZ`, neither of which has anything to
    /// do with how long a person waits before deciding a board is dead.
    last_banner: Option<Instant>,
}

impl Shell {
    const NEW: Shell = Shell {
        line: [0u8; 64],
        len: 0,
        spoken: false,
        last_banner: None,
    };

    /// Handle at most one byte from `uart`, or count one turn of idleness.
    ///
    /// `announce` re-prints the banner and prompt periodically while nothing has been
    /// typed. Printing them once is invisible: the CPU starts the moment the FPGA is
    /// configured and the host takes about half a second to enumerate and bind a tty, so
    /// a terminal attaching afterwards has already missed everything. Worse, an idle
    /// shell that only prints on input is indistinguishable from a dead one -- there is
    /// nothing to see until you type, and no reason to believe typing will work.
    ///
    /// It is off for every console but the first, because on this board the second one's
    /// TX pin is shared with JTAG TMS and an unbidden transmission is bus contention.
    /// See `target::ANNOUNCING`.
    /// `index` selects this console's receive ring in `src/irq.rs`, and is also what
    /// `load` needs to know which port a transfer is arriving on. It is the index into
    /// `target::UART_BASES`, so it is the same number everywhere.
    fn poll(&mut self, index: usize, uart: &mut Uart, announce: bool, devices: &mut Devices) {
        // From the ring the interrupt handler fills, not from LSR. `uart` is still needed
        // for everything this function ECHOES; only the receive direction moved.
        let byte = match irq::pop(index) {
            Some(byte) => byte,
            None => {
                if announce && !self.spoken {
                    let now = clock::now();
                    let last = *self.last_banner.get_or_insert(now);
                    // 2 s, measured. Slow enough to read, fast enough that
                    // attaching does not feel dead -- and now the SAME 2 s under
                    // both dispatchers, at any `SYNC_MHZ`, with any number of
                    // consoles, because it is a duration rather than a count of
                    // something whose cost varies.
                    if last.elapsed(now) >= clock::millis(BANNER_INTERVAL_MS) {
                        self.last_banner = Some(now);
                        // Two lines printed is work, even though nobody asked
                        // for them. See `src/metrics.rs`.
                        metrics::busy();
                        banner(uart);
                        let _ = write!(uart, "> ");
                    }
                }
                return;
            }
        };
        // First keypress: stop re-announcing, the user is here.
        self.spoken = true;

        match byte {
            // Enter. Both, because terminals disagree about which they send.
            b'\r' | b'\n' => {
                let _ = write!(uart, "\n");
                if self.len > 0 {
                    let len = self.len;
                    // Copied out before dispatch so `run` may borrow the uart mutably
                    // while the line it was given stays valid.
                    let mut line = [0u8; 64];
                    line[..len].copy_from_slice(&self.line[..len]);
                    self.len = 0;
                    shell::run(index, uart, &line[..len], devices);
                }
                let _ = write!(uart, "> ");
            }
            // Backspace and delete. Erase on screen as well as in the buffer, or the
            // display and the buffer disagree about what the command is.
            0x08 | 0x7f => {
                if self.len > 0 {
                    self.len -= 1;
                    let _ = write!(uart, "\x08 \x08");
                }
            }
            // Printable ASCII only. Echo, since the device gets raw bytes and nothing
            // else will show what was typed.
            0x20..=0x7e => {
                if self.len < self.line.len() {
                    self.line[self.len] = byte;
                    self.len += 1;
                    uart.put(byte);
                }
            }
            // Everything else -- stray control codes, terminal escape sequences -- is
            // dropped. Echoing or reporting them is worse than silence: an escape
            // sequence would be replayed at the terminal, and a chatty default turns a
            // stuck RX FIFO into an unstoppable wall of text.
            _ => {}
        }
    }
}

/// The console the banner, the bootloader and any panic speak on.
fn primary() -> Uart {
    Uart::new(target::UART_BASES[0])
}

/// One line of the boot report: what came up, and what it came up AS.
///
/// The board used to say nothing between the banner and the first power sample.
/// Everything in `boot` either worked silently or failed silently, so the two
/// were the same picture -- and a peripheral that is configured wrongly looks
/// exactly like one that is configured rightly until something downstream
/// misbehaves and gets blamed instead. The masked I2C interrupt sat in this
/// firmware for months and would have been one line here.
///
/// **The detail is read back from what was configured, not restated as a
/// literal.** A line that prints the number the code was written with reports
/// the source, not the machine, and is exactly the kind of claim this project
/// keeps having to withdraw.
///
/// `status` is a short verdict -- `ok`, `ABSENT`, `WARN`, `FAIL` -- and any
/// explanation belongs in `detail`. It comes SECOND, before the detail, because
/// `core::fmt::Arguments` ignores width and padding: a `{:52}` on the detail
/// silently does nothing and the column never lines up. Two `&str` fields pad
/// properly, so the verdicts form a column that can be scanned for the one that
/// is not `ok`.
///
/// An absent peripheral prints `ABSENT` and stays in the list. A missing line
/// reads as a subsystem nobody thought about; a present one reading `ABSENT`
/// reads as a board without it, which is the truth on the emulator.
fn init_line(uart: &mut Uart, what: &str, status: &str, detail: core::fmt::Arguments) {
    let _ = writeln!(uart, "init  {:9} {:7} {}", what, status, detail);
}

/// Everything that happens before the first turn of `#[idle]`.
///
/// Called from `rtic_app`'s `#[init]`. Factored out of the entry point for #245,
/// when there were two dispatchers and a board that came up differently under
/// them would have made the comparison meaningless. The second dispatcher is
/// gone; this stays factored because `#[init]` runs with interrupts masked and
/// the phase structure below is easier to read than it would be inlined there.
///
/// It ends with interrupts on and the tick running, so the caller may not assume
/// it has the machine to itself afterwards.
fn boot() -> Devices {
    // ---- 1. THE MACHINE -------------------------------------------------
    //
    // Nothing here touches a bus, a pin, or a part. It is the CPU's own
    // facilities, and it comes first so that everything after it is measured,
    // timed and interruptible -- rather than the other way round, which is how
    // this was written and which meant the whole of boot was counted under the
    // wrong performance-counter events and stamped `000000.000`.
    //
    // The CPU's four performance counters, pointed at the events #115 names.
    // FIRST, because they free-run from reset: a selector written late means
    // everything counted before it was a different event. This used to be the
    // last line of `boot`, so the I2C configuration and both PHY probes -- the
    // expensive part of coming up -- were counted as something else.
    sched::init();

    // The 1 ms tick. Before the peripherals, so a slow one is visibly slow: the
    // stamp on every line below comes from this, and a peripheral that took
    // 40 ms to answer used to be indistinguishable from one that took none.
    //
    // `mstatus.MIE` is still clear, so the first tick simply stays pending until
    // `irq::init` below turns delivery on. A pending tick is not a lost one.
    timer::start();

    // The interrupt CONTROLLER, and no source. Each peripheral claims its own
    // below, once it is in a state where an interrupt from it would mean
    // something -- see `irq::claim`. Enabling delivery with nothing enabled
    // cannot deliver anything, which is what lets this come before the parts.
    irq::init();

    // ---- 2. THE CONSOLE -------------------------------------------------
    //
    // Ahead of every other peripheral, because it is the channel everything
    // below reports on. A board that fails to bring up its console has no way
    // to say so, so this is the one peripheral whose failure is silent by
    // construction and the one that therefore goes first.
    //
    // Every UART, not just the primary: an uninitialised 16550 has its FIFOs in
    // whatever state the last boot left them, and on this SoC a `j _start`
    // reboot restarts the CPU without resetting the peripherals. A port left
    // holding half a command line would run it as the first command of the new
    // session.
    for &base in target::UART_BASES {
        Uart::new(base).init();
    }
    irq::claim_consoles();

    let mut console = primary();

    // The banner is the first thing this image says, and reaching it is already the
    // report on the boot: the bootloader ran, found nothing staged or nothing that
    // checked out, and handed over to the image the bitstream placed. A staged image
    // that verified would be printing its own banner here instead.
    banner(&mut console);

    init_line(&mut console, "uart",
              "ok",
              format_args!("{} port(s), rx through the irq ring, no divisor here",
                           target::UART_BASES.len()));

    // ---- 3. THE PERIPHERALS ---------------------------------------------
    //
    // Parts on buses and pins. Each reports itself, and each claims its own
    // interrupt source when it is ready to be interrupted rather than being
    // enabled from a list somewhere else.
    let mut devices = Devices::new();

    // The board's I2C controller, set up once rather than per command.
    //
    // `I2c::init` is idempotent, but it writes CTR and clears the interrupt
    // flag, and the power monitor's poll runs twenty times a second --
    // re-initialising a bus on that cadence would be a needless write to a
    // peripheral three devices now share. The `i2c` scan command still calls it,
    // because a scan is also how a wedged bus gets recovered.
    if let Some(bus) = devices.bus.as_mut() {
        bus.init();
        // The SCL rate this build will actually clock, derived the way the
        // controller derives it -- `f_SCL = f_sync / (5 * (PRER + 1))` -- from
        // the prescale the gateware's own `prescale_for` produced and the clock
        // it produced it for, rather than from `I2C_SCL_HZ`. The divider is an
        // integer, so what comes out is what the bus gets and not what was
        // asked for -- 1 MHz happens to land exactly at PRER 11, and the next
        // rate somebody picks may not.
        let scl_hz = target::BOARD
            .map(|board| target::TIME_HZ / (5 * (board.i2c_prescale as u32 + 1)))
            .unwrap_or(0);
        init_line(&mut console, "i2c",
                  "ok",
                  format_args!("{} Hz scl, prescale {} at {} Hz sync",
                               scl_hz,
                               target::BOARD.map(|b| b.i2c_prescale).unwrap_or(0),
                               target::TIME_HZ));

        // Uniform bipolar VSENSE: any port can source or sink through the
        // bidirectional switch tree. A failed write is retried by the poller.
        //
        // The result is REPORTED. It was discarded with `let _`, so a power
        // monitor that never took its configuration produced a board that came
        // up looking identical and measured on whatever range the part reset to
        // -- and the poller's retry, which is the reason discarding it was
        // defensible, is invisible from here either way.
        let configured = devices.power.configure(bus);
        init_line(&mut console, "pac1954",
                  if configured.is_ok() { "ok" } else { "WARN" },
                  format_args!("4 channels, bipolar vsense, refresh every {} ms{}",
                               power::interval_ms(),
                               if configured.is_ok() { "" }
                               else { " -- no answer; the poller retries" }));

        // Both Type-C controllers, configured so they interrupt on a state
        // change rather than needing to be polled. AFTER the controller is set
        // up, obviously, and BEFORE `irq::init()`, so that nothing is asserting
        // when the source is first enabled -- `configure` clears the parts'
        // interrupt registers on the way through for exactly that reason.
        devices.type_c.start(&mut console, bus);
        init_line(&mut console, "fusb302b",
                  "ok",
                  format_args!("{} controller(s), interrupt on state change",
                               target::TYPE_C_IRQS.len()));
    } else {
        // Listed, not skipped. On the emulator there is no I2C and nothing on
        // it, and a boot report that simply omitted them would read as a boot
        // that had not got to them yet.
        for what in ["i2c", "pac1954", "fusb302b"] {
            init_line(&mut console, what, "ABSENT",
                      format_args!("no i2c on this target"));
        }
    }

    // Type-C claims its sources LAST, and only now.
    //
    // `type_c.start` above cleared both parts' interrupt registers. Enabling
    // before that would deliver a state change from the previous session, and
    // the report it produced would describe a cable that may no longer be there.
    // This is the ordering constraint that used to force the whole interrupt
    // controller to come up after the peripherals; splitting `irq::init` means
    // only this one line has to wait.
    irq::claim_type_c();

    // WHICH sources, read back from the PLIC rather than counted from the lists
    // that were walked. The mask is the hardware's answer, and it is the only
    // thing here that can disagree with the code above.
    //
    // This line is why the report exists. `enabled 00000036` is bits 1, 2, 4 and
    // 5 -- the two consoles and the two Type-C controllers -- and bit 3, the
    // I2C transaction-complete source, is CLEAR. It is wired in
    // `gateware/soc/top.py` and nothing claims it, so it has never asserted, and
    // the power monitor spins on the bus instead of being woken by it. See #246.
    {
        let plic = plic::Plic::new(target::PLIC_BASE);
        let enabled = plic.enabled();
        let i2c_masked = target::BOARD.is_some()
            && enabled & (1 << cynthion_soc_pac::base::BOARD_I2C_IRQ) == 0;
        init_line(&mut console, "plic",
                  if i2c_masked { "WARN" } else { "ok" },
                  format_args!("enabled {:08x}: {} console(s), {} type-c{}",
                               enabled,
                               target::UART_IRQS.len(),
                               target::TYPE_C_IRQS.len(),
                               if i2c_masked {
                                   " -- i2c source MASKED, the bus is polled \
                                    rather than woken (#246)"
                               } else {
                                   ""
                               }));
    }

    init_line(&mut console, "timer", "ok",
              format_args!("{} ms tick on mtimecmp, {} Hz counter",
                           timer::PERIOD_MS, target::TIME_HZ));
    let (fe, be) = bench::hpm::stalls();
    init_line(&mut console, "hpm",
              if fe == 0 && be == 0 { "ABSENT" } else { "ok" },
              format_args!("4 counters selected: frontend/backend stalls, cache{}",
                           if fe == 0 && be == 0 { " -- read as hardwired zero here" }
                           else { "" }));
    init_line(&mut console, "sched",
              "ok",
              format_args!("{}, {} task: power_refresh every {} ms",
                           sched::MODEL, 1, power::interval_ms()));

    devices
}

/// The loop body's board half: everything a handler deferred, drained on a
/// console that normal context owns.
///
/// Shared by both dispatchers (#245). It takes the console rather than making
/// one, because under RTIC the caller is holding a lock and the borrow is what
/// says so.
fn housekeeping(console: &mut Uart, devices: &mut Devices) {
    // Anything an interrupt handler wanted to say. Formatted and
    // transmitted HERE, in normal context, on a console this loop owns --
    // which is the entire arrangement: a handler cannot reach a `Uart`, and
    // `events::drain` cannot be called without one. See `src/events.rs`.
    events::drain(console);

    // Anything a console has LOST, on the same terms and for the same
    // reason: the read of LSR that discovers an overrun happens inside the
    // interrupt handler, which may not print. The bits wait in
    // `src/uart.rs` until here. A console that drops input silently is the
    // failure this board keeps meeting; this is where it stops being
    // silent.
    uart::report_errors(console);

    // A deferred Type-C interrupt, if one is waiting. Every pass rather than
    // on a timer: the source is MASKED between the handler and here, so the
    // only latency is one turn of this loop and nothing is lost while it
    // takes. See `src/typec.rs`.
    if let Some(bus) = devices.bus.as_mut() {
        devices.type_c.service(console, bus);
        devices.type_c.poll(console, bus);
    }
}

/// The loop body's console half: one byte from each shell, round-robin.
///
/// Fair by construction and with no arbitration to get wrong: a console that is
/// being pasted into cannot starve the others, because it still only gets one
/// byte per turn.
///
/// Bytes come from the interrupt handler's rings, not from LSR, so the byte is
/// already collected before this asks for it -- a console busy printing cannot
/// miss one. What the caller still decides is how much of one console's input is
/// handled before the other's, which is a fairness property worth keeping.
fn consoles(shells: &mut [Shell; MAX_CONSOLES], devices: &mut Devices) {
    for (index, &base) in target::UART_BASES.iter().enumerate() {
        let mut uart = Uart::new(base);
        shells[index].poll(index, &mut uart, index < target::ANNOUNCING, devices);
    }
}

fn banner(uart: &mut Uart) {
    let _ = write!(uart, "\n");
    crate::log!(uart, "Cynthion RISC-V SoC - Rust firmware");
    crate::log!(uart, "type `help` or `?` for commands");
}








/// What `led`, `i2c` and `sideband` say when there is no board under them.
///
/// The QEMU build has `target::BOARD == None`. Reporting that is better than
/// hiding the commands: `scripts/soc_test.py` then still checks that they are
/// registered and spelled the same as the help text, and a person who typed one
/// on the wrong target gets told which target they are on rather than
/// `unknown command`. See the comment on `target::BOARD`.
fn board_absent(uart: &mut Uart) {
    let _ = writeln!(uart, "no board peripherals on this target");
}


















/// Does the 16550 at `base` have a working scratch register?
///
/// Two patterns, not one: a single value could match a bus that returns the last thing it
/// saw, and 0x00/0xff could match a floating or tied-off read. Restores nothing afterwards
/// because SCR is defined to do nothing.
fn scratch_responds(base: usize) -> bool {
    const SCR: usize = 7;
    let reg = (base + SCR) as *mut u8;
    // SAFETY: SCR is eight bits of scratch on every 16550; writing it has no effect on
    // any other register, the FIFOs, or anything transmitted. `base` comes from
    // target::UART_BASES, which is the SoC's own address map.
    unsafe {
        let mut ok = true;
        for pattern in [0x5au8, 0xa5] {
            write_volatile(reg, pattern);
            ok &= read_volatile(reg) == pattern;
        }
        ok
    }
}

unsafe extern "C" {
    /// Where a reboot goes, from `memory.x` / `memory-qemu.x`.
    ///
    /// On the board this is `firmware/cynthion-boot` at 0x0, so a reboot re-reads the
    /// staging header. Under QEMU there is no bootloader and nothing at 0, so it is
    /// this image's own entry point. Taken from the linker rather than written here
    /// precisely so this file does not have to know which target it is on.
    static _reset_vector: u8;
}

/// Restart, through whatever sits at the reset vector.
///
/// This is how a staged image gets run: `load` writes the header and comes here, the
/// bootloader finds it, verifies it and jumps to it. There is no second path -- the
/// shell never copies an image into place itself, because it is executing from the
/// region an image lands in.
fn reboot() -> ! {
    // Interrupts off first. riscv-rt's `_abs_start` zeroes `mie` and `mip` as its first
    // instructions, so this is belt and braces on the way into the shell -- but the
    // bootloader has no trap vector at all, and an interrupt taken between here and
    // there would dispatch through a handler whose ring is about to be re-zeroed.
    irq::shutdown();

    unsafe {
        core::arch::asm!(
            "fence",
            "fence.i",
            "jr {vector}",
            vector = in(reg) (&raw const _reset_vector) as usize,
            options(noreturn),
        )
    }
}




/// There is nowhere to report a panic except the console, and no way to recover.
///
/// Printing rather than silently spinning matters: a panicking CPU and a hung one look
/// identical from the host, and that ambiguity has cost real time on this project.
#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    // A fresh handle rather than the one that panicked: taking it by value cannot
    // deadlock, and a `Uart` is nothing but an address so constructing one costs nothing.
    //
    // Deliberately NOT `init()`ed. Initialising clears the transmit FIFO, which would
    // discard whatever the panicking code had already queued -- quite possibly the last
    // line printed before things went wrong, which is the one worth having. LCR resets to
    // 0 on both targets, so DLAB is clear and THR is reachable without any setup.
    let mut uart = primary();
    let _ = writeln!(uart, "\n*** PANIC: {}", info);
    loop {}
}


