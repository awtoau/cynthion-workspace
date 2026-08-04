# Concurrency models on this SoC, measured

Issue #115 asked whether RTIC is the right shape for this machine, or whether a
`docs/rtic-adoption.md` answers the RTIC half. This is the comparison: five
models built as skeletons, one table, and the constraint that decides between
them.

The constraint is the **I-cache**. 4 KiB, direct-mapped, one way, against 41,400
bytes of `.text`. Flash has 3,392 KiB free for a 58 KB image and block RAM has
~46 KiB free of 63 KiB, so neither is the budget. The `opt-level` table in
`firmware/cynthion-soc/Cargo.toml` is why: going from `z` to `3` grew `.text` by
79% and cost **5.4x the IPC**. On this machine code size is a speed question,
and a runtime that is resident on the dispatch path is resident in that cache.

## 1. How the numbers were taken

Every skeleton does the **same visible work**: a PLIC front end, two sources,
one shared counter, an idle loop. Same crate, same `opt-level = "z"`, same LTO,
same linker script, so the difference between any two `.text` figures is the
runtime and nothing else.

* `scripts/soc_model_probe.py` builds the Rust skeletons in
  `firmware/cynthion-soc/src/bin/model_*.rs` and `src/bin/rtic.rs`.

Each language needs its own floor, because riscv-rt's trap dispatch and gcc's
are not the same code. `runtime` below is always a difference from the floor in
the same language with the same compiler.

Neither probe is in `./dev.py gate`: between them they fetch a Rust dependency
graph and a kernel tarball, and a gate that needs the network fails on a flight.

## 2. What each model costs

| model | `.text` | runtime | of I-cache | `.bss` |
|---|---|---|---|---|
| bare riscv-rt (the Rust floor) | 760 | — | — | 4 |
| **cooperative**, hand-written | 984 | **224** | 5% | 8 |
| **Embassy 0.10**, `platform-riscv32` | 1,808 | **1,048** | 26% | 120 |
| **RTIC 2.3**, `riscv-clint-backend` | 2,312 | **1,552** | 38% | 24 |
| bare C (the C floor) | 186 | — | — | 4 |

For scale, the shell itself is 41,400 bytes of `.text`, 17,056 of `.rodata` and
9,656 of `.bss`.

`configUSE_TIMERS` off, `croutine`/`event_groups`/`stream_buffer` not linked,
every option in it is off unless this firmware would use it.

### None of them gives a task a stack

That is why the `.bss` column is tens of bytes rather than kilobytes. RTIC's
tasks and the cooperative jobs run to completion on the one stack, and an
Embassy task is a compiler-sized state machine in a `static`.

A model that gave each task its own stack would be decided by a different
number entirely -- not the runtime's size but how deep the shell's own call
chain is. `memory.x` reserves an 8 KiB floor and calls it "a floor, not a
measurement of what this firmware needs", because riscv-rt gives no stack-usage
figure and nothing here computes one. The failure mode when such a floor is too
small is the one `memory.x` already records: `.bss` overwritten by stack frames,
the receive ring's indices coming back as stack addresses, and a panic about an
index of 64016 in a 256-byte array.

## 3. What each model needs, and what it fixes

### The current design: superloop, PLIC interrupts, one `mtime` deadline

`src/main.rs`'s loop runs the power poll, the event drain, the console error
report, the Type-C service and the two shells, in that order, forever.
`src/irq.rs` claims PLIC sources and either drains a UART into a ring or masks a
Type-C source and sets a bit. `src/timer.rs` advances one `mtimecmp` by 1 ms.

**Measured this session under QEMU: an untouched shell is 0.10% and 0.23% busy**
across two runs (`scripts/soc_test.py`, "an untouched shell is mostly idle").
The rest is `irq::pop` returning `None`. There is no throughput problem to
solve and nothing to arbitrate between.

What actually goes wrong is latency, and it has one cause: **a turn of the main
loop is unbounded, and every deferred thing waits for it.**

* **Measured: a 50 ms poll with a 61 ms worst gap.** `stats` reports `worst gap`
  after a command that deliberately spends 20 ms in one turn. The poll did not
  slip because the CPU was busy — it slipped because one turn was long and
  nothing preempts a turn.
* **`load` stops every periodic job for the whole transfer.** `main.rs:1622`
  blocks on one console's ring until the image is complete. Interrupts still
  fill the rings, which is a real improvement over the polled version, but
  `power::poll`, `typec::service` and `typec::poll` do not run at all, and a
  Type-C source deferred just before the transfer stays **masked** for its
  duration.
