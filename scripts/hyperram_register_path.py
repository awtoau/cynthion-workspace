#!/usr/bin/env python3
#
# Can the CPU write the HyperRAM's configuration registers? See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Prove -- or disprove -- the CR0 read and write paths, in that order.

    ./scripts/hyperram_register_path.py                # the four steps
    ./scripts/hyperram_register_path.py --skip-all     # steps 1-2 only
    ./scripts/hyperram_register_path.py --passes 16    # per cell

Assumes a non-DQS BIST bitstream is already loaded:

    CYNTHION_HYPERRAM_BIST=1 CYNTHION_HYPERRAM_BIST_DQS=0 \\
      CYNTHION_HYPERRAM_CK_MHZ=85.7143 \\
      python3 scripts/soc_run.py --skip-tests --force-flash

Transcript -> `tmp/logs/hyperram-register-path.txt`, log -> `tmp/logs/dev.log`.

## The question, and the order it has to be asked in

`REG_DEVICE_READBACK` (30) holds CR0 **as the part reports it**, latched by the
engine's `CONFIG_VERIFY` state. That state is only reached through a commanded
run with an apply bit set -- so a bare `bist status` after a reconfigure reports
`0x0000` because nothing has configured anything yet, NOT because the part is
mute. Every step here therefore runs a cell first and reads the status after.

1. read path -- one cell at the power-on axes, then the readback. `0x8F2F` is
   the datasheet power-on default (latency code 2, fixed, drive 3). Anything
   that is not `0x0000` or `0xffff` means the part answered.
2. write path -- a DIFFERENT legal CR0, then the readback. The read path
   proves nothing about the write path: the engine could be reading a register
   the CPU never changed.
3. `bist latency` -- `CR0[7:4]` is SPARSE (Table 8, rev A01-006 p.21). Legal:
   0 (5 clocks, 133 MHz), 1 (6, 166), 2 (7, 200, POR), 14 (3, 83), 15 (4, 100).
   3-13 RESERVED. At CK 85.7 the datasheet predicts `{0, 1, 2, 15}`, 14 marginal.
4. `bist all` -- the full runtime cross product, 4,096 cells.

## Why the shell and not a serial open

`tio_user.py --serve` holds the console tty and forwards it on port 9000. A
second opener gets the tty and none of the bytes, which reads as a dead board.
`soc_shell.Link` routes through the socket when the service is up.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bist_rows  # noqa: E402
from devlog import emit  # noqa: E402
import soc_shell  # noqa: E402

TRANSCRIPT = ROOT / "tmp" / "logs" / "hyperram-register-path.txt"

# `  CR0     part reports 0x8f2f  latency code 2 = 7 clocks  fixed  drive 3`.
# The clocks and the RESERVED marker sit between the code and the mode, and this
# pattern omitted both -- so it matched nothing and every step reported "no CR0
# line in `bist status`" whatever the part said.
CR0 = re.compile(r"CR0\s+part reports\s+(0x[0-9a-fA-F]+)\s+latency code\s+(\d+)"
                 r"\s+=\s+\d+ clocks(?:\s+RESERVED)?\s+"
                 r"(fixed|variable)\s+drive\s+(\d+)")

# One row shape for every sweep now, so `bist latency` and `bist all` read alike.
LATENCY_ROW = ALL_ROW = bist_rows.ROW
TALLY = bist_rows.SUMMARY

# The datasheet's legal `CR0[7:4]` codes and the clocks each selects.
# Table 8, W956A8MBYA rev A01-006 p. 21. 3-13 are RESERVED.
LEGAL_LATENCY = {0: (5, 133), 1: (6, 166), 2: (7, 200), 14: (3, 83), 15: (4, 100)}


class Shell:
    """One console session, with every exchange kept."""

    def __init__(self):
        self.link = soc_shell.Link.open(None)
        self.link.settle(0.05)
        self.transcript = []
        emit(f"console: {self.link.how}")
        # A bare Enter lands at a clean prompt whatever was half-typed.
        self.send("", budget=2.0)

    def send(self, command, budget):
        """One command, and everything up to the next prompt.

        `budget` is per command and stated at each call site: a bounded wait
        that expires returns whatever arrived, which is reported rather than
        retried. `read_until_prompt` returns on the prompt, so the budget only
        bites when the shell says nothing.
        """
        started = time.monotonic()
        self.link.write(command.encode() + b"\r")
        text = self.link.read_until_prompt(budget_s=budget).decode("ascii", "replace")
        elapsed = time.monotonic() - started
        self.transcript.append(f"--- {command or '<enter>'} "
                               f"({elapsed:.2f} s of {budget:g} s) ---\n{text}")
        if not text.strip():
            emit(f"  !! `{command}` returned NOTHING in {budget:g} s")
        return text

    def close(self):
        self.link.close()
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text("\n".join(self.transcript))
        emit(f"transcript -> {TRANSCRIPT.relative_to(ROOT)}")


