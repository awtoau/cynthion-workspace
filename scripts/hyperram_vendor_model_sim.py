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
sys.path.insert(0, str(ROOT / "scripts"))

from shared_paths import resolve_shared  # noqa: E402

# `sources/**` is gitignored, so a worktree does not carry it. `resolve_shared`
# falls back to the main checkout rather than telling someone to re-fetch vendor
# IP they already have; `HYPERRAM_MODEL_ZIP` overrides both. See #365.
_MODEL_REL = Path("sources/models/W956X8MBY_verilog_p.zip")
MODEL_ZIP = (resolve_shared(ROOT, _MODEL_REL, env_var="HYPERRAM_MODEL_ZIP")
             or ROOT / _MODEL_REL)
TESTBENCH = ROOT / "gateware" / "probes" / "hyperram" / "vendor_model_tb.sv"
FAULT_TB = ROOT / "gateware" / "probes" / "hyperram" / "fault_model_tb.sv"
OPEN_MODEL = ROOT / "gateware" / "probes" / "hyperram" / "hyperram_model.v"
WORKDIR = ROOT / "tmp" / "hyperram-vendor-model"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_vendor_model_sim.log"

DIAMOND = Path(os.environ.get("DIAMOND_ROOT", Path.home() / "lscc" / "diamond" / "3.14"))

# The grades Config-AC.v defines AC parameters for. Anything else declares none:
# T85 and T104 set tCK only and fail with 7 undefined identifiers in the protected
# region -- docs/chips/hyperram/config-ac.md.
GRADES = ("T100", "T133", "T166", "T200", "T250")

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
    # The RWDS level during CA is the half of the latency question #338 turns
    # on, so it stays here. The exact edge count does NOT: this testbench finds
    # it by hunting for the strobe, and the hunt burns a different number of
    # edges on the two models even where both drive data and strobe on the same
    # edge -- so a single expected string cannot match both, and pinning one
    # made a correct fix to the twin look like a regression.
    #
    # dqs_latency_probe_tb.sv measures the data edge directly, on both models,
    # over the whole sparse code table. That is where an exact count belongs;
    # scripts/hyperram_dqs_model_sim.py --stage probe runs it.
    "RWDS during CA = 1",
    "RWDS during CA = 0",
    "PASS wrapped burst wraps to the group start",
    "PASS hybrid burst: leaves the group after one pass",
    "PASS device is silent in deep power down",
    "PASS CR0 after reset recovery = 8f2f",
    # Both models now, not the vendor alone: the twin has a refresh cadence.
    "PASS latency varies transaction to transaction",
    # And the same axis walked over the whole sparse code table, in CK rather
    # than edges. `fix` and `var` differ by L, so a controller that takes the
    # wrong branch misses the whole burst -- which is what #338 measures.
    "PASS fix L=3 latency 6 CK",
    "PASS var L=3 latency 3 CK",
    "PASS fix L=7 latency 14 CK",
    "PASS var L=7 latency 7 CK",
    "PASS var write = 2004",
    # The negative control, and the only direction that loses data: a host on the
    # long count against a device on the short one starts capturing L clocks into
    # the burst. Both models lose all 128 words; the board loses 121-128 (#338).
    "PASS the long count against a short-latency device loses the burst",
)
TCSM_MARKER = "tCSM violation"

# What the device asks for over the CA period, and what it then delivers. RWDS is
# not driven for the first tDSV (12 ns, Config-AC.v T166/3.0 V), so the first two
# CA edges at 100 MHz carry a float and only the last four carry the answer.
# `zz0000` is "no extra latency", `zz1111` is "take 2L". These are the exact
# strings both models print; a change in either is a change in the protocol fact
# that #342 records, not a cosmetic one.
CA_RWDS_MARKERS = (
    "var L=7 code=2: RWDS over the CA = zz0000",
    "fix L=7 code=2: RWDS over the CA = zz1111",
)

# The 2x election. The vendor model has distributed refresh and elects the long
# latency on a variable-latency transaction when one is pending; the open twin has
# no refresh and never does. Both are correct for what they model, so this is
# reported rather than required -- see docs/chips/hyperram/models.md.
ELECTION_RE = re.compile(r"variable-latency elections: (\d+) short, (\d+) long, of (\d+)")

