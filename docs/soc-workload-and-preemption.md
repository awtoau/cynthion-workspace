# The workload this SoC is for, and what preemption costs against it

Issue #115. Everything measured before this was against an **empty shell** at
0.10–0.23% busy, and the repo owner has withdrawn that baseline: this core is
being built as a **USB controller**, and the workload that decides the
concurrency question is device emulation — bursty, latency-sensitive, arriving
at the host's convenience, cache-hostile by nature.

An idle shell is not a workload. This document replaces it.

**Index:** [`soc-concurrency-models.md`](soc-concurrency-models.md) is the size
comparison of five runtimes · [`rtic-adoption.md`](rtic-adoption.md) is the RTIC
spike · [`riscv-core-build.md`](riscv-core-build.md) has the CPU's performance
counters.

Reproduce with `./scripts/soc_workload.py` and
`./scripts/soc_icache_model.py`. Neither touches the board.

---

## 0. The headline, and what it does not say

| | superloop (today) | preemption |
|---|---|---|
| worst arrival→handled | **1,266 µs** | **271 µs** |
| mean | 418 µs | 170 µs |
| events past the 375 µs deadline | **700 of 2,000** | **0 of 2,000** |
| the deferred job's own worst case | 1,001 µs | 1,001 µs |
| CPU busy | 26% | 26% |
| `.text` | — | **+440 bytes** |
| cost per dispatch | — | **70 instructions** |

**This does not say adopt RTIC.** It says the one defect the earlier analysis
named — the unbounded turn — is real under a real workload, is worth 4.7x on
the tail, and is fixable for 440 bytes without RTIC, without a monotonic and
without resource locking. What RTIC additionally sells is checked resource
access, and **nothing here measures that**, because a compile-time property has
no runtime number. Section 6 says what would decide it.

---

## 1. Q1 — what the real workload is, and how faithfully it is reproduced

### What moondancer actually does

moondancer is a workspace member of `greatscottgadgets/cynthion` at
`firmware/moondancer` — there is no standalone repo, and neither it nor
`luna-soc` is checked out in this tree. Every line number below was read from
the mirror. `firmware/cynthion-soc/src/workload.rs`'s module comment carries the
same citations beside the code that imitates them.

| step | where | what it costs |
|---|---|---|
| handler drains the 8-byte setup FIFO **one MMIO read per byte** | `lunasoc-hal/src/usb.rs:380-392` | 8 bus transactions |
| enqueues into a **64-slot** queue | `moondancer/src/bin/moondancer.rs:28` | |
| **a full queue is `loop { nop }` in interrupt context** | `bin/moondancer.rs:30-46` | the queue is an assertion, not a buffer |
| main loop dispatches | `moondancer/src/gcp/moondancer.rs:82` | |
| zeroes a **1 KiB** buffer per command, verbs zero their own | `bin/moondancer.rs:460`, `gcp/moondancer.rs:560`, `:821`; the constant is `libgreat/src/gcp.rs:15` | 1–3 KB of memset, independent of payload |
| payload copied repeatedly FIFO→`rx_buffer`→packet | `gcp/moondancer.rs:147-162` | |
| response written **one 8-bit MMIO store per byte** | `lunasoc-hal/src/usb.rs:487` | 512 bus transactions |

Upstream's own measurements, in comments beside the code that produced them:
`moondancer/examples/bulk_speed_test.rs:390` reads **5.03 MB/s**, `:394` reads
6.39 MB/s with no memory access behind it, `:414-416` reads ~4.04 MB/s for the
shape actually shipped. At 60 MHz, 5.03 MB/s is **11.9 cycles per MMIO byte
store**, so a 512-byte packet is **~5,000–6,000 cycles, 85–100 µs** — about 80%
of a 125 µs high-speed microframe. That is why the part tops out near one packet
per microframe, and it means moondancer under bulk load genuinely runs at
**70–80% CPU**.

### The deadline, which is not the one people assume

Three different numbers get called "the USB deadline" and only one of them binds
here.

