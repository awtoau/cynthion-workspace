//! The timestamp on a log line, and which lines get one.
//!
//! Six digits of seconds, a point, three of milliseconds, fixed width:
//!
//!     000000.023  hyperram probe ok
//!     000012.481  power target_c 5.156 V 12 mA
//!     000012.532  type-c aux: vbus present, source
//!
//! Fixed width so the lines align in a column and intervals can be read off
//! without arithmetic. The milliseconds come from `src/timer.rs`, which counts
//! them in the 1 ms tick handler.
//!
//! ## What is stamped, and what is not
//!
//! | line                                       | stamped |
//! | ------------------------------------------ | ------- |
//! | a spontaneous report -- power, Type-C, UART | yes     |
//! | a deferred event drained from `events.rs`   | yes     |
//! | the boot banner and the bootloader's report | yes     |
//! | a panic                                     | yes     |
//! | the shell's prompt and its echo of a keypress | no    |
//! | the reply to a typed command                | no      |
//!
//! The rule is whether a human asked for the line. **A log records what
//! happened; a shell reply is half of a conversation the reader is already in**,
//! and a timestamp on it is noise in a column they are trying to scan. It would
//! also destroy the property that makes the column useful: every stamped line
//! is an event, so the gaps between them are the intervals between events.
//!
//! ## The timestamp is captured when an event is PUSHED
//!
//! An interrupt handler may not print (#122, and `src/events.rs`), so what it
//! wants to say goes into a ring that normal context drains. If the ring entry
//! were stamped at drain, **every deferred line would report when it was
//! printed** -- which is exactly wrong for the events most worth timing: a
//! Type-C state change, a console overrun, a fault. Those are the lines a reader
//! is trying to correlate with something else, and the drain time correlates
//! with nothing but the shell's own business.
//!
//! So `events::push` calls [`now`] and stores the result alongside the code and
//! payload, and `events::report` formats [`Stamp`] from the stored value rather
//! than from the clock. It also makes the drop counter more useful: a gap in the
//! column shows where the lost lines were.
//!
//! ## Integer only
//!
//! Division and modulo by 1000, no soft-float. This CPU is `rv32imac`; a `f32`
//! divide would pull in a compiler-builtin routine and put it in a boot path, to
//! render a number that is already an integer number of milliseconds.
//! `src/power.rs` establishes the same approach for volts and milliamps.
//!
//! ## The column is stable before the counter runs
//!
//! Anything printed before `timer::start` -- the banner and everything the
//! bootloader says -- is stamped `000000.000` rather than left unstamped. A
//! column that begins partway down the output is harder to read than one that
//! begins at zero, and a reader who sees the first few lines share a stamp
//! learns something true: they happened before there was a clock to tell them
//! apart.

use core::fmt;

use crate::timer;

/// Seconds the six-digit field can show before it repeats: 11.57 days.
///
/// The counter behind it wraps at 49.7 days (see `MILLIS` in `src/timer.rs`), so
/// this is what wraps first and it is the only wrap a reader will ever meet.
const SECONDS_SPAN: u32 = 1_000_000;

/// A millisecond count, formatted as `ssssss.mmm`.
///
/// Constructed from a stored value by `src/events.rs` and from the clock by
/// [`now`]. Holding the number rather than formatting eagerly is what lets a
/// deferred entry carry the time it was pushed at.
#[derive(Clone, Copy)]
pub struct Stamp(u32);

impl Stamp {
    /// A stamp on a millisecond count taken earlier.
    pub const fn at(millis: u32) -> Stamp {
        Stamp(millis)
    }

    /// The raw millisecond count, for `src/events.rs` to store.
    pub const fn millis(&self) -> u32 {
        self.0
    }
}

impl fmt::Display for Stamp {
    /// Exactly ten characters, always.
    ///
    /// `{:06}` and `{:03}` rather than arithmetic on the digits: the width and
    /// the zero padding are the whole point, and a hand-rolled version is one
    /// more place for a line to come out ragged. The modulo is what keeps the
    /// seconds field six digits after 11.57 days rather than pushing the column
    /// sideways.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{:06}.{:03}",
            (self.0 / 1000) % SECONDS_SPAN,
            self.0 % 1000
        )
    }
}

/// The time now, for a line about to be printed or an event about to be pushed.
pub fn now() -> Stamp {
    Stamp(timer::millis())
}

/// Write one log line, stamped with the time now.
///
/// One `writeln!`, with the caller's arguments nested inside it as
/// `format_args!`, rather than a stamp written separately and then the message.
/// Both were measured on the shipping build and they are within 24 bytes of each
/// other, so this is the one that also makes the line atomic.
///
/// The macro names the rule as much as it saves typing: `log!` means "this is an
/// event", a plain `writeln!` means "this is a reply", and the two are greppable.
#[macro_export]
macro_rules! log {
    ($uart:expr, $($arg:tt)*) => {
        {
            let _ = ::core::writeln!($uart, "{} {}", $crate::log::now(),
                                     ::core::format_args!($($arg)*));
        }
    };
}

/// Write one log line carrying a time captured earlier.
///
/// For `src/events.rs`, which has a stamp from when the record was pushed and
/// must not use the one from when it is being drained.
#[macro_export]
macro_rules! log_at {
    ($uart:expr, $stamp:expr, $($arg:tt)*) => {
        {
            let _ = ::core::writeln!($uart, "{} {}", $stamp,
                                     ::core::format_args!($($arg)*));
        }
    };
}
