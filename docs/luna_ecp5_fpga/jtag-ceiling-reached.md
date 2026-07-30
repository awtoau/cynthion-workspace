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

## The standard test, and why there was not one

Its absence was actively misleading, so this is now the only figure worth quoting.

These docs contained roughly **forty distinct millisecond figures** for
configuration timing with no way to tell which measured the same work. All
honestly recorded, none comparable: different bitstreams, different firmware,
different scopes -- whole configure versus shift path versus a single `SCAN` call.

Two errors that followed from that, both mine:

- I compared 890 ms against a recorded 1680 ms and reported **1.89x**. The two came
  from different bitstreams. Same payload the figures were 846 and 774 ms, a 1.09x
  change reported as nearly double.
- The `2575 -> 1680 ms` above and the `953-1017 ms` in the section below are both
  labelled configure time. Both are correct. They measure different payloads on
  different firmware.

**`scripts/jtag_fixed_benchmark.py`, committed payload of 122880 bytes.** Fixed
rather than a real bitstream because `top.bit` changes size whenever the gateware
does, so a "faster" result can be a smaller bitstream. The size sits mid-range of
the real bitstreams here (103364 to 136184 bytes) and divides evenly by
128/256/512/1024, so no chunk size gets a short final transfer the others do not.

Scope is the **shift path only**: no `ISC_ENABLE`, no CRC check, no DONE poll, TAP
left in SHIFT-DR. The FPGA ignores the data, so its configuration survives and the
benchmark is safe to repeat against a board that is doing something else. The
absolute number is correspondingly lower than a real configure.

| chunk | chunks | best | throughput | spread |
|---|---|---|---|---|
| 128 B | 960 | 667.6 ms | 184.1 KB/s | 1.8 ms |
| 256 B | 480 | 558.8 ms | 219.9 KB/s | 3.3 ms |
| **512 B** | **240** | **488.9 ms** | **251.3 KB/s** | 3.1 ms |

Spread of 1.8-3.3 ms against 4-15 ms for real-bitstream timing. That matters: the
remaining levers are expected to save less than the chunk change did, so the
instrument has to resolve a few milliseconds.

## The 1.53x claim, restated on the standard test: it is 1.13x

`scripts/jtag_speed_bisect.py` reflashes historical firmware and benchmarks each
commit, so claims made before the standard test existed get real numbers rather
than being taken on trust.

Only four commits between the stock `v1.1.1` tag and HEAD touch the JTAG or SPI
path, so this is five direct measurements rather than a search. All at 256 B/chunk,
because 512 needs `GET_INFO`, which only HEAD implements -- on older firmware the
host silently falls back to 256, so a "512" row would read as no change.

| commit | what | 256 B/chunk |
|---|---|---|
| `4bf7691` | enable LTO | 639.1 ms |
| **`e034daa`** | **pipelined `spi_send` + suppress TDO readback** | **566.9 ms** |
| `0e9bfb1` | `JTAG_BUFFER_SIZE` define, no functional change | 562.1 ms |
| `HEAD` | 512-byte buffers + `GET_INFO` | 555.4 ms |

**So `e034daa` is 639 -> 567 ms, or 1.13x -- not the 1.53x recorded at the time.**
That original figure was a whole-configure measurement on a different payload, and
it is not wrong so much as not comparable. On identical work the change is real and
about a quarter the size claimed.

The `0e9bfb1` row is the useful control: a pure refactor, and it moves 4.8 ms, which
sets the noise floor for reading the others.

Stock `v1.1.1` could not be measured. Two artifacts of mixing eras rather than facts
about it: `SOURCES` is a wildcard over `src/*.c`, so files this project added stay in
the build after an older checkout (they are untracked there, so git leaves them),
and building `stack_probe.c` against stock fails on `-Werror=array-bounds` and then
at **107% ROM / 103% RAM** because stock has no LTO. The script now sets such files
aside per commit. That the stock Makefile cannot fit our firmware is itself the
clearest evidence LTO is load-bearing here.

## Repository state worth knowing

The `0xb8` synthetic benchmark and `debris/code/spi-dma-cynthion-d11.c` are
**not on main** — they live in other agents' worktrees, and the two apollo
commits have diverged. The flashed firmware has no `0xb8`, so the synthetic
benchmark was unavailable here; marginal-cost differencing measures the same
thing without needing new firmware.
