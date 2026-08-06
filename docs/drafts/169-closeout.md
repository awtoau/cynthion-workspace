# #169 close-out plan

Reviewed against the tree at `origin/main` = `72fc7ea`. Every claim below was
re-checked in the tree; where the issue text and the tree disagree, the tree wins
and the difference is called out.

---

## Part 1 — what #169 asked for that is already done

| #169 said | tree at `72fc7ea` |
|---|---|
| eight submodules in `.gitmodules` | **four**: apollo, cynthion, cynthion-hardware, vexiiriscv. facedancer, luna, packetry, saturn-v are gone |
| step 1: drop `facedancer` | **done.** No `.gitmodules` entry, no `EDITABLE` line in `machine_setup.py` (which now carries a comment citing #169), no assertion in `check.py` (same), no `install.py` manifest entry, no `upstream_ci.py` entry (same), no `versions.json` line |
| `gateware` check is ~98% of lint wall time | **deleted**, with a ten-line comment at the end of `build_checks()` recording why. Question 3 is answered: deleted, not repointed — `socmap` covers the gateware this repo builds |
| `.gitignore:17` unanchored `lib/` will untrack the next `lib/` | **fixed.** The block is now `/lib/` and `/lib64/`, anchored, with a comment naming the 34 + 2 files it cost. **This bullet of #169 is obsolete** |
| — (not in #169) | the `flutter` check is gone too, and the dashboard is at `debris/code/app-flutter-dashboard/` |
| — (not in #169) | `scripts/submodule_patch_audit.py` exists and reports all four submodules `safe -- every commit is on a remote` |

**Overlap with #190 (closed).** `amaranth-soc` and `amaranth-stdio` are now
declared in `machine_setup.py:AMARANTH_GIT`, and a new `amaranthsoc` check fails
if the import resolves inside luna-soc's vendored tree. This covers *half* of
#169's cynthion-side complaint — the vendored-`amaranth_soc` shadow is now a red
check rather than an invisible fallback. It does **not** cover the other half:
`repos/cynthion`'s `pyproject.toml` still requires the `awtoau/awto-luna-soc`
fork, and that pin only disappears when the `cynthion` editable install does.
`luna_soc` itself is already a pip install (`0.3.2+awto.1`, site-packages), not a
submodule, so it is not a blocker for removing `repos/`.

## Part 2 — numbers in #169 that are now wrong

Stated so the issue is not re-read as current.

- **eight checks, half touching `repos/`** → still eight, still four touching
  `repos/`, but a *different* four. `gateware` and `flutter` are gone;
  `amaranthsoc` is new. The four are `rust`, `apollo`, `python`,
  `freethreading` (below).
- **"four platform imports"** → **six** sites import
  `cynthion.gateware.platform.cynthion_r1_4`, and one of the six is not a
  platform import at all.
- **`gateware/soc/vexii_hello_soc.py:176, 181, 277, 312, 1642`** (stale
  comment citations) → now `177, 182, 278, 313, 1711`.
- **`scripts/sideband_build.py:40`** → line **43**, and #169 missed
  `scripts/fabric_build.py:72`, which does the same `sys.path.insert(0,
  "repos/apollo")`.
- **`machine_setup.py:72-76`** → the `EDITABLE` list is now ~line 83 and holds
  one entry.
- `gateware/soc/vexii_hello_soc.py:84` — **still correct**, still the reason
  `repos/apollo` cannot be deleted today.

## Part 3 — the four checks whose subject is inside `repos/`

| check | what it runs | what it would test if `repos/` were gone |
|---|---|---|
| `rust` | `cargo check` + `make clippy` with `cwd = repos/cynthion/firmware` (moondancer) | **nothing.** The subject is upstream's firmware. Deleting it deletes the check |
| `apollo` | `tools/get_deps.py` in `repos/apollo/lib/tinyusb`, `make APOLLO_BOARD=cynthion` in `repos/apollo/firmware`, then `scripts/apollo_budget_check.py` (in-repo) | the budget check is ours and survives — but it reads `repos/apollo/firmware/_build/.../firmware.elf`, so with no submodule there is no ELF to measure. See #199 |
| `python` | `import cynthion, apollo_fpga` (both editable, resolving into `repos/`), then pytest over `repos/cynthion/cynthion/python/tests/` **and** `tests/` | our `tests/` still run; the import assertion becomes a dependency probe rather than a subject; the submodule's 3 tests go |
| `freethreading` | `sys._is_gil_enabled()` after `import cynthion, apollo_fpga` | the interpreter assertion survives; the import list would need replacing with whatever the heaviest real import is (amaranth, luna) |

This is #169's question 2, and the answer the tree suggests: `python` and
`freethreading` do not need the submodules as *subjects*, only as *heavy
imports*, and any pip-installed package serves that role. `rust` and `apollo` do
need them, and that is the whole of question 1.

**#199 changes the framing.** `apollo_budget_check.py` misses `.relocate`, so
both Apollo ceilings are already breached (flash 95.48% vs 95%, RAM 86.72% vs
85%). The `apollo` check is the only thing in the gate measuring that, and it is
currently measuring it wrong. Nothing in #169 should propose weakening or
removing the `apollo` check until #199 is closed.

---

## Part 4 — what remains, in dependency order

### Step A — converge the platform imports (no hardware, no blockers)

Six sites import `cynthion.gateware.platform.cynthion_r1_4`:

| site | what it does | risk |
|---|---|---|
| `scripts/soc_generate_pac.py:138` | elaborates the SoC to read its memory map — this **is** the `socmap` check | low; `--check` compares against the committed SVD, so a drift shows immediately |
| `scripts/soc_diagram.py:171` | wraps `platform.request` to record what the SoC asked for | low |
| `scripts/bram_patch.py:255` | `elaborate_il()` re-elaborates and compares RTLIL against the build directory | **the one real risk.** If the vendored platform emits RTLIL that differs by so much as a module name, every existing build directory reads as stale. Must be validated against a fresh build, and `tests/test_bram_patch_freshness.py` is the test that will say so |
| `gateware/probes/bram_probe/bram_probe.py:192` | `--build` only | low, and it is a bring-up probe |
| `gateware/soc/vexii_hello_soc.py:1705` | the `--build` path for the SoC bitstream. Its comment says "the installed cynthion package, not the in-repo source tree", which is about `amaranth_boards`, not about `board` | low, **but sequence it last**: another agent is live in this file |
| `scripts/phy_probe.py:15` | **not a platform import.** `cynthion.selftest.registers` — four integer constants — and the script only works with upstream's *selftest bitstream* loaded, which is built from `repos/cynthion` | decide separately (below) |

The target already exists and is already the majority: `gateware/board/`
(`__init__.py`, `core.py`, `cynthion_r1_4.py`, `resources.py`) is used by 12
in-tree sites, depends only on `amaranth`, and documents in `core.py` exactly
what it dropped from the LUNA chain and why.

**Equivalence is already evidenced.** `debris/scripts/platform_vendor_compare.py`
ran real place-and-route both ways and compared device utilisation *and* the full
signal-to-ball pin assignment. It is retired, not deleted — re-run it as the
acceptance evidence for this step rather than writing a new comparison.

- **Verification:** `dev.py lint` (`socmap` is the direct subject), `dev.py test`,
  `scripts/soc_sims.py`, `dev.py build` (the bitstream must still build), plus
  `platform_vendor_compare.py` on at least `blinky`. Check counts must be
  unchanged at 8.
- **Hardware:** none. Elaboration and P&R only.
- **After this step,** the only thing holding `repos/cynthion` is the `rust`
  check, the `python` import assertion and pytest path, and `phy_probe.py`.

**`phy_probe.py` needs a decision, not a conversion.** Vendoring four register
constants is trivial; the script is useless without upstream's selftest
bitstream, which this repo does not build. Either accept it as a
"needs `repos/cynthion`" hardware script, or retire it. Not a blocker for
anything else, and not worth holding #169 open for.

### Step B — vendor the `apollo_fpga.gateware` modules (no hardware)

Blocks step C. Six modules, **2,534 lines**:

| module | lines | non-amaranth imports |
|---|---|---|
| `sideband.py` | 700 | `amaranth_stdio.serial` |
| `flash_id.py` | 483 | `luna.gateware.stream`, `.sideband` |
| `qspi_flash.py` | 454 | **`glasgow.gateware.{ports,qspi}`** |
| `flash_bridge.py` | 439 | `luna` (6 modules), `usb_protocol`, **`apollo_fpga.ApolloDebugger`** |
| `variable_clock.py` | 359 | none beyond amaranth + `ecppll` on PATH |
| `advertiser.py` | 99 | `luna.gateware.usb.usb2.request`, `usb_protocol` |

In-tree consumers: `gateware/soc/vexii_hello_soc.py:84`,
`gateware/probes/qspi/qspi_gateware.py:48-49`,
`gateware/probes/adv_uart/adv_uart_gateware.py:28`,
`gateware/probes/sideband/sideband_gateware.py:32-37`,
`gateware/probes/sideband/test_protocol.py:33`,
`gateware/probes/hyperram/hyperram_dqs_top.py:134`,
`scripts/qspi_burst_sim.py:111`.

Two findings that change the shape of this step:

1. **Vendoring is mandatory, not optional, if `apollo_fpga` becomes a pip
   dependency.** `variable_clock.py` is explicitly ours
   (`docs/upstream-boundary.md` lists it as a divergence: upstream offers
   60/120/240 MHz only). `sideband.py` and `qspi_flash.py` are local additions of
   the same family. A published `apollo-fpga` wheel will not contain them, so
   **step C is impossible until step B is done** — #169's suggested order is
   right, and the reason is stronger than the issue states.
2. **`flash_bridge.py` imports the host library at module scope**
   (`from apollo_fpga import ApolloDebugger`). Vendoring it as-is does not
   detach it from `apollo_fpga`; that import has to be moved into the host-side
   function or dropped. `sideband_gateware.py` pulls `SPIStreamController` from
   this module, so it is on the critical path.

**Undeclared dependency found while checking this** — `glasgow` resolves to a
checkout outside the workspace and appears in neither `machine_setup.py`'s
`PACKAGES_PIP` nor `install.py`. It is the exact shape #190 fixed for
`amaranth-soc`: a build input that works only because a particular machine has it
lying around. Vendoring `qspi_flash.py` inherits it. Worth its own issue (below).

- **Verification:** `dev.py lint`, `dev.py test`, `scripts/soc_sims.py`,
  `dev.py build`, plus an elaboration of each consumer above.
  `scripts/sideband_link_sim.py` / `sideband_advertise_sim.py` are the closest
  thing to unit tests for `sideband.py`.
- **Hardware:** none for the vendoring. Confirming the sideband and QSPI
  bitstreams still behave *does* need the board, and should be a separate,
  explicitly hardware-gated step.

### Step C — `apollo_fpga` host side becomes a pip dependency

Blocked by step B. Three separate jobs, and the first is a question, not work:

1. **Establish whether the fork's 60 local commits touch `apollo_fpga/` host
   code.** `repos/apollo` is 60 ahead of `awtoau/awto-apollo` and the docs record
   local JTAG-speed work naming `apollo_fpga/jtag.py` and `apollo_fpga/ecp5.py`.
   If any of it is live, "take the pip release" silently reverts it. This is the
   first thing to run — `git log --oneline upstream/main..HEAD -- apollo_fpga/`
   inside the submodule — and it decides whether step C is possible at all.
2. **Nine scripts import the host library** (`fast_loader.py`, `phy_probe.py`,
   `flash_backup.py`, `fabric_control.py`, `fabric_run.py`, `qspi_burst.py`,
   `soc_jtag_stage.py`, `hyperram_ceiling.py`, `hyperram_identify.py`). These need
   no source change — only that the package resolves.
3. **Nine files spawn the CLI by path**, `repos/apollo/apollo_fpga/commands/cli.py`:
   `soc_run.py:475,688`, `fabric_run.py:88`, `fpga_job_runner.py:20`,
   `riscv_clock_ladder.py:117`, `riscv_flash_check.py:45`,
   `hyperram_ceiling.py:69`, `riscv_console_capture.py:41`,
   `diamond/ladder.py:300`, and a printed hint in `soc_jtag_stage.py:405`. All
   become `-m apollo_fpga.commands.cli`. Mechanical, and verifiable without a
   board via `--help` / `info` returning "no device".
   `scripts/sideband_build.py:43` and `scripts/fabric_build.py:72` drop their
   `sys.path.insert(0, "repos/apollo")` in the same change.

- **Verification:** `dev.py lint`, `dev.py test`, `scripts/soc_sims.py`, plus
  `python3 -m apollo_fpga.commands.cli --help` for the path substitution.
- **Hardware:** **yes, for final acceptance.** Every one of the nine CLI callers
  configures or flashes the board. The substitution can be proven correct without
  hardware; that it still *works* cannot.

### Step D — the checks, and #169's question 1

Only after A–C. `rust` and `apollo` are the last holders of `repos/cynthion` and
`repos/apollo`, and neither can be repointed — their subjects are upstream's
firmware. The choice is the owner's:

- keep both submodules purely so the gate keeps building upstream firmware, or
- accept that this repo no longer gates upstream's firmware, delete both checks,
  and let `scripts/upstream_ci.py` (which clones fresh and touches no `repos/*`)
  be the only thing that looks upstream.

**Do not decide this as a side effect of A–C.** It is the actual content of
question 1 and the only part of #169 that is a decision rather than a task.

---

## Part 5 — split out of #169

Five things are in or adjacent to #169 that do not depend on removing a
submodule, and each is deliverable on its own:

1. **`firmware/*` is linted by nothing.** #169's own "done when" bullet 2. The
   `rust` check runs clippy with `-Dwarnings` plus a pedantic set against
   *moondancer*; the four crates in `firmware/` (`cynthion-boot`,
   `cynthion-payload`, `cynthion-soc`, `cynthion-soc-pac`) have no `[lints]`
   table and no clippy invocation anywhere. There is no workspace manifest above
   `firmware/`, so it is one cargo call per crate — the same shape `dev.py`
   already handles for `fmt`. #169's counts (10 + 3 errors) are months old and
   must be re-measured, not carried over. **Zero dependency on `repos/`; do this
   first, independent of everything above.**
2. **pytest rootdir is the submodule's.** Confirmed live in
   `tmp/logs/check-python.log`: the warnings summary reports the submodule's
   tests as `tests/test_fpga_adv_uart.py`, i.e. rootdir resolved to
   `repos/cynthion/cynthion/python`, and node IDs collide with our own `tests/`.
   30 tests run, 27 of them ours. Fixable in one file today, and it stops being
   *possible* to fix cleanly the moment someone adds a root `pyproject.toml` for
   another reason. Note there is deliberately no root `pyproject.toml` —
   `machine_setup.py` is this repo's dependency mechanism — so the fix is a
   `pytest.ini`, not a `pyproject.toml`.
3. **`glasgow` is an undeclared build dependency** reached through
   `apollo_fpga.gateware.qspi_flash`, resolving to a checkout outside the
   workspace. Same failure shape as #190.
4. **Vendoring the six `apollo_fpga.gateware` modules** (step B) is 2,534 lines
   across six files with three distinct external dependencies and one host-side
   import to unpick. That is not a sub-task of an audit issue.
5. **`docs/install.md` is stale well beyond facedancer.** It documents an
   `awto-*` sibling-directory layout that predates `repos/`, targets
   `CynthionPlatformRev0D2` throughout, and line 507 tells the reader to
   `pip install -e repos/facedancer`, which cannot succeed. Fixing only the
   facedancer lines would leave a doc that is still wrong; it needs one pass, as
   its own change. `docs/git.md`'s "four places a submodule lives" uses
   `facedancer` as its worked example for the same reason.

What is then left in #169 itself: step A, and question 1.

---

## Part 6 — changes made by this review

**None.** The one candidate the brief flagged — the `.gitignore` `lib/` trap —
was already fixed on `main` before this review started: the block is `/lib/` and
`/lib64/`, anchored, with a comment naming what it cost. Nothing else on the
remaining list is both small and fully verifiable without touching files another
agent is live in.
