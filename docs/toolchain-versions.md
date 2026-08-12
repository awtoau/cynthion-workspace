# Toolchain versions and the constraints on them

What every layer of this project's toolchain is, and the rules that govern which
versions are usable. Versions verified 2026-08-05 by running the tools, reading
the lockfiles, and comparing against upstream.

**This file records versions and constraints, not plans.** Whether something
*should* be upgraded belongs in an issue, not here.

`./dev.py doctor` checks that every tool named below is on PATH.

## FPGA toolchain

One OSS CAD Suite build supplies all three. There is no separate installation.

| tool | in use | upstream | note |
|---|---|---|---|
| OSS CAD Suite | `20260522` | nightly tags, `2026-08-04` latest | nightly-only; there is no stable channel, so "the release" is whichever nightly is pinned |
| Yosys | `0.65+57` (`9d0cdb855`) | `0.67` (2026-07-09) | a post-0.65 dev build, two minor releases behind |
| nextpnr-ecp5 | `0.10-74` (`ee605e2b`) | `0.10` (2026-03-12) | **ahead of the last release**, 74 commits past the tag |
| ecppack / prjtrellis | `1.4-79` (`56bb170`) | `1.4` (2023-05-16) | ahead of the tag; prjtrellis has not cut a release in over three years and its commits are build/packaging cleanups |

Installed at `~/opt/oss-cad-suite`, sourced via `environment`. `docs/install.md`
carries the download URL.

## Python

Free-threaded CPython, installed as the default `python3`. **No virtualenv** —
see the Python strategy section of the root `README.md` for why, and for the PATH
ordering that console scripts depend on.

| | |
|---|---|
| interpreter | CPython **3.15.0b3**, free-threading build |
| GIL | disabled (`sys._is_gil_enabled()` → `False`) |
| location | `~/opt/cpython-315t/bin/python3` |
| override | `CYN_PYTHON` |

3.15 is beta and no distro packages a free-threaded build of it.
`scripts/install.py` resolves the interpreter itself, preferring `python3.15t`
and falling back through `python3.14t` / `python3.15` / `python3.14`.

**No upstream dependency declares support for 3.15, or tests free-threading.**
Amaranth and amaranth-soc have no issue, PR or CI job mentioning no-GIL or 3.15.
This project is ahead of its dependencies here and gets no upstream signal; the
`freethreading` check in `scripts/check.py` is the only thing asserting that no
import silently re-enables the GIL.

### Packages

Every package below is at the newest version its index offers.
`pip list --outdated` is empty.

| package | installed | newest available | source |
|---|---|---|---|
| `amaranth` | 0.5.9 | 0.5.9 | PyPI |
| `amaranth-soc` | `0.1a1.dev32+g3e3d8b7` | `3e3d8b7` is upstream `main` HEAD | **git only** |
| `amaranth-stdio` | `0.1.dev37+gd296ba4` | `d296ba4` is upstream `main` HEAD | **git only** |
| `luna-usb` | 0.2.3 | 0.2.3 | PyPI |
| `luna-soc` | `0.3.2+awto.1` | 0.3.2 | `awtoau/awto-luna-soc` fork |
| `usb-protocol` | 0.9.2 | 0.9.2 | PyPI |
| `cynthion` | `0.2.4.post30+git.edf35c99.dirty` | — | editable, from `repos/cynthion` |
| `apollo-fpga` | `1.1.1.post3+git.39a2213a.dirty` | — | editable, from `repos/apollo` |
| `pyusb` 1.3.1, `pyserial` 3.5, `pyvcd` 0.4.1, `numpy` 2.5.1, `pytest` 9.1.1 | | | PyPI |

`facedancer` is **not installed** in the live environment. Nothing in
`gateware/` or `scripts/` imports it; the patches recorded against it in
`docs/upstream-boundary.md` apply to a facedancer install, not to this one.

`amaranth-boards` is **not installed** either. The two constructors that were
needed from it, `LEDResources` and `ULPIResource`, are copied into
`gateware/board/resources.py`.

### Constraints that must hold

