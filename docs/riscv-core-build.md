# Building the RISC-V core

The core is generated, not vendored; regeneration takes seconds. Index: [`hardware.md`](hardware.md). CPU notes: [`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md).

## The command

`gateware/soc/cpu/cpu.py` runs it. Nothing else needs to be invoked by hand:

```python
import cpu.cpu
vexii_cpu.generate(reset_addr=0x0, cache_sets=64)   # -> repos/vexiiriscv/VexiiRiscv.v
```

Under that it is one `sbt` run against `repos/vexiiriscv`:

    sbt --batch --no-server "runMain vexiiriscv.Generate <flags>"

Requirements: `sbt` and a JDK. Java 25 works (verified 2026-08-04, `openjdk 25.0.4`, distro `sbt`). A failed regeneration is an error to read, not a frozen toolchain.

The generator writes `repos/vexiiriscv/VexiiRiscv.v` — a fixed path, because
SpinalHDL's target directory is the process cwd and sbt's cwd must be the project
root. `generate(output=...)` copies it to the caller's path; `top.py` passes
`tmp/awto_soc/build/<variant>/VexiiRiscv.v`, and that copy is what synthesis
reads.

`gateware/probes/cpu_matrix/soc-cpu/VexiiRiscv.v` is a checked-in copy that
`scripts/soc/soc_generate_pac.py` uses for its metadata-only walk, so the memory map
can be regenerated without sbt.

### Concurrency ([#306](https://github.com/awtoau/cynthion-workspace/issues/306))

* One generator at a time. `cpu.py`'s lock covers the sbt run **and** the copy,
  so a caller passing `output=` cannot read a half-written file.
* The lock lives beside the **checkout**, not beside the worktree:
  `<checkout>/../../tmp/vexii-generate.lock`. `git worktree add` does not
  populate submodules, so every worktree shares the main checkout's
  `repos/vexiiriscv` — a per-worktree lock would have excluded nothing.
* `VEXII_ROOT` moves the sources and the lock together.
* Not caching the netlist is deliberate here: every build regenerates, and a
  build that skipped it once picked up another configuration's core and left the
  board mute with every check passing ([#306](https://github.com/awtoau/cynthion-workspace/issues/306)).

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

**The plugin is not optional while `rdtime` is kept.** `--with-rdtime` adds
zicntr, and `withPerformanceCounters` is `zihpm || zicntr`, so dropping
`--performance-counters` generates a **byte-identical** core to
`--performance-counters 0` — the plugin, its 8-bit buffers and its CSR-RAM flush
FSM are all still there. Only the count is free. [#471](https://github.com/awtoau/cynthion-workspace/issues/471).

**Ask the CPU before instrumenting the fabric.** The counters answer stall and miss questions in one build. History: [`history/soc-usb.md`](history/soc-usb.md).

## What regeneration costs

* **Seconds of wall clock.** `sbt` is warm after the first run.
* **Area.** The four counters are 670 LUT4-equivalents and 94 FFs, counted off the two netlists.
* **Timing.** The counters are not what holds the clock down; measurement over 40 seeds a side in [#481](https://github.com/awtoau/cynthion-workspace/issues/481), [#467](https://github.com/awtoau/cynthion-workspace/issues/467). `SYNC_MHZ` as a constraint does not change what nextpnr produces ([#478](https://github.com/awtoau/cynthion-workspace/issues/478)).
* **Nothing else.** The PAC, the linker scripts and the firmware are unaffected
  unless a `--region` or an address moves, and `./dev.py run` regenerates the
  peripheral map itself.

## When to regenerate rather than reason

Whenever the question is about the CPU's own behaviour: stalls, cache hit rates,
branch prediction, whether an instruction is implemented. A flag and a rebuild is
usually cheaper than an afternoon of inference, and it produces a number instead
of an argument.
