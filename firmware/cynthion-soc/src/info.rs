//! What this image is, what CPU it is on, and what gateware it is running inside.
//!
//! Everything here comes from inside the process. Nothing is passed in by a host,
//! and nothing is a constant anyone typed twice.
//!
//!     line       from
//!     ---------  --------------------------------------------------------------
//!     image      build.rs, through `env!("OUT_DIR")/build_info.rs`
//!     tools      the compiler cargo used, its target triple and profile
//!     memory     riscv-rt's linker symbols, so it follows `memory.x`
//!     flash      the generated window, and `FLASH_CACHED` from the SoC script
//!     boot       the linker's reset vector, and the word `cynthion-boot` left
//!                at `target::BOOT_STATUS`
//!     cpu        the core's own `misa` and identity CSRs
//!     trap       `mstatus`, `mtvec`, and the PLIC's threshold and enables
//!     gateware   a CSR in the bitstream (`gateware/soc/peripherals/gateware_id.py`)
//!
//! ## The gateware line is the reason this command exists
//!
//! Firmware and gateware are built separately. `load` replaces the firmware over
//! the console without rebuilding the bitstream, which is the point of it, so the
//! pair that are running together need never have been built together. A firmware
//! addressing a peripheral map it was not built against reads plausible numbers
//! from the wrong registers and reports them, and nothing anywhere says so.
//!
//! So both sides carry the same 32-bit identifier -- the short git hash with bit
//! 31 set when the tree was dirty, which is also what
//! `gateware/build_helpers.py` stamps into the ECP5's USERCODE -- and this
//! command prints both and says when they differ. A mismatch is not always a
//! fault: staging firmware onto a bitstream from an earlier commit is a normal
//! working state. It is a fact worth having on screen before spending a day on a
//! peripheral that moved.
//!
//! `sync_hz` is checked the same way. `target::TIME_HZ` is a hand-maintained copy
//! of `SYNC_MHZ`, and its own comment says the symptom of forgetting is a "50 ms"
//! poll that runs at 37 ms. Now it is checked rather than trusted.
//!
//! ## `misa` understates this core
//!
//! VexiiRiscv hardwires `misa` to 0x40001100 -- `rv32im` -- while being generated
//! `--with-rva --with-rvc`, and the compressed and atomic instructions the shell
//! executes on every line prove it. Both are printed: what the core claims, and
//! what the gateware says it was built with. The disagreement is the core's, and
//! `selftest` is what settles it by running the instructions.

use core::fmt::Write;

use crate::clock::Hz;
use crate::plic;
use crate::target;
use crate::uart::Uart;

/// Provenance, written by `build.rs` at compile time.
pub mod build {
    include!(concat!(env!("OUT_DIR"), "/build_info.rs"));
}

/// The gateware's account of itself: five read-only words in the bitstream.
///
/// Register map and encodings: `gateware/soc/peripherals/gateware_id.py`.
pub mod gateware {
    use core::ptr::read_volatile;

    use crate::target;

    const MAGIC_OFFSET: usize = 0x00;
    const GIT: usize = 0x04;
    const BUILT: usize = 0x08;
    const SYNC_HZ: usize = 0x0c;
    const CPU: usize = 0x10;
    const USB_HZ: usize = 0x14;
    const DIE: usize = 0x18;
    const BUS_FAULT: usize = 0x1c;

    /// "CYN1". A window that decodes to nothing reads as zeros on this bus
    /// rather than faulting, so this is how "the peripheral is there" is told
    /// from "the address answers".
    pub const MAGIC: u32 = 0x4359_4e31;

    pub const CPU_M: u32 = 1 << 24;
    pub const CPU_A: u32 = 1 << 25;
    pub const CPU_C: u32 = 1 << 26;
    pub const CPU_RDTIME: u32 = 1 << 27;

