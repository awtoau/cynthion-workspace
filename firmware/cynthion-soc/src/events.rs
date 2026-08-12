//! What an interrupt handler is allowed to say, and when it gets said.
//!
//! [`push`] records a code, a 64-bit payload and the time in a ring; the main
//! loop drains it with [`drain`] and formats there, on a console it owns.
//! **The time is captured by [`push`], not [`drain`]** -- easy to get backwards,
//! see `src/log.rs`.
//!
//! ## A handler must never print
//!
//! - `Uart::put` waits for `LSR.THRE` -- blocks for as long as the host takes to
//!   drain a FIFO.
//! - On a **level-sensitive shared source that is a hang, not a delay**: the line
//!   stays asserted, the interrupt is retaken on return, and it presents as a dead
//!   CPU with a running clock. Mistaken for dead gateware here more than once.
//! - `core::fmt` is also not cheap: `writeln!` with two arguments is a dispatch
//!   through `Arguments`, a conversion per value and a call per fragment. A code
//!   and a payload is four stores.
//!
//! ## A record is two numbers, never a string
//!
//! A string needs a bound, a length field and a truncation rule, and entries stop
//! being one size -- so the ring stops being a plain array. The tag in the code's
//! top byte is what makes the value readable again at drain.
//!
//! | tag | payload            | rendered as         |
//! | --- | ------------------ | ------------------- |
//! | 0   | none               | `(no payload)`      |
//! | 1   | `u8`               | `u8 a5`             |
//! | 2   | `u16`              | `u16 beef`          |
//! | 3   | `u32`              | `u32 deadbeef`      |
//! | 4   | `u64`              | `u64 0123456789ab…` |
//! | 5   | `f32`              | raw bits, see below |
//! | 8   | `f64`              | raw bits, see below |
//! | 16  | 8 x `u8`           | `bytes 00 11 22 …`  |
//! | 17  | 8 ASCII characters | `"cynthion"`        |
//!
//! - Tag 17 makes the no-strings rule liveable: an 8-character identifier with no
//!   allocator, length field or truncation rule.
//! - Tags 16 and 17 read the value **big-endian** (byte 0 is the top byte), so hex
//!   and characters run the same direction.
//! - A value wider than its tag is **not** truncated silently: [`Payload`] prints
//!   the masked value and then the whole one, marked.
//!
//! ## Float tags are reserved, not implemented
//!
//! - This CPU is `rv32imac`: **no F extension**.
//! - Carrying tags 5 and 8 is free (64 bits like the rest). *Rendering* one pulls
//!   in `core::fmt`'s float path and compiler-builtins soft-float -- hundreds of
//!   bytes. `src/power.rs` avoids the same thing with exact integer rationals.
//! - So [`drain`] prints their **raw bits** with a note saying why.
//! - Recorded because an unimplemented arm that looks like an oversight is the
//!   trap: someone adds the formatter and the image grows by a page.
//! - Want a float payload? Use fixed-point under tag 3 or 4.
//!
//! ## The code: a tag, a subsystem and a number
//!
//! ```text
//!   0x TT SS NNNN
//!      ^^            tag        bits 31..24, the table above
//!         ^^         subsystem  bits 23..16, who is speaking
//!            ^^^^    number     bits 15.. 0, which of their events
//! ```
//!
//! | subsystem | who                                                   | const          |
//! | --------- | ----------------------------------------------------- | -------------- |
//! | 0x00      | the ring itself: its self-test, its own health         | [`SUB_RING`]   |
//! | 0x01      | Type-C, `src/typec.rs` and the handler in `src/irq.rs` | [`SUB_TYPE_C`] |
//! | 0x02      | the consoles, `src/uart.rs`                            | reserved       |
//! | 0x03      | the power rails, `src/power.rs`                        | reserved       |
//!
//! - Byte aligned so the hex reads directly.
//! - Built by [`code`], never written as a literal, so a code carries its own type
//!   declaration: `code(TAG_U8, SUB_TYPE_C, 1)`. Keeps codes greppable -- `code
//!   01.01.0001` leads to `SUB_TYPE_C` and the one constant numbered 1.
//! - A reserved row has **no constant on purpose**: an unused `pub const` is
//!   something the next person must decide whether to trust. The number is claimed
//!   so it cannot be reused; the constant appears with the first event needing it.
//! - `src/uart.rs` overruns deliberately do NOT use this ring: an overrun repeats,
//!   and the useful report is "the line lost input, N times" -- a latched bitmap
//!   plus a counter, not N records. A ring entry is for something that happened
//!   once, at a time worth knowing.
//!
//! ## How the rule is enforced
//!
//! - **Ownership.** `main` owns the `Uart` values and passes them by `&mut`; a
//!   handler is a free function with nothing to hand it one. No global `print!`,
//!   no logging singleton, no `static mut` in this crate. `src/irq.rs` takes
//!   [`crate::uart::UartRx`], which has no transmit method and no
//!   `core::fmt::Write`, so `write!` there does not compile.
//! - **Grep, for the rest.** Rust privacy is per-module-tree, so a private item in
//!   the crate root is nameable from every child module.
//!   `scripts/soc_irq_log_check.py` fails any module whose handler mentions
//!   `write!`, `writeln!`, `fmt::Write` or `Uart` -- the `irqlog` check in
//!   `scripts/check.py`.
//! - A handler CAN log; it just cannot be what formats and transmits. A rule with
//!   no alternative gets worked around rather than followed.
//!
//! ## Wait-free, bounded, lossy on purpose
//!
//! - [`push`] never spins and never waits. A full ring DROPS the record and
//!   increments a counter: a storm degrades to lost lines, not a stalled handler.
//! - **The drop count is reported** by the `irq` command and by [`drain`] the first
//!   time it notices, with the time of the first record lost -- so the gap in the
//!   timestamp column has a stated start instead of being a silence to notice.
//!
//! ## One producer at a time, on one hart
//!
//! - [`log_from_irq!`] is usable from ANY context, so a push from normal context
//!   can be interrupted mid-copy by a push from a handler.
//! - [`push`] therefore clears `mstatus.MIE` for the length of the copy and
//!   restores it: three or four instructions, no loop, still wait-free.
//! - **In a handler this costs nothing**: hardware already cleared MIE on trap
//!   entry and this firmware never sets it inside a handler, so the restore writes
//!   back the zero it read.
//! - A compare-exchange loop would be lock-free rather than wait-free, and would
//!   still need the payload published separately.
//!
//! Nothing here is target-specific: compiled unchanged for the FPGA and QEMU, and
//! `scripts/soc_test.py` exercises every tag, fill, wrap and drop count through the
//! `log` shell command.

