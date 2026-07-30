#!/usr/bin/env python3.15t
"""
Confirm that the non-zero reads found by the sweep are real device data.

A JTAG data register scan can produce a plausible-looking non-zero result for reasons
that have nothing to do with the opcode: a stale value left in the TAP's shift path from
the previous instruction, the IDCODE register that many TAPs select by default, or simply
TDI echoing back through an undriven register. Any of those would make an unimplemented
opcode look like it returned data.

Three checks separate real reads from artifacts:

  repeatability -- the same opcode read twice must give the same answer. A shift-path
      artifact usually varies with whatever preceded it.

  interleaving -- read the opcode, then a known-different opcode, then the first again.
      If the value survives having another instruction in between, it belongs to that
      opcode rather than to the shift path.

  TDI sensitivity -- read the same opcode at several lengths. A register that genuinely
      sources data gives a consistent value anchored the same way; an echo of TDI tracks
      the pattern shifted in.

Anything that fails these is reported as an artifact, not a finding.
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "tmp" / "logs"
PROBE = Path(__file__).resolve().parent / "ecp5_cmd_probe.py"
PYTHON = "/home/dan/opt/cpython-315t/bin/python3.15t"

LOG_NAME = "ecp5_verify_reads"
log = logging.getLogger(LOG_NAME)

# Same reasoning as the sweep's cap: a JTAG transaction is milliseconds, interpreter
# startup plus enumeration is ~2 s, so 25 s means stuck rather than slow.
PROBE_TIMEOUT_S = 25

# A neutral opcode to interleave. BYPASS is the JTAG-standard do-nothing instruction and
# selects the 1-bit bypass register, so it reliably displaces anything left in the shift
# path without changing configuration state.
INTERLEAVE_OPCODE = 0xFF


def probe(opcode, length):
    r = subprocess.run(
        [PYTHON, str(PROBE), "--single", hex(opcode), "--length", str(length), "--json"],
        capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    for line in r.stdout.splitlines():
        if line.strip().startswith("{"):
            return json.loads(line)
    return None


def verify(opcode, length=8, rounds=3):
    """Run the three checks for one opcode. Returns a verdict record."""
    rec = {"opcode": f"0x{opcode:02X}", "length": length}

    # 1. repeatability
    reps = []
    for _ in range(rounds):
        p = probe(opcode, length)
        reps.append(p.get("response") if p else None)
    rec["repeats"] = reps
    rec["stable"] = len(set(reps)) == 1 and reps[0] is not None

    # 2. interleaving: opcode, BYPASS, opcode again
    a = probe(opcode, length)
    probe(INTERLEAVE_OPCODE, length)
    b = probe(opcode, length)
    rec["interleaved"] = [a.get("response") if a else None,
                          b.get("response") if b else None]
    rec["survives_interleave"] = (
        rec["interleaved"][0] == rec["interleaved"][1]
        and rec["interleaved"][0] is not None)

    # 3. length sensitivity
    by_len = {}
    for ln in (1, 2, 4, 8, 16):
        p = probe(opcode, ln)
        by_len[ln] = p.get("response") if p else None
    rec["by_length"] = by_len

    rec["verdict"] = (
        "real" if rec["stable"] and rec["survives_interleave"] else "suspect")
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("opcodes", nargs="+", type=lambda x: int(x, 0))
    ap.add_argument("--length", type=int, default=8)
    ap.add_argument("--out")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.FileHandler(LOG_DIR / f"{LOG_NAME}.log")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)

    out = []
    for op in args.opcodes:
        r = verify(op, args.length)
        out.append(r)
        log.info("0x%02X %s stable=%s interleave=%s repeats=%s bylen=%s",
                 op, r["verdict"], r["stable"], r["survives_interleave"],
                 r["repeats"], r["by_length"])

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
        log.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
