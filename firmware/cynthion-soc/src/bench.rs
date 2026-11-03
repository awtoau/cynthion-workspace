//! `bench` -- how fast each memory is from the CPU, and whether the cache is live.
//!
//! Two regions, measured with `mcycle` and `minstret` around a volatile walk:
//!
//! | region   | where      | cached          | patterns                |
//! |----------|------------|-----------------|---------------------------|
//! | bram     | a static   | main=1, exe=1   | read/write, seq/random   |
//! | flash    | 0x10000000 | main=1, exe=1   | read only, seq/random    |
//!
//! ## The ratio is the measurement
//!
//! - D-cache and I-cache: 4 KiB, 64 sets, 1 way, 64-byte lines. From
//!   `--lsu-l1-sets 64 --lsu-l1-ways 1` in `gateware/soc/cpu/cpu.py`; line
//!   length is the bank memory in the generated core,
//!   `LsuL1Plugin_logic_banks_0_mem`, 1024 words -- 4 KiB over 64 sets is 64
//!   bytes.
//! - Sequential walk: one refill per 16 words. Random walk over a working set
//!   much larger than 4 KiB misses on nearly every access.
//! - **The ratio between them is what proves the cache is doing anything.** A
//!   flat result means either the cache is not working or the region is not
//!   cached.
//! - Each region walked at two sizes: smaller than the cache measures the
//!   cache, larger measures the memory behind it -- the difference is the
//!   evidence.
//!
//! | region   | small | large  | accesses seq / random | random misses |
//! |----------|-------|--------|--------------------------|-----------------|
//! | bram     | 2 KiB | 8 KiB  | 4096 / 4096             | ~50%            |
//! | flash    | 2 KiB | 16 KiB | 4096 / 512              | ~75%            |
//!
//! ## Short on purpose
//!
//! - Whole command ~25 ms. Every comparison here differs by a factor, not a per
//!   cent, so it converges in a few thousand accesses; longer buys precision
//!   nobody uses.
//! - Sizes set by what the cache is -- 64 sets -- rather than a clock: the
//!   small set is half of it, the large sets are 2x to 4x, enough to be
//!   clearly outside.
//! - Each flash miss is a whole 64-byte line over a single-bit SPI bus, so the
//!   random flash walk (512 accesses) is still the longest case, a few ms.
//!   Nothing here is averaged over repeats; a case that moved between runs
//!   would need to say so and spend more, and none does.
//! - Block RAM's large set is 8 KiB rather than tens of KiB because the buffer
//!   is `.bss` inside the 64 KiB the image already lives in; see `RAM_WORDS`.
//!
//! ## What is done about the optimiser
//!
//! - Every access is `read_volatile`/`write_volatile`: none can be folded away,
//!   hoisted out of the loop or merged with its neighbour. Reads accumulate
//!   into a printed checksum, so the load has a use even without the volatile.
//! - `bench` verifies content, not absence of error: block RAM is
//!   filled with a known pattern and read back word for word; flash checked
//!   against the two values `check` uses.
//! - Instructions per access printed beside cycles per access: separates a
//!   stalled bus from slow code, and compares the two patterns honestly -- a
//!   random step is an xorshift, a sequential step is an increment, so the
//!   random loop retires more instructions per access and the column says by
//!   how much.
//!
//! ## What this does not measure
//!
//! - The 1 ms tick and any console interrupt land inside the measured interval:
//!   at worst a few hundred cycles per ms against runs of tens of thousands,
//!   under a per cent everywhere, not corrected for.

use core::fmt::Write;
use core::ptr::{read_volatile, write_volatile};
use core::sync::atomic::AtomicU32;

use crate::memory;
use crate::metrics;
use crate::target;
use crate::uart::Uart;

