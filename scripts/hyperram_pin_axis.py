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

LOG = ROOT / "tmp" / "logs" / "hyperram-pin-axis.log"

CLI = Path("repos") / "apollo" / "apollo_fpga" / "commands" / "cli.py"


def apollo_cli():
    """Apollo's CLI, which a git worktree does not have a copy of.

    Submodules live in the main working tree only, so an agent running from
    `.claude/worktrees/...` finds no `repos/` at all. `--git-common-dir` names the
    shared `.git`, whose parent is that main tree.
    """
    if (ROOT / CLI).exists():
        return ROOT / CLI
    common = subprocess.run(("git", "rev-parse", "--path-format=absolute",
                             "--git-common-dir"), cwd=ROOT,
                            capture_output=True, text=True, check=True)
    main = Path(common.stdout.strip()).parent / CLI
    if not main.exists():
        raise SystemExit(f"no apollo CLI at {ROOT / CLI} or {main}")
    return main


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


# Apollo must take the shared JTAG bus off the running design before it can
# configure. When the design still holds it the part ID reads back `ffffffff` and
# the attempt fails outright; an immediate retry succeeds, so this is contention
# and not a dead board. Three attempts, each a full handshake of about a second.
CONFIGURE_TRIES = 3


def alive(rung):
    """Does the configured design answer, and does it carry the rung asked for?

    **A zero exit from `apollo configure` is not a running design.** Once, an
    attempt that failed with "bitstream provides data past the device's SRAM
    array" was followed by a retry that returned zero and left the FPGA blank --
    no console, no USB. The next thing to touch the board would have called that
    a pin attribute killing the design, which is precisely the misattribution
    this whole rig exists to avoid.

    So the gate is the board's own answer, not the programmer's exit code.
    """
    try:
        board = matrix.Board()
    except Exception as failure:                        # no tty yet, or no design
        emit(f"  post-configure check: no console ({failure})")
        return False
    try:
        # Asked twice. The banner is held in the log ring and flushed on the first
        # received byte, so it interleaves with the first command's echo and the
        # shell answers "unknown command" to a perfectly good line. One repeat is
        # enough -- the ring is drained by then -- and the alternative is scoring
        # a live board dead.
        for _ in range(2):
            text = board.send(f"bist ck {rung}", 4)
            if matrix.RUNG.findall(text):
                return True
    finally:
        board.close()
    emit("  post-configure check: console answers but reports no CK rung")
    return False


def configure(bitstream, rung):
    for attempt in range(1, CONFIGURE_TRIES + 1):
        result = subprocess.run([sys.executable, str(apollo_cli()), "configure",
                                 str(bitstream)], capture_output=True, text=True)
        if result.returncode != 0:
            emit(f"  configure attempt {attempt}/{CONFIGURE_TRIES} failed: "
                 + (result.stderr or result.stdout).strip().splitlines()[-1][:160])
            continue
        if alive(rung):
            emit(f"  configured {bitstream.name}, board answers"
                 + (f" (attempt {attempt})" if attempt > 1 else ""))
            return
        emit(f"  configure attempt {attempt}/{CONFIGURE_TRIES} returned 0 but the "
             f"board did not come up")
    raise SystemExit(f"configure failed {CONFIGURE_TRIES} times for {bitstream}")


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
            state = " ".join(
                f"{key.split('/')[0]}.{attr}={value}"
                for key, attrs in sorted(pin.items())
                for attr, value in sorted(attrs.items())
                if attr in ("DRIVE", "SLEWRATE", "HYSTERESIS", "PULLMODE"))
        rows.append((path.name, run["label"], run["summary"]["pass"],
                     run.get("ck_mhz"), state))
    for name, label, passed, ck, state in rows:
        emit(f"{name:46s} {label:22s} {passed:5d} pass  CK {ck}")
        emit(f"    {state[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
