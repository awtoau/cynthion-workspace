#!/usr/bin/env python3
#
# Every peripheral output port must reach something. See #305.
# SPDX-License-Identifier: BSD-3-Clause

"""Find peripheral `Out(...)` ports that nothing in the SoC ever reads.

    ./scripts/check_ports_connected.py          # exit 1 if any port dangles

## Why this exists

`VbusControl.control_vbus_in_en` and `.aux_vbus_in_en` drove no pad for as long
as they existed. `top.py` never requested the pads, so the register behind them
was RW scratch -- and a shell verb, two driver functions and two docstrings all
described a working input selector on top of it (#305).

**Writing a register and observing nothing is indistinguishable from writing it
and it working.** Nothing failed at build time: an undriven output elaborates,
places and routes without a word. That is why it survived, and it is the same
shape as every other fault found in the 2026-08-10 audit.

## What it checks

For each peripheral instantiated in `top.py`, every `Out(...)` name in that
class's signature must appear as `.<name>` somewhere in the SoC sources. A port
read nowhere is either dead or a defect, and both want deleting.

Not a substitute for elaboration -- it is a grep with the port list supplied by
the class rather than by hand. It catches the case that matters: a port added to
a signature and never wired up.

Report -> `tmp/logs/check-ports-connected.log`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOC = ROOT / "gateware" / "soc"
TOP = SOC / "top.py"
PERIPHERALS = SOC / "peripherals"
LOG = ROOT / "tmp" / "logs" / "check-ports-connected.log"

# `m.submodules.vbus_ctrl = vbus_ctrl = VbusControl()`
INSTANTIATED = re.compile(r"m\.submodules\.\w+\s*=\s*\w+\s*=\s*(\w+)\(")
# `"control_vbus_en":   Out(1),` -- the name is what a signature declares.
OUT_PORT = re.compile(r"""["'](\w+)["']\s*:\s*Out\(""")


def sources():
    """Every SoC Python file, which is where a port could be read."""
    return sorted(SOC.rglob("*.py"))


def main():
    top = TOP.read_text()
    classes = set(INSTANTIATED.findall(top))

    # One concatenated haystack: a port may be read in top.py or by another
    # peripheral, and either counts as connected.
    haystack = "\n".join(path.read_text() for path in sources())

    dangling = []
    checked = 0
    for path in sorted(PERIPHERALS.glob("*.py")):
        text = path.read_text()
        for klass in classes:
            if not re.search(rf"class {klass}\b", text):
                continue
            for port in OUT_PORT.findall(text):
                checked += 1
                if not re.search(rf"\.{port}\b", haystack.replace(f'"{port}"', "")):
                    dangling.append(f"{path.relative_to(ROOT)}: {klass}.{port}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{checked} output port(s) checked across {len(classes)} instantiated peripheral(s)"]
    lines += [f"  DANGLING {entry}" for entry in dangling]
    if not dangling:
        lines.append("  every output port is read somewhere")
    LOG.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if dangling else 0


if __name__ == "__main__":
    sys.exit(main())
