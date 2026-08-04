# Adopting RTIC on the SoC: it compiles, and it does not use the PLIC

Issue #115. The interrupt work that unblocked it landed in `26ef424`: a standard
PLIC, a standard CLINT, both consoles interrupt-driven. What remained was RTIC
itself, which nothing in the tree had ever compiled.

Now something has. `firmware/cynthion-soc/src/bin/rtic.rs` is a working
`#[rtic::app]` for this machine, built by `--features rtic` and by
`scripts/rtic_probe.py`, linked for the board and for QEMU. This document is
what that build taught, and what the migration would cost.

**The headline is a correction.** `docs/decisions.md` decision 7 says the PLIC
was written from the specification partly because "RTIC's RISC-V backend and
every generic Rust PLIC driver (`riscv-peripheral`) expect this register map, so
a non-standard controller means writing an RTIC backend before RTIC can be
used". The second half of that sentence is true of `riscv-peripheral` and
**false of RTIC**. RTIC has no PLIC backend, for any RISC-V target, in any
released version. Its generic RISC-V backends dispatch tasks from a *software*
interrupt controller and never read a claim register.

The PLIC still earns its place — it is what QEMU has, so the gate covers the
interrupt path that ships — but not for the reason decision 7 gives.

---

## 1. Which RTIC, and which backend

**`rtic = "2.3"`, feature `riscv-clint-backend`.** 2.3.0 is current; 2.1.2 was
the first release with a generic RISC-V backend at all and 2.1.3 added the
second. There is no reason to pin older: this is our SoC, and
`docs/upstream-boundary.md` is the policy.

The four RISC-V backends 2.3.0 offers:

| backend | dispatch | fits us |
|---|---|---|
| `riscv-esp32c3-backend` | the ESP32-C3's own interrupt matrix | no — different chip |
| `riscv-esp32c6-backend` | the ESP32-C6's PLIC_MX | no — different chip |
| `riscv-mecall-backend` | `ecall`, taken as a machine exception | works, but every pend is a trap, and unpending is `mepc += 4` |
| **`riscv-clint-backend`** | CLINT `msip`, the machine software interrupt | **chosen** |

The CLINT backend wins because the hardware is already there and idle.
`ecp5-test/riscv/vexii_clint.py` implements `msip` at CLINT offset 0 and
`vexii_hello_soc.py:1089` wires it to `cpu.irq_software`, with a comment saying
"nothing raises yet". QEMU's `-M virt` CLINT has the same register. So pending a
task is one MMIO store on both targets, and the `mecall` backend's exception
round trip buys nothing.

## 2. What RTIC's RISC-V model actually is

Both generic backends are `riscv-slic` backends. `riscv-slic` is a **software**
interrupt controller: a priority-ordered queue in a `static`, drained from one
hardware source.

Read `rtic-macros`'s own binding, `src/codegen/bindings/riscv_slic.rs`. Its
`extra_modules` takes every hardware task's `binds =` name, chains it with the
software dispatchers, and passes the whole list to `riscv_slic::codegen!` as
`swi = [...]`. Its `pre_init_enable_interrupts` then calls
`set_priority(slic::SoftwareInterrupt::#name, p)` for hardware tasks and
software tasks alike. There is no other path.

So on this machine:

    UART raises its 16550 line
      -> PLIC muxes it, raises MachineExternal
        -> our handler claims, translates, `rtic::export::pend(...)`, completes
          -> SLIC raises `msip`, taking MachineSoft
            -> SLIC pops the highest-priority pending source
              -> the RTIC task runs

Two controllers in series, not one. **`binds = X` does not name a PLIC source.**
It names a SLIC source, and something has to connect the two — that something is
`machine_external` in the skeleton, which is the claim loop from `src/irq.rs`
with `pend` where the work used to be.

Three consequences worth stating plainly.

**`Plic::set_threshold` is not what RTIC locks with.** `src/plic.rs:131` says
"RTIC will use this for critical sections". It will not. `rtic::Mutex::lock`
calls `riscv_slic::lock`, which raises the SLIC's own threshold — a byte in
`.bss`. The PLIC threshold stays at 0 forever. The method is still worth having
(it is the only way to see, from the shell, that a source is masked by level
rather than by enable) but its stated purpose is gone.

**Every `pend` and every `lock` takes a global critical section.**
`riscv-slic`'s API is `critical_section::with` throughout, and the only
implementation available here is `riscv`'s `critical-section-single-hart`, which
clears `mstatus.MIE`. So RTIC's cheap-looking `pend` from inside the PLIC
handler disables *all* interrupts for the duration, including the timer. That is
the latency term to measure before believing any scheduling claim.

