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
//! - Rule: whether a human asked for the line. **A log records what happened; a
//!   shell reply is half of a conversation the reader is already in**, and a
//!   timestamp on it is noise in a column they're scanning. It would also
//!   destroy what makes the column useful: every stamped line is an event, so
//!   the gaps between them are the intervals between events.
//!
//! ## The timestamp is captured when an event is PUSHED
//!
//! - An interrupt handler may not print (#122, `src/events.rs`), so what it
//!   wants to say goes into a ring that normal context drains. If the ring
//!   entry were stamped at drain, **every deferred line would report when it
//!   was printed** -- exactly wrong for the events most worth timing: a Type-C
//!   state change, a console overrun, a fault. Those are the lines a reader is
//!   correlating with something else, and drain time correlates with nothing
//!   but the shell's own business.
//! - `events::push` calls [`now`] and stores the result alongside the code and
//!   payload; `events::report` formats [`Stamp`] from the stored value, not
//!   from the clock. Also makes the drop counter more useful: a gap in the
//!   column shows where the lost lines were.
//!
//! ## Integer only
//!
//! - Division and modulo by 1000, no soft-float. This CPU is `rv32imac`; an
//!   `f32` divide would pull in a compiler-builtin routine and put it in a boot
//!   path, to render a number that's already an integer number of
//!   milliseconds. `src/power.rs` does the same for volts and milliamps.
//!
//! ## The column is stable before the counter runs
//!
//! - Anything printed before `timer::start` (banner, bootloader output) is
//!   stamped `000000.000` rather than left unstamped. A column beginning
//!   partway down the output is harder to read than one beginning at zero, and
//!   a reader who sees the first few lines share a stamp learns something
//!   true: they happened before there was a clock to tell them apart.

use core::sync::atomic::{AtomicU32, Ordering};

use crate::timer;

/// The two fixed-width formatters, host-testable because nothing in them reads
/// a clock (#337).
mod format;

pub use format::{Clock, Stamp};

/// The time now, for a line about to be printed or an event about to be pushed.
pub fn now() -> Stamp {
    Stamp::at(timer::millis())
}

/// Wall-clock seconds at boot, or zero when nobody has told the board the time.
///
/// **There is no RTC on this board and this does not invent one.** `time set`
/// writes it, a reset loses it, and the shell says so rather than presenting an
/// uptime as a date.
///
/// Zero is "unset" rather than an `Option<u32>`: the prompt reads this on every
/// Enter, and one relaxed load is the whole cost.
static WALL_AT_BOOT: AtomicU32 = AtomicU32::new(0);

/// Tell the board what time it is, in Unix seconds.
///
/// Stored as the epoch AT BOOT, not the epoch now, so uptime remains the only
/// thing counting and the two cannot drift apart.
pub fn set_wall(epoch: u32) {
    WALL_AT_BOOT.store(epoch.saturating_sub(timer::millis() / 1000), Ordering::Relaxed);
}

/// Unix seconds now, or `None` if nobody has said.
pub fn wall() -> Option<u32> {
    match WALL_AT_BOOT.load(Ordering::Relaxed) {
        0 => None,
        at_boot => Some(at_boot + timer::millis() / 1000),
    }
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
