//! One console's line editor, its idle banner, and the round-robin over them.
//!
//! The last of the shell that was still in `main.rs` (#296). `main` now boots
//! the board and hands the consoles to this; it holds no shell state at all.
//!
//! Per-console, not global: `spoken` latching on one port must not silence the
//! re-banner on another, and two half-typed lines must not share a buffer.

use core::fmt::Write;

use crate::clock::{self, Instant};
use crate::uart::Uart;
use crate::{irq, metrics, target, Devices, MAX_CONSOLES};

/// One console's line editor and its idle state.
///
/// Per-console rather than global: `spoken` latching on one port must not silence the
/// re-banner on another, and two half-typed command lines must not share a buffer.
/// `pub` for the reason [`Devices`] is: RTIC's `#[local]` resources land in
/// generated signatures too.
pub struct Shell {
    line: [u8; 64],
    len: usize,
    /// Set by the first keypress. From then on the prompt is on screen and reprinting
    /// the banner would fight the line being edited.
    spoken: bool,
    /// When the banner was last printed. `None` until the first idle poll, which
    /// is what stops the first one measuring against a zero instant -- the same
    /// origin defect the scheduler had.
    ///
    /// A TIMESTAMP, not a turn count. This was `idle: u32`, compared against
    /// 12,000,000 turns and described as "~2 s at 60 MHz". A turn is not a unit
    /// of time: under `--features rtic` `#[idle]` takes two SLIC locks per pass
    /// and each turn costs about four times as much, so the same count took
    /// about eight seconds and the shell looked silent. It also scaled with the
    /// number of consoles and with `SYNC_MHZ`, neither of which has anything to
    /// do with how long a person waits before deciding a board is dead.
    last_banner: Option<Instant>,
}

/// The loop body's console half: one byte from each shell, round-robin.
///
/// Fair by construction and with no arbitration to get wrong: a console that is
/// being pasted into cannot starve the others, because it still only gets one
/// byte per turn.
///
/// Bytes come from the interrupt handler's rings, not from LSR, so the byte is
/// already collected before this asks for it -- a console busy printing cannot
/// miss one. What the caller still decides is how much of one console's input is
/// handled before the other's, which is a fairness property worth keeping.
pub(crate) fn consoles(shells: &mut [Shell; MAX_CONSOLES], devices: &mut Devices) {
    for (index, &base) in target::UART_BASES.iter().enumerate() {
        let mut uart = Uart::new(base);
        shells[index].poll(index, &mut uart, index < target::ANNOUNCING, devices);
    }
}

pub(crate) fn banner(uart: &mut Uart) {
    let _ = write!(uart, "\n");
    crate::log!(uart, "Cynthion RISC-V SoC - Rust firmware");
    crate::log!(uart, "type `help` or `?` for commands");
}

/// What `led`, `i2c` and `sideband` say when there is no board under them.
///
/// The QEMU build has `target::BOARD == None`. Reporting that is better than
/// hiding the commands: `scripts/soc_test.py` then still checks that they are
/// registered and spelled the same as the help text, and a person who typed one
/// on the wrong target gets told which target they are on rather than
/// `unknown command`. See the comment on `target::BOARD`.
pub(crate) fn board_absent(uart: &mut Uart) {
    let _ = writeln!(uart, "no board peripherals on this target");
}

impl Shell {
    pub(crate) const NEW: Shell = Shell {
        line: [0u8; 64],
        len: 0,
        spoken: false,
        last_banner: None,
    };

    /// Handle at most one byte from `uart`, or count one turn of idleness.
    ///
    /// `announce` re-prints the banner and prompt periodically while nothing has been
    /// typed. Printing them once is invisible: the CPU starts the moment the FPGA is
    /// configured and the host takes about half a second to enumerate and bind a tty, so
    /// a terminal attaching afterwards has already missed everything. Worse, an idle
    /// shell that only prints on input is indistinguishable from a dead one -- there is
    /// nothing to see until you type, and no reason to believe typing will work.
    ///
    /// It is off for every console but the first, because on this board the second one's
    /// TX pin is shared with JTAG TMS and an unbidden transmission is bus contention.
    /// See `target::ANNOUNCING`.
    /// `index` selects this console's receive ring in `src/irq.rs`, and is also what
    /// `load` needs to know which port a transfer is arriving on. It is the index into
    /// `target::UART_BASES`, so it is the same number everywhere.
    pub(crate) fn poll(&mut self, index: usize, uart: &mut Uart, announce: bool, devices: &mut Devices) {
        // From the ring the interrupt handler fills, not from LSR. `uart` is still needed
        // for everything this function ECHOES; only the receive direction moved.
        let byte = match irq::pop(index) {
            Some(byte) => byte,
            None => {
                if announce && !self.spoken {
                    let now = clock::now();
                    let last = *self.last_banner.get_or_insert(now);
                    // 2 s, measured. Slow enough to read, fast enough that
                    // attaching does not feel dead -- and now the SAME 2 s under
                    // both dispatchers, at any `SYNC_MHZ`, with any number of
                    // consoles, because it is a duration rather than a count of
                    // something whose cost varies.
                    if last.elapsed(now) >= clock::millis(BANNER_INTERVAL_MS) {
                        self.last_banner = Some(now);
                        // Two lines printed is work, even though nobody asked
                        // for them. See `src/metrics.rs`.
                        metrics::busy();
                        banner(uart);
                        let _ = write!(uart, "> ");
                    }
                }
                return;
            }
        };
        // First keypress: stop re-announcing, the user is here.
        self.spoken = true;

        match byte {
            // Enter. Both, because terminals disagree about which they send.
            b'\r' | b'\n' => {
                let _ = write!(uart, "\n");
                if self.len > 0 {
                    let len = self.len;
                    // Copied out before dispatch so `run` may borrow the uart mutably
                    // while the line it was given stays valid.
                    let mut line = [0u8; 64];
                    line[..len].copy_from_slice(&self.line[..len]);
                    self.len = 0;
                    crate::shell::run(index, uart, &line[..len], devices);
                }
                let _ = write!(uart, "> ");
            }
            // Backspace and delete. Erase on screen as well as in the buffer, or the
            // display and the buffer disagree about what the command is.
            0x08 | 0x7f => {
                if self.len > 0 {
                    self.len -= 1;
                    let _ = write!(uart, "\x08 \x08");
                }
            }
            // Printable ASCII only. Echo, since the device gets raw bytes and nothing
            // else will show what was typed.
            0x20..=0x7e => {
                if self.len < self.line.len() {
                    self.line[self.len] = byte;
                    self.len += 1;
                    uart.put(byte);
                }
            }
            // Everything else -- stray control codes, terminal escape sequences -- is
            // dropped. Echoing or reporting them is worse than silence: an escape
            // sequence would be replayed at the terminal, and a chatty default turns a
            // stuck RX FIFO into an unstoppable wall of text.
            _ => {}
        }
    }
}

/// How long an idle console waits before printing the banner again.
///
/// Two seconds: slow enough to read, fast enough that attaching to a quiet board
/// does not feel like attaching to a dead one. A shell that only prints on input
/// is indistinguishable from one that has stopped -- there is nothing to see
/// until you type, and no reason to believe typing will work.
///
/// Milliseconds, measured against `rdtime`, and NOT a count of loop turns. It
/// was a count, and a turn is not a unit of time: under `--features rtic` a turn
/// costs about four times as much, so the same number took four times as long
/// and the QEMU suite reported the shell as silent.
pub(crate) const BANNER_INTERVAL_MS: u32 = 2_000;

