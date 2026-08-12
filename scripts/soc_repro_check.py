#!/usr/bin/env python3
#
# Two builds of one tree, byte-compared. #441.
# SPDX-License-Identifier: BSD-3-Clause

"""
Does this design build to the same netlist twice?

Until #441 it did not, and nothing said so: every recorded Fmax was a sample
from a different netlist, so a number attributed to a source change was partly
the elaboration moving underneath it.

## What it compares

    --stage elaborate   Amaranth only, ~10 s a run. Hashes `top.il`, the RTLIL
                        the platform hands yosys. Catches everything upstream
                        of synthesis, which is where both of #441's causes were.
    --stage synthesise  the whole flow, ~150 s a run. Hashes `top.json`, the
                        yosys netlist. The bitstream is NOT the artifact: it
                        carries USERCODE from HEAD by design (`gateware_digest`
                        in soc_run.py), so two bitstreams differ for a reason
                        that is not nondeterminism.

Each run is a SEPARATE PROCESS. Both of #441's causes -- `datetime.now()` and a
name from `id()` -- are constant within one process and vary between two, so a
loop inside one interpreter would have reported the design reproducible.

## The negative control

`--perturb` is the run that must fail. It drops one throwaway `.py` into
`gateware/soc/` between the two runs, which moves exactly one 32-bit constant:
the source digest in `gateware_id`'s `built` word. If the comparison reports
those two netlists as identical, it cannot detect anything and its PASS is
worthless -- so the control exits non-zero when the netlists match, and zero
when they differ.

Run it before believing a PASS. This repo has twelve instruments that reported
success while structurally unable to fail.

    ./scripts/soc_repro_check.py --perturb            # the control, first
    ./scripts/soc_repro_check.py                      # elaboration, 2 runs
    ./scripts/soc_repro_check.py --stage synthesise   # the netlist

## Reading a failure

The report names the first differing lines of the two artifacts. Names point at
an identity-derived label, values at a live clock or a hash, and a difference in
line COUNT at something structural. #441's two causes looked like:

    cell \\top.serial.usb.USBStreamInEndpoint_3591923852624   -- id()
    32'01101010000110000001110101010000                       -- datetime.now()
"""

import argparse
import hashlib
import os
import shutil
import subprocess
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
OUT = ROOT / "tmp" / "repro"

# The perturbation, and why it is a file rather than an edit: `source_id()`
# hashes every `.py` under gateware/soc, so an added one moves the `built` word
# and nothing else. One 32-bit constant is the smallest difference this design
# can have, so a check that catches it catches anything.
PERTURB = ROOT / "gateware" / "soc" / "repro_perturbation.py"
PERTURB_TEXT = ("# Written and deleted by scripts/soc_repro_check.py --perturb.\n"
                "# Its only effect is to move gateware_id's source digest.\n")

# A synthesis run's bound. The shipping SoC without --parallel-refine takes
# ~160 s (#361); 1.25x of that is the floor, and `run_bounded` tightens it to
# measured history after the first run. On expiry the run is killed and the
# stage reports which one and how long it had.
SYNTH_FLOOR = 200.0

# Elaboration is Amaranth plus a cached VexiiRiscv.v -- ~10 s measured on this
# design. 1.25x rounded up, with the same expiry behaviour.
ELABORATE_FLOOR = 30.0


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def first_differences(left, right, limit=6):
    """The first differing lines of two text artifacts, for the report."""
    lines = ([], [])
    for index, (a, b) in enumerate(zip(left.read_text().splitlines(),
                                       right.read_text().splitlines())):
        if a != b:
            lines[0].append(f"  {index + 1}: {a[:110]}")
            lines[1].append(f"  {index + 1}: {b[:110]}")
        if len(lines[0]) == limit:
            break
    return lines


def elaborate(target):
    """One elaboration, in its own interpreter, leaving RTLIL at `target`."""
    script = (
        "import sys;"
        f"sys.path[:0] = [{str(ROOT / 'gateware')!r}, {str(ROOT / 'gateware' / 'soc')!r}];"
        "import top;"
        "from board.cynthion_r1_4 import CynthionPlatformRev1D4 as P;"
        "plan = P().prepare(top.AwtoSoc(firmware=[0] * (top.RAM_SIZE // 4)), 'top');"
        f"open({str(target)!r}, 'w').write(plan.files['top.il'])")
    return run_bounded([sys.executable, "-c", script], family="gateware-elaborate",
                       cwd=ROOT, floor=ELABORATE_FLOOR)


def synthesise(target, firmware, bootloader, pnr_opts):
    """One full build, in its own interpreter, leaving the netlist at `target`."""
    # `top.tim` ACCUMULATES across runs in one build directory, so the previous
    # run's block would be read as this one's.
    (BUILD / "top.tim").unlink(missing_ok=True)
    command = (f'source "$HOME/opt/oss-cad-suite/environment" && '
               f'AMARANTH_nextpnr_opts="{pnr_opts}" '
               f'YOSYS_MAX_THREADS="{YOSYS_MAX_THREADS}" '
               f'{sys.executable} {ROOT / "gateware" / "soc" / "top.py"} --build '
               f'--firmware {firmware} --bootloader {bootloader}')
    result = run_bounded(["bash", "-c", command], family="gateware-repro", cwd=ROOT,
                         floor=SYNTH_FLOOR)
    if result is not None and result.returncode == 0:
        shutil.copy2(BUILD / "top.json", target)
        shutil.copy2(BUILD / "top.tim", target.with_suffix(".tim"))
    return result