**Packet level is soft.** luna-soc's IN endpoint NAKs every IN token while the
CPU is still filling the FIFO — *"We'll wait for it to do so, and NAK any
packets that arrive"*, `usb2/ep_in.py:279-292` — and the OUT endpoint NAKs when
it is not primed (`nak_receives = token.is_out & ~ready_to_receive & ~stalled`,
`usb2/ep_out.py:277`). USB 2.0 §8.5.3.3 makes that legitimate flow control. **A
late CPU costs a retry, not a protocol error.** Anyone reasoning from "125 µs
microframe" is reasoning about a deadline the hardware does not impose.

**Transfer level is hard and generous.** USB 2.0 §9.2.6.4: first data packet of
a control read within **500 ms**; status stage within **50 ms** of the last data
packet; a request with no data stage complete within **50 ms**. §9.2.6.3:
`SetAddress` complete within 50 ms, then 2 ms recovery. §9.2.6.1: 10 ms of reset
recovery. Nothing this firmware does is within three orders of magnitude of
those.

**There is exactly one hard, silent window, and it is in the gateware.** The
control endpoint's setup FIFO is 8 bytes deep and is *cleared by the arrival of
the next SETUP token*:

    clear_fifo = new_setup | reset_requested        ep_control.py:130
    ResetInserter(clear_fifo)(SyncFIFOBuffered(width=8, depth=8))   :135

Miss it and the previous setup packet is destroyed with the host already ACKed;
the firmware then reads zero bytes (`moondancer/src/util.rs:62-63`). That
endpoint **cannot NAK**, by construction and by spec: *"always ACKs packets, and
does not allow for any flow control; as a USB device must always be ready to
accept control packets. [USB2.0: 8.6.1]"* — `ep_control.py:30-34`.

So the deadline that binds is **one host control-transfer inter-arrival**. At
high speed a short control transfer occupies about three microframes, so
**~375 µs**, and that is what this workload is measured against. It is three
orders of magnitude tighter than §9.2.6.4 and three times looser than a
microframe. A model built against either of those is modelling the wrong thing.

### The synthetic, and exactly where it lies

`firmware/cynthion-soc/src/workload.rs`, behind `--features workload`. Per
event: an 8-byte setup drain one MMIO read at a time, a 64-slot queue, two 1 KiB
buffer fills, three 512-byte copies, and 512 MMIO stores. Measured at
**4,169 instructions**, against upstream's ~5,000–6,000 cycles.

Arrivals are **real 16550 interrupts** on a real PLIC source into the real
`irq.rs` handler. They are injected by the 1 ms tick through the 16550's **local
loopback** (`workload::tick`), four per tick, 4,000/s — half moondancer's peak,
chosen so the queue does not saturate, because at saturation the latency figure
measures the backlog rather than the scheduling.

The generator is **in the timer handler on purpose**. A generator in the main
loop would be stopped by exactly the thing under test and would hide the defect
it exists to measure.

Where it differs, stated plainly:

* **Not a USB device controller.** One received byte is one USB event. The
  "FIFO" is the 16550's scratch register — eight bits of MMIO with no side
  effect of any kind, so a byte-at-a-time loop over it is one real bus
  transaction per byte, which is the property that makes moondancer's loop cost
  what it costs.
* **Self-clocked, not host-clocked.** This buys reproducibility — the two models
  see the byte-identical arrival sequence, which is what makes the comparison a
  comparison — and gives up the host's own jitter. Bursty at 1 ms granularity,
  not microframe-aligned.
* **No second USB port.** moondancer spends three to six interrupts on its host
  link for every one on the port under test. Leaving that out makes the
  superloop look *better*, not worse.
* **The long job is synthetic.** On the board it is a millisecond of I2C clearing
  a FUSB302B on a shared controller. Under QEMU there is no I2C, so it is spun
  on the CLINT for the same 1,000 µs.
* **QEMU retires one instruction per cycle.** The board measures IPC 0.302 at
  `opt-level = "z"`. Instruction counts here are real; cycle counts are
  optimistic by ~3.3x.

### Making QEMU able to measure anything at all

