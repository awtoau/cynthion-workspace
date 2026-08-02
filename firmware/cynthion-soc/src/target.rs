//! Where the peripherals are, and nothing else.
//!
//! This used to be two files -- `target_soc.rs` and `target_qemu.rs` -- each with
//! its own console driver, because the SoC's bespoke console and QEMU's 16550
//! were different hardware with different register maps. They are now the same
//! hardware: `ecp5-test/riscv/uart16550.py` is an NS16550A and `-M virt` is an
//! NS16550A, so `src/uart.rs` drives both and the difference collapses to a list
//! of addresses and a linker script.
//!
//! That is the prize. `scripts/soc_test.py` boots this firmware under QEMU and
//! asserts what its shell says; that gate is only evidence about the board to the
//! extent that the two builds share source. Two console drivers meant the gate
//! tested one of them. One driver means it tests the one that ships.
//!
//! Addresses below are read out of the two designs, not assumed.
//!
//! The hardware ones come from `cynthion_soc_pac::base`, which
//! `scripts/soc_generate_pac.py` writes out of `HelloSoC.decoder.bus.memory_map`
//! -- the SoC's own description of itself. Nothing here is transcribed any more.
//! Move a peripheral in the gateware and the constant follows it; rename one and
//! this file stops compiling. That is the whole point: every address in this file
//! used to be a number a human had copied out of `vexii_hello_soc.py`, and three
//! peripherals were added in one sitting that way.
//!
//! The QEMU ones come from the machine's own device tree:
//!
//!     qemu-system-riscv32 -M virt -machine dumpdtb=tmp/virt.dtb -display none
//!     dtc -I dtb -O dts tmp/virt.dtb
//!
//! gives `serial@10000000 { compatible = "ns16550a"; }` and `memory@80000000`.
//!
//! **The PAC supplies addresses and nothing else, and that is deliberate.** It
//! also contains svd2rust register accessors, which this firmware does not use,
//! for two independent reasons. The first is this file's reason for existing: a
//! PAC generated from our map hardcodes our bases, so a driver written against
//! `pac::console` would be a driver that cannot run under QEMU, and the shared
//! source that makes `scripts/soc_test.py` evidence about the board would be
//! gone. The second is that svd2rust emits one natural-width volatile access per
//! register, while every CSR here sits behind an `amaranth_soc` multiplexer with
//! a granularity of 8 bits, where a multi-byte register is read by latching a
//! shadow from its low byte and written by committing on its high byte -- see
//! `Gpio::set_mode` in `src/gpio.rs`. A `u16` access is a different bus
//! transaction from the two ordered byte accesses the hardware specifies.

/// Every 16550 this build can talk on. The first is the primary console: the one
/// that gets the boot banner, the bootloader's reports and any panic.
///
/// The order is the address map's order, and firmware treats index 0 specially
/// and the rest identically. Adding a third is a line here plus an instance in
/// the gateware.
#[cfg(not(feature = "qemu"))]
pub const UART_BASES: &[usize] = &[
    // The USB CDC-ACM console on the AUX port -- an ordinary /dev/ttyACM node.
    cynthion_soc_pac::base::CONSOLE,
    // The Apollo-facing serial port on the shared JTAG pins (R14/T14). See the
    // comment on APOLLO_UART_BASE in ecp5-test/riscv/vexii_hello_soc.py for why
    // firmware must never speak first on this one.
    cynthion_soc_pac::base::APOLLO_UART,
];

/// `virt` has one UART. The multi-console code paths still run -- the loop just
/// iterates once -- so the shared logic is exercised either way.
#[cfg(feature = "qemu")]
pub const UART_BASES: &[usize] = &[0x1000_0000];

/// The interrupt controller.
///
/// A standard RISC-V PLIC on both targets, which is the whole reason
/// `src/plic.rs` needs no `#[cfg]`.
#[cfg(not(feature = "qemu"))]
pub const PLIC_BASE: usize = cynthion_soc_pac::base::PLIC;