def fmax(timing):
    """The clock frequencies nextpnr reported, from one build's `top.tim`."""
    found = {}
    for line in timing.read_text().splitlines():
        if "Max frequency for clock" in line:
            body = line.split("Max frequency for clock")[-1]
            name, _, rest = body.partition(":")
            found[name.strip().strip("'")] = rest.strip().split()[0]
    return found


def run_stage(args):
    OUT.mkdir(parents=True, exist_ok=True)
    artifacts = []

    firmware = BUILD / "rust_fw.bin"
    bootloader = BUILD / "rust_boot.bin"
    if args.stage == "synthesise" and not (firmware.exists() and bootloader.exists()):
        emit(f"no firmware beside the build directory ({BUILD.relative_to(ROOT)}).")
        emit("Run `./scripts/soc_run.py --build-only` once first: this compares "
             "GATEWARE, so every run has to be handed the same firmware image.")
        return 1

    pnr_opts = NEXTPNR_OPTS
    if args.no_parallel_refine:
        pnr_opts = " ".join(o for o in NEXTPNR_OPTS.split() if o != "--parallel-refine")

    for index in range(args.runs):
        # The perturbation goes in before the LAST run, so the runs before it
        # are the ordinary comparison and the last one is the control.
        if args.perturb and index == args.runs - 1:
            PERTURB.write_text(PERTURB_TEXT)
            emit(f"--perturb: wrote {PERTURB.relative_to(ROOT)} before run "
                 f"{index + 1}; one 32-bit constant should move")

        suffix = "il" if args.stage == "elaborate" else "json"
        target = OUT / f"{variant.slug()}-run{index + 1}.{suffix}"
        emit(f"run {index + 1}/{args.runs}: {args.stage} -> "
             f"{target.relative_to(ROOT)}")
        if args.stage == "elaborate":
            result = elaborate(target)
        else:
            result = synthesise(target, firmware, bootloader, pnr_opts)
        if result is None:
            emit(f"run {index + 1} was KILLED on its timeout; see the line above "
                 f"for the bound and where it came from")
            return 1
        if result.returncode != 0:
            emit(f"run {index + 1} FAILED (rc={result.returncode})")
            for line in (result.stderr or "").splitlines()[-15:]:
                emit(f"  {line}")
            return 1
        artifacts.append(target)
        emit(f"  {digest(target)}  {target.stat().st_size} bytes")

    same = len({digest(path) for path in artifacts}) == 1
    if not same:
        left, right = artifacts[0], artifacts[-1]
        emit("DIFFERENT. First differing lines:")
        for a, b in zip(*first_differences(left, right)):
            emit(f"  {left.name}{a}")
            emit(f"  {right.name}{b}")

    if args.stage == "synthesise":
        for path in artifacts:
            timing = path.with_suffix(".tim")
            if timing.exists():
                emit(f"  {path.name}: " + "  ".join(f"{k} {v}"
                                                    for k, v in fmax(timing).items()))

    if args.perturb:
        # The control passes when the comparison NOTICES the perturbation.
        if same:
            emit("CONTROL FAILED: the netlists matched across a deliberate "
                 "one-constant change. This check cannot detect anything and no "
                 "PASS from it means a thing.")
            return 1
        emit("control passed: a one-constant change was reported as DIFFERENT, "
             "so a match from this check is evidence rather than a default.")
        return 0

    if same:
        emit(f"REPRODUCIBLE: {args.runs} runs, one {args.stage} digest.")
        return 0
    emit(f"NOT REPRODUCIBLE: {args.runs} runs produced "
         f"{len({digest(p) for p in artifacts})} distinct {args.stage} digests.")
    return 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("elaborate", "synthesise"),
                        default="elaborate",
                        help="what to hash: the RTLIL Amaranth emits, or the "
                             "yosys netlist the placer is handed")
    parser.add_argument("--runs", type=int, default=2,
                        help="how many separate processes to compare")
    parser.add_argument("--perturb", action="store_true",
                        help="the negative control: change one constant before "
                             "the last run and require the comparison to notice")
    parser.add_argument("--no-parallel-refine", action="store_true",
                        help="synthesise only: the placer flag that makes Fmax a "
                             "distribution (#361). Does not touch the netlist, so "
                             "it matters only when the Fmax lines are the point")
    args = parser.parse_args()

    emit(f"variant {variant.slug()}")
    try:
        return run_stage(args)
    finally:
        if PERTURB.exists():
            PERTURB.unlink()
            emit(f"removed {PERTURB.relative_to(ROOT)}")
        for cache in (ROOT / "gateware" / "soc").rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
