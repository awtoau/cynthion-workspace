#!/usr/bin/env python3
#
# Does the die heat under sustained load, and where does it plateau? #341.
# SPDX-License-Identifier: BSD-3-Clause

"""
Hold the CPU at full load for MINUTES and sample the die temperature throughout.

    ./scripts/die_thermal_curve.py --minutes 5
    ./scripts/die_thermal_curve.py --minutes 10 --sample-every 4

## Why minutes, and why sampled

Every die temperature this project has recorded is 50 C, at idle and under a
half-second load alike. Half a second cannot heat a die, so the identical
readings were never evidence of anything -- the question had not been asked.

Two outcomes and both are results:

  * **It climbs.** `tCSM` halves above 85 C (#341), and the HyperRAM burst cap
    this design derives is built from a tCSM taken at whatever temperature the
    die happened to be. A die that reaches 85 under load means that cap is
    wrong, not marginal.
  * **It does not move at all, over minutes, at 100% busy.** Then the DTR is
    the thing to doubt: a sensor returning one number regardless is the same
    shape as every other dead instrument found here. `dtr code` is reported
    alongside the degrees for exactly that reason -- the code is the
    measurement and the degrees are a conversion of it.

Sampled throughout rather than at the ends, because a plateau cannot be told
from a flat line by two points.

## The load

`cpu stress`, which is capped at 2000 ms a call, so the run is that many calls
back to back. Each one is verified in firmware: an 8 KiB strided walk against
the 4 KiB D-cache checksummed against a host-computed value, 64 deliberate
exceptions a pass, and the timer tick. A rung that computes wrongly while
heating says so rather than merely getting hot.

`info` between batches for the reading. It costs ~40 ms against 2 s of load, so
the duty cycle is above 98%.

Through `scripts/board.py` only. Logs to ./tmp/logs/die_thermal_curve.log,
samples to ./tmp/die_thermal_curve.json.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "die_thermal_curve.log"
RESULT = ROOT / "tmp" / "die_thermal_curve.json"

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402

BOARD = ROOT / "scripts" / "board.py"

# The firmware's own ceiling on one `cpu stress` call, from
# `shell/cpu.rs:STRESS_MAX_MS`. The run length is a count of these.
STRESS_MS = 2000

OUTPUT = []


def say(line=""):
    emit(line)
    OUTPUT.append(line)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--sample-every", type=int, default=3,
                        help=f"`info` after this many {STRESS_MS} ms loads")
    parser.add_argument("--bitstream", default=None)
    args = parser.parse_args()

    bitstream = Path(args.bitstream) if args.bitstream else \
        variant.build_dir(ROOT) / "top.bit"
    if not bitstream.exists():
        raise SystemExit(f"no bitstream at {bitstream}; build it first")

    loads = max(1, round(args.minutes * 60 * 1000 / STRESS_MS))
    commands = ["info"]
    for index in range(loads):
        commands.append(f"cpu stress {STRESS_MS}")
        if (index + 1) % args.sample_every == 0 or index + 1 == loads:
            commands.append("info")

    # Per command, not for the run: `cpu stress 2000` is 2 s of work, and the
    # arbiter's default of 2 s would expire on every one of them.
    #
    #   waits for   one `cpu stress` call plus its reply
    #   expected    2.0 s, the firmware's own ceiling
    #   multiplier  2.0x, because a rung near its limit runs the same loop
    #               slower and the point is to read what it prints, not to
    #               time it
    #   on expiry   the step is `timeout` and the run stops at that sample
    budget = STRESS_MS / 1000 * 2

    say(f"die thermal curve: {loads} x {STRESS_MS} ms of verified full load "
        f"({args.minutes:g} min), sampled every {args.sample_every}")
    say(f"  bitstream {bitstream.name}, budget {budget:g} s/command")
    say()

    done = subprocess.run(
        [sys.executable, str(BOARD), "--json", "run",
         "--bitstream", str(bitstream), "--budget", str(budget),
         "--label", f"die thermal curve, {args.minutes:g} min", *commands],
        cwd=ROOT, capture_output=True, text=True)
    try:
        record = json.loads(done.stdout[done.stdout.index("{"):])
    except ValueError:
        raise SystemExit((done.stderr or done.stdout)[-800:])

    samples, wrong, passes = [], 0, 0
    for step in record.get("transcript") or []:
        text = step.get("reply", "")
        if step.get("command", "").strip() == "info":
            found = re.search(r"die ([+-]?\d+) C \(dtr code (\d+)\)", text)
            clock = re.search(r"^(\d+\.\d+) ", text.strip(), re.M) or \
                re.search(r"(\d{6}\.\d{3})", text)
            if found:
                samples.append({"uptime": clock.group(1) if clock else None,
                                "celsius": int(found.group(1)),
                                "dtr_code": int(found.group(2))})
        elif step.get("command", "").startswith("cpu stress"):
            if "verdict  PASS" in text:
                passes += 1
            elif "BAD" in text or "FAIL" in text:
                wrong += 1

    say(f"  load: {passes} of {loads} calls verified PASS, {wrong} reported a "
        f"wrong answer")
    say()
    say(f"  {'uptime':>12}  {'die C':>6}  {'dtr':>4}")
    for sample in samples:
        say(f"  {sample['uptime'] or '?':>12}  {sample['celsius']:>6}  "
            f"{sample['dtr_code']:>4}")

    degrees = [s["celsius"] for s in samples]
    say()
    if not degrees:
        say("  NO SAMPLES -- `info` did not report a die temperature")
    elif len(set(degrees)) == 1:
        say(f"  FLAT: {degrees[0]} C on every one of {len(degrees)} samples "
            f"across {args.minutes:g} minutes at full load, dtr code "
            f"{samples[0]['dtr_code']} throughout.")
        say(f"  A sensor that returns one number regardless is not a "
            f"measurement. Doubt the DTR before believing the die.")
    else:
        say(f"  {min(degrees)} C -> {max(degrees)} C, "
            f"{max(degrees) - min(degrees)} C of rise over "
            f"{args.minutes:g} minutes")
        settled = degrees[len(degrees) // 2:]
        say(f"  second half: {min(settled)}..{max(settled)} C"
            + ("  -- plateaued" if max(settled) == min(settled)
               else "  -- still moving, run longer"))
        if max(degrees) >= 85:
            say(f"  *** {max(degrees)} C reaches the tCSM knee (#341): the "
                f"burst cap this design derives was taken at a lower "
                f"temperature and is wrong here.")

    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(
        {"minutes": args.minutes, "loads": loads, "verified_passes": passes,
         "wrong": wrong, "samples": samples}, indent=2) + "\n")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(OUTPUT) + "\n")
    say(f"wrote {RESULT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
