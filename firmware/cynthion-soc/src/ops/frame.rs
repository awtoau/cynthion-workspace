//! SLIP framing, receive state machine and frame builder. Pure.
//!
//! Wire format: `docs/binary-protocol.md` (envelope), `docs/device-interface.md`.

use super::proto::*;
use crate::hyperram::Crc32;

pub const END: u8 = 0xC0;
pub const ESC: u8 = 0xDB;
pub const ESC_END: u8 = 0xDC;
pub const ESC_ESC: u8 = 0xDD;
pub const ETX: u8 = 0x03;
pub const QUERY: u8 = b'?';

pub const HEADER: usize = 12;
pub const CRC: usize = 4;
pub const RX_MAX: usize = HEADER + MAX_REQ_BODY + CRC;
pub const TX_MAX: usize = HEADER + MAX_RSP_BODY + CRC;

/// What one received byte turned into.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Rx {
    Pending,
    /// A valid REQ with this id; the body is [`Parser::body`].
    Request(u32),
    /// A frame that never became a request: (ERR_* code, value).
    Reject(u32, u32),
    /// ETX between frames.
    Leave,
    /// `?` between frames.
    Query,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    /// Just after an END: nothing buffered. The only state ETX and `?` mean anything in.
    Idle,
    Frame,
    Esc,
    /// Longer than the buffer: discard to the next END.
    Skip,
}

pub struct Parser {
    buf: [u8; RX_MAX],
    len: usize,
    state: State,
    bad_escape: bool,
}

impl Parser {
    pub const fn new() -> Self {
        Parser { buf: [0; RX_MAX], len: 0, state: State::Idle, bad_escape: false }
    }

    pub fn reset(&mut self) {
        self.len = 0;
        self.state = State::Idle;
        self.bad_escape = false;
    }

    /// The last accepted request's body: opcode and arguments.
    pub fn body(&self) -> &[u8] {
        &self.buf[HEADER..self.len - CRC]
    }

    fn append(&mut self, byte: u8) {
        if self.len == RX_MAX {
            self.state = State::Skip;
        } else {
            self.buf[self.len] = byte;
            self.len += 1;
        }
    }

    pub fn push(&mut self, byte: u8) -> Rx {
        match self.state {
            State::Idle => match byte {
                END => Rx::Pending,
                ETX => Rx::Leave,
                QUERY => Rx::Query,
                _ => {
                    self.len = 0;
                    self.bad_escape = false;
                    self.state = State::Frame;
                    self.byte_in_frame(byte)
                }
            },
            State::Frame => self.byte_in_frame(byte),
            State::Esc => {
                match byte {
                    ESC_END => self.append(END),
                    ESC_ESC => self.append(ESC),
                    END => {
                        self.bad_escape = true;
                        return self.finish();
                    }
                    _ => self.bad_escape = true,
                }
                if self.state == State::Esc {
                    self.state = State::Frame;
                }
                Rx::Pending
            }
            State::Skip => {
                if byte == END {
                    self.reset();
                    Rx::Reject(ERR_OVERFLOW, RX_MAX as u32)
                } else {
                    Rx::Pending
                }
            }
        }
    }

    fn byte_in_frame(&mut self, byte: u8) -> Rx {
        match byte {
            END => self.finish(),
            ESC => {
                self.state = State::Esc;
                Rx::Pending
            }
            _ => {
                self.append(byte);
                Rx::Pending
            }
        }
    }

    /// An END closed the frame. Checks in the order the doc states.
    fn finish(&mut self) -> Rx {
        let len = self.len;
        let bad_escape = self.bad_escape;
        self.state = State::Idle;
        self.bad_escape = false;
        if len == 0 && !bad_escape {
            return Rx::Pending;
        }
        if bad_escape {
            return Rx::Reject(ERR_ESCAPE, len as u32);
        }
        if len < HEADER + CRC {
            return Rx::Reject(ERR_RUNT, len as u32);
        }
        let body = u16::from_le_bytes([self.buf[2], self.buf[3]]) as usize;
        if body > MAX_BODY || HEADER + body + CRC != len {
            return Rx::Reject(ERR_LENGTH, body as u32);
        }
        let mut crc = Crc32::new();
        for &b in &self.buf[..len - CRC] {
            crc.push(b);
        }
        let got = u32::from_le_bytes([
            self.buf[len - 4], self.buf[len - 3], self.buf[len - 2], self.buf[len - 1],
        ]);
        if crc.finish() != got {
            return Rx::Reject(ERR_CRC, got);
        }
        let version = self.buf[1] >> 4;
        if version != VERSION {
            return Rx::Reject(ERR_VERSION, version as u32);
        }
        if self.buf[0] != KIND_REQ {
            return Rx::Reject(ERR_KIND, self.buf[0] as u32);
        }
        if body < 2 {
            return Rx::Reject(ERR_SHORT_REQ, body as u32);
        }
        Rx::Request(u32::from_le_bytes([self.buf[4], self.buf[5], self.buf[6], self.buf[7]]))
    }
}

/// Fill the header and CRC around a body already at `buf[HEADER..HEADER + body_len]`.
/// Returns the unescaped frame length.
pub fn seal(buf: &mut [u8; TX_MAX], kind: u8, flags: u8, seq: u32, at: u32, body_len: usize) -> usize {
    buf[0] = kind;
    buf[1] = (VERSION << 4) | (flags & 0xF);
    buf[2..4].copy_from_slice(&(body_len as u16).to_le_bytes());
    buf[4..8].copy_from_slice(&seq.to_le_bytes());
    buf[8..12].copy_from_slice(&at.to_le_bytes());
    let end = HEADER + body_len;
    let mut crc = Crc32::new();
    for &b in &buf[..end] {
        crc.push(b);
    }
    buf[end..end + CRC].copy_from_slice(&crc.finish().to_le_bytes());
    end + CRC
}

/// `END`, the escaped frame, `END`: what goes on the wire.
pub fn escape(frame: &[u8], put: &mut dyn FnMut(u8)) {
    put(END);
    for &b in frame {
        match b {
            END => {
                put(ESC);
                put(ESC_END);
            }
            ESC => {
                put(ESC);
                put(ESC_ESC);
            }
            _ => put(b),
        }
    }
    put(END);
}
