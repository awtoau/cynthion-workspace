#!/usr/bin/env python3
# Does the Apollo hang follow a bitstream's length or its content? Load variants, recover, log.
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/apollo/hang_bisect.py

Builds variants of tmp/isolate-B/top.bit (hangs Apollo) and tmp/isolate-X/top.bit (does not) under
tmp/hang-bisect/<name>/ and loads each through the board arbiter:

    pad-x    X + zero bytes up to B's length   (B's length, X's content)
    cut-b    B truncated to X's length         (X's length, B's content)

The FPGA rejects these as invalid streams; what is tested is Apollo's reaction to the transfer.
Verdict per variant: the job is REFUSED and Apollo answers no GET_ID = hung; the watchdog reset
lands Apollo in the bootloader, which `usb_recover.py` leaves. Log: ./tmp/logs/hang_bisect.log.
"""

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "hang_bisect.log"
B = ROOT / "tmp" / "isolate-B"
X = ROOT / "tmp" / "isolate-X"
OUT = ROOT / "tmp" / "hang-bisect"
VARIANT = "ck180-sync60-fdrive8-fslewSLOW"
# One arbiter job measured at ~2 s (healthy) and ~16 s until its first timeout (hung); the arbiter
# retries three times. 120 s is the earlier foreground limit that held; on expiry the run is a failure.
JOB_LIMIT_S = 120
# usb_recover: probe plus leave-bootloader on a healthy path is seconds; the slow rungs only run if
# Apollo stays dead (~2.5 min measured), so the limit covers the whole ladder.
RECOVER_LIMIT_S = 240


def make_variants():
    b, x = (B / "top.bit").read_bytes(), (X / "top.bit").read_bytes()
    variants = {"pad-x": (X, x + bytes(len(b) - len(x))), "cut-b": (B, b[:len(x)])}
    for name, (source, data) in variants.items():
        directory = OUT / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "top.bit").write_bytes(data)
        meta = json.loads((source / "usercode.json").read_text())
        meta["gateware_digest"] = f"hang-bisect-{name}"
        (directory / "usercode.json").write_text(json.dumps(meta, indent=2))
    return {name: OUT / name / "top.bit" for name in variants}


def run(command, limit):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=limit, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {limit} s"
    return done.returncode, (done.stdout + done.stderr).strip()


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    for name, bitstream in make_variants().items():
        rc, out = run(["python3", "scripts/board/board.py", "run", "--variant", VARIANT,
                       "--bitstream", str(bitstream), "selftest"], JOB_LIMIT_S)
        verdict = "loaded" if rc == 0 else "HUNG or refused"
        logging.info("%s (%d bytes): %s -- %s", name, bitstream.stat().st_size, verdict,
                     out.splitlines()[0] if out else "")
        run(["python3", "scripts/board/board.py", "stop"], 30)
        rc, out = run(["python3", "scripts/board/usb_recover.py"], RECOVER_LIMIT_S)
        logging.info("%s: recovery rc=%d %s", name, rc, out.splitlines()[-1] if out else "")
        if rc != 0:
            logging.error("Apollo did not come back; stopping before the next variant")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