/// The block RAM under test: 8 KiB, twice the D-cache.
///
/// **Twice, not eight times, and the reason is the region.** Block RAM is 64 KiB
/// total and this image already occupies 40 KiB of it, so `.bss` here comes
/// straight out of the stack -- 16 KiB left 4.4 KiB of stack, which is not a
/// trade worth making for a cleaner ratio. 8 KiB leaves about 12 KiB.
///
/// What that costs the measurement is stated rather than hidden: at twice the
/// cache a random walk finds a resident line about half the time, so the block
/// RAM ratio below is against a 50% miss rate and not against a 100% one. Flash
/// is where the larger miss rate is measured, at four times the cache.
///
/// `AtomicU32` rather than a `static mut`, which is banned crate-wide -- see
/// `scripts/soc/soc_irq_log_check.py`. Nothing here uses the atomic operations; the
/// type is here because it is the one way to get a writable static with a
/// pointer that may legally be written through. All zeroes, so it costs `.bss`
/// and not a byte of the image.
const RAM_WORDS: usize = 2 * 1024;
static RAM: [AtomicU32; RAM_WORDS] = [const { AtomicU32::new(0) }; RAM_WORDS];

/// The two working sets, in 32-bit words.
///
/// 512 words is 2 KiB, half the D-cache, so after one warm-up pass every access
/// hits. 2048 words is 8 KiB; see the note above.
const SMALL_WORDS: usize = 512;
const LARGE_WORDS: usize = RAM_WORDS;

/// The same two sizes for flash. 16 KiB is 256 lines against 64 sets, so a
/// random walk finds a resident line about one time in four and a sequential one
/// one time in sixteen -- and the same span serves both patterns, so the two
/// rows are comparable rather than merely both large. It stays well inside the
/// 4 MiB the part holds, above which the address aliases back onto offset 0 and
/// a read past the end succeeds with offset 0's data.
const FLASH_SMALL_WORDS: usize = 512;
const FLASH_LARGE_WORDS: usize = 4 * 1024;
const _: () = assert!(FLASH_LARGE_WORDS * 4 <= target::FLASH_SIZE);

/// How many accesses each walk makes. See the table in the module comment.
///
/// `FLASH_SEQ_ACCESSES` is exactly `FLASH_LARGE_WORDS`, so the sequential walk
/// covers its span once rather than part of it -- a row labelled 16 KiB that
/// touched 4 KiB would be a lie about the working set.
const RAM_ACCESSES: u32 = 4096;
const FLASH_SEQ_ACCESSES: u32 = FLASH_LARGE_WORDS as u32;
const FLASH_RND_ACCESSES: u32 = 512;

/// What the fill pattern is built from. Odd and with bits in both halves, so a
/// stuck address line or a stuck byte lane shows up as a mismatch rather than as
/// a coincidence.
const PATTERN: u32 = 0x9e37_79b9;

/// The stress walk's stride, in words. 17 words is 68 bytes, past the 64-byte
/// line, and coprime with the 2048-word buffer, so one pass visits every word
/// and nearly every access misses.
const STRESS_STRIDE: usize = 17;
const _: () = assert!(RAM_WORDS.is_power_of_two());

/// The checksum's seed and multiplier. Odd, with bits in both halves, so a
/// dropped bit anywhere in the datapath changes the answer.
const STRESS_SEED: u32 = 0x1234_5678;
const STRESS_MIX: u32 = 0x2545_f491;

/// The answer, computed by the HOST compiler.
///
/// This is what makes `cpu stress` a measurement rather than a benchmark: the
/// board must arrive at a number it did not compute. A score nobody checks says
/// nothing about a CPU whose failure mode is a wrong answer.
pub(crate) const STRESS_GOLDEN: u32 = stress_golden();

const fn stress_golden() -> u32 {
    let mut sum = STRESS_SEED;
    let mut index = 0usize;
    let mut done = 0;
    while done < RAM_WORDS {
        let word = PATTERN.wrapping_mul(index as u32 + 1);
        sum = sum.rotate_left(7) ^ word.wrapping_mul(STRESS_MIX);
        sum = sum.wrapping_add(word);
        index = (index + STRESS_STRIDE) & (RAM_WORDS - 1);
        done += 1;
    }
    sum
}

