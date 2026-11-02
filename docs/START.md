# START

Read this first. Entry point is [`./dev.py`](../dev.py) (`--help`, `describe` = JSON, `doctor` = toolchain check).

## Rules
- Scripts live in [`scripts/`](../scripts), log to `tmp/logs/<name>.log`; most share `tmp/logs/dev.log`. Temp files in `tmp/`, never `/tmp`.
- Python = free-threaded 3.15t, system env, **no venv** (`python3 -c "import sys; print(sys._is_gil_enabled())"` -> `False`).
- `./cyn` is retired: use `./dev.py`. Nothing runs on GitHub Actions: use [`scripts/check.py`](../scripts/check.py).
- Never open the console tty or run `apollo configure` yourself: [board arbiter](board-arbiter.md) (`./dev.py board ...`).
- No `sleep`/`timeout` in commands.

## 1. Setup (once)
```
git clone --recurse-submodules https://github.com/awtoau/cynthion-workspace
./dev.py setup              # packages, submodules, Rust target, Python pkgs, udev  (-- --system adds sudo dnf)
./dev.py doctor             # needs cargo, riscv64-linux-gnu-objcopy, yosys, nextpnr-ecp5, qemu-system-riscv32
```
- [`machine_setup.py`](../scripts/machine_setup.py) does NOT install the FPGA flow: OSS CAD Suite via `./scripts/install.py toolchain-install` (`toolchain-status`, `versions`).
- Also needed: `sbt` + JDK (Java 25 ok), `arm-none-eabi-gcc` (Apollo).
- Python 3.15t: `uv python install 3.15t`; put its `bin/` before `~/.local/bin` on `PATH`.
- Interpreter override: `CYN_PYTHON`. Do not run python from inside `repos/cynthion/` (shadows `cynthion`).
- [install.md](install.md) STALE parts: `cyn ...` flash commands, `act`/CI sections, `install.py setup|rebuild|clone-repos|ci-*`. Still valid: toolchain, prerequisites. See STALE DOCS.

## 2. Map
| What | Where |
|---|---|
| SoC gateware (Amaranth) | [`gateware/soc/top.py`](../gateware/soc/top.py), [`variant.py`](../gateware/soc/variant.py), [`cpu/cpu.py`](../gateware/soc/cpu/cpu.py), `peripherals/` |
| Board platform / pins | [`gateware/board/`](../gateware/board) |
| SoC firmware (Rust) | [`firmware/cynthion-soc`](../firmware/cynthion-soc), PAC [`cynthion-soc-pac`](../firmware/cynthion-soc-pac), host tests [`cynthion-soc-tests`](../firmware/cynthion-soc-tests) |
| Bootloader (Rust) | [`firmware/cynthion-boot`](../firmware/cynthion-boot) |
| Apollo (SAMD11, C) | `repos/apollo/firmware` (submodule) |
| VexiiRiscv CPU source | `repos/vexiiriscv` (submodule, Scala) |
| Scripts / entry point | [`scripts/`](../scripts), [`scripts/dev.py`](../scripts/dev.py) |
| Pytest | [`tests/`](../tests) |
| Docs index | [`hardware.md`](hardware.md), [`chips/`](chips) |

Submodules (4): apollo, cynthion, cynthion-hardware, vexiiriscv ([`.gitmodules`](../.gitmodules)).

