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
- [docs/luna_ecp5_fpga](docs/luna_ecp5_fpga) — flash, HyperRAM, USB and BRAM in depth
- [docs/moondancer](docs/moondancer) — the soft CPU
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

## Repository map

| Path | Repo | Upstream | Contents |
|------|------|----------|----------|
| `repos/cynthion` | awtoau/awto-cynthion | greatscottgadgets/cynthion | Firmware (Rust), gateware (Python/Amaranth), Python host library |
| `repos/apollo` | awtoau/awto-apollo | greatscottgadgets/apollo | Apollo ARM MCU firmware (C/TinyUSB) |
| `repos/luna` | awtoau/awto-luna | greatscottgadgets/luna | LUNA USB gateware library |
| `repos/saturn-v` | awtoau/awto-saturn-v | greatscottgadgets/saturn-v | Apollo DFU bootloader |
| `repos/packetry` | awtoau/awto-packetry | greatscottgadgets/packetry | USB capture + analysis tool |
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
./scripts/check.py --fast       # skip slow checks (~4 s)
./scripts/check.py --parallel   # concurrent
./scripts/check.py --list       # what is available, and what is unavailable here
```

| Check | What |
|-------|------|
| `rust` | `cargo check` + `make clippy` for moondancer (riscv32imac) |
| `apollo` | SAMD11 firmware build + size report |
| `python` | import check + pytest on the resolved interpreter |
| `freethreading` | asserts the interpreter is free-threaded *and* that no import re-enables the GIL |
| `socmap` | the committed SVD still matches the SoC's memory map |
| `irqlog` | no interrupt handler can reach a console |
| `paths` | no tracked file names one machine's filesystem — this repo is public |
| `gateware` | analyzer gateware elaboration (dry run), ~15 s |

Exit status is 0 only if every selected check passed, so it works as a hook:

```bash
ln -s ../../scripts/check.py .git/hooks/pre-push
```

Each check writes its full output to `tmp/logs/check-<name>.log`. A check whose
tooling is absent is reported as skipped, not failed, so the runner stays usable
on a partially-provisioned machine.

**Gotcha:** do not run gateware elaboration from `repos/cynthion/`. That directory
contains a `cynthion/` subdirectory which Python treats as a namespace package,
shadowing the installed one — `cynthion.__file__` becomes `None` and
`cynthion.shared.usb` fails to resolve. The workspace root works, as does
`repos/cynthion/cynthion/python`.

## Python strategy

**The workspace runs on free-threaded (no-GIL) CPython 3.15t, installed as the
default `python3`. There is no virtualenv.**

Free-threading is not incidental here: the parallel build path in
`scripts/install.py` uses real threads, and gateware elaboration is the main
beneficiary. A GIL-enabled interpreter works but serialises that work.

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
