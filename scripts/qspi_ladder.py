#!/usr/bin/env python3
#
# Sweep quad-SPI sample offset and clock divisor to find what actually reads.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sweeps quad flash SCK by writing the divisor at runtime, and verifies the data.

The divisor is a register in the bitstream, not a build-time constant, so the
whole ladder runs against ONE configured FPGA: write divisor, trigger a read,
compare bytes, repeat. A sweep that used to mean a rebuild and reconfigure per
step -- minutes each -- now takes seconds, and it covers rates a rebuild ladder
could not reach at all.

SCK = sync / (divisor + 1). At the bitstream's 120 MHz sync that gives 120, 60,
40, 30, 24, 20, 17 and 15 MHz, which spans the 30-to-60 MHz region the earlier
rebuild-per-frequency ladder jumped straight over.

The sample offset is still build-time -- it selects between pipeline registers
-- so `--offsets` rebuilds, while `--divisors` alone does not.

Every point is checked against bytes read through `apollo flash-read`, an
entirely separate path, because a throughput figure alone cannot tell a working
quad read from a fast stream of nonsense.

    ./scripts/qspi_ladder.py                       # runtime sweep, no rebuild
    ./scripts/qspi_ladder.py --divisors 0 1 2 3
    ./scripts/qspi_ladder.py --offsets 0 1 2       # rebuilds per offset
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
REG_DIVISOR, REG_START = 6, 7


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def build(offset):
    """Rebuild and reconfigure for a given sample offset.

    Only the offset needs this: it selects between pipeline registers and is
    fixed in the bitstream. The divisor is a runtime register.
    """
    text = GATEWARE.read_text()
    text = re.sub(r"QSPI_OFFSET = \d+", f"QSPI_OFFSET = {offset}", text)
    GATEWARE.write_text(text)

    script = (
        'import sys; sys.path.insert(0,"ecp5-test"); '
        'sys.path.insert(0,"repos/apollo")\n'
        'from qspi.qspi_gateware import QSPITest\n'
        'from cynthion_platform.cynthion_r1_4 import '
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


def measure(divisor):
    """Set the divisor, re-run the read, and return the result.

    Writing REG_START with a changed value re-triggers; the gateware
    edge-detects it, so the counter here just has to differ each call.
    """
    script = (
        'import sys; sys.path.insert(0,"repos/apollo")\n'
        'from apollo_fpga import ApolloDebugger\n'
        'd = ApolloDebugger()\n'
        f'd.registers.register_write({REG_DIVISOR}, {divisor})\n'
        # Re-trigger, then poll `busy` rather than sleeping: the read takes
        # microseconds and the JTAG round trip alone is longer than that.
        f'd.registers.register_write({REG_START}, {divisor} + 1)\n'
        'for _ in range(200):\n'
        f'    st = d.registers.register_read({REG_STATUS})\n'
        '    if (st & 1) and not (st >> 1) & 1:\n'
        '        break\n'
        f'cyc = d.registers.register_read({REG_TIME})\n'
        'out = []\n'
        f'for a in range({COMPARE_BYTES}):\n'
        f'    d.registers.register_write({REG_ADDR}, a)\n'
        f'    out.append(d.registers.register_read({REG_DATA}) & 0xFF)\n'
        'print(st, cyc, " ".join(str(b) for b in out))\n'
    )
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    status, cycles = int(parts[0]), int(parts[1])
    return status, cycles, bytes(int(v) for v in parts[2:])


def sync_mhz():
    """The sync frequency the bitstream was actually built at.

    Read from the FPGA rather than assumed, because every rate reported here
    is derived from it and a stale constant would scale the whole table.
    """
    script = (
        'import sys; sys.path.insert(0,"repos/apollo")\n'
        'from apollo_fpga import ApolloDebugger\n'
        'd = ApolloDebugger()\n'
        f'print(d.registers.register_read({REG_STATUS}))\n'
    )
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return (int(result.stdout.strip()) >> 8) & 0xFFFF


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
    parser.add_argument("--divisors", type=int, nargs="+",
                        default=[0, 1, 2, 3, 4, 5, 7],
                        help="SCK divisors to sweep at runtime")
    parser.add_argument("--offsets", type=int, nargs="+", default=None,
                        help="sample offsets; each one REBUILDS the bitstream")
    parser.add_argument("--no-build", action="store_true",
                        help="use whatever is already configured on the FPGA")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    original = GATEWARE.read_text()
    expected = reference_bytes()
    offsets = args.offsets if args.offsets is not None else [None]

    try:
        with LOG.open("w") as handle:
            emit(handle, f"quad SPI ladder: {READ_BYTES} bytes per read, "
                         f"first {COMPARE_BYTES} verified against apollo")
            emit(handle, f"reference: "
                         f"{' '.join(f'{b:02x}' for b in expected[:8])} ...")
            emit(handle)

            found = []
            for offset in offsets:
                if offset is not None and not args.no_build:
                    ok, detail = build(offset)
                    if not ok:
                        emit(handle, f"  offset {offset}: BUILD FAIL: {detail}")
                        continue

                sync = sync_mhz()
                if sync is None:
                    emit(handle, "  cannot read the FPGA -- is it configured?")
                    continue

                label = "" if offset is None else f", offset {offset}"
                emit(handle, f"  sync {sync} MHz{label}")
                emit(handle, f"  {'divisor':>8} {'SCK':>8} {'MB/s':>8}  "
                             f"{'shape':<22} verdict")

                for divisor in args.divisors:
                    result = measure(divisor)
                    if result is None:
                        emit(handle, f"  {divisor:>8} {'':>8} {'':>8}  "
                                     f"NO RESPONSE")
                        continue

                    status, cycles, data = result
                    sck = sync / (divisor + 1)
                    rate = (READ_BYTES / (cycles / (sync * 1e6)) / 1e6
                            if cycles else 0)

                    if not status & 1:
                        verdict, shape = "FAIL", "never completed"
                    else:
                        verdict, shape = classify(data, expected)
                    if verdict == "PASS":
                        found.append((divisor, offset, sck, rate))

                    emit(handle, f"  {divisor:>8} {sck:>7.1f}M {rate:>8.2f}  "
                                 f"{shape:<22} {verdict}")
                emit(handle)

            if found:
                best = max(found, key=lambda f: f[3])
                where = "" if best[1] is None else f", offset {best[1]}"
                emit(handle, f"fastest verified: divisor {best[0]}{where} -- "
                             f"SCK {best[2]:.1f} MHz, {best[3]:.2f} MB/s")
            else:
                emit(handle, "no combination verified clean")
            emit(handle, f"log: {LOG}")
    finally:
        GATEWARE.write_text(original)

    return 0


if __name__ == "__main__":
    sys.exit(main())