use core::fmt;
use core::fmt::Write;
use core::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use crate::fusb302::{Interrupts, Port};
use crate::log;
use crate::uart::Uart;

/// Records held between a handler and the main loop.
///
/// Sixteen slots of sixteen bytes: 256 bytes of the 32 KiB half of block RAM
/// this image gets. The main loop drains on every pass -- microseconds apart --
/// so this is not a latency budget: it is how many events may arrive in one
/// burst while the loop is inside a command that is printing. A shell command
/// can spin for milliseconds in `Uart::put`, and a Type-C line can assert twice
/// in a row, so 16 is generous rather than tight. Overrunning it is not a
/// failure, it is a counted loss.
///
/// A power of two, so `% RING` is an AND and `SLOTS[i]` is a shift -- see
/// [`Slot`] for why the entry is 16 bytes without any padding being spent on it.
const RING: usize = 16;

// --- the code -----------------------------------------------------------------

/// Where the tag sits in a code. See the module comment.
const TAG_SHIFT: u32 = 24;
/// Where the subsystem sits in a code.
const SUBSYSTEM_SHIFT: u32 = 16;

/// No payload; the code stands alone.
pub const TAG_NONE: u32 = 0;
/// The low 8 bits of the payload.
pub const TAG_U8: u32 = 1;
/// The low 16 bits of the payload.
pub const TAG_U16: u32 = 2;
/// The low 32 bits of the payload.
pub const TAG_U32: u32 = 3;
/// The whole 64-bit payload.
pub const TAG_U64: u32 = 4;
/// An `f32`'s bit pattern. **Reserved; printed as bits.** See the module
/// comment: rendering it would pull soft-float into a `no_std` image on a CPU
/// with no F extension.
pub const TAG_F32: u32 = 5;
/// An `f64`'s bit pattern. **Reserved; printed as bits**, for the same reason
/// as [`TAG_F32`].
pub const TAG_F64: u32 = 8;
/// Eight bytes, most significant first. See [`bytes8`].
pub const TAG_BYTES8: u32 = 16;
/// Eight ASCII characters, first character in the top byte. See [`ascii8`].
pub const TAG_ASCII8: u32 = 17;

