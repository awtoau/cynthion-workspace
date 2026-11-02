# cynthion-workspace

Agents: start at [docs/START.md](docs/START.md)

Integration workspace for the Cynthion USB proxy stack.

Pins the submodule versions, and holds the SoC gateware (`gateware/`), the SoC firmware
(`firmware/`), the dev-cycle scripts (`scripts/`) and the local check gate
(`scripts/check.py`). Nothing runs on GitHub Actions.

Setup and build: [docs/START.md](docs/START.md).

## Documentation

**Start at [docs/hardware.md](docs/hardware.md).** It is the board index: what every chip
on Cynthion r1.4 is, how it is wired, which bus reaches it, and where the registers come
from. One note per chip under [docs/chips/](docs/chips). Look there before deriving a
hardware fact from a pin dump — it has been derived already, and getting it wrong is the
common outcome.

Deeper investigations, by area:

- [docs/apollo_samd11_mcu](docs/apollo_samd11_mcu) — the debug MCU and its firmware
- [linux-on-cynthion](linux-on-cynthion) — booting Linux on this board: analysis, plan, sweeps
- [docs/chips/ecp5](docs/chips/ecp5) — flash, HyperRAM, USB and BRAM in depth
- [docs/chips/vexiiriscv-cpu.md](docs/chips/vexiiriscv-cpu.md) — the soft CPU
- [docs/upstream-boundary.md](docs/upstream-boundary.md) — what we take from upstream and
  what we have replaced

## Quick start

```bash
git clone --recurse-submodules https://github.com/awtoau/cynthion-workspace
cd cynthion-workspace
./dev.py setup                  # one-time: submodules, Rust, Python, udev
./dev.py doctor                 # verify every tool is on PATH
./dev.py gate                   # run the checks before every commit
```

`./dev.py` is the one entry point: `--help` for humans, `describe` for agents.
Nothing else needs to be memorised, and `./dev.py audit` says what every other
script in `scripts/` is and whether anything still reaches it.

`scripts/` layout:
- top level: entry points and shared libraries (`dev.py`, `check.py`, `soc_run.py`, `soc_test.py`, `tio_user.py`, `devlog.py`, ...)
- subdirectories: `hyperram/` `soc/` `riscv/` `fabric/` `apollo/` `sideband/` `issues/` `board/` `tools/` `diamond/`
- scripts import each other by bare module name; `import _paths` puts every subdirectory on `sys.path`
- a script in a subdirectory first inserts `scripts/` (its `ROOT / "scripts"` line), then imports `_paths`
- `dev.py` finds a script by bare name (`script("hyperram_verify.py")`); `_paths.find_script(name)` does the same

