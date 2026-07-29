# HyperRAM: verified throughput and where it stops

The r1.4 HyperRAM is a 16-bit DDR self-refreshing DRAM on dedicated FPGA pins:
8 data lines (`F2 B1 C2 E1 E3 E2 F3 G4`), a differential clock pair
(`C3`/`D3`), `RWDS` (`D1`), `CS` (`B2`) and `RESET` (`C1`).

## Streaming: 2048-word burst

Write 2048 16-bit words, read back, gateware compares **every word** against the
pattern written and counts mismatches. No independent reference path exists —
nothing else on the board can read this chip — so the test is self-verifying by
construction, not by comparison.

| sync clock | write | read | errors | nextpnr timing | verdict |
|---|---|---|---|---|---|
| 60 MHz | 118.9 MB/s | 118.7 MB/s | 0 / 2048 | PASS 105/60 | **PASS** |
| 120 MHz | 237.8 MB/s | 237.3 MB/s | 0 / 2048 | *FAIL* 105/120 | **PASS** |
| 240 MHz | — | — | — | FAIL 124/240 | build refused |

120 MHz is the verified ceiling. Five reconfigurations returned bit-identical
cycle counts and zero errors each time.

Bus efficiency is 99.1% write, 98.9% read; the 19 and 23 spare cycles are the
command and latency phases, amortised over a 2048-word burst.

At 120 MHz nextpnr reports the design fails timing (105 MHz achievable against
120 required) and yet every word verifies, repeatably. Relying on a path the
tool says does not close is a deliberate choice. At 240 MHz nextpnr produces no
bitstream at all, so that is a hard stop.

## FIFO-style access: alternating writes and reads

A capture buffer does not get a 2048-word burst — writes and reads alternate and
every turnaround pays the command and latency phase again. `hyperram_fifo.py`
sweeps chunk size under that pattern: write N words, read N words back and
verify, repeat until 16384 words have moved each way, at every N from 8 to 4096.
The same volume moves at every chunk size, so cycle counts differ only by the
number of turnarounds.

| chunk | bytes | write | read | combined | % of streaming | errors |
|---|---|---|---|---|---|---|
| 8 | 16 | 68.6 MB/s | 56.5 MB/s | 61.9 MB/s | 26.1% | 0 |
| 16 | 32 | 106.7 MB/s | 91.4 MB/s | 98.5 MB/s | 41.5% | 0 |
| 32 | 64 | 147.7 MB/s | 132.4 MB/s | 139.6 MB/s | 58.8% | 0 |
| 64 | 128 | 182.9 MB/s | 170.7 MB/s | 176.6 MB/s | 74.4% | 0 |
| 128 | 256 | 207.6 MB/s | 199.5 MB/s | 203.4 MB/s | 85.7% | 0 |
| **256** | **512** | **222.6 MB/s** | **217.9 MB/s** | **220.2 MB/s** | **92.8%** | **0** |
| 512 | 1024 | 231.0 MB/s | 228.4 MB/s | 229.7 MB/s | 96.8% | 0 |
| 1024 | 2048 | 235.4 MB/s | 234.1 MB/s | 234.7 MB/s | 98.9% | 0 |
| 2048 | 4096 | 237.7 MB/s | 237.0 MB/s | 237.3 MB/s | 100.0% | 0 |
| 4096 | 8192 | 238.8 MB/s | 238.5 MB/s | 238.7 MB/s | 100.6% | 0 |

The combined figure is total bytes over total time, which is what a FIFO sees —
not the average of the two rates. All 163840 words verified against an
address-derived pattern with zero mismatches; a full rebuild and reconfiguration
returned bit-identical cycle counts.

One USB high-speed bulk packet is 512 bytes, which sits above the 90% mark
without tuning.

### Overhead is a constant, not a rate

Dividing each phase by its repetition count:

    cycles per write transaction = N + 20
    cycles per read transaction  = N + 26

Exactly 20 and 26 across all ten sizes, no size dependence. At N=8 the overhead
is 2.5–3x the payload; at N=4096 it is half a percent. This reconciles with the
streaming test independently: that measured 2067 write and 2071 read cycles for
2048 words against 2068 and 2074 predicted here.