/// `virt`'s PLIC, read out of the device tree rather than assumed:
///
///     qemu-system-riscv32 -M virt -machine dumpdtb=tmp/virt.dtb -display none
///     dtc -I dtb -O dts tmp/virt.dtb
///
/// gives `plic@c000000 { compatible = "sifive,plic-1.0.0", "riscv,plic0"; }`
/// with `interrupts-extended = <cpu 11>, <cpu 9>` -- 11 being the machine
/// external interrupt, so context 0 is hart 0 in machine mode on this machine
/// exactly as it is on the SoC.
#[cfg(feature = "qemu")]
pub const PLIC_BASE: usize = 0x0c00_0000;

/// The PLIC source number each entry of `UART_BASES` is wired to, in the same
/// order.
///
/// Source 0 is reserved by the specification as "nothing pending", so real
/// sources start at 1. These come from `HelloSoC.interrupt_sources`, which is
/// declared immediately below the `plic.sources[...]` wiring it describes -- so
/// the number the firmware enables and the wire that raises it cannot disagree.
#[cfg(not(feature = "qemu"))]
pub const UART_IRQS: &[u32] = &[
    cynthion_soc_pac::base::CONSOLE_IRQ,
    cynthion_soc_pac::base::APOLLO_UART_IRQ,
];

/// `virt` puts its 16550 on source 10 -- `serial@10000000 { interrupts = <0x0a>; }`
/// in the device tree above.
#[cfg(feature = "qemu")]
pub const UART_IRQS: &[u32] = &[10];

// A UART with no source number, or a source number with no UART, would be a
// console that never interrupts or a handler that dispatches to nothing. Both
// are silent failures; catch them where they are declared.
const _: () = assert!(UART_BASES.len() == UART_IRQS.len());

/// How many times the `time` CSR advances per second.
///
/// On the board it is a counter incremented once per `sync` cycle inside the CPU
/// wrapper (`rdtime` in `ecp5-test/riscv/vexii_cpu.py`), so it is the `sync`
/// frequency and nothing else -- raise `SYNC_MHZ` in the gateware and this must
/// follow, or every interval built on it stretches or shrinks in proportion. The
/// symptom of forgetting is a "50 ms" poll that runs at 37 ms, which nothing
/// fails on and nobody notices.
#[cfg(not(feature = "qemu"))]
pub const TIME_HZ: u32 = 60_000_000;

/// `virt`'s CLINT runs at 10 MHz -- `timebase-frequency = <0x989680>` in the
/// device tree dumped in the comment on `PLIC_BASE` above. Read out of the
/// machine rather than assumed, like every other constant in this file.
#[cfg(feature = "qemu")]
pub const TIME_HZ: u32 = 10_000_000;

/// Consoles that announce themselves while idle.
///
/// Index 0 only, and this is a hardware constraint rather than a preference. The
/// second UART's TX pin (T14) is wired to the same net as JTAG TMS, which the
/// Apollo microcontroller drives whenever it is configuring the FPGA or scanning
/// the chain. The FPGA tri-states its side except while transmitting, so the two
/// only contend if the FPGA transmits unbidden. A console that re-banners every
/// couple of seconds does exactly that, forever.
///
/// So: the FPGA never speaks first on a shared pin. Type on the Apollo tty and it
/// answers; leave it alone and it is electrically absent. That bounds the
/// contention window to "a human is using this port", which is not a window in
/// which anyone is also running `apollo jtag-scan`.
pub const ANNOUNCING: usize = 1;

