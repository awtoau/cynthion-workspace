//! Where the peripherals are, and nothing else.
//!
//! Every difference between the FPGA build and the QEMU build lives in this file
//! and in a linker script. `src/uart.rs` and `src/plic.rs` are compiled unchanged
//! for both, because the SoC's console is an NS16550A and `-M virt`'s is an
//! NS16550A, and both have a standard PLIC. `scripts/soc_test.py` is evidence
//! about the board only to the extent that the two builds share source, so
//! resist adding a `#[cfg]` anywhere else.
//!
//! ## Where the addresses come from
//!
//!   * **Hardware:** `cynthion_soc_pac::base`, which `scripts/soc_generate_pac.py`
//!     writes out of `HelloSoC.decoder.bus.memory_map` -- the SoC's own
//!     description of itself. Move a peripheral in the gateware and the constant
//!     follows; rename one and this file stops compiling. Nothing here is
//!     transcribed by hand.
//!   * **QEMU:** the machine's own device tree.
//!
//!         qemu-system-riscv32 -M virt -machine dumpdtb=tmp/virt.dtb -display none
//!         dtc -I dtb -O dts tmp/virt.dtb
//!
//!     gives `serial@10000000 { compatible = "ns16550a"; }` and `memory@80000000`.
//!
//! ## The PAC supplies addresses and nothing else
//!
//! Its svd2rust register accessors are deliberately unused, for two independent
//! reasons:
//!
//!   * A PAC generated from our map hardcodes our bases, so a driver written
//!     against `pac::console` could not run under QEMU -- and the shared source
//!     that makes the test gate meaningful would be gone.
//!   * svd2rust emits one natural-width volatile access per register, but every
//!     CSR here sits behind an `amaranth_soc` multiplexer with granularity 8,
//!     where a multi-byte register is read by latching a shadow from its low byte
//!     and written by committing on its high byte (see `Gpio::set_mode` in
//!     `src/gpio.rs`). A `u16` access is a different bus transaction from the two
//!     ordered byte accesses the hardware specifies.
//!
//! Hand-transcribed constants versus a generated PAC: `docs/decisions.md`.

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

/// The core-local interruptor: `mtime` and `mtimecmp`, and so the 1 ms tick.
///
/// A standard RISC-V CLINT on both targets, which is the whole reason
/// `src/timer.rs` needs no `#[cfg]` -- the same reason `src/plic.rs` needs none.
#[cfg(not(feature = "qemu"))]
pub const CLINT_BASE: usize = cynthion_soc_pac::base::CLINT;

/// `virt`'s CLINT, read out of the device tree rather than assumed:
///
///     qemu-system-riscv32 -M virt -machine dumpdtb=tmp/virt.dtb -display none
///     dtc -I dtb -O dts tmp/virt.dtb
///
/// gives `clint@2000000 { compatible = "sifive,clint0", "riscv,clint0"; }` with
/// `interrupts-extended = <cpu 3>, <cpu 7>` -- 3 being the machine software
/// interrupt and 7 the machine timer, which are the two this peripheral drives
/// on the SoC as well.
#[cfg(feature = "qemu")]
pub const CLINT_BASE: usize = 0x0200_0000;

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

/// The gateware's own account of itself -- git ref, build time, `sync`
/// frequency, cache geometry -- or `None` on a target that is not a bitstream.
///
/// Separate from `BOARD` even though both are `Some` on the same target,
/// because they answer different questions. `BOARD` is about hardware attached
/// to the SoC; this is about the SoC. A build that dropped every board
/// peripheral would still want it, and QEMU is not a bitstream at all, so under
/// `-M virt` `info` says so rather than inventing an identity.
///
/// See `ecp5-test/riscv/gateware_id.py` for the register map.
#[cfg(not(feature = "qemu"))]
pub const GATEWARE: Option<usize> = Some(cynthion_soc_pac::base::BOARD_GATEWARE);

#[cfg(feature = "qemu")]
pub const GATEWARE: Option<usize> = None;

