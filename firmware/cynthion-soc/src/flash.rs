//! The SPI flash CONTROLLER: every opcode the memory map cannot issue.
//!
//! Two paths reach the W25Q32 and they do different things:
//!
//! | path | opcodes | driver |
//! |------|---------|--------|
//! | `ModalSPIFlashMemoryMap` at `SPIFLASH` | one read opcode, no write in its FSM (`gateware/soc/top.py:534`) | `target::flash_word` |
//! | `HoldableSPIController` at `SPI0` | any | this file |
//!
//! So JEDEC, SFDP, the status register, erase and page program are only
//! reachable from here. Ported from the C bring-up firmware that found the
//! path's faults -- `scripts/riscv_firmware.py`, which keeps the ILA evidence.
//!
//! ## Register access (`amaranth_soc.csr`, granularity 8)
//!
//!     0x00  phy     length[5:0] | width[9:6] | mask[17:10]   32-bit write
//!     0x04  cs      a write PULSE; cannot hold chip select. Unused here
//!     0x05  status  rx_ready[0], tx_ready[1]
//!     0x08  data.rx 32-bit read -- READING POPS THE RX FIFO
//!     0x0c  data.tx 32-bit write
//!     0x20  hold    LATCHING chip select, `HoldableSPIController`
//!
//! A multi-byte register latches its shadow on its LOWEST byte and commits on
//! its HIGHEST, so each access above is one aligned 32-bit or byte access and
//! not a sequence. `data.rx` has a side effect on read, so `SPI0` must stay in
//! an uncached PMA region.
//!
//! ## Two gotchas that cost days in the C firmware
//!
//! - **Data is right-aligned, not pre-shifted.** The PHY left-justifies:
//!   `sr_out.eq(source.data << (32 - source.len))`. An 8-bit transfer of `0x9f`
//!   becomes `0x9f000000` on its own; passing `0x9f << 24` shifts it a further
//!   24 and clocks out eight zero bits. 32-bit transfers are unaffected, which
//!   is why the address-bearing commands here do pack the opcode into 31..24.
//! - **Chip select must be HELD across a multi-transfer command.** Upstream's
//!   `cs` register is a write pulse and its CS collapses to TX FIFO occupancy,
//!   which empties whenever the CPU is slower than the PHY -- always. `hold` is
//!   the latch that fixes it; with it asserted, push/pop one transfer at a time
//!   is safe and FIFO depth stops being a constraint.
//!
//! ## Interrupts are masked for the length of a command
//!
//! `.text` and `.rodata` are in flash (`memory.x`), so a handler running inside
//! a held-CS window fetches through the memory map, the crossbar re-grants, and
//! `controller.cs` follows the grant away from us -- the command is cut in half
//! and the answer is garbage. Masking removes the interrupt half of that; the
//! I-cache miss half is #456.

use core::ptr::{read_volatile, write_volatile};

use crate::metrics;
use crate::target;

/// Register offsets in the `spi0` CSR window. See the table above.
const PHY: usize = 0x00;
const STATUS: usize = 0x05;
const RX: usize = 0x08;
const TX: usize = 0x0c;
const HOLD: usize = 0x20;

const STATUS_RX_READY: u8 = 1 << 0;
const STATUS_TX_READY: u8 = 1 << 1;

const CMD_READ_DATA: u32 = 0x03;
const CMD_JEDEC_ID: u32 = 0x9f;
const CMD_SFDP: u32 = 0x5a;
const CMD_READ_STATUS1: u32 = 0x05;
const CMD_WRITE_ENABLE: u32 = 0x06;
const CMD_PAGE_PROGRAM: u32 = 0x02;
/// 4 KiB. There is deliberately no block or chip erase in this driver.
const CMD_SECTOR_ERASE: u32 = 0x20;

/// Status register 1: WIP, and the write-enable latch.
pub const SR1_BUSY: u8 = 0x01;
pub const SR1_WEL: u8 = 0x02;

