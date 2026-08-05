//! The #115 USB workload on RTIC 2.3, against the superloop and the
//! hand-written dispatcher.
//!
//! `docs/soc-workload-and-preemption.md` §6 names this as the cheapest next
//! measurement: *"flesh out `src/bin/rtic.rs` until it runs `workload::handle`,
//! and re-run `scripts/soc_workload.py` against it"*. This is that. Same
//! `src/workload.rs`, same arrival sequence, same 4,169 instructions of
//! device-emulation work per event, same 1,000 µs deferred job on the same 5 ms
//! grid. What differs is who dispatches:
//!
//!     superloop   main.rs's turn calls service() then drain()
//!     preempt     src/dispatch.rs, from the tail of the PLIC handler
//!     rtic        two #[task]s on the SLIC, drained from the machine software
//!                 interrupt, dispatched by riscv-slic in priority order
//!
//! ## The PLIC front end is still hand-written, and has to be
//!
//! `binds =` names a **SLIC** source. `riscv-slic-macros` 0.2.0's `codegen!`
//! takes `pac`, `swi = [...]` and a backend, and nothing else: there is no
//! hardware-interrupt list in the macro's grammar, so no `#[task]` can bind a
//! PLIC source on any RISC-V target. [`machine_external`] below is therefore the
//! same claim loop `src/irq.rs` has, with `rtic::export::pend` where the work
//! used to be, and the queue between it and the task is a `static` that RTIC
//! does not check.
//!
//! ## What RTIC does check here
//!
//! `Shared::progress`. The event task adds to it, the deferral task adds to it
//! and `#[idle]` waits on it, so its ceiling is priority 2 and the compiler --
//! not a comment -- is what stops idle reading a half-written pair. In the other
//! two models the same state is `COMPLETED`, an `AtomicU32` inside
//! `src/workload.rs`. That is the whole of the trade, on the hot path, where it
//! can be costed: see the `lock` row in the `rtic` report.
//!
//! ## Priorities
//!
//! Event task 2, deferral task 1, idle 0 -- `src/dispatch.rs`'s assignment
//! exactly, so the two preemptive models differ in mechanism and not in policy.
//! A 1,000 µs deferral must not delay an event whose deadline is 375 µs, and
//! nothing else here is allowed to run at all.
//!
//! ## Built by `scripts/soc_rtic_workload.py`, never by the default build
//!
//! `required-features = ["rticwl"]`, so cargo does not compile, link or lint
//! this without it, and `src/main.rs` does not know it exists.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU32, Ordering};

#[allow(dead_code)]
#[path = "../clock.rs"]
mod clock;
// `src/uart.rs`'s `report_errors` calls `crate::log!`, so the macro has to be
// declared before the module that uses it. It is the shell's stamped log line
// and it works here unchanged: `log::now` reads `timer::millis`, which this
// binary starts.
#[macro_use]
#[allow(dead_code)]
#[path = "../log.rs"]
mod log;
#[allow(dead_code)]
#[path = "../plic.rs"]
mod plic;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;
#[allow(dead_code)]
#[path = "../timer.rs"]
mod timer;
#[allow(dead_code)]
#[path = "../uart.rs"]
mod uart;
#[allow(dead_code)]
#[path = "../workload.rs"]
mod workload;

/// `src/uart.rs` sizes its per-console arrays from this. Same value as
/// `src/main.rs`, and the same assertion under it.
pub const MAX_CONSOLES: usize = 4;
const _: () = assert!(target::UART_BASES.len() <= MAX_CONSOLES);

/// The two counter reads `src/workload.rs` wants, without `src/metrics.rs`.
///
/// metrics.rs is the shell's turn accounting and pulls in `power` and the whole
/// I2C stack with it; the workload uses exactly these two functions from it, and
/// they are copied verbatim so that the instruction counts here and in the other
/// two models are produced by the identical instruction.
mod metrics {
    pub fn mcycle() -> u32 {
        let value: u32;
        // SAFETY: a read of an implemented, side-effect-free machine counter.
        unsafe {
            core::arch::asm!("csrr {0}, mcycle", out(reg) value, options(nomem, nostack));
        }
        value
    }

