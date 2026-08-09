#!/usr/bin/env python3
#
# The whole HyperRAM matrix: every reachable CK, every runtime axis. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Sweep CK across every value the second PLL reaches, and the runtime axes at each.

    ./scripts/hyperram_matrix.py                 # the lot
    ./scripts/hyperram_matrix.py --ck 100 120    # only these
    ./scripts/hyperram_matrix.py --passes 8      # per cell

Results stream to `tmp/hyperram-matrix.csv` AS THEY ARRIVE, and the log to
`tmp/logs/hyperram_matrix.log`. Partial results are the normal case: this takes
the better part of an hour and a wedged engine or a failed build must not cost
the rows already collected.

## The shape

| axis | values | how |
|---|---|---|
| CK frequency | 15 reachable 100-200 MHz | **one bitstream each** |
| device drive `CR0[14:12]` | 8 | runtime |
| clock mode `CR1[6]` | 2 | runtime |
| capture phase `READCLKSEL` | 8 | runtime |

So 15 builds x 128 cells = 1,920 cells. The inner 128 are one `bist sweep`.

## Builds fail timing about half the time, so it retries

Identical source has produced `clk` Fmax from 56.21 to 67.27 MHz against a
60 MHz constraint (#306). A build that fails timing is not a bad configuration,
it is the same configuration placed differently, so retrying is legitimate --
and NOT retrying would drop CK rungs for a reason unrelated to the part.

Every attempt is logged with its Fmax, so a rung that needed three goes is
visible rather than smoothed over. `--retries 0` turns it off.

## What a row means

`verdict` is the engine's own, and it cannot be `PASS` without the negative
control having run AND failed. A `NO RESULT` row is kept, not dropped: a cell
that produced nothing is data about the rig, and dropping it would make the
matrix look more complete than it is.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from devlog import emit  # noqa: E402
import soc_shell  # noqa: E402
from hyperram_clocks import reachable_ck  # noqa: E402

CSV = ROOT / "tmp" / "hyperram-matrix.csv"

# `drive  clk  sel   errors     words   control  verdict`
ROW = re.compile(r"^\s*(\d)\s+(dif|se)\s+(\d)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*?)\s*$")

FMAX = re.compile(r"Max frequency for clock\s+'\$glbnet\$clk':\s*([\d.]+) MHz.*?(PASS|FAIL)")


def build_and_load(ck_mhz, retries):
    """One bitstream at this CK, loaded. Returns (ok, fmax, attempts)."""
    env = dict(os.environ)
    env["CYNTHION_HYPERRAM_BIST"] = "1"
    env["CYNTHION_HYPERRAM_CK_MHZ"] = f"{ck_mhz:g}"
    log = ROOT / "tmp" / "logs" / f"matrix-ck{ck_mhz:g}.log"

    for attempt in range(1, retries + 2):
        # `--force-flash` because the firmware is identical between rungs and the
        # written-image check would skip it; the CONFIGURE is what must happen.
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "soc_run.py"),
             "--skip-tests", "--force-flash"],
            cwd=ROOT, env=env, capture_output=True, text=True)
        log.write_text(done.stdout + done.stderr)
        found = FMAX.search(done.stdout)
        fmax = float(found.group(1)) if found else None
        if done.returncode == 0:
            return True, fmax, attempt
        emit(f"    attempt {attempt}: build failed"
             + (f", clk {fmax:.2f} MHz" if fmax else "")
             + (" -- retrying, this is #306 not the design" if attempt <= retries
                else " -- giving up on this rung"))
    return False, None, retries + 1