## 3. Build
| Thing | Command | Output | Log |
|---|---|---|---|
| Apollo d11 | `cd repos/apollo/firmware && make APOLLO_BOARD=cynthion` | `_build/cynthion_d11/firmware.elf` | `tmp/logs/check-apollo.log` (via check) |
| Apollo guards | `python3 scripts/apollo/apollo_budget_check.py --stack-measured 344`, `python3 scripts/apollo/verify_vectors.py` | pass/fail | `tmp/logs/dev.log` |
| SoC fw + boot + gateware | `./dev.py build` (= `soc_run.py --build-only`, no board) | `tmp/awto_soc/build/<variant-slug>/` (`rust_fw.bin`, `rust_boot.bin`, `top.bit`) | `tmp/logs/synthesis-<slug>.log`, `dev.log` |
| SoC fw only | `cd firmware/cynthion-soc && cargo check --release`; `python3 scripts/soc/soc_bin_matrix.py` (all `[[bin]]`) | `firmware/cynthion-soc/target/riscv32imac-unknown-none-elf/release/` | check logs |
| Many variants | `./dev.py build-many` ([`soc_build_fanout.py`](../scripts/soc/soc_build_fanout.py)) | one dir per variant | `dev.log` |
| PAC / SVD | `./dev.py pac` | `firmware/cynthion-soc-pac` | `dev.log` |
| VexiiRiscv netlist | automatic inside the gateware build; `generate()` in `cpu.py` runs `sbt --batch --no-server "runMain vexiiriscv.Generate ..."` | `repos/vexiiriscv/VexiiRiscv.v`, copied to `tmp/awto_soc/build/<slug>/VexiiRiscv.v` | `dev.log` |

- Variant = env `CYNTHION_HYPERRAM_CK_MHZ`, `CYNTHION_HYPERRAM_DQS`, `CYNTHION_SYNC_MHZ` ([`variant.py`](../gateware/soc/variant.py)); defaults in `VARIANT_ENV`.
- Bitstream cache: `tmp/bitcache/<digest>/`; unchanged sources = no resynthesis.
- [`fast_build_env.py`](../scripts/fast_build_env.py) sets `AMARANTH_nextpnr_opts` (`--parallel-refine`) + `YOSYS_MAX_THREADS=1`; `soc_run.py --no-parallel-refine` drops it (reproducible placement).
- CPU: [riscv-core-build.md](riscv-core-build.md); flags = `GENERATE_FLAGS` in `cpu.py`. Regenerates every build, seconds when sbt is warm.
- Apollo flags: LTO is load-bearing; `verify_vectors.py` catches a binary that links and does nothing ([samd11-apollo.md](chips/samd11-apollo.md)).

## 4. Test
- Fast gate: `./dev.py gate` = `test` (QEMU) -> `lint` (`check.py --parallel`) -> `sim-once` -> `build`. Fail-fast, no board. Run before every commit.
- `./dev.py ci` = `test lint build sim docs`, runs all, one GO/NO-GO.
- [`check.py`](../scripts/check.py) (`--list`, `--fast` skips slow, `--parallel`, `--verbose`): `rust socfw apollo python freethreading amaranthsoc featureiso socmap review repro irqlog paths doclinks`. Each logs `tmp/logs/check-<name>.log`. Skips (not fails) when a tool is absent; fails on an unpopulated submodule.
- QEMU shell tests: `./dev.py test` = [`soc_test.py`](../scripts/soc_test.py) (`--no-build`, `-v`, `--features`); builds into `tmp/qemu-build`. `./dev.py test-board` runs the same on hardware.
- Simulations: `./dev.py sim-once` (one cycle each) or `./dev.py sim` (+ repetition) = [`soc_sims.py`](../scripts/soc_sims.py) (`--list`, `--tier once|soak`, `--jobs`, NAME filter).
- Pytest: `python3 -m pytest tests/ -q` (also the `python` check).

## 5. Run on the board
- `./dev.py run` = [`soc_run.py`](../scripts/soc_run.py): QEMU gate -> build -> configure over JTAG (SRAM only) -> read console.
  - `--skip-tests` no QEMU gate; `--no-build` load what exists; `--build-only` no hardware; `--firmware-only` (= `./dev.py fw`) rebuild + flash `.text`, no synthesis; `--force-flash`, `--no-flash`, `--no-read`, `--features`, `--seed`, `--no-parallel-refine`.
- Multiple agents: submit via `./dev.py board run|shell|confirm|configure|status` (HTTP 127.0.0.1:9100, [`board.py`](../scripts/board/board.py)); do not call `soc_run.py` concurrently.
- Console: `./scripts/tio_user.py` once, leave running; serves TCP 9000 (`--no-serve` = exclusive). `soc_run.py`/`soc_shell.py` read through 9000. Two readers on the tty garble output.
- Board state traps:
  - Replug / power cycle drops the SoC (SRAM config): re-run `./dev.py run --no-build` or `board configure`.
  - SoC needs the Apollo FORK firmware, not upstream's. `cynthion update` replaces it with upstream's; `soc_run.py` refuses stock (`stock-apollo`). Check and restore: [apollo-firmware.md](apollo-firmware.md).
  - `apollo force-offline` hands the ECP5 port back to Apollo after a bitstream is running.