Two changes to how QEMU is run, both of which matter and neither of which was
in the tree:

**`-icount shift=4`.** Without it `mcycle` and `minstret` on `virt` are *both*
the host TSC — which is why `./dev.py test` has reported `ipc 1.000` for every
build ever made, and that figure is not a measurement of anything. With
`-icount`, `minstret` becomes the true retired instruction count and `mcycle`
becomes virtual nanoseconds. `shift=4` is 62.5 MHz, within 4% of the board's
60 MHz.

**`-rtc clock=vm`.** The deferral source is `virt`'s goldfish RTC alarm, and
goldfish arms its timer on `rtc_clock`, which defaults to `QEMU_CLOCK_HOST`. A
5 ms alarm therefore fired 5 ms of *wall* time later, and under `-icount
sleep=off` virtual time runs ahead of wall time by whatever TCG manages — an
effective 8.5–9.8 ms, varying run to run. That confound was visible as the two
models seeing 46 and 53 assertions of a job that should have asserted 100 times
each. With `clock=vm` both see exactly 100, closest gap exactly 5,000 µs.

---

## 2. Q2 — preemption alone, without RTIC

RTIC bundles four separable things: resource locking with a ceiling analysis,
preemption between priorities, a monotonic, and the SLIC. The defect list says
**the one real defect is the unbounded turn, and only preemption fixes it.** So:
what does preemption alone cost?

`firmware/cynthion-soc/src/dispatch.rs`, behind `--features preempt`. A ready
bitmap, a threshold, a table of `fn()`, 172 lines. No locking, no monotonic, no
software interrupt controller, no second `riscv` crate, no `Cargo.lock` growth.

    variant                      board .text   .bss
    shell                             41,400  9,656
    shell + workload                  42,776 12,276
    shell + workload + preempt        43,216 12,300
    ------------------------------------------------
    the dispatcher alone                +440    +24

**440 bytes**, of which ~140 is the instrumentation that produced the
70-instructions-per-dispatch figure below; measured at **+300 / +20** before
that was added. Against RTIC's **1,552 bytes** and 32 packages where there were
14 (`rtic-adoption.md` §4).

**70 instructions per dispatch**, measured directly: `dispatch::run` brackets
everything that is not the task with `minstret` and accumulates it — 42,000
instructions over 600 dispatches. Against 4,169 instructions of work per event,
that is **1.7%**.

### Is there an RTIC configuration that gives just this?

**No, and the reason is structural rather than a matter of feature flags.** RTIC
2.3's generic RISC-V backends are both `riscv-slic` backends
(`rtic-adoption.md` §2): `rtic-macros`'s binding passes every hardware task's
`binds =` name to `riscv_slic::codegen!` as a software interrupt, and there is no
other path. You cannot take RTIC's preemption without taking the SLIC, because
the SLIC *is* how RTIC preempts. And `riscv-slic`'s API is
`critical_section::with` throughout, so you cannot take the SLIC without taking
a global interrupt disable on every `pend` — see Q3.

Dropping the monotonic is possible (just do not schedule), and dropping resource
locking is possible (declare nothing `#[shared]`), but those are the two cheap
parts. The expensive part is not separable.

### What the hand-written one does not have

Ceiling analysis. Two tasks that share state, or a task sharing with the main
loop, are the caller's problem exactly as they are today. That omission is the
measurement: it is precisely what separates "preemption" from "RTIC", and it is
the thing with no runtime number.

---

## 3. Q3 — how it actually works, top down

### The interrupt path, pin to task

