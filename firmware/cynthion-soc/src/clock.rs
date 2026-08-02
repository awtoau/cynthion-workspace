//! The one thing in this firmware that knows how much time has passed.
//!
//! Everything periodic here -- the power monitor's 50 ms poll, and anything that
//! follows it -- needs an answer to "has it been long enough yet", and until now
//! the only answer available was a loop counter. `Shell::poll` still has one, and
//! its comment says exactly what is wrong with it: *"~2 s at 60 MHz with one
//! console, and proportionally longer with more"*. A count of loop turns measures
//! the loop, not the clock, so it changes whenever the loop does -- adding a
//! console or a command lengthens every interval built on it, silently.
//!
//! ## `rdtime`, and why it is available on both targets
//!
//! RISC-V defines the `time` CSR as a read-only, free-running counter. The SoC's
//! CPU is generated `--with-rdtime` and `ecp5-test/riscv/vexii_cpu.py` drives it
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
