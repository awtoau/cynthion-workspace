//! Driver for the data path window (`gateware/soc/datapath/core.py`): the RV's per-block
//! control. Payload bytes never pass through here. The shell (`shell/datapath.rs`) is syntax.
//!
//! - Register map and sequence: `docs/data-path.md`. Reference model: `Rv` in
//!   `scripts/soc/datapath_sim.py` (proven in simulation only).
//! - Not in `top.py` yet (#578): `target::DATAPATH` is `None` and every call returns `NoDataPath`.
//! - Every wait is `wait_until`: a limit, an op name, elapsed on expiry.

use core::fmt;
use core::ptr::{read_volatile, write_volatile};

use crate::clock;
use crate::usb_host::{self, Host};

const IRQ_EN: usize = 0x00;
const EVENTS: usize = 0x01;
const CTRL: usize = 0x02;
const STATUS: usize = 0x03;
const D_FLAGS: usize = 0x04;
const D_TAG: usize = 0x05;
const D_AUX: usize = 0x06;
const D_ERR: usize = 0x07;
const D_LEN0: usize = 0x08;
const ADDR: usize = 0x0c;
const EP: usize = 0x0d;
const MPS_LO: usize = 0x0e;
const LEN0: usize = 0x10;
const NAK_LO: usize = 0x14;
const R_RESP: usize = 0x18;
const R_FLAGS: usize = 0x19;
const R_NAKS_LO: usize = 0x1a;
const R_MOVED0: usize = 0x1c;
const H_FLAGS: usize = 0x20;
const H_TAG: usize = 0x21;
const H_AUX: usize = 0x22;
const H_STATUS: usize = 0x23;
const H_LEN0: usize = 0x24;

const CTRL_POP: u8 = 1 << 0;
const CTRL_ARM: u8 = 1 << 1;
const CTRL_SEND: u8 = 1 << 2;

const ST_DESC: u8 = 1 << 0;
const ST_SEQ_BUSY: u8 = 1 << 1;
const ST_TX_BUSY: u8 = 1 << 2;
const ST_PRESENT: u8 = 1 << 6;

const EP_DIR_IN: u8 = 1 << 4;
const EP_TOGGLE: u8 = 1 << 5;
const EP_ZLP: u8 = 1 << 6;
const EP_DISCARD: u8 = 1 << 7;

const R_SHORT: u8 = 1 << 0;
const R_NAK_LIMIT: u8 = 1 << 1;
const R_STALL: u8 = 1 << 2;
const R_ERROR: u8 = 1 << 3;
const R_OVERRUN: u8 = 1 << 4;
const R_SEG: u8 = 1 << 5;
const R_ABORT: u8 = 1 << 6;

const AUX_MORE: u8 = 1 << 4;
const AUX_SHORT: u8 = 1 << 5;

const TYPE_BULK: u8 = 2;
const TYPE_INTERRUPT: u8 = 3;

/// Result STATUS codes (block header byte 4), `gateware/soc/datapath/header.py`.
pub const ST_OK: u8 = 0;
pub const ST_STALL: u8 = 1;
pub const ST_NAK_LIMIT: u8 = 2;
pub const ST_ERROR: u8 = 3;
pub const ST_BAD_HEADER: u8 = 4;
pub const ST_REJECTED: u8 = 5;
pub const ST_OVERRUN: u8 = 6;

/// Gateware `NAK_LIMIT_DEFAULT`; the reset value of NAK_LIMIT.
pub const NAK_LIMIT: u16 = 1000;
/// Gateware `ERR_LIMIT`: transaction errors in a row before a descriptor stops.
const ERR_LIMIT: u32 = 3;

/// One NAKed transaction, HS: IN ~1 us (60 usb cycles); OUT also re-pushes up to 512 B
/// at one byte per sync cycle (8.5 us at 60 MHz). 10 us covers both.
const NAK_US: u32 = 10;
/// One ACKed packet: HS 512 B push 8.5 + wire 8.5 + txa window gap 15 us = 32; FS 64 B:
/// txa gap 500 + wire 43 + SIE IPD 17 = 560 (`guh/sie.py` SOF window constants).
const PKT_US_HS: u32 = 35;
const PKT_US_FS: u32 = 600;
/// One errored transaction: the shim watchdog, `usb_host` XFER_US.
const ERR_US: u32 = 1_600;
/// Result send: the builder waits on the PC's read. Not a hardware bound: the longest
/// the RV waits for a PC that has stopped reading. On expiry the result stays queued.
const SEND_US: u32 = 100_000;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// No data path in this bitstream (#578), or the window does not report `present`.
    NoDataPath,
    Timeout { op: &'static str, limit_us: u32, elapsed_us: u32 },
    Host(usb_host::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {
            Error::NoDataPath => f.write_str("no data path in this bitstream (#578)"),
            Error::Timeout { op, limit_us, elapsed_us } => {
                write!(f, "timeout: {} limit {} us elapsed {} us", op, limit_us, elapsed_us)
            }
            Error::Host(e) => write!(f, "host: {}", e),
        }
    }
}

