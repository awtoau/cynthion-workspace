//! Firmware for the Cynthion r1.4 VexiiRiscv SoC: a `no_std` shell over the board.
//!
//! ## This is an image, not the resident half
//!
//! `firmware/cynthion-boot` owns 0x0 and is what the CPU's reset vector points at: 492
//! bytes that read a staged image out of HyperRAM, check its CRC, copy it here and
//! jump. This crate is what it jumps to -- one of the two images the bitstream carries,
//! and replaceable in seconds by staging another over it.
//!
//! What follows from that: this file may grow. It is not the thing that has to survive
//! a bad load, so a new command costs space in the 63 KiB image region rather than in
//! the resident one. `load` stages and calls `reboot()`; nothing here copies an image
//! into place, because this code is executing from where an image lands.
//!
//! `riscv-rt` provides the entry and the trap vector, and there is no HAL crate
//! under that. The drivers here are small enough to read in one sitting, and the addresses
//! they use are generated from the gateware into `cynthion_soc_pac` and checked by the
//! `socmap` step of `scripts/check.py` -- so a HAL would sit between this firmware and a
//! memory map it already has from the machine that defines it. A console is
//! `core::fmt::Write` over a standard 16550 in `src/uart.rs`, about six lines, and that is
//! what makes `writeln!` work.
//!
//! ## One bus, one owner per device
//!
//! The board's parts hang off one I2C controller behind a three-way mux, and `src/bus.rs`
//! owns both. It is the only module that can construct a controller, every transfer names
//! the bus it wants, and each device whose protocol spans transactions has exactly one
//! driver running it: `power::Monitor::poll` for the PAC1954's REFRESH cycle,
//! `typec::Controllers` for the FUSB302Bs' read-to-clear interrupt registers. Commands
//! report what those drivers cached rather than reading the parts on their own account --
//! `power` prints a sample and its age and touches nothing. `Devices` below holds the one
//! `Bus` and lends it out by `&mut`; see `src/bus.rs` for why that is structure and not a
//! lock.
//!
//! ## Two targets, one shell, one driver
//!
//! This file is compiled unchanged for the FPGA and for QEMU, and so is `src/uart.rs`:
//!
//!     default          -> src/target.rs + memory.x       (image at 0x0000_0400)
//!     --features qemu  -> src/target.rs + memory-qemu.x  (image at 0x8000_0000)
//!
//! One console driver serves both targets. The SoC's console peripheral
//! (`ecp5-test/riscv/uart16550.py`) and QEMU's `-M virt` are both a standard NS16550A, so
//! `src/uart.rs` drives each unchanged and the whole difference is base addresses, a
//! flash stand-in and a linker script.
//!
//! `scripts/soc_test.py` builds the QEMU variant, drives this shell over a pipe and
//! asserts what it says; `scripts/soc_run.py` will not configure the board until those
//! assertions pass. The value of that gate depends entirely on the two builds sharing
//! source, so resist the urge to `#[cfg]` anything below this line -- put the difference
//! in `src/target.rs` instead.
//!
//! ## More than one console
//!
//! The shell is not a singleton and neither is the console. `Shell` holds one line
//! editor's worth of state, and the main loop runs one per UART in `target::UART_BASES`,
//! taking a byte from each in turn. Two people on two ports get two independent prompts; a
//! command typed on one replies on that one. The only asymmetry is index 0, which is the
//! port the boot banner and any panic go to, because those happen before or outside any
//! prompt.
//!
//! ## Received bytes arrive by interrupt, not by polling
//!
//! Each UART raises a PLIC source when a byte lands; the handler in `src/irq.rs` moves it
//! into a per-console ring, and the loop below takes bytes out with `irq::pop`. The shell
//! reads identically from a user's point of view -- what changed is that the byte was
//! already collected before the loop asked for it, so a console that is busy printing no
//! longer has to be back at `uart.get()` in time.
//!
//! Transmit is still a bounded spin in `Uart::put`, deliberately. See `IER_ERBFI` in
//! `src/uart.rs` for why enabling the transmit-empty interrupt on this peripheral would be
//! a storm rather than a service.

#![no_std]
#![no_main]

use core::fmt::Write;
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

use riscv_rt::entry;