    /// One 32-bit register, low byte first.
    ///
    /// Four byte reads rather than one word read, because these sit behind an
    /// `amaranth_soc` CSR multiplexer with granularity 8: reading the LOWEST
    /// address latches a shadow of the whole register and the rest come from
    /// that shadow. See `Gpio::set_mode` in `src/gpio.rs`.
    fn read(base: usize, offset: usize) -> u32 {
        // SAFETY: four bytes inside the window `target::GATEWARE` names, which
        // is generated from the SoC's own memory map. Read-only constants in an
        // uncached `main=0` region; volatile because it is a device.
        unsafe {
            let reg = (base + offset) as *const u8;
            (read_volatile(reg) as u32)
                | (read_volatile(reg.add(1)) as u32) << 8
                | (read_volatile(reg.add(2)) as u32) << 16
                | (read_volatile(reg.add(3)) as u32) << 24
        }
    }

    /// Everything the peripheral holds, read once.
    #[derive(Clone, Copy)]
    pub struct Id {
        pub magic: u32,
        /// Bit 31 dirty, bits 30..0 the short git hash. The encoding
        /// `gateware/build_helpers.py` stamps into USERCODE.
        pub git: u32,
        /// Packed UTC: year-2000, month, day, hour, minute, second.
        pub built: u32,
        pub sync_hz: u32,
        /// Cache sets, ways, and the ISA the core was generated with.
        pub cpu: u32,
        pub usb_hz: u32,
        /// The die temperature readout: bit 8 there is one, bit 7 the value is
        /// good, bits 5..0 the code. NOT degrees -- see `celsius`.
        pub die: u32,
        /// What `bus/fault.BusFault` has terminated: unclaimed addresses
        /// in 7..0, timeouts in 15..8, and the longest any request waited
        /// in 31..16. The last is the one that keeps the bound honest.
        pub bus_fault: u32,
    }

    impl Id {
        pub fn read(base: usize) -> Id {
            Id {
                magic: read(base, MAGIC_OFFSET),
                git: read(base, GIT),
                built: read(base, BUILT),
                sync_hz: read(base, SYNC_HZ),
                cpu: read(base, CPU),
                usb_hz: read(base, USB_HZ),
                die: read(base, DIE),
                bus_fault: read(base, BUS_FAULT),
            }
        }

        /// Junction temperature in degrees Celsius, or `None` if this build has
        /// no DTR or has not completed a conversion.
        ///
        /// The DTR's output is a code, not a temperature, and the mapping is
        /// not linear: one degree per step between codes 21 and 29, ten degrees
        /// per step at the ends. Table 4.3 of FPGA-TN-02210 (*Power Consumption
        /// and Management for ECP5 and ECP5-5G Devices*) is the only place it
        /// exists, and it is reproduced in `DEGREES` below.
        ///
        /// Section 2.20 of the family data sheet (FPGA-DS-02012) says the block
        /// "is not specifically calibrated for absolute accuracy" and publishes
        /// no tolerance, so treat this as a reading and not a measurement.
        /// Split rather than signed, because `core::fmt`'s signed `Display` is
        /// several hundred bytes of code this firmware would otherwise never
        /// instantiate, and the image region of block RAM does not have them.
        pub fn celsius(&self) -> Option<(&'static str, u32)> {
            if self.die & DIE_PRESENT == 0 || self.die & DIE_VALID == 0 {
                return None;
            }
            let biased = DEGREES[(self.die & 0x3f) as usize] as u32;
            Some(if biased < 58 {
                ("-", 58 - biased)
            } else {
                ("", biased - 58)
            })
        }

        pub fn present(&self) -> bool {
            self.magic == MAGIC
        }

        pub fn dirty(&self) -> bool {
            self.git & 0x8000_0000 != 0
        }

        pub fn hash(&self) -> u32 {
            self.git & 0x7fff_ffff
        }

        pub fn cache_sets(&self) -> u32 {
            self.cpu & 0xffff
        }

        pub fn cache_ways(&self) -> u32 {
            (self.cpu >> 16) & 0xff
        }
    }

