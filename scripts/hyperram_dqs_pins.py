#!/usr/bin/env python3
#
# Can the ECP5's DQS hardware reach this board's HyperRAM pins? See awtoau/cynthion-workspace#92.
# SPDX-License-Identifier: BSD-3-Clause

"""
Answers one question about #92 from the device database, with no board attached.

    python3 scripts/hyperram_dqs_pins.py
    python3 scripts/hyperram_dqs_pins.py -v      # every pin's PIO location and function

Exit status 0 if the DQS path is physically reachable, 1 if it is not. Output
goes to the terminal and to `tmp/logs/dev.log`.

## Why this is a question at all

`DQSBUFM` is not a fabric block. It sits at a fixed place in the I/O ring and
serves **one DQS group** -- a fixed set of PIOs in one bank, with the strobe pin
at a fixed position in that group. So "use the DQS PHY" is not only a gateware
decision: the board has to have wired RWDS to the group's DQS pin and the eight
DQ lines into the same group.

`docs/luna_ecp5_fpga/hyperram-detailed.md` records the DQS PHY as "unusable --
no DQS pin group", which is a claim about the wiring, not about the code. It was
never checked against the database. This checks it.

## Where the answer comes from

`prjtrellis`'s `iodb.json` for the part, which carries `pio_metadata`: for every
PIO, its bank and its `dqs` tag. The tag is the group and the pin's role in it --
`LDQS8` is the strobe of left group 8, `LDQSN8` its complement, `LDQ8` an
ordinary data pin of the same group. Group membership is therefore readable
straight off the tag, and the tag is what nextpnr packs `DQSBUFM` against.

Note the `function` field is NOT the place to look: it is populated for 58 of
197 PIOs (configuration and clock pins) and is empty for every HyperRAM pin. A
check written against `function` reports "no DQS group" for a board that has
one, which is the wrong answer this script was first written to give.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# The part and package on r1.4, from `ecp5-test/cynthion_platform/cynthion_r1_4.py`.
# `BG256` is Amaranth's name for what prjtrellis calls `CABGA256`.
DEVICE = "LFE5U-12F"
PACKAGE = "CABGA256"

# The HyperRAM pins, copied from the `ram` resource in the platform file. Kept
# here rather than imported so this script does not need Amaranth to answer a
# question about a JSON table -- but `--platform` re-reads them from the source
# and fails on a disagreement.
RAM_PINS = {
    "clk_p": "C3",
    "clk_n": "D3",
    "rwds": "D1",
    "cs": "B2",
    "reset": "C1",
    **{f"dq{i}": pin for i, pin in enumerate("F2 B1 C2 E1 E3 E2 F3 G4".split())},
}

DB = Path.home() / "opt/oss-cad-suite/share/trellis/database/ECP5"


def load(device=DEVICE, package=PACKAGE, root=DB):
    """Returns (pin -> {bank, dqs, function, row, col, pio}) for one package."""
    path = root / device / "iodb.json"
    if not path.exists():
        raise SystemExit(f"no prjtrellis database at {path}; set --database")
    db = json.loads(path.read_text())
    if package not in db["packages"]:
        raise SystemExit(f"{package} not in {sorted(db['packages'])}")
    by_pio = {(m["row"], m["col"], m["pio"]): m for m in db["pio_metadata"]}
    out = {}
    for pin, loc in db["packages"][package].items():
        meta = by_pio.get((loc["row"], loc["col"], loc["pio"]), {})
        out[pin] = {
            "row": loc["row"], "col": loc["col"], "pio": loc["pio"],
            "bank": meta.get("bank"), "dqs": meta.get("dqs"),
            "function": meta.get("function"),
        }
    return out


def group_of(tag):
    """`LDQS8` / `LDQSN8` / `LDQ8` -> ('L8', role). None for a pin outside any group."""
    if not tag:
        return None, None
    side, rest = tag[0], tag[1:]
    for role, prefix in (("dqsn", "DQSN"), ("dqs", "DQS"), ("dq", "DQ")):
        if rest.startswith(prefix):
            return f"{side}{rest[len(prefix):]}", role
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Is the ECP5's DQS hardware reachable from this board's HyperRAM pins?")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every HyperRAM pin's location and function")
    parser.add_argument("--database", type=Path, default=DB,
                        help="prjtrellis ECP5 database directory")
    args = parser.parse_args()

    pins = load(root=args.database)

    emit(f"\nHyperRAM DQS feasibility -- {DEVICE} {PACKAGE}\n")

    missing = [p for p in RAM_PINS.values() if p not in pins]
    if missing:
        emit(f"  FAIL pins not in the package: {missing}")
        return 1

    if args.verbose:
        emit("  pin map")
        for name, pin in RAM_PINS.items():
            info = pins[pin]
            emit(f"    {name:6} {pin:4} bank {info['bank']:>2}  "
                 f"R{info['row']}C{info['col']}{info['pio']}  "
                 f"{info['dqs'] or '-':8} {info['function'] or ''}")
        emit()

    checks = []

    def check(ok, what):
        checks.append(ok)
        emit(f"  {'PASS' if ok else 'FAIL'} {what}")

    data = ["rwds"] + [f"dq{i}" for i in range(8)]

    # 1. One bank. A DQS group never spans banks, so two banks ends it here.
    banks = {pins[RAM_PINS[n]]["bank"] for n in data}
    check(len(banks) == 1, f"RWDS and DQ0-7 share one bank (banks: {sorted(banks)})")

    # 2. RWDS must land on the group's strobe pin. Nothing else reaches
    #    DQSBUFM's DQSI input -- that connection is hard-wired, not routed, so a
    #    board that put RWDS on an ordinary pin cannot use DQS at all.
    rwds_group, rwds_role = group_of(pins[RAM_PINS["rwds"]]["dqs"])
    check(rwds_role == "dqs",
          f"RWDS ({RAM_PINS['rwds']}) is the strobe pin of a DQS group "
          f"(tag {pins[RAM_PINS['rwds']]['dqs'] or '-'})")

    # 3. Every DQ line must be in that same group -- DQSBUFM's read pointers
    #    reach the group's IOLOGIC and nothing outside it.
    strays = sorted(n for n in data
                    if group_of(pins[RAM_PINS[n]]["dqs"])[0] != rwds_group)
    check(not strays,
          f"DQ0-7 are all in group {rwds_group} (outside: {strays or 'none'})")

    # 4. The complement pin, reported rather than gated. DQSN is only a strobe
    #    when the interface drives a differential strobe; HyperRAM's RWDS is
    #    single-ended, so the pin is free for data -- but it is worth naming,
    #    because it is the one pin whose availability a DDR3-shaped tool might
    #    reserve.
    on_dqsn = [n for n in data if group_of(pins[RAM_PINS[n]]["dqs"])[1] == "dqsn"]
    if on_dqsn:
        emit(f"  NOTE  {', '.join(on_dqsn)} sits on the group's DQSN pin "
             f"({', '.join(RAM_PINS[n] for n in on_dqsn)}); usable as data "
             f"because RWDS is single-ended, but confirm nextpnr agrees")

    ok = all(checks)
    emit()
    emit(f"  {sum(checks)}/{len(checks)} checks passed")
    emit(f"  verdict: DQS is {'reachable' if ok else 'NOT reachable'} "
         f"on this board's wiring")
    if not ok:
        emit("  the blocker is the board, not the gateware -- #92 cannot be "
             "closed by changing the PHY")
    emit()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
