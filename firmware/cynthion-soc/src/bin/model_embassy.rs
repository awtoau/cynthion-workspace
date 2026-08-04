//! An Embassy skeleton for this SoC, built only by `--features embassy`.
//!
//! Same visible work as `src/bin/rtic.rs` and `src/bin/model_bare.rs`: a PLIC
//! front end, two sources, one shared counter, an idle loop. What differs is
//! the runtime underneath, which is what `scripts/soc_model_probe.py` measures.
//!
//! ## Embassy is a thread-mode executor, so the PLIC front end stays
//!
//! `embassy-executor` 0.10's `platform-riscv32` is a `wfi`-based thread-mode
//! executor: one poll loop, no priorities, no preemption. A hardware interrupt
//! cannot BE a task -- it wakes one. So `machine_external` below is the same
//! claim loop `src/irq.rs` has, ending in `Signal::signal` instead of in the
//! work, exactly as the RTIC skeleton ends in `pend`.
//!
//! The consequence is the one that decides the model: every task runs at the
//! same priority, cooperatively, at `await` points. Nothing preempts anything.
//! `power::poll`'s millisecond of I2C delays a console byte by that millisecond
//! unless it is broken into `await`s, and there is no compiler check that it is.
//!
//! ## No per-task stacks, and that is the point
//!
//! A task is a state machine in a `static` sized by the compiler from its own
//! locals, so N tasks cost N futures rather than N stacks. That is the
//! difference from a FreeRTOS-style model, and on 46 KiB of block RAM it is the
//! difference that matters.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU32, Ordering};

use embassy_executor::{Executor, Spawner};
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::signal::Signal;
use static_cell::StaticCell;

#[allow(dead_code)]
#[path = "../plic.rs"]
mod plic;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;

use riscv::interrupt::Interrupt;

/// The shared counter every skeleton increments. An `Atomic` rather than an
/// `embassy_sync::Mutex` because the handler touches nothing but the signals --
/// a mutex here would be measuring `embassy-sync`, not the executor.
static SERVICED: AtomicU32 = AtomicU32::new(0);

/// What the handler wakes. One per source, which is Embassy's answer to
/// `binds =`: the interrupt does not dispatch, it publishes.
static CONSOLE_RX: Signal<CriticalSectionRawMutex, ()> = Signal::new();
static TYPE_C: Signal<CriticalSectionRawMutex, ()> = Signal::new();

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

#[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
fn machine_external() {
    let plic = plic::Plic::new(target::PLIC_BASE);
    while let Some(source) = plic.claim() {
        if target::UART_IRQS.contains(&source) {
            CONSOLE_RX.signal(());
        } else if target::TYPE_C_IRQS.contains(&source) {
            TYPE_C.signal(());
        }
        plic.complete(source);
    }
}

#[embassy_executor::task]
async fn console_rx() {
    loop {
        CONSOLE_RX.wait().await;
        SERVICED.fetch_add(1, Ordering::Relaxed);
    }
}

#[embassy_executor::task]
async fn type_c() {
    loop {
        TYPE_C.wait().await;
        SERVICED.fetch_add(1, Ordering::Relaxed);
    }
}

#[embassy_executor::task]
async fn init(spawner: Spawner) {
    let plic = plic::Plic::new(target::PLIC_BASE);
    plic.set_threshold(0);
    for &source in target::UART_IRQS.iter().chain(target::TYPE_C_IRQS) {
        plic.set_priority(source, 1);
        plic.enable(source);
        plic.complete(source);
    }
    // SAFETY: every source is configured and both signals are const-initialised.
    unsafe { riscv::interrupt::enable_interrupt(Interrupt::MachineExternal) };

    // `if let Ok` rather than `unwrap`: a spawn can only fail by the task's
    // single pool slot already being taken, and unwrapping would link
    // `core::panicking` and its formatting into a measurement of the executor.
    if let Ok(token) = console_rx() {
        spawner.spawn(token);
    }
    if let Ok(token) = type_c() {
        spawner.spawn(token);
    }
}

static EXECUTOR: StaticCell<Executor> = StaticCell::new();

#[riscv_rt::entry]
fn main() -> ! {
    // SAFETY: nothing is asking yet; the sources are enabled inside `init`.
    unsafe { riscv::interrupt::enable() };
    EXECUTOR.init(Executor::new()).run(|spawner| {
        if let Ok(token) = init(spawner) {
            spawner.spawn(token);
        }
    })
}
