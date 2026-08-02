#!/usr/bin/env python3
#
# FPGA_ADV sideband speed ceiling measurement.
# See awtoau/cynthion-workspace#85.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Rebuilds both ends of the sideband link at successive baud rates and measures
round-trip integrity at each, to find where the link actually breaks.

Uses the POWER command as the payload: 18 bytes with a CRC-8 covering all of
them, so corruption is counted directly rather than inferred. That also
exercises the turnaround, which a one-way stream would not -- and turnaround is
the part expected to fail first, since Apollo bit-bangs its transmit and that
jitter does not shrink as the bit period does.

Both ends must agree on the rate, so each step rebuilds the firmware and the
bitstream and reflashes. That is slow (a few minutes per rate) but it is the
only honest way to test: a mismatched pair fails for the wrong reason.

    ./scripts/sideband_speed_ladder.py                 # full ladder
    ./scripts/sideband_speed_ladder.py --rates 230400  # one rate
    ./scripts/sideband_speed_ladder.py --samples 200
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
FPGA_SRC = ROOT / "ecp5-test" / "sideband" / "sideband_gateware.py"
FW_SRC = FIRMWARE / "src" / "boards" / "cynthion_d11" / "fpga_adv.c"
BITSTREAM = ROOT / "ecp5-test" / "sideband" / "build" / "top.bit"
LOG = ROOT / "tmp" / "sideband_speed_ladder.log"

DEFAULT_RATES = [115200, 230400, 460800, 921600, 1000000]

# 60 MHz sync domain on the FPGA, 48 MHz CPU on the SAMD11.
FPGA_CLK = 60e6
APOLLO_CLK = 48e6


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def set_baud(baud):
    """Rewrite the baud constant on both sides."""
    fw = FW_SRC.read_text()
    fw = re.sub(r"#define ADV_UART_BAUD \d+UL",
                f"#define ADV_UART_BAUD {baud}UL", fw)
    FW_SRC.write_text(fw)

    gw = FPGA_SRC.read_text()
    gw = re.sub(r"baud=\d+\)", f"baud={baud})", gw)
    FPGA_SRC.write_text(gw)


def divisor_error(baud):
    """How far each end's integer divisor lands from the requested rate.

    Both ends divide a fixed clock, so the achievable rate is quantised. A UART
    tolerates roughly +/-2% total across both ends before framing fails, and at
    high baud the quantisation alone can exceed that -- which would be a
    resolution limit rather than a signal-integrity one.
    """
    fpga_div = round(FPGA_CLK / baud)
    apollo_div = round(APOLLO_CLK / baud)
    fpga_actual = FPGA_CLK / fpga_div
    apollo_actual = APOLLO_CLK / apollo_div
    return (100 * (fpga_actual - baud) / baud,
            100 * (apollo_actual - baud) / baud)