Read costs 6 cycles more than write per turnaround — the read latency the write
path does not wait for.

### Margin over USB

The fastest USB direction measured on this board is **48.5 MB/s** (388.0 Mbps,
bulk IN on a direct root port). At 512-byte granularity HyperRAM sustains
220.2 MB/s for a simultaneous write-and-read FIFO — a **4.5x margin** — and
61.9 MB/s at the smallest chunk measured (1.3x). HyperRAM is not the constraint
at any chunk size worth using.

Topology matters more than either figure: through four hub levels USB drops to
36.5 MB/s (292.2 Mbps), which would put the margin at 6.0x. Quote the direct
number, since that is the configuration worth building for.

### Timing report disagreement

nextpnr reports this FIFO design *passing* at 175/120 MHz, where the streaming
build was reported *failing* at 105/120 MHz — same clock, same PHY, same
controller. The reports differ by 70 MHz on essentially the same critical path,
and both builds verify every word. Further evidence that the static estimate on
this path tracks something other than whether the design works.

### The limit is the fabric, not the chip

The stop at 240 MHz is a place-and-route failure in this design, not a device
limit. Raising it further is a matter of pipelining or using the DQS PHY.

Available clocks are only **60, 120 and 240 MHz**: `LunaECP5DomainGenerator`
drives the sync domain from one of three PLL outputs and raises `KeyError` for
anything else, so the region between 120 and 240 cannot be explored without a
custom PLL.

## Why the non-DQS PHY

LUNA ships two: `HyperRAMPHY`/`HyperRAMInterface` and
`HyperRAMDQSPHY`/`HyperRAMDQSInterface`. The DQS variant uses the ECP5's DQS
hardware (`DQSBUFM`, `TSHX2DQSA`, `DDRDLLA`) and should reach higher rates,
because the strobe travels with the data rather than timing being estimated.

It cannot be used on this board as written: it assigns to `bus.clk` as a single
net, but the platform declares the HyperRAM clock as a **differential pair**, so
the assignment fails. Interposing a buffer does not help — nextpnr requires
`DELAYG` to sit directly on a top-level pin and fails packing with *"must be
connected directly to top level input or output"*. Making the DQS path work means
changing the platform's clock declaration or adapting the PHY.

## Trap: the interface is 16 bits, not 32

`HyperRAMInterface` is **16 bits wide, not 32**. A 32-bit test against it returns
data that looks exactly like a bit-shift — low byte correct, upper bits displaced
by a consistent amount — a convincing impersonation of a timing or sampling
fault. Capturing the actual bytes into block RAM settles it: the displacement is
too regular across every word to be noise.

## Comparison

| Device | Interface | Verified rate |
|---|---|---|
| HyperRAM | 16-bit DDR @ 120 MHz | **237.3 MB/s** |
| Config flash, quad | 4-bit SDR @ 30 MHz | 14.92 MB/s |
| Config flash, single | 1-bit SDR @ 30 MHz | 3.75 MB/s |

~16x the quad flash rate, which is what a 16-bit DDR bus against a 4-bit SDR one
should give. Flash is a read-mostly store with 45–400 ms sector erases; this is
true random-access memory.

## Appendix: USB throughput reference

| source | Mbps | MB/s |
|---|---|---|
| USB 2.0 high-speed line rate | 480.0 | 60.0 |
| Protocol maximum, 13 × 512 B per microframe | 426.0 | 53.2 |
| **Measured, direct root port** | **388.0** | **48.5** |
| Measured, four hub levels deep | 292.2 | 36.5 |
| HyperRAM FIFO at 512-byte granularity | 1762 | 220.2 |

388.0 Mbps is 91.1% of protocol maximum; the remaining 9% is host-controller
scheduling, outside the device. HyperRAM has 4.5x headroom over the fastest the
USB link delivers, so USB is the constraint. Derivation of the 426 Mbps figure
and the gateware instrumentation that rules out the device are in
`usb-performance.md`.
