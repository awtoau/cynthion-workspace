//! The periodic tick, and the millisecond count every log line is stamped from.
//!
//! - A CLINT raises the machine timer interrupt while `mtime >= mtimecmp`. The
//!   handler adds one period to `mtimecmp` and returns -- the whole tick.
//! - Standard rather than bespoke, same reason as `src/plic.rs`: RTIC and Zephyr
//!   both drive a RISC-V tick this way and neither drives a custom timer without
//!   a port being written. QEMU's `-M virt` has a real CLINT, so this file
//!   compiles unchanged for both targets and `scripts/soc_test.py` exercises the
//!   tick that ships. Only `CLINT_BASE` and `TIME_HZ` differ, both in
//!   `src/target.rs`.
//!
//! ## Add the period; never reload from now
//!
//! - `mtimecmp += PERIOD`, not `mtimecmp = mtime + PERIOD`. Reloading from the
//!   current counter adds interrupt latency to every period -- drift proportional
//!   to system load, worst exactly when a timestamp matters. Adding puts
//!   deadlines on an absolute grid: a late handler shortens the next interval
//!   instead of stretching the sequence.
//! - Counter is 64 bits, no wrap to reason about at either target's rate: 9700
//!   years at 60 MHz.
//!
//! ## 1 ms
//!
//! - [`PERIOD_MS`], adjustable. Not 1 us -- a million interrupts a second to gain
//!   resolution a read of the 60 MHz counter already has. `rdtime`
//!   (`src/clock.rs`) measures how long something took; this decides when
//!   something should happen. A millisecond is the finest grain anything in this
//!   firmware schedules on.
//!
//! ## Registers
//!
//! Byte offsets from `target::CLINT_BASE`, the SiFive/QEMU layout:
//!
//! | offset   | name          | access | what               |
//! |----------|---------------|--------|---------------------|
//! | `0x0000` | `msip`        | RW     | hart 0's machine software interrupt |
//! | `0x4000` | `mtimecmp_lo` | RW     | deadline, low half  |
//! | `0x4004` | `mtimecmp_hi` | RW     | high half           |
//! | `0xbff8` | `mtime_lo`    | R      | counter, low half   |
//! | `0xbffc` | `mtime_hi`    | R      | high half           |
//!
//! - **No read here changes any state.** No acknowledge register, nothing to
//!   tabulate; only way to lower the interrupt is to move the deadline, which is
//!   what the handler does.
//!
//! ## Writing a 64-bit deadline from a 32-bit machine
//!
//! - Two stores cannot be atomic, and the intermediate value is briefly a real
//!   deadline the comparator acts on. Raising the low half first can make
//!   `mtimecmp` momentarily smaller than `mtime` and fire an unscheduled
//!   interrupt. [`set_mtimecmp`] uses the standard three-store sequence:
//!
//!   1. `mtimecmp_lo = 0xffffffff` -- "never", whatever the high half is
//!   2. `mtimecmp_hi = new high`
//!   3. `mtimecmp_lo = new low`
//!
//! - Step 1 makes step 2 safe: with the low half at all ones the pair can only
//!   be in the future, so no combination of old high and new high can match.
//!
//! ## What it costs
//!
//! - Handler is bounded and short: two 32-bit loads of `mtimecmp`, a 64-bit add,
//!   the three stores above, two counter increments. Reads the `time` CSR twice
//!   more to record its own duration -- a `csrr` is a register read, not a bus
//!   transaction, so instrumentation is ~2 cycles and stays in the shipping
//!   build.
//! - First unconditional periodic load in the system, so the `time` shell
//!   command reports it: `cost` is the worst handler duration seen, `late` is
//!   the worst gap between a deadline and the handler starting. Both in counter
//!   ticks, both max since boot. A `late` that grows without bound is the
//!   failure that matters -- something holding interrupts off longer than a
//!   period.
//!
//! ## Nothing here may print
//!
//! - [`tick`] is an interrupt handler, so the rule in `src/events.rs` applies:
//!   no `Uart`, no `write!`, no `core::fmt`. Reports through counters this
//!   module exposes, read by normal context. `scripts/soc_irq_log_check.py`
//!   checks this file cannot reach a console -- why timestamp formatting lives
//!   in `src/log.rs`, not here.

use core::sync::atomic::{AtomicU32, Ordering};

use riscv::interrupt::Interrupt;

use crate::clock;
use crate::target;

/// How often the tick fires, and the resolution every timestamp in
/// `src/log.rs` is printed at.
///
/// Adjustable: nothing here assumes 1, and `millis()` counts periods rather
/// than interrupts, so a different value changes the interrupt rate and not the
/// meaning of anything built on it.
pub const PERIOD_MS: u32 = 1;