| # | stage | where | what it costs |
|---|---|---|---|
| 1 | a device asserts a level | 16550 with `IER.ERBFI` set, `src/uart.rs:95` | — |
| 2 | the PLIC gates it against enable and threshold, ORs into one line | `ecp5-test/riscv/vexii_plic.py`; QEMU's `plic@c000000` | combinational |
| 3 | the CPU takes `MachineExternal`, clears `mstatus.MIE` **in hardware** | — | pipeline flush |
| 4 | riscv-rt's `_default_start_trap` pushes a trap frame | riscv-macros 0.4.1 `src/riscv_rt/asm.rs:180-195` | 16 stores + 16 loads |
| 5 | `_start_trap_rust` → `_dispatch_core_interrupt` indexes `__CORE_INTERRUPTS` | riscv-rt 0.18 `src/interrupts.rs` | a load and an indirect jump |
| 6 | `machine_external` reads the claim register | `src/irq.rs:228`, `src/plic.rs:180` | **one uncached MMIO read** |
| 7 | route by source number | `src/irq.rs:230-247` | linear scan of ≤2 entries |
| 8a | console: drain RBR into the ring until empty or full | `src/irq.rs:179` | one MMIO read per byte |
| 8b | Type-C: **complete, then disable**, then record | `src/irq.rs:290` | two MMIO writes |
| 9 | complete the claim — always, including for a source with no handler | `src/plic.rs:195` | one MMIO write |
| 10 | loop to 6 until the claim reads 0 | `src/irq.rs:229` | one more MMIO read |
| 11 | **(preempt only)** `dispatch::run` at the tail | `src/irq.rs:254` | see below |
| 12 | restore the frame, `mret` | asm.rs | 16 loads |

**Where the latency is.** Steps 3–5 and 12 are fixed and unavoidable — a trap
frame and a dispatch table. Step 6 and step 9 are the PLIC's, one uncached MMIO
transaction each; on this SoC that is a Wishbone round trip, not a cache hit.
Everything after that is the handler's own choice, and **that is the only part
any concurrency model changes.** No runtime under consideration touches steps
1–5.

The claim/complete pairing at 6 and 9 is load-bearing in a way worth restating:
a claim that is never completed gates that source off for the rest of the
session, with `pending` reading zero and the peripheral asserting into the void.

### `dispatch::run`, and the sharp edge under it

`src/dispatch.rs:116`. For each ready task whose priority is above the one
already running: clear the bit, raise the threshold, **set `mstatus.MIE`**, call
the task, clear `MIE`, drop the threshold.

Setting `MIE` inside the trap is the whole mechanism. The task runs with
interrupts on, so a byte arriving in the middle of a millisecond of I2C is
serviced in the microseconds it takes to trap. The handler nests: the new trap
runs the claim loop, pends what it found, and calls `run` again — which does
nothing unless what it pended outranks the task already running. **The threshold
is what bounds the nesting to one frame per priority level**, and the measured
worst depth is 2, which is the claim checked rather than asserted.

**riscv-rt does not save `mepc`.** `_default_start_trap` saves the caller-saved
GPRs and nothing else: no `mepc`, no `mcause`, no `mstatus`. That is correct for
a runtime that never nests and wrong the instant one does — the inner trap
overwrites `mepc` and the outer `mret` returns to wherever the inner trap was
taken from. The symptom is an instruction stream that resumes in the wrong
place, occasionally, under load. `dispatch::run` therefore saves and restores
both around every task (`src/dispatch.rs:162-180`), four CSR accesses per
dispatch. **This is a cost of preemption on this runtime that neither existing
document mentions, and any adoption of RTIC pays some version of it too.**

### The timer, and what a monotonic has to guarantee that one `mtimecmp` does not

`src/timer.rs`. The CLINT raises `MachineTimer` while `mtime >= mtimecmp`. The
handler reads the deadline, adds one period, writes it back, and returns.

Two properties the current code has and states its reasons for:

* **Add the period, never reload from now** (`timer.rs:245`). Reloading adds the
  interrupt latency to every period, so the tick runs slow in proportion to
  system load — worst exactly when a timestamp matters. Adding puts deadlines on
  an absolute grid.
* **The three-store sequence** (`timer.rs:197`): write `mtimecmp_lo = 0xffffffff`
  first, so no combination of old high and new high can match, then the high
  half, then the low half. Two stores cannot be atomic on rv32 and the
  intermediate value is a real deadline the comparator will act on.