    pub fn minstret() -> u32 {
        let value: u32;
        // SAFETY: as `mcycle`; the same plugin decodes both.
        unsafe {
            core::arch::asm!("csrr {0}, minstret", out(reg) value, options(nomem, nostack));
        }
        value
    }

    /// "This turn did something", which is meaningless here: there are no turns.
    ///
    /// `uart::report_errors` calls it, and this binary never calls
    /// `report_errors` -- the shell's busy/idle split is a property of a main
    /// loop, and `#[idle]` spinning on one resource is not one. Empty rather
    /// than absent so that `src/uart.rs` compiles unmodified.
    pub fn busy() {}
}

/// Where the dispatch instructions go, and what a `critical_section` costs.
///
/// Every counter here is behind `--features rticprobe`, and the probe build is
/// **not** the build the latency table is taken from: two `csrr`s and two atomic
/// adds per probe point would land inside the very path being timed. The clean
/// build gives the latency and the size; this one gives the anatomy.
///
/// `docs/soc-workload-and-preemption.md` §2 measured the hand-written
/// dispatcher the same way -- `dispatch::run` brackets everything that is not
/// the task with `minstret` -- so the two numbers are comparable.
#[cfg(feature = "rticprobe")]
pub mod probe {
    use core::sync::atomic::{AtomicU32, Ordering};

    /// Instructions inside `rtic::export::pend`, and how many times.
    pub static PEND: AtomicU32 = AtomicU32::new(0);
    pub static PENDS: AtomicU32 = AtomicU32::new(0);

    /// `minstret` as the PLIC front end returned, and the instructions from
    /// there to the first one of the task body: `mret`, the machine software
    /// trap, riscv-rt's frame save, `__riscv_slic_pop` and the threshold raise.
    pub static AT_RETURN: AtomicU32 = AtomicU32::new(0);
    pub static TRAP: AtomicU32 = AtomicU32::new(0);
    pub static TRAPS: AtomicU32 = AtomicU32::new(0);

    /// Instructions inside the `lock` on `Shared::progress`.
    pub static LOCK: AtomicU32 = AtomicU32::new(0);
    pub static LOCKS: AtomicU32 = AtomicU32::new(0);

    /// Critical sections: how many, how many instructions inside them in total,
    /// and the longest one. Nested acquires are not counted -- only the
    /// outermost one actually holds `mstatus.MIE` down.
    pub static CS: AtomicU32 = AtomicU32::new(0);
    pub static CS_INSTR: AtomicU32 = AtomicU32::new(0);
    pub static CS_WORST: AtomicU32 = AtomicU32::new(0);
    pub static CS_ENTERED: AtomicU32 = AtomicU32::new(0);

    /// What an EMPTY critical section costs in this build.
    ///
    /// Every figure here is inflated by the probe that took it -- two `csrr
    /// minstret`s, a subtract and two atomic read-modify-writes sit inside the
    /// `acquire`/`release` pair. So the instrument is calibrated before it is
    /// used: one `critical_section::with(|_| {})` whose body is nothing, whose
    /// measured cost is therefore the floor, and which every other figure can be
    /// read against. A new instrument's first measurement is of a known
    /// quantity.
    pub static CS_FLOOR: AtomicU32 = AtomicU32::new(0);

    pub fn calibrate() {
        reset();
        critical_section::with(|_| {});
        CS_FLOOR.store(CS_WORST.load(Ordering::Relaxed), Ordering::Relaxed);
        reset();
    }

    pub fn reset() {
        for counter in [
            &PEND, &PENDS, &AT_RETURN, &TRAP, &TRAPS, &LOCK, &LOCKS, &CS, &CS_INSTR, &CS_WORST,
        ] {
            counter.store(0, Ordering::Relaxed);
        }
    }
}

