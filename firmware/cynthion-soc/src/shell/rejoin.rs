//! Putting the typed line back together after `embedded-cli` has taken it apart.
//!
//! `embedded-cli` hands a completed line over as a name plus a typed argument
//! list. Every command here owns its own byte grammar (#303), so the pieces are
//! rejoined and re-parsed rather than the grammar being moved into the crate.
//!
//! **The rejoin used to keep only `Arg::Value` and drop everything else.** That
//! is #347: `-3` is not a value to that crate, it is a short option whose
//! character is not alphabetic, so `ArgsIter` yields `Err` and the whole token
//! vanished. `bist phase clkos2 -3` reached the verb with no count at all,
//! `unwrap_or(0)` took zero steps, and the verb printed a fresh state as though
//! it had worked. `power limit <kind> <port> -100` loses its threshold the same
//! way.
//!
//! No `crate::` paths: `firmware/cynthion-soc-tests` includes this file and
//! drives it through a real `Cli` (#337).

use embedded_cli::arguments::{Arg, ArgError};

/// Why a line could not be put back exactly as it was typed.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Fault {
    /// A token like `-3` or `-100`. `embedded-cli` reports it as a short option
    /// with a non-alphabetic character and does not carry the character, so the
    /// text is gone and no amount of care here recovers it. `--` before it makes
    /// the crate treat the rest of the line as values.
    Dropped,
    /// The rejoined line is longer than the buffer. Reachable because collapsed
    /// short options expand -- `-vs` comes back as `-v -s`.
    TooLong,
}

/// Rebuild `name` and its arguments into `line`, and return how many bytes.
///
/// Every variant is put back, not only `Arg::Value`. `Arg::DoubleDash`
/// contributes nothing: its only job was to switch the crate's iterator to
/// values-only, which it has already done by the time this sees it.
pub fn rejoin<'a>(
    name: &str,
    args: impl Iterator<Item = Result<Arg<'a>, ArgError>>,
    line: &mut [u8],
) -> Result<usize, Fault> {
    let mut used = 0usize;

    let mut push = |bytes: &[u8], used: &mut usize| -> Result<(), Fault> {
        if *used + bytes.len() > line.len() {
            return Err(Fault::TooLong);
        }
        line[*used..*used + bytes.len()].copy_from_slice(bytes);
        *used += bytes.len();
        Ok(())
    };

    push(name.as_bytes(), &mut used)?;
    for arg in args {
        match arg {
            Ok(Arg::Value(value)) => {
                push(b" ", &mut used)?;
                push(value.as_bytes(), &mut used)?;
            }
            Ok(Arg::LongOption(option)) => {
                push(b" --", &mut used)?;
                push(option.as_bytes(), &mut used)?;
            }
            Ok(Arg::ShortOption(option)) => {
                push(b" -", &mut used)?;
                let mut utf8 = [0u8; 4];
                push(option.encode_utf8(&mut utf8).as_bytes(), &mut used)?;
            }
            Ok(Arg::DoubleDash) => {}
            Err(ArgError::NonAsciiShortOption) => return Err(Fault::Dropped),
        }
    }
    Ok(used)
}

/// What to print when a line cannot be rebuilt. One line, and it says what to
/// type instead -- a command that silently ran without its argument is what this
/// replaces.
pub fn explain(fault: Fault) -> &'static str {
    match fault {
        Fault::Dropped => "argument dropped: the line editor reads a leading `-` as \
                           an option and does not keep the rest. Put `--` in front \
                           of it, as in `power limit uv aux -- -100`",
        Fault::TooLong => "line too long once the editor's tokens were rejoined",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // `Arg` is not `Clone`, so the array is moved in rather than borrowed.
    fn rebuilt<'a, const N: usize>(
        name: &str,
        args: [Result<Arg<'a>, ArgError>; N],
    ) -> Result<std::string::String, Fault> {
        let mut line = [0u8; 64];
        let used = rejoin(name, args.into_iter(), &mut line)?;
        Ok(std::str::from_utf8(&line[..used]).unwrap().into())
    }

    #[test]
    fn values_come_back_separated_by_single_spaces() {
        assert_eq!(rebuilt("bist", []).unwrap(), "bist");
        assert_eq!(
            rebuilt("bist", [Ok(Arg::Value("cell")), Ok(Arg::Value("3"))]).unwrap(),
            "bist cell 3"
        );
    }

    /// The shell takes no flags, but a typed one must not disappear either -- a
    /// dropped word is the fault this module exists for.
    #[test]
    fn options_are_put_back_with_their_dashes() {
        assert_eq!(
            rebuilt("x", [Ok(Arg::ShortOption('v')), Ok(Arg::LongOption("all"))]).unwrap(),
            "x -v --all"
        );
        // Collapsed short options arrive already separated, so they come back
        // separated. Longer than what was typed, which is why `TooLong` exists.
        assert_eq!(
            rebuilt("x", [Ok(Arg::ShortOption('v')), Ok(Arg::ShortOption('s'))]).unwrap(),
            "x -v -s"
        );
    }

    /// `--` did its work inside the crate before this ran; repeating it here
    /// would put a word into the line that no command's grammar knows.
    #[test]
    fn a_double_dash_leaves_nothing_behind() {
        assert_eq!(
            rebuilt("bist", [Ok(Arg::DoubleDash), Ok(Arg::Value("-3"))]).unwrap(),
            "bist -3"
        );
    }

    /// #347. The old rejoin matched only `Ok(Arg::Value(..))`, so this token was
    /// skipped and the command ran one argument short.
    #[test]
    fn a_token_the_editor_destroyed_is_a_fault_not_a_silent_gap() {
        let args = [
            Ok(Arg::Value("clkos2")),
            Err(ArgError::NonAsciiShortOption),
        ];
        assert_eq!(rebuilt("bist", args), Err(Fault::Dropped));
    }

    #[test]
    fn a_line_that_does_not_fit_is_a_fault_not_a_truncation() {
        let mut line = [0u8; 8];
        let args = [Ok(Arg::Value("0123456789"))];
        assert_eq!(
            rejoin("mem", args.into_iter(), &mut line),
            Err(Fault::TooLong)
        );
        // And the name alone can overrun it.
        assert_eq!(rejoin("a_very_long_command", [].into_iter(), &mut line),
                   Err(Fault::TooLong));
    }

    #[test]
    fn the_explanation_names_the_spelling_that_works() {
        assert!(explain(Fault::Dropped).contains("--"));
    }
}
