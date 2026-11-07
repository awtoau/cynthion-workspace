# Driver design and hardware assumptions

The driver implements a sink PHY for `usbpd-traits`; voltage selection,
EPR policy and extended-message reassembly belong to the policy engine.
Register fields come from `device.ddsl`. `interface.rs` handles I2C transport,
`packet.rs` handles headers/FIFO coding, and `lib.rs` sequences the PHY.

## Interrupt servicing

One seven-byte read covers STATUS0A through INTERRUPT (0x3c..=0x42). Reading
INTERRUPTA, INTERRUPTB and INTERRUPT clears their flags (FUSB302B datasheet,
tables 35, 36 and 39). Byte positions are destructured in register order and
decoded using the generated fieldsets. Software retains the events another
operation still needs, especially a hard reset pending cleanup.

RX availability comes from STATUS1.RX_EMPTY, independent of I_CRC_CHK. A TX
status read can consume I_CRC_CHK while a policy packet remains queued.
I_GCRCSENT is drained with the same burst even though the driver does not
need to retain it; otherwise it can keep INT_N asserted.

The driver preserves pending events when reset cleanup is cancelled between
I2C operations. This does not make every operation cancellation-safe: an I2C
implementation can perform hardware side effects before its future completes,
and a partially consumed FIFO frame cannot be reconstructed automatically.

## FIFO and acknowledgement handling

The transmit sequence is three Sync-1 tokens, one Sync-2, PACKSYM plus message
length, message bytes, JAM_CRC, EOP, TXOFF and the TXON command (table 41).
TXON is issued last in the same I2C write. PD messages contain a two-byte
header and at most seven four-byte data objects, giving a 30-byte maximum.
The FIFO and I2C address bytes are outside that message length.

The receive token for ordinary SOP is distinct from SOP' and SOP'' (table 42).
This driver accepts ordinary SOP only. A seven-byte first read contains a
whole control frame: one token, two header bytes and four CRC bytes. For a data
frame those last four bytes are the first data object. One further read consumes
the remaining data and CRC, stopping exactly before the next queued frame.

With AUTO_RETRY, I_TXSENT means the hardware has received matching GoodCRC.
The hardware also places GoodCRC in RX FIFO; the driver consumes it and delivers
only policy messages. Message type, object count and the extended-header flag
are checked so a type-1 data/extended message is not mistaken for GoodCRC.

The datasheet's BMC Power Delivery section describes valid received packets
being stored in FIFO. STATUS0.CRC_CHK describes the latest reception and resets
at the next SOP (table 37); it is not per-entry metadata for the FIFO queue.
Gating the oldest queued packet on that live bit would associate it with a
different reception. The driver relies on PHY CRC processing and RX_EMPTY.

## Initialization and timing

Initialization resets the controller, powers its blocks, configures interrupts
and retries, measures both CC pins with Rd present, selects the stronger CC,
resets PD logic, then enables AUTO_CRC. No RX flush follows AUTO_CRC: the source
may already have seen GoodCRC and started its response timer. The oscillator
stays enabled during the active session, avoiding extra POWER transactions.

GoodCRC uses the SWITCHES1 reset-default specification revision 2.0. USB PD
Revision 3.2 Version 1.1, section 6.2.1.1.5, permits revision 2.0 GoodCRC in
response to revision 3.x messages, as well as requiring it for revision 2.0
messages. This register does not need to track the policy engine's revision.
See the [USB-IF specification](https://www.usb.org/document-library/usb-power-delivery).

The datasheet's recommendation to clear IRQs before TOGGLE applies to autonomous
attachment detection. This driver uses manual CC selection with TOGGLE disabled;
normal RX/TX servicing drains the IRQ snapshot without discarding retained events.
No source on either CC pin is an initialization error. The driver does not
track later detach/orientation changes; reinitialize for a new attachment.

Named timing constants preserve the established reset/measurement settle delays,
IRQ polling interval and RX/TX/hard-reset waiting budgets. They are driver
scheduling choices, not a claim that each number is a USB-PD normative timer.
IRQ waits are bounded by operation deadlines; an indefinitely stalled I2C
implementation still requires its own timeout/cancellation recovery.

The built-in VBUS check requires the FUSB302B sense input to be connected. For
boards such as Pinecil V2 use `VbusDetect` externally; `()` explicitly bypasses
waiting and supplies neither a voltage measurement nor detach detection.