    /// The junction temperature at which the HyperRAM's tCSM collapses.
    ///
    /// Winbond's own `Config-AC.v` (`sources/README.md`, and
    /// `docs/chips/hyperram/models.md`) switches tCSM on `` `define LA_85C ``:
    /// **4000 ns below 85 C, 1000 ns above**. Both controllers derive their CS#
    /// cap from the 4000 ns figure alone -- `T_CSM_NS` in
    /// `gateware/soc/peripherals/hyperram_dqs_controller.py` and
    /// `hyperram_controller.py`, `HYPERRAM_TCSM_NS` in `gateware/soc/bootram.py`
    /// -- so above this the cap is four times too loose and nothing else on the
    /// board would say so.
    pub const TCSM_KNEE_C: u32 = 85;

    /// This build has a DTR at all. Zero from a design without one and zero
    /// from a conversion that never completed would otherwise read the same.
    pub const DIE_PRESENT: u32 = 1 << 8;
    /// The block's own data-valid bit, DTROUT[7].
    pub const DIE_VALID: u32 = 1 << 7;

    /// FPGA-TN-02210 Table 4.3, code to junction temperature, biased by 58 so
    /// the whole table fits in bytes: -58 C is 0 and 132 C is 190.
    static DEGREES: [u8; 64] = [
        0, 2, 4, 6, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 28, 38, 48, 54, 58, 62, 68, 79, 80, 81,
        82, 83, 84, 85, 86, 87, 98, 108, 118, 128, 134, 138, 139, 140, 141, 142, 143, 144, 145,
        146, 147, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 174, 178,
        182, 186, 190,
    ];

    /// The one in this build, or `None` on a target that is not a bitstream.
    pub fn id() -> Option<Id> {
        target::GATEWARE.map(Id::read)
    }
}

/// Read a machine CSR by name.
///
/// Every CSR named here is decoded by this core -- `mvendorid`, `marchid`,
/// `mimpid`, `mhartid`, `misa`, `mstatus` and `mtvec` are all in VexiiRiscv's
/// implemented list, and all exist on `virt` -- so none of these reads can raise
/// an illegal instruction, which on this firmware would abort rather than
/// return. Do not extend this macro without checking the generated core first.
macro_rules! csr {
    ($name:literal) => {{
        let value: u32;
        // SAFETY: a read of an implemented, side-effect-free machine CSR.
        unsafe {
            core::arch::asm!(concat!("csrr {0}, ", $name), out(reg) value,
                             options(nomem, nostack));
        }
        value
    }};
}

unsafe extern "C" {
    /// Section boundaries, from riscv-rt's `link.x`. Taken from the linker so
    /// that a change to `memory.x` moves this report with it.
    static __stext: u8;
    static __etext: u8;
    static __srodata: u8;
    static __erodata: u8;
    static __sdata: u8;
    static __edata: u8;
    static __sbss: u8;
    static __ebss: u8;
    /// The bottom of the stack region -- everything below it is allocated, and
    /// everything between it and `__sstack` is what the stack has to grow into.
    static __estack: u8;
    static __sstack: u8;
    /// Where a reset lands: the bootloader at 0x0 on the board, this image's own
    /// entry under QEMU. Taken from the linker for the same reason as the rest --
    /// `memory.x` decides it, so printing a literal here could disagree with it.
    static _reset_vector: u8;
    /// The base of the writable region, so the budget below is measured against
    /// the REGION rather than against whatever section happens to be lowest.
    static _ram_start: u8;
}

fn at(symbol: &u8) -> u32 {
    (symbol as *const u8) as u32
}

