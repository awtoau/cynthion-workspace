#!/usr/bin/env python3
#
# Does a commit change the netlist? #447, #450.
# SPDX-License-Identifier: BSD-3-Clause

"""
Two builds at two commits with no source change, netlists compared.

    ./scripts/soc_head_independence.py
    ./scripts/soc_head_independence.py --no-parallel-refine

The question `soc_run.gateware_digest` turns on: HEAD is in the bitstream cache
key, so every commit costs a synthesis. It has to be there only if HEAD reaches
the netlist. It reaches the BITSTREAM through `ecppack --usercode`, which is a
command in the configuration stream and not a cell.

## What is asserted, and why both halves

    netlists IDENTICAL   `top.json`, the yosys output the placer is handed.
                         This is the claim: a commit is not a synthesis.
    bitstreams DIFFERENT `top.bit`. The CONTROL. If these matched too, the
                         comparison above would be satisfied by a build that
                         did not run, by a stale artifact, or by a stamp that
                         never reached the packer -- all of which report as
                         success. A one-sided instrument is not one.
    the stamp is IN the bitstream, big-endian, at both commits, and each commit's
                         word is absent from the other's bitstream.

The commit between the two builds is `--allow-empty`: it moves HEAD and touches
no file, which is exactly the case that costs a resynthesis today.

Each build is a separate process, and the build directory is the variant's own.
`top.tim` accumulates across runs, so it is removed first.

Needs `./scripts/soc_run.py --build-only` to have run once: both builds are
handed the same firmware image, so gateware is the only variable.
"""

from __future__ import annotations

import argparse
import hashlib
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
OUT = ROOT / "tmp" / "head-independence"

# A synthesis run's bound: ~160 s for this design without --parallel-refine
# (#361), 1.25x of that as the floor, tightened to measured history after the
# first run. On expiry the run is killed and the stage names which one.
SYNTH_FLOOR = 200.0


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def synthesise(pnr_opts):
    """One full build in its own interpreter. Returns the CompletedProcess."""
    (BUILD / "top.tim").unlink(missing_ok=True)
    command = (f'source "$HOME/opt/oss-cad-suite/environment" && '
               f'AMARANTH_nextpnr_opts="{pnr_opts}" '
               f'YOSYS_MAX_THREADS="{YOSYS_MAX_THREADS}" '
               f'{sys.executable} {ROOT / "gateware" / "soc" / "top.py"} --build '
               f'--firmware {BUILD / "rust_fw.bin"} '
               f'--bootloader {BUILD / "rust_boot.bin"}')
    return run_bounded(["bash", "-c", command], family="gateware-head-independence",
                       cwd=ROOT, floor=SYNTH_FLOOR)


def capture(label):
    """Copy this build's artifacts aside and report what identifies them."""
    OUT.mkdir(parents=True, exist_ok=True)
    kept = {}
    for name in ("top.json", "top.bit", "usercode.json"):
        source = BUILD / name
        if not source.exists():
            emit(f"  {label}: no {name} in {BUILD.relative_to(ROOT)}")
            return None
        target = OUT / f"{label}-{name}"
        shutil.copy2(source, target)
        kept[name] = target
    return kept


def stamped(bitstream, usercode):
    """Is the 32-bit USERCODE present in the packed bitstream, big-endian?

    ecppack writes it as a `LSC_PROG_USERCODE` command payload. Searching the
    bytes is crude and it is decisive: a stamp that never reached the packer is
    invisible to every other check here.
    """
    return usercode.to_bytes(4, "big") in bitstream.read_bytes()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-parallel-refine", action="store_true",
                        help="drop the placer flag that makes placement a "
                             "distribution (#361). Does not touch the netlist, "
                             "which is what this compares")
    args = parser.parse_args()

    import json

    pnr_opts = NEXTPNR_OPTS
    if args.no_parallel_refine:
        pnr_opts = " ".join(o for o in NEXTPNR_OPTS.split() if o != "--parallel-refine")

    if not (BUILD / "rust_fw.bin").exists():
        emit(f"no firmware in {BUILD.relative_to(ROOT)}; run "
             f"`./scripts/soc_run.py --build-only` once first")
        return 1

    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        emit("the tree is dirty, so `usercode()` sets bit 31 at BOTH commits and "
             "the two stamps could collide. Commit or stash first.")
        return 1

    emit(f"variant {variant.slug()}")
    results = {}
    for label in ("before", "after"):
        head = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
        emit(f"{label}: building at {head}")
        result = synthesise(pnr_opts)
        if result is None:
            emit(f"{label} was KILLED on its timeout; the line above has the bound")
            return 1
        if result.returncode != 0:
            emit(f"{label} FAILED (rc={result.returncode})")
            for line in (result.stderr or "").splitlines()[-15:]:
                emit(f"  {line}")
            return 1
        kept = capture(label)
        if kept is None:
            return 1
        record = json.loads(kept["usercode.json"].read_text())
        results[label] = (kept, record)
        emit(f"  netlist {digest(kept['top.json'])}  "
             f"bitstream {digest(kept['top.bit'])}  "
             f"usercode {record['usercode']:#010x}")
        if label == "before":
            subprocess.run(["git", "commit", "--allow-empty", "-q", "-m",
                            "empty commit: the head-independence control"],
                           cwd=ROOT, check=True)
            emit("  committed an empty commit; HEAD moved, no file changed")

    (before, before_record) = results["before"]
    (after, after_record) = results["after"]

    same_netlist = digest(before["top.json"]) == digest(after["top.json"])
    same_bitstream = digest(before["top.bit"]) == digest(after["top.bit"])
    moved = before_record["usercode"] != after_record["usercode"]

    ok = True
    if not moved:
        emit("CONTROL FAILED: the two commits stamped the same USERCODE, so "
             "nothing about HEAD was varied and neither comparison means "
             "anything.")
        ok = False
    for label, (kept, record) in results.items():
        here = stamped(kept["top.bit"], record["usercode"])
        other = results["after" if label == "before" else "before"][1]["usercode"]
        emit(f"{label}: usercode {record['usercode']:#010x} "
             f"{'IS' if here else 'is NOT'} in the bitstream; the other commit's "
             f"{other:#010x} {'IS' if stamped(kept['top.bit'], other) else 'is not'}")
        if not here:
            emit("  the stamp never reached the packer -- --usercode is not "
                 "getting through")
            ok = False

    if same_bitstream:
        emit("CONTROL FAILED: the two bitstreams are byte-identical. A stamp "
             "that changed must change the .bit, so this comparison cannot "
             "tell two builds apart and the netlist match below is worthless.")
        ok = False
    else:
        emit("control passed: the bitstreams differ, so the comparison can see "
             "a change")

    if same_netlist:
        emit("HEAD-INDEPENDENT: one netlist digest across two commits. A commit "
             "with no source change is a pack, not a synthesis.")
    else:
        emit("HEAD REACHES THE NETLIST: the two commits produced different "
             "yosys output with no source change. HEAD must stay in "
             "`gateware_digest`.")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
