# RTIC — the concurrency design

How this firmware schedules work, and what constrains it. **This is the design.**
Numbers appear only where they bound something.

**Index:** [`README.md`](README.md) · CPU
[`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md) · interrupts
[`soc-interrupts.md`](soc-interrupts.md)

## The model

**RTIC is the dispatcher and there is no alternative in the tree.**
It emits `fn main`; there is no `#[entry]` elsewhere, no `rtic` cargo feature,
and no superloop.

- **Tasks run to completion on one stack.** RTIC gives no task its own stack,
  which is why the dispatcher costs tens of bytes of `.bss` rather than
  kilobytes. The linker script reserves an 8 KiB floor.
- **Priority is declared per task and decides preemption**, in software, through
  RTIC's `riscv-slic` backend. Nothing in the interrupt controller participates —
  [`soc-interrupts.md`](soc-interrupts.md).
- **Shared resources are checked at compile time.** That is what the dispatcher
  is bought for, over a hand-written cooperative loop.
- **Tasks are released by the 1 ms tick's counter, not by a deadline queue.**
  Nothing in the shipping firmware exercises a monotonic.

## What it is sized against

This core is a **USB controller**, and the workload is device emulation —
bursty, latency-sensitive, arriving at the host's convenience, cache-hostile.
Not the console, which is idle almost always and measures nothing.

The reference is moondancer (`greatscottgadgets/cynthion`,
`firmware/moondancer`), and the shape that matters:

| step | cost |
|---|---|
| handler drains the 8-byte setup FIFO, one MMIO read per byte | 8 bus transactions |
| **a full queue is `loop { nop }` in interrupt context** | the queue is an assertion, not a buffer |
| response written one 8-bit MMIO store per byte | 512 bus transactions |

Upstream's own figure is 5.03 MB/s, so at 60 MHz a 512-byte packet is
**~5,000–6,000 cycles, 85–100 µs** — about 80% of a 125 µs high-speed
microframe. Under bulk load the part runs at **70–80% CPU**.

**So the design is bounded by the tail, not the mean.** RTIC bounds the worst
case and costs the mean; that is the trade taken deliberately.

| | bound |
|---|---|
| worst arrival→handled | **274 µs** |
| events past the 375 µs deadline | **0** |
| worst window with `mstatus.MIE` clear | 60 instructions, ~1 µs |

## Why there is no monotonic yet

**The CLINT has one comparator.** The 1 ms tick owns `mtimecmp`, and
`Mono::start()` claims it — so adopting a monotonic is not an addition, it moves
**every** periodic job at once, including the tick that stamps every log line.

A CLINT monotonic exists and is measured — 7 µs worst late — behind
`--features rticmono`, as a separate binary. It is written here because
`rtic-monotonics` 2.2.1 ships two RISC-V backends, ESP32-C3 and C6 SYSTIMER, and
neither is a CLINT one. Independent work, not derived from either — #508.

Adopting it is the open question, and the single comparator is the whole of the
cost.

## The constraint that shapes it: the I-cache

A property of the machine, not of the runtime.

The I-cache is **8 KiB, 64 sets × 2 ways**. The RTIC build's hot footprint is
**5,440 B** against the bare build's 4,032 B — the dispatcher's `.text` costs
about 1.4 KB of footprint, and footprint is what transfers between builds.

It fits at 8 KiB. It did not at 4 KiB, which is why both L1s were grown (#110,
#283, #292), spending block RAM that is now at 79%. That is a real trade and the
reason the cache size is part of this design rather than an unrelated setting.

**If the cache ever shrinks**, the 424-byte hand-written dispatcher closes the
same latency defect with a footprint that fits. It stays in the tree as that
fallback, not as a rival.

## Traps this is shaped around

- **A `#[cfg]` on a feature that no longer exists is not a compile error.**
  Deleting the `rtic` feature made the tick's cfg permanently false, so it
  stopped pending its task and the firmware ran with no scheduler — compiling
  cleanly, booting, answering every command. One test assertion caught it.
  **Grep for a feature name after deleting one.**
- **The `model` line in the `rtic` command stays.** A transcript that does not
  say what produced it cannot be compared with one taken after the next change.

## Open

| | |
|---|---|
| adopt the monotonic | one comparator, so it moves every periodic job at once |
| the I2C transaction-complete interrupt has never fired | #246. Until it does, the power task spins on I2C rather than being woken by it |
| IPC and `ICACHE_MISS` on silicon | the 16550 implements the MSR half of local loopback and not the data half, so nothing on the FPGA can inject an arrival |
| what shrinking the hot set would take | unmeasured |

## Reproducing the numbers

```bash
./scripts/soc_rtic_workload.py --events 2000
./scripts/soc_rtic_monotonic.py
./scripts/soc_rtic_workload.py --events 200 --trace
./scripts/soc_icache_model.py tmp/logs/trace-rtic.log --elf <the rtic elf>
```

The figures above come from the `workload` feature — a synthetic stand-in built
to be measured, fair between models because all of them ran it, and not a
statement about what the shipping firmware does.