**The PLIC front end cannot itself be an RTIC task**, so it stays hand-written
and stays subject to every rule in `src/irq.rs`: no printing, no long spins,
never read a byte with nowhere to put it. RTIC does not replace that file. It
replaces what that file *dispatches to*.

## 3. What it took to make it build

Five things, and none of them is documented anywhere upstream.

| obstacle | fix | where |
|---|---|---|
| `#[app]` wants `device::Peripherals` | `peripherals = false` | `src/bin/rtic.rs` |
| the CLINT backend wants a `riscv-peripheral`-shaped PAC — `CLINT::mswi().msip(Hart::H0)` | a 30-line `device` module | `src/bin/rtic.rs` |
| riscv-slic emits `#pac::interrupt::CoreInterrupt`; riscv 0.16 calls that enum `Interrupt` | `pub use ... as CoreInterrupt` | `src/bin/rtic.rs` |
| riscv-slic emits `use super::#pac` from a module nested inside the app module, so the device path must resolve from *there*, not from the crate root | `use super::device;` inside `mod app` | `src/bin/rtic.rs` |
| **RTIC's stack check references `_ebss`; riscv-rt 0.18 exports `__ebss`** | `PROVIDE(_ebss = __ebss);` | `memory.x`, `memory-qemu.x` |

The last one is an upstream mismatch and the only one that is a bug rather than
a shim: `rtic-macros` 2.3.0's `check_stack_overflow_before_init` was written
against a riscv-rt that has not existed since 0.12. It fails at **link**, after a
clean compile, with `undefined symbol: _ebss` and the linker's own
`did you mean: __ebss` hint. Worth reporting upstream; the alias is one line and
costs the shipping image nothing, because `PROVIDE` only defines a symbol
something references.

There is also a documentation wart: the backend argument is `backend = H0`, not
the `backend = [hart_id = H0]` that `riscv-slic`'s own macro syntax suggests.
`rtic-macros/src/syntax/parse/app.rs` parses a bare `Ident`.

## 4. What it costs

Measured by `scripts/rtic_probe.py`, `opt-level = "z"`, LTO, riscv32imac:

| build | `.text` | `.rodata` | `.bss` |
|---|---|---|---|
| shell, board | 39,516 | 16,516 | 9,656 |
| shell, QEMU | 34,096 | 11,792 | 74,196 |
| rtic skeleton, board | 2,312 | 136 | 24 |
| rtic skeleton, QEMU | 2,248 | 124 | 24 |

The skeleton is two tasks, one shared `u32` and a PLIC front end, so almost all
of its 2,312 bytes is runtime. A plain `riscv-rt` program with one handler and
one loop, built the same way, is 556 bytes — so **RTIC and the SLIC are about
1,750 bytes of `.text` and 24 bytes of `.bss`** before any of this firmware's
work moves into them.

Also 32 packages in `Cargo.lock` where there were 14, including two copies of
the `riscv` crate: `riscv-slic` 0.2.0 pins 0.12.1 while this crate and RTIC use
0.16. Both are inline-asm wrappers with no symbols, so they coexist — but it is
the dependency graph doubling for one feature, in a crate whose Cargo.toml
currently opens by explaining that it has no HAL.

### Which budget this actually spends

Not flash, and not block RAM. Both are wrong answers and worth killing here.

* **Flash**: `.text` and `.rodata` live at 0x100B0000 with 3,392 KiB. The image
  is 56 KB. RTIC would take 0.05% of what is left.
* **Block RAM**: 63 KiB holds `.data`, `.bss` and the stack. `.bss` is 9,656
  bytes and `memory.x` asserts 8 KiB of stack headroom, so ~46 KiB is free. 24
  bytes is nothing. (The 492-byte bootloader in the 1 KiB at 0x0 is the tight
  budget on this SoC, and RTIC does not go there.)
* **The I-cache is the budget.** 4 KiB, direct-mapped, one way, against 39.5 KB
  of `.text`, and every miss is a full quad-SPI transaction. The `opt-level`
  table in `firmware/cynthion-soc/Cargo.toml` is the measurement that matters:
  going from `z` to `3` grew `.text` by 79% and cost **5.4x the IPC**. Code size
  on this machine is a *speed* question.

  1,750 bytes is 43% of the cache. It is not fatal — the shell already misses
  constantly at 39 KB — but the RTIC runtime is on the *hot* path by
  construction: every dispatch runs through `__riscv_slic_pop` and
  `critical_section::with`. That is the one place where a permanent resident of
  the cache is bought, and nothing here has measured what it displaces.

