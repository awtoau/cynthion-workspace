#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Standalone ECP5 cell counts for the ULPI bridge options (#581): yosys synth_ecp5, no P&R.

- Each bridge alone, all ports as top-level IO, clocks/resets as inputs.
- Log: tmp/logs/ulpi_bridge_area.log; results: tmp/ulpi_bridge/area.json.
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path


def repo_root():
    env = os.environ.get("CYNTHION_WORKSPACE_REPO")
    if env:
        return Path(env)
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, cwd=Path(__file__).parent, check=True).stdout.strip()
        return Path(out)
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent


ROOT = repo_root()
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth import ClockDomain, Elaboratable, Module  # noqa: E402
from amaranth.back import rtlil  # noqa: E402

from ulpi_bridge import AsyncBridge, PhaseCheck, RatioBridge  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "ulpi_bridge_area.log"
WORK = ROOT / "tmp" / "ulpi_bridge"
CELLS = ("LUT4", "CCU2C", "TRELLIS_FF", "DP16KD", "PDPW16KD", "TRELLIS_DPR16X4", "PFUMX", "L6MUX21")
# yosys on this design: seconds; stall guard per the host-process rule (no output for 60 s)
STALL_S = 60


class Top(Elaboratable):
    def __init__(self, dut):
        self.dut = dut
        self.usb, self.fast = ClockDomain("usb"), ClockDomain("fast")

    def elaborate(self, platform):
        m = Module()
        m.domains += [self.usb, self.fast]
        m.submodules.dut = self.dut
        return m


def ports(top):
    d = top.dut
    sigs = [top.usb.clk, top.usb.rst, top.fast.clk, top.fast.rst]
    if isinstance(d, PhaseCheck):
        return sigs + [d.safe, d.ok, d.fault, d.clear, d.misses]
    sigs += [d.rx_data, d.rx_last, d.rx_valid, d.rx_ready, d.rx_overflow, d.tx_data, d.tx_last,
             d.tx_valid, d.tx_ready, d.rxw_word, d.rxw_valid, d.rxw_ready, d.txw_word,
             d.txw_valid, d.txw_ready]
    if isinstance(d, RatioBridge):
        sigs += [d.up, d.check.ok, d.check.fault, d.check.clear]
    return sigs


def synth(name, dut, log):
    top = Top(dut)
    il = WORK / f"{name}.il"
    il.write_text(rtlil.convert(top, ports=ports(top)))
    cmd = ["yosys", "-q", "-p", f"read_rtlil {il}; synth_ecp5 -top top; tee -o {WORK / name}.stat stat"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = proc.communicate(timeout=STALL_S)[0]
    if proc.returncode:
        raise RuntimeError(f"yosys failed for {name}:\n{out}")
    text = (WORK / f"{name}.stat").read_text()
    counts = {c: int(m.group(1)) for c in CELLS
              if (m := re.search(rf"^\s+(\d+)\s+(?:[\d.]+\s+)?{c}\s*$", text, re.M))
              or (m := re.search(rf"^\s+{c}\s+(\d+)\s*$", text, re.M))}
    log.info("%-12s %s", name, counts)
    return counts


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler()])
    log = logging.getLogger()
    res = {}
    for name, mk in (("a_async", lambda: AsyncBridge()), ("b_ratio", lambda: RatioBridge()),
                     ("b_check", lambda: PhaseCheck())):
        res[name] = synth(name, mk(), log)
    (WORK / "area.json").write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
