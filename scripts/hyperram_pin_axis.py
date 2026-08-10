#!/usr/bin/env python3
#
# One pin-attribute point: patch, configure, and record it against its control. #311.
# SPDX-License-Identifier: BSD-3-Clause

"""Move one FPGA pin attribute and measure what it did.

    ./scripts/hyperram_pin_axis.py --label baseline --repeat 3
    ./scripts/hyperram_pin_axis.py --label hyst-off dq.HYSTERESIS=OFF rwds.HYSTERESIS=OFF
    ./scripts/hyperram_pin_axis.py --label ck-drive-4 ck.DRIVE=4
    ./scripts/hyperram_pin_axis.py --report          # every point recorded so far

One invocation is one point:

1. `hyperram_pin_patch.py` rewrites the attributes into a BUILT bitstream, and
   proves only the targeted PIO tiles moved.
2. `apollo configure` loads it. Every point starts from a fresh configure, which
   is also the only way out of the DQS read path's slipped state (#349).
3. `hyperram_matrix_diff.py` records N identical runs of the 4096-cell matrix,
   each stamped with the pin state unpacked from the bitstream that was loaded.
4. The N runs are diffed against each other. **That spread is this point's noise
   floor**, and only movement beyond it can be attributed to the attribute.

## Why `--repeat` defaults to 3 and not 2

Two runs give one comparison, and one comparison cannot distinguish "these pins
are marginal" from "the read path slipped during run 2". Three give two
comparisons and a middle: a slip shows as one large step that does not come back,
marginality as small movement in both directions.

## What the control is

`--label baseline` with no assignments still patches: it packs the build's own
`top.config` through the same `ecppack`, so the control bitstream differs from
every measured point ONLY in the PIO bits. Comparing against the build's
`top.bit` instead would compare two packer invocations as well.

Runs land in `results/hyperram/`; the log in `tmp/logs/hyperram-pin-axis.log`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hyperram_matrix_diff as matrix  # noqa: E402
import hyperram_pin_patch as pins  # noqa: E402
import soc_confirm  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "hyperram-pin-axis.log"


def emit(line=""):
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def patch(build_dir, label, assignments):
    """Produce the point's bitstream. Returns its path.

    Shelled out rather than imported so the patch's own console output -- the
    config delta and the round-trip verdict -- lands in this log verbatim. That
    text is the evidence that the bitstream is only the requested change.
    """
    out = pins.OUT / f"{label}.bit"
    command = [sys.executable, str(ROOT / "scripts" / "hyperram_pin_patch.py"),
               "--build-dir", str(build_dir), "--out", str(out), *assignments]
    if not assignments:
        command.append("--repack")
    result = subprocess.run(command, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        emit("  | " + line)
    if result.returncode != 0:
        emit(result.stderr[-600:])
        raise SystemExit(f"pin patch failed for {label}")
    return out


def carries_rung(rung):
    """Does the design that answered carry the CK rung asked for?

    Identity, not liveness -- `soc_confirm` has already proved something is
    running. A board answering with a different rung is a wrong bitstream, which
    is not a case a retry improves.
    """
    try:
        board = matrix.Board()
    except Exception as failure:
        emit(f"  rung check: no console ({failure})")
        return False
    try:
        # Asked twice: the banner is flushed on the first received byte and
        # interleaves with the first command's echo, so the shell answers
        # "unknown command" to a perfectly good line.
        for _ in range(2):
            if matrix.RUNG.findall(board.send(f"bist ck {rung}", 4)):
                return True
    finally:
        board.close()
    return False


def configure(bitstream, rung):
    """Load the point's bitstream and prove the design is running.

    The liveness gate used to live here, added after this rig was bitten by a
    configure that returned zero over a blank FPGA. It is `soc_confirm` now
    (#360), so every path gets it and each cause is named rather than reported
    as "the board did not come up".
    """
    if soc_confirm.configure_and_confirm(bitstream) != 0:
        raise SystemExit(f"no design running after configuring {bitstream}")
    if not carries_rung(rung):
        raise SystemExit(
            f"the design answers but reports no CK rung {rung}: the bitstream on "
            f"the board is not {bitstream.name}")
    emit(f"  configured {bitstream.name}, board answers on rung {rung}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("assignments", nargs="*", metavar="GROUP.ATTR=VALUE")
    parser.add_argument("--label", required=False, default=None,
                        help="names the bitstream and the saved runs")
    parser.add_argument("--build-dir", type=Path, default=pins.BUILD)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--passes", type=int, default=2,
                        help="passes per cell; 4096 cells either way")
    parser.add_argument("--rung", type=int, default=0)
    parser.add_argument("--against", default=None, metavar="LABEL",
                        help="also diff this point's last run against the last "
                             "run recorded under LABEL -- the control")
    parser.add_argument("--report", action="store_true",
                        help="summarise every recorded point and stop")
    args = parser.parse_args()

    if args.report:
        return report()
    if not args.label:
        raise SystemExit("--label is required to record a point")

    build_dir = args.build_dir.resolve()
    emit(f"\n=== {args.label}  {' '.join(args.assignments) or '(control)'}")
    bitstream = patch(build_dir, args.label, args.assignments)
    configure(bitstream, args.rung)

    written = [matrix.record(args.label, args.passes, args.rung,
                             bitstream, build_dir)
               for _ in range(args.repeat)]
    spread = 0
    for first, second in zip(written, written[1:]):
        spread += matrix.diff(str(first), str(second))
    emit(f"\n{args.label}: {spread} cell(s) moved across {len(written)} identical "
         f"runs -- this point's own noise floor")

    if args.against:
        # LAST run of each side, not the first. A fresh configure's opening run
        # is the one that catches a marginal cell mid-settle; comparing settled
        # against settled keeps the point's own noise out of the control diff.
        control = sorted(matrix.RESULTS.glob(f"*-{args.against}.json"))
        if not control:
            raise SystemExit(f"no run labelled {args.against!r} to compare against")
        emit(f"\n--- {args.label} vs {args.against}, the control")
        against = matrix.diff(str(control[-1]), str(written[-1]))
        emit(f"\n{args.label}: {against} cell(s) differ from {args.against}, "
             f"against a noise floor of {spread}")
    return 0


def report():
    """Every recorded run, newest last: pass count and the pins it was taken at."""
    rows = []
    for path in sorted(matrix.RESULTS.glob("*.json")):
        run = matrix.load(path)
        pin = run.get("pins")
        if pin is None:
            state = "pins UNRECORDED"
        else:
            # Collapsed to distinct values per group: eight DQ pads at the same
            # setting is one fact, and printed per pad it buries the one pad that
            # differs.
            seen = {}
            for key, attrs in sorted(pin.items()):
                for attr in ("DRIVE", "SLEWRATE", "HYSTERESIS", "PULLMODE"):
                    seen.setdefault((key.split("/")[0], attr), set()).add(
                        str(attrs.get(attr)))
            state = " ".join(f"{group}.{attr}={'/'.join(sorted(values))}"
                             for (group, attr), values in sorted(seen.items()))
        rows.append((path.name, run["label"], run["summary"]["pass"],
                     run.get("ck_mhz"), state))
    for name, label, passed, ck, state in rows:
        emit(f"{name:46s} {label:22s} {passed:5d} pass  CK {ck}")
        emit(f"    {state[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
