#!/usr/bin/env python3
#
# Did the DQS path demonstrate DQS, and does the capture phase carry information? #349, #343.
# SPDX-License-Identifier: BSD-3-Clause

"""BURSTDET and the `readclksel` axis, which nothing else in the rig reports.

    ./scripts/hyperram_dqs_evidence.py                 # 8 phases, 3 repeats
    ./scripts/hyperram_dqs_evidence.py --repeat 5
    ./scripts/hyperram_dqs_evidence.py --drive 3 --latency 2
    ./scripts/hyperram_dqs_evidence.py --soak          # does the state survive a sweep?

## Run it on a FRESHLY RECONFIGURED board, every time

Measured 2026-08-10 at CK 120: a cell reading 0 errors of 8192 words reads 8192
of 8192 after `bist latency` or `bist all` has run, and stays there. Only an FPGA
reconfigure clears it -- `reset` does not. So a sweep measures a part that the
sweep itself is breaking, and rows taken late in one are not comparable with
rows taken early. `--soak` is the reproducer.

The in-band check is the CR0 readback: the engine's `CONFIG_VERIFY` reads it
through the same path as the data, and its correct value is known from the
commanded axes. `CR0 = 0x8F0F | drive<<12 | latency<<4 | fixed<<3`.

## The two gaps this fills

1. **BURSTDET is not in any printed line.** `bist status` prints the STATUS word
   as hex and decodes only bits 0-3. `dll_locked`, `dll_ready` and
   `burstdet_seen` are bits 4, 5 and 6 -- put there by
   `hyperram_ceiling_top.py`'s `status_extra` through `probes/bist.py:174`.
   A DQS pass with BURSTDET clear has not demonstrated DQS.

2. **`readclksel` has no readback.** `hyperram_probe.py`'s `sel` register is
   `csr.action.W`, access `"w"`. So `hyperram_axis_liveness.py`'s method -- command
   two values, read both back -- cannot reach it, and the axis needs a different
   argument. This one is behavioural: sweep the phase with every other axis held,
   repeat, and ask whether the spread ACROSS phases exceeds the spread WITHIN one
   phase. If it does not, the axis is not discriminating and the run cannot tell
   a dead wire from a real null result.

Transcript -> `tmp/logs/hyperram-dqs-evidence.txt`, log -> `tmp/logs/hyperram_dqs_evidence.log`.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_shell  # noqa: E402

TRANSCRIPT = ROOT / "tmp" / "logs" / "hyperram-dqs-evidence.txt"
LOG = ROOT / "tmp" / "logs" / "hyperram_dqs_evidence.log"

# `      real   : status 0x4078f7 busy=1 done=1 error=1 negative=0`. The REAL
# pass, not `bist status`'s "at rest" -- the control runs second, so at rest is
# the CONTROL's status and its BURSTDET is not the measurement's.
STATUS_REAL = re.compile(r"real\s*:\s*status\s+(0x[0-9a-fA-F]+)")
# `000197.956      3  dif    0      8192      8192      8192  fail`
CELL = re.compile(r"^\s*\d+\.\d+\s+(\d)\s+(dif|se)\s+(\d)"
                  r"\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S.*?)\s*$")
# `      first bad: index 0x0  got 0x0000003f  want 0xffbf0000`
FIRST_BAD = re.compile(r"first bad: index (0x[0-9a-f]+)\s+got (0x[0-9a-f]+)"
                       r"\s+want (0x[0-9a-f]+)")
CR0_BACK = re.compile(r"CR0\s+part reports\s+(0x[0-9a-fA-F]+)")


def expected_cr0(drive, latency, fixed):
    """What the part must report back for these axes. Table 8, rev A01-006 p.21."""
    return 0x8F07 | (drive << 12) | (latency << 4) | (0x8 if fixed else 0)

# STATUS bit positions. Bits 0-3 are the harness's own; 4 up are `status_extra`
# from `hyperram_ceiling_top.py:1180`, through `probes/bist.py:174`.
#
# The layout is CHECKED, not assumed: bits 8-15 carry the CK as built, so
# `(status >> 8) & 0xff` must equal the rung `bist ck` reports. A bit map that
# is off by one fails that and says so.
BITS = {"busy": 0, "done": 1, "error": 2, "negative": 3,
        "dll_locked": 4, "dll_ready": 5, "burstdet": 6, "bad_seen": 7}


def decode(word):
    fields = {name: (word >> shift) & 1 for name, shift in BITS.items()}
    fields["ck_mhz"] = (word >> 8) & 0xFF
    fields["passes"] = word >> 16
    return fields


class Board:
    def __init__(self, log):
        self.link = soc_shell.Link.open(None)
        self.log = log
        self.transcript = []
        # The first prompt after a configure is the banner's, so an unprimed
        # link returns before its own reply. Same reason as hyperram_ck_sweep.py.
        self.link.write(b"\r")
        self.link.read_until_prompt(budget_s=3)

    def send(self, command, budget=8):
        self.link.write(command.encode() + b"\r")
        text = self.link.read_until_prompt(budget_s=budget).decode("ascii", "replace")
        self.transcript.append(f"$ {command}\n{text}")
        return text

    def close(self):
        self.link.close()
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text("\n".join(self.transcript))


def cell(board, drive, clock, sel, latency, mode):
    """One cell, and the STATUS word of its REAL pass.

    Passes are not ours to choose: `shell/bist.rs` hands `bist cell` a literal
    `DEFAULT_PASSES` (64), so a sixth word would be parsed and dropped. 64 x 128
    burst words = 8192 words per cell.
    """
    text = board.send(f"bist cell {drive} {clock} {sel} {latency} {mode}", 20)
    rows = [m for m in (CELL.match(line) for line in text.splitlines()) if m]
    found = STATUS_REAL.search(text)
    if not rows or not found:
        raise SystemExit(
            "no cell row or no `real: status` line parsed. The PARSE is the "
            "first suspect -- that has been right five times on this rig. "
            f"Reply was:\n{text}")
    row = rows[-1]
    word = int(found.group(1), 0)
    bad = FIRST_BAD.search(text)
    return dict(sel=int(row.group(3)),
                errors=int(row.group(4)), words=int(row.group(5)),
                control=int(row.group(6)), verdict=row.group(7),
                status=word,
                got=bad.group(2) if bad else "", want=bad.group(3) if bad else "",
                **decode(word))


def soak(board, log, ck_reported):
    """Does the read path survive its own sweep? Probe, sweep, probe again."""
    # The probe is one cell plus its CR0 readback. All three of errors, WORDS
    # and CR0 must be right:
    #   * zero errors over a short cell is the rig's oldest flattering answer --
    #     one sweep row scored PASS on 17 words of 512 (#226), and the first
    #     version of this probe repeated it, calling `0 of 111` intact;
    #   * a cell can read clean while the REGISTER path is slipped, and the
    #     reverse, so neither alone says the read path is whole.
    probe_axes = (0, "se", 2, 0, "fix")
    # 64 passes x 128 burst words, both fixed in the firmware.
    FULL = 8192

    def probe(tag):
        got = cell(board, probe_axes[0], probe_axes[1], probe_axes[2],
                   probe_axes[3], probe_axes[4])
        back = CR0_BACK.search(board.send("bist status"))
        cr0 = int(back.group(1), 0) if back else -1
        want = expected_cr0(probe_axes[0], probe_axes[3], probe_axes[4] == "fix")
        short = got["words"] < FULL
        whole = got["errors"] == 0 and cr0 == want and not short
        log.info("  %-34s errors %6d of %-6d CR0 %#06x want %#06x  %s",
                 tag, got["errors"], got["words"], cr0, want,
                 "SHORT -- no result" if short else
                 "INTACT" if whole else "SLIPPED")
        return whole

    log.info("CK %g MHz. Run this straight after a reconfigure -- `reset` does "
             "not clear the slipped state.", ck_reported)
    log.info("")
    probe("baseline")
    for command in ("bist latency 8", "bist sweep 8"):
        board.send(command, 120)
        if not probe(f"after `{command}`"):
            log.info("")
            log.info("  ^^ that sweep BROKE the read path. Every row it printed "
                     "after the break measures a part in a state the sweep put "
                     "it in, so the rows are not comparable with each other.")
            return 1
    log.info("")
    log.info("  the read path survived both sweeps")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--repeat", type=int, default=3,
                        help="runs per phase; 1 cannot separate a dead axis from noise")
    parser.add_argument("--drive", type=int, default=3)
    parser.add_argument("--latency", type=int, default=2)
    parser.add_argument("--mode", default="fix", choices=("fix", "var"))
    parser.add_argument("--clock", default="dif", choices=("dif", "se"))
    parser.add_argument("--soak", action="store_true",
                        help="probe, run a sweep, probe again -- does the read "
                             "path survive its own sweep?")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, mode="w")])
    log = logging.getLogger()

    board = Board(log)
    try:
        # The CK the bitstream carries, so the STATUS bit map can be CHECKED
        # against a value already known rather than assumed correct.
        rung = board.send("bist ck")
        found = re.search(r"rung\s+\d+\s+([\d.]+)\s+MHz", rung)
        if not found:
            raise SystemExit(f"no CK rung reported. Reply was:\n{rung}")
        ck_reported = float(found.group(1))

        if args.soak:
            return soak(board, log, ck_reported)

        log.info("CK %g MHz, drive %d, clock %s, latency %d %s, %d repeats",
                 ck_reported, args.drive, args.clock, args.latency, args.mode,
                 args.repeat)
        log.info("")
        log.info("sel  run  errors    words  control  verdict  "
                 "dll_lock dll_rdy burstdet  status      got/want")

        table = {}
        for sel in range(8):
            table[sel] = []
            for run in range(args.repeat):
                got = cell(board, args.drive, args.clock, sel,
                           args.latency, args.mode)
                if got["sel"] != sel:
                    raise SystemExit(
                        f"asked for sel {sel}, the row says {got['sel']}")
                if got["ck_mhz"] != round(ck_reported):
                    raise SystemExit(
                        f"STATUS bits 8-15 say CK {got['ck_mhz']} MHz, `bist ck` "
                        f"says {ck_reported:g}. The bit map is wrong, so every "
                        "flag decoded from this word is wrong too.")
                table[sel].append(got)
                log.info("%3d  %3d  %6d  %7d  %7d  %-7s  %8d %7d %8d  %#08x  %s/%s",
                         sel, run, got["errors"], got["words"], got["control"],
                         got["verdict"], got["dll_locked"], got["dll_ready"],
                         got["burstdet"], got["status"], got["got"], got["want"])

        log.info("")
        log.info("=== BURSTDET")
        seen = [r["burstdet"] for rows in table.values() for r in rows]
        locked = [r["dll_locked"] for rows in table.values() for r in rows]
        ready = [r["dll_ready"] for rows in table.values() for r in rows]
        log.info("  dll_locked set in %d of %d runs", sum(locked), len(locked))
        log.info("  dll_ready  set in %d of %d runs", sum(ready), len(ready))
        log.info("  burstdet   set in %d of %d runs", sum(seen), len(seen))
        if not any(seen):
            log.info("  BURSTDET NEVER SET. DQSBUFM saw no strobe inside its "
                     "window, so nothing here demonstrates DQS -- a cell that "
                     "passes did so on a fixed-latency count that landed right.")
        elif all(seen):
            log.info("  BURSTDET always set -- the strobe was found at every phase.")
        else:
            log.info("  BURSTDET set at some phases only, which is itself the "
                     "capture-phase axis carrying information.")

        log.info("")
        log.info("=== readclksel, which has NO readback (`sel` is csr.action.W)")
        log.info("  so the argument is behavioural: does the spread ACROSS "
                 "phases exceed the spread WITHIN one?")
        within = 0
        for sel, rows in table.items():
            counts = [r["errors"] for r in rows]
            span = max(counts) - min(counts)
            within = max(within, span)
            log.info("    sel %d  errors %s  within-phase span %d",
                     sel, counts, span)
        means = {sel: sum(r["errors"] for r in rows) / len(rows)
                 for sel, rows in table.items()}
        across = max(means.values()) - min(means.values())
        log.info("  worst within-phase span %d", within)
        log.info("  across-phase span of the means %.1f", across)

        distinct_burstdet = len({tuple(r["burstdet"] for r in rows)
                                 for rows in table.values()}) > 1
        if across > within and across > 0:
            log.info("  LIVE -- the phase moves the result by more than the "
                     "run-to-run noise")
        elif distinct_burstdet:
            log.info("  LIVE -- phases differ in BURSTDET, which only the "
                     "DQSBUFM read window can do")
        elif within > 0:
            log.info("  NOT PROVEN -- run-to-run noise is as large as the "
                     "phase effect, so this run cannot separate a dead axis "
                     "from a real null")
        else:
            log.info("  NOT PROVEN -- every phase gave an identical, stable "
                     "result. Nothing here distinguishes a connected axis with "
                     "no effect from a disconnected one.")
    finally:
        board.close()
        log.info("")
        log.info("transcript -> %s", TRANSCRIPT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
