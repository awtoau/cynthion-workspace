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
//! ## This image executes from the flash it drives
//!
//! Read off the linked ELF, not assumed -- `rust-objdump -h`, and `memory.x`:
//!
//!     .text    VMA 100b0000  LMA 100b0000   the SPIFLASH window, executed in place
//!     .rodata  VMA 100c4400  LMA 100c4400   the same window
//!     .data    VMA 00000400  LMA 100cd4e0   copied to block RAM by riscv-rt
//!     .bss     VMA 00000508                 block RAM
//!
//! and `SPIFLASH_CACHED` is true, so the window is `main=1` and an I-cache miss
//! is a memory-map read. Any instruction outside `.data` can therefore reach
//! the flash at any moment.
//!
//! Two consequences, both enforced rather than hoped for: [`burst`] is
//! `.data`-resident and [`resident`] refuses to run if it ever is not, and
//! interrupts are masked across a command so no handler fetches inside one.
//! Neither applies to a build whose `.text` is in block RAM -- but this one's
//! is not, and #460 is where the mechanism is written up.

use core::ptr::{read_volatile, write_volatile};

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

/// The longest program this driver takes, so the transfer list is a fixed array
/// on the stack: [`burst`] runs from block RAM and must not reach a heap or a
/// `&str`, and there is neither here anyway.
pub const MAX_PROGRAM_WORDS: usize = 16;

/// The 4 KiB sector erase and program may touch, and nothing else.
///
/// 2 MiB in: clear of the bitstream at offset 0 and of the image at 0xb0000,
/// and well below 4 MiB, where the address wraps back onto the bitstream while
/// appearing to be nowhere near it. Same sector as `scripts/riscv_firmware.py`.
pub const SCRATCH: u32 = 0x0020_0000;

/// `SPIFlashMemoryMap.MMAP_DEFAULT_TIMEOUT`: how long the memory map keeps chip
/// select asserted after a burst, in `sync` cycles.
const MMAP_CS_HOLD_CYCLES: u32 = 256;

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

/// How long WIP has to rise before the operation counts as never started.
/// 1 ms against a tSE of 45 ms typical -- 45x clear of the shortest real one,
/// and the part sets WIP on the command's chip-select rise, so this is orders
/// above what it takes.
const START_LIMIT_CYCLES: u32 = target::TIME_HZ / 1000;

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
    /// The transfer loop linked into the flash window it drives.
    NotResident,
    /// WIP never rose: the part did not take the erase or program.
    NeverStarted,
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
            Error::NotResident => write!(
                f, "the transfer loop is in the flash window; it must be in block RAM"),
            Error::NeverStarted => write!(f, "WIP never rose; the part did not take the command"),
        }
    }
}

/// One transfer of a command: `len` bits, `mask` on the DQ drivers, `data` out
/// and, on return, the word the flash sent back.
///
/// SPI is full duplex, so a send and a receive are the same operation with
/// different masks and every transfer produces an RX word.
#[derive(Clone, Copy)]
pub struct Op {
    len: u8,
    mask: u8,
    data: u32,
}

impl Op {
    /// `len` bits out on DQ0.
    const fn send(len: u8, data: u32) -> Op {
        Op { len, mask: 1, data }
    }

    /// `len` bits in, every DQ released so the flash can drive them.
    const fn recv(len: u8) -> Op {
        Op { len, mask: 0, data: 0 }
    }
}

