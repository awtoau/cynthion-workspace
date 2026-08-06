//! `model_coop`, plus three periodic jobs multiplexed onto the ONE `mtimecmp`
//! this SoC has.
//!
//! Built by `scripts/soc_model_probe.py`. Its pair is `model_coop_hwtimer`,
//! which schedules the same three jobs on three hardware comparators. The
//! difference between the two `.text` figures is what a software timer queue
//! costs, and it is the measurement `docs/soc-concurrency.md` uses to
//! answer whether the FPGA should grow comparators.
//!
//! Everything expensive here follows from one comparator: the deadlines are
//! 64-bit because `mtime` is, the handler must decide which of them are due,
//! and it must then find the earliest of the rest and write it back through the
//! three-store sequence in `src/timer.rs`. That is the whole of the difference.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};
use core::sync::atomic::{AtomicU32, Ordering};

#[allow(dead_code)]
#[path = "../plic.rs"]
mod plic;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;

use riscv::interrupt::Interrupt;

const MTIMECMP_LO: usize = 0x4000;
const MTIMECMP_HI: usize = 0x4004;
const MTIME_LO: usize = 0xbff8;
const MTIME_HI: usize = 0xbffc;
const NEVER_LO: u32 = 0xffff_ffff;

static READY: AtomicU32 = AtomicU32::new(0);
static SERVICED: AtomicU32 = AtomicU32::new(0);

const JOB_CONSOLE_RX: u32 = 0;
const JOB_TYPE_C: u32 = 1;

/// The three periodic jobs, at the periods `src/main.rs` runs them at today:
/// the power poll, the Type-C fault sweep, and the 1 ms tick.
const PERIODS_MS: [u32; 3] = [50, 50, 1];

/// When each periodic job is next due, in `mtime` ticks. The software timer
/// queue -- unsorted, because three entries do not repay a sort, but every
/// insert still has to find the minimum.
///
/// An `UnsafeCell` rather than a `static mut`, which
/// `scripts/soc_irq_log_check.py` rejects, and rather than atomics, which
/// riscv32imac does not have at 64 bits. Zero-cost either way.
struct Deadlines(core::cell::UnsafeCell<[u64; 3]>);

// SAFETY: one hart. The handler cannot preempt itself -- `mstatus.MIE` is clear
// on trap entry and nothing here sets it -- and normal context touches this
// only before interrupts are enabled.
unsafe impl Sync for Deadlines {}

static DEADLINES: Deadlines = Deadlines(core::cell::UnsafeCell::new([0; 3]));

const JOBS: [fn(); 5] = [console_rx, type_c, periodic, periodic, periodic];

fn console_rx() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
    plic::Plic::new(target::PLIC_BASE).enable(target::UART_IRQS[0]);
}

fn type_c() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
    if let Some(&source) = target::TYPE_C_IRQS.first() {
        plic::Plic::new(target::PLIC_BASE).enable(source);
    }
}

fn periodic() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
}

fn mtime() -> u64 {
    // The standard rv32 read: high, low, high, retry if the high half moved.
    loop {
        // SAFETY: the CLINT window.
        unsafe {
            let hi = read_volatile((target::CLINT_BASE + MTIME_HI) as *const u32);
            let lo = read_volatile((target::CLINT_BASE + MTIME_LO) as *const u32);
            if hi == read_volatile((target::CLINT_BASE + MTIME_HI) as *const u32) {
                return ((hi as u64) << 32) | lo as u64;
            }
        }
    }
}

fn set_mtimecmp(deadline: u64) {
    // SAFETY: the CLINT window. The order is load-bearing -- see `src/timer.rs`.
    unsafe {
        write_volatile((target::CLINT_BASE + MTIMECMP_LO) as *mut u32, NEVER_LO);
        write_volatile(
            (target::CLINT_BASE + MTIMECMP_HI) as *mut u32,
            (deadline >> 32) as u32,
        );
        write_volatile(
            (target::CLINT_BASE + MTIMECMP_LO) as *mut u32,
            deadline as u32,
        );
    }
}

fn ticks(ms: u32) -> u64 {
    (target::TIME_HZ as u64 * ms as u64) / 1000
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

/// The whole cost of one comparator: decide which deadlines have passed, mark
/// those jobs ready, advance them, then find the earliest of all three and
/// program it. Every one of those steps disappears when each job has its own
/// comparator.
#[riscv_rt::core_interrupt(Interrupt::MachineTimer)]
fn machine_timer() {
    let now = mtime();
    let mut next = u64::MAX;
    for (index, &period) in PERIODS_MS.iter().enumerate() {
        // SAFETY: see the `unsafe impl Sync` above.
        let deadline = unsafe { &mut (*DEADLINES.0.get())[index] };
        if *deadline <= now {
            READY.fetch_or(1 << (2 + index as u32), Ordering::Release);
            // Add the period, never reload from now: see `src/timer.rs`.
            *deadline += ticks(period);
        }
        if *deadline < next {
            next = *deadline;
        }
    }
    set_mtimecmp(next);
}

#[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
fn machine_external() {
    let plic = plic::Plic::new(target::PLIC_BASE);
    while let Some(source) = plic.claim() {
        let job = if target::UART_IRQS.contains(&source) {
            Some(JOB_CONSOLE_RX)
        } else if target::TYPE_C_IRQS.contains(&source) {
            Some(JOB_TYPE_C)
        } else {
            None
        };
        plic.complete(source);
        if let Some(job) = job {
            plic.disable(source);
            READY.fetch_or(1 << job, Ordering::Release);
        }
    }
}

#[riscv_rt::entry]
fn main() -> ! {
    let plic = plic::Plic::new(target::PLIC_BASE);
    plic.set_threshold(0);
    for &source in target::UART_IRQS.iter().chain(target::TYPE_C_IRQS) {
        plic.set_priority(source, 1);
        plic.enable(source);
        plic.complete(source);
    }

    let now = mtime();
    let mut next = u64::MAX;
    for (index, &period) in PERIODS_MS.iter().enumerate() {
        let deadline = now + ticks(period);
        // SAFETY: before interrupts are enabled, so nothing else can be here.
        unsafe { (*DEADLINES.0.get())[index] = deadline };
        if deadline < next {
            next = deadline;
        }
    }
    set_mtimecmp(next);

    // SAFETY: every source and every deadline is configured.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
        riscv::interrupt::enable_interrupt(Interrupt::MachineTimer);
        riscv::interrupt::enable();
    }

    loop {
        let ready = READY.load(Ordering::Acquire);
        if ready == 0 {
            riscv::asm::wfi();
            continue;
        }
        let job = ready.trailing_zeros();
        READY.fetch_and(!(1 << job), Ordering::Relaxed);
        JOBS[job as usize]();
    }
}
