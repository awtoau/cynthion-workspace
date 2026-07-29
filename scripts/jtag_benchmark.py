#!/usr/bin/env python3.15t
"""
Drive Apollo's synthetic JTAG benchmark (vendor request 0xb8).

The point of the firmware-side benchmark is that it removes USB from the
measurement: the host issues one small control transfer to start a run, the MCU
clocks (chunk x repeats) bytes over JTAG out of a buffer it already holds, and
one IN transfer collects elapsed time plus a TDO checksum. Nothing bulk crosses
USB while the clocking happens.

This script is the host side of that, plus the positive controls that make the
numbers trustworthy:

  * scaling   -- a run twice the size must cost about twice as much. If the cost
                 is flat, the harness is measuring nothing and the timing is
                 meaningless however clean it looks.
  * checksum  -- TDO folded into a checksum by the firmware. A stalled or no-op
                 loop looks identical to a fast one on a stopwatch; a checksum
                 that changes with the data is the evidence bytes really moved.
  * sweep     -- re-run at a range of SERCOM baud dividers to find where the
                 link actually stops working, rather than assuming the current
                 rate is a hardware limit.

Logs to ./tmp/logs/jtag_benchmark.log as well as the terminal.
"""

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "repos" / "apollo"))

from apollo_fpga import ApolloDebugger  # noqa: E402

# Must match the enum in repos/apollo/firmware/src/vendor.c. Checked against the
# firmware rather than assumed: an earlier harness in this project used 0xb5,
# which is GOTO_STATE, and produced plausible-looking timings for a request that
# was not doing the work anyone thought it was.
REQUEST_JTAG_BENCHMARK = 0xB8
REQUEST_JTAG_START = 0xBF
REQUEST_JTAG_STOP = 0xBE
REQUEST_JTAG_GOTO_STATE = 0xB5
REQUEST_JTAG_SET_OUT_BUFFER = 0xB1
REQUEST_JTAG_SCAN = 0xB3

# jtag_tap_state_t in repos/apollo/firmware/src/jtag.h.
STATE_TEST_LOGIC_RESET = 0
STATE_RUN_TEST_IDLE = 1
STATE_SHIFT_DR = 4
STATE_SHIFT_IR = 11

# ECP5: 8-bit IR, all-ones selects BYPASS.
ECP5_BYPASS = 0xFF

# The TAP must be in SHIFT_DR *with BYPASS loaded* for the readback to be
# predictable. Two distinct failures were hit getting here, and both looked
# fine on a stopwatch:
#   - not in SHIFT_DR at all: TDO reads back all zeroes, perfectly stably.
#   - in SHIFT_DR with the resident instruction: TDO reads back all ones.
# Only comparing against the expected BYPASS response distinguishes either from
# a working link. scripts/jtag_tdo_probe.py establishes the model empirically.

# SERCOM SPI master on the SAMD11: SCK = f_ref / (2 * (BAUD + 1)), f_ref = 48MHz.
SERCOM_REF_HZ = 48_000_000
DIVIDER_KEEP = 0xFF


def sck_for_divider(divider: int) -> float:
    return SERCOM_REF_HZ / (2 * (divider + 1))


def setup_logging() -> logging.Logger:
    log_dir = REPO_ROOT / "tmp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("jtag_benchmark")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    fh = logging.FileHandler(log_dir / "jtag_benchmark.log", mode="a")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(sh)

    return logger


