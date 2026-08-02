//! `selftest` -- run the parts of this machine that can be run from inside it.
//!
//! `check` reports four numbers. This runs the instruction classes the firmware
//! is built with, walks the memory it lives in, and asks each peripheral that
//! has an identity to give it.
//!
//!     item      what it proves
//!     --------  ------------------------------------------------------------
//!     alu       the base integer instructions, on operands the compiler
//!               cannot fold
//!     muldiv    `M` -- mul, mulh, div, rem, and the signed cases
//!     comp      `C` -- compressed encodings, executed AND two bytes each
//!     atomic    `A` -- lr/sc and three amo forms
//!     ram       block RAM address and data lines, over what is free
//!     clock     the `time` CSR advances, which every interval here rests on
//!     uart <n>  each 16550 answers on its scratch register
//!     gateware  the build-id window answers with its magic
//!     flash     the memory-mapped flash reads its known first word
//!     phy       the TARGET USB3343 gives its vendor and product id
//!
//! ## Why `A` is worth a test of its own
//!
//! Atomics are why the cached CPU configuration is mandatory: VexiiRiscv's
//! cacheless Wishbone bridge asserts `!withAmo`, so a build that dropped the
//! caches would not have them. That is a gateware decision this firmware cannot
//! see, and its symptom is an illegal instruction from a line of Rust that looks
//! like arithmetic.
//!
//! ## What this deliberately does not do
//!
//! * **No I2C.** The bus has one owner per device protocol -- `power::Monitor`
//!   for the PAC1954's REFRESH cycle, `typec::Controllers` for the FUSB302Bs'
//!   read-to-clear registers -- and a second reader lands inside a window those
//!   drivers own. `power` and `typec` report what they cached; this does not
//!   reach past them. See `src/bus.rs`.
//! * **No flash writes.** The bitstream is at offset 0. An erase here would take
//!   the board out with it, and there is no state a self-test needs to leave
//!   behind.
//! * **No PLIC claim.** Reading the claim register takes an interrupt away from
//!   the handler and never completes it, killing the console from a diagnostic.
//!   `info` reports the threshold and enables, which are free of side effects.
//! * **No ULPI scratch walk.** `phy` does that, and it WRITES a register on a
//!   PHY; this reads the identity and stops.
//! * **Nothing that outlives it.** The one thing written is the block RAM
//!   between this image's `.bss` and its stack: memory nothing owns, reached
//!   through the linker's own symbols. It does not touch the bootloader's
//!   kilobyte at 0x0, which is what recovers a board.

use core::fmt::Write;
use core::ptr::{read_volatile, write_volatile};

use crate::clock;
use crate::info;
use crate::target;
use crate::uart::Uart;
use crate::ulpi;

/// How an item came out. `Skip` is not `Pass`: a target with no board cannot
/// answer for one, and counting that as a pass would make the summary line say
/// more than it knows.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Outcome {
    Pass,
    Fail,
    Skip,
}

struct Report {
    passed: u32,
    failed: u32,
    skipped: u32,
}

impl Report {
    fn item(&mut self, uart: &mut Uart, name: &str, outcome: Outcome,
            detail: core::fmt::Arguments) {
        let label = match outcome {
            Outcome::Pass => {
                self.passed += 1;
                "ok  "
            }
            Outcome::Fail => {
                self.failed += 1;
                "FAIL"
            }
            Outcome::Skip => {
                self.skipped += 1;
                "skip"
            }
        };
        let _ = writeln!(uart, "{:8} {}  {}", name, label, detail);
    }

    fn ok(&mut self, uart: &mut Uart, name: &str, passed: bool,
          detail: core::fmt::Arguments) {
        self.item(uart, name,
                  if passed { Outcome::Pass } else { Outcome::Fail }, detail);
    }
}

/// Defeat constant folding: this must measure the CPU, not the compiler.
fn opaque(value: u32) -> u32 {
    // SAFETY: our own stack slot.
    unsafe { read_volatile(&value) }
}

pub fn command(uart: &mut Uart) {
    let mut report = Report { passed: 0, failed: 0, skipped: 0 };

    alu(uart, &mut report);
    muldiv(uart, &mut report);
    compressed(uart, &mut report);
    atomics(uart, &mut report);
    ram(uart, &mut report);
    clock_advances(uart, &mut report);
    consoles(uart, &mut report);
    gateware(uart, &mut report);
    flash(uart, &mut report);
    phy(uart, &mut report);

    let total = report.passed + report.failed;
    if report.failed == 0 {
        let _ = writeln!(uart, "{} of {} ok, {} skipped", report.passed, total,
                         report.skipped);
    } else {
        let _ = writeln!(uart, "{} of {} FAILED, {} skipped", report.failed,
                         total, report.skipped);
    }
}

