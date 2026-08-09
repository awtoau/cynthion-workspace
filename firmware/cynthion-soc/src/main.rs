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
mod hyperram;
mod info;
mod irq;
mod log;
mod memory;
mod metrics;
mod plic;
mod power;
mod power_rails;
// THE dispatcher. `#[rtic::app]` emits this firmware's `#[no_mangle] fn main`,
// so there is no `#[entry]` anywhere in this file and no second loop for one to
// attach to. #245.
mod rtic_app;
mod sched;
mod selftest;
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
                    run(index, uart, &line[..len], devices);
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
                               power::INTERVAL_MS,
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
                           sched::MODEL, 1, power::INTERVAL_MS));

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
const HELP: &[(&str, &str)] = &[
    ("bench [region]", "time bram, flash or hyperram"),
    ("board", "every connector, rail and controller"),
    ("bram read <hex>", "one word of block RAM"),
    (
        "check",
        "arithmetic the compiler could have folded, at runtime",
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

/// Everything HyperRAM-specific, under one verb.
///
///     hr status   the DQS read path's self-report
///     hr read <hex>  one word over the staging port
///     hr sel <n>  bits 2:0 READCLKSEL, bit 3 read-window phase
///     hr sweep    try every READCLKSEL and say which ones read correctly
///     hr test     round-trip one word through the staging port
///     hr cross    do the window and the staging port agree?
///     hr bench    the same walk as `bench hyperram`
///     hr id       HyperBus has no identify
fn hyperram_command(uart: &mut Uart, rest: &[u8]) {
    match rest {
        b"status" => {
            let (locked, ready, seen, bursts) = bench::dqs_status();
            let _ = writeln!(
                uart,
                "dqs: dll {} {}, burstdet {} ({} bursts)",
                if locked { "locked" } else { "UNLOCKED" },
                if ready { "ready" } else { "NOT-READY" },
                if seen { "seen" } else { "NEVER" },
                bursts
            );
        }
        b"test" => {
            // Round-trip one word so the HyperRAM path can be checked without
            // staging a whole image.
            hyperram::write_header(0, 0);
            match hyperram::staged() {
                Ok(_) => {
                    let _ = writeln!(
                        uart,
                        "hyperram round-trip BAD: zero length should be rejected"
                    );
                }
                // `Length` specifically: the magic was written and read back,
                // which is the round trip this checks. `NoMagic` or `Silent`
                // would mean the word did not survive.
                Err(hyperram::Reject::Length) => {
                    let _ = writeln!(uart, "hyperram write+read ok");
                }
                Err(_) => {
                    let _ = writeln!(uart, "hyperram round-trip BAD: the magic did not read back");
                }
            }
            hyperram::invalidate();
        }
        b"cross" => {
            let result = bench::hyper_cross_check();
            // Printed either way. On a pass it is the evidence that a DQS read
            // used its strobe rather than a latency count that landed right by
            // luck; on a failure it says which layer to look at.
            let (locked, ready, seen, bursts) = bench::dqs_status();
            let _ = writeln!(
                uart,
                "dqs: dll {} {}, burstdet {} ({} bursts)",
                if locked { "locked" } else { "UNLOCKED" },
                if ready { "ready" } else { "NOT-READY" },
                if seen { "seen" } else { "NEVER" },
                bursts
            );
            let (wrote_w, win_w, stg_w) = result.window_written;
            let (wrote_s, win_s, stg_s) = result.staged_written;
            let _ = writeln!(
                uart,
                "  window wrote {:08x}: window {:08x} staging {:08x}",
                wrote_w, win_w, stg_w
            );
            let _ = writeln!(
                uart,
                "  staging wrote {:08x}: window {:08x} staging {:08x}",
                wrote_s, win_s, stg_s
            );
            let (good, bitmap, want, got) = bench::hyper_line_write_check();
            let _ = writeln!(
                uart,
                "  line write: {}/16 correct, bad {:016b} want {:08x} got {:08x}, ck-stalled {} cycles",
                good, bitmap, want, got, bench::stalls()
            );
            if result.ok() {
                let _ = writeln!(uart, "hyperram ports agree");
            } else {
                let _ = writeln!(uart, "hyperram ports DISAGREE");
            }
        }
        b"bench" => bench::command(uart, b"hyperram"),
        b"id" => memory::command(uart, memory::Region::Hyperram, b"id"),
        _ if rest.starts_with(b"read") => memory::command(uart, memory::Region::Hyperram, rest),
        _ if rest.starts_with(b"sel") => match parse_hex(trim(&rest[3..])) {
            Some(n) if n < 16 => {
                bench::set_readclksel(n as u8);
                let _ = writeln!(uart, "readclksel {}", n);
            }
            _ => {
                let _ = writeln!(uart, "usage: hr sel <0-3f>  (2:0 tap, 3 phase, 5:4 read stall)");
            }
        },
        _ if rest.starts_with(b"ramp") => {
            // A 0-255 byte ramp: every byte names its own position, so a
            // displacement, a duplication or a swapped pair is READ off the dump
            // rather than inferred from four bytes of a single word.
            //
            // `hr ramp` VERIFIES what is already there -- use it after staging
            // the ramp over JTAG, which writes through a path that shares none
            // of this SoC's write logic, so a failure is then unambiguously a
            // READ fault. `hr ramp w` writes it through the memory window first,
            // which tests the write path against the same known pattern.
            const RAMP_AT: usize = 0x4000;      // bytes into the window
            const RAMP_LEN: usize = 256;
            let base = cynthion_soc_pac::base::HYPERRAM + RAMP_AT;
            let writing = trim(&rest[4..]) == b"w";

            if writing {
                for i in (0..RAMP_LEN).step_by(4) {
                    let word = (i as u32)
                        | ((i as u32 + 1) << 8)
                        | ((i as u32 + 2) << 16)
                        | ((i as u32 + 3) << 24);
                    // SAFETY: 4-byte aligned, inside the decoded 8 MiB window.
                    unsafe { core::ptr::write_volatile((base + i) as *mut u32, word) };
                }
                bench::evict_pub();
            }

            let mut wrong = 0;
            let mut first_bad = RAMP_LEN;
            let mut got = [0u8; 16];
            for i in 0..RAMP_LEN {
                // SAFETY: as above; byte reads inside the same window.
                let byte = unsafe { core::ptr::read_volatile((base + i) as *const u8) };
                if i < 16 {
                    got[i] = byte;
                }
                if byte != i as u8 {
                    wrong += 1;
                    if first_bad == RAMP_LEN {
                        first_bad = i;
                    }
                }
            }

            let _ = writeln!(uart, "ramp {} at +{:x}, {} bytes",
                             if writing { "written and verified" } else { "verified" },
                             RAMP_AT, RAMP_LEN);
            let _ = write!(uart, "  first 16 want 00..0f got");
            for byte in got.iter() {
                let _ = write!(uart, " {:02x}", byte);
            }
            let _ = writeln!(uart, "");
            if wrong == 0 {
                let _ = writeln!(uart, "  {}/{} correct -- the path is clean", RAMP_LEN, RAMP_LEN);
            } else {
                let _ = writeln!(uart, "  {}/{} wrong, first at +{:x}",
                                 wrong, RAMP_LEN, first_bad);
            }
        }
        b"sweep" => {
            // One bitstream, eight settings. The tap that captures returning
            // data is a property of the board and CK, and the built-in default
            // is upstream's untested guess.
            for setting in 0..64u8 {
                bench::set_readclksel(setting);
                let (good, bitmap, want, got) = bench::hyper_line_write_check();
                let (_, _, seen, bursts) = bench::dqs_status();
                let _ = writeln!(
                    uart,
                    "  tap {} phase {} rd-stall {}: {:2}/16, bad {:016b}, burstdet {} ({}), want {:08x} got {:08x}",
                    setting & 7, (setting >> 3) & 1, setting >> 4, good, bitmap,
                    if seen { "y" } else { "n" }, bursts, want, got
                );
            }
        }
        _ => {
            let _ = writeln!(
                uart,
                "usage: hr status|read <hex>|sel <n>|sweep|test|cross|bench|id"
            );
        }
    }
}

/// Width of the first column. One more than the longest entry above, so every
/// description starts in the same place and none of them touch the name.
const HELP_WIDTH: usize = 20;

fn help(uart: &mut Uart) {
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

/// `map` -- every peripheral window this SoC decodes.
///
/// From `cynthion_soc_pac::hardware`, generated by the same walk of the SoC's own
/// memory map that produces every address the firmware uses. So the board cannot
/// report a map it is not running.
///
/// **It issues no bus traffic.** Reading some registers has side effects -- the
/// SPI controller's `data` pops the RX FIFO, the FUSB302B's interrupt registers
/// are read-to-clear -- so a command that dumped live values would destroy the
/// state someone was inspecting, and the damage would surface somewhere else.
/// This prints the description only. Live values are a separate decision.
fn map_command(uart: &mut Uart) {
    #[cfg(not(feature = "qemu"))]
    {
        let _ = writeln!(uart, "peripheral            base      size  regs");
        for entry in cynthion_soc_pac::hardware::PERIPHERALS {
            let _ = writeln!(
                uart,
                "{:20} {:08x} {:9x} {:5}",
                entry.name, entry.base, entry.size, entry.registers
            );
        }
    }
    #[cfg(feature = "qemu")]
    let _ = uart.write_str("no generated peripheral map on this target\n");
}

/// `pmod` -- the physical connectors, and whether anything has claimed a pin.
///
/// The `claimed` column is the reason this is generated rather than written down:
/// it is computed from what `elaborate()` actually requested, so it cannot say a
/// pin is free when something already took it. Every pin currently reads free --
/// the SoC requests neither PMOD nor the mezzanine.
///
/// Pins absent from a connector's map are its power pins: on a PMOD, 5 and 11 are
/// ground and 6 and 12 are 3V3.
fn pmod_command(uart: &mut Uart) {
    #[cfg(not(feature = "qemu"))]
    {
        let _ = writeln!(uart, "connector  pin  ball  resource        state");
        for entry in cynthion_soc_pac::hardware::CONNECTOR_PINS {
            let _ = writeln!(
                uart,
                "{:10} {:>3}  {:5} {:15} {}",
                entry.connector,
                entry.pin,
                entry.ball,
                if entry.resource.is_empty() {
                    "--"
                } else {
                    entry.resource
                },
                if entry.claimed { "claimed" } else { "free" }
            );
        }
    }
    #[cfg(feature = "qemu")]
    let _ = uart.write_str("no connectors on this target\n");
}

/// Dispatch one command line.
///
/// `index` is which console this arrived on, needed by `load` so a transfer reads from the
/// right receive ring.
fn run(index: usize, uart: &mut Uart, line: &[u8], devices: &mut Devices) {
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
        b"map" => map_command(uart),
        b"pmod" => pmod_command(uart),
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
        b"led" => board_led(uart, rest),
        b"i2c" => board_i2c(uart, rest, devices),
        b"power" => board_power(uart, rest, devices),
        b"phy" => board_phy(uart, trim(rest)),
        // Split here rather than inside the command, because "there is no board"
        // is a fact about this build and not about the Type-C controllers. The
        // command then takes a `&mut Bus` it can use unconditionally.
        b"typec" => match devices.bus.as_mut() {
            Some(bus) => typec::command(uart, rest, &mut devices.type_c, bus),
            None => board_absent(uart),
        },
        b"vbus" => vbus_command(uart, rest, devices),
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
        b"sideband" => board_sideband(uart, rest),
        b"check" => {
            let a: u32 = 0x1234_5678;
            let b: u32 = 0x9abc_def0;
            // SAFETY: our own stack slots; volatile defeats constant folding, so this
            // measures the CPU rather than the compiler.
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
            Some(len) => load(index, uart, len),
            None => {
                let _ = writeln!(uart, "usage: load <hex byte count>");
            }
        },
        b"hr" => hyperram_command(uart, trim(rest)),
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

/// `led`, `led <colour>`, `led <colour> on|off|fabric`.
///
/// Colours only -- see the module comment in `src/gpio.rs` for why an index is
/// not accepted here.
fn board_led(uart: &mut Uart, rest: &[u8]) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let pins = gpio::Gpio::new(board.gpio);

    // Split the argument into a colour and an optional state.
    let rest = trim(rest);
    let (name, state) = match rest.iter().position(|&b| b == b' ') {
        Some(i) => (&rest[..i], trim(&rest[i + 1..])),
        None => (rest, &rest[..0]),
    };

    if !name.is_empty() {
        let led = match gpio::led_by_name(name) {
            Some(led) => led,
            None => {
                let _ = writeln!(
                    uart,
                    "no LED of that colour; they are red, \
                                        orange, yellow, green, blue, violet"
                );
                return;
            }
        };
        match state {
            b"on" => pins.set_led(led, true),
            b"off" => pins.set_led(led, false),
            b"fabric" => pins.release_led(led),
            b"" => {}
            _ => {
                let _ = writeln!(uart, "usage: led <colour> [on|off|fabric]");
                return;
            }
        }
    }

    // Always list afterwards, so a command that set something shows the result
    // rather than reporting success and leaving the state to be guessed at.
    // This is the only way to confirm an LED from a terminal: nobody reading
    // this output can see the board.
    for (led, colour) in gpio::LEDS {
        let owner = match pins.led_owner(led) {
            gpio::Owner::Cpu => "cpu",
            gpio::Owner::Fabric => "fabric",
        };
        let _ = writeln!(
            uart,
            "  {:7} {:3}  driven by {}",
            colour,
            if pins.led_lit(led) { "on" } else { "off" },
            owner
        );
    }
    let _ = writeln!(
        uart,
        "  button  {}",
        if pins.button() { "pressed" } else { "released" }
    );
    let _ = writeln!(
        uart,
        "  power monitor {}",
        if pins.power_monitor_down() {
            "POWERED DOWN"
        } else {
            "running"
        }
    );
}

/// Scan the power monitor's I2C bus and identify what is on it.
///
/// The scan covers 0x08..0x77 because 0x00..0x07 and 0x78..0x7f are reserved by
/// the I2C specification for general call, ten-bit addressing and the like --
/// probing them can put a device into a mode nobody asked for.
fn board_i2c(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    // Which of the three buses. There is one controller and three pin-sets, and
    // nothing in a reply says which bus it came from -- both FUSB302Bs answer
    // 0x22 with the same identity byte -- so the bus is named on every call
    // below rather than selected once and remembered.
    let which = trim(rest);

    // `i2c soak <bus> <prescale> <reads>` -- find where the bus stops working.
    //
    // **The rate ceiling cannot be computed.** It depends on SDA's rise time,
    // which depends on bus capacitance, which is a property of the copper and
    // is in no datasheet. Arithmetic can say a rate is out of spec; only the
    // board can say whether it works. So this sweeps and counts.
    //
    // It reads the device's IDENTITY every pass, not its address. An address
    // ACK is one bit and a marginal bus gets it right by luck; an identity is
    // sixteen bits that have to be exactly right, from a register read with a
    // repeated START in the middle -- which is the part of the protocol with
    // the tightest setup interval.
    if let Some(args) = which.strip_prefix(b"soak").map(trim) {
        return i2c_soak(uart, args, devices);
    }

    let (bus_select, label) = match which {
        b"" | b"power" => (bus::BUS_POWER_MONITOR, "power_monitor"),
        b"target" => (bus::BUS_TARGET_C, "target_type_c"),
        b"aux" => (bus::BUS_AUX_C, "aux_type_c"),
        _ => {
            let _ = writeln!(uart, "usage: i2c [power|target|aux]");
            let _ = writeln!(uart, "       i2c soak <power|target|aux> <prescale> <reads>");
            return;
        }
    };

    let bus = match devices.bus.as_mut() {
        Some(bus) => bus,
        None => return board_absent(uart),
    };
    // A scan is also how a wedged controller gets recovered, which is why this
    // one command re-initialises. Nothing else does; see `Bus::init`.
    bus.init();

    let _ = writeln!(
        uart,
        "i2c   @{:08x} prescale {} bus {} ({})",
        bus.i2c_base(),
        bus.prescale(),
        bus_select,
        label
    );

    let mut found = [0u8; 8];
    let mut count = 0usize;
    for address in 0x08u8..=0x77 {
        match bus.probe(bus_select, address) {
            Ok(true) => {
                let _ = writeln!(uart, "  {:02x} answers", address);
                if count < found.len() {
                    found[count] = address;
                }
                count += 1;
            }
            Ok(false) => {}
            Err(error) => {
                // Report and stop. A bus that has gone wrong will report the
                // same thing 111 more times, and the first report is the one
                // that says where it happened.
                let _ = writeln!(uart, "  {:02x} {}", address, error.as_str());
                return;
            }
        }
    }
    let _ = writeln!(uart, "  {} device(s)", count);

    // Identify anything that answered. The PAC195x family is what this bus is
    // for, so ask each address whether it is one -- a part that is not simply
    // reports whatever those registers mean to it, which is why the
    // manufacturer id is checked before the product name is trusted.
    for &address in found.iter().take(count.min(found.len())) {
        let mut id = [0u8; 1];
        let manufacturer = match bus.read_registers(
            bus_select,
            address,
            bus::pac195x::REG_MANUFACTURER_ID,
            &mut id,
        ) {
            Ok(()) => id[0],
            Err(error) => {
                let _ = writeln!(uart, "  {:02x} id read failed: {}", address, error.as_str());
                continue;
            }
        };
        if manufacturer != bus::pac195x::MANUFACTURER_MICROCHIP {
            let _ = writeln!(
                uart,
                "  {:02x} manufacturer {:02x}, not a PAC195x",
                address, manufacturer
            );
            continue;
        }
        let mut product = [0u8; 1];
        let mut revision = [0u8; 1];
        let _ = bus.read_registers(
            bus_select,
            address,
            bus::pac195x::REG_PRODUCT_ID,
            &mut product,
        );
        let _ = bus.read_registers(
            bus_select,
            address,
            bus::pac195x::REG_REVISION_ID,
            &mut revision,
        );
        let _ = writeln!(
            uart,
            "  {:02x} {} manufacturer {:02x} revision {:02x}",
            address,
            bus::pac195x::product_name(product[0]),
            manufacturer,
            revision[0]
        );
    }
}

/// `power`, or `power floor <port> <mA>`.
///
/// **Prints the poller's cached sample and touches no bus.** The 50 ms poll
/// already reads every channel and keeps the values to compute the 100 mA delta,
/// so the data is here; issuing a second read would only add a caller that can
/// land inside the poll's REFRESH window, which is issue #123 and is what the
/// deleted 2 ms retry was papering over.
///
/// All four rails regardless of the change threshold, which is unchanged: the
/// threshold keeps the background log readable, and a command that inherited it
/// could not answer "what is it now".
///
/// Ports are named, never numbered, for the same reason the LEDs are: the PAC's
/// channel order is not the port order anyone would guess (channel 1 is
/// TARGET_A), and "channel 3" in a bug report means nothing to the person
/// holding the board.
fn board_power(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    if devices.bus.is_none() {
        return board_absent(uart);
    }

    let rest = trim(rest);
    if rest.starts_with(b"floor") {
        let rest = trim(&rest[b"floor".len()..]);
        let (name, value) = match rest.iter().position(|&b| b == b' ') {
            Some(i) => (&rest[..i], trim(&rest[i + 1..])),
            None => (rest, &rest[..0]),
        };
        let channel = match power::PORTS.iter().position(|&p| p.as_bytes() == name) {
            Some(channel) => channel,
            None => {
                let _ = writeln!(
                    uart,
                    "no port of that name; they are \
                                        target_a, target_c, aux, control"
                );
                return;
            }
        };
        match parse_decimal(value) {
            // A floor is a magnitude. The bipolar full-scale current is 5 A,
            // so anything higher can never be crossed.
            Some(milliamps) if milliamps <= 5000 => {
                devices.power.set_floor(channel, milliamps * 1000)
            }
            _ => {
                let _ = writeln!(
                    uart,
                    "usage: power floor <port> <mA>  \
                                        (0..5000)"
                );
                return;
            }
        }
    } else if rest.starts_with(b"alert") || rest.starts_with(b"limit")
        || rest.starts_with(b"samples") || rest.starts_with(b"bracket")
    {
        return power_alert_command(uart, rest, devices);
    } else if !rest.is_empty() {
        let _ = writeln!(uart, "usage: power [floor <port> <mA>]");
        let _ = writeln!(uart, "       power alert [on|off]");
        let _ = writeln!(uart, "       power limit <ov|oc|uv|uc> <port> <mV|mA>");
        let _ = writeln!(uart, "       power samples <ov|oc|uv|uc> <port> <1|4|8|16>");
        let _ = writeln!(uart, "       power bracket <port> <+/-mA> <+/-mV>");
        return;
    }

    // The header carries the age, because every number under it is that old.
    //
    // Not decoration. A poller that has stopped leaves four voltages that are
    // individually plausible and jointly a lie, and there is nothing in them to
    // say so -- which is the same shape of failure as a stale bus select. The
    // age is the only thing on this screen that can contradict them.
    let _ = write!(
        uart,
        "power @{:02x}  poll {} ms  change {} mA  ",
        power::ADDRESS,
        power::INTERVAL_MS,
        power::CHANGE_UA / 1000
    );
    match devices.power.age() {
        power::Age::Millis(ms) => {
            let _ = writeln!(uart, "sampled {} ms ago", ms);
        }
        power::Age::Older => {
            let _ = writeln!(
                uart,
                "sampled OVER {} s ago -- the poll has \
                                    stopped",
                power::AGE_LIMIT_MS / 1000
            );
        }
        power::Age::Never => {
            let _ = writeln!(uart, "NO SAMPLE YET");
        }
    }

    // ONE row per port, with the reading and every setting that governs it.
    //
    // The measurement, its four limits, the debounce, the floor and whether the
    // alert is armed all describe the same port, and they used to be in three
    // places: `power` for the reading, a second block for the floor, and
    // `power alert` for the limits. A reader asking "what is aux doing and what
    // will it tell me about" had to hold three tables in their head.
    //
    // Limits are read FROM THE PART. `ALERT_STATUS` deliberately is not -- it is
    // read-to-clear and belongs to the service path, so `fired` comes from what
    // that path recorded. Reading it here is the defect this table was built
    // after.
    // NO BUS. `power` prints what the poller and the setters recorded, which is
    // the #123 rule and what `soc_i2c_owner_sim.py` asserts: a second reader
    // lands inside the 1 ms window after a refresh and reports a fault on a
    // working bus. The limits are still the part's -- they are written by the
    // code that programs them, including the auto-backoff -- they are simply
    // not fetched by a display command.
    //
    // `power alert` is the authoritative view and does read the part.
    let enabled = devices.power.alert_enable_cached();
    let fired = devices.power.alert_history();

    // The header uses the SAME width specifiers as the rows below. Written out
    // by hand it drifted by one column the first time a field changed width,
    // and a misaligned table is read wrong rather than read as broken.
    let _ = writeln!(
        uart,
        "  {:9}  {:>6} {:>7} {:>7}   {:>6} {:>7} {:>7}  {:>4}  {:>4}  {:>4}  {:>6}",
        "port", "volts", "uv", "ov", "amps", "uc", "oc", "dbnc", "armd", "fird", "floor"
    );

    let sample = devices.power.latest();
    for channel in 0..4 {
        let floor = devices.power.floor(channel);

        // Each limit, as the part holds it. A zero limit is not a threshold
        // anybody set -- it is the POR value -- so it prints as `--` rather than
        // as `0.000`, which would read as a limit of zero volts.
        let mut cell = [[0u8; 9]; 4];
        let mut armed = [b'-'; 4];
        let mut hit = [b'-'; 4];
        // Under before over, so each pair reads low-to-high like the range it
        // describes. The flag columns below index into this order and the
        // header names it, so the three cannot disagree.
        for (slot, limit) in [
            power::Limit::UnderVoltage,
            power::Limit::OverVoltage,
            power::Limit::UnderCurrent,
            power::Limit::OverCurrent,
        ]
        .iter()
        .enumerate()
        {
            let raw = devices.power.alert_limit_cached(*limit, channel);
            let value = if limit.is_current() {
                power::current_ua(raw) / 1000
            } else {
                power::bus_mv(raw) as i32
            };
            let text = &mut cell[slot];
            if raw == 0 {
                text[..2].copy_from_slice(b"--");
            } else {
                let mut buffer = FixedWriter::new(text);
                let _ = write!(
                    buffer,
                    "{}{}.{:03}",
                    if value < 0 { "-" } else { "" },
                    (value / 1000).abs(),
                    (value % 1000).unsigned_abs()
                );
            }
            // POSITIONAL, not initials. The first letter of each limit name
            // gives `o u o u` for ov/uv/oc/uc, which says nothing about which
            // slot is which -- the header already fixes the order, so a slot
            // only has to say yes or no.
            let bit = limit.bit(channel);
            if enabled & bit != 0 {
                armed[slot] = b'*';
            }
            if fired & bit != 0 {
                hit[slot] = b'!';
            }
        }

        // The debounce, which is per limit but is one value in practice. Shown
        // as the OV one with a `*` when the four disagree, rather than four more
        // columns for a number nobody sets differently per limit.
        let samples = devices
            .power
            .alert_samples_cached(power::Limit::UnderVoltage, channel);

        match sample {
            Some(sample) => {
                let reading = sample.readings[channel];
                // AMPS, because the header says amps. `current_ua / 1000` is
                // milliamps and printing it under an "amps" heading is a
                // thousandfold error that looks like a plausible current.
                let magnitude = reading.current_ua.unsigned_abs();
                let _ = write!(
                    uart,
                    "  {:9}  {:2}.{:03} {:>7} {:>7}   {}{}.{:03}",
                    power::PORTS[channel],
                    reading.bus_mv / 1000,
                    reading.bus_mv % 1000,
                    as_str(&cell[0]),
                    as_str(&cell[1]),
                    if reading.current_ua < 0 { "-" } else { " " },
                    // ROUNDED, not truncated. -762 uA truncates to `-0.000`,
                    // which is a minus sign in front of nothing and reads as a
                    // formatting bug rather than as a current below the
                    // display's resolution.
                    (magnitude + 500) / 1_000_000,
                    (((magnitude + 500) / 1000) % 1000),
                );
            }
            None => {
                let _ = write!(
                    uart,
                    "  {:9}      -- {:>7} {:>7}       --",
                    power::PORTS[channel],
                    as_str(&cell[0]),
                    as_str(&cell[1])
                );
            }
        }
        let _ = writeln!(
            uart,
            " {:>7} {:>7}  {:>4}  {}{}{}{}  {}{}{}{}  {:>2}.{:03}",
            as_str(&cell[2]),
            as_str(&cell[3]),
            samples,
            armed[0] as char, armed[1] as char, armed[2] as char, armed[3] as char,
            hit[0] as char, hit[1] as char, hit[2] as char, hit[3] as char,
            floor / 1_000_000,
            (floor / 1000) % 1000
        );
    }
    if sample.is_none() {
        // Two polls after reset there is genuinely nothing to say -- one to
        // issue a REFRESH_V and one to read what it latched.
        let _ = writeln!(
            uart,
            "  no sample yet; last phase {}",
            devices.power.phase()
        );
    }
    let _ = writeln!(
        uart,
        "  volts and amps; armed/fired columns are uv ov uc oc; `--` a limit \
         nobody set"
    );

    if devices.power.failures > 0 {
        let _ = writeln!(
            uart,
            "  {} failed poll(s) since the last good one",
            devices.power.failures
        );
    }
}

/// `phy` -- identity and state of the USB3343 on TARGET.
///
/// **How to tell a live PHY from an absent one:** the identity registers are
/// necessary and not sufficient. A bus that returns a constant, a PHY held in
/// reset, or a window whose data lines are stuck can all produce a number that
/// looks like an answer, and `0x0424`/`0x0009` are only two of the eight bytes
/// the bus can carry. So this ALSO walks a single bit across the scratch
/// register: eight writes, eight read-backs, each value seen once. That fails on
/// a stuck line, on a shorted pair and on a constant, and it is the same test
/// `scripts/phy_probe.py` and the shipped `cynthion selftest` make.
///
/// A PHY that is not there does not read as zeros -- it never releases `dir`,
/// so the gateware's 68 us timeout fires and this says so, which is a different
/// message from "answered, wrongly".
fn board_phy(uart: &mut Uart, rest: &[u8]) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let phy = ulpi::Ulpi::new(board.ulpi);

    if rest == b"reset" {
        return board_phy_reset(uart, &phy);
    }

    // A named read, so one failure reports which register it was on rather than
    // leaving the caller to count lines.
    let read = |uart: &mut Uart, name: &str, address: u8| -> Option<u8> {
        match phy.read(address) {
            Ok(value) => {
                let _ = writeln!(uart, "  {:16} {:02x}  {:02x}", name, address, value);
                Some(value)
            }
            Err(error) => {
                let _ = writeln!(uart, "  {:16} {:02x}  {}", name, address, error.as_str());
                None
            }
        }
    };

    let _ = writeln!(uart, "ulpi  @{:08x}  target_phy", board.ulpi);
    let _ = writeln!(uart, "  register         at  value");

    let vendor_low = read(uart, "vendor id low", ulpi::usb3343::REG_VENDOR_ID_LOW);
    let vendor_high = read(uart, "vendor id high", ulpi::usb3343::REG_VENDOR_ID_LOW + 1);
    let product_low = read(uart, "product id low", ulpi::usb3343::REG_PRODUCT_ID_LOW);
    let product_high = read(
        uart,
        "product id high",
        ulpi::usb3343::REG_PRODUCT_ID_LOW + 1,
    );
    read(
        uart,
        "function control",
        ulpi::usb3343::REG_FUNCTION_CONTROL,
    );
    read(uart, "otg control", ulpi::usb3343::REG_OTG_CONTROL);
    let debug = read(uart, "debug", ulpi::usb3343::REG_DEBUG);

    match (vendor_low, vendor_high, product_low, product_high) {
        (Some(vl), Some(vh), Some(pl), Some(ph)) => {
            let vendor = ((vh as u16) << 8) | vl as u16;
            let product = ((ph as u16) << 8) | pl as u16;
            let _ = writeln!(
                uart,
                "  vendor {:04x} product {:04x} {}",
                vendor,
                product,
                if vendor == ulpi::usb3343::VENDOR_ID && product == ulpi::usb3343::PRODUCT_ID {
                    "USB3343 ok"
                } else {
                    "NOT a USB3343"
                }
            );
        }
        _ => {
            let _ = writeln!(uart, "  identity incomplete; the PHY did not answer");
            return;
        }
    }

    if let Some(debug) = debug {
        // LineState is D+ in bit 0 and D- in bit 1, straight from the receiver.
        // With nothing plugged into TARGET both are low, which is SE0 -- so `00`
        // here is the expected reading on an idle port and not a fault.
        let _ = writeln!(uart, "  linestate dp {} dm {}", debug & 1, (debug >> 1) & 1);
    }

    // The walking bit. Eight patterns, each with exactly one bit set, so every
    // data line is driven high on its own and read back on its own.
    let mut lines_ok = 0u8;
    let mut failed = false;
    for bit in 0..8 {
        let pattern = 1u8 << bit;
        if phy.write(ulpi::usb3343::REG_SCRATCH, pattern).is_err() {
            failed = true;
            break;
        }
        match phy.read(ulpi::usb3343::REG_SCRATCH) {
            Ok(value) if value == pattern => lines_ok |= pattern,
            Ok(_) => {}
            Err(_) => {
                failed = true;
                break;
            }
        }
    }
    if failed {
        let _ = writeln!(uart, "  scratch walk did not complete");
    } else {
        let _ = writeln!(
            uart,
            "  scratch walk {:02x}  {}",
            lines_ok,
            if lines_ok == 0xff {
                "all 8 data lines ok"
            } else {
                "A DATA LINE IS STUCK"
            }
        );
    }
}

/// `phy reset` -- pulse TARGET's RESETB, and prove that it reached the pin.
///
/// The proof matters more than the reset. Between the `soc-clocks` work and
/// #241 both driven ULPI reset pads were tied de-asserted, and NOTHING SAID SO:
/// the PHY answers its identity registers either way, because its own power-on
/// reset had already run at cold boot. A command that pulsed a wire and printed
/// "done" would have passed on the broken bitstream.
///
/// So the check is a register the reset is specified to clear. The USB334x
/// datasheet Rev 1.2 section 5.6.2: cycling RESETB low for at least 1 us
/// "reset[s] the ULPI registers to their default state (and reset[s] all
/// internal state machines)", and Table 7.1 gives the Scratch register's default
/// as 00h. Write 0x5a, reset, read:
///
///     0x00  the pad moved, the PHY saw it
///     0x5a  the pad did not move, or is not connected to RESETB
///
/// The value survives on the broken build and is cleared on the fixed one, so
/// this command distinguishes them without a scope.
fn board_phy_reset(uart: &mut Uart, phy: &ulpi::Ulpi) {
    const MARKER: u8 = 0x5a;

    let _ = writeln!(uart, "phy reset  target_phy");

    // 1. Leave a mark the reset is specified to erase.
    if let Err(error) = phy.write(ulpi::usb3343::REG_SCRATCH, MARKER) {
        let _ = writeln!(uart, "  scratch write   {}", error.as_str());
        return;
    }
    match phy.read(ulpi::usb3343::REG_SCRATCH) {
        Ok(MARKER) => {
            let _ = writeln!(uart, "  scratch set     {:02x}", MARKER);
        }
        // Without this the whole test is vacuous: a scratch register that never
        // held the marker reads 0x00 afterwards whatever the reset did.
        Ok(other) => {
            let _ = writeln!(
                uart,
                "  scratch set     {:02x}  NOT {:02x} -- the PHY did not take the \
                 marker, so this test cannot tell you anything",
                other, MARKER
            );
            return;
        }
        Err(error) => {
            let _ = writeln!(uart, "  scratch read    {}", error.as_str());
            return;
        }
    }

    // 2. RESETB low for 2.133 us, then 1.200 ms of the PHY's preparation time.
    // Both are counted in gateware against the 60.000 MHz oscillator; this
    // returns when the PHY is ready, not when the pulse ends.
    let _ = writeln!(uart, "  resetb          low 2.133 us, then 1.200 ms tprep");
    if let Err(error) = phy.reset_phy() {
        let _ = writeln!(uart, "  reset           {}", error.as_str());
        return;
    }

    // 3. And the PHY must still be there afterwards. A reset that left it
    // wedged, or a preparation time cut short, shows up here as a timeout.
    let vendor = match phy.read(ulpi::usb3343::REG_VENDOR_ID_LOW) {
        Ok(value) => value,
        Err(error) => {
            let _ = writeln!(
                uart,
                "  after reset     {}  -- the PHY did not come back",
                error.as_str()
            );
            return;
        }
    };

    match phy.read(ulpi::usb3343::REG_SCRATCH) {
        Ok(0x00) => {
            let _ = writeln!(
                uart,
                "  scratch now     00  RESET REACHED THE PHY (vendor {:02x})",
                vendor
            );
        }
        Ok(MARKER) => {
            let _ = writeln!(
                uart,
                "  scratch now     {:02x}  RESET DID NOT REACH THE PHY -- the pad \
                 never moved (#241)",
                MARKER
            );
        }
        Ok(other) => {
            let _ = writeln!(
                uart,
                "  scratch now     {:02x}  neither 00 nor {:02x}; the window is \
                 returning something else entirely",
                other, MARKER
            );
        }
        Err(error) => {
            let _ = writeln!(uart, "  scratch read    {}", error.as_str());
        }
    }
}

/// `i2c soak <bus> <prescale> <reads>` -- hammer one bus at one rate and count.
///
/// The answer to "will it run faster", obtained rather than derived. Restores
/// the build's own prescale before returning, whatever happens, so a failed
/// experiment does not leave the board on a rate that half works.
fn i2c_soak(uart: &mut Uart, args: &[u8], devices: &mut Devices) {
    let mut field = args.split(|&b| b == b' ').filter(|f| !f.is_empty());
    let (bus_select, label) = match field.next() {
        Some(b"power") => (bus::BUS_POWER_MONITOR, "power_monitor"),
        Some(b"target") => (bus::BUS_TARGET_C, "target_type_c"),
        Some(b"aux") => (bus::BUS_AUX_C, "aux_type_c"),
        _ => {
            let _ = writeln!(uart, "usage: i2c soak <power|target|aux> <prescale> <reads>");
            return;
        }
    };
    let prescale = match field.next().and_then(parse_decimal) {
        Some(value) => value as u16,
        None => {
            let _ = writeln!(uart, "usage: i2c soak <power|target|aux> <prescale> <reads>");
            return;
        }
    };
    let reads = field.next().and_then(parse_decimal).unwrap_or(1000);

    let bus = match devices.bus.as_mut() {
        Some(bus) => bus,
        None => return board_absent(uart),
    };
    let restore = bus.prescale();

    // f_SCL = f_sync / (5 * (PRER + 1)), the formula the bit engine implements.
    let scl_hz = target::TIME_HZ / (5 * (prescale as u32 + 1));
    let _ = writeln!(
        uart,
        "i2c soak  {} at prescale {} = {} Hz scl, {} reads",
        label, prescale, scl_hz, reads
    );

    bus.set_prescale(prescale);

    let address = if bus_select == bus::BUS_POWER_MONITOR { 0x10 } else { 0x22 };
    // The register whose value we know: the PAC1954's manufacturer id, and the
    // FUSB302B's device id. A read that returns the RIGHT value is the check; a
    // read that merely completes proves nothing about timing.
    let register = if bus_select == bus::BUS_POWER_MONITOR { 0xfe } else { 0x01 };

    let mut expected: Option<u8> = None;
    let mut errors = 0u32;
    let mut wrong = 0u32;
    let mut done = 0u32;
    for _ in 0..reads {
        let mut byte = [0u8; 1];
        match bus.read_registers(bus_select, address, register, &mut byte) {
            Ok(()) => {
                match expected {
                    // The first successful read defines the answer, so this
                    // needs no table of device ids and works on any register.
                    None => expected = Some(byte[0]),
                    Some(want) if byte[0] != want => wrong += 1,
                    Some(_) => {}
                }
            }
            Err(_) => errors += 1,
        }
        done += 1;
    }

    bus.set_prescale(restore);

    let _ = writeln!(
        uart,
        "  {} reads  {} bus errors  {} wrong values  expected {:02x}",
        done,
        errors,
        wrong,
        expected.unwrap_or(0)
    );
    // The verdict, stated rather than left to be inferred from two zeroes.
    // ONE failure in a thousand is a failure: this is the rate at which a
    // marginal bus works, and "mostly" is the signature it presents with.
    let _ = writeln!(
        uart,
        "  {} at {} Hz -- prescale restored to {}",
        if errors == 0 && wrong == 0 && expected.is_some() {
            "CLEAN"
        } else {
            "FAILED"
        },
        scl_hz,
        restore
    );
}

/// Format into a fixed byte buffer, so a column can be right-aligned without an
/// allocator.
///
/// `no_std` with no heap: `{:>7}` needs something with a known width, and there
/// is no `String` to build one in. Eight bytes covers `-99.999`, and anything
/// longer is truncated rather than panicking -- a clipped cell in a status table
/// is a display fault, and a panic in the shell is the board.
struct FixedWriter<'a> {
    buffer: &'a mut [u8],
    used: usize,
}

impl<'a> FixedWriter<'a> {
    fn new(buffer: &'a mut [u8]) -> Self {
        buffer.fill(0);
        FixedWriter { buffer, used: 0 }
    }
}

impl core::fmt::Write for FixedWriter<'_> {
    fn write_str(&mut self, text: &str) -> core::fmt::Result {
        for &byte in text.as_bytes() {
            if self.used >= self.buffer.len() {
                break;
            }
            self.buffer[self.used] = byte;
            self.used += 1;
        }
        Ok(())
    }
}

