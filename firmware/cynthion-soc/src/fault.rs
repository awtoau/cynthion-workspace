//! The trap that names the address, instead of an abort nobody can read (#409).
//!
//! - Without this, `ExceptionHandler` resolves to `abort` -- an infinite loop
//!   with no output. A bus fault and a hang then look identical from a console.
//! - The gateware raises ERR for an unclaimed or unanswered address
//!   (`gateware/soc/bus/fault.py`), so those become a load/store access fault
//!   and arrive here.
//! - Resumes rather than halts: `mepc` is advanced past the faulting
//!   instruction, so the shell survives its own bad read and can be asked again.
//!
//! The core does not write the load's destination register on a fault, so a
//! resumed load leaves stale data in it. **The printed fault is the result; the
//! value is not.**

use core::fmt::Write;
use core::ptr::read_volatile;
use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use riscv::register::{mcause, mepc, mtval};
use riscv_rt::TrapFrame;

/// How many faults get a line before this goes quiet.
///
/// A repeating fault is a loop, and a loop printing at console rates buries
/// everything before it. Sixteen is more distinct faults than a session
/// produces and few enough to scroll back past. Resuming continues either way --
/// only the printing stops.
const REPORT_LIMIT: u32 = 16;

/// How many exceptions this image has taken, for `cpu fault` to report.
static TAKEN: AtomicU32 = AtomicU32::new(0);

/// Set while the handler is running, so a fault raised by the handler's own
/// console writes does not recurse until the stack is gone.
static INSIDE: AtomicBool = AtomicBool::new(false);

/// How many exceptions this image has taken.
pub(crate) fn taken() -> u32 {
    TAKEN.load(Ordering::Relaxed)
}

/// The exception the core could not handle, on the console, then onwards.
///
/// `#[no_mangle]` with this exact name is riscv-rt 0.18's hook; the `&mut`
/// signature is its returning form, and returning is what makes the shell
/// survive.
#[no_mangle]
#[allow(non_snake_case)]
fn ExceptionHandler(_frame: &mut TrapFrame) {
    let epc = mepc::read();
    let cause = mcause::read().bits();
    let tval = mtval::read();
    let seen = TAKEN.fetch_add(1, Ordering::Relaxed);
    let next = resume_from(epc);

    // Printing is itself a peripheral access, so it can be what faulted. The
    // guard keeps a broken console from re-entering the report; the count keeps
    // a repeating fault from burying everything before it.
    if !INSIDE.swap(true, Ordering::Relaxed) && seen < REPORT_LIMIT {
        let mut uart = crate::primary();
        let _ = writeln!(
            uart,
            "\n*** TRAP {} ({}) pc {:08x} addr {:08x}",
            cause,
            describe(cause),
            epc,
            tval
        );
        if next.is_none() {
            let _ = writeln!(uart, "*** pc is outside this image; STOPPED");
        } else if seen + 1 == REPORT_LIMIT {
            let _ = writeln!(uart, "*** {} traps reported; going quiet", REPORT_LIMIT);
        }
    }
    INSIDE.store(false, Ordering::Relaxed);

    match next {
        // SAFETY: this is the trap being returned from, `mstatus.MIE` is clear
        // for its whole duration, and `next` is the instruction after the one
        // that faulted.
        Some(next) => unsafe { mepc::write(next) },
        // `mepc` points somewhere this image cannot read, so there is no
        // instruction to step over and nothing sane to return to.
        None => loop {},
    }
}

/// The address after the instruction at `epc`, or `None` if it cannot be read.
///
/// Length from the low two bits: `11` is a 32-bit instruction, anything else a
/// 16-bit compressed one. Bounded to the two windows this image executes from,
/// because reading `epc` blind is how a trap handler takes a trap.
fn resume_from(epc: usize) -> Option<usize> {
    let base = crate::target::BRAM_BASE;
    let bram = epc >= base && epc < base + crate::target::BRAM_SIZE;
    let flash = match crate::target::FLASH_WINDOW {
        Some((base, size)) => epc >= base && epc < base + size,
        None => false,
    };
    if !bram && !flash || epc & 1 != 0 {
        return None;
    }
    // SAFETY: bounded above to a decoded, readable window, and 2-byte aligned as
    // every instruction on this core is.
    let half = unsafe { read_volatile(epc as *const u16) };
    Some(epc + if half & 0b11 == 0b11 { 4 } else { 2 })
}

/// The `mcause` codes this SoC can actually produce, in words.
fn describe(cause: usize) -> &'static str {
    match cause {
        0 => "instruction address misaligned",
        1 => "instruction access fault",
        2 => "illegal instruction",
        3 => "breakpoint",
        4 => "load address misaligned",
        5 => "load access fault",
        6 => "store address misaligned",
        7 => "store access fault",
        11 => "environment call",
        _ => "unexpected on this core",
    }
}
