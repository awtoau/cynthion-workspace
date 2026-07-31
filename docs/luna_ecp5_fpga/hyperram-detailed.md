# HyperRAM: what the part is, how much of it there is, and how fast it goes

Everything measured about the r1.4 HyperRAM, in one place.

## The part: a dual-die stack, and ID0 describes one die

Nothing in this workspace recorded what this chip was. Throughput had been characterised
in detail without anyone naming the manufacturer or the density, and the platform file
declares pins only. `scripts/hyperram_identify.py` asks the chip directly.

**ID0 `0x0c86`.** Decoded against the ISSI datasheets now in `sources/`:

| field | bits | value | meaning |
|---|---|---|---|
| die address | 15:14 | `00` | **die 0 of a stack** -- bottom die |
| row address bits | 12:8 | `01100` = 12 | 4096 rows |
| column address bits | 7:4 | `1000` = 8 | 9 column bits, 512 columns |
| manufacturer | 3:0 | `0110` = 6 | **not ISSI** -- the datasheet gives ISSI as `0011` |

So ID0 declares 4096 x 512 x 2 = **4 MiB for the die it is describing**, and bits 15:14
say that is die 0 of a stack rather than a whole part.

ID1 `0x0001`, CR0 `0x8f2f`, CR1 `0xffc1`.

## It holds 8 MiB, and the datasheet explains why

The measurement below found storage to 8 MiB against ID0's declared 4. **That is not
undocumented capacity.** `sources/ISSI-IS66WVH16M8-128Mbit-HyperRAM.pdf` states it
directly: *"The device is a dual die stack of two 64Mb die"*, and its table 5.2
repurposes ID0 bits 15:14 -- reserved on the single-die 64 Mbit part -- as a **Die
Address**, `00` for the bottom die and `01` for the top.

Two 64 Mb dies of 4 MiB each is 8 MiB, which is exactly what was measured.