/// The ring's own affairs: its self-test and its health.
pub const SUB_RING: u32 = 0x00;
/// Type-C: `src/typec.rs` and the deferring handler in `src/irq.rs`.
pub const SUB_TYPE_C: u32 = 0x01;

/// Build a code from a tag, a subsystem and a number.
///
/// Masks rather than asserts, deliberately: an `assert!` would be free in a
/// `const` but would put a panic path in the binary for the run-time callers
/// (`push_tag_samples`), and this firmware counts bytes. An out-of-range
/// argument then corrupts only its own field instead of the tag above it.
pub const fn code(tag: u32, subsystem: u32, number: u32) -> u32 {
    ((tag & 0xff) << TAG_SHIFT) | ((subsystem & 0xff) << SUBSYSTEM_SHIFT) | (number & 0xffff)
}

/// The payload tag a code declares.
pub const fn tag_of(code: u32) -> u32 {
    code >> TAG_SHIFT
}

/// Which subsystem a code belongs to.
pub const fn subsystem_of(code: u32) -> u32 {
    (code >> SUBSYSTEM_SHIFT) & 0xff
}

/// Which of that subsystem's events a code is.
pub const fn number_of(code: u32) -> u32 {
    code & 0xffff
}

/// Eight bytes as a payload, byte 0 in the top of the value.
pub const fn bytes8(value: &[u8; 8]) -> u64 {
    u64::from_be_bytes(*value)
}

/// Eight ASCII characters as a payload, first character in the top of the value.
///
/// The same bytes as [`bytes8`] and a separate name on purpose: which of the two
/// a caller writes is what decides whether the drain prints hex or characters,
/// and a payload built by the wrong one is a line nobody can read.
///
/// Fixed at eight because that is what fits, which is the whole point: no
/// length, no bound to check, no truncation rule. A caller with fewer characters
/// pads with spaces and one with more picks a shorter name.
pub const fn ascii8(value: &[u8; 8]) -> u64 {
    bytes8(value)
}

// --- the codes ----------------------------------------------------------------

/// A Type-C `int` line asserted. The payload is the port's bit -- 1 for TARGET,
/// 2 for AUX.
///
/// Exact rather than a guess, because each controller has its own PLIC source:
/// the handler knows which one from the claim, with no register read. Two
/// records, not one with two bits, if both assert.
pub const TYPE_C_INT: u32 = code(TAG_U8, SUB_TYPE_C, 1);

/// A Type-C `fault` line asserted. The payload is the bitmap.
///
/// Distinct from [`TYPE_C_INT`] because it means something different, and it is
/// the one worth noticing without having to read a register first.
pub const TYPE_C_FAULT: u32 = code(TAG_U8, SUB_TYPE_C, 2);

/// Why a Type-C `int` line asserted: the port and the three read-to-clear
/// interrupt registers, packed by `fusb302::Interrupts::payload`.
///
/// Pushed by `typec::Controllers::service` -- normal context, not a handler --
/// and it belongs here anyway. The registers are read-to-clear, so their contents
/// exist exactly once and only in the reader's hand; putting them in this ring
/// keeps them in the same stamped column as the [`TYPE_C_INT`] record they
/// explain, and keeps the decoding beside every other decoding rather than in the
/// middle of the service loop.
pub const TYPE_C_CAUSE: u32 = code(TAG_U32, SUB_TYPE_C, 3);

