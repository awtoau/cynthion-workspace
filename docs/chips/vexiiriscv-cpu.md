# VexiiRiscv — the SoC's CPU

The RISC-V core in `gateware/soc/top.py`. Not a chip on the board:
it is a soft core inside the [ECP5](ecp5/lfe5u-12f.md), and it has a note here for
the same reason the flash and the HyperRAM do — what it actually does on this
board differs from what its parameters suggest, and the measurements have to live
somewhere they can be found.

**Index:** [`../hardware.md`](../hardware.md)

## Performance

Structure and rules: [`../plans/performance-sections.md`](../plans/performance-sections.md).

- **Almost every ceiling on this page is the fabric's, not the core's.** A soft
  CPU has no datasheet; what it can do is set by the parameters it was
  generated with and by what nextpnr can place. Keeping those two apart is
  most of the value here.
- Everything below is in **cycles and IPC**, which do not move when `SYNC_MHZ`
  does. `SYNC_MHZ` has been 30, 72, 30 and 60 in the last week
  (`git log -p gateware/soc/top.py`), so any MB/s figure is only meaningful
  with its clock attached; cycle counts are the durable form.

### 1. Theoretical maximum — the core's own parameters

| axis | figure | where it comes from |
|---|---|---|
| issue width | 1 lane, 1 decoder, in order | `GENERATE_FLAGS`, `ParamSimple` defaults |
| **IPC ceiling** | **1.00** | one lane retires at most one instruction per cycle |
| I-cache | 8 KiB — 64 sets × **2 ways** × 64 B line | `--fetch-l1-sets 64 --fetch-l1-ways 2` |
| D-cache | 8 KiB — 64 sets × **2 ways** × 64 B line | `--lsu-l1-sets 64 --lsu-l1-ways 2` |
| line length | 64 B | `LsuL1Plugin_logic_banks_0_mem` in the generated Verilog is 1024 words; 4 KiB over 64 sets |
| D-cache hit | 1 cycle by construction | block RAM is single-cycle on this part |
| BTB | 512 sets, 1 chunk, 16-bit hash | `--with-btb` at `Param.scala` defaults |
| return stack | **0** entries — `rasDepth` follows `--with-ras`, which is absent | `cpu.py` |
| perf counters | `mhpmcounter3..6` + `mcycle`/`minstret` | `--performance-counters 4` |
| **compressed instructions** | **on, and 50.1% of `.text`** | `--with-rvc`, and `riscv32imac` — see below |

### Compressed instructions are already on, and already paid for

Both halves are in place and neither needs changing: the core is generated with
`--with-rvc`, and the firmware target is **`riscv32imac`** — the `c` there *is*
the compressed extension. The board agrees: `misa 40001105`, bit 2 set, reported
by `info` as `rv32imac`.

Measured over the release binary, counting 16- against 32-bit encodings:

| | |
|---|---|
| 16-bit (compressed) instructions | 9,417 |
| 32-bit instructions | 9,383 |
| **compressed share** | **50.1%** |
| `.text` as built | 56,366 B |
| `.text` with no compression | 75,200 B |
| **saved** | **18,834 B — 25% smaller** |

That matters more here than on most targets, because **`.text` is this design's
binding constraint** rather than RAM: code is fetched from SPI flash through an
8 KiB I-cache, and `docs/rtic.md` measured 1,700 extra bytes of `.text` moving
frontend stalls from 44 per 1,000 cycles to 452. Compression is worth ~18.8 KB
against exactly that pressure.

**There is no further compressed extension available**, and this is worth
recording so it is not re-investigated:

- VexiiRiscv has no `Zca`/`Zcb`/`Zcmp`/`Zcmt`. Its only `Zc`-looking flag is
  `--with-rvZcbm`, which is **Zicbom** — cache-block management, a naming
  coincidence rather than a code-size extension.
- `rustc` 1.97 exposes only `c` as a target feature for
  `riscv32imac-unknown-none-elf`; there are no `zc*` features to enable.

`--with-rvZcbm` is still worth knowing about for an unrelated reason: it provides
`cbo.inval`, which `scripts/riscv_firmware.py` names as the proper replacement
for its read-the-whole-cache flush hack.

