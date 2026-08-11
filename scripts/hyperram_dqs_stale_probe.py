#!/usr/bin/env python3
#
# Is the leaked word a REGISTER READ's return word, and which register? #349.
# SPDX-License-Identifier: BSD-3-Clause

"""Board reads coming back as `0xff81ff81`/`0xffc1ffc1` -- from where?

    ./scripts/hyperram_dqs_stale_probe.py            # the discriminator
    ./scripts/hyperram_dqs_stale_probe.py --status   # just say what the board is

Two mechanisms produce those words and they want opposite fixes:

  A. the data burst ADDRESSES register space. Refuted in simulation --
     `hyperram_dqs_model_sim.py --stage config` has the device model decode the
     CA itself and report `is_register=0` for every data burst, with the control
     (register space forced on) caught. CS# High measured at 80 ns against a
     10 ns tCSHI at every boundary, so the "CS# never rises" reading is out too.
  B. the data burst addresses MEMORY and the read path hands back the word its
     data registers were still holding -- which, in the engine's own sequence, is
     `CONFIG_VERIFY_CR1`'s. Simulation cannot reach this: it is DQSBUFM, and no
     open model exists.

## The discriminator, and it needs no rebuild

The engine reads CR0 back and then CR1 back, in that order, through the same
path as the data. Under B the value a read returns is the PREVIOUS read's, so:

  * `CR1 part reports` should equal the CORRECT CR0 value, and
  * the word a failing cell reports as `got` should equal the CR1 value the
    firmware wrote for that cell's `dif`/`se` -- 0xff81 / 0xffc1, duplicated
    into both halves.

Under A the CR1 readback would be correct and only the data would be wrong.

So the run walks the `dif`/`se` axis and asks whether the leaked word TRACKS the
CR1 value commanded. That correlation is the measurement; `bist status`'s two
readback lines are the second, independent one.

Transcript -> `tmp/logs/hyperram-dqs-stale.txt`, log -> `tmp/logs/hyperram_dqs_stale_probe.log`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bist_rows  # noqa: E402
import soc_shell  # noqa: E402

TRANSCRIPT = ROOT / "tmp" / "logs" / "hyperram-dqs-stale.txt"
LOG = ROOT / "tmp" / "logs" / "hyperram_dqs_stale_probe.log"

# `firmware/cynthion-soc/src/bist.rs`: the two CR1 values the shell writes. Kept
# here as the EXPECTED correlate, not as a setting -- if the firmware changes
# them this probe must stop matching rather than quietly track it.
CR1_BY_CLOCK = {"dif": 0xFF81, "se": 0xFFC1}

FIRST_BAD = bist_rows.FIRST_BAD
CR0_BACK = bist_rows.CR0_BACK
CR1_BACK = bist_rows.CR1_BACK


def expected_cr0(drive, latency, fixed):
    """What the part must report for these axes. Table 8, rev A01-006 p.21."""
    return 0x8F07 | (drive << 12) | (latency << 4) | (0x8 if fixed else 0)


class Board:
    def __init__(self, node=None):
        # `node` bypasses the forwarding service on port 9000. Worth having:
        # the service survives a reconfigure that its serial handle does not,
        # and every read then returns EMPTY -- which reads as a board that
        # booted and hung, not as a link that is no longer connected to it.
        self.link = soc_shell.Link.open(node)
        self.transcript = []
        # The first prompt after a configure is the banner's, so an unprimed
        # link returns before its own reply.
        self.link.write(b"\r")
        self.link.read_until_prompt(budget_s=3)

    def send(self, command, budget=20):
        self.link.write(command.encode() + b"\r")
        text = self.link.read_until_prompt(budget_s=budget).decode("ascii", "replace")
        self.transcript.append(f"$ {command}\n{text}")
        return text

    def close(self):
        self.link.close()
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text("\n".join(self.transcript))


# A row and the first-bad line beneath it are needed TOGETHER, so the sweep is
# parsed as a pair rather than through `bist_rows.rows`.
LAT_ROW = bist_rows.ROW

# `bist latency` sweeps at `dif`, so every cell in it writes this CR1.
# `firmware/cynthion-soc/src/bist.rs:latency`: `single_ended_clock: false`.
LATENCY_SWEEP_CR1 = CR1_BY_CLOCK["dif"]


def latency_sweep(board, log, passes):
    """Every mistimed cell's leaked word, against the CR1 the cell wrote.

    The prediction under mechanism B: a cell whose CR0 latency does not match the
    controller's built-in wait has its read window in the wrong place, so the read
    returns whatever the data registers were holding -- and the last thing they
    captured is `CONFIG_VERIFY_CR1`'s word, `{CR1, CR1}`.

    Under any mechanism where the memory read is simply DISPLACED, the leaked word
    is a memory pattern instead, and the CR1 value never appears.
    """
    # 240 s: 32 cells x 2 passes (real + control) x `passes` x 128 words at
    # ~2 ms per cell is under 1 s of transfer, but a mistimed cell parks the
    # engine until its 256-cycle stall escape fires, 32 times over, and the
    # console prints a row and a first-bad line each at 115200 baud. Measured at
    # ~30 s for `--passes 1`; 8x that, because a sweep that trips the escape on
    # every cell is exactly the case being measured and must reach its report.
    text = board.send(f"bist latency {passes}", 240)
    want = (LATENCY_SWEEP_CR1 << 16) | LATENCY_SWEEP_CR1

    lines = text.splitlines()
    rows = []
    for i, line in enumerate(lines):
        m = LAT_ROW.match(line)
        if not m:
            continue
        bad = None
        for j in range(i + 1, min(i + 3, len(lines))):
            bad = FIRST_BAD.search(lines[j])
            if bad:
                break
        cell = bist_rows.cell(m)
        rows.append(dict(latency=cell["lat"], mode=cell["mode"],
                         errors=cell["errors"], words=cell["words"],
                         verdict=cell["verdict"],
                         got=int(bad["got"], 0) if bad else None))
    if not rows:
        bist_rows.require_rows(text, f"bist latency {passes}")

    log.info("`bist latency` writes CR1 = %#06x on every cell, so the word this "
             "predicts is %#010x", LATENCY_SWEEP_CR1, want)
    log.info("")
    log.info("lat  mode   errors    words  leaked word   is it {CR1,CR1}?")
    hits = clean = other = 0
    for row in rows:
        if row["errors"] == 0:
            clean += 1
            mark = "(clean -- no leak to explain)"
        elif row["got"] == want:
            hits += 1
            mark = "YES"
        else:
            other += 1
            mark = "no"
        log.info("%3d  %-4s  %7d  %7d  %-12s  %s", row["latency"], row["mode"],
                 row["errors"], row["words"],
                 f"{row['got']:#010x}" if row["got"] is not None else "-", mark)

    log.info("")
    log.info("%d cells: %d clean, %d leaked exactly {CR1,CR1}, %d leaked "
             "something else", len(rows), clean, hits, other)
    if hits and not clean:
        log.info("NOT DISCRIMINATING -- no cell was clean, so nothing here "
                 "separates 'the mistimed window returns the held word' from "
                 "'this build never reads memory at all'")
    elif hits:
        log.info("the leak is a REGISTER READ's return word and it appears only "
                 "where the part's latency and the controller's wait disagree")
    else:
        log.info("no cell leaked the CR1 word: mechanism B is not what this "
                 "sweep produced, whatever produced the board's earlier rows")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drive", type=int, default=3)
    ap.add_argument("--latency", type=int, default=2)
    ap.add_argument("--sel", type=int, default=0)
    ap.add_argument("--status", action="store_true",
                    help="print what the board is and stop")
    ap.add_argument("--latency-sweep", action="store_true",
                    help="run `bist latency` and tabulate every row's leaked "
                         "word against the CR1 value that cell wrote")
    ap.add_argument("--passes", type=int, default=1)
    ap.add_argument("--node", default=None,
                    help="serial device, bypassing the forwarding "
                         "service on port 9000")
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, mode="w")])
    log = logging.getLogger()

    board = Board(args.node)
    try:
        rung = board.send("bist ck", 8)
        found = bist_rows.RUNG.search(rung)
        if not found:
            raise SystemExit(f"no CK rung reported -- is this the DQS SoC "
                             f"bitstream? Reply was:\n{rung}")
        log.info("CK %s MHz", found["mhz"])
        if args.status:
            log.info("%s", board.send("bist status", 8))
            return 0

        if args.latency_sweep:
            return latency_sweep(board, log, args.passes)

        log.info("")
        log.info("clk  CR1 written  CR0 reports  CR1 reports  errors/words  "
                 "leaked word     tracks CR1?")

        verdict = []
        for clock in ("dif", "se"):
            command = (f"bist cell {args.drive} {clock} {args.sel} "
                       f"{args.latency} fix")
            text = board.send(command, 30)
            row = bist_rows.require_rows(text, command)
            bad = FIRST_BAD.search(text)
            status = board.send("bist status", 8)
            cr0 = CR0_BACK.search(status)
            cr1 = CR1_BACK.search(status)
            cr0_v = int(cr0.group(1), 0) if cr0 else -1
            cr1_v = int(cr1.group(1), 0) if cr1 else -1
            want_cr1 = CR1_BY_CLOCK[clock]
            got = int(bad["got"], 0) if bad else -1
            # Both halves equal to the CR1 value is the register-read return
            # word, measured in simulation as `{cr, cr}`.
            tracks = got >= 0 and got == ((want_cr1 << 16) | want_cr1)
            verdict.append((clock, cr0_v, cr1_v, got, tracks))
            log.info("%-4s %#06x       %#06x       %#06x       %s/%s  %-14s  %s",
                     clock, want_cr1, cr0_v, cr1_v,
                     row[-1]["errors"], row[-1]["words"],
                     f"{got:#010x}" if got >= 0 else "(none -- clean)",
                     "YES" if tracks else "no")

        log.info("")
        want0 = expected_cr0(args.drive, args.latency, True)
        one_late = [c for c, cr0_v, cr1_v, _, _ in verdict if cr1_v == want0]
        tracking = [c for c, _, _, _, t in verdict if t]

        if len(tracking) == 2:
            log.info("BOTH clock modes leaked a word equal to their OWN CR1, "
                     "duplicated into both halves. That word is a register "
                     "read's return value; it cannot come from memory.")
        elif tracking:
            log.info("ONE of the two leaked its CR1 (%s). A correlation on one "
                     "value is not a correlation.", ",".join(tracking))
        else:
            log.info("neither leaked its CR1 -- the read path was intact for "
                     "both, so this run cannot discriminate. Run it after the "
                     "trigger (`bist latency`), not before.")

        if one_late:
            log.info("`CR1 part reports` equals the CORRECT CR0 value (%#06x) "
                     "for %s -- the register readback is ONE TRANSACTION "
                     "BEHIND, which is the same displacement seen in the data.",
                     want0, ",".join(one_late))
        else:
            log.info("`CR1 part reports` is not CR0's value, so the register "
                     "readback is not displaced in this run.")
    finally:
        board.close()
        log.info("")
        log.info("transcript -> %s", TRANSCRIPT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
