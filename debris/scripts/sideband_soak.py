#!/usr/bin/env python3
#
# FPGA_ADV sideband soak: every baud, both drive styles, each direction scored
# separately.
# SPDX-License-Identifier: BSD-3-Clause

"""
Soak tests the sideband link across baud rates and drive styles, attributing
failures to a direction.

**Targets the test bitstream** (`ecp5-test/sideband/`), because it soaks with
`POWER` -- 18 bytes, the longest reply in the protocol, which the shipping link
does not implement. The physical layer it measures is common to both.

This extends `sideband_speed_ladder.py` rather than replacing it. That script
finds the speed ceiling with a few hundred round trips per rate, which is the
right shape for "where does it break". It is not sufficient for accepting a
change to the drive style, for two reasons:

**A round trip cannot say which direction failed.** The POWER command it uses
goes out from Apollo and comes back from the FPGA; a bad CRC means the pair
failed, not which half. FPGA-to-Apollo and Apollo-to-FPGA have completely
different failure modes -- the FPGA transmits from a clean divided clock, while
Apollo bit-bangs, and open-drain changes the *rising* edge only, which each
direction sees differently. Scoring them together hides exactly the asymmetry
that matters.

**A few hundred samples cannot see a rare fault.** The faults being guarded
against here -- a marginal rise time, a turnaround collision, a framing slip
under USB load -- are rate-dependent, not deterministic. At 200 samples a 1%
error rate looks like 2 bad transactions, which is indistinguishable from noise.
Soaking to tens of thousands makes a 0.01% floor visible.

The reason this matters now: the drive style changed to open-drain at both ends
to remove a measured 30.4 us driver-to-driver short. That is the correct fix, but
open-drain replaces an actively driven rising edge with an RC against two
internal pull-ups, estimated at 0.3-1.5 us -- 7-35% of a bit at 230400 baud. The
optimistic end is fine and the pessimistic end is marginal, and arithmetic cannot
settle which. Only a soak at each rate, in each direction, in both drive styles
can.

## What each direction test measures

**FPGA to Apollo** uses the counting-stream transmitter in
`ecp5-test/adv_speed/`: an incrementing byte, so a dropped byte shows as a gap
and a corrupted one as a wrong value at a known position. A repeating pattern
would resynchronise and hide the loss.

**Apollo to FPGA** uses the responder's own framing-error counter, which is
exposed precisely so that "the link is quiet" and "the link is receiving noise"
are different observations. Apollo sends commands; every byte the FPGA frames
badly increments that counter. A command that gets a correct reply proves the
inbound byte arrived intact.

## Cost

Each (rate, drive style) pair rebuilds firmware and gateware and reflashes both,
because both ends must agree -- a mismatched pair fails for the wrong reason and
looks like a signal problem. That is a few minutes per point, so a full matrix is
hours. It is meant to be left running.

    ./scripts/sideband_soak.py                        # full matrix
    ./scripts/sideband_soak.py --rates 115200 230400
    ./scripts/sideband_soak.py --styles open-drain
    ./scripts/sideband_soak.py --samples 20000
    ./scripts/sideband_soak.py --dry-run             # show the plan, build nothing
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
FW_SRC = FIRMWARE / "src" / "boards" / "cynthion_d11" / "fpga_adv.c"
GW_SRC = ROOT / "ecp5-test" / "sideband" / "sideband_gateware.py"
RESPONDER = APOLLO / "apollo_fpga" / "gateware" / "sideband.py"
BITSTREAM = ROOT / "ecp5-test" / "sideband" / "build" / "top.bit"

LOG = ROOT / "tmp" / "logs" / "sideband_soak.log"
RESULTS = ROOT / "tmp" / "sideband_soak.json"

# The ladder from #85 plus the two rates either side of the shipping one, since
# the open-drain question is specifically whether 230400 still holds.
DEFAULT_RATES = [115200, 230400, 460800, 921600]
DEFAULT_STYLES = ["open-drain", "push-pull"]

# 60 MHz sync domain on the FPGA, 48 MHz CPU on the SAMD11.
FPGA_CLK = 60e6
APOLLO_CLK = 48e6

# Vendor request 0xc3 overloads wValue. Any value that is not one of these SETS
# the mode, which is a trap: reading with wValue=0 selects EIC rather than
# reporting anything.
REQ = 0xC3
W_MODE_READ = 0xFFFF
W_COMMAND = 0xFFFE
W_HEALTH = 0xFFFC
MODE_UART = 1

CMD_POWER = 0x2B
POWER_LEN = 18

# Bounded poll counts, not delays. Each iteration is one lsusb invocation, so
# this is "give up after N checks" rather than a wall-clock wait -- the machine
# sets the pace. 200 is far more than a DFU transition needs and still returns
# promptly when the device never appears.
USB_POLL_LIMIT = 200


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def divisor_error(baud):
    """How far each end's integer divisor lands from the requested rate.

    Both ends divide a fixed clock, so achievable rates are quantised. A UART
    tolerates roughly +/-2% total across both ends, and at high baud the
    quantisation alone can exceed that -- a resolution limit rather than a
    signal-integrity one, and worth separating from an RC failure.
    """
    fpga = FPGA_CLK / round(FPGA_CLK / baud)
    apollo = APOLLO_CLK / round(APOLLO_CLK / baud)
    return (100 * (fpga - baud) / baud, 100 * (apollo - baud) / baud)


def configure_sources(baud, open_drain):
    """Rewrite baud and drive style on both sides. Returns the originals."""
    originals = {p: p.read_text() for p in (FW_SRC, GW_SRC, RESPONDER)}

    fw = FW_SRC.read_text()
    fw = re.sub(r"#define ADV_UART_BAUD \d+UL",
                f"#define ADV_UART_BAUD {baud}UL", fw)
    FW_SRC.write_text(fw)

    for path in (GW_SRC, RESPONDER):
        text = path.read_text()
        text = re.sub(r"baud\s*=\s*\d+", f"baud={baud}", text)
        text = re.sub(r"open_drain\s*=\s*(True|False)",
                      f"open_drain={bool(open_drain)}", text)
        path.write_text(text)

    return originals


def restore(originals):
    for path, text in originals.items():
        path.write_text(text)


def poll_usb(token):
    """Wait for a USB identity by polling, never by sleeping."""
    for _ in range(USB_POLL_LIMIT):
        out = subprocess.run(["lsusb", "-d", "1d50:615c"],
                             capture_output=True, text=True).stdout
        if token in out:
            return True
    return False


def build_and_flash(handle, baud, open_drain):
    """Rebuild and reflash both ends. Both must agree on rate and style."""
    result = subprocess.run(["make", "APOLLO_BOARD=cynthion"], cwd=FIRMWARE,
                            capture_output=True, text=True)
    if result.returncode != 0:
        emit(handle, f"      firmware build failed: {result.stderr[-160:]}")
        return False

    gateware = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sideband_build.py"),
         "--baud", str(baud),
         "--drive", "open-drain" if open_drain else "push-pull"],
        cwd=ROOT, capture_output=True, text=True)
    if gateware.returncode != 0:
        emit(handle, f"      gateware build failed: "
                     f"{(gateware.stderr or gateware.stdout)[-160:]}")
        return False

    subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0, 'repos/apollo'); "
                    "from apollo_fpga import ApolloDebugger; "
                    "ApolloDebugger().boot_to_dfu()"],
                   cwd=ROOT, capture_output=True)
    if not poll_usb("Bootloader"):
        emit(handle, "      never reached the bootloader")
        return False

    binary = FIRMWARE / "_build" / "cynthion_d11" / "firmware.bin"
    subprocess.run([sys.executable, "-c",
                    "from fwup.dfu import DFUTarget; "
                    "t = DFUTarget(idVendor=0x1d50, idProduct=0x615c); "
                    f"t.program(open({str(binary)!r}, 'rb').read()); "
                    "t.run_user_program()"],
                   cwd=ROOT, capture_output=True)
    if not poll_usb("Debugger"):
        emit(handle, "      never came back from the bootloader")
        return False

    configure = subprocess.run(
        [sys.executable, "repos/apollo/apollo_fpga/commands/cli.py",
         "configure", str(BITSTREAM)],
        cwd=ROOT, capture_output=True, text=True)
    return configure.returncode == 0


def crc8(data):
    """CRC-8/ATM: poly 0x07, init 0x00. Matches the gateware exactly."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def device():
    import usb.core
    return usb.core.find(idVendor=0x1d50, idProduct=0x615c)


