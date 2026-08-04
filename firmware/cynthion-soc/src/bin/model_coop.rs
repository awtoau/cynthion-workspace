//! A cooperative run-to-completion scheduler, in as few bytes as it can be
//! written: a ready bitmap, a table of handlers, and a dispatch loop.
//!
//! Built by `scripts/soc_model_probe.py`. Same visible work as the other
//! skeletons -- a PLIC front end, two sources, one shared counter -- so the
//! difference from `model_bare` is this scheduler and nothing else.
//!
//! The model: a handler claims, marks a job ready, masks its source, completes.
//! Nothing else. The job runs in NORMAL CONTEXT from the dispatch loop, so it
//! may spin on I2C and may print, which is the thing `src/irq.rs` currently
//! hand-rolls per source (`defer_type_c` and `PENDING_TYPE_C`). There are no
//! per-job stacks because a job returns before the next one starts, and no
//! locks because only the ready bitmap is shared with a handler.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU32, Ordering};

#[allow(dead_code)]
#[path = "../plic.rs"]
mod plic;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;

use riscv::interrupt::Interrupt;

/// One bit per job, lowest bit highest priority. The whole scheduler state.
static READY: AtomicU32 = AtomicU32::new(0);

/// The shared counter every skeleton increments.
static SERVICED: AtomicU32 = AtomicU32::new(0);

/// Job numbers, in priority order.
const JOB_CONSOLE_RX: u32 = 0;
const JOB_TYPE_C: u32 = 1;

/// The dispatch table. Priority is the index; there is no sorting anywhere
/// because `trailing_zeros` on the ready word is the scheduler's whole
/// decision.
const JOBS: [fn(); 2] = [console_rx, type_c];

fn console_rx() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
    // Where the source is re-armed: the handler masked it, and this is the
    // normal-context code that knows the work is done.
    plic::Plic::new(target::PLIC_BASE).enable(target::UART_IRQS[0]);
}

fn type_c() {
    SERVICED.fetch_add(1, Ordering::Relaxed);
    if let Some(&source) = target::TYPE_C_IRQS.first() {
        plic::Plic::new(target::PLIC_BASE).enable(source);
    }
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

/// Claim, mark, mask, complete. Bounded by the number of pending sources and
/// nothing else -- no work is done here, so no source can hold the CPU.
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
        // Complete BEFORE disable, never the other way round: the PLIC ignores
        // a completion for a source the context is not enabled for. See
        // `src/irq.rs::defer_type_c`, which found that out on the board.
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
    // SAFETY: every source is configured and both statics are const-initialised.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
        riscv::interrupt::enable();
    }

    loop {
        let ready = READY.load(Ordering::Acquire);
        if ready == 0 {
            // Nothing to run. `wfi` rather than a spin: the next interrupt is
            // what makes anything ready, so there is nothing to poll for.
            riscv::asm::wfi();
            continue;
        }
        let job = ready.trailing_zeros();
        READY.fetch_and(!(1 << job), Ordering::Relaxed);
        JOBS[job as usize]();
    }
}