/// A `critical_section` implementation that measures itself.
///
/// The question `docs/soc-workload-and-preemption.md` §3 leaves open is not how
/// many critical sections RTIC takes but **how long the machine spends with
/// `mstatus.MIE` clear**, because that is the window in which the 1 ms tick
/// cannot run and a second endpoint cannot be serviced. `riscv-slic` calls
/// `critical_section::with` on every `pend`, every threshold raise and every
/// threshold restore, so instrumenting the implementation catches all of them
/// including the ones inside the generated code.
///
/// Same body as `riscv`'s `critical-section-single-hart`, which is what the
/// clean build links: read `mstatus.MIE`, clear it, and restore it only if this
/// acquire was the one that cleared it. The measurement is the two `csrr
/// minstret`s around that.
#[cfg(feature = "rticprobe")]
mod cs {
    use super::probe;
    use core::sync::atomic::Ordering;
    use critical_section::RawRestoreState;

    struct SingleHartProbe;
    critical_section::set_impl!(SingleHartProbe);

    unsafe impl critical_section::Impl for SingleHartProbe {
        unsafe fn acquire() -> RawRestoreState {
            let was_enabled = riscv::register::mstatus::read().mie();
            riscv::interrupt::disable();
            if was_enabled {
                probe::CS_ENTERED.store(super::metrics::minstret(), Ordering::Relaxed);
            }
            was_enabled
        }

        unsafe fn release(was_enabled: RawRestoreState) {
            if was_enabled {
                let held = super::metrics::minstret()
                    .wrapping_sub(probe::CS_ENTERED.load(Ordering::Relaxed));
                probe::CS.fetch_add(1, Ordering::Relaxed);
                probe::CS_INSTR.fetch_add(held, Ordering::Relaxed);
                probe::CS_WORST.fetch_max(held, Ordering::Relaxed);
                // SAFETY: this acquire is the one that cleared `MIE`, so the
                // caller's invariant is the same one `critical-section-single-hart`
                // relies on.
                unsafe { riscv::interrupt::enable() };
            }
        }
    }
}

/// PLIC claims and completions, per handler pass.
///
/// Issue 3 of the six: **correct acknowledgement and completion for each
/// source.** A claim that is never completed gates that source off for the rest
/// of the session -- `pending[i] = sources[i] & ~claimed[i]` -- and the failure
/// is silent, so it is counted rather than argued. `defer` completes its source
/// a second time on the way out of the loop, exactly as `src/irq.rs` does.
static CLAIMS: AtomicU32 = AtomicU32::new(0);
static COMPLETES: AtomicU32 = AtomicU32::new(0);

/// Bytes that arrived while the run was not active, i.e. the command line.
///
/// A ring rather than a `Uart` read from `#[idle]`, because the 16550's RX
/// interrupt is the PLIC source the front end owns: a reader in idle would race
/// the handler for the same FIFO. Single producer (the handler), single consumer
/// (idle), which is the same argument `src/irq.rs`'s `RINGS` makes and the same
/// one RTIC cannot check.
static LINE: Line = Line::new();

struct Line {
    bytes: [AtomicU32; 32],
    head: AtomicU32,
    tail: AtomicU32,
}

impl Line {
    const fn new() -> Self {
        Self {
            bytes: [const { AtomicU32::new(0) }; 32],
            head: AtomicU32::new(0),
            tail: AtomicU32::new(0),
        }
    }

    fn push(&self, byte: u8) {
        let head = self.head.load(Ordering::Relaxed);
        let next = (head + 1) % 32;
        if next == self.tail.load(Ordering::Acquire) {
            return;
        }
        self.bytes[head as usize].store(byte as u32, Ordering::Relaxed);
        self.head.store(next, Ordering::Release);
    }

    fn pop(&self) -> Option<u8> {
        let tail = self.tail.load(Ordering::Relaxed);
        if tail == self.head.load(Ordering::Acquire) {
            return None;
        }
        let byte = self.bytes[tail as usize].load(Ordering::Relaxed) as u8;
        self.tail.store((tail + 1) % 32, Ordering::Release);
        Some(byte)
    }
}

