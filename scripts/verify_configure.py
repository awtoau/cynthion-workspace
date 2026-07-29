#!/usr/bin/env python3
"""
Verify that an `apollo configure` actually configured the ECP5, rather than merely
running fast.

Configures the FPGA, then reads the ECP5 status register and asserts the DONE bit is
set and no error bits are flagged. This is the correctness gate for the performance
work: a change that speeds up configuration but corrupts TDO will still "succeed"
from the host's point of view, so DONE must be checked explicitly.

Logs to ./tmp/logs/verify_configure.log as well as the terminal.

Usage:
    python3.15t scripts/verify_configure.py --bitstream tmp/bitstreams/bench-298k.bit
"""

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
LOG_PATH = LOG_DIR / "verify_configure.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("verify_configure")
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
    parser.add_argument("--bitstream", "-b", type=Path,
                        default=ROOT / "tmp" / "bitstreams" / "bench-298k.bit")
    parser.add_argument("--repeat", "-n", type=int, default=1,
                        help="verify this many consecutive configures (default: 1)")
    args = parser.parse_args()

    logger = setup_logging()

    if not args.bitstream.exists():
        logger.error(f"bitstream not found: {args.bitstream}")
        return 1

    bitstream = args.bitstream.read_bytes()

    from apollo_fpga import ApolloDebugger
    from apollo_fpga.ecp5 import ECP5_JTAGProgrammer

    logger.info("=" * 72)
    logger.info("verify_configure")
    logger.info(f"  bitstream: {args.bitstream} ({len(bitstream)} bytes)")

    failures = 0

    for attempt in range(args.repeat):
        debugger = ApolloDebugger()
        try:
            # A configured FPGA drives the shared lines and will not re-enter ISC;
            # every configure must start from an offline FPGA. `apollo configure`
            # gets this via its own --force-offline handling, so do the same here
            # rather than measuring a state the real tool never runs in.
            try:
                debugger.force_fpga_offline()
            except Exception as exc:
                logger.debug(f"  force_fpga_offline: {exc!r}")

            with debugger.jtag as jtag:
                programmer = ECP5_JTAGProgrammer(jtag)

                start = time.perf_counter()
                programmer.configure(bitstream)
                elapsed = time.perf_counter() - start

                # Read the status register back and decode the bits that matter.
                status = programmer._read_status()
                done = bool(status & (1 << 8))
                isc_enabled = bool(status & (1 << 9))
                fail = bool(status & (1 << 13))

                # Bits 23:23 upward carry the BSE error code; non-zero means the
                # bitstream was rejected (CRC, bad command, wrong device, ...).
                bse_error = (status >> 23) & 0x7

                ok = done and not fail and bse_error == 0
                if not ok:
                    failures += 1

                logger.info(
                    f"  run {attempt + 1}/{args.repeat}: {elapsed * 1e3:8.1f} ms  "
                    f"status=0x{status:08x} DONE={int(done)} FAIL={int(fail)} "
                    f"ISC={int(isc_enabled)} BSE_ERR={bse_error}  "
                    f"{'PASS' if ok else 'FAIL'}")
        except Exception as exc:
            failures += 1
            logger.error(f"  run {attempt + 1}/{args.repeat}: EXCEPTION: {exc!r}")
        finally:
            try:
                debugger.close()
            except Exception:
                pass

    if failures:
        logger.error(f"  RESULT: {failures}/{args.repeat} runs FAILED verification")
    else:
        logger.info(f"  RESULT: all {args.repeat} run(s) verified -- DONE set, no errors")
    logger.info("=" * 72)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
