#!/usr/bin/env python3
#
# Which of #441's two causes moved the LUT count. Three arms, three builds each.
# SPDX-License-Identifier: BSD-3-Clause

"""
Separates #441's two causes of an unreproducible build, because they were
confounded: both changed on every build, so no number attributed to one of them
excluded the other.

## The two, and the claim being tested

    built word    `gateware_id` packed `datetime.now()` into a 32-bit constant
    module name   luna names CDC's second IN endpoint `..._{id(endpoint)}`

`docs/soc-size-review.md` blamed the LUT spread -- 14,178 / 14,200 / 14,025 /
14,476 TRELLIS_COMB across four builds -- on the constant. The mechanism does
not obviously reach that far: a 32-bit constant read through a CSR bank is
tie-offs and a read mux, so what can fold differently is bounded by that mux.
A module name is not bounded that way -- names set the order yosys and ABC
traverse and map in, and mapping order moves hundreds of LUTs routinely.

So: pin one, vary the other, three builds an arm.

    shipped      neither varies -- the fix as committed
    names-vary   `built` pinned, luna's `id()` naming left alone
    consts-vary  naming pinned, `built` repacked from the wall clock per build

An arm's spread is that arm's cause. `git` is pinned in the two diagnostic arms
and is not a variable in any case: `usercode()` reads the tree, which does not
change between two builds a minute apart.

## What to read

TRELLIS_COMB per build, TRELLIS_FF beside it, and the netlist digest. FF is the
control: a register is a register wherever it is placed, so FF moving means the
FUNCTION changed and FF holding still while COMB moves means only the MAPPING
did. All arms run `--no-parallel-refine`, so the placer contributes nothing
(#361) and the `clk` figures are comparable.

    ./scripts/soc_repro_arms.py                    # all three arms
    ./scripts/soc_repro_arms.py --arms names-vary  # one of them
    ./scripts/soc_repro_arms.py --runs 5

Needs `./scripts/soc_run.py --build-only` to have run once: every build is
handed the same firmware image, so gateware is the only thing under test.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402
from fast_build_env import NEXTPNR_OPTS, YOSYS_MAX_THREADS  # noqa: E402
from subprocess_timeout_from_history import run_bounded  # noqa: E402

BUILD = variant.build_dir(ROOT)
OUT = ROOT / "tmp" / "repro-arms"

UTIL_RE = re.compile(r"^Info:\s+(\S+):\s+(\d+)/\s*(\d+)")
FMAX_RE = re.compile(r"Max frequency for clock\s+'(\S+)':\s+([\d.]+) MHz")
CELLS = ("TRELLIS_COMB", "TRELLIS_FF")

# (constants, names) per arm. "real" leaves the shipped code path alone.
ARMS = {
    "shipped":     ("real", "real"),
    "names-vary":  ("fixed", "vary"),
    "consts-vary": ("vary", "real"),
}

# A synthesis run's bound: ~160 s for this design without --parallel-refine
# (#361), 1.25x of that as the floor, tightened to measured history after the
# first run. On expiry the run is killed and named.
SYNTH_FLOOR = 200.0

# The driver, one fresh interpreter per build. `id()` and a wall clock are both
# constant within a process, so an in-process loop would measure neither.
DRIVER = '''
import sys
from pathlib import Path
ROOT = Path({root!r})
sys.path[:0] = [str(ROOT / "gateware"), str(ROOT / "gateware" / "soc")]

import luna_stable_names
if {names!r} == "vary":
    luna_stable_names.stable_endpoint_names = lambda: None

import peripherals.gateware_id as gid
if {consts!r} == "vary":
    # The historical packing, byte for byte: year-2000, month, day, hour,
    # minute, second, which is what `built` held before #441.
    from datetime import datetime, timezone
    w = datetime.now(timezone.utc).utctimetuple()
    word = (((w.tm_year - 2000) & 0x3f) << 26 | (w.tm_mon & 0xf) << 22
            | (w.tm_mday & 0x1f) << 17 | (w.tm_hour & 0x1f) << 12
            | (w.tm_min & 0x3f) << 6 | (w.tm_sec & 0x3f))
    gid.source_id = lambda word=word: word
    gid.usercode = lambda: 0
elif {consts!r} == "fixed":
    gid.source_id = lambda: 0
    gid.usercode = lambda: 0

sys.argv = ["top.py", "--build", "--firmware", {firmware!r},
            "--bootloader", {bootloader!r}]
import top
sys.exit(top.main())
'''


def utilisation(timing):
    """Cells and Fmax from a finished nextpnr run's `top.tim`."""
    cells, fmax = {}, {}
    for line in timing.read_text().splitlines():
        match = UTIL_RE.match(line.strip())
        if match and match.group(1) in CELLS:
            cells[match.group(1)] = int(match.group(2))
        match = FMAX_RE.search(line)
        if match:
            fmax[match.group(1).replace("$glbnet$", "")] = float(match.group(2))
    return cells, fmax


