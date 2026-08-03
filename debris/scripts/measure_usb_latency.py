#!/usr/bin/env python3
"""
Discriminate whether Apollo's JTAG plumbing is latency-bound or bandwidth-bound.

Times SET_OUT_BUFFER across a range of payload sizes. If per-call time is roughly
flat with size, the cost is per-transaction round-trip latency and the fix is fewer
round trips. If per-call time scales with size, it is bandwidth-limited and moving
to bulk endpoints is the answer.

Also times a zero-payload request (GET_STATE) to establish the pure round-trip floor.

Logs to ./tmp/logs/measure_usb_latency.log as well as the terminal.

Usage:
    python3.15t scripts/measure_usb_latency.py
"""

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
LOG_PATH = LOG_DIR / "measure_usb_latency.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("measure_usb_latency")
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


def time_request(fn, iterations: int) -> float:
    """Returns median seconds per call, to reject scheduler outliers."""
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iterations", "-n", type=int, default=200,
                        help="calls timed per payload size (default: 200)")
    args = parser.parse_args()

    logger = setup_logging()

    from apollo_fpga import ApolloDebugger
    from apollo_fpga import jtag as jtag_mod

    debugger = ApolloDebugger()

    logger.info("=" * 72)
    logger.info("measure_usb_latency: SET_OUT_BUFFER cost vs payload size")
    logger.info(f"  iterations per size: {args.iterations}")

    try:
        with debugger.jtag:
            # Pure round-trip floor: a request that carries no payload at all.
            floor = time_request(
                lambda: debugger.in_request(jtag_mod.REQUEST_JTAG_GET_STATE, length=1),
                args.iterations)
            logger.info(f"  round-trip floor (GET_STATE, 0 payload bytes): "
                        f"{floor * 1e6:7.1f} us/req")
            logger.info("")
            logger.info(f"  {'size':>6}  {'us/req':>9}  {'us/byte':>9}  {'marginal us/B':>14}")

            results = []
            for size in (1, 8, 16, 32, 64, 128, 256):
                payload = bytes(size)
                per_call = time_request(
                    lambda: debugger.out_request(
                        jtag_mod.REQUEST_JTAG_SET_OUT_BUFFER, data=payload),
                    args.iterations)
                results.append((size, per_call))

                # Marginal cost per byte relative to the smallest payload isolates
                # the size-dependent component from the fixed per-request cost.
                base_size, base_time = results[0]
                if size > base_size:
                    marginal = (per_call - base_time) / (size - base_size) * 1e6
                    marginal_str = f"{marginal:14.3f}"
                else:
                    marginal_str = f"{'--':>14}"

                logger.info(f"  {size:6d}  {per_call * 1e6:9.1f}  "
                            f"{per_call / size * 1e6:9.3f}  {marginal_str}")

            # Verdict.
            smallest_time = results[0][1]
            largest_size, largest_time = results[-1]
            ratio = largest_time / smallest_time
            marginal_per_byte = (largest_time - smallest_time) / (largest_size - 1) * 1e6

            logger.info("")
            logger.info(f"  256B call is {ratio:.2f}x the cost of a 1B call")
            logger.info(f"  fixed per-request cost   ~= {smallest_time * 1e6:7.1f} us")
            logger.info(f"  marginal cost per byte   ~= {marginal_per_byte:7.3f} us/B "
                        f"({marginal_per_byte * 256:.1f} us over a 256B chunk)")

            if ratio < 1.5:
                logger.info("  VERDICT: LATENCY-BOUND -- per-call time is nearly flat with "
                            "payload size.")
                logger.info("           Fix = fewer round trips. Bulk endpoints alone will "
                            "not help much.")
            elif ratio > 3.0:
                logger.info("  VERDICT: BANDWIDTH-BOUND -- per-call time scales with payload "
                            "size.")
                logger.info("           Fix = higher-throughput transfers (bulk endpoints).")
            else:
                logger.info("  VERDICT: MIXED -- both a significant fixed cost and a "
                            "size-dependent cost.")
    finally:
        try:
            debugger.close()
        except Exception:
            pass

    logger.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
