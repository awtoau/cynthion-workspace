# RTIC with the #115 workload: one migrated path, measured

`docs/soc-workload-and-preemption.md` §6 named the cheapest next measurement —
*"flesh out `src/bin/rtic.rs` until it runs `workload::handle`, and re-run
`scripts/soc_workload.py` against it"*. This is that, plus the four other
questions the same issue lists that could be answered with it.

**Index:** [`soc-workload-and-preemption.md`](soc-workload-and-preemption.md) is
the workload and the preemption measurement ·
[`soc-concurrency-models.md`](soc-concurrency-models.md) is the five-runtime size
table · [`rtic-adoption.md`](rtic-adoption.md) is the first spike ·
[`rtic-usb-port.md`](rtic-usb-port.md) is moondancer's control path on RTIC.

Reproduce:

```bash
./scripts/soc_rtic_workload.py --events 2000     # the tables in §2-§5
./scripts/soc_rtic_monotonic.py                  # §6
./scripts/soc_rtic_workload.py --events 200 --trace
./scripts/soc_icache_model.py tmp/logs/trace-rtic.log --elf <the rtic elf>
./scripts/soc_feature_isolation_check.py         # the shipping image is unchanged
```

---

## 0. The headline

| | superloop | preempt | **RTIC** |
|---|---|---|---|
| worst arrival→handled | 1,223 µs | 271 µs | **274 µs** |
| events past the 375 µs deadline | 600 / 2,000 | 0 | **0** |
| dispatcher `.text` | — | +424 B | **+1,812 B** |
| dispatch cost per event | — | 21 instr | **~180 instr** |
| hot I-cache footprint | 3,904 B — **fits** | — | 5,312 B — **does not** |
| worst window with `mstatus.MIE` clear | 0 | 0 | **60 instr, ~1 µs** |

**RTIC fixes the unbounded turn as completely as the hand-written dispatcher
does.** 274 µs against 271 µs, zero deadline misses either way. Everything that
separates them is cost, and the cost that matters on this machine is the cache.

---

## 1. What was migrated, and what makes it a comparison

`firmware/cynthion-soc/src/bin/workload_rtic.rs`, behind `--features rticcs`.
The same `src/workload.rs` the other two models run: the same 8-byte MMIO setup
drain in the handler, the same 64-slot queue, the same 4,169 instructions of
device emulation per event, the same 1,000 µs deferred job provoked on the same
5 ms absolute grid, the same 16550 loopback arrival sequence at 4,000/s.

**`src/bin/workload_bare.rs` is new too, and it is the control that makes the
size number mean anything.** The shell's `--features workload` and
`--features preempt` builds are the control for *latency* — their figures are
in `soc-workload-and-preemption.md` §5 and `scripts/soc_rtic_workload.py`
re-asserts them on every run before it believes anything else — but they cannot
be the control for *size*: the shell is 42 KB of console, power monitor, I2C and
Type-C that an RTIC binary does not contain, so differencing the two measures the
shell. `workload_bare.rs` is `workload_rtic.rs` with a superloop where the
`#[rtic::app]` is: same modules by the same `#[path]`, same PLIC front end, same
console, same tick.

The only structural difference is who dispatches:

| | arrival | event work | the 1,000 µs job |
|---|---|---|---|
| superloop | handler enqueues | `drain()` on the next turn | `service()` on the next turn |
| preempt | handler enqueues, `dispatch::pend` | task, priority 2, `mstatus.MIE` set | task, priority 1 |
| **RTIC** | handler enqueues, `rtic::export::pend` | `#[task(binds = UsbEvent, priority = 2)]` | `#[task(binds = TypeC, priority = 1)]` |

### `binds =` names a SLIC source. Checked, not assumed.

This is the claim the earlier documents rest on, and it is worth restating with
the evidence rather than the citation, because it decides what a port can even
look like.

`rtic-macros` 2.3.0, `src/codegen/bindings/riscv_slic.rs:229-238`, builds
`hw_slice` from every hardware task's `binds =`, chains it with the dispatchers,
and passes **the whole list as `swi = [...]`**. `riscv-slic-macros` 0.2.0's
`codegen!` grammar (`src/input.rs`) accepts four identifiers — `slic`, `pac`,
`swi`, `backend` — and there is no hardware-interrupt list in it at all.

So the name in `binds =` is a software interrupt on a software controller,
whatever it is spelled. **The PLIC front end is not and cannot be an RTIC task**,
on any RISC-V target, in this version. Both binaries here prove it from the other
direction: `machine_external` is an ordinary `#[riscv_rt::core_interrupt]` living
outside the `#[rtic::app]`'s dispatch entirely, and it works.

What that costs, concretely: the queue between the handler and the task is a
`static` RTIC does not check, exactly as `rtic-usb-port.md` §5 found. What it
does **not** cost is preemption or correctness — the front end pends, and the
SLIC dispatches in priority order from there.