/// The device the CLINT backend names. Identical to `src/bin/rtic.rs`'s and
/// `src/bin/usb_rtic.rs`'s; `docs/rtic-adoption.md` §3 is why it is written out
/// rather than taken from `riscv-peripheral`.
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

    /// `msip` for hart 0, at CLINT offset 0.
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

/// Diverging and silent: `#[idle]` owns the console and a panic has no way to
/// take it back.
#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

#[rtic::app(device = device, peripherals = false, backend = H0)]
mod app {
    use super::{clock, device, plic, target, timer, uart, workload, CLAIMS, COMPLETES, LINE};
    use core::fmt::Write;
    use core::sync::atomic::Ordering;
    use riscv::interrupt::Interrupt;

    /// The one piece of workload state RTIC owns.
    ///
    /// `events` is written by the priority-2 task and read by idle, which is
    /// what makes the ceiling 2 and what makes this a resource rather than an
    /// atomic. `defers` is written by the priority-1 task, so the same resource
    /// is genuinely three-way shared and the ceiling is not decorative.
    #[shared]
    struct Shared {
        progress: Progress,
    }

    pub struct Progress {
        pub events: u32,
        pub defers: u32,
    }

    #[local]
    struct Local {
        console: uart::Uart,
    }

    #[init]
    fn init(_cx: init::Context) -> (Shared, Local) {
        let mut console = uart::Uart::new(target::UART_BASES[0]);
        console.init();

        let plic = plic::Plic::new(target::PLIC_BASE);
        plic.set_threshold(0);
        for &source in target::UART_IRQS {
            plic.set_priority(source, 1);
            plic.enable(source);
            // Release a claim left in flight by a `j _start` reboot, as
            // `irq::init` does and for the same reason.
            plic.complete(source);
        }
        uart::UartRx::new(target::UART_BASES[0]).enable_rx_interrupt();

        // SAFETY: every source is configured and every static the front end
        // touches is const-initialised before this runs.
        unsafe { riscv::interrupt::enable_interrupt(Interrupt::MachineExternal) };

        // The 1 ms tick, which is also the workload's arrival generator. After
        // the PLIC, for `src/main.rs`'s reason: neither ordering is load-bearing
        // but the deadline must be programmed before `mie.MTIE` is set, and
        // `timer::start` does both in that order.
        timer::start();

        (
            Shared {
                progress: Progress {
                    events: 0,
                    defers: 0,
                },
            },
            Local { console },
        )
    }

    /// The shell, in miniature: read `usb <n>`, run it, print the report.
    ///
    /// Priority 0 and preemptible by both tasks, which is the structural claim
    /// under test -- in the superloop this loop *is* the scheduler and every
    /// microsecond it spends is a microsecond an event waits.
    #[idle(local = [console], shared = [progress])]
    fn idle(mut cx: idle::Context) -> ! {
        let console = cx.local.console;
        let _ = console.write_str("Cynthion RISC-V SoC: workload on RTIC\n");

        let mut line = [0u8; 16];
        let mut used = 0;
        loop {
            let Some(byte) = LINE.pop() else {
                continue;
            };
            if byte != b'\r' && byte != b'\n' {
                if used < line.len() {
                    line[used] = byte;
                    used += 1;
                }
                continue;
            }
            let want = number(&line[..used]);
            used = 0;
            if want == 0 {
                let _ = console.write_str("usage: usb <events>\n");
                continue;
            }

            #[cfg(feature = "rticprobe")]
            super::probe::calibrate();
            cx.shared.progress.lock(|p| {
                p.events = 0;
                p.defers = 0;
            });
            let started = workload::begin();

            // The wait. Nothing else happens here: both jobs are tasks, and the
            // only thing this loop does is ask whether the run is over -- which
            // is the same shape as `--features preempt`'s `spin_loop`, and the
            // only fair comparison to it.
            //
            // **What it asks matters, and this is the measurement `--features
            // rticspin` exists for.** `lock` raises the SLIC threshold to the
            // resource's ceiling -- 2, the event task's own priority -- so an
            // idle loop that polls the shared resource spends part of every
            // iteration in a window where the task it is waiting for cannot be
            // dispatched. `pend` from the front end sees `is_ready()` false and
            // does not even raise `msip`; the event waits for idle's lock to
            // finish. Polling `workload::completed()`, an atomic outside RTIC,
            // takes no threshold and no critical section.
            #[cfg(feature = "rticspin")]
            while cx.shared.progress.lock(|p| p.events) < want {
                core::hint::spin_loop();
            }
            #[cfg(not(feature = "rticspin"))]
            while workload::completed() < want {
                core::hint::spin_loop();
            }

            workload::finish(console, started);
            let (events, defers) = cx.shared.progress.lock(|p| (p.events, p.defers));
            let (ticks, cost, late) = timer::stats();
            let _ = writeln!(
                console,
                "  rtic    shared events {} defers {}  claims {} completes {}",
                events,
                defers,
                CLAIMS.load(Ordering::Relaxed),
                COMPLETES.load(Ordering::Relaxed)
            );
            let per_us = target::TIME_HZ / 1_000_000;
            let _ = writeln!(
                console,
                "  tick    ticks {} worst cost {} us  worst late {} us",
                ticks,
                cost / per_us,
                late / per_us
            );
            #[cfg(feature = "rticprobe")]
            report_probe(console);
        }
    }