mod bench;
mod board;
mod bus;
mod clock;
mod events;
mod gpio;
mod hyperram;
mod fusb302;
mod info;
mod irq;
mod log;
mod metrics;
mod plic;
mod power;
mod power_rails;
mod selftest;
mod sideband;
mod target;
mod timer;
mod typec;
mod vbus;
mod uart;
mod ulpi;

use bus::Bus;
use target::flash_word;
use uart::Uart;

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
struct Devices {
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
                Some(board) => Some(Bus::new(board.i2c, board.i2c_mux,
                                             board.i2c_prescale)),
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
struct Shell {
    line: [u8; 64],
    len: usize,
    /// Set by the first keypress. From then on the prompt is on screen and reprinting
    /// the banner would fight the line being edited.
    spoken: bool,
    idle: u32,
}

impl Shell {
    const NEW: Shell = Shell {
        line: [0u8; 64],
        len: 0,
        spoken: false,
        idle: 0,
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
    fn poll(&mut self, index: usize, uart: &mut Uart, announce: bool,
            devices: &mut Devices) {
        // From the ring the interrupt handler fills, not from LSR. `uart` is still needed
        // for everything this function ECHOES; only the receive direction moved.
        let byte = match irq::pop(index) {
            Some(byte) => byte,
            None => {
                if announce && !self.spoken {
                    self.idle = self.idle.wrapping_add(1);
                    // ~2 s at 60 MHz with one console, and proportionally longer with
                    // more, since each turn of the outer loop now polls each of them.
                    // Not calibrated; it only has to be slow enough to read and fast
                    // enough that attaching does not feel dead. Under QEMU the same
                    // count runs in about two seconds, because each pass costs one
                    // emulated MMIO read -- close enough that the test does not need
                    // its own value.
                    if self.idle >= 12_000_000 {
                        self.idle = 0;
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

#[entry]
fn main() -> ! {
    // Every UART, not just the primary: an uninitialised 16550 has its FIFOs in whatever
    // state the last boot left them, and on this SoC a `j _start` reboot restarts the CPU
    // without resetting the peripherals. A port left holding half a command line would
    // run it as the first command of the new session.
    for &base in target::UART_BASES {
        Uart::new(base).init();
    }

    let mut console = primary();

    // The banner is the first thing this image says, and reaching it is already the
    // report on the boot: the bootloader ran, found nothing staged or nothing that
    // checked out, and handed over to the image the bitstream placed. A staged image
    // that verified would be printing its own banner here instead.
    banner(&mut console);

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

        // Uniform bipolar VSENSE: any port can source or sink through the
        // bidirectional switch tree. A failed write is retried by the poller.
        let _ = devices.power.configure(bus);

        // Both Type-C controllers, configured so they interrupt on a state
        // change rather than needing to be polled. AFTER the controller is set
        // up, obviously, and BEFORE `irq::init()`, so that nothing is asserting
        // when the source is first enabled -- `configure` clears the parts'
        // interrupt registers on the way through for exactly that reason.
        devices.type_c.start(&mut console, bus);
    }

    // Interrupts on, and not before now.
    //
    // After `Uart::init()` on every port, so nothing is asking yet. The bootloader
    // leaves them off and takes no traps at all, so this is the first thing in the boot
    // that enables one.
    irq::init();

    // The 1 ms tick, and with it the millisecond count every line below is
    // stamped from.
    //
    // After `irq::init()`, which is what sets `mstatus.MIE`; `timer::start`
    // programs the deadline and sets its own `mie` bit, and neither ordering
    // between the two is load-bearing.
    //
    // Everything printed before this point is stamped 000000.000, which is
    // true: there was no clock to tell those lines apart. See `src/log.rs`.
    timer::start();

    let mut shells = [Shell::NEW; MAX_CONSOLES];

    loop {
        // Close the turn that just ended: two `csrr`s, charged to busy or idle
        // by whether anything called `metrics::busy` during it. See
        // `src/metrics.rs` for why the tick is not one of the things that does.
        metrics::turn();

        // The board's own periodic work, before the consoles.
        //
        // Here rather than in an interrupt handler: the power monitor's poll
        // spins on an I2C bus for a couple of milliseconds and prints when a
        // rail changes, and a handler may do neither -- see `src/irq.rs`. A 50
        // ms period does not need a handler to be met, and one turn of this
        // loop is microseconds.
        //
        // It reports on the PRIMARY console only. The second port's TX pin is
        // JTAG TMS and this firmware never transmits there unbidden, which is
        // exactly what a background monitor would be doing -- see
        // `target::ANNOUNCING`.
        devices.power.poll(&mut console, devices.bus.as_mut());

        // Anything an interrupt handler wanted to say. Formatted and
        // transmitted HERE, in normal context, on a console this loop owns --
        // which is the entire arrangement: a handler cannot reach a `Uart`, and
        // `events::drain` cannot be called without one. See `src/events.rs`.
        events::drain(&mut console);

        // Anything a console has LOST, on the same terms and for the same
        // reason: the read of LSR that discovers an overrun happens inside the
        // interrupt handler, which may not print. The bits wait in
        // `src/uart.rs` until here. A console that drops input silently is the
        // failure this board keeps meeting; this is where it stops being
        // silent.
        uart::report_errors(&mut console);

        // A deferred Type-C interrupt, if one is waiting. Every pass rather than
        // on a timer: the source is MASKED between the handler and here, so the
        // only latency is one turn of this loop and nothing is lost while it
        // takes. See `src/typec.rs`.
        if let Some(bus) = devices.bus.as_mut() {
            devices.type_c.service(&mut console, bus);
            devices.type_c.poll(&mut console, bus);
        }

        // Round-robin, one byte per console per pass. Fair by construction and with no
        // arbitration to get wrong: a console that is being pasted into cannot starve
        // the others, because it still only gets one byte per turn.
        //
        // Bytes come from the interrupt handler's rings, not from LSR, so the byte is
        // already collected before this loop asks for it -- a console busy printing
        // cannot miss one. What the loop still decides is how much of one console's input
        // is handled before the other's, which is a fairness property worth keeping.
        for (index, &base) in target::UART_BASES.iter().enumerate() {
            let mut uart = Uart::new(base);
            shells[index].poll(index, &mut uart, index < target::ANNOUNCING,
                               &mut devices);
        }
    }
}

fn banner(uart: &mut Uart) {
    let _ = write!(uart, "\n");
    crate::log!(uart, "Cynthion RISC-V SoC - Rust firmware");
    crate::log!(uart, "type `help` or `?` for commands");
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
        b"help" | b"?" => {
            let _ = uart.write_str("help id read check info selftest ports irq time stats \
                                    bench log board led i2c power phy typec vbus sideband \
                                    load reset\n");
        }
        b"id" => {
            // Reads through the memory map, which is the verified path. The JEDEC id
            // itself needs the SPI controller, which the C firmware drives; this
            // reports what the memory map can see.
            let _ = writeln!(uart, "flash @0 {:08x}", flash_word(0));
        }
        b"ports" => {
            // Answers "is the second UART actually there" without a bitstream rebuild.
            // SCR is eight bits of scratch that do nothing else, so writing a pattern
            // and reading it back distinguishes a peripheral that exists from an address
            // that decodes to nothing -- which on this bus returns zeros rather than
            // faulting, and so is otherwise invisible.
            for (index, &base) in target::UART_BASES.iter().enumerate() {
                let present = scratch_responds(base);
                let _ = writeln!(uart, "  {} {:08x} {}", index, base,
                                 if present { "ok" } else { "NO RESPONSE" });
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
            // Every register read below is side-effect free. In particular this does NOT
            // read the claim register: that would take an interrupt away from the handler
            // and never complete it, killing the console from a diagnostic command. See
            // `Plic::claim`.
            let plic = plic::Plic::new(target::PLIC_BASE);
            let _ = writeln!(uart, "plic  @{:08x} pending {:08x} enabled {:08x}",
                             target::PLIC_BASE, plic.pending(), plic.enabled());
            for console in 0..target::UART_BASES.len() {
                let (interrupts, stalls, buffered) = irq::stats(console);
                // `lost` counts LSR reads that found an error bit set -- an
                // overrun or a framing error, both of which mean input that
                // never reached the shell. Zero is the only good value; a
                // number that climbs while nothing is typed is a noisy line.
                let _ = writeln!(uart,
                                 "  {} src {} irqs {} stalls {} buffered {} lost {}",
                                 console, target::UART_IRQS[console],
                                 interrupts, stalls, buffered,
                                 uart::error_reads(console));
            }
            // The Type-C sources, one per FUSB302B rather than one for both.
            //
            // Separately visible is half the point of giving them a source
            // each: a TARGET count that climbs while AUX stays at zero says
            // which connector something is happening on, and a shared source
            // could only ever have shown the sum. The `enabled` word above is
            // the other half -- a port whose bit is clear there is one the
            // handler has masked and `typec` has not finished servicing.
            for (port, &source) in target::TYPE_C_IRQS.iter().enumerate() {
                let _ = writeln!(uart, "  type-c {:6} src {} irqs {}",
                                 fusb302::Port::ALL[port].name(), source,
                                 irq::type_c_interrupts(port));
            }
            // The deferred log's own health. A handler may not print, so it
            // records; if the ring fills, records are dropped rather than the
            // handler stalling, and this is where that shows up. A nonzero
            // count is not a failure by itself -- it means a burst outran the
            // shell -- but a count that keeps climbing means events are being
            // lost continuously, which is the state in which a fault becomes
            // invisible. See `src/events.rs`.
            let _ = writeln!(uart, "  log  waiting {} dropped {}",
                             events::waiting(), events::dropped());
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
            // The low word alone would have divided in one instruction and
            // wrapped every 71.6 s at 60 MHz (see `src/clock.rs`), which is
            // shorter than the intervals this line exists to be compared over.
            let mtime = timer::mtime();
            let _ = writeln!(uart, "  uptime  {}  ticks {}  period {} ms  {}",
                             log::now(), ticks, timer::PERIOD_MS,
                             if timer::running() { "running" } else { "STOPPED" });
            let _ = writeln!(uart, "  clint   @{:08x}  mtime {:08x}:{:08x} at {} Hz",
                             target::CLINT_BASE, (mtime >> 32) as u32,
                             mtime as u32, target::TIME_HZ);
            let _ = writeln!(uart, "  cost    worst {} ticks  late worst {} ticks",
                             cost, late);
        }
        b"stats" => metrics::command(uart),
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
        b"phy" => board_phy(uart),
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
            let _ = writeln!(uart, "log pushed {} tag samples, waiting {} dropped {}",
                             pushed, events::waiting(), events::dropped());
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
            let _ = writeln!(uart, "log pushed {} of {}, waiting {} dropped {}",
                             pushed, count, events::waiting(), events::dropped());
        }
        b"sideband" => board_sideband(uart, rest),
        b"read" => match parse_hex(rest) {
            Some(offset) => {
                // Bounded to the 4 MiB the flash actually holds. Above that the address
                // aliases back onto offset 0, which would read as real data.
                if offset >= 0x0040_0000 {
                    let _ = writeln!(uart, "offset past 4 MiB; it would alias to 0");
                } else {
                    let word = flash_word(offset as usize & !3);
                    let _ = writeln!(uart, "flash @{:06x} {:08x}", offset, word);
                }
            }
            None => {
                let _ = writeln!(uart, "usage: read <hex offset>");
            }
        },
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

            let _ = writeln!(uart, "sum   {:08x} {}", sum,
                             if sum == 0xacf1_3568 { "ok" } else { "BAD" });
            let _ = writeln!(uart, "prod  {:08x} {}", prod,
                             if prod == 0x369d_0368 { "ok" } else { "BAD" });
            let _ = writeln!(uart, "@0    {:08x} {}", f0,
                             if f0 == 0x6150_00ff { "ok" } else { "BAD" });
            let _ = writeln!(uart, "@40   {:08x} {}", f40,
                             if f40 == 0x2a55_8800 { "ok" } else { "BAD" });

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
            for millis in [0u32, 1, 999, 1_000, 61_000,
                           999_999_999, 1_000_000_000] {
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
        b"hrtest" => {
            // Round-trip one word so the HyperRAM path can be checked without staging a
            // whole image.
            hyperram::write_header(0, 0);
            match hyperram::staged() {
                Ok(_) => {
                    let _ = writeln!(uart,
                        "hyperram round-trip BAD: zero length should be rejected");
                }
                // `Length` specifically: the magic was written and read back, which is
                // the round trip this checks. `NoMagic` or `Silent` would mean the word
                // did not survive.
                Err(hyperram::Reject::Length) => {
                    let _ = writeln!(uart, "hyperram write+read ok");
                }
                Err(_) => {
                    let _ = writeln!(uart,
                        "hyperram round-trip BAD: the magic did not read back");
                }
            }
            hyperram::invalidate();
        }
        b"reset" => {
            let _ = writeln!(uart, "restarting");
            reboot();
        }
        _ => {
            let _ = writeln!(uart, "unknown command; try `help`");
        }
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
                let _ = writeln!(uart, "no LED of that colour; they are red, \
                                        orange, yellow, green, blue, violet");
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
        let _ = writeln!(uart, "  {:7} {:3}  driven by {}",
                         colour,
                         if pins.led_lit(led) { "on" } else { "off" },
                         owner);
    }
    let _ = writeln!(uart, "  button  {}",
                     if pins.button() { "pressed" } else { "released" });
    let _ = writeln!(uart, "  power monitor {}",
                     if pins.power_monitor_down() { "POWERED DOWN" } else { "running" });
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
    let (bus_select, label) = match which {
        b"" | b"power" => (bus::BUS_POWER_MONITOR, "power_monitor"),
        b"target" => (bus::BUS_TARGET_C, "target_type_c"),
        b"aux" => (bus::BUS_AUX_C, "aux_type_c"),
        _ => {
            let _ = writeln!(uart, "usage: i2c [power|target|aux]");
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

    let _ = writeln!(uart, "i2c   @{:08x} prescale {} bus {} ({})",
                     bus.i2c_base(), bus.prescale(), bus_select, label);

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
            bus_select, address, bus::pac195x::REG_MANUFACTURER_ID, &mut id) {
            Ok(()) => id[0],
            Err(error) => {
                let _ = writeln!(uart, "  {:02x} id read failed: {}",
                                 address, error.as_str());
                continue;
            }
        };
        if manufacturer != bus::pac195x::MANUFACTURER_MICROCHIP {
            let _ = writeln!(uart, "  {:02x} manufacturer {:02x}, not a PAC195x",
                             address, manufacturer);
            continue;
        }
        let mut product = [0u8; 1];
        let mut revision = [0u8; 1];
        let _ = bus.read_registers(bus_select, address,
                                   bus::pac195x::REG_PRODUCT_ID, &mut product);
        let _ = bus.read_registers(bus_select, address,
                                   bus::pac195x::REG_REVISION_ID, &mut revision);
        let _ = writeln!(uart, "  {:02x} {} manufacturer {:02x} revision {:02x}",
                         address, bus::pac195x::product_name(product[0]),
                         manufacturer, revision[0]);
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
                let _ = writeln!(uart, "no port of that name; they are \
                                        target_a, target_c, aux, control");
                return;
            }
        };
        match parse_decimal(value) {
            // A floor is a magnitude. The bipolar full-scale current is 5 A,
            // so anything higher can never be crossed.
            Some(milliamps) if milliamps <= 5000 =>
                devices.power.set_floor(channel, milliamps * 1000),
            _ => {
                let _ = writeln!(uart, "usage: power floor <port> <mA>  \
                                        (0..5000)");
                return;
            }
        }
    } else if !rest.is_empty() {
        let _ = writeln!(uart, "usage: power [floor <port> <mA>]");
        return;
    }

    // The header carries the age, because every number under it is that old.
    //
    // Not decoration. A poller that has stopped leaves four voltages that are
    // individually plausible and jointly a lie, and there is nothing in them to
    // say so -- which is the same shape of failure as a stale bus select. The
    // age is the only thing on this screen that can contradict them.
    let _ = write!(uart, "power @{:02x}  poll {} ms  change {} mA  ",
                   power::ADDRESS, power::INTERVAL_MS,
                   power::CHANGE_UA / 1000);
    match devices.power.age() {
        power::Age::Millis(ms) => {
            let _ = writeln!(uart, "sampled {} ms ago", ms);
        }
        power::Age::Older => {
            let _ = writeln!(uart, "sampled OVER {} s ago -- the poll has \
                                    stopped", power::AGE_LIMIT_MS / 1000);
        }
        power::Age::Never => {
            let _ = writeln!(uart, "NO SAMPLE YET");
        }
    }

    match devices.power.latest() {
        Some(sample) => {
            for channel in 0..4 {
                let floor = devices.power.floor(channel);
                // A space, not a stamp: this is the reply to a typed command.
                // The indent it produces is the one the rows always had.
                power::report(uart, &" ", channel, &sample.readings[channel],
                              sample.readings[channel].current_ua.unsigned_abs() >= floor);
            }
        }
        // No rails printed, rather than zeros or dashes. Two polls after reset
        // there is genuinely nothing to say -- one to issue a REFRESH and one to
        // read what it latched -- and inventing a row per channel would put four
        // measurements on screen that were never measured.
        None => {
            let _ = writeln!(uart, "  the 50 ms poll has not completed one yet; \
                                    last phase {}", devices.power.phase());
        }
    }

    for channel in 0..4 {
        let _ = writeln!(uart, "  {:8} floor {}.{:03} mA",
                         power::PORTS[channel],
                         devices.power.floor(channel) / 1000,
                         devices.power.floor(channel) % 1000);
    }
    if devices.power.failures > 0 {
        let _ = writeln!(uart, "  {} failed poll(s) since the last good one",
                         devices.power.failures);
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
fn board_phy(uart: &mut Uart) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return board_absent(uart),
    };
    let phy = ulpi::Ulpi::new(board.ulpi);

    // A named read, so one failure reports which register it was on rather than
    // leaving the caller to count lines.
    let read = |uart: &mut Uart, name: &str, address: u8| -> Option<u8> {
        match phy.read(address) {
            Ok(value) => {
                let _ = writeln!(uart, "  {:16} {:02x}  {:02x}", name, address, value);
                Some(value)
            }
            Err(error) => {
                let _ = writeln!(uart, "  {:16} {:02x}  {}", name, address,
                                 error.as_str());
                None
            }
        }
    };

    let _ = writeln!(uart, "ulpi  @{:08x}  target_phy", board.ulpi);
    let _ = writeln!(uart, "  register         at  value");

    let vendor_low = read(uart, "vendor id low", ulpi::usb3343::REG_VENDOR_ID_LOW);
    let vendor_high = read(uart, "vendor id high",
                           ulpi::usb3343::REG_VENDOR_ID_LOW + 1);
    let product_low = read(uart, "product id low", ulpi::usb3343::REG_PRODUCT_ID_LOW);
    let product_high = read(uart, "product id high",
                            ulpi::usb3343::REG_PRODUCT_ID_LOW + 1);
    read(uart, "function control", ulpi::usb3343::REG_FUNCTION_CONTROL);
    read(uart, "otg control", ulpi::usb3343::REG_OTG_CONTROL);
    let debug = read(uart, "debug", ulpi::usb3343::REG_DEBUG);

    match (vendor_low, vendor_high, product_low, product_high) {
        (Some(vl), Some(vh), Some(pl), Some(ph)) => {
            let vendor = ((vh as u16) << 8) | vl as u16;
            let product = ((ph as u16) << 8) | pl as u16;
            let _ = writeln!(uart, "  vendor {:04x} product {:04x} {}",
                             vendor, product,
                             if vendor == ulpi::usb3343::VENDOR_ID
                                 && product == ulpi::usb3343::PRODUCT_ID {
                                 "USB3343 ok"
                             } else {
                                 "NOT a USB3343"
                             });
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
        let _ = writeln!(uart, "  linestate dp {} dm {}", debug & 1,
                         (debug >> 1) & 1);
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
        let _ = writeln!(uart, "  scratch walk {:02x}  {}", lines_ok,
                         if lines_ok == 0xff { "all 8 data lines ok" }
                         else { "A DATA LINE IS STUCK" });
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
        let _ = writeln!(uart, "  reporting state {} events {} error {} \
                                reconfigured {}",
                         value & sideband::STATE_MASK,
                         (value & sideband::EVENTS != 0) as u8,
                         (value & sideband::ERROR != 0) as u8,
                         (value & sideband::RECONFIGURED != 0) as u8);
    } else {
        // Do NOT decode the payload bits here. With OWN clear they are stored
        // and ignored, and printing them under a heading that reads like a
        // report would say the link is announcing something it is not -- which
        // is the one lie a diagnostic for a debug link must not tell.
        let _ = writeln!(uart, "  reporting the fabric's own state; these bits \
                                are stored and unused");
    }
    // Printed either way: neither the port request nor the byte channel is part
    // of the payload, so OWN says nothing about them.
    let _ = writeln!(uart, "  CONTROL port {}",
                     if value & sideband::ADVERTISE != 0 { "REQUESTED" }
                     else { "not requested" });
    let (received, count) = link.received();
    let _ = writeln!(uart, "  message out {:02x}, in {:02x} after {} byte(s)",
                     link.message(), received, count);
}

fn sideband_usage(uart: &mut Uart) {
    let _ = writeln!(uart, "usage: sideband [ctrl [tx]]");
    let _ = writeln!(uart, "  ctrl bit 7 takes the link from the fabric, \
                            bit 5 asks for the CONTROL port");
    let _ = writeln!(uart, "  tx   the byte a PING returns");
}

/// Drop leading and trailing spaces. The line editor does not, and an argument
/// compared against `b"on"` must not have one on either end.
fn trim(text: &[u8]) -> &[u8] {
    let start = text.iter().position(|&b| b != b' ').unwrap_or(text.len());
    let end = text.iter().rposition(|&b| b != b' ').map_or(start, |i| i + 1);
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
/// the streaming sink in `ecp5-test/riscv/jtag_stage.py` moves 32 KiB over the same wire
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
    let mut pending: Option<u8> = None;

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

        // HyperRAM is 16 bits wide, so bytes are paired little-endian.
        match pending.take() {
            None => pending = Some(byte),
            Some(low) => hyperram::write_word((low as u16) | ((byte as u16) << 8)),
        }
    }

    // An odd-length image still has to fill its final word.
    if let Some(low) = pending {
        hyperram::write_word(low as u16);
    }

    let crc = crc.finish();
    hyperram::write_header(len, crc);
    let _ = writeln!(uart, "staged {} bytes, crc {:08x}; rebooting", received, crc);

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
    if digits == 0 { None } else { Some(value) }
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
    let argument = core::str::from_utf8(rest).unwrap_or("").trim();

    if argument.is_empty() {
        let (control_in, aux_in) = vbus::inputs();
        let _ = writeln!(uart, "vbus {:02x}  c{} a{} t{}  in c{} a{}",
                         vbus::state(),
                         vbus::is_closed(vbus::Source::Control) as u8,
                         vbus::is_closed(vbus::Source::Aux) as u8,
                         vbus::is_closed(vbus::Source::TargetC) as u8,
                         control_in as u8, aux_in as u8);
        return;
    }
    if argument == "off" {
        vbus::open_all();
        if let Some(bus) = devices.bus.as_mut() {
            let _ = fusb302::configure(bus, fusb302::Port::Target);
        }
        let _ = writeln!(uart, "open");
        return;
    }
    let charge = match argument {
        "charge" => Some(fusb302::HostCurrent::Default),
        "charge 1.5" => Some(fusb302::HostCurrent::A1_5),
        "charge 3" => Some(fusb302::HostCurrent::A3),
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
            Ok(mv) => { let _ = writeln!(uart, "on {} mV", mv); }
            Err(error) => {
                let _ = fusb302::configure(bus, fusb302::Port::Target);
                vbus_refusal(uart, error);
            }
        }
        return;
    }
    if let Some(which) = argument.strip_prefix("input").map(str::trim) {
        let ok = match which {
            "control" => vbus::prefer_control(&devices.power),
            "both" => { vbus::allow_all_inputs(); true }
            _ => false,
        };
        let _ = writeln!(uart, "input {}", if ok { "set" } else { "no" });
        return;
    }
    match vbus::Source::parse(argument) {
        None => { let _ = writeln!(uart, "?"); }
        Some(source) => match vbus::close(source, &devices.power) {
            Ok(mv) => { let _ = writeln!(uart, "on {} mV", mv); }
            Err(error) => vbus_refusal(uart, error),
        },
    }
}

fn vbus_refusal(uart: &mut Uart, refusal: vbus::Refusal) {
    match refusal {
        vbus::Refusal::TooHigh(mv) => { let _ = writeln!(uart, "no: {} mV high", mv); }
        vbus::Refusal::TooLow(mv) => { let _ = writeln!(uart, "no: {} mV low", mv); }
        vbus::Refusal::Stale => { let _ = writeln!(uart, "no: stale"); }
    }
}
