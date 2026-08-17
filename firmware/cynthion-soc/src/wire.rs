//! One writer to a console's wire, so a line cannot be spliced into another.
//!
//! [`submit`] takes a FINISHED line and either writes it or queues it. Exactly
//! one caller is on the wire at a time; everyone else leaves a whole line in the
//! ring and returns. The claim is the only thing interrupts are held off for --
//! a few hundred nanoseconds -- never the write, which is milliseconds.
//!
//! ## Why not a lock
//!
//! - `Uart::put` waits for `LSR.THRE`. At 115200 an 80-character line is ~7 ms
//!   on the wire, and a shell reply is not bounded at all: `bench` holds the
//!   console for 22 s. Masking for either is not a trade, it is a fault.
//! - A spin lock on one hart deadlocks: the preempting task spins at a priority
//!   the holder cannot run at, so the holder never finishes.
//! - RTIC's priority ceiling is the deadlock-free version, but it needs a
//!   `Context`, and `shell/editor.rs` and `shell/console.rs` mint consoles deep
//!   inside `idle`'s call tree where there is none.
//!
//! ## Why the buffer is per-writer and not here
//!
//! `core::fmt` emits one `writeln!` as SEVERAL `write_str` calls. A shared
//! staging buffer would be clobbered by the second writer exactly as the wire
//! is now, so the partial line has to live with its writer -- in the `Uart`
//! handle, which `crate::primary()` mints per caller. This module only ever
//! sees whole lines.
//!
//! ## What is not covered
//!
//! `Uart::put` still writes a byte straight to the wire, because the editor's
//! echo has to appear as it is typed rather than at the newline. A task's line
//! can land between two echoed characters. That is cosmetic and on input; the
//! defect this fixes is a corrupted log line ([#541]).
//!
//! [#541]: https://github.com/awtoau/cynthion-workspace/issues/541

use core::sync::atomic::{AtomicBool, AtomicU8, AtomicU32, AtomicUsize, Ordering};

use crate::target;

/// The longest line that can be submitted whole.
///
/// The longest line this firmware emits is `bench`'s per-line counter row at
/// 118 characters; 192 leaves room without rounding up to another 4 KiB of
/// stack across the handles that carry one.
pub const LINE: usize = 192;

/// Bytes of queued lines.
///
/// Holds ~20 full lines. Sized so a burst of task logging during a long shell
/// reply is queued rather than dropped: `docs/soc-interrupts.md` lists 17
/// sources, and the block RAM has the room (`gateware/soc/peripherals/block_ram.py`).
const RING: usize = 4096;

/// Framed as `[console index][length][bytes...]`, so a pop returns a whole line
/// or nothing. `length` is one byte because [`LINE`] is under 256.
static RING_BYTES: [AtomicU8; RING] = [const { AtomicU8::new(0) }; RING];

/// Next byte to be written. Advanced only by [`enqueue`].
static HEAD: AtomicUsize = AtomicUsize::new(0);
/// Next byte to be read. Advanced only by [`dequeue`].
static TAIL: AtomicUsize = AtomicUsize::new(0);

/// Set while someone is on the wire. The claim, and the whole lock.
static ON_WIRE: AtomicBool = AtomicBool::new(false);

/// Lines lost because the ring was full. Never reset.
///
/// The issue asked whether a splice was invisible to the firmware as well as to
/// the reader. It was. A drop is now a number, and `console status` prints it.
static DROPPED: AtomicU32 = AtomicU32::new(0);

/// Lines cut at [`LINE`] because the writer never sent a newline.
///
/// A cut line CAN be interleaved -- it is two submissions -- so it is counted
/// separately from a drop rather than folded in. Non-zero means a caller is
/// writing more than a line between newlines.
static SPLIT: AtomicU32 = AtomicU32::new(0);

/// `(lines dropped, lines cut)`.
pub fn stats() -> (u32, u32) {
    (DROPPED.load(Ordering::Relaxed), SPLIT.load(Ordering::Relaxed))
}

/// Count one line cut at [`LINE`]. Called by `Uart` when its buffer fills.
pub fn count_split() {
    SPLIT.fetch_add(1, Ordering::Relaxed);
}