/// Where `firmware/cynthion-boot` leaves its one word of boot status, or `None` on a
/// target that has no bootloader under it.
///
/// A block RAM address rather than a peripheral, so it is not in the generated map and
/// cannot come from `cynthion_soc_pac::base`. It is `ORIGIN(BOOT) + LENGTH(BOOT) - 4`
/// in `firmware/cynthion-boot/memory.x` and nowhere else; `scripts/soc_generate_pac.py
/// --check` compares the two.
///
/// `None` under QEMU, and that is the truth rather than a stub: `-M virt` jumps straight
/// to this image's entry point, so nothing has written a status and 0x3fc is not memory.
/// `info` says so instead of printing a number it made up.
#[cfg(not(feature = "qemu"))]
pub const BOOT_STATUS: Option<usize> = Some(0x3fc);

#[cfg(feature = "qemu")]
pub const BOOT_STATUS: Option<usize> = None;

/// High 24 bits of a real status word: "BOT". `firmware/cynthion-boot` writes it with
/// every report, so an uninitialised block RAM word is not mistaken for one.
pub const BOOT_STATUS_MARK: u32 = 0x424f_5400;

/// What each code means, indexed by the low byte of the status word.
///
/// The order is `Status` in `firmware/cynthion-boot/src/main.rs`. A table rather than a
/// match because the shell only ever renders these, and a table cannot acquire a branch.
pub const BOOT_STATUS_TEXT: &[&str] = &[
    "staged image verified and copied",
    "nothing staged",
    "staged image failed its CRC",
    "staged header rejected: bad length",
    "hyperram did not answer",
    "the bootloader panicked",
    "staged image verified but NOT installed: this build boots from flash",
];

/// One PLIC source per FUSB302B `int` line, in `fusb302::Port::ALL` order.
///
/// Not one OR-ed source: a shared level obliges its handler to clear every
/// asserting device before returning, and one source per device removes that
/// obligation rather than documenting it. See `docs/decisions.md` decision 8.
///
/// A slice, empty on a target with no Type-C hardware, so `src/irq.rs` matches a
/// claimed source against it exactly as it does `UART_IRQS` -- an empty slice
/// never matches, and there is no sentinel that could be mistaken for source 0.
/// The index a match yields is the port, which is what the deferral bitmap and
/// `src/typec.rs` are indexed by.
#[cfg(not(feature = "qemu"))]
pub const TYPE_C_IRQS: &[u32] = &[
    cynthion_soc_pac::base::BOARD_I2C_MUX_TARGET_IRQ,
    cynthion_soc_pac::base::BOARD_I2C_MUX_AUX_IRQ,
];

/// `virt` has no Type-C controllers and nothing raises these.
#[cfg(feature = "qemu")]
pub const TYPE_C_IRQS: &[u32] = &[];

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
    /// `i2c_mux.I2CBusMux`: which of the three I2C buses the one controller
    /// drives, and the two Type-C controllers' `int` and `fault` lines.
    pub i2c_mux: usize,
    /// `ulpi_window.UlpiRegisters`, on TARGET_PHY and only on TARGET_PHY.
    ///
    /// One window, not three. AUX carries the USB console this firmware answers
    /// on and CONTROL is shared with Apollo; a register master on either would
    /// corrupt a link something else is using. See the module comment in
    /// `src/ulpi.rs`.
    pub ulpi: usize,
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
    i2c_mux: cynthion_soc_pac::base::BOARD_I2C_MUX,
    ulpi: cynthion_soc_pac::base::BOARD_ULPI,
    i2c_prescale: 149,
});

#[cfg(feature = "qemu")]
pub const BOARD: Option<Board> = None;

/// Where block RAM starts, and how much of it there is.
///
/// The WHOLE window, not this image's slice of it: on the board that is 64 KiB
/// from 0, so the bootloader's first kilobyte -- and the boot status word at
/// 0x3fc that `info` reports -- is inside it. `bram read 3fc` is then a way to
/// see that word raw, next to the sentence `info` makes of it; bounding this to
/// `_ram_start` would have put it out of reach for no gain.
#[cfg(not(feature = "qemu"))]
pub const BRAM_BASE: usize = cynthion_soc_pac::base::RAM;

#[cfg(not(feature = "qemu"))]
pub const BRAM_SIZE: usize = cynthion_soc_pac::base::RAM_SIZE;