class Benchmark:
    def __init__(self, debugger, logger):
        self.dev = debugger
        self.log = logger

    def select_bypass(self):
        """Load BYPASS and park the TAP in SHIFT_DR.

        BYPASS makes TDO a one-bit delay of TDI, which is the only response
        simple enough to predict for arbitrary data at arbitrary length -- and
        so the only one that lets the firmware verify the readback itself,
        without the host having to send the expected data across USB and defeat
        the purpose of the benchmark.
        """
        self.dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_TEST_LOGIC_RESET)
        self.dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_RUN_TEST_IDLE)
        self.dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_SHIFT_IR)

        self.dev.out_request(REQUEST_JTAG_SET_OUT_BUFFER, data=bytes([ECP5_BYPASS]))
        self.dev.out_request(REQUEST_JTAG_SCAN, value=8, index=0)

        self.dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_RUN_TEST_IDLE)
        self.dev.out_request(REQUEST_JTAG_GOTO_STATE, value=STATE_SHIFT_DR)

    def run(self, repeats: int, chunk: int = 256, divider: int = DIVIDER_KEEP):
        """One synthetic run. Returns (elapsed_ms, total_bytes, checksum)."""
        if not 1 <= repeats <= 0xFFFF:
            raise ValueError(f"repeats out of range: {repeats}")
        if not 1 <= chunk <= 256:
            raise ValueError(f"chunk out of range: {chunk}")

        # 256 does not fit in a byte; the firmware reads 0 as a full buffer.
        chunk_field = 0 if chunk == 256 else chunk
        index = (divider << 8) | chunk_field

        # Re-established before every run: a benchmark at a divider the FPGA
        # cannot follow can leave the TAP somewhere unexpected, and the next
        # run must not inherit that.
        self.select_bypass()

        # The timeout must exceed the longest run this can request. 65535
        # repeats of 256 bytes at the slowest divider we sweep is a few seconds;
        # 20s leaves headroom without hiding a genuine hang forever.
        raw = self.dev.in_request(
            REQUEST_JTAG_BENCHMARK, value=repeats, index=index, length=12, timeout=20000
        )
        if len(raw) != 12:
            raise RuntimeError(f"short benchmark reply: {len(raw)} bytes")

        return {
            "device_ms": int.from_bytes(raw[0:4], "little"),
            "total_bytes": int.from_bytes(raw[4:6], "little") * 256,
            "mismatches": int.from_bytes(raw[6:10], "little"),
            "sample_sent": raw[10],
            "sample_received": raw[11],
        }

    def timed_run(self, repeats, chunk=256, divider=DIVIDER_KEEP, trials=3):
        """Repeat a run and keep the fastest, to reject scheduler noise."""
        results = []
        for _ in range(trials):
            host_start = time.perf_counter()
            r = self.run(repeats, chunk, divider)
            r["host_ms"] = (time.perf_counter() - host_start) * 1000
            results.append(r)

        best = dict(min(results, key=lambda r: r["device_ms"]))
        best["host_ms"] = min(r["host_ms"] for r in results)

        # Every trial must be clean. One bad trial out of several is still a
        # broken link, and taking the best would hide exactly that.
        best["worst_mismatches"] = max(r["mismatches"] for r in results)
        best["clean"] = best["worst_mismatches"] == 0
        return best


def control_scaling(bench, log) -> bool:
    """Positive control: cost must scale with size."""
    log.info("")
    log.info("=== Control 1: does cost scale with transfer size? ===")
    log.info(f"{'bytes':>10} {'device ms':>10} {'us/byte':>9} {'bad bytes':>10}")

    points = []
    for repeats in (8, 64, 256, 1024):
        r = bench.timed_run(repeats, chunk=256)
        us_per_byte = (r["device_ms"] * 1000) / r["total_bytes"]
        points.append((r["total_bytes"], r["device_ms"]))
        log.info(
            f"{r['total_bytes']:>10} {r['device_ms']:>10} "
            f"{us_per_byte:>9.3f} {r['worst_mismatches']:>10}"
        )

    # A 128x size increase must produce a broadly proportional time increase.
    small_bytes, small_ms = points[0]
    big_bytes, big_ms = points[-1]
    size_ratio = big_bytes / small_bytes
    time_ratio = big_ms / max(small_ms, 1)

    log.info(f"size x{size_ratio:.0f} produced time x{time_ratio:.1f}")
    ok = time_ratio > size_ratio * 0.5
    log.info(f"scaling control: {'PASS' if ok else 'FAIL - harness measures nothing'}")
    return ok


def control_readback(bench, log) -> bool:
    """Positive control: TDO must read back exactly what the TAP owes us."""
    log.info("")
    log.info("=== Control 2: is TDO really carrying the data we sent? ===")

    ok = True
    for chunk in (256, 128, 64):
        r = bench.timed_run(64, chunk=chunk, trials=2)
        total = r["total_bytes"]
        log.info(
            f"chunk {chunk:>3}: {r['worst_mismatches']:>6} bad of {total:>7} bytes"
            f"   sent 0x{r['sample_sent']:02x} -> got 0x{r['sample_received']:02x}"
        )
        if not r["clean"]:
            ok = False

    # An all-zero readback is the specific failure this control exists to catch:
    # it is what the TAP returns when it is not in SHIFT_DR, and it is perfectly
    # stable, so only comparing against expected data detects it.
    log.info(f"readback control: {'PASS' if ok else 'FAIL - TDO does not match TDI'}")
    return ok