# Measured on this machine: vlib 25 ms, vlog 51 ms, vsim 555 ms (200 us of model
# time). The one term not measured is a cold FlexLM checkout, and that is the whole
# reason this is not sub-second: 10 s is ~18x the slowest measured step, spent
# entirely on that unknown. On expiry we log which step, its limit and its elapsed
# time, and exit non-zero -- a hung vsim otherwise looks like a slow one.
STEP_TIMEOUT_S = 10

# FAULT INJECTION, open model only -- the vendor model has no such parameters.
# One Icarus run per knob setting, each asserted to change the bus in the ONE way
# its name promises. The control run is first and must be indistinguishable from
# the part; a knob that changes the bus with every parameter at its default has
# broken the model rather than faulted it. See docs/chips/hyperram/sim-audit.md.
#
# `zz1111` is the part: RWDS floats for tDSV and then answers. A held fault has no
# tDSV, so it reads on every CA edge -- which is what makes the two cases
# distinguishable at all, and what #338 is about.
FAULT_CASES = (
    ("control", {}, (
        # 27, not 28: the twin drives data on the same edge the part does, and
        # this row pins it. dqs_latency_probe_tb.sv checks it at every latency
        # code. See the note on the strobe hunt above.
        "ca rwds = zz1111, strobe at 27 edges, id0 = 0c86",
        "mem[0x000100] = 1234",
        "burst served 8 of 8",
        "cr0 after write = af2f",
        # The tCSHI monitor, proved the way the tCSM one is: by a deliberate
        # violation. It is the only judge of the gap now that the Python models
        # have stopped judging it against their own copy of the number. (#346)
        "ERROR tCSHI violation",
    )),
    ("rwds stuck High", {"FAULT_CA_RWDS": 1}, (
        "ca rwds = 111111",
        "id0 = 0c86",
    )),
    ("rwds stuck Low", {"FAULT_CA_RWDS": 2}, (
        "ca rwds = 000000",
        "id0 = 0c86",
    )),
    ("rwds never driven", {"FAULT_CA_RWDS": 3}, (
        "ca rwds = zzzzzz",
        "id0 = 0c86",
    )),
    # The read watchdog's two shapes (#316). `id0 = zzzz` is the same silence in
    # register space, which is what makes "never answers" a device state rather
    # than a memory-array quirk.
    ("device never answers", {"FAULT_DELIVER": 0}, (
        "id0 = zzzz",
        "burst served 0 of 8",
    )),
    ("device stops after 3 words", {"FAULT_DELIVER": 3}, (
        "id0 = 0c86",
        "burst served 3 of 8",
    )),
    # The CR0/CR1 verify path. Against a device that always accepts, a controller
    # that never issued the write and one whose write was dropped read back the
    # same value -- so the check only discriminates against this.
    ("register write refused", {"FAULT_REFUSE_REG": 1}, (
        "REFUSED (fault injection)",
        "cr0 after write = 8f2f",
    )),
)


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
    for marker in CA_RWDS_MARKERS:
        if marker in out:
            log.info("  [%s] %s", which, marker)
        else:
            failures.append(f"{which}: missing {marker!r}")
    if TCSM_MARKER not in out:
        failures.append(f"{which}: the deliberate tCSM violation was not reported")
    else:
        log.info("  [%s] tCSM violation reported, as the stimulus intends", which)
    m = ELECTION_RE.search(out)
    if not m:
        failures.append(f"{which}: the variable-latency election tally is missing")
    else:
        log.info("  [%s] variable latency: %s of %s transactions took 2L (refresh)",
                 which, m.group(2), m.group(3))
    m = re.search(r"=== done, (\d+) failures ===", out)
    if not m:
        failures.append(f"{which}: testbench did not reach its summary line")
    elif m.group(1) != "0":
        failures.append(f"{which}: testbench reported {m.group(1)} failures")
    return failures