/// The base integer set, on values the compiler cannot see.
fn alu(uart: &mut Uart, report: &mut Report) {
    let a = opaque(0x1234_5678);
    let b = opaque(0x9abc_def0);
    let mut ok = a.wrapping_add(b) == 0xacf1_3568;
    ok &= a.wrapping_sub(b) == 0x7777_7788;
    ok &= (a ^ b) == 0x8888_8888;
    ok &= (a & b) == 0x1234_5670;
    ok &= (a | b) == 0x9abc_def8;
    ok &= (a << 5) == 0x468a_cf00;
    ok &= (a >> 5) == 0x0091_a2b3;
    // Arithmetic versus logical shift: the one the compiler picks by the type,
    // and the one an `sra` that was wired as `srl` would get wrong.
    ok &= ((b as i32) >> 4) == -0x6543_211;
    ok &= (b >> 4) == 0x09ab_cdef;
    ok &= (a < b) && ((a as i32) > (b as i32));
    report.ok(uart, "alu", ok,
              format_args!("add sub and or xor shift compare, signed and not"));
}

/// `M`: the multiply and divide unit, including the signed corners.
fn muldiv(uart: &mut Uart, report: &mut Report) {
    let a = opaque(0x1234_5678);
    let b = opaque(0x9abc_def0);
    let mut ok = a.wrapping_mul(3) == 0x369d_0368;
    // The high half is a separate instruction from the low half, and a core
    // that returned the low half for both would pass every test that only
    // multiplies small numbers.
    ok &= (((a as u64) * (b as u64)) >> 32) as u32 == 0x0b00_ea4e;
    ok &= ((a as i32 as i64 * b as i32 as i64) >> 32) as u32 == 0xf8cc_93d6;
    ok &= a / opaque(1000) == 305_419;
    ok &= a % opaque(1000) == 896;
    // Signed division truncates toward zero in RISC-V, so this is -7 and not
    // -8, and the remainder takes the dividend's sign.
    ok &= (opaque((-50i32) as u32) as i32) / (opaque(7) as i32) == -7;
    ok &= (opaque((-50i32) as u32) as i32) % (opaque(7) as i32) == -1;
    // Division by zero is defined rather than trapping: all-ones for the
    // quotient, the dividend for the remainder.
    let (quotient, remainder): (u32, u32);
    // SAFETY: two arithmetic instructions on registers.
    unsafe {
        core::arch::asm!("divu {q}, {a}, {z}", "remu {r}, {a}, {z}",
                         a = in(reg) a, z = in(reg) opaque(0),
                         q = out(reg) quotient, r = out(reg) remainder,
                         options(nomem, nostack));
    }
    ok &= quotient == 0xffff_ffff && remainder == a;
    report.ok(uart, "muldiv", ok,
              format_args!("mul mulh div rem, signed and by zero (M)"));
}

/// `C`: three compressed instructions, and the six bytes they occupy.
///
/// The result alone would not settle it -- the assembler could have emitted the
/// 32-bit forms and the answer would be the same. The label difference is what
/// says the encodings were two bytes each, and it is computed by the assembler
/// from the instructions it actually emitted.
fn compressed(uart: &mut Uart, report: &mut Report) {
    let value: u32;
    let length: u32;
    // SAFETY: arithmetic on registers the compiler allocated for us. `c.li`,
    // `c.addi` and `c.slli` are CI-format and take any register but x0, which
    // `out(reg)` never gives.
    unsafe {
        core::arch::asm!(
            ".option push",
            ".option rvc",         // explicit c.* mnemonics need it enabled
            "1:",
            "c.li {v}, 5",
            "c.addi {v}, 3",
            "c.slli {v}, 4",
            "2:",
            ".option pop",
            "li {l}, 2b - 1b",
            v = out(reg) value,
            l = out(reg) length,
            options(nomem, nostack));
    }
    let ok = value == 128 && length == 6;
    report.ok(uart, "comp", ok,
              format_args!("(5+3)<<4 = {} in {} bytes (C)", value, length));
}

