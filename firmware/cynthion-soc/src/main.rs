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
//!   Shell reads identically from a user's point of view: the byte is already
//!   collected before the loop asks, so a console busy printing need not be back at
//!   `uart.get()` in time.
//! - Transmit is still a bounded spin in `Uart::put`, deliberately -- see `IER_ERBFI`
//!   in `src/uart.rs` for why enabling the transmit-empty interrupt on this peripheral
//!   would be a storm, not a service.

// UNCONDITIONAL. `cargo test` cannot build this crate for the host whatever the
// lang items say -- `src/selftest.rs` is RISC-V asm, which does not assemble for
// x86. The unit tests live in `firmware/cynthion-soc-tests`, which includes the
// pure modules and leaves this half alone (#337).
#![no_std]
#![no_main]

use core::fmt::Write;
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

mod bench;
// The HyperRAM BIST engine's driver (#226). Built into every image rather than
// `#[cfg]`-gated: `Bist::present` reads the engine's ident and refuses to
// measure without it, so a shipping image says "this bitstream has no engine"
// where a gated one could not say anything at all.
mod bist;
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
// The exception handler. Without one, riscv-rt links `abort` and a bus fault is
// an infinite loop with no output -- indistinguishable from the hang it
// replaced (#409).
mod fault;
// The SPI flash CONTROLLER, not the memory map: every opcode the map's FSM
// cannot issue -- JEDEC, SFDP, status, erase, page program (#442).
mod flash;
mod fusb302;
mod gpio;
// The orange LED, toggled by a periodic RTIC task. If it stops, the OS is dead
// (#411). One task, one LED, no gateware -- the GPIO peripheral's push-pull mode
// is the whole handover.
mod heartbeat;
mod hyperram;
mod info;
// THE peripheral bring-up contract (#315): one `<peripheral>_init()` per part,
// ordered, verified, non-destructive and re-runnable. `boot` below runs the
// CPU's own facilities and hands the board to it.
mod init;
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



/// The console the banner, the bootloader and any panic speak on.
fn primary() -> Uart {
    Uart::new(target::UART_BASES[0])
}

/// A handle on console `index`. `Uart` is one `usize`, so this is free.
pub(crate) fn primary_for(index: usize) -> Uart {
    Uart::new(target::UART_BASES[index.min(target::UART_BASES.len() - 1)])
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
    // timed and interruptible -- the other way round counts the whole of boot
    // under the wrong performance-counter events and stamps `000000.000`.
    //
    // The CPU's four performance counters, pointed at the events #115 names.
    // FIRST, because they free-run from reset: a selector written late means
    // everything counted before it was a different event, so the I2C
    // configuration and both PHY probes count as something else.
    sched::init();

    // The 1 ms tick. Before the peripherals, so a slow one is visibly slow: the
    // stamp on every line below comes from this, and without it a peripheral
    // taking 40 ms to answer is indistinguishable from one taking none.
    //
    // `mstatus.MIE` is still clear, so the first tick simply stays pending until
    // `irq::init` below turns delivery on. A pending tick is not a lost one.
    timer::start();

    // The interrupt CONTROLLER, and no source. Each peripheral claims its own
    // below, once it is in a state where an interrupt from it would mean
    // something -- see `Plic::claim_source`. Enabling delivery with nothing
    // enabled cannot deliver anything, which lets this come before the parts.
    irq::init();

    // ---- 2. THE BOARD ---------------------------------------------------
    //
    // One `<peripheral>_init()` per part, in `src/init.rs`, ordered so that the
    // console comes first and every step after it can report what it did and
    // what it read back. It banners on the way through, because only the step
    // that establishes the console knows the console can carry it.
    //
    // It completes inside `#[init]` rather than becoming a task, and that IS
    // the ordering guarantee: `devices` does not exist as a shared resource
    // until `#[init]` returns, so nothing can run against a half-established
    // board.
    let mut devices = Devices::new();
    let mut console = primary();
    init::bringup(&mut console, &mut devices, true);

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
/// Open every VBUS switch. The first thing a panic does, before it prints.
///
/// A panicking CPU spins forever with whatever the VBUS register was last set
/// to, and a CPU reset does not clear it -- so a board that panicked
/// mid-`vbus control` goes on passing host power to an attached target with
/// nothing running (#315). One store, and the gate is combinational, so all
/// four open in the same cycle.
///
/// `#[inline(never)]`: inlined into the handler it costs kilobytes of `.text`
/// and `.rodata`, because panic-formatting machinery elsewhere in the image
/// stops being eliminated. Out of line it costs twenty bytes.
#[inline(never)]
#[cold]
fn open_vbus_on_panic() {
    if target::BOARD.is_some() {
        vbus::open_all();
    }
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    // POWER FIRST, before anything is printed.
    open_vbus_on_panic();

    // A fresh handle rather than the one that panicked: taking it by value cannot
    // deadlock, and a `Uart` is nothing but an address so constructing one costs nothing.
    //
    // Deliberately NOT `init()`ed. Initialising clears the transmit FIFO, which would
    // discard whatever the panicking code had already queued -- quite possibly the last
    // line printed before things went wrong, which is the one worth having. LCR resets to
    // 0 on both targets, so DLAB is clear and THR is reachable without any setup.
    let mut uart = primary();
    // LOCATION, not the whole `PanicInfo`. What resolves the ambiguity this
    // handler exists for -- a panicking CPU against a hung one -- is that a line
    // appeared at all, and then where. The message payload costs the formatting
    // machinery; `file:line:col` is a `&str` and two integers.
    match info.location() {
        Some(at) => {
            let _ = writeln!(uart, "\n*** PANIC at {}:{}:{}",
                             at.file(), at.line(), at.column());
        }
        None => {
            let _ = writeln!(uart, "\n*** PANIC, location unknown");
        }
    }
    loop {}
}


