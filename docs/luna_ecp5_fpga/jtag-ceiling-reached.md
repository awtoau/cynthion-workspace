# JTAG configuration: the path is done, the time is in USB

Two proposals — transmit-only DMA, and raising SCK to 24 MHz — were investigated
and **neither was implemented**, because measurement showed both would be
no-ops or unsafe. This records why, since both looked well-founded.

## There is no CPU cost left for DMA to remove

The "0.455 µs/byte CPU term" that justified TX-only DMA is an artifact of the
same mistake that produced the earlier 3.93 µs/byte figure: dividing a whole
`SCAN` call by its payload, which charges the fixed ~145 µs USB round trip to
per-byte clocking.

Measuring the **marginal** cost instead — differencing 64-bit and 2048-bit scans
over the same round trip, so the fixed cost cancels — gives **0.663 / 0.684 /
0.715 µs/byte** across three runs against a **0.667 µs/byte** wire floor.

That is **2-3% overhead, not 41%**. The pipelined `spi_send()` already took the
win by keeping the shifter continuously fed.

This independently reproduces why the earlier DMA attempt was archived, and it
also revises the synthetic-benchmark conclusion that the path was CPU-bound with
1.11 µs/byte of overhead. Same artifact, measured two different ways.

## SCK is already at the ceiling, and cannot be swept

`f_SCK = 48 MHz / (2 x (BAUD+1))`, so the divider steps are:

| BAUD | SCK | period |
|---|---|---|
| 0 | 24.0 MHz | 41.7 ns |
| **1** | **12.0 MHz** | **83.3 ns** (current) |
| 2 | 8.0 MHz | 125 ns |

**There is no setting between 12 and 24 MHz.** The requested sweep cannot be
run; it is one binary step.

And the ceiling is the SAMD11, not the ECP5. Datasheet Table 35-50 gives `tSCK`
**min 84 ns — an 11.9 MHz rated maximum** in master mode. So BAUD=1 is already
marginally past rated and BAUD=0 would be 2x past.

Separately, `tMIS` (MISO setup) is 21 ns typical against a 20.8 ns half-period
at 24 MHz, so **duplex readback cannot validate 24 MHz even in principle** —
which removes the instrument that would have proved the wire still worked.

## Where the time actually is

Profiling a verified configure (`DONE=1`, `BSE_ERR=0`):

| | |
|---|---|
| end-to-end | 953-1017 ms |
| bulk shift | 679 ms |
| **of which wire time** | **67 ms** |
| **USB per-chunk overhead** | **612 ms (90% of the shift)** |

394 chunks, two control transfers each. **JTAG clocking is about 7% of
end-to-end.** Zeroing it entirely would take ~1000 ms to ~940 ms.

The levers that would matter are **chunk size** — capped by
`jtag_out_buffer[256]` and RAM-bound at 2728 of 4096 bytes — and **collapsing
`SET_OUT_BUFFER` + `SCAN` into one transfer**.

## The TX-only argument was correct, and still not worth doing

The reasoning holds and was verified: the ECP5 self-validates via
`LSC_RESET_CRC`, BSE code 3 is `CRC check failed`, and the host already
suppresses `GET_IN_BUFFER` for the burst. **Dropping TDO loses no error
detection.**

It would buy roughly **2 ms of ~950 ms**, which does not justify the
silent-failure surface it creates.

The stricter verification it implied was adopted anyway: every configure now
decodes `DONE`, `FAIL` and the BSE code explicitly rather than relying on an
exception not being raised.

## Repository state worth knowing

The `0xb8` synthetic benchmark and `debris/code/spi-dma-cynthion-d11.c` are
**not on main** — they live in other agents' worktrees, and the two apollo
commits have diverged. The flashed firmware has no `0xb8`, so the synthetic
benchmark was unavailable here; marginal-cost differencing measures the same
thing without needing new firmware.
