#!/usr/bin/env python3
#
# Flash read modes and speeds, verified against known-good data.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sweeps SCK rate against read opcode and checks the bytes, not a checksum.

Every combination is verified by comparing the first bytes the FPGA read
against the same region read through Apollo's own path, so a pass means the
data was genuinely correct rather than merely that a transfer completed. That
distinction cost real debugging time: an even-length XOR fold reports 0x00 both
for a dead MISO and for a healthy read of constant data, so the fold alone
could not tell a working link from a broken one.

The reference is captured once via `apollo flash-read`, which uses a completely
independent path (JTAG bit-banged by the SAMD11) -- so agreement means two
unrelated mechanisms concur, not that one mechanism is self-consistent.

    ./scripts/flash_modes.py
    ./scripts/flash_modes.py --sync 60 120
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWARE = ROOT / "ecp5-test" / "sideband" / "sideband_gateware.py"
BITSTREAM = ROOT / "ecp5-test" / "sideband" / "build" / "top.bit"
REFERENCE = ROOT / "tmp" / "flash_reference.bin"
LOG = ROOT / "tmp" / "flash_modes.log"

# Only 60, 120 and 240 MHz are available: LunaECP5DomainGenerator drives the
# sync domain from one of three PLL outputs and rejects anything else. SCK is
# half of these, so 30/60/120 MHz -- which brackets the part's 50 MHz rating
# for opcode 0x03.
DEFAULT_SYNC = [60, 120]
COMPARE_BYTES = 64
READ_BYTES = 4096

REG_FLASH_ID, REG_TIME, REG_SUM = 10, 11, 12
REG_CAPTURE_ADDR, REG_CAPTURE_DATA = 13, 14


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def configure(sync_mhz, fast_read):
    text = GATEWARE.read_text()
    text = re.sub(r'CLOCK_FREQUENCIES = \{[^}]*\}',
                  f'CLOCK_FREQUENCIES = {{"fast": {sync_mhz}, '
                  f'"sync": {sync_mhz}, "usb": 60}}', text)
    text = re.sub(r'FLASH_USE_FAST_READ = \d',
                  f'FLASH_USE_FAST_READ = {1 if fast_read else 0}', text)
    GATEWARE.write_text(text)

    script = (
        'import sys; sys.path.insert(0,"ecp5-test"); '
        'sys.path.insert(0,"repos/apollo")\n'
        'from sideband.sideband_gateware import SidebandTest\n'
        'from cynthion.gateware.platform.cynthion_r1_4 import '
        'CynthionPlatformRev1D4\n'
        'CynthionPlatformRev1D4().build(SidebandTest(), do_program=False, '
        'build_dir="ecp5-test/sideband/build")\n'
    )
    build = subprocess.run(
        ["bash", "-c",
         f'source "$HOME/opt/oss-cad-suite/environment" && '
         f'python3.15t -c {shell_quote(script)}'],
        cwd=ROOT, capture_output=True, text=True)
    if build.returncode != 0:
        tail = (build.stderr or build.stdout).strip().splitlines()
        return False, tail[-1] if tail else "build failed"

    if subprocess.run(["apollo", "configure", str(BITSTREAM)],
                      cwd=ROOT, capture_output=True).returncode != 0:
        return False, "configure failed"
    return True, ""


def read_back():
    """Returns (cycles, crc, done, captured_bytes)."""
    script = (
        'import sys; sys.path.insert(0,"repos/apollo")\n'
        'from apollo_fpga import ApolloDebugger\n'
        'd = ApolloDebugger()\n'
        f'cyc = d.registers.register_read({REG_TIME})\n'
        f'sm  = d.registers.register_read({REG_SUM})\n'
        'out = []\n'
        f'for a in range({COMPARE_BYTES}):\n'
        f'    d.registers.register_write({REG_CAPTURE_ADDR}, a)\n'
        f'    out.append(d.registers.register_read({REG_CAPTURE_DATA}) & 0xFF)\n'
        'print(cyc, sm, " ".join(str(b) for b in out))\n'
    )
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    cycles, summary = int(parts[0]), int(parts[1])
    data = bytes(int(v) for v in parts[2:])
    return cycles, summary & 0xFF, (summary >> 8) & 1, data


def reference_bytes():
    """The same region through Apollo's own independent path."""
    if not REFERENCE.exists():
        subprocess.run(["apollo", "flash-read", str(REFERENCE),
                        "--length", str(COMPARE_BYTES)],
                       cwd=ROOT, capture_output=True)
    return REFERENCE.read_bytes()[:COMPARE_BYTES]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sync", type=int, nargs="+", default=DEFAULT_SYNC)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    original = GATEWARE.read_text()
    expected = reference_bytes()

    try:
        with LOG.open("w") as handle:
            emit(handle, f"flash read modes, {READ_BYTES} bytes per run, "
                         f"first {COMPARE_BYTES} verified against apollo")
            emit(handle)
            emit(handle, f"  reference: "
                         f"{' '.join(f'{b:02x}' for b in expected[:8])} ...")
            emit(handle)
            emit(handle, f"  {'SCK':>7} {'opcode':>8} {'MB/s':>7} {'crc':>5}  "
                         f"data     verdict")

            for sync in args.sync:
                for fast in (False, True):
                    opcode = "0x0B" if fast else "0x03"
                    ok, detail = configure(sync, fast)
                    if not ok:
                        emit(handle, f"  {sync/2:>5.0f}M {opcode:>8} "
                                     f"{'':>7} {'':>5}  BUILD FAIL: {detail}")
                        continue

                    result = read_back()
                    if result is None:
                        emit(handle, f"  {sync/2:>5.0f}M {opcode:>8} "
                                     f"{'':>7} {'':>5}  NO RESPONSE")
                        continue

                    cycles, crc, done, data = result
                    rate = (READ_BYTES / (cycles / (sync * 1e6)) / 1e6
                            if cycles else 0)

                    if not done:
                        verdict, shape = "FAIL", "never completed"
                    elif data == expected:
                        verdict, shape = "PASS", "matches"
                    elif not any(data):
                        verdict, shape = "FAIL", "all zeros (MISO dead)"
                    elif all(b == 0xFF for b in data):
                        verdict, shape = "FAIL", "all ones (MISO stuck high)"
                    else:
                        differing = sum(1 for a, b in zip(data, expected)
                                        if a != b)
                        verdict = "FAIL"
                        shape = f"{differing}/{len(expected)} bytes differ"

                    emit(handle, f"  {sync/2:>5.0f}M {opcode:>8} {rate:>7.2f} "
                                 f"{crc:#04x}  {shape:<22} {verdict}")

            emit(handle)
            emit(handle, f"log: {LOG}")
    finally:
        GATEWARE.write_text(original)
        print("\n(gateware source restored)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