/// Name the window `address` lives in, and where in it.
///
/// Written by address rather than by section name so that a section moving
/// between block RAM and flash is reported by the move, not by remembering to
/// edit a label. `.rodata` made exactly that move and the label was not edited.
fn write_region(uart: &mut Uart, address: u32) {
    match target::FLASH_WINDOW {
        Some((base, size)) if (address as usize) >= base && (address as usize) < base + size => {
            // The flash OFFSET, because that is the number a person needs: it is
            // what `apollo flash-program --offset` was given and what a reflash
            // must be given again. The mapped address alone does not say it.
            let _ = write!(uart, "flash +{:x}", address as usize - base);
        }
        _ => {
            let _ = write!(uart, "bram");
        }
    }
}

/// `info` -- everything the running image knows about itself.
/// What the bootloader left behind on its way past.
///
/// The whole reason this image is running rather than a staged one, in a word the
/// bootloader wrote before it jumped. Reporting it here is what makes a fallback
/// diagnosable from a console -- the alternative, which this project has already paid
/// for once, is a board that silently comes up on the wrong image and looks fine.
///
/// It is read and rendered, never acted on. Nothing in this firmware behaves
/// differently because of it.
fn boot_status(uart: &mut Uart) {
    // A continuation of the `boot` line above, which has already said where the
    // reset vector and the image are; this line says what happened there.
    let Some(address) = target::BOOT_STATUS else {
        let _ = writeln!(uart, "         no bootloader on this target");
        return;
    };

    // SAFETY: a fixed word inside the bootloader's own region, which no image writes.
    let word = unsafe { core::ptr::read_volatile(address as *const u32) };

    if word & 0xffff_ff00 != target::BOOT_STATUS_MARK {
        // No mark: the word is whatever block RAM initialised to. Reported as unknown
        // rather than decoded, because decoding it would invent an answer.
        let _ = writeln!(
            uart,
            "         {:08x} at {:x}: no mark, nothing wrote a status",
            word, address
        );
        return;
    }

    let code = (word & 0xff) as usize;
    let text = target::BOOT_STATUS_TEXT
        .get(code)
        .copied()
        .unwrap_or("unknown code");
    let _ = writeln!(uart, "         {} ({})", text, code);
}