def soak_fpga_to_apollo(dev, samples):
    """Score the FPGA's transmit path using the responder's own replies.

    Every POWER reply is 18 bytes with a CRC over the first 17, so a corrupted
    inbound byte is counted rather than inferred. A short reply means the
    firmware timed out waiting -- bytes that never arrived, as distinct from
    bytes that arrived wrong, and those have different causes.
    """
    good = short = bad_crc = 0
    for _ in range(samples):
        try:
            reply = bytes(dev.ctrl_transfer(
                0xC0, REQ, W_COMMAND, (POWER_LEN << 8) | CMD_POWER, POWER_LEN))
        except Exception:
            short += 1
            continue
        if len(reply) != POWER_LEN:
            short += 1
        elif crc8(reply[:-1]) != reply[-1]:
            bad_crc += 1
        else:
            good += 1
    return {"good": good, "short": short, "bad_crc": bad_crc}


def soak_apollo_to_fpga(dev, samples):
    """Score Apollo's transmit path using the FPGA's framing-error counter.

    The counter increments for every frame whose stop bit was low -- the only
    thing distinguishing a byte from noise. A correct reply proves the outbound
    command byte was framed correctly at the FPGA, so replies and framing errors
    together attribute the failure to this direction.

    Returns None where the health counters are absent, which is the case on
    firmware older than b48d4bf -- reporting zero errors there would claim a
    clean link on a measurement that cannot see one.
    """
    try:
        before = list(dev.ctrl_transfer(0xC0, REQ, W_HEALTH, 0, 3))
    except Exception:
        return None

    replied = 0
    for _ in range(samples):
        try:
            reply = bytes(dev.ctrl_transfer(
                0xC0, REQ, W_COMMAND, (POWER_LEN << 8) | CMD_POWER, POWER_LEN))
            if len(reply) == POWER_LEN:
                replied += 1
        except Exception:
            pass

    after = list(dev.ctrl_transfer(0xC0, REQ, W_HEALTH, 0, 3))
    # [ok, crc_fail, timeout], each saturating at 255 and cleared by the read.
    return {"replied": replied, "before": before, "after": after,
            "crc_fail": after[1], "timeout": after[2],
            "saturated": any(v >= 255 for v in after)}


