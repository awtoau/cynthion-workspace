//! The config flash command port (#593): JEDEC, status, write enable, erase, program,
//! read, calibrate, reset. Register map, codes and bounds:
//! `docs/chips/flash-soc-peripheral.md`.
//!
//! - Data moves through the peripheral's 4 KiB buffer (`FLASH_BUF`), not the XIP window.
//! - While a command is pending the peripheral answers XIP with ERR, and `.text` is in
//!   the XIP window: [`run`] is `.data`-resident ([`resident`] checks) and runs with
//!   interrupts masked. Operands and the buffer are written before it, from anywhere.
//! - Erase and program are refused outside `TEST_LO..TEST_HI` twice: here, and by the
//!   gateware's WRITE_LO/WRITE_HI, which [`Flash::take`] sets to the same window.

use core::ptr::{read_volatile, write_volatile};

use crate::target;

/// The #590 test region: 1 MiB, blank when found, clear of every used sector.
pub const TEST_LO: u32 = 0x0028_0000;
pub const TEST_HI: u32 = 0x0038_0000;
pub const SECTOR: u32 = 4096;
pub const BLOCK: u32 = 65536;
pub const PAGE: u32 = 256;
pub const BUF: u32 = 4096;

pub const OP_READ: u8 = 1;
pub const OP_PROGRAM: u8 = 2;
pub const OP_ERASE_4K: u8 = 3;
pub const OP_ERASE_64K: u8 = 4;
pub const OP_JEDEC: u8 = 5;
pub const OP_STATUS: u8 = 6;
pub const OP_WREN: u8 = 7;
pub const OP_CALIBRATE: u8 = 8;
pub const OP_RESET: u8 = 9;

const STATUS_BUSY: u16 = 1 << 0;

/// Gateware codes, STATUS[11:8].
pub const CODE_NAMES: [&str; 14] = [
    "ok", "busy", "arg", "not-ready", "uncalibrated", "protect", "wel", "watchdog",
    "handshake", "not-started", "cal-none", "abort", "cal-data", "cal-verify",
];

/// Firmware poll bounds, `sync` ticks, 1.25x each (datasheet maxima, rev G 9.6, plus the
/// gateware's own 1.25x watchdog margin and 64 us of handshake/transaction slack):
/// tBE2 2 s, tSE 400 ms, tPP 3 ms; READ 4 KiB = 43 us at SCK 190; CALIBRATE 2 x 512
/// probes ~1.4 ms; RESET tRST 30 us.
fn ticks(op: u8) -> u32 {
    let us: u32 = match op {
        OP_ERASE_64K => 5 * (5 * 2_000_000 / 4 + 64) / 4,
        OP_ERASE_4K => 5 * (5 * 400_000 / 4 + 64) / 4,
        OP_PROGRAM => 5 * (5 * 3_000 / 4 + 64) / 4,
        OP_CALIBRATE => 5 * (1_400 + 64) / 4,
        _ => 5 * (43 + 64) / 4,
    };
    us.saturating_mul(target::TIME_HZ / 1_000_000)
}

/// Write CMD, then poll STATUS until not busy. Nothing here may fetch from flash: no
/// calls, no formatting. Returns STATUS, or 0xffff on expiry.
///
/// # Safety
/// `base` is the FLASH CSR window.
#[inline(never)]
#[unsafe(link_section = ".data.flash_run")]
unsafe fn run(base: usize, op: u8, limit: u32) -> u16 {
    let status = (base + cynthion_soc_pac::flash::offset::STATUS) as *const u8;
    let cmd = (base + cynthion_soc_pac::flash::offset::CMD) as *mut u8;
    // A macro, not a closure: a closure may be outlined into `.text`.
    macro_rules! now {
        () => {{
            let t: u32;
            // SAFETY: `rdtime`, no side effect.
            #[allow(unused_unsafe)]
            unsafe { core::arch::asm!("rdtime {0}", out(reg) t, options(nomem, nostack)) };
            t
        }};
    }
    unsafe {
        write_volatile(cmd, op);
        let start = now!();
        loop {
            let lo = read_volatile(status) as u16;
            let s = lo | (read_volatile(status.add(1)) as u16) << 8;
            if s & STATUS_BUSY == 0 {
                return s;
            }
            if now!().wrapping_sub(start) > limit {
                return 0xffff;
            }
        }
    }
}