def one_build(arm, index, firmware, bootloader, pnr_opts):
    """One build of one arm, returning its row or None."""
    consts, names = ARMS[arm]
    driver = OUT / f"driver-{arm}.py"
    driver.write_text(DRIVER.format(root=str(ROOT), names=names, consts=consts,
                                    firmware=str(firmware),
                                    bootloader=str(bootloader)))
    # `top.tim` ACCUMULATES across runs in one build directory, so an earlier
    # arm's block would be read as this one's.
    (BUILD / "top.tim").unlink(missing_ok=True)

    command = (f'source "$HOME/opt/oss-cad-suite/environment" && '
               f'AMARANTH_nextpnr_opts="{pnr_opts}" '
               f'YOSYS_MAX_THREADS="{YOSYS_MAX_THREADS}" '
               f'{sys.executable} {driver}')
    result = run_bounded(["bash", "-c", command], family="gateware-repro",
                         cwd=ROOT, floor=SYNTH_FLOOR)
    if result is None:
        emit(f"{arm} run {index}: KILLED on its timeout")
        return None
    if result.returncode != 0:
        emit(f"{arm} run {index}: FAILED rc={result.returncode}")
        for line in (result.stderr or "").splitlines()[-15:]:
            emit(f"  {line}")
        return None

    netlist = OUT / f"{arm}-{index}.json"
    shutil.copy2(BUILD / "top.json", netlist)
    cells, fmax = utilisation(BUILD / "top.tim")
    row = {"arm": arm, "run": index,
           "netlist": hashlib.sha256(netlist.read_bytes()).hexdigest()[:16],
           "comb": cells.get("TRELLIS_COMB"), "ff": cells.get("TRELLIS_FF"),
           "clk": fmax.get("clk")}
    emit(f"  {arm} run {index}: netlist {row['netlist']}  "
         f"COMB {row['comb']}  FF {row['ff']}  clk {row['clk']} MHz")
    return row


def spread(values):
    values = [v for v in values if v is not None]
    return max(values) - min(values) if values else None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arms", nargs="*", choices=sorted(ARMS),
                        default=sorted(ARMS))
    parser.add_argument("--runs", type=int, default=3,
                        help="builds per arm; a spread needs at least three")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    firmware, bootloader = BUILD / "rust_fw.bin", BUILD / "rust_boot.bin"
    if not (firmware.exists() and bootloader.exists()):
        emit(f"no firmware beside {BUILD.relative_to(ROOT)}. Run "
             f"`./scripts/soc_run.py --build-only` once first -- every arm has "
             f"to be handed the same image, or firmware is a fourth variable.")
        return 1

    pnr_opts = " ".join(o for o in NEXTPNR_OPTS.split() if o != "--parallel-refine")
    emit(f"variant {variant.slug()}, {args.runs} builds per arm, "
         f"nextpnr {pnr_opts}")

    rows = []
    for arm in args.arms:
        consts, names = ARMS[arm]
        emit(f"arm {arm}: built constant {consts}, module naming {names}")
        for index in range(1, args.runs + 1):
            row = one_build(arm, index, firmware, bootloader, pnr_opts)
            if row is None:
                return 1
            rows.append(row)

    emit("")
    emit(f"{'arm':<12} {'netlists':>8} {'COMB spread':>12} {'FF spread':>10} "
         f"{'clk spread':>11}")
    for arm in args.arms:
        mine = [r for r in rows if r["arm"] == arm]
        distinct = len({r["netlist"] for r in mine})
        clk = spread([r["clk"] for r in mine])
        emit(f"{arm:<12} {distinct:>8} {spread([r['comb'] for r in mine]):>12} "
             f"{spread([r['ff'] for r in mine]):>10} "
             f"{clk if clk is None else f'{clk:.2f}':>11}")

    (OUT / "arms.json").write_text(json.dumps(rows, indent=2))
    emit(f"rows -> {(OUT / 'arms.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
