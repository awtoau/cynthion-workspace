# Where `apollo configure` actually spends its time

Measured on Cynthion r1.4, SAMD11D14AM, firmware `a7b8283`, 2026-07-29.
Bitstream `ecp5-test/led_patterns.bit`, 100698 bytes. Every configure quoted here
was verified through the ECP5 status register (`DONE=1`, `FAIL=0`, `BSE_ERR=0`);
an unverified configure is not evidence, because a configure that silently does
nothing looks fast on a stopwatch.

Instruments (all committed, all logging to `./tmp/logs/`):

- `scripts/probe_scan_effect.py` — marginal per-byte clocking cost, by differencing
  scans of different bit counts over the same USB round trip.
- `scripts/verify_configure.py` — configure + explicit `DONE`/`FAIL`/BSE decode.
- `scripts/profile_configure.py` — per-call-site breakdown of one real configure.

## The headline

| | ms | % of configure |
|---|---|---|
| bulk bitstream shift | 679 | 71% |
| small control shifts (14 calls) | 94 | 10% |
| `run_test` | 13 | 1% |
| host-side / idle | 168 | 18% |
| **total** | **953** | |

And within that 679 ms bulk shift:

| | ms | % of bulk |
|---|---|---|
| actual JTAG wire time | 67 | 10% |
| USB per-chunk overhead | 612 | **90%** |

**JTAG clocking is ~7% of end-to-end configure time.** Reducing it to literally
zero would take a ~1000 ms configure to ~940 ms.

## Two premises that do not survive measurement

### 1. There is no CPU term left for DMA to remove

The claim that the SPI loop costs ~0.455 µs/byte of CPU on top of a 0.667 µs/byte
wire comes from dividing a whole SCAN call by its payload, which charges the fixed
~145 µs USB round trip to per-byte clocking.

Measuring the *marginal* cost instead — differencing 64-bit and 2048-bit scans over
the same round trip — gives, across three runs:

```
0.663, 0.684, 0.715 µs/byte   (mean ~0.687)
wire floor at 12 MHz          0.667 µs/byte
```

The polled loop is already at the wire floor; CPU overhead is **2–3%**, not 41%.
The pipelined `spi_send()` (keep the transmitter one byte ahead of the receiver, so
the shifter never idles while the CPU collects the previous byte) already took that
win. TX-only DMA would be removing a cost that is no longer there.

This independently reproduces the conclusion already recorded in the archived DMA
implementation, which was built, flashed and hardware-verified and then *not*
shipped because it measured slightly slower (0.880 vs 0.770 µs/byte) while costing
+348 B flash and +96 B RAM.

### 2. SCK cannot be raised — 12 MHz is the rated ceiling, and we are at it

SERCOM SPI baud on this part is `f_SCK = f_GCLK / (2 × (BAUD + 1))`, with
GCLK0 = 48 MHz (`CONF_CPU_FREQUENCY`). So the divider is not a continuum:

| BAUD | SCK |
|---|---|
| 0 | 24 MHz |
| **1 (current)** | **12 MHz** |
| 2 | 8 MHz |

There is no setting between 12 and 24 MHz — a "sweep the divider upward until
readback breaks" cannot be run, because the next step up is the *only* step up.

And 24 MHz is out of spec by 2×. SAM D11 datasheet Table 35-50 (SPI timing
characteristics), master mode:

- `tSCK` **min 84 ns** → **11.9 MHz maximum SCK**
- feature list, §26: *"Master operation: Serial clock speed up to 12MHz"*

BAUD=1 gives tSCK = 83.3 ns, i.e. the current setting is already at (marginally
past) the rated limit. BAUD=0 gives 41.7 ns, half the rated minimum.

Note also `tMIS` (MISO setup to SCK) = 21 ns typical, against a 20.8 ns half-period
at 24 MHz. Duplex readback cannot meet MISO setup at 24 MHz even typically — so a
"keep a duplex readback mode to verify the wire at higher speeds" plan cannot
validate 24 MHz operation regardless of how the bulk path is written.

The ceiling here is the SAMD11's SERCOM, not the ECP5's TCK rating and not board
routing.

## What TX-only would and would not buy

Dropping TDO readback on the bulk path is *correct* — the ECP5 validates the stream
itself via `LSC_RESET_CRC` and reports failures as BSE error code 3 (`CRC check
failed`) through `STATUS_FLAG_FAIL`, so per-byte readback was never the integrity
mechanism. The host already discards it and `GET_IN_BUFFER` is already suppressed
for the burst (`ignore_response=True` in `_scan_data_chunk`).

But the firmware-side saving is bounded by the 2–3% of clocking that is CPU, which
is ~2 ms of a ~950 ms configure. It is not worth new code and new silent-failure
surface.

## Where the time actually is, if this is revisited

612 ms of USB per-chunk overhead across 394 chunks — ~1.55 ms per chunk, spread
over **two** control transfers each (`SET_OUT_BUFFER` then `SCAN`).

The chunk size is 256 bytes, fixed by `jtag_out_buffer[256]` in
`firmware/src/jtag.c` and advertised to the host as `max_bits_per_scan`. Halving
the chunk count halves this term. The binding constraint is RAM: the build uses
2728 B of 4096 B, leaving ~1368 B free, so the buffer cannot grow far without
finding RAM elsewhere.

The other available lever is transfers-per-chunk: `SET_OUT_BUFFER` + `SCAN` are two
round trips where a single combined request carrying both payload and bit count
would be one. That is a protocol change, not a clocking change.

Either of those attacks the 90%. Neither the DMA nor the SCK work attacks anything
larger than the 10%.
