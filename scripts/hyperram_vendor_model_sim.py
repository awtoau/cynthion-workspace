#!/usr/bin/env python3
#
# Run Winbond's own W956A8MBYA model against a testbench, under Diamond's Questa.
# SPDX-License-Identifier: BSD-3-Clause

"""The vendor's encrypted Verilog model, elaborated and checked.

`sources/models/W956X8MBY_verilog_p.zip` is AES-encrypted to Mentor's key
(`key_keyowner = "Mentor Graphics Corporation"`), so Icarus, Verilator and
cocotb can never read it. **Diamond bundles Questa Sim Lattice OEM Edition,
which holds that key**, so it runs here.

Two things are needed and neither is documented by Winbond:

- `-sv`. The protected region is SystemVerilog; in Verilog-2001 mode vlog stops
  with "syntax error in protected region".
- `+define+T<grade>`. `Config-AC.v`'s AC-parameter block is an
  `ifdef T100/T133/T166/T200/T250` chain **with no default branch**, so with no
  grade defined it declares no timing parameters at all and every identifier in
  the protected region is undefined.

What it buys: this is the only model that implements the register space. It
reports `ID0 = 0x0c86`, `CR0 = 0x8f2f`, `CR1 = 0xffc1` at power-up -- the values
the board reports -- so `hyperram_identify.py`'s expectations can be checked
without hardware. See `docs/chips/hyperram/survey.md`.

Usage:

    scripts/hyperram_vendor_model_sim.py                # 166 MHz grade
    scripts/hyperram_vendor_model_sim.py --grade T250   # the grade the datasheet has no column for
    scripts/hyperram_vendor_model_sim.py --keep         # leave the work library for vsim -gui

Log: `tmp/logs/hyperram_vendor_model_sim.log`.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_ZIP = ROOT / "sources" / "models" / "W956X8MBY_verilog_p.zip"
TESTBENCH = ROOT / "gateware" / "probes" / "hyperram" / "vendor_model_tb.sv"
WORKDIR = ROOT / "tmp" / "hyperram-vendor-model"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_vendor_model_sim.log"

DIAMOND = Path(os.environ.get("DIAMOND_ROOT", Path.home() / "lscc" / "diamond" / "3.14"))

# The grades Config-AC.v defines AC parameters for. Anything else declares none.
GRADES = ("T85", "T100", "T104", "T133", "T166", "T200", "T250")

# Power-up values the part reports, from docs/chips/hyperram/w956a8.md. The model
# agreeing with the board is the whole point of running it, so a mismatch fails.
EXPECTED = {
    "ID_REG0": "0c86",
    "ID_REG1": "0001",
    "CONFIG_REG0": "8f2f",
    "CONFIG_REG1": "ffc1",
}

# Measured on this machine: vlib 25 ms, vlog 51 ms, vsim 555 ms (200 us of model
# time). The one term not measured is a cold FlexLM checkout, and that is the whole
# reason this is not sub-second: 10 s is ~18x the slowest measured step, spent
# entirely on that unknown. On expiry we log which step, its limit and its elapsed
# time, and exit non-zero -- a hung vsim otherwise looks like a slow one.
STEP_TIMEOUT_S = 10


log = logging.getLogger("vendor-model")


def setup_logging(verbose: bool) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(message)s"
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=[logging.FileHandler(LOGFILE, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def questa_bin(name: str) -> Path:
    path = DIAMOND / "questasim" / "bin" / name
    if not path.exists():
        raise SystemExit(
            f"{name} not found under {DIAMOND}/questasim/bin -- set DIAMOND_ROOT to a "
            f"Diamond install that includes Questa Sim Lattice Edition"
        )
    return path


def extract_model(part: str) -> Path:
    """Unpack the nested per-voltage zip for `part` into the work directory."""
    if not MODEL_ZIP.exists():
        raise SystemExit(
            f"{MODEL_ZIP.relative_to(ROOT)} is missing -- it is gitignored vendor IP. "
            f"Fetch it via the route in sources/README.md."
        )
    WORKDIR.mkdir(parents=True, exist_ok=True)
    inner_name = f"{part}_verilog_p.zip"
    with zipfile.ZipFile(MODEL_ZIP) as outer:
        if inner_name not in outer.namelist():
            raise SystemExit(f"{inner_name} not in {MODEL_ZIP.name}: {outer.namelist()}")
        outer.extract(inner_name, WORKDIR)
    with zipfile.ZipFile(WORKDIR / inner_name) as inner:
        inner.extractall(WORKDIR)
    model = WORKDIR / f"{part}.modelsim.vp"
    if not model.exists():
        raise SystemExit(f"{model.name} not produced by extraction")
    log.info("model    %s (%d KiB)", model.name, model.stat().st_size // 1024)
    log.info("config   %s -- plaintext AC parameters", (WORKDIR / "Config-AC.v").name)
    return model


def run(step: str, argv: list[str], env: dict[str, str]) -> str:
    log.debug("%s: %s", step, " ".join(argv))
    try:
        proc = subprocess.run(
            argv, cwd=WORKDIR, env=env, capture_output=True, text=True, timeout=STEP_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        log.error("%s exceeded its %d s limit (ran %.1f s) -- treating as hung", step,
                  STEP_TIMEOUT_S, exc.timeout)
        raise SystemExit(2) from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        log.debug("  %s", line)
    if proc.returncode != 0:
        log.error("%s failed (exit %d); last lines:", step, proc.returncode)
        for line in out.splitlines()[-15:]:
            log.error("  %s", line)
        raise SystemExit(proc.returncode)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--grade", default="T166", choices=GRADES,
                    help="AC parameter set from Config-AC.v (default: T166, the 6I on this board)")
    ap.add_argument("--part", default="W956A8MBYA",
                    help="W956A8MBYA (3.0 V, ours) or W956D8MBYA (1.8 V)")
    ap.add_argument("--keep", action="store_true", help="keep the work library for vsim -gui")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every tool line")
    args = ap.parse_args()

    setup_logging(args.verbose)
    log.info("Winbond %s model, %s AC parameters", args.part, args.grade)

    if not args.keep and WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    model = extract_model(args.part)
    shutil.copy(TESTBENCH, WORKDIR / TESTBENCH.name)

    env = dict(os.environ)
    env.setdefault("LM_LICENSE_FILE", str(DIAMOND / "license" / "license.dat"))

    if (WORKDIR / "work").exists():
        shutil.rmtree(WORKDIR / "work")
    run("vlib", [str(questa_bin("vlib")), "work"], env)
    run("vlog", [str(questa_bin("vlog")), "-sv", f"+define+{args.grade}",
                 model.name, TESTBENCH.name], env)
    out = run("vsim", [str(questa_bin("vsim")), "-c", "-voptargs=+acc", "tb",
                       "-do", "run -all; quit -f"], env)

    # The model prints its own power-up register dump; check it against the board.
    failures = []
    for name, want in EXPECTED.items():
        m = re.search(rf"{name}\s*=\s*([0-9a-fA-F]{{4}})", out)
        got = m.group(1).lower() if m else None
        status = "ok" if got == want else "MISMATCH"
        log.info("  %-12s = %s  (expect %s)  %s", name, got or "<absent>", want, status)
        if got != want:
            failures.append(f"{name}: model {got}, board {want}")

    reg_read = re.findall(r"register out .*\(0x([0-9a-f]{2})\)", out)
    if reg_read[:2] == ["0c", "86"]:
        log.info("  bus read of ID0 returned 0c 86 -- CA decode and 14 CK latency both good")
    else:
        failures.append(f"bus read of ID0 returned {reg_read[:2]}, expected ['0c', '86']")

    if failures:
        for f in failures:
            log.error("FAIL %s", f)
        return 1

    log.info("PASS -- vendor model agrees with the board on every power-up register")
    if not args.keep:
        shutil.rmtree(WORKDIR, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
