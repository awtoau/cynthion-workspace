# VexiiRiscv — the SoC's CPU

The RISC-V core in `ecp5-test/riscv/vexii_hello_soc.py`. Not a chip on the board:
it is a soft core inside the [ECP5](lfe5u-12f-ecp5.md), and it has a note here for
the same reason the flash and the HyperRAM do — what it actually does on this
board differs from what its parameters suggest, and the measurements have to live
somewhere they can be found.

**Index:** [`../hardware.md`](../hardware.md)

## Not committed — generated at elaboration

`ecp5-test/riscv/vexii_cpu.py` runs the Scala generator on every build and hands
Amaranth the resulting `VexiiRiscv.v`. A checked-in Verilog would drift silently
from the flags that produced it; a nine-second `sbt` run would not.

So **every parameter below is a flag change plus a rebuild, not a source change**,
and there is no generated file to keep in step.

Source: `repos/vexiiriscv`, options in `src/main/scala/vexiiriscv/Param.scala`.
The choice of VexiiRiscv over VexRiscv, and cached over cacheless, is in
[`../decisions.md`](../decisions.md).

## Configuration

`GENERATE_FLAGS` in `ecp5-test/riscv/vexii_cpu.py`. RV32IMAC with `rdtime`,
single issue, Wishbone throughout, plus the debug module on the existing JTAG
chain.

| | |
|---|---|
| ISA | RV32IMAC, `--with-rdtime` |
| issue width | 1 lane, 1 decoder |
| I-cache | **4 KiB** — 64 sets × 1 way × 64 B line |
| D-cache | **4 KiB** — 64 sets × 1 way × 64 B line |
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

    python3 -c "import sys; sys.path.insert(0,'ecp5-test/riscv'); \
                import vexii_cpu; print(vexii_cpu.generate(0))"

The emitted `VexiiRiscv.v` is also left in `tmp/vexii_hello/build/` after any SoC
build, which is where the net names in a nextpnr critical path resolve to.

## Scripts

| | |
|---|---|
| `scripts/soc_run.py` | firmware, gateware, configure, console — the whole loop |
| `scripts/soc_shell.py bench` | cycles/access, MB/s, instructions/access and IPC per region |
| `scripts/soc_timing_sweep.py` | repeated place-and-route, min/median/max; `--allow-fail` records a configuration that misses the constraint instead of refusing to report it |
| `scripts/cpu_matrix.py`, `ecp5-test/riscv/cpu_area.py` | core configurations against area |
