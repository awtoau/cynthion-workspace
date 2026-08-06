//! The same workload, the same front end, no runtime: the control binary for
//! `src/bin/workload_rtic.rs`.
//!
//! The shell's `--features workload` and `--features preempt` builds are the
//! control for *latency* -- their figures are in
//! `docs/soc-concurrency.md` §5 and the harness re-asserts them --
//! but they cannot be the control for *size*: the shell is 42 KB of console,
//! power monitor, I2C and Type-C that the RTIC binary does not contain, so
//! differencing their `.text` measures the shell and not the dispatcher.
//!
//! This binary exists to make that difference mean something. Same modules by
//! the same `#[path]`, same PLIC front end, same 16550 loopback, same 1 ms tick,
//! same `src/workload.rs`. The only thing that differs is who dispatches: a
//! superloop here (`workload::command`, which calls `service()` then `drain()`
//! once per turn), an `#[rtic::app]` there. The same discipline
//! `src/bin/usb_bare.rs` and `src/bin/usb_rtic.rs` already use.
//!
//! Built by `scripts/soc_rtic_workload.py`; `required-features = ["wlbare"]`, so
//! the default build does not compile, link or lint it.

#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU32, Ordering};

// Before the module that uses it: `uart::report_errors` calls `crate::log!`.
#[macro_use]
#[allow(dead_code)]
#[path = "../log.rs"]
mod log;
#[allow(dead_code)]
#[path = "../clock.rs"]
mod clock;
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
// Everything that formats, so this file -- a handler module -- contains no
// `writeln!`, no `fmt::Write` and no `Uart`. See its module comment and
// `scripts/soc_irq_log_check.py`.
#[allow(dead_code)]
#[path = "../wl_report.rs"]
mod wl_report;
#[allow(dead_code)]
#[path = "../workload.rs"]
mod workload;

pub const MAX_CONSOLES: usize = 4;
const _: () = assert!(target::UART_BASES.len() <= MAX_CONSOLES);

/// The two counter reads `src/workload.rs` wants, without `src/metrics.rs`.
/// Identical to `src/bin/workload_rtic.rs`'s, so that the instruction counts the
/// two binaries report are produced by the same instruction.
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

    /// See `src/bin/workload_rtic.rs`: there are no turns here to be busy in,
    /// and `report_errors` is never called.
    pub fn busy() {}
}

static CLAIMS: AtomicU32 = AtomicU32::new(0);
static COMPLETES: AtomicU32 = AtomicU32::new(0);

/// Command bytes that arrived while the run was not active. Same ring, same
/// argument, as `src/bin/workload_rtic.rs`.
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

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

/// The PLIC front end. Byte for byte the RTIC binary's, minus the two `pend`s:
/// there is nothing to pend, because the superloop below picks the work up on
/// its next turn. That absence is exactly the difference under test.
#[riscv_rt::core_interrupt(riscv::interrupt::Interrupt::MachineExternal)]
fn machine_external() {
    let plic = plic::Plic::new(target::PLIC_BASE);
    while let Some(source) = plic.claim() {
        CLAIMS.fetch_add(1, Ordering::Relaxed);
        if target::UART_IRQS.contains(&source) {
            let base = target::UART_BASES[0];
            let mut rx = uart::UartRx::new(base);
            while let Some(byte) = rx.get() {
                if workload::arrival(base, byte) {
                    continue;
                }
                LINE.push(byte);
            }
        } else if source == workload::source::SOURCE {
            // Complete, disable, record -- `src/irq.rs`'s order, for
            // `src/irq.rs`'s reason.
            plic.complete(source);
            COMPLETES.fetch_add(1, Ordering::Relaxed);
            plic.disable(source);
            workload::defer(clock::Instant::ZERO.elapsed(clock::now()));
        }
        plic.complete(source);
        COMPLETES.fetch_add(1, Ordering::Relaxed);
    }
}

#[riscv_rt::entry]
fn main() -> ! {
    let mut console = wl_report::Console::new(target::UART_BASES[0]);
    console.banner("workload bare");

    let plic = plic::Plic::new(target::PLIC_BASE);
    plic.set_threshold(0);
    for &source in target::UART_IRQS {
        plic.set_priority(source, 1);
        plic.enable(source);
        plic.complete(source);
    }
    uart::UartRx::new(target::UART_BASES[0]).enable_rx_interrupt();

    // SAFETY: every source is configured and every static the handler touches is
    // const-initialised before this runs.
    unsafe { riscv::interrupt::enable_interrupt(riscv::interrupt::Interrupt::MachineExternal) };
    // SAFETY: `mstatus.MIE`, with every handler installed at link time.
    unsafe { riscv::interrupt::enable() };
    timer::start();

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
        // `workload::command` is the shell's `usb <n>`: it resets, switches the
        // console into loopback, and then IS the scheduler -- `service()` and
        // `drain()` once per turn until the run is over.
        console.run(strip(&line[..used]));
        used = 0;

        let (ticks, cost, late) = timer::stats();
        let per_us = target::TIME_HZ / 1_000_000;
        console.plic(
            "bare",
            CLAIMS.load(Ordering::Relaxed),
            COMPLETES.load(Ordering::Relaxed),
        );
        console.tick(ticks, cost / per_us, late / per_us);
    }
}

/// Drop the command word, leaving the digits `workload::command` parses.
fn strip(line: &[u8]) -> &[u8] {
    let mut start = 0;
    while start < line.len() && line[start] != b' ' {
        start += 1;
    }
    &line[start..]
}