* **Deferral is hand-rolled per source.** `irq::defer_type_c` masks the PLIC
  source, records a port in `PENDING_TYPE_C`, and depends on `typec::service`
  being called from the loop to re-enable it. The comment explaining why the
  completion must precede the disable is there because getting it the other way
  round gated the source off permanently, and that was found on the board.
* **`RINGS` is correct by argument, not by construction.** Four paragraphs of
  module comment about who may advance which index. The reasoning is right;
  nothing re-checks it when a third source is added.

What is *not* wrong: the round-robin is fair by construction, nothing is
dropped, the handler cannot livelock a level-sensitive source, and the busy
figure says there is no contention. **"Nothing serious" is a defensible reading
of the first three of those.** The unbounded turn is the one that is a real
defect rather than a stylistic one.

### RTIC 2.3

`docs/rtic-adoption.md` is the full evaluation. What matters here:

* **It does not use the PLIC.** Both generic RISC-V backends are `riscv-slic`
  backends — a software controller drained from the machine software interrupt.
  `binds = X` names a SLIC source, not a PLIC one. `src/irq.rs`'s claim loop
  survives adoption and RTIC adds a second controller in series.
* **From the gateware it needs `msip`**, which `vexii_clint.py` already has and
  `vexii_hello_soc.py` already wires to `cpu.irq_software`.
* **It fixes the two things above that are about checking**: shared resources
  get a compile-time ceiling analysis, and a low-priority task may take a
  millisecond on I2C because a console interrupt preempts it, so the
  mask-and-defer dance goes away.
* **It does not fix the unbounded turn by itself.** The shell lives in `#[idle]`
  and a `load` running there is preemptible — that is the fix — but the periodic
  work still needs a monotonic, and `rtic-monotonics` 2.2.1 has SysTick, STM32
  and Silabs and nothing for RISC-V. It would have to be written over
  `mtime`/`mtimecmp`.
* **Every `pend` and every `lock` takes a global critical section.**
  `riscv-slic` calls `critical_section::with` throughout and the only
  implementation here is `critical-section-single-hart`, which clears
  `mstatus.MIE`.
* 1,552 bytes, 38% of the I-cache, on the dispatch path by construction.

### Embassy 0.10

Not covered by `docs/rtic-adoption.md`, and the direct competitor.

* **RISC-V support exists**: `embassy-executor` 0.10 has `platform-riscv32`, and
  the skeleton links for riscv32imac with no shim of any kind — no `_ebss`
  alias, no hand-written `device` module, none of the five obstacles the RTIC
  skeleton needed. It is the least friction of the four.
* **It needs nothing from the gateware.** The thread-mode executor is a `wfi`
  loop over a poll queue; it does not want `msip` and does not want a
  comparator. `embassy-time` would want one, and is not part of this figure.
* **There is no interrupt-mode executor for RISC-V, so there is one priority.**
  Every task runs cooperatively at `await` points and nothing preempts anything.
  A hardware interrupt cannot be a task; it wakes one, so the PLIC front end
  stays exactly as it does under RTIC.
* **What it fixes**: the deferral. A woken task runs in thread mode and may spin
  on I2C for a millisecond, so `defer_type_c`'s mask-and-defer becomes an
  `await`. What it does **not** fix is the unbounded turn — one task that does
  not `await` blocks every other task for as long as it runs, which is the same
  defect the superloop has, moved.
* **No ceiling analysis.** Shared state goes in an `embassy_sync` mutex whose
  correctness is a runtime property, not a compile-time one. This is the
  substantive thing RTIC has and Embassy does not.
* 1,048 bytes, 26% of the I-cache. 120 bytes of `.bss` for two tasks, which
  grows per task rather than being fixed.

### A cooperative run-to-completion scheduler

`src/bin/model_coop.rs`. A `READY` bitmap, a table of `fn()`, and a dispatch
loop that takes `trailing_zeros` of the ready word. Handlers claim, mark, mask,
complete; jobs run in normal context and re-arm their own source.

* **224 bytes, 5% of the I-cache.** No dependency, no `Cargo.lock` growth, no
  second `riscv` crate.
* **It needs nothing from the gateware** beyond the PLIC that exists.
* **What it fixes**: the deferral, generically. `irq::defer_type_c` and
  `PENDING_TYPE_C` become the bitmap every source uses, priority becomes the bit
  position rather than the order of statements in `main`, and the "who re-arms
  this source" question has one answer instead of one per source.
