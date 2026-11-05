# Toolchain versions and the constraints on them

- Versions and constraints only. Whether to upgrade something is an issue, not this file.
- Installed column read from this machine on 2026-11-02 (`python3 -m pip list`, tool `--version`).
- `./dev.py doctor` checks every tool named below is on PATH.
- Dated upstream snapshot (2026-08-05) and old drift tables: [`history/upstream-toolchain.md`](history/upstream-toolchain.md).

## Pins that follow main (2026-11-06)

- FPGA tools, one prefix `~/opt/fpga-main`, pins in [`scripts/toolchain_env.py`](../scripts/toolchain_env.py), built by [`scripts/yosys/build_fpga_toolchain.py`](../scripts/yosys/build_fpga_toolchain.py): yosys `8f3cc3d0c` (+ abc9 fix), prjtrellis `2d12005` (db `9754ff4`), nextpnr `937c770c`. To follow main: bump a pin, rerun.
- Python: `luna-usb` `352cdbf` and `pygreat` (libgreat `699d267`) from main via `pip install git+...@<sha>`; `amaranth-soc`, `amaranth-stdio` at main tips. `amaranth` stays on 0.5.10: luna main still imports `Record`, removed in amaranth main ([#83](https://github.com/awtoau/cynthion-workspace/issues/83)).
- `repos/vexiiriscv` at upstream `dev` `9f70315d`; rust `stable` 1.99.1 via rustup (dnf rust removed).
- The bitstream cache key includes the yosys/nextpnr binaries, the Python package versions and the VexiiRiscv commit (`soc_run.toolchain_identity`).

## Pinned vs latest

| tool | pinned (`versions.json`) | installed 2026-11-02 | latest upstream |
|---|---|---|---|
| amaranth | 0.5.9 | 0.5.10 | 0.5.10 |
| luna-usb | 0.2.3 | 0.2.3 | 0.2.3 |
| Yosys | 0.65+57 (`9d0cdb855`) | 0.65+57 | v0.70 |
| nextpnr-ecp5 | 0.10-74 (`ee605e2b`) | 0.10-74 | 0.11.1 |

- `versions.json` is a baseline snapshot, not a lock; its other entries (Python 3.15.0b3, cynthion `.post30`, luna-soc 0.2.0) predate the installed set.
- "Latest upstream" is from the owner's knowledge of releases (`gh release list` / PyPI), not re-queried here: UNVERIFIED.
- VexiiRiscv: workspace gitlink `f8774d4a` vs checkout `03345d8d` vs `origin/dev`. TODO: decide the pin and bump the gitlink.

## FPGA toolchain

One OSS CAD Suite build supplies all three; `~/opt/oss-cad-suite-20261104/oss-cad-suite`, sourced via `~/opt/fpga-main/environment`. Nightly-only: no stable channel, so "the release" is whichever nightly is pinned.

| tool | in use | note |
|---|---|---|
| OSS CAD Suite | `20260522` (`~/opt/oss-cad-suite/VERSION`) | download: `https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz`, extract to `~/opt/` |
| Yosys | `0.65+57` (`9d0cdb855`) | post-0.65 dev build |
| nextpnr-ecp5 | `0.10-74` (`ee605e2b`) | 74 commits past the 0.10 tag |
| ecppack / prjtrellis | `1.4-79` (`56bb170`) | prjtrellis last release 1.4 (2023-05-16); commits since are build/packaging cleanups |

## Python

Free-threaded CPython as the default `python3`. **No virtualenv**; PATH ordering for console scripts: [`../README.md`](../README.md#python-strategy).

| | |
|---|---|
| interpreter | CPython **3.15.2**, free-threading build |
| GIL | disabled (`sys._is_gil_enabled()` -> `False`) |
| location | `~/opt/cpython-ft/bin/python3` (symlink to `~/opt/cpython-3152t`) |
| override | `CYN_PYTHON` |

- No distro packages a free-threaded 3.15; install with `uv python install 3.15t` or build from source with `--disable-gil`.
- `scripts/install.py` resolves the interpreter, preferring `python3.15t`, falling back through `python3.14t` / `python3.15` / `python3.14`.
- No upstream dependency declares support for 3.15 or tests free-threading (no Amaranth / amaranth-soc issue, PR or CI job mentions no-GIL). The `freethreading` check in `scripts/check.py` is the only guard against an import re-enabling the GIL.

### Packages

| package | installed 2026-11-02 | source |
|---|---|---|
| `amaranth` | 0.5.10 | PyPI |
| `amaranth-soc` | `0.1a1.dev32+g3e3d8b7` | **git only** |
| `amaranth-stdio` | `0.1.dev37+gd296ba4` | **git only** |
| `luna-usb` | 0.2.3 | PyPI |
| `luna-soc` | `0.3.2+awto.1` | `awtoau/awto-luna-soc` fork ([pin](upstream-boundary.md#luna-soc-fork-pin)) |
| `usb-protocol` | 0.9.2 | PyPI |
| `cynthion` | `0.2.4.post32+git.7fa0c6a4` | editable, `repos/cynthion/cynthion/python` |
| `apollo-fpga` | `1.1.1.post64+git.90c8b7b6` | editable, `repos/apollo` |
| `pyusb` 1.3.1, `pyserial` 3.5, `pyvcd` 0.4.2, `numpy` 2.5.4, `pytest` 9.2.0 | | PyPI |

- `facedancer` is not installed here; nothing in `gateware/` or `scripts/` imports it. Patches recorded against it ([`upstream-boundary.md`](upstream-boundary.md#patches-carried-against-dependency-trees)) apply to a facedancer checkout, not this environment.
- `amaranth-boards` is not installed; `LEDResources` and `ULPIResource` are copied into `gateware/board/resources.py`.

### Constraints that must hold

**`pip install amaranth-soc` from PyPI does not get you amaranth-soc.** The PyPI name is a placeholder (version `0`, uploaded 2021, no modules; `import amaranth_soc` raises `ModuleNotFoundError`). Install from git:

    amaranth-soc @ git+https://github.com/amaranth-lang/amaranth-soc.git

`amaranth-stdio` is the same shape: PyPI entry is version `0` and the repository has **no tags**, so there is no version to pin to.

**Real `amaranth-soc` must stay installed, or `luna_soc` silently substitutes its own.** `luna_soc/__init__.py` appends its vendored tree to `sys.path` only when the real package is missing:

    try:
        import amaranth_soc
        import amaranth_stdio
    except:
        sys.path.append(.../gateware/vendor)

- Removing the real package brings the vendored copy back with no error and no version string.
- The vendored tree was last re-synced 2025-01-07 and misses four upstream fixes, including Python 3.14 annotation support ([`d8b5892`](https://github.com/amaranth-lang/amaranth-soc/commit/d8b58925533d9dd6be64a2ca9993bfe3a6d46ae9), 2026-01-28), without which this fails:

      class Probe(csr.Register, access="rw"):
          value: csr.Field(csr.action.RW, 8)

      TypeError: Field collection must be a dict, list, or Field, not None

- Both packages are declared in `scripts/machine_setup.py`, pinned to the commits above ([#190](https://github.com/awtoau/cynthion-workspace/issues/190); before that nothing declared either and a clean install used the 2025 vendored tree).
- `scripts/soc/amaranth_soc_check.py` (the `amaranthsoc` check) asserts neither name resolves inside `luna_soc/gateware/vendor`. `./scripts/amaranth_soc_check.py --simulate-vendored` proves it still fails.
- Importing `luna_soc.gateware.vendor.amaranth_soc` directly yields a different `wishbone.Interface` class object from the `sys.path`-aliased `amaranth_soc`; `Decoder.add()` then rejects a structurally identical bus.

**Fork version tags must use the PEP 440 local form.** `0.3.2+awto.1` works; `0.3.2-awto.1` breaks the wheel build (`setuptools-git-versioning` parses the tag through `packaging.version.Version`, which rejects a hyphen). `luna-soc` derives its version from git tags: a fork with no tags reports `starting_version = "0.2.0"` while containing 0.3.2, which reads exactly like a stale dependency.

**Editable installs decide whether edits take effect.** `apollo_fpga`, `awto_probe` and `cynthion` are editable, so edits under `repos/` apply. `luna` and `luna_soc` are not: `import luna` resolves to site-packages and a checkout is inert.

**Do not run anything that imports `cynthion` from inside `repos/cynthion/`.** Its `cynthion/` subdirectory is a namespace package that shadows the installed one: `cynthion.__file__` is `None` and `cynthion.shared.usb` fails to resolve.

### Two CSR behaviours that look like version skew

- `csr.action.RW` fields cannot have `r_data` driven by gateware (software owns the value; elaboration error). Split write-data and read-data into separate registers.
- `csr.Bridge` already adds every register in the builder as a submodule; adding them again raises `DuplicateElaboratable`.

## Rust firmware

| | in use |
|---|---|
| `rustc` / `cargo` | 1.97.0 (2026-07-07) |
| target | `riscv32imac-unknown-none-elf` |
| toolchain file | **none**: `stable` by default |

No `rust-toolchain.toml` anywhere, so firmware builds against whatever `stable` is.

Own crates (`firmware/cynthion-boot`, `cynthion-payload`, `cynthion-soc`, `cynthion-soc-pac`) are on current dependencies deliberately (the memory map is ours, so no upstream to stay compatible with): `riscv` 0.16.1, `riscv-rt` 0.18.0, `embedded-hal` 1.0.0, `critical-section` 1.2.0.

`repos/cynthion/firmware/moondancer` is upstream's and six major versions behind: `riscv = "0.10"`, `riscv-rt = "0.11"`, `zerocopy = "0.7"` (own TODO to bump), `rust-version = "1.68"`. Built by the `rust` check.

Release profiles are measured: `opt-level = "z"` is chosen for **speed** because the I-cache dominates (measured at 4 KiB direct-mapped; now 8 KiB 2-way, re-measure: [#167](https://github.com/awtoau/cynthion-workspace/issues/167)). Tables and reasoning are in each `Cargo.toml` `[profile.release]` comment; re-measure with `./dev.py optlevel`.

## RISC-V core generation

Generated, not vendored: [`riscv-core-build.md`](riscv-core-build.md).

| | in use | note |
|---|---|---|
| VexiiRiscv | submodule `repos/vexiiriscv`, gitlink `f8774d4a` | no releases, no tags: the SHA is the only version |
| JDK | OpenJDK 25.0.4 | works; no toolchain freeze |
| sbt launcher | 2.0.4 | |
| sbt (build) | **1.10.0** | pinned by `repos/vexiiriscv/project/build.properties`; the 2.x launcher bootstraps it |
| Scala | 2.12.18 | `repos/vexiiriscv/project/version.conf`, first entry wins |
| SpinalHDL | built from source | `SPINALHDL_FROM_SOURCE=1` by default, from `ext/SpinalHDL` |

`--region` is mandatory when generating: VexiiRiscv's `defaultPma` covers only `0x80000000` and `0x10000000`, so memory at `0x00000000` is in no region and every access traps, including every stack operation. Looks exactly like a dead CPU.

## RISC-V host tooling

| tool | version |
|---|---|
| `riscv64-linux-gnu-gcc` | 16.2.1 (Fedora cross) |
| `riscv64-linux-gnu-*` binutils | 2.46 |
| `qemu-system-riscv32` | 10.2.2 |

- `riscv64-linux-gnu-gcc` targets Linux but produces fine bare-metal objects: header of `scripts/riscv/riscv_firmware.py`.
- QEMU is used only as `-M virt`: it presents an NS16550A at `0x10000000` and a PLIC at `0x0c000000` (driven by `src/intc.rs`). Hence the SoC console is a standard NS16550A (`gateware/soc/peripherals/uart16550.py`): one driver for board and test gate.
- `scripts/soc_test.py` drives it with `memory-qemu.x`. The `qemu` feature without that linker script links `.text` into the flash window, where `virt` has no memory: traps on the first fetch.

## Apollo SAMD11 firmware

| tool | version | note |
|---|---|---|
| `arm-none-eabi-gcc` | 15.2.0 (Fedora) | `/usr/bin` |
| linker used by that gcc | Fedora's binutils | `-print-prog-name=ld` -> `/usr/arm-none-eabi/bin/ld`, **not** the one on PATH |
| `arm-none-eabi-nm` / `size` / `objcopy` on PATH | **2.41.0.20231009** | Arm GNU Toolchain 13.2.rel1 (2023) |

**ARM toolchain: dnf only** (`arm-none-eabi-gcc` 15.2, binutils 2.45). The `/opt/arm-gnu-toolchain` 13.2 directory (binutils only, no gcc) and its `/etc/profile.d` PATH entry were removed 2026-11-06; copy in `/mnt/2tb/wastebasket/arm-gnu-toolchain-13.2-*`. Apollo links 8 B larger with the 2.45 linker (13780 B, ceiling 13784).

- The guards still resolve binutils beside the compiler: `scripts/apollo/arm_binutils_resolve.py`.

## Submodules

`.gitmodules` lists four. `scripts/tools/submodule_patch_audit.py` refuses to let one be removed while it holds work on no remote. `luna`, `facedancer`, `packetry`, `saturn-v` are not submodules.

| submodule | gitlink | fork / source |
|---|---|---|
| `repos/apollo` | `90c8b7b6` | `awtoau/awto-apollo` |
| `repos/cynthion` | `7fa0c6a4` | `awtoau/awto-cynthion` |
| `repos/cynthion-hardware` | `e5cf493e` | `awtoau/awto-cynthion-hardware` (KiCad sources) |
| `repos/vexiiriscv` | `f8774d4a` | `SpinalHDL/VexiiRiscv` directly |

- Drift vs upstream: `git -C repos/<name> log --oneline upstream/main..HEAD | wc -l` (ahead), `HEAD..upstream/main` (behind). Not recorded here: counts go stale. Apollo description: [`apollo-firmware.md`](apollo-firmware.md).
- The `luna-soc` fork pin and its status live in [`upstream-boundary.md`](upstream-boundary.md#luna-soc-fork-pin).
- Worktrees: [`git.md`](git.md#worktrees).

## Upstream maintenance (dormant defaults)

A dormant upstream means patches stay ours. None are archived.

- `greatscottgadgets/apollo`: default branch last moved 2024-12-10 (v1.1.1, 2024-11-23). A `hil-ci` branch moved 2026-05-08, so the repo is not dead.
- `greatscottgadgets/luna-soc`: 2025-05-19 (0.3.2).
- `amaranth-lang/amaranth-soc`: no releases (tag `v0.1a` only); CSR register API tracked by an open RFC ([amaranth-soc#68](https://github.com/amaranth-lang/amaranth-soc/issues/68)), so it keeps drifting from any vendored snapshot.
- `amaranth-lang/amaranth-stdio`: no tags. `SpinalHDL/VexiiRiscv`: no tags.
