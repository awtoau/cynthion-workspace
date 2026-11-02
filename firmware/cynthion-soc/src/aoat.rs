//! AOAT test frames for the bench app's `sink`, address pattern only, no CRC (flag bit1 tells the
//! sink to skip it). Format: awto-dji `scripts/re/aoa_testkit.py`.
//!
//! Frame, little endian: `AOAT`, test u16, pattern 0 (addr), flags u8 (bit0 last, bit1 no CRC),
//! seed u32, seq u32, total_len u32, payload, zero u32. Payload word `w` = `4w ^ mix`.

pub const MIN_FRAME: u32 = 24;
const HDR: u32 = 20;
const FLAGS_NOCRC: u8 = 2;
const FLAG_LAST: u8 = 1;

/// A run of `frames` whole frames of `frame` bytes each.
pub struct Aoat {
    frame: u32,
    frames: u32,
    seed: u32,
    test: u16,
    seq: u32,
    pos: u32,
    mix: u32,
    /// Each frame is its own USB transfer (packets end at the frame edge); `xfer_zlp` ends a
    /// whole-packet frame with a zero-length packet.
    pub xfer: bool,
    pub xfer_zlp: bool,
}

impl Aoat {
    pub fn new(frame: u32, frames: u32, seed: u32, test: u16) -> Self {
        let mut a = Aoat { frame, frames, seed, test, seq: 0, pos: 0, mix: 0, xfer: false, xfer_zlp: false };
        a.mix = a.mix_for(0);
        a
    }

    fn mix_for(&self, seq: u32) -> u32 {
        seq.wrapping_mul(0x9e37_79b1) ^ self.seed ^ ((self.test as u32) << 16)
    }

    pub fn frame_len(&self) -> u32 {
        self.frame
    }

    pub fn frame_left(&self) -> u32 {
        self.frame - self.pos
    }

    fn header(&self, at: u32) -> u8 {
        let flags = FLAGS_NOCRC | if self.seq + 1 == self.frames { FLAG_LAST } else { 0 };
        let word = |value: u32, i: u32| value.to_le_bytes()[i as usize];
        match at {
            0..=3 => b"AOAT"[at as usize],
            4..=5 => self.test.to_le_bytes()[(at - 4) as usize],
            6 => 0,
            7 => flags,
            8..=11 => word(self.seed, at - 8),
            12..=15 => word(self.seq, at - 12),
            _ => word(self.frame, at - 16),
        }
    }

    /// The next `buf.len()` bytes of the stream.
    pub fn fill(&mut self, buf: &mut [u8]) {
        for slot in buf.iter_mut() {
            *slot = if self.pos < HDR {
                self.header(self.pos)
            } else if self.pos < self.frame - 4 {
                let i = self.pos - HDR;
                ((4 * (i / 4)) ^ self.mix).to_le_bytes()[(i % 4) as usize]
            } else {
                0
            };
            self.pos += 1;
            if self.pos == self.frame {
                self.pos = 0;
                self.seq += 1;
                self.mix = self.mix_for(self.seq);
            }
        }
    }
}
