#!/usr/bin/env python3
"""Test whether the real ``amaranth-soc`` package is a drop-in for luna_soc's
vendored copy.

Builds a throwaway venv under ``tmp/`` (never the system environment), installs
real ``amaranth-soc`` plus ``amaranth`` and ``luna-usb`` into it, and then tries
to elaborate the two designs that actually depend on the vendored copy:

  * ``ecp5-test/i2c/multiplexed.py``    - a CSR-bus peripheral, the simplest case
  * ``ecp5-test/riscv/hello_soc.py``    - a full SoC: wishbone Decoder, blockram,
                                          VexRiscv, and a console peripheral

The interesting question is not "does amaranth_soc import" but "does the CSR /
wishbone API our designs call still exist with the same shapes". So each probe
exercises the specific API surface rather than just importing.

No timeouts or sleeps: pip and the elaboration probes are bounded local
operations that terminate on their own. pip is given a generous subprocess
budget only because a cold wheel download is network-bound; on expiry the run
is reported as a failure to install rather than silently continuing.

Results to tmp/logs/amaranth_soc_dropin_test.log and the terminal.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TMP = REPO_ROOT / "tmp"
VENV_DIR = TMP / "amaranth-soc-dropin-venv"
LOG_DIR = TMP / "logs"
AEST = timezone(timedelta(hours=10))

# pip needs to fetch wheels over the network on a cold cache. 900 s is the
# budget for that download+build; on expiry we report an install failure rather
# than pretending the probe ran, because a half-built venv would give
# meaningless import errors that look like API breakage.
PIP_BUDGET_S = 900

LINES: list[str] = []


def emit(msg: str = "") -> None:
    print(msg)
    LINES.append(msg)


def run(args: list[str], cwd: Path | None = None, env: dict | None = None,
        budget: int | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=budget,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# Probes.  Each is a self-contained script run inside the throwaway venv.
# --------------------------------------------------------------------------

PROBE_IMPORT = r'''
import amaranth_soc, amaranth
print("amaranth      =", amaranth.__version__)
print("amaranth_soc  =", getattr(amaranth_soc, "__version__", "(no __version__)"))
print("amaranth_soc  @", amaranth_soc.__file__)
import amaranth_soc.csr, amaranth_soc.wishbone
print("csr/wishbone import OK")
'''

# The exact CSR idiom ecp5-test/i2c/multiplexed.py uses: class-level
# csr.Field annotations, which is what the py3.14 annotation bug broke.
PROBE_CSR_ANNOTATION = r'''
from amaranth.hdl import unsigned
from amaranth_soc import csr

class Control(csr.Register, access="w"):
    start : csr.Field(csr.action.W, unsigned(1))
    stop  : csr.Field(csr.action.W, unsigned(1))

class Status(csr.Register, access="r"):
    busy  : csr.Field(csr.action.R, unsigned(1))
    ack   : csr.Field(csr.action.R, unsigned(1))

c = Control()
s = Status()
print("class-level csr.Field annotations OK")
# Iterating a Register yields (path, field) pairs, so unpack two, not three.
print("Control fields:", [tuple(p) for p, _ in c])
print("Status  fields:", [tuple(p) for p, _ in s])
'''

# The multiplexed.py structure: a csr.Builder with several registers, frozen
# and turned into a memory map.  This is the peripheral-construction path.
PROBE_CSR_BUILDER = r'''
from amaranth.hdl import unsigned
from amaranth_soc import csr
from amaranth_soc.memory import MemoryMap

class Control(csr.Register, access="w"):
    start : csr.Field(csr.action.W, unsigned(1))

class Data(csr.Register, access="rw"):
    byte : csr.Field(csr.action.RW, unsigned(8))

b = csr.Builder(addr_width=8, data_width=8)
b.add("control", Control())
b.add("data", Data())
bridge = csr.Bridge(b.as_memory_map())
print("csr.Builder + csr.Bridge OK, addr_width =", bridge.bus.addr_width)
'''

# The hello_soc.py structure: wishbone Decoder with a CSR bridge and a blockram
# behind it.  Decoder.add() is the call the import-order hack exists to protect.
PROBE_WISHBONE_DECODER = r'''
from amaranth.hdl import unsigned
from amaranth_soc import csr, wishbone
from amaranth_soc.wishbone import Decoder
from amaranth_soc.memory import MemoryMap

class Tx(csr.Register, access="w"):
    data : csr.Field(csr.action.W, unsigned(8))

b = csr.Builder(addr_width=8, data_width=8)
b.add("tx", Tx())
bridge = csr.Bridge(b.as_memory_map())

dec = Decoder(addr_width=30, data_width=32, granularity=8, features={"cti","bte"})

# The bridge lives in amaranth_soc.csr.wishbone, which is exactly the path
# ecp5-test/riscv/hello_soc.py:171 already imports.
from amaranth_soc.csr.wishbone import WishboneCSRBridge
adapter = WishboneCSRBridge(bridge.bus, data_width=32)
dec.add(adapter.wb_bus, addr=0x0000, name="console")
print("wishbone.Decoder.add(WishboneCSRBridge) OK")
print("decoder bus signature:", type(dec.bus).__name__)

# wishbone.Arbiter too, hello_soc.py:178 uses it.
arb = wishbone.Arbiter(addr_width=30, data_width=32, granularity=8,
                       features={"cti","bte"})
print("wishbone.Arbiter OK")
'''

# Does real amaranth-soc provide the things luna_soc supplies that are NOT
# amaranth_soc at all?  These are the genuine gaps if we drop luna_soc.
PROBE_LUNA_ONLY = r'''
missing = []
for mod, name in [
    ("amaranth_soc.blockram", "blockram (luna_soc.gateware.core.blockram)"),
    ("amaranth_soc.cpu", "cpu / VexRiscv (luna_soc.gateware.cpu)"),
]:
    try:
        __import__(mod)
        print("PRESENT:", name)
    except ImportError as e:
        missing.append((name, str(e)))
for name, err in missing:
    print("ABSENT :", name, "->", err)
'''


# The decisive test: import and elaborate the REAL ecp5-test/i2c/multiplexed.py
# against real amaranth-soc, with the luna_soc import stubbed out. If this
# converts to RTLIL the peripheral is genuinely portable and the luna_soc
# dependency there is purely the sys.path aliasing hack.
PROBE_REAL_MULTIPLEXED = r'''
import sys, types
from pathlib import Path

# Stub luna_soc so the "aliases amaranth_soc" import is a no-op. Real
# amaranth_soc is already installed standalone in this venv, so nothing needs
# aliasing -- which is exactly the point being tested.
for name in ("luna_soc", "luna_soc.gateware", "luna_soc.gateware.core",
             "luna_soc.gateware.core.blockram"):
    sys.modules[name] = types.ModuleType(name)

sys.path.insert(0, str(Path(REPO) / "ecp5-test" / "i2c"))
import multiplexed
print("imported ecp5-test/i2c/multiplexed.py OK")

dut = multiplexed.MultiplexedI2C()
print("MultiplexedI2C() constructed OK")
print("bus signature members:", sorted(dut.signature.members.keys()))

from amaranth.back import rtlil
text = rtlil.convert(dut, name="multiplexed_i2c")
print("elaborated to RTLIL OK,", len(text.splitlines()), "lines")
'''


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    emit(f"amaranth-soc drop-in test {datetime.now(AEST).isoformat(timespec='seconds')}")
    emit(f"host python: {sys.version.splitlines()[0]}")
    emit(f"throwaway venv: {VENV_DIR.relative_to(REPO_ROOT)}")
    emit("the system environment is NOT touched at any point")
    emit()

    if not VENV_DIR.exists():
        emit("creating venv (with pip) ...")
        venv.EnvBuilder(with_pip=True, clear=False).create(str(VENV_DIR))
    vpy = VENV_DIR / "bin" / "python"
    if not vpy.exists():
        emit(f"FATAL: no interpreter at {vpy}")
        return 1

    # `pip install amaranth-soc` from PyPI installs a *placeholder* -- version
    # "0", no modules, `import amaranth_soc` raises ModuleNotFoundError. The
    # real package is distributed from git only. Prefer the local upstream
    # checkout if present, else fetch git directly.
    local_soc = TMP / "forks" / "amaranth-soc"
    soc_spec = (
        str(local_soc) if (local_soc / "pyproject.toml").exists()
        else "amaranth-soc @ git+https://github.com/amaranth-lang/amaranth-soc.git"
    )
    emit(f"amaranth-soc source: {soc_spec}")
    emit("installing real amaranth-soc + amaranth into the venv ...")
    # luna-usb is installed too, and deliberately: it is the part we are KEEPING.
    # multiplexed.py pulls I2CInitiator from luna.gateware.interface.i2c, so
    # without it the probe fails on `luna`, which says nothing about luna_soc.
    rc, out = run(
        [str(vpy), "-m", "pip", "install", "--quiet",
         "amaranth", soc_spec, "amaranth-stdio", "luna-usb"],
        budget=PIP_BUDGET_S,
    )
    emit(f"pip rc={rc}")
    if rc != 0:
        emit(out[-4000:])
        emit("could not install; aborting before the probes so we do not report")
        emit("install failure as API breakage")
        return 1

    rc, out = run([str(vpy), "-m", "pip", "freeze"])
    emit("installed:")
    for line in out.splitlines():
        if any(k in line.lower() for k in ("amaranth", "soc", "stdio")):
            emit(f"    {line}")
    emit()

    probes = [
        ("import + versions", PROBE_IMPORT),
        ("class-level csr.Field annotations (the py3.14 bug)", PROBE_CSR_ANNOTATION),
        ("csr.Builder + csr.Bridge (multiplexed.py shape)", PROBE_CSR_BUILDER),
        ("wishbone.Decoder + WishboneCSRBridge (hello_soc.py shape)", PROBE_WISHBONE_DECODER),
        ("things only luna_soc provides", PROBE_LUNA_ONLY),
        ("REAL multiplexed.py elaborated against real amaranth-soc",
         PROBE_REAL_MULTIPLEXED),
    ]

    results = {}
    for name, src in probes:
        emit(f"--- probe: {name}")
        script = TMP / "dropin_probe.py"
        # REPO lets a probe reach the real design files under ecp5-test/.
        script.write_text(f"REPO = {str(REPO_ROOT)!r}\n" + src)
        rc, out = run([str(vpy), str(script)])
        results[name] = rc
        for line in out.strip().splitlines():
            emit(f"    {line}")
        emit(f"    => rc={rc} {'PASS' if rc == 0 else 'FAIL'}")
        emit()

    emit("=== summary ===")
    for name, rc in results.items():
        emit(f"  {'PASS' if rc == 0 else 'FAIL'}  {name}")

    log = LOG_DIR / "amaranth_soc_dropin_test.log"
    log.write_text("\n".join(LINES) + "\n")
    print(f"log -> {log.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
