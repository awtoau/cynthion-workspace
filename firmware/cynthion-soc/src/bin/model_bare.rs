//! The floor: what this machine costs with no concurrency runtime at all.
//!
//! Built by `scripts/soc_model_probe.py` as the zero of the comparison in
//! `docs/rtic.md`. Every other skeleton does the same visible
//! work -- an interrupt front end, two sources, one counter, an idle loop --
//! so the difference between its `.text` and this one is the runtime.
//!
//! This is also the shape `src/main.rs` has today, reduced: the handler moves
//! the work it can do into a `static`, and normal context picks it up.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU32, Ordering};

#[allow(dead_code)]
#[path = "../intc.rs"]
mod intc;
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
    let intc = intc::Intc::new(target::INTC_BASE);
    loop {
        let ready = intc.ready();
        if ready == 0 {
            return;
        }
        let mut remaining = ready;
        while remaining != 0 {
            let source = remaining.trailing_zeros();
            remaining &= !(1 << source);
            // One arm, not two: the other skeletons dispatch to different tasks
            // here, and this one has no tasks to dispatch to. That is the floor.
            if target::UART_IRQS.contains(&source) || target::TYPE_C_IRQS.contains(&source) {
                SERVICED.fetch_add(1, Ordering::Relaxed);
            }
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
    // SAFETY: every source is configured and `SERVICED` needs no initialisation.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
        riscv::interrupt::enable();
    }

    loop {
        core::hint::spin_loop();
    }
}