/// A NUL-padded cell as a `&str`, for `{:>7}`.
fn as_str(cell: &[u8]) -> &str {
    let end = cell.iter().position(|&b| b == 0).unwrap_or(cell.len());
    core::str::from_utf8(&cell[..end]).unwrap_or("?")
}

/// A decimal that may be negative. `parse_decimal` is unsigned, and a current
/// limit can legitimately be below zero -- the VBUS switch tree is
/// bidirectional, so a port can sink and its VSENSE code is signed.
fn parse_signed(text: &[u8]) -> Option<i32> {
    match text.split_first() {
        Some((b'-', rest)) => parse_decimal(rest).map(|v| -(v as i32)),
        _ => parse_decimal(text).map(|v| v as i32),
    }
}

/// Which limit a word names, or `None`.
fn parse_limit(name: &[u8]) -> Option<power::Limit> {
    power::Limit::ALL.iter().copied().find(|l| l.name().as_bytes() == name)
}

/// A port name to a PAC channel index. Named rather than numbered because
/// channel order is NOT connector order on this part -- channel 1 is TARGET_A,
/// not CONTROL -- and a bare index invites exactly that mistake.
fn parse_port(name: &[u8]) -> Option<usize> {
    power::PORTS.iter().position(|&p| p.as_bytes() == name)
}

