//! The line editor: TAB completion and history, from `embedded-cli` (#171).
//!
//! **The crate edits the line; it does not dispatch.** A completed line goes to
//! [`crate::shell::run`], which still takes `&mut Devices` and owns all 37
//! commands. Nothing was ported into the crate's derive macros, so the command
//! set stays one table in `shell.rs` and #303's API boundary is untouched.
//!
//! What the crate supplies:
//!
//! - **TAB completion**, merged to the longest common prefix over [`HELP`]
//! - **history**, an in-place ring, up and down arrows
//! - **`Cli::write`**, which erases the line, prints, then restores the prompt
//!   and whatever was half-typed. That is the fix for an async `events::drain`
//!   line landing in the middle of a command being edited.
//!
//! Per console, not global: two half-typed lines must not share a buffer.

use embedded_cli::cli::{Cli, CliBuilder, CliHandle};
use embedded_cli::command::RawCommand;
use embedded_cli::service::{Autocomplete, CommandProcessor, Help, ProcessError};
use embedded_cli::autocomplete::{Autocompletion, Request};
use embedded_io::{ErrorType, Write as IoWrite};

use crate::shell::{HELP, HELP_WIDTH};
use crate::uart::Uart;

/// Longest command line accepted. The old editor's buffer was 64.
const COMMAND_LEN: usize = 64;

/// History ring, in bytes. Eight typical commands.
const HISTORY_LEN: usize = 256;

/// `Uart` as an `embedded_io::Write`.
///
/// The crate writes through `embedded_io`; this firmware's console implements
/// `core::fmt::Write`. A short adapter rather than changing either.
///
/// OWNS its `Uart` rather than borrowing one. `Cli` holds its writer for its
/// whole life and lives in a `#[local]` resource, while `crate::primary()` hands
/// out a fresh handle per call -- a borrow could not outlive the call. `Uart` is
/// one `usize`, so owning it costs nothing.
pub struct UartIo(pub Uart);

/// The console cannot fail: `Uart::put` waits for THRE and returns nothing.
///
/// `embedded_io::Error` requires `core::error::Error`, which requires `Display`
/// -- so an error type that can never be constructed still needs the ceremony.
#[derive(Debug)]
pub struct Never;

impl core::fmt::Display for Never {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str("the console cannot fail")
    }
}

impl core::error::Error for Never {}

impl embedded_io::Error for Never {
    fn kind(&self) -> embedded_io::ErrorKind {
        embedded_io::ErrorKind::Other
    }
}

impl ErrorType for UartIo {
    type Error = Never;
}

