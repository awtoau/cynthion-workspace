#!/usr/bin/env python3
#
# Run Winbond's own W956A8MBYA model against a testbench, under Diamond's Questa.
# SPDX-License-Identifier: BSD-3-Clause

"""The vendor's encrypted model, an open twin, and a testbench that holds them equal.

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

What it buys: the vendor model is the only one that implements the register
space, and it reports `ID0 = 0x0c86`, `CR0 = 0x8f2f`, `CR1 = 0xffc1` at power-up
-- the values the board reports.

**And it is the oracle for `hyperram_model.v`**, the open twin next to it. The
same testbench drives both (`vendor_model_tb.sv` instantiates whatever
`DUT_MODULE names), so the twin can be used in Icarus, Verilator and cocotb with
the vendor model as the thing that says it is still right. Encryption stops us
reading the source; it does not stop us checking the behaviour.

Usage:

    scripts/hyperram_vendor_model_sim.py                 # both, and they must agree
    scripts/hyperram_vendor_model_sim.py --sim icarus    # open twin only, no Diamond needed
    scripts/hyperram_vendor_model_sim.py --grade T250    # the grade the datasheet has no column for
    scripts/hyperram_vendor_model_sim.py --keep          # leave the work dir for vsim -gui

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
OPEN_MODEL = ROOT / "gateware" / "probes" / "hyperram" / "hyperram_model.v"
WORKDIR = ROOT / "tmp" / "hyperram-vendor-model"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_vendor_model_sim.log"

DIAMOND = Path(os.environ.get("DIAMOND_ROOT", Path.home() / "lscc" / "diamond" / "3.14"))

# The grades Config-AC.v defines AC parameters for. Anything else declares none.
GRADES = ("T85", "T100", "T104", "T133", "T166", "T200", "T250")

# The testbench reports its own pass/fail; these are what a good run must contain.
# The tCSM line is a DELIBERATE violation -- its absence means the check is gone,
# which is exactly the silent regression this script exists to catch.
REQUIRED_MARKERS = (
    "PASS ID0 = 0c86",
    "PASS CR0 (POR) = 8f2f",
    "PASS CR1 (POR) = ffc1",
    "PASS CR0 after write = af2f",
    "PASS differential clock accepted",
    "PASS mem[0x000000] = dead",
    "PASS mem[0x3fffff] = 5aa5",
)
TCSM_MARKER = "tCSM violation"

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


def check_output(out: str, which: str) -> list[str]:
    """The testbench grades itself; this decides whether the run counts."""
    failures = []
    for marker in REQUIRED_MARKERS:
        if marker in out:
            log.info("  [%s] %s", which, marker)
        else:
            failures.append(f"{which}: missing {marker!r}")
    if TCSM_MARKER not in out:
        failures.append(f"{which}: the deliberate tCSM violation was not reported")
    else:
        log.info("  [%s] tCSM violation reported, as the stimulus intends", which)
    m = re.search(r"=== done, (\d+) failures ===", out)
    if not m:
        failures.append(f"{which}: testbench did not reach its summary line")
    elif m.group(1) != "0":
        failures.append(f"{which}: testbench reported {m.group(1)} failures")
    return failures


def run_open(grade: str) -> int:
    """Same testbench, same stimulus, open model, open simulator."""
    for tool in ("iverilog", "vvp"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not on PATH -- needed for the open model")
    WORKDIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(TESTBENCH, WORKDIR / TESTBENCH.name)
    shutil.copy(OPEN_MODEL, WORKDIR / OPEN_MODEL.name)
    env = dict(os.environ)
    run("iverilog", ["iverilog", "-g2012", "-DDUT_MODULE=hyperram_model",
                     "-o", "tb.vvp", TESTBENCH.name, OPEN_MODEL.name], env)
    return check_output(run("vvp", ["vvp", "tb.vvp"], env), "open")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sim", default="both", choices=("questa", "icarus", "both"),
                    help="questa runs the vendor model, icarus runs the open twin, "
                         "both runs each and requires them to agree (default)")
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

    if args.sim == "icarus":
        failures = run_open(args.grade)
        for f in failures:
            log.error("FAIL %s", f)
        if not failures:
            log.info("PASS -- the open model passes the shared testbench under Icarus")
        if not args.keep:
            shutil.rmtree(WORKDIR, ignore_errors=True)
        return 1 if failures else 0
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

    failures = check_output(out, "vendor")
    if args.sim == "both":
        failures += run_open(args.grade)
    if failures:
        for f in failures:
            log.error("FAIL %s", f)
        return 1

    if args.sim == "both":
        log.info("PASS -- vendor and open models agree with the board and with each other")
    else:
        log.info("PASS -- the vendor model agrees with the board on every register and word")
    if not args.keep:
        shutil.rmtree(WORKDIR, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
