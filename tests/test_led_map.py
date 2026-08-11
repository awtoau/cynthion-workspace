# The firmware's LED map must equal the platform's. #415.
# SPDX-License-Identifier: BSD-3-Clause

"""One table, checked across two languages.

The colour of an LED is the one fact the FPGA toolchain cannot verify.
`LEDResources` is positional, the generated `top.lpf` names the pins
`led_0..led_5`, and neither carries a colour -- so a REVERSED order lived in the
`Led` enum, a table in `shell/led.rs` and the `GPIO_*` constants for months, and
nothing could contradict it. Three agreeing copies read exactly like
corroboration.

`gateware/board/cynthion_r1_4.py` owns the table now. Python derives from it
directly; Rust cannot, so this asserts the two are equal instead.

**It must fail when they diverge.** Reverse either side and this goes red -- that
is the whole point, and it is checked by `test_the_check_can_fail`.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))

from board.cynthion_r1_4 import LEDS, LED_PINS  # noqa: E402

GPIO_RS = ROOT / "firmware" / "cynthion-soc" / "src" / "gpio.rs"


def rust_map():
    """(index, ball, refdes, colour) as `gpio.rs` states it.

    Parsed rather than duplicated: a copy of the table in this file would be a
    fourth place to disagree, which is the defect being guarded.
    """
    text = GPIO_RS.read_text()

    enum = re.search(r"pub enum Led \{(.*?)\}", text, re.S)
    assert enum, "no `pub enum Led` in gpio.rs"
    order = [(name.lower(), int(value)) for name, value in
             re.findall(r"(\w+)\s*=\s*(\d+),", enum.group(1))]

    def arm_map(fn):
        body = re.search(r"pub fn %s\(self\).*?\{(.*?)\n    \}" % fn, text, re.S)
        assert body, f"no `{fn}` in gpio.rs"
        return {colour.lower(): value for colour, value in
                re.findall(r"Led::(\w+)\s*=>\s*\"([^\"]+)\"", body.group(1))}

    balls, refdes = arm_map("ball"), arm_map("refdes")
    return [(index, balls[colour], refdes[colour], colour)
            for colour, index in sorted(order, key=lambda pair: pair[1])]


def test_firmware_matches_the_platform():
    assert rust_map() == [tuple(row) for row in LEDS]


def test_the_resource_pins_come_from_the_table():
    """`LEDResources(pins=...)` must not restate the balls."""
    assert LED_PINS == " ".join(ball for _i, ball, _s, _c in LEDS)


def test_the_check_can_fail():
    """A check that cannot go red is not a check.

    Reversing one side must be caught. Seven instruments in this repo were found
    in one week reporting success while structurally unable to fail.
    """
    assert rust_map() != [tuple(row) for row in reversed(LEDS)], (
        "the map is its own reverse -- this test could not detect a reversal, "
        "which is the exact defect it exists for")


def test_every_index_appears_once():
    indices = [index for index, _b, _s, _c in LEDS]
    assert indices == list(range(len(LEDS)))
    assert len({colour for _i, _b, _s, colour in LEDS}) == len(LEDS)


@pytest.mark.parametrize("colour,index", [("blue", 1), ("yellow", 3)])
def test_the_two_points_fixed_on_the_bench(colour, index):
    """Blue is the 2 Hz heartbeat, yellow the 1 Hz fabric flash.

    These two were established by watching the board, not by reading the sheet,
    and they are what caught the reversal. Pinned so a future "correction" has to
    argue with an observation.
    """
    assert dict((c, i) for i, _b, _s, c in LEDS)[colour] == index