def sweep_dividers(bench, log, repeats=512):
    """Push SCK up until TDO readback stops agreeing with itself."""
    log.info("")
    log.info("=== SCK sweep: where does the link actually break? ===")
    log.info(
        f"{'div':>4} {'SCK MHz':>9} {'device ms':>10} {'us/byte':>9} "
        f"{'bad bytes':>10} {'verdict':>10}"
    )

    rows = []

    # Descending divider = ascending SCK. 1 is the firmware default (12MHz);
    # 0 is the fastest the SERCOM can produce (24MHz).
    for divider in (7, 5, 3, 2, 1, 0):
        try:
            r = bench.timed_run(repeats, chunk=256, divider=divider, trials=3)
        except Exception as exc:  # noqa: BLE001
            log.info(f"{divider:>4} {sck_for_divider(divider)/1e6:>9.1f}  FAILED: {exc}")
            rows.append({"divider": divider, "ok": False, "device_ms": None})
            continue

        us_per_byte = (r["device_ms"] * 1000) / r["total_bytes"]
        verdict = "clean" if r["clean"] else "CORRUPT"

        rows.append({
            "divider": divider,
            "sck_hz": sck_for_divider(divider),
            "device_ms": r["device_ms"],
            "us_per_byte": us_per_byte,
            "mismatches": r["worst_mismatches"],
            "ok": r["clean"],
        })
        log.info(
            f"{divider:>4} {sck_for_divider(divider)/1e6:>9.1f} {r['device_ms']:>10} "
            f"{us_per_byte:>9.3f} {r['worst_mismatches']:>10} {verdict:>10}"
        )

    log.info("")
    good = [r for r in rows if r.get("ok") and r["device_ms"]]
    if not good:
        log.error("no clean run at any rate -- the link is not working; ignore timings")
        return rows

    fastest = min(good, key=lambda r: r["us_per_byte"])
    log.info(
        f"fastest rate with intact readback: divider {fastest['divider']} "
        f"= {fastest['sck_hz']/1e6:.1f} MHz SCK, {fastest['us_per_byte']:.3f} us/byte"
    )

    baseline = next((r for r in rows if r["divider"] == 1 and r.get("ok")), None)
    if baseline and fastest["divider"] != 1:
        speedup = baseline["us_per_byte"] / fastest["us_per_byte"]
        log.info(
            f"versus the 12.0 MHz default: {speedup:.2f}x faster per byte "
            f"({baseline['us_per_byte']:.3f} -> {fastest['us_per_byte']:.3f} us/byte)"
        )

    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=512,
                        help="repeats for the headline measurement")
    parser.add_argument("--skip-sweep", action="store_true",
                        help="run the controls only, no SCK sweep")
    args = parser.parse_args()

    log = setup_logging()
    log.info("=" * 70)
    log.info(f"JTAG synthetic benchmark, {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    log.info("=" * 70)

    dev = ApolloDebugger()
    log.info(f"device firmware: {dev.get_firmware_version()}")

    # The benchmark drives the scan chain, so open a JTAG session around it just
    # as a real scan would; otherwise the pins are not set up for it.
    dev.out_request(REQUEST_JTAG_START)
    try:
        bench = Benchmark(dev, log)

        scaling_ok = control_scaling(bench, log)
        readback_ok = control_readback(bench, log)

        if not (scaling_ok and readback_ok):
            log.error("")
            log.error("CONTROLS FAILED - timings below are not trustworthy.")

        log.info("")
        log.info("=== Headline: JTAG path in isolation ===")
        r = bench.timed_run(args.repeats, chunk=256, trials=5)
        us_per_byte = (r["device_ms"] * 1000) / r["total_bytes"]
        usb_overhead = r["host_ms"] - r["device_ms"]
        log.info(f"bytes clocked   : {r['total_bytes']}")
        log.info(f"device-measured : {r['device_ms']} ms  ({us_per_byte:.3f} us/byte)")
        log.info(f"host-measured   : {r['host_ms']:.1f} ms")
        log.info(f"USB round trip  : {usb_overhead:.1f} ms (the whole cost of asking)")
        log.info(f"bad bytes       : {r['worst_mismatches']}")
        log.info(f"effective rate  : {8 / us_per_byte:.2f} Mbit/s")

        # The SERCOM is clocked at 12MHz, so 0.667us/byte is the rate at which
        # the wire is saturated. Anything above that is MCU overhead in the
        # polled send loop, and is the only part CPU-side work could recover.
        wire_floor = 8 / (sck_for_divider(1) / 1e6)
        log.info(
            f"wire floor at this SCK: {wire_floor:.3f} us/byte; "
            f"MCU overhead {us_per_byte - wire_floor:.3f} us/byte "
            f"({100 * (us_per_byte - wire_floor) / us_per_byte:.0f}% of the total)"
        )

        if not args.skip_sweep:
            sweep_dividers(bench, log)

    finally:
        dev.out_request(REQUEST_JTAG_STOP)

    log.info("")
    log.info("log written to ./tmp/logs/jtag_benchmark.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