/// One stress pass: fill the buffer, then stride it folding every word into a
/// checksum. Returns what this CPU made of it, for comparison with
/// [`STRESS_GOLDEN`]. 2048 stores and 2048 loads, nearly all of them misses.
pub(crate) fn stress_checksum() -> u32 {
    let base = ram_base();
    for index in 0..RAM_WORDS {
        // SAFETY: see `ram_base`.
        unsafe { write_volatile(base.add(index), PATTERN.wrapping_mul(index as u32 + 1)) };
    }
    let mut sum = STRESS_SEED;
    let mut index = 0usize;
    for _ in 0..RAM_WORDS {
        // SAFETY: see `ram_base`. Volatile, so no pass folds into the last.
        let word = unsafe { read_volatile(base.add(index)) };
        sum = sum.rotate_left(7) ^ word.wrapping_mul(STRESS_MIX);
        sum = sum.wrapping_add(word);
        index = (index + STRESS_STRIDE) & (RAM_WORDS - 1);
    }
    sum
}

/// One walk's cost.
struct Run {
    cycles: u32,
    instret: u32,
    accesses: u32,
    /// Bytes moved per access.
    width: u32,
}

impl Run {
    /// Cycles per access, in hundredths. Split rather than multiplied up, because
    /// `cycles * 100` overflows a `u32` on the longer runs.
    fn cycles_per_access(&self) -> u32 {
        if self.accesses == 0 {
            return 0;
        }
        (self.cycles / self.accesses) * 100 + (self.cycles % self.accesses) * 100 / self.accesses
    }

    /// Instructions per access, in hundredths.
    fn instr_per_access(&self) -> u32 {
        if self.accesses == 0 {
            return 0;
        }
        (self.instret / self.accesses) * 100 + (self.instret % self.accesses) * 100 / self.accesses
    }

    /// Megabytes per second, in hundredths.
    ///
    /// `MB/s = width * f / cycles_per_access / 1e6`, and `f / 10_000` is 6000 at
    /// 60 MHz, so the whole thing is one 32-bit divide with no term above 2.4
    /// million. `mcycle` counts `sync` cycles and `TIME_HZ` is the `sync`
    /// frequency, so the two are the same clock on this SoC.
    fn mbytes_per_second(&self) -> u32 {
        let per_access = self.cycles_per_access();
        if per_access == 0 {
            return 0;
        }
        (target::TIME_HZ / 10_000) * self.width * 100 / per_access
    }

    /// Instructions per cycle, in thousandths.
    fn ipc(&self) -> u32 {
        let cycles = self.cycles_per_access();
        if cycles == 0 {
            return 0;
        }
        self.instr_per_access() * 1000 / cycles
    }
}

/// The low halves of `mcycle` and `minstret` around `body`.
///
/// `minstret` is read inside the `mcycle` bracket at both ends, so the counter
/// reads themselves are charged to the interval rather than escaping it. Both
/// wrap at 71.6 s on this clock and every walk here is milliseconds, so
/// `wrapping_sub` is exact.
/// Decimal digits, or None. `bench flash` must not read `flash` as a duration.
fn digits(text: &[u8]) -> Option<u32> {
    if text.is_empty() || !text.iter().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let mut n: u32 = 0;
    for b in text {
        n = n.checked_mul(10)?.checked_add((b - b'0') as u32)?;
    }
    Some(n)
}

/// How long each walk runs, in seconds. `bench [region] [seconds]`.
///
/// A DURATION rather than an access count: a fixed count measures flash for
/// milliseconds and block RAM for microseconds, so the slow path gets the least
/// averaging and the fast one wastes time. One second of every path is the same
/// confidence in each.
pub(crate) static SECONDS: core::sync::atomic::AtomicU32 =
    core::sync::atomic::AtomicU32::new(1);

/// `body` repeatedly, until a second (or `SECONDS`) of cycles is spent.
///
/// `accesses` is what ONE pass does; the returned `Run` carries the total, so
/// every derived figure -- cycles per access, MB/s, IPC -- is over the whole
/// interval and needs no scaling.
#[inline(always)]
fn measure_for<F: FnMut() -> u32>(accesses: u32, width: u32, mut body: F) -> (Run, u32) {
    let seconds = SECONDS.load(core::sync::atomic::Ordering::Relaxed);
    let budget = target::TIME_HZ.saturating_mul(seconds);
    let start_cycle = metrics::mcycle();
    let start_instr = metrics::minstret();
    let mut checksum = 0u32;
    let mut done: u32 = 0;
    loop {
        checksum ^= body();
        done = done.saturating_add(accesses);
        if metrics::mcycle().wrapping_sub(start_cycle) >= budget {
            break;
        }
    }
    let end_instr = metrics::minstret();
    let end_cycle = metrics::mcycle();
    (
        Run {
            cycles: end_cycle.wrapping_sub(start_cycle),
            instret: end_instr.wrapping_sub(start_instr),
            accesses: done,
            width,
        },
        checksum,
    )
}

