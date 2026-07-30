# SERCOM DMA does not help, and the number that motivated it was an artifact

> **RETIRED 2026-07-31. THE TITLE OF THIS DOCUMENT IS WRONG.**
>
> DMA is the single largest code win in this project: **-85 ms, 1.26x** (`d43f765`).
> This document is why it went untried for months, so it is kept as a record of how
> a correct measurement produced a wrong conclusion.
>
> What it measured was real. The implementation it tested **spun on `TCMPL` after
> arming the channels**, so the CPU was freed by DMA and immediately burned in a
> spin; `tud_task()` stayed blocked for the whole transfer and the result was
> polling plus setup cost. That is the +13 to +36 ms recorded below.
>
> DMA was never tested as an *asynchronous* mechanism. Arming the channels and
> returning -- polling completion from `jtag_scan_task()` -- is the entire difference
> between -36 ms and +85 ms.
>
> The narrower lesson, worth more than the result: **a negative result is only as
> broad as what was actually varied.** What varied here was *who clocks the bytes*,
> never *whether the CPU is free while they are clocked*.
>
> Current record: `docs/apollo_samd11_mcu/apollo-configure-speed-investigation.md`.


DMA was implemented, verified on hardware and measured. It is **marginally
slower** than the existing polled path, so it was not shipped. The useful part
is why.

## Measured

Cynthion r1.4, 304726-byte bitstream, every run verified `DONE=1` with no
FAIL/BSE bits.

| configuration | configure | marginal clocking | flash | RAM |
|---|---|---|---|---|
| polled, pipelined (baseline) | **1698-1715 ms** | 0.770 µs/B | 11232 B | 2728 B |
| DMA | 1711-1751 ms | 0.880 µs/B | 11580 B | 2824 B |

Had it shipped: +348 B flash, +96 B RAM. Affordable, and buying nothing.

## The 3.93 µs/byte figure was wrong

That number — quoted repeatedly as the bottleneck, and the entire justification
for DMA — came from dividing a whole 1006 µs `SCAN` call by its 256 bytes.
That charges the fixed ~200 µs USB round trip and per-call overhead to
*per-byte* clocking, inflating it by roughly 5x.

Measuring the **marginal** cost instead — differencing 64-bit and 2048-bit
scans against the same staged buffer, so the fixed cost cancels — shows the
polled loop was already at **0.770 µs/byte against a 0.667 µs/byte wire
floor**.

The earlier pipelining change had already taken that win. There was no
CPU-side bottleneck left to remove, so DMA only added per-chunk setup cost,
paid about 1200 times at a 256-byte chunk. Enlarging the buffer would not
rescue it: clocking is already at the wire rate.

## How it was caught

The baseline and DMA firmwares measured *identically* on the first isolation
attempt — 329.7 against 329.1 µs. Rather than accept that, a positive control
was written asserting that `SCAN` is accepted and that its cost actually scales
with bit count: **a stalled or no-op request looks exactly like a fast one on a
stopwatch.**

The first harness also used wrong request numbers — 0xb5 is `GOTO_STATE`, not
`SCAN`. Checking against the firmware enum rather than trusting plausible
timings is what surfaced it.

That is the second time in this project a positive control has invalidated a
measurement that otherwise looked clean.

## What this revises

The stated expectation was "DMA makes USB the bottleneck, so bulk streaming
becomes worth revisiting". **That does not follow — USB already was the
bottleneck.**

It also revises the earlier streaming result. Bulk streaming was explained away
as MCU clocking being the limit; that explanation was wrong, so streaming
failed for reasons still unexplained.

**The real remaining target is `SET_OUT_BUFFER`**: roughly 1000 ms of the
1680 ms, and almost entirely control-transfer bandwidth.

## Kept

The DMA implementation is archived at `debris/code/spi-dma-cynthion-d11.c`
with its measurements in the header. It works; the DMAC setup is fiddly enough
to be worth keeping if chunk size or SCK rate ever change the trade-off.