- Other: `./dev.py probe` (is it alive/ours), `flash`, `capture`, `typec`, `stage`.

## 6. Worktrees
```
git worktree add .claude/worktrees/<name> -b <branch>
cd .claude/worktrees/<name>
./dev.py worktree-setup      # = scripts/worktree_setup.py setup [--prune]; then runs check
./dev.py worktree-check      # non-zero + fix if not buildable
```
- `git worktree add` populates no submodules. Setup links each (recursively, incl. `ext/SpinalHDL`) as a linked worktree of `.git/modules/<path>`: shared objects, own HEAD/index. No network. Never copy submodule trees ([git.md](git.md)).
- `sources/**` is gitignored: resolved from the main checkout by [`shared_paths.py`](../scripts/shared_paths.py) (env, here, then main).
- Per worktree: `tmp/` (logs, bitcache, `awto_soc/build`, `qemu-build`), `firmware/*/target`, `repos/vexiiriscv` (`VexiiRiscv.v`, sbt `target/`) and the generator lock `<worktree>/tmp/vexii-generate.lock` after setup.
- Without setup, `cpu.py` falls back to the main checkout's `repos/vexiiriscv` and its lock `<main>/tmp/vexii-generate.lock` (`VEXII_ROOT` overrides both). Then all worktrees share one generator dir and one lock.
- Machine-global: the one board, port 9100, TCP 9000. Arbiter state is in the MAIN tree (`tmp/board-arbiter/`, `tmp/logs/board-arbiter.log`).
- Two worktrees building at once: safe for gateware/firmware after setup (own dirs); NOT safe (a) without setup (shared `VexiiRiscv.v`; lock only serialises) or (b) both configuring the board outside the arbiter. sbt/coursier cache sharing: UNVERIFIED.
- Same-worktree concurrency is by variant dir; the lock blocks without timeout (~40 s per generate).
- Retire: `python3 scripts/retire_worktrees.py` (report), `--move` (to the machine-wide wastebasket), `--prune`, `--stamp`. Needs: branch merged into `main`, on `origin`, no tracked changes. Never deletes.
- Commit small, one logical change; tests before the fix.

## 7. Logs, issues
- `tmp/logs/dev.log` (everything), `check-<name>.log`, `synthesis-<slug>.log`, `board-arbiter.log`. Reports in `tmp/`.
- Search before filing: `gh search issues --repo awtoau/cynthion-workspace --state open "<kw>"`. Findings go in issues, not chat.
- File with `./dev.py gh-post issue|comment|close` (scrub built in), not bare `gh issue`.

## STALE DOCS
- [install.md](install.md): `cyn apollo flash`, `cyn fpga flash`; `act`/workflow/CI sections and `install.py ci-install|ci-list`; `install.py setup` flow (superseded by `./dev.py setup`).
- [README.md](../README.md): "CI runs 3.15t ... nightly matrix"; check table lists 8 of 13 checks; "whole set runs well under a second" (slow checks exist); repo map omits `repos/vexiiriscv`; "does not contain code".
- [git.md](git.md): lists seven submodules (facedancer, luna, packetry, saturn-v absent from `.gitmodules`); "eleven submodules" vs four top-level; hardcoded pin `90c8b7b6`.
- [riscv-core-build.md](riscv-core-build.md): "every worktree shares the main checkout's `repos/vexiiriscv`" is false after `worktree-setup` (lock is then per worktree).
- [chips/samd11-apollo.md](chips/samd11-apollo.md): cites `scripts/apollo_reflash.py`, which does not exist.
- [`soc_run.py`](../scripts/soc_run.py) docstring: shell image "baked into the bitstream at 0x400" vs [`dev.py`](../scripts/dev.py) `fw`: ".text lives in flash, nothing of the firmware travels in the bitstream".