pub const SECTOR_SIZE: u32 = 4096;
pub const PAGE_SIZE: u32 = 256;

/// The 4 KiB sector erase and program may touch, and nothing else.
///
/// 2 MiB in: clear of the bitstream at offset 0 and of the image at 0xb0000,
/// and well below 4 MiB, where the address wraps back onto the bitstream while
/// appearing to be nowhere near it. Same sector as `scripts/riscv_firmware.py`.
pub const SCRATCH: u32 = 0x0020_0000;

/// Waiting on the TX or RX FIFO.
///
/// One 32-bit transfer behind a full 16-deep FIFO is 17 x 32 bits at
/// SCK = sync/2 (`FLASH_DIVISOR = 0`), i.e. 1088 `sync` cycles. 1.25x. On
/// expiry the command gives up and names the side that stalled.
const FIFO_LIMIT_CYCLES: u32 = 1360;

/// Waiting for WIP to clear after a sector erase. W25Q32 tSE(max) is 400 ms;
/// 1.25x. On expiry `Error::NeverIdle` carries the elapsed cycles.
const ERASE_LIMIT_CYCLES: u32 = target::TIME_HZ / 2;

/// The same, for a page program. tPP(max) 3 ms, 1.25x.
const PROGRAM_LIMIT_CYCLES: u32 = target::TIME_HZ / 256;

/// A sector erase this fast did not erase anything: tSE is 45 ms typical, so
/// 1 ms is 45x under the typical case. The C firmware saw exactly this -- an
/// erase "completing" 14,000 times too fast -- while the command path was
/// broken, so the check is the first thing a caller should report.
pub const ERASE_FLOOR_US: u32 = 1000;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// The TX FIFO never made room: the PHY is not draining it.
    TxStalled,
    /// No RX word came back: a transfer was accepted and never completed.
    RxStalled,
    /// WIP never cleared, with the cycles waited.
    NeverIdle(u32),
    /// Write Enable did not latch, with the status byte that says so.
    NotEnabled(u8),
    /// An erase or program address outside [`SCRATCH`].
    OutsideScratch,
    /// A page program that would wrap onto its own start.
    CrossesPage,
}

impl core::fmt::Display for Error {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Error::TxStalled => write!(f, "TX FIFO never drained"),
            Error::RxStalled => write!(f, "no RX word came back"),
            Error::NeverIdle(cycles) => {
                write!(f, "still busy after {} us", cycles / (target::TIME_HZ / 1_000_000))
            }
            Error::NotEnabled(sr1) => write!(f, "write enable did not latch (sr1 {:02x})", sr1),
            Error::OutsideScratch => write!(
                f, "outside the reserved sector at {:06x}", SCRATCH),
            Error::CrossesPage => write!(f, "a page program cannot cross a page boundary"),
        }
    }
}

/// The one handle on the controller.
#[derive(Clone, Copy)]
pub struct Flash {
    base: usize,
}

impl Flash {
    /// `None` where the target has no such peripheral -- QEMU's `virt`.
    pub fn take() -> Option<Flash> {
        target::SPI0_BASE.map(|base| Flash { base })
    }

    fn reg8(&self, offset: usize) -> *mut u8 {
        (self.base + offset) as *mut u8
    }

    fn reg32(&self, offset: usize) -> *mut u32 {
        (self.base + offset) as *mut u32
    }

    /// Transfer length in bits, bus width in lanes, DQ output-enable mask.
    /// `mask = 1` drives DQ0 (send); `mask = 0` releases every DQ so the flash
    /// can drive them (receive).
    fn phy_set(&self, len: u32, mask: u32) {
        // SAFETY: one 32-bit write of a read/write CSR. The register's high
        // byte is inside this access and is what commits it.
        unsafe {
            write_volatile(self.reg32(PHY), (len & 0x3f) | (1 << 6) | ((mask & 0xff) << 10));
        }
    }

