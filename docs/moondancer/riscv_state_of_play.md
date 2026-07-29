# RISC-V on Cynthion: where the work actually is

Written to gather scattered prior work into one place, after a session where
the flash benchmarking hit a wall that only a soft CPU can get past.

## The short version

- **32-bit is enough, and 64-bit is formally parked.** The cores were generated
  and the sizing question answered: RV64 does not fit the `LFE5U-12F` alongside
  the USB fabric, and nothing here needs it. Recorded as a decision in
  `riscv_alternatives.md` rather than left as an open candidate.
- **VexiiRiscv was already built**, benchmarked, and compared against a
  Moondancer-like configuration. The tree still exists.
- **The comparison was found to be not-yet-fair**, and the prior work says so
  plainly. That is the open question, not the build.

## What exists, and where

The VexiiRiscv work was moved to the wastebasket during a cleanup and is intact:

    /mnt/2tb/wastebasket/cynthion-workspace-20260728-093000/
        riscv-64-work-vexiiriscv/     868 MB, a real git checkout
        riscv-64-work-nextpnr/        1.5 GB
        riscv-64-work-prjtrellis/      16 MB
        riscv-64-out/                 1.5 GB
    /mnt/2tb/wastebasket/riscv-sim-workspaces-20260726-000000/
        workspaces/                    36 GB, 76 simulation workspaces

The checkout is at **VexiiRiscv v0.0.0-1297-gf8774d4** and contains a generated
`VexiiRiscv.v` (1.7 MB) plus `build.sbt` and `build.mill`. **Nothing needs
rebuilding** — recovering this is cheaper than regenerating it, and it is the
exact tree the recorded benchmark numbers came from.

Note the directories are named `riscv-64-*` for historical reasons; the builds
that were actually benchmarked are RV32.

## Existing documents

| Document | What it settles |
|---|---|
| `docs/moondancer/riscv_alternatives.md` | Canonical. Carries the decision to park RV64; retains VexiiRiscv as the RV32 successor. Its later sections are the record of how that was concluded, not an active plan. |
| `docs/luna_ecp5_fpga/vexriscv_update_blocked.md` | Why the current VexRiscv is frozen: Scala 2.11.12 against Java 25. |
| `docs/luna_ecp5_fpga/riscv32_equivalence_and_variation_report_2026-07-22.md` | The benchmark comparison, and why it is not yet a fair one. |

## The benchmark result

Two RV32 builds, both VexiiRiscv, same benchmarks and compiler flags
(`-march=rv32imac -mabi=ilp32`, GCC 11.1.0, seed 2):

| Metric | Stripped RV32 | Moondancer-like RV32 | Delta |
|---|---:|---:|---:|
| Dhrystone µs/run | 64 | 74 | +15.6% |
| Dhrystones/sec | 15623 | 13415 | −14.1% |
| DMIPS/MHz | 0.74 | 0.63 | −14.9% |
| CoreMark total ticks | 6,133,969 | 6,361,949 | +3.7% |
| CoreMark/MHz | 1.63 | 1.57 | −3.7% |

The stripped build is more efficient per clock, which is unsurprising — it has
fewer features.

## Why this is not yet the answer

The report's own conclusion is that **the comparison is valid for new-vs-new CPU
efficiency but not for equivalence with the legacy VexRiscv system.** Two
reasons, both stated there:

1. **Several architectural changes were varied at once** — RVA, I-cache, D-cache
   and supervisor mode all differ between the two configurations, so a 14%
   Dhrystone delta cannot be attributed to any one of them.
2. **The system composition differs.** The legacy comparison involves the USB
   fabric; the new builds do not include it, so area, timing and any
   USB-active workload are not comparable.

That is a well-formed negative result, and it is worth more than a number would
have been: it says exactly what to do next rather than leaving a misleading
figure in circulation.

## What the prior work recommends next

A single-factor matrix, one toggle at a time:

- `base` — xlen32 + rvm + rvc + rdtime
- `base + supervisor`
- `base + rva`
- `base + i4k + d4k`
- `base + rva + i4k + d4k` — the closest to Moondancer-like

Plus a full-system parity build including the USB fabric, and reporting
throughput at achieved Fmax alongside CoreMark/MHz.

## Area, and what can be removed

The RV32 report's table could not be used to choose a core, because VexRiscv's
area figure included the whole USB fabric while the VexiiRiscv rows did not.
Measured properly, core alone, on r1.4:

| core | LUT4 | FF | Fmax |
|---|---|---|---|
| VexRiscv `cynthion` | **4739** | 1683 | 64.9 MHz |
| VexRiscv `cynthion+jtag` | 5410 | 1832 | 58.4 MHz |
| *JTAG debug module costs* | *671* | *149* | *−6.5 MHz* |
| VexiiRiscv stripped | 6592 | 2695 | 73.4 MHz |
| VexiiRiscv moondancer-like | 6876 | 3756 | **146.4 MHz** |

That inverts the naive reading: VexRiscv is about 1900 LUTs **smaller** than
VexiiRiscv, not 5800 larger. VexiiRiscv's real advantage is **Fmax** — 146 MHz
against 58–65, so roughly 2.3× the clock for about 30% more area.

The VexiiRiscv rows still include SoC glue the VexRiscv measurement does not, so
they are closer to like-for-like than before but not equal.

### VexiiRiscv is configurable, and most of it is opt-in

The 5.24 CoreMark/MHz headline is the maximal build. A minimal one is much
smaller, because the expensive features are **opt-in rather than opt-out**:

| flag | feature | note |
|---|---|---|
| `--fetch-l1`, `--lsu-l1` | instruction and data caches | opt-in; largest single saving in both LUTs and BRAM |
| `--without-mmu` | SV32/SV39 virtual memory | only needed for Linux |
| `--dual-issue` | second execution lane | opt-in |
| `--with-btb`, `--with-gshare`, `--with-ras` | branch prediction | all opt-in; BTB consumes block RAM |
| `--without-late-alu` | second ALU stage | costs IPC, saves area |
| `--without-div` | hardware divider | software divide instead |
| `--without-lsu-bypass` | load/store forwarding | costs IPC |
| `--without-mul` | hardware multiplier | **keep it** — the ECP5 has DSP blocks, so this is cheap |

The stripped configuration measured at 6592 LUTs already omits most of these,
which is why it reaches only 1.63 CoreMark/MHz rather than 5.24. The interesting
question for this board is therefore not "which core" but **which point on the
VexiiRiscv curve fits alongside a USB stack**, and that is answerable by
building rather than arguing.

## The earlier sweep, and why its numbers were discarded

The RV32 report quotes two configurations. The work behind it covered far more:
57 configurations with place-and-route timing, 258 exhaustive core builds, and
692 log files. All of it has been deleted, because reading the scripts that
produced it showed the numbers do not mean what they appear to mean.

**Fmax was routed at a 25 MHz target.** `build_nextpnr_cmd` in
`riscv-64/scripts/profile_shared.py` passes `--freq 25.0` with
`--timing-allow-fail`, so the router stops caring once it clears 25 MHz and
reports whatever it happened to achieve. That is a lower bound produced by a
relaxed constraint, not a ceiling. Rerouting the same I$+D$ configuration at a
200 MHz target gives **82.6 MHz** against the 146.4 MHz in the archived data.

**The bare-core builds measured a pruned design.** The wrapper in
`42_run_vexii_nextpnr_timing.py` ties every core output to an unconnected wire
and drives the instruction bus with a constant `32'h00000013` — a `nop`.
Synthesis then removes the output side as dead logic; the generator log reports
"567 signals were pruned". Whatever those rows measured, it was not a CPU that
could execute anything.

**The sweep varied XLEN and the ISA base, and nothing recorded which.** Core
builds carry no `output_prefix`, so their configuration is unrecoverable from
the filename — only the SoC builds spell their features out. A report built by
reading filenames labels every core result as having no features at all.

### The 73 vs 146 question is still open

An earlier draft of this document claimed the sweep resolved it: that
`microsoc_exh_01_i4k_d4k` measured "exactly 146.4 MHz", matching the report's
faster row, and that caches therefore doubled the clock.

That reasoning was wrong. Both figures came from the same 25 MHz-target
pipeline, and the 73.4 MHz row was one of the pruned bare-core builds — so the
agreement was two outputs of one broken instrument, not corroboration. The two
configurations also differ in XLEN, which the table had no column for: the
archived data splits cleanly with every 32-bit result above 91 MHz and every
64-bit result below. Most of the "doubling" was ISA width.

The mechanism proposed for it — that an L1 terminates the common case inside
the CPU and shortens the critical path — remains plausible and is worth
testing. It has not been tested. On the one configuration measured properly so
far, the critical path runs through LSU address generation into the data bus.

### What replaces it

`scripts/riscv_matrix_config.py` generates configurations for the target that
exists: 32-bit, bare metal, no supervisor mode. It sweeps cache size at 4, 8
and 16 KiB, which the old matrix fixed at 4 KiB and never varied — block RAM is
the scarce resource on a 12F (56 blocks, shared with firmware and USB buffers),
so the wall is worth finding by measurement.

`scripts/riscv_core_wrapper.py` attaches block RAM to both buses with a real
one-cycle handshake, so bare cores can be placed without being optimised away.

Neither produces CoreMark. That needs firmware on the core, which needs CPU
bring-up. No CoreMark output exists in the archived data either — it was never
run, despite the report implying otherwise.

Branch prediction adds further: `btb + gshare` reaches 183 MHz, and with `ras`
and dual-issue 192 MHz — though `ras` alone is consistently *worse* than
`gshare` alone, which is worth knowing before enabling it.

### What is still missing

The sweep recorded timing but not area in the same summaries, and **no CoreMark
at all** — so performance-per-LUT still cannot be computed from it. The
`core_exh` series has nextpnr logs that do contain area figures and would fill
that in.

## Why this matters for the flash work

The flash benchmarking stalled on a measurement problem: a JTAG register read
takes ~35 ms while a 4-byte flash read takes ~1 µs, so the host cannot time
short transfers at all. A soft CPU next to the flash controller fixes that —
it issues reads at fabric speed and times them with a local cycle counter.

That makes the CPU work a dependency of the flash work rather than a parallel
track, and it gives the CPU a concrete first job: measuring small random reads,
which is also exactly the XIP pattern a CPU executing from flash would generate.

## Open questions

- Does the VexRiscv toolchain blocker still apply? It was Scala 2.11.12 against
  Java 25, and **Java 17 is now installed** (`java-17-temurin-jdk`) — which was
  the incompatibility. Worth re-testing before assuming the freeze holds.
- Is the recovered tree buildable as-is, or does it need its own toolchain setup?
- Should the wastebasket copy be restored into the workspace, or rebuilt from
  upstream at a pinned version?