**A larger stack was checked and ruled out on hardware.** The family does include a
256 Mbit part (`32M8`, named in the 128 Mbit datasheet's ordering table), so 4x was a
live possibility rather than a theoretical one. Probing 8, 16 and 24 MiB directly returns
`0x8484` at every point: the part ends at 8 MiB. It is the 128 Mbit dual-die device, not
the 256 Mbit one.

**This corrects an earlier reading of the same data.** Before the datasheets were
fetched, ID0's 4 MiB was taken as the whole part and the 8 MiB result was reported as a
part carrying twice its marking. The measurement was right; the interpretation was wrong,
because a single-die field was being read on a stacked part.

One field remains unexplained: the datasheet gives ISSI as manufacturer `0011` and this
part reports `0110`. So either it is not ISSI, or the encoding differs from these two
datasheets. Naming it ISSI was a guess from a lookup table written before the primary
source was available, and that guess is now removed from the tooling.

## The measurement itself

Asked because the ECP5 on this board carries more usable fabric than its marking implies
(`ecp5-test/fabric/FABRIC_TEST.md`, pluribus#98). See #109.

The boundary was **bracketed on hardware**, not assumed:

| probe point | result |
|---|---|
| 3.97 MiB | holds its marker |
| 6 MiB | holds its marker |
| 7.00 MiB | holds its marker |
| **7.94 MiB** | **holds its marker** |
| **8 MiB** | **`0x8484` -- nothing there** |
| 9, 10.5, 12 MiB | nothing there |
| **16 MiB, 24 MiB** | **nothing there** -- tested directly, so a 256 Mbit (4x) part is excluded |

The edge falls exactly on 2x the per-die capacity -- which is the second die ending,
exactly as a two-die stack predicts.

### Three ways this could have been fake, each tested

**Mirroring** -- the most likely way to be fooled. All four probe banks are written
*distinct* markers and all four are read back. If two addresses were the same storage,
the later write would clobber the earlier and a bank would hold the wrong value. Every
bank holds its own: `0xb000`, `0xb001`, `0xb002`, `0xb003`.

**Unbacked addresses.** An address with no storage returns `0x0000` or `0xFFFF` -- and
that is exactly what appears above 8 MiB, where the readback is `0x8484` rather than the
marker. Real storage returns the byte written, so the failure mode looks visibly
different from the success mode.

**Decay.** A 12 ms retention wait (720,000 cycles at 60 MHz) sits between the writes and
the readback. HyperRAM is DRAM: it self-refreshes on a ~64 ms per-row budget, and the
controller refreshes only the region it believes exists. Space above the declared end
would decay inside that window if it were unrefreshed. It does not.

### What this establishes, now that the datasheet is in hand

**8 MiB is real, documented capacity** -- a 128 Mbit dual-die part, not a 64 Mbit part
with a bonus. That is a stronger result than the original reading: the vendor *has*
committed to it, so a design can use it.

What the probe adds beyond the datasheet is that both dies are actually reachable and
independently addressable through the LUNA controller at 0-8 MiB linear, with no die-select
handling needed in gateware. That was not obvious in advance and is worth having.

Still not established: behaviour over temperature or over hours rather than milliseconds,
and whether other boards carry the same part.

### No temperature sensor -- and why that matters here

HyperRAM exposes no readable temperature. There is no ID or CR field returning a value.
What ISSI-class parts have instead is **temperature-compensated self-refresh**: the die
senses its own temperature and adjusts its refresh rate internally. That is a control
mechanism, not an instrument -- nothing to read out.

The registers as found on this board: **CR0 `0x8f2f`** decodes to normal operation (not
deep power down), initial latency 2, fixed latency set, wrapped burst, burst length 3.
**CR1 `0xffc1`** carries the distributed-refresh and PPR controls.

This bears directly on the 8 MiB result. **DRAM leaks faster when hot, so refresh margin
gets worse with temperature** -- and the controller only refreshes the 4 MiB it believes
exists. If the upper 4 MiB is refreshed at all it is by the die's own self-refresh, which
is exactly the mechanism that has to work harder when warm. A room-temperature 12 ms pass
is therefore the *easy* case, and that is why temperature is listed above as
unestablished rather than as a formality.

For a temperature-varying retention test the instrument is not in the HyperRAM: the
**ECP5 has an on-die temperature diode** (`DTR`, one of the six configuration primitives
catalogued in `../apollo_samd11_mcu/apollo-configure-speed-investigation.md`). The FPGA
sits beside the HyperRAM, so its die temperature is a usable proxy for local ambient.

### Contrast: the flash is exactly what it says

The same question asked of the configuration flash came back a clean negative -- SFDP
declares 4 MiB, everything above it aliases offset 0, 4-byte addressing absent. See
`flash-detailed.md`. Two parts on one board, same question, opposite answers.

## Three bugs found writing the probe, all one shape

Worth recording because they produced *plausible wrong answers* rather than failures.

`HyperRAMInterface` samples its control signals when it reaches the relevant internal
state, not when `start_transfer` is asserted:

- **`final_word`** is sampled when `read_ready` fires. Pulsing it at issue leaves the
  controller in `READ_DATA` forever -- it never returns to `idle`, and the FSM stalls
  with exactly one word captured. That presents identically to a device that answers
  once and then stops.
- **`perform_write` and `write_data`** are sampled when the controller reaches
  `WRITE_DATA`, several cycles later. Pulsing them wrote **zeros** -- which then read
  back as two banks "aliasing" when they were merely both zero. That is a false positive
  for the exact thing being tested.

`hyperram_stress.py` holds all three correctly; that is what working code looks like on
this interface.

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

## Open work

| issue | what | blocked on |
|---|---|---|
| **#90** | Wishbone peripheral, so a CPU can reach the HyperRAM at all. There is a working low-level driver and no bus adapter, which is why the memory work stalled while the flash work did not | nothing -- design work, survey already done |
| **#92** | Bring up the DQS path. The variant in use times reads against a fixed latency count; the DQS variant uses the ECP5's DQS hardware and is what would make 120 MHz safe rather than merely observed-working | #90, and a RISC-V to drive it |
| #91 | RISC-V bring-up | the SoC is silent; cause not established |
| #109 | The capacity question above | **answered: 8 MiB against a declared 4.** What remains is retention over hours rather than milliseconds, and whether other boards behave the same |

Two things this document reports that nothing can currently cross-check: **no independent
reference path exists**, because nothing else on the board can read this chip. Every
throughput figure is self-verifying by construction -- gateware comparing readback against
what it wrote -- rather than by comparison against another reader. #90 is what would
change that.

And the 120 MHz result relies on a path **nextpnr says does not close** (105 MHz
achievable against 120 required) while every word verifies repeatably. That is a
deliberate choice, and #92's DQS path is the principled fix.

---

# Merged from `hyperram-detailed.md`

**Conclusion: keep LUNA's**, with a specific measurable inefficiency worth
fixing, and one alternative worth borrowing interface shape from.

## Candidates found

| Project | Language | Licence | Last touched | Verdict |
|---|---|---|---|---|
| **LUNA** `HyperRAMInterface` | Amaranth | BSD-3 | in use | **Keep.** ECP5 DDR primitives, verified working here |
| **ChipFlow** `chipflow-digital-ip` | Amaranth | BSD-2 | Jan 2026 | Borrow ideas; no FPGA I/O |
| Squishy | Amaranth | BSD-3 | Jan 2026 | Empty stub, 27 lines |
| litex-hyperram | Migen | **none** | Dec 2019 | Unlicensed, 7 years stale |
| orbtrace | Migen | — | — | Wraps `litehyperbus`, not standalone |

Glasgow has **no** HyperRAM support. The search that mattered was for *Amaranth*
implementations specifically; Migen and LiteX ones exist but would need porting.

## Why LUNA wins

LUNA instantiates real ECP5 DDR hardware. HyperRAM is a DDR interface, and on an
FPGA that means vendor I/O primitives; it cannot be written portably.

The two PHYs use different primitives, which matters because only one of them
works on this board:

| PHY | primitives | on Cynthion r1.4 |
|---|---|---|
| non-DQS (`HyperRAMPHY`) | `ODDRX1F`, `IDDRX1F`, `DELAYF` | **the verified path** |
| DQS (`HyperRAMDQSPHY`) | `DQSBUFM`, `TSHX2DQSA`, `DDRDLLA` | unusable — no DQS pin group |

Every measurement in `hyperram-detailed.md` is on the non-DQS path.

ChipFlow's is the better-*structured* code by some distance, but it targets ASICs
and contains **no FPGA I/O primitives at all** — no `DDRBuffer`, no `Instance()`.
Adapting it means writing the ECP5 DDR layer from scratch, which is exactly the
part LUNA already has working.

LUNA's is verified on this board: 32 KiB bulk write/read, retention across ~6 ms,
and 4096 random-address operations, all with **zero errors** at 120 MHz
(`ecp5-test/hyperram/`).

## What ChipFlow does better

- **Latency is a runtime CSR**, a 4-bit read/write register field. LUNA
  hard-codes `LOW_LATENCY_CLOCKS = 7` and `HIGH_LATENCY_CLOCKS = 14`.
- **The Wishbone peripheral already exists.** ChipFlow's `data_bus` is a 32-bit
  Wishbone interface with byte granularity, plus a separate CSR control bus —
  exactly the wrapper missing on the LUNA side.

## The concrete inefficiency in LUNA

`HyperRAMInterface` samples RWDS correctly to detect whether the device is asking
for extra latency:

    m.d.sync += extra_latency.eq(self.phy.rwds.i)

and then discards the result:

    # FIXME: our HyperRAM part has a fixed latency, but we could need to detect
    # different variants from the configuration register in the future.
    with m.If(extra_latency | 1):
        m.d.sync += latency_clocks_remaining.eq(self.HIGH_LATENCY_CLOCKS-2)
    with m.Else():
        m.d.sync += latency_clocks_remaining.eq(self.LOW_LATENCY_CLOCKS-2)

`extra_latency | 1` is unconditionally true, so the low-latency branch is dead
code and every transaction takes the 14-clock path.

This is conservative rather than wrong — an acknowledged shortcut with a FIXME
against it, and the longer latency is always safe. It costs about **7 cycles per
transaction**.

Measured per-transaction overhead is **20 cycles for a write and 26 for a read**
at 120 MHz, constant across chunk sizes (`hyperram-detailed.md`). So the fixed
latency shortcut is roughly a third of that overhead. Against a 512-byte
transfer it disappears into the noise; against a single word it is most of the
cost.

Fixing it is a one-line change plus a test. Irrelevant for streaming FIFO use;
worth having if anything does small scattered accesses.

## Plan

The two projects are strong in non-overlapping places, so this is a stack rather
than a merge:

| Layer | LUNA | ChipFlow | Take |
|---|---|---|---|
| ECP5 DDR I/O primitives | yes | **none** | LUNA |
| HyperBus protocol FSM | yes | yes | LUNA — verified on this board |
| Latency as runtime CSR | no | yes | ChipFlow |
| 32-bit Wishbone data bus | no | yes | ChipFlow |
| CSR control registers | no | yes | ChipFlow |

1. **Keep `HyperRAMInterface` and `HyperRAMPHY` untouched.**
2. **Write a Wishbone wrapper on top**, shaped after ChipFlow's `data_bus`:
   32-bit, byte granularity, separate CSR bus for control.
3. **Make latency a CSR field** rather than a constant. That also gives somewhere
   to put the RWDS fix if it proves worthwhile — measure the gain first.
4. **Do not adopt ChipFlow wholesale.**

What is taken from ChipFlow is the **interface shape**, not the code — it has no
ECP5 layer, so nothing in it would function here even if copied verbatim. That
makes it a design reference rather than a dependency. Licences are compatible
regardless: BSD-2 into a BSD-3 codebase is fine.
