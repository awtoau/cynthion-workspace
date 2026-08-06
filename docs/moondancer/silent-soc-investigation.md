# The silent RISC-V SoC: solved, after two real bugs and three wrong diagnoses

**Resolved 2026-07-31.** The console prints:

    prod 369d0368
    tick 00000000
    tick 00000001

`0x12345678 * 3 = 0x369d0368`, so the CPU does real multiplication rather than storing a
constant, and the tick counter advances between separate reads -- it executes rather than
replaying a buffer.

## The two faults, and why one hid the other

**1. VexiiRiscv had an unconnected bus master** (`d35e0b5`). The core generates three
Wishbone masters and the wrapper wired two, leaving every uncached I/O access -- including
every console write -- routed to a port with no `ACK`, `DAT_MISO` or `ERR` driver. Detail
below; the fix is right regardless of what else was wrong.

**2. The console was never CDC-ACM** (`49ebdfa`). It claimed to be in three comments while
building a bare vendor-specific interface with one bulk IN endpoint and no CDC
descriptors, so the kernel correctly declined to bind a serial driver and no tty node ever
appeared. That is what sent this investigation to read `/dev/ttyACM1`, an ST-LINK.

Swapping it to LUNA's `USBSerialDevice` produced a real tty -- and still no output,
because the swap introduced a third fault of mine:

    serial.tx.last.eq(~console.source.valid)

`last` marks the final beat of a packet and the endpoint only observes it on a beat where
`valid` is high, so that expression is **unsatisfiable**. No packet was ever terminated.
`USBStreamInEndpoint` has a `flush` input for exactly this, but `USBSerialDevice` does not
expose it, so the boundary must come from `last` asserted alongside `valid`.

**Why it took so long:** two independent faults in series. Fixing either alone changes
nothing observable, so each fix looked like it had failed.

## The lesson worth keeping

Every wrong diagnosis here shared a shape: **a null result treated as evidence before the
measurement was shown capable of producing a non-null one.** A silent console, a
`state=0` sideband read, a zero-byte tty read -- each was consistent with the fault and
equally consistent with the instrument not working.

The sideband case is the sharpest. `state` was wired to `Cat(cpu.ibus.cyc, cpu.iobus.cyc)`
-- Wishbone strobes, high only during a transaction, sampled whenever the host happens to
ask. Reading 0 was near-certain even on a busy CPU, and it nearly got reported as a dead
core. Latching them sticky ("has this bus **ever** moved") turned the same wires into a
decisive answer: `state=3`, both buses active, CPU confirmed running while the console was
still silent -- which is what localised the fault to the last hop.

---

Original record follows, kept because the reasoning is what got here.

---

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

## The sideband cannot currently diagnose this, and why

The obvious instrument is the FPGA_ADV sideband: a separate wire (T6) that
touches neither USB nor the JTAG data pins, already instantiated in both SoCs,
with `console.source.valid` routed to it. It did not answer, and the reasons are
worth recording because none of them is "the link is broken".

**The vendor ABI is not what a reader would guess.** Request `0xc3` overloads
`wValue`, and any value it does not recognise **sets the mode** rather than reading
anything — so an initial query with `wValue=0` selected EIC mode, the opposite of what
was wanted. The full table is
[`../sideband.md`](../chips/cynone-sideband.md#10-the-host-interface).

**UART mode must be selected explicitly.** EIC is the power-on default, so a
host that never chooses behaves like older firmware. With `wValue=1` the mode
reads back as `1`, confirming the ABI is right.

**Commands still return zero bytes, which the firmware documents as timeout** —
the FPGA is not answering.

**And the link-health counters that would confirm that are not in the flashed
firmware.** The board runs `v1.1.1-17-ga7b8283`; `0xFFFC` arrived in `b48d4bf`,
one commit later, so it stalls with a pipe error. Expected, not a fault.

Baud was checked and ruled out **for this firmware**: the gateware responder and
`ecp5-test/sideband_debug.py` both defaulted to 115200, matching the flashed
`a7b8283`. `b48d4bf` raises both to 230400, which *would* break the link if the
gateware were rebuilt against it while the MCU stayed on `a7b8283` — worth
knowing before flashing that commit. 230400 is the settled rate and 115200 is the
worse one; see [`../sideband.md`](../chips/cynone-sideband.md#2-rate-230400-and-faster-is-better).

So the sideband is a sound instrument that currently cannot be read. Making it
diagnostic means flashing `b48d4bf` or later, which is a firmware change and
should be done deliberately rather than mid-diagnosis.

## Block RAM cannot be read back over JTAG

The natural alternative — write markers to RAM and read them over JTAG — does
not work either. `LSC_EBR_READ` (0xB0) was probed on live silicon and is
**inert**, so there is no JTAG path to block RAM contents. A probe firmware was
written on that assumption before this was rechecked; it is kept at
`scripts/riscv_probe_firmware.py` because its staged markers are the right
design, but they need a readback path that exists.

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