impl From<usb_host::Error> for Error {
    fn from(e: usb_host::Error) -> Self {
        Error::Host(e)
    }
}

/// One descriptor from the queue: the request header's fields.
#[derive(Clone, Copy)]
pub struct Descriptor {
    pub flags: u8,
    pub tag: u8,
    pub aux: u8,
    pub err: u8,
    pub len: u32,
}

impl Descriptor {
    pub fn ep(&self) -> u8 { self.flags & 0xf }
    pub fn is_in(&self) -> bool { self.flags & 0x10 != 0 }
    pub fn kind(&self) -> u8 { (self.flags >> 5) & 3 }
    pub fn zlp(&self) -> bool { self.flags & 0x80 != 0 }
}

/// What one served descriptor did, for the shell.
#[derive(Clone, Copy)]
pub struct Served {
    pub desc: Descriptor,
    pub status: u8,
    pub moved: u32,
    pub naks: u32,
    pub blocks: u16,
}

pub struct Status {
    pub status: u8,
    pub nak_limit: u16,
}

#[derive(Clone, Copy)]
struct Regs(usize);

impl Regs {
    fn rd(self, offset: usize) -> u8 {
        // SAFETY: byte read of a data path CSR; none of those used here has a read side
        // effect (CTL_RX, the only one, is not read by this driver).
        unsafe { read_volatile((self.0 + offset) as *const u8) }
    }

    fn wr(self, offset: usize, value: u8) {
        // SAFETY: byte write; a 32-bit store would strobe the whole word.
        unsafe { write_volatile((self.0 + offset) as *mut u8, value) }
    }

    fn get(self, base: usize, n: usize) -> u32 {
        (0..n).fold(0, |v, i| v | (self.rd(base + i) as u32) << (8 * i))
    }

    fn put(self, base: usize, value: u32, n: usize) {
        for i in 0..n {
            self.wr(base + i, (value >> (8 * i)) as u8);
        }
    }
}

fn wait_until(op: &'static str, limit_us: u32, mut done: impl FnMut() -> bool) -> Result<(), Error> {
    let started = clock::now();
    let limit = clock::micros(limit_us);
    loop {
        if done() {
            return Ok(());
        }
        let elapsed = started.elapsed(clock::now());
        if elapsed > limit {
            return Err(Error::Timeout { op, limit_us, elapsed_us: clock::to_micros(elapsed) });
        }
    }
}

fn status_of(rflags: u8) -> u8 {
    if rflags & R_STALL != 0 {
        ST_STALL
    } else if rflags & R_NAK_LIMIT != 0 {
        ST_NAK_LIMIT
    } else if rflags & (R_ERROR | R_ABORT) != 0 {
        ST_ERROR
    } else if rflags & R_OVERRUN != 0 {
        ST_OVERRUN
    } else {
        ST_OK
    }
}

pub struct DataPath {
    base: Option<usize>,
    /// Next data toggle per endpoint, bit n = endpoint n; [OUT, IN].
    toggles: [u16; 2],
    nak_limit: u16,
    fifos_cleared: bool,
}

impl DataPath {
    pub const fn new(base: Option<usize>) -> Self {
        DataPath { base, toggles: [0, 0], nak_limit: NAK_LIMIT, fifos_cleared: false }
    }

    fn regs(&self) -> Result<Regs, Error> {
        let r = self.base.map(Regs).ok_or(Error::NoDataPath)?;
        if r.rd(STATUS) & ST_PRESENT == 0 {
            return Err(Error::NoDataPath);
        }
        Ok(r)
    }

    pub fn status(&self) -> Result<Status, Error> {
        let r = self.regs()?;
        Ok(Status { status: r.rd(STATUS), nak_limit: r.get(NAK_LO, 2) as u16 })
    }

    /// NAKs in a row before a descriptor returns (0/1: the first NAK stops it).
    pub fn set_nak_limit(&mut self, limit: u16) -> Result<(), Error> {
        let r = self.regs()?;
        r.put(NAK_LO, limit as u32, 2);
        self.nak_limit = limit;
        Ok(())
    }

    /// The CPU pump used the engine: clear its FIFOs again before the next arm.
    pub fn pump_used(&mut self) {
        self.fifos_cleared = false;
    }

    /// Forget the toggles too: after a bus reset, a role change or a new device.
    pub fn forget_bus(&mut self) {
        self.toggles = [0, 0];
        self.fifos_cleared = false;
    }

    /// Worst case for one arm of `len` bytes at the programmed NAK limit, x1.25.
    fn arm_limit_us(&self, len: u32, mps: u32, high_speed: bool) -> u32 {
        let packets = len / mps + 1;
        let pkt = if high_speed { PKT_US_HS } else { PKT_US_FS };
        let us = packets * (self.nak_limit as u32 * NAK_US + pkt) + ERR_LIMIT * ERR_US;
        us.saturating_add(us / 4)
    }