/// A test record, pushed by the `log` shell command. The payload is the
/// sequence number the command gave it.
///
/// Here rather than in a test file because the ring is `no_std` firmware with no
/// unit-test harness: the only way to exercise fill, wrap and drop counting on
/// the code that actually ships is to make the shipping build able to push one.
pub const TEST: u32 = code(TAG_U32, SUB_RING, 1);

/// Where the sample codes pushed by `log tags` start.
///
/// Offset past [`TEST`] and given the tag as its number, so a sample's code
/// names its own tag twice -- `03.00.0013` is the `u32` one -- and no sample can
/// collide with a real event.
const TAG_SAMPLE: u32 = 0x10;

/// One record per tag, chosen to be unmistakable in a console capture.
///
/// A code per row rather than a tag, so each row states the whole thing it is
/// testing and the last row can be a code that does not match its value.
///
/// The floats are `1.0` in each width, written as bit patterns rather than as
/// literals: this crate must not name an `f32` at all, or the constant
/// evaluation reaches for soft-float before the formatter gets a chance to. See
/// the module comment.
const TAG_SAMPLES: [(u32, u64); 10] = [
    (code(TAG_NONE, SUB_RING, TAG_SAMPLE + TAG_NONE), 0),
    (code(TAG_U8, SUB_RING, TAG_SAMPLE + TAG_U8), 0xa5),
    (code(TAG_U16, SUB_RING, TAG_SAMPLE + TAG_U16), 0xbeef),
    (code(TAG_U32, SUB_RING, TAG_SAMPLE + TAG_U32), 0xdead_beef),
    (
        code(TAG_U64, SUB_RING, TAG_SAMPLE + TAG_U64),
        0x0123_4567_89ab_cdef,
    ),
    (code(TAG_F32, SUB_RING, TAG_SAMPLE + TAG_F32), 0x3f80_0000),
    (
        code(TAG_F64, SUB_RING, TAG_SAMPLE + TAG_F64),
        0x3ff0_0000_0000_0000,
    ),
    (
        code(TAG_BYTES8, SUB_RING, TAG_SAMPLE + TAG_BYTES8),
        bytes8(&[0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77]),
    ),
    (
        code(TAG_ASCII8, SUB_RING, TAG_SAMPLE + TAG_ASCII8),
        ascii8(b"cynthion"),
    ),
    // A code that lies: `u16` over a value needing seventeen bits. The tag is a
    // promise, and the point of this row is that a broken one is printed rather
    // than truncated into a wrong number that looks right. An unexercised guard
    // is a guard nobody knows is broken.
    (code(TAG_U16, SUB_RING, TAG_SAMPLE + 0x20), 0x1_2345),
];

// --- the ring -----------------------------------------------------------------

/// One record: a code, a 64-bit payload and a push time. Sixteen bytes.
///
/// The payload is two `AtomicU32` halves rather than an `AtomicU64` because
/// there is no `AtomicU64` on this target -- `rv32a` has 32-bit `lr`/`sc` and
/// nothing wider -- so a 64-bit payload is two words whichever way it is
/// written. **That is why the entry is exactly 16 bytes with no padding**: 4 + 4
/// + 4 + 4, a power of two for free, so indexing is a shift and `% RING` is an
/// AND. There were no bytes to weigh up.
///
/// Parallel atomics rather than a struct behind an `UnsafeCell`, for the same
/// reason `src/irq.rs` uses `[AtomicU8; N]`: it needs no `unsafe impl Sync` and
/// no unsafe block, and on this target it compiles to the same loads and stores
/// either way.
struct Slot {
    code: AtomicU32,
    /// The low half of the payload.
    lo: AtomicU32,
    /// The high half of the payload.
    hi: AtomicU32,
    /// When this record was PUSHED, in milliseconds since the tick started.
    ///
    /// The fourth word, and the reason it is here rather than being read by
    /// [`drain`]: a record drained a hundred milliseconds after the event would
    /// otherwise report the drain. See the module comment in `src/log.rs` --
    /// the events worth timing are exactly the ones this ring carries.
    at: AtomicU32,
}