def build_and_flash(handle, baud):
    env = dict(os.environ)
    env["PATH"] = (str(Path.home() / "opt" / "cpython-315t" / "bin")
                   + os.pathsep + env["PATH"])

    subprocess.run(["make", "APOLLO_BOARD=cynthion"], cwd=FIRMWARE,
                   env=env, capture_output=True)
    result = subprocess.run(["make", "APOLLO_BOARD=cynthion"], cwd=FIRMWARE,
                            env=env, capture_output=True, text=True)
    if "Error" in result.stderr:
        emit(handle, f"  firmware build FAILED: {result.stderr[-200:]}")
        return False

    gw = subprocess.run(
        ["bash", "-c",
         'source "$HOME/opt/oss-cad-suite/environment" && python3.15t - <<PY\n'
         'import sys; sys.path.insert(0,"ecp5-test"); sys.path.insert(0,"repos/apollo")\n'
         'from sideband.sideband_gateware import SidebandTest\n'
         'from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4\n'
         'CynthionPlatformRev1D4().build(SidebandTest(), do_program=False,\n'
         '                               build_dir="ecp5-test/sideband/build")\n'
         'PY'],
        cwd=ROOT, env=env, capture_output=True, text=True)
    if gw.returncode != 0:
        emit(handle, f"  gateware build FAILED: {gw.stderr[-200:]}")
        return False

    # Apollo firmware goes over DFU; the bitstream over JTAG.
    subprocess.run([sys.executable, "-c",
                    "from apollo_fpga import ApolloDebugger; "
                    "ApolloDebugger().boot_to_dfu()"],
                   env=env, capture_output=True)
    # Poll for the bootloader rather than sleeping.
    for _ in range(200):
        out = subprocess.run(["lsusb", "-d", "1d50:615c"],
                             capture_output=True, text=True).stdout
        if "Bootloader" in out:
            break
    subprocess.run([sys.executable, "-c",
                    "from fwup.dfu import DFUTarget;"
                    "t=DFUTarget(idVendor=0x1d50,idProduct=0x615c);"
                    f"t.program(open('{FIRMWARE}/_build/cynthion_d11/firmware.bin','rb').read());"
                    "t.run_user_program()"],
                   env=env, capture_output=True)
    for _ in range(200):
        out = subprocess.run(["lsusb", "-d", "1d50:615c"],
                             capture_output=True, text=True).stdout
        if "Debugger" in out:
            break
    subprocess.run(["apollo", "configure", str(BITSTREAM)],
                   env=env, capture_output=True)
    return True


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def measure(samples):
    """Returns (good, short, bad_crc)."""
    import usb.core, usb.backend.libusb1, usb.util
    backend = usb.backend.libusb1.get_backend()
    dev = usb.core.find(idVendor=0x1d50, idProduct=0x615c, backend=backend)
    if dev is None:
        return None

    IN = usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE
    OUT = usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE

    dev.ctrl_transfer(OUT, 0xc3, 1, 0, None)   # UART mode

    good = short = bad = 0
    for _ in range(samples):
        try:
            reply = bytes(dev.ctrl_transfer(IN, 0xc3, 0xFFFE, (18 << 8) | 0x2B, 18))
        except Exception:
            short += 1
            continue
        if len(reply) != 18:
            short += 1
        elif crc8(reply[:-1]) != reply[-1]:
            bad += 1
        else:
            good += 1
    return good, short, bad


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rates", type=int, nargs="+", default=DEFAULT_RATES)
    parser.add_argument("--samples", type=int, default=200)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    original = FW_SRC.read_text(), FPGA_SRC.read_text()

    try:
        with LOG.open("w") as handle:
            emit(handle, f"sideband speed ladder, {args.samples} POWER "
                         f"transactions per rate (18 bytes each)")
            emit(handle)
            emit(handle, f"  {'baud':>8} {'fpga err':>9} {'apollo err':>11} "
                         f"{'ok':>5} {'short':>6} {'crc':>5}  verdict")

            for baud in args.rates:
                fpga_err, apollo_err = divisor_error(baud)
                set_baud(baud)
                if not build_and_flash(handle, baud):
                    emit(handle, f"  {baud:>8}  build failed")
                    continue

                result = measure(args.samples)
                if result is None:
                    emit(handle, f"  {baud:>8}  device not found after flashing")
                    continue

                good, short, bad = result
                rate = good / args.samples * 100
                verdict = ("PASS" if rate == 100 else
                           "MARGINAL" if rate >= 95 else "FAIL")
                emit(handle, f"  {baud:>8} {fpga_err:>8.2f}% {apollo_err:>10.2f}% "
                             f"{good:>5} {short:>6} {bad:>5}  {verdict} ({rate:.1f}%)")

            emit(handle)
            emit(handle, f"log: {LOG}")
    finally:
        # Always restore the sources, so a failed run does not leave the tree
        # at whatever rate it died on.
        FW_SRC.write_text(original[0])
        FPGA_SRC.write_text(original[1])
        print("\n(sources restored to their original baud)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
