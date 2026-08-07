//! The one thing in this firmware that knows how much time has passed.
//!
//! Everything periodic here -- the power monitor's 50 ms poll, and anything that follows
//! it -- needs an answer to "has it been long enough yet".
//!
//! A loop counter measures the loop, not the clock: adding a console or a command
//! silently lengthens every interval built on it. `rdtime` does not. `Shell::poll` still
//! counts turns, and its own comment shows the cost -- *"~2 s at 60 MHz with one console,
//! and proportionally longer with more"*.
//!
//! ## `rdtime`, and why it is available on both targets
//!
//! RISC-V defines the `time` CSR as a read-only, free-running counter. The SoC's
//! CPU is generated `--with-rdtime` and `gateware/soc/cpu/cpu.py` drives it
//! from a counter incremented once per `sync` cycle; QEMU's `-M virt` drives it
//! from the CLINT. So this file needs no `#[cfg]` -- only the tick RATE differs,
//! and that is `target::TIME_HZ`, exactly where every other target difference
//! lives.
//!
//! Reading it has no side effect whatsoever, which is the register discipline
//! this project enforces everywhere else and which a time source has no excuse
//! for breaking.
//!
//! ## 32 bits, and wrapping arithmetic
//!
//! Only the low half is read. `timeh` exists on both machines, but a 64-bit read
//! on rv32 is a three-instruction retry loop against a counter that wraps, and
//! nothing here needs to know the absolute time -- only how long ago something
//! was. At 60 MHz the low word wraps every 71.6 seconds, and
//! `Instant::elapsed` uses `wrapping_sub`, which gives the correct interval
//! across a wrap for any interval shorter than that. A 50 ms poll has three
//! orders of magnitude of margin; anything wanting to measure a minute must read
//! the high word too, and this comment is where it should find that out.

use crate::target;

/// A reading of the `time` CSR. Not a wall clock: only differences mean anything.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Instant(u32);

impl Instant {
    /// The counter's own zero, for a `const` initialiser.
    ///
    /// Not "the moment the machine started" -- the counter is free-running and
    /// nothing resets it. It exists because a `static` must be initialised
    /// before `main` runs, where `now()` cannot be called. The one visible
    /// consequence is that the first interval measured from it may be short.
    pub const ZERO: Instant = Instant(0);

    /// An `Instant` at a counter value that came from somewhere other than
    /// [`now`].
    ///
    /// One caller: `src/timer.rs`, which compares the CLINT's `mtimecmp`
    /// against a reading to find out how late a tick was. That is sound because
    /// the CLINT compares against this very counter -- `cpu/clint.py` takes
    /// `mtime` from the CPU's `rdtime` rather than keeping one of its own -- so
    /// a deadline and a reading are values on the same scale.
    ///
    /// Not a way to invent a time. Anything that constructs one out of a number
    /// with a different origin gets an interval that means nothing.
    pub const fn at(ticks: u32) -> Instant {
        Instant(ticks)
    }

    /// Ticks since `self`, correct across one wrap of the counter.
    pub fn elapsed(&self, now: Instant) -> u32 {
        now.0.wrapping_sub(self.0)
    }
}

/// Read the `time` CSR.
pub fn now() -> Instant {
    let ticks: u32;
    // SAFETY: `csrr` from `time` (0xc01) is a read of an architectural counter.
    // It has no side effect, cannot fault in machine mode, and touches no memory,
    // so there is nothing for the compiler to reorder around that would matter.
    unsafe {
        core::arch::asm!("csrr {0}, time", out(reg) ticks, options(nomem, nostack));
    }
    Instant(ticks)
}

/// Ticks in `millis` milliseconds, at this target's counter rate.
///
/// `const` so an interval is a compile-time constant on both targets rather than
/// a division in the poll loop. The multiply is done first and in `u64`, because
/// at 60 MHz `TIME_HZ * 1000` overflows a `u32` and the result would be a poll
/// interval of nonsense -- which presents as a monitor that either never polls or
/// polls flat out.
pub const fn millis(millis: u32) -> u32 {
    ((target::TIME_HZ as u64 * millis as u64) / 1000) as u32
}

/// The other direction: how many milliseconds `ticks` is, on this target.
///
/// For reporting an interval that was measured rather than chosen -- the age of
/// the power monitor's cached sample is the one caller. Truncating rather than
/// rounding, so "0 ms" means "less than a millisecond" and never "about a
/// millisecond"; an age is a claim about staleness and rounding it upward is the
/// wrong direction to be wrong in.
///
/// `TIME_HZ / 1000` is exact on both targets (60_000 and 10_000 ticks per
/// millisecond), so there is no accumulating error to think about. A future
/// clock that is not a whole number of kHz would need this rewritten, and the
/// symptom would be an age that drifts a few per cent -- which is why the
/// division is written this way round and not as `ticks * 1000 / TIME_HZ`, whose
/// symptom would instead be an overflow at 71 ticks.
pub fn to_millis(ticks: u32) -> u32 {
    ticks / (target::TIME_HZ / 1000)
}

/// Ticks as whole microseconds, truncating, for intervals too short for
/// [`to_millis`] to say anything about.
///
/// Scheduler lateness is the caller: the interesting answers there are tens of
/// microseconds, which [`to_millis`] renders as `0` and makes the whole
/// comparison unreadable. Reporting it in raw ticks instead put a bare
/// `25786941` next to a `50 ms` on one line, and two numbers in different units
/// on one line get compared -- so both are now printed.
///
/// `TIME_HZ / 1_000_000` is 60 at 60 MHz but **10 does not divide 1_000_000 into
/// whole ticks on the QEMU target** (10 MHz gives 10 ticks/us, which is fine;
/// the guard is for a future clock below 1 MHz, where the divisor would be zero
/// and this would trap). `max(1)` makes that a wrong number rather than a
/// division by zero, and `to_millis` is the one to use if it ever matters.
pub fn to_micros(ticks: u32) -> u32 {
    ticks / (target::TIME_HZ / 1_000_000).max(1)
}
