//! `bench` -- how fast each memory is from the CPU, and whether the cache is live.
//!
//! Three regions, measured with `mcycle` and `minstret` around a volatile walk:
//!
//!     region     where         cached          patterns
//!     ---------  ------------  --------------  ---------------------
//!     bram       a static      main=1 exe=1    read/write, seq/random
//!     flash      0x10000000    main=1 exe=1    read only, seq/random
//!     hyperram   CSR port      main=0, never   read/write, seq/random
//!
//! ## The ratio is the measurement
//!
//! The D-cache is 4 KiB: 64 sets, 1 way, 64-byte lines. Sets and ways come from
//! `--lsu-l1-sets 64 --lsu-l1-ways 1` in `ecp5-test/riscv/vexii_cpu.py`; the
//! line length is the bank memory in the generated core,
//! `LsuL1Plugin_logic_banks_0_mem`, which is 1024 words -- 4 KiB over 64 sets is
//! 64 bytes. The I-cache is the same shape.
//!
//! So a sequential walk takes one refill per 16 words and a random walk over a
//! working set much larger than 4 KiB misses on nearly every access. **The ratio
//! between them is what proves the cache is doing anything.** A flat result means
//! either the cache is not working or the region is not cached -- and the
//! HyperRAM rows are the control, because that port is `main=0` and must be flat.
//!
//! Each region is walked at two sizes for the same reason: a working set smaller
//! than 4 KiB measures the cache, a larger one measures the memory behind it, and
//! the difference between the two is the evidence.
//!
//!     region     small    large    accesses seq / random   random misses
//!     ---------  -------  -------  ---------------------   -------------
//!     bram         2 KiB    8 KiB    4096 / 4096            ~50%
//!     flash        2 KiB   16 KiB    4096 /  512            ~75%
//!     hyperram     1 KiB    4 KiB    1024 / 1024            uncached
//!
//! ## Short on purpose
//!
//! The whole command is about 25 ms. Every comparison here is between numbers
//! that differ by a factor, not by a per cent, so it converges in a few thousand
//! accesses and running longer buys precision nobody is going to use. The sizes
//! are set by what the cache is -- 64 sets -- rather than by a clock: the small
//! set is half of it and the large sets are 2x to 4x, which is enough to be
//! clearly outside.
//!
//! Each flash miss is a whole 64-byte line over a single-bit SPI bus, so the
//! random flash walk is 512 accesses and still the longest case at a few
//! milliseconds. Nothing here is averaged over repeats; a case that moved
//! between runs would need to say so and spend more, and none does.
//!
//! Block RAM's large set is 8 KiB rather than tens of KiB because the buffer is
//! `.bss` inside the 64 KiB the image already lives in; see `RAM_WORDS`.
//!
//! ## What is done about the optimiser
//!
//! Every access is `read_volatile` or `write_volatile`, so none of them can be
//! folded away, hoisted out of the loop or merged with its neighbour. Reads are
//! accumulated into a checksum that is printed, so the load has a use even
//! without the volatile. `bench` also verifies content rather than absence of
//! error: block RAM and HyperRAM are filled with a known pattern and read back
//! word for word, and flash is checked against the two values `check` uses.
//!
//! Instructions per access is printed beside cycles per access. That is what
//! separates a stalled bus from slow code, and it is also how the two patterns
//! are compared honestly: a random step is an xorshift, a sequential step is an
//! increment, so the random loop retires more instructions per access and the
//! column says by how much.
//!
//! ## What this does not measure
//!
//! HyperRAM here is the CSR staging port, one 16-bit word per transaction with
//! no FIFO and no burst -- not a memory-mapped region. Its numbers are the port's,
//! not the part's, and no cache can apply to them. Making it a `main=1` region
//! is #90 and is not done.
//!
//! The 1 ms tick and any console interrupt land inside the measured interval.
//! At worst that is a few hundred cycles per millisecond against runs of tens of
//! thousands, so it is under a per cent everywhere and is not corrected for.