/// The next word index of a walk over `mask + 1` words.
///
/// `random` is always a literal at the call site and this is `inline(always)`, so
/// the branch is folded and each caller gets one specialised loop.
///
///   * random: xorshift32 -- six single-cycle ALU ops, so no multiplier latency
///     to explain away, and a full period, so no index repeats until the working
///     set has been covered many times over.
///   * sequential: an increment, which walks 16 words per 64-byte line.
#[inline(always)]
fn step(state: &mut u32, mask: usize, random: bool) -> usize {
    if random {
        let mut x = *state;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        *state = x;
        (x as usize) & mask
    } else {
        let index = *state as usize & mask;
        *state = state.wrapping_add(1);
        index
    }
}

/// The block RAM buffer as a raw pointer.
///
/// SAFETY of every use below: `AtomicU32` is `repr(transparent)` over
/// `UnsafeCell<u32>`, so this points at `RAM_WORDS` writable words and writing
/// through it is what the interior mutability is for. Every index is masked into
/// the array before use.
fn ram_base() -> *mut u32 {
    RAM.as_ptr() as *mut u32
}

/// Read `accesses` words out of the first `mask + 1` of the buffer.
#[inline(always)]
fn ram_read(mask: usize, accesses: u32, random: bool) -> u32 {
    let base = ram_base();
    let mut state = 1u32;
    let mut sum = 0u32;
    for _ in 0..accesses {
        let index = step(&mut state, mask, random);
        // SAFETY: see `ram_base`. Volatile, so the load is issued rather than
        // hoisted, merged or dropped.
        sum = sum.wrapping_add(unsafe { read_volatile(base.add(index)) });
    }
    sum
}

/// Store `accesses` words into the first `mask + 1` of the buffer.
#[inline(always)]
fn ram_write(mask: usize, accesses: u32, random: bool) -> u32 {
    let base = ram_base();
    let mut state = 1u32;
    for count in 0..accesses {
        let index = step(&mut state, mask, random);
        // SAFETY: see `ram_base`.
        unsafe { write_volatile(base.add(index), count ^ PATTERN) };
    }
    0
}

/// Fill the whole buffer with the pattern, and report how many words read back
/// as something else.
///
/// This is the content check: a walk that never faults proves nothing, and a
/// bus that returns zeroes would otherwise be indistinguishable from a fast one.
fn ram_verify() -> u32 {
    let base = ram_base();
    for index in 0..RAM_WORDS {
        // SAFETY: see `ram_base`.
        unsafe { write_volatile(base.add(index), PATTERN ^ index as u32) };
    }
    let mut bad = 0;
    for index in 0..RAM_WORDS {
        // SAFETY: see `ram_base`.
        if unsafe { read_volatile(base.add(index)) } != PATTERN ^ index as u32 {
            bad += 1;
        }
    }
    bad
}

/// Read `accesses` words out of the first `mask + 1` words of flash.
///
/// `target::flash_word` is the only path to the flash window, for the reason its
/// own comment gives: under QEMU that address is the machine's UART.
#[inline(always)]
fn flash_read(mask: usize, accesses: u32, random: bool) -> u32 {
    let mut state = 1u32;
    let mut sum = 0u32;
    for _ in 0..accesses {
        let index = step(&mut state, mask, random);
        sum = sum.wrapping_add(target::flash_word(index * 4));
    }
    sum
}