---

## 2. Latency: RTIC fixes the defect

2,000 events, 4,000/s, identical arrival sequence, 100 assertions of the deferred
job on an exact 5 ms grid.

| model | worst | mean | missed 375 µs | deferred worst | tick worst late |
|---|---|---|---|---|---|
| superloop, in the shell | 1,265 µs | 418 µs | 700 / 2,000 | 1,001 µs | — |
| preempt, in the shell | 271 µs | 170 µs | 0 | 1,001 µs | — |
| superloop, `workload_bare` | 1,223 µs | 402 µs | 600 / 2,000 | 1,001 µs | 3 µs |
| **RTIC**, `workload_rtic` | **274 µs** | **172 µs** | **0** | 1,005 µs | 0 µs |
| RTIC, idle polls the resource | 275 µs | 173 µs | 0 | 1,006 µs | 3 µs |

* **The tail is the same as the hand-written dispatcher's**, 274 against 271, and
  the 3 µs is the SLIC's dispatch path (§3). Both are 4.5x better than the
  superloop they replace.
* **The deferred job pays 4-5 µs**, 1,005 against 1,001. It is preempted the same
  number of times; what it pays is the SLIC's own trap on the way back.
* **Nothing dropped, nothing arrived early**, in any model.
* The bare superloop's 1,223 µs against the shell superloop's 1,265 is the
  shell's own turn — the power poll, the event drain, the two shell polls —
  which is not in the bare binary. The defect is the unbounded turn either way.

---

## 3. Dispatch overhead: measured, not estimated

`--features rticprobe` is a separate build that brackets every stage with `csrr
minstret`, in the same way `dispatch::run` brackets everything that is not the
task to produce the 70-instructions figure. **It never produces a latency
number**: two counter reads and two atomic adds per probe point would land inside
the path being timed.

The instrument is calibrated first, against an empty `critical_section::with`:
**8 instructions**, which is this build's floor and what every figure below
should be read against.

| stage | instructions | times per 2,000 events |
|---|---|---|
| `rtic::export::pend` | **74** | 2,100 |
| the front end's `mret` → the first instruction of the task body | **283** | 500 |
| `lock` on the `#[shared]` resource | **102** | 600 |
| **per event** | **~180** | |
| the hand-written dispatcher, for comparison | **21** | |

The middle row is the one nothing had measured: `mret`, the machine software
trap, riscv-rt's 16-store frame save, `__riscv_slic_pop`'s critical section and
binary-heap pop, and the threshold raise. 283 instructions, once per dispatch —
and a dispatch drains four events, because arrivals come in bursts of four, so it
is 71 instructions per event rather than 283.

`pend` at 74 instructions is the expensive one, because it happens **once per
arriving byte** where `dispatch::pend` is a single `amoor.w`. This port pends
where the preempt model pends, deliberately: a smarter port would hoist it out of
the drain loop, and the table prices that hoist at 74 × (1 − 1/4) ≈ 55
instructions an event.

**~180 instructions against 4,169 of work is 4.3% per event**, where the
hand-written dispatcher is 0.5%. At the board's measured IPC of 0.302 both
figures are cycles ×3.3, and both stay small against a 375 µs deadline. This is a
real cost and it is not the one that decides anything.

---

## 4. Critical sections: the duration is small, the aggregate is not the number

`riscv-slic` calls `critical_section::with` on every `pend`, every threshold
raise and every threshold restore, and the only implementation available is one
that clears `mstatus.MIE`. `soc-workload-and-preemption.md` §3 named this as
unmeasured and pointed at the count. **The count is the wrong instrument.**

`--features rticprobe` registers its own `critical_section` implementation — the
same single-hart body, with `csrr minstret` around it — so every section is
timed, including the ones inside generated code. Nested acquires are excluded:
only the outermost one actually holds `MIE` down.

| | sections | instructions with `MIE` clear | mean | **worst** |
|---|---|---|---|---|
| `#[idle]` waits on an atomic | 2,504 | 120,580 (0.4% of the run) | 48 | **60** |
| `#[idle]` waits on the `#[shared]` resource | 214,896 | 9,679,129 (**31%** of the run) | 45 | **60** |

**The worst window is 60 instructions**, ~1 µs at 62.5 MHz and 1 IPC, ~3 µs at
the board's 0.302. Against a 375 µs deadline that is 0.8%, and `src/timer.rs`'s
own lateness detector — which exists for exactly this and was named as the thing
to watch — reported **0-3 µs worst lateness on the 1 ms tick**, the same as the
superloop's.

The second row is the finding. Two configurations differing in one line spend
0.4% and 31% of the run with interrupts globally disabled, **and their worst
latencies differ by 1 µs**. Aggregate time with `MIE` clear does not predict
latency; the longest single window does, and it is short because every SLIC
critical section is a heap peek.

