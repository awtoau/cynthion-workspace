//! moondancer's USB control path on RTIC 2.3, against `src/bin/usb_bare.rs`.
//!
//! Same ported state machine, same endpoint stand-in, same queue, same
//! descriptor tables, same work per event. What differs is who dispatches: a
//! superloop there, an `#[rtic::app]` hardware task here. Built by
//! `scripts/soc_usb_probe.py`; see `src/usb.rs` for what is real and what is a
//! stand-in, and `docs/rtic.md` for the backend.
//!
//! ## What RTIC did not take over, and why
//!
//! **The event queue survives adoption.** `usb::EventQueue` is a hand-rolled
//! SPSC ring whose correctness is an argument in a comment, and that argument is
//! exactly what RTIC's ceiling analysis was supposed to replace. It cannot: the
//! producer is [`machine_external`], the PLIC front end, which is not an RTIC
//! task and has no `lock`. `binds =` names a SLIC source, so no RTIC task can
//! bind the PLIC. Anything a hardware handler produces therefore reaches RTIC
//! through something RTIC does not check.
//!
//! What RTIC does check is everything downstream of the queue: `control`,
//! `endpoints` and `vendor_requests` are shared between the task and `#[idle]`,
//! and the compiler computes the ceiling. In the bare binary those three are
//! locals in `main` and the argument for why the handler may not touch them is
//! that it has no reference to them -- which is also a real argument, and is why
//! this comparison is worth making rather than assuming.
//!
//! ## What it cost to build
//!
//! Nothing new. The five shims `docs/rtic.md` records -- `peripherals =
//! false`, the `device` module, the `CoreInterrupt` alias, `use super::device`
//! inside the app module, and `PROVIDE(_ebss = __ebss)` in both linker scripts
//! -- were all it took the first time and all it takes with real work in the
//! tasks. Nothing about the port needed a sixth.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::AtomicU32;

#[allow(dead_code)]
#[path = "../plic.rs"]
mod plic;
#[allow(dead_code)]
#[path = "../target.rs"]
mod target;
#[allow(dead_code)]
#[path = "../usb.rs"]
mod usb;
#[allow(dead_code)]
#[path = "../usb_report.rs"]
mod usb_report;

/// The control endpoint's receive buffer, `LIBGREAT_MAX_COMMAND_SIZE` upstream.
const RX: usize = 1024;

/// Produced by the PLIC front end, consumed by the `UsbControl` task. Not an
/// RTIC resource -- see the module comment.
static EVENTS: usb::EventQueue = usb::EventQueue::new();
static FRAMES: usb::Frames = usb::Frames::new();
static JOURNAL: usb::Journal = usb::Journal::new();
static REPORTS: AtomicU32 = AtomicU32::new(0);