/// The CPU's own performance counters, which the core now carries (#173).
///
/// **This is the question a night of gateware probes could not settle**: is a
/// tight loop stalling on instruction fetch? A five-instruction walk is 20 bytes
/// against a 4 KiB I-cache, so after the first iteration it should be entirely
/// hits -- and if it is not, the I-cache is not caching the flash window and that
/// is a defect, not an artefact.
///
/// VexiiRiscv implements `mhpmcounter3..6` with an event selected by writing an
/// id to the matching `mhpmevent`. The ids are its own
/// (`vexiiriscv/misc/Service.scala`):
///
///     0x04  STALLED_CYCLES_FRONTEND   waiting on instruction fetch
///     0x05  STALLED_CYCLES_BACKEND    waiting on data
///     0x11  ICACHE_MISS
///     0x19  DCACHE_LOAD_MISS
///
/// The plugin keeps 8-bit registers and flushes into the 64-bit CSR RAM when the
/// MSB sets, so these are cheap on an FPGA -- but they cost timing: the design
/// stopped closing at 72 MHz when they were added, which is why `SYNC_MHZ` is 60.
///
/// `pub(crate)` since #245: `src/sched.rs` reads the two stall counters for the
/// `rtic` command. It shares this module rather than writing its own selector
/// writes, because two modules pointing the same four counters at different
/// events is a measurement that changes depending on which command ran last.
pub(crate) mod hpm {
    /// Event ids, from VexiiRiscv's own table.
    pub const STALL_FRONTEND: u32 = 0x04;
    pub const STALL_BACKEND: u32 = 0x05;
    pub const DCACHE_LOAD_MISS: u32 = 0x19;
    /// Cycles the D-cache spent WAITING, as opposed to working.
    pub const DCACHE_WAITING: u32 = 0x1A;

    /// Point the four counters at the events worth watching.
    ///
    /// Written once before a walk. `mcountinhibit` is left alone: the counters
    /// free-run and the caller takes a difference, which is the same discipline
    /// the gateware probes use.
    pub fn select() {
        // SAFETY: machine-mode CSR writes on a core generated
        // `--performance-counters 4`, so 3..6 are implemented. Writing an
        // unimplemented one would trap, which is why this does not loop further.
        unsafe {
            core::arch::asm!("csrw 0x323, {0}", in(reg) STALL_FRONTEND,
                             options(nomem, nostack));
            core::arch::asm!("csrw 0x324, {0}", in(reg) STALL_BACKEND,
                             options(nomem, nostack));
            core::arch::asm!("csrw 0x325, {0}", in(reg) DCACHE_WAITING,
                             options(nomem, nostack));
            core::arch::asm!("csrw 0x326, {0}", in(reg) DCACHE_LOAD_MISS,
                             options(nomem, nostack));
        }
    }

    /// The two stall counters, low words: `(frontend, backend)`.
    ///
    /// A LIFETIME reading rather than a difference, which is what makes it
    /// useful to the `rtic` command: the counters free-run from reset and so
    /// does `mcycle`, so the ratio of one to the other is the fraction of the
    /// whole session the CPU spent waiting -- for instruction fetch (0x04) or
    /// for data (0x05). A dispatcher that idles differently moves it.
    ///
    /// Reads only. [`select`] must have run first, or these are counting
    /// whatever the plugin's reset value selects; `crate::sched::init` is what
    /// calls it, once, at boot.
    pub fn stalls() -> (u32, u32) {
        let (frontend, backend): (u32, u32);
        // SAFETY: as `read` below; reads of implemented machine CSRs.
        unsafe {
            core::arch::asm!("csrr {0}, 0xb03", out(reg) frontend, options(nomem, nostack));
            core::arch::asm!("csrr {0}, 0xb04", out(reg) backend, options(nomem, nostack));
        }
        (frontend, backend)
    }
}

/// One row of the table.
fn row(uart: &mut Uart, region: &str, set: &str, pattern: &str, run: &Run) {
    let cycles = run.cycles_per_access();
    let rate = run.mbytes_per_second();
    let instr = run.instr_per_access();
    let ipc = run.ipc();
    let _ = writeln!(
        uart,
        "{:<9}{:>7} {:<10}{:>8}.{:02}{:>8}.{:02}{:>7}.{:02}{:>5}.{:03}",
        region,
        set,
        pattern,
        cycles / 100,
        cycles % 100,
        rate / 100,
        rate % 100,
        instr / 100,
        instr % 100,
        ipc / 1000,
        ipc % 1000,
    );
}

