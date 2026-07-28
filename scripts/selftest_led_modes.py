#!/usr/bin/env python3
"""LED test-mode exercise over the Apollo debug link.

Extends the static LED test in selftest_leds.py with the animated modes added to
the selftest gateware. Requires a rebuilt selftest bitstream:
    .venv/bin/python -m cynthion.gateware.selftest.top --upload

Static mode is verified by register round-trip. The animated modes cannot be
verified from the host -- the pattern is generated in gateware and REGISTER_LEDS
is not driven by it -- so those are run for visual inspection, holding each mode
long enough to watch. Logs to tmp/selftest_led_modes.log.
"""

import argparse
import logging
import sys
from pathlib import Path

from apollo_fpga import ApolloDebugger
from cynthion.selftest.registers import (
    REGISTER_ID, REGISTER_LEDS, REGISTER_LED_MODE, REGISTER_LED_SPEED,
    LED_MODE_STATIC, LED_MODE_CHASE, LED_MODE_BOUNCE,
    LED_MODE_BLINK, LED_MODE_COUNT_UP, LED_MODE_BAR,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "tmp" / "selftest_led_modes.log"

EXPECTED_ID = 0x54455354
NUM_LEDS = 6

# Animated modes, in the order they are demonstrated.
ANIMATED_MODES = (
    (LED_MODE_CHASE,    "chase",    "single LED sweeping up, wrapping at the top"),
    (LED_MODE_BOUNCE,   "bounce",   "single LED sweeping up and back down"),
    (LED_MODE_BLINK,    "blink",    "all six blinking together"),
    (LED_MODE_COUNT_UP, "count-up", "6-bit binary counter"),
    (LED_MODE_BAR,      "bar",      "bar filling from LED0 then clearing"),
)

# Gateware default; ~10 animation steps/second.
DEFAULT_SPEED = 100


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, mode="w")):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def check_static(dut, value):
    """Write a 6-bit pattern in static mode and read it back."""
    dut.registers.register_write(REGISTER_LEDS, value)
    actual = dut.registers.register_read(REGISTER_LEDS) & 0b111111
    ok = actual == value
    logging.info("  write %s -> read %s  %s",
                 format(value, "06b"), format(actual, "06b"), "ok" if ok else "MISMATCH")
    return ok


def check_mode_register(dut, mode):
    """Select a mode and confirm the mode register holds it."""
    dut.registers.register_write(REGISTER_LED_MODE, mode)
    actual = dut.registers.register_read(REGISTER_LED_MODE) & 0b111
    ok = actual == mode
    if not ok:
        logging.error("  mode register: wrote %d, read %d  MISMATCH", mode, actual)
    return ok


def test_static(dut):
    """The original static-pattern test, run with the mode register set to static."""
    logging.info("static mode:")
    failures = 0
    failures += not check_mode_register(dut, LED_MODE_STATIC)

    for value in (0b000000, 0b111111):
        failures += not check_static(dut, value)
    for i in range(NUM_LEDS):
        failures += not check_static(dut, 1 << i)
    for i in range(NUM_LEDS):
        failures += not check_static(dut, 0b111111 & ~(1 << i))

    return failures


def demo_animated(dut, speed, dwell_steps):
    """Run each animated mode in turn for visual inspection.

    dwell_steps is how many animation steps to hold each mode for; the dwell is
    driven by the operator pressing Enter rather than a timer, so nothing here
    depends on guessing how long a person needs to look at the board.
    """
    failures = 0
    dut.registers.register_write(REGISTER_LED_SPEED, speed)
    readback = dut.registers.register_read(REGISTER_LED_SPEED) & 0xFF
    if readback != speed:
        logging.error("speed register: wrote %d, read %d  MISMATCH", speed, readback)
        failures += 1
    logging.info("animation speed set to %d (~%.1f steps/sec)", speed, 1000 / (speed + 1))

    for mode, name, description in ANIMATED_MODES:
        logging.info("mode %d (%s): %s", mode, name, description)
        failures += not check_mode_register(dut, mode)
        if dwell_steps:
            input(f"    watching '{name}' -- press Enter for the next mode ")

    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speed", type=int, default=DEFAULT_SPEED,
                        help="animation divisor; lower is faster (default: %(default)s)")
    parser.add_argument("--no-pause", action="store_true",
                        help="cycle the modes without pausing for inspection")
    parser.add_argument("--leave-mode", type=int, default=LED_MODE_STATIC,
                        help="mode to leave the board in (default: static)")
    args = parser.parse_args()

    if not 0 <= args.speed <= 0xFF:
        parser.error("--speed must be 0..255")

    setup_logging()
    dut = ApolloDebugger()

    device_id = dut.registers.register_read(REGISTER_ID)
    if device_id != EXPECTED_ID:
        logging.error("ID register is %#x, expected %#x -- selftest gateware not loaded?",
                      device_id, EXPECTED_ID)
        return 1
    logging.info("selftest gateware present (ID %#x)", device_id)

    failures = test_static(dut)
    failures += demo_animated(dut, args.speed, dwell_steps=not args.no_pause)

    # Leave the board in a known state.
    dut.registers.register_write(REGISTER_LED_MODE, args.leave_mode)
    if args.leave_mode == LED_MODE_STATIC:
        dut.registers.register_write(REGISTER_LEDS, 0b101010)
        logging.info("left LEDs static at 101010")
    else:
        logging.info("left LEDs in mode %d", args.leave_mode)

    if failures:
        logging.error("LED mode test FAILED (%d mismatches)", failures)
        return 1
    logging.info("LED mode test PASSED (all registers round-tripped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