So the remaining code-size levers are not ISA ones. They are `core::fmt` (whose
`Formatter::pad`, `pad_integral` and `write` together are ~1.8 KB before any
`Display` impl), and `run` at **23,258 bytes** — the largest function in the
firmware by an order of magnitude.

**Two ways, since #292.** Both caches were direct-mapped, where two lines sharing
an index evict each other unconditionally — no associativity to absorb it, no
policy to tune. That is the conflict-miss case, and it is what RTIC's preempting
handlers hit: each is its own instruction working set, and a bigger cache does
not help two that collide.

- 2 ways, PLRU replacement, so a colliding pair can both stay resident
- 4 ways does not fit — 58 blocks on a 56-block die
- 3 ways does not exist — SpinalHDL's PLRU asserts `isPow2`
- capacity is not the lever here: 8 KiB direct-mapped (#283) preceded this and
  addressed the wrong failure mode

At `sync` = 60 MHz an IPC of 1.00 would be **60 MIPS**. Nothing here approaches
it, and section 3 says by how much.

### 2. Achievable on this board — the fabric binds, not the core

**nextpnr sets the clock, and it is a distribution rather than a number.**
`scripts/soc_timing_sweep.py` places and routes three times per configuration,
because `--parallel-refine` mutates a shared placement across sixteen threads and
the same netlist has spread 8 MHz between runs:

| configuration | LUT | FF | BRAM | fmax min / median / max |
|---|---|---|---|---|
| no predictor | 12,508 | 6,554 | 42 / 56 | 74.54 / 75.24 / 78.75 MHz |
| BTB relaxed + `--relaxed-branch` (ships) | 12,903 | 6,942 | 44 / 56 | 64.23 / **71.81** / 72.88 MHz |

So "the SoC runs at 71.81 MHz" is a placement statistic. Quoting the median
without the min is quoting a configuration that builds two times in three.

**The clearest proof that the fabric binds is the BTB.** `--with-btb` at the
generator's default `jumpAt = 1` closed at **57.55 MHz against a 60 MHz
constraint** — a hard fail — with the critical path starting at
`BtbPlugin_logic_mem.0.0.DOA8`: 4.10 ns of `DP16KD` clock-to-q before a single
LUT, then the 16-bit hash compare, the hit decision and the fetch redirect all in
one cycle. Nothing about *prediction* was wrong. `--relaxed-btb` moved the
redirect to `jumpAt = 2` and it closed. See the variants table below.

Three more places the fabric, not the core, is the limit:

- **`--btb-sets 128` bought nothing and still cost 44 BRAM**, because 128 × ~50
  bits does not fall below one `DP16KD` pair. The BTB's *size* was never the
  problem — its *depth in a cycle* was.
- **`--relaxed-branch` was worth 11 MHz for 0.7% of a cycle count**, and the path
  it fixed was routing-bound: 13 ns of routing against 3 ns of logic on a die
  52% full. That ratio is a placement fact.
- **The performance counters cost clock.** `firmware/cynthion-soc/src/bench.rs`
  records that the design stopped closing at 72 MHz when they were added, which
  is why `SYNC_MHZ` is 60. The instrument that measures the stalls is part of
  what sets the ceiling.

**Block RAM, not LUT, is what stops the caches growing.** From
`linux-on-cynthion/results/sweep_20260729.json`, 132 configurations — **that
sweep's fmax column is withdrawn; its area and BRAM rows stand** (they include
SoC glue, so they are not bare-core figures):

| cache sets | per cache | n | BRAM min–max (median) | LUT median |
|---|---|---|---|---|
| 0 (cacheless) | — | 3 | 8–8 (8) | 4,072 |
| 64 | 4 KiB | 43 | 10–20 (**18**) | 7,048 |
| 128 | 8 KiB | 43 | 13–24 (**22**) | 6,654 |
| 256 | 16 KiB | 43 | 17–32 (**30**) | 6,695 |

**LUT is flat across the whole range and BRAM is not.** Going from 4 KiB to
16 KiB caches costs **+12 blocks** at the median, which is exactly the
arithmetic: two caches × 12 KiB of extra data ÷ 2 KiB per `DP16KD` = 12. The SoC
places **44 of 56** today, so 16 KiB caches would need 56 of 56 and leave nothing
for anything else. See [`ecp5/bram-budget.md`](ecp5/bram-budget.md).

