#!/usr/bin/env python3
#
# Push HyperRAM clock until the data comes back wrong.
# SPDX-License-Identifier: BSD-3-Clause

"""
Raises the HyperRAM clock until reads stop verifying.

The detector is the gateware's own per-word comparison against the pattern it
wrote, so a failure is counted in words rather than inferred from a summary.
Partial failure is the interesting case and it is reported as a count: a
handful of bad words means marginal timing, while all of them means the bus has
stopped working entirely, and those want different responses.

Timing closure is reported alongside, because a design that fails to meet
timing at a given clock may still read correctly -- nextpnr's estimate is
conservative -- and conversely one that meets timing may still fail on the
wire, where the chip's own limits apply. Both facts are worth having.

    ./scripts/hyperram_ladder.py
    ./scripts/hyperram_ladder.py --clocks 60 120
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWARE = ROOT / "ecp5-test" / "hyperram" / "hyperram_speed.py"
BITSTREAM = ROOT / "ecp5-test" / "hyperram" / "build" / "top.bit"
LOG = ROOT / "tmp" / "hyperram_ladder.log"

# LunaECP5DomainGenerator drives sync from one of three PLL outputs and raises
# KeyError for anything else, so these are the only rates available without a
# custom PLL. 120 MHz is where the part's own 166 MHz rating starts to matter.
DEFAULT_CLOCKS = [60, 120]

TRANSFER_WORDS = 2048
BYTES_PER_WORD = 2

REG_ID, REG_STATUS, REG_WRITE_TIME, REG_READ_TIME = 1, 2, 3, 4
REG_CAPTURE_ADDR, REG_CAPTURE_DATA, REG_FIRST = 5, 6, 7


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def build(clock_mhz):
    text = GATEWARE.read_text()
    text = re.sub(r"CLOCK_MHZ = \d+", f"CLOCK_MHZ = {clock_mhz}", text)
    GATEWARE.write_text(text)

    script = (
        'import sys; sys.path.insert(0,"ecp5-test"); '
        'sys.path.insert(0,"repos/apollo")\n'
        'from hyperram.hyperram_speed import HyperRAMSpeedTest\n'
        'from cynthion.gateware.platform.cynthion_r1_4 import '
        'CynthionPlatformRev1D4\n'
        'CynthionPlatformRev1D4().build(HyperRAMSpeedTest(), do_program=False, '
        'build_dir="ecp5-test/hyperram/build")\n'
    )
    result = subprocess.run(
        ["bash", "-c",
         f'source "$HOME/opt/oss-cad-suite/environment" && '
         f'python3.15t -c {shell_quote(script)}'],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return False, tail[-1][:60] if tail else "build failed"

    timing = "unknown"
    report = ROOT / "ecp5-test" / "hyperram" / "build" / "top.tim"
    if report.exists():
        matches = re.findall(r"Max frequency for clock '\$glbnet\$clk': "
                             r"([\d.]+) MHz \((\w+) at ([\d.]+) MHz\)",
                             report.read_text())
        if matches:
            achieved, verdict, target = matches[0]
            timing = f"{verdict} {float(achieved):.0f}/{float(target):.0f}"

    if subprocess.run(["apollo", "configure", str(BITSTREAM)],
                      cwd=ROOT, capture_output=True).returncode != 0:
        return False, "configure failed"
    return True, timing


def read_back():
    script = (
        'import sys; sys.path.insert(0,"repos/apollo")\n'
        'from apollo_fpga import ApolloDebugger\n'
        'd = ApolloDebugger()\n'
        f'st = d.registers.register_read({REG_STATUS})\n'
        f'wt = d.registers.register_read({REG_WRITE_TIME})\n'
        f'rt = d.registers.register_read({REG_READ_TIME})\n'
        'out = []\n'
        'for a in range(8):\n'
        f'    d.registers.register_write({REG_CAPTURE_ADDR}, a)\n'
        f'    out.append(d.registers.register_read({REG_CAPTURE_DATA}) & 0xFFFF)\n'
        'print(st, wt, rt, " ".join(str(v) for v in out))\n'
    )
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    status, write_time, read_time = int(parts[0]), int(parts[1]), int(parts[2])
    words = [int(v) for v in parts[3:]]
    return status, write_time, read_time, words


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clocks", type=int, nargs="+", default=DEFAULT_CLOCKS)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    original = GATEWARE.read_text()
    total_bytes = TRANSFER_WORDS * BYTES_PER_WORD
    expected = [(((~i) & 0xFF) << 8) | (i & 0xFF) for i in range(8)]

    try:
        with LOG.open("w") as handle:
            emit(handle, f"HyperRAM clock ladder: {TRANSFER_WORDS} 16-bit "
                         f"words ({total_bytes} bytes) written then read back")
            emit(handle)
            emit(handle, f"  {'clk':>5} {'write':>9} {'read':>9} {'errors':>9} "
                         f"{'timing':>12}  verdict")

            best = None
            for clock in args.clocks:
                ok, detail = build(clock)
                if not ok:
                    emit(handle, f"  {clock:>4}M {'':>9} {'':>9} {'':>9} "
                                 f"{'':>12}  BUILD FAIL: {detail}")
                    continue

                result = read_back()
                if result is None:
                    emit(handle, f"  {clock:>4}M {'':>9} {'':>9} {'':>9} "
                                 f"{detail:>12}  NO RESPONSE")
                    continue

                status, write_time, read_time, words = result
                write_done = status & 1
                read_done = (status >> 1) & 1
                errors = (status >> 8) & 0xFFFF

                write_rate = (total_bytes / (write_time / (clock * 1e6)) / 1e6
                              if write_time else 0)
                read_rate = (total_bytes / (read_time / (clock * 1e6)) / 1e6
                             if read_time else 0)

                if not (write_done and read_done):
                    verdict = "FAIL: did not complete"
                elif errors == 0 and words == expected:
                    verdict = "PASS"
                    best = (clock, read_rate)
                elif errors >= TRANSFER_WORDS:
                    verdict = "FAIL: every word wrong"
                else:
                    verdict = f"FAIL: marginal"

                emit(handle, f"  {clock:>4}M {write_rate:>7.1f}MB {read_rate:>7.1f}MB "
                             f"{errors:>9} {detail:>12}  {verdict}")

            emit(handle)
            if best:
                emit(handle, f"fastest verified: {best[0]} MHz, "
                             f"{best[1]:.1f} MB/s read")
            else:
                emit(handle, "no clock verified clean")
            emit(handle, f"log: {LOG}")
    finally:
        GATEWARE.write_text(original)
        print("\n(gateware source restored)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
