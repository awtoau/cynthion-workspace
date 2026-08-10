//! What the board's shell actually receives when a line is typed at it (#347).
//!
//! A real `embedded_cli::Cli`, fed byte by byte, with `shell/rejoin.rs` behind
//! it -- the same two pieces `shell/editor.rs` wires together. The argument
//! grammar under test is the crate's own, so this can answer questions about
//! `-3` that no assertion over hand-built `Arg`s could.

use std::cell::RefCell;
use std::rc::Rc;

use embedded_cli::cli::{CliBuilder, CliHandle};
use embedded_cli::command::RawCommand;
use embedded_cli::service::{Autocomplete, CommandProcessor, Help, ProcessError};
use embedded_cli::writer::Writer;
use embedded_io::{ErrorType, Write};

use cynthion_soc_tests::rejoin::{rejoin, Fault};

/// A sink for the editor's own echo, which nothing here looks at.
struct Discard;

#[derive(Debug)]
struct Never;

impl std::fmt::Display for Never {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("cannot fail")
    }
}

impl std::error::Error for Never {}

impl embedded_io::Error for Never {
    fn kind(&self) -> embedded_io::ErrorKind {
        embedded_io::ErrorKind::Other
    }
}

impl ErrorType for Discard {
    type Error = Never;
}

impl Write for Discard {
    fn write(&mut self, buf: &[u8]) -> Result<usize, Never> {
        Ok(buf.len())
    }
    fn flush(&mut self) -> Result<(), Never> {
        Ok(())
    }
}

/// No command table: this exercises the tokeniser, not completion.
struct Commands;

impl Autocomplete for Commands {
    fn autocomplete(
        _request: embedded_cli::autocomplete::Request<'_>,
        _autocompletion: &mut embedded_cli::autocomplete::Autocompletion<'_>,
    ) {
    }
}

impl Help for Commands {
    fn command_count() -> usize {
        0
    }
    fn list_commands<W: Write<Error = E>, E: embedded_io::Error>(
        _writer: &mut Writer<'_, W, E>,
    ) -> Result<(), E> {
        Ok(())
    }
    fn command_help<
        W: Write<Error = E>,
        E: embedded_io::Error,
        F: FnMut(&mut Writer<'_, W, E>) -> Result<(), E>,
    >(
        _parent: &mut F,
        _command: RawCommand<'_>,
        _writer: &mut Writer<'_, W, E>,
    ) -> Result<(), embedded_cli::service::HelpError<E>> {
        Err(embedded_cli::service::HelpError::UnknownCommand)
    }
}

/// `shell/editor.rs`'s `Dispatch`, with the call to `shell::run` replaced by a
/// recording of the bytes it would have been handed.
struct Record(Rc<RefCell<Option<Result<String, Fault>>>>);

impl CommandProcessor<Discard, Never> for Record {
    fn process<'a>(
        &mut self,
        _cli: &mut CliHandle<'_, Discard, Never>,
        raw: RawCommand<'a>,
    ) -> Result<(), ProcessError<'a, Never>> {
        let mut line = [0u8; 64];
        let outcome = rejoin(raw.name(), raw.args().args(), &mut line)
            .map(|used| std::str::from_utf8(&line[..used]).unwrap().to_owned());
        *self.0.borrow_mut() = Some(outcome);
        Ok(())
    }
}

/// Type `typed`, press Enter, and return what `shell::run` would receive.
fn typed(typed: &str) -> Result<String, Fault> {
    let seen = Rc::new(RefCell::new(None));
    let mut cli = CliBuilder::default()
        .writer(Discard)
        .command_buffer([0u8; 64])
        .history_buffer([0u8; 128])
        .build()
        .unwrap();
    let mut record = Record(seen.clone());
    for byte in typed.bytes().chain(std::iter::once(b'\n')) {
        cli.process_byte::<Commands, _>(byte, &mut record).unwrap();
    }
    let seen = seen.borrow_mut().take();
    seen.expect("the editor never dispatched a command")
}

/// The control: an ordinary line survives unchanged, so a failure below is
/// about the minus sign and not about this harness.
#[test]
fn an_ordinary_line_arrives_as_it_was_typed() {
    assert_eq!(typed("bist cell 3 dif 0").unwrap(), "bist cell 3 dif 0");
    assert_eq!(typed("power").unwrap(), "power");
    // Double spaces collapse. Every command splits on whitespace and drops
    // empties, so this has never mattered.
    assert_eq!(typed("bist   cell  3").unwrap(), "bist cell 3");
}

/// #347, at the layer it actually happens.
///
/// `embedded-cli` reads `-3` as a short option whose character is not
/// alphabetic and reports `Err(NonAsciiShortOption)`, carrying no character. The
/// old rejoin kept only `Arg::Value`, so the token left no trace: the verb saw
/// no count, `unwrap_or(0)` took zero steps, and it printed a fresh state as
/// though the step had happened.
///
/// So `parse_signed` never saw the argument. Nothing was wrong with it for `-3`.
#[test]
fn a_negative_argument_does_not_reach_the_command_and_is_now_refused() {
    assert_eq!(typed("bist phase clkos2 -3"), Err(Fault::Dropped));
    // The other caller in #347. `power limit` takes signed thresholds because
    // the VBUS switch tree is bidirectional.
    assert_eq!(typed("power limit uv aux -100"), Err(Fault::Dropped));
}

/// And the spelling that does work, which the error message names.
#[test]
fn a_double_dash_carries_the_sign_through() {
    assert_eq!(typed("bist phase clkos2 -- -3").unwrap(), "bist phase clkos2 -3");
    assert_eq!(
        typed("power limit uv aux -- -100").unwrap(),
        "power limit uv aux -100"
    );
}

/// A bare `-` is under the crate's two-byte threshold, so it stays a value.
/// Worth pinning: it is the one dash spelling that has always survived.
#[test]
fn a_lone_dash_is_still_a_value() {
    assert_eq!(typed("bist phase -").unwrap(), "bist phase -");
}

/// An alphabetic short option is recoverable, and used to be dropped as
/// silently as `-3`.
#[test]
fn an_alphabetic_option_survives_the_round_trip() {
    assert_eq!(typed("bist -v cell").unwrap(), "bist -v cell");
    assert_eq!(typed("bist --all cell").unwrap(), "bist --all cell");
}
