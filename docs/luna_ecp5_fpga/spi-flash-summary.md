# Configuration flash over SPI: what was done, and what it measured

A summary of the SPI/QSPI flash work, parked at a working state. Detail lives in
[flash-speed.md](flash-speed.md); this is the shape of it, the numbers, and an
explicit list of what was **not** finished.

## The part

**Winbond W25Q32**, JEDEC ID `EF 40 16` — 4 MiB, quad-capable, rated to 104 MHz
for fast reads and 50 MHz for plain `0x03`. The schematic says `W25Q32JVSS`; the
datasheet consulted was the FV revision J, whose instruction set matches.

Quad Enable is **already set** in status register 2 (`SR2 = 0x02`), so quad
modes need no configuration write and no loss of hardware write protection.

## Measured throughput

All figures verified byte-exact against `apollo flash-read`, which reaches the
same flash through an entirely independent path (JTAG bit-banged by the SAMD11).
A pass means two unrelated mechanisms agree, not that one is self-consistent.

| Mode | SCK | Throughput | Status |
|---|---|---|---|
| `0x03` single | 15 MHz | 1.87 MB/s | PASS |
| `0x03` single | 30 MHz | 3.75 MB/s | PASS |
| `0x0B` fast single | 30 MHz | 3.75 MB/s | PASS |
| `0x6B` quad output | 30 MHz | 14.92 MB/s | PASS |
| `0x6B` quad output | 48 MHz | 23.9 MB/s | PASS — **shipping default** |
| `0x6B` quad output | 60 MHz | 29.85 MB/s | PASS |
| `0x6B` quad output | 80 MHz | 39.79 MB/s | PASS, but **out of spec** |
| `0x6B` quad output | 120 MHz | — | FAIL |
| `0xEB` quad I/O | 48 MHz | 23.9 MB/s | PASS |

Quad is **4× single-lane at the same clock**, which is the safe way to get
throughput: the gain is lane count, not clock rate.

## The ceiling is the FPGA pin, not the flash

The flash is rated to 104 MHz and never gets the chance. `MCLK` is a
*configuration* pin: after configuration the other SPI pins become ordinary user
I/O, but MCLK stays owned by the configuration block and is reachable only
through the `USRMCLK` primitive. Lattice's frequency table for it stops at
**62 MHz** (FPGA-TN-02039), and the datasheet quotes `fMCLK` only as a ±20%
tolerance on those selectable frequencies — there is no maximum above 62 because
the pin was not meant to run there.

This is **not a board design fault.** MCLK is "always reserved for use in MSPI
mode … as the reference clock for performing memory transactions with the
external SPI PROM", so a board that configures itself from this flash has no
alternative ball. The data pins carry dual designations (`PB11A/B`, `PB9A/B`)
and are ordinary bank-8 I/O as well, which is exactly why they keep working at
full speed. The asymmetry is in the silicon.

A board that loads its bitstream over USB and never boots from flash *could*
route the clock to an ordinary pin and reach the flash's own 104 MHz — but that
is a PCB change, and it costs the recovery path, since the FPGA would then
depend entirely on the debug controller to come up.

## Clocking

`SCK = sync / (divisor + 1)`, with the divisor writable at runtime over JTAG and
the sync frequency fixed at build time.

PLL configuration is delegated to **`ecppll`**, Project Trellis's own calculator,
rather than computed. Two earlier attempts to derive the dividers by hand failed:
the first blamed the output dividers, the second "corrected" the VCO to a value
that fitted every symptom while being wrong about the cause. `ecppll` picks a
different VCO per target — 480 MHz for 240, 576 for 192, 640 for 160 — so no
fixed-VCO scheme could have reached those frequencies at all.

The shipping build uses **96 MHz sync → 48 MHz SCK**. Higher was tried:

- 120 MHz sync (60 MHz SCK) works, but adding burst logic pushed the design to
  119 MHz against the 120 required and the build failed outright.
- 160 MHz sync (80 MHz SCK) routes anywhere between 127 and 157 MHz depending on
  the netlist, so it failed about as often as it succeeded.

96 MHz closes at 122 MHz with real margin and stays buildable as more is added.
An operating point that only compiles sometimes is not one.

## Quad I/O (`0xEB`) — implemented, and less useful than expected

`0xEB` sends the 24-bit address on four lanes as well as the data, halving
per-transaction overhead from 40 clocks to 20. That saving is **fixed per
transaction**, so it matters in inverse proportion to transfer size:

| Read size | Predicted gain |
|---|---|
| 4 B | 42% |
| 32 B | 19% |
| 256 B | 3.6% |
| 32 KiB | **0.03%** (measured: 131117 vs 131157 cycles) |

An earlier note in this workspace suggested `0xEB` as a way to recover
throughput lost by clocking slower. **It is not** — dropping 80 MHz to 48 MHz
costs 40%, and `0xEB` returns 0.03% of it on streaming reads. The datasheet is
explicit that it exists for "faster random access for code execution (XIP)",
which makes it the right mode for a RISC-V executing from flash and close to
irrelevant for bulk transfer.

## Two bugs worth remembering

**CS was inverted twice.** Glasgow's controller inverts chip select internally,
expecting an active-high port, while the platform declares `cs` with `PinsN`.
The two cancelled: CS was never asserted, and every read returned zeros at the
correct speed for every offset and divisor. Offset-independence is what
identified it — a sampling error shifts or corrupts data, it does not silence it.

**The chip was never deselected.** The reader relied on the `chip` field of the
final payload beat, but by then `bytes_left` is 0 so `valid` is low and that
frame was never sent. A *single* read still worked, so this hid behind every
rebuild-and-reconfigure measurement and only appeared once reads could repeat
without reconfiguring. Fixing it doubled the apparent ceiling.

## What is NOT done

- **The burst sequencer is broken.** It asserts `start` on the completion edge
  while the reader has not returned to IDLE, so `busy` latches high. Committed
  deliberately, marked in the source.
- **Small-read performance is unmeasured.** A JTAG register read takes ~35 ms;
  a 4-byte flash read takes ~1 µs. The instrument is 35,000× slower than the
  thing measured, so the host cannot time short transfers however the gateware
  is arranged. This needs a soft CPU inside the FPGA.
- **`0xEB`'s benefit is predicted, not measured.** The 42%/19% figures above are
  arithmetic. Confirming them needs the same in-FPGA measurement.
- **Dual modes (`0x3B`, `0xBB`) are unimplemented**, as are Continuous Read with
  wrap and QPI mode — all of which reduce clocks per byte rather than needing a
  faster pin.
- **80 MHz is verified on one board only**, at room temperature and nominal
  voltage, and the failure mode is abrupt rather than graceful.
- **Write and erase are untested.** Everything here is reads.

## Files

| Path | What |
|---|---|
| `repos/apollo/apollo_fpga/gateware/qspi_flash.py` | Glasgow controller wrapper, USRMCLK bridge, quad reader |
| `repos/apollo/apollo_fpga/gateware/flash_id.py` | JEDEC ID, status registers, capture buffer |
| `repos/apollo/apollo_fpga/gateware/variable_clock.py` | ecppll-driven PLL |
| `ecp5-test/qspi/qspi_gateware.py` | Test bitstream |
| `scripts/qspi_ladder.py` | Divisor/offset sweep, verified against apollo |
| `scripts/qspi_burst.py` | Small-read comparison (blocked, see above) |
| `scripts/flash_modes.py` | Opcode and clock sweep |