def readback(shell, budget):
    """`bist status`'s CR0 line, as (raw, latency, fixed, drive), or None."""
    text = shell.send("bist status", budget)
    found = CR0.search(text)
    if not found:
        emit("  no CR0 line in `bist status` -- the whole reply follows")
        for line in text.splitlines():
            emit(f"    {line}")
        return None, text
    raw, latency, mode, drive = found.groups()
    return (int(raw, 16), int(latency), mode == "fixed", int(drive)), text


def rows_of(text, pattern):
    """Every result row in a sweep's output."""
    return [dict(latency=row["lat"], fixed=row["fixed"], drive=row["drive"],
                 clock=row["clk"], readclksel=row["sel"], errors=row["errors"],
                 words=row["words"], control_errors=row["control"],
                 verdict=row["verdict"])
            for row in (bist_rows.cell(m) for m in pattern.finditer(text))]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--passes", type=int, default=16,
                        help="passes per cell; 16 x 128 = 2048 words (default 16)")
    parser.add_argument("--skip-all", action="store_true",
                        help="stop after the latency sweep; skip the 4096-cell run")
    args = parser.parse_args()

    shell = Shell()
    try:
        # ---- step 0: what the engine says before anything has configured it.
        # Expected `0x0000`: `CONFIG_VERIFY` is only reached through a commanded
        # run, so this is the control for step 1 rather than a result.
        emit("")
        emit("step 0 -- readback BEFORE any run (expect 0x0000, nothing has configured yet)")
        # **Waits for**: the prompt after ~12 lines of status over a 115200 baud
        # console -- ~900 bytes, ~80 ms. **Multiplier**: ~12x, because the CSR
        # reads behind it are microseconds and the console is the whole cost.
        # **On expiry**: whatever arrived is printed and the step reports it.
        before, _ = readback(shell, budget=1.0)
        emit(f"  {before}")

        # ---- step 1: the READ path.
        emit("")
        emit("step 1 -- one cell at the power-on axes, then the readback")
        emit("  drive 3, dif, sel 0, latency 2 fixed -> CR0 should read 0x8f2f")
        # **Waits for**: one cell = two passes of `--passes` bursts, plus its
        # report. **Expected**: ~2 ms measured per cell at 64 passes; the report
        # is a few hundred bytes of console. **Multiplier**: ~100x on the console
        # cost, which is the only part that is not microseconds.
        # **On expiry**: the row is missing and the step says so.
        cell_a = shell.send("bist cell 3 dif 0 2", budget=3.0)
        for line in cell_a.splitlines():
            emit(f"    {line}")
        read_path, _ = readback(shell, budget=1.0)
        emit(f"  CR0 now reads {read_path}")

        # ---- step 2: the WRITE path. A DIFFERENT legal CR0.
        #
        # Latency code 15 = 4 clocks (100 MHz), drive 6. Both fields differ from
        # the power-on default, so a readback that still says 2/3 means the write
        # did not land even though the read path works.
        emit("")
        emit("step 2 -- a DIFFERENT legal CR0: drive 6, latency code 15 (4 clocks)")
        cell_b = shell.send("bist cell 6 dif 0 15", budget=3.0)
        for line in cell_b.splitlines():
            emit(f"    {line}")
        write_path, _ = readback(shell, budget=1.0)
        emit(f"  CR0 now reads {write_path}")

        verdict = "UNKNOWN"
        if read_path and write_path:
            raw_a, lat_a, _, drive_a = read_path
            raw_b, lat_b, _, drive_b = write_path
            if raw_a in (0x0000, 0xffff):
                verdict = f"READ PATH DEAD -- readback {raw_a:#06x}"
            elif raw_a == raw_b:
                verdict = (f"READ ok ({raw_a:#06x}) but WRITE DID NOT LAND -- "
                           f"CR0 unchanged after asking for drive 6 latency 15")
            elif (lat_b, drive_b) == (15, 6):
                verdict = (f"READ AND WRITE BOTH PROVEN -- {raw_a:#06x} -> "
                           f"{raw_b:#06x}, exactly what was asked for")
            else:
                verdict = (f"CR0 CHANGED {raw_a:#06x} -> {raw_b:#06x} but not to "
                           f"what was asked (wanted latency 15 drive 6, "
                           f"got latency {lat_b} drive {drive_b})")
        emit("")
        emit(f"  REGISTER PATH: {verdict}")

        # ---- step 3: the latency sweep, against the datasheet.
        emit("")
        emit(f"step 3 -- bist latency {args.passes}")
        emit("  datasheet legal: 0, 1, 2, 14, 15. RESERVED: 3-13.")
        emit("  at CK 85.7 the prediction is {0, 1, 2, 15}, 14 marginal (85.7 > 83)")
        # **Waits for**: 32 cells (16 codes x fix/var) and 32 report rows.
        # **Expected**: ~2 ms per cell = 64 ms of engine, plus ~3 kB of console
        # at 115200 = ~260 ms. **Multiplier**: ~25x. **On expiry**: the rows that
        # arrived are still parsed and the count is reported against 32.
        latency_text = shell.send(f"bist latency {args.passes}", budget=8.0)
        latency_rows = rows_of(latency_text, LATENCY_ROW)
        # `bist latency` prints a row for every cell, so an empty parse is the
        # parser -- and reads exactly like a board that reported nothing.
        if not latency_rows:
            bist_rows.require_rows(latency_text, f"bist latency {args.passes}")
        emit(f"  {len(latency_rows)} rows of 32")
        for row in latency_rows:
            flag = "legal" if row["latency"] in LEGAL_LATENCY else "RESERVED"
            emit(f"    lat {row['latency']:2} {'fix' if row['fixed'] else 'var'}  "
                 f"{flag:8}  errors {row['errors']:8}  words {row['words']:8}  "
                 f"control {row['control_errors']:8}  {row['verdict']}")
        passing = sorted({r["latency"] for r in latency_rows
                          if r["verdict"] == "PASS"})
        emit(f"  PASS set: {passing}")
        emit(f"  datasheet legal codes:  {sorted(LEGAL_LATENCY)}")

        if args.skip_all:
            return 0

        # ---- step 4: the full cross product.
        emit("")
        emit(f"step 4 -- bist all {args.passes}: 4096 cells")
        # **Waits for**: 4,096 cells, and the console is the whole cost -- the
        # engine runs a cell in ~2 ms and even an all-timeout run is ~1.1 ms of
        # spinning per cell, so ~8 s total either way.
        # **Expected**: only non-passes print. The worst case is every cell
        # failing with its `first bad` line, ~130 bytes each, 4,096 of them =
        # ~530 kB at 115200 = ~46 s; every cell TIMING OUT adds two timeout
        # lines and two fsm lines, ~370 bytes each = ~1.5 MB = ~132 s.
        # **Multiplier**: 1.15x on that worst case.
        # **On expiry**: the rows that arrived are tallied and the count is
        # reported against 4096, which distinguishes a slow run from a wedge.
        all_text = shell.send(f"bist all {args.passes}", budget=150.0)
        all_rows = rows_of(all_text, ALL_ROW)
        tally = bist_rows.summary(all_text)
        if tally:
            emit("  engine tally: {passed} pass, {failed} fail, {no_result} "
                 "no result of {total}".format(**tally))
        else:
            emit("  NO TALLY LINE -- the sweep did not finish inside the budget")
        emit(f"  {len(all_rows)} non-pass rows came back")
        # A tally claiming failures beside a parse that found no row is the
        # silent no-match, and it reads as a clean 4096-cell matrix.
        if tally and (tally["failed"] + tally["no_result"]) and not all_rows:
            bist_rows.require_rows(all_text, f"bist all {args.passes}")
        # The SHAPE, not the count: which latency codes the failures sit under,
        # and whether any axis is uniformly bad.
        by_latency = {}
        for row in all_rows:
            by_latency.setdefault(row["latency"], []).append(row)
        for latency in sorted(by_latency):
            rows = by_latency[latency]
            kinds = {}
            for row in rows:
                kinds[row["verdict"]] = kinds.get(row["verdict"], 0) + 1
            flag = "legal" if latency in LEGAL_LATENCY else "RESERVED"
            emit(f"    lat {latency:2} {flag:8}  {len(rows):4} non-pass: "
                 + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
    finally:
        shell.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
