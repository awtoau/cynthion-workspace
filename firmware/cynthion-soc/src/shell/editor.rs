//! The line editor: `embedded-cli` 0.2.1 in front of OUR dispatcher (#171).
//!
//! What the crate does here is exactly three things -- TAB completion, history,
//! and the erase/restore that keeps an asynchronous log line from corrupting a
//! half-typed command. What it does NOT do is decide what a command means:
//! [`crate::shell::run`] is still the only dispatcher, it still takes
//! `&mut Devices`, and the crate never sees the command table. See
//! [`Dispatch::process`], which is the whole seam.
//!
//! ## Why the crate is fed one byte at a time
//!
//! `Cli::process_byte` is the entire input API, which is what makes the crate
//! usable at all here: bytes arrive from `irq::pop` and `#[idle]` handles one
//! per console per turn. A crate with a blocking `read_line` would have stalled
//! `events::drain` and starved the other console, and that ruled out most of the
//! field in #171.
//!
//! ## Why the completed line is REASSEMBLED
//!
//! `Cli` hands the processor a `RawCommand`: a name and an `ArgList`. The
//! argument text has already been tokenised in place -- runs of spaces
//! collapsed, quotes stripped, separators overwritten with NUL -- and the crate
//! exposes no way to get the original line back. So this module puts one back
//! together, single-spaced, and hands `shell::run` the byte slice it has always
//! taken.
//!
//! That is lossless for every command this shell has, all of which take
//! whitespace-separated words, and it is why the `Arg::ShortOption` and
//! `Arg::LongOption` arms below write their dashes back rather than being
//! `unreachable!()`: nothing in `HELP` uses a `-flag` today, and a command that
//! grows one must not silently lose it.

use core::convert::Infallible;

use embedded_cli::arguments::Arg;
use embedded_cli::autocomplete::{Autocompletion, Request};
use embedded_cli::cli::{Cli, CliBuilder, CliHandle};
use embedded_cli::command::RawCommand;
use embedded_cli::service::{Autocomplete, CommandProcessor, Help, ProcessError};

use crate::uart::Uart;
use crate::{target, Devices};

/// The longest command line the editor will hold.
///
/// 64, which is what the hand-rolled editor held, so nothing that fitted before
/// stops fitting. The longest thing in `HELP` is
/// `power samples <k> <port> <n>` at 28 characters with its arguments spelled
/// out, so this is better than twice the worst real line.
pub(crate) const LINE: usize = 64;

/// The history ring, in bytes -- not in lines.
///
/// The crate packs entries end to end into one buffer and evicts the oldest when
/// a new one does not fit, so this is "about four typical commands" rather than
/// a fixed depth. 256 B is roughly a dozen `power limit ov 0 5000`s.
///
/// Per console, and there are `MAX_CONSOLES` of them, so this is the term that
/// dominates what the editor costs in RAM: 4 x (64 + 256) = 1,280 B of the 63 KiB
/// block RAM. Measured in `.bss` rather than asserted -- see the report in #171.
pub(crate) const HISTORY: usize = 256;

/// The 16550 behind `embedded_io::Write`.
///
/// A second face on the same peripheral, not a second peripheral: `Uart` is a
/// base address and `put` is a bounded spin on THRE, so a handle made here and a
/// handle made by `consoles()` write to the same FIFO in the order the calls
/// happen. That is what lets `shell::run` keep writing through `core::fmt::Write`
/// while the crate writes through this.
///
/// **No LF-to-CRLF translation here, deliberately**, unlike `core::fmt::Write for
/// Uart`. The crate emits `\r\n` itself (`codes::CRLF`) and translating would
/// turn it into `\r\r\n`.
pub struct Console(Uart);

impl embedded_io::ErrorType for Console {
    /// Transmitting cannot fail: `put` spins until the FIFO takes the byte and
    /// has no error to report. `Infallible` is what makes every `let _ =` on a
    /// `Cli` call below a statement about a `Result` that cannot be `Err`.
    type Error = Infallible;
}

