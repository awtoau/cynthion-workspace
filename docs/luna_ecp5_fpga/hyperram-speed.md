# HyperRAM: verified throughput and where it stops

The r1.4 HyperRAM is a 16-bit DDR self-refreshing DRAM on dedicated FPGA pins:
8 data lines (`F2 B1 C2 E1 E3 E2 F3 G4`), a differential clock pair
(`C3`/`D3`), `RWDS` (`D1`), `CS` (`B2`) and `RESET` (`C1`).

## Measured results

Every figure is a write of 2048 16-bit words followed by a read back, with the
gateware comparing **every word** against the pattern it wrote and counting
mismatches. There is no independent reference path here — nothing else on the
board can read this chip — so the test is self-verifying by construction rather
than by comparison.

| sync clock | write | read | errors | nextpnr timing | verdict |
|---|---|---|---|---|---|
| 60 MHz | 118.9 MB/s | 118.7 MB/s | 0 / 2048 | PASS 105/60 | **PASS** |
| 120 MHz | 237.8 MB/s | 237.3 MB/s | 0 / 2048 | *FAIL* 105/120 | **PASS** |
| 240 MHz | — | — | — | FAIL 124/240 | build refused |

**120 MHz is the verified ceiling: 237.3 MB/s read, 237.8 MB/s write.** Five
reconfigurations returned bit-identical cycle counts and zero errors each time,
so this is a stable operating point rather than a lucky sample.

Bus efficiency is 99.1% on write and 98.9% on read — the 19 and 23 spare cycles
are the command and latency phases, which a 2048-word burst amortises almost
completely.

### Timing closure is not the same as working

At 120 MHz nextpnr reports the design *fails* timing (105 MHz achievable
against 120 required) and yet every word verifies, repeatably. Its static
estimate is conservative for this path. This is exactly why the ladder measures
data rather than trusting the report — and equally why the report is still
recorded in the table, because relying on a path the tool says does not close
is a deliberate choice, not an accident.

At 240 MHz nextpnr refuses outright (124 MHz achievable) and produces no
bitstream, so that is a hard stop rather than a judgement call.

### The limit is the fabric, not the chip

The stop at 240 MHz is a place-and-route failure in our design. The part itself
is rated far higher, and the 120 MHz result is not near any device limit —
raising it further is a matter of pipelining the design or using the DQS PHY
(see below), not of the RAM giving up.

Note the available clocks are only **60, 120 and 240 MHz**:
`LunaECP5DomainGenerator` drives the sync domain from one of three PLL outputs
and raises `KeyError` for anything else, so the interesting region between 120
and 240 cannot be explored without a custom PLL.

## Why the non-DQS PHY

LUNA ships two: `HyperRAMPHY`/`HyperRAMInterface` and
`HyperRAMDQSPHY`/`HyperRAMDQSInterface`. The DQS variant uses the ECP5's DQS
hardware (`DQSBUFM`, `TSHX2DQSA`, `DDRDLLA`) and should reach higher rates,
because the strobe travels with the data rather than timing being estimated.

It cannot be used on this board as written. It assigns to `bus.clk` as a single
net, but the platform declares the HyperRAM clock as a **differential pair**, so
the assignment fails outright. Interposing a buffer does not help either:
nextpnr requires `DELAYG` to sit directly on a top-level pin and fails packing
with *"must be connected directly to top level input or output"*.

Making the DQS path work therefore means either changing the platform's clock
declaration or adapting the PHY, and is the obvious next step for going faster.

## A trap worth recording

`HyperRAMInterface` is **16 bits wide, not 32**. A 32-bit test against it
returns data that looks exactly like a bit-shift — low byte correct, upper bits
displaced by a consistent amount — which is a convincing impersonation of a
timing or sampling fault, and was briefly diagnosed as one here.

What settled it was the block-RAM capture: the displacement was too regular
across every word to be noise. The same technique had already corrected two
wrong conclusions in the flash work. A pass/fail count says something is wrong;
only the bytes say what.

## Comparison

| Device | Interface | Verified rate |
|---|---|---|
| HyperRAM | 16-bit DDR @ 120 MHz | **237.3 MB/s** |
| Config flash, quad | 4-bit SDR @ 30 MHz | 14.92 MB/s |
| Config flash, single | 1-bit SDR @ 30 MHz | 3.75 MB/s |

HyperRAM is ~16× the quad flash rate, which is what a 16-bit DDR bus against a
4-bit SDR one should give. For a RISC-V system this is the natural place for
anything write-heavy: flash is a read-mostly store with 45–400 ms sector
erases, while this is true random-access memory.
