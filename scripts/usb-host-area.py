#!/usr/bin/env python3
"""Measure the fabric cost of GUH USB-host engines on Cynthion r1.4.

Background
----------
`docs/usb-host-proposal.md` argues that USB host mode on Cynthion is a port
job rather than a from-scratch gateware effort, because apfaudio/guh already
implements a USB2 high-speed host engine on top of LUNA and already builds
for CynthionPlatformRev1D4 in its own CI.

The open question for the proposal is area: an LFE5U-12F has 24288 LUTs, and
the device stack plus a RISC-V CPU already claims a large share. This script
synthesises the GUH examples for the Cynthion platform and reports the LUT
and BRAM totals that nextpnr prints, so the proposal quotes measured numbers
instead of guesses.

No hardware is touched -- this is synthesis and place-and-route only. Nothing
is uploaded to a board.

Usage
-----
    scripts/usb-host-area.py --guh-dir tmp/host-research/guh

Progress and the full toolchain logs land in tmp/logs/usb-host-area.log.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AEST = timezone(timedelta(hours=10))

WORKSPACE = Path(__file__).resolve().parent.parent
LOG_DIR = WORKSPACE / "tmp" / "logs"
LOG_PATH = LOG_DIR / "usb-host-area.log"

CYNTHION_PLATFORM = "cynthion.gateware.platform:CynthionPlatformRev1D4"
EXAMPLES = ("midi_host", "keyboard_host", "msc_host")

log = logging.getLogger("usb-host-area")


def setup_logging(verbose: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    # Mandatory file log: everything, including the toolchain chatter the
    # console suppresses.
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    )
    log.addHandler(handler)


def now() -> str:
    return datetime.now(AEST).isoformat(timespec="seconds")


# nextpnr-ecp5 reports utilisation as e.g. "Info: TRELLIS_COMB:  4712/24288    19%"
_UTIL_RE = re.compile(
    r"^\s*Info:\s+(?P<cell>[A-Z0-9_]+):\s+(?P<used>\d+)/(?P<total>\d+)\s+(?P<pct>\d+)%",
    re.MULTILINE,
)

# Cells worth surfacing; everything else is noise for an area discussion.
_CELLS_OF_INTEREST = ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "MULT18X18D", "TRELLIS_IO")


def parse_utilisation(text: str) -> dict[str, dict[str, int]]:
    """Pull the cell-utilisation table out of nextpnr's output."""
    found: dict[str, dict[str, int]] = {}
    for match in _UTIL_RE.finditer(text):
        cell = match.group("cell")
        if cell not in _CELLS_OF_INTEREST:
            continue
        # nextpnr prints the table more than once; the last one is post-pack.
        found[cell] = {
            "used": int(match.group("used")),
            "total": int(match.group("total")),
            "percent": int(match.group("pct")),
        }
    return found


def build_example(example: str, guh_dir: Path, python: str) -> dict:
    """Synthesise one GUH example for Cynthion and return its area figures."""
    log.info("[%s] building for Cynthion r1.4 ...", example)

    script = guh_dir / "examples" / f"{example}.py"
    if not script.is_file():
        return {"example": example, "ok": False, "error": f"missing {script}"}

    env = dict(os.environ)
    env["LUNA_PLATFORM"] = CYNTHION_PLATFORM
    # Keep each build's artifacts separate so nextpnr logs do not overwrite.
    build_dir = WORKSPACE / "tmp" / "host-research" / "build" / example
    build_dir.mkdir(parents=True, exist_ok=True)
    env["BUILD_DIR"] = str(build_dir)

    # --dry-run stops short of touching a board; we only want synth + pnr.
    cmd = [python, str(script), "--dry-run", "--keep-files"]
    log.debug("[%s] cmd: %s", example, " ".join(cmd))

    proc = subprocess.run(
        cmd,
        cwd=str(guh_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    log.debug("[%s] toolchain output:\n%s", example, output)

    # top_level_cli writes into ./build by default; search both locations for
    # whatever log nextpnr left behind.
    util = parse_utilisation(output)
    if not util:
        for candidate in list(build_dir.rglob("*.log")) + list(
            (guh_dir / "build").rglob("*.log")
        ):
            util = parse_utilisation(candidate.read_text(errors="replace"))
            if util:
                log.debug("[%s] utilisation recovered from %s", example, candidate)
                break

    result = {
        "example": example,
        "ok": proc.returncode == 0 and bool(util),
        "returncode": proc.returncode,
        "utilisation": util,
    }
    if not util:
        # Surface the tail of the output so a failure is diagnosable from the log.
        result["error"] = "no utilisation table found"
        result["output_tail"] = output[-2000:]
        log.warning("[%s] no utilisation table; see %s", example, LOG_PATH)
    else:
        comb = util.get("TRELLIS_COMB", {})
        bram = util.get("DP16KD", {})
        log.info(
            "[%s] LUT %s/%s (%s%%), BRAM %s/%s",
            example,
            comb.get("used", "?"),
            comb.get("total", "?"),
            comb.get("percent", "?"),
            bram.get("used", "?"),
            bram.get("total", "?"),
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--guh-dir",
        default=str(WORKSPACE / "tmp" / "host-research" / "guh"),
        help="checkout of apfaudio/guh",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter that has luna, cynthion and guh importable",
    )
    parser.add_argument(
        "--examples",
        nargs="*",
        default=list(EXAMPLES),
        help=f"which examples to build (default: {' '.join(EXAMPLES)})",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    log.info("usb-host-area starting at %s", now())
    log.info("log file: %s", LOG_PATH)

    guh_dir = Path(args.guh_dir).resolve()
    if not guh_dir.is_dir():
        log.error("GUH checkout not found: %s", guh_dir)
        log.error("clone it with: git clone https://github.com/apfaudio/guh")
        return 2
    log.info("GUH checkout: %s", guh_dir)

    results = [build_example(ex, guh_dir, args.python) for ex in args.examples]

    log.info("")
    log.info("=== Area summary (LFE5U-12F, 24288 LUT / 56 BRAM) ===")
    for r in results:
        if r["ok"]:
            comb = r["utilisation"].get("TRELLIS_COMB", {})
            bram = r["utilisation"].get("DP16KD", {})
            log.info(
                "  %-16s %6s LUT (%2s%%)  %3s BRAM",
                r["example"],
                comb.get("used", "?"),
                comb.get("percent", "?"),
                bram.get("used", "?"),
            )
        else:
            log.info("  %-16s FAILED: %s", r["example"], r.get("error", "?"))

    out = WORKSPACE / "tmp" / "host-research" / "area-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated": now(), "results": results}, indent=2))
    log.info("")
    log.info("results written to %s", out)

    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