**What `rtic_time::Monotonic` requires beyond that.** The claim that followed
here — that `rtic-monotonics` 2.2.1 has no RISC-V implementation — was wrong: it
ships `esp32c3` and `esp32c6` backends. What it has none of is a **CLINT**
backend, and all four requirements below are met by
`rtic_time::TimerQueue` plus the five methods in `src/bin/mono_rtic.rs`
([`rtic-workload-port.md`](rtic-workload-port.md) §7). The list is still what
they are:

1. **A `now()` that is monotone and wide enough not to wrap** during any
   scheduled delay. `timer.rs::mtime` already does the rv32 high-low-high retry
   loop; `clock.rs` deliberately reads only the low half and documents the 71.6 s
   wrap, so the monotonic cannot use `clock::now`.
2. **`set_compare(instant)` for an *arbitrary* instant**, not a fixed period.
   That is where the "did I just set a deadline in the past?" race lives, and
   `Monotonic` has to detect it and fire immediately rather than wait 71.6
   seconds for a wrap.
3. **N software timers multiplexed onto one comparator** — a sorted queue, an
   insert path, and a re-arm on every insert that lands earlier than the head.
   `mtimecmp` gives one deadline; a monotonic has to give many.
4. **`on_interrupt` / `enable_timer` hooks** wired into RTIC's own dispatch.

So `mtimecmp` is one deadline on a grid, and a monotonic is an arbitrary-deadline
priority queue with a wrap-safe clock under it. The gap is the sorted queue and
the set-in-the-past case, and it is the bulk of the remaining work in
`rtic-adoption.md` §7 step 2. Measured in
[`soc-concurrency-models.md`](soc-concurrency-models.md) §4 at **148 bytes** of
`.text` for three jobs — which is what a hardware comparator per job would save,
and is why more comparators weaken the case for RTIC rather than strengthening
it.

### What `critical_section::with` does on every `pend` and every `lock`

`riscv-slic` calls `critical_section::with` throughout, and the only
implementation available here is `riscv`'s `critical-section-single-hart`, which
**clears `mstatus.MIE`**.

So under RTIC, `pend` from inside the PLIC handler disables *all* interrupts for
its duration — including the timer. In the middle of a USB transfer that is a
window in which the 1 ms tick cannot run and a second endpoint cannot be
serviced. `timer.rs` already reports worst lateness, so the detector exists
before the bug does — watch `late` in `time`.

**Not measured here**, because the RTIC skeleton has never run this workload;
`src/bin/rtic.rs` is two tasks incrementing a counter. What can be said is what
the hand-written dispatcher does instead: it takes **no** global critical
section. `pend` is one `fetch_or` on an `AtomicU32`, which on riscv32imac with
the A extension is an `amoor.w` — a single instruction, no interrupt disable,
and interrupts stay on across it.

---

## 4. Q4 — what a resident runtime displaces in the I-cache, under load

### How this was measured, and why it is a model

The CPU's own counters are the right instrument — `ICACHE_MISS 0x11`,
`STALLED_CYCLES_FRONTEND 0x04`, `--performance-counters 4` is already enabled
and `src/bench.rs`'s `hpm` module already reads them
([`riscv-core-build.md`](riscv-core-build.md)). **They are on the board, and the
board was unavailable for this work.** QEMU has no cache at all, and Fedora's
`qemu-system-riscv32` is built without plugin support (`-plugin help` says so),
so the contrib cache plugin is not available either.

The substitute is `scripts/soc_icache_model.py`: QEMU's `-d in_asm,exec,nochain`
gives every translation block's extent once and every execution of one in order,
which is enough to replay the exact sequence of instruction-cache lines through
a model of the real geometry — 64 sets × 1 way × 64 bytes, from
`vexii_cpu.py`'s `GENERATE_FLAGS`.

Exact about: the geometry, the line sequence per block, the order blocks ran in.
A model about: no speculative fetch, no prefetch, no branch predictor, and the
addresses are the **QEMU build's** (linked at 0x80000000, `.text` 38,724) rather
than the board's (flash window, `.text` 42,776), so the set a given function
lands in differs. Miss *counts* transfer; the specific conflicts do not.

### The result

200 events, windowed from the workload's first instruction so the boot is out:

