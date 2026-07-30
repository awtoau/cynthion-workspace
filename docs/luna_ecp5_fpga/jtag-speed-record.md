# JTAG configuration speed: the comparable record

One place for the numbers, because there was not one and it caused real errors.

## Why this file exists

The docs in this directory contain roughly **forty distinct millisecond figures**
for configuration timing, with no way to tell which measured the same work. They
were all honestly recorded and they are not comparable: different bitstreams,
different firmware, different scopes (whole configure versus shift path versus a
single `SCAN` call).

Two consequences that actually happened, both today:

- A 1680 ms figure was compared against 890 ms and reported as **1.89x**. The two
  came from different bitstreams. Same payload, the real figures were 846 and
  774 ms — a 1.09x change reported as nearly double.
- `2575 -> 1680 ms` and `953-1017 ms` both sit in these docs labelled as configure
  time. Both are correct. They measure different payloads on different firmware.

So the answer to "are we recording the improvements" was **yes, and unusably**.

## The standard test

`scripts/jtag_fixed_benchmark.py`, committed payload of **122880 bytes**.

Fixed rather than a real bitstream because `top.bit` changes size whenever the
gateware does, so a "faster" result can be a smaller bitstream. 122880 sits
mid-range of the real bitstreams here (103364 to 136184 bytes) and divides evenly
by 128/256/512/1024, so no chunk size gets a short final transfer the others do
not.

**Scope: the shift path only.** No `ISC_ENABLE`, no CRC check, no DONE poll, TAP
left in SHIFT-DR. The FPGA ignores the data, so its configuration is untouched and
this is safe to repeat against a board that is doing something else. The absolute
number is therefore lower than a real configure by whatever that sequence costs.

Best-of rather than mean: USB scheduling adds latency and never removes it, so the
minimum is closest to the path's real cost.

## The record

Firmware `v1.1.1-40-g246c4a9`, 122880-byte payload, best of three:

| chunk | chunks | best | throughput | spread |
|---|---|---|---|---|
| 128 B | 960 | 667.6 ms | 184.1 KB/s | 1.8 ms |
| 256 B | 480 | 558.8 ms | 219.9 KB/s | 3.3 ms |
| **512 B** | **240** | **488.9 ms** | **251.3 KB/s** | 3.1 ms |

Spread of 1.8-3.3 ms, against 4-15 ms for the real-bitstream comparison. That
matters for what comes next: the remaining levers are predicted to save less than
the chunk change did, so the instrument has to resolve a few milliseconds.

## What has been done, and what it bought

Ordered by when, with the caveat that only the last row is on the standard test.

| change | effect | measured how |
|---|---|---|
| Pipelined `spi_send()` | clocking to within 2-3% of the wire | marginal cost differencing |
| Suppress TDO readback | 28% of runtime | whole-configure timing |
| Chunk 256 -> 512 B | **72 ms** (846 -> 774 real bitstream), **70 ms** on the standard test | both |

The first two predate the standard test and cannot be restated on it without
reverting the firmware, which is not worth doing — their mechanisms are
established independently.

## What is left

| lever | status | expected |
|---|---|---|
| **Double-buffer USB fill against SPI clocking** | untried | **the largest remaining** |
| Collapse `SET_OUT_BUFFER` + `SCAN` | untried | smaller than the chunk change |
| Chunk 512 -> 1024 B | blocked on RAM | ~35 ms by extrapolation |

**Double buffering is the big one and the reason is structural.** `spi_send()` is
already pipelined *internally* — one byte queued behind the one in flight, which is
why marginal clocking sits at 0.663-0.715 µs/byte against a 0.667 µs/byte wire
floor. There is nothing left to win inside a chunk.

But at the *chunk* level the path is strictly serialised: USB fill, then clock out,
then fill, then clock out. Nothing overlaps. With two buffers the next chunk's USB
transfer could proceed while the current one clocks, which is a different kind of
win from shaving request count.

**Chunk 1024 is blocked by RAM, not the protocol.** Two buffers at 1024 puts static
RAM at 96.5% of 4 KB. The measured stack high-water is 344 bytes of a 1024-byte
reservation, so there is real margin — but not enough to leave 144 bytes of slack on
a part with no MPU, where an overflow is silent `.bss` corruption. The buffers being
mutually exclusive with the console ring (#103) is the change that would free it.

## Rejected, with reasons

Recorded so they are not revisited.

**SCK 12 -> 24 MHz.** `f_SCK = 48 MHz / (2 x (BAUD+1))`, so the steps are 8 / 12 /
24 with nothing between. The ceiling is the SAMD11, not the ECP5: datasheet Table
35-50 gives `tSCK` min 84 ns, an 11.9 MHz rated maximum, so 12 MHz is already
marginally past rated. Separately `tMIS` is 21 ns against a 20.8 ns half-period at
24 MHz, so duplex readback could not validate it even in principle.

**SERCOM DMA.** Implemented, measured, marginally *slower* — 1711-1751 ms against
1698-1715 ms polled. The "0.455 µs/byte CPU term" that justified it was an artifact
of dividing a whole `SCAN` call by its payload, which charges a fixed ~145 µs USB
round trip to per-byte clocking. Archived at `debris/code/spi-dma-cynthion-d11.c`.

**TX-only (dropping TDO entirely).** Correct reasoning — the ECP5 self-validates by
CRC, so no error detection is lost — and worth about 2 ms of 950. Not worth the
silent-failure surface.
