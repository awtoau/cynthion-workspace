#!/usr/bin/env python3
#
# Apollo watchdog poll (#554): runs the firmware's own decision logic on the host.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds and runs `repos/apollo/firmware/test/test_fpga_adv_wd.c`, which compiles
the real `fpga_adv_wd.h` (the file the SAMD11 firmware includes) against a model
of the poll loop and the SoC's 1 ms tick byte, and reports each of its checks.

Also checks the SoC half is wired: the 1 ms tick writes the sideband `tx` byte
that `PING` returns (the gateware half is `sideband_link_sim.py`).

Periods and bounds: docs/chips/cynone-sideband.md section 14.
Output goes to the console and to tmp/logs/dev.log.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: F401  (scripts/ subdirectories on sys.path)
from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

TEST_DIR = ROOT / "repos" / "apollo" / "firmware" / "test"
TIMER_RS = ROOT / "firmware" / "cynthion-soc" / "src" / "timer.rs"
# Host gcc build of one small file: 0.1 s measured; 20 s catches a hung model
# (the model loops are bounded) without waiting out a wedge.
BUILD_LIMIT_S = 20


def main():
    emit("Apollo watchdog poll")
    checks = Checks(emit)

    if not (TEST_DIR / "Makefile").exists():
        checks.check("apollo submodule is populated", False,
                     "run ./dev.py worktree-setup")
        return checks.summary()

    try:
        proc = subprocess.run(
            ["make", "-C", str(TEST_DIR), "run-test_fpga_adv_wd"],
            capture_output=True, text=True, timeout=BUILD_LIMIT_S)
        out, rc = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        out, rc = f"timed out after {BUILD_LIMIT_S} s", 1

    results = re.findall(r"^\s+(ok|FAIL)\s+- (.*)$", out, re.M)
    for status, name in results:
        checks.check(name, status == "ok")
    checks.check("the C test ran and reported checks", bool(results) and rc == 0,
                 out[-400:] if rc else "")

    tick = TIMER_RS.read_text()
    checks.check("the 1 ms tick writes the sideband tx byte",
                 "set_message(ticks as u8)" in tick)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