---

## 5. Priorities and shared resources, and the configuration mistake worth naming

Priorities are `src/dispatch.rs`'s exactly — event task 2, deferral 1, idle 0 —
so the two preemptive models differ in mechanism and not in policy. The
`#[shared]` resource is `progress`, written by both tasks and read by idle, so its
ceiling is 2 and the compiler computes it. That is the one piece of this
workload's state RTIC checks; the event queue and the frame accumulator are
`static`s, for §1's reason.

**An `#[idle]` that polls a `#[shared]` resource is a priority-2 blocker.**
`lock` raises the SLIC threshold to the ceiling, so `pend` from the front end
finds `is_ready()` false, does not raise `msip` at all, and the event waits for
idle's lock to finish. It is 100% duty on the poll loop: 214,896 critical
sections against 2,504.

It costs almost nothing *here* — 1 µs — because the lock is 102 instructions and
the event is 4,169. It would not stay that way with a resource whose critical
section did real work. The fix is to wait on something outside RTIC, which is
what `--features rticspin` off does and is also, uncomfortably, a small argument
against the thing RTIC is adopted for.

**This is not something the example configurations warn about**, and it is the
kind of thing only a run finds. Both configurations are in the tree so that
either can be re-measured.

---

## 6. PLIC acknowledgement and completion: counted, not argued

The front end keeps `src/irq.rs`'s order exactly — **complete, disable,
record** — because the other order threw the completion away, left `claimed` set,
and gated a Type-C source off permanently, on the board.

Counted over 2,000 events rather than asserted:

    claims 1,108   completes 1,208   (bare: 1,109 / 1,209)

The 100 extra completions are the deferral source, completed once by
`defer_workload` and once by the loop's tail, which is what `src/irq.rs` does. The
proof that the ordering is right is the other half of the report: **100
assertions, closest gap exactly 5,000 µs**, on a source that is masked and
re-enabled once per assertion. A completion lost anywhere in that path stops the
source for the rest of the session and the count would have stalled.

RTIC is not involved in any of it, which is the point: the PLIC survives adoption
untouched and the adapter is 20 lines.

---

## 7. A CLINT monotonic exists. The claim that it could not was wrong.

`rtic-adoption.md` §9 and `rtic-usb-port.md` §8 both say `rtic-monotonics` 2.2.1
"has SysTick, STM32 and Silabs and nothing for RISC-V".

**It ships `esp32c3.rs` and `esp32c6.rs`, both RISC-V.** What it has no backend
for is the *CLINT*, and `rtic_time::TimerQueueBackend` is five methods:

| method | on this SoC | where the hard part already is |
|---|---|---|
| `now()` | 64-bit `mtime`, high-low-high retry | `src/timer.rs:170` |
| `set_compare(t)` | the three-store `mtimecmp` sequence | `src/timer.rs:197` |
| `clear_compare_flag()` | nothing — the CLINT has no flag | |
| `pend_interrupt()` | `mtimecmp = 0` — a real hardware pend | |
| `timer_queue()` | a `static` | |

`src/bin/mono_rtic.rs` is that, about 60 lines. The sorted queue, the insert
path, the re-arm on an earlier insert and the deadline-in-the-past case — the
bulk of the work `soc-workload-and-preemption.md` §3 lists — are
`rtic_time::TimerQueue`'s.

Measured, 100 periods on an absolute 5 ms grid with `delay_until`:

    late    worst 7 us   mean 7 us
    early   0 ticks
    past    a deadline set 2,500 us behind now fired 8 us later

The third line is the race a fixed-period `mtimecmp` never has to meet, exercised
on purpose: one period overruns by 1.5x, so the next `delay_until` is asked for
an instant that has already gone. It fires immediately rather than waiting for
`mtime` to wrap.

**Two costs.** The dependency graph goes **15 → 30 → 41 packages** for shell →
RTIC → RTIC with a monotonic. And **the CLINT has one comparator**, so adopting
one is not additive: `src/timer.rs` cannot be in the same binary, and the linker
says so directly —

    error: symbol `MachineTimer` is already defined

Every periodic job moves onto the queue at once, including the 1 ms tick that
stamps every log line. That is what a monotonic is *for*, and it is still a
migration rather than an addition.

Also found while building it: **the SLIC backend supports async software tasks
without being told.** `rtic-macros`'s `pre_init_preprocessing` rejects an explicit
`dispatchers` argument and synthesises one SLIC source per software-task priority
instead.

---

## 8. The I-cache, which is the one that decides

`scripts/soc_icache_model.py` over both traces, 200 events, windowed from the
workload's first instruction. 64 sets × 1 way × 64 B, from `vexii_cpu.py`'s own
flags.