**`pip install amaranth-soc` from PyPI does not get you amaranth-soc.** The PyPI
name is a placeholder: version `0`, uploaded 2021, no modules, and `import
amaranth_soc` then raises `ModuleNotFoundError`. It has to come from git:

    amaranth-soc @ git+https://github.com/amaranth-lang/amaranth-soc.git

`amaranth-stdio` is the same shape and worse — the PyPI entry is version `0` and
the repository carries **no tags at all**, so there is no version to pin to.

**Real `amaranth-soc` must stay installed, or `luna_soc` silently substitutes its
own.** `luna_soc/__init__.py` appends its vendored tree to `sys.path` only when
the real package is missing:

```python
try:
    import amaranth_soc
    import amaranth_stdio
except:
    sys.path.append(.../gateware/vendor)
```

So the presence of real `amaranth-soc` is what keeps every design — ours *and*
luna_soc's own peripherals — bound to upstream. Remove it and the vendored copy
comes back with no error and no version string. The vendored tree was last
re-synced 2025-01-07 and is missing four upstream fixes, including the Python
3.14 annotation support in
[`d8b5892`](https://github.com/amaranth-lang/amaranth-soc/commit/d8b58925533d9dd6be64a2ca9993bfe3a6d46ae9)
(2026-01-28), without which the documented `csr.Field` annotation form fails:

    class Probe(csr.Register, access="rw"):
        value: csr.Field(csr.action.RW, 8)

    TypeError: Field collection must be a dict, list, or Field, not None

Both are declared in `scripts/machine_setup.py`, pinned to the commits above, so
a fresh environment gets them rather than the vendored fallback. Until #190
nothing declared either, and a clean install silently used the 2025 vendored
tree. `scripts/amaranth_soc_check.py` — the `amaranthsoc` check in
`scripts/check.py` — asserts neither name resolves inside
`luna_soc/gateware/vendor`, because a declaration is invisible if it is dropped:

    ./scripts/amaranth_soc_check.py --simulate-vendored   # proves it still fails

**Version tags on the fork must use the PEP 440 local form.** `0.3.2+awto.1`
works; `0.3.2-awto.1` breaks the wheel build outright, because
`setuptools-git-versioning` parses the tag through `packaging.version.Version`,
which rejects a hyphen. `luna-soc` derives its version from git tags, so a fork
with no tags reports `starting_version = "0.2.0"` while actually containing
0.3.2 — a package that misreports its version reads exactly like a stale
dependency.

**Editable installs are the difference between editing code and editing
decoration.** `apollo_fpga`, `awto_probe` and `cynthion` are installed editable,
so edits under `repos/` take effect. `luna` and `luna_soc` are **not**, so
`import luna` resolves to site-packages and the checkout under `repos/` is
inert.

**Do not run anything that imports `cynthion` from inside `repos/cynthion/`.**
That directory contains a `cynthion/` subdirectory which Python treats as a
namespace package, shadowing the installed one: `cynthion.__file__` becomes
`None` and `cynthion.shared.usb` fails to resolve.

### Two CSR behaviours that read as version skew and are not

Both are correct-by-design and cost time to distinguish from a broken dependency:

- `csr.action.RW` fields cannot have `r_data` driven by gateware. RW means
  software owns the value; returning a read byte through one is an elaboration
  error. Split write-data and read-data into separate registers.
- `csr.Bridge` already adds every register in the builder as a submodule. Adding
  them again raises `DuplicateElaboratable`.

## Rust firmware

| | in use | current |
|---|---|---|
| `rustc` / `cargo` | 1.97.0 (2026-07-07) | 1.97.0 |
| target | `riscv32imac-unknown-none-elf` | installed |
| toolchain file | **none** — `stable` by default | |

No `rust-toolchain.toml` exists anywhere in the tree, so the firmware builds
against whatever `stable` currently is.

Our own firmware crates (`firmware/cynthion-boot`, `cynthion-payload`,
`cynthion-soc`, `cynthion-soc-pac`) are on current dependencies, and deliberately
so — the memory map is ours, so there is no upstream to stay compatible with:

| crate | locked | newest |
|---|---|---|
| `riscv` | 0.16.1 | 0.16.1 |
| `riscv-rt` | 0.18.0 | 0.18.0 |
| `embedded-hal` | 1.0.0 | 1.0.0 |
| `critical-section` | 1.2.0 | |

`repos/cynthion/firmware/moondancer` is upstream's firmware and is **six major
versions behind on both**: `riscv = "0.10"`, `riscv-rt = "0.11"`, plus
`zerocopy = "0.7"` carrying its own TODO to bump. It declares `rust-version =
"1.68"`. It is built by the `rust` check in `scripts/check.py`.

Release profiles are measured, not conventional — `opt-level = "z"` is chosen for
**speed**, because the I-cache dominates (4 KiB direct-mapped when measured;
now 8 KiB 2-way, so re-measure — #167). The tables and
their reasoning are in the `[profile.release]` comments of each `Cargo.toml`;
re-measure with `./dev.py optlevel` if the cache geometry changes.

## RISC-V core generation

The core is **generated, not vendored** — see `docs/riscv-core-build.md`.

| | in use | note |
|---|---|---|
| VexiiRiscv | `f8774d4` (2026-07-20) | pinned as a submodule; upstream `dev` is 48 commits ahead as of 2026-08-05 |
| JDK | OpenJDK 25.0.4 | verified working; there is no toolchain freeze |
| sbt launcher | 2.0.4 | |
| sbt (build) | **1.10.0** | pinned by `repos/vexiiriscv/project/build.properties`; the 2.x launcher bootstraps it |
| Scala | 2.12.18 | `repos/vexiiriscv/project/version.conf`, first entry wins |
| SpinalHDL | built from source | `SPINALHDL_FROM_SOURCE=1` by default, from `ext/SpinalHDL` |

**VexiiRiscv has no releases and no tags.** The submodule SHA is the only
version there is, and upstream `dev` moves daily.

`--region` is not optional when generating. VexiiRiscv's `defaultPma` covers only
`0x80000000` and `0x10000000`, so a design with memory at `0x00000000` is in no
region at all and every access traps, including every stack operation. The
failure looks exactly like a dead CPU.

## RISC-V host tooling

| tool | version |
|---|---|
| `riscv64-linux-gnu-gcc` | 16.1.1 (Fedora cross 16.1.1-1) |
| `riscv64-linux-gnu-*` binutils | 2.46 |
| `qemu-system-riscv32` | 10.2.2 |

`riscv64-linux-gnu-gcc` targets Linux but produces fine bare-metal objects; see
the header of `scripts/riscv_firmware.py`.

QEMU is used only as `-M virt`, and the pairing is deliberate: `virt` presents an
NS16550A at `0x10000000` and a PLIC at `0x0c000000`, which is why the SoC console
is a standard NS16550A (`gateware/soc/peripherals/uart16550.py`) — one driver serves both
the board and the test gate. `scripts/soc_test.py` drives it, with
`memory-qemu.x`; building the `qemu` feature without that linker script links
`.text` into the flash window, where `virt` has no memory, and produces an image
that traps on the first fetch.

## Apollo SAMD11 firmware

| tool | version | note |
|---|---|---|
| `arm-none-eabi-gcc` | 15.2.0 (Fedora) | `/usr/bin` |
| linker used by that gcc | Fedora's own binutils | `-print-prog-name=ld` → `/usr/arm-none-eabi/bin/ld`, **not** the one on PATH |
| `arm-none-eabi-nm` / `size` / `objcopy` on PATH | **2.41.0.20231009** | Arm GNU Toolchain 13.2.rel1, from 2023 |

**The ARM toolchain is split, and the split is invisible.**
`/opt/arm-gnu-toolchain/…-13.2.Rel1/bin` precedes `/usr/bin` on PATH and contains
binutils but **no gcc**. Compilation and linking are therefore consistent — gcc
finds its own bundled `ld` regardless of PATH — but any script that shells out to
a binutil by bare name gets the 2023 build, reading an ELF produced by a compiler
ten major versions newer.

The three guards do not shell out by bare name. `scripts/arm_binutils_resolve.py` resolves them beside the
compiler — `arm-none-eabi-gcc -print-prog-name=nm` → 2.45 — and `verify_vectors.py`
(`nm`), `apollo_budget_check.py` (`size`) and `apollo_memory_report.py`
(`size`, `nm`) each print which binary they used and warn that PATH would have
given the 2023 one. `size` needs the fallback path: gcc never invokes it, so
`-print-prog-name=size` answers with the bare name and the install prefix is
derived from a tool gcc does know. On the current build both versions agree
byte-for-byte on `size -A` and `nm`, so nothing was being misreported *yet*.

The PATH entry itself is untouched; see #191 for whether it should be.

LTO is enabled and load-bearing for the flash budget; `verify_vectors.py` guards
a silent failure mode it can introduce.

**TinyUSB is pinned at `5b08a65` (2023-09-27) — 3915 commits behind master.**
That pin is upstream Apollo's, in `repos/apollo/.gitmodules`, not ours. Latest
TinyUSB release is 0.21.0 (2026-06-30). The SAMD11's flash budget is the
constraint on moving it.

## Submodules

`repos/` pins four. `scripts/submodule_patch_audit.py` refuses to let one be
removed while it holds work that is on no remote.

| submodule | pinned | fork | upstream default branch | drift |
|---|---|---|---|---|
| `repos/apollo` | `69c6ba8` (2026-08-03) | `awtoau/awto-apollo` | last moved **2024-12-10** | **60 ahead, 0 behind** |
| `repos/cynthion` | `7fa0c6a` (2026-07-28) | `awtoau/awto-cynthion` | 2026-05-22 (`0.2.5`) | 25 ahead, 1 behind |
| `repos/vexiiriscv` | `f8774d4` (2026-07-20) | upstream directly | `dev`, 2026-07-27 | 48 behind |
| `repos/cynthion-hardware` | `e5cf493` | `awtoau/awto-cynthion-hardware` | KiCad sources | — |

`repos/cynthion`'s Python package still pins the luna-soc fork:

    "luna-soc @ git+https://github.com/awtoau/awto-luna-soc.git@main",

`gateware/soc/top.py` is the one design that still imports
`cynthion` for its platform (`cynthion.gateware.platform.cynthion_r1_4`) rather
than the vendored `gateware/board/`, so it reaches that pin. Every
other design uses the vendored platform, which imports only `amaranth` and
`amaranth.build`.

## Upstream maintenance status

Relevant because a dormant upstream means patches stay ours indefinitely, and
`docs/upstream-boundary.md` records what has already been replaced.

| upstream | last commit to default branch | latest release |
|---|---|---|
| `greatscottgadgets/apollo` | **2024-12-10** | v1.1.1 (2024-11-23) |
| `greatscottgadgets/luna-soc` | **2025-05-19** | 0.3.2 (2025-05-19) |
| `greatscottgadgets/luna` | 2025-08-22 | 0.2.3 (2025-08-22) |
| `greatscottgadgets/cynthion` | 2026-05-22 | 0.2.5 (2026-05-22) |
| `greatscottgadgets/facedancer` | 2026-05-22 | 3.1.3 (2026-05-22) |
| `amaranth-lang/amaranth` | 2026-07-16 | v0.5.9 (2026-07-16) |
| `amaranth-lang/amaranth-soc` | 2026-05-23 | none — tag `v0.1a` only |
| `amaranth-lang/amaranth-stdio` | 2026-01-27 | none — no tags |
| `amaranth-lang/amaranth-boards` | 2026-08-01 | v0.0.20 (2026-08-01) |
| `YosysHQ/yosys` | 2026-08-04 | v0.67 (2026-07-09) |
| `YosysHQ/nextpnr` | 2026-08-04 | nextpnr-0.10 (2026-03-12) |
| `YosysHQ/prjtrellis` | 2026-05-09 | **1.4 (2023-05-16)** |
| `SpinalHDL/VexiiRiscv` | 2026-07-27 | none — no tags |

None are archived. Apollo's `main` is frozen, but a `hil-ci` branch moved
2026-05-08, so the repository is not dead — only its default branch is.
`amaranth-soc`'s CSR register API is still tracked by an open RFC
([#68](https://github.com/amaranth-lang/amaranth-soc/issues/68)) and has no
frozen release, so it will keep drifting from any vendored snapshot.
