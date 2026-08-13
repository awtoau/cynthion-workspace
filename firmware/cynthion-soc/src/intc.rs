//! The interrupt controller: pending bits and enables, and nothing else.
//!
//! `gateware/soc/cpu/intc.py` on the board, `-M virt`'s PLIC under QEMU. The
//! two register maps are the only `#[cfg]` here, because the operations are the
//! same four: enable, disable, read what is pending, acknowledge.
//!
//! **Nothing claims and nothing completes.** The PLIC offers both and this
//! driver uses neither: a claim is arbitration for a controller that can
//! preempt, and no controller can -- it gives the CPU one line, and `mstatus.MIE`
//! is cleared by hardware on trap entry. `docs/soc-interrupts.md`.
//!
//! ## The two shapes of source
//!
//! Which one a source is fixed in the gateware and is a fact about the signal:
//!
//!   * **level** -- a backlog the CPU drains (a 16550's FIFO). The pending bit
//!     cannot be acknowledged while the line is asserted, so the order is drain
//!     the peripheral, then [`Intc::clear`]. A source whose clear is too slow
//!     for a handler is masked instead, and the task that does the clear
//!     acknowledges and unmasks.
//!   * **edge** -- an event the CPU cannot clear: `FAULTB` held for 30 ms, a
//!     button held down, a PLL that stays unlocked. Acknowledging is
//!     unconditional, so the handler clears it and is not re-entered until the
//!     next edge. No mask needed, and none used.
//!
//! ## Register access
//!
//! On the board each register is one CPU word of byte-wide CSR: read the low
//! byte first (it latches the shadow), and write all four with the high one
//! last (it commits). Three writes write nothing -- the same rule `src/gpio.rs`
//! documents, and the same trap.

use core::ptr::{read_volatile, write_volatile};

/// The board's controller: two mask registers, four byte addresses each.
#[cfg(not(feature = "qemu"))]
mod map {
    /// Enabled sources. Read/write, one bit per source.
    pub const ENABLE: usize = 0x0;
    /// Pending sources. Read to see them, write a 1 to acknowledge one.
    pub const PENDING: usize = 0x4;
    /// Byte addresses per register. `alignment=2` in the gateware, so this is
    /// 4 for any source count up to 32 and the fourth access commits a write.
    pub const BYTES: usize = 4;
}

/// `virt`'s PLIC, driven as pending bits and enables.
///
/// The claim register at 0x200004 is never touched. What the PLIC adds over the
/// board's controller -- arbitration between sources, and gating one while its
/// handler runs -- is what this design does not want, and skipping the claim
/// leaves exactly the pending-and-enable behaviour the board has.
///
/// Every source is level-sensitive on this machine, so [`super::Intc::clear`]
/// has nothing to do: the pending bit follows the line.
#[cfg(feature = "qemu")]
mod map {
    pub const PRIORITY: usize = 0x0000_0000;
    pub const PENDING: usize = 0x0000_1000;
    pub const ENABLE: usize = 0x0000_2000;
    pub const THRESHOLD: usize = 0x0020_0000;
}

/// An interrupt controller at a fixed base address.
///
/// `Copy` for the same reason `Uart` is: an instance is nothing but an address,
/// so a caller may materialise one where it needs it rather than threading a
/// handle through every function.
#[derive(Clone, Copy)]
pub struct Intc {
    base: usize,
}

impl Intc {
    pub const fn new(base: usize) -> Self {
        Intc { base }
    }

    /// Which sources are pending, as a bitmap. No side effects.
    pub fn pending(&self) -> u32 {
        self.read_mask(map::PENDING)
    }

    /// Which sources may raise the CPU line, as a bitmap. No side effects.
    pub fn enabled(&self) -> u32 {
        self.read_mask(map::ENABLE)
    }

    /// What the handler has to service: pending and not masked.
    pub fn ready(&self) -> u32 {
        self.pending() & self.enabled()
    }

    /// The lowest-numbered source needing service, or `None`.
    ///
    /// **No side effect**, unlike the PLIC claim this replaces: it reads two
    /// registers and returns. A handler loop over it therefore terminates only
    /// because each pass either acknowledges its source or masks it -- do
    /// neither and this returns the same number forever.
    ///
    /// Re-reading per pass rather than servicing a snapshot is deliberate: a
    /// source that goes pending while an earlier one is being serviced is
    /// picked up in the same trap instead of costing a second trap frame.
    pub fn next_ready(&self) -> Option<u32> {
        match self.ready() {
            0 => None,
            ready => Some(ready.trailing_zeros()),
        }
    }