impl Slot {
    const fn new() -> Self {
        Slot {
            code: AtomicU32::new(0),
            lo: AtomicU32::new(0),
            hi: AtomicU32::new(0),
            at: AtomicU32::new(0),
        }
    }
}

static SLOTS: [Slot; RING] = [const { Slot::new() }; RING];

/// Next slot to be written. Advanced only by [`push`].
static HEAD: AtomicUsize = AtomicUsize::new(0);
/// Next slot to be read. Advanced only by [`drain`].
static TAIL: AtomicUsize = AtomicUsize::new(0);
/// Records lost because the ring was full. Never reset.
static DROPPED: AtomicU32 = AtomicU32::new(0);
/// How many drops the console has already been told about, so a loss is
/// reported once rather than on every pass of the main loop.
static REPORTED: AtomicU32 = AtomicU32::new(0);
/// When the first record of the current unreported burst was lost.
///
/// The lost records took their timestamps with them, so the column jumps. This
/// is the start of that jump, said out loud: "5 lost from 000012.481" turns a
/// silence a reader has to notice into a fact on the line that reports it.
static LOST_FROM: AtomicU32 = AtomicU32::new(0);

/// Record an event. Safe from any context, including an interrupt handler.
///
/// `code` carries the tag that says how to read `value`; see [`code`] and the
/// module comment. Never blocks, never allocates, never formats and never waits.
/// Returns `false` if the ring was full and the record was dropped, which
/// callers are free to ignore -- the count is reported by [`drain`] either way.
pub fn push(code: u32, value: u64) -> bool {
    // Interrupts off for the length of the copy. See the module comment: this
    // is what lets normal context push into the same ring a handler pushes
    // into, and it is a no-op when called from a handler.
    //
    // `riscv::interrupt::free` reads `mstatus`, clears MIE, runs the closure and
    // restores what it read -- so it cannot enable interrupts that were off.
    riscv::interrupt::free(|| {
        let head = HEAD.load(Ordering::Relaxed);
        let next = (head + 1) % RING;
        // The ring holds RING-1 records, not RING: head == tail must mean
        // empty, so the completely-full state is not representable. One wasted
        // slot buys a pair of indices with no separate count for the two sides
        // to disagree about -- the same trade `src/irq.rs` makes.
        if next == TAIL.load(Ordering::Acquire) {
            let lost = DROPPED.fetch_add(1, Ordering::Relaxed);
            // The first loss of a burst dates the gap it is about to leave in
            // the timestamp column. One compare and one store, still no loop.
            if lost == REPORTED.load(Ordering::Relaxed) {
                LOST_FROM.store(log::now().millis(), Ordering::Relaxed);
            }
            return false;
        }
        SLOTS[head].code.store(code, Ordering::Relaxed);
        SLOTS[head].lo.store(value as u32, Ordering::Relaxed);
        SLOTS[head]
            .hi
            .store((value >> 32) as u32, Ordering::Relaxed);
        // HERE, not in `drain`. One atomic load of a counter the tick handler
        // maintains -- no MMIO, no division, nothing that could make a push
        // expensive enough to think about.
        SLOTS[head].at.store(log::now().millis(), Ordering::Relaxed);
        // Release: the payload must be visible before the index that publishes
        // it, or the consumer can read a slot before it was written.
        HEAD.store(next, Ordering::Release);
        true
    })
}

/// Record an event from any context, including an interrupt handler.
///
/// The macro exists so that the compile error a `writeln!` in a handler
/// produces has an obvious answer. It is a thin wrapper over [`push`]; the
/// value is the name.
///
/// The `as u64` widens any unsigned integer the caller has to hand. A payload
/// that is not one -- eight bytes, eight characters -- goes through
/// [`bytes8`] or [`ascii8`] first, which is the point at which the caller has
/// to have decided what tag the code declares.
#[macro_export]
macro_rules! log_from_irq {
    ($code:expr) => {
        $crate::events::push($code, 0)
    };
    ($code:expr, $value:expr) => {
        $crate::events::push($code, $value as u64)
    };
}

/// How many records have been dropped since boot.
pub fn dropped() -> u32 {
    DROPPED.load(Ordering::Relaxed)
}