pub fn command(uart: &mut Uart) {
    let dirty = if build::GIT_DIRTY { "dirty" } else { "clean" };
    let hash = if build::GIT_HASH.is_empty() {
        "no-git"
    } else {
        build::GIT_HASH
    };
    let _ = writeln!(
        uart,
        "image    {} {}  {}  {}",
        hash,
        dirty,
        build::GIT_BRANCH,
        build::BUILT
    );
    // `-O{}` beside the profile, because `release` is a name and not a setting:
    // `scripts/opt_level_sweep.py` builds every level under it.
    let _ = writeln!(
        uart,
        "tools    {}  {} {} -O{}",
        build::RUSTC,
        build::TARGET,
        build::PROFILE,
        build::OPT_LEVEL
    );

    // WHICH LINE EDITOR IS RUNNING, and it is not cosmetic.
    //
    // "TAB does not complete" has two causes that look identical from a
    // terminal: the firmware has no completion, or the terminal is eating the
    // key before it reaches the wire. A build that cannot say which editor it
    // carries leaves the reader guessing between them -- the same shape as a
    // transcript that does not say what produced it.
    let _ = writeln!(
        uart,
        "shell    {}  {}",
        crate::shell::console::EDITOR,
        crate::shell::console::EDITOR_KEYS
    );

    // One row per section, each carrying the window it is actually in.
    //
    // A table rather than a line of prose per section: four rows share one format
    // string, which is cheaper in `.text` than four, and it makes the columns line
    // up without any of them being padded by hand.
    //
    // SAFETY: the linker's own symbols; only their addresses are taken.
    let sections = unsafe {
        [
            ("text", at(&__stext), at(&__etext) - at(&__stext)),
            ("rodata", at(&__srodata), at(&__erodata) - at(&__srodata)),
            ("data", at(&__sdata), at(&__edata) - at(&__sdata)),
            ("bss", at(&__sbss), at(&__ebss) - at(&__sbss)),
        ]
    };
    for (index, (name, start, size)) in sections.iter().enumerate() {
        // The label on the first row only; the rest are continuations of it.
        // Every other block here reads that way, and repeating "memory" four
        // times makes four facts look like four unrelated lines.
        let _ = uart.write_str(if index == 0 { "memory   " } else { "         " });
        let _ = write!(uart, "{:6} {:6} at {:08x}  ", name, size, start);
        write_region(uart, *start);
        let _ = writeln!(uart);
    }

    // SAFETY: as above.
    // Measured from `_ram_start`, NOT from `__stext`.
    //
    // `__stext` was the lowest thing in RAM only while `.text` was in RAM. Once it
    // moved to flash the subtraction spanned two address spaces and this reported
    // "54856 free of 4025876480" -- a number with no meaning, printed with the
    // same confidence as the correct one.
    let (free, total) = unsafe {
        (
            at(&__sstack) - at(&__estack),
            at(&__sstack) - at(&_ram_start),
        )
    };
    // "free" is what the stack grows into, so it is not all spare -- but it is
    // the number that says how much more firmware fits, which is the question
    // this half of block RAM keeps forcing. Both numbers are block RAM only, and
    // now say so: `.rodata` is counted in neither, which is the whole point of
    // having moved it and was invisible while the two lines sat together.
    let _ = writeln!(
        uart,
        "         {} free of {} bram (stack grows down into it)",
        free, total
    );

    if let Some((base, size)) = target::FLASH_WINDOW {
        let _ = writeln!(
            uart,
            "flash    window {:08x}+{:x}, {}",
            base,
            size,
            if target::FLASH_CACHED {
                "cached"
            } else {
                // Worth spelling out: uncached is not a small
                // constant factor here, and nothing else on screen
                // would hint at it.
                "uncached (a full SPI transaction per load)"
            }
        );
    }

    // SAFETY: an address-only read of a linker-defined absolute symbol.
    let _ = write!(
        uart,
        "boot     reset vector {:08x}, image at {:08x} in ",
        unsafe { at(&_reset_vector) },
        unsafe { at(&__stext) }
    );
    write_region(uart, unsafe { at(&__stext) });
    let _ = writeln!(uart);

    boot_status(uart);

    let misa = csr!("misa");
    let _ = write!(uart, "cpu      misa {:08x} ", misa);
    write_isa(uart, misa);
    let _ = writeln!(
        uart,
        "  vendor {:x} arch {:x} impl {:x} hart {}",
        csr!("mvendorid"),
        csr!("marchid"),
        csr!("mimpid"),
        csr!("mhartid")
    );

    let plic = plic::Plic::new(target::PLIC_BASE);
    let _ = writeln!(
        uart,
        "trap     mstatus {:08x} mtvec {:08x}",
        csr!("mstatus"),
        csr!("mtvec")
    );
    // Threshold and enables only. NOT the claim register -- reading that takes
    // an interrupt away from the handler and never completes it, which would
    // kill the console from a diagnostic. See `Plic::claim`.
    let _ = writeln!(
        uart,
        "plic     @{:08x} threshold {} enabled {:08x}",
        target::PLIC_BASE,
        plic.threshold(),
        plic.enabled()
    );

    match gateware::id() {
        None => {
            let _ = writeln!(
                uart,
                "gateware none -- this target is not a \
                                    bitstream"
            );
        }
        Some(id) if !id.present() => {
            // Zeros here mean the window decoded to nothing, which is what a
            // bitstream built before this peripheral existed looks like.
            let _ = writeln!(
                uart,
                "gateware magic {:08x}, want {:08x} -- this \
                                    bitstream carries no id",
                id.magic,
                gateware::MAGIC
            );
        }
        Some(id) => {
            let _ = write!(
                uart,
                "gateware {:07x} {}  ",
                id.hash(),
                if id.dirty() { "dirty" } else { "clean" }
            );
            write_built(uart, id.built);
            let _ = writeln!(
                uart,
                "  sync {} usb {}  cache {}x{}",
                Hz(id.sync_hz),
                Hz(id.usb_hz),
                id.cache_sets(),
                id.cache_ways()
            );

            // The one thing the die itself will say. Not the chip's identity:
            // USERCODE, IDCODE and the TraceID are all reachable over JTAG and
            // from nowhere in the fabric, so the CPU cannot learn which part it
            // is running on -- see the header of gateware_id.py.
            match id.celsius() {
                Some((sign, degrees)) => {
                    // The raw code stays, the caption goes. `uncalibrated` was
                    // true and useless: it had been printed on every `info` long
                    // enough to read as part of the label rather than as
                    // something to act on, and nothing was going to act on it.
                    // The reading is taken as the die temperature. #262.
                    //
                    // The code is kept because it is the measurement and the
                    // degrees are a conversion of it -- anything that ever does
                    // want to check the mapping needs the code, and it costs a
                    // field.
                    let _ = writeln!(
                        uart,
                        "         die {}{} C (dtr code {})",
                        sign,
                        degrees,
                        id.die & 0x3f
                    );
                }
                None if id.die & gateware::DIE_PRESENT == 0 => {
                    let _ = writeln!(
                        uart,
                        "         die no readout in this \
                                            bitstream"
                    );
                }
                None => {
                    let _ = writeln!(
                        uart,
                        "         die conversion has not \
                                            completed"
                    );
                }
            }

            // What the fabric has had to terminate, and how close a legitimate
            // access has come to the bound (#409). `worst` is the margin: it is
            // measured against BUS_TIMEOUT_CYCLES, which is 1.25x a DERIVED
            // figure, and a number near the bound is a board about to fault on
            // traffic that is fine.
            let _ = writeln!(
                uart,
                "         bus  {} unclaimed, {} timed out, worst wait {} cycles",
                id.bus_fault & 0xff,
                (id.bus_fault >> 8) & 0xff,
                id.bus_fault >> 16
            );

            if id.git != build::GIT_WORD {
                let _ = writeln!(
                    uart,
                    "         MISMATCH: this firmware was \
                                        built from {} {}",
                    hash, dirty
                );
            }
            if id.sync_hz != target::TIME_HZ {
                // Every interval in this firmware is derived from TIME_HZ. A
                // disagreement here means every one of them is wrong by the
                // ratio, silently.
                let _ = writeln!(
                    uart,
                    "         SYNC MISMATCH: firmware \
                                        assumes {}",
                    Hz(target::TIME_HZ)
                );
            }
            // MEASURED, not declared. Everything above is a constant baked in
            // at elaboration and says what the gateware was ASKED for. This is
            // counted in fabric against the 60 MHz oscillator, so it says what
            // the silicon is doing -- and the two disagreeing is a fault that
            // nothing else in this image can see.
            //
            // A PLL with its feedback unconnected reported `sync 30000000` from
            // the constant while `sync` was not oscillating at all, and finding
            // that cost a bisect across two rebuilds.
            //
            // One reader, in `src/clock.rs`, shared with the `clocks` line of
            // the boot report -- two copies of a CSR decode is two things to
            // keep in step with the gateware.
            match crate::clock::measured() {
                None => {
                    let _ = writeln!(uart,
                        "         clocks: NO MEASUREMENT YET (window incomplete)");
                }
                Some(measured) => {
                    let _ = writeln!(uart,
                        "         measured sync {} kHz, pll {}",
                        measured.khz,
                        if measured.locked { "locked" } else { "UNLOCKED" });
                    if !within_one_percent(measured.khz, id.sync_hz / 1000) {
                        let _ = writeln!(uart,
                            "         CLOCK MISMATCH: gateware declares {} kHz, \
                             the fabric counts {}",
                            id.sync_hz / 1000, measured.khz);
                    }
                }
            }

            if id.cpu & gateware::CPU_RDTIME == 0 {
                // `src/clock.rs` is the only thing here that knows how much
                // time has passed, and it is `rdtime` and nothing else. A core
                // generated without it does not fail -- it traps.
                //
                // This line covers `stats` as well. `--with-rdtime` adds
                // `zicntr`, and `zicntr` is what instantiates the plugin that
                // decodes `mcycle` and `minstret` -- one flag, both CSRs, so
                // one warning. See `src/metrics.rs`.
                let _ = writeln!(
                    uart,
                    "         NO RDTIME: this core was not \
                                        generated with the time counter"
                );
            }
            let generated = isa_from(id.cpu);
            if generated & !misa != 0 {
                // Not a fault. VexiiRiscv hardwires `misa` to rv32im whatever
                // it was generated with, so this line is the record of which
                // account to believe -- and `selftest` runs the instructions.
                let _ = write!(uart, "         generated ");
                write_isa(uart, 1 << 30 | generated);
                let _ = writeln!(uart, ", which misa does not report");
            }
        }
    }
}

