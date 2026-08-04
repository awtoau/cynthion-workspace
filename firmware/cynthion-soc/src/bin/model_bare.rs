//! The floor: what this machine costs with no concurrency runtime at all.
//!
//! Built by `scripts/soc_model_probe.py` as the zero of the comparison in
//! `docs/soc-concurrency-models.md`. Every other skeleton does the same visible
//! work -- a PLIC front end, two sources, one shared counter, an idle loop --
//! so the difference between its `.text` and this one is the runtime.
//!
//! This is also the shape `src/main.rs` has today, reduced: the handler moves
//! the work it can do into a `static`, and normal context picks it up.

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

/// The work both sources do, so that every skeleton shares one counter and the
/// comparison is of the runtime and not of the payload.
static SERVICED: AtomicU32 = AtomicU32::new(0);

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

#[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
fn machine_external() {
    let plic = plic::Plic::new(target::PLIC_BASE);
    while let Some(source) = plic.claim() {
        // One arm, not two: the other skeletons dispatch to different tasks
        // here, and this one has no tasks to dispatch to. That is the floor.
        if target::UART_IRQS.contains(&source) || target::TYPE_C_IRQS.contains(&source) {
            SERVICED.fetch_add(1, Ordering::Relaxed);
        }
        plic.complete(source);
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
    // SAFETY: every source is configured and `SERVICED` needs no initialisation.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
        riscv::interrupt::enable();
    }

    loop {
        core::hint::spin_loop();
    }
}
