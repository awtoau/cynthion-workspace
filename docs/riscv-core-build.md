# Building the RISC-V core — and why you should, more often than you think

**The core is generated, not vendored, and regenerating it takes seconds.** That
sentence is the whole point of this file, because it was assumed false for a long
time and the assumption cost real work.

**Index:** [`hardware.md`](hardware.md) · CPU notes:
[`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md)

## The command

`gateware/soc/cpu/cpu.py` runs it. Nothing else needs to be invoked by hand:

```python
import cpu.cpu
vexii_cpu.generate(reset_addr=0x0, cache_sets=64)   # -> repos/vexiiriscv/VexiiRiscv.v
```

Under that it is one `sbt` run against `repos/vexiiriscv`:

    sbt --batch --no-server "runMain vexiiriscv.Generate <flags>"

**Requirements: `sbt` and a JDK. Java 25 works.** There is no toolchain freeze —
that was believed and it is not true. Verified 2026-08-04 on
`openjdk 25.0.4` with `sbt` from the distro. If a regeneration fails, read the
error; do not conclude the toolchain is unusable.

The generated file lands at `repos/vexiiriscv/VexiiRiscv.v` and is copied to
`gateware/probes/cpu_matrix/soc-cpu/VexiiRiscv.v`, which is what the SoC elaborates
against and what `scripts/soc_generate_pac.py` uses for its metadata-only walk.

## The flags, and where they live

`GENERATE_FLAGS` in `gateware/soc/cpu/cpu.py` is the single list. Each entry
carries its reasoning in a comment there; that file is the authority and this is
a map, not a copy.

| what | flag |
|---|---|
| 32-bit, M/C/A, `rdtime` | `--xlen 32 --with-rvm --with-rvc --with-rva --with-rdtime` |
| I-cache, 64 sets × 2 ways | `--with-fetch-l1 --fetch-l1-sets 64 --fetch-l1-ways 2` |
| D-cache, same shape | `--with-lsu-l1 --lsu-l1-sets 64 --lsu-l1-ways 2` |
| Wishbone on all three buses | `--fetch-wishbone --lsu-wishbone --lsu-l1-wishbone` |
| branch target buffer | `--with-btb --relaxed-branch --relaxed-btb` |
| JTAG debug module on ER2 | `--debug-jtag-instruction` |
| **performance counters** | `--performance-counters 4` |
| PMA regions | `--region base=...,size=...,main=,exe=` per window |

`--region` is not optional. VexiiRiscv's `defaultPma` covers only `0x80000000`
and `0x10000000`, so a design with memory at `0x00000000` has it in **no region
at all** and every access traps — including every stack operation. The failure
looks exactly like a dead CPU.

## The performance counters, and the lesson attached to them

`--performance-counters 4` gives `mhpmcounter3..6`, each selected by writing an
event id to the matching `mhpmevent` CSR (`0x323..0x326`), read back from
`0xb03..0xb06`. The ids are VexiiRiscv's own, in
`repos/vexiiriscv/src/main/scala/vexiiriscv/misc/Service.scala`:

| id | event |
|---|---|
| `0x04` | `STALLED_CYCLES_FRONTEND` — waiting on instruction fetch |
| `0x05` | `STALLED_CYCLES_BACKEND` — waiting on data |
| `0x10`/`0x11` | `ICACHE_ACCESS` / `ICACHE_MISS` |
| `0x12` | `ICACHE_WAITING` |
| `0x18`/`0x19` | `DCACHE_LOAD_ACCESS` / `DCACHE_LOAD_MISS` |
| `0x1A` | `DCACHE_WAITING` |

`src/bench.rs`'s `hpm` module reads them around a walk.

**Why this file exists.** Chasing a HyperRAM performance question, five gateware
probes were built and five readings of the source produced five wrong
explanations — sixteen transactions per line, 316 CK inside the burst, a
non-streaming data phase, slow plumbing, and a benchmark bounded by instruction
fetch. Every one was refuted by measurement. The counters settled it in one
build, and had been available in the core the entire time:

    front-stall 7   back-stall 294   dcache-waiting 79   dcache-miss 1

Seven cycles of fetch stall and zero I-cache misses killed the "bounded by flash"
theory outright. **Ask the CPU before instrumenting the fabric.**

## What regeneration costs

* **Seconds of wall clock.** `sbt` is warm after the first run.
* **Area and timing.** Adding the four counters stopped the design closing at
  `SYNC_MHZ = 72`; it was already passing on placement luck there (71.45 to 76.99
  MHz across identical rebuilds). `SYNC_MHZ` is 60 with the counters on, which has
  20% margin. **Check the frequency line on every build** — `soc_run.py` prints
  it now precisely because a passing build used to say nothing about its margin.
* **Nothing else.** The PAC, the linker scripts and the firmware are unaffected
  unless a `--region` or an address moves, and `./dev.py run` regenerates the
  peripheral map itself.

## When to regenerate rather than reason

Whenever the question is about the CPU's own behaviour: stalls, cache hit rates,
branch prediction, whether an instruction is implemented. A flag and a rebuild is
usually cheaper than an afternoon of inference, and it produces a number instead
of an argument.