No virtualenv: packages install into the default free-threaded interpreter, so
plain `python3` and the `cynthion` / `apollo` console scripts all agree. See
[Python strategy](#python-strategy).

### Apollo firmware

The SoC needs **our Apollo fork**, not upstream's. `cynthion update` replaces it
with upstream's; `soc_run.py` then refuses to configure.
Check and restore: [`docs/apollo-firmware.md`](docs/apollo-firmware.md).

### The board console

Start it once and leave it running:

```bash
./scripts/tio_user.py           # attaches, and serves on TCP 9000
```

**One process can read a tty, and this is that process.** It fans the stream out
on port 9000 by default, so everything else -- `soc_run.py`, `soc_shell.py`, an
agent -- reads *and writes* through the socket while your terminal stays
attached. Nothing has to take the port off you, and nothing has to be restarted
to let something else in.

Without it running, tools open the tty directly, which is fine when nothing else
has it. What is NOT fine is two readers at once: the bytes interleave, each takes
what the other never sees, and the result reads as a dead board rather than as a
contended port. `soc_shell.py` names the offending process when it detects this.

`--no-serve` makes the tty exclusive to your terminal, which is occasionally what
you want and is never the default.

## Repository map

| Path | Repo | Upstream | Contents |
|------|------|----------|----------|
| `repos/cynthion` | awtoau/awto-cynthion | greatscottgadgets/cynthion | Firmware (Rust), gateware (Python/Amaranth), Python host library |
| `repos/apollo` | awtoau/awto-apollo | greatscottgadgets/apollo | Apollo ARM MCU firmware (C/TinyUSB) |
| `repos/cynthion-hardware` | awtoau/awto-cynthion-hardware | greatscottgadgets/cynthion-hardware | KiCad schematics and PCB layout |
| `repos/vexiiriscv` | SpinalHDL/VexiiRiscv (no fork) | SpinalHDL/VexiiRiscv | The SoC's RISC-V CPU generator (Scala/SpinalHDL, `ext/SpinalHDL` recursed) |

Those four are the only submodules (`.gitmodules`). `luna`, `facedancer`, `packetry` and
`saturn-v` are not submodules; the mirrors below are reference copies only.

## Local mirrors

Upstream repos are mirrored at `~/git_mirror/greatscottgadgets/` for offline access and
reference (KiCad files, upstream history). The `cynthion-hardware` schematics also have a
standalone copy at `~/git_mirror/cynthion-hardware/`.

```
~/git_mirror/greatscottgadgets/
  apollo/               ARM MCU firmware upstream
  cynthion/             gateware + Python lib upstream
  cynthion-hardware/    KiCad schematics + PCB
  facedancer/           Facedancer upstream
  luna/                 LUNA gateware upstream
  packetry/             USB capture tool upstream
  saturn-v/             DFU bootloader upstream
~/git_mirror/cynthion-hardware/   standalone KiCad copy
~/git_mirror/packetry/            standalone packetry copy
```

## Checks run locally, not on GitHub

**Nothing in this repo runs on GitHub Actions.** The workflows have been deleted
outright — there is no `.github/workflows/` here, by design. Checks run natively
on the dev machine, against the real toolchain, the real hardware, and the real
free-threaded interpreter — no Docker, no cloud runners, no queue.

Do not add workflow files back. If something needs automating, extend
`scripts/check.py`.

```bash
./scripts/check.py              # everything
./scripts/check.py rust python  # just those
./scripts/check.py --fast       # skip checks marked slow (`featureiso`, `repro`)
./scripts/check.py --parallel   # concurrent
./scripts/check.py --list       # what is available, and what is unavailable here
```

`./scripts/check.py --list` is the source of truth (13 checks, plus which are unavailable on this machine):

| Check | What |
|-------|------|
| `rust` | `cargo check` + `make clippy` for moondancer (riscv32imac) |
| `socfw` | `cynthion-soc`'s unit tests on this machine, then all 12 target builds |
| `apollo` | SAMD11 firmware build + size report |
| `python` | import check + pytest on the resolved interpreter |
| `freethreading` | asserts the interpreter is free-threaded *and* that no import re-enables the GIL |
| `amaranthsoc` | `amaranth_soc` is the real package, not luna_soc's vendored copy |
| `featureiso` | no spike feature reaches the shipping shell (slow) |
| `socmap` | the committed SVD still matches the SoC's memory map |
| `review` | the SoC review pass can still report a regression |
| `repro` | two elaborations of this tree give one RTLIL digest (slow) |
| `irqlog` | no interrupt handler can reach a console |
| `paths` | no tracked file names one machine's filesystem; this repo is public |
| `doclinks` | every doc link resolves and every issue ref is one |

`socfw` runs `cargo test` in `firmware/cynthion-soc-tests`, a host crate that
`#[path]`-includes `cynthion-soc`'s pure modules — the firmware itself is
`no_std`/`no_main` with RISC-V asm and cannot be built for this machine. One of
those tests walks the firmware's source and fails if a `#[cfg(test)]` module is
not included, so coverage cannot be lost by deleting a line (#337).

It then runs `scripts/soc/soc_bin_matrix.py`, which builds every `[[bin]]` the crate
declares in the features that binary declares. `required-features` makes cargo
skip a target entirely when the feature is off, so a plain `cargo check` says
nothing about eleven of the twelve — and both RTIC binaries and the workload
control sat broken on `main` for a day (#362). The target list is read from
`Cargo.toml`, so a new binary is covered when it is declared.

There is no `gateware` check on upstream's USB analyzer top (upstream code, a board
revision this workspace does not target, ~98% of the runtime). The gateware this repo
builds is covered by `socmap`, which elaborates the SoC itself. See #169.

Exit status is 0 only if every selected check passed, so it works as a hook:

```bash
ln -s ../../scripts/check.py .git/hooks/pre-push
```

Each check writes its full output to `tmp/logs/check-<name>.log`. A check whose
tooling is absent is reported as skipped, not failed, so the runner stays usable
on a partially-provisioned machine.

**Gotcha:** do not run anything that imports `cynthion` from `repos/cynthion/`.
That directory contains a `cynthion/` subdirectory which Python treats as a
namespace package, shadowing the installed one — `cynthion.__file__` becomes
`None` and `cynthion.shared.usb` fails to resolve. The workspace root works, as
does `repos/cynthion/cynthion/python`.

## Filing on the tracker

**One command, and the scrub is in it — never `gh issue` by hand.**

```bash
./scripts/issues/gh_post.py issue   --title "..." --body-file tmp/x.md --label p0
./scripts/issues/gh_post.py comment 498 --body-file tmp/x.md
./scripts/issues/gh_post.py close   498 --body-file tmp/x.md
```

It refuses, before any network call, on a private path (the patterns come from
`private_path_check.py`, so there is one set), on a credential, and on anything
naming work that stays off a public tracker. `awtoau/cynthion-workspace` is
pre-approved and is the default; **every other repo is refused**, naming the
approval that would be needed. `--dry-run` scrubs and files nothing.

## Python strategy

**The workspace runs on free-threaded (no-GIL) CPython 3.15t, installed as the
default `python3`. There is no virtualenv.**

Free-threading is not incidental here: the parallel build path in
`scripts/install.py` and `check.py --parallel` use real threads, and the
firmware builds are the main beneficiary. A GIL-enabled interpreter works but
serialises that work.

| | |
|---|---|
| Interpreter | CPython **3.15t** free-threaded |
| Environment | System / default — no venv to create or activate |
| Install | `python3 -m pip install -e repos/cynthion/cynthion/python` |
| Verify | `python3 -c "import sys; print(sys._is_gil_enabled())"` → `False` |
| Override | `CYN_PYTHON=/path/to/python` |

No distro packages a free-threaded build of 3.15, so it is
installed via `uv python install 3.15t` or built from source with
`--disable-gil`. `scripts/install.py` resolves the interpreter itself, preferring
`python3.15t` and falling back through `python3.14t` / `python3.15` /
`python3.14` for a machine mid-upgrade.

### PATH ordering matters

Console scripts (`cynthion`, `apollo`) install next to the 3.15t interpreter.
That directory must precede `~/.local/bin`, or a shim left behind by an older
interpreter shadows them and fails with `ModuleNotFoundError`:

```bash
export PATH="$HOME/opt/cpython-ft/bin:$PATH"
```

### Upstream pinning

Upstream `pyproject.toml` files declare open lower bounds (`requires-python =
">=3.9"`) with no upper cap, so 3.15t is permitted — the pins that previously
held this workspace on 3.12/3.14 were all in local tooling, not upstream.
