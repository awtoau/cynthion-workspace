#!/usr/bin/env python3
#
# The latency pass set at each CK rung this bitstream carries. #313, #331.
# SPDX-License-Identifier: BSD-3-Clause

"""Run `bist latency` once per CK rung and compare the pass sets.

    ./scripts/hyperram_ck_sweep.py                 # every rung, 16 passes
    ./scripts/hyperram_ck_sweep.py --passes 64

Assumes a non-DQS bitstream with two rungs is loaded:

    CYNTHION_HYPERRAM_CK_MHZ=80,90 \\
      python3 scripts/soc_run.py --skip-tests --force-flash

## What it can and cannot see

`DCSC` is a 2:1 mux, so a bitstream carries at most TWO rungs and a wider sweep
costs more builds. `scripts/hyperram_ck_rungs.py` does that arithmetic.

**The non-DQS CK ceiling is the fabric, not the part.** This PHY emits one CK per
`sync` cycle, so CK is `hr_clk` and nextpnr caps it near 94 MHz -- a 96 MHz build
does not exist. The part is rated 166 MHz. So a clean sweep here says the FPGA
kept up, not that the memory's limit was found; that measurement needs the 4:1
DQS path.

Transcript -> `tmp/logs/hyperram-ck-sweep.txt`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bist_rows  # noqa: E402
import soc_shell  # noqa: E402

TRANSCRIPT = ROOT / "tmp" / "logs" / "hyperram-ck-sweep.txt"

RUNG = bist_rows.RUNG

# Datasheet Table 8, rev A01-006 p.21. Sparse: 3..13 are RESERVED.
LEGAL = {0, 1, 2, 14, 15}


class Board:
    def __init__(self):
        self.link = soc_shell.Link.open(None)
        self.log = []
        # PRIME THE LINK. `read_until_prompt` returns on the first prompt it
        # sees, and after a configure that is the banner's -- so the first real
        # command returns before its own reply arrives and parses as "no rungs",
        # which reads exactly like a bitstream without the verb.
        self.link.write(b"\r")
        self.link.read_until_prompt(budget_s=3)

    def send(self, command, budget):
        self.link.write(command.encode() + b"\r")
        text = self.link.read_until_prompt(budget_s=budget).decode("ascii", "replace")
        self.log.append(f"$ {command}\n{text}")
        return text

    def close(self):
        self.link.close()
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text("\n".join(self.log))


def rungs(board):
    """(index, MHz) for every rung, by asking the board rather than the build."""
    # Select rung 0 rather than a bare `bist ck`: the first command after a
    # configure can collide with the banner and come back as `unknown command`,
    # which reads exactly like a build without the verb.
    text = board.send("bist ck 0", 4)
    found = [(int(m["rung"]), float(m["mhz"])) for m in RUNG.finditer(text)]
    if not found:
        raise SystemExit(
            "no rungs reported. Either this bitstream has no CK selector, or the "
            f"firmware predates `bist ck`. Reply was:\n{text}")
    if "NOT LOCKED" in text:
        raise SystemExit("the PLL is not locked; no CK measurement means anything")
    return found


def sweep(board, passes):
    """`lat -> (sel, errors, words, control, verdict)` for the fixed rows.

    One row per code, at the capture phase that did best of the eight (#421):
    a code that fails here failed at every phase, not at a chosen one.
    """
    # `bist latency` runs 256 cells on a DQS build, 32 on a non-DQS one; the
    # poll bound inside the firmware scales with passes, so the budget here only
    # has to cover the serial round trip plus the run.
    text = board.send(f"bist latency {passes}", 30)
    rows = {}
    for row in bist_rows.rows(text):
        if row["fixed"]:
            rows[row["lat"]] = (row["sel"], row["errors"], row["words"],
                                row["control"], row["verdict"])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--passes", type=int, default=16,
                        help="passes per cell (default 16)")
    args = parser.parse_args()

    board = Board()
    try:
        table = rungs(board)
        print(f"{len(table)} rung(s): "
              + ", ".join(f"{mhz:g} MHz" for _, mhz in table))

        results, silent = {}, []
        for index, mhz in table:
            board.send(f"bist ck {index}", 4)
            rows = sweep(board, args.passes)
            if not rows:
                silent.append(f"{mhz:g} MHz")
                print(f"\n  {mhz:g} MHz -- NO ROWS PARSED, the run did not report")
                continue
            passed = sorted(c for c, r in rows.items() if r[4] == "PASS")
            results[mhz] = (passed, rows)
            print(f"\n  {mhz:g} MHz  PASS {passed}")
            for code in sorted(rows):
                sel, errors, words, control, verdict = rows[code]
                if verdict == "PASS" or code in LEGAL:
                    mark = "legal" if code in LEGAL else "RESERVED"
                    print(f"      lat {code:2d} {mark:8s} sel {sel} "
                          f"errors {errors:5d} words {words:5d} "
                          f"control {control:5d}  {verdict}")

        # A rung that parsed nothing must not exit 0: an empty result set reads
        # exactly like a board that reported nothing, and the per-rung note
        # above scrolls past (#333). ANY silent rung, not only all of them --
        # a comparison missing a rung is not the comparison that was asked for.
        if silent:
            raise SystemExit(
                f"NO ROWS PARSED AT {', '.join(silent)} of {len(table)} rung(s). "
                "Either `bist latency` is not in this build or its row format "
                "has moved away from scripts/bist_rows.py -- which "
                "tests/test_bist_row_parsers.py should have caught. "
                f"Transcript: {TRANSCRIPT}")

        if len(results) > 1:
            print("\ncomparison")
            sets = {mhz: set(passed) for mhz, (passed, _) in results.items()}
            common = set.intersection(*sets.values())
            print(f"  passes at every rung: {sorted(common)}")
            for mhz, s in sets.items():
                lost = sorted(common ^ s)
                if lost:
                    print(f"  {mhz:g} MHz differs by {lost}")
            if all(s == common for s in sets.values()):
                print("  IDENTICAL at every rung -- nothing in this band is "
                      "marginal, which is what the fabric ceiling predicts")
    finally:
        board.close()
        print(f"\ntranscript -> {TRANSCRIPT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
