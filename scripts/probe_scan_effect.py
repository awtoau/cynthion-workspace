#!/usr/bin/env python3
"""
Positive control for the SCAN timing harness.

A SCAN that is refused (stalled) or that clocks nothing is indistinguishable, on a
stopwatch, from one that is merely fast -- and a bogus "fast" number is exactly the
failure mode this performance work has to avoid. Before trusting any SCAN timing,
this checks that SCAN is doing observable work:

  1. The request must not stall.
  2. Scan cost must scale with the number of bits. If a 2048-bit scan costs the same
     as a 128-bit one, the harness is measuring USB round trips, not JTAG clocking.

Logs to ./tmp/logs/probe_scan_effect.log as well as the terminal.

Usage:
    python3.15t scripts/probe_scan_effect.py --label dma
"""

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
LOG_PATH = LOG_DIR / "probe_scan_effect.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

REQUEST_SET_OUT_BUFFER = 0xb1
REQUEST_SCAN = 0xb3
REQUEST_JTAG_START = 0xbf
REQUEST_JTAG_STOP = 0xbe
REQUEST_EMERGENCY_RESET = 0xec


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("probe_scan_effect")
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="unlabelled")
    parser.add_argument("--iterations", "-n", type=int, default=100)
    args = parser.parse_args()

    logger = setup_logging()

    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()

    logger.info("=" * 72)
    logger.info(f"probe_scan_effect: label={args.label}")

    try:
        for request in (REQUEST_EMERGENCY_RESET, REQUEST_JTAG_STOP):
            try:
                debugger.out_request(request)
            except Exception as exc:
                logger.debug(f"  cleanup {request:#x}: {exc!r}")

        debugger.out_request(REQUEST_JTAG_START)

        # 1. Does SCAN succeed at all?
        debugger.out_request(REQUEST_SET_OUT_BUFFER, data=bytes(256))
        try:
            debugger.out_request(REQUEST_SCAN, value=256 * 8)
            logger.info("  SCAN accepted (not stalled)")
        except Exception as exc:
            logger.error(f"  SCAN REJECTED: {exc!r} -- timings would be meaningless")
            return 1

        # 2. Does cost scale with bit count? The staging buffer is 256 bytes, so we
        #    vary the bit count within it rather than the payload size.
        logger.info(f"  {'bits':>7} {'us/call':>10}")
        results = {}
        for bits in (64, 512, 1024, 2048):
            samples = []
            for _ in range(args.iterations):
                start = time.perf_counter()
                debugger.out_request(REQUEST_SCAN, value=bits)
                samples.append((time.perf_counter() - start) * 1e6)
            results[bits] = statistics.median(samples)
            logger.info(f"  {bits:>7} {results[bits]:>10.1f}")

        span = results[2048] - results[64]
        logger.info(f"  2048-bit minus 64-bit: {span:.1f} us")
        if span < 20:
            logger.error("  SCAN cost does NOT scale with bit count -- the harness is "
                         "measuring USB round trips, not JTAG clocking.")
        else:
            per_byte = span / ((2048 - 64) / 8)
            logger.info(f"  marginal clocking cost: {per_byte:.3f} us/byte "
                        f"(wire floor 0.667 us/byte at 12 MHz SCK)")

    finally:
        try:
            debugger.out_request(REQUEST_JTAG_STOP)
        except Exception:
            pass
        try:
            debugger.close()
        except Exception:
            pass

    logger.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