impl IoWrite for UartIo {
    fn write(&mut self, buf: &[u8]) -> Result<usize, Never> {
        for byte in buf {
            self.0.put(*byte);
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> Result<(), Never> {
        Ok(())
    }
}

/// The command set, as the crate sees it: names and summaries from [`HELP`].
///
/// A hand-written impl rather than the derive macros, because the derive path
/// wants every command as an enum variant with typed arguments -- which would
/// move 37 commands' argument parsing into this crate and out of the modules
/// that own them. The table is already the single source of truth; this reads it.
pub struct Commands;

impl Autocomplete for Commands {
    fn autocomplete(
        request: Request<'_>,
        autocompletion: &mut Autocompletion<'_>,
    ) {
        // `Request` is non-exhaustive; only the command-name case is ours to
        // answer. Argument completion would need each command's own vocabulary,
        // which lives in `shell/*` and is #303's boundary to cross, not this
        // module's.
        let Request::CommandName(typed) = request else {
            return;
        };

        // THE SAME MATCHER THE ARGUMENT POSITIONS USE. The crate only ever asks
        // about the first word -- `Request::from_input` returns `None` as soon as
        // the line contains a space -- so this is depth 0 of `complete`, not a
        // rule of its own. Two matchers is how `i2c st` came to list `selftest`.
        let mut names = [""; CANDIDATES];
        let count = crate::shell::complete(typed, &mut names);
        if count == 0 {
            return;
        }

        // AMBIGUOUS COMPLETIONS ARE PARTIAL, and saying so is what stops a space
        // being appended. Without it `po` + TAB left `po ` -- the space ends the
        // word, so typing `wer` next gives command `po` with argument `wer`
        // rather than `power`. `merge_autocompletion` treats a completion as
        // finished unless told otherwise.
        if count > 1 {
            autocompletion.mark_partial();
        }

        let common = crate::shell::shared_prefix(&names[..count.min(names.len())]);
        if common.len() > typed.len() {
            // Only the part not already typed.
            autocompletion.merge_autocompletion(&common[typed.len()..]);
        }
    }
}

impl Help for Commands {
    fn command_count() -> usize {
        HELP.len()
    }

    /// ONE LINE PER FAMILY.
    ///
    /// **The crate calls this, not `shell::help`.** `help` and `?` are handled
    /// inside `embedded-cli`, so the collapsing has to live here -- it was in
    /// `shell::help` first, which is now unreachable for those two words and was
    /// silently doing nothing.
    fn list_commands<W: IoWrite<Error = E>, E: embedded_io::Error>(
        writer: &mut embedded_cli::writer::Writer<'_, W, E>,
    ) -> Result<(), E> {
        let mut index = 0;
        while index < HELP.len() {
            let (entry, mut summary) = HELP[index];
            let name = crate::shell::first_word(entry);

            let mut end = index;
            while end < HELP.len() && crate::shell::first_word(HELP[end].0) == name {
                end += 1;
            }

            // The family's own summary -- the bare row -- not its first
            // subcommand's.
            for (row, text) in &HELP[index..end] {
                if crate::shell::second_word(row).is_empty() {
                    summary = text;
                    break;
                }
            }

            // NAME ALONE in the first column; the subcommands go after the
            // summary. `hyperram` has nine, and putting them beside the name
            // pushed the column out and wrapped the line -- the alignment the
            // column exists for was lost to the thing it was listing.
            writer.write_str(name)?;
            for _ in name.len()..HELP_WIDTH {
                writer.write_str(" ")?;
            }
            writer.write_str(summary)?;

            let mut first = true;
            for (row, _) in &HELP[index..end] {
                let sub = crate::shell::second_word(row);
                if sub.is_empty() {
                    continue;
                }
                writer.write_str(if first { "  [" } else { "|" })?;
                first = false;
                writer.write_str(sub)?;
            }
            if !first {
                writer.write_str("]")?;
            }
            writer.writeln_str("")?;

            index = end;
        }
        Ok(())
    }

    fn command_help<
        W: IoWrite<Error = E>,
        E: embedded_io::Error,
        F: FnMut(&mut embedded_cli::writer::Writer<'_, W, E>) -> Result<(), E>,
    >(
        _parent: &mut F,
        command: RawCommand<'_>,
        writer: &mut embedded_cli::writer::Writer<'_, W, E>,
    ) -> Result<(), embedded_cli::service::HelpError<E>> {
        for (name, summary) in HELP {
            if name.split(' ').next() == Some(command.name()) {
                writer.write_str(name)?;
                writer.write_str("  ")?;
                writer.writeln_str(summary)?;
                return Ok(());
            }
        }
        Err(embedded_cli::service::HelpError::UnknownCommand)
    }
}

/// This console's `Cli`, with its buffers.
pub type Editor = Cli<UartIo, Never, [u8; COMMAND_LEN], [u8; HISTORY_LEN]>;

/// Build one for the console at `base`.
pub fn editor(uart: Uart) -> Option<Editor> {
    CliBuilder::default()
        .writer(UartIo(uart))
        .command_buffer([0u8; COMMAND_LEN])
        .history_buffer([0u8; HISTORY_LEN])
        .prompt("> ")
        .build()
        .ok()
}

/// Forwards a completed line to `shell::run`.
///
/// The crate has already split off the command name; `raw.name()` plus
/// `raw.args()` is what it parsed. This firmware's dispatcher wants the line
/// back as bytes, because every command owns its own argument grammar (#303) --
/// so the two are rejoined here rather than the grammar being moved.
pub struct Dispatch<'a> {
    pub index: usize,
    pub devices: &'a mut crate::Devices,
}

impl CommandProcessor<UartIo, Never> for Dispatch<'_> {
    fn process<'a>(
        &mut self,
        cli: &mut CliHandle<'_, UartIo, Never>,
        raw: RawCommand<'a>,
    ) -> Result<(), ProcessError<'a, Never>> {
        // Rejoined from the crate's tokens. `ArgList` yields typed `Arg`s and
        // exposes no raw tail, so values are put back with single spaces --
        // which is what every command here parses anyway, none of them taking
        // `--flags` or quoted strings.
        fn push(line: &mut [u8], len: &mut usize, bytes: &[u8]) {
            let take = bytes.len().min(line.len() - *len);
            line[*len..*len + take].copy_from_slice(&bytes[..take]);
            *len += take;
        }

        let mut line = [0u8; COMMAND_LEN];
        let mut len = 0usize;
        push(&mut line, &mut len, raw.name().as_bytes());
        for arg in raw.args().args() {
            if let Ok(embedded_cli::arguments::Arg::Value(value)) = arg {
                push(&mut line, &mut len, b" ");
                push(&mut line, &mut len, value.as_bytes());
            }
        }

        // `run` writes through its own `Uart`, not through the CLI's writer, so
        // the two must not interleave. `cli.writer()` is untouched here for that
        // reason: the command owns the screen until it returns.
        let mut uart = crate::primary_for(self.index);
        crate::shell::run(self.index, &mut uart, &line[..len], self.devices);
        let _ = cli;
        Ok(())
    }
}

/// How many completions one TAB can hold.
///
/// The root has the most, one per command family. Sized above that so the count
/// `complete` returns is never the truncated one.
pub const CANDIDATES: usize = 24;
