What concurrency model should this SoC use? Everything measured so far, in one
place, so the question is not re-argued from an idle shell.

Docs: `soc-concurrency-models.md` (the model comparison),
`soc-workload-and-preemption.md` (a device-emulation load and preemption),
`rtic-usb-port.md` (real moondancer control transfers on RTIC),
`rtic-adoption.md` (the original RTIC plan).

## The one real defect

**A turn of the main loop is unbounded, and every deferred thing waits for it.**

Measured: a 50 ms poll with a **61 ms worst gap**; `load` stops every periodic
job for a whole transfer. Under a synthetic device-emulation load, worst-case
latency **1,266 us** with **700 of 2000** deadlines missed.

Nothing else on the list is a defect. The shell is **0.10-0.23% busy**, the
round-robin is fair, nothing is dropped, and `RINGS` is correct — by argument
rather than by construction, but correct.

## What each model costs

Same skeleton, same work, same `opt-level = "z"`, so `runtime` is a difference
against the language floor rather than a total:

| model | runtime `.text` | of the 4 KiB I-cache | RAM |
|---|---|---|---|
| cooperative, hand-written | **224** | 5% | none |
| hand-written **preemption** | **440** | 11% | none |
| Embassy 0.10 † | 1,048 | 26% | a future |
| RTIC 2.3 | 1,552 | 38% | **+1,332** (`.uninit`, not `.bss`) |

With the **real** control path in the tasks rather than a counter, RTIC is
**+1,568 / 38.3%** — the real workload made it *more* expensive, not less.

† not currently reproducible: `embassy-executor` has never been declared in
`Cargo.toml`, so that row cannot be re-taken.

## Findings that decide it

* **The hot footprint is 5,632 bytes against a 4 KiB cache.** It does not fit
  before any runtime is added, and the `.text`->footprint multiplier is 1.2x.
* **No RTIC subset gives preemption alone.** Both generic RISC-V backends are
  `riscv-slic` backends and the SLIC *is* how RTIC preempts. The monotonic and
  resource locking are the droppable parts.
* **RTIC cannot bind a hardware interrupt.** `binds =` names a SLIC source, so no
  RTIC task can consume the PLIC front end — and the event queue, the piece with
  the longest hand-written correctness argument, is exactly what it cannot
  check. The trade is not "compile-time correctness for cache"; it is
  "correctness for *some* shared state, for cache".
* **`riscv-rt` saves no `mepc`.** `_default_start_trap` saves caller-saved GPRs
  and nothing else — fine for a runtime that never nests, wrong the instant one
  does. Any preemptive runtime here pays a version of this.
* **Hardware timers beat a software queue and weaken both Rust frameworks.**
  Three comparators against one `mtimecmp`: 1,188 bytes against 1,336, and 8
  bytes of `.bss` against 40 — that 148 bytes IS the timer queue, and the
  set-in-the-past race goes with it. A 64-bit `mtimecmp` is 64 DFF + 66
  LUT4-equivalents, a quarter of a percent of the DFFs in use. But
  `rtic_time::Monotonic` and `embassy-time` each want exactly ONE `set_compare`,
  so cheap comparators erode the case for both.

## What is still unmeasured

Board-only, and #115 should not close until they exist:

* RTIC's own I-cache footprint and latency under a real load
* the cost of `critical_section::with` on every `pend` and `lock`
* real IPC and miss counts — note `./dev.py test`'s `ipc 1.000` is the host TSC
  under QEMU and has never measured anything
* ceiling analysis: the one thing RTIC provides that the hand-written
  dispatcher does not, and the one with no runtime number

## Children

| | |
|---|---|
| #115 | Adopt RTIC on the SoC — open; do not close while the four above are unmeasured |
| #178 | decision 19 needs to be about *why* RTIC specifically |

## Where this points, without deciding it

Preemption fixes the one real defect and costs 440 bytes hand-written. RTIC
costs 1,568 for the same fix plus guarantees it cannot extend to the queue that
most needs them. That is an argument, not a conclusion — the unmeasured list
above is what would turn it into one.
