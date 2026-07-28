#!/usr/bin/env python3
#
# Sweep quad-SPI sample offset and clock divisor to find what actually reads.
# SPDX-License-Identifier: BSD-3-Clause

"""
Finds the working (offset, divisor) combinations for quad flash reads.

The sample offset compensates for the round trip from the FPGA, through
USRMCLK and the pad, into the flash, and back through the input register. That
delay is roughly fixed in nanoseconds, so the number of clock periods it spans
grows as SCK rises -- which is why offset and divisor have to be swept together
rather than independently.

Every combination is checked against bytes read through `apollo flash-read`,
an entirely separate path, because a throughput figure alone cannot tell a
working quad read from a fast stream of nonsense.

    ./scripts/qspi_ladder.py
    ./scripts/qspi_ladder.py --divisors 1 2 --offsets 0 1 2 3 4
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWARE = ROOT / "ecp5-test" / "qspi" / "qspi_gateware.py"
BITSTREAM = ROOT / "ecp5-test" / "qspi" / "build" / "top.bit"
REFERENCE = ROOT / "tmp" / "flash_reference.bin"
LOG = ROOT / "tmp" / "qspi_ladder.log"

COMPARE_BYTES = 64
READ_BYTES = 4096
SYNC_MHZ = 60

REG_ID, REG_TIME, REG_ADDR, REG_DATA, REG_STATUS = 1, 2, 3, 4, 5


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def build(divisor, offset):
    text = GATEWARE.read_text()
    text = re.sub(r"QSPI_DIVISOR = \d+", f"QSPI_DIVISOR = {divisor}", text)
    text = re.sub(r"QSPI_OFFSET = \d+", f"QSPI_OFFSET = {offset}", text)
    GATEWARE.write_text(text)

    script = (
        'import sys; sys.path.insert(0,"ecp5-test"); '
        'sys.path.insert(0,"repos/apollo")\n'
        'from qspi.qspi_gateware import QSPITest\n'
        'from cynthion.gateware.platform.cynthion_r1_4 import '
        'CynthionPlatformRev1D4\n'
        'CynthionPlatformRev1D4().build(QSPITest(), do_program=False, '
        'build_dir="ecp5-test/qspi/build")\n'
    )
    result = subprocess.run(
        ["bash", "-c",
         f'source "$HOME/opt/oss-cad-suite/environment" && '
         f'python3.15t -c {shell_quote(script)}'],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return False, tail[-1] if tail else "build failed"

    if subprocess.run(["apollo", "configure", str(BITSTREAM)],
                      cwd=ROOT, capture_output=True).returncode != 0:
        return False, "configure failed"
    return True, ""


def read_back():
    script = (
        'import sys; sys.path.insert(0,"repos/apollo")\n'
        'from apollo_fpga import ApolloDebugger\n'
        'd = ApolloDebugger()\n'
        f'cyc = d.registers.register_read({REG_TIME})\n'
        f'st  = d.registers.register_read({REG_STATUS})\n'
        'out = []\n'
        f'for a in range({COMPARE_BYTES}):\n'
        f'    d.registers.register_write({REG_ADDR}, a)\n'
        f'    out.append(d.registers.register_read({REG_DATA}) & 0xFF)\n'
        'print(cyc, st, " ".join(str(b) for b in out))\n'
    )
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    return int(parts[0]), int(parts[1]), bytes(int(v) for v in parts[2:])


def reference_bytes():
    if not REFERENCE.exists():
        subprocess.run(["apollo", "flash-read", str(REFERENCE),
                        "--length", str(COMPARE_BYTES)],
                       cwd=ROOT, capture_output=True)
    return REFERENCE.read_bytes()[:COMPARE_BYTES]


def classify(data, expected):
    if data == expected:
        return "PASS", "matches"
    if not any(data):
        return "FAIL", "all zeros"
    if all(b == 0xFF for b in data):
        return "FAIL", "all ones"
    # A shifted sample point often shows as the right bytes in the wrong
    # nibble alignment, which is worth naming rather than lumping in with
    # generic corruption.
    for shift in (1, 2, 3, 4):
        if bytes(data[shift:]) == expected[:len(data) - shift]:
            return "FAIL", f"shifted by {shift} bytes"
    differing = sum(1 for a, b in zip(data, expected) if a != b)
    return "FAIL", f"{differing}/{len(expected)} differ"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--divisors", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--offsets", type=int, nargs="+",
                        default=[0, 1, 2, 3, 4, 5, 6, 7])
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    original = GATEWARE.read_text()
    expected = reference_bytes()

    try:
        with LOG.open("w") as handle:
            emit(handle, f"quad SPI ladder: {READ_BYTES} bytes per run, "
                         f"first {COMPARE_BYTES} verified against apollo")
            emit(handle, f"reference: "
                         f"{' '.join(f'{b:02x}' for b in expected[:8])} ...")
            emit(handle)
            emit(handle, f"  {'divisor':>8} {'SCK':>7} {'offset':>7} "
                         f"{'MB/s':>7}  {'shape':<20} verdict")

            found = []
            for divisor in args.divisors:
                sck = SYNC_MHZ / (divisor + 1)
                for offset in args.offsets:
                    ok, detail = build(divisor, offset)
                    if not ok:
                        emit(handle, f"  {divisor:>8} {sck:>6.1f}M {offset:>7} "
                                     f"{'':>7}  BUILD FAIL: {detail[:40]}")
                        continue

                    result = read_back()
                    if result is None:
                        emit(handle, f"  {divisor:>8} {sck:>6.1f}M {offset:>7} "
                                     f"{'':>7}  NO RESPONSE")
                        continue

                    cycles, status, data = result
                    rate = (READ_BYTES / (cycles / (SYNC_MHZ * 1e6)) / 1e6
                            if cycles else 0)
                    verdict, shape = classify(data, expected)
                    if verdict == "PASS":
                        found.append((divisor, offset, rate))

                    emit(handle, f"  {divisor:>8} {sck:>6.1f}M {offset:>7} "
                                 f"{rate:>7.2f}  {shape:<20} {verdict}")

            emit(handle)
            if found:
                best = max(found, key=lambda f: f[2])
                emit(handle, f"fastest working: divisor {best[0]}, "
                             f"offset {best[1]}, {best[2]:.2f} MB/s")
            else:
                emit(handle, "no working combination found")
            emit(handle, f"log: {LOG}")
    finally:
        GATEWARE.write_text(original)
        print("\n(gateware source restored)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
