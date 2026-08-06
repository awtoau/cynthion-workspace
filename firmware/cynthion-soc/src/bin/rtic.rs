//! An RTIC skeleton for this SoC, built only by `--features rtic`.
//!
//! It is a spike, not the firmware: it boots, wires the PLIC to RTIC and runs
//! two tasks that count. The shell is still `src/main.rs`. What this exists to
//! do is hold the answer to the question `docs/decisions.md` decision 19 was
//! asserting without evidence -- whether RTIC accepts this machine at all --
//! in a form that a compiler re-checks. See `docs/soc-concurrency.md`.
//!
//! ## RTIC does not use the PLIC
//!
//! This is the finding that shapes everything below, and it is the opposite of
//! what `src/plic.rs` and decision 7 assume. RTIC 2.3's RISC-V backends
//! (`riscv-clint-backend`, `riscv-mecall-backend`) are `riscv-slic` backends: a
//! SOFTWARE interrupt controller. Every RTIC task, including the ones spelled
//! `#[task(binds = ...)]`, is a SLIC source dispatched in software priority
//! order out of the machine software interrupt. RTIC never claims, never
//! completes, and never touches a priority or threshold register.
//!
//! So a real hardware source needs a front end, and [`machine_external`] below
//! is it: the same claim loop `src/irq.rs` has, ending in `pend` instead of in
//! the work. The PLIC decides WHICH source; the SLIC decides WHEN the handler
//! for it runs relative to everything else. `Plic::set_threshold` is not what
//! RTIC locks with -- the SLIC has its own threshold, in a `static`.
//!
//! ## Why the modules are included by path
//!
//! This crate has no `[lib]`, so a `src/bin/` target cannot say `use crate::`.
//! The addresses and the PLIC driver are included from the files the shell
//! uses, rather than copied, so a source number that moves in the gateware
//! moves here too. Adding a `[lib]` to share them properly is migration work,
//! not spike work.

#![no_std]
#![no_main]

use core::panic::PanicInfo;

// `allow(dead_code)`: these files are sized for the shell, and a spike uses a
// handful of what they define. The alternative is 18 warnings on every build of
// this target, which is how a real one gets missed.
#[allow(dead_code)]
#[path = "../plic.rs"]
mod plic;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;

/// The device the CLINT backend names.
///
/// riscv-slic's generated `__riscv_slic_swi_pend` is written against
/// `riscv-peripheral`'s CLINT type and reaches for
/// `CLINT::mswi().msip(Hart::H0)`. That is four accessors over one word, so it
/// is written out here instead of taking the dependency -- the same argument
/// `src/main.rs` makes for having no HAL.
mod device {
    pub mod interrupt {
        // riscv 0.16 calls this enum `Interrupt`; riscv-slic emits
        // `CoreInterrupt`, which is what riscv 0.13 and later PACs generate.
        pub use riscv::interrupt::Interrupt as CoreInterrupt;

        #[derive(Clone, Copy)]
        pub enum Hart {
            H0 = 0,
        }
    }

    /// `msip` for hart 0, at CLINT offset 0. `src/timer.rs` documents the rest
    /// of the register map and deliberately omits this one, because until now
    /// nothing raised a software interrupt at us.
    pub struct Msip(*mut u32);

    impl Msip {
        pub fn pend(&self) {
            // SAFETY: the CLINT window, uncached on the SoC and a device under
            // QEMU. Bit 0 is the only bit `vexii_clint.py` implements.
            unsafe { core::ptr::write_volatile(self.0, 1) }
        }

        pub fn unpend(&self) {
            // SAFETY: as above.
            unsafe { core::ptr::write_volatile(self.0, 0) }
        }
    }

    pub struct Mswi;

    impl Mswi {
        pub fn msip(&self, hart: interrupt::Hart) -> Msip {
            Msip((super::target::CLINT_BASE + 4 * hart as usize) as *mut u32)
        }
    }

    // The spelling is not ours to choose: riscv-slic's macro emits `#pac::CLINT`.
    #[allow(clippy::upper_case_acronyms)]
    pub struct CLINT;

    impl CLINT {
        pub fn mswi() -> Mswi {
            Mswi
        }
    }
}

/// Diverging, and silent. The shell's panic handler prints; this one cannot,
/// because nothing here has initialised a console.
#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

#[rtic::app(device = device, peripherals = false, backend = H0)]
mod app {
    use super::{device, plic, target};
    use riscv::interrupt::Interrupt;

    /// What the two tasks have in common, so that the skeleton exercises a
    /// `lock` rather than only a dispatch. RTIC computes the ceiling from the
    /// priorities below and raises the SLIC threshold to it.
    #[shared]
    struct Shared {
        serviced: u32,
    }

    #[local]
    struct Local {}

    #[init]
    fn init(_cx: init::Context) -> (Shared, Local) {
        let plic = plic::Plic::new(target::PLIC_BASE);
        plic.set_threshold(0);
        for &source in target::UART_IRQS.iter().chain(target::TYPE_C_IRQS) {
            plic.set_priority(source, 1);
            plic.enable(source);
            // Release a claim left in flight by a `j _start` reboot, exactly as
            // `irq::init` does and for the same reason.
            plic.complete(source);
        }

        // SAFETY: every source is configured and the rings RTIC dispatches into
        // are its own resources, initialised by this function's return.
        unsafe { riscv::interrupt::enable_interrupt(Interrupt::MachineExternal) };

        (Shared { serviced: 0 }, Local {})
    }

    #[idle]
    fn idle(_cx: idle::Context) -> ! {
        loop {
            core::hint::spin_loop();
        }
    }

    /// The PLIC front end: claim, translate, pend, complete.
    ///
    /// Not an RTIC task and cannot be one -- see the module comment. It runs at
    /// hardware priority, above every SLIC source, so it must stay this short.
    /// The work belongs in the tasks below.
    #[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
    fn machine_external() {
        let plic = plic::Plic::new(target::PLIC_BASE);
        while let Some(source) = plic.claim() {
            if target::UART_IRQS.contains(&source) {
                rtic::export::pend(slic::SoftwareInterrupt::ConsoleRx);
            } else if target::TYPE_C_IRQS.contains(&source) {
                rtic::export::pend(slic::SoftwareInterrupt::TypeC);
            }
            // Always, including for a source with no task. See `src/irq.rs`.
            plic.complete(source);
        }
    }

    /// Where `irq::service` goes. A byte is waiting on some console.
    ///
    /// Higher priority than Type-C because a full 16550 FIFO drops input and a
    /// plug event does not.
    #[task(binds = ConsoleRx, priority = 2, shared = [serviced])]
    fn console_rx(mut cx: console_rx::Context) {
        cx.shared.serviced.lock(|n| *n += 1);
    }

    /// Where `typec::service` goes, once it can be told which port.
    ///
    /// Note what does NOT survive the move: `irq::defer_type_c` masks the source
    /// because clearing a FUSB302B is a millisecond of I2C and a handler may not
    /// spin that long. An RTIC task at a low priority is preemptible, so the
    /// mask-and-defer dance becomes a task that simply takes its time.
    #[task(binds = TypeC, priority = 1, shared = [serviced])]
    fn type_c(mut cx: type_c::Context) {
        cx.shared.serviced.lock(|n| *n += 1);
    }
}
