//! The device interface: binary request/response ops over the drivers (#303).
//!
//! Contract: `docs/device-interface.md`. `proto`, `frame`, `dispatch` and
//! `engine` are pure and run in the host tests; `board` binds them to the
//! drivers and this file binds them to a console.
//!
//! While a console is in binary mode it has ONE writer, `service`, running in
//! idle context. Text from tasks (`wire::raw`) is diverted into a small ring and
//! emitted by `service` as TEXT records between responses, so a frame is never
//! split by a log line.

pub(crate) mod board;
pub(crate) mod dispatch;
pub(crate) mod engine;
pub(crate) mod frame;
pub(crate) mod proto;

use core::cell::UnsafeCell;
use core::fmt::Write;
use core::sync::atomic::{AtomicU32, AtomicU8, Ordering};

use self::engine::{emit, Engine, Identity, Sink, Step};
use self::proto::{LEAVE_HOST, LEAVE_WATCHDOG};
use crate::uart::{put_at, Uart};
use crate::{target, timer, Devices};

/// Console index + 1 that is in binary mode; 0 for none. Read by tasks (via
/// `divert_text`) and idle, written by idle.
static ACTIVE: AtomicU8 = AtomicU8::new(0);
/// Console index + 1 that asked to enter, until `begin` runs after the prompt.
static PENDING: AtomicU8 = AtomicU8::new(0);

struct EngineCell(UnsafeCell<Engine>);
// SAFETY: touched only by `begin`/`service`, which run from `Shell::poll` in
// idle context on one hart.
unsafe impl Sync for EngineCell {}
static ENGINE: EngineCell = EngineCell(UnsafeCell::new(Engine::new()));

/// Diverted text: `[len][bytes]` entries. 256 bytes holds one longest line or
/// several short ones; a line that does not fit is counted and reported as DROP.
const TEXT_RING: usize = 256;

struct Ring {
    buf: [u8; TEXT_RING],
    head: usize,
    tail: usize,
}
struct RingCell(UnsafeCell<Ring>);
// SAFETY: every access is inside `riscv::interrupt::free`.
unsafe impl Sync for RingCell {}
static TEXT: RingCell = RingCell(UnsafeCell::new(Ring { buf: [0; TEXT_RING], head: 0, tail: 0 }));
static TEXT_LOST: AtomicU32 = AtomicU32::new(0);
static TEXT_REPORTED: AtomicU32 = AtomicU32::new(0);

/// Longest diverted line: `wire::LINE`.
const LINE: usize = crate::wire::LINE;

struct WireSink {
    base: usize,
}

impl Sink for WireSink {
    fn frame(&mut self, frame: &[u8]) {
        let base = self.base;
        emit(frame, &mut |byte| put_at(base, byte));
    }
}

pub(crate) fn active(index: usize) -> bool {
    ACTIVE.load(Ordering::Relaxed) == index as u8 + 1
}

/// The `ops` command. Entry happens after the prompt, in [`begin`].
pub(crate) fn request_enter(index: usize, uart: &mut Uart) {
    match ACTIVE.load(Ordering::Relaxed) {
        0 => PENDING.store(index as u8 + 1, Ordering::Relaxed),
        _ => {
            let _ = writeln!(uart, "ops: busy, another console is in binary mode");
        }
    }
}

/// Start the session requested by `ops`, once the command has returned and the
/// editor has printed its prompt. The first bytes after this are frames.
pub(crate) fn begin(index: usize) {
    if PENDING.load(Ordering::Relaxed) != index as u8 + 1 {
        return;
    }
    PENDING.store(0, Ordering::Relaxed);
    crate::wire::set_divert(divert_text);
    // Anything queued before the switch goes out as text first.
    crate::wire::flush_pending();
    ACTIVE.store(index as u8 + 1, Ordering::Relaxed);
    let now = timer::millis();
    let identity = Identity {
        fw_git: crate::info::build::GIT_WORD,
        gw_git: 0,
        time_hz: target::TIME_HZ,
        session_id: crate::clock::Instant::ZERO.elapsed(crate::clock::now()),
    };
    // SAFETY: see `EngineCell`.
    let engine = unsafe { &mut *ENGINE.0.get() };
    let mut sink = WireSink { base: target::UART_BASES[index] };
    engine.enter(now, identity, &mut sink);
}