/// `random / sequential` at the large working set.
///
/// The one number that says the cache is live: a line refill amortised over 16
/// words against a line refill per access. **A ratio near 1.00 means either the
/// cache is not working or the region is not cached**.
fn ratio(uart: &mut Uart, region: &str, seq: &Run, rnd: &Run) {
    let sequential = seq.cycles_per_access();
    let random = rnd.cycles_per_access();
    if sequential == 0 {
        return;
    }
    let times = random * 100 / sequential;
    let _ = writeln!(
        uart,
        "{:<9}large set: random is {}.{:02}x SLOWER than sequential",
        region,
        times / 100,
        times % 100
    );
}

/// The column widths are the ones `row` uses, written the same way so the two
/// cannot drift.
fn header(uart: &mut Uart) {
    let _ = writeln!(
        uart,
        "{:<9}{:>7} {:<10}{:>11}{:>11}{:>10}{:>9}",
        "region", "set", "pattern", "cycles/acc", "MB/s", "instr/acc", "ipc"
    );
}

/// `bench bram` -- the baseline everything else is measured against.
fn bram(uart: &mut Uart) {
    // Fill and check before timing anything: the read walks below are only a
    // measurement if the buffer holds what it is supposed to.
    let bad = ram_verify();

    let small = SMALL_WORDS - 1;
    let large = LARGE_WORDS - 1;

    // Warm-up, not timed. One pass over the large set, which is all it takes:
    // the first access after reset finds a cold cache and a cold cache is not
    // the steady state this is reporting. `ram_verify` above has already
    // touched every line, so this is belt and braces and costs 2048 accesses.
    let mut sum = ram_read(large, LARGE_WORDS as u32, false);

    let (run, got) = measure_for(RAM_ACCESSES, 4, || ram_read(small, RAM_ACCESSES, false));
    row(uart, "bram", "2 KiB", "read seq", &run);
    sum ^= got;
    let (run, got) = measure_for(RAM_ACCESSES, 4, || ram_read(small, RAM_ACCESSES, true));
    row(uart, "bram", "2 KiB", "read rnd", &run);
    sum ^= got;
    let (seq, got) = measure_for(RAM_ACCESSES, 4, || ram_read(large, RAM_ACCESSES, false));
    row(uart, "bram", "8 KiB", "read seq", &seq);
    sum ^= got;
    let (rnd, got) = measure_for(RAM_ACCESSES, 4, || ram_read(large, RAM_ACCESSES, true));
    row(uart, "bram", "8 KiB", "read rnd", &rnd);
    sum ^= got;

    let (run, _) = measure_for(RAM_ACCESSES, 4, || ram_write(small, RAM_ACCESSES, false));
    row(uart, "bram", "2 KiB", "write seq", &run);
    let (run, _) = measure_for(RAM_ACCESSES, 4, || ram_write(small, RAM_ACCESSES, true));
    row(uart, "bram", "2 KiB", "write rnd", &run);
    let (run, _) = measure_for(RAM_ACCESSES, 4, || ram_write(large, RAM_ACCESSES, false));
    row(uart, "bram", "8 KiB", "write seq", &run);
    let (run, _) = measure_for(RAM_ACCESSES, 4, || ram_write(large, RAM_ACCESSES, true));
    row(uart, "bram", "8 KiB", "write rnd", &run);

    // Again, because the write walks above have just overwritten the pattern.
    // The second pass is what says the stores landed where they were addressed.
    //
    // `sum` is printed for one reason: without a use, the compiler keeps the
    // volatile load -- it must -- but drops the accumulate, and the read walk
    // stops being a read whose result the pipeline waits for. It came out as
    // `lw zero, 0(a1)` before this line existed.
    let bad = bad + ram_verify();
    ratio(uart, "bram", &seq, &rnd);
    let _ = writeln!(
        uart,
        "bram     {} KiB pattern, {} words wrong, sum {:08x}",
        RAM_WORDS / 256,
        bad,
        sum
    );
}

