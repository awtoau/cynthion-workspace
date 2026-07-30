# The silent RISC-V SoC: one real bug fixed, and two wrong diagnoses

Both SoCs — VexRiscv and VexiiRiscv — enumerate over USB and produce no output.
This records what was established, what was fixed, and two diagnoses that were
wrong, because both looked convincing and one of them was mine twice over.

## Fixed: VexiiRiscv had an unconnected bus master

**The core generates three Wishbone masters. The wrapper wired two.**

| bus | carries | was wired |
|---|---|---|
| `FetchL1Wishbone` | instruction fetch | yes, as `ibus` |
| `LsuL1Wishbone` | cached data → block RAM | yes, as `dbus` |
| **`LsuCachelessWishbone`** | **uncached data → I/O** | **no** |

The console is at `0xf0000000` in a `main=0` PMA region, so every console
access is routed to the cacheless master — whose `ACK`, `DAT_MISO` and `ERR`
had no driver. A store there can never complete.

`yosys` said so plainly, eleven times, in `top.rpt`:

    Warning: Wire ...LsuCachelessWishbonePlugin_logic_bridge_down_ERR
             is used but has no driver.

Fixed by exposing a third master (`iobus`) and adding it to the arbiter. After
the fix: **zero undriven wires**, 81.55 MHz against a 60 MHz target (both clocks
PASS), 7358 LUT, 41 of 56 BRAM.

This was a real defect and the fix is right regardless of what else is wrong.
It is also worth understanding *why* three masters exist, since it looks like
over-engineering: a write-back cache can only move whole lines, so MMIO cannot
share the cached path. The cache would read registers nobody asked to read,
write neighbours nobody intended, and do it whenever a line happened to be
evicted rather than when the store issued. VexRiscv solves the same problem with
one bus and a hardcoded "uncached iff address bit 31"; VexiiRiscv makes it
declarative per region — more capable, and less forgiving, because an
unconnected port costs nothing at synthesis and produces a CPU that runs, passes
timing and enumerates while never reaching a peripheral.

## Wrong diagnosis 1: the firmware's ready-poll

    while (!CONSOLE_READY) { }
    CONSOLE_DATA = c;

This looked decisive — a spin on a register that reads zero would hang on the
first character, matching every symptom. It is not the cause: `ready` is driven
from `fifo.w_rdy`, which is high on an empty FIFO, and the same peripheral and
the same firmware work in the VexRiscv SoC.

## Wrong diagnosis 2: reading a completely different device

The console was being read from `/dev/ttyACM1`, chosen as the newest node by
mtime. **`/dev/ttyACM1` is `0483:374e` — an ST-LINK.** Unrelated hardware.
Every "zero bytes" result was a read of somebody else's device.

**No `ttyACM` node belongs to this SoC at all, and none should.** The device
exposes a single **vendor-specific** interface (class `0xff`, subclass `0xff`)
with one bulk IN endpoint at `0x81`, 512 bytes. There are no CDC descriptors, so
the kernel correctly declines to bind a serial driver.

That matters beyond this bug: the docstring calls this a "USB CDC-ACM" console
and it is not one. Any instruction to read it as a serial port is wrong.

## What a positive control established

Loading the **known-good VexRiscv** bitstream produced the same silence. That
inverted the investigation: the fault is not VexiiRiscv-specific, and every
hypothesis about the VexiiRiscv bus, PMA regions or cache behaviour was aimed at
the wrong target.

This is the third time in this project a positive control has invalidated a
clean-looking measurement. The pattern is consistent enough to state as a rule:
**a null result is not evidence until the measurement is shown capable of
producing a non-null one.**

## Where it actually stands

Reading endpoint `0x81` directly with libusb, interface claimed, three attempts:
`110 Operation timed out` each time. The device self-identifies correctly as
`Cynthion / RISC-V console`, so enumeration and descriptors are sound.

**Nothing is queued on the endpoint.** The cause is not established. What is
now known, and was not before:

- the transport is a raw bulk endpoint, not a serial port
- the fault affects both CPUs, so it is in the SoC or the firmware, not the core
- the VexiiRiscv bus wiring was genuinely broken and is now fixed
- `top.rpt` and `top.tim` hold the yosys and nextpnr logs; the Python build log
  contains one line and searching it proves nothing (an earlier "zero undriven
  wires" claim here was made against the wrong file and was meaningless)

## Next, in order

1. **Instrument the FIFO write side.** `console.source.valid` is already routed
   to the sideband, but the sideband did not answer after configuration —
   probably the JTAG/UART pin mux. A latching counter readable over JTAG would
   settle whether any store reaches the peripheral, without depending on a
   channel that shares pins with the programming path.
2. **Check whether the endpoint is ever primed.** The flush condition is
   `endpoint.flush.eq(~console.source.valid)`, which was itself a fix; whether
   the IN endpoint ever gets a transfer queued has not been verified on this
   build.
3. **Confirm the firmware runs at all** — a store to a known block-RAM address,
   read back over JTAG. That separates "CPU not executing" from "peripheral not
   reached" with no USB involvement.
