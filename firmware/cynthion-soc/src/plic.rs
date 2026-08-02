//! A RISC-V PLIC driver, parameterised by base address.
//!
//! Like `src/uart.rs`, and for the same reason: nothing below is conditional on
//! the target. The SoC has a PLIC at `PLIC_BASE` (`ecp5-test/riscv/vexii_plic.py`)
//! and QEMU's `-M virt` has one at 0x0c000000, both with the register map the
//! RISC-V PLIC specification defines, so this file is compiled unchanged for the
//! board and for the emulator. The whole difference is two constants in
//! `src/target.rs`: where it is, and which source number the UART is on.
//!
//! That is what makes `scripts/soc_test.py` evidence about the board's interrupt
//! path rather than about a second implementation of it -- the same argument
//! that made the console peripheral a standard 16550 in the first place.
//!
//! ## Context 0
//!
//! A PLIC serves several "contexts" -- (hart, privilege level) pairs -- each
//! with its own enable bits, threshold and claim register. This SoC has exactly
//! one hart running in machine mode, so context 0, and `virt`'s context 0 is
//! also hart 0 machine mode (its device tree gives `interrupts-extended =
//! <cpu 11>, <cpu 9>`, and 11 is the machine external interrupt). So the
//! offsets below are correct on both without a stride calculation.
//!
//! ## What is deliberately not here
//!
//! No interrupt vector table, no handler registration, no priority scheme. The
//! PLIC is a mux with a mask; what to do about a source belongs to the code that
//! owns the source. See `src/irq.rs`.

use core::ptr::{read_volatile, write_volatile};

/// Per-source priority registers, one 32-bit word each, indexed by source.
/// Source 0 does not exist, so its word is reserved and never touched.
const PRIORITY_BASE: usize = 0x0000_0000;

/// Pending bits. Read-only, one bit per source, and completely free of side
/// effects -- which is why it is safe to print from the shell's `irq` command
/// while interrupts are live.
const PENDING_BASE: usize = 0x0000_1000;

/// Enable bits for context 0.
const ENABLE_BASE: usize = 0x0000_2000;

/// Context 0's priority threshold. Sources at or below it are masked.
const THRESHOLD: usize = 0x0020_0000;

/// Context 0's claim/complete register.
///
/// Reading it takes the highest-priority pending source and returns its number,
/// or 0 for "nothing pending". Writing a source number back says the handler is
/// finished with it.
///
/// **This is the one register in the SoC outside a UART's RBR whose read changes
/// state**, and it is four megabytes from anything that gets polled -- see the
/// register-discipline section of `ecp5-test/riscv/vexii_plic.py`. Do not read it
/// speculatively, do not read it to "see what is pending" (that is what
/// `PENDING_BASE` is for), and do not read it twice per interrupt.
const CLAIM: usize = 0x0020_0004;

/// A PLIC at a fixed base address.
///
/// `Copy` for the same reason `Uart` is: an instance is nothing but an address,
/// so a caller may materialise one where it needs it instead of threading a
/// handle through every function.
#[derive(Clone, Copy)]
pub struct Plic {
    base: usize,
}

impl Plic {
    pub const fn new(base: usize) -> Self {
        Plic { base }
    }

    fn reg(&self, offset: usize) -> *mut u32 {
        (self.base + offset) as *mut u32
    }

    /// Set a source's priority. 0 means "never interrupt".
    ///
    /// The SoC's PLIC implements three bits (1..7), matching what `virt` offers,
    /// so a value above 7 reads back truncated rather than rejected. That is the
    /// standard's own WARL behaviour and the reason a driver that cares should
    /// read back rather than assume.
    pub fn set_priority(&self, source: u32, priority: u32) {
        // SAFETY: a word inside the PLIC's window, which is an uncached `main=0`
        // PMA region on the SoC and a device on `virt`. Volatile because it is a
        // device.
        unsafe {
            write_volatile(
                self.reg(PRIORITY_BASE + 4 * source as usize),
                priority,
            );
        }
    }

    /// Let context 0 be interrupted by `source`.
    ///
    /// Read-modify-write, because the enable register holds every source's bit
    /// and this SoC has two. Not interrupt-safe, and does not need to be: it is
    /// called from `init()` before `mstatus.MIE` is set.
    pub fn enable(&self, source: u32) {
        // SAFETY: as above.
        unsafe {
            let reg = self.reg(ENABLE_BASE);
            write_volatile(reg, read_volatile(reg) | (1 << source));
        }
    }

    /// Stop context 0 being interrupted by `source`, without losing it.
    ///
    /// The peripheral keeps asserting and the PLIC keeps the pending bit; only
    /// the path to the CPU is cut. That is what makes this usable as deferral:
    /// a source whose service needs something a handler may not do -- a
    /// millisecond of I2C, a console write -- is masked by the handler and
    /// re-enabled by normal context once the work is done. Without it a
    /// level-sensitive source that the handler cannot clear re-enters
    /// immediately and forever, which is a hang that looks like a dead CPU.
    ///
    /// Read-modify-write, like `enable`, and with the same caveat: it is not
    /// interrupt-safe against another writer of this register. The handler is
    /// the only thing that calls it, and a handler cannot preempt itself.
    pub fn disable(&self, source: u32) {
        // SAFETY: as above.
        unsafe {
            let reg = self.reg(ENABLE_BASE);
            write_volatile(reg, read_volatile(reg) & !(1 << source));
        }
    }

    /// Mask every source at or below `level` for context 0.
    ///
    /// 0 lets anything with a nonzero priority through, which is what this
    /// firmware wants: there is nothing here whose latency matters enough to
    /// starve the other console for. RTIC will use this for critical sections.
    pub fn set_threshold(&self, level: u32) {
        // SAFETY: as above.
        unsafe { write_volatile(self.reg(THRESHOLD), level) }
    }

    /// Which sources are requesting service, as a bitmap. No side effects.
    pub fn pending(&self) -> u32 {
        // SAFETY: as above. This register is combinational from the source
        // levels in gateware and read-only in QEMU; reading it twice gives the
        // same answer.
        unsafe { read_volatile(self.reg(PENDING_BASE)) }
    }

    /// Which sources context 0 accepts, as a bitmap. No side effects.
    pub fn enabled(&self) -> u32 {
        // SAFETY: as above.
        unsafe { read_volatile(self.reg(ENABLE_BASE)) }
    }

    /// Take the highest-priority pending source, or `None` if there is none.
    ///
    /// # This read has a side effect
    ///
    /// The claimed source stops being reported as pending and stops raising the
    /// CPU's interrupt line until [`Plic::complete`] is called with the same
    /// number. That is what stops a level-sensitive source -- which a 16550's is
    /// -- from re-entering the handler before it has executed an instruction.
    ///
    /// So: call this once per pass of the handler loop, and always pair it with
    /// `complete`. A claim that is never completed leaves that source silently
    /// dead for the rest of the session, with `pending` reading 0 and the
    /// peripheral still asserting.
    pub fn claim(&self) -> Option<u32> {
        // SAFETY: as above.
        let source = unsafe { read_volatile(self.reg(CLAIM)) };
        if source == 0 {
            None
        } else {
            Some(source)
        }
    }

    /// Tell the PLIC the handler is finished with `source`.
    ///
    /// If the source is still asserting -- a 16550 whose FIFO was not fully
    /// drained -- the interrupt is raised again immediately. That is correct:
    /// there is still work.
    pub fn complete(&self, source: u32) {
        // SAFETY: as above.
        unsafe { write_volatile(self.reg(CLAIM), source) }
    }
}
