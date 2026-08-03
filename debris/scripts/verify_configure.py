#!/usr/bin/env python3.15t
"""
Configure the ECP5 over JTAG and prove it actually worked.

The synthetic benchmark measures the JTAG path in isolation, but the change it
motivates -- pipelining spi_send() so the SERCOM transmitter runs a byte ahead
of the receiver -- sits on the path every real configuration uses. A faster
transfer that quietly corrupts a bitstream is worse than a slow one, and a naive
"did apollo configure exit cleanly" check does not catch it: the tool can report
success while the FPGA has silently failed to come up.

So this gates on the ECP5's own status register: DONE set, and none of the
error bits. It also times the end-to-end configure, which is the number to
compare against the synthetic one -- they measure different things, and the gap
between them is the USB cost.

Configuration is volatile. This never writes flash.

Logs to ./tmp/logs/verify_configure.log.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "repos" / "apollo"))

from apollo_fpga import ApolloDebugger  # noqa: E402
from apollo_fpga.ecp5 import ECP5_JTAGProgrammer, ECP5_JTAGDebugSPIConnection  # noqa: E402


def setup_logging():
    log_dir = REPO_ROOT / "tmp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("verify_configure")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(log_dir / "verify_configure.log", mode="a")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(sh)
    return logger


# Bits that mean the configuration did not take. Checked explicitly rather than
# trusting a clean exit from the programming call.
ERROR_FLAGS = [
    ("FAIL", 1 << 13),
    ("EXECUTION_FAIL", 1 << 26),
    ("ID_ERROR", 1 << 27),
    ("INVALID_COMMAND", 1 << 28),
]
DONE_FLAG = 1 << 8
BUSY_FLAG = 1 << 12


def check_status(status: int, log) -> bool:
    log.info(f"ECP5 status register: 0x{status:08x}")

    ok = True
    if not (status & DONE_FLAG):
        log.error("  DONE is NOT set -- the FPGA did not finish configuring")
        ok = False
    else:
        log.info("  DONE set")

    if status & BUSY_FLAG:
        log.error("  BUSY still set -- configuration logic has not settled")
        ok = False

    for name, mask in ERROR_FLAGS:
        if status & mask:
            log.error(f"  {name} set")
            ok = False

    if ok:
        log.info("  no error bits set")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bitstream",
        nargs="?",
        default=str(REPO_ROOT / "ecp5-test" / "led_patterns.bit"),
        help="bitstream to configure with (volatile; flash is never written)",
    )
    parser.add_argument("--runs", type=int, default=3,
                        help="how many times to configure, to check repeatability")
    args = parser.parse_args()

    log = setup_logging()
    log.info("=" * 66)
    log.info(f"configure verification, {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    log.info("=" * 66)

    data = Path(args.bitstream).read_bytes()
    log.info(f"bitstream: {args.bitstream} ({len(data)} bytes)")

    dev = ApolloDebugger()
    log.info(f"firmware : {dev.get_firmware_version()}")

    all_ok = True
    timings = []

    for run in range(1, args.runs + 1):
        log.info("")
        log.info(f"--- run {run} of {args.runs} ---")

        dev.force_fpga_offline()

        start = time.perf_counter()
        with dev.jtag as jtag:
            programmer = dev.create_jtag_programmer(jtag)
            programmer.configure(data)
        elapsed_ms = (time.perf_counter() - start) * 1000
        timings.append(elapsed_ms)

        # Read status through a fresh JTAG session, so the check does not
        # depend on state left over from the programming session.
        with dev.jtag as jtag:
            programmer = dev.create_jtag_programmer(jtag)
            status = programmer._read_status()

        log.info(f"configure took {elapsed_ms:.1f} ms")
        if not check_status(status, log):
            all_ok = False

    log.info("")
    log.info("=" * 66)
    log.info(f"end-to-end configure: best {min(timings):.1f} ms, "
             f"worst {max(timings):.1f} ms over {args.runs} runs")
    log.info(f"bitstream {len(data)} bytes -> "
             f"{min(timings) * 1000 / len(data):.3f} us/byte end to end")
    log.info(f"verdict: {'PASS' if all_ok else 'FAIL'}")
    log.info("=" * 66)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