// msip at 0x0000 is deliberately absent from this list. The peripheral has it
// and a driver can reach it, but nothing in this firmware raises a software
// interrupt at itself, and a constant no code uses is a constant that stops
// matching the hardware without anything failing.
const MTIMECMP_LO: usize = 0x4000;
const MTIMECMP_HI: usize = 0x4004;
const MTIME_LO: usize = 0xbff8;
const MTIME_HI: usize = 0xbffc;

/// A deadline that cannot be reached before the high half is written. See the
/// three-store sequence in the module comment.
const NEVER_LO: u32 = 0xffff_ffff;

/// Milliseconds since [`start`]. The number every timestamp is formatted from.
///
/// 32 bits, not 64, and not because 64 would cost more: **riscv32imac has no
/// 64-bit atomic** (`target_has_atomic` stops at 32), so an `AtomicU64` does not
/// exist on this target and a pair of `AtomicU32`s would need the same
/// read-high, read-low, read-high retry loop [`mtime`] uses -- on a value whose
/// only consumer is a timestamp.
///
/// It is also enough. 32 bits of milliseconds is 49.7 days, and the six-digit
/// seconds field in `src/log.rs` runs out at 11.57 days, so **the printed column
/// wraps four times before the counter behind it does**. Anything wanting a
/// longer absolute time wants `mtime`, which is 64 bits and 9700 years.
static MILLIS: AtomicU32 = AtomicU32::new(0);

/// How many times the handler has run. Distinct from `MILLIS`, which counts
/// PERIOD_MS at a time, and the pair is what shows the tick is running at the
/// rate it claims.
static TICKS: AtomicU32 = AtomicU32::new(0);

/// Worst handler duration, in counter ticks. See "What it costs".
static WORST_COST: AtomicU32 = AtomicU32::new(0);

/// Worst gap between a deadline passing and the handler starting, in counter
/// ticks.
static WORST_LATE: AtomicU32 = AtomicU32::new(0);

/// Has [`start`] run? Until it has, `millis()` is zero and stays zero.
///
/// Read by `src/log.rs`, which prints a stamp of the same width either way
/// rather than a ragged line -- a column that appears partway down the boot
/// output is harder to read than one that starts at zero.
static RUNNING: AtomicU32 = AtomicU32::new(0);

/// One 32-bit CLINT register.
///
/// # Safety
///
/// `offset` must name one of the constants above. The window is a `main=0` PMA
/// region on the SoC and an ordinary MMIO range under QEMU, so a volatile
/// access is exactly one bus transaction of the width requested.
fn read(offset: usize) -> u32 {
    unsafe { core::ptr::read_volatile((target::CLINT_BASE + offset) as *const u32) }
}

fn write(offset: usize, value: u32) {
    unsafe { core::ptr::write_volatile((target::CLINT_BASE + offset) as *mut u32, value) }
}

/// The 64-bit counter, read coherently.
///
/// The standard rv32 sequence: read the high half, the low half, then the high
/// half again, and repeat if it moved. Two separate registers cannot be sampled
/// at one instant, and without this a read taken across the low half's carry
/// pairs a pre-carry high word with a post-carry low word -- an answer 4.3
/// billion ticks in the past, once every 71.6 seconds at 60 MHz.
///
/// It terminates: the loop repeats only when the high half advances, which at
/// either target's rate is once every 71.6 seconds or more.
pub fn mtime() -> u64 {
    loop {
        let high = read(MTIME_HI);
        let low = read(MTIME_LO);
        if read(MTIME_HI) == high {
            return ((high as u64) << 32) | low as u64;
        }
    }
}

/// The current deadline.
///
/// Safe to read non-atomically here because only [`tick`] writes it, and this is
/// called from normal context with the handler unable to run inside a single
/// load. The pair is read low-then-high without a retry loop for the same
/// reason a retry loop would not help: a handler that ran in between changed
/// both halves, and re-reading would simply see the newer pair.
fn mtimecmp() -> u64 {
    let low = read(MTIMECMP_LO);
    let high = read(MTIMECMP_HI);
    ((high as u64) << 32) | low as u64
}

/// Set the deadline, with no window in which a spurious interrupt can fire.
///
/// The three stores are the sequence in the module comment, and the order is
/// the whole of the correctness argument.
fn set_mtimecmp(deadline: u64) {
    write(MTIMECMP_LO, NEVER_LO);
    write(MTIMECMP_HI, (deadline >> 32) as u32);
    write(MTIMECMP_LO, deadline as u32);
}

/// Counter ticks in one period, on this target.
///
/// `clock::millis` is `const` and exact at both rates (60_000 and 10_000 ticks
/// per millisecond), so this is a constant rather than a division anywhere.
const PERIOD: u32 = clock::millis(PERIOD_MS);

