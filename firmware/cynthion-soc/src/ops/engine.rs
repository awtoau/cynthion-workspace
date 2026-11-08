//! One binary session: receive, dispatch, respond, watchdog. Pure.
//!
//! The board's glue feeds it bytes and a clock and gives it a [`Sink`]; the host
//! tests feed it a fake [`Driver`](super::dispatch::Driver) and a `Vec`.

use super::dispatch::{dispatch, Driver};
use super::frame::{escape, seal, Parser, Rx, HEADER, TX_MAX};
use super::proto::*;

/// Where finished frames go: the unescaped frame, header to CRC.
pub trait Sink {
    fn frame(&mut self, frame: &[u8]);
}

/// Identity stamped into SESSION.
pub struct Identity {
    pub fw_git: u32,
    pub gw_git: u32,
    pub time_hz: u32,
    pub session_id: u32,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Step {
    Continue,
    /// The session ended; the LEAVE_* reason.
    Left(u32),
}

pub struct Engine {
    parser: Parser,
    tx: [u8; TX_MAX],
    /// The stream counter of pushed records. Responses carry the request id instead.
    seq: u32,
    last_activity: u32,
    identity: Identity,
    resync: bool,
}

impl Engine {
    pub const fn new() -> Self {
        Engine {
            parser: Parser::new(),
            tx: [0; TX_MAX],
            seq: 0,
            last_activity: 0,
            identity: Identity { fw_git: 0, gw_git: 0, time_hz: 0, session_id: 0 },
            resync: true,
        }
    }

    /// Begin a session: reset, and emit SESSION (RESYNC).
    pub fn enter(&mut self, now: u32, identity: Identity, sink: &mut dyn Sink) {
        self.parser.reset();
        self.seq = 0;
        self.last_activity = now;
        self.identity = identity;
        self.resync = true;
        self.session(now, sink);
    }

    fn push_record(&mut self, kind: u8, now: u32, body_len: usize, sink: &mut dyn Sink) {
        let flags = if self.resync { FLAG_RESYNC } else { 0 };
        self.resync = false;
        let n = seal(&mut self.tx, kind, flags, self.seq, now, body_len);
        self.seq = self.seq.wrapping_add(1);
        sink.frame(&self.tx[..n]);
    }

    fn session(&mut self, now: u32, sink: &mut dyn Sink) {
        let i = &self.identity;
        let fields = [i.fw_git, i.gw_git, i.time_hz, i.session_id];
        let b = &mut self.tx[HEADER..HEADER + 20];
        b[0] = VERSION;
        b[1] = 0; // kinds: no producers yet
        b[2..4].copy_from_slice(&0u16.to_le_bytes());
        for (k, v) in fields.iter().enumerate() {
            b[4 + 4 * k..8 + 4 * k].copy_from_slice(&v.to_le_bytes());
        }
        self.push_record(KIND_SESSION, now, 20, sink);
    }

    fn error(&mut self, now: u32, code: u32, value: u32, sink: &mut dyn Sink) {
        self.tx[HEADER..HEADER + 4].copy_from_slice(&code.to_le_bytes());
        self.tx[HEADER + 4..HEADER + 8].copy_from_slice(&value.to_le_bytes());
        self.push_record(KIND_ERROR, now, 8, sink);
    }

    /// A line of console text as TEXT records (at most 128 bytes each).
    pub fn text(&mut self, now: u32, line: &[u8], sink: &mut dyn Sink) {
        for chunk in line.chunks(MAX_RSP_BODY) {
            self.tx[HEADER..HEADER + chunk.len()].copy_from_slice(chunk);
            self.push_record(KIND_TEXT, now, chunk.len(), sink);
        }
    }

    /// `lost` text lines were dropped since the last report.
    pub fn dropped(&mut self, now: u32, lost: u32, sink: &mut dyn Sink) {
        let b = &mut self.tx[HEADER..HEADER + 12];
        b[0..4].copy_from_slice(&lost.to_le_bytes());
        b[4..8].copy_from_slice(&self.seq.to_le_bytes());
        b[8..12].copy_from_slice(&now.to_le_bytes());
        self.push_record(KIND_DROP, now, 12, sink);
    }

    fn leave(&mut self, now: u32, why: u32, sink: &mut dyn Sink) -> Step {
        self.error(now, ERR_LEFT, why, sink);
        self.parser.reset();
        Step::Left(why)
    }

    /// The watchdog. Call on every turn; `now` is milliseconds, wrapping.
    pub fn tick(&mut self, now: u32, sink: &mut dyn Sink) -> Step {
        if now.wrapping_sub(self.last_activity) >= WATCHDOG_MS {
            return self.leave(now, LEAVE_WATCHDOG, sink);
        }
        Step::Continue
    }

    /// One received byte.
    pub fn feed(&mut self, now: u32, byte: u8, d: &mut dyn Driver, sink: &mut dyn Sink) -> Step {
        self.last_activity = now;
        match self.parser.push(byte) {
            Rx::Pending => {}
            Rx::Leave => return self.leave(now, LEAVE_HOST, sink),
            Rx::Query => self.session(now, sink),
            Rx::Reject(code, value) => self.error(now, code, value, sink),
            Rx::Request(id) => {
                // The request body is in the parser; the response is built in `tx`.
                let n = dispatch(
                    d,
                    self.parser.body(),
                    &mut self.tx[HEADER..HEADER + MAX_RSP_BODY],
                );
                let len = seal(&mut self.tx, KIND_RSP, 0, id, now, n);
                sink.frame(&self.tx[..len]);
            }
        }
        Step::Continue
    }

    /// Restart the silence clock after work that took the CPU (an op's own time).
    pub fn touch(&mut self, now: u32) {
        self.last_activity = now;
    }
}

/// Escape a finished frame onto a byte sink.
pub fn emit(frame: &[u8], put: &mut dyn FnMut(u8)) {
    escape(frame, put);
}