/// `power alert`, `power limit`, `power samples`, `power bracket` -- the ALERT
/// configuration, all of it settable and readable from here (#270).
///
/// **These are not firmware constants, deliberately.** The right thresholds are
/// not knowable in advance: whether 3.5 A trips on a real device, or a bracket
/// sits inside the ADC noise, is found by trying it. A constant means a rebuild
/// and a reflash per attempt, and a number nobody is sure about stays unchanged.
///
/// Every read comes FROM THE PART. `power limit` prints what the device holds,
/// not what firmware last wrote -- the difference between "the write was issued"
/// and "the write took", which matters here because a limit written while alerts
/// are enabled is a write whose effect is not what was asked for.
fn power_alert_command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    // Split borrow: the bus and the monitor are separate fields of `Devices`,
    // and the setters below write the monitor's cache while using the bus. An
    // immutable alias to the monitor beside a mutable bus does not compile, and
    // should not -- the cache is state this command changes.
    let Devices { bus, power: monitor, .. } = devices;
    let bus = match bus.as_mut() {
        Some(bus) => bus,
        None => return board_absent(uart),
    };

    let mut field = rest.split(|&b| b == b' ').filter(|f| !f.is_empty());
    let verb = field.next().unwrap_or(b"");

    // ---- power alert [on|off] -------------------------------------------
    if verb == b"alert" {
        let arg = field.next();

        if arg == Some(b"clear".as_slice()) {
            devices.power.alert_forget();
            let _ = writeln!(uart, "alert history cleared");
            return;
        }

        if arg == Some(b"on".as_slice()) || arg == Some(b"off".as_slice()) {
            let want_on = arg == Some(b"on".as_slice());
            if want_on {
                if let Err(error) = monitor.alert_pin_enable(bus) {
                    let _ = writeln!(uart, "alert pin: {}", error.as_str());
                    return;
                }
            }
            // Arm only the limits that have actually been SET.
            //
            // Arming all sixteen would arm the twelve still at their POR zero,
            // and a zero limit is not "no limit" -- it is a threshold every
            // rail is permanently on the wrong side of. The first version did
            // that and produced `target_a oc limit 0 mA now 1 mA over by`, which
            // is a true statement about a limit nobody set.
            //
            // Disarming, by contrast, disarms everything: turning it off should
            // not depend on what happens to be configured.
            let mut count = 0;
            for limit in power::Limit::ALL {
                for channel in 0..4 {
                    let configured = monitor
                        .alert_limit(bus, limit, channel)
                        .map(|raw| raw != 0)
                        .unwrap_or(false);
                    let on = want_on && configured;
                    if want_on && !configured {
                        // Explicitly disarm it, so `power alert on` after a
                        // limit is cleared does not leave a stale arm behind.
                        let _ = monitor.alert_arm(bus, limit, channel, false);
                        continue;
                    }
                    if let Err(error) = monitor.alert_arm(bus, limit, channel, on) {
                        let _ = writeln!(uart, "alert {}: {}", limit.name(), error.as_str());
                        return;
                    }
                    if on {
                        count += 1;
                    }
                }
            }
            if want_on {
                let _ = writeln!(
                    uart,
                    "alerts armed: {} limit(s) with a threshold set",
                    count
                );
                if count == 0 {
                    let _ = writeln!(
                        uart,
                        "  nothing to arm -- set one with `power limit` or \
                         `power bracket` first"
                    );
                }
            } else {
                let _ = writeln!(uart, "alerts disarmed");
            }
            return;
        }

        // Bare `power alert`: the whole picture.
        //
        // `enable` and `routed` are read from the part -- they are plain R/W
        // registers and reading them costs nothing. **`ALERT_STATUS` is NOT**,
        // because it is read-to-clear and belongs to the service path. Reading
        // it here stole events from the handler and made the alert look
        // intermittent; `alert_history` is what the service path recorded.
        let (enabled, routed) = match (monitor.alert_enabled(bus), monitor.alert_routed(bus)) {
            (Ok(e), Ok(r)) => (e, r),
            _ => {
                let _ = writeln!(uart, "alert: the monitor did not answer");
                return;
            }
        };
        let fired = monitor.alert_history();
        let _ = writeln!(
            uart,
            "alert  enable {:06x}  routed {:06x}  fired {:06x}  (since boot)",
            enabled, routed, fired
        );
        // No per-port table here. `power` shows every limit, its debounce and
        // whether it is armed, one row per port -- repeating it under a second
        // command is two places to read the same thing and two places for it to
        // go stale.
        //
        // What this command adds is the RAW masks and the authoritative read:
        // `power` shows the cache, this reads `ALERT_ENABLE` and `GPIO_ALERT2`
        // from the part, so the two disagreeing is itself the finding.
        let _ = writeln!(
            uart,
            "  bit order, high to low: oc1-4 uc1-4  ov1-4 uv1-4  op1-4 accovf acccount cc"
        );
        let _ = writeln!(uart, "  `power` has the per-port limits; this is the raw state");
        let _ = writeln!(uart, "  power alert clear   forget what has fired");
        let _ = writeln!(uart, "  power alert on|off  arm every limit that has a threshold");
        return;
    }

    // ---- power limit <kind> <port> <value> --------------------------------
    if verb == b"limit" {
        let (limit, channel, value) = match (
            field.next().and_then(parse_limit),
            field.next().and_then(parse_port),
            field.next().and_then(parse_signed),
        ) {
            (Some(l), Some(c), Some(v)) => (l, c, v),
            _ => {
                let _ = writeln!(
                    uart,
                    "usage: power limit <ov|oc|uv|uc> <port> <mV|mA>"
                );
                return;
            }
        };
        let raw = if limit.is_current() {
            power::ua_to_code(value.saturating_mul(1000))
        } else {
            // The LIMIT scale, not the measurement scale -- they differ by two.
            power::mv_to_limit_code(value)
        };
        if let Err(error) = monitor.alert_set_limit(bus, limit, channel, raw) {
            let _ = writeln!(uart, "limit: {}", error.as_str());
            return;
        }
        // What was ACTUALLY programmed, read back and converted. The scale is
        // 152.588 uA or 488.3 uV per code, so a request rarely lands exactly --
        // and a threshold rounded silently and echoed as the requested value is
        // the lie this read-back exists to prevent.
        let back = monitor.alert_limit(bus, limit, channel).unwrap_or(0);
        let _ = writeln!(
            uart,
            "{} {} = {} {} (asked {}, code {:04x})",
            limit.name(),
            power::PORTS[channel],
            if limit.is_current() {
                power::current_ua(back) / 1000
            } else {
                power::bus_mv(back) as i32
            },
            if limit.is_current() { "mA" } else { "mV" },
            value,
            back
        );
        return;
    }

    // ---- power samples <kind> <port> <n> ----------------------------------
    if verb == b"samples" {
        let (limit, channel, want) = match (
            field.next().and_then(parse_limit),
            field.next().and_then(parse_port),
            field.next().and_then(parse_decimal),
        ) {
            (Some(l), Some(c), Some(n)) => (l, c, n),
            _ => {
                let _ = writeln!(
                    uart,
                    "usage: power samples <ov|oc|uv|uc> <port> <1|4|8|16>"
                );
                return;
            }
        };
        match monitor.alert_set_nsamples(bus, limit, channel, want) {
            Ok(actual) => {
                let _ = writeln!(
                    uart,
                    "{} {} samples = {} (asked {}), {} ms at 1024 SPS",
                    limit.name(),
                    power::PORTS[channel],
                    actual,
                    want,
                    actual * 1000 / 1024
                );
            }
            Err(error) => {
                let _ = writeln!(uart, "samples: {}", error.as_str());
            }
        }
        return;
    }

    // ---- power bracket <port> <+/-mA> <+/-mV> -----------------------------
    //
    // The change detector. The part has no "value changed" alert; a tight
    // bracket around the present reading IS one, and this is the command that
    // makes finding the noise floor a matter of typing rather than rebuilding.
    if verb == b"bracket" {
        let (channel, d_ma, d_mv) = match (
            field.next().and_then(parse_port),
            field.next().and_then(parse_decimal),
            field.next().and_then(parse_decimal),
        ) {
            (Some(c), Some(a), Some(v)) => (c, a as i32, v as i32),
            _ => {
                let _ = writeln!(uart, "usage: power bracket <port> <+/-mA> <+/-mV>");
                return;
            }
        };
        let sample = match monitor.latest() {
            Some(sample) => sample,
            None => {
                let _ = writeln!(uart, "bracket: no sample yet");
                return;
            }
        };
        let reading = sample.readings[channel];
        let ma = reading.current_ua / 1000;
        let mv = reading.bus_mv as i32;

        let plan = [
            (power::Limit::OverCurrent, power::ua_to_code((ma + d_ma) * 1000)),
            (power::Limit::UnderCurrent, power::ua_to_code((ma - d_ma) * 1000)),
            (power::Limit::OverVoltage, power::mv_to_limit_code(mv + d_mv)),
            (power::Limit::UnderVoltage, power::mv_to_limit_code(mv - d_mv)),
        ];
        for (limit, raw) in plan {
            if monitor.alert_set_limit(bus, limit, channel, raw).is_err()
                || monitor.alert_set_nsamples(bus, limit, channel, 1).is_err()
                || monitor.alert_arm(bus, limit, channel, true).is_err()
            {
                let _ = writeln!(uart, "bracket: the monitor did not answer");
                return;
            }
        }
        if let Err(error) = monitor.alert_pin_enable(bus) {
            let _ = writeln!(uart, "alert pin: {}", error.as_str());
            return;
        }
        let _ = writeln!(
            uart,
            "bracket {}  current {}..{} mA  voltage {}..{} mV  samples 1",
            power::PORTS[channel],
            ma - d_ma,
            ma + d_ma,
            mv - d_mv,
            mv + d_mv
        );
        let _ = writeln!(uart, "  any excursion asserts ALERT on src {}",
                         cynthion_soc_pac::base::BOARD_I2C_MUX_POWER_ALERT_IRQ);
        return;
    }
}

