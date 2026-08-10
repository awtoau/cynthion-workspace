# cynthion-workspace

Integration workspace for the Cynthion USB proxy stack.

This repo does not contain code — it pins submodule versions, provides fast
dev-cycle check scripts, and owns CI configuration.

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

No virtualenv: packages install into the default free-threaded interpreter, so
plain `python3` and the `cynthion` / `apollo` console scripts all agree. See
[Python strategy](#python-strategy).

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
./scripts/check.py --fast       # skip any check marked slow (none currently are)
./scripts/check.py --parallel   # concurrent
./scripts/check.py --list       # what is available, and what is unavailable here
```

| Check | What |
|-------|------|
| `rust` | `cargo check` + `make clippy` for moondancer (riscv32imac) |
| `socfw` | `cynthion-soc`'s unit tests on this machine, then all 12 target builds |
| `apollo` | SAMD11 firmware build + size report |
| `python` | import check + pytest on the resolved interpreter |
| `freethreading` | asserts the interpreter is free-threaded *and* that no import re-enables the GIL |
| `socmap` | the committed SVD still matches the SoC's memory map |
| `irqlog` | no interrupt handler can reach a console |
| `paths` | no tracked file names one machine's filesystem — this repo is public |

`socfw` runs `cargo test` in `firmware/cynthion-soc-tests`, a host crate that
`#[path]`-includes `cynthion-soc`'s pure modules — the firmware itself is
`no_std`/`no_main` with RISC-V asm and cannot be built for this machine. One of
those tests walks the firmware's source and fails if a `#[cfg(test)]` module is
not included, so coverage cannot be lost by deleting a line (#337).

It then runs `scripts/soc_bin_matrix.py`, which builds every `[[bin]]` the crate
declares in the features that binary declares. `required-features` makes cargo
skip a target entirely when the feature is off, so a plain `cargo check` says
nothing about eleven of the twelve — and both RTIC binaries and the workload
control sat broken on `main` for a day (#362). The target list is read from
`Cargo.toml`, so a new binary is covered when it is declared.

The whole set runs in well under a second. There was a `gateware` check that
elaborated the upstream USB analyzer top out of `repos/cynthion` for
`CynthionPlatformRev0D2` — upstream code, and a board revision this workspace
does not target. It was ~98% of the runtime. The gateware this repo does build
is covered by `socmap`, which elaborates the SoC itself. See #169.

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

3.15 is still beta and no distro packages a free-threaded build of it, so it is
installed via `uv python install 3.15t` or built from source with
`--disable-gil`. `scripts/install.py` resolves the interpreter itself, preferring
`python3.15t` and falling back through `python3.14t` / `python3.15` /
`python3.14` for a machine mid-upgrade.

### PATH ordering matters

Console scripts (`cynthion`, `apollo`) install next to the 3.15t interpreter.
That directory must precede `~/.local/bin`, or a shim left behind by an older
interpreter shadows them and fails with `ModuleNotFoundError`:

```bash
export PATH="$HOME/opt/cpython-315t/bin:$PATH"
```

### Upstream pinning

Upstream `pyproject.toml` files declare open lower bounds (`requires-python =
">=3.9"`) with no upper cap, so 3.15t is permitted — the pins that previously
held this workspace on 3.12/3.14 were all in local tooling, not upstream.

CI runs 3.15t as the job that matters; 3.13 and 3.14 remain in the nightly
matrix only to catch code that accidentally depends on free-threading.