/// The device the CLINT backend names.
///
/// riscv-slic's generated `__riscv_slic_swi_pend` is written against
/// `riscv-peripheral`'s CLINT type and reaches for `CLINT::mswi().msip(Hart::H0)`.
/// Four accessors over one word, so it is written out rather than taking the
/// dependency -- the argument `src/main.rs` makes for having no HAL.
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
    use super::{device, plic, target, usb, usb_report, EVENTS, FRAMES, JOURNAL, REPORTS, RX};
    use core::sync::atomic::Ordering;
    use riscv::interrupt::Interrupt;
    use usb::UsbDriver as _;

    /// What the task and idle both reach. RTIC computes the ceiling from the
    /// priorities and raises the SLIC threshold to it; in the bare binary these
    /// are locals in `main` and nothing computes anything.
    #[shared]
    struct Shared {
        control: usb::Control<RX>,
        endpoints: usb::Endpoints,
        vendor_requests: u32,
    }

    /// The console, which only idle may have. A task that spun on `LSR.THRE`
    /// would block every lower-priority task for as long as the host took to
    /// drain, so the journal exists and this stays here.
    #[local]
    struct Local {
        console: usb_report::Console,
    }

    #[init]
    fn init(_cx: init::Context) -> (Shared, Local) {
        let plic = plic::Plic::new(target::PLIC_BASE);
        plic.set_threshold(0);
        for &source in target::UART_IRQS {
            plic.set_priority(source, 1);
            plic.enable(source);
            // Release a claim left in flight by a `j _start` reboot, as
            // `irq::init` does and for the same reason.
            plic.complete(source);
        }
        usb_report::rx_enable(target::UART_BASES[0]);

        // SAFETY: every source is configured, and the statics the front end
        // touches are const-initialised before this runs.
        unsafe { riscv::interrupt::enable_interrupt(Interrupt::MachineExternal) };

        (
            Shared {
                control: usb::Control::new(0),
                endpoints: usb::Endpoints::new(),
                vendor_requests: 0,
            },
            Local {
                console: usb_report::Console::new(target::UART_BASES[0]),
            },
        )
    }

    /// Lowest priority, preempted by the task. The shell would live here.
    #[idle(local = [console], shared = [control, endpoints, vendor_requests])]
    fn idle(mut cx: idle::Context) -> ! {
        usb_report::banner(cx.local.console, "rtic");

        loop {
            while let Some(record) = JOURNAL.take() {
                usb_report::dispatched(cx.local.console, record);
            }

            if REPORTS.swap(0, Ordering::Acquire) > 0 {
                // Drain again before summarising. The task records the last
                // event before it dequeues the report, so this cannot miss it
                // -- but the drain above may have run just before that record
                // landed, which would print the summary ahead of its own line.
                while let Some(record) = JOURNAL.take() {
                    usb_report::dispatched(cx.local.console, record);
                }
                // One lock over all three, so the ceiling is taken once and the
                // summary cannot see a half-updated run.
                (
                    &mut cx.shared.control,
                    &mut cx.shared.endpoints,
                    &mut cx.shared.vendor_requests,
                )
                    .lock(|control, endpoints, vendor_requests| {
                        usb_report::summary(
                            cx.local.console,
                            control,
                            endpoints,
                            &EVENTS,
                            *vendor_requests,
                        );
                    });
            }
        }
    }

    /// The PLIC front end: claim, read the peripheral, build an event, enqueue,
    /// pend, complete.
    ///
    /// Not an RTIC task and cannot be one. It runs at hardware priority, above
    /// every SLIC source, so it must stay this short -- and note that
    /// `rtic::export::pend` takes a global critical section, clearing
    /// `mstatus.MIE` for its duration, once per event.
    #[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
    fn machine_external() {
        let plic = plic::Plic::new(target::PLIC_BASE);
        while let Some(source) = plic.claim() {
            if target::UART_IRQS.contains(&source) {
                while let Some(byte) = usb_report::rx_get(target::UART_BASES[0]) {
                    if let Some(framed) = FRAMES.push(byte) {
                        EVENTS.enqueue(framed);
                        rtic::export::pend(slic::SoftwareInterrupt::UsbControl);
                    }
                }
            }
            plic.complete(source);
        }
    }

    /// Where moondancer's `main_loop` body goes: drain the queue, run the
    /// control state machine, hand a vendor request on.
    ///
    /// Preemptible by nothing here and preempting idle, which is the structural
    /// difference from the superloop -- the console printing in idle cannot
    /// delay this, where in the bare binary a long turn does exactly that.
    #[task(binds = UsbControl, priority = 2, shared = [control, endpoints, vendor_requests])]
    fn usb_control(cx: usb_control::Context) {
        let mut control = cx.shared.control;
        let mut endpoints = cx.shared.endpoints;
        let mut vendor_requests = cx.shared.vendor_requests;

        while let Some(framed) = EVENTS.dequeue() {
            let event = match framed {
                usb::Framed::Report => {
                    REPORTS.fetch_add(1, Ordering::Release);
                    continue;
                }
                usb::Framed::Event(event) => event,
            };
            (&mut control, &mut endpoints, &mut vendor_requests).lock(
                |control, endpoints, vendor_requests| {
                    if let Some(setup) = control.dispatch_event(endpoints, event) {
                        *vendor_requests += 1;
                        if setup.direction() == usb::Direction::HostToDevice {
                            endpoints.stall_endpoint_out(0);
                        } else {
                            endpoints.stall_endpoint_in(0);
                        }
                    }
                    JOURNAL.record(event, control.next, endpoints.writes);
                },
            );
        }
    }
}