impl embedded_io::Write for Console {
    fn write(&mut self, buf: &[u8]) -> Result<usize, Infallible> {
        for &byte in buf {
            self.0.put(byte);
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> Result<(), Infallible> {
        Ok(())
    }
}

/// One console's editor.
pub type Editor = Cli<Console, Infallible, [u8; LINE], [u8; HISTORY]>;

/// Build one, on the console at `index`.
///
/// **This prints the prompt**, because `Cli::from_builder` does, and there is no
/// way to ask it not to. Every caller therefore has to be somewhere a prompt is
/// wanted -- which on console 1 means "after a key was pressed" and never
/// before. See `target::ANNOUNCING`.
pub(crate) fn build(index: usize) -> Option<Editor> {
    CliBuilder::default()
        .writer(Console(Uart::new(target::UART_BASES[index])))
        .command_buffer([0u8; LINE])
        .history_buffer([0u8; HISTORY])
        .prompt(crate::PROMPT)
        .build()
        .ok()
}

/// What TAB completes against.
///
/// The command NAMES, taken from the first word of each `HELP` entry rather than
/// from a second list. `HELP` is the listing a person reads and the thing
/// `scripts/soc_test.py` asserts, so a name that completes but is not listed --
/// or listed but does not complete -- cannot happen by construction. That drift
/// is exactly what the table in `shell.rs` was introduced to end.
pub(crate) struct Commands;

impl Autocomplete for Commands {
    fn autocomplete(request: Request<'_>, autocompletion: &mut Autocompletion<'_>) {
        let Request::CommandName(name) = request else {
            return;
        };
        for (entry, _) in crate::shell::HELP {
            let candidate = crate::shell::name_of(entry);
            if !candidate.starts_with(name) {
                continue;
            }
            // `get(..)` rather than `candidate[name.len()..]`, and this is worth
            // 2,632 bytes of `.text` -- measured. A `str` range index that can
            // fail links `core::str::slice_error_fail`, which formats its message
            // with `{:?}` on a `char`, which links `<char as Debug>::fmt` at 1,434
            // bytes on its own. Nothing else in this firmware indexes a `str` by
            // range, so this one call site was paying for the whole path.
            //
            // Longest COMMON prefix of everything that matches, which is what
            // `merge_autocompletion` accumulates. Typing `pow` + TAB gives
            // `power` because all six `power ...` entries share it; typing `p` +
            // TAB gives nothing, because `phy`, `pmod`, `ports` and `power` share
            // only the `p` already typed.
            if let Some(rest) = candidate.get(name.len()..) {
                if !rest.is_empty() {
                    autocompletion.merge_autocompletion(rest);
                }
            }
        }
    }
}

/// Nothing, and that is the point.
///
/// The crate's `help` feature is off (see `Cargo.toml`), which leaves this trait
/// without a single method -- the bound on `process_byte` stays, the code behind
/// it does not. `help` is one arm of `shell::run` printing `shell::HELP`.
impl Help for Commands {}

/// The seam: a completed line goes to `shell::run` and nowhere else.
pub(crate) struct Dispatch<'a> {
    pub(crate) index: usize,
    pub(crate) devices: &'a mut Devices,
}

impl<'a> CommandProcessor<Console, Infallible> for Dispatch<'a> {
    fn process<'b>(
        &mut self,
        _cli: &mut CliHandle<'_, Console, Infallible>,
        raw: RawCommand<'b>,
    ) -> Result<(), ProcessError<'b, Infallible>> {
        let mut line = [0u8; LINE];
        let mut len = 0usize;

        // A closure rather than six copies of the bounds check. Truncation is
        // silent because the editor's buffer is the same size as this one: the
        // only way to overflow here is for the tokeniser to have made the line
        // LONGER than what was typed, which it cannot do.
        let mut push = |text: &str| {
            for &byte in text.as_bytes() {
                if len < line.len() {
                    line[len] = byte;
                    len += 1;
                }
            }
        };

        push(raw.name());
        for arg in raw.args().args() {
            push(" ");
            match arg {
                Ok(Arg::Value(value)) => push(value),
                Ok(Arg::LongOption(name)) => {
                    push("--");
                    push(name);
                }
                Ok(Arg::ShortOption(name)) => {
                    push("-");
                    // ASCII by construction: the crate rejects a non-ASCII short
                    // option before it gets here.
                    let mut buffer = [0u8; 4];
                    push(name.encode_utf8(&mut buffer));
                }
                Ok(Arg::DoubleDash) => push("--"),
                // `NonAsciiShortOption`. Dropped rather than guessed at: it is a
                // token this shell has no grammar for, and `shell::run`'s
                // fallthrough will say `unknown command` for the line as a whole.
                Err(_) => {}
            }
        }

        // A second handle on the same 16550 the `Cli` holds. See `Console`.
        let mut uart = Uart::new(target::UART_BASES[self.index]);
        crate::shell::run(self.index, &mut uart, &line[..len], self.devices);
        Ok(())
    }
}