    /// Chip select, latched. Assert before a multi-transfer command.
    fn hold(&self, on: bool) {
        // SAFETY: a byte write of a read/write CSR.
        unsafe { write_volatile(self.reg8(HOLD), on as u8) }
    }

    fn status(&self) -> u8 {
        // SAFETY: a byte read of a read-only CSR, no side effect.
        unsafe { read_volatile(self.reg8(STATUS)) }
    }

    /// One transfer out and its answer back. Every transfer produces an RX
    /// word, whether or not the data mattered, so this is the only shape.
    fn xfer(&self, len: u32, mask: u32, data: u32) -> Result<u32, Error> {
        self.phy_set(len, mask);

        let start = metrics::mcycle();
        while self.status() & STATUS_TX_READY == 0 {
            if metrics::mcycle().wrapping_sub(start) > FIFO_LIMIT_CYCLES {
                return Err(Error::TxStalled);
            }
        }
        // SAFETY: a 32-bit write of the TX field; its high byte commits.
        unsafe { write_volatile(self.reg32(TX), data) }

        let start = metrics::mcycle();
        while self.status() & STATUS_RX_READY == 0 {
            if metrics::mcycle().wrapping_sub(start) > FIFO_LIMIT_CYCLES {
                return Err(Error::RxStalled);
            }
        }
        // SAFETY: a 32-bit read of the RX field. This POPS the FIFO, which is
        // why the window must not be cached.
        Ok(unsafe { read_volatile(self.reg32(RX)) })
    }

    /// A whole command, with chip select held down for it and interrupts off.
    ///
    /// Both are about the same failure: anything that steals the SPI path
    /// mid-command leaves the flash halfway through an opcode.
    fn command<T>(&self, body: impl FnOnce(&Self) -> Result<T, Error>) -> Result<T, Error> {
        riscv::interrupt::free(|| {
            self.hold(true);
            let result = body(self);
            self.hold(false);
            result
        })
    }

    /// The JEDEC id: `0x9f`, then three bytes. `ef4016` on this board.
    pub fn jedec_id(&self) -> Result<u32, Error> {
        self.command(|spi| {
            // Right-aligned. `0x9f << 24` is the mistake this comment exists for.
            spi.xfer(8, 1, CMD_JEDEC_ID)?;
            let mut id = 0;
            for _ in 0..3 {
                id = (id << 8) | (spi.xfer(8, 0, 0)? & 0xff);
            }
            Ok(id)
        })
    }

    /// Status register 1. Bit 0 is WIP, bit 1 the write-enable latch.
    pub fn status1(&self) -> Result<u8, Error> {
        self.command(|spi| {
            spi.xfer(8, 1, CMD_READ_STATUS1)?;
            Ok((spi.xfer(8, 0, 0)? & 0xff) as u8)
        })
    }

    /// `out.len()` bytes of the SFDP space at `offset`: `0x5a`, a 24-bit
    /// address, then ONE dummy byte before the data.
    ///
    /// SFDP is what declares the 4 MiB every address above it aliases back
    /// into, so this is the one authority on the part's size that is not a
    /// constant somewhere.
    pub fn sfdp(&self, offset: u32, out: &mut [u8]) -> Result<(), Error> {
        self.command(|spi| {
            spi.xfer(32, 1, (CMD_SFDP << 24) | (offset & 0x00ff_ffff))?;
            spi.xfer(8, 0, 0)?;
            for byte in out.iter_mut() {
                *byte = (spi.xfer(8, 0, 0)? & 0xff) as u8;
            }
            Ok(())
        })
    }

