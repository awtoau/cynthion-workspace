# History: SoC, USB and Apollo docs

Removed from live docs (terse-ify pass), verbatim, with source file. Not current; do not rely on any number here.

---

## Deleted dead docs

### Source: docs/apollo/pending-patches/README.md

# Patches that were never applied

The three survivors of `patches/`, which held 23 files and has been deleted.

That directory was a `git format-patch` export of commits from 2026-05-19/21 that
was never cleaned up afterwards. **19 of its 23 files were already landed as
commits** — the same change existing twice, once in git history and once as stale
text. One was superseded. Provenance was established with
`git patch-id --stable` against every commit in each target repo, which is
title-blind and resolved 18 outright; the rest by reverse-apply and `git log -S`.

The 19 landed files were deleted rather than moved to `debris/`: an export of
commits that exist in history is regenerable by definition, and `debris/` is for
content a user could not recreate.

Three files had no corresponding commit. They are kept here.

## 1. `apollo-uart-rx-ring-buffer.diff` — a real bug fix, worth applying

The substantive one.

**The bug:** `uart_byte_received_cb()` calls `tud_cdc_write_char()` and flushes
**directly from the UART RX ISR**. That can fire while TinyUSB is inside a
critical section, which is a reentrancy hazard rather than a style problem.

**The fix:** a 256-byte RX ring buffer, so the ISR only enqueues and the console
task drains it.

