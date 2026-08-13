//! `model_coop`, plus three periodic jobs on three HARDWARE comparators.
//!
//! Built by `scripts/soc_model_probe.py`. Its pair is `model_coop_swqueue`,
//! which schedules the same three jobs on the one `mtimecmp`
//! `gateware/soc/cpu/clint.py` provides today. The difference between the
//! two `.text` figures is what the software timer queue costs.
//!
//! **The peripheral this assumes does not exist yet.** It is the smallest thing
//! that would do: per timer, a 32-bit reload register and a write-to-acknowledge
//! register, an auto-reloading down-counter, and its own source. 32-bit and
//! auto-reloading rather than a second `mtimecmp` because a 64-bit comparator is
//! a carry chain on a design closing 69.5 MHz against a 60 MHz target, and
//! because the rollover a 32-bit counter has is exactly what auto-reload removes.
//!
//! What that buys the firmware is the whole of the handler: there is no
//! deadline to compare, no period to add, no minimum to find, and no
//! set-in-the-past case, because the comparator that fired is the one that is
//! due, by construction.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::ptr::write_volatile;
use core::sync::atomic::{AtomicU32, Ordering};

#[allow(dead_code)]
#[path = "../intc.rs"]
mod intc;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;

use riscv::interrupt::Interrupt;

/// The timer block, in the SoC's peripheral window. Two registers per timer:
/// the reload, and a write-to-acknowledge that lowers the level.
const TIMERS_BASE: usize = 0xf000_2000;
const TIMER_STRIDE: usize = 8;
const TIMER_RELOAD: usize = 0;
const TIMER_ACK: usize = 4;

/// Sources for the three timers. Free numbers in this SoC's map, which has
/// four sources of thirty-one.
const TIMER_IRQS: [u32; 3] = [5, 6, 7];

/// The same three periods `src/main.rs` runs today.
const PERIODS_MS: [u32; 3] = [50, 50, 1];

static READY: AtomicU32 = AtomicU32::new(0);
static SERVICED: AtomicU32 = AtomicU32::new(0);

const JOB_CONSOLE_RX: u32 = 0;
const JOB_TYPE_C: u32 = 1;

const JOBS: [fn(); 5] = [console_rx, type_c, periodic, periodic, periodic];

fn console_rx() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
    intc::Intc::new(target::INTC_BASE).enable(target::UART_IRQS[0]);
}

fn type_c() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
    if let Some(&source) = target::TYPE_C_IRQS.first() {
        intc::Intc::new(target::INTC_BASE).enable(source);
    }
}

fn periodic() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
}

fn timer_reg(index: usize, offset: usize) -> *mut u32 {
    (TIMERS_BASE + index * TIMER_STRIDE + offset) as *mut u32
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

/// One claim loop for everything, timers included. A timer source is
/// acknowledged and its job marked ready; there is no arithmetic because the
/// comparator did it.
#[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
fn machine_external() {
    let intc = intc::Intc::new(target::INTC_BASE);
    while let Some(source) = intc.next_ready() {
        if target::UART_IRQS.contains(&source) {
            intc.clear(source);
            intc.disable(source);
            READY.fetch_or(1 << JOB_CONSOLE_RX, Ordering::Release);
        } else if target::TYPE_C_IRQS.contains(&source) {
            intc.clear(source);
            intc.disable(source);
            READY.fetch_or(1 << JOB_TYPE_C, Ordering::Release);
        } else if let Some(index) = TIMER_IRQS.iter().position(|&t| t == source) {
            // SAFETY: the timer window, a device.
            unsafe { write_volatile(timer_reg(index, TIMER_ACK), 1) };
            READY.fetch_or(1 << (2 + index as u32), Ordering::Release);
            intc.clear(source);
        } else {
            intc.clear(source);
        }
    }
}

#[riscv_rt::entry]
fn main() -> ! {
    let intc = intc::Intc::new(target::INTC_BASE);
    intc.init();
    for &source in target::UART_IRQS.iter().chain(target::TYPE_C_IRQS) {
        intc.clear(source);
        intc.enable(source);
    }

    for (index, &period) in PERIODS_MS.iter().enumerate() {
        // SAFETY: the timer window, a device. The counter starts when the
        // reload is written.
        unsafe {
            write_volatile(
                timer_reg(index, TIMER_RELOAD),
                (target::TIME_HZ / 1000) * period,
            );
        }
        intc.clear(TIMER_IRQS[index]);
        intc.enable(TIMER_IRQS[index]);
    }

    // SAFETY: every source and every timer is configured.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
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
