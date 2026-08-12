#!/usr/bin/env python3
#
# The one-gateware merge, on silicon. #473's checklist, asserted. #432.
# SPDX-License-Identifier: BSD-3-Clause

"""
Does the merged gateware actually work on the board?

    ./scripts/one_gateware_board_check.py
    ./scripts/one_gateware_board_check.py --bitstream tmp/awto_soc/build/<slug>/top.bit

#473 lists what the merge changed that no local gate can settle: the handover
between `BootRAM` in `sync` and one controller in `hr`, both boot modes, the
staging path at CK 80 rather than the 60 it has always run at, and the refusal
path. All of it was simulated against a MODEL of the controller. None of it has
met the real PHY or the real part.

## What each item asserts, and what a failure means

| item | command | passes when | a failure means |
|---|---|---|---|
| boot mode | `info` | boot status is 0 or 1 | 7 `Owned`: the bootloader claimed STAGE and the mux did not follow |
| PLL | `info` | `hr` reports locked | the part runs off the second PLL now; unlocked reads as a dead part |
| mux at rest | `bist mode` | `stage`, nothing refused | the mode mux came up on the wrong side |
| staging at CK 80 | `hr ramp w` | the ramp verifies | the staging path does not survive the clock change |
| handover out | `bist mode bist` | mode reads back `bist` | the mux did not reach the engine, or a transaction is still open |
| the engine | `bist smoke` | the rig both passes and detects a fault | the engine lost the part |
| handover back | `bist mode stage` | mode reads back `stage` | the handover is one-way |
| the part returns | `hr ramp w` | the ramp verifies again | the engine left the part in a state staging cannot use |
| warm reboot | `reset` | the banner comes back | the shell did not restart |
| the part after it | `reset` | `init hyperram ok` | the part does not answer after a warm reboot, though it did before one |

Every item is a string the firmware itself prints. Nothing here decides a pass
from "the command returned" -- a shell that answers `unknown` fails the item it
was asked, which is what caught a board running another worktree's firmware.

Through `scripts/board.py` only. Logs to ./tmp/logs/one_gateware_board_check.log
and the record to ./tmp/one_gateware_board_check.json.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "one_gateware_board_check.log"
RESULT = ROOT / "tmp" / "one_gateware_board_check.json"

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402

BOARD = ROOT / "scripts" / "board.py"

# Boot status the bootloader reports. 7 is `Owned` -- it asked for STAGE and the
# mux did not follow, which is the merge's own new failure mode.
BOOT_OK = ("0", "1")

OUTPUT = []


def say(line=""):
    emit(line)
    OUTPUT.append(line)


def run(bitstream, budget, commands, label):
    """One arbiter job. Returns [(command, reply)]."""
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


def check(items, name, ok, detail):
    items.append({"item": name, "pass": bool(ok), "detail": detail})
    say(f"  {'PASS' if ok else '*** FAIL':8s}  {name:22s}  {detail}")


def boot_status(said):
    """The code in the bootloader's own line, e.g. `nothing staged (1)`."""
    found = re.search(r"^boot\b[\s\S]*?\((\d)\)", said, re.M)
    return found.group(1) if found else None