/// Put any queued lines on the wire NOW, if nobody else is draining.
///
/// **Ordering, and the console does not work without it.** The ring is drained
/// only by whoever finishes a submission, while `Uart::put` writes the editor's
/// prompt and echo straight to the wire. So a reply sat queued until the NEXT
/// command submitted something, and every reply arrived one command late:
/// `bram status` answered with "time", `cpu status` answered with "bram
/// status", and `info` returned 18 bytes of prompt so the board could not
/// identify itself at all.
///
/// Re-entrant by construction: draining calls `put_at`, which calls this, which
/// sees the claim held and returns.
pub fn flush_pending() {
    let ours = riscv::interrupt::free(|| {
        if ON_WIRE.load(Ordering::Relaxed)
            || HEAD.load(Ordering::Relaxed) == TAIL.load(Ordering::Relaxed) {
            return false;
        }
        ON_WIRE.store(true, Ordering::Relaxed);
        true
    });
    if ours {
        drain_queued();
    }
}

/// Pop and write queued lines until the ring is empty, then release the claim.
///
/// Released with the ring observed empty inside the same interrupts-off window
/// that observed it, so a line cannot be queued into a ring nobody will drain.
fn drain_queued() {
    loop {
        let mut queued = [0u8; LINE];
        let next = riscv::interrupt::free(|| {
            let taken = dequeue(&mut queued);
            if taken.is_none() {
                ON_WIRE.store(false, Ordering::Relaxed);
            }
            taken
        });
        match next {
            Some((queued_base, length)) => raw(queued_base, &queued[..length]),
            None => return,
        }
    }
}

/// Write `line` to `base`'s wire, or queue it whole if another writer is on it.
///
/// Never blocks on another writer and never returns a partial line to the wire.
pub fn submit(base: usize, line: &[u8]) {
    // The claim. `riscv::interrupt::free` reads `mstatus`, clears MIE, runs the
    // closure and restores what it read, so it cannot enable interrupts that
    // were already off -- the same call and the same reason as `events::push`.
    let ours = riscv::interrupt::free(|| {
        if ON_WIRE.load(Ordering::Relaxed) {
            enqueue(base, line);
            false
        } else {
            ON_WIRE.store(true, Ordering::Relaxed);
            true
        }
    });
    if !ours {
        return;
    }

    // Interrupts are ON from here. A task that logs while we are writing sees
    // `ON_WIRE` and queues; we pick it up below rather than letting it onto the
    // wire behind us.
    raw(base, line);
    drain_queued();
}

/// Bytes straight to the wire. The caller owns the claim.
fn raw(base: usize, line: &[u8]) {
    for &byte in line {
        crate::uart::put_at(base, byte);
    }
}

/// Copy one framed line in. Caller holds interrupts off.
fn enqueue(base: usize, line: &[u8]) {
    let head = HEAD.load(Ordering::Relaxed);
    let tail = TAIL.load(Ordering::Relaxed);
    // One byte is never used, so `head == tail` can only mean empty and the two
    // sides need no separate count to disagree about -- as `events::push`.
    let free = (tail + RING - head - 1) % RING;
    if line.len() + 2 > free {
        DROPPED.fetch_add(1, Ordering::Relaxed);
        return;
    }

    let mut at = head;
    let mut put = |byte: u8| {
        RING_BYTES[at].store(byte, Ordering::Relaxed);
        at = (at + 1) % RING;
    };
    put(index_of(base) as u8);
    put(line.len() as u8);
    for &byte in line {
        put(byte);
    }
    HEAD.store(at, Ordering::Relaxed);
}

/// Copy one framed line out. Caller holds interrupts off.
fn dequeue(out: &mut [u8; LINE]) -> Option<(usize, usize)> {
    let mut at = TAIL.load(Ordering::Relaxed);
    if at == HEAD.load(Ordering::Relaxed) {
        return None;
    }

    let mut take = || {
        let byte = RING_BYTES[at].load(Ordering::Relaxed);
        at = (at + 1) % RING;
        byte
    };
    let index = take() as usize;
    let length = (take() as usize).min(LINE);
    for slot in out.iter_mut().take(length) {
        *slot = take();
    }
    TAIL.store(at, Ordering::Relaxed);
    Some((base_of(index), length))
}

/// Which console owns `base`. Linear over at most four entries, as
/// `uart::count_drop`'s.
fn index_of(base: usize) -> usize {
    let mut index = 0;
    while index < target::UART_BASES.len() {
        if target::UART_BASES[index] == base {
            return index;
        }
        index += 1;
    }
    0
}

fn base_of(index: usize) -> usize {
    target::UART_BASES[index.min(target::UART_BASES.len() - 1)]
}