/// THE WHOLE COMMAND, and its wait, from block RAM. See the module's layout.
///
/// - Between `hold(1)` and `hold(0)` nothing may touch the flash window: chip
///   select follows the crossbar's grant, so a memory-map read inside the
///   window takes the grant and the part reads our opcode as more of its burst.
/// - `busy_limit` non-zero polls WIP here and returns the cycles it took. The
///   part answers only the status register while WIP is set, so a wait anywhere
///   else cannot be fetched for the 45 ms of a sector erase.
/// - Therefore: no formatting, no calls out, no `&str`, no nested `fn` -- a
///   nested one links into `.text`. #460.
///
/// # Safety
/// `base` must be the SPI0 CSR window.
#[inline(never)]
#[unsafe(link_section = ".data.flash_burst")]
unsafe fn burst(base: usize, ops: &mut [Op], busy_limit: u32) -> Result<u32, Error> {
    let phy = (base + PHY) as *mut u32;
    let status = (base + STATUS) as *const u8;
    let rx = (base + RX) as *const u32;
    let tx = (base + TX) as *mut u32;
    let hold = (base + HOLD) as *mut u8;

    // `mcycle`, inline. A call to `metrics::mcycle` would leave RAM.
    macro_rules! cycles {
        () => {{
            let value: u32;
            // SAFETY: a read of an implemented, side-effect-free counter.
            #[allow(unused_unsafe)]
            unsafe {
                core::arch::asm!("csrr {0}, mcycle", out(reg) value,
                                 options(nomem, nostack));
            }
            value
        }};
    }

    let mut poll = [Op::send(8, CMD_READ_STATUS1), Op::recv(8)];
    let mut result = Ok(0);
    let started = cycles!();
    let mut command = true;
    // WIP must be SEEN SET before a clear reading ends the wait. The part sets
    // it on the command's chip-select rise, so the first poll can beat it -- and
    // a wait that ends before the operation starts returns the CPU to a flash
    // window that stops answering microseconds later.
    let mut saw_busy = false;

    unsafe {
        write_volatile(hold, 0);
        // LET THE MEMORY MAP LET GO FIRST.
        //
        // `SPIFlashMemoryMap` keeps chip select asserted for
        // MMAP_DEFAULT_TIMEOUT = 256 `sync` cycles after every burst so a
        // sequential read can skip the command and address phases. Chip select
        // at the pad follows the crossbar's grant, and the grant does not move
        // to this controller until it has a transfer ready -- so without this
        // wait CS never rises between the map's read and our opcode, and the
        // flash takes our command bytes as more of its burst. Measured: JEDEC
        // read back `140c34` and SFDP `20469420`, both plausible flash content
        // rather than registers.
        //
        // 2x the timeout, counted from here rather than from the map's last
        // transfer -- which was the instruction fetch that got us in here, so
        // this is the conservative end. Only before the first command: nothing
        // reaches the memory map after that until this function returns.
        let idle = cycles!();
        while cycles!().wrapping_sub(idle) < 2 * MMAP_CS_HOLD_CYCLES {}

        'command: loop {
            let list: &mut [Op] = if command { &mut *ops } else { &mut poll };

            write_volatile(hold, 1);
            for op in list.iter_mut() {
                write_volatile(phy, u32::from(op.len) | (1 << 6) | (u32::from(op.mask) << 10));

                let start = cycles!();
                while read_volatile(status) & STATUS_TX_READY == 0 {
                    if cycles!().wrapping_sub(start) > FIFO_LIMIT_CYCLES {
                        result = Err(Error::TxStalled);
                        write_volatile(hold, 0);
                        break 'command;
                    }
                }
                write_volatile(tx, op.data);

                let start = cycles!();
                while read_volatile(status) & STATUS_RX_READY == 0 {
                    if cycles!().wrapping_sub(start) > FIFO_LIMIT_CYCLES {
                        result = Err(Error::RxStalled);
                        write_volatile(hold, 0);
                        break 'command;
                    }
                }
                // Reading POPS the RX FIFO, which is why this window must not
                // be cached.
                op.data = read_volatile(rx);
            }
            write_volatile(hold, 0);

            if busy_limit == 0 {
                break;
            }
            let elapsed = cycles!().wrapping_sub(started);
            if !command {
                if poll[1].data as u8 & SR1_BUSY != 0 {
                    saw_busy = true;
                } else if saw_busy {
                    result = Ok(elapsed);
                    break;
                } else if elapsed > START_LIMIT_CYCLES {
                    // WIP never rose: the part did not take the command, and
                    // returning here would hand the CPU back to a window the
                    // part is about to stop answering.
                    result = Err(Error::NeverStarted);
                    break;
                }
            }
            if elapsed > busy_limit {
                result = Err(Error::NeverIdle(elapsed));
                break;
            }
            command = false;
        }
    }
    result
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

    /// One command, chip select held for it and interrupts masked over it.
    ///
    /// Masked because an RTIC handler is `.text` and `.text` is the flash
    /// window: a handler inside a chip-select window is the same fault as a
    /// cache miss inside one.
    fn run(&self, ops: &mut [Op]) -> Result<(), Error> {
        self.run_until_idle(ops, 0).map(|_| ())
    }

    /// The same, then WIP polled to completion before returning to any code in
    /// the flash window. Returns the cycles the part was busy.
    fn run_until_idle(&self, ops: &mut [Op], limit: u32) -> Result<u32, Error> {
        resident()?;
        // SAFETY: `base` came from the generated memory map.
        riscv::interrupt::free(|| unsafe { burst(self.base, ops, limit) })
    }

    /// The JEDEC id: `0x9f`, then three bytes. `ef4016` on this board.
    pub fn jedec_id(&self) -> Result<u32, Error> {
        // Right-aligned. The PHY left-justifies, so `0x9f << 24` would clock
        // out eight zero bits -- the mistake this comment exists for.
        let mut ops = [Op::send(8, CMD_JEDEC_ID), Op::recv(8), Op::recv(8), Op::recv(8)];
        self.run(&mut ops)?;
        Ok(ops[1..4].iter().fold(0, |id, op| (id << 8) | (op.data & 0xff)))
    }

    /// Status register 1. Bit 0 is WIP, bit 1 the write-enable latch.
    pub fn status1(&self) -> Result<u8, Error> {
        let mut ops = [Op::send(8, CMD_READ_STATUS1), Op::recv(8)];
        self.run(&mut ops)?;
        Ok((ops[1].data & 0xff) as u8)
    }

    /// The first eight bytes of the SFDP space: `0x5a`, a 24-bit address, then
    /// ONE dummy byte before the data.
    ///
    /// SFDP is what declares the 4 MiB every address above it aliases back
    /// into, so this is the one authority on the part's size that is not a
    /// constant somewhere.
    pub fn sfdp(&self, offset: u32, out: &mut [u8; 8]) -> Result<(), Error> {
        let mut ops = [Op::recv(8); 10];
        ops[0] = Op::send(32, (CMD_SFDP << 24) | (offset & 0x00ff_ffff));
        self.run(&mut ops)?;
        for (byte, op) in out.iter_mut().zip(&ops[2..]) {
            *byte = (op.data & 0xff) as u8;
        }
        Ok(())
    }

    /// One 32-bit word at `offset`, over the CONTROLLER rather than the memory
    /// map -- so a readback after a program owes nothing to the D-cache.
    ///
    /// Byte order matches the memory map's (`SPIFlashMemoryMap.reverse_bytes`),
    /// so a word written by [`Flash::page_program`] reads back identically
    /// either way.
    pub fn read_word(&self, offset: u32) -> Result<u32, Error> {
        let mut ops = [Op::recv(8); 5];
        ops[0] = Op::send(32, (CMD_READ_DATA << 24) | (offset & 0x00ff_ffff));
        self.run(&mut ops)?;
        Ok(ops[1..5]
            .iter()
            .enumerate()
            .fold(0, |word, (index, op)| word | ((op.data & 0xff) << (8 * index))))
    }

    /// Write Enable, its own chip-select assertion. The flash clears the latch
    /// itself when the operation completes, so it is one per operation.
    fn write_enable(&self) -> Result<(), Error> {
        self.run(&mut [Op::send(8, CMD_WRITE_ENABLE)])?;
        let sr1 = self.status1()?;
        if sr1 & SR1_WEL == 0 {
            return Err(Error::NotEnabled(sr1));
        }
        Ok(())
    }

    /// Erase the 4 KiB sector containing `offset`. Returns the cycles it took.
    ///
    /// WIP is polled, not waited out: tSE is 45 ms typical against 400 ms
    /// maximum, so any fixed delay is either mostly idle or corrupting.
    pub fn sector_erase(&self, offset: u32) -> Result<u32, Error> {
        scratch_only(offset)?;
        self.write_enable()?;
        // Opcode and 24-bit address as ONE 32-bit transfer: the four bytes the
        // flash wants, in order, inside a single chip-select window.
        self.run_until_idle(
            &mut [Op::send(32, (CMD_SECTOR_ERASE << 24) | (offset & 0x00ff_ffff))],
            ERASE_LIMIT_CYCLES)
    }

    /// Program up to one 256-byte page. Returns the cycles it took.
    ///
    /// A page program cannot cross a page boundary -- the address wraps within
    /// the page and the overflow silently overwrites its own start -- so the
    /// bound is checked here rather than trusted to the caller.
    pub fn page_program(&self, offset: u32, words: &[u32]) -> Result<u32, Error> {
        scratch_only(offset)?;
        if words.len() > MAX_PROGRAM_WORDS {
            return Err(Error::CrossesPage);
        }
        let bytes = words.len() as u32 * 4;
        if offset % PAGE_SIZE + bytes > PAGE_SIZE {
            return Err(Error::CrossesPage);
        }
        self.write_enable()?;

        let mut ops = [Op::send(32, 0); MAX_PROGRAM_WORDS + 1];
        ops[0] = Op::send(32, (CMD_PAGE_PROGRAM << 24) | (offset & 0x00ff_ffff));
        for (op, word) in ops[1..].iter_mut().zip(words) {
            // Byte-reversed on the way out to match the reversal the memory map
            // applies on the way in. Without it a word written as 0x11223344
            // reads back as 0x44332211 -- a byte-order bug that looks exactly
            // like a flash fault.
            op.data = word.swap_bytes();
        }
        self.run_until_idle(&mut ops[..=words.len()], PROGRAM_LIMIT_CYCLES)
    }
}

/// [`burst`] is in the flash window it drives, which is a refusal and not a
/// warning: the alternative is the CPU erasing the ground it stands on.
///
/// Checked rather than trusted to the attribute, because nothing else would
/// notice an outlined branch or a dropped `link_section`.
fn resident() -> Result<(), Error> {
    match target::FLASH_WINDOW {
        Some((base, size)) if (burst as *const () as usize).wrapping_sub(base) < size => {
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

/// Microseconds from a cycle count, for reporting an erase or a program
/// against the datasheet figures those bounds came from.
pub fn micros(cycles: u32) -> u32 {
    cycles / (target::TIME_HZ / 1_000_000)
}
