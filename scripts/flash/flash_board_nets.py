#!/usr/bin/env python3
#
# Print every net on the configuration flash and what else sits on it (#flash baseline).
# SPDX-License-Identifier: BSD-3-Clause

"""Wiring of the config flash from the r1.4 KiCad schematic (pin facts only).

    ./scripts/flash/flash_board_nets.py [--ref U7]

- Exports the netlist with `kicad-cli` into tmp/flash-baseline/, then lists, per flash pin,
  the net and every other component pin on it.
- Log: tmp/logs/flash_board_nets.log.
"""

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def repo_root():
    env = os.environ.get("CYNTHION_WORKSPACE_REPO")
    if env:
        return Path(env)
    try:
        return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                   text=True, check=True, cwd=Path(__file__).parent).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent.parent


def main_checkout():
    """A worktree has no submodules checked out; the main checkout (git's common dir) does."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True,
                                cwd=Path(__file__).parent).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return ROOT


ROOT = repo_root()
HW = ROOT / "repos" / "cynthion-hardware"
if not (HW / "cynthion.kicad_sch").exists():
    HW = main_checkout() / "repos" / "cynthion-hardware"
OUT = ROOT / "tmp" / "flash-baseline" / "cynthion.net.xml"
LOG = ROOT / "tmp" / "logs" / "flash_board_nets.log"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="U7", help="flash reference designator")
    args = ap.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", str(OUT),
                    str(HW / "cynthion.kicad_sch")], check=True, capture_output=True)
    root = ET.parse(OUT).getroot()
    values = {c.get("ref"): c.findtext("value") for c in root.iter("comp")}
    lines = [f"{args.ref} = {values.get(args.ref)}; schematic {HW / 'cynthion.kicad_sch'}"]
    rows = []
    for net in root.iter("net"):
        nodes = net.findall("node")
        mine = [n for n in nodes if n.get("ref") == args.ref]
        for n in mine:
            others = [f"{o.get('ref')}({values.get(o.get('ref'))}).{o.get('pin')}"
                      f"[{o.get('pinfunction') or ''}]" for o in nodes if o is not n]
            rows.append((int(n.get("pin")), n.get("pinfunction"), net.get("name"), others))
    for pin, fn, name, others in sorted(rows):
        lines.append(f"pin {pin} {fn:<12} net {name:<28} -> {', '.join(others)}")
    text = "\n".join(lines)
    print(text)
    LOG.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
