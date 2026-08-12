#!/usr/bin/env python3
#
# Does the part still hold what was written after the FPGA is reloaded? #479.
# SPDX-License-Identifier: BSD-3-Clause

"""
Retention across a reconfigure: write in one job, read in the next.

    ./scripts/hyperram_retention.py
    ./scripts/hyperram_retention.py --bitstream tmp/bit-prefix.bit

## Why this exists rather than `hr ramp w`

`hr ramp w` writes 256 bytes and verifies them in the same breath, and it
returned `256/256 correct -- the path is clean` on a part that was held in
hardware reset and stored nothing: the value came back out of the write path.
It gave a PASS for the one thing it was asked about, and an earlier
"CK 80 staging PASS" was withdrawn because of it (#479).

A retention check has to put the write and the read on opposite sides of
something that clears every place the value could be hiding. This one uses a
**reconfigure**: the bitstream is reloaded, so the fabric, the controller, the
PHY and the CPU's caches all start again from nothing. Only the part itself
carries anything across.

## The sequence

    job 1   configure, `bist smoke` (the engine writes its own patterns over
            the array), `hr ramp` -- expected WRONG, which is what proves the
            ramp is not left over from an earlier session -- then `hr ramp w`
            to write it, then `reset`

    job 2   the `reset` times out with no prompt, so the arbiter marks the
            board unknown and RECONFIGURES for this job. Then `hr ramp`
            VERIFY-ONLY, `hr read 4000` and `hr cross`.

Job 2 writes nothing before it reads. A part that stores nothing reads zeros
here, whatever job 1 said.

## Proven able to fail

Run it against a bitstream built before the #479 fix: job 2's ramp comes back
`255/256 wrong`. The negative control is the point of the script.

Through `scripts/board.py` only. Log `./tmp/logs/hyperram_retention.log`,
record `./tmp/hyperram_retention.json`.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT = ROOT / "tmp" / "hyperram_retention.json"

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402

BOARD = ROOT / "scripts" / "board.py"

OUTPUT = []


def say(line=""):
    emit(line)
    OUTPUT.append(line)


def run(bitstream, budget, commands, label):
    """One arbiter job. Returns the record and [(command, reply)]."""
    done = subprocess.run(
        [sys.executable, str(BOARD), "--json", "run",
         "--bitstream", str(bitstream), "--budget", str(budget),
         "--label", label, *commands],
        cwd=ROOT, capture_output=True, text=True)
    try:
        record = json.loads(done.stdout[done.stdout.index("{"):])
    except ValueError:
        return {"status": "no record",
                "error": (done.stderr or done.stdout)[-400:]}, []
    return record, [(step.get("command", ""), step.get("reply", ""))
                    for step in record.get("transcript") or []]


def reply(replies, command):
    """Every reply to `command`, joined. Empty when it was never asked."""
    return "\n".join(text for asked, text in replies
                     if asked.strip() == command.strip())


def ramp_verdict(text):
    """`(correct, wrong)` out of an `hr ramp` reply, or `None` if it did not run."""
    if found := re.search(r"(\d+)/(\d+) correct", text):
        return int(found.group(1)), 0
    if found := re.search(r"(\d+)/(\d+) wrong", text):
        return int(found.group(2)) - int(found.group(1)), int(found.group(1))
    return None


def check(items, name, ok, detail):
    items.append({"item": name, "pass": bool(ok), "detail": detail})
    say(f"  {'PASS' if ok else '*** FAIL':8s}  {name:34s}  {detail}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bitstream", default=None,
                        help="default: this variant's own top.bit")
    parser.add_argument("--budget", type=float, default=25.0,
                        help="seconds per command; `bist smoke` is the slow one")
    parser.add_argument("--expect-fail", action="store_true",
                        help="the negative control: exit 0 only if it FAILS")
    args = parser.parse_args()

    bitstream = Path(args.bitstream) if args.bitstream else \
        variant.build_dir(ROOT) / "top.bit"
    if not bitstream.exists():
        raise SystemExit(f"no bitstream at {bitstream}; build it first")

    say("HyperRAM retention across a reconfigure")
    say(f"  bitstream {bitstream}")
    say()

    # `reset` last, and it is expected to time out: no prompt comes back, so the
    # arbiter marks the board unknown and the next job reconfigures. That is the
    # reload this check is built around.
    first, wrote = run(
        bitstream, args.budget,
        ["info", "init", "bist mode", "bist mode bist", "bist smoke",
         "bist mode stage", "hr ramp", "hr ramp w", "reset"],
        "retention: scramble, write, then let the board go unknown")

    items = []
    info = reply(wrote, "info")
    check(items, "the board answered at all", "aux>" in info or info.strip() != "",
          (info.strip().splitlines() or ["no reply"])[0][:70])

    # #484's symptom, in the firmware's own words: `init hyperram FAIL -- no
    # round trip after 4 CS# pulses -- the part is not answering`.
    started = reply(wrote, "init")
    line = next((row for row in started.splitlines() if "hyperram" in row), "")
    check(items, "`init hyperram` answers", bool(line) and "FAIL" not in line,
          line.strip()[:70] or "`init` said nothing about hyperram")

    smoke = reply(wrote, "bist smoke")
    tally = re.search(r"(\d+) pass, (\d+) fail, (\d+) no result of (\d+)", smoke)
    check(items, "the BIST engine completes cells",
          bool(tally) and "WEDGED" not in smoke and int(tally.group(3)) == 0,
          (f"{tally.group(1)} pass, {tally.group(2)} fail, "
           f"{tally.group(3)} no result of {tally.group(4)}"
           + ("; rig WEDGED" if "WEDGED" in smoke else "")) if tally
          else "no tally in the reply")

    # The ramp BEFORE the write, after the engine has been over the array. Wrong
    # here is what rules out "the pattern was already there from another run".
    before = ramp_verdict(reply(wrote, "hr ramp"))
    check(items, "the ramp is gone before it is written",
          before is not None and before[1] > 0,
          f"{before[1]} of 256 wrong" if before else "no ramp reply")

    after_write = ramp_verdict(reply(wrote, "hr ramp w"))
    check(items, "the write path itself reports clean",
          after_write is not None and after_write[1] == 0,
          f"{after_write[0]}/256 correct" if after_write else "no reply")

    # THE MEASUREMENT. A second job, which reconfigures because the `reset`
    # above left no prompt. Nothing here writes before the read.
    say()
    second, read = run(
        bitstream, args.budget,
        ["hr ramp", "hr read 4000", "hr cross", "bist status"],
        "retention: verify-only, on the far side of a reconfigure")

    reconfigured = second.get("provenance", {}).get("configured_by_this_job")
    check(items, "the FPGA was reloaded for this job", bool(reconfigured),
          "configure ran" if reconfigured else
          "NO reconfigure -- this proves nothing about retention")

    kept = ramp_verdict(reply(read, "hr ramp"))
    check(items, "the part KEPT the ramp across it",
          kept is not None and kept[1] == 0,
          f"{kept[0]}/256 correct" if kept and kept[1] == 0 else
          (f"{kept[1]} of 256 wrong" if kept else "no ramp reply"))

    cross = reply(read, "hr cross")
    check(items, "the two ports agree", "DISAGREE" not in cross and cross.strip() != "",
          "ports agree" if "DISAGREE" not in cross and cross.strip() else
          "ports DISAGREE")

    passed = all(item["pass"] for item in items)
    say()
    say(f"{sum(item['pass'] for item in items)} of {len(items)} items pass")
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(
        {"bitstream": str(bitstream), "items": items,
         "jobs": [first, second], "output": OUTPUT}, indent=2))
    say(f"record {RESULT.relative_to(ROOT)}")

    if args.expect_fail:
        say("negative control: a PASS here would mean the check cannot fail")
        return 1 if passed else 0
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
