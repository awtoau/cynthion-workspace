# RTIC on this SoC: the measurement behind the decision, and what it costs

**RTIC is the concurrency model** — [`architecture.md`](architecture.md), issue #115.
This is the evidence, not a re-argument.

> **The numbers below measure a synthetic workload, not this system.** They come
> from the `workload` feature — a stand-in built to be measured, because no real
> load existed to measure. They are fair between the models, since all three ran
> the same stand-in, but they are not a statement about what the shipping
> firmware does.
>
> Adoption is not gated on them. The real system has instruments already:
> `--performance-counters 4` exposes `STALLED_CYCLES_FRONTEND`/`BACKEND`, and the
> PLIC counts `irqs`, `stalls`, `buffered` and `lost` per source. Those, under
> RTIC against the poller, are the comparison worth having.
>
> **The first conversion is done (#245, below) and the comparison is NOT taken
> here.** It waits for the board.

**Index:** [`hardware.md`](hardware.md) · CPU:
[`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md)

## The PAC1954 on RTIC, and where its numbers will come from

Issue #245. `--features rtic` now converts the SHELL rather than building a spike
beside it: `src/rtic_app.rs` is the entry point, `src/main.rs`'s `#[entry]` is
gated off, and the PAC1954's 50 ms REFRESH cycle is a
`#[task(binds = PowerRefresh, priority = 1)]` released by the 1 ms tick instead
of a poll at the top of the main loop.

**The default build is unchanged and is still the superloop.** That is checked
rather than asserted, by `scripts/soc_feature_isolation_check.py`, and
`scripts/soc_test.py` asserts that the shipping image says `superloop` when asked.

Both models call the same `power::Monitor::service`, so the only variable between
them is how it was reached. The shell's `rtic` command reports which model it was
built as, the task's run count, its release lateness against its period, the
PLIC's per-source counters, and `STALLED_CYCLES_FRONTEND`/`_BACKEND`:

    > rtic
    model    superloop  (1 task)
    task     power_refresh prio -1 period 50 ms  runs 99  pends 99 (= runs)
             late worst 2140 ticks  mean 370 ticks  gap worst 50 ms over 99 polls
    plic  @f0400000 pending 00000000 enabled 00000036
      0 src 1 irqs 15   stalls 0 buffered 0 lost 0
      ...
    stalls   frontend N backend N  of M cycles

**The comparison itself is deferred to hardware and is not in this document.**
QEMU cannot take it: `-M virt` has no PAC1954, so the two milliseconds of I²C the
board spends inside the task are absent, and it reads `mhpmcounter3`/`4` as
hardwired zero, so both stall counters come back `--`. A figure taken there would
measure the emulator's idle loop and be quoted afterwards as if it measured this
SoC — which is the exact failure the caveat at the top of this file exists to
prevent. Run `rtic` on the board under each build; that is the measurement.

## What it fixes, and what it costs

| | superloop (today) | preempt | RTIC |
|---|---|---|---|
| worst arrival→handled | 1,220 µs | 271 µs | **274 µs** |
| events past the 375 µs deadline | 600 / 2,000 | 0 | **0** |
| dispatcher `.text` | — | +424 B | +1,812 B |
| dispatch cost per event | — | 21 instr | ~180 instr |
| worst window with `mstatus.MIE` clear | 0 | 0 | 60 instr, ~1 µs |

**RTIC closes the unbounded turn** — 274 µs against the hand-written
dispatcher's 271, zero deadline misses either way. The dispatcher is kept in the
tree as the fallback if the I-cache stays 4 KiB, not as a rival.

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
| is checked access worth 1,812 bytes? | **yes**, it is adopted |

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
| IPC and `ICACHE_MISS` on silicon | needs a bitstream first: `peripherals/uart16550.py` implements the MSR half of local loopback and not the data half, so nothing on the FPGA can inject an arrival |
| what shrinking the hot set would take | unmeasured |

## Reproducing

```bash
./scripts/soc_rtic_workload.py --events 2000
./scripts/soc_rtic_monotonic.py
./scripts/soc_rtic_workload.py --events 200 --trace
./scripts/soc_icache_model.py tmp/logs/trace-rtic.log --elf <the rtic elf>
./scripts/soc_feature_isolation_check.py    # the shipping image is unchanged
```