/// Is a counted frequency the declared one, within 1%?
///
/// The monitor's window quantises to about 0.003%, so anything outside 1% is a
/// real disagreement and not measurement noise. Here rather than at each caller:
/// `info` and the boot report must draw the line in the same place, or a board
/// passes one and fails the other.
pub fn within_one_percent(measured_khz: u32, declared_khz: u32) -> bool {
    let slack = declared_khz / 100;
    measured_khz + slack >= declared_khz && measured_khz <= declared_khz + slack
}

/// The `misa` extension bits the gateware says the core was generated with.
///
/// `'a' as u32 - 'a' as u32` is zero and clippy's `eq_op` says so, correctly.
/// It is written out anyway: every line here is `letter - 'a'`, which is what
/// the specification says bit 25..0 means, and the one line that read `1 << 0`
/// instead would be the one a reader has to decode. Allowed rather than
/// special-cased, so the four lines stay comparable at a glance.
#[allow(clippy::eq_op)]
fn isa_from(cpu: u32) -> u32 {
    let mut bits = 1 << ('i' as u32 - 'a' as u32);
    if cpu & gateware::CPU_M != 0 {
        bits |= 1 << ('m' as u32 - 'a' as u32);
    }
    if cpu & gateware::CPU_A != 0 {
        bits |= 1 << ('a' as u32 - 'a' as u32);
    }
    if cpu & gateware::CPU_C != 0 {
        bits |= 1 << ('c' as u32 - 'a' as u32);
    }
    bits
}

