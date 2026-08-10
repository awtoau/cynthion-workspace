#!/usr/bin/env python3
#
# Two CPU generations at once, and what each of them got. #306.
# SPDX-License-Identifier: BSD-3-Clause

"""Start two VexiiRiscv generations together and check both netlists.

    ./scripts/vexii_generate_race.py
    ./scripts/vexii_generate_race.py --outdir tmp/vexii-race

The generator writes `VexiiRiscv.v` into the checkout -- a fixed path, shared by
every build on this machine -- and `generate()` copies it to the caller's
`output=` under a lock. This is the check that the lock does what it claims, and
it is a check because the dangerous outcome of the race is not a crash: it is a
build that SUCCEEDS carrying the other configuration's core, which #306 recorded
as a bitstream that configured and left the board mute.

Two different configurations are generated so that "got the other one's file" is
visible. Each netlist states its own geometry -- one `ways_<n>_mem [0:sets-1]`
tag memory per way -- so what came back can be compared against what was asked
for.

Evidence, all four of which have to hold:

  * each output has the geometry its own process asked for
  * the two outputs differ
  * the two sbt runs did not overlap in time -- that is the lock working
  * both processes named the same lock file

Running it costs two sbt runs (~40 s each, serialised by the very lock being
tested). It touches no board and builds no gateware.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# One tag memory per way, addressed by set: `FetchL1Plugin_logic_ways_0_mem
# [0:63]` is way 0 of a 64-set cache. The netlist states its own geometry.
WAY_MEM_RE = re.compile(
    r"reg \[\d+:\d+\] (FetchL1|LsuL1)Plugin_logic_ways_(\d+)_mem \[0:(\d+)\]")

# The two configurations to generate at once. Different enough that a swap is
# obvious in the geometry, and both are configurations this SoC has shipped.
CONFIGS = ({"cache_sets": 64, "cache_ways": 2},
           {"cache_sets": 128, "cache_ways": 1})

CHILD = """
import json, sys, time
sys.path.insert(0, {gateware!r})
import cpu.cpu as c
started = time.time()
path = c.generate(0x0, {sets}, output=__import__("pathlib").Path({out!r}),
                  cache_ways={ways})
print(json.dumps({{"started": started, "finished": time.time(),
                   "netlist": str(path), "checkout": str(c.VEXII),
                   "lock": str(c.VEXII.parent.parent / "tmp"
                              / "vexii-generate.lock")}}))
"""


def geometry(path: Path) -> dict:
    """{plugin: (ways, sets)} as the netlist itself declares them."""
    found = {}
    for plugin, way, last in WAY_MEM_RE.findall(path.read_text()):
        ways, sets = found.get(plugin, (0, int(last) + 1))
        found[plugin] = (max(ways, int(way) + 1), sets)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outdir", type=Path,
                        default=ROOT / "tmp" / "vexii-race",
                        help="where each process writes its own netlist")
    args = parser.parse_args()
    outdir = args.outdir if args.outdir.is_absolute() else ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    children = []
    for index, config in enumerate(CONFIGS, 1):
        out = outdir / f"VexiiRiscv-{config['cache_sets']}x{config['cache_ways']}.v"
        out.unlink(missing_ok=True)
        code = CHILD.format(gateware=str(ROOT / "gateware" / "soc"),
                            sets=config["cache_sets"], ways=config["cache_ways"],
                            out=str(out))
        emit(f"start {index}: {config['cache_sets']} sets x {config['cache_ways']} "
             f"ways -> {out.relative_to(ROOT)}")
        children.append((config, out, subprocess.Popen(
            [sys.executable, "-c", code], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))

    rows = []
    for config, out, child in children:
        stdout, stderr = child.communicate()
        row = {"config": config, "rc": child.returncode, "output": str(out)}
        try:
            row.update(json.loads(stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            row["stderr"] = stderr[-600:]
        rows.append(row)

    ok = True
    for row in rows:
        config = row["config"]
        want = (config["cache_ways"], config["cache_sets"])
        got = geometry(Path(row["output"])) if Path(row["output"]).exists() else {}
        matched = got and all(value == want for value in got.values())
        ok &= bool(matched) and row["rc"] == 0
        emit(f"{config['cache_sets']}x{config['cache_ways']}: rc={row['rc']} "
             f"geometry={ {k: f'{w} ways x {s} sets' for k, (w, s) in got.items()} } "
             f"{'OK' if matched else 'WRONG -- this is the #306 failure'}")
        if "stderr" in row:
            emit(f"  {row['stderr']}")

    if all("started" in row for row in rows):
        first, second = sorted(rows, key=lambda r: r["started"])
        overlap = first["finished"] - second["started"]
        emit(f"checkout {first['checkout']}")
        emit(f"lock     {first['lock']}")
        emit(f"locks match: {first['lock'] == second['lock']}")
        emit(f"overlap: {overlap:+.1f} s "
             f"({'SERIALISED' if overlap <= 0 else 'CONCURRENT -- the lock did not hold'})")
        ok &= overlap <= 0 and first["lock"] == second["lock"]

    contents = {Path(row["output"]).read_bytes() for row in rows
                if Path(row["output"]).exists()}
    emit(f"distinct netlists: {len(contents)} of {len(rows)}")
    ok &= len(contents) == len(rows)

    emit("PASS: two concurrent generations, each with its own core" if ok
         else "FAIL: see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