def run_open(grade: str, hunt: int, bursts: int) -> int:
    """Same testbench, same stimulus, open model, open simulator."""
    for tool in ("iverilog", "vvp"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not on PATH -- needed for the open model")
    WORKDIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(TESTBENCH, WORKDIR / TESTBENCH.name)
    shutil.copy(OPEN_MODEL, WORKDIR / OPEN_MODEL.name)
    env = dict(os.environ)
    run("iverilog", ["iverilog", "-g2012", "-DDUT_MODULE=hyperram_model",
                     f"-DREFRESH_HUNT_N={hunt}", f"-DBURST_HUNT_N={bursts}",
                     "-o", "tb.vvp", TESTBENCH.name, OPEN_MODEL.name], env)
    return check_output(run("vvp", ["vvp", "tb.vvp"], env), "open")


def run_faults() -> list[str]:
    """The twin told to misbehave, one Icarus run per knob."""
    for tool in ("iverilog", "vvp"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not on PATH -- needed for the fault runs")
    WORKDIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(FAULT_TB, WORKDIR / FAULT_TB.name)
    shutil.copy(OPEN_MODEL, WORKDIR / OPEN_MODEL.name)
    env = dict(os.environ)

    failures = []
    for name, defines, expected in FAULT_CASES:
        target = f"fault-{name.replace(' ', '-')}.vvp"
        argv = ["iverilog", "-g2012"]
        argv += [f"-D{key}={value}" for key, value in defines.items()]
        argv += ["-o", target, FAULT_TB.name, OPEN_MODEL.name]
        run("iverilog", argv, env)
        out = run("vvp", ["vvp", target], env)
        missing = [marker for marker in expected if marker not in out]
        if missing:
            for marker in missing:
                failures.append(f"fault[{name}]: missing {marker!r}")
            for line in out.splitlines():
                if "[fault]" in line:
                    log.error("  [fault:%s] %s", name, line.split("] ", 1)[-1])
        else:
            log.info("  [fault] %s: %s", name, expected[0])
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sim", default="both", choices=("questa", "icarus", "both", "fault"),
                    help="questa runs the vendor model, icarus runs the open twin and "
                         "its fault knobs, both runs each and requires them to agree "
                         "(default), fault runs the knobs alone")
    ap.add_argument("--grade", default="T166", choices=GRADES,
                    help="AC parameter set from Config-AC.v (default: T166, the 6I on this board)")
    ap.add_argument("--part", default="W956A8MBYA",
                    help="W956A8MBYA (3.0 V, ours) or W956D8MBYA (1.8 V)")
    ap.add_argument("--hunt", type=int, default=256, metavar="N",
                    help="variable-latency transactions to run while looking for a "
                         "refresh-forced 2x election (default 256, ~54 us of model time)")
    ap.add_argument("--bursts", type=int, default=64, metavar="N",
                    help="variable-latency bursts of 128 words -- the BIST's own "
                         "geometry -- to run in the same hunt (default 64, ~96 us)")
    ap.add_argument("--keep", action="store_true", help="keep the work library for vsim -gui")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every tool line")
    args = ap.parse_args()

    setup_logging(args.verbose)
    log.info("Winbond %s model, %s AC parameters", args.part, args.grade)

    if not args.keep and WORKDIR.exists():
        shutil.rmtree(WORKDIR)

    if args.sim in ("icarus", "fault"):
        failures = [] if args.sim == "fault" else run_open(args.grade, args.hunt,
                                                           args.bursts)
        failures += run_faults()
        for f in failures:
            log.error("FAIL %s", f)
        if not failures and args.sim == "fault":
            log.info("PASS -- every fault knob changes the bus in the one way it claims")
        elif not failures:
            log.info("PASS -- the open model passes the shared testbench under Icarus, "
                     "and every fault knob does what its name says")
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
    run("vlog", [str(questa_bin("vlog")), "-sv", f"+define+{args.grade}",                  f"+define+REFRESH_HUNT_N={args.hunt}",
                 f"+define+BURST_HUNT_N={args.bursts}",
                 model.name, TESTBENCH.name], env)
    out = run("vsim", [str(questa_bin("vsim")), "-c", "-voptargs=+acc", "tb",
                       "-do", "run -all; quit -f"], env)

    failures = check_output(out, "vendor")
    if args.sim == "both":
        failures += run_open(args.grade, args.hunt, args.bursts)
        failures += run_faults()
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