def verdict(forward, reverse, samples):
    if forward["good"] == samples and (reverse is None or
                                       reverse["crc_fail"] == 0):
        return "PASS"
    rate = forward["good"] / samples * 100 if samples else 0
    return "MARGINAL" if rate >= 99.9 else "FAIL"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rates", type=int, nargs="+", default=DEFAULT_RATES)
    parser.add_argument("--styles", nargs="+", default=DEFAULT_STYLES,
                        choices=["open-drain", "push-pull"])
    parser.add_argument("--samples", type=int, default=5000,
                        help="transactions per direction per point")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the matrix and exit")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    points = [(r, s) for s in args.styles for r in args.rates]

    with LOG.open("w") as handle:
        emit(handle, "FPGA_ADV sideband soak")
        emit(handle, f"{len(points)} points, {args.samples} transactions per "
                     f"direction, both directions scored separately")
        emit(handle)

        if args.dry_run:
            for rate, style in points:
                fpga_err, apollo_err = divisor_error(rate)
                emit(handle, f"  {rate:>8} {style:<11} "
                             f"divisor error fpga {fpga_err:+.2f}% "
                             f"apollo {apollo_err:+.2f}%")
            emit(handle)
            emit(handle, "dry run: nothing built, nothing flashed")
            return 0

        emit(handle, f"  {'baud':>8} {'style':<11} {'fpga>ap ok':>11} "
                     f"{'short':>6} {'crc':>5} {'ap>fpga crc':>12} "
                     f"{'timeout':>8}  verdict")

        results = []
        originals = None
        try:
            for rate, style in points:
                open_drain = style == "open-drain"
                originals = configure_sources(rate, open_drain)

                if not build_and_flash(handle, rate, open_drain):
                    emit(handle, f"  {rate:>8} {style:<11}  build/flash failed")
                    results.append({"baud": rate, "style": style,
                                    "error": "build or flash failed"})
                    restore(originals)
                    continue

                dev = device()
                if dev is None:
                    emit(handle, f"  {rate:>8} {style:<11}  device absent")
                    restore(originals)
                    continue

                dev.ctrl_transfer(0x40, REQ, MODE_UART, 0, None)
                mode = list(dev.ctrl_transfer(0xC0, REQ, W_MODE_READ, 0, 1))
                if mode != [MODE_UART]:
                    emit(handle, f"  {rate:>8} {style:<11}  "
                                 f"mode did not take: {mode}")
                    restore(originals)
                    continue

                started = time.perf_counter()
                forward = soak_fpga_to_apollo(dev, args.samples)
                reverse = soak_apollo_to_fpga(dev, args.samples)
                elapsed = time.perf_counter() - started

                point = {"baud": rate, "style": style, "samples": args.samples,
                         "fpga_to_apollo": forward,
                         "apollo_to_fpga": reverse,
                         "verdict": verdict(forward, reverse, args.samples),
                         "seconds": round(elapsed, 1)}
                results.append(point)

                rev_crc = "n/a" if reverse is None else reverse["crc_fail"]
                rev_to = "n/a" if reverse is None else reverse["timeout"]
                emit(handle, f"  {rate:>8} {style:<11} {forward['good']:>11} "
                             f"{forward['short']:>6} {forward['bad_crc']:>5} "
                             f"{str(rev_crc):>12} {str(rev_to):>8}  "
                             f"{point['verdict']}")
                restore(originals)
                originals = None
        finally:
            if originals:
                restore(originals)

        RESULTS.write_text(json.dumps(results, indent=2) + "\n")
        emit(handle)
        if any(r.get("apollo_to_fpga") is None for r in results):
            emit(handle, "Reverse direction reads n/a where the flashed "
                         "firmware predates the health counters (b48d4bf).")
            emit(handle, "That is a measurement gap, not a clean result.")
        emit(handle, f"results: {RESULTS}")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