**Not the DMA driver from [#66](https://github.com/awtoau/cynthion-workspace/issues/66).** Verified: zero DMA/DMAC references. That issue
tracks a *ping-pong DMA* driver on SERCOM2 that was reviewed and deliberately
**deselected**. This is a plain ISR-safe ring buffer, a much smaller change, and
its deselection does not apply.

**It bundles a second, unrelated change**: removal of the forced
`hand_off_usb()` / `allow_fpga_takeover_usb(true)` at boot. That is a **policy
change**, not a bug fix, and needs a decision rather than review. Split before
applying.

**Apply state:** plain `git apply` fails on context only — upstream added an
`apollo_mode.h` include. `git apply -3` applies both files cleanly. That is an
apply check and nothing more: **no firmware was built and none was flashed**, so
whether the ring buffer fixes the reentrancy in practice is untested.

## 2. `0020-scripts-apollod.py-graceful-shutdown-...patch` — stranded

57 lines adding graceful shutdown via a `cmd shutdown` path to `apollod.py`.

Real work, but the target moved: `grep shutdown apollod.py` returns nothing. The
`cyn` daemon this once pointed at as the replacement is itself retired — it
imported a module that had moved to `debris/`, so it raised `ModuleNotFoundError`
on every run, and both it and the `./cyn` wrapper are now deleted. Only worth
applying if `apollod.py` has a future. Otherwise delete.

## 3. `moondancer-uart0-log-port.diff` — recommend deleting

A one-line debug flip, `Port::Both` → `Port::Uart0`. It **removes** a log
destination, so it is a local debugging convenience rather than a fix.

## Three things that looked valuable and had all landed

Recorded because they were flagged as possible unlanded work and were not, so the
next person does not re-investigate:

| looked like | actually |
|---|---|
| isochronous IN skeleton across gateware and firmware | landed as `4ab5361`; the 164 added `ep_iso_in.py` lines are byte-identical, and the file has since been fixed twice on top (`53d3ea4`, `56a64c2`) |
| endpoint `max_packet_size` clamp to `EP_MAX` | landed as `6fa3e0a`, exact patch-id match |
| "iso-13 progress" text found nowhere in the repo | in `repos/cynthion/awto.md` — a superproject grep misses it because it is inside a submodule |

## What "carrying patches" means here

Worth separating, because the two get conflated:

- **`repos/apollo` is 34 commits ahead of upstream.** Those are live: they are in
  the code that gets built and flashed. Submitting the upstreamable ones is
  issue [#102](https://github.com/awtoau/cynthion-workspace/issues/102).
- **These three files are not carried anywhere.** They are unapplied text. Nothing
  builds them.

### Source: docs/patchset/patchset_overview.md

## Apollo Modification History

The canonical patch artifact for the current set was
`patches/apollo/0000-wip-issue22-apollo-fixes-20260722.diff`. That directory no
longer exists: the patch was applied to the `repos/apollo` submodule, so the
change itself is the record and a loose diff beside it would only be a second,
divergent copy. Recover the text with `git log` in the submodule if the
before-and-after is needed.

For the evidence/issue workflow that goes with these changes, see [apollo_change_process.md](../apollo_samd11_mcu/apollo_change_process.md).

### Related Build-System Changes

The build-system work that introduced logging, fail-fast checks, and parallel execution is summarized separately here:

- [0001-improved-build-system-logging-fail-fast-parallelization.md](0001-improved-build-system-logging-fail-fast-parallelization.md)
- [0002-parallel-build-execution-setup-and-build-threading.md](0002-parallel-build-execution-setup-and-build-threading.md)
- [install.md](../install.md)

### 2026-07-22 - Apollo console/UART race hardening

Patch set: `0000-wip-issue22-apollo-fixes-20260722.diff`, applied to the
`repos/apollo` submodule (see above).

Files touched:
- `firmware/src/console.c`
- `firmware/src/main.c`

What changed:
- Buffered UART RX in interrupt context instead of writing directly into TinyUSB.
- Flushed buffered console data from task context.
- Added a fallback path to initialize UART if callbacks were missed.

Why it matters:
- Reduces contention between UART callbacks and TinyUSB critical sections.
- Makes the Apollo console path less sensitive to timing around REPL and `riscv` command handling.

Verification status:
- Track runtime evidence and build output alongside the patch set, not here.
- Use the issue comment / `tmp/` log pattern from [apollo_change_process.md](../apollo_samd11_mcu/apollo_change_process.md).

---

### Source: docs/patchset/0001-improved-build-system-logging-fail-fast-parallelization.md

## Improved Build System

### What's New (2026-05-23)

**Comprehensive logging system** with colored output and file logging
- Console + file logging to `./tmp/logs/<timestamp>.log`
- DEBUG level (--verbose) and INFO level output
- Thread-safe for parallel execution

**Fail-fast prerequisite checks** before attempting builds
- OS detection (Fedora/Ubuntu with dnf/apt)
- Critical tool verification (git, gcc, make, python3.14)
- FPGA toolchain check (arm-none-eabi-gcc, rustc)
- OSS CAD Suite functional verification
- Clear installation guidance for missing packages

**Python 3.14 no-GIL parallelization**
- Parallel build execution using `concurrent.futures.ThreadPoolExecutor`
- 55% speedup (33 min → 18 min with 4 threads)
- New CLI options: `--parallel`, `--jobs N`

### Apollo Firmware Fixed

**Issue:** TinyUSB submodule initialization failed  
**Fix:** Applied: `git submodule deinit -f lib/tinyusb && git submodule update --init`  
**Result:** Apollo firmware now builds successfully ✓

---

### Source: docs/patchset/0002-parallel-build-execution-setup-and-build-threading.md

## Parallel Build Execution

Related change set:
- [0001-improved-build-system-logging-fail-fast-parallelization.md](0001-improved-build-system-logging-fail-fast-parallelization.md)

### Usage

```bash
# Sequential (original, ~33 minutes)
./scripts/install.py setup

# Parallel with auto-detect (4 threads, ~18 minutes)
./scripts/install.py --parallel setup

# Parallel with custom threads
./scripts/install.py --parallel --jobs 2 setup   # 2 threads
./scripts/install.py --parallel --jobs 8 setup   # 8 threads

# Dry run to preview
./scripts/install.py --parallel --dry-run setup
```

### Performance

| Mode | Time | Speedup |
|------|------|---------|
| Sequential | ~33 min | 1.0x |
| Parallel (2 threads) | ~25 min | 1.3x |
| Parallel (4 threads) | ~18 min | 1.8x |
| Parallel (8 threads) | ~18 min | 1.8x |

### How It Works

1. **Setup Phase (Sequential)**: 12 min
   - Fail-fast prerequisite checks
   - Repository cloning/pulling
   - Submodule initialization
   - Toolchain verification
   - Python environment setup

2. **Build Phase (Parallel)**: 8 min
   - Thread 1: Apollo Firmware (10 min)
   - Thread 2: moondancer (5 min)
   - Thread 3: Analyzer Gateware (8 min)
   - Thread 4: Facedancer Gateware (8 min)
   - All run simultaneously

**Total: 20 minutes (setup + parallel builds)**

### CLI Options

```bash
--parallel              # Enable parallel builds (ThreadPoolExecutor)
--jobs N                # Max threads (default: 4)
--verbose               # Show full output
--dry-run               # Preview without executing
```

---

### Source: docs/drafts/200-repro.md

## Reproduction must use their tooling, not ours

Every number here was taken with our build system, our platform and our harness.
That proves nothing upstream can check. Before any PR, it has to rebuild and
re-measure under `BUILD.md`.

**This cannot be done on this machine.** `BUILD.md:97` instructs

    pip uninstall -y -r <(pip freeze)

and we run the free-threaded system environment with no venv. Their install
would take it apart.

**They have no container.** `ci/` is `build.sh` (123 B) and `test.sh` (110 B),
one workflow, no Dockerfile, no nix, no devcontainer.

### Proposal

A container built from `BUILD.md` verbatim, with the board passed through:

- their Python, their Yosys/OSS CAD Suite, their Rust, `pip install ".[gateware]"`
- our harness copied in, nothing of our build system
- `--device` for the Cynthion, so measurement happens on real hardware
- pinned base image and recorded tool versions, so a rung is reproducible

Isolation both ways: their install cannot touch this machine, and our
environment cannot flatter their result.

Worth offering upstream as its own small PR — they have nothing like it, and it
is the difference between a number they must trust and one they can run.

### Rules for the submission

- **Terse.** Message and docs both.
- **Cite numbers, measured on real hardware.** No estimates, no simulation
  figures presented as measurements.
- **Ship the harness.** A claim without the code to re-take it is an assertion.
- **Double-check every figure** against `results.json` before it is quoted.

### Verify before submitting

- [ ] Container builds from `BUILD.md` with no step from our tree
- [ ] Their gateware builds unmodified inside it
- [ ] The ladder re-measured inside it, on hardware, and the numbers match
- [ ] Any that do not match are investigated before anything is sent


---

## apollo configure speed: full investigation record (issues #100, #108, #199)

### Source: docs/apollo_samd11_mcu/apollo-configure-speed-investigation.md

# `apollo configure`: speed investigation and results

How fast the ECP5 can be programmed through Apollo, what was tried, and what the
measurements actually showed.

**This is about the transport, not the destination.** The benchmark shifts into SRAM
because that is cheap to repeat and leaves the board alone, but what is being measured
and improved is the path from the host to the ECP5's TAP: USB control transfers into
the SAMD11, then SERCOM SPI out to JTAG. Everything reached over that path benefits.

That includes **programming the configuration flash**: `apollo flash-program` and
`flash-erase` open the same `device.jtag` session and go through
`create_jtag_programmer()`, so the SPI flash is reached *through* the JTAG tunnel, not
by a separate mechanism. The DMA, chunking and buffer work here applies to them
unchanged. `apollo flash --fast` is the exception -- it loads a bridge onto the FPGA
and programs the flash through the FPGA's own USB, bypassing this path entirely.

What this document does **not** cover: the W25Q32's own read modes and QSPI timing,
which is `../chips/w25q32-config-flash.md` -- a different chip, reached by the FPGA
rather than by the host.

This file was previously `luna_ecp5_fpga/jtag-ceiling-reached.md`. The old title
claimed the path was done; it then got 2.22x faster, which is the sort of thing a
title should not assert.

## Final result

**713.9 -> 322.2 ms at 1024-byte chunks: 2.22x against the stock release**, on the
122880-byte fixed payload, measured on a direct USB port. Shipped as `6520707`
(`awtoau/awto-apollo`), on a 14 KB / 4 KB part, with the ceilings never raised.
Verified on hardware: IDCODE `0x21111043`, configure `rc=0`, `check.py` 6 passed.

The flash and RAM percentages once quoted here were the pre-[#199](https://github.com/awtoau/cynthion-workspace/issues/199) figures, which
omitted `.relocate` and read as under both ceilings when both were over. Current
numbers: `docs/chips/samd11-apollo.md` § Memory budget.

Where the 391.7 ms came from, largest first:

| change | worth | how |
|---|---|---|
| **DMA-clocked JTAG** (`d43f765`) | **-85 ms** | the CPU stops spinning on SERCOM flags, so `tud_task()` runs while bytes are on the wire |
| USB topology | -46 ms | moved off a 4-hub chain. No code |
| 512- then 1024-byte chunks | -109 ms | fewer transactions, each amortising a fixed ~145 us cost |
| pipelined `spi_send` + LTO | -147 ms | keep the shifter fed; reclaim 2968 bytes to afford the rest |
| housekeeping on a 20 ms tick (`7060149`) | -6 ms | stop polling human-scale inputs at 200 kHz |

Those sum to 393 ms against an actual 392 -- the difference is the double-buffering
step and rounding, since each figure is a difference of two independently measured
rows rather than an isolated A/B.

**The remaining gap is 240.3 ms, and it is USB, not JTAG.** Wire time for this payload
is 81.9 ms, so the transport still costs 3.9x what the bits do. Per-transaction cost is
~145 us against ~47 us of wire for a 64-byte packet.

**The next lever is not the one this work assumed.** Dispatching SETUP from the USB
interrupt was built and measured (`c8b9c1f`, unshipped -- **still open, see [#100](https://github.com/awtoau/cynthion-workspace/issues/100)**; it
works and is faster, but stalls on a chunk-size change and is 246 bytes over budget) and
is worth **7.8 ms -- 3.2% of the remaining gap**. That bounds the whole software-latency theory: even a perfect
version of it leaves ~230 ms unexplained. What is left is the 64-byte control endpoint
itself, and the only change with the right order of magnitude is a **dedicated bulk
endpoint** -- 19 packets per frame against control's measured 5.8, so ~3.3x on the
transport term. See "But a DEDICATED bulk endpoint is a different proposition".

---

The rest of this document is the working record: what was measured, what was tried,
and what turned out to be wrong. It starts where the work started.

Two proposals — transmit-only DMA, and raising SCK to 24 MHz — were investigated
and **neither was implemented**, because measurement showed both would be
no-ops or unsafe. This records why, since both looked well-founded.

> **Superseded in part.** DMA *was* implemented later and is worth **-85 ms, 1.26x** --
> the largest code win in this document (`d43f765`, see below). The section immediately
> following is still correct about what it measured, and was still the wrong conclusion:
> it asked whether DMA could remove **per-byte CPU cost** and rightly answered no. The
> value of DMA is not the per-byte term at all. It is that a CPU not spinning on SERCOM
> flags can run `tud_task()`, which is where the ~98 us of NAK per USB transaction was
> going. A correct measurement can still answer the wrong question.

## There is no CPU cost left for DMA to remove (the right answer to the wrong question)

The "0.455 µs/byte CPU term" that justified TX-only DMA is an artifact of the
same mistake that produced the earlier 3.93 µs/byte figure: dividing a whole
`SCAN` call by its payload, which charges the fixed ~145 µs USB round trip to
per-byte clocking.

Measuring the **marginal** cost instead — differencing 64-bit and 2048-bit scans
over the same round trip, so the fixed cost cancels — gives **0.663 / 0.684 /
0.715 µs/byte** across three runs against a **0.667 µs/byte** wire floor.

That is **2-3% overhead, not 41%**. The pipelined `spi_send()` already took the
win by keeping the shifter continuously fed.

This independently reproduces why the earlier DMA attempt was archived, and it
also revises the synthetic-benchmark conclusion that the path was CPU-bound with
1.11 µs/byte of overhead. Same artifact, measured two different ways.

## SCK is already at the ceiling, and cannot be swept

`f_SCK = 48 MHz / (2 x (BAUD+1))`, so the divider steps are:

| BAUD | SCK | period |
|---|---|---|
| 0 | 24.0 MHz | 41.7 ns |
| **1** | **12.0 MHz** | **83.3 ns** (current) |
| 2 | 8.0 MHz | 125 ns |

**There is no setting between 12 and 24 MHz.** The requested sweep cannot be
run; it is one binary step.

And the ceiling is the SAMD11, not the ECP5. Datasheet Table 35-50 gives `tSCK`
**min 84 ns — an 11.9 MHz rated maximum** in master mode. So BAUD=1 is already
marginally past rated and BAUD=0 would be 2x past.

Separately, `tMIS` (MISO setup) is 21 ns typical against a 20.8 ns half-period
at 24 MHz, so **duplex readback cannot validate 24 MHz even in principle** —
which removes the instrument that would have proved the wire still worked.

## Where the time actually is

Profiling a verified configure (`DONE=1`, `BSE_ERR=0`):

| | |
|---|---|
| end-to-end | 953-1017 ms |
| bulk shift | 679 ms |
| **of which wire time** | **67 ms** |
| **USB per-chunk overhead** | **612 ms (90% of the shift)** |

394 chunks, two control transfers each. **JTAG clocking is about 7% of
end-to-end.** Zeroing it entirely would take ~1000 ms to ~940 ms.

The levers that would matter are **chunk size** — capped by
`jtag_out_buffer[256]` and RAM-bound at 2728 of 4096 bytes — and **collapsing
`SET_OUT_BUFFER` + `SCAN` into one transfer**.

## The TX-only argument was correct, and still not worth doing

The reasoning holds and was verified: the ECP5 self-validates via
`LSC_RESET_CRC`, BSE code 3 is `CRC check failed`, and the host already
suppresses `GET_IN_BUFFER` for the burst. **Dropping TDO loses no error
detection.**

It would buy roughly **2 ms of ~950 ms**, which does not justify the
silent-failure surface it creates.

The stricter verification it implied was adopted anyway: every configure now
decodes `DONE`, `FAIL` and the BSE code explicitly rather than relying on an
exception not being raised.

## The standard test, and why there was not one

Its absence was actively misleading, so this is now the only figure worth quoting.

These docs contained roughly **forty distinct millisecond figures** for
configuration timing with no way to tell which measured the same work. All
accurately recorded, none comparable: different bitstreams, different firmware,
different scopes -- whole configure versus shift path versus a single `SCAN` call.

Two errors that followed from that, both mine:

- I compared 890 ms against a recorded 1680 ms and reported **1.89x**. The two came
  from different bitstreams. Same payload the figures were 846 and 774 ms, a 1.09x
  change reported as nearly double.
- The `2575 -> 1680 ms` above and the `953-1017 ms` in the section below are both
  labelled configure time. Both are correct. They measure different payloads on
  different firmware.

**`scripts/jtag_fixed_benchmark.py`, committed payload of 122880 bytes.** Fixed
rather than a real bitstream because `top.bit` changes size whenever the gateware
does, so a "faster" result can be a smaller bitstream. The size sits mid-range of
the real bitstreams here (103364 to 136184 bytes) and divides evenly by
128/256/512/1024, so no chunk size gets a short final transfer the others do not.

Scope is the **shift path only**: no `ISC_ENABLE`, no CRC check, no DONE poll, TAP
left in SHIFT-DR. The FPGA ignores the data, so its configuration survives and the
benchmark is safe to repeat against a board that is doing something else. The
absolute number is correspondingly lower than a real configure.

| chunk | chunks | best | throughput | spread |
|---|---|---|---|---|
| 128 B | 960 | 667.6 ms | 184.1 KB/s | 1.8 ms |
| 256 B | 480 | 558.8 ms | 219.9 KB/s | 3.3 ms |
| **512 B** | **240** | **488.9 ms** | **251.3 KB/s** | 3.1 ms |

Spread of 1.8-3.3 ms against 4-15 ms for real-bitstream timing. That matters: the
remaining levers are expected to save less than the chunk change did, so the
instrument has to resolve a few milliseconds.

## The 1.53x claim, restated on the standard test: it is 1.13x

`scripts/jtag_speed_bisect.py` reflashes historical firmware and benchmarks each
commit, so claims made before the standard test existed get real numbers rather
than being taken on trust.

Only four commits between the stock `v1.1.1` tag and HEAD touch the JTAG or SPI
path, so this is five direct measurements rather than a search. All at 256 B/chunk,
because 512 needs `GET_INFO`, which only HEAD implements -- on older firmware the
host silently falls back to 256, so a "512" row would read as no change.

One table, both chunk sizes, on the 122880-byte payload throughout.

| commit / limit | what | 256 B | 512 B | 1024 B |
|---|---|---|---|---|
| **`v1.1.1`** | **stock release** | **713.9 ms** (11.5%) | n/a | n/a |
| `4bf7691` | enable LTO | 639.1 ms (12.8%) | n/a | n/a |
| **`e034daa`** | **pipelined `spi_send` + suppress TDO readback** | **566.9 ms** (14.4%) | n/a | n/a |
| `0e9bfb1` | `JTAG_BUFFER_SIZE` define, no functional change | 562.1 ms (14.6%) | n/a | n/a |
| `bb82d39` | 512-byte buffers + `GET_INFO` | 555.4 ms (14.7%) | 488.9 ms (16.8%) | n/a |
| **`19242e8`** | **two reported limits + 1024-byte writes** | 564.0 ms (14.5%) | 489.5 ms (16.7%) | **457.6 ms (17.9%)** |
| `cd4a85c` | double-buffered staging | -- | 469.1 ms (17.5%) | 455.6 ms (18.0%) |
| **direct USB port** | **no code change -- moved off a 4-hub chain** | -- | **425.2 ms (19.3%)** | **409.1 ms (20.0%)** |
| **`d43f765`** | **clock by DMA, stop blocking `tud_task()`** -- *over flash budget, see below* | -- | **343.3 ms (23.9%)** | **324.4 ms (25.2%)** |
| `e4bc0f0` | LED patterns out, paying for DMA -- speed unchanged, budget restored | -- | 348.8 ms (23.5%) | 327.9 ms (25.0%) |
| **`7060149`** | **LED + button on a 5 ms tick, not every pass** | -- | **330.2 ms (24.8%)** | **318.1 ms (25.7%)** |
| `6520707` | tick relaxed to 20 ms -- correct rate, not a speed change | -- | 341.2 ms (24.0%) | 322.2 ms (25.4%) |
| | | | | |
| `c8b9c1f` | *dispatch SETUP from the USB ISR* -- **NOT SHIPPED**, stalls and is 246 B over budget | -- | 328.3 ms (25.0%) | 314.4 ms (26.0%) |
| | | | | |
| **no USB payload** | `0xb9`, pattern generated in firmware | 275 ms (30%) | 137 ms (60%) | not measured |
| **theoretical wire** | 12 MHz SCK, 1 bit per clock | 81.9 ms (100%) | 81.9 ms (100%) | 81.9 ms (100%) |

**Reading the table.** Every time is for the **same 122880-byte payload** -- the columns
are the JTAG *chunk size* the transport used to move it, not different amounts of work.
So a row compares one firmware against itself at different chunk sizes, and a column
compares firmwares at the same chunk size.

The percentage in each cell is **that cell against the theoretical wire time for the
same payload**, 81.9 ms. So `457.6 ms (17.9%)` means: at 1024-byte chunks this firmware
takes 457.6 ms to move 122880 bytes, which is 17.9% of the 81.9 ms the wire alone would
need. Higher is better, 100% is unreachable.

**Cumulative against stock**, each on the best chunk size it supports:
**713.9 -> 457.6 ms, 1.56x.**

**The no-USB row at 1024 B is not measured, and the attempt broke the board twice.**
`0xb9` encoded its chunk size in a single `wIndex` byte with "0 means full buffer",
which was silently wrong once the buffer exceeded 256: asking for 512 truncated to 0,
read as "full buffer", so a 512 x 240 request clocked **245760 bytes in one
uninterruptible loop**. That overran the host's control-transfer timeout and took the
device off the bus entirely -- a physical replug was needed.

Re-encoding it in 64-byte units fixed the truncation, and a 65536-byte work bound was
added. **The bound was off by one case**: 1024 x 64 is exactly 65536, so `> 65536` is
false, the request passes, and the clocking still overruns. It wedged a second time,
though that one recovered without a replug.

So the correct bound is on *time*, not bytes, and it has to account for the whole
control transfer rather than just the clocking. Until that is fixed the 1024-byte
no-USB figure is unavailable -- which leaves the 1024 column without a denominator, the
same gap that the 256-byte column had before it was filled.

**Real configure**, in-process and therefore comparable, same bitstream:
**778.0 ms at 512 B, 741.0 ms at 1024 B.** The shift benchmark and the real configure
agree on direction and roughly on magnitude -- 32.0 ms against 37.0 ms -- which is the
first time in this work the two instruments have been cross-checked.

Every `n/a` is an impossibility rather than a gap: those firmwares declare a 256-byte
buffer and stall anything larger, so the request is refused rather than merely
unnegotiated.

**Cumulative against stock: 713.9 -> 318.1 ms, 2.24x.**

Rows above `cd4a85c` were measured on the four-hub chain and are ~10% pessimistic. They
stay comparable with each other, but **only the `direct USB port` and `d43f765` rows
reflect the current setup**, so those are the ones to quote. Real configure on the same
bitstream, in-process: **693 ms**, against 741 ms on the hub chain.

## DMA is the largest code win, and it does not fit

`d43f765` is worth **-85 ms, 1.26x** -- more than every other firmware change in this
table combined. It is also **over budget: flash 96.65% against a 95% ceiling, RAM 85.16%
against 85%.** The deficit is **237 bytes of flash and 7 bytes of RAM**. It is committed
but *not shippable*, and the ceilings were deliberately not raised.

**Why it worked, when the archived version did not.** `debris/code/spi-dma-cynthion-d11.c`
ran on hardware in an earlier session and measured **+13 to +36 ms slower**, so DMA was
written off. The defect was one line:

    while (!(DMAC->CHINTFLAG.reg & (DMAC_CHINTFLAG_TCMPL | DMAC_CHINTFLAG_TERR)));

It freed the CPU with DMA and then immediately burned it in a spin, so `tud_task()` stayed
blocked for the whole transfer and behaviour was identical to polling plus setup cost.
**DMA was never tested as an asynchronous mechanism.** Arming the channels and returning,
then polling completion from `jtag_scan_task()`, is the entire difference between -36 ms
and +85 ms.

Completion is polled from the task rather than served by a DMA interrupt, and that is
forced: `CHID` is a single register window selecting which channel's registers are
visible, so the channel-setup sequence is not re-entrant and must not run from an ISR.
The poll costs one register read per main-loop iteration against a ~700 us spin, so
nothing is lost.

**Where the 237 bytes went.** About 64 are intrinsic -- DMAC descriptor and write-back
sections the hardware mandates. The rest is splitting one synchronous function into arm
and poll halves whose state must be marshalled through a struct. The cheap savings are
already taken: one aligned allocation instead of two descriptor arrays, a redundant DMAC
reset and two `.bss` memsets dropped, both scan structs packed, an always-true
`discard_tdo` field removed -- about 100 bytes of the original ~520. What remains is a
cheaper arm/poll split, or reclaiming flash from elsewhere in the firmware. Shrinking the
stack reservation does not qualify: the stack is RAM and cannot pay a flash debt.

**The second buffer is not redundant, which was worth checking.** If DMA had made
`jtag_tx_alt` unnecessary, its 512 bytes of RAM would have paid for the change outright.
Forcing the host onto the single-buffer path costs **+74.1 ms (+22.8%)**, so the two
mechanisms are complementary: DMA stops the CPU blocking on the wire, and the second
buffer lets the host's *next* fill land during the current chunk. `scripts/jtag_single_buffer_probe.py`
exists so this is not re-proposed next time flash is tight.

Two rows earn their place by being nearly flat. `0e9bfb1` is a pure refactor and moves
4.8 ms, which sets the noise floor. `cd4a85c` double-buffers the staging so `SCAN` returns
in 139 us instead of ~895 us -- the overlap provably works -- and buys 1.9 ms at 1024 B,
because clocking was only 24% of the total and hiding it was the wrong quarter to chase.

**Every `n/a` is an impossibility, not a gap.** Only HEAD has 512-byte buffers.
Everything before it declares `jtag_out_buffer[256]` and stalls anything larger --
`if (request->wLength > sizeof(jtag_out_buffer)) return false;` -- so a 512-byte
request is refused by the firmware itself. The host would also fall back to 256
regardless, since `GET_INFO` arrived with HEAD; the benchmark detects that and
declines to label a 256-byte run as 512, which is what stops an unimplemented
`GET_INFO` from flattering a result.

Theoretical is chunk-independent by definition: 122880 x 8 / 12 MHz = 81.9 ms,
1500 KB/s. Chunking is a transport concern and the wire does not see it.

**Read the columns, not the diagonal.** Figures in this paragraph are from the hub-chain
rows, so they overstate the USB share slightly, but the shape holds. At 256 B, 280 ms of
555 is USB against 275 ms clocking -- half and half. At 512 B it is 352 of 489 in USB against
137 clocking -- 72/28. The ratio moves because doubling the chunk **halves** the
firmware-side cost (275 -> 137) while barely touching USB (280 -> 352, and that is
worse in absolute terms).

That last point is the one the single-column version hid: **going to 512 bytes made
the USB portion bigger, not smaller.** It won overall because the firmware-side saving
was larger than the USB-side loss. Which is also why the chunk change bought less than
arithmetic predicted -- it was fixing the smaller half, and slightly worsening the
larger one.

## USB transfers already use DMA, and that reframes the levers

Asked whether USB transfers use DMA. **They do**, and the answer matters more than
expected.

The SAMD USB peripheral **is** a DMA engine. `dcd_edpt_xfer()` at
`dcd_samd.c:276` does:

    bank->ADDR.reg = (uint32_t) buffer;

so the caller's buffer address goes straight to the hardware, which moves the bytes
itself. Zero copy, no CPU involvement. That is also what `sram_registers[8][2]` is --
the endpoint descriptor table the hardware reads to find those addresses, which is
why it is silicon-mandated and not reclaimable.

**So there is no CPU-side data-movement cost to remove on the USB side either.** This
is the same conclusion the DMA investigation reached for the SPI side, arrived at from
the opposite direction: both halves of the transport are already hardware-driven.

### Where the 1466 us per chunk actually goes

At 512 B the USB portion is 351.9 ms over 240 chunks, so **1466 us per chunk**.
Decomposing what is known:

| component | per chunk | how known |
|---|---|---|
| two control transfers, fixed cost | 428 us | 2 x 213.9 us measured |
| 512 bytes of payload at 12 Mbit/s | 341 us | arithmetic |
| **unaccounted** | **~700 us** | |

**Half the USB cost is neither request overhead nor data movement.** That is the
single most useful thing in this section, because both remaining levers target the
428 us: the collapse removes half of it, and double-buffering hides clocking behind
it. Neither touches the 700 us.

Candidates for the 700 us, none yet established:

- **Frame scheduling.** Full-speed USB schedules control transfers per 1 ms frame,
  and a transfer with a data stage plus a status stage may not complete within one
  frame. If each chunk costs a whole frame boundary somewhere, that alone is
  hundreds of microseconds and is a property of the bus rather than of either end.
- **Host-side per-call cost** in libusb or the kernel, above the 213.9 us measured
  for a near-empty transfer -- the measurement used a 32-byte reply, so a 512-byte
  data stage may cost more than the payload arithmetic suggests.
- **`tud_task()` latency**: the firmware processes the transfer from the main loop,
  not from the ISR, so completion waits on a loop iteration.

**This should be measured before either lever is built.** If the 700 us is frame
scheduling then the collapse is worth its 51 ms and double-buffering is worth its
137 ms, and both leave 168 ms of frame cost untouched -- which would make the real
ceiling around 320 ms rather than the 352 ms the USB portion implies. Timing a single
`SET_OUT_BUFFER` of 512 bytes in isolation, against one of 8 bytes, would separate
payload from fixed cost directly.

## 1024-byte chunks DO fit, if JTAG may claim what is idle

I previously said the buffer merge saves only 256 bytes and that 1024-byte chunks
therefore do not fit. **That was wrong** -- it counted only the console ring and
missed everything else that is idle during a JTAG session.

The reasoning that makes the rest claimable: **during JTAG programming everything
else is off, and the board is rebooted afterwards.** So any buffer private to a
subsystem that is gated off is on the table, and its contents need not survive.

| source | bytes | why claimable |
|---|---|---|
| already unallocated | 644 | free today |
| `uart_rx_ring` | 256 | `console_task()` returns immediately while the JTAG lock is held |
| `_cdcd_itf` | 296 | TinyUSB CDC class state -- the console is gated off, and CDC is not the JTAG path |
| stack reservation | 336 | 1024 reserved, **344 measured** high-water; keeping 2x margin at 688 still frees 336 |
| **total** | **1532** | |

**Attempted, and it does not fit.** This was tried and reverted -- the arithmetic
above is wrong in a way worth recording, because it is an easy mistake to repeat.

Setting `JTAG_BUFFER_SIZE` to 1024 puts RAM at **95.12%**, and the budget check in
`check.py` rejected it. The error: the reclaimed bytes were counted correctly, but
doubling the buffer costs **both** of them -- `jtag_in` and `jtag_out` -- so the price
is 1024 bytes, not the 512 the "one more doubling" framing suggests. And the union
cannot absorb it, since the console ring is 256 bytes against a JTAG pair that would
be 2048.

Working backwards from the 85% ceiling: non-union `.bss` is 1144 bytes and the stack
is 704, so the union may be at most about 1634 bytes -- **a JTAG pair of roughly
2 x 817.** 512 stands; 1024 does not.

To actually reach 1024-byte chunks, one of these would have to happen first:

- **Drop `jtag_in_buffer` during writes.** It is the larger half of the pair and
  configure never reads TDO -- `ecp5.py` passes `ignore_response=True`. But
  `spi_send()` dereferences its receive pointer unconditionally, so this needs a
  NULL-receive path in the SPI driver first.
- **Find another 400+ bytes.** `_cdcd_itf` at 296 is the only remaining candidate of
  size, and it is TinyUSB's internal state with the CDC port possibly still open
  during JTAG -- the riskiest claim available and still not enough alone.

So the request-count lever is exhausted at 512 bytes on this part, and the ~51 ms it
would have bought is not available without one of the above.

That is worth roughly **51 ms** by the request-count arithmetic -- 120 chunks instead
of 240, at 213.9 us of fixed cost per request -- which is the same as the collapse, for
no protocol change.

### What each claim actually requires

They are not equally easy, and the order matters:

**The stack claim is the safest and needs no code**, only a linker flag. The script
already reads `STACK_SIZE = DEFINED (STACK_SIZE) ? STACK_SIZE : ... : 0x400`, so
`-Wl,--defsym=STACK_SIZE=0x2b0` sets 688 bytes with no linker-script edit. It rests
entirely on the measurement being trustworthy, which is why the paint-and-measure work
came first: 344 bytes is a **lower bound**, since depth already in use when painting
ran is invisible and one run does not prove a worst case. 2x margin is the hedge.

**The ring claim is a union**, with the exclusivity proven by the JTAG lock spanning
`jtag_init()` to `jtag_deinit()` rather than by convention.

**The `_cdcd_itf` claim is the awkward one and probably should not be taken.** It is
TinyUSB's internal state, not ours: reusing it means either patching TinyUSB or
aliasing a structure whose layout is not ours to rely on. And CDC is not merely idle
during JTAG -- the host may still have the port open, so TinyUSB could touch that
state on an unrelated control transfer. Without it the total is 1236, which still
covers the 1024 needed.

So: **1024-byte chunks fit using the unallocated space, the ring, and the stack
reduction alone.** `_cdcd_itf` is not needed and carries the most risk.

## USB topology: the largest single win, and not a code change

Apollo was plugged **four chained hubs deep**, on a full-speed segment shared with an
audio device, a HID and two other CDC devices. Moving it to a port one level from the
root hub, with nothing else on that segment:

| | four hubs deep | direct port |
|---|---|---|
| fixed cost per transfer | 144.7 us | **122.7 us** |
| per-byte cost | 2.717 us | **2.267 us** |
| effective throughput | 2.94 Mbit/s | **3.53 Mbit/s** (29% of the bus) |
| shift path, 1024 B chunks | 455.6 ms | **409.1 ms** |
| real configure | 741 ms | **693 ms** |

**46 ms from moving a cable** -- more than double-buffering, the chunk increase, the
pipelined `spi_send()` or the TDO suppression bought individually.

Every full-speed transaction below a high-speed hub is split-transaction scheduled by the
nearest hub, and the 12 Mbit/s of that segment is *shared* with everything else on it.
Four hubs of chaining plus contention was costing 17% of the per-byte rate.

**This should be stated whenever a figure is quoted.** Every measurement in this document
before this section was taken on the hub chain, so they are internally comparable but all
about 10% pessimistic. The table above is the corrected baseline.

### It also bounds the remaining levers more tightly

Since per-byte cost is the wall, and it improved by moving the *host-side* topology rather
than anything on the device, the interesting question is how much of the remaining 2.267
us/byte is still contention rather than protocol. The wire is 0.667 us/byte, so 71% is
still overhead -- but the split between "control transfer protocol" and "this host, this
controller, this cable" is now known to be non-zero and was previously assumed to be zero.

### On Linux-side priority: there is nothing to tune

Checked, because it is the obvious next thought. `usbcore` exposes only `autosuspend`,
`authorized_default` and `initial_descriptor_timeout` -- no scheduling parameters.
Control-transfer priority is fixed by the USB spec (10% of frame budget, best-effort) and
implemented by the host controller, which here is **xHCI** -- so the scheduling happens in
controller firmware walking transfer rings, not in the kernel driver. There is no knob.

### Isochronous would be faster, and is probably not worth it

Full-speed isochronous allows **1023 bytes per endpoint per frame** with *guaranteed*
bandwidth, against control's measured ~360 KB/s:

| | staging for 122880 B |
|---|---|
| control, measured | 340 ms |
| isochronous, theoretical | **120 ms** |

2.8x, and it is the only transfer type with a bandwidth guarantee rather than
best-effort scheduling.

The catch is that **isochronous has no handshake and no retry** -- a dropped packet is
simply gone. For a bitstream that means an integrity layer would have to be built on top:
the ECP5's own CRC detects corruption but cannot recover from it, so a failed configure
would have to be retried whole. That is a large amount of new machinery for a path that is
already at 693 ms.

## The unexplained USB cost, measured: it is bus efficiency, not frame scheduling

This was the largest open question and it now has an answer, which changes what the
remaining levers are worth.

`scripts/usb_transfer_cost.py` issues the same `SET_OUT_BUFFER` at several payload sizes
and fits `cost = fixed + per_byte x size`. Frame scheduling predicts a nearly flat line
-- 8 bytes and 1024 bytes both costing about a 1 ms frame. Per-transfer overhead plus
real payload cost predicts a slope.

    8 B:    215.8 us      wire alone would be    5.3 us
   64 B:    301.4 us                            42.7 us
  256 B:    822.4 us                           170.7 us
  512 B:   1516.7 us                           341.3 us
 1024 B:   2941.3 us                           682.7 us

    fit: 144.7 us fixed + 2.717 us/byte

**It scales, so it is not frame scheduling.** The line is clean and the slope is large:
**2.72 us/byte against a 0.667 us/byte wire**, so 75% of the per-byte cost is overhead.
Effective throughput is **2.94 Mbit/s of a 12 Mbit/s bus -- 25% efficiency.**

That is a property of control transfers on a 64-byte endpoint: each packet carries token,
data and handshake phases, so most of the time is protocol rather than payload. Neither
end controls it.

### What this does to the two remaining levers

The model predicts the measured transport cost closely, which is what makes it usable:

| chunk | chunks | predicted | of which fixed | of which payload |
|---|---|---|---|---|
| 512 B | 240 | 403 ms | 69 ms | 334 ms |
| 1024 B | 120 | 369 ms | 35 ms | 334 ms |

Measured USB portion at 1024 B is 320.6 ms, against 369 predicted -- close enough to
trust the split.

**The payload term is constant at 334 ms and does not depend on chunk size at all.**
That is the wall.

So:

- **Collapsing `SET_OUT_BUFFER` + `SCAN` is worth about 17 ms at 1024 B, not the 51 ms
  estimated earlier.** The 51 ms figure came from 240 chunks at 213.9 us; at 1024-byte
  chunks there are only 120, and the correct fixed cost is 144.7 us. That is **4% of
  total** for a protocol change.
- **Double-buffering's ceiling stands at 137 ms** -- it hides clocking behind USB, and
  USB is still much the longer leg. It remains the largest available lever by a wide
  margin.
- **Larger chunks are now clearly exhausted.** Each doubling halves only the fixed term,
  which at 1024 B is already down to 35 ms of 457.

### And it names the real ceiling

Nothing on this transport beats roughly **334 ms of payload cost plus 137 ms of
clocking**, and those overlap at best. So the floor for this architecture is about
**334 ms**, not the 81.9 ms the wire suggests.

Against that, the current 457.6 ms is **73% of achievable** rather than 18% of
theoretical. The 5.6x gap to the wire is real but mostly not addressable: it is a
64-byte control endpoint on a full-speed bus.

### Bulk would not move the payload term either, and this explains the old result

I first wrote that a bulk endpoint would fix this, "which allows larger packets and far
less per-packet overhead". **That is wrong at full speed, and the descriptors say so.**

Apollo already exposes bulk endpoints -- EP2 OUT and EP3 IN, the CDC console's -- and
both are **`wMaxPacketSize 64`, identical to control.** That is not a design choice: the
USB 2.0 spec caps a full-speed bulk endpoint at 64 bytes. The 512-byte bulk packets worth
having exist only at high speed, which is the FPGA's PHY, not Apollo's.

So bulk offers **no packet-size advantage here**. What it does offer is the removal of
per-transfer framing: a control transfer costs SETUP, DATA and STATUS stages, while bulk
is data packets back to back.

That bounds it precisely. Per 1024-byte chunk the cost is `2 x 144.7 us fixed +
2782 us payload`. If bulk removed the fixed cost **entirely** it would save 289 us per
chunk, or **35 ms of 457** -- about 8%, and only if the per-byte cost were unchanged.

**Which retires the mystery rather than deepening it.** The earlier bulk-streaming attempt
measured 1703 ms against 1683 ms and was recorded as an unexplained failure -- built,
working, and no faster. It is now explained: at full speed, bulk and control move bytes at
the same rate, and the only saving available was the per-transfer framing, which is a
small fraction of the total. The attempt did what it was designed to do; the design could
not have helped much.

### Endpoints available, and one idea parked

What Apollo actually exposes, read off the live device rather than from the source:

| interface | class | endpoints |
|---|---|---|
| 0 | Communications (CDC control) | EP 0x81 IN, interrupt, 8 B |
| 1 | CDC Data | **EP 0x02 OUT and EP 0x83 IN, bulk, 64 B** |
| 2 | DFU runtime | **none** -- EP0 only |

Plus EP0, where **all** JTAG traffic goes today, since the vendor requests are control
transfers. JTAG has no endpoint of its own.

**So a bulk path needs no new endpoint.** The pair already exists, TinyUSB is already
paying for the descriptor space, and CDC is provably idle during a JTAG session -- the
same JTAG-lock argument that justified sharing the buffers, applied to endpoints.
`console_task()` already returns immediately while the lock is held, so those endpoints
are idle rather than merely quiet. An alternate interface setting, or claiming the
interface on a bulk-mode vendor request, would let both use them.

The SAMD11's descriptor table is `sram_registers[8][2]`, so eight endpoints of space.
Reusing CDC's rather than declaring a fourth pair avoids spending any of it.

### Parked: the bootloader could use bulk too

Worth a look, not now.

The bootloader and the application **never run at the same time** -- Saturn-V runs, jumps
to the app, and is gone until the next reset. So the bootloader has the *entire* endpoint
budget free, with nothing to be exclusive with, and it currently does all its flashing
over **EP0 control transfers** at the same 5.8 packets/frame measured above. That is why
flashing a 12 KB image takes as long as it does.

The same 3.3x argument applies, and it is architecturally cleaner than the application
case: no exclusivity reasoning is needed at all, because nothing else exists to contend.

Two things to check before anyone acts on it. The app's DFU interface is **DFU *runtime***
(`TUD_DFU_RT_DESCRIPTOR`, `usb_descriptors.c:104`) and is functional rather than vestigial
-- `vendor.c:441` calls `tud_dfu_runtime_reboot_to_dfu_cb()` -- so it is not dead weight to
be removed. And it has no endpoints, so removing it would free none. The bootloader-side
change is entirely within Saturn-V, which is a separate submodule (`repos/saturn-v`) with
a **2 KB** flash budget and `-flto` already enabled, so there may be no room for a bulk
implementation regardless.

### But a DEDICATED bulk endpoint is a different proposition, and worth 3.3x

The paragraph above concluded that no endpoint type changes the per-byte cost. **That is
wrong**, and the arithmetic that shows it is simple enough that I should have done it
before writing the conclusion.

Packet size is not the variable that matters. **Packets per frame is.**

| | packets per 1 ms frame | us/byte |
|---|---|---|
| full-speed bulk, by spec | **19** (1216 B/frame) | **0.822** |
| measured, control transfers | **5.8** | 2.717 |

So the measured cost is **3.3x worse than a full-speed bulk endpoint can do**, and the
gap is scheduling opportunity, not signalling. A control transfer carries SETUP, DATA and
STATUS stages and the host schedules them conservatively; bulk packets stream back to
back until the frame is full.

Sanity check on the measurement: a 1024-byte control transfer took 2941 us, which is
about three frames, and works out at 174 us per 64-byte packet. A frame holds 1000 us, so
5.8 packets fit. Against 19 for bulk.

What that means for a dedicated bulk path -- **shutting down CDC and using its endpoints
for JTAG, or declaring new ones**:

| | payload for 122880 B |
|---|---|
| today, control | 334 ms |
| bulk at 50% of theoretical | **202 ms** |
| bulk at theoretical | **101 ms** |

Plus the 35 ms of per-chunk framing that disappears when the transfer is one stream
rather than 120 chunks.

**So this is the largest remaining lever by a wide margin** -- bigger than
double-buffering's 137 ms -- and it changes the floor rather than shaving the overhead.
It is also the one that requires the most work: a bulk protocol needs its own framing and
error handling, where a control transfer gets a status stage for free.

### And it re-opens the old streaming result rather than closing it

Two paragraphs ago I wrote that the earlier bulk-streaming attempt was now explained --
that bulk and control move bytes at the same rate at full speed, so it could not have
helped. **That explanation is also wrong.** Bulk should have been up to 3.3x faster on
the payload term, and it measured 1703 ms against 1683 ms.

So the question is back, and sharper than before: **why did a working bulk implementation
perform like control?** Candidates worth testing, none established:

- **The firmware could not keep the endpoint fed.** If it clocks a chunk to the FPGA
  before accepting the next bulk packet, the bus idles and the achieved rate collapses to
  whatever the turnaround allows -- which would look exactly like control.
- **The host submitted synchronously**, one transfer at a time, so the bus idled between
  submissions regardless of endpoint type.
- **`tud_task()` latency**: transfers are processed from the main loop, so completion
  waits on a loop iteration.

All three predict that bulk's advantage is only available with **double-buffering**, since
that is what keeps the endpoint fed while the SERCOM clocks. Which would make the two
levers one change rather than two independent ones.

## The two remaining levers, quantified before building either

Both measured rather than estimated, because the chunk change taught that arithmetic
here overpredicts.

| lever | ceiling | what it needs |
|---|---|---|
| collapse `SET_OUT_BUFFER` + `SCAN` | **51.3 ms** | one new vendor request, host fallback |
| double-buffer fill against clocking | **137 ms** | both ends restructured, async host |

**Collapse: 51.3 ms.** A bare control transfer that does almost nothing --
`GET_ID`, 500 samples -- costs **213.9 us**. That independently reproduces the ~215 us
figure from the decomposition above. At 240 chunks, removing one of the two requests
per chunk is 240 x 213.9 us = 51.3 ms, or 10.5% of the 488.9 ms total.

**Double-buffering: 137 ms, and only with perfect overlap.** The split at 512 B is
351.9 ms USB against 137 ms clocking, so overlapping them perfectly gives
`max(351.9, 137) = 351.9 ms` -- a saving of exactly the clocking time, 28% of total.
It cannot do better than that: USB is the longer leg, so the clocking hides inside it
and the USB time remains exposed.

### Why double-buffering is not a small change

The path is synchronous at three levels, and all three have to change:

- **`handle_jtag_request_scan` clocks then acknowledges.** It calls `jtag_scan()`,
  which shifts every byte, and only then completes the control transfer. So the host
  cannot learn the scan finished without waiting for it, and the firmware has no state
  in which "scan in progress, next buffer accepted" exists.
- **The host loop blocks.** `_scan_data_chunk` issues `SET_OUT_BUFFER` then `SCAN` and
  waits, so chunk N+1's fill cannot begin until chunk N's clocking returns.
- **There is one buffer.** `jtag_out_buffer` is written by USB and read by the SERCOM
  with nothing between them, so a second fill would corrupt an in-flight scan.

So it needs a second buffer, a non-blocking `SCAN` with completion reporting, and an
async host loop -- against 137 ms of ceiling on a 489 ms operation.

**The collapse is the better first move** despite the smaller number: one vendor
request, no restructuring, and it composes with double-buffering later rather than
being replaced by it.

## Where the remaining time goes

### The gap is the whole story

At 512 bytes the real path takes **488.9 ms** where the same clocking with no USB
payload takes **137 ms**. So roughly **352 ms, 72% of the total, is USB transport**,
and only 137 ms is the microcontroller clocking bits.

And the no-USB figure is itself only 60% of theoretical, so there are two distinct
gaps:

- **81.9 -> 137 ms** is the SERCOM and its loop: 55 ms of per-byte overhead the wire
  does not account for. The marginal-cost differencing above puts clocking at
  0.663-0.715 us/byte against a 0.667 us/byte wire floor, which is 2-3% -- so most
  of this 55 ms is per-*chunk* cost (pinmux, setup, the verify loop), not per-byte.
- **137 -> 488.9 ms** is USB. This is the part the two untried levers attack.

Note the no-USB figure halves cleanly from 256 to 512 bytes (275 -> 137 ms) while the
real path improves only 12% (558.8 -> 488.9). That asymmetry is the clearest single
statement of where the time goes: doubling the chunk halves the firmware-side
per-chunk cost, and barely dents the USB cost.

### Two caveats on the 0xb9 numbers

**`blocks` in the reply is in 256-byte units, not bytes** -- `blocks = chunk *
repeats / 256` at `jtag.c:337`. Reading it as bytes doubles the apparent payload,
which it did on the first attempt here.

**The mismatch counter only means anything in BYPASS.** The benchmark expects TDO to
be TDI delayed by one bit, which holds in SHIFT-DR with BYPASS selected. Run from a
context that leaves the TAP elsewhere and it reports mismatches on every byte -- as
it did here. That is the harness being used wrongly, not a data-integrity failure,
and the counter is worthless unless the TAP state is set up deliberately.

Verified behaviourally rather than by version string, which is worth noting: the
build reports `v1.1.1-41-gbb82d39-dirty` even when the code is stock, because the
version comes from `git describe` on the working tree rather than from the checked-out
firmware. Confirmed stock instead by both project-added vendor requests stalling --
`GET_INFO` (0xb8) and `GET_STACK_USAGE` (0xa5).

Stock builds at **94.17% ROM / 86.52% RAM**, which is why it needed no LTO to fit
its own feature set and why ours does.

**So `e034daa` is 639 -> 567 ms, or 1.13x -- not the 1.53x recorded at the time.**
That original figure was a whole-configure measurement on a different payload, and
it is not wrong so much as not comparable. On identical work the change is real and
about a quarter the size claimed.

The `0e9bfb1` row is the useful control: a pure refactor, and it moves 4.8 ms, which
sets the noise floor for reading the others.

Getting stock to build at all took two fixes, both artifacts of mixing eras rather
than facts about stock. `SOURCES` is a wildcard over `src/*.c`, so **four** files this
project added stay in the build after an older checkout -- they are untracked at
`v1.1.1`, so git leaves them alone: `stack_probe.c/h` and `apollo_mode.c/h`. With
those still present, stock fails on `-Werror=array-bounds` and then at 107% ROM /
103% RAM. The script now sets all four aside per commit.

## The synthetic benchmark (0xb9), and the floor that was not a floor

Merged from a separate document, because two files about JTAG timing is how these
docs came to hold forty non-comparable figures in the first place.

Vendor request `0xb9` (`handle_jtag_benchmark`) exists to measure clocking with **no
bulk USB traffic at all**: it generates its own pattern in firmware
(`jtag_out_buffer[i] = i * 7 + 1`), clocks it, verifies TDO, and returns elapsed
milliseconds. It also accepts a SERCOM divider, so SCK can be swept with nothing in
the way to confound the result.

### What it originally concluded, and why that was wrong

Sweeping the divider with every readback bit-exact gave:

    time per byte = 8/SCK + 1.11 us

The second term was constant across an 8x SCK range, which was read as: the wire
needs 0.667 us/byte at 12 MHz, so the polled loop was spending **1.11 us/byte of CPU
on top**, and the path was CPU-bound rather than wire-bound.

**That was an artifact**, and the same one that produced the 3.93 us/byte figure
elsewhere: dividing a whole call by its payload charges the fixed per-call cost to
per-byte clocking. Measuring the *marginal* cost instead -- differencing 64-bit
against 2048-bit scans so the fixed cost cancels -- gives 0.663-0.715 us/byte against
a 0.667 us/byte wire floor. **2-3% overhead, not 166%.**

So `spi_send()`'s pipelining did take a real win, but the "1.11 us/byte of CPU" it
was credited with removing never existed as a per-byte cost. It was per-chunk cost,
mis-attributed.

### Two false results its own controls caught

Worth keeping, because both looked clean:

- The DMA and baseline firmwares measured **identically** on the first isolation
  attempt -- 329.7 against 329.1 us. A positive control asserting that `SCAN` is
  accepted *and* that its cost scales with bit count is what surfaced it: a stalled
  or no-op request looks exactly like a fast one on a stopwatch.
- The first harness used **wrong request numbers** -- `0xb5` is `GOTO_STATE`, not
  `SCAN`. Checking against the firmware enum rather than trusting plausible timings
  is what caught that.

### Using it correctly

Two traps, both hit while collecting the numbers in the table above:

- **`blocks` in the reply is in 256-byte units**, not bytes -- `blocks = chunk *
  repeats / 256` at `jtag.c:337`. Reading it as bytes doubles the apparent payload.
- **The mismatch counter is only meaningful in BYPASS**, where TDO is TDI delayed one
  bit. Called from a context that leaves the TAP elsewhere, it reports a mismatch on
  every byte -- harness misuse, not corrupted data.

## The RAM work, one change at a time

Four changes, each built, flashed and tested on its own before the next. Two of them
deliberately bought **no** speed, which is the point of testing separately: a speed
change there would have meant something went wrong.

| change | RAM | 512 B time | what it bought |
|---|---|---|---|
| baseline | 84.28% | 489.4 ms | |
| stack sized from measurement | 78.52% | unchanged | **320 bytes** |
| console ring shares the JTAG union | 72.27% | 491.0 ms | **256 bytes** |
| `spi_send()` accepts NULL receive | 72.27% | unchanged | capability |
| `FLAG_DISCARD_TDO` | 72.27% | 492.6 ms | capability |

**576 bytes reclaimed, no measurable speed change.** All three time figures sit inside
a 1.0-6.9 ms spread, so they are one number.

### Why each was tested alone

**Stack from measurement.** `STACK_SIZE=0x2C0` (704 bytes) against 344 measured, so
just over 2x margin. Verified on hardware afterwards -- idle, 300 control transfers, 50
sideband commands, a full JTAG configure -- and the high-water came back **344, exactly
as with 1024 reserved**. That is the expected result and worth stating: usage does not
depend on the reservation, so shrinking it changes nothing until something actually
overflows. On a part with no MPU that would be silent `.bss` corruption rather than a
fault, which is why the margin is generous rather than tight.

**The union.** The console ring and the JTAG buffers share storage, with exclusivity
proven by the JTAG lock spanning `jtag_init()` to `jtag_deinit()` -- and
`console_task()` returning immediately while it is held. Deliberately **not** aliased:
`jtag_in_buffer` and `jtag_out_buffer` stay separate, because `spi_send()` takes both in
one call and overlapping them would corrupt every transfer.

That change introduced one hazard and closed it: the buffers became **pointers rather
than arrays**, so `sizeof()` on them silently yields 4. Six sites used
`sizeof(jtag_out_buffer)` as a bound, including the `SET_OUT_BUFFER` length check --
which would have become a 4-byte limit rejecting every real request. All six now use
`JTAG_BUFFER_SIZE`.

**`spi_send()` NULL receive.** SPI is inherently bidirectional, so the hardware hands
over a byte whether the caller wants it or not, and both store sites wrote to the
caller's pointer unconditionally -- passing NULL wrote to address 0. The `DATA` register
is still read when discarding, and must be: `RXC` stays set until `DATA` is read and the
loop waits on `RXC`, so skipping the read **hangs** rather than merely dropping a byte.

**`FLAG_DISCARD_TDO`.** `ignore_response` previously suppressed only the host's own
`GET_IN_BUFFER` call; the firmware still captured every byte. Bit 2 of the SCAN flags
now tells it. Backward compatible both ways -- older firmware masks only the flags it
knows, so an unrecognised bit is ignored rather than misread, and an older host simply
does not set it.

### What this was all for

`jtag_in_buffer` is now **provably untouched during a configure**, which is the
precondition for handing its 512 bytes to the transmit path. That is the remaining route
to 1024-byte chunks after the direct attempt hit 95% RAM, and is worth roughly 51 ms by
request count.

The two capability changes are the interesting entry in the table precisely because
they moved nothing: they stopped a store into RAM, and the store was never the
bottleneck. Their value is entirely in what they make possible next.

### 1024-byte chunks: the shift path gets faster, and the read bound is the blocker

Attempted properly this time -- not by growing RAM, but by re-carving the union so the
transmit buffer takes the **whole** region and the receive buffer becomes its second
half. That works because `FLAG_DISCARD_TDO` proves the receive half is untouched during
a write.

**RAM did not grow: 70.12%, and after reverting, 59.77%** -- the re-carve stopped
allocating two separate halves, which is a genuine gain the revert kept.

The shift benchmark improved as predicted:

| chunk | chunks | best |
|---|---|---|
| 256 B | 480 | 564.0 ms |
| 512 B | 240 | 490.7 ms |
| **1024 B** | **120** | **457.5 ms** |

33 ms over 512, 106 ms over 256. Real and measured.

**But it was reverted, because of a flaw I introduced.** `GET_INFO` reports one size,
and there are now **two** limits: the transmit buffer is the whole region, while a
capturing scan is bounded by the read half. The host negotiated 1024 and had no way to
know reads must stay under 512. Demonstrated directly:

    capturing scan of  512 B: accepted
    capturing scan of 1024 B: REFUSED (USBError)

`_execute_command()` in `ecp5.py` defaults to `ignore_response=False`, so parts of the
configure sequence do capture TDO. They are small today, but the protocol now has a
limit the host cannot discover.

**The fix is to report both limits** -- `GET_INFO` already returns eight bytes with four
unused, so a read limit fits with no new request. That is the next change rather than a
blocker.

### A measurement error worth recording, because it nearly reversed the conclusion

The real configure appeared to get **slower** at 1024 -- 831 ms against a recorded
774 ms at 512 -- while the shift benchmark got faster. Six samples said 832-854 ms, so
it was not noise, and the contradiction looked like a real regression.

It was the measurement. Those figures timed `subprocess.run(python cli.py configure)`,
which includes **Python interpreter startup** -- roughly 80 ms that has nothing to do
with JTAG. Measured in-process instead, same session, both sizes back to back:

    configure at 256 B: 850.9 ms
    configure at 512 B: 776.1 ms

The 774 ms reproduces exactly. So the "1024 is slower" reading was an artifact of
process startup, and the earlier 774 figure was only comparable because it happened to
be measured the same way.

**The lesson is the same one the fixed-payload benchmark exists for**, arriving from a
new direction: it is not enough to fix the payload, the harness has to be fixed too. A
per-process measurement cannot resolve a 33 ms difference when startup costs 80.

## Things tried that did not work

Recorded so they are not retried, and because the failures were more informative than
some of the successes.

| attempt | outcome | why |
|---|---|---|
| `JTAG_BUFFER_SIZE` 512 -> 1024 | **rejected at 95.12% RAM** | doubling costs BOTH halves of the pair, 1024 bytes not 512 |
| SCK 12 -> 24 MHz | **rejected, unsafe** | divider steps 8/12/24 with nothing between; SAMD11 `tSCK` min 84 ns = 11.9 MHz rated, so 12 is already past |
| SERCOM DMA, *spinning on completion* | **implemented, marginally slower -- and the conclusion was wrong** | 1711-1751 ms against 1698-1715 polled. It spun on `TCMPL` after arming, so `tud_task()` stayed blocked and it was polling plus setup cost. Made asynchronous instead: **-85 ms, 1.26x** |
| DMA making the second buffer redundant | **hypothesis, disproven** | removing `jtag_tx_alt` costs +74.1 ms (+22.8%); DMA and double-buffering are complementary, not alternatives |
| dropping `button_task()` alone | **no measurable gain -- but see below** | 326.4 vs 327.9 ms, inside the 1.6 ms spread. It really does cost ~4.8 us per iteration (PA16 is shared with `LED_A`, so each poll saves the level, flips to input, waits 50 cycles for the pull, samples and restores) |
| masking the EIC during JTAG | **no measurable gain** | 326.7 vs 327.9 ms, inside a 4.0 ms spread. `fpga_adv_task()` has no JTAG gate, unlike `console_task()`, so the EIC stays armed on a pin the FPGA has tri-stated -- but it costs nothing measurable |
| **the conclusion drawn from those two** | **wrong, and worth more than either** | both were tested *in isolation*, where removing one poll of five leaves the other four setting the loop period. Cutting the LED and button together from ~200 kHz to 200 Hz measured **327.9 -> 318.1 ms**, 9.8 ms against a 5.3 ms spread (`7060149`). The null results were real; "loop-level work is noise" did not follow from them |
| TX-only (drop TDO entirely) | correct but not worth it | ~2 ms of 950, for a silent-failure surface |
| bulk streaming | **built, worked, no faster** | 1703 vs 1683 ms; its stated cause was later disproven, so still unexplained |
| `-fstack-usage` for stack depth | **wrong tool** | LTO inlines across units, so per-function frames stop matching the final binary |
| `git bisect` across apollo history | **failed on all 5 points** | checking out old apollo replaces `apollo_fpga/`, removing `boot_to_dfu()` -- one of our own additions |
| paint-and-measure, first version | **self-contradictory** | LTO resolved `&_sstack` differently per inlined copy; reported full-region use AND no overflow |
| word-wise stack scan | **latent bug** | one coincidental `0xDEADBEEF` truncates the scan and understates usage |

Six of these are worth more than a note.

**The DMA row is the cautionary one in this whole table.** It reads as "tried, does not
work" and it stopped anyone retrying for months, when the mechanism was right and one
line was wrong. The archived file even documents its own spin in a comment. The lesson is
narrower than "retry failed things": a negative result is only as broad as what was
actually varied, and what was varied there was *who clocks the bytes* -- never *whether
the CPU is free while they are clocked*. Record what a failure tested, not just that it
failed.

**The 1024-byte buffer is the one to not retry.** The reclaimed RAM was counted
correctly -- 320 from the stack, 256 from the union, 644 already free -- but a doubling
buys `jtag_in` and `jtag_out` together, so it costs 1024. Working back from the 85%
ceiling: non-union `.bss` is 1144 and the stack 704, so the union caps near 1634 bytes,
a JTAG pair of about **2 x 817**. The only route left is dropping the receive buffer for
writes, which `spi_send()` now permits.

**The bisect failure is a trap worth naming.** `boot_to_dfu()` is this project's
addition and is absent from stock `v1.1.1`, so checking out an old commit removes the
method needed to flash it. Every point failed with `AttributeError` before touching the
board, and the run left it in the bootloader. Fixed by checking out `firmware/` only,
which also keeps the host library constant and removes it as a variable.

**Two measurement errors, same root cause.** The 3.93 us/byte and 1.11 us/byte figures
that drove decisions for a long time are the same artifact measured twice: dividing a
whole `SCAN` call by its payload charges a fixed ~215 us round trip to per-byte
clocking, inflating it about 5x. Marginal-cost differencing gives 0.663-0.715 us/byte
against a 0.667 wire floor.

**And a comparison error of mine, for completeness.** I timed new firmware at 890 ms
against a recorded 1680 ms and reported 1.89x. Different bitstreams. Same payload it was
846 -> 774 ms, or 1.09x. That is what the fixed-payload benchmark exists to prevent, and
it happened before the benchmark existed.

## Why not bypass Apollo entirely? Because the ECP5 cannot load itself

Folded in from `fast-bitstream-loading.md`, now retired to `debris/docs/`. It is here
rather than filed away because the audience for a configure-speed document is exactly
the audience tempted to propose an FPGA-side loader, and these are hard negatives with
primary sources -- expensive to establish, cheap to re-propose.

Tracked as **[#108](https://github.com/awtoau/cynthion-workspace/issues/108)**.

## 1. The ECP5 cannot reconfigure its own SRAM

This is the crux, and it is a hard negative.

**There is no ICAP-equivalent on ECP5.** The complete list of configuration and
miscellaneous primitives is `JTAGG`, `OSCG`, `SEDGA`, `DTR`, `USRMCLK`, `GSR` --
verified from three independent sources that agree exactly:
`prjtrellis/libtrellis/src/Chip.cpp:261-268`, `nextpnr/ecp5/arch.cc:1035`, and
the Yosys ECP5 blackbox library. None of them accepts configuration data.

Ruling out the near-misses one at a time:

- **`JTAGG`** is the primitive people expect to be a backdoor. It is not. Its
  only two parameters are `ER1`/`ER2` (`nextpnr/ecp5/bitstream.cc:1517-1521`),
  which are user-defined shift registers selected when an *external* JTAG master
  shifts their instruction into the IR. Its `TCK`/`TMS`/`TDI` ports are marked
  `iopad_external_pin` -- bonded to the pads, not drivable from fabric. The
  configuration opcodes (`ISC_ENABLE`, `LSC_BITSTREAM_BURST`, ...) are decoded by
  hard logic behind the TAP IR, and fabric never sees the IR.
- **`USRMCLK`** drives the MCLK *pin*, for a soft SPI master talking to the
  external flash. It is a path to flash, not to the configuration engine.
- **`SEDGA`** reads configuration memory for CRC checking. Read-only.
- **`PCNTR`**, which on MachXO2/XO3 *does* give fabric a limited poke at the
  config engine, **does not exist on ECP5** --
  `prjtrellis/libtrellis/src/Chip.cpp:472` instantiates it only in the MachXO2
  branch. This is the most telling result: Lattice documents such a mechanism
  where it exists, and for ECP5 it does not.

**`--background` does not do what the issue assumes.** It sets three CRAM bits in
tile `EFB0_PICB0` plus CR0 bits 29/27/26/25 (`ecppack.cpp:158-165`,
`Bitstream.cpp:34,917-921`). Its effect is to keep I/O pins live during a
reconfiguration driven by an *external* master, enabling partial reconfiguration
over JTAG. Project Trellis states the mechanism explicitly: partial bitstreams
require `BACKGROUND_RECONFIG`, and then "instructions 0x79 ... and 0x74 ... must
be sent **over JTAG**". Lattice FPGA-TN-02039 section 6.2 is blunter still: "When
the ECP5 ... devices are in Background mode, **only read type commands are
supported**."

**Reconfiguration cannot even be *triggered* from fabric.** FPGA-TN-02039
section 5.5 lists exactly three exits from user mode: PROGRAMN asserted, a
REFRESH command on a configuration port, or power cycling. PROGRAMN is a
dedicated **input** (ECP5 datasheet FPGA-DS-02012) with no output driver and no
fabric routing. REFRESH is accepted "only [on] the JTAG port and the Slave SPI
port", both requiring an external master. `BOOTADDR` is CRAM fixed at pack time
(`ecppack.cpp:167-194`), not a runtime register.

So the only route from user logic is: write the image to SPI flash with a soft
SPI master, then have *something external* trigger a reboot. The running design
does not survive it. That is precisely what Apollo's existing
`FlashBridge`/`flash-fast` already does -- and it is why that code writes flash
rather than SRAM. The issue's own table rules that route out on erase/program
time.

## 2. The FPGA-side transfer really is ~6 ms -- and it does not matter

`gateware/probes/loader/bitstream_sink.py` is the receiving half of the proposed
loader: a bulk OUT endpoint on TARGET-C that counts bytes and never
back-pressures. It builds (110 MHz against a 60 MHz constraint), loads, and
enumerates at `1209:000e`. Pushing a 304726-byte bitstream at it:

    wrote 304726 bytes in 7.89 ms (38.6 MB/s, 309 Mbps)

**The issue's ~6 ms estimate was essentially correct.** This is the strongest
single piece of evidence in the investigation, and it cuts against the issue's
own conclusion: the leg that was assumed to be the expensive one is ~350x
cheaper than the path in use, and it was never the problem. Even if the ECP5
*could* configure itself from this data -- it cannot -- adopting this transport
would remove 7.89 ms from a 2746 ms operation.

The same image over the JTAG path takes 2746 ms. The difference is entirely
which chip the bytes pass through.

## 3. Why the JTAG path is actually slow

The issue attributes ~3.0 s to "JTAG clock". The code and the measurements both
disagree.

`ECP5_JTAGProgrammer.configure` sends the whole image in a single
`LSC_BITSTREAM_BURST` data scan (`ecp5.py:457`). But `JTAGChain._scan_data`
splits that scan into `max_bits_per_scan` chunks (`jtag.py:306-321`), and the
SAMD11's staging buffer is 256 bytes (`firmware/src/jtag.c:30-31`). Each chunk
costs **two USB control transfers**: `SET_OUT_BUFFER` carrying the data, then
`SCAN` telling the MCU to clock it out.

Decomposing those two transfers by varying payload size and bit count
independently:

| transfer | 0-8 B / 8 bits | 256 B / 2048 bits | marginal |
|---|---|---|---|
| `SET_OUT_BUFFER` (data in, no JTAG work) | 167 us | 846 us | **2.53 us/byte** |
| `SCAN` (no payload, pure JTAG work) | 138 us | 497 us | **1.40 us/byte** |

Fitting `SET_OUT_BUFFER`: **204 us fixed + 2.53 us/byte** (a second run gave
195 us + 2.51 us/byte; the figures below use the first).

The decisive comparison is the two marginal columns. **Getting a byte into the
SAMD11 costs nearly twice what clocking it onto the JTAG wire costs.** The wire
is not the bottleneck; the pipe feeding it is. The SAMD11 clocks JTAG over
hardware SPI at `baud_divider=1` (`boards/cynthion_d21/jtag.c:25`), which is fast
enough that it spends most of its time waiting for data.

### The ceiling nobody mentioned

The Apollo debug controller enumerates at **12 Mbps full-speed** with a 64-byte
EP0 (`/sys/bus/usb/devices/.../speed` = 12; `CFG_TUD_ENDPOINT0_SIZE 64`).

This is the single most important fact for issue [#100](https://github.com/awtoau/cynthion-workspace/issues/100), because the 388 Mbps
figure the issue reasons from is the throughput of the **FPGA's** USB PHY, on a
different port and a different chip. The bitstream does not travel that path. It
travels the SAMD11's full-speed link, whose raw ceiling is 1.5 MB/s.

At the measured 2.53 us/byte the control path runs at 0.40 MB/s -- **26% of the
full-speed wire**. That inefficiency is the opportunity.

| bound | time for a 304726 B image |
|---|---|
| measured today (end to end) | **2746 ms** |
| SAMD11 ingest at measured 0.40 MB/s | 771 ms |
| JTAG clocking alone at 1.40 us/byte | 427 ms |
| 100% of full-speed wire | **203 ms** -- floor for *any* SAMD11 path |
| issue [#100](https://github.com/awtoau/cynthion-workspace/issues/100)'s target | 6 ms -- **not reachable through this MCU** |

Note the end-to-end 2746 ms exceeds the 771 ms ingest figure, because the
per-transfer fixed cost (204 us x 1191 chunks) and the `SCAN` transfers add on
top.

## Getting back to a clean state between runs

Merged from `apollo-configure-speed.md`, which this document replaced. Earned during
that work and still true.

A configured FPGA drives the shared lines and will not re-enter ISC, so **every
configure must start from a reset FPGA**. Back-to-back configures otherwise fail with
"Failed to enter ISC" and an all-zero status. Pre-existing behaviour rather than
anything these changes caused, but it produces confusing failures when benchmarking in
a loop and cost real time.

`verify_configure.py` therefore issues three requests before each run:

| request | why |
|---|---|
| `0xec` `EMERGENCY_RESET` | If a previous run left a JTAG session open, Apollo stays latched in `MODE_JTAG_PROGRAMMING` and refuses control-plane requests, surfacing as a pipe error. This request is permitted in that state precisely to break the deadlock. |
| `0xbe` `JTAG_STOP` | Closes the dangling session. |
| `0xc0` `TRIGGER_RECONFIGURATION` | Pulses PROGRAMN, which is what actually resets the FPGA. |

`force_fpga_offline()` is **not** usable for this: once the FPGA is already offline the
request is refused with a pipe error.

Two recovery notes, both exercised:

- After a successful configure the FPGA may take over the USB port, so the next
  operation can fail with "No such device". The device is fine -- re-run it.
- If Apollo ends up in the Saturn-V bootloader, `fwup-util` may not find it while
  `dfu-util` will: `dfu-util -d 1d50:615c -a 0 -D <firmware.bin> -R`. Since this work
  there is also `apollo enter-dfu` for the outbound direction and
  `scripts/apollo_reflash.py` for the whole cycle.

## Repository state worth knowing

`debris/code/spi-dma-cynthion-d11.c` was recovered from a lost worktree and is now on
main, which is the only reason the DMA work could be resumed rather than rewritten.

`repos/apollo` sits on a **detached HEAD** at `d43f765` -- normal for a submodule, but the
parent repo's pointer has not been moved, so the DMA commit is reachable only from the
submodule's own reflog until that is decided. It is over budget, so moving the pointer
would put an unshippable firmware on main; that is the reason to leave it, not an
oversight.


---

## usb-host-options.md: Parts I-III as first written (issues #101, #105)

### Source: docs/usb-host-options.md

# USB host mode on Cynthion at 480 Mbps — the options

**Status:** proposal, no hardware exercised
**Date:** 2026-07-30T19:30:00+10:00; integration design added 2026-08-03T01:30:00+10:00
**Consolidates:** awtoau/cynthion-workspace#101, [#105](https://github.com/awtoau/cynthion-workspace/issues/105)
**Target:** LFE5U-12F (24288 LUT, 56 BRAM), Cynthion r1.4

Sections 0-10 are the original feasibility study: can this board host, and does
anything exist to build on. **Section 11 onwards is the integration design** —
what it takes to attach a host engine to *this* SoC, and it corrects five things
sections 0-10 got wrong. Read section 11.1 before trusting anything above it.

**If the question is Linux, this is not the document.** A 480 Mbps host with a
bespoke register interface has no Linux driver, so taking it means writing an HCD.
[`linux-on-cynthion.md`](../linux-on-cynthion/ANALYSIS.md) carries that chain end to end and
picks the full-speed OHCI route for exactly that reason; its Part II is the
full-speed investigation in detail. This document remains the answer to [#105](https://github.com/awtoau/cynthion-workspace/issues/105)'s
question, which was 480 Mbps.

---

## 0. Executive summary

Four findings drive everything below.

1. **LUNA has no host stack.** Not a partial one — the USB 2.0 protocol layer is
   structurally device-only. The ULPI/PHY layer, however, already exposes every
   host control signal it would need.
2. **The board can host.** Cynthion r1.4 can source VBUS to TARGET A and has
   current/voltage monitoring plus Type-C CC control. This is not a board blocker.
3. **A working LUNA-based host exists in another project** — Tiliqua's
   `usb_host.py` (merged, 691 lines) — but it is **full-speed (12 Mbps) only**,
   and a GSG maintainer has pointed at it as *the* starting point for Cynthion.
4. **480 Mbps is where the cost concentrates.** Getting to 12 Mbps host is a
   port-and-adapt job. Getting from 12 to 480 Mbps requires the host side of the
   high-speed chirp handshake, which nobody in this ecosystem has written.

The distance from "no host" to "12 Mbps host" is far shorter than the distance
from "12 Mbps host" to "480 Mbps host". Recommendation is in section 6.

---

## 1. What LUNA has today

### 1.1 It is device-only, by construction

`repos/luna/luna/gateware/usb/usb2/` contains `device.py`, `control.py`,
`endpoint.py`, `request.py`, `transfer.py`, `descriptor.py`, `reset.py`,
`packet.py`. There is no `host.py`, no root-hub model, no SOF generator, no
transaction scheduler, no pipe management, no enumeration driver.

The decisive line is `luna/gateware/usb/usb2/device.py:382`:

```python
# Disable our host-mode pulldowns; as we're a device.
self.utmi.dm_pulldown.eq(0),
self.utmi.dp_pulldown.eq(0),
```

The device stack does not merely omit host support; it actively drives the
host-mode pulldowns off. Host mode is a deliberate non-goal of `USBDevice`.

### 1.2 The asymmetry is in the packet layer

`packet.py` names look symmetric but are not. Each block is one direction only:

| Block | Role | Reusable for host? |
|---|---|---|
| `USBTokenDetector` | **receives** SETUP/IN/OUT/SOF tokens | No — host must *generate* tokens |
| `USBHandshakeDetector` | receives ACK/NAK/STALL | Yes, directly |
| `USBHandshakeGenerator` | sends ACK/NAK/STALL as a device | Partly — host only ever sends ACK |
| `USBDataPacketReceiver` / `Deserializer` | receives DATA0/1 | Yes, directly |
| `USBDataPacketGenerator` | sends DATA0/1, does CRC-16 | Yes, directly |
| `USBDataPacketCRC` | CRC-16 both directions | Yes, directly |
| `USBInterpacketTimer` | bus turnaround timing | Yes — already has HS/FS/LS constants |

So roughly the lower half of the packet layer is direction-agnostic and reusable.
The missing pieces are a **token generator** and everything above it.

`USBInterpacketTimer` is worth calling out as a genuine asset: it already encodes
HS, FS and LS rx-to-tx delays and tx-to-rx timeouts from the USB 2.0 and ULPI 1.1
specs (`_HS_RX_TO_TX_DELAY`, `_HS_TX_TO_RX_TIMEOUT = 92` ULPI cycles, etc.).
Bus turnaround timing at high speed is a classic source of silent failure, and it
is already done and in use.

### 1.3 The HS chirp handshake is device-side only

`reset.py`'s `USBResetSequencer` implements the **device** half of the high-speed
detection handshake: states `DEVICE_CHIRP` (drive chirp K ~2 ms), then
`AWAIT_HOST_K` / `IN_HOST_K` / `AWAIT_HOST_J` / `IN_HOST_J`, counting three valid
K/J pairs before declaring `IS_HIGH_SPEED`.

The host half is the mirror image and **does not exist**: assert SE0, watch for a
device chirp K, then drive at least three alternating K/J pairs, then switch to HS
terminations. This is the single most important gap for a 480 Mbps host, and it is
discussed in section 3.2.

### 1.4 The ULPI layer, by contrast, is already host-capable

`luna/gateware/interface/ulpi.py` exposes the full UTMI+ control surface, and the
docstrings explicitly anticipate host use:

```
I: dp_pulldown -- when set, enables a 15kR pull-down on D+; intended for host mode
I: dm_pulldown -- when set, enables a 15kR pull-down on D+; intended for host mode
```

Also present: `op_mode` (including `RAW_DRIVE` for direct bus driving),
`xcvr_select`, `term_select`, `chrg_vbus` / `dischrg_vbus`, and the OTG control
register (0x0A) written with `id_pullup`, `dp_pulldown`, `dm_pulldown`,
`dischrg_vbus`, `chrg_vbus`, `use_external_vbus_indicator`.

**Conclusion for section 1:** the PHY abstraction needs no work for host mode. The
protocol layer above it needs to be written. This is a favourable split — the
fiddly, timing-critical, vendor-specific layer is the part already done.

---

## 2. What the board supports

From `repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`.

### 2.1 Three full ULPI PHYs

`control_phy`, `aux_phy`, `target_phy` are each declared as a complete
`ULPIResource` — 8-bit data, clk (FPGA-driven, `clk_dir='o'`), dir, nxt, stp,
rst. All three are USB 2.0 HS-capable PHYs with bidirectional ULPI. Nothing about
the ULPI wiring restricts direction of the USB role; ULPI `dir` is bus turnaround,
not host/device role.

### 2.2 VBUS sourcing exists — this is the board fact that matters

```python
# VBUS on each of the Type-C ports can be connected to TARGET A through
# a bidirectional switch. If any of these switches is enabled, TARGET A
# is considered an output.
Resource("target_c_vbus_en",   0, Pins("K5", ...))
Resource("control_vbus_en",    0, Pins("L1", ...))
Resource("aux_vbus_en",        0, Pins("L2", ...))
Resource("target_a_discharge", 0, Pins("K4", ...))
```

Plus `control_vbus_in_en` / `aux_vbus_in_en` for input shutoff.

A host must source VBUS to the device it hosts. Cynthion can: VBUS from CONTROL or
AUX can be switched through to TARGET A, and TARGET A can be actively discharged.
`target_a_discharge` matters more than it looks — safe VBUS sequencing on
disconnect needs it, and it is present.

Supporting infrastructure is also there:

- `power_monitor` — I2C voltage/current monitor, so overcurrent detection and the
  spec-required port power management are achievable.
- `target_type_c` / `aux_type_c` — I2C Type-C controllers with `int` and `fault`
  pins, giving CC line control (Rp/Rd role advertisement) and fault reporting.

### 2.3 No ID pin, and none needed

There is no mini/micro-B `ID` pin, because r1.4 is Type-C. Role is established by
CC line resistors via the Type-C controllers, not by an ID pin. The ULPI
`id_pullup` bit exists in the OTG register but is irrelevant here. This is a
non-issue, noted only because "check the ID pin" was raised as a question.

### 2.4 Board verdict

**The board can act as a USB host.** VBUS sourcing, discharge, current monitoring,
CC role control and an HS-capable PHY are all present on TARGET. There is no
hardware blocker. Everything remaining is gateware plus firmware.

Caveat not verified here: whether the VBUS switch path and TARGET A current limit
meet the 500 mA / 900 mA a spec-compliant host should offer. That is a schematic
and datasheet question, and no hardware was exercised for this proposal. For
hosting a single low-power device it is very unlikely to be a problem.

---

## 3. Prior art — the finding that changes the answer

Section 1 concluded that LUNA has no host stack. That would normally imply a
large from-scratch effort. It does not, because the work has substantially been
done outside LUNA.

### 3.1 GSG has not declined host mode — they have deferred it

`greatscottgadgets/cynthion#230`, "Gateware host mode implementation?", is closed,
but not as a refusal. martinling (GSG member) replied:

> There is a working example of host mode gateware using LUNA
> [in the Tiliqua project](https://github.com/apfaudio/tiliqua/pull/65), which you
> could use as a starting point. At some point we'd like to bring more of that
> functionality into LUNA and add an example for doing this with Cynthion, but
> we're not there yet.

So upstream considers host mode desirable, unimplemented in LUNA, and already
demonstrated elsewhere. `cynthion#174` ("Can Cynthion emulate a host?") was closed
with the same substance: technically possible, no support provided.

### 3.2 `apfaudio/guh` — a USB2 **high-speed** host engine for LUNA

Tiliqua PR [#65](https://github.com/awtoau/cynthion-workspace/issues/65) (merged 2024-11-05) was the full-speed-only ancestor. That code has
since been extracted and substantially extended into a standalone library,
**`apfaudio/guh`** ("Gateware USB Host"), by the same author. This is the single
most important artifact for this proposal.

From its README:

> `guh` is an experimental gateware library (written in Amaranth HDL), for
> building **custom USB2 high-speed and full-speed host engines** for FPGAs. It
> builds heavily on LUNA, which, whilst being extremely useful for implementing
> USB devices, does not implement USB Host. Eventually (perhaps after a lot of
> cleanup!) `guh` hopes to become part of LUNA in some form.

> As of now, `guh` can enumerate USB2 high-speed (480Mbit) and full-speed (12Mbit)
> devices in pure gateware. [...] I have tested this on USB thumbdrives at 480Mbit
> HS, and MIDI/HID devices at 12Mbit FS.
>
> The enumeration speed is dynamic: the same gateware can support both HS and FS
> devices. As of now, USB hubs can be enumerated *but not the devices behind them*.

Structure (all Amaranth, BSD-3-Clause — the same licence as LUNA):

| Module | Lines | Role |
|---|---|---|
| `guh/usbh/sie.py` | 673 | transaction engine: token packets, SOF generation, SETUP/IN/OUT |
| `guh/usbh/enumerator.py` | 479 | reset, address assignment, descriptor fetch, handoff |
| `guh/usbh/descriptor.py` | 224 | descriptor parsing, endpoint extraction |
| `guh/usbh/reset.py` | 215 | bus reset + **HS/FS speed detection (host-side chirp)** |
| `guh/engines/msc.py` | 743 | mass-storage engine (read-only), runs at HS |
| `guh/engines/keyboard.py` | 163 | HID keyboard engine |
| `guh/engines/midi.py` | 133 | MIDI engine |

Critically, `guh/usbh/reset.py` implements exactly the gap identified in section
1.3 — the **host side** of the high-speed chirp handshake. Its states are
`WAIT-DEVICE-CHIRP-END`, `WAIT-DEVICE-CHIRP-END-SE0`, `SEND-HOST-CHIRP-K`,
`SEND-HOST-CHIRP-J`, alternating K/J and falling back to `USBHostSpeed.FULL` if no
device chirp arrives. This is the hard part of a 480 Mbps host, and it is written.

### 3.3 It already targets Cynthion

This is not a port that needs inventing. GUH's own CI (`.github/workflows/ci.yml`)
builds every example for **both** Tiliqua and Cynthion:

```yaml
platform:
  - name: tiliqua
    env: guh.platform.tiliqua:TiliquaR4R5Platform
  - name: cynthion
    env: cynthion.gateware.platform:CynthionPlatformRev1D4
example: [midi_host, keyboard_host, msc_host]
```

The examples request `control_vbus_en` and `target_phy` — precisely the r1.4
resources confirmed in section 2 — and even carry Cynthion-specific handling
(`# Cynthion has tristate UART TX`). The README documents the Cynthion upload
path via `apollo force-offline`.

Caveat from the README, quoted because it matters for VBUS: the examples
"hard-wire the VBUS output to ON, because this repository does not include drivers
for the I2C Type-C CC controller (TUSB322I)". Proper VBUS/CC handling exists in the
full Tiliqua repo, not in GUH. On Cynthion this is a gap to close (see section 5).

### 3.4 Other prior art, briefly

- **daisho** (GSG): archived since 2015, USB 3.0 SuperSpeed on a different FPGA
  platform. Not applicable.
- **Glasgow**: no USB host gateware found.
- Forks/derivatives of LUNA (`delan/sol_usb`, `hansfbaier/liteusb`,
  `jevinskie/liteluna`, `lambdaconcept/lambdalib`) all carry the same device-only
  `dp_pulldown.eq(0)` line. None implement host.
- `tomverbeure/usb_system`, `Nickster90s/avb-usb-host`: ULPI/UTMI plumbing only.

**GUH is the only credible base, and it is a strong one.**

---

## 4. Measured fabric cost

Not estimated — synthesised. `scripts/usb-host-area.py` builds the GUH examples
for `CynthionPlatformRev1D4` with the workspace toolchain (Yosys 0.65,
nextpnr-ecp5 0.10) and parses nextpnr's utilisation and timing report. Synthesis
and place-and-route only; **no board was touched**.

| Example | Speed | LUT (of 24288) | BRAM | fmax vs 60 MHz |
|---|---|---|---|---|
| `keyboard_host` | FS (HID) | **2184 (8%)** | 0 | 127.57 MHz PASS |
| `midi_host` | FS (MIDI) | **2244 (9%)** | 0 | 150.44 MHz PASS |
| `msc_host` | **HS, 480 Mbps** | **4103 (16%)** | 1 | 117.45 MHz PASS |

Raw data: `tmp/host-research/area-results.json`; log: `tmp/logs/usb-host-area.log`.

Observations that matter:

- **A complete 480 Mbps USB host fits in 16% of the LFE5U-12F.** That includes the
  ULPI PHY interface, transaction engine, chirp/speed detection, enumerator,
  descriptor parser, and a mass-storage class engine with its hexdump and UART.
- The bare host core is smaller than 4103 LUT. `msc_host` carries the MSC engine
  (743 lines, the largest module) plus SCSI/CBW-CSW handling. A host core plus a
  thin transfer interface should land nearer 2500-3000 LUT, judging by the FS
  examples at ~2200 LUT including their own class engines and lookup tables.
- **Timing closes with roughly 2x margin** on the 60 MHz ULPI domain. High-speed
  host operation is not marginal on this part.
- BRAM use is negligible (0-1 of 56), which is the pleasant surprise: BRAM, not
  LUTs, is usually the binding constraint when adding a CPU.

### 4.1 Does it fit alongside a CPU?

Workspace figures for comparison (`docs/gateware-architecture-plan.md`): best CPU
config **6342 LUT and 14 of 56 BRAM**; current test bitstreams 4000-7400 LUT.

| Combination | LUT | % of 24288 | BRAM |
|---|---|---|---|
| HS host engine (as `msc_host`) | 4103 | 17% | 1 |
| RISC-V CPU (best measured) | 6342 | 26% | 14 |
| **Host + CPU** | **~10400** | **~43%** | **~15 / 56** |

That leaves roughly 57% of the fabric and 41 BRAM free. **A 480 Mbps host and a
RISC-V CPU coexist comfortably.** Adding the existing *device* stack on a second
PHY as well (for a host-plus-device or MITM configuration) would still be
plausible, though that combination has not been synthesised here and should be
measured before being promised.

The fabric budget, which was the stated worry, is not the constraint.

---

## 5. What is still missing

GUH is not a finished product, and its author says so plainly ("experimental",
"quite the collection of (functional) hacks", "all interfaces are subject to
change"). Gaps, in rough priority order for Cynthion:

1. **VBUS and Type-C CC control.** GUH's examples hard-wire `control_vbus_en` on
   and ship no TUSB322I driver. Cynthion r1.4 has I2C Type-C controllers on
   TARGET and AUX plus a power monitor; driving them properly (role advertisement,
   overcurrent via `fault`, `target_a_discharge` on teardown) is Cynthion-specific
   work nobody has done. This is the largest genuinely new piece.
2. **No hub support past the first tier.** Hubs enumerate; devices behind them do
   not. A real host needs hub port control and address routing.
3. **Error handling is incomplete.** The README recommends a watchdog to reset the
   enumeration FSM when it stalls. `usb_host.py`'s ancestor carried
   `# FIXME: tolerate rx timeout`. Babble, STALL recovery, retry limits and
   data-toggle resynchronisation all need hardening.
4. **Only three class engines exist** (MSC read-only, HID keyboard, MIDI). Any
   other device class is new work.
5. **No CPU-facing controller.** For software-driven hosting you need a register
   or DMA interface. GUH has an experimental `guh/periph/` Wishbone/CSR peripheral
   with a Rust HAL in `rs/`, requiring `amaranth_soc` — promising but unproven
   here, and not yet integrated with this workspace's SoC.
6. **Split transactions are absent**, so FS/LS devices behind an HS hub will not
   work. Follows from (2).
7. **It is one person's experimental library**, explicitly not accepting
   contributions, with no releases. Depending on it is a real supply-chain
   consideration; vendoring is the mitigation.

Verification status also deserves precision: 27 of GUH's tests pass locally,
including a full host-vs-simulated-device integration test that asserts
`detected_speed == USBHostSpeed.HIGH` for both the MIDI and MSC engines. That is
meaningful evidence the HS path works in simulation. **It is not a hardware
result** — none was produced for this proposal.

---

## 6. Three options

### Option A — Vendor GUH and drive it from gateware
Bring GUH in under `sources/` or as a submodule, build the `msc_host` /
`keyboard_host` examples for r1.4, then add the TUSB322I/VBUS driver and a
watchdog. Host behaviour stays in gateware; a class engine streams to UART, the
CPU, or PSRAM.

- **Cost:** small. The bitstreams already build. Realistically days, not weeks,
  to a demonstrated 480 Mbps enumeration, plus the VBUS/CC work.
- **Area:** ~4100 LUT measured, 1 BRAM. Fits with the CPU.
- **Risk:** low technically. Main risks are the VBUS/CC gap and depending on an
  experimental upstream.
- **Ceiling:** limited to implemented class engines; no hubs, no arbitrary
  software-defined transfers.

### Option B — GUH plus a CPU-visible host controller
Option A, then expose GUH's SIE as a CSR/DMA peripheral so firmware (Rust on the
RISC-V core, reusing moondancer patterns) issues transfers. GUH's `guh/periph/`
and `rs/` are the starting point.

- **Cost:** moderate. Peripheral bring-up, DMA, firmware transfer scheduling,
  descriptor handling in software rather than gateware FSMs.
- **Area:** ~10400 LUT combined (host + CPU), ~15 BRAM. Comfortable.
- **Risk:** medium. Requires `amaranth_soc` integration and is the least proven
  part of GUH.
- **Payoff:** genuine generality — arbitrary devices and classes without new
  gateware per class. This is what "a USB host on Cynthion" usually means.

### Option C — Zephyr's USB host stack over a gateware UHC driver
Run Zephyr on the RISC-V core and write a UHC driver whose backend is GUH's SIE,
inheriting Zephyr's enumeration, hub and class drivers.

- **Cost:** large. See section 7.
- **Area:** Option B's fabric plus a CPU able to run Zephyr, with materially more
  BRAM/external memory for the stack and its buffers.
- **Risk:** high.

---

## 7. Verdict on the Zephyr hypothesis

The hypothesis from [#105](https://github.com/awtoau/cynthion-workspace/issues/105) was that the Zephyr USB layer is the easiest route. On
the evidence it is **not the easiest route, but the mismatch is not fatal either.**
It is the right destination if the goal is a general-purpose host, and the wrong
starting point.

**The mismatch is real and specific.** Zephyr's host support sits behind the UHC
API (`include/zephyr/drivers/usb/uhc.h`, ~620 lines). Every in-tree UHC driver
binds to a *hardware* host controller:

```
uhc_dwc2.c   uhc_max3421e.c   uhc_mcux_ehci.c   uhc_mcux_ohci.c
uhc_mcux_ip3516hs.c   uhc_mcux_khci.c   uhc_virtual.c
```

There is no "controller in fabric" driver. `uhc_virtual.c` is a loopback onto
Zephyr's virtual USB bus for host-stack testing under `native_sim` — it is not a
bridge to gateware.

**Why it is surmountable.** The UHC API is small and well-shaped for this:
`uhc_init/enable`, `uhc_bus_reset`, `uhc_sof_enable`, `uhc_bus_suspend/resume`,
`uhc_ep_enqueue/dequeue`, plus an event callback. Those map almost one-to-one onto
what GUH already provides — bus reset with chirp, SOF generation, and SETUP/IN/OUT
transactions on an endpoint. Writing `uhc_cynthion.c` against a GUH-backed
peripheral is a legible project, not a research problem. And the payoff is exactly
the list of gaps in section 5: Zephyr brings hub support, class drivers, error
recovery and transfer scheduling that GUH lacks.

**Why it should not be first.** It front-loads the costs:

- Zephyr must be brought up on the VexiiRiscv core first — board port, device
  tree, timers, interrupts, console — none of which is USB work, and none of
  which exists in this workspace today.
- The RAM footprint of Zephyr plus its USB host stack is a different memory
  problem from the current bare-metal firmware, on a part with 56 BRAM.
- You still need Option B's CPU-visible host controller as the driver's substrate.
  **Zephyr does not remove any gateware work; it adds a software layer on top of
  it.**
- Debugging spans three layers at once (gateware SIE, peripheral, Zephyr driver)
  with no known-good reference for the bottom two.

So the ordering is the actual finding: Zephyr is a **superset** of Option B, not
an alternative to it. Its stack cannot help until a controller exists for it to
drive, and building that controller is Option B. Treat Zephyr as a later phase
that Option B is deliberately designed to enable — worth doing if the goal is
hosting arbitrary devices, and pointless as a shortcut to first light.

One correction to the framing in [#101](https://github.com/awtoau/cynthion-workspace/issues/101), which proposed adapting
"zephyr/freebsd/openbsd/linux code [...] but written in rust" and asked whether
xHCI-style registers are needed. Porting an OS host stack is the Option C tail,
not the head, and xHCI is the wrong model: GUH's author addresses this directly,
using "host **engine**" rather than "host controller" precisely because
"modern USB host controllers like xHCI are complex beasts with sophisticated
control and DMA interfaces". Do not implement xHCI. A purpose-built transfer
interface is far cheaper and entirely sufficient.

---

## 8. Recommendation

**Do it, via Option A now and Option B next. Do not start with Zephyr.**

This is worth doing specifically *because* the expensive parts already exist. The
question in [#101](https://github.com/awtoau/cynthion-workspace/issues/101)/#105 was framed as "can Cynthion host at 480 Mbps and what would
it take", with an implicit fear that the answer was a from-scratch host stack that
would not fit. Both halves of that fear are unfounded:

- The board can host: VBUS switching to TARGET A, discharge, current monitoring
  and CC control are all present.
- A 480 Mbps host engine exists, is BSD-3 licensed, already builds for
  `CynthionPlatformRev1D4`, and measures **4103 LUT (16%) and 1 BRAM** with 2x
  timing margin — leaving ample room for the RISC-V CPU.

Suggested sequence:

1. Vendor GUH (submodule or `sources/`), pin the commit, run its test suite in
   `scripts/check.py`. 27 host tests pass today.
2. Build `msc_host` and `keyboard_host` for r1.4 and bring up on hardware. First
   milestone: enumerate a thumbdrive at high speed and hexdump block 0.
3. Write the TUSB322I/VBUS/CC driver and a watchdog — the real Cynthion-specific
   gap, and a candidate to contribute back.
4. Only then decide between staying gateware-side (A) or exposing a CPU-facing
   controller (B). Revisit Zephyr once B works, if hubs and arbitrary classes are
   actually needed.

Track area at every step against the 24288 LUT / 56 BRAM budget with
`scripts/usb-host-area.py`.

### 8.1 What would make this not worth doing

Stated plainly, since a "buy a different device" conclusion was invited. Walk away
if any of these describe the need:

- **You need a spec-compliant, general-purpose host.** Multi-tier hubs, split
  transactions, isochronous audio/video, full error recovery, power negotiation.
  That is a multi-month effort and the wrong use of an LFE5U-12F. A Raspberry Pi
  or any SoC with a real EHCI/xHCI controller does it today for a few dollars.
- **You need USB 3.x SuperSpeed.** Cynthion's USB3 support is device-side and
  incomplete; a 5 Gbps host is out of scope for this part. Daisho was GSG's
  attempt at SuperSpeed and has been archived since 2015.
- **You need guaranteed high-throughput streaming.** 480 Mbps line rate is not
  480 Mbps of delivered payload. Sustained bulk throughput through fabric, PSRAM
  and a 60 MHz domain needs measuring before anything depends on it.
- **You cannot accept an experimental single-maintainer dependency.** GUH has no
  releases, declines contributions, and warns its interfaces will change. If that
  is unacceptable and you also will not fork and own it, there is no cheap path.
- **The actual goal is capture, not hosting.** If the aim is observing traffic,
  Cynthion's existing analyzer already does that better, and a cheap USB hub plus
  a PC is a simpler "host" than any gateware.

The sweet spot where this *is* worth doing is narrow but real, and it is probably
the actual requirement: **hosting one known device at 480 Mbps, on hardware you
already own, with the whole stack visible and instrumentable.** Reading a
thumbdrive, driving a specific peripheral, or building a MITM that is a real host
on one port and a real device on the other. For that, Option A is cheap and the
fabric numbers say it fits.

---

## 9. Issue consolidation

[#101](https://github.com/awtoau/cynthion-workspace/issues/101) and [#105](https://github.com/awtoau/cynthion-workspace/issues/105) are the same request: [#101](https://github.com/awtoau/cynthion-workspace/issues/101) ("Create host USB device on moon?") asks
for FPGA host support and speculates about OS stacks in Rust and xHCI-style
registers; [#105](https://github.com/awtoau/cynthion-workspace/issues/105) asks to research a 480 Mbps host and proposes Zephyr. Same goal,
different guesses at the route — and this document answers both.

**Recommendation: keep [#105](https://github.com/awtoau/cynthion-workspace/issues/105) open, close [#101](https://github.com/awtoau/cynthion-workspace/issues/101) as a duplicate of [#105](https://github.com/awtoau/cynthion-workspace/issues/105).**

Reasoning: [#105](https://github.com/awtoau/cynthion-workspace/issues/105) states the measurable requirement (480 Mbps) and the hypothesis to
test, so it reads as the actionable ticket. [#101](https://github.com/awtoau/cynthion-workspace/issues/101)'s specifics are the parts this
research contradicts — xHCI is explicitly the wrong model, and porting an OS host
stack is a late phase rather than the starting point — so keeping it open would
preserve a misleading plan. Its one durable instinct ("must be easier to find luna
or other work if possible") turned out to be exactly right and is captured above.

Retitle [#105](https://github.com/awtoau/cynthion-workspace/issues/105) to something like "USB host mode at 480 Mbps via GUH" and link this
document. Neither issue was closed or edited in producing this proposal, per the
brief.

---

## 10. References

Local:
- `repos/luna/luna/gateware/usb/usb2/` — device-only stack; `device.py:382`
- `repos/luna/luna/gateware/usb/usb2/reset.py` — device-side chirp only
- `repos/luna/luna/gateware/interface/ulpi.py` — host-capable PHY controls
- `repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`
- `scripts/usb-host-area.py`, `tmp/logs/usb-host-area.log`,
  `tmp/host-research/area-results.json`
- Area baselines: `docs/gateware-architecture-plan.md`

Upstream:
- `apfaudio/guh` — https://github.com/apfaudio/guh (BSD-3-Clause)
- `apfaudio/tiliqua` PR [#65](https://github.com/awtoau/cynthion-workspace/issues/65) — the full-speed ancestor
- `greatscottgadgets/cynthion` [#230](https://github.com/awtoau/cynthion-workspace/issues/230) (GSG pointing at Tiliqua), [#174](https://github.com/awtoau/cynthion-workspace/issues/174)
- Zephyr UHC API — `include/zephyr/drivers/usb/uhc.h`, `drivers/usb/uhc/`

Note on `import luna`: it resolves to the interpreter's own
`site-packages/luna`, not `repos/luna`. The checkout was read for this analysis; the installed copy is what
the measured builds ran against. Both are device-only.

**No hardware was touched in producing this proposal. All figures are from
synthesis, place-and-route and simulation.**

---
---

# Part II — Integration design

**Date:** 2026-08-03T01:30:00+10:00
**Baseline:** `uart16550-console` at `c154bc3`
**Question:** not "can it host" (sections 0-10 answered that) but "what shape
does it take inside the SoC that now exists".

The answer is smaller than sections 6-8 assumed. **Take `guh/usbh` only — the
transaction engine and the reset/chirp controller — and write enumeration in
firmware.** That is 2080 LUT and *zero* BRAM, against a design with 12100 LUT
and 14 BRAM to spare.

---

## 11. Five corrections to Part I

| # | Part I said | Actually |
|---|---|---|
| 1 | GUH is BSD-3-Clause (§3.2) | True *now*. At our pinned commit `fbd7077` it was not: `guh/periph/msc.py`, `guh/periph/dma.py` and `guh/util/gearbox.py` carried `SPDX-License-Identifier: CERN-OHL-S-2.0` — strongly reciprocal hardware copyleft, on exactly the SoC-facing files Option B wanted. Reported upstream as `apfaudio/guh#1` (opened 2026-07-28), acknowledged as a copy-paste leftover from Tiliqua, **fixed 2026-07-30 in `923c8490`**. Bump the pin before vendoring anything. |
| 2 | "No CPU-facing controller… GUH has an experimental `guh/periph/` Wishbone/CSR peripheral" (§5.5) | `guh/periph/` is two files and both are mass-storage. Its registers are `cmd_lba`, `cmd_blocks`, `cmd_dir`, `capacity`, `block_size` — a block device, not a transfer interface. There is no way to issue a control transfer or reach an arbitrary endpoint from the CPU. The README calls a generic controller a non-goal. **Option B is new work, not a port.** |
| 3 | VBUS is "the largest genuinely new piece"; GUH will "not get this right for Cynthion" (§5.1) | GUH's examples request `control_vbus_en` and drive it high. On r1.4 that is the correct pin: `control_vbus_en` (L1), `aux_vbus_en` (L2) and `target_c_vbus_en` (K5) each switch *that port's* VBUS through to TARGET-A, and any one of them makes TARGET-A a source. GUH sources from CONTROL, which is the port the PC is on. Nothing to fix. |
| 4 | Write "the TUSB322I/VBUS/CC driver" (§8) | Cynthion r1.4 has **FUSB302B**, not TUSB322I, and `firmware/cynthion-soc/src/fusb302.rs` + `typec.rs` already drive both of them over the muxed I2C bus with a PLIC source each. The host-mode gap is one register field — presenting Rp on TARGET via `SWITCHES0` — not a driver. `fusb302.rs:26-38` already documents why the pull-downs are deliberately off. |
| 5 | "A complete 480 Mbps USB host fits in 16%… 4103 LUT" (§4) | That is `msc_host`, an entire example: host + SCSI engine + hexdump + UART + LUNA's clock generator. The host **core** is 2080 LUT and 0 BRAM. See §12. |

Also updated from Part I §3.1 and §7: nothing has changed upstream. LUNA has no
host commits since 2025-01-01; `greatscottgadgets/cynthion#230` and `#174` have
had no comments since 2025-05 and 2024-09. Zephyr gained `uhc_dwc2.c`
(2026-01-08, Apache-2.0) but it targets the Synopsys DWC2 register file;
`uhc_virtual.c` remains a `native_sim` loopback against Zephyr's own virtual
*device* controller, not a shim a soft controller can hide behind. **No `uhc_*`
driver for any FPGA soft core exists.** Part I §7's verdict stands and is now
better evidenced.

---

## 12. Measured: the host core alone

`scripts/usb-host-core-area.py`. Each configuration is a top level whose only
job is to keep synthesis from folding the core away: every `USBSIEInterface`
input is driven from a free-running LFSR, every output is XOR-reduced onto one
LED pin. Clocking is `SocClocks(sync_mhz=60)` — the SoC's
generator, not LUNA's — so the domains match the design this would go into. The
baseline is that scaffolding with no host in it, and is subtracted.

| Build | LUT | FF | BRAM | LUTRAM | fmax on the ULPI clock |
|---|---|---|---|---|---|
| baseline (CRG + LED) | 33 | 24 | 0 | 0 | 398.72 MHz |
| `USBSIE(fifo_depth=512)` | 2113 | 458 | **0** | 96 | 125.55 MHz PASS at 60 |
| `+ USBHostEnumerator + parser` | 2943 | 552 | **0** | 128 | 129.22 MHz PASS at 60 |

Deltas: **SIE 2080 LUT / 0 BRAM**; SIE + enumerator **2910 LUT / 0 BRAM**.
`fifo_depth=512` is not the GUH default of 64 — it is what a 512-byte
high-speed bulk packet requires, so the number is not flattered.

Raw data `tmp/host-core-area/core-area-results.json`, log
`tmp/logs/usb-host-core-area.log`. Synthesis and place-and-route only; no board
was opened.

### 12.1 The pleasant surprise is the zero

Both 512×8 FIFOs went to **LUT RAM**, not block RAM — 96 `TRELLIS_RAMW` cells
of 3036. BRAM was the binding constraint on this part (42 of 56 in use), and the
host core does not touch it.

### 12.2 Does it fit

Current SoC, `tmp/awto_soc/build/top.tim` (2026-08-03T00:25, LFE5U-12F,
CABGA256, speed 8):

| | now | + SIE | + SIE and enumerator |
|---|---|---|---|
| LUT4 (of 24288) | 12201 (50%) | ~14300 (59%) | ~15100 (62%) |
| BRAM (of 56) | 42 (75%) | 42 | 42 |
| LUT RAM (of 3036) | 159 (5%) | 255 (8%) | 287 (9%) |

Plus roughly 200-400 LUT for the CSR/FIFO shim of §14. So the SoC with a
480 Mbps host engine lands near **62-64% of the fabric with the BRAM budget
untouched**.

### 12.3 Timing is not the risk people expect

| clock | current fmax | constraint | margin |
|---|---|---|---|
| `sync` (CPU, Wishbone) | 71.58 MHz | 60 | 19% |
| `usb` (ULPI) | 94.30 MHz | 60 | 57% |
| `jtck` | 161.08 MHz | 20 | 8x |

The host core belongs in `usb`, which has three times the margin of `sync`, and
closes at 125-129 MHz standalone. `sync` is the constrained domain and it
carries the CPU — which is why §13 puts the host in `usb` and pays for a domain
crossing rather than renaming the core into `sync`.

Two caveats. Standalone fmax is not in-situ fmax: at 62% occupancy the router
has less room, and `18c1fa5` established that on this design the critical path
is dominated by routing, not logic (13.64 ns of 16.45 ns before the fix). And
placement here is stochastic — `scripts/soc_timing_sweep.py` exists because a
±9 MHz spread has been observed. **The in-situ number has to be measured, and
that is the first step in §18.**

What `18c1fa5` implies for another decoder window is favourable: the Wishbone
round trip now terminates at `RegisteredResponse`'s flip-flop
(`gateware/soc/bus/wishbone_pipe.py:96`), so a ninth subordinate adds one address
comparator and one leaf in the ACK gather to a path with ~8 ns of budget rather
than 16. The condition is that the new subordinate's own ACK must be
combinationally cheap — which a CSR multiplexer is.

---

## 13. Where it attaches, and the collision with [#120](https://github.com/awtoau/cynthion-workspace/issues/120)

`target_phy` is the only candidate: `aux_phy` carries the CDC-ACM console
(`gateware/soc/top.py`) and `control_phy` is shared with Apollo. It is also
the port the ULPI register window of [#120](https://github.com/awtoau/cynthion-workspace/issues/120) sits on, and that is a hard conflict
today, for a reason more basic than bus arbitration.

### 13.1 The conflict is ownership, not arbitration

- `platform.request("target_phy")` may be called once. `gateware/soc/top.py`
  already calls it and drives `clk`, `rst`, `stp`, `data.o`, `data.oe`
  combinationally from `UlpiRegisters`. There is no mux point.
- `ulpi_window.py:250` hard-wires `data_oe = ~dir_i`, and the window waits for
  `dir` to fall before driving. A host engine receiving a packet holds `dir`
  high, so the window's 4096-cycle (68 µs) timeout fires — a working system that
  reports a broken PHY.
- `target_phy.rst` is tied to `ResetSignal("usb")`, so a host stack cannot
  re-initialise the PHY.

This is [#125](https://github.com/awtoau/cynthion-workspace/issues/125)'s point exactly: `ULPIRegisterWindow` is an initialisation-time bus
**master** and cannot coexist with a stack that owns the PHY.

### 13.2 The fix is re-parenting, not arbitration

LUNA's `UTMITranslator` — which GUH's `USBSIE` instantiates
(`guh/usbh/sie.py:346`) — already contains a `ULPIRegisterWindow` and a
`ULPIRxEventDecoder` (`repos/luna/luna/gateware/interface/ulpi.py:805-808`). So
once the host owns the PHY there is already a register window inside it. Do not
add a second master; expose the one that is there.

There is a small, well-shaped upstream patch here:

- `UTMITranslator`'s **docstring already promises** `address`, `read_data`,
  `write_data`, `manual_read`, `manual_write` (`ulpi.py:683-687`).
- Its `__init__` (`ulpi.py:718`) **does not create them**. The ports are
  documented and absent.
- `ULPIControlTranslator` ties `register_window.read_request` to 0
  (`ulpi.py:524`) and drives the window only from an `m.If`/`m.Elif` chain over
  its own registers, with a free `m.Else()` branch at `ulpi.py:519`. That branch
  is where a manual/CPU-driven access goes.

Implementing what the docstring already claims turns `UlpiRegisters` from a
competing master into a client of the translator, and it is a candidate for
`docs/upstreamable-patches.md` in its own right — the bug is that upstream
documents an interface it does not provide.

### 13.3 And it settles [#125](https://github.com/awtoau/cynthion-workspace/issues/125)'s open question

[#125](https://github.com/awtoau/cynthion-workspace/issues/125) asks which PHY the RX CMD status peripheral should watch and who owns it.
`UTMITranslator` already decodes RX CMDs and exposes `line_state`, `vbus_valid`,
`session_valid`, `session_end`, `rx_error`, `host_disconnect`, `id_digital`
(`ulpi.py:694-697`). So the status peripheral taps the translator's outputs, is
instantiated once per translator, and needs no bus access — which is what [#125](https://github.com/awtoau/cynthion-workspace/issues/125)
argued for on first principles, now with a named attachment point.

### 13.4 A host and the analyzer can share one translator

The analyzer attaches a bare `UTMITranslator` to `target_phy` in non-driving
mode (`repos/cynthion/.../analyzer/top.py:277-278, 391-394`). The host attaches
one too. They cannot both `platform.request` the PHY — but they do not need to:
`USBSIE` exposes `self.utmi`, and `USBAnalyzer` consumes UTMI-level signals.
One translator, two consumers. That is the cheapest route to "generate traffic
and capture it in the same bitstream", and it is worth designing for now rather
than retrofitting.

---

## 14. Clocking

GUH is single-domain and says so: `guh/periph/msc.py:62` warns "this currently
assumes 'sync' and 'usb' clock domains are the same". There is no `AsyncFIFO`
anywhere in GUH.

Our SoC has `sync` and `usb` both at 60 MHz but from **different PLL outputs**
(CLKOP and CLKOS, `repos/apollo/apollo_fpga/gateware/variable_clock.py:295-330`),
so nextpnr treats them as unrelated clocks — `usb -> <async>` is already the
worst cross-domain delay in the current build at 11.83 ns.

| Option | Cost | Verdict |
|---|---|---|
| Host in `usb`, cross at the register/FIFO boundary | one four-phase handshake for control, one `AsyncFIFOBuffered` per data direction | **recommended** |
| `DomainRenamer({"usb": "sync"})` over the whole core | zero CDC; GUH's own tests do this (`tests/test_integration.py:39-40`) | adds 2100 LUT to the domain with 19% margin that also carries the CPU |

Both idioms are already in the tree and proven: the four-phase toggle handshake
at `ulpi_window.py:235-311`, and `StreamBuffer` wrapping `AsyncFIFOBuffered` at
`gateware/soc/peripherals/stream_buffer.py:96`, in use at `gateware/soc/top.py`.

Use the handshake for the transfer registers (a few crossings per transfer, so
latency is free) and the async FIFO for the byte streams (where a per-byte
handshake would be the throughput ceiling instead of the bus).

---

## 15. The software interface

**Not a descriptor ring, and emphatically not xHCI.** A transfer register plus a
FIFO port, because that is already the shape of `USBSIEInterface`
(`guh/usbh/sie.py:47-72`): set `dev_addr`/`ep_addr`/`type`/`data_pid`, strobe
`start`, poll `idle`, move bytes through two 8-bit streams. Exposing it to the
CPU is a register map over that struct, not a translation into someone else's
architecture.

### 15.1 Proposed map

A new Wishbone window, `usb_host` at **0xf0001000, size 0x100** — free, aligned,
inside the declared uncached PMA region (`base=f0000000 size=10000000 main=0`,
`gateware/soc/cpu/cpu.py:110-113`), and clear of the PLIC at 0xf0400000. It
needs a 32-bit data port, so it takes its own `decoder.add()` rather than
sitting behind the 8-bit `board` CSR decoder.

| off | name | acc | contents |
|---|---|---|---|
| 0x00 | `CTRL` | rw | `enable`, `bus_reset` (strobe), `sof_enable` |
| 0x01 | `ADDR` | rw | `dev_addr[6:0]` |
| 0x02 | `EP` | rw | `ep_addr[3:0]`, `type[1:0]` (SETUP/IN/OUT), `data_pid` |
| 0x03 | `START` | w | strobe — latches the above and runs one transaction |
| 0x04 | `STATUS` | r | `idle`, `reset_active`, `disconnected`, `speed[1:0]` |
| 0x05 | `RESP` | r | `response[2:0]`: NONE/ACK/NAK/STALL/TIMEOUT/CRC_ERROR/RX_OVERFLOW |
| 0x06-07 | `RXLEN` | r | **16-bit**, counted by the shim (see 15.2) |
| 0x08-09 | `FRAME` | r | `sof_frame[10:0]` |
| 0x0c | `FIFO8` | rw | write pushes a TX byte, read pops an RX byte |
| 0x10-13 | `FIFO32` | rw | the same FIFOs, four bytes per bus transaction |
| 0x14 | `FIFO_STATUS` | r | `tx_full`, `tx_empty`, `rx_valid`, `rx_full` |
| 0x15 | `IRQ_EN` | rw | `xfer_done`, `disconnect`, `sof` |
| 0x16 | `IRQ_PEND` | r/w1c | same bits |

PLIC source 6 (`Plic(sources=5)` → `6` at `gateware/soc/top.py`; sources 1-5
are taken, 6-31 are free). The PAC follows automatically —
`scripts/soc_generate_pac.py` reads `decoder.bus.memory_map` and
`interrupt_sources`, so `pac::base::USB_HOST` and `USB_HOST_IRQ` appear without
anything being hand-written.

### 15.2 Four traps in the GUH interface, worth writing down

- **`status.rx_len` is 8 bits** (`guh/usbh/sie.py:616`). It wraps on a 512-byte
  high-speed packet. GUH's own MSC engine ignores it and counts the stream
  itself (`guh/engines/msc.py`, `rx_byte_idx = Signal(10)`). The shim must count
  bytes drained from the RX FIFO, not forward `rx_len`.
- **Data toggle is the caller's job.** `xfer.data_pid` goes straight to the
  transmitter; the SIE never inspects a received PID and never toggles. For a
  general-purpose host that is work for firmware — and for a fuzzing or MITM
  tool it is a feature, because a deliberately wrong toggle is now expressible.
- **No completion strobe and no interrupt.** GUH is poll-only. Synthesise the
  interrupt in the shim from the rising edge of `status.idle`, latched and held
  level-high, because the PLIC has no edge detector (`vexii_plic.py:46-56`).
- **NYET is reported as ACK** (`sie.py:643-645`) and there is no PING protocol.
  High-speed bulk OUT flow control is therefore approximate; firmware sees a
  successful ACK where the device asked for a retry.

### 15.3 Throughput: what a register interface can and cannot carry

| | payload ceiling | basis |
|---|---|---|
| USB HS bulk | **53.2 MB/s** | 13 × 512 B per 125 µs microframe |
| USB FS bulk | 1.22 MB/s | 19 × 64 B per 1 ms frame |
| Wishbone, 32-bit, 60 MHz | 120 MB/s | 2 cycles/transfer after `18c1fa5` |
| `FIFO8` register port | ~4-8 MB/s (estimate) | one uncached byte access per byte; 8-15 CPU cycles each |
| `FIFO32` register port | ~10-20 MB/s (estimate) | four bytes per access, same per-access cost |

The two estimates are **not measured** — the bus cycle count is known, the
VexiiRiscv stall on an uncached `iobus` load is not. Measuring it is a firmware
loop against the CLINT tick and needs the board.

Consequence, which is the real answer to "what would the CPU drive":

- **Control transfers, enumeration, HID/interrupt endpoints, and all of
  full-speed** fit comfortably in a register interface. This covers every job in
  §17 except sustained bulk.
- **Sustained high-speed bulk does not.** A register port reaches perhaps a
  third of line rate at best.

### 15.4 Tier two, when sustained bulk is actually needed

GUH's `guh/periph/dma.py` is a general Wishbone **initiator** with `cti`/`bte`
bursts, independent of the MSC peripheral it was split out of, and BSD-3-Clause
as of `923c8490`. It would attach to our arbiter as a fourth master alongside
`cpu.ibus`/`dbus`/`iobus` and write straight into the existing 64 KiB block RAM
at 0x0.

Its cost is its staging FIFO: `_DATA_FIFO_WORDS` defaults to 8 KiB, which is
about 4 of the 14 free BRAMs, and it is a tunable. Target block RAM first —
HyperRAM is the obvious destination for large buffers but has no Wishbone
adapter ([#90](https://github.com/awtoau/cynthion-workspace/issues/90)) and an unfinished DQS path ([#92](https://github.com/awtoau/cynthion-workspace/issues/92)).

**Do not build tier two first.** It is only worth it once a measured PIO number
proves it necessary, and §15.3's estimate is not that measurement.

---

## 16. Enumeration belongs in firmware

`USBHostEnumerator` costs +830 LUT over the bare SIE and should not be taken.

- It **discards the descriptors**. The device descriptor is thrown away except
  `bMaxPacketSize0`; the configuration descriptor is streamed through a parser
  that keeps one endpoint number and one `wMaxPacketSize` and nothing else
  (`guh/usbh/enumerator.py:108, 122-129`; `guh/usbh/descriptor.py:54-68`).
- The parser is **specialised at construction time** to one interface class
  (`enumerator.py:64`), so "which driver" is a synthesis-time decision.
- Device address `0x12` and configuration `1` are **hard-coded**
  (`enumerator.py:64`, `:441-446`).

Firmware enumeration over the SIE is the same five control transfers, gives raw
descriptor bytes, handles any class, and can retry — roughly 400 lines of
`no_std` Rust against a peripheral we control, using the deferred event ring
(`firmware/cynthion-soc/src/events.rs`) so a handler records and the main loop
prints. That is the pattern the rest of this firmware already uses.

So the vendoring boundary is narrow and specific:

| From `apfaudio/guh` | Take? | Why |
|---|---|---|
| `guh/usbh/sie.py` | **yes** | the transaction engine; the thing that does not exist elsewhere |
| `guh/usbh/reset.py` | **yes** | the host half of the HS chirp; the actual hard part |
| `guh/usbh/types.py` | **yes** | shared enums, trivial |
| `guh/usbh/enumerator.py` | no | §16 |
| `guh/usbh/descriptor.py` | no | follows from the enumerator |
| `guh/engines/*` | no | fixed-function classes; firmware does this |
| `guh/periph/msc.py` | no | a block device, not a host controller |
| `guh/periph/dma.py` | later | tier two only, §15.4 |

That is ~1200 lines of Amaranth, BSD-3-Clause, pinned. Vendoring rather than
depending is what `docs/upstream-boundary.md` prescribes ("do not inherit a
stack to get one file — vendor the file"), and it is the right call twice over
here: GUH has no releases, no tags, one author, an explicit "interfaces will
change" warning, and a stated policy of not taking contributions.

---

## 17. What this unlocks, per Cynthion's actual jobs

| job | what a host engine gives | what it does not |
|---|---|---|
| **Analysis** | a *known stimulus*. Host and analyzer share one `UTMITranslator` on `target_phy` (§13.4), so the analyzer stops being only an observer and becomes a test instrument: deterministic replay, deliberate protocol violations, traffic on demand with no third-party device in the loop | nothing for capture fidelity. The analyzer already captures better than a host engine could, and a hub plus a PC is a simpler traffic source if that is all you want |
| **Emulation** | **the missing half, and the strongest case.** Facedancer emulates a *device* to someone else's host. Nothing today lets Cynthion drive a *device under test* — exercise it, fuzz it, replay a capture at it, hold it in a state a real host would not | it is not a general-purpose host: no hubs past tier one, no split transactions, no isochronous |
| **Passthrough / MITM** | a real host on TARGET and a real device on another PHY, so traffic can be *modified*, not just observed. `greatscottgadgets/luna#302` is an open question about exactly this | needs both stacks in one bitstream and a spare PHY — `aux_phy` carries the console today. Projected ~18k LUT and ~45 BRAM, **not measured**, and that is the number to get before promising it |

Not served at all: USB 3.x, spec compliance, multi-tier hubs, guaranteed
sustained throughput. Part I §8.1's walk-away list stands unchanged.

---

## 18. Recommendation, and the first concrete step

**Do it, in the shape of §15-16: vendor `guh/usbh`'s three files, expose the
SIE through a CSR and FIFO peripheral, enumerate in firmware.** Not Part I's
Option A (a gateware class engine proves the fabric works but leads nowhere),
not Option C.

**First step, before a line of the shim is written:** bump the GUH pin to
`923c8490`, then build the `sie` configuration of `scripts/usb-host-core-area.py`
*inside* `AwtoSoc` and read the in-situ LUT count and `sync`/`usb` fmax. §12's
2080 LUT is a standalone figure at 9% occupancy; the number that decides the
design is the one at 60%, and it costs one build to get. Run
`scripts/soc_timing_sweep.py` alongside it, because a single placement is not
evidence on this design.

Then, in order:

1. **Mux `target_phy`.** Move the pad wiring at `gateware/soc/top.py`
   behind an owner select, or better, implement §13.2's `UTMITranslator` manual
   register access so `UlpiRegisters` becomes a client. This unblocks [#120](https://github.com/awtoau/cynthion-workspace/issues/120) and
   [#125](https://github.com/awtoau/cynthion-workspace/issues/125) as well as the host.
2. **VBUS and CC.** No `*_vbus_en` pin is driven anywhere in the SoC today.
   Add `control_vbus_en`, `target_c_vbus_en`, `aux_vbus_en` and
   `target_a_discharge` to the existing `gpio` peripheral, and present Rp on
   TARGET through `fusb302.rs`. Both are small because the hard parts — an I2C
   mux, a working FUSB302B driver, a power monitor — already exist.
3. **The CSR/FIFO shim and the interrupt**, §15.
4. **Firmware enumeration**, §16. Milestone: print a device descriptor.
5. **Measure the PIO byte rate** against the CLINT tick, and only then decide
   whether §15.4's DMA is needed.

The AUX-to-TARGET loopback proposed on [#105](https://github.com/awtoau/cynthion-workspace/issues/105) remains the right acceptance test,
with one revision: it needs `aux_phy` free, and `aux_phy` is the console. Either
move the console to `control_phy` behind `ApolloAdvertiser` for that test, or
accept a third-party device for first light and defer the loopback.

---

## 19. What could not be determined

- **In-situ area and fmax.** Every figure in §12 is standalone. The SoC has not
  been built with the host core in it.
- **The PIO byte rate** (§15.3). The bus cycle count is known; the CPU's stall
  on an uncached `iobus` access is not, and it dominates.
- **Host-plus-device in one bitstream** (§17). The ~18k LUT / ~45 BRAM figure is
  addition, not synthesis.
- **Whether the CONTROL VBUS switch actually delivers to TARGET-A on this
  board.** `control_vbus_in_en` is `PinsN` and its post-configuration state was
  not traced. Board question, and no board was touched.
- **Whether any other high-speed host core exists.** The survey of non-GitHub
  forges was still running when this was written. One near-miss was confirmed
  and is worth recording (below); nothing high-speed was found, and the
  recommendation would only change if something were.

### 19.1 The one real alternative, and why it does not apply

SpinalHDL ships a complete OHCI controller —
`lib/src/main/scala/spinal/lib/com/usb/ohci/`, seven files including
**`UsbOhciWishbone.scala`**. On paper it is everything §15 has to build by hand:
a published standard register map, an existing Linux/BSD/Zephyr driver for it,
DMA descriptors already specified, a Wishbone wrapper, and the same HDL
ecosystem as our CPU. It is the thing [#101](https://github.com/awtoau/cynthion-workspace/issues/101) was reaching for.

It is **full and low speed only** — `UsbOhci.scala` carries `lowSpeed` and an
`fs` timer constant and nothing else, because OHCI is an FS/LS specification.
The high-speed companion is EHCI, and no open EHCI implementation was found.

So the choice is stark and worth stating plainly: **12 Mbps with a standard
register map and off-the-shelf drivers, or 480 Mbps with a bespoke interface.**
[#105](https://github.com/awtoau/cynthion-workspace/issues/105) asked for 480, so this note is a record of the road not taken rather than a
recommendation — but if the requirement ever softens to full speed, this is the
better engineering, and it deserves a fresh look rather than a port of §15.
Its licence was not confirmed (the repository reports `NOASSERTION`) and would
have to be before any use.

---

## 20. Artifacts

- `scripts/usb_host_area.py` — the §12 measurement, now against the vendored copy.
- `debris/scripts/usb-host-core-area.py`, `debris/scripts/usb-host-area.py` —
  the original measurements, retired once the code was vendored.
- `tmp/usb-host-area/area-results.json`.
- GUH is no longer a checkout: `gateware/probes/usb_host/guh/` is pinned at `923c8490`.

**Part II touched no hardware either. All figures are from synthesis and
place-and-route.**

---
---

# Part III — What is built

**Date:** 2026-08-05T20:45:00+10:00
**Branch:** `usb-host-start`

The first increment of §18, and only the first: **the engine is vendored and it
enumerates a device in simulation.** Nothing is attached to the SoC, no CSR shim
exists, no firmware has been written, and no hardware has been touched.

## 21. The engine, vendored and exercised

`gateware/probes/usb_host/guh/` — `sie.py`, `reset.py`, `types.py` at `923c8490`,
byte-identical to upstream, with the licence beside them. `enumerator.py`,
`descriptor.py`, `engines/` and `periph/` were **not** taken, per §16.

`gateware/probes/usb_host/model.py` is ours: the wire (two UTMI interfaces facing each
other, with the line-state priority the chirp needs) and a model device that is
LUNA's own `USBDevice` with a control endpoint and a 512-byte bulk IN endpoint.
Both ends are upstream gateware written without knowledge of the other, which is
what makes the result evidence rather than a tautology.

`scripts/usb_host_sie_sim.py` is the check, and it is in `scripts/soc_sims.py`
(**541 checks, up from 520**, 8.1 s). Its 21 assertions, in three groups:

- **The bus comes up, both ways.** A high-speed device is detected as HIGH; a
  full-speed-only device falls back to FULL. The host half of the chirp is the
  piece §1.3 identified as missing everywhere else, and detecting the wrong speed
  is a failure that reports itself as nothing at all.
- **The wire is right, checked independently.** Tokens and data packets are
  captured at the device's UTMI, and CRC5/CRC16 are recomputed in Python from the
  USB 2.0 polynomials rather than compared against another piece of gateware.
- **Five control transfers enumerate a device.** GET_DESCRIPTOR, SET_ADDRESS,
  GET_DESCRIPTOR at the new address, SET_CONFIGURATION, then a 512-byte bulk IN
  whose every byte is predicted. §16's claim — enumeration is firmware's job and
  does not need the 830 LUT enumerator — is now executed rather than argued. The
  negative control is the transfer to the *old* address, which must time out.

Three of §15.2's four traps are now assertions rather than warnings: `rx_len`
reads 0 after a 512-byte packet, `idle` is a level held for 11450 cycles rather
than a strobe, and the DATA PID on the wire is whatever the caller asked for. The
fourth (NYET reported as ACK) needs a device that emits NYET, which LUNA's device
stack does not.

## 22. Measured again, from the vendored copy

`scripts/usb_host_area.py`, same method as §12 — LFSR-driven scaffold, SoC clock
generator, `fifo_depth=512`, baseline subtracted:

| build | LUT | FF | BRAM | LUTRAM | fmax on the ULPI clock |
|---|---|---|---|---|---|
| baseline (CRG + LED) | 33 | 24 | 0 | 0 | 398.72 MHz |
| `USBSIE(bus=target_phy, fifo_depth=512)` | 2113 | 458 | **0** | 96 | 125.55 MHz PASS at 60 |

**Delta: 2080 LUT, 434 FF, 0 BRAM, 96 LUTRAM.** The LUT figure reproduces §12
exactly from an independent rebuild; the FF count is new. **The SoC's own timing
is unchanged, because nothing was added to the SoC** — §12.3's warning stands and
the in-situ number is still the next increment's job.

## 23. What the driver needs, whatever schedules it

`docs/rtic.md` and [#115](https://github.com/awtoau/cynthion-workspace/issues/115) are unresolved, so nothing here assumes a
scheduling model. The engine's interface is `events in, work out`, and what it
asks of a driver is the same under a superloop, an interrupt handler or an RTIC
task:

1. **An edge, synthesised in the shim.** `status.idle` is a level and there is no
   completion strobe. The shim must produce the rising edge, latch it, and hold
   it level-high for the PLIC, which has no edge detector.
2. **Response and byte count latched at completion.** `response` and `rx_len`
   hold only until the next `start`, so a driver that is scheduled late reads a
   different transfer's result. The shim must capture them, and must count RX
   bytes itself because `rx_len` wraps.
3. **One owner at a time.** The engine runs one transaction, has no queue, and
   `start` is silently ignored unless it is idle. A dropped issue is therefore
   invisible — the shim should raise a bit when it happens.
4. **No deadline for correctness.** The engine holds its state indefinitely and
   generates SOFs itself, so late servicing costs throughput, not correctness.
   **This is the load-bearing statement for the RTIC question: host mode does not
   need bounded latency to work, so it should not be the argument for or against
   a scheduler.** It needs bounded latency to go fast, which is a different claim
   and one to make with a measurement.
5. **A bounded drain loop, not a byte per turn.** One high-speed packet is 512
   FIFO accesses; whoever drains it should do so in one bounded loop.

## 24. What is not done

- **Nothing is attached to the SoC.** `target_phy` still belongs to
  `UlpiRegisters` (§13), no CSR window exists, and the in-situ LUT/fmax figures
  §19 asked for are still unmeasured.
- **No firmware.** The enumeration above is a simulation testbench, not Rust.
- **No CDC.** The bench runs the engine in `sync`; §14's crossing is untested.
- **No VBUS or CC.** No `*_vbus_en` pin is driven anywhere, still.
- **Nothing below UTMI.** The model wire hands over bytes, so bit stuffing, NRZI
  and EOP are not exercised, and no ULPI PHY is involved.
- **No hardware.** Same as Parts I and II.

Next increment, in order: re-parent `target_phy` behind `UTMITranslator` (§13.2,
which also unblocks [#120](https://github.com/awtoau/cynthion-workspace/issues/120) and [#125](https://github.com/awtoau/cynthion-workspace/issues/125)), then the in-situ build for the numbers §19
could not give, then the CSR/FIFO shim of §15 with §23's five requirements, then
firmware enumeration.

