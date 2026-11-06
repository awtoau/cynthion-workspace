//! The config-flash COMMAND PORT: every opcode the XIP window does not issue (#593).
//!
//! - Peripheral: `gateware/soc/peripherals/flash.py`; map, codes, errors:
//!   `docs/chips/flash-soc-peripheral.md`.
//! - One command = optional WREN, one /CS-low transaction (descriptor, address, up to
//!   256 bytes through `SPI0_BUF`), optional BUSY poll bounded by `wait_us`.
//! - Buffer bytes are little-endian words: byte 0 is bits 7..0 of word 0, the same
//!   order the XIP window reads, so a programmed word reads back unchanged.
//! - `.text` executes from this flash, and while a write-class command runs XIP
//!   answers ERR. [`run`] is `.data`-resident, interrupts masked; [`resident`]
//!   refuses if it is not (#460, #463).

use core::ptr::{read_volatile, write_volatile};

use crate::target;

/// Register byte offsets in the `spi0` window: 32-bit words, `REG_*` in flash.py.
mod offset {
    pub const STATUS: usize = 0x00;
    pub const CMD: usize = 0x04;
    pub const DESC: usize = 0x10;
    pub const ADDR: usize = 0x14;
    pub const LEN: usize = 0x18;
    pub const WAIT_US: usize = 0x1c;
    pub const POLL_US: usize = 0x20;
    pub const CAL: usize = 0x24;
    pub const CAL_US: usize = 0x28;
}

const OP_RUN: u32 = 1;
const OP_CALIBRATE: u32 = 2;
/// In the `len` word, above the 9-bit byte count.
const FLAG_WREN: u32 = 1 << 16;
const FLAG_WAIT: u32 = 1 << 17;

const STATUS_BUSY: u32 = 1 << 0;
pub const STATUS_CAL: u32 = 1 << 3;
pub const STATUS_X4: u32 = 1 << 4;
const STATUS_SAW_BUSY: u32 = 1 << 5;

const W1: u32 = 0;

/// A transaction descriptor, as `desc()` in `gateware/flash_baseline/spiflash.py`.
const fn desc(cmd: u32, abits: u32, dummy: u32, read: bool, tail: u32) -> u32 {
    cmd | 1 << 8 | abits << 10 | W1 << 16 | dummy << 18 | W1 << 23 | (read as u32) << 25
        | tail << 28
}

const JEDEC: u32 = desc(0x9f, 0, 0, true, 0);
const READ_STATUS1: u32 = desc(0x05, 0, 0, true, 0);
const SFDP: u32 = desc(0x5a, 24, 8, true, 0);
const FAST_READ: u32 = desc(0x0b, 24, 8, true, 1);
const PAGE_PROGRAM: u32 = desc(0x02, 24, 0, false, 0);
/// 4 KiB. There is deliberately no block or chip erase in this driver.
const SECTOR_ERASE: u32 = desc(0x20, 24, 0, false, 0);

/// Status register 1: WIP, and the write-enable latch.
pub const SR1_BUSY: u8 = 0x01;
pub const SR1_WEL: u8 = 0x02;

pub const SECTOR_SIZE: u32 = 4096;
pub const PAGE_SIZE: u32 = 256;
pub const MAX_PROGRAM_WORDS: usize = 64;

/// The 4 KiB sector erase and program may touch, and nothing else: the first of the
/// #590 test region (0x280000-0x37ffff, docs/chips/flash-baseline.md).
pub const SCRATCH: u32 = 0x0028_0000;

/// BUSY bound for a sector erase: tSE max 400 ms, x1.25. Expiry: the gateware's WDOG.
const ERASE_WAIT_US: u32 = 500_000;
/// The same for a page program: tPP max 3 ms, x1.25.
const PROGRAM_WAIT_US: u32 = 3_750;
/// A command with no poll: three maximal transactions at SCK >= 90 MHz < 100 us.
const SHORT_US: u32 = 100;

/// An erase this fast erased nothing: tSE is 45 ms typical, 1 ms is 45x under it.
pub const ERASE_FLOOR_US: u32 = 1000;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// The peripheral refused or ended the command with this code (see the doc).
    Code(u8),
    /// The status never went idle within the CPU's own bound: the fsck side stopped.
    Timeout,
    /// An erase or program address outside [`SCRATCH`].
    OutsideScratch,
    /// A page program that would wrap onto its own start.
    CrossesPage,
    /// The command loop linked into the flash window it drives.
    NotResident,
    /// WIP never rose: the part did not take the erase or program.
    NeverStarted,
}