    /// Acknowledge one source.
    ///
    /// Unconditional for an edge source. **Ignored for a level source whose
    /// line is still asserted** -- clear the peripheral first, or this has done
    /// nothing and the handler is re-entered.
    pub fn clear(&self, source: u32) {
        self.write_pending(1 << source);
    }

    /// Let `source` raise the CPU line.
    pub fn enable(&self, source: u32) {
        self.update(1 << source, 0);
    }

    /// Stop `source` raising the CPU line, without losing it.
    ///
    /// The peripheral keeps asserting and the pending bit stays set; only the
    /// path to the CPU is cut. That is what makes this deferral: a level source
    /// whose clear needs something a handler may not do -- a millisecond of I2C
    /// -- is masked here and unmasked by the task that did the clear.
    pub fn disable(&self, source: u32) {
        self.update(0, 1 << source);
    }

    /// Read/modify/write of the enable mask, safe against the handler.
    ///
    /// The handler masks sources and normal context unmasks them, so without
    /// this a task's stale write-back re-enables a source the handler had just
    /// deferred -- which un-defers it before its clear has happened. Inside a
    /// handler `mstatus.MIE` is already clear and `free` is a read and a write
    /// of `mstatus`.
    fn update(&self, set: u32, clear: u32) {
        riscv::interrupt::free(|| {
            let mask = self.enabled();
            self.write_enable((mask | set) & !clear);
        });
    }
}

#[cfg(not(feature = "qemu"))]
impl Intc {
    /// Nothing to configure: both registers reset to zero and every source is
    /// enabled by the driver that owns it.
    pub fn init(&self) {}

    fn read_mask(&self, offset: usize) -> u32 {
        let mut value = 0;
        // Ascending from the low byte, which is the access that latches the
        // multiplexer's read shadow. Any other order reads whatever the shadow
        // held from the last register anyone touched.
        for index in 0..map::BYTES {
            // SAFETY: a byte inside the controller's window, which the SoC
            // decodes as an uncached `main=0` CSR region.
            let byte = unsafe { read_volatile((self.base + offset + index) as *const u8) };
            value |= (byte as u32) << (8 * index);
        }
        value
    }

    fn write_mask(&self, offset: usize, value: u32) {
        for index in 0..map::BYTES {
            // SAFETY: as above. The last write is the one that commits.
            unsafe {
                write_volatile(
                    (self.base + offset + index) as *mut u8,
                    (value >> (8 * index)) as u8,
                );
            }
        }
    }

    fn write_enable(&self, mask: u32) {
        self.write_mask(map::ENABLE, mask);
    }

    /// Write-1-to-clear: a 0 bit acknowledges nothing, so this needs no
    /// read-modify-write and cannot lose a source that went pending beside it.
    fn write_pending(&self, mask: u32) {
        self.write_mask(map::PENDING, mask);
    }
}

#[cfg(feature = "qemu")]
impl Intc {
    /// Admit every source: threshold 0, and a priority above it per source.
    ///
    /// `virt`'s PLIC will not raise the CPU line for a source whose priority is
    /// not strictly above the threshold, and both reset to 0 -- so without this
    /// an enabled, pending, asserting source is silent. The board's controller
    /// has neither register and needs none of it.
    ///
    /// One level for all of them. Priority is RTIC's, in software; a ranking
    /// here would decide the order of sources within one trap and nothing else.
    pub fn init(&self) {
        // SAFETY: words inside the PLIC's window, which is a device on `virt`.
        unsafe {
            write_volatile((self.base + map::THRESHOLD) as *mut u32, 0);
            for source in 1..32u32 {
                write_volatile(
                    (self.base + map::PRIORITY + 4 * source as usize) as *mut u32,
                    1,
                );
            }
        }
    }

    fn read_mask(&self, offset: usize) -> u32 {
        // SAFETY: as above.
        unsafe { read_volatile((self.base + offset) as *const u32) }
    }

    fn write_enable(&self, mask: u32) {
        // SAFETY: as above.
        unsafe { write_volatile((self.base + map::ENABLE) as *mut u32, mask) }
    }

    /// Nothing to do: every source on this machine is a level, so its pending
    /// bit follows the line and drops when the peripheral is serviced.
    fn write_pending(&self, _mask: u32) {}
}