/// `sideband`, `sideband <ctrl>`, or `sideband <ctrl> <tx>`.
fn board_sideband(uart: &mut Uart, rest: &[u8]) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let link = sideband::Sideband::new(board.sideband);

    let rest = trim(rest);
    if !rest.is_empty() {
        // Split on the first space: the control register, then optionally the
        // byte a PING returns. Two arguments rather than two commands because
        // they are read back together and are usually set together.
        let split = rest.iter().position(|&byte| byte == b' ');
        let (first, second) = match split {
            Some(at) => (&rest[..at], trim(&rest[at + 1..])),
            None => (rest, &b""[..]),
        };
        match (parse_hex(first), second.is_empty()) {
            (Some(value), true) => link.write(value as u8),
            (Some(value), false) => match parse_hex(second) {
                Some(message) => {
                    link.write(value as u8);
                    link.set_message(message as u8);
                }
                None => return sideband_usage(uart),
            },
            (None, _) => return sideband_usage(uart),
        }
    }

    let value = link.read();
    let _ = writeln!(uart, "sideband @{:08x} ctrl {:02x}", board.sideband, value);
    if value & sideband::OWN != 0 {
        let _ = writeln!(
            uart,
            "  reporting state {} events {} error {} \
                                reconfigured {}",
            value & sideband::STATE_MASK,
            (value & sideband::EVENTS != 0) as u8,
            (value & sideband::ERROR != 0) as u8,
            (value & sideband::RECONFIGURED != 0) as u8
        );
    } else {
        // Do NOT decode the payload bits here. With OWN clear they are stored
        // and ignored, and printing them under a heading that reads like a
        // report would say the link is announcing something it is not -- which
        // is the one lie a diagnostic for a debug link must not tell.
        let _ = writeln!(
            uart,
            "  reporting the fabric's own state; these bits \
                                are stored and unused"
        );
    }
    // Printed either way: neither the port request nor the byte channel is part
    // of the payload, so OWN says nothing about them.
    let _ = writeln!(
        uart,
        "  CONTROL port {}",
        if value & sideband::ADVERTISE != 0 {
            "REQUESTED"
        } else {
            "not requested"
        }
    );
    let (received, count) = link.received();
    let _ = writeln!(
        uart,
        "  message out {:02x}, in {:02x} after {} byte(s)",
        link.message(),
        received,
        count
    );
}

