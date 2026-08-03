#!/usr/bin/env python3
#
# Find where the configuration flash actually stops reading correctly.
# SPDX-License-Identifier: BSD-3-Clause

"""
Pushes the flash SCK up until the data comes back wrong.

The architectural ceiling of SPIStreamController on a 60 MHz domain is 30 MHz
(SCK = clk / period, period >= 2), but that is a property of the divider, not of
the flash. To find the part's real limit the sync domain itself is raised, so
SCK = sync / 2 can exceed 30 MHz.

The detector is the checksum, not the cycle count. A transfer that runs at any
speed will report *some* duration; only the XOR of the bytes read says whether
those bytes were real. The reference value is captured at a known-good rate and
every faster run is compared against it, so a corrupt read is caught even when
it completes promptly and reports done=1.

    ./scripts/flash_speed_ladder.py
    ./scripts/flash_speed_ladder.py --sync 60 80 100
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWARE = ROOT / "ecp5-test" / "sideband" / "sideband_gateware.py"
BITSTREAM = ROOT / "ecp5-test" / "sideband" / "build" / "top.bit"
LOG = ROOT / "tmp" / "flash_speed_ladder.log"

# Sync-domain frequencies to try, in MHz. SCK is half each of these at
# period=2.
#
# Only 60, 120 and 240 are available: LunaECP5DomainGenerator drives the sync
# domain from one of three PLL outputs and rejects anything else with a
# KeyError, so intermediate rates are not a matter of tuning. The resulting SCK
# steps -- 30, 60 and 120 MHz -- happen to bracket the W25Q32's 50 MHz rating
# for opcode 0x03 nicely: one comfortably under, two over.
DEFAULT_SYNC = [60, 120, 240]

READ_BYTES = 4096

REGISTER_FLASH_ID = 10
REGISTER_FLASH_TIME = 11
REGISTER_FLASH_SUM = 12

EXPECTED_ID = (0xEF, 0x40, 0x16)


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def set_sync(mhz):
    """Rewrite the sync-domain frequency in the bitstream source."""
    text = GATEWARE.read_text()
    text = re.sub(r'CLOCK_FREQUENCIES = \{[^}]*\}',
                  f'CLOCK_FREQUENCIES = {{"fast": {mhz}, "sync": {mhz}, '
                  f'"usb": 60}}',
                  text)
    GATEWARE.write_text(text)


def build_and_configure():
    """Returns (ok, detail). Timing failure is a real result, not an error."""
    script = (
        'import sys; sys.path.insert(0,"ecp5-test"); '
        'sys.path.insert(0,"repos/apollo")\n'
        'from sideband.sideband_gateware import SidebandTest\n'
        'from cynthion_platform.cynthion_r1_4 import '
        'CynthionPlatformRev1D4\n'
        'CynthionPlatformRev1D4().build(SidebandTest(), do_program=False, '
        'build_dir="ecp5-test/sideband/build")\n'
    )
    result = subprocess.run(
        ["bash", "-c",
         f'source "$HOME/opt/oss-cad-suite/environment" && python3.15t -c '
         f'{shell_quote(script)}'],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return False, tail[-1] if tail else "build failed"

    # Whether the design met timing at this clock: a build that fails timing
    # may still program and still read correctly, so it is reported rather
    # than treated as fatal.
    timing = "unknown"
    report = ROOT / "ecp5-test" / "sideband" / "build" / "top.tim"
    if report.exists():
        text = report.read_text()
        fmax = re.findall(r"Max frequency for clock '\$glbnet\$clk': "
                          r"([\d.]+) MHz \((\w+) at ([\d.]+) MHz\)", text)
        if fmax:
            achieved, verdict, target = fmax[0]
            timing = f"{verdict} {achieved}/{target} MHz"

    configure = subprocess.run(["apollo", "configure", str(BITSTREAM)],
                               cwd=ROOT, capture_output=True, text=True)
    if configure.returncode != 0:
        return False, "configure failed"
    return True, timing


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def read_result():
    """Returns (id_tuple, cycles, checksum, done)."""
    script = (
        'import sys; sys.path.insert(0,"repos/apollo")\n'
        'from apollo_fpga import ApolloDebugger\n'
        'd = ApolloDebugger()\n'
        f'raw = d.registers.register_read({REGISTER_FLASH_ID})\n'
        f'cyc = d.registers.register_read({REGISTER_FLASH_TIME})\n'
        f'sm  = d.registers.register_read({REGISTER_FLASH_SUM})\n'
        'print(raw, cyc, sm)\n'
    )
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    raw, cycles, summary = (int(v) for v in result.stdout.split())
    ids = (raw & 0xFF, (raw >> 8) & 0xFF, (raw >> 16) & 0xFF)
    return ids, cycles, summary & 0xFF, (summary >> 8) & 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sync", type=int, nargs="+", default=DEFAULT_SYNC,
                        help="sync-domain frequencies in MHz")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    original = GATEWARE.read_text()
    reference_checksum = None

    try:
        with LOG.open("w") as handle:
            emit(handle, f"flash read ladder: {READ_BYTES} bytes, opcode 0x03, "
                         f"SCK = sync/2")
            emit(handle)
            emit(handle, f"  {'sync':>6} {'SCK':>8} {'MB/s':>7} {'id':>9} "
                         f"{'sum':>5} {'timing':>18}  verdict")

            for mhz in args.sync:
                set_sync(mhz)
                ok, detail = build_and_configure()
                if not ok:
                    emit(handle, f"  {mhz:>4}M {'':>8} {'':>7} {'':>9} {'':>5} "
                                 f"{'':>18}  BUILD FAIL: {detail}")
                    continue

                result = read_result()
                if result is None:
                    emit(handle, f"  {mhz:>4}M {'':>8} {'':>7} {'':>9} {'':>5} "
                                 f"{detail:>18}  NO RESPONSE")
                    continue

                ids, cycles, checksum, done = result
                sck = mhz / 2
                rate = READ_BYTES / (cycles / (mhz * 1e6)) / 1e6 if cycles else 0

                if reference_checksum is None and ids == EXPECTED_ID and done:
                    reference_checksum = checksum

                id_text = "".join(f"{b:02x}" for b in ids)
                if ids != EXPECTED_ID:
                    verdict = "FAIL: wrong JEDEC ID"
                elif not done:
                    verdict = "FAIL: read never completed"
                elif checksum != reference_checksum:
                    verdict = (f"FAIL: checksum {checksum:#04x} != "
                               f"{reference_checksum:#04x}")
                else:
                    verdict = "PASS"

                emit(handle, f"  {mhz:>4}M {sck:>6.0f}M {rate:>7.2f} "
                             f"{id_text:>9} {checksum:#04x} {detail:>18}  "
                             f"{verdict}")

            emit(handle)
            emit(handle, f"log: {LOG}")
    finally:
        GATEWARE.write_text(original)
        print("\n(gateware source restored)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