impl core::fmt::Display for Error {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Error::Code(code) => write!(f, "{}", code_name(*code)),
            Error::Timeout => write!(f, "the peripheral never went idle"),
            Error::OutsideScratch => write!(
                f, "outside the reserved sector at {:06x}", SCRATCH),
            Error::CrossesPage => write!(f, "a page program cannot cross a page boundary"),
            Error::NotResident => write!(
                f, "the command loop is in the flash window; it must be in block RAM"),
            Error::NeverStarted => write!(f, "WIP never rose; the part did not take the command"),
        }
    }
}

fn code_name(code: u8) -> &'static str {
    match code {
        1 => "BUSY: a command is in flight",
        2 => "ARG: bad op, length or wait",
        3 => "NOT_READY: not calibrated or not locked",
        7 => "WDOG: a transaction or the BUSY poll ran past its bound",
        11 => "ABORT",
        13 => "CAL_NONE: no clean capture point",
        _ => "unknown code",
    }
}

/// One command, start to idle, from block RAM with interrupts masked.
///
/// - No calls out: a call is a fetch from `.text`, which is the flash, which answers
///   ERR while a write-class command runs. Word stores, loads and `mcycle` only.
/// - `limit` in `mcycle`s; returns the final status word, or `Err(Timeout)`.
///
/// # Safety
/// `base` must be the SPI0 register window.
#[inline(never)]
#[unsafe(link_section = ".data.flash_run")]
unsafe fn run(base: usize, d: u32, addr: u32, len_flags: u32, wait_us: u32, op: u32,
              limit: u32) -> Result<u32, ()> {
    macro_rules! w32 {
        ($off:expr, $v:expr) => {
            unsafe { write_volatile((base + $off) as *mut u32, $v) }
        };
    }
    macro_rules! cycles {
        () => {{
            let value: u32;
            // SAFETY: a read of an implemented, side-effect-free counter.
            #[allow(unused_unsafe)]
            unsafe {
                core::arch::asm!("csrr {0}, mcycle", out(reg) value, options(nomem, nostack));
            }
            value
        }};
    }
    w32!(offset::DESC, d);
    w32!(offset::ADDR, addr);
    w32!(offset::LEN, len_flags);
    w32!(offset::WAIT_US, wait_us);
    w32!(offset::CMD, op);
    let started = cycles!();
    loop {
        let status = unsafe { read_volatile((base + offset::STATUS) as *const u32) };
        if status & STATUS_BUSY == 0 {
            // A speculative fetch into the window during the command got ERR: drop
            // whatever the I-cache kept of it before returning into the window.
            // SAFETY: an instruction-stream fence; no memory operands.
            unsafe { core::arch::asm!("fence.i", options(nostack)) };
            return Ok(status);
        }
        if cycles!().wrapping_sub(started) > limit {
            return Err(());
        }
    }
}

/// The one handle on the command port.
#[derive(Clone, Copy)]
pub struct Flash {
    base: usize,
    buf: usize,
}

impl Flash {
    /// `None` where the target has no such peripheral -- QEMU's `virt`.
    pub fn take() -> Option<Flash> {
        match (target::SPI0_BASE, target::SPI0_BUF_BASE) {
            (Some(base), Some(buf)) => Some(Flash { base, buf }),
            _ => None,
        }
    }

    fn r32(&self, off: usize) -> u32 {
        // SAFETY: a word register inside the generated SPI0 window.
        unsafe { read_volatile((self.base + off) as *const u32) }
    }

    /// Buffer word `i` (received bytes), little-endian.
    fn buf_word(&self, i: usize) -> u32 {
        // SAFETY: inside the 256 B SPI0_BUF window; callers keep i < 64.
        unsafe { read_volatile((self.buf + 4 * i) as *const u32) }
    }

    fn set_buf_word(&self, i: usize, value: u32) {
        // SAFETY: as `buf_word`; a write lands in the bytes to send.
        unsafe { write_volatile((self.buf + 4 * i) as *mut u32, value) }
    }

    /// Run one command; Ok carries the status (saw_busy etc.).
    fn command(&self, d: u32, addr: u32, len: u32, flags: u32, wait_us: u32, op: u32)
               -> Result<u32, Error> {
        resident()?;
        let us = wait_us.max(SHORT_US);
        // CPU bound: wait x 1.25 + 128 us (three maximal transactions and the crossings).
        let limit = (us + us / 4 + 128).saturating_mul(target::TIME_HZ / 1_000_000);
        // SAFETY: `base` came from the generated memory map.
        let status = riscv::interrupt::free(|| unsafe {
            run(self.base, d, addr, len | flags, wait_us, op, limit)
        })
        .map_err(|()| Error::Timeout)?;
        match ((status >> 8) & 0xf) as u8 {
            0 => Ok(status),
            code => Err(Error::Code(code)),
        }
    }