## 5. The counter-argument, met rather than ignored

`docs/hardware.md:820` measures an untouched shell at **0.15% busy** under QEMU.
99.85% of cycles are `irq::pop` returning `None`.

Taken at face value that is an argument that a scheduler buys nothing: there is
no contention to arbitrate, so priorities and preemption have no work to do.
It is a fair argument and it should not be waved away. Three things are true
alongside it.

**A scheduler is not what RTIC is for here.** What RTIC provides that this
firmware lacks is *checked* resource access. `RINGS` in `src/irq.rs` is a
hand-rolled SPSC ring whose correctness argument is four paragraphs of module
comment about who may touch which index; `PENDING_TYPE_C` is an atomic bitmap
with a written explanation of why a swap and not two flags. Both are right. Both
are right because someone reasoned carefully, and nothing re-checks the
reasoning when the next source is added. RTIC's ceiling analysis is that
re-check, at compile time.

**The 0.15% is a fact about an idle console, not about the work queued behind
it.** The measurement is of a shell at a prompt. `power::Monitor::poll` spins on
I2C for a couple of milliseconds twenty times a second, `typec::service` masks
its own interrupt because clearing a FUSB302B is a millisecond of I2C, and both
sit in the main loop because a handler may not be slow. Under RTIC they are
low-priority tasks that *may* be slow, because a console interrupt preempts
them. That is the structural win, and it is invisible in a busy percentage taken
at an idle prompt.

**Against that: 1,750 bytes on the hot path of a 4 KiB cache, a doubled
dependency graph, and a global interrupt disable on every pend.** RTIC buys a
compile-time correctness argument for the price of cache and dependencies, and
nobody has yet measured the cache half.

This document does not close decision 19. It removes the wrong reasons on both
sides.

## 6. What `#[app]` this SoC needs

The skeleton is the shape. Fleshed out:

```rust
#[rtic::app(device = device, peripherals = false, backend = H0)]
mod app {
    #[shared]
    struct Shared {
        bus: Bus,              // src/bus.rs -- one I2C controller, three devices
        consoles: Consoles,    // the rings, or what replaces them
    }

    #[local]
    struct Local {
        power: power::Monitor,
        type_c: typec::Controllers,
    }

    #[init]  fn init(cx: init::Context) -> (Shared, Local);
    #[idle]  fn idle(cx: idle::Context) -> !;      // the shell lives here

    #[task(binds = ConsoleRx, priority = 3, ...)]  fn console_rx(..);
    #[task(binds = TypeC,     priority = 2, ...)]  fn type_c(..);
    #[task(priority = 1, shared = [bus])]          async fn power_poll(..);
}
```

`#[shared]` is the interesting column. `bus` is the one that pays: `src/bus.rs`
exists to make "one owner per device" structural, by holding the single `Bus`
and lending it by `&mut`. That is exactly an RTIC shared resource with a
ceiling, and moving it there turns a convention the reviewer enforces into one
the compiler does.

`#[idle]` holding the shell is deliberate. The shell is a line editor and a
command table; it is not periodic and it has no deadline. RTIC's idle is
lowest-priority code that everything preempts, which is what it should be.

### Task or plain function

| module | today | under RTIC | why |
|---|---|---|---|
| `irq.rs` claim loop | `MachineExternal` handler | **stays**, shrinks to claim/pend/complete | RTIC cannot bind a PLIC source |
| `irq.rs` `service`/`RINGS` | handler + SPSC ring | **hardware task** `ConsoleRx` | the ring becomes a shared resource; the mask-when-full dance stays, it is a 16550 property |
| `typec.rs` `service` | mask-and-defer to the main loop | **task**, priority below the console | preemptible, so the I2C millisecond stops being a reason to defer |
| `power.rs` `poll` | 50 ms poll in the main loop | **software task**, needs a monotonic | the only thing here that wants a timer queue |
| `timer.rs` `tick` | `MachineTimer` handler | **stays**, or becomes the monotonic | RTIC does not own `MachineTimer` |
| `events.rs` drain | main loop | **idle** | it needs a `Uart`, and only non-handler code has one |
| `uart.rs`, `plic.rs`, `bus/i2c.rs`, `fusb302.rs`, `hyperram.rs` | plain drivers | **unchanged** | RTIC schedules; it does not drive |
| `main.rs` shell, every command | main loop | **idle** | no deadline, lowest priority |
| `bench.rs`, `selftest.rs`, `info.rs`, `memory.rs` | commands | **unchanged** | called from idle |

