//! The interrupt controller: pending bits and enables, and nothing else.
//!
//! `gateware/soc/cpu/intc.py` on the board, `-M virt`'s PLIC under QEMU. Four
//! operations either way: enable, disable, take the next source needing service,
//! acknowledge it.
//!
//! **No priority and no arbitration.** Priority is RTIC's, in software on
//! `msip`, and no controller could preempt regardless: it gives the CPU one
//! line and `mstatus.MIE` is cleared by hardware on trap entry.
//! `docs/soc-interrupts.md`.
//!
//! Taking a source is free of side effects on the board and is the claim under
//! QEMU, whose pending bit sets on a rising line and is cleared by nothing else
//! -- see the `map` module. So [`Intc::next_ready`] and [`Intc::clear`] are one
//! pair, once each per source, on both.
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

/// `virt`'s PLIC.
///
/// **Taking a source and acknowledging it are the claim and the complete here,
/// and there is no choice about it.** QEMU's `sifive_plic_irq_request` sets a
/// pending bit on a rising line and never clears one; the claim read is the only
/// thing that does, and a write to the pending register is rejected as a guest
/// error. So a driver that skipped the claim would loop on a bit nothing could
/// clear -- which is a fact about this model, not about the design.
///
/// What the design does not use is what the claim adds BEYOND that: priority
/// arbitration. Every source is left at 1, so the claim returns the lowest
/// ready source and orders nothing.
#[cfg(feature = "qemu")]
mod map {
    pub const PRIORITY: usize = 0x0000_0000;
    pub const PENDING: usize = 0x0000_1000;
    pub const ENABLE: usize = 0x0000_2000;
    pub const THRESHOLD: usize = 0x0020_0000;
    /// Read: take the highest-priority pending source and clear its pending bit.
    /// Write: release the source whose number is written.
    pub const CLAIM: usize = 0x0020_0004;
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

    /// The lowest-numbered source needing service, or `None`.
    ///
    /// **No side effect here**, and one on QEMU -- see that branch. A handler
    /// loop over this terminates only because each pass either acknowledges its
    /// source or masks it; do neither and it returns the same number forever.
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

    /// The lowest-numbered source needing service, or `None`.
    ///
    /// **This read has a side effect on this target**: it is the PLIC claim, so
    /// it clears that source's pending bit and gates the source until
    /// [`Intc::clear`] releases it. Pair the two, once each per source -- an
    /// unreleased claim leaves that source dead for the session.
    ///
    /// The number matches the board's `ready().trailing_zeros()`: with every
    /// priority at 1, QEMU's arbitration walks the sources in order and returns
    /// the first that is pending and enabled.
    pub fn next_ready(&self) -> Option<u32> {
        // SAFETY: as above.
        match unsafe { read_volatile((self.base + map::CLAIM) as *const u32) } {
            0 => None,
            source => Some(source),
        }
    }

    /// Acknowledge one source: the PLIC complete.
    ///
    /// Unconditional -- QEMU checks the source number and nothing else, so this
    /// works for a source the handler has just masked, exactly as the board's
    /// W1C does.
    pub fn clear(&self, source: u32) {
        // SAFETY: as above.
        unsafe { write_volatile((self.base + map::CLAIM) as *mut u32, source) }
    }

    fn read_mask(&self, offset: usize) -> u32 {
        // SAFETY: as above.
        unsafe { read_volatile((self.base + offset) as *const u32) }
    }

    fn write_enable(&self, mask: u32) {
        // SAFETY: as above.
        unsafe { write_volatile((self.base + map::ENABLE) as *mut u32, mask) }
    }
}