    fn arm(&self, r: Regs, addr: u8, ep_bits: u8, mps: u32, len: u32, high_speed: bool)
           -> Result<(u8, u8, u32, u32), Error> {
        r.wr(ADDR, addr);
        r.wr(EP, ep_bits);
        r.put(MPS_LO, mps, 2);
        r.put(LEN0, len, 3);
        r.wr(EVENTS, 0x3);
        r.wr(CTRL, CTRL_ARM);
        let limit = if ep_bits & EP_DISCARD != 0 {
            // Discard waits only for the PC's remaining payload, one byte per sync cycle.
            SEND_US
        } else {
            self.arm_limit_us(len, mps, high_speed)
        };
        wait_until("data path descriptor", limit, || r.rd(STATUS) & ST_SEQ_BUSY == 0)?;
        Ok((r.rd(R_RESP), r.rd(R_FLAGS), r.get(R_MOVED0, 3), r.get(R_NAKS_LO, 2)))
    }

    fn send(&self, r: Regs, d: &Descriptor, aux: u8, status: u8, len: u32) -> Result<(), Error> {
        wait_until("result send", SEND_US, || r.rd(STATUS) & ST_TX_BUSY == 0)?;
        r.wr(H_FLAGS, d.flags);
        r.wr(H_TAG, d.tag);
        r.wr(H_AUX, aux);
        r.wr(H_STATUS, status);
        r.put(H_LEN0, len, 3);
        r.wr(CTRL, CTRL_SEND);
        Ok(())
    }

    /// A descriptor at the head of the queue, popped; `None` when the queue is empty.
    fn pop(&self, r: Regs) -> Option<Descriptor> {
        if r.rd(STATUS) & ST_DESC == 0 {
            return None;
        }
        let d = Descriptor { flags: r.rd(D_FLAGS), tag: r.rd(D_TAG), aux: r.rd(D_AUX),
                             err: r.rd(D_ERR), len: r.get(D_LEN0, 3) };
        r.wr(CTRL, CTRL_POP);
        Some(d)
    }

    /// Serve one descriptor if one is queued: arm, wait, answer (`docs/data-path.md`).
    pub fn serve_one(&mut self, host: &Host) -> Result<Option<Served>, Error> {
        let r = self.regs()?;
        let Some(d) = self.pop(r) else { return Ok(None) };
        let mut served = Served { desc: d, status: ST_OK, moved: 0, naks: 0, blocks: 1 };
        let codec = d.aux & 0xf;
        if d.err != 0 {
            served.status = ST_BAD_HEADER;
            self.send(r, &d, codec, ST_BAD_HEADER, 0)?;
            return Ok(Some(served));
        }
        let (high_speed, addr) = (host.high_speed()?, host.addr()?);
        let mps = if high_speed { 512 } else { 64 };
        if codec != 0 || (d.kind() != TYPE_BULK && d.kind() != TYPE_INTERRUPT) {
            if !d.is_in() && d.len != 0 {
                self.arm(r, addr, d.ep() | EP_DISCARD, mps, d.len, high_speed)?;
            }
            served.status = ST_REJECTED;
            self.send(r, &d, codec, ST_REJECTED, 0)?;
            return Ok(Some(served));
        }
        if !self.fifos_cleared {
            // The engine's RX FIFO must not hold bytes left by the CPU pump.
            host.clear_fifos()?;
            self.fifos_cleared = true;
        }
        let dir = d.is_in() as usize;
        let bit = 1u16 << d.ep();
        let mut left = d.len;
        loop {
            let toggle = self.toggles[dir] & bit != 0;
            let bits = d.ep() | if d.is_in() { EP_DIR_IN } else { 0 }
                | if toggle { EP_TOGGLE } else { 0 } | if d.zlp() { EP_ZLP } else { 0 };
            let (resp, rflags, moved, naks) = self.arm(r, addr, bits, mps, left, high_speed)?;
            if resp & 8 != 0 { self.toggles[dir] |= bit } else { self.toggles[dir] &= !bit }
            let status = status_of(rflags);
            let short = rflags & R_SHORT != 0;
            let more = d.is_in() && status == ST_OK && rflags & R_SEG != 0 && !short && moved < left;
            let aux = codec | if more { AUX_MORE } else { 0 } | if short { AUX_SHORT } else { 0 };
            self.send(r, &d, aux, status, moved)?;
            served.moved += moved;
            served.naks += naks;
            served.status = status;
            if !d.is_in() && status != ST_OK && moved < left {
                self.arm(r, addr, d.ep() | EP_DISCARD, mps, left - moved, high_speed)?;
            }
            if !more {
                return Ok(Some(served));
            }
            left -= moved;
            served.blocks += 1;
        }
    }

    /// Serve descriptors until none arrives for `idle_us`, or `max` are done.
    pub fn serve(&mut self, host: &Host, max: u32, idle_us: u32,
                 mut each: impl FnMut(&Served)) -> Result<u32, Error> {
        let r = self.regs()?;
        r.wr(IRQ_EN, 0);
        let mut done = 0;
        while done < max {
            match wait_until("next block", idle_us, || r.rd(STATUS) & ST_DESC != 0) {
                Ok(()) => {}
                Err(Error::Timeout { .. }) => break,
                Err(e) => return Err(e),
            }
            if let Some(s) = self.serve_one(host)? {
                each(&s);
                done += 1;
            }
        }
        Ok(done)
    }
}