/// How many records are waiting to be printed.
pub fn waiting() -> usize {
    let head = HEAD.load(Ordering::Relaxed);
    let tail = TAIL.load(Ordering::Relaxed);
    (head + RING - tail) % RING
}

/// Push [`TAG_SAMPLES`] -- one record per payload tag. Returns how many fit.
///
/// Driven by `log tags` in the shell, and through the same [`push`] a handler
/// calls. It is the only exercise the tag decoding gets: every sample lands on
/// the generic arm of [`report`], which is where a tag is actually interpreted,
/// so `scripts/soc_test.py` asserting on these lines is asserting on the decoder
/// rather than on ten hand-written sentences.
///
/// Ten records, into a ring that holds fifteen, so a clean ring takes them all
/// and a return of less than ten is a real drop.
pub fn push_tag_samples() -> u32 {
    let mut pushed = 0;
    let mut index = 0;
    while index < TAG_SAMPLES.len() {
        let (code, value) = TAG_SAMPLES[index];
        if push(code, value) {
            pushed += 1;
        }
        index += 1;
    }
    pushed
}

/// Print everything waiting, and any losses since the last time.
///
/// Called from the main loop with the primary console. This is where the
/// formatting happens, in normal context, on a `Uart` the caller owns -- which
/// is the whole arrangement: a handler cannot reach a `Uart`, and this function
/// cannot be called without one.
pub fn drain(uart: &mut Uart) {
    loop {
        let tail = TAIL.load(Ordering::Relaxed);
        // Acquire, pairing with the release in `push`.
        if tail == HEAD.load(Ordering::Acquire) {
            break;
        }
        let code = SLOTS[tail].code.load(Ordering::Relaxed);
        let lo = SLOTS[tail].lo.load(Ordering::Relaxed) as u64;
        let hi = SLOTS[tail].hi.load(Ordering::Relaxed) as u64;
        let at = log::Stamp::at(SLOTS[tail].at.load(Ordering::Relaxed));
        TAIL.store((tail + 1) % RING, Ordering::Release);
        // Formatting a record is work, and it is work an interrupt caused.
        crate::metrics::busy();
        report(uart, at, code, (hi << 32) | lo);
    }

    // Losses, once each. Reported here rather than only by the `irq` command
    // because a drop that nobody types a command to discover is a drop nobody
    // knows about, and the whole reason the ring is lossy is that the
    // alternative was worse -- which is only true if the loss is visible.
    let dropped = DROPPED.load(Ordering::Relaxed);
    let reported = REPORTED.load(Ordering::Relaxed);
    if dropped != reported {
        REPORTED.store(dropped, Ordering::Relaxed);
        // Two times on one line, and they are different times. The line itself
        // is stamped NOW, because it is about the ring and the moment it
        // describes is the moment the loss was noticed; `from` is when the
        // first lost record would have been stamped, which is where the gap in
        // the column begins.
        crate::log!(
            uart,
            "irq log: {} event(s) LOST from {} -- the ring filled \
                           faster than the shell drained it",
            dropped - reported,
            log::Stamp::at(LOST_FROM.load(Ordering::Relaxed))
        );
    }
}

// --- formatting, all of it in normal context ----------------------------------

/// Why the float tags print bits instead of a number.
///
/// Short because it is `.rodata` in an image that does not fit its half of block
/// RAM. The rest of the reason -- that a formatter drags compiler-builtins
/// soft-float in with it -- is in the module comment, where it costs nothing.
const NO_FLOAT: &str = "(bits only: rv32imac has no F extension)";

/// A payload rendered according to the tag in its code.
///
/// A `Display` rather than a function so the rendering nests inside a
/// `format_args!` like any other argument, and so a code with its own sentence
/// in [`report`] can use it or not.
struct Payload {
    tag: u32,
    value: u64,
}

impl Payload {
    const fn of(code: u32, value: u64) -> Payload {
        Payload {
            tag: tag_of(code),
            value,
        }
    }

