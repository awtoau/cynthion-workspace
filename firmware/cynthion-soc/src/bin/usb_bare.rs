//! moondancer's USB control path on this SoC, in moondancer's own shape: an
//! interrupt handler that produces events, a queue, and a superloop that
//! dispatches them.
//!
//! This is the control for `src/bin/usb_rtic.rs`. Same ported state machine,
//! same endpoint stand-in, same queue, same descriptor tables, same work done
//! per event -- so the difference between the two `.text` figures is RTIC and
//! nothing else. Built by `scripts/soc_usb_probe.py`.
//!
//! The original is `firmware/moondancer/src/bin/moondancer.rs`: `MachineExternal`
//! calls `get_usb_interrupt_event`, `dispatch_event` enqueues, and `main_loop`
//! drains the queue into `Control::dispatch_event` and then
//! `handle_vendor_request`. That structure is reproduced here line for line in
//! intent; what changed is where the events come from, because this machine has
//! no USB device controller. See `src/usb.rs`.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU32, Ordering};

use riscv::interrupt::Interrupt;

use usb::UsbDriver as _;

#[allow(dead_code)]
#[path = "../intc.rs"]
mod intc;
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

static EVENTS: usb::EventQueue = usb::EventQueue::new();
static FRAMES: usb::Frames = usb::Frames::new();
static JOURNAL: usb::Journal = usb::Journal::new();

/// Report frames seen. Consumed by the loop, which is where printing is allowed.
static REPORTS: AtomicU32 = AtomicU32::new(0);

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

/// Claim, read the peripheral, build an event, enqueue, complete.
///
/// moondancer's handler does exactly this and reads the eight setup bytes out of
/// `USB0_EP_CONTROL` while still in the handler, "for lowest latency". Here the
/// FIFO is a 16550's and the bytes are frames rather than a packet register --
/// that is the stand-in, and it is the only one on this path.
#[riscv_rt::core_interrupt(Interrupt::MachineExternal)]
fn machine_external() {
    let intc = intc::Intc::new(target::INTC_BASE);
    while let Some(source) = intc.next_ready() {
        if target::UART_IRQS.contains(&source) {
            // Drain, because the 16550's interrupt is a level: returning with a
            // byte still waiting re-enters this handler forever. `src/irq.rs`
            // has the full argument.
            while let Some(byte) = usb_report::rx_get(target::UART_BASES[0]) {
                if let Some(framed) = FRAMES.push(byte) {
                    EVENTS.enqueue(framed);
                }
            }
        }
        intc.clear(source);
    }
}

#[riscv_rt::entry]
fn main() -> ! {
    let intc = intc::Intc::new(target::INTC_BASE);
    intc.init();
    for &source in target::UART_IRQS {
        intc.clear(source);
        intc.enable(source);
    }
    usb_report::rx_enable(target::UART_BASES[0]);

    // SAFETY: every source is configured and every static the handler touches is
    // const-initialised before `main` runs.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineExternal);
        riscv::interrupt::enable();
    }

    // On the stack, exactly as moondancer's `Firmware` is a local in its `main`.
    // The RTIC binary cannot do that -- a resource is a `static` -- which is
    // most of the `.bss` difference between the two.
    let mut control = usb::Control::<RX>::new(0);
    let mut endpoints = usb::Endpoints::new();
    let mut console = usb_report::Console::new(target::UART_BASES[0]);
    let mut vendor_requests = 0_u32;

    usb_report::banner(&mut console, "bare");

    loop {
        while let Some(framed) = EVENTS.dequeue() {
            let event = match framed {
                usb::Framed::Report => {
                    REPORTS.fetch_add(1, Ordering::Release);
                    continue;
                }
                usb::Framed::Event(event) => event,
            };
            if let Some(setup) = control.dispatch_event(&mut endpoints, event) {
                // Where `handle_vendor_request` picks it up. Upstream dispatches
                // a libgreat command here; the reply path is the same stall, and
                // GCP is a protocol rather than a scheduling question.
                vendor_requests += 1;
                if setup.direction() == usb::Direction::HostToDevice {
                    endpoints.stall_endpoint_out(0);
                } else {
                    endpoints.stall_endpoint_in(0);
                }
            }
            JOURNAL.record(event, control.next, endpoints.writes);
        }

        while let Some(record) = JOURNAL.take() {
            usb_report::dispatched(&mut console, record);
        }

        if REPORTS.swap(0, Ordering::Acquire) > 0 {
            // Drain again before summarising. The record for the last event is
            // written before the report is dequeued, so this cannot miss it --
            // but the drain above may have run just before that record landed,
            // which would print the summary ahead of the line it summarises.
            while let Some(record) = JOURNAL.take() {
                usb_report::dispatched(&mut console, record);
            }
            usb_report::summary(&mut console, &control, &endpoints, &EVENTS, vendor_requests);
        }
    }
}
