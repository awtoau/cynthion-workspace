//! The console for the USB spikes, and the only place in them that formats.
//!
//! Both `src/bin/usb_rtic.rs` and `src/bin/usb_bare.rs` carry a
//! `#[riscv_rt::core_interrupt]`, which makes them handler modules: they may not
//! contain `write!`, `writeln!`, `fmt::Write` or the name `Uart`
//! (`scripts/soc_irq_log_check.py`). That is the right rule and this is how the
//! shell obeys it too -- `src/irq.rs` records, `src/events.rs` formats. Every
//! function here is called from normal context, never from a handler and never
//! from an RTIC task.
//!
//! A private 16550 rather than `src/uart.rs`, which reaches for `crate::metrics`
//! and `crate::log!` and cannot be `#[path]`-included into a binary that has
//! neither.

use core::fmt::Write;
use core::ptr::{read_volatile, write_volatile};

use crate::usb;

const RBR_THR: usize = 0;
const IER: usize = 1;
const LSR: usize = 5;

const LSR_DR: u8 = 1 << 0;
const LSR_THRE: u8 = 1 << 5;
const IER_ERBFI: u8 = 1 << 0;

/// Turns to wait for the transmit holding register. At 115200 baud a character
/// is 87 us, so a full 16-deep FIFO drains in 1.4 ms; one turn of this loop is
/// an uncached MMIO read, tens of cycles at most. 200,000 covers that with room
/// and bounds the case where nothing is draining the other end -- the same
/// argument `src/uart.rs` makes for its own bound.
const THRE_SPINS: u32 = 200_000;

/// The transmit half. Owned by whoever is printing, which is the point: a
/// handler has nowhere to be handed one from.
pub struct Console {
    base: usize,
}

impl Console {
    pub const fn new(base: usize) -> Self {
        Console { base }
    }

    fn put(&mut self, byte: u8) {
        let mut spins = 0_u32;
        // SAFETY: LSR is a plain status read on a 16550 with no side effect.
        while unsafe { read_volatile((self.base + LSR) as *const u8) } & LSR_THRE == 0 {
            spins += 1;
            if spins > THRE_SPINS {
                return;
            }
        }
        // SAFETY: THR on a peripheral that is present -- the console this image
        // was linked for.
        unsafe { write_volatile((self.base + RBR_THR) as *mut u8, byte) }
    }
}

impl Write for Console {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        for byte in s.bytes() {
            if byte == b'\n' {
                self.put(b'\r');
            }
            self.put(byte);
        }
        Ok(())
    }
}

// - the receive half, which a handler may use --------------------------------

/// No transmit method, by construction. This is what the interrupt handler in
/// each binary calls, and it is the same split `src/uart.rs` makes with
/// `UartRx`.
pub fn rx_enable(base: usize) {
    // SAFETY: IER on the console this image was linked for. Only ERBFI is set;
    // the transmit and modem bits stay clear.
    unsafe { write_volatile((base + IER) as *mut u8, IER_ERBFI) }
}

/// One byte, or `None`. Reading RBR pops the FIFO, so the caller must have
/// somewhere to put it.
pub fn rx_get(base: usize) -> Option<u8> {
    // SAFETY: both are plain 16550 registers; the LSR read has no side effect
    // and RBR is only read once DR says there is a byte.
    unsafe {
        if read_volatile((base + LSR) as *const u8) & LSR_DR == 0 {
            return None;
        }
        Some(read_volatile((base + RBR_THR) as *const u8))
    }
}

// - what the spikes say ------------------------------------------------------

/// Announce, so a test knows which binary it is talking to and that it booted.
pub fn banner(console: &mut Console, model: &str) {
    let _ = writeln!(console, "\nusb-port {model}: ready");
}

/// One dispatched event: what it was, and what the state machine did with it.
///
/// The line a test asserts on. `writes` is the endpoint stand-in's write count
/// after the event, so a descriptor request is visible as the count moving.
pub fn dispatched(console: &mut Console, record: [u8; 4]) {
    let [code, endpoint, state, writes] = record;
    let name = match code {
        10 => "BusReset",
        12 => "ReceivePacket",
        13 => "SendComplete",
        201 => "ReceiveSetupPacket",
        _ => "Unknown",
    };
    let state = usb::STATE_NAMES
        .get(state as usize)
        .copied()
        .unwrap_or("Unknown");
    let _ = writeln!(
        console,
        "usb: {name}({endpoint}) -> {state} writes {writes}"
    );
}

/// Everything the run produced, in one block a test can assert line by line.
pub fn summary(
    console: &mut Console,
    control: &usb::Control<1024>,
    endpoints: &usb::Endpoints,
    queue: &usb::EventQueue,
    vendor_requests: u32,
) {
    use core::sync::atomic::Ordering;

    let address = match endpoints.address {
        Some(address) => address as i32,
        None => -1,
    };
    let configuration = match control.configuration {
        Some(configuration) => configuration as i32,
        None => -1,
    };
    let _ = writeln!(
        console,
        "usb: dispatched {} state {} address {} configuration {}",
        control.dispatched.load(Ordering::Relaxed),
        usb::STATE_NAMES[control.next.index() as usize],
        address,
        configuration,
    );
    let _ = writeln!(
        console,
        "usb: writes {} bytes {} zlps {} primes {} stalls {} halts {}",
        endpoints.writes,
        endpoints.bytes_written,
        endpoints.zlps,
        endpoints.primes,
        endpoints.stalls,
        endpoints.halts_cleared,
    );
    let _ = writeln!(
        console,
        "usb: vendor {} overflows {} depth {}",
        vendor_requests,
        queue.overflows.load(Ordering::Relaxed),
        queue.depth_max.load(Ordering::Relaxed),
    );
    let trace = &control.trace;
    let _ = writeln!(
        console,
        "usb: trace descriptor {} configuration {} feature {} zlp {} overflow {} length {} state {}",
        trace.unhandled_descriptor.load(Ordering::Relaxed),
        trace.unknown_configuration.load(Ordering::Relaxed),
        trace.unhandled_feature.load(Ordering::Relaxed),
        trace.expected_zlp.load(Ordering::Relaxed),
        trace.receive_overflow.load(Ordering::Relaxed),
        trace.length_mismatch.load(Ordering::Relaxed),
        trace.state_error.load(Ordering::Relaxed),
    );
    let _ = write!(console, "usb: first");
    for byte in &endpoints.first[..endpoints.first_len.min(18)] {
        let _ = write!(console, " {byte:02x}");
    }
    let _ = writeln!(console, " ({} bytes)", endpoints.first_len);
    let _ = write!(console, "usb: last");
    for byte in &endpoints.last[..endpoints.last_len.min(18)] {
        let _ = write!(console, " {byte:02x}");
    }
    let _ = writeln!(console, " ({} bytes)", endpoints.last_len);
    let _ = writeln!(console, "usb: done");
}
