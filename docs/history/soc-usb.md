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

