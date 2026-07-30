#!/usr/bin/env python3.15t
"""
Work out what the TAP actually returns on TDO during a shift, empirically.

The synthetic benchmark needs a readback check to prove bytes really moved, and
that check needs to know what a correct response looks like. Assuming BYPASS
(TDO = TDI delayed one bit) turned out to be wrong: the readback was clearly
data-dependent but matched no model. Rather than guess again, this probe uses
the ordinary SET_OUT_BUFFER/SCAN path -- whose behaviour is not in question --
to send a known pattern and print exactly what comes back, so the relationship
can be read off rather than assumed.

Logs to ./tmp/logs/jtag_tdo_probe.log.
"""

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "repos" / "apollo"))

from apollo_fpga import ApolloDebugger  # noqa: E402

REQUEST_JTAG_START = 0xBF
REQUEST_JTAG_STOP = 0xBE
REQUEST_JTAG_SET_OUT_BUFFER = 0xB1
REQUEST_JTAG_GET_IN_BUFFER = 0xB2
REQUEST_JTAG_SCAN = 0xB3
REQUEST_JTAG_GOTO_STATE = 0xB5

STATE_SHIFT_IR = 11
STATE_SHIFT_DR = 4
STATE_RESET = 0
STATE_IDLE = 1

# ECP5 BYPASS is all-ones in a 8-bit IR.
ECP5_IR_BITS = 8
ECP5_BYPASS = 0xFF


def setup_logging():
    log_dir = REPO_ROOT / "tmp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jtag_tdo_probe")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(log_dir / "jtag_tdo_probe.log", mode="a")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(sh)
    return logger


def scan(dev, data: bytes) -> bytes:
    """Send data through the current TAP state via the ordinary scan path."""
    dev.out_request(REQUEST_JTAG_SET_OUT_BUFFER, data=data)
    dev.out_request(REQUEST_JTAG_SCAN, value=len(data) * 8, index=0)
    return dev.in_request(REQUEST_JTAG_GET_IN_BUFFER, length=len(data))


def describe(sent: bytes, got: bytes) -> str:
    """Try the obvious models and report which, if any, fits."""
    # Model A: TDO == TDI (transparent).
    if got == sent:
        return "TDO == TDI exactly (no delay)"

    # Model B: one-bit delay, LSB-first (what BYPASS gives on an LSB-first SPI).
    expected = bytearray()
    carry = 0
    for b in sent:
        expected.append(((b << 1) | carry) & 0xFF)
        carry = b >> 7
    if bytes(expected) == got:
        return "TDO == TDI delayed one bit (BYPASS, LSB-first)"

    # Model C: one-bit delay the other direction.
    expected = bytearray()
    carry = 0
    for b in sent:
        expected.append(((b >> 1) | (carry << 7)) & 0xFF)
        carry = b & 1
    if bytes(expected) == got:
        return "TDO == TDI delayed one bit (MSB-first shift)"

    return "no simple model fits"


def main() -> int:
    log = setup_logging()
    dev = ApolloDebugger()
    log.info("=" * 66)
    log.info(f"TDO probe against firmware {dev.get_firmware_version()}")
    log.info("=" * 66)

    dev.out_request(REQUEST_JTAG_START)
    try:
        pattern = bytes((i * 7 + 1) & 0xFF for i in range(16))

        # Case 1: whatever state the chain is left in by JTAG_START.
        dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_SHIFT_DR)
        got = scan(dev, pattern)
        log.info("")
        log.info("SHIFT_DR, instruction as-left:")
        log.info(f"  sent: {pattern.hex()}")
        log.info(f"  got : {bytes(got).hex()}")
        log.info(f"  -> {describe(pattern, bytes(got))}")

        # Case 2: explicitly load BYPASS, which should give a clean one-bit
        # delay and is the state the benchmark's model assumes.
        dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_RESET)
        dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_IDLE)
        dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_SHIFT_IR)
        scan(dev, bytes([ECP5_BYPASS]))
        dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_IDLE)
        dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_SHIFT_DR)

        got = scan(dev, pattern)
        log.info("")
        log.info("SHIFT_DR with BYPASS loaded:")
        log.info(f"  sent: {pattern.hex()}")
        log.info(f"  got : {bytes(got).hex()}")
        log.info(f"  -> {describe(pattern, bytes(got))}")

        # Case 3: repeat the same scan, to see whether the response is a pure
        # function of the data or depends on what preceded it. The benchmark
        # re-sends one buffer many times, so this is the case that matters.
        got2 = scan(dev, pattern)
        log.info("")
        log.info("Same scan again (benchmark re-sends one buffer repeatedly):")
        log.info(f"  got : {bytes(got2).hex()}")
        log.info(f"  -> {'identical to previous' if got2 == got else 'DIFFERS from previous'}")

    finally:
        dev.out_request(REQUEST_JTAG_STOP)

    return 0


if __name__ == "__main__":
    sys.exit(main())