/// `A`: a reservation pair and three read-modify-write forms.
fn atomics(uart: &mut Uart, report: &mut Report) {
    let mut cell: u32 = 0;
    let address = &mut cell as *mut u32;
    let mut ok = true;

    // lr/sc, with retries. A reservation is broken by anything that intervenes,
    // and interrupts are live here -- a console byte landing between the pair
    // is a legitimate failure of that attempt and not of the CPU. Each attempt
    // is four instructions; eight consecutive interrupts inside that window is
    // not a sequence this machine produces, so a run that never succeeds is a
    // real fault.
    let mut stored = false;
    for _ in 0..8 {
        let (old, status): (u32, u32);
        // SAFETY: `cell` is our own stack slot, word aligned, in cacheable
        // block RAM -- which is where lr/sc are defined to work on this core.
        unsafe {
            core::arch::asm!(
                "lr.w {old}, ({p})",
                "sc.w {st}, {new}, ({p})",
                p = in(reg) address, new = in(reg) 0x5a5a_5a5au32,
                old = out(reg) old, st = out(reg) status,
                options(nostack));
        }
        if status == 0 {
            ok &= old == 0 && cell == 0x5a5a_5a5a;
            stored = true;
            break;
        }
    }
    ok &= stored;

    // SAFETY: as above.
    let swapped: u32;
    let added: u32;
    let ored: u32;
    unsafe {
        core::arch::asm!("amoswap.w {out}, {new}, ({p})",
                         p = in(reg) address, new = in(reg) 0x0000_00f0u32,
                         out = out(reg) swapped, options(nostack));
        core::arch::asm!("amoadd.w {out}, {add}, ({p})",
                         p = in(reg) address, add = in(reg) 0x0000_000fu32,
                         out = out(reg) added, options(nostack));
        core::arch::asm!("amoor.w {out}, {bits}, ({p})",
                         p = in(reg) address, bits = in(reg) 0x0000_ff00u32,
                         out = out(reg) ored, options(nostack));
    }
    // Each returns the value it replaced, which is the property that makes an
    // amo an atomic rather than a store.
    ok &= swapped == 0x5a5a_5a5a && added == 0x0000_00f0 && ored == 0x0000_00ff;
    ok &= cell == 0x0000_ffff;

    report.ok(uart, "atomic", ok,
              format_args!("lr/sc amoswap amoadd amoor (A)"));
}

/// Block RAM address and data lines, walked over what this image is not using.
///
/// Two independent faults, two patterns. A stuck or shorted ADDRESS line makes
/// two different offsets the same location, which a walk of one-hot offsets
/// finds because every one of them holds a value only it should have. A stuck
/// DATA line survives that -- every address still reads back what it wrote --
/// and needs one bit walked across a single word.
///
/// The free space between `__ebss` and the stack, rather than a static buffer.
/// A buffer big enough to reach address bit 14 would be 32 KiB permanently
/// spent out of a 63 KiB region; this costs nothing and covers the same bits,
/// because the gap is what is left of block RAM once this image is in it --
/// currently about 25 KiB.
///
/// It does NOT walk downwards into the bootloader's kilobyte at 0x0. That is
/// the code that recovers a board from a bad image, and a self-test that could
/// damage it would be testing the one thing that must not be under test.
fn ram(uart: &mut Uart, report: &mut Report) {
    unsafe extern "C" {
        /// End of `.bss`, from riscv-rt's link.x. Everything above it up to
        /// `_stack_start` is stack that this image has not grown into.
        static __ebss: u8;
        static _stack_start: u8;
    }

    // A margin below the live stack.
    //
    // 4 KiB, against a shell whose deepest call chain here is a few hundred
    // bytes: `main -> command -> selftest::run -> ram`, each holding a `Report`
    // and a handful of locals. The reservation is what makes writing below `sp`
    // safe to state rather than to hope, and 4 KiB is an order of magnitude
    // more than the measurement so a future command cannot quietly invalidate
    // it.
    const STACK_RESERVE: usize = 4096;

    // Aligned up to 4 KiB so the one-hot offsets land on addresses whose low
    // bits are the walked bit and nothing else.
    let base = (((&raw const __ebss) as usize) + 0xfff) & !0xfff;
    let top = ((&raw const _stack_start) as usize) - STACK_RESERVE;
    let size = top.saturating_sub(base);

    let mut ok = true;
    let mut addresses = 0u32;

    // Offsets 0, 4, 8, 16 ... up to the gap. Each holds a value derived from
    // its own offset, so an aliased pair disagrees whichever way round it is
    // read.
    let mut offset = 0usize;
    loop {
        let cell = (base + offset) as *mut u32;
        // SAFETY: inside the gap between `.bss` and the stack reservation,
        // whose bounds come from the linker.
        unsafe { write_volatile(cell, (offset as u32) ^ 0xa5a5_a5a5) };
        addresses += 1;
        offset = if offset == 0 { 4 } else { offset << 1 };
        if offset >= size {
            break;
        }
    }
    let mut offset = 0usize;
    loop {
        let cell = (base + offset) as *const u32;
        // SAFETY: as above.
        ok &= unsafe { read_volatile(cell) } == (offset as u32) ^ 0xa5a5_a5a5;
        offset = if offset == 0 { 4 } else { offset << 1 };
        if offset >= size {
            break;
        }
    }

    let cell = base as *mut u32;
    for bit in 0..32 {
        for pattern in [1u32 << bit, !(1u32 << bit)] {
            // SAFETY: as above.
            unsafe {
                write_volatile(cell, pattern);
                ok &= read_volatile(cell) == pattern;
            }
        }
    }

    report.ok(uart, "ram", ok,
              format_args!("free {:08x}+{:x}: {} addresses, \
                            32 data lines", base, size, addresses));
}