**The CPU's real clock ceiling is unmeasured.**
[`../soc-clocking.md`](../soc-clocking.md) §2 withdraws the "corrupts above
60 MHz" result: its signature — correct counter values, dropped characters, fine
while `sync == usb` — was the console's own `SyncFIFOBuffered` CDC bug. That FIFO
is fixed and the ladder has not been re-run. **No number in this repository
bounds this CPU's clock**, including ones in its own commit messages.

### 3. Measured

`scripts/soc_shell.py bench` on the board, all three regions walked with
`read_volatile`/`write_volatile` and `mcycle`/`minstret` around them. Cycle
counts are clock-invariant; the walk's own loop is fetched from flash, which
section 4 returns to.

| region | working set | pattern | cycles/access | what it exercises |
|---|---|---|---|---|
| block RAM | 2 KiB | read seq | **22.77** | every fetch an I-cache hit, every load a D-cache hit — the floor |
| block RAM | 8 KiB | read seq | 25.38 | one D-cache line refill per 16 accesses |
| block RAM | 8 KiB | write rnd | 84.07 | ~50% miss on a write-back cache |
| flash | 16 KiB | read seq | 43.99 | one 64-byte line per 16 accesses, quad SPI |
| flash | 16 KiB | read rnd | 352.66 | a line refill on essentially every access |
| HyperRAM | 4 KiB | read seq | 148.15 | CSR staging port — `main=0`, uncached by construction |

**Cycles per instruction, with nothing in the way: 3.23.** The block RAM 2 KiB
sequential row is seven instructions per access (22.77 × IPC 0.309 = 7.04), so
22.77 / 7.04 = 3.23 cycles per instruction with no memory, no miss and no
mispredict. Before the BTB the same row read 28.77 at IPC 0.244 — 7.02
instructions, **4.09 cycles each**.