/// One turn for the console in binary mode: at most one received byte, the
/// watchdog, and one diverted text line.
pub(crate) fn service(index: usize, byte: Option<u8>, devices: &mut Devices) {
    let mut sink = WireSink { base: target::UART_BASES[index] };
    // SAFETY: see `EngineCell`.
    let engine = unsafe { &mut *ENGINE.0.get() };

    let mut step = Step::Continue;
    if let Some(byte) = byte {
        let mut driver = board::Board { devices, console: index };
        step = engine.feed(timer::millis(), byte, &mut driver, &mut sink);
        // Silence is measured from the end of the op, not its start.
        engine.touch(timer::millis());
    }
    if step == Step::Continue {
        step = engine.tick(timer::millis(), &mut sink);
    }
    if let Step::Left(why) = step {
        ACTIVE.store(0, Ordering::Relaxed);
        discard_text();
        let mut uart = crate::primary_for(index);
        let _ = match why {
            LEAVE_WATCHDOG => writeln!(uart, "\nops: watchdog, back to text"),
            LEAVE_HOST => writeln!(uart, "\nops: back to text"),
            _ => writeln!(uart, "\nops: left"),
        };
        return;
    }

    let mut line = [0u8; LINE];
    if let Some(n) = pop_text(&mut line) {
        engine.text(timer::millis(), &line[..n], &mut sink);
    } else {
        let lost = TEXT_LOST.load(Ordering::Relaxed);
        let reported = TEXT_REPORTED.swap(lost, Ordering::Relaxed);
        if lost != reported {
            engine.dropped(timer::millis(), lost.wrapping_sub(reported), &mut sink);
        }
    }
}

/// Called with every finished line headed for `base`'s wire. True when the
/// console is in binary mode and the line was taken instead.
pub(crate) fn divert_text(base: usize, line: &[u8]) -> bool {
    let active = ACTIVE.load(Ordering::Relaxed);
    if active == 0 || target::UART_BASES[active as usize - 1] != base {
        return false;
    }
    let n = line.len().min(LINE);
    riscv::interrupt::free(|| {
        // SAFETY: interrupts are off; see `RingCell`.
        let ring = unsafe { &mut *TEXT.0.get() };
        let free = (ring.tail + TEXT_RING - ring.head - 1) % TEXT_RING;
        if n + 1 > free {
            TEXT_LOST.fetch_add(1, Ordering::Relaxed);
            return;
        }
        let mut at = ring.head;
        for byte in core::iter::once(n as u8).chain(line[..n].iter().copied()) {
            ring.buf[at] = byte;
            at = (at + 1) % TEXT_RING;
        }
        ring.head = at;
    });
    true
}

fn pop_text(out: &mut [u8; LINE]) -> Option<usize> {
    riscv::interrupt::free(|| {
        // SAFETY: interrupts are off; see `RingCell`.
        let ring = unsafe { &mut *TEXT.0.get() };
        if ring.head == ring.tail {
            return None;
        }
        let n = ring.buf[ring.tail] as usize;
        let mut at = (ring.tail + 1) % TEXT_RING;
        for slot in out.iter_mut().take(n) {
            *slot = ring.buf[at];
            at = (at + 1) % TEXT_RING;
        }
        ring.tail = at;
        Some(n)
    })
}

fn discard_text() {
    riscv::interrupt::free(|| {
        // SAFETY: interrupts are off; see `RingCell`.
        let ring = unsafe { &mut *TEXT.0.get() };
        ring.tail = ring.head;
    });
}