/// `misa` as an ISA string: `rv32imac`.
///
/// Bits 31:30 are MXL -- 1 is 32-bit -- and bits 25..0 are one per extension
/// letter. The letters are printed in the order the specification names them
/// rather than alphabetically, because `rv32imac` is a string people recognise
/// and `rv32acim` is not.
fn write_isa(uart: &mut Uart, misa: u32) {
    let _ = write!(
        uart,
        "rv{}",
        match misa >> 30 {
            1 => "32",
            2 => "64",
            _ => "??",
        }
    );
    for letter in b"imafdqlcbjtpvnsuh" {
        if misa & (1 << (letter - b'a')) != 0 {
            uart.put(*letter);
        }
    }
    if misa & 0x03ff_ffff == 0 {
        let _ = write!(uart, " (no extensions reported)");
    }
}

/// The gateware's packed build time, as ISO 8601.
///
/// UTC, and it says so, because the field carries no offset -- a bitstream is
/// built on one machine and read on another. The firmware's own timestamp
/// beside it carries the builder's offset, which is why the two look different.
fn write_built(uart: &mut Uart, built: u32) {
    let _ = write!(
        uart,
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        2000 + (built >> 26),
        (built >> 22) & 0xf,
        (built >> 17) & 0x1f,
        (built >> 12) & 0x1f,
        (built >> 6) & 0x3f,
        built & 0x3f
    );
}
