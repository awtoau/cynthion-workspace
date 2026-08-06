# RTIC on this SoC: what the workload needs, and what each model costs

Issue #115, and the decision is [`decisions.md`](decisions.md) 19.

**Index:** [`hardware.md`](hardware.md) · CPU:
[`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md)

## The answer

| | superloop (today) | preempt | RTIC |
|---|---|---|---|
| worst arrival→handled | 1,220 µs | 271 µs | **274 µs** |
| events past the 375 µs deadline | 600 / 2,000 | 0 | **0** |
| dispatcher `.text` | — | +424 B | +1,812 B |
| dispatch cost per event | — | 21 instr | ~180 instr |
| worst window with `mstatus.MIE` clear | 0 | 0 | 60 instr, ~1 µs |

**The unbounded turn is real, and it is fixable for 424 bytes without RTIC.**
RTIC closes it just as completely — 274 µs against 271 — so everything that
separates them is cost, not capability.

What RTIC additionally sells is **checked resource access**, a compile-time
property with no runtime number. That is the whole of the remaining judgement.

## The workload that decides it

This core is a **USB controller**. The workload is device emulation: bursty,
latency-sensitive, arriving at the host's convenience, cache-hostile by nature.
Not the console — that is idle almost all the time and measuring against it
produces numbers that describe nothing.

moondancer is the reference, read from the mirror (`greatscottgadgets/cynthion`,
`firmware/moondancer`; `src/workload.rs` carries the same citations beside the
code that imitates them):

| step | where | cost |
|---|---|---|
| handler drains the 8-byte setup FIFO, one MMIO read per byte | `lunasoc-hal/src/usb.rs:380-392` | 8 bus transactions |
| enqueues into a 64-slot queue | `bin/moondancer.rs:28` | |
| **a full queue is `loop { nop }` in interrupt context** | `bin/moondancer.rs:30-46` | the queue is an assertion, not a buffer |
| zeroes a 1 KiB buffer per command; verbs zero their own | `bin/moondancer.rs:460`, `gcp/moondancer.rs:560` | 1–3 KB of memset, independent of payload |
| response written one 8-bit MMIO store per byte | `lunasoc-hal/src/usb.rs:487` | 512 bus transactions |

Upstream's own figure, in a comment beside the code that produced it:
`examples/bulk_speed_test.rs:390` reads **5.03 MB/s**. At 60 MHz that is 11.9
cycles per MMIO byte store, so a 512-byte packet costs **~5,000–6,000 cycles,
85–100 µs** — about 80% of a 125 µs high-speed microframe. The part tops out near
one packet per microframe, and under bulk load moondancer genuinely runs at
**70–80% CPU**.

That is why the tail matters: at 26% busy in the harness the superloop already
misses 600 deadlines in 2,000, and the real duty cycle is three times higher.

## What each model costs

| model | `.text` | runtime | `.bss` | preempts | checks sharing | needs |
|---|---|---|---|---|---|---|
| superloop (today) | — | 0 | — | no | no | nothing |
| cooperative, hand-written | 984 | 224 | 8 | no | no | a dispatcher |
| **RTIC 2.3** `riscv-clint-backend` | 2,312 | 1,552 | 24 | **yes, by priority** | **yes, at compile time** | an `rtic_time::Monotonic` |
| bare riscv-rt (the Rust floor) | 760 | — | 4 | | | |
| bare C (the C floor) | 186 | — | 4 | | | |

**Neither gives a task a stack**, which is why `.bss` is tens of bytes rather
than kilobytes: RTIC tasks and cooperative jobs both run to completion on the one
stack. A model that did give each task a stack would be decided by a different
number — how deep the shell's call chain is, which nothing here computes.
`memory.x` reserves an 8 KiB floor and says so.

**Every model leaves `src/irq.rs`'s PLIC claim loop in place.** Neither runtime
has a PLIC backend, and that is not a gap in either of them.

## RTIC, measured rather than argued

`firmware/cynthion-soc/src/bin/workload_rtic.rs`, behind `--features rticcs`,
running the same workload as `workload_bare.rs` so the two are comparable.

| question | answer |
|---|---|
| does it fix the unbounded turn? | **yes** — 274 µs, zero deadline misses |
| dispatch cost | **~180 instructions/event**, 4.3% of an event |
| `critical_section` per pend | **74 instructions**, worst window 60 |
| does the PLIC survive adoption? | **yes** — 1,108 claims, 1,208 completes, nothing gated off |
| is there a CLINT monotonic? | **yes** — written and measured, 7 µs worst late |
| priorities and shared resources configurable? | **yes**, and one obvious configuration is a priority-2 blocker |
| is checked access worth 1,812 bytes? | **a judgement**, and it stays one |

**`rtic-monotonics` 2.2.1 has two RISC-V backends.** What it lacks is a CLINT
one, which is why writing ours was small. An earlier claim that RISC-V had
nothing was wrong.

## The I-cache constraint, and what to do about it

Stated once, because it is a property of the machine rather than of any runtime.

The I-cache is **4 KiB, direct-mapped, one way**. Measured over both traces with
`scripts/soc_icache_model.py`, 200 events:

| | `workload_bare` | `workload_rtic` |
|---|---|---|
| footprint | **4,032 B — fits, by one line** | **5,440 B — does not** |
| misses | 573 (0.03%) | 1,393 (0.05%) |

+1,812 bytes of `.text` cost +1,408 bytes of footprint. Footprint transfers
between builds; the specific set conflicts do not, because the model uses the
QEMU build's addresses and has no prefetch.

**The solutions, which is the only part still open:**

1. **Grow the cache.** The die is a 25F and the SoC is sized for a 12F — #110.
   This is the direct fix and the one that makes the question go away. It costs
   block RAM, which is at 79% after the BTB
   ([`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md)), so it is a real
   trade rather than a free one.
2. **Take the 424-byte dispatcher.** It closes the same defect and leaves the hot
   set fitting.
3. **Shrink the hot set** so a resident runtime fits beside it. Nothing has
   measured what that would take.

Nothing here is a reason to reject RTIC on a machine with a bigger cache.

## Not settled

| | |
|---|---|
| IPC and `ICACHE_MISS` on silicon | needs a bitstream first: `uart16550.py` implements the MSR half of local loopback and not the data half, so nothing on the FPGA can inject an arrival |
| whether checked resource access earns 1,812 bytes | a judgement, not a measurement |
| what shrinking the hot set would take | unmeasured |

## Reproducing

```bash
./scripts/soc_rtic_workload.py --events 2000
./scripts/soc_rtic_monotonic.py
./scripts/soc_rtic_workload.py --events 200 --trace
./scripts/soc_icache_model.py tmp/logs/trace-rtic.log --elf <the rtic elf>
./scripts/soc_feature_isolation_check.py    # the shipping image is unchanged
```