**IPC: 0.309 at best, 0.056 at worst, and the axis is code size.** From
`./dev.py optlevel` (#167), the whole firmware rather than a walk:

| `opt-level` | `.text` | IPC | flash seq |
|---|---|---|---|
| `z` | 36,160 | **0.302** | 9.63 MB/s |
| `s` | 43,348 | 0.171 | 11.11 |
| `3` | 64,576 | 0.056 | 11.57 |

79% more code costs **5.4× the IPC**, and flash throughput *rises* while it
happens — the link got faster and the CPU got five times slower, so what changed
is how often it has to go there. 36 KB of `.text` against 4 KiB direct-mapped is
the entire mechanism. This is why `opt-level = "z"` is set for speed.

**Cache miss cost, derived from adjacent rows** — not measured in isolation, and
labelled so:

| | derivation | cycles |
|---|---|---|
| block RAM line refill | (25.38 − 22.77) × 16 accesses per line | **≈ 42** |
| flash line refill, quad SPI | 352.66 − 43.99 | **≈ 309** |
| D-cache hit | by construction, never isolated | 1 |

**Fetch versus data stalls: NEVER MEASURED.** This is the row the counters exist
for, and there is no reading in this tree.

The hardware is there. `--performance-counters 4` is in `GENERATE_FLAGS`, and
`bench.rs`'s `hpm` module writes `mhpmevent3..6` and reads `mhpmcounter3..6`:

    0x04  STALLED_CYCLES_FRONTEND   waiting on instruction fetch
    0x05  STALLED_CYCLES_BACKEND    waiting on data
    0x19  DCACHE_LOAD_MISS
    0x1A  DCACHE_WAITING

It is wired into the HyperRAM memory-window walk only, and **no counter output is
recorded anywhere in this repository.** The one figure attributed to them — *"the
CPU spent 79% of every cache line stalled on instruction fetch"*, with HyperRAM
reading 13.3 MB/s while the bus did 63.1 — appears in the doc comments of
`bench.rs` and `gateware/soc/cpu/cpu.py` and nowhere else. **Treat it as the
reason the counters were added, not as a counter reading.**

One trap for whoever runs them: `firmware/cynthion-soc/src/metrics.rs:75` states
that `mhpmcounter3..31` "decode and read hardwired zero" because "nothing passes
`--performance-counters N`". That is **stale** — `cpu.py:123` passes it — and
`metrics.rs` is the file most likely to be read first.

### 4. The gap, and what closes it

Ranked, with what each is worth.

1. **The I-cache, and it is most of the gap.** 4 KiB, one way, against 36 KB of
   `.text`. Worth: the `opt-level` table brackets it at **5.4× in IPC** from code
   size alone, and that is a lower bound on what associativity or capacity could
   recover. Cost: +12 BRAM for 16 KiB caches, and there are exactly 12 free.
   Whether it then closes timing is **unknown**.
2. **Firmware out of block RAM.** 64 KiB of program memory is ~32 blocks. Execute
   in place from flash, or from HyperRAM, and the cache budget above stops being
   all-or-nothing. Cost: fetch latency — which is what the I-cache exists to
   hide, so the order matters and (1) has to come with it.
3. **`--with-ras`.** `rasDepth = 4` inside `BtbPlugin`, cheap in area, and a
   shell returns constantly. **Never measured.** Worth: unknown, and it is the
   obvious next one.
4. **The clock.** nextpnr's median is 71.81 MHz against the 60 MHz the design
   ships at, so ~20% is sitting in the placement statistic — and the *real*
   ceiling is unmeasured above that. Worth: 20% of throughput at least, for the
   price of re-running `nextpnr_allow_fail_ladder.py` with the fixed
   `StreamBuffer` and a readout that is not the console. Every cycle count above
   is clock-invariant, so this is pure gain.
5. **Read the counters.** Not a gap in performance but a gap in knowing where the
   gap is: the split between frontend and backend stalls would rank items 1–3
   instead of leaving them argued. Costs one `bench` run.
6. **`GSharePlugin`.** Needs `withBtb`, adds a 4 KiB history table — three more
   `DP16KD` against 44 of 56 — and a `HistoryPlugin` on the fetch path that had
   to be relaxed twice to reach 71 MHz. It only improves direction prediction for
   branches the BTB already has a target for. The BRAM makes it last.

### Summary

| path | theoretical | board max | measured | % of board max | what closes the gap |
|---|---|---|---|---|---|
| IPC, tight loop all hits | 1.00 (single issue, in order) | 1.00 — the fabric does not lower it | **0.309** — block RAM 2 KiB seq | 31% | I-cache; RAS; unknown remainder |
| IPC, whole firmware | 1.00 | 1.00 | **0.302** at `opt-level = "z"`; 0.056 at `3` | 30% / 6% | I-cache size — 4 KiB against 36 KB `.text` |
| cycles per instruction | 1.00 | 1.00 | **3.23**, nothing in the way (was 4.09 before the BTB) | 31% | fetch pipeline depth |
| D-cache hit | 1 cycle | 1 cycle | 1 by construction, **never isolated** | — | a targeted micro-walk |
| block RAM line refill | — | — | **≈ 42 cycles**, derived from two rows | — | — |
| flash line refill | — | — | **≈ 309 cycles**, derived; quad SPI at 144 MHz SCK | — | SCK is already at the instrument's limit |
| HyperRAM, CSR staging port | — | uncached by construction (`main=0`) | 148.15 cycles/access | — | the memory window (#90), not this port |
| fetch stall fraction | 0% | — | **NEVER MEASURED** — counters present, nothing recorded | — | one `bench` run; read `mhpmcounter3` |
| data stall fraction | 0% | — | **NEVER MEASURED** | — | same run, `mhpmcounter4` |
| clock | 400 MHz PLL `fOUT` | 71.81 MHz median nextpnr, 3 runs; **true ceiling unmeasured** | 60 MHz shipping | 84% of the median | re-run the ladder with the fixed `StreamBuffer` |
| BRAM budget | 56 on the die | 56 | 44 used; 16 KiB caches would need 56 | 79% | firmware out of block RAM |

## Not committed — generated at elaboration

`gateware/soc/cpu/cpu.py` runs the Scala generator on every build and hands
Amaranth the resulting `VexiiRiscv.v`. A checked-in Verilog would drift silently
from the flags that produced it; a nine-second `sbt` run would not.

So **every parameter below is a flag change plus a rebuild, not a source change**,
and there is no generated file to keep in step.

Source: `repos/vexiiriscv`, options in `src/main/scala/vexiiriscv/Param.scala`.
The choice of VexiiRiscv over VexRiscv, and cached over cacheless, is in
[`../architecture.md`](../architecture.md).

## Configuration

`GENERATE_FLAGS` in `gateware/soc/cpu/cpu.py`. RV32IMAC with `rdtime`,
single issue, Wishbone throughout, plus the debug module on the existing JTAG
chain.

| | |
|---|---|
| ISA | RV32IMAC, `--with-rdtime` |
| issue width | 1 lane, 1 decoder |
| I-cache | **8 KiB** — 64 sets × 2 ways × 64 B line |
| D-cache | **8 KiB** — 64 sets × 2 ways × 64 B line |
| buses | three masters: `ibus`, `dbus` (cached), `iobus` (uncached, for peripherals) |
| interrupts | one machine-external wire; concentration is the [PLIC](../hardware.md#register-reference)'s job |
| debug | `--debug-jtag-instruction`, ER1/ER2 on the TAP Apollo already owns |
| prediction | `BtbPlugin`, 512 sets, relaxed; no `GSharePlugin`, no `RasPlugin` |

**All three bus flags are needed.** `--fetch-wishbone --lsu-wishbone
--lsu-l1-wishbone` — a cached core still has an uncached LSU path, and omitting
`--lsu-wishbone` leaves it native and unconnected. The only symptom is undriven
`LsuPlugin_logic_bus_*` wires.

**Every memory region must be declared.** `defaultPma` covers 0x80000000 and
0x10000000 only, and this SoC has its block RAM at 0. An undeclared region traps
on every access including stack pushes, and presents as a dead CPU.

## What it costs, and what it runs at

Whole SoC, not the CPU alone, from `scripts/soc_timing_sweep.py` — three
place-and-route runs per configuration, because one build cannot support a claim
here: `--parallel-refine` mutates a shared placement across sixteen threads and
the same netlist has spread 8 MHz between runs.

| | LUT | FF | BRAM | Fmax min / median / max |
|---|---|---|---|---|
| no predictor | 12508 | 6554 | 42 / 56 | 74.54 / 75.24 / 78.75 MHz |
| BTB, relaxed, `--relaxed-branch` | 12903 | 6942 | 44 / 56 | 64.23 / **71.81** / 72.88 MHz |

Constraint is 60 MHz. BRAM is the tight resource on this die, at 75% before the
BTB and 79% after.

**A cached core is bigger than a cacheless one, and an early sweep said the
opposite by more than 5x.** Its wrapper tied the L1 `cmd_ready` low, so the cached
cores could never fetch and were pruned as trivially small. Repaired, CPU alone:

| configuration | LUT | BRAM |
|---|---|---|
| cacheless | 4072 | 8 |
| i4k + d4k, as first measured | 967 | — |
| i4k + d4k, wrapper repaired | **5212** | 14 |

Kept because the failure is silent: a harness that prevents the thing it measures
from working reports a small, plausible number rather than an error.

## The fetch floor, and the branch predictor (NEW, 2026-08-03)

`bench` walks seven instructions per access with **every one hitting the
I-cache**, so its block RAM sequential row is a direct reading of the fetch path
with no memory in the way. It read 28.77 cycles for those seven instructions —
four cycles an instruction, IPC 0.244 (#140).

The cause was that the core was generated with `BranchPlugin` and `LearnPlugin`
and **no `BtbPlugin`**: branches were resolved and recorded, but nothing acted on
the record, so every taken backward branch redirected the three-stage fetch.

One row per commit. `scripts/soc_shell.py bench` on the board; Fmax over three
sweep runs each.

| commit | change | metric | before | after | factor |
|---|---|---|---|---|---|
| `btb` | `--with-btb --relaxed-btb --relaxed-branch` | block RAM 2 KiB read seq, cycles/access | 28.78 | 22.77 | **1.26×** |
| `btb` | — same commit | block RAM 2 KiB read seq, IPC | 0.244 | 0.309 | 1.27× |
| `btb` | — same commit | block RAM 8 KiB read seq, cycles/access | 31.54 | 25.38 | 1.24× |
| `btb` | — same commit | block RAM 8 KiB write rnd, cycles/access | 97.08 | 84.07 | 1.15× |
| `btb` | — same commit | flash 16 KiB read seq, cycles/access | 50.11 | 43.99 | 1.14× |
| `btb` | — same commit | flash 16 KiB read rnd, cycles/access | 355.57 | 352.66 | 1.01× — flash-bound, not fetch-bound |
| `btb` | — same commit | HyperRAM 4 KiB read seq, cycles/access | 155.22 | 148.15 | 1.05× |

**Every row moves, and the ones that move least say why.** Flash 16 KiB random
is 350 cycles of cache-line refill against fourteen instructions of loop, so
there is almost no fetch stall left to remove and it barely moves. Block RAM
sequential is nearly all loop, and it moves most. That ordering is the evidence
the change did what it claims.

### Variants tried, and what each cost

Three of these do not ship. They are here so the next person does not retry them.

| variant | Fmax min / median / max | verdict |
|---|---|---|
| `--with-btb` alone (jumpAt = 1) | 57.55, one run — **FAIL at 60 MHz** | **dropped.** Critical path starts at `BtbPlugin_logic_mem.0.0.DOA8`: 4.10 ns of block RAM clk-to-q, then the 16-bit hash compare, the hit decision and the fetch redirect, all in one cycle |
| `--with-btb --relaxed-btb` | 58.54 / 60.59 / 62.90 | **dropped.** The BTB's own path is fixed, but 1 run in 3 still misses 60 MHz. A configuration that builds two times in three is not a configuration that builds |
| `--with-btb --relaxed-btb --btb-sets 128` | 58.77 / 59.01 / 60.82 | **dropped, and it is the informative one.** A quarter of the entries did *not* buy timing — it was slightly worse, and **still 44 BRAM**, because 128 × ~50 bits does not fall below one DP16KD pair. The BTB's size was never the problem |
| `--with-btb --relaxed-btb --relaxed-branch` | 64.23 / 71.81 / 72.88 | **kept.** All three runs pass |

**The lever was `--relaxed-branch`, not anything about the BTB.** Once
`--relaxed-btb` moved the BTB's own redirect to `jumpAt = 2`, the critical path
left the BTB entirely and became `BranchPlugin BRANCH_CTRL → PcPlugin
output_fire → FetchL1 banks` — the *mispredict* redirect from the execute stage.
A BTB adds a jump interface to `PcPlugin`, so the execute-stage redirect now
loses an age arbitration it did not have to win before, and that path is
routing-bound: 13 ns of routing against 3 ns of logic on a die 52% full.
`--relaxed-branch` sets `BranchPlugin(jumpAt = 1)`, giving that redirect its own
stage.

**And it is very nearly free.** One extra cycle on a *mispredicted* branch, on a
core that now predicts most of them: block RAM 2 KiB sequential went 22.62 →
22.77 cycles, 0.7%, inside run-to-run noise, in exchange for 11 MHz.

### Not tried

**`GSharePlugin`.** It needs `withBtb`, adds a 4 KiB history table — three more
DP16KD against 44 of 56 already used — and a `HistoryPlugin` on the same fetch
path that had to be relaxed twice to reach 71 MHz. The direction-prediction it
buys applies only to conditional branches the BTB already has a target for.

**`RasPlugin`.** `--with-ras` is `rasDepth = 4` inside `BtbPlugin`, so it is
cheap in area, and a shell returns constantly. It was not measured; it is the
obvious next one.

## Regenerating and reading the core

    python3 -c "import sys; sys.path.insert(0,'gateware/soc'); \
                import cpu.cpu; print(vexii_cpu.generate(0))"

The emitted `VexiiRiscv.v` is also left in `tmp/vexii_hello/build/` after any SoC
build, which is where the net names in a nextpnr critical path resolve to.

## Scripts

| | |
|---|---|
| `scripts/soc_run.py` | firmware, gateware, configure, console — the whole loop |
| `scripts/soc_shell.py bench` | cycles/access, MB/s, instructions/access and IPC per region |
| `scripts/soc_timing_sweep.py` | repeated place-and-route, min/median/max; `--allow-fail` records a configuration that misses the constraint instead of refusing to report it |

