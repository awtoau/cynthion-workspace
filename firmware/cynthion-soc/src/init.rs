//! Peripheral bring-up, and the report it makes.
//!
//! Issue #315. **Only a power cycle resets the external chips** -- neither a CPU
//! reset (`jr _reset_vector`) nor an FPGA reconfigure does -- so nothing may
//! assume a part is at its power-on defaults, and firmware is what establishes
//! the state instead.
//!
//! ## The boot report is RETAINED
//!
//! The CPU starts the instant the FPGA is configured and the host takes ~0.5 s
//! to enumerate and bind a tty, so everything printed at boot goes at a console
//! nobody has attached to yet. Only the banner was reprinted on attach
//! (`shell/console.rs`), and it went to console 0 alone.
//!
//! So each line is formatted ONCE into a fixed buffer and pushed at the console
//! that is listening now; [`replay`] flushes the buffer on the first received
//! byte, on whichever console someone actually attached to, alongside the banner
//! that already waits there.
//!
//! `src/events.rs`'s shape, not its code: timestamps captured at push rather
//! than at drain (`src/log.rs`), and a drop counter that reports itself. A
//! second record ring would cost more than a byte buffer and buy nothing -- the
//! records here are already text by the time anyone reads them.

use core::fmt::{self, Write};
use core::sync::atomic::{AtomicUsize, Ordering};

use crate::log;
use crate::uart::Uart;

/// Bytes of boot report kept for a console that attaches late.
///
/// Thirteen lines of stamp, name, verdict and a detail with room to explain a
/// failure. Overflow is COUNTED and reported rather than silently truncating,
/// which is the property `src/events.rs`'s drop counter has.
const RETAINED: usize = 1600;

const REPORT_BYTES: usize = RETAINED;

struct Retained(core::cell::UnsafeCell<[u8; REPORT_BYTES]>);

// SAFETY: one writer. The sequence runs to completion inside `#[init]` with
// interrupts masked, on one core. Readers take a shared slice of the prefix
// `USED` names, which the writer only ever extends.
unsafe impl Sync for Retained {}

static REPORT: Retained = Retained(core::cell::UnsafeCell::new([0; REPORT_BYTES]));
static USED: AtomicUsize = AtomicUsize::new(0);
static LOST: AtomicUsize = AtomicUsize::new(0);

/// Append into the retained buffer, counting what did not fit.
struct Append {
    at: usize,
    limit: usize,
    lost: usize,
}

impl Write for Append {
    fn write_str(&mut self, text: &str) -> fmt::Result {
        // SAFETY: see `Retained`. The borrow ends with this call.
        let buffer = unsafe { &mut *REPORT.0.get() };
        for &byte in text.as_bytes() {
            if self.at >= self.limit {
                self.lost += 1;
                continue;
            }
            buffer[self.at] = byte;
            self.at += 1;
        }
        Ok(())
    }
}

/// One line of the boot report: what came up, and what it came up AS.
///
/// **The detail is read back from what was configured, not restated as a
/// literal.** A line that prints the number the code was written with reports
/// the source, not the machine, and is exactly the kind of claim this project
/// keeps having to withdraw.
///
/// `status` is a short verdict -- `ok`, `ABSENT`, `WARN`, `FAIL` -- and any
/// explanation belongs in `detail`. It comes SECOND because
/// `core::fmt::Arguments` ignores width and padding: a `{:52}` on the detail
/// silently does nothing, while two `&str` fields pad properly and the verdicts
/// form a column that can be scanned for the one that is not `ok`.
///
/// An absent peripheral prints `ABSENT` and stays in the list. A missing line
/// reads as a subsystem nobody thought about; a present one reading `ABSENT`
/// reads as a board without it, which is the truth on the emulator.
pub(crate) fn line(uart: &mut Uart, what: &str, status: &str, detail: fmt::Arguments) {
    // Formatted once, into the buffer, then the same bytes go at whoever is
    // already listening. The stamp is captured HERE rather than at replay, for
    // the reason `src/log.rs` gives: a line drained later must still report
    // when it happened.
    let start = USED.load(Ordering::Relaxed);
    let mut append = Append { at: start, limit: RETAINED, lost: 0 };
    let _ = writeln!(append, "{} init  {:9} {:7} {}", log::now(), what, status, detail);
    USED.store(append.at, Ordering::Relaxed);
    LOST.fetch_add(append.lost, Ordering::Relaxed);
    // SAFETY: see `Retained`; the append above has finished with it.
    let buffer: &[u8; REPORT_BYTES] = unsafe { &*REPORT.0.get() };
    let fresh = &buffer[start..append.at];
    let _ = uart.write_str(core::str::from_utf8(fresh).unwrap_or("?"));
}

/// Print the retained boot report. Called on a console's first keypress, right
/// after the banner it shares that moment with.
pub(crate) fn replay(uart: &mut Uart) {
    let used = USED.load(Ordering::Relaxed);
    if used == 0 {
        return;
    }
    // SAFETY: see `Retained`. Everything below `used` was written at boot and
    // is not written again.
    let buffer: &[u8; REPORT_BYTES] = unsafe { &*REPORT.0.get() };
    let _ = uart.write_str(core::str::from_utf8(&buffer[..used]).unwrap_or("?"));
    let lost = LOST.load(Ordering::Relaxed);
    if lost != 0 {
        let _ = writeln!(uart, "init  report    WARN    {} bytes of it did not fit", lost);
    }
}
