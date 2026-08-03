#!/usr/bin/env python3
"""LED test over the Apollo debug link, against the Cynthion selftest gateware.

Requires the selftest bitstream to be loaded:
    .venv/bin/python -m cynthion.gateware.selftest.top --upload

Writes patterns to REGISTER_LEDS (6 bits, LED0..LED5) and reads each back to
confirm the register round-trips. Logs to tmp/selftest_leds.log.
"""

import logging
import sys
from pathlib import Path

from apollo_fpga import ApolloDebugger
from cynthion.selftest.registers import REGISTER_ID, REGISTER_LEDS

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "tmp" / "selftest_leds.log"

EXPECTED_ID = 0x54455354
NUM_LEDS = 6


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, mode="w")):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def check(dut, value):
    """Write a 6-bit pattern to the LED register and read it back."""
    dut.registers.register_write(REGISTER_LEDS, value)
    actual = dut.registers.register_read(REGISTER_LEDS) & 0b111111
    ok = actual == value
    logging.info("  write %s -> read %s  %s",
                 format(value, "06b"), format(actual, "06b"), "ok" if ok else "MISMATCH")
    return ok


def main():
    setup_logging()
    dut = ApolloDebugger()

    device_id = dut.registers.register_read(REGISTER_ID)
    if device_id != EXPECTED_ID:
        logging.error("ID register is %#x, expected %#x -- selftest gateware not loaded?",
                      device_id, EXPECTED_ID)
        return 1
    logging.info("selftest gateware present (ID %#x)", device_id)

    failures = 0

    logging.info("all off / all on:")
    for value in (0b000000, 0b111111):
        failures += not check(dut, value)

    logging.info("walking single LED (LED0..LED%d):", NUM_LEDS - 1)
    for i in range(NUM_LEDS):
        failures += not check(dut, 1 << i)

    logging.info("walking inverse:")
    for i in range(NUM_LEDS):
        failures += not check(dut, 0b111111 & ~(1 << i))

    # Leave a recognisable pattern so the result is visible on the board.
    dut.registers.register_write(REGISTER_LEDS, 0b101010)
    logging.info("left LEDs at 101010")

    if failures:
        logging.error("LED test FAILED (%d mismatches)", failures)
        return 1
    logging.info("LED test PASSED (all patterns read back correctly)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