/// The machine timer interrupt.
///
/// riscv-rt dispatches here through `_dispatch_core_interrupt`; the symbol is
/// installed by the attribute, and without it this would reach `DefaultHandler`,
/// which aborts.
///
/// The line is a LEVEL -- `mtime >= mtimecmp`, with no acknowledge register --
/// so advancing the deadline is not bookkeeping, it is the only thing that
/// lowers the interrupt. A handler that returned without it would be re-entered
/// before the interrupted code executed one instruction, forever: the same
/// livelock `src/irq.rs` describes for a 16550, arrived at from the other
/// direction.
#[riscv_rt::core_interrupt(Interrupt::MachineTimer)]
fn tick() {
    let entered = clock::now();

    let deadline = mtimecmp();

    // How late the handler was: the deadline is a value on the same counter
    // `entered` was read from -- the CLINT compares against `rdtime` itself, see
    // `cpu/clint.py` -- so the low halves subtract directly. Both were going
    // to be read anyway, so this costs one subtraction.
    //
    // Discarded if it exceeds a period, which means the low half wrapped
    // between the two (71.6 s at 60 MHz) and the difference is noise rather
    // than a measurement. A tick that really was a whole period late shows up
    // as the next one being early, which is the point of adding rather than
    // reloading.
    let late = clock::Instant::at(deadline as u32).elapsed(entered);
    if late < PERIOD {
        WORST_LATE.fetch_max(late, Ordering::Relaxed);
    }

    // ADD, do not reload. See the module comment: this is what keeps the
    // deadlines on an absolute grid rather than drifting by the interrupt
    // latency once per period.
    set_mtimecmp(deadline + PERIOD as u64);

    MILLIS.fetch_add(PERIOD_MS, Ordering::Relaxed);
    TICKS.fetch_add(1, Ordering::Relaxed);

    // The #115 workload's arrival generator. Here because a generator that ran
    // in the main loop would be stopped by the long turn it exists to measure.
    #[cfg(feature = "workload")]
    crate::workload::tick();

    // The PAC1954's REFRESH cycle, released on this grid rather than by the main
    // loop noticing (#245). Here for the same reason the line above is, and it
    // is the whole point of the conversion: a release decided by the loop is a
    // release the loop can be late for, and this one cannot be. It pends a SLIC
    // source -- one MMIO store to `msip` -- and prints nothing, which is the rule
    // this file already lives under.
    crate::rtic_app::tick();

    WORST_COST.fetch_max(entered.elapsed(clock::now()), Ordering::Relaxed);
}

/// Start the tick.
///
/// Call once, from `main`, after `irq::init()` has enabled interrupts globally.
/// The order between this and the PLIC does not matter -- they are different
/// `mie` bits and different handlers -- but the deadline must be programmed
/// BEFORE `mie.MTIE` is set, which is why both happen here and in this order.
/// Neither target's reset value for `mtimecmp` may be relied on: the SoC's is
/// all ones and QEMU's is zero, and one of those fires immediately.
pub fn start() {
    set_mtimecmp(mtime() + PERIOD as u64);
    RUNNING.store(1, Ordering::Release);

    // SAFETY: the handler above is installed at link time, the trap vector was
    // set by riscv-rt's `_setup_interrupts`, the deadline is programmed, and
    // every counter is a `static` initialised before `main` ran. There is no
    // window in which the handler can touch uninitialised state.
    unsafe {
        riscv::interrupt::enable_interrupt(Interrupt::MachineTimer);
    }
}

/// Stop the tick.
///
/// Called before jumping to a loaded payload, alongside `irq::shutdown`, and for
/// the same reason: a timer interrupt taken into a handler belonging to the code
/// you just left is a fault somewhere unrelated with nothing pointing back here.
/// The deadline is pushed out of reach as well as the bit cleared, so a payload
/// that sets `mie.MTIE` for its own timer does not inherit one that is already
/// due.
pub fn shutdown() {
    riscv::interrupt::disable_interrupt(Interrupt::MachineTimer);
    write(MTIMECMP_LO, NEVER_LO);
    write(MTIMECMP_HI, NEVER_LO);
}

/// Milliseconds since [`start`]. Zero until then.
pub fn millis() -> u32 {
    MILLIS.load(Ordering::Relaxed)
}

/// Is the tick running? A stamp printed before it is has nothing behind it.
pub fn running() -> bool {
    RUNNING.load(Ordering::Acquire) != 0
}

/// `(ticks, worst_cost, worst_late)` for the `time` command, all since boot and
/// all in counter ticks except the first.
pub fn stats() -> (u32, u32, u32) {
    (
        TICKS.load(Ordering::Relaxed),
        WORST_COST.load(Ordering::Relaxed),
        WORST_LATE.load(Ordering::Relaxed),
    )
}