| | superloop | preempt |
|---|---|---|
| blocks executed | 1,241,490 | 2,104,024 |
| line accesses | 1,482,676 | 2,214,978 |
| **misses** | **1,599** (0.11%) | **483** (0.02%) |
| **footprint** | **88 lines = 5,632 B** over 51 sets | **96 lines = 6,144 B** over 49 sets |
| sets holding >1 line | 26 | 34 |
| evicting pairs | 37 | 47 |

**Three things this says, and one it does not.**

**1. The workload does not fit, and it did not before the dispatcher either.**
The hot footprint is **5,632 bytes against a 4 KiB cache** — 1.4 KB over, with
88 distinct lines competing for 64 sets. The idle shell's "38% of the cache" was
measured against a tight loop that fits; under load the resident set already
exceeds the cache before any runtime is added.

**2. The dispatcher's 440 bytes of `.text` cost 512 bytes of footprint**, eight
lines, **12.5% of the cache** — not 38%, and not free either. The ratio is
above 1.0 because a function that spans a line boundary occupies two lines
whatever its size, and this is the number that should be used to project any
runtime's cache cost: **bytes of `.text` understate footprint**, here by 1.2x.
Applying that ratio to RTIC's 1,552 bytes projects ~1.9 KB, ~47% of the cache —
a projection, not a measurement, and the reason to run this workload against
`src/bin/rtic.rs` before believing either number.

**3. Absolute misses fell, and the reason is not flattering to the superloop.**
1,599 → 483. Under preemption the code that runs between events is a one-line
spin; under the superloop it is the service-and-drain call chain, which evicts.
**The real shell's main loop is much larger than the one in this harness** — the
power poll, the event drain, the error report, the Type-C service and two shell
polls — so the real superloop's inter-event eviction is worse than measured
here, not better. That is a reason to distrust the 3.3x improvement as a
headline while accepting its direction.

**What it does not say:** anything about IPC or stalls. Those are
`STALLED_CYCLES_FRONTEND` and `STALLED_CYCLES_BACKEND` on the board, and QEMU's
IPC is 1.0 by construction.

---

## 5. The migration: Type-C deferral, ported

The smallest real case, and the one with a scar on it. `irq::defer_type_c`
(`src/irq.rs:290`) masks the PLIC source, records a port in `PENDING_TYPE_C`,
and depends on `typec::service` being called from the main loop. Its comment
about **completing before disabling** is there because the other order threw the
completion away, left `claimed` set, and — since
`pending[i] = sources[i] & ~claimed[i]` — gated the source off permanently. One
interrupt per port per boot, found on the board.

Ported at `src/irq.rs:262` as `defer_workload`, and the port keeps that order
exactly: **complete, disable, record.** What changes is only who runs the
service afterwards — the main loop, or `dispatch::pend(TASK_TYPE_C)` and a task
that runs with `mstatus.MIE` set.

**The masking stays.** That is worth stating because it is tempting to think
preemption removes it: it does not. The source is masked because a FUSB302B is
still asserting until it is cleared over I2C, which is a property of the device,
not of the scheduler. What preemption removes is the *dependence on the turn* —
`PENDING_TYPE_C`, `take_type_c`, and the requirement that the main loop reach
the service before anything else can happen.

Under QEMU there is no Type-C hardware (`target::TYPE_C_IRQS` is empty), so the
stand-in is `virt`'s goldfish RTC alarm on PLIC source 11: a second
level-sensitive line the handler cannot clear cheaply and the guest can
schedule. Same obligation shape, no FUSB302B.

### Result

2,000 events at 4,000/s, a 1,000 µs deferred job asserted 100 times on an exact
5 ms grid, identical arrival sequence, identical work per event (4,198 vs 4,169
instructions):

    model       worst    mean   missed 375 us   deferred worst   dropped
    superloop   1266 us  418 us   700 / 2000        1001 us         0
    preempt      271 us  170 us     0 / 2000        1001 us         0

* **4.7x on the tail, 2.5x on the mean, and the deadline misses go to zero.**
  The superloop's 1,266 µs worst case is the 1,000 µs job plus the queueing
  behind it — the unbounded turn, exactly as named.