/// The `time` CSR advances, and by a plausible amount.
///
/// Every interval in this firmware is a difference of two of these. A counter
/// stuck at a value makes the power monitor poll once and never again, which
/// looks like a dead bus rather than a dead clock.
fn clock_advances(uart: &mut Uart, report: &mut Report) {
    let start = clock::now();
    // A bounded spin, not a delay: the loop exists to give the counter
    // something to advance across, and it is over as soon as it has.
    let mut ticks = 0u32;
    for _ in 0..10_000u32 {
        ticks = start.elapsed(clock::now());
        if ticks > 0 {
            break;
        }
    }
    report.ok(uart, "clock", ticks > 0,
              format_args!("time advanced {} ticks at {} Hz", ticks,
                           target::TIME_HZ));
}

/// Every 16550 answers on its scratch register.
///
/// SCR is eight bits that do nothing else, so this distinguishes a peripheral
/// that is there from an address that decodes to nothing -- which on this bus
/// reads as zeros rather than faulting. The same test `ports` makes, per
/// console, because a self-test that skipped the second port would miss the one
/// that is hardest to get wired right.
fn consoles(uart: &mut Uart, report: &mut Report) {
    for (index, &base) in target::UART_BASES.iter().enumerate() {
        let present = crate::scratch_responds(base);
        // The name is built without an allocator, so the index goes in the
        // detail rather than the label.
        report.ok(uart, "uart", present,
                  format_args!("console {} at {:08x} answers on scratch",
                               index, base));
    }
}

/// The build-id window answers with its magic.
fn gateware(uart: &mut Uart, report: &mut Report) {
    match info::gateware::id() {
        None => report.item(uart, "gateware", Outcome::Skip,
                            format_args!("this target is not a bitstream")),
        Some(id) => report.ok(uart, "gateware", id.present(),
                              format_args!("magic {:08x}, want {:08x}",
                                           id.magic, info::gateware::MAGIC)),
    }
}

/// The memory-mapped flash reads its known first word.
///
/// A read, and only a read. Offset 0 is the bitstream's own header, which is
/// what makes `615000ff` a value with a reason behind it -- and what makes
/// writing anything here a way to lose the board.
fn flash(uart: &mut Uart, report: &mut Report) {
    let word = target::flash_word(0);
    report.ok(uart, "flash", word == 0x6150_00ff,
              format_args!("@0 {:08x}, the bitstream header", word));
}

/// The TARGET PHY names itself.
fn phy(uart: &mut Uart, report: &mut Report) {
    let board = match target::BOARD {
        Some(board) => board,
        None => return report.item(uart, "phy", Outcome::Skip,
                                   format_args!("no board on this target")),
    };
    let phy = ulpi::Ulpi::new(board.ulpi);
    let read = |address: u8| phy.read(address).ok();
    match (read(ulpi::usb3343::REG_VENDOR_ID_LOW),
           read(ulpi::usb3343::REG_VENDOR_ID_LOW + 1),
           read(ulpi::usb3343::REG_PRODUCT_ID_LOW),
           read(ulpi::usb3343::REG_PRODUCT_ID_LOW + 1)) {
        (Some(vl), Some(vh), Some(pl), Some(ph)) => {
            let vendor = ((vh as u16) << 8) | vl as u16;
            let product = ((ph as u16) << 8) | pl as u16;
            report.ok(uart, "phy",
                      vendor == ulpi::usb3343::VENDOR_ID
                          && product == ulpi::usb3343::PRODUCT_ID,
                      format_args!("vendor {:04x} product {:04x}", vendor,
                                   product));
        }
        // Not "answered wrongly": a PHY that is not there never releases `dir`
        // and the gateware's timeout fires instead.
        _ => report.ok(uart, "phy", false,
                       format_args!("the PHY did not answer")),
    }
}
