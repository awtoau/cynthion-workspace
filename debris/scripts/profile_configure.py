#!/usr/bin/env python3
"""
Locate where `apollo configure` actually spends its time.

Performance work on the JTAG path has repeatedly been aimed at the SPI clocking
loop. The marginal cost of that loop is measurable (scripts/probe_scan_effect.py)
and sits at ~0.69 us/byte against a 0.667 us/byte wire floor at 12 MHz SCK, so
clocking accounts for only ~69 ms of a ~1000 ms configure. This script accounts
for the rest, so that effort is aimed at whatever actually dominates.

It wraps the JTAG chain's transfer primitives with counters and timers, runs one
real configure, and reports a breakdown by call site. Nothing is simulated: the
timings come from a genuine configure that is then verified via the ECP5 status
register (DONE set, no FAIL, BSE error code 0), because a configure that silently
did nothing would otherwise look extremely fast.

Logs to ./tmp/logs/profile_configure.log as well as the terminal.

Usage:
    python3.15t scripts/profile_configure.py --bitstream ecp5-test/led_patterns.bit
"""

import argparse
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
LOG_PATH = LOG_DIR / "profile_configure.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

STATUS_DONE = 1 << 8
STATUS_FAIL = 1 << 13


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("profile_configure")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%dT%H:%M:%S%z")
    fh = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bitstream", default="ecp5-test/led_patterns.bit")
    args = parser.parse_args()

    logger = setup_logging()

    from apollo_fpga import ApolloDebugger
    from apollo_fpga.ecp5 import ECP5_JTAGProgrammer

    path = (ROOT / args.bitstream) if not Path(args.bitstream).is_absolute() else Path(args.bitstream)
    payload = path.read_bytes()

    logger.info("=" * 72)
    logger.info("profile_configure")
    logger.info(f"  bitstream: {args.bitstream} ({len(payload)} bytes)")

    debugger = ApolloDebugger()
    try:
        chain = debugger.jtag
        chain.initialize()

        stats = defaultdict(lambda: {"calls": 0, "bytes": 0, "seconds": 0.0})

        # Wrap the two primitives every JTAG operation funnels through. Attribute
        # each call to the largest payload it carries so the bitstream burst is
        # separable from the many small control shifts around it.
        def wrap(name, fn):
            def wrapped(*a, **kw):
                nbytes = 0
                for v in list(a) + list(kw.values()):
                    if isinstance(v, (bytes, bytearray)):
                        nbytes = max(nbytes, len(v))
                start = time.perf_counter()
                try:
                    return fn(*a, **kw)
                finally:
                    elapsed = time.perf_counter() - start
                    bucket = f"{name}:bulk" if nbytes > 1024 else f"{name}:small"
                    s = stats[bucket]
                    s["calls"] += 1
                    s["bytes"] += nbytes
                    s["seconds"] += elapsed
            return wrapped

        for attr in ("_shift_data", "shift_data", "run_test", "_pause_to_run_test"):
            if hasattr(chain, attr):
                setattr(chain, attr, wrap(attr, getattr(chain, attr)))

        programmer = ECP5_JTAGProgrammer(chain)

        wall_start = time.perf_counter()
        programmer.configure(payload)
        wall = time.perf_counter() - wall_start

        # A configure that did nothing is indistinguishable from a fast one on a
        # stopwatch, so confirm the device actually accepted the bitstream.
        status = programmer._read_status()
        done = bool(status & STATUS_DONE)
        fail = bool(status & STATUS_FAIL)
        bse = (status >> 23) & 0x7
        verdict = "PASS" if (done and not fail and bse == 0) else "FAIL"

        logger.info(f"  configure wall time: {wall*1000:.1f} ms")
        logger.info(f"  status=0x{status:08x} DONE={int(done)} FAIL={int(fail)} "
                    f"BSE_ERR={bse}  {verdict}")
        if verdict != "PASS":
            logger.error("  configure did NOT verify -- timings below are meaningless")
            return 1

        logger.info(f"  {'bucket':<28} {'calls':>7} {'bytes':>10} {'ms':>9} {'%wall':>7}")
        accounted = 0.0
        for bucket in sorted(stats, key=lambda b: -stats[b]["seconds"]):
            s = stats[bucket]
            accounted += s["seconds"]
            logger.info(f"  {bucket:<28} {s['calls']:>7} {s['bytes']:>10} "
                        f"{s['seconds']*1000:>9.1f} {s['seconds']/wall*100:>6.1f}%")
        logger.info(f"  {'accounted':<28} {'':>7} {'':>10} {accounted*1000:>9.1f} "
                    f"{accounted/wall*100:>6.1f}%")
        logger.info(f"  {'unaccounted (host/idle)':<28} {'':>7} {'':>10} "
                    f"{(wall-accounted)*1000:>9.1f} {(wall-accounted)/wall*100:>6.1f}%")

    finally:
        try:
            debugger.close()
        except Exception:
            pass

    logger.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