Note what does *not* move: every driver. That is the point — the tasks are the
five things that today have to argue about who runs when.

## 7. Migration order

Each step is separately revertible and each ends with `./dev.py gate` green.

1. **Done.** The dependency, the feature, the skeleton, `scripts/rtic_probe.py`,
   this document, the `_ebss` alias. Nothing the shell links changes.
2. **A CLINT monotonic.** `rtic-monotonics` 2.2.1 has SysTick, STM32 and Silabs
   timers and **nothing for RISC-V** — checked, not assumed. So this is an
   implementation of `rtic_time::Monotonic` over `src/timer.rs`'s `mtime` and
   `mtimecmp`, which is the bulk of the remaining new code. Unit-testable on the
   host: the three-store `set_mtimecmp` sequence and the wrap arithmetic are
   pure functions of two `u32`s.
3. **Give the crate a `[lib]`.** The skeleton includes `plic.rs` and `target.rs`
   by `#[path]` because a `src/bin/` target cannot say `use crate::`. That is
   fine for a spike and wrong for the product. This step is mechanical, touches
   no logic, and is worth doing on its own so the diff that follows is only
   about RTIC.
4. **Move the console.** `ConsoleRx` becomes a real task; `RINGS` becomes a
   shared resource. This is where the QEMU gate earns its keep: `soc_test.py`
   drives the shell over a pipe and asserts what it says, so a broken receive
   path is a red gate rather than a dead board.
5. **Move Type-C**, and delete the mask-and-defer with it.
6. **Move the power poll** onto the monotonic from step 2.
7. **Swap the entry point**: the shell moves into `#[idle]` and
   `src/main.rs`'s `#[entry]` goes away, at which point the `rtic` feature stops
   being a feature and the second `[[bin]]` is deleted.
8. **Measure.** `metrics.rs` and the `time` command already report busy/idle,
   worst handler cost and worst lateness. Re-run them and put the before/after
   in decision 19, including the I-cache question from section 4.

Steps 4 through 7 are the ones that can break the board. Nothing before step 4
changes a byte of the shipping image.

## 8. What could go wrong

* **The I-cache, and it is not measured.** Section 4. This is the risk that
  would show up as "everything is slower now" with nothing pointing at RTIC.
  Step 8 exists for it; running it earlier would be better.
* **`critical_section` disabling the timer.** Every `pend` and every `lock`
  clears `mstatus.MIE`. `timer.rs` already reports worst lateness, so this one
  has a detector before it has a bug — watch `late` in `time`.
* **`nested()` and re-entrancy.** `riscv-slic` re-enables interrupts inside the
  software handler so higher-priority sources can preempt. The PLIC front end
  can therefore run *inside* a task, pending the task that is already running.
  The SLIC's threshold is what makes that safe, and the whole argument rests on
  the threshold being restored on every path out of `__riscv_slic_pop`.
* **A handler that prints.** `scripts/soc_irq_log_check.py` scans `src/**.rs`
  recursively, so it already covers `src/bin/rtic.rs` — verified. It must keep
  covering the tasks after they move, and the rule for a *task* is weaker than
  for a handler (a task is preemptible) but not absent: a task that spins on
  `LSR.THRE` blocks every lower-priority task for as long as it takes.
* **Two `riscv` crates.** Harmless today because neither exports symbols. It
  stops being harmless the moment either grows a `#[no_mangle]`, or the
  `critical-section` implementation registration moves.
* **The `_ebss` alias rotting.** It is `PROVIDE`d, so if RTIC fixes the spelling
  the alias silently does nothing and the build keeps working. That is the good
  failure mode; the bad one is riscv-rt renaming `__ebss`, which breaks the
  shell first and loudly.
* **Network on the first build.** `--features rtic` fetches sixteen crates.
  `./dev.py gate` does not, and `scripts/rtic_probe.py` is deliberately not in
  the gate for that reason.

## 9. What this run did not do

* Nothing runs on the board. The skeleton has never been programmed.
* No monotonic. Step 2 above.
* No task actually does this firmware's work — `console_rx` and `type_c`
  increment a counter.
* Decision 19 is still open. It is better informed, and its "blocked by:
  nothing known" is now a build result rather than a hope, but the trade in
  section 5 is a judgement nobody has made yet.