    /// Digits only; anything else makes the line unusable, which idle reports.
    fn number(text: &[u8]) -> u32 {
        let mut value = 0u32;
        let mut any = false;
        for &byte in text {
            if byte == b' ' || byte == b'u' || byte == b's' || byte == b'b' {
                continue;
            }
            let Some(digit) = byte.checked_sub(b'0') else {
                return 0;
            };
            if digit > 9 {
                return 0;
            }
            value = value * 10 + digit as u32;
            any = true;
        }
        if any {
            value
        } else {
            0
        }
    }

    #[cfg(feature = "rticprobe")]
    fn report_probe(console: &mut uart::Uart) {
        use super::probe;
        let pends = probe::PENDS.load(Ordering::Relaxed).max(1);
        let traps = probe::TRAPS.load(Ordering::Relaxed).max(1);
        let locks = probe::LOCKS.load(Ordering::Relaxed).max(1);
        let cs = probe::CS.load(Ordering::Relaxed).max(1);
        let _ = writeln!(
            console,
            "  probe   pend {} x {} instr  trap->task {} x {} instr  lock {} x {} instr",
            pends,
            probe::PEND.load(Ordering::Relaxed) / pends,
            traps,
            probe::TRAP.load(Ordering::Relaxed) / traps,
            locks,
            probe::LOCK.load(Ordering::Relaxed) / locks
        );
        let _ = writeln!(
            console,
            "  cs      taken {} total {} instr  mean {} worst {} floor {} instr",
            cs,
            probe::CS_INSTR.load(Ordering::Relaxed),
            probe::CS_INSTR.load(Ordering::Relaxed) / cs,
            probe::CS_WORST.load(Ordering::Relaxed),
            probe::CS_FLOOR.load(Ordering::Relaxed)
        );
    }

    /// The PLIC front end: claim, drain the peripheral, pend, complete.
    ///
    /// Not an RTIC task and cannot be one -- see the module comment. The body is
    /// `src/irq.rs::machine_external` with `rtic::export::pend` where
    /// `dispatch::pend` is, and it keeps `defer_workload`'s order exactly:
    /// **complete, disable, record**, because the other order gated a Type-C
    /// source off permanently and that was found on the board.
    #[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
    fn machine_external() {
        let plic = plic::Plic::new(target::PLIC_BASE);
        while let Some(source) = plic.claim() {
            CLAIMS.fetch_add(1, Ordering::Relaxed);
            if target::UART_IRQS.contains(&source) {
                let base = target::UART_BASES[0];
                let mut rx = uart::UartRx::new(base);
                while let Some(byte) = rx.get() {
                    // One arriving byte is one USB event. `arrival` drains the
                    // stand-in setup FIFO here, in the handler, because that is
                    // where the gateware window is.
                    if workload::arrival(base, byte) {
                        pend_usb();
                        continue;
                    }
                    LINE.push(byte);
                }
            } else if source == workload::source::SOURCE {
                plic.complete(source);
                COMPLETES.fetch_add(1, Ordering::Relaxed);
                plic.disable(source);
                workload::defer(clock::Instant::ZERO.elapsed(clock::now()));
                pend_type_c();
            }
            // ALWAYS, including for a source with no task, and including the
            // deferral source completed above -- `src/irq.rs` does the same
            // double completion and it is what the control models measure.
            plic.complete(source);
            COMPLETES.fetch_add(1, Ordering::Relaxed);
        }
        #[cfg(feature = "rticprobe")]
        super::probe::AT_RETURN.store(super::metrics::minstret(), Ordering::Relaxed);
    }

