# cynthion-workspace

Integration workspace for the Cynthion USB proxy stack.

This repo does not contain code — it pins submodule versions, provides fast
dev-cycle check scripts, and owns CI configuration.

## Documentation

- Consolidated documentation snapshot: [docs/full.md](docs/full.md)
- Hardware-specific docs:
  - [docs/apollo_samd11_mcu](docs/apollo_samd11_mcu)
  - [docs/luna_ecp5_fpga](docs/luna_ecp5_fpga)
  - [docs/moondancer](docs/moondancer)

## Quick start

```bash
git clone --recurse-submodules https://github.com/awtoau/cynthion-workspace
cd cynthion-workspace
./scripts/setup-dev.sh          # one-time: packages + toolchain checks
./scripts/install.py prereqs    # verify the environment
./scripts/check.py --fast       # run the checks before every commit
```

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
| `repos/facedancer` | awtoau/awto-facedancer | greatscottgadgets/facedancer | Patched Facedancer host library |
| `repos/packetry` | awtoau/awto-packetry | greatscottgadgets/packetry | USB capture + analysis tool |
| `repos/cynthion-hardware` | awtoau/awto-cynthion-hardware | greatscottgadgets/cynthion-hardware | KiCad schematics and PCB layout |
| `app/` | *(in-tree)* | — | Flutter dashboard — topology graph, TTY log, power rails |

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

**Nothing in this repo runs on GitHub Actions.** Both workflows are disabled;
their YAML is kept only as a reference for what the local runner reproduces.
Checks run natively on the dev machine, against the real toolchain and the real
free-threaded interpreter — no Docker, no cloud runners, no queue.

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
| `flutter` | `analyze` + `test` (reported, non-blocking) |
| `gateware` | analyzer elaboration — **currently broken upstream**, see below |

Exit status is 0 only if every selected check passed, so it works as a hook:

```bash
ln -s ../../scripts/check.py .git/hooks/pre-push
```

Each check writes its full output to `tmp/logs/check-<name>.log`. A check whose
tooling is absent is reported as skipped, not failed, so the runner stays usable
on a partially-provisioned machine.

**Known-broken:** `gateware` fails because `cynthion/python/src/shared/` is empty
and untracked upstream, so `top.py`'s `cynthion.shared.usb.bVendorId` has nothing
to resolve against. It is excluded from `--fast` but deliberately still listed.

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
| Install | `python3 -m pip install -e repos/cynthion/cynthion/python -e repos/facedancer` |
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