/// [`run`] in the XIP window would fetch from the part it is waiting on.
fn resident() -> bool {
    match target::FLASH_WINDOW {
        Some((base, size)) => (run as *const () as usize).wrapping_sub(base) >= size,
        None => true,
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// A gateware code other than ok.
    Code(u8),
    /// The poll ran out (`ticks`).
    Timeout,
    /// Erase/program outside the test region, or a bad length.
    Outside,
    /// `run` linked into the flash window.
    NotResident,
}

impl core::fmt::Display for Error {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Error::Code(c) => write!(f, "code {} {}", c,
                                     CODE_NAMES.get(*c as usize).unwrap_or(&"?")),
            Error::Timeout => write!(f, "no answer within the poll bound"),
            Error::Outside => write!(f, "outside {:06x}..{:06x}, or a bad length",
                                     TEST_LO, TEST_HI),
            Error::NotResident => write!(f, "the poll loop is in the flash window"),
        }
    }
}

#[derive(Clone, Copy)]
pub struct Flash {
    base: usize,
    buf: usize,
}

impl Flash {
    /// `None` where there is no such peripheral (QEMU). Sets the gateware write window.
    pub fn take() -> Option<Flash> {
        let (base, buf) = target::FLASH_PORT?;
        let f = Flash { base, buf };
        // 4 KiB units, 2-byte footprints
        f.w16(cynthion_soc_pac::flash::offset::WRITE_LO, (TEST_LO >> 12) as u16);
        f.w16(cynthion_soc_pac::flash::offset::WRITE_HI, (TEST_HI >> 12) as u16);
        Some(f)
    }

    fn w8(&self, off: usize, v: u8) {
        // SAFETY: a byte inside the generated FLASH CSR window.
        unsafe { write_volatile((self.base + off) as *mut u8, v) }
    }

    fn r8(&self, off: usize) -> u8 {
        // SAFETY: as `w8`.
        unsafe { read_volatile((self.base + off) as *const u8) }
    }

    fn w16(&self, off: usize, v: u16) {
        self.w8(off, v as u8);
        self.w8(off + 1, (v >> 8) as u8);
    }

    /// Low byte first; amaranth-soc commits on the footprint's last byte (4 here).
    fn w32(&self, off: usize, v: u32) {
        for i in 0..4 {
            self.w8(off + i, (v >> (8 * i)) as u8);
        }
    }

    pub fn r32(&self, off: usize) -> u32 {
        (0..4).fold(0, |acc, i| acc | (self.r8(off + i) as u32) << (8 * i))
    }

    pub fn status(&self) -> u16 {
        self.r8(cynthion_soc_pac::flash::offset::STATUS) as u16
            | (self.r8(cynthion_soc_pac::flash::offset::STATUS + 1) as u16) << 8
    }

    /// Sticky errors, and clear them.
    pub fn take_errors(&self) -> u16 {
        let off = cynthion_soc_pac::flash::offset::ERRORS;
        let e = self.r8(off) as u16 | (self.r8(off + 1) as u16) << 8;
        self.w8(off, 0xff);
        self.w8(off + 1, 0xff);
        e
    }

    pub fn buf_put(&self, off: u32, data: &[u8]) {
        for (i, b) in data.iter().enumerate() {
            // SAFETY: inside the 4 KiB FLASH_BUF window; callers keep off + len <= BUF.
            unsafe { write_volatile((self.buf + off as usize + i) as *mut u8, *b) }
        }
    }

