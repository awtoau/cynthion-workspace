#!/usr/bin/env python3
"""Name the registers on nextpnr's critical paths for the HyperRAM clocks (#628).

nextpnr names path ends by cell (`$auto$ff.cc:337:slice$128130`); this resolves each
to the net its Q drives and the Amaranth source line, from the build's `top.json`.

    ./scripts/hyperram/hr_timing_paths.py --build tmp/awto_soc/build/ck180-sync60

Reads the LAST nextpnr run in `top.tim` (the file accumulates). Log: tmp/logs/hr_timing_paths.log.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "hr_timing_paths.log"

HEADER_RE = re.compile(r"^Info: Critical path report for (?:clock|cross-domain path) '?(.+?)'?(?: \(| ->|:)")
SECTION_RE = re.compile(r"^Info: Critical path report for (.*):$")
FMAX_RE = re.compile(r"^(?:Info|Warning): Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz")
STEP_RE = re.compile(r"^Info:\s+(clk-to-q|routing|logic|setup)\s+([\d.]+)\s+([\d.]+)\s+(.*)$")


def last_run(text):
    """`top.tim` accumulates; the last run starts at its last 'constraining clock' block."""
    starts = [m.start() for m in re.finditer(r"^Info: constraining clock net", text, re.M)]
    return text[starts[-1] if starts else 0:]


def cell_nets(design):
    """cell name -> (Q net name, src) for every FF in the yosys JSON."""
    out = {}
    for mod in design["modules"].values():
        names = {}
        for net, info in mod.get("netnames", {}).items():
            for bit in info["bits"]:
                if isinstance(bit, int) and (bit not in names or names[bit].startswith("$")):
                    names[bit] = net
        for cname, cell in mod.get("cells", {}).items():
            q = cell.get("connections", {}).get("Q")
            if q:
                out[cname] = (names.get(q[0], "?"), cell.get("attributes", {}).get("src", ""))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", type=Path, required=True, help="dir holding top.tim and top.json")
    ap.add_argument("--match", default="hyperram", help="report sections whose header contains this")
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    text = last_run((args.build / "top.tim").read_text())
    cells = cell_nets(json.loads((args.build / "top.json").read_text()))
    lines = [f"build {args.build}"]
    for line in text.splitlines():
        m = FMAX_RE.match(line)
        if m and args.match in m.group(1):
            lines.append(f"fmax {m.group(1)} {m.group(2)} MHz")
    section, steps = None, []

    def flush():
        if section is None or not steps:
            return
        src_cell = steps[0][3].split("Source ", 1)[-1].rsplit(".", 1)[0]
        dst_cell = steps[-1][3].split("Source ", 1)[-1].rsplit(".", 1)[0]
        total = steps[-1][2]
        nets = [s[3].split("Net ", 1)[1].split(" (")[0] for s in steps if s[0] == "routing"]
        named = [n for n in nets if not n.startswith("$")]
        lines.append(f"\n{section}: {total} ns")
        lines.append(f"  from {src_cell} = {cells.get(src_cell, ('?', ''))}")
        lines.append(f"  to   {dst_cell} = {cells.get(dst_cell, ('?', ''))}"
                     f" pin {steps[-1][3].rsplit('.', 1)[-1]}")
        lines.append(f"  named nets: {named}")

    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            flush()
            section = m.group(1) if args.match in m.group(1) else None
            steps = []
            continue
        if section is None:
            continue
        s = STEP_RE.match(line)
        if s:
            steps.append((s.group(1), float(s.group(2)), float(s.group(3)), s.group(4)))
    flush()
    out = "\n".join(lines)
    print(out)
    LOG.write_text(out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