def sweep(passes, budget_s):
    """Drive one `bist sweep` and return its rows."""
    link = soc_shell.Link.open(None)
    try:
        link.write(b"\r")
        link.read_until_prompt(budget_s=2.0)
        link.write(f"bist sweep {passes}\r".encode())
        text = link.read_until_prompt(budget_s=budget_s).decode("utf-8", "replace")
    finally:
        link.close()

    rows = []
    for line in text.splitlines():
        found = ROW.match(line)
        if not found:
            continue
        drive, clk, sel, errors, words, control, verdict = found.groups()
        rows.append(dict(drive=int(drive), clock=clk, readclksel=int(sel),
                         errors=int(errors), words=int(words),
                         control_errors=int(control), verdict=verdict))
    return rows, text


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ck", type=float, nargs="*", default=None,
                        help="only these CK values; default is every reachable one")
    parser.add_argument("--passes", type=int, default=8,
                        help="passes per cell (default 8 = 1024 words)")
    parser.add_argument("--retries", type=int, default=2,
                        help="build retries per rung when timing fails (#306)")
    # **Waits for**: the shell's prompt after a 128-cell sweep.
    # **Expected**: MEASURED at under 1 second on the board -- the engine runs a
    #   cell in microseconds and the console is the slow part.
    # **Multiplier**: 3x. `read_until_prompt` returns on the prompt rather than
    #   on a duration, so this only bites when nothing comes back.
    # **On expiry**: the rung records zero cells, which is how a wedged engine
    #   is reported. It was 150 s -- 150x the worst case -- so a wedged rung sat
    #   there for two and a half minutes to learn that nothing had arrived.
    parser.add_argument("--budget", type=float, default=3.0,
                        help="seconds to wait for one 128-cell sweep (it takes <1)")
    args = parser.parse_args()

    rungs = args.ck if args.ck else reachable_ck(100, 200, dqs=True)
    emit(f"hyperram matrix: {len(rungs)} CK rungs x 128 cells, "
         f"{args.passes} passes each")
    emit(f"  CK: {', '.join(f'{c:g}' for c in rungs)}")
    emit(f"  streaming to {CSV.relative_to(ROOT)}")
    emit("")

    fields = ["ck_mhz", "drive", "clock", "readclksel", "errors", "words",
              "control_errors", "verdict", "fmax_mhz", "build_attempts"]
    CSV.parent.mkdir(parents=True, exist_ok=True)
    with CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()

        started = time.monotonic()
        for index, ck in enumerate(rungs, 1):
            emit(f"[{index}/{len(rungs)}] CK {ck:g} MHz -- building")
            ok, fmax, attempts = build_and_load(ck, args.retries)
            if not ok:
                emit(f"  SKIPPED: no bitstream at CK {ck:g}")
                continue
            # `fmax` is None when synthesis was SKIPPED -- the digest matched, so
            # the bitstream was already correct and nextpnr printed no timing
            # line. That is a valid outcome, not a missing measurement, and
            # formatting None with `:.2f` raised four frames up. Same defect as
            # the one in soc_generate_pac.py's cross-check: a diagnostic that
            # crashes instead of reporting.
            emit(f"  built in {attempts} attempt(s), "
                 + (f"clk {fmax:.2f} MHz" if fmax is not None
                    else "synthesis skipped, bitstream already current")
                 + " -- sweeping")

            rows, text = sweep(args.passes, args.budget)
            (ROOT / "tmp" / "logs" / f"matrix-sweep-ck{ck:g}.txt").write_text(text)
            for row in rows:
                writer.writerow(dict(row, ck_mhz=ck,
                                     fmax_mhz="" if fmax is None else fmax,
                                     build_attempts=attempts))
            handle.flush()

            tally = {}
            for row in rows:
                key = "PASS" if row["verdict"] == "PASS" else (
                    "fail" if row["verdict"] == "fail" else "NO RESULT")
                tally[key] = tally.get(key, 0) + 1
            clean = [r["errors"] for r in rows if r["verdict"] == "fail"]
            emit(f"  {len(rows)} cells: "
                 + ", ".join(f"{v} {k}" for k, v in sorted(tally.items()))
                 + (f"; best {min(clean)} errors" if clean else ""))
            if not rows:
                emit("  NOTHING CAME BACK -- the engine is wedged at this rung")

        emit("")
        emit(f"done in {(time.monotonic() - started) / 60:.1f} min "
             f"-> {CSV.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
