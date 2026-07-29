# The synthetic JTAG benchmark, and the floor that was not a floor

Measuring the JTAG path from the host measures USB as well: the host stages 256
bytes with `SET_OUT_BUFFER`, then issues `SCAN`. A synthetic benchmark in the
firmware removes USB from the measurement — one control transfer to start, the
MCU clocks from a buffer it already holds, one IN transfer to collect the
result.

Vendor request `0xb8`, verified against the firmware enum rather than assumed.
Cost: **244 B flash, 16 B RAM** (78.15% → 80.19% of the 14 KB application
flash).

## The finding: the path was CPU-bound, not wire-bound

Sweeping the SERCOM divider, with every readback bit-exact:

    time per byte = 8/SCK + 1.11 µs

The second term is **constant across an 8x SCK range**. SCK is 12 MHz, so the
wire needs 0.667 µs/byte — and the polled loop was spending **1.11 µs/byte of
CPU** on top.

So the "0.667 µs/byte wire floor" quoted earlier was not the floor. The path was
CPU-bound, and raising SCK could not have fixed it.

**This revises the DMA result.** DMA was measured against a mis-attributed
floor and judged to have nothing to take. There was headroom; DMA simply was
not the way to reach it.

The cause was in `spi_send()`: write a byte, wait for `RXC`, read it — leaving
the shifter idle while the CPU collected the previous byte. The SERCOM's DATA
register is write-buffered, so handing over byte N+1 before reading byte N
overlaps them.

## Results, reported separately because they measure different things

| | before | after |
|---|---|---|
| **synthetic**, JTAG only | 1.770 µs/byte | **1.122 µs/byte** |
| of which MCU overhead | 1.11 | **0.455** |
| **end-to-end** configure | 1059 ms | **969 ms** |

The synthetic predicted a 65 ms saving; 90 ms landed. That the two agree is the
cross-validation.

## A correction to the baseline

The end-to-end baseline measured **1059 ms, not 1680 ms**, A/B'd by reflashing
the saved baseline image. The 1680 ms figure quoted elsewhere came from a
different bitstream. So the saving here is **90 ms (8.5%)**, not 700.

## The controls caught two more false results

Both had clean, plausible timings:

- TAP not in `SHIFT_DR` → TDO reads back **all zeroes**, perfectly stably.
- In `SHIFT_DR` without BYPASS loaded → **all ones**.

A checksum-based check passed the second. It was replaced with a bit-exact
comparison against BYPASS's one-bit delay, and rather than guess the model a
third time, `jtag_tdo_probe.py` establishes it empirically from the known-good
scan path.

That is now three separate occasions in this project where a positive control
invalidated a measurement that looked clean.

## Where the time actually is

**JTAG now has no headroom worth taking.** At 1.122 µs/byte it is **113 ms of
the 969 ms**; the other **856 ms (88%) is USB and protocol**. Raising SCK to
24 MHz buys only 1.07x now that the CPU dominates again.

So `SET_OUT_BUFFER` is the target.

One note for that work: the bulk-streaming failure was explained away as "MCU
clocking is the limit". That was half right in a misleading way — the MCU *was*
the limit, but in the SPI loop, not in USB handling. With the SPI loop fixed,
the bulk-streaming result is still unexplained and now cleanly re-testable,
because the JTAG side no longer confounds it.
