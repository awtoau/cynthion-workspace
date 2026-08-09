//! `load <hex>` -- receive a firmware image over the console and boot it.
//!
//! Moved out of `main.rs` (#296). It was a bare `load` at the crate root, which
//! says nothing about what is being loaded; `staging::load` does.
//!
//! `hyperram.rs` owns the staging area and its header; this owns the transfer.

use core::fmt::Write;

use crate::uart::Uart;
use crate::{hyperram, irq, reboot};

/// Receive `len` bytes over the console and stage them in HyperRAM, then reboot.
///
/// The bytes arrive over the USB bulk OUT endpoint -- the same transport `apollo
/// flash-write` uses, and about four orders of magnitude faster than a JTAG register
/// interface, which `scripts/soc_jtag_stage.py --benchmark` measures at 28 ms per 16-bit
/// word. That is a property of poking a control-plane register per word, not of JTAG:
/// the streaming sink in `gateware/soc/bus/jtag_stage.py` moves 32 KiB over the same wire
/// in 85 ms, and unlike this path it needs no running CPU.
///
/// They go to HyperRAM rather than straight into the image region because the next step
/// is a reboot, and a reboot is exactly what block RAM does not survive intact: the
/// shell doing the receiving is executing from it. HyperRAM is external and keeps its
/// contents across a CPU reset.
pub(crate) fn load(index: usize, uart: &mut Uart, len: u32) {
    if len == 0 || len > hyperram::MAX_IMAGE {
        let _ = writeln!(uart, "length must be 1..{:x}", hyperram::MAX_IMAGE);
        return;
    }

    let _ = writeln!(uart, "send {} bytes", len);

    let mut crc = hyperram::Crc32::new();
    let mut received = 0u32;
    let mut pending: u32 = 0;
    let mut held: u32 = 0;

    // Seek once; the gateware auto-increments, so the inner loop is one store per word.
    hyperram::seek_image();

    while received < len {
        // Blocking on THIS console, and only this one: once the sender has started there
        // is nothing else to do, and returning to the prompt mid-transfer would interpret
        // the image as commands. The other consoles' shells are not run for the duration,
        // which is correct -- a transfer in flight is not a moment to run a command.
        //
        // Their interrupts still fire and still fill their rings; the handler does not
        // know or care that this loop is running. That is a change for the better: on the
        // polled version, anything typed on the other port during a transfer was lost to
        // a 16-byte FIFO overrun. Here it waits.
        //
        // This must read the ring rather than the UART. The handler has already taken the
        // byte out of the 16550's FIFO, so `uart.get()` would spin forever on an LSR.DR
        // that the handler keeps clearing -- a `load` that hangs with the data arriving
        // perfectly.
        let byte = match irq::pop(index) {
            Some(b) => b,
            None => continue,
        };
        crc.push(byte);
        received += 1;

        // The staging port moves a 32-bit pair, so bytes are grouped four at a time,
        // little-endian.
        pending = (pending >> 8) | ((byte as u32) << 24);
        held += 1;
        if held == 4 {
            hyperram::write_pair(pending);
            held = 0;
        }
    }

    // A length that is not a multiple of four still has to fill its final pair. The
    // unused bytes are outside `len`, so the bootloader never reads them.
    if held != 0 {
        hyperram::write_pair(pending >> (8 * (4 - held)));
    }

    let crc = crc.finish();
    hyperram::write_header(len, crc);
    let _ = writeln!(
        uart,
        "staged {} bytes, crc {:08x}; rebooting",
        received, crc
    );

    // The bootloader takes it from here: it re-reads these bytes, checks this CRC
    // against what HyperRAM actually gives back, and jumps.
    reboot();
}