* **What it does not fix**: the unbounded turn, and nothing checks resource
  access. It is the current design with the ad-hoc parts named — an honest
  description, and the reason its number is so small.

## 4. Hardware timers, which is a separate question

`vexii_clint.py` provides exactly one `mtimecmp`. The owner's question was
whether more comparators change the ranking. Two things were measured.

**In firmware**, the same three periodic jobs (the 50 ms power poll, the 50 ms
Type-C sweep, the 1 ms tick), scheduled two ways on the same cooperative
dispatcher:

| | `.text` | `.bss` |
|---|---|---|
| three deadlines on one `mtimecmp` (`model_coop_swqueue.rs`) | 1,336 | 40 |
| three comparators, one PLIC line each (`model_coop_hwtimer.rs`) | 1,188 | 8 |

**148 bytes of `.text` and 32 of `.bss`.** That is the whole of a software timer
queue at three jobs: the 64-bit deadline array, the "which of these has passed"
scan, the "add the period, never reload from now" arithmetic, the search for the
minimum, and the three-store `set_mtimecmp` sequence — replaced by an
acknowledge write, because the comparator that fired is the one that is due.

It also removes the set-in-the-past race, which is a correctness property and
not a size one: with an auto-reloading counter there is no absolute deadline to
program, so there is no window in which the deadline is already behind `mtime`.

**In gateware** (`scripts/soc_timer_area_probe.py`, `synth_ecp5`, out of
context):

| shape | per timer, DFF | per timer, LUT4-equivalent |
|---|---|---|
| 64-bit `mtimecmp` against `mtime` | 64 | 66 |
| 32-bit auto-reloading down-counter | 65 | not stable — see below |

The DFF column is exact and is the answer to "are these cheap": the design uses
7,482 of 24,288 flip-flops, so four more comparators is 256 more, a quarter of a
percent. The LUT4 column for the 64-bit shape is exactly linear (66, 132, 264,
532 for 1, 2, 4, 8). For the 32-bit reload shape it is **not even monotonic in
the timer count** (925 at four timers, 881 at eight), which is out-of-context
mapping variance and means those numbers carry an order and not a value.

**The Fmax cost is not measured.** Nothing above is placed. The design closes
69.5 MHz against a 60 MHz target and these cells were synthesised alone; a
64-bit comparison is a carry chain on that margin and the only way to know is to
add one and rebuild. `./dev.py build` regenerates the VexiiRiscv core from
Scala, so that experiment needs an sbt server running.

### Which models the timers change

| model | with one `mtimecmp` | with N comparators |
|---|---|---|
| superloop | periodic work is an `elapsed()` check per turn, on every turn | each job is a PLIC source; no polling of the clock at all |
| cooperative | as above, plus a deadline array | 148 bytes smaller, and the set-in-the-past case disappears |
| RTIC | needs an `rtic_time::Monotonic` written from scratch | **weakens the case for RTIC**: `Monotonic` wants one `set_compare`, so N comparators means N monotonics or not using RTIC's scheduling for the periodic jobs — and scheduling is much of what RTIC is adopted for |
| Embassy | needs an `embassy-time` driver written from scratch, same shape | same weakening, same reason |

Hardware timers help the two models that have no timer abstraction and take
something away from the two that do.

## 5. What is not measured

* **Fmax, for any number of comparators.** Section 4.
* **Cache displacement.** Every `runtime` figure above is bytes of `.text`, not
  misses. What 1,552 resident bytes displace out of a 4 KiB direct-mapped cache
  depends on where the linker put them, and nothing here has run two models on
  the board and compared IPC. `./dev.py optlevel` is the tool that would.
* **Interrupt latency, for any model.** `timer.rs` reports worst lateness and
  `metrics.rs` reports worst turn, so the instrument exists; no model but the
  current one has been run.
  N of and nothing in this tree computes even one.
* **Anything on the board.** Every figure here is a build result or a QEMU
  measurement. No skeleton has been programmed.

## 6. The comparison in one line each

| model | runtime bytes | preempts | checks sharing | needs new gateware | needs new code |
|---|---|---|---|---|---|
| superloop (today) | 0 | no | no | no | none |
| cooperative | 224 | no | no | no | a dispatcher |
| Embassy | 1,048 | no | no | no | an `embassy-time` driver, for periodic work |
| RTIC | 1,552 | yes, by priority | **yes, at compile time** | no | an `rtic_time::Monotonic` |

Every model leaves `src/irq.rs`'s PLIC claim loop in place. None of the four
runtimes has a PLIC backend, and that is not a gap in any of them — it is what a
software scheduler is.