fn sideband_usage(uart: &mut Uart) {
    let _ = writeln!(uart, "usage: sideband [ctrl [tx]]");
    let _ = writeln!(
        uart,
        "  ctrl bit 7 takes the link from the fabric, \
                            bit 5 asks for the CONTROL port"
    );
    let _ = writeln!(uart, "  tx   the byte a PING returns");
}

/// Drop leading and trailing spaces. The line editor does not, and an argument
/// compared against `b"on"` must not have one on either end.
fn trim(text: &[u8]) -> &[u8] {
    let start = text.iter().position(|&b| b != b' ').unwrap_or(text.len());
    let end = text
        .iter()
        .rposition(|&b| b != b' ')
        .map_or(start, |i| i + 1);
    &text[start..end]
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

/// Receive `len` bytes over the console and stage them in HyperRAM, then reboot.
///
/// The bytes arrive over the USB bulk OUT endpoint -- the same transport `apollo
/// flash-write` uses, and about four orders of magnitude faster than a JTAG register
/// interface, which `scripts/soc_jtag_stage.py --benchmark` measures at 28 ms per 16-bit
/// word. That is a property of poking a control-plane register per word, not of JTAG:
/// the streaming sink in `gateware/soc/bus/jtag_stage.py` moves 32 KiB over the same wire
/// in 85 ms, and unlike this path it needs no running CPU.
///
/// They go to HyperRAM rather than straight into the image region because the next step
/// is a reboot, and a reboot is exactly what block RAM does not survive intact: the
/// shell doing the receiving is executing from it. HyperRAM is external and keeps its
/// contents across a CPU reset.
fn load(index: usize, uart: &mut Uart, len: u32) {
    if len == 0 || len > hyperram::MAX_IMAGE {
        let _ = writeln!(uart, "length must be 1..{:x}", hyperram::MAX_IMAGE);
        return;
    }

    let _ = writeln!(uart, "send {} bytes", len);

    let mut crc = hyperram::Crc32::new();
    let mut received = 0u32;
    let mut pending: u32 = 0;
    let mut held: u32 = 0;

    // Seek once; the gateware auto-increments, so the inner loop is one store per word.
    hyperram::seek_image();

    while received < len {
        // Blocking on THIS console, and only this one: once the sender has started there
        // is nothing else to do, and returning to the prompt mid-transfer would interpret
        // the image as commands. The other consoles' shells are not run for the duration,
        // which is correct -- a transfer in flight is not a moment to run a command.
        //
        // Their interrupts still fire and still fill their rings; the handler does not
        // know or care that this loop is running. That is a change for the better: on the
        // polled version, anything typed on the other port during a transfer was lost to
        // a 16-byte FIFO overrun. Here it waits.
        //
        // This must read the ring rather than the UART. The handler has already taken the
        // byte out of the 16550's FIFO, so `uart.get()` would spin forever on an LSR.DR
        // that the handler keeps clearing -- a `load` that hangs with the data arriving
        // perfectly.
        let byte = match irq::pop(index) {
            Some(b) => b,
            None => continue,
        };
        crc.push(byte);
        received += 1;

        // The staging port moves a 32-bit pair, so bytes are grouped four at a time,
        // little-endian.
        pending = (pending >> 8) | ((byte as u32) << 24);
        held += 1;
        if held == 4 {
            hyperram::write_pair(pending);
            held = 0;
        }
    }

    // A length that is not a multiple of four still has to fill its final pair. The
    // unused bytes are outside `len`, so the bootloader never reads them.
    if held != 0 {
        hyperram::write_pair(pending >> (8 * (4 - held)));
    }

    let crc = crc.finish();
    hyperram::write_header(len, crc);
    let _ = writeln!(
        uart,
        "staged {} bytes, crc {:08x}; rebooting",
        received, crc
    );

    // The bootloader takes it from here: it re-reads these bytes, checks this CRC
    // against what HyperRAM actually gives back, and jumps.
    reboot();
}

/// Parse an ASCII decimal number. `None` if empty or malformed.
///
/// Separate from `parse_hex` rather than a base parameter, because the two are
/// used for different things and confusing them is silent: `power floor aux 20`
/// meaning 32 mA would be a threshold nobody could explain. Addresses and
/// lengths are hex here; quantities a person states in engineering units are
/// decimal.
fn parse_decimal(text: &[u8]) -> Option<u32> {
    let text = trim(text);
    if text.is_empty() {
        return None;
    }
    let mut value: u32 = 0;
    for &byte in text {
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            _ => return None,
        };
        value = value.checked_mul(10)?.checked_add(digit as u32)?;
    }
    Some(value)
}

