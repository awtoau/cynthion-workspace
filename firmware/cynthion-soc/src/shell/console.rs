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
use crate::shell::editor::{candidates, editor, family, Commands, Dispatch, Editor};
use crate::{irq, metrics, target, Devices, MAX_CONSOLES};

/// One console's line editor and its idle state.
///
/// Per-console rather than global: `spoken` latching on one port must not silence the
/// re-banner on another, and two half-typed command lines must not share a buffer.
/// `pub` for the reason [`Devices`] is: RTIC's `#[local]` resources land in
/// generated signatures too.
pub struct Shell {
    /// The first word being typed, shadowed.
    ///
    /// `Cli` owns the line and exposes no accessor, and the crate's
    /// `Autocomplete` hook has no writer -- so listing candidates on an
    /// ambiguous TAB needs the prefix tracked here. Only the FIRST word, because
    /// only command names complete.
    ///
    /// It can drift: recalling a line from history replaces the input without a
    /// printable byte passing through here. Cleared on anything that could
    /// desynchronise it, so a stale list is never shown -- TAB after a recall
    /// lists nothing rather than something wrong.
    word: [u8; 16],
    word_len: usize,
    /// The line editor, built on first keypress.
    ///
    /// `Option<Option<..>>`: the outer is "not built yet", the inner is "could
    /// not be built". `Shell::NEW` is a `const` used to fill an array, and a
    /// `Cli` needs a writer and two buffers, so it cannot be made in const
    /// context.
    editor: Option<Option<Editor>>,
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
        word: [0u8; 16],
        word_len: 0,
        editor: None,
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

        // The line editor owns everything from here: echo, backspace, TAB
        // completion over `HELP`, and the history ring on the arrow keys.
        // A completed line reaches `shell::run` through `Dispatch` -- the crate
        // edits, this firmware still dispatches (#171, #303).
        let editor = match self.editor.get_or_insert_with(|| editor(crate::primary_for(index))) {
            Some(editor) => editor,
            // A CLI that could not be built means its buffers could not be
            // written to the console. Nothing useful is left to say on it.
            None => return,
        };
        // Track the first word so an ambiguous TAB can say what it matched.
        match byte {
            b' ' | b'\r' | b'\n' | 0x03 | 0x1b => self.word_len = 0,
            0x08 | 0x7f => {
                self.word_len = self.word_len.saturating_sub(1);
            }
            0x20..=0x7e => {
                if self.word_len < self.word.len() {
                    self.word[self.word_len] = byte;
                    self.word_len += 1;
                }
            }
            _ => {}
        }

        let mut dispatch = Dispatch { index, devices };
        let _ = editor.process_byte::<Commands, _>(byte, &mut dispatch);

        // TAB with more than one match: list them. The crate has already merged
        // the common prefix, so this explains why nothing more was added --
        // otherwise `po` completing to `po` is indistinguishable from a dead key.
        if byte == b'\t' && self.word_len > 0 {
            let typed = core::str::from_utf8(&self.word[..self.word_len]).unwrap_or("");
            let (count, names) = candidates(typed);
            let (kin, rows) = family(typed);
            if count > 1 {
                // Ambiguous prefix: the command names to choose between.
                let _ = editor.write(|writer| {
                    for name in names.iter().take(count.min(names.len())) {
                        writer.write_str(name)?;
                        writer.write_str("  ")?;
                    }
                    Ok(())
                });
            } else if kin > 1 {
                // A complete command that is also a family -- `power`, `flash`,
                // `bench`. One row each, with its argument syntax, because the
                // question at this point is "what can follow it", not "which
                // command did I mean".
                let _ = editor.write(|writer| {
                    for (entry, summary) in rows.iter().take(kin.min(rows.len())) {
                        writer.write_str("\r\n  ")?;
                        writer.write_str(entry)?;
                        for _ in entry.len()..crate::shell::HELP_WIDTH {
                            writer.write_str(" ")?;
                        }
                        writer.write_str(summary)?;
                    }
                    Ok(())
                });
            }
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

/// Which line editor this build carries, for `info`.
///
/// Swapping the editor must move this string with it. A board that reports the
/// editor it does not have is worse than one that reports nothing.
pub(crate) const EDITOR: &str = "embedded-cli 0.2.1";

/// What it responds to, so "TAB does nothing" can be told apart from "TAB never
/// arrived". Every one of these needs a RAW terminal: in canonical mode the tty
/// handles TAB itself and buffers the line until Enter, so none of them reach
/// the board.
pub(crate) const EDITOR_KEYS: &str = "TAB completes, up/down history, raw tty needed";