    /// `pend`, with the probe around it.
    ///
    /// Per arriving byte, where `--features preempt` puts its `amoor.w`. A
    /// smarter port would hoist it out of the drain loop; this one does not,
    /// because the question is what the same structure costs on each runtime,
    /// and the report gives the per-pend figure that would price the hoist.
    #[inline(always)]
    fn pend_usb() {
        #[cfg(feature = "rticprobe")]
        let before = super::metrics::minstret();
        rtic::export::pend(slic::SoftwareInterrupt::UsbEvent);
        #[cfg(feature = "rticprobe")]
        {
            super::probe::PEND.fetch_add(
                super::metrics::minstret().wrapping_sub(before),
                Ordering::Relaxed,
            );
            super::probe::PENDS.fetch_add(1, Ordering::Relaxed);
        }
    }

    #[inline(always)]
    fn pend_type_c() {
        #[cfg(feature = "rticprobe")]
        let before = super::metrics::minstret();
        rtic::export::pend(slic::SoftwareInterrupt::TypeC);
        #[cfg(feature = "rticprobe")]
        {
            super::probe::PEND.fetch_add(
                super::metrics::minstret().wrapping_sub(before),
                Ordering::Relaxed,
            );
            super::probe::PENDS.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// The event task: 4,169 instructions of device emulation per event.
    ///
    /// Priority 2, so it preempts the 1,000 µs deferral below. That is the whole
    /// mechanism the 375 µs deadline needs, and it is the same policy
    /// `src/dispatch.rs` implements by hand.
    #[task(binds = UsbEvent, priority = 2, shared = [progress])]
    fn usb_event(mut cx: usb_event::Context) {
        #[cfg(feature = "rticprobe")]
        {
            let entered = super::metrics::minstret();
            super::probe::TRAP.fetch_add(
                entered.wrapping_sub(super::probe::AT_RETURN.load(Ordering::Relaxed)),
                Ordering::Relaxed,
            );
            super::probe::TRAPS.fetch_add(1, Ordering::Relaxed);
        }

        let done = workload::usb_drain();
        if done > 0 {
            lock_progress(&mut cx.shared.progress, |p| p.events += done);
        }
    }

    /// The deferred job: a millisecond that must not delay an event.
    #[task(binds = TypeC, priority = 1, shared = [progress])]
    fn type_c(mut cx: type_c::Context) {
        if workload::type_c_run() {
            lock_progress(&mut cx.shared.progress, |p| p.defers += 1);
        }
    }

    /// The `lock`, with the probe around it. Raises the SLIC threshold to the
    /// ceiling and drops it again -- two `critical_section`s, which is what the
    /// `cs` row counts.
    #[inline(always)]
    fn lock_progress<R>(
        resource: &mut impl rtic::Mutex<T = Progress>,
        body: impl FnOnce(&mut Progress) -> R,
    ) -> R {
        #[cfg(feature = "rticprobe")]
        let before = super::metrics::minstret();
        let result = resource.lock(body);
        #[cfg(feature = "rticprobe")]
        {
            super::probe::LOCK.fetch_add(
                super::metrics::minstret().wrapping_sub(before),
                Ordering::Relaxed,
            );
            super::probe::LOCKS.fetch_add(1, Ordering::Relaxed);
        }
        result
    }
}