| | `workload_bare` | `workload_rtic` |
|---|---|---|
| blocks executed | 1,302,879 | 2,085,672 |
| line accesses | 1,409,424 | 2,295,405 |
| **misses** | **342** (0.02%) | **1,329** (0.06%) |
| **footprint** | **61 lines = 3,904 B** | **83 lines = 5,312 B** |
| sets holding >1 line | 9 | 23 |

**The bare binary's hot footprint fits in the 4 KiB cache. The RTIC one does
not.** 3,904 B against 4,096; 5,312 B against 4,096. That is the sharpest form
the cache question has taken, and it is sharper than
`soc-workload-and-preemption.md` §4's version because there the shell's own
5,632 B already exceeded the cache before any runtime was added, so the
dispatcher's 512 B could only make a bad number worse.

+1,812 bytes of `.text` cost +1,408 bytes of footprint — a ratio of 0.78, where
the hand-written dispatcher's 440 bytes cost 512 (1.16). RTIC's code is more
contiguous; it is also four times as much of it.

Two honest limits on the miss counts, both from
`soc-icache_model.py`'s own docstring: the addresses are the QEMU build's, so
which set a function lands in differs from the board, and there is no prefetch or
speculation in the model. **Footprint transfers; the specific conflicts do
not.**

---

## 9. What it cost to build, and what it would cost to go further

`workload_rtic.rs` is 676 lines against `workload_bare.rs`'s 227, and about 200
of the difference is the probe, the instrumented critical section and comments.
`src/workload.rs` needed 86 lines changed, all additive and all `#[cfg]`-gated:
`usb_drain`, `type_c_run`, `begin`, `finish`, `completed`, and one `if` in the
model name. **No shim beyond `rtic-adoption.md` §3's five was needed**, for the
third time.

Dependencies, `cargo tree` on the QEMU target:

    shell                 15 packages
    + rtic                30
    + rtic + monotonic    41

### What has NOT been measured, and why

**Nothing here has run on the board, and this workload cannot.** The arrival
generator injects bytes through the 16550's local loopback, and
`ecp5-test/riscv/uart16550.py:545` says in as many words that *"the DATA half of
loopback — THR looping back into RBR — is not implemented"*. The MSR half is, for
Linux's `autoconfig`; the data half is not. So every figure above is QEMU with
`-icount`, plus a cache model, and:

* **`ICACHE_MISS` and `STALLED_CYCLES_FRONTEND` are still unmeasured**, on a core
  that has `--performance-counters 4` enabled and `src/bench.rs`'s `hpm` module
  already reading them. What stands between here and those numbers is a gateware
  change: route the TX stream back into the RX FIFO when `MCR.LOOP` is set,
  rebuild the bitstream, and flash three firmware images. That is the single
  highest-value thing left, and it is a bitstream away rather than a rewrite.
* **IPC.** QEMU retires one instruction per cycle; the board measures 0.302 at
  `opt-level = "z"`. Every instruction count here is real and every cycle
  conversion is a 3.3x estimate.
* **Stack.** No model here gives a task its own stack, so nothing measures what
  RTIC's `#[shared]`/`#[local]` placement in `.uninit` does to the 8 KiB floor
  `memory.x` reserves. `rtic-usb-port.md` §4 has the only figure: +1,332 bytes,
  transferred off the stack rather than added.

---

## 10. What this settles

| question | before | now |
|---|---|---|
| does RTIC fix the unbounded turn? | not measured | **settled: yes**, 274 µs against preempt's 271 |
| what does RTIC's dispatch cost? | estimated | **measured: ~180 instr/event**, 4.3% of an event |
| what does `critical_section` cost per pend? | not measured | **measured: 74 instr, worst window 60** |
| does the PLIC survive adoption? | argued | **counted: 1,108 claims, 1,208 completes, nothing gated off** |
| is there a CLINT monotonic? | "nothing for RISC-V" — **wrong** | **written and measured: 7 µs worst late** |
| what does the runtime displace in the cache? | projected at ~47% | **measured: the hot set stops fitting**, 3,904 B → 5,312 B |
| are the priorities and resources configurable? | asserted | **settled: yes**, and one obvious configuration is a priority-2 blocker |
| is checked resource access worth 1,812 bytes? | a judgement | still a judgement |

**The trade, stated in the terms this measurement supports:** RTIC buys
preemption that is already available for 424 bytes, plus a compile-time ceiling
on whatever state can be reached from a task — which here is one counter pair,
because the piece with the longest correctness argument is produced by a hardware
handler RTIC structurally cannot own. It costs 1,812 bytes of `.text` on the hot
path of a 4 KiB direct-mapped cache that the same program otherwise fits inside,
~180 instructions an event, and 15 packages.

Nothing in that is a reason to reject RTIC on a machine with a bigger cache.
On *this* machine, the cache line of the table is the whole argument, and it is
now a measurement rather than a projection.