    /// One 32-bit word at `offset`, over the CONTROLLER rather than the memory
    /// map -- so a readback after a program owes nothing to the D-cache.
    ///
    /// Byte order matches the memory map's (`SPIFlashMemoryMap.reverse_bytes`),
    /// so a word written by [`page_program`] reads back identically either way.
    pub fn read_word(&self, offset: u32) -> Result<u32, Error> {
        self.command(|spi| {
            spi.xfer(32, 1, (CMD_READ_DATA << 24) | (offset & 0x00ff_ffff))?;
            let mut word = 0;
            for shift in [0, 8, 16, 24] {
                word |= (spi.xfer(8, 0, 0)? & 0xff) << shift;
            }
            Ok(word)
        })
    }

    /// Write Enable, its own chip-select assertion. The flash clears the latch
    /// itself when the operation completes, so it is one per operation.
    fn write_enable(&self) -> Result<(), Error> {
        self.command(|spi| spi.xfer(8, 1, CMD_WRITE_ENABLE).map(|_| ()))?;
        let sr1 = self.status1()?;
        if sr1 & SR1_WEL == 0 {
            return Err(Error::NotEnabled(sr1));
        }
        Ok(())
    }

    /// Spin on WIP, and return the cycles it took.
    ///
    /// Polling the part, not waiting a fixed time: tSE is 45 ms typical against
    /// 400 ms maximum, so any fixed delay is either mostly idle or corrupting.
    fn wait_ready(&self, limit: u32) -> Result<u32, Error> {
        let start = metrics::mcycle();
        loop {
            if self.status1()? & SR1_BUSY == 0 {
                return Ok(metrics::mcycle().wrapping_sub(start));
            }
            let elapsed = metrics::mcycle().wrapping_sub(start);
            if elapsed > limit {
                return Err(Error::NeverIdle(elapsed));
            }
        }
    }

    /// Erase the 4 KiB sector containing `offset`. Returns the cycles it took.
    pub fn sector_erase(&self, offset: u32) -> Result<u32, Error> {
        scratch_only(offset)?;
        self.write_enable()?;
        // Opcode and 24-bit address as ONE 32-bit transfer: the four bytes the
        // flash wants, in order, inside a single chip-select window.
        self.command(|spi| {
            spi.xfer(32, 1, (CMD_SECTOR_ERASE << 24) | (offset & 0x00ff_ffff)).map(|_| ())
        })?;
        self.wait_ready(ERASE_LIMIT_CYCLES)
    }

    /// Program up to one 256-byte page. Returns the cycles it took.
    ///
    /// A page program cannot cross a page boundary -- the address wraps within
    /// the page and the overflow silently overwrites its own start -- so the
    /// bound is checked here rather than trusted to the caller.
    pub fn page_program(&self, offset: u32, words: &[u32]) -> Result<u32, Error> {
        scratch_only(offset)?;
        let bytes = words.len() as u32 * 4;
        if offset % PAGE_SIZE + bytes > PAGE_SIZE {
            return Err(Error::CrossesPage);
        }
        self.write_enable()?;
        self.command(|spi| {
            spi.xfer(32, 1, (CMD_PAGE_PROGRAM << 24) | (offset & 0x00ff_ffff))?;
            for word in words {
                // Byte-reversed on the way out to match the reversal the memory
                // map applies on the way in. Without it a word written as
                // 0x11223344 reads back as 0x44332211 -- a byte-order bug that
                // looks exactly like a flash fault.
                spi.xfer(32, 1, word.swap_bytes())?;
            }
            Ok(())
        })?;
        self.wait_ready(PROGRAM_LIMIT_CYCLES)
    }
}

/// Erase and program reach one sector and no other address.
fn scratch_only(offset: u32) -> Result<(), Error> {
    if !(SCRATCH..SCRATCH + SECTOR_SIZE).contains(&offset) {
        return Err(Error::OutsideScratch);
    }
    Ok(())
}

/// Microseconds from a cycle count, for reporting an erase or a program
/// against the datasheet figures those bounds came from.
pub fn micros(cycles: u32) -> u32 {
    cycles / (target::TIME_HZ / 1_000_000)
}