    pub fn buf_get(&self, off: u32, out: &mut [u8]) {
        for (i, b) in out.iter_mut().enumerate() {
            // SAFETY: as `buf_put`.
            *b = unsafe { read_volatile((self.buf + off as usize + i) as *const u8) };
        }
    }

    /// One command with the operands already written; the RESULT register on success.
    pub fn command(&self, op: u8) -> Result<u32, Error> {
        if !resident() {
            return Err(Error::NotResident);
        }
        let limit = ticks(op);
        // SAFETY: the generated window; masked so no handler fetches from flash meanwhile.
        let s = riscv::interrupt::free(|| unsafe { run(self.base, op, limit) });
        if s == 0xffff {
            return Err(Error::Timeout);
        }
        match ((s >> 8) & 0xf) as u8 {
            0 => Ok(self.r32(cynthion_soc_pac::flash::offset::RESULT)),
            c => Err(Error::Code(c)),
        }
    }

    fn operands(&self, addr: u32, buf: u32, len: u32) {
        self.w32(cynthion_soc_pac::flash::offset::ADDR, addr);
        // 2-byte footprints: BUF_ADDR at +0x18, LEN at +0x1a
        self.w16(cynthion_soc_pac::flash::offset::BUF_ADDR, buf as u16);
        self.w16(cynthion_soc_pac::flash::offset::LEN, len as u16);
    }

    /// `ef4016` on this board.
    pub fn jedec_id(&self) -> Result<u32, Error> {
        self.command(OP_JEDEC).map(|r| r & 0xff_ffff)
    }

    /// `SR3 << 16 | SR2 << 8 | SR1`.
    pub fn status_regs(&self) -> Result<u32, Error> {
        self.command(OP_STATUS)
    }

    pub fn write_enable(&self) -> Result<(), Error> {
        self.command(OP_WREN).map(|_| ())
    }

    pub fn calibrate(&self) -> Result<(), Error> {
        self.command(OP_CALIBRATE).map(|_| ())
    }

    pub fn reset(&self) -> Result<(), Error> {
        self.command(OP_RESET).map(|_| ())
    }

    /// Erase the 4 KiB sector (or 64 KiB block) at `addr`; BUSY flash cycles.
    pub fn erase(&self, addr: u32, block: bool) -> Result<u32, Error> {
        let size = if block { BLOCK } else { SECTOR };
        let base = addr & !(size - 1);
        if base < TEST_LO || base + size > TEST_HI {
            return Err(Error::Outside);
        }
        self.operands(addr, 0, 0);
        self.command(if block { OP_ERASE_64K } else { OP_ERASE_4K })?;
        Ok(self.r32(cynthion_soc_pac::flash::offset::BUSY_CYCLES))
    }

    /// Program `data` (<= 256 B, one page) at `addr`; BUSY flash cycles.
    pub fn program(&self, addr: u32, data: &[u8]) -> Result<u32, Error> {
        let n = data.len() as u32;
        if n == 0 || n > PAGE || addr % PAGE + n > PAGE || addr < TEST_LO || addr + n > TEST_HI {
            return Err(Error::Outside);
        }
        self.buf_put(0, data);
        self.operands(addr, 0, n);
        self.command(OP_PROGRAM)?;
        Ok(self.r32(cynthion_soc_pac::flash::offset::BUSY_CYCLES))
    }

    /// Read `out.len()` (<= 4096) bytes at `addr` over the command port (EBh), so a
    /// readback owes nothing to the CPU caches.
    pub fn read(&self, addr: u32, out: &mut [u8]) -> Result<(), Error> {
        let n = out.len() as u32;
        if n == 0 || n > BUF {
            return Err(Error::Outside);
        }
        self.operands(addr, 0, n);
        self.command(OP_READ)?;
        self.buf_get(0, out);
        Ok(())
    }
}

/// Microseconds from flash-clock cycles (SCK 190 MHz).
pub fn sck_us(cycles: u32) -> u32 {
    cycles / 190
}