use core::fmt::Write;
use core::ptr::{read_volatile, write_volatile};
use core::sync::atomic::AtomicU32;

use crate::hyperram;
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
/// `scripts/soc_irq_log_check.py`. Nothing here uses the atomic operations; the
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

/// HyperRAM, in 16-bit words: 512 is 1 KiB and 2048 is 4 KiB. Uncached, so the
/// size decides nothing and the two rows being equal is the point.
const HYPER_SMALL_WORDS: u32 = 512;
const HYPER_LARGE_WORDS: u32 = 2048;

/// Where in HyperRAM the walk goes: word 0x10000, which is 128 KiB in.
///
/// Above the staging area -- the header is at words 0..16 and an image can reach
/// word 32272 -- so benchmarking never destroys something waiting to boot. The
/// part is 8 MiB, so this and the 4 KiB above it are inside it.
const HYPER_BASE: u32 = 0x0001_0000;
const _: () = assert!(HYPER_BASE > hyperram::IMAGE_WORD + hyperram::MAX_IMAGE / 2);

/// How many accesses each walk makes. See the table in the module comment.
///
/// `FLASH_SEQ_ACCESSES` is exactly `FLASH_LARGE_WORDS`, so the sequential walk
/// covers its span once rather than part of it -- a row labelled 16 KiB that
/// touched 4 KiB would be a lie about the working set.
const RAM_ACCESSES: u32 = 4096;
const FLASH_SEQ_ACCESSES: u32 = FLASH_LARGE_WORDS as u32;
const FLASH_RND_ACCESSES: u32 = 512;
const HYPER_ACCESSES: u32 = 1024;

/// What the fill pattern is built from. Odd and with bits in both halves, so a
/// stuck address line or a stuck byte lane shows up as a mismatch rather than as
/// a coincidence.
const PATTERN: u32 = 0x9e37_79b9;