/// `virt`'s DRAM, from the device tree dumped in the comment on `PLIC_BASE`:
/// `memory@80000000`. The length is `memory-qemu.x`'s RAM region and not the
/// machine's whole 64 MiB -- the two must agree, because a bound larger than the
/// linker script's would let a read wander outside the region this image was
/// built for and call it block RAM.
#[cfg(feature = "qemu")]
pub const BRAM_BASE: usize = 0x8000_0000;

#[cfg(feature = "qemu")]
pub const BRAM_SIZE: usize = 0x0010_0000;

/// How much HyperRAM the part holds: 8 MiB.
///
/// A size with no base, because nothing in this firmware reaches HyperRAM through
/// the memory window at `cynthion_soc_pac::base::HYPERRAM`. Every access goes over
/// the CSR staging port in `src/hyperram.rs`, whose spins are bounded -- see the
/// comment on `Region::word` in `src/memory.rs` for why a shell command must take
/// the bounded path.
#[cfg(not(feature = "qemu"))]
pub const HYPERRAM_SIZE: usize = cynthion_soc_pac::base::HYPERRAM_SIZE;

/// The same bound on the target with no HyperRAM, so the code that checks against
/// it compiles and runs identically. `virt` answers from the `.bss` stand-in in
/// `src/hyperram.rs` for the first 63 KiB or so and reports "did not answer" above
/// that, which is a real answer about the guard rather than a stub.
#[cfg(feature = "qemu")]
pub const HYPERRAM_SIZE: usize = 0x0080_0000;

/// Memory-mapped configuration flash. Offset 0 holds the bitstream, which is why
/// `615000ff` is a known-good value.
///
/// Whether the D-cache backs this window is `FLASH_CACHED` below, generated from
/// the gateware. It used to be asserted here in prose, and the prose was wrong
/// within a commit of the window being switched to uncached.
///
/// Note for anyone reading both branches below: QEMU's `virt` puts its 16550 at
/// this exact address. That collision is why flash is reached through
/// `flash_word()` rather than by the shell dereferencing a constant.
#[cfg(not(feature = "qemu"))]
const FLASH_BASE: usize = cynthion_soc_pac::base::SPIFLASH;

/// How much flash the memory map decodes: 4 MiB, which is what the part holds.
/// Above it the address aliases back onto offset 0, so a read past the end
/// succeeds and returns offset 0's data. `src/bench.rs` bounds its walk with
/// this.
#[cfg(not(feature = "qemu"))]
pub const FLASH_SIZE: usize = cynthion_soc_pac::base::SPIFLASH_SIZE;

/// The same bound on the target with no flash, so the code that bounds itself
/// against it compiles and runs identically. What `flash_word` returns inside
/// the window is a stand-in; the window is not.
#[cfg(feature = "qemu")]
pub const FLASH_SIZE: usize = 0x0040_0000;

/// The flash window as `(base, size)`, for deciding whether an address is in it.
///
/// `info` classifies each linker symbol by which window it lands in rather than
/// labelling sections from a table, so `.rodata` moving between block RAM and
/// flash changes the report without changing the code that prints it. That
/// property is the point: the previous report said `rodata 11548` on the same
/// line as `of 64512` bytes of block RAM, months after `.rodata` had left it.
///
/// `None` under QEMU is the answer rather than a stub. `virt` has no flash,
/// `memory-qemu.x` links `.rodata` into RAM, and 0x1000_0000 there is the 16550 --
/// so there is no window to classify against and every section is correctly bram.
#[cfg(not(feature = "qemu"))]
pub const FLASH_WINDOW: Option<(usize, usize)> = Some((FLASH_BASE, FLASH_SIZE));

#[cfg(feature = "qemu")]
pub const FLASH_WINDOW: Option<(usize, usize)> = None;

/// Whether the flash window is cached, generated from the SoC's own
/// `FLASH_CACHED`. See `cynthion_soc_pac::base::SPIFLASH_CACHED`.
#[cfg(not(feature = "qemu"))]
pub const FLASH_CACHED: bool = cynthion_soc_pac::base::SPIFLASH_CACHED;

/// Unreachable on this target -- `FLASH_WINDOW` is `None`, so nothing consults
/// this -- but it must exist for the reporting code to compile once rather than
/// twice.
#[cfg(feature = "qemu")]
pub const FLASH_CACHED: bool = false;

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
