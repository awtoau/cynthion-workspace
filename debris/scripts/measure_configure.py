#!/usr/bin/env python3
"""
Measure the wall-clock cost of `apollo configure` (ECP5 SRAM configuration over JTAG).

Times the end-to-end configure, and optionally decomposes the bitstream-burst phase
into its constituent USB stages (SET_OUT_BUFFER feeding Apollo's staging buffer vs
SCAN clocking it out over JTAG) so that a change can be attributed to a stage.

Logs to ./tmp/logs/measure_configure.log as well as the terminal.

Usage:
    python3.15t scripts/measure_configure.py --bitstream tmp/bitstreams/bench-298k.bit
    python3.15t scripts/measure_configure.py --repeat 5 --decompose
"""

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

# Workspace root is the parent of scripts/.
ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
LOG_PATH = LOG_DIR / "measure_configure.log"

# Use the vendored Apollo, not whatever is installed site-wide, so the numbers
# describe the tree under test.
sys.path.insert(0, str(ROOT / "repos" / "apollo"))


def setup_logging(verbose: bool) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("measure_configure")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%dT%H:%M:%S%z")

    fh = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


class StageTimer:
    """Accumulates per-USB-request time so the burst can be attributed by stage."""

    def __init__(self):
        self.totals = {}
        self.counts = {}
        self.bytes = {}

    def add(self, stage: str, elapsed: float, nbytes: int = 0) -> None:
        self.totals[stage] = self.totals.get(stage, 0.0) + elapsed
        self.counts[stage] = self.counts.get(stage, 0) + 1
        self.bytes[stage] = self.bytes.get(stage, 0) + nbytes

    def report(self, logger: logging.Logger) -> None:
        if not self.totals:
            return
        logger.info("  stage decomposition:")
        for stage in sorted(self.totals, key=lambda s: -self.totals[s]):
            total = self.totals[stage]
            count = self.counts[stage]
            nbytes = self.bytes[stage]
            per_byte = f"{total / nbytes * 1e6:8.2f} us/B" if nbytes else "        --    "
            logger.info(
                f"    {stage:<22} {total * 1e3:9.1f} ms  "
                f"{count:6d} req  {per_byte}  {total / count * 1e6:8.1f} us/req"
            )


def instrument(debugger, timer: StageTimer):
    """Wrap out_request/in_request to attribute time to the JTAG stage involved."""
    from apollo_fpga import jtag as jtag_mod

    names = {
        jtag_mod.REQUEST_JTAG_SET_OUT_BUFFER: "SET_OUT_BUFFER",
        jtag_mod.REQUEST_JTAG_GET_IN_BUFFER: "GET_IN_BUFFER",
        jtag_mod.REQUEST_JTAG_SCAN: "SCAN",
        jtag_mod.REQUEST_JTAG_CLEAR_OUT_BUFFER: "CLEAR_OUT_BUFFER",
        jtag_mod.REQUEST_JTAG_RUN_CLOCK: "RUN_CLOCK",
        jtag_mod.REQUEST_JTAG_GO_TO_STATE: "GO_TO_STATE",
    }

    real_out = debugger.out_request
    real_in = debugger.in_request

    def out_request(number, value=0, index=0, data=None, timeout=500):
        start = time.perf_counter()
        result = real_out(number, value, index, data, timeout)
        timer.add(names.get(number, f"out:0x{number:02x}"),
                  time.perf_counter() - start, len(data) if data else 0)
        return result

    def in_request(number, value=0, index=0, length=0, timeout=500):
        start = time.perf_counter()
        result = real_in(number, value, index, length, timeout)
        timer.add(names.get(number, f"in:0x{number:02x}"),
                  time.perf_counter() - start, length)
        return result

    debugger.out_request = out_request
    debugger.in_request = in_request


def wait_for_reenumeration(logger: logging.Logger, timeout: float = 10.0) -> bool:
    """Polls until Apollo is enumerable again after a fast-mode reboot.

    Fast mode ends with the device resetting, so the host must expect it to vanish
    and come back. Polls rather than sleeping a fixed interval.
    """
    from apollo_fpga import ApolloDebugger

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            debugger = ApolloDebugger()
            debugger.close()
            return True
        except Exception:
            continue

    logger.error(f"  device did not re-enumerate within {timeout}s")
    return False


def run_once(bitstream: bytes, decompose: bool, logger: logging.Logger, fast: bool = False):
    """Perform one full configure, returning (elapsed_seconds, StageTimer)."""
    from apollo_fpga import ApolloDebugger
    from apollo_fpga.ecp5 import ECP5_JTAGProgrammer

    timer = StageTimer()
    debugger = ApolloDebugger()

    try:
        if decompose:
            instrument(debugger, timer)

        with debugger.jtag as jtag:
            programmer = ECP5_JTAGProgrammer(jtag)
            start = time.perf_counter()
            programmer.configure(bitstream, fast=fast) if fast else programmer.configure(bitstream)
            elapsed = time.perf_counter() - start
    finally:
        try:
            debugger.close()
        except Exception:
            pass

    # Fast mode reboots the device; wait for it to come back before the next run so
    # the following iteration does not race the re-enumeration.
    if fast:
        wait_for_reenumeration(logger)

    return elapsed, timer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bitstream", "-b", type=Path,
                        default=ROOT / "tmp" / "bitstreams" / "bench-298k.bit",
                        help="bitstream to configure (default: the staged benchmark)")
    parser.add_argument("--repeat", "-n", type=int, default=3,
                        help="number of configure runs to time (default: 3)")
    parser.add_argument("--decompose", "-d", action="store_true",
                        help="attribute burst time to individual USB request stages")
    parser.add_argument("--label", "-l", default="",
                        help="label recorded in the log, e.g. 'baseline' or 'fastmode'")
    parser.add_argument("--fast", "-f", action="store_true",
                        help="use bulk-endpoint streaming (device reboots after each run)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    if not args.bitstream.exists():
        logger.error(f"bitstream not found: {args.bitstream}")
        return 1

    bitstream = args.bitstream.read_bytes()

    logger.info("=" * 72)
    logger.info(f"measure_configure: {args.label or '(unlabelled)'}")
    logger.info(f"  bitstream: {args.bitstream}  ({len(bitstream)} bytes)")
    logger.info(f"  repeat:    {args.repeat}   decompose: {args.decompose}   fast: {args.fast}")

    times = []
    for i in range(args.repeat):
        try:
            elapsed, timer = run_once(bitstream, args.decompose, logger, fast=args.fast)
        except Exception as exc:
            logger.error(f"  run {i + 1}: FAILED: {exc!r}")
            return 1

        times.append(elapsed)
        rate = len(bitstream) / elapsed / 1024
        logger.info(f"  run {i + 1}/{args.repeat}: {elapsed * 1e3:8.1f} ms "
                    f"({rate:6.1f} KiB/s, {elapsed / len(bitstream) * 1e6:.2f} us/byte)")
        if args.decompose:
            timer.report(logger)

    best = min(times)
    mean = statistics.mean(times)
    logger.info(f"  RESULT: best {best * 1e3:.1f} ms   mean {mean * 1e3:.1f} ms"
                + (f"   stdev {statistics.stdev(times) * 1e3:.1f} ms" if len(times) > 1 else ""))
    logger.info(f"  throughput (best): {len(bitstream) / best / 1024:.1f} KiB/s")
    logger.info("=" * 72)

    return 0


if __name__ == "__main__":
    sys.exit(main())