    /// The JEDEC id: `0x9f`, then three bytes. `ef4016` on this board.
    pub fn jedec_id(&self) -> Result<u32, Error> {
        self.command(JEDEC, 0, 3, 0, 0, OP_RUN)?;
        let w = self.buf_word(0);
        Ok((w & 0xff) << 16 | (w & 0xff00) | (w >> 16) & 0xff)
    }

    /// Status register 1. Bit 0 is WIP, bit 1 the write-enable latch.
    pub fn status1(&self) -> Result<u8, Error> {
        self.command(READ_STATUS1, 0, 1, 0, 0, OP_RUN)?;
        Ok(self.buf_word(0) as u8)
    }

    /// The first eight bytes of the SFDP space: `0x5a`, a 24-bit address, 8 dummy clocks.
    pub fn sfdp(&self, offset: u32, out: &mut [u8; 8]) -> Result<(), Error> {
        self.command(SFDP, offset & 0x00ff_ffff, 8, 0, 0, OP_RUN)?;
        let words = [self.buf_word(0), self.buf_word(1)];
        for (i, byte) in out.iter_mut().enumerate() {
            *byte = (words[i / 4] >> (8 * (i % 4))) as u8;
        }
        Ok(())
    }

    /// One 32-bit word at `offset` over the command port (0Bh), not XIP, so a
    /// readback owes nothing to the caches. Same byte order as the window.
    pub fn read_word(&self, offset: u32) -> Result<u32, Error> {
        self.command(FAST_READ, offset & 0x00ff_ffff, 4, 0, 0, OP_RUN)?;
        Ok(self.buf_word(0))
    }

    /// Erase the 4 KiB sector containing `offset`. Returns the BUSY time in us.
    pub fn sector_erase(&self, offset: u32) -> Result<u32, Error> {
        scratch_only(offset)?;
        let status = self.command(SECTOR_ERASE, offset & 0x00ff_ffff, 0,
                                  FLAG_WREN | FLAG_WAIT, ERASE_WAIT_US, OP_RUN)?;
        self.busy_time(status)
    }

    /// Program up to one 256-byte page. Returns the BUSY time in us.
    pub fn page_program(&self, offset: u32, words: &[u32]) -> Result<u32, Error> {
        scratch_only(offset)?;
        if words.len() > MAX_PROGRAM_WORDS
            || offset % PAGE_SIZE + words.len() as u32 * 4 > PAGE_SIZE
        {
            return Err(Error::CrossesPage);
        }
        for (i, word) in words.iter().enumerate() {
            self.set_buf_word(i, *word);
        }
        let status = self.command(PAGE_PROGRAM, offset & 0x00ff_ffff, words.len() as u32 * 4,
                                  FLAG_WREN | FLAG_WAIT, PROGRAM_WAIT_US, OP_RUN)?;
        self.busy_time(status)
    }

    fn busy_time(&self, status: u32) -> Result<u32, Error> {
        if status & STATUS_SAW_BUSY == 0 {
            return Err(Error::NeverStarted);
        }
        Ok(self.r32(offset::POLL_US))
    }

    /// Rerun the capture calibration (XIP answers ERR while it runs). Bounded by the
    /// scan's own length, which the gateware reports (`cal_us`).
    pub fn calibrate(&self) -> Result<(), Error> {
        let bound = self.r32(offset::CAL_US);
        self.command(0, 0, 0, 0, bound, OP_CALIBRATE).map(|_| ())
    }

    /// (status, cal register, sticky errors): the calibration and health readout.
    pub fn health(&self) -> (u32, u32, u8) {
        let status = self.r32(offset::STATUS);
        (status, self.r32(offset::CAL), (status >> 24) as u8)
    }
}

/// [`run`] is in the flash window it drives: a refusal, not a warning.
fn resident() -> Result<(), Error> {
    match target::FLASH_WINDOW {
        Some((base, size)) if (run as *const () as usize).wrapping_sub(base) < size => {
            Err(Error::NotResident)
        }
        _ => Ok(()),
    }
}

/// Erase and program reach one sector and no other address.
fn scratch_only(offset: u32) -> Result<(), Error> {
    if !(SCRATCH..SCRATCH + SECTOR_SIZE).contains(&offset) {
        return Err(Error::OutsideScratch);
    }
    Ok(())
}