* **The deferred job pays nothing.** 1,001 µs either way. It starts sooner under
  preemption and is then interrupted, and the two cancel at this load. It is not
  free in general: at higher event rates the preempted job finishes later, and
  that is the trade to watch.
* **The queue never overflowed** in either model, so both figures are
  scheduling and not backlog.
* **Worst nesting depth 2**, which is the bound `dispatch.rs` claims.

---

## 6. What remains undecided, and what would decide it

**Decision 19 stays open.** This measures preemption, which is one of RTIC's
four parts, and it measures it in a hand-written form. It does not measure RTIC.

| question | status | what would settle it |
|---|---|---|
| is the unbounded turn a real defect? | **settled: yes** | measured, §5 |
| does preemption alone fix it? | **settled: yes**, 4.7x for 440 bytes | measured, §5 |
| is there an RTIC subset that gives preemption alone? | **settled: no** | `rtic-macros`'s binding has one path, §2 |
| what does RTIC's runtime displace in the cache? | **settled**: +1,408 B of footprint, 34% of the cache, and the hot set stops fitting — 4,032 B → 5,440 B | [`rtic-workload-port.md`](rtic-workload-port.md) §8 |
| what does `critical_section::with` cost per `pend`? | **settled**: 74 instructions, worst window 60 | [`rtic-workload-port.md`](rtic-workload-port.md) §3-§4 |
| does RTIC fix the unbounded turn too? | **settled: yes**, 274 µs against preempt's 271 | [`rtic-workload-port.md`](rtic-workload-port.md) §2 |
| IPC, I-cache misses, frontend/backend stalls | **not measured, and not measurable here** | the board — and this workload cannot reach it: the gateware's 16550 has no DATA loopback (`uart16550.py:545`), so the arrival generator needs a bitstream change first |
| is checked resource access worth 1,112 bytes over the dispatcher? | **not measurable** | a judgement, not a number |

The last row is the real question and this document cannot answer it. `RINGS`
and `PENDING_TYPE_C` are correct by four paragraphs of argument rather than by
construction, and the Type-C ordering bug in §5 is precisely the class of defect
a ceiling analysis does not catch and careful reading did — after it reached the
board. That cuts both ways, and someone has to decide which way.

**The cheapest next measurement was to flesh out `src/bin/rtic.rs` until it ran
`workload::handle`.** Done, as `src/bin/workload_rtic.rs` against a same-shape
`src/bin/workload_bare.rs`, and it filled three of the four rows above:
[`rtic-workload-port.md`](rtic-workload-port.md).

**The cheapest one now needs the board, and the board needs a bitstream.** The
16550 in `ecp5-test/riscv/uart16550.py` implements the MSR half of local loopback
and not the DATA half, so nothing on the FPGA can inject an arrival. Route TX
back into the RX FIFO under `MCR.LOOP`, rebuild, flash three images, and
`ICACHE_MISS` and `STALLED_CYCLES_FRONTEND` answer the cache question with the
CPU's own counters instead of a model.

---

## 7. Reproducing

```bash
./scripts/soc_workload.py --sizes          # build all variants, size them
./scripts/soc_workload.py --events 2000    # the table in §5
./scripts/soc_workload.py --events 200 --trace
./scripts/soc_icache_model.py tmp/logs/trace-workload.log \
    --elf tmp/workload-builds/workload-qemu/riscv32imac-unknown-none-elf/release/cynthion-soc
./scripts/soc_image_identical.py           # the shipping image is unchanged
```

Neither `workload` nor `preempt` is in `./dev.py gate`, and both are off by
default. `soc_image_identical.py` reports 43 `.text` symbols identical in size
against `main`; the `.text` bytes differ by 1,454 in a section of the same total
size, which is LTO re-ordering caused by adding `[features]` entries to
`Cargo.toml` — cargo seeds `-C metadata` from the feature set, that seeds symbol
hashes, and those decide emission order. `.rodata` differs by 23 bytes, which is
`build.rs`'s commit stamp.