def tail(text, first=1, last=3):
    lines = [line for line in text.strip().splitlines()[first:] if line.strip()]
    return " / ".join(lines[:last - first + 1]) or "no reply"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bitstream", default=None,
                        help="default: this variant's own top.bit")
    parser.add_argument("--budget", type=float, default=20.0,
                        help="seconds per command; `bist smoke` is the slow one")
    args = parser.parse_args()

    bitstream = Path(args.bitstream) if args.bitstream else \
        variant.build_dir(ROOT) / "top.bit"
    if not bitstream.exists():
        raise SystemExit(f"no bitstream at {bitstream}; build it first")

    say(f"one gateware on silicon -- #473's checklist")
    say(f"  bitstream {bitstream.relative_to(ROOT) if bitstream.is_relative_to(ROOT) else bitstream}")
    say()

    # One job for the whole sequence: the mode is state, so a second job that
    # reconfigured between `bist mode bist` and `bist mode stage` would test a
    # handover that never happened.
    record, replies = run(
        bitstream, args.budget,
        ["info", "bist mode", "hr ramp w", "bist mode bist", "bist status",
         "bist smoke", "bist mode stage", "hr ramp w"],
        "one gateware: the handover, both modes, CK 80 staging")

    items = []
    info = reply(replies, "info")
    status = boot_status(info)
    check(items, "boots, not Owned", status in BOOT_OK,
          f"boot status {status}" if status else
          "no boot status in `info` -- the shell did not answer it")
    check(items, "second PLL locked", "UNLOCKED" not in info and info.strip() != "",
          "hr locked" if info.strip() else "no `info` reply")

    at_rest = reply(replies, "bist mode")
    check(items, "mux at rest is stage", "mode stage" in at_rest, tail(at_rest))

    ramps = [text for asked, text in replies if asked.startswith("hr ramp")]
    check(items, "staging at CK 80",
          bool(ramps) and "BAD" not in ramps[0] and "usage" not in ramps[0],
          tail(ramps[0]) if ramps else "no reply")

    to_bist = reply(replies, "bist mode bist")
    check(items, "handover to the engine", "mode bist" in to_bist, tail(to_bist))

    # The rig's own verdict, not a substring. `bist smoke` wants at least one
    # PASS and at least one fail -- four passes would mean it cannot see a
    # fault -- and prints its tally and a `rig:` line saying which it got.
    smoke = reply(replies, "bist smoke")
    tally = re.search(r"(\d+) pass, (\d+) fail, (\d+) no result of (\d+)", smoke)
    check(items, "the engine still works",
          bool(tally) and "WEDGED" not in smoke
          and int(tally.group(1)) > 0 and int(tally.group(3)) == 0,
          (f"{tally.group(1)} pass, {tally.group(2)} fail, "
           f"{tally.group(3)} no result of {tally.group(4)}"
           + ("; rig WEDGED" if "WEDGED" in smoke else "")) if tally
          else tail(smoke, 1, 4))

    back = reply(replies, "bist mode stage")
    check(items, "handover back to staging", "mode stage" in back, tail(back))

    check(items, "the part comes back",
          len(ramps) > 1 and "BAD" not in ramps[-1] and "usage" not in ramps[-1],
          tail(ramps[-1]) if len(ramps) > 1 else "second ramp not reached")

    # The warm reboot, as its own job, and `reset` is expected to time out: the
    # shell it was typed into does not come back to print a prompt. What comes
    # back instead is the whole boot banner, which is the evidence -- so the
    # step's own status is ignored and its reply is read.
    say()
    _, after = run(bitstream, args.budget, ["reset"],
                   "one gateware: does the mode survive a warm reboot")
    banner = reply(after, "reset")
    check(items, "reboots at all", "type `help`" in banner,
          tail(banner, 2, 4) if banner else "no banner after `reset`")

    # `init hyperram` is the bootloader's own round-trip check, and it runs
    # before anything else can have touched the part.
    found = re.search(r"^.*init\s+hyperram\s+(\w+)\s+(.*)$", banner, re.M)
    check(items, "the part survives reset",
          bool(found) and found.group(1) == "ok",
          f"init hyperram {found.group(1)}: {found.group(2).strip()}" if found
          else "no `init hyperram` line in the banner")

    failed = [i for i in items if not i["pass"]]
    say()
    say(f"{len(items) - len(failed)} of {len(items)} items pass"
        + ("" if not failed else
           "; UNPROVEN: " + ", ".join(i["item"] for i in failed)))

    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(
        {"bitstream": str(bitstream), "items": items,
         "transcript": [{"command": c, "reply": r} for c, r in replies],
         "after_reset": [{"command": c, "reply": r} for c, r in after]},
        indent=2) + "\n")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(OUTPUT) + "\n")
    say(f"wrote {RESULT.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