/// Where the board's own peripherals are, or `None` on a target that has no
/// board.
///
/// One `Option` rather than three, and an `Option` rather than a `#[cfg]`'d
/// stand-in like `flash_word`'s. The reason is the difference between the two
/// cases: `flash_word` has a QEMU answer because `check` must exercise the same
/// comparison and formatting on both targets, and a plausible constant does
/// that. An LED, an I2C bus and a one-wire link to a microcontroller have no
/// plausible constant. A model of them under QEMU would only ever confirm that
/// the model agrees with the driver, which is worth nothing, and it would have
/// to be maintained forever alongside the gateware it is pretending to be.
///
/// So the shell says so instead. `led`, `i2c` and `sideband` all exist in the
/// QEMU build, all parse their arguments, and all report that the hardware is
/// absent -- which means `scripts/soc_test.py` still checks that they are
/// registered, spelled correctly and reachable, and does not pretend to check
/// anything else. What the drivers themselves do is checked in
/// `scripts/soc_board_sim.py`, against the gateware, and on the board.
pub struct Board {
    /// `amaranth_soc.gpio.Peripheral`: six LEDs, the power monitor's PWRDN, and
    /// the USER button.
    pub gpio: usize,
    /// `i2c_master.I2CMaster` on the power monitor's bus.
    pub i2c: usize,
    /// `sideband_csr.SidebandControl`, which decides what the FPGA_ADV link
    /// reports.
    pub sideband: usize,
    /// PRER for the I2C bus. `f_SCL = f_sync / (5 * (PRER + 1))`, so at 60 MHz
    /// 149 gives 80 kHz -- see the bit-timing section of
    /// `ecp5-test/riscv/i2c_master.py` for why 80 and not 100.
    ///
    /// This is a constant here rather than computed, because the firmware does
    /// not know what `sync` runs at: `SYNC_MHZ` lives in the gateware. If the
    /// clock changes, this changes with it, and the symptom of forgetting is a
    /// bus that violates its own setup times and answers most of the time.
    pub i2c_prescale: u16,
}

#[cfg(not(feature = "qemu"))]
pub const BOARD: Option<Board> = Some(Board {
    gpio: cynthion_soc_pac::base::BOARD_GPIO,
    i2c: cynthion_soc_pac::base::BOARD_I2C,
    sideband: cynthion_soc_pac::base::BOARD_SIDEBAND,
    i2c_prescale: 149,
});

#[cfg(feature = "qemu")]
pub const BOARD: Option<Board> = None;

/// Memory-mapped configuration flash, `main=1 exe=1` so the D-cache backs it.
/// Offset 0 holds the bitstream, which is why `615000ff` is a known-good value.
///
/// Note for anyone reading both branches below: QEMU's `virt` puts its 16550 at
/// this exact address. That collision is why flash is reached through
/// `flash_word()` rather than by the shell dereferencing a constant.
#[cfg(not(feature = "qemu"))]
const FLASH_BASE: usize = cynthion_soc_pac::base::SPIFLASH;

/// One 32-bit word from the memory-mapped configuration flash.
///
/// `offset` is a byte offset from the start of flash and must already be word
/// aligned; the caller bounds it to the 4 MiB the part holds, because above that
/// the address aliases back onto offset 0 and would read as real data.
#[cfg(not(feature = "qemu"))]
pub fn flash_word(offset: usize) -> u32 {
    // SAFETY: inside the 4 MiB flash window, which the SoC decodes as
    // `main=1 exe=1` memory. Volatile because the mapping is a device, not RAM
    // the compiler may cache.
    unsafe { core::ptr::read_volatile((FLASH_BASE + offset) as *const u32) }
}

/// Stand-in for the SoC's memory-mapped configuration flash.
///
/// `virt` has no flash, and the address the SoC uses for it (0x1000_0000) is
/// where this machine's UART lives -- so the hardware path cannot simply be left
/// in place. A read of an unmapped `virt` address is worse than wrong: it raises
/// a load access fault, the default trap handler never returns, and `check` would
/// hang the test rather than fail it.
///
/// The two constants are the values the real part holds at those offsets
/// (bitstream header at 0, and 0x40), so `check` exercises the same comparison
/// and formatting code on both targets and prints `ok` on both. This is the one
/// place where a QEMU pass is weaker than hardware: it confirms `check` reports
/// what it read, not what the flash contains. Everything else `check` covers --
/// the arithmetic, the CPU, the formatting -- is the real thing.
#[cfg(feature = "qemu")]
pub fn flash_word(offset: usize) -> u32 {
    match offset {
        0x00 => 0x6150_00ff,
        0x40 => 0x2a55_8800,
        _ => 0,
    }
}