/// One walk's cost.
struct Run {
    cycles: u32,
    instret: u32,
    accesses: u32,
    /// Bytes moved per access: 4 for the two 32-bit regions, 2 for HyperRAM.
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
#[inline(always)]
fn measure<F: FnOnce() -> u32>(accesses: u32, width: u32, body: F) -> (Run, u32) {
    let start_cycle = metrics::mcycle();
    let start_instr = metrics::minstret();
    let checksum = body();
    let end_instr = metrics::minstret();
    let end_cycle = metrics::mcycle();
    (
        Run {
            cycles: end_cycle.wrapping_sub(start_cycle),
            instret: end_instr.wrapping_sub(start_instr),
            accesses,
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



/// The HyperRAM transaction counters (#173), read around one walk.
///
/// **This is the measurement that five readings of the source could not make.**
/// Every readable thing says a cache-line refill should reach the part as one
/// burst -- the CPU emits `CTI=INCR_BURST`/`BTE=LINEAR`, the window tests for
/// exactly that, the decoder and arbiter carry the features, the burst cap is 374
/// beats against the 16 a line needs, and the simulation passes 56 checks
/// including "one CS# assertion per line". The board disagrees, at 336 CK where a
/// coalesced burst is 51.
///
/// So count it instead of reading it again. `beats / starts` is the whole answer:
/// 16 means every beat is its own HyperBus transaction, 1 means the line is one
/// burst and the time is going somewhere else entirely.
mod probe {
    use cynthion_soc_pac::base::HYPERRAM_PROBE as BASE;

    /// Byte offsets, from the generated map.
    const STARTS: usize = 0x00;
    const BEATS: usize = 0x02;
    const BURST_BEATS: usize = 0x04;
    const MAX_RUN: usize = 0x06;
    const WORDS: usize = 0x08;
    const BUSY: usize = 0x0c;
    const WANT: usize = 0x10;
    const ARMING: usize = 0x14;
    const CYC: usize = 0x18;
    const CLEAR: usize = 0x1c;

    /// Two byte reads, low first.
    ///
    /// These sit behind an `amaranth_soc` multiplexer with granularity 8, where
    /// reading the LOWEST address latches a shadow of the whole register and the
    /// rest come from that shadow. A 16-bit access would be a different bus
    /// transaction from the two ordered byte accesses the hardware specifies --
    /// the same reason `gpio.rs` writes Mode's low byte then its high byte.
    fn read16(offset: usize) -> u32 {
        // SAFETY: two bytes inside the generated HYPERRAM_PROBE window, an
        // uncached `main=0` region. Read-only counters; volatile because it is a
        // device.
        unsafe {
            let reg = (BASE + offset) as *const u8;
            (core::ptr::read_volatile(reg) as u32)
                | (core::ptr::read_volatile(reg.add(1)) as u32) << 8
        }
    }

    /// Zero every counter, so what follows is measured over one walk rather than
    /// since reset. A total cannot separate "this walk did nothing" from "this
    /// walk did nothing but an earlier one did".
    pub fn clear() {
        // SAFETY: as above; a write-only strobe field.
        unsafe { core::ptr::write_volatile((BASE + CLEAR) as *mut u8, 1) };
    }

    /// Four bytes, low first, for the same reason as `read16`.
    fn read32(offset: usize) -> u32 {
        // SAFETY: four bytes inside the generated window; read-only counters.
        unsafe {
            let reg = (BASE + offset) as *const u8;
            (core::ptr::read_volatile(reg) as u32)
                | (core::ptr::read_volatile(reg.add(1)) as u32) << 8
                | (core::ptr::read_volatile(reg.add(2)) as u32) << 16
                | (core::ptr::read_volatile(reg.add(3)) as u32) << 24
        }
    }

    /// `(starts, beats, busy, want, arming)`.
    ///
    /// `words` and `burst_beats` are dropped from the readout: `read_ready` is a
    /// LEVEL held across a whole transaction rather than a per-word pulse, so
    /// counting it -- by level or by edge -- measures the transaction, which
    /// `starts` already reports. An instrument that cannot be interpreted is
    /// worse than one that is absent.
    pub fn read() -> (u32, u32, u32, u32, u32, u32) {
        (read16(STARTS), read16(BEATS), read32(BUSY), read32(WANT),
         read32(ARMING), read32(CYC))
    }
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
mod hpm {
    /// Event ids, from VexiiRiscv's own table.
    pub const STALL_FRONTEND: u32 = 0x04;
    pub const STALL_BACKEND: u32 = 0x05;
    pub const ICACHE_MISS: u32 = 0x11;
    pub const DCACHE_LOAD_MISS: u32 = 0x19;

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
            core::arch::asm!("csrw 0x325, {0}", in(reg) ICACHE_MISS,
                             options(nomem, nostack));
            core::arch::asm!("csrw 0x326, {0}", in(reg) DCACHE_LOAD_MISS,
                             options(nomem, nostack));
        }
    }

    /// The four counters, low words. A walk is far under 2^32 of anything.
    pub fn read() -> (u32, u32, u32, u32) {
        let (a, b, c, d): (u32, u32, u32, u32);
        // SAFETY: as above; reads of implemented machine CSRs.
        unsafe {
            core::arch::asm!("csrr {0}, 0xb03", out(reg) a, options(nomem, nostack));
            core::arch::asm!("csrr {0}, 0xb04", out(reg) b, options(nomem, nostack));
            core::arch::asm!("csrr {0}, 0xb05", out(reg) c, options(nomem, nostack));
            core::arch::asm!("csrr {0}, 0xb06", out(reg) d, options(nomem, nostack));
        }
        (a, b, c, d)
    }
}

/// **These walks are fetched from flash, and that bounds what they can report.**
///
/// `.text` moved to flash in #162 stage two, so a walk's own loop -- about five
/// instructions per access -- comes from a part doing 11.27 MB/s. Measuring
/// HyperRAM this way gave 13.3 MB/s while the bus was doing 63.1: the CPU spent
/// 79% of every cache line stalled on instruction fetch rather than on the memory
/// under test.
///
/// Putting the loop in block RAM via `.data.*` was tried and backed out: it works
/// -- riscv-rt copies `.data` from flash to RAM at startup and block RAM is
/// `exe=1` -- but it moves `.init.rust` to a RAM load address with no loader
/// behind it now that the firmware does not travel in the bitstream.
///
/// The counters supersede it. `STALLED_CYCLES_FRONTEND` reports the fetch stall
/// directly, so the artefact can be subtracted rather than engineered around.
/// Read `accesses` 32-bit words from the HyperRAM MEMORY WINDOW.
///
/// The other HyperRAM walk below goes through the CSR staging port, one register
/// write and one poll per 16-bit word, uncached. This one is an ordinary load
/// from `0x20000000` -- a `main=1 exe=1` PMA region backed by
/// `bootram.mmap.bus`, so the D-cache serves hits and a miss is one burst.
///
/// **The two together are the measurement.** The gateware has mapped this window
/// for some time and no firmware used it (#90), so nothing had ever compared the
/// two paths on this board. The ratio says what the staging port costs and
/// whether the burst coalescing in the controller reaches the CPU.
///
/// Volatile, so the compiler cannot hoist or elide the walk; the pattern that
/// decides cache behaviour is `step`, exactly as for flash.
#[inline(always)]
fn hyper_window_read(mask: usize, accesses: u32, random: bool) -> u32 {
    let mut state = 1u32;
    let mut sum = 0u32;
    for _ in 0..accesses {
        let index = step(&mut state, mask, random);
        // SAFETY: inside the 8 MiB window the SoC decodes at HYPERRAM, which is
        // `main=1` memory rather than a device. Volatile anyway, because the
        // point is to measure the access rather than to let it be optimised away.
        let word = unsafe {
            core::ptr::read_volatile(
                (cynthion_soc_pac::base::HYPERRAM + index * 4) as *const u32)
        };
        sum = sum.wrapping_add(word);
    }
    sum
}

/// Read `accesses` 16-bit words from the HyperRAM staging port.
///
/// Sequential rides the port's address auto-increment, so it is one `ctrl` write
/// and one poll per word. Random pays a `seek` as well, and the controller starts
/// a fresh transaction rather than continuing from where it was.
#[inline(always)]
fn hyper_read(mask: u32, accesses: u32, random: bool) -> u32 {
    let mut state = 1u32;
    let mut sum = 0u32;
    for count in 0..accesses {
        if random {
            let index = step(&mut state, mask as usize, true) as u32;
            hyperram::seek_word(HYPER_BASE + index);
        } else if count & mask == 0 {
            // Back to the start of the working set, which is also the initial
            // seek. The port auto-increments, so this is the only address
            // bookkeeping a sequential walk needs.
            hyperram::seek_word(HYPER_BASE);
        }
        sum = sum.wrapping_add(hyperram::read_word() as u32);
    }
    sum
}

/// Store `accesses` 16-bit words into the HyperRAM bench area.
#[inline(always)]
fn hyper_write(mask: u32, accesses: u32, random: bool) -> u32 {
    let mut state = 1u32;
    for count in 0..accesses {
        if random {
            let index = step(&mut state, mask as usize, true) as u32;
            hyperram::seek_word(HYPER_BASE + index);
        } else if count & mask == 0 {
            hyperram::seek_word(HYPER_BASE);
        }
        hyperram::write_word(count as u16);
    }
    0
}

/// Fill the HyperRAM bench area with a pattern and count the words that do not
/// read back.
///
/// Never touches words 0..`IMAGE_WORD`, so a staged image waiting to boot
/// survives a `bench`.
fn hyper_verify() -> u32 {
    hyperram::seek_word(HYPER_BASE);
    for index in 0..HYPER_LARGE_WORDS {
        hyperram::write_word((index ^ PATTERN) as u16);
    }
    hyperram::seek_word(HYPER_BASE);
    let mut bad = 0;
    for index in 0..HYPER_LARGE_WORDS {
        if hyperram::read_word() != (index ^ PATTERN) as u16 {
            bad += 1;
        }
    }
    bad
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
/// cache is not working or the region is not cached** -- which is why the
/// HyperRAM row prints one too, as the control that must be flat.
fn ratio(uart: &mut Uart, region: &str, seq: &Run, rnd: &Run) {
    let sequential = seq.cycles_per_access();
    let random = rnd.cycles_per_access();
    if sequential == 0 {
        return;
    }
    let times = random * 100 / sequential;
    let _ = writeln!(
        uart,
        "{:<9}random / sequential, large set: {}.{:02}x",
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

    let (run, got) = measure(RAM_ACCESSES, 4, || ram_read(small, RAM_ACCESSES, false));
    row(uart, "bram", "2 KiB", "read seq", &run);
    sum ^= got;
    let (run, got) = measure(RAM_ACCESSES, 4, || ram_read(small, RAM_ACCESSES, true));
    row(uart, "bram", "2 KiB", "read rnd", &run);
    sum ^= got;
    let (seq, got) = measure(RAM_ACCESSES, 4, || ram_read(large, RAM_ACCESSES, false));
    row(uart, "bram", "8 KiB", "read seq", &seq);
    sum ^= got;
    let (rnd, got) = measure(RAM_ACCESSES, 4, || ram_read(large, RAM_ACCESSES, true));
    row(uart, "bram", "8 KiB", "read rnd", &rnd);
    sum ^= got;

    let (run, _) = measure(RAM_ACCESSES, 4, || ram_write(small, RAM_ACCESSES, false));
    row(uart, "bram", "2 KiB", "write seq", &run);
    let (run, _) = measure(RAM_ACCESSES, 4, || ram_write(small, RAM_ACCESSES, true));
    row(uart, "bram", "2 KiB", "write rnd", &run);
    let (run, _) = measure(RAM_ACCESSES, 4, || ram_write(large, RAM_ACCESSES, false));
    row(uart, "bram", "8 KiB", "write seq", &run);
    let (run, _) = measure(RAM_ACCESSES, 4, || ram_write(large, RAM_ACCESSES, true));
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

    let (run, got) = measure(FLASH_SEQ_ACCESSES, 4, || {
        flash_read(small, FLASH_SEQ_ACCESSES, false)
    });
    row(uart, "flash", "2 KiB", "read seq", &run);
    sum ^= got;
    let (run, got) = measure(FLASH_RND_ACCESSES, 4, || {
        flash_read(small, FLASH_RND_ACCESSES, true)
    });
    row(uart, "flash", "2 KiB", "read rnd", &run);
    sum ^= got;
    let (seq, got) = measure(FLASH_SEQ_ACCESSES, 4, || {
        flash_read(large, FLASH_SEQ_ACCESSES, false)
    });
    row(uart, "flash", "16 KiB", "read seq", &seq);
    sum ^= got;
    let (rnd, got) = measure(FLASH_RND_ACCESSES, 4, || {
        flash_read(large, FLASH_RND_ACCESSES, true)
    });
    row(uart, "flash", "16 KiB", "read rnd", &rnd);
    sum ^= got;
    ratio(uart, "flash", &seq, &rnd);

    // Content, not absence of error. The two constants are the ones `check`
    // uses; the checksum is over every walk above, and a window that is dead
    // rather than slow returns 00000000 or ffffffff for all of it.
    let known = target::flash_word(0) == 0x6150_00ff && target::flash_word(0x40) == 0x2a55_8800;
    let _ = writeln!(
        uart,
        "flash    @0 {:08x} @40 {:08x} {}  sum {:08x}",
        target::flash_word(0),
        target::flash_word(0x40),
        if known { "ok" } else { "BAD" },
        sum
    );
}

/// Does the staging port answer at all?
///
/// One word out and back. Worth its own call because every spin in
/// `hyperram::read_word` is bounded at 100_000 iterations rather than being
/// unbounded, and a dead port would therefore make the walks below take about a
/// minute rather than fail -- which reads as a hung shell. See the same bound's
/// comment in `src/hyperram.rs`, which exists for the same reason.
///
/// `pub` for `src/memory.rs`: `hyperram read` needs the same distinction between
/// "the port is dead" and "these words really are 0xffff", and one probe both
/// commands share cannot answer that question two ways. It writes, so that module
/// calls it only when a read came back ambiguous.
pub fn hyper_present() -> bool {
    hyperram::seek_word(HYPER_BASE);
    hyperram::write_word(PATTERN as u16);
    hyperram::seek_word(HYPER_BASE);
    hyperram::read_word() == PATTERN as u16
}

/// `bench hyperram` -- the CSR staging port, which is not a memory-mapped region.
fn hyper(uart: &mut Uart) {
    if !hyper_present() {
        let _ = writeln!(uart, "hyperram did not answer; not walking it");
        return;
    }

    let small = HYPER_SMALL_WORDS - 1;
    let large = HYPER_LARGE_WORDS - 1;

    // THE MEMORY WINDOW FIRST, because it is the path that matters and the one
    // nothing had ever exercised. 2 KiB and 16 KiB match the flash rows so the
    // three regions are read off the same axes.
    {
        let small_words = (FLASH_SMALL_WORDS - 1) as usize;
        let large_words = (FLASH_LARGE_WORDS - 1) as usize;
        let mut sum = hyper_window_read(large_words, 64, false);
        let (run, got) = measure(FLASH_SEQ_ACCESSES, 4, || {
            hyper_window_read(small_words, FLASH_SEQ_ACCESSES, false)
        });
        row(uart, "hyper win", "2 KiB", "read seq", &run);
        sum ^= got;
        probe::clear();
        hpm::select();
        let before = hpm::read();
        let (seq, got) = measure(FLASH_SEQ_ACCESSES, 4, || {
            hyper_window_read(large_words, FLASH_SEQ_ACCESSES, false)
        });
        let after = hpm::read();
        let (starts, beats, busy, want, arming, cyc) = probe::read();
        row(uart, "hyper win", "16 KiB", "read seq", &seq);
        sum ^= got;

        // The counters, over exactly the walk above.
        //
        // `beats / starts` is the coalescing: a 64-byte line is 16 32-bit beats,
        // so 16.00 means ONE HyperBus transaction per line and 1.00 would mean one
        // per beat. Measured 16.00, with every beat flagged `burst` and a longest
        // run of 16 -- the coalescing works, which is the opposite of what #173
        // assumed from the timing alone.
        let _ = writeln!(
            uart,
            "hyper win  transactions {}  beats {}  ({} beats per transaction, \
             16 is one per cache line)",
            starts, beats, if starts > 0 { beats / starts } else { 0 });
        // WHERE THE TIME GOES, per 64-byte cache line.
        //
        // The controller is busy for a fraction of a line and idle for the rest;
        // `want` and `arming` attribute that idle time to this SoC's own
        // plumbing rather than leaving it inferred. `want` is the window asking
        // while the owner FSM sits in IDLE -- the round trip it takes per
        // transaction. `arming` is the fixed handshake after `start` before the
        // bus moves.
        if starts > 0 {
            let _ = writeln!(
                uart,
                "hyper win  per line: cyc {}  busy {}  want {}  arming {}  (cycles)",
                cyc / starts, busy / starts, want / starts, arming / starts);

            // THE CPU'S OWN ACCOUNT, over the same walk. `front` is cycles
            // stalled on instruction fetch: a five-instruction loop in a 4 KiB
            // I-cache should show almost none, and anything else means the
            // I-cache is not holding the flash window.
            let front = after.0.wrapping_sub(before.0);
            let back = after.1.wrapping_sub(before.1);
            let imiss = after.2.wrapping_sub(before.2);
            let dmiss = after.3.wrapping_sub(before.3);
            let _ = writeln!(
                uart,
                "hyper win  per line: front-stall {}  back-stall {}  \
                 icache-miss {}  dcache-miss {}",
                front / starts, back / starts, imiss / starts, dmiss / starts);
        }
        if starts > 0 {
            let per = (beats * 100) / starts;
            let _ = writeln!(
                uart,
                "hyper win  {}.{:02} beats per transaction -- 16.00 is one \
                 transaction per 64-byte cache line, 1.00 is one per beat",
                per / 100, per % 100);
        }
        let (rnd, got) = measure(FLASH_RND_ACCESSES, 4, || {
            hyper_window_read(large_words, FLASH_RND_ACCESSES, true)
        });
        row(uart, "hyper win", "16 KiB", "read rnd", &rnd);
        sum ^= got;
        ratio(uart, "hyper win", &seq, &rnd);
        let _ = sum;
    }

    // Warm-up, not timed. Nothing to warm -- the port is uncached -- but the
    // first transaction after a seek is the one that pays for the controller
    // waking up, and that is not what the rows below are for.
    let mut sum = hyper_read(large, 16, false);

    let (run, got) = measure(HYPER_ACCESSES, 2, || {
        hyper_read(small, HYPER_ACCESSES, false)
    });
    row(uart, "hyperram", "1 KiB", "read seq", &run);
    sum ^= got;
    let (rnd, got) = measure(HYPER_ACCESSES, 2, || {
        hyper_read(large, HYPER_ACCESSES, true)
    });
    row(uart, "hyperram", "4 KiB", "read rnd", &rnd);
    sum ^= got;
    let (seq, got) = measure(HYPER_ACCESSES, 2, || {
        hyper_read(large, HYPER_ACCESSES, false)
    });
    row(uart, "hyperram", "4 KiB", "read seq", &seq);
    sum ^= got;
    let (run, _) = measure(HYPER_ACCESSES, 2, || {
        hyper_write(small, HYPER_ACCESSES, false)
    });
    row(uart, "hyperram", "1 KiB", "write seq", &run);
    let (run, _) = measure(HYPER_ACCESSES, 2, || {
        hyper_write(large, HYPER_ACCESSES, false)
    });
    row(uart, "hyperram", "4 KiB", "write seq", &run);
    let (run, _) = measure(HYPER_ACCESSES, 2, || {
        hyper_write(large, HYPER_ACCESSES, true)
    });
    row(uart, "hyperram", "4 KiB", "write rnd", &run);

    let bad = hyper_verify();
    ratio(uart, "hyperram", &seq, &rnd);
    let _ = writeln!(
        uart,
        "hyperram word {:x}.. 8 KiB pattern, {} words wrong, sum {:08x}",
        HYPER_BASE, bad, sum
    );
}

/// `bench`, `bench bram`, `bench flash`, `bench hyperram`.
pub fn command(uart: &mut Uart, rest: &[u8]) {
    let _ = writeln!(
        uart,
        "bench    mcycle at {} Hz; D-cache 4 KiB = 64 sets x 1 way x 64 B line",
        target::TIME_HZ
    );
    // No region is every region, so it is tested before the name is parsed.
    if rest.is_empty() {
        header(uart);
        bram(uart);
        flash(uart);
        hyper(uart);
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
            header(uart);
            hyper(uart);
        }
        None => {
            let _ = writeln!(uart, "usage: bench [bram|flash|hyperram]");
        }
    }
}