/// Parse an ASCII hex number. `None` if empty or malformed -- better than a wrong
/// address silently read.
fn parse_hex(text: &[u8]) -> Option<u32> {
    let text = match text.iter().position(|&b| b != b' ') {
        Some(i) => &text[i..],
        None => return None,
    };
    let mut value: u32 = 0;
    let mut digits = 0;
    for &byte in text {
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            b'A'..=b'F' => byte - b'A' + 10,
            b' ' => break,
            _ => return None,
        };
        value = value.checked_mul(16)?.checked_add(digit as u32)?;
        digits += 1;
    }
    if digits == 0 {
        None
    } else {
        Some(value)
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

/// `vbus` -- pass host power through to a target, or report the switches.
///
/// Terse on purpose. Every string here is `.rodata` in a 63 KiB image whose
/// stack is whatever is left above `.bss`, and a chatty command in this firmware
/// is paid for in stack depth -- see the ASSERT in `memory.x`, which exists
/// because this exact command overran it.
fn vbus_command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    // `trim(rest)`, not `core::str::from_utf8(rest).unwrap_or("").trim()`.
    //
    // This was the only place in the firmware that touched `core::str`, and it
    // cost 1,152 bytes of `.text` and 256 of `.rodata`: UTF-8 validation,
    // `<str>::trim`, and the Unicode whitespace table `<str>::trim` consults --
    // to compare four ASCII words on a line the 16550 delivered as bytes.
    // `crate::trim` is 88 bytes and is what every other command already uses.
    let argument = trim(rest);

    if argument.is_empty() {
        // `in c/a` is the INPUT register, and it reaches no pin.
        // `top.py` deliberately does not request
        // `control_vbus_in_en`/`aux_vbus_in_en` -- nothing here has a reason to
        // command a power input closed, and hardware overvoltage protection
        // (D17, a 5.6 V zener) backs that. So the register reads back whatever
        // was last written to it and drives nothing.
        //
        // Printed as `(nc)` because printing it bare made the shell able to lie
        // about power: `in c1 a0` reads as the board's input state and is not.
        let (control_in, aux_in) = vbus::inputs();
        let _ = writeln!(
            uart,
            "vbus {:02x}  c{} a{} t{}  in c{} a{} (nc)",
            vbus::state(),
            vbus::is_closed(vbus::Source::Control) as u8,
            vbus::is_closed(vbus::Source::Aux) as u8,
            vbus::is_closed(vbus::Source::TargetC) as u8,
            control_in as u8,
            aux_in as u8
        );
        return;
    }
    if argument == b"off" {
        vbus::open_all();
        if let Some(bus) = devices.bus.as_mut() {
            let _ = fusb302::configure(bus, fusb302::Port::Target);
        }
        let _ = writeln!(uart, "open");
        return;
    }
    let charge = match argument {
        b"charge" => Some(fusb302::HostCurrent::Default),
        b"charge 1.5" => Some(fusb302::HostCurrent::A1_5),
        b"charge 3" => Some(fusb302::HostCurrent::A3),
        _ => None,
    };
    if let Some(current) = charge {
        let bus = match devices.bus.as_mut() {
            Some(bus) => bus,
            None => return board_absent(uart),
        };
        if fusb302::source_target(bus, current).is_err() {
            let _ = writeln!(uart, "?");
            return;
        }
        match vbus::charge_target_c(&devices.power) {
            Ok(mv) => {
                let _ = writeln!(uart, "on {} mV", mv);
            }
            Err(error) => {
                let _ = fusb302::configure(bus, fusb302::Port::Target);
                vbus_refusal(uart, error);
            }
        }
        return;
    }
    if let Some(which) = argument.strip_prefix(b"input").map(trim) {
        let ok = match which {
            b"control" => vbus::prefer_control(&devices.power),
            b"both" => {
                vbus::allow_all_inputs();
                true
            }
            _ => false,
        };
        let _ = writeln!(uart, "input {}", if ok { "set" } else { "no" });
        return;
    }
    match vbus::Source::parse(argument) {
        None => {
            let _ = writeln!(uart, "?");
        }
        Some(source) => match vbus::close(source, &devices.power) {
            Ok(mv) => {
                let _ = writeln!(uart, "on {} mV", mv);
            }
            Err(error) => vbus_refusal(uart, error),
        },
    }
}

fn vbus_refusal(uart: &mut Uart, refusal: vbus::Refusal) {
    match refusal {
        vbus::Refusal::TooHigh(mv) => {
            let _ = writeln!(uart, "no: {} mV high", mv);
        }
        vbus::Refusal::TooLow(mv) => {
            let _ = writeln!(uart, "no: {} mV low", mv);
        }
        vbus::Refusal::Stale => {
            let _ = writeln!(uart, "no: stale");
        }
    }
}