/// `bench flash` -- memory-mapped, read only, and the region where the cache
/// ratio is largest because a miss is a whole SPI transaction.
fn flash(uart: &mut Uart) {
    let small = FLASH_SMALL_WORDS - 1;
    let large = FLASH_LARGE_WORDS - 1;

    // Warm-up, not timed: 64 words is four line refills, enough that the first
    // timed access is not the first flash transaction since reset.
    let mut sum = flash_read(large, 64, false);

    let (run, got) = measure_for(FLASH_SEQ_ACCESSES, 4, || {
        flash_read(small, FLASH_SEQ_ACCESSES, false)
    });
    row(uart, "flash", "2 KiB", "read seq", &run);
    sum ^= got;
    let (run, got) = measure_for(FLASH_RND_ACCESSES, 4, || {
        flash_read(small, FLASH_RND_ACCESSES, true)
    });
    row(uart, "flash", "2 KiB", "read rnd", &run);
    sum ^= got;
    let (seq, got) = measure_for(FLASH_SEQ_ACCESSES, 4, || {
        flash_read(large, FLASH_SEQ_ACCESSES, false)
    });
    row(uart, "flash", "16 KiB", "read seq", &seq);
    sum ^= got;
    let (rnd, got) = measure_for(FLASH_RND_ACCESSES, 4, || {
        flash_read(large, FLASH_RND_ACCESSES, true)
    });
    row(uart, "flash", "16 KiB", "read rnd", &rnd);
    sum ^= got;
    ratio(uart, "flash", &seq, &rnd);

    // Content, not absence of error: the ECP5 preamble at 0, and a word at 0x40
    // that is neither 00000000 nor ffffffff (a dead window). The word's value is
    // the image's, not a constant. The checksum covers every walk above.
    let (head, mid) = (target::flash_word(0), target::flash_word(0x40));
    let known = head == 0x6150_00ff && mid != 0 && mid != 0xffff_ffff;
    let _ = writeln!(
        uart,
        "flash    reads {} -- @0 {:08x} @40 {:08x}, checksum {:08x}",
        if known { "CORRECT" } else { "WRONG" },
        head,
        mid,
        sum
    );
}

/// `bench`, `bench bram`, `bench flash`.
pub fn command(uart: &mut Uart, rest: &[u8]) {
    // A trailing number is the per-walk duration in seconds. `bench 5`,
    // `bench flash 5`, and the region alone keeps the 1 s default.
    let rest = crate::shell::parse::trim(rest);
    let (rest, seconds) = match rest.iter().rposition(|&b| b == b' ') {
        Some(i) if digits(&rest[i + 1..]).is_some() => {
            (crate::shell::parse::trim(&rest[..i]), digits(&rest[i + 1..]))
        }
        _ => match digits(rest) {
            Some(n) => (&rest[..0], Some(n)),
            None => (rest, None),
        },
    };
    SECONDS.store(seconds.unwrap_or(1).max(1),
                  core::sync::atomic::Ordering::Relaxed);
    let _ = writeln!(
        uart,
        "bench    mcycle at {}; {} s per walk; D-cache 4 KiB = 64 sets x 1 way x 64 B line",
        crate::clock::Hz(target::TIME_HZ),
        SECONDS.load(core::sync::atomic::Ordering::Relaxed)
    );
    // No region is every region, so it is tested before the name is parsed.
    if rest.is_empty() {
        header(uart);
        bram(uart);
        flash(uart);
        return;
    }

    // The name comes from `memory::Region`, which is also what `flash read` and
    // its two siblings parse. Matching byte strings here as well would be a second
    // list of the same three memories, free to drift from the first the moment a
    // region is added or renamed -- and the shell would then accept a spelling for
    // `bench` that `read` rejected.
    match memory::Region::parse(rest) {
        Some(memory::Region::Bram) => {
            header(uart);
            bram(uart);
        }
        Some(memory::Region::Flash) => {
            header(uart);
            flash(uart);
        }
        Some(memory::Region::Hyperram) => {
            let _ = writeln!(uart, "bench: HyperRAM is measured by `hr test`");
        }
        None => {
            let _ = writeln!(uart, "usage: bench [bram|flash]");
        }
    }
}