    /// How many bits the tag promises. 64 for anything wider or unknown.
    const fn bits(&self) -> u32 {
        match self.tag {
            TAG_NONE => 0,
            TAG_U8 => 8,
            TAG_U16 => 16,
            TAG_U32 | TAG_F32 => 32,
            _ => 64,
        }
    }
}

impl fmt::Display for Payload {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let bits = self.bits();
        let kept = if bits >= 64 {
            self.value
        } else {
            self.value & ((1u64 << bits) - 1)
        };

        // One hex digit per four bits, so `u8 a5` and `u64 0123456789abcdef`
        // come out of the same `write!`. A runtime width rather than four
        // literal `{:02x}`/`{:04x}`/`{:08x}`/`{:016x}` sites: each site is its
        // own `core::fmt` fragment table in the binary, and this image is
        // already over its half of block RAM (see the memory split in
        // `memory.x`). Same output, one call site.
        let digits = (bits / 4) as usize;

        match self.tag {
            TAG_NONE => f.write_str("(no payload)")?,
            TAG_U8 | TAG_U16 | TAG_U32 | TAG_U64 => {
                write!(f, "u{} {:0digits$x}", bits, kept)?;
            }
            TAG_F32 | TAG_F64 => {
                write!(f, "f{} bits {:0digits$x} {}", bits, kept, NO_FLOAT)?;
            }
            TAG_BYTES8 => {
                f.write_str("bytes")?;
                for byte in kept.to_be_bytes() {
                    write!(f, " {:02x}", byte)?;
                }
            }
            TAG_ASCII8 => {
                f.write_char('"')?;
                for byte in kept.to_be_bytes() {
                    // Substituted rather than escaped: the field is eight
                    // characters wide and it stays eight characters wide, so a
                    // capture can be matched on position.
                    f.write_char(if (0x20..=0x7e).contains(&byte) {
                        byte as char
                    } else {
                        '.'
                    })?;
                }
                f.write_char('"')?;
            }
            other => write!(f, "tag {} value {:016x}", other, kept)?,
        }

        // A promise the value did not keep. Printed rather than swallowed: a
        // silently truncated payload is a wrong number that looks right, which
        // is the worst thing a log line can be.
        if kept != self.value {
            write!(
                f,
                " !! declared {} bits, value is {:016x}",
                bits, self.value
            )?;
        }
        Ok(())
    }
}

/// One line for one record.
///
/// All the formatting for every event code lives here, in one `match`, in
/// normal context. That is deliberate: a code whose arguments are formatted at
/// the push site would be formatting inside a handler again, one indirection
/// further away where nobody would notice.
///
/// A code with its own arm gets a sentence, because it knows what its payload
/// means. Everything else goes to the last arm, which knows only the tag -- and
/// that arm is the whole reason the tag exists: a code nobody has written a
/// sentence for still prints something a reader can use.
///
/// `at` is when the record was PUSHED. Every arm uses it and none of them reads
/// the clock; see `src/log.rs` for why that distinction is the point of the
/// field.
fn report(uart: &mut Uart, at: log::Stamp, code: u32, value: u64) {
    match code {
        TYPE_C_INT => {
            crate::log_at!(uart, at, "type-c: int asserted, port {:02x}", value as u8);
        }
        TYPE_C_FAULT => {
            crate::log_at!(
                uart,
                at,
                "type-c: FAULT asserted, controllers {:02x}",
                value as u8
            );
        }
        TYPE_C_CAUSE => {
            let value = value as u32;
            crate::log_at!(
                uart,
                at,
                "type-c {}: int {}",
                match Port::ALL.get(Interrupts::port_of(value)) {
                    Some(port) => port.name(),
                    // A payload built by something other than `Interrupts::payload`.
                    // Printed rather than indexed into, on the same principle as the
                    // broken-promise arm of `Payload`.
                    None => "?",
                },
                Interrupts::from_payload(value)
            );
        }
        TEST => {
            crate::log_at!(uart, at, "log test {}", value as u32);
        }
        _ => {
            crate::log_at!(
                uart,
                at,
                "irq log: code {:02x}.{:02x}.{:04x} {}",
                tag_of(code),
                subsystem_of(code),
                number_of(code),
                Payload::of(code, value)
            );
        }
    }
}
