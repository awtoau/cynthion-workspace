#!/usr/bin/env python3
# AOA repeatability: role off/host, reset, aoa, app echo mode, echo at several sizes, N times.
# SPDX-License-Identifier: BSD-3-Clause
"""
    ./scripts/soc/aoa_repeat.py [--cycles 10] [--sizes 1 64 512 4096] [--phone IP]

Drives the board shell (`scripts/soc_shell.py`) and the bench app control port
(`dev.py ctl` in the awto-dji bench worktree). One line per cycle, a pass/fail count at
the end. Log: tmp/logs/aoa_repeat.log. Needs `vbus control` already done and the app installed.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "tmp" / "logs" / "aoa_repeat.log"
CTL_REPO = Path("/mnt/2tb/git/awto-dji-wt-ctl")

# One shell command is ~4 s on this console (prompt wait); a cycle is ~8 commands.
SHELL_LIMIT_S = 60
CTL_LIMIT_S = 20
# Accessory open follows re-enumeration by ~1-2 s on the Pixel; 6 x 0.5 s = 3 s covers it.
OPEN_TRIES, OPEN_GAP_S = 6, 0.5


def say(line, log):
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def shell(commands):
    run = subprocess.run([sys.executable, str(ROOT / "scripts/soc_shell.py"), *commands],
                         cwd=ROOT, capture_output=True, text=True, timeout=SHELL_LIMIT_S)
    return run.stdout


def ctl(phone, *args):
    run = subprocess.run(["./dev.py", "ctl", "--target", phone, *args], cwd=CTL_REPO,
                         capture_output=True, text=True, timeout=CTL_LIMIT_S)
    return run.stdout


def echo_ok(text, size):
    # "echo out N in N bytes ..." and no "stopped:" / "mismatch" line for this size
    block = text.split(f"usb target_a echo 1 {size} 1", 1)[-1]
    match = re.search(rf"echo out {size} in {size} bytes", block)
    return bool(match) and "stopped:" not in block.split("aux>")[1] and "mismatch" not in block


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 64, 512, 4096])
    parser.add_argument("--phone", default="192.168.25.125")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    passed = 0
    with LOG.open("w") as log:
        for cycle in range(1, args.cycles + 1):
            started = time.monotonic()
            out = shell(["usb target_a role off", "usb target_a role host", "usb target_a reset",
                         "usb target_a aoa"])
            log.write(out)
            enumerated = "accessory 18d1:2d0" in out
            mode = ""
            results = {}
            if enumerated:
                # The app opens the accessory a moment after the phone re-enumerates; echo set
                # before that gets no reads. Re-set the mode until it reports the accessory open.
                for _ in range(OPEN_TRIES):
                    mode = ctl(args.phone, "mode", "echo", f"test={cycle}")
                    log.write(mode)
                    if '"accessory_open": true' in mode:
                        break
                    time.sleep(OPEN_GAP_S)
                text = shell([f"usb target_a echo 1 {size} 1" for size in args.sizes])
                log.write(text)
                results = {size: echo_ok(text, size) for size in args.sizes}
            ok = enumerated and '"ok": true' in mode and all(results.values())
            passed += ok
            say(f"cycle {cycle}: {'PASS' if ok else 'FAIL'}  aoa {'yes' if enumerated else 'NO'}  "
                f"echo {results or '-'}  {time.monotonic() - started:.0f} s", log)
        say(f"{passed}/{args.cycles} cycles passed", log)
    return 0 if passed == args.cycles else 1


if __name__ == "__main__":
    sys.exit(main())
