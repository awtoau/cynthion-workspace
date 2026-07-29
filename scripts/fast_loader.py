#!/usr/bin/env python3
#
# Where the bitstream-load time actually goes, and how much of it is removable.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures and reduces the time to get a bitstream into the ECP5's SRAM.

Issue #100 proposes replacing JTAG with a USB bulk endpoint on the FPGA itself,
on the theory that ~3 s for a 294 KB image is the JTAG clock. That theory does
not survive contact with the code. Two things are wrong with it:

**The ECP5 cannot configure its own SRAM.** There is no ICAP-equivalent on this
part -- no fabric path into the configuration engine at all. See
docs/luna_ecp5_fpga/fast-bitstream-loading.md for the evidence. A design running
in SRAM cannot replace itself, so the loader gateware the issue describes cannot
exist, and `--background` does not change that: it enables partial
reconfiguration driven by an *external* master, and in background mode the
persistent ports are read-only.

**The bottleneck was never the JTAG clock.** `LSC_BITSTREAM_BURST` shifts the
whole image in one JTAG data scan, but `JTAGChain._scan_data` breaks that scan
into `max_bits_per_scan` chunks, and the SAMD11's buffer is 256 bytes
(`firmware/src/jtag.c:30-31`). Each chunk costs two USB *control* transfers --
`SET_OUT_BUFFER` then `SCAN` -- and control transfers are scheduled once per
microframe. A 294 KB image is ~1150 chunks, so ~2300 transfers at 125 us of
scheduling each is ~290 ms of pure bus latency before any bit is clocked. The
SAMD11 sends each chunk over hardware SPI, not bit-banged GPIO
(`jtag.c:78-81`), so the clock is comparatively cheap.

That reframes the problem. The removable cost is per-transaction overhead, not
the wire, and it is removable without any new gateware:

  * `--mode measure`   -- time raw JTAG scans at several chunk sizes, and
                         decompose the cost into per-transfer and per-byte
                         parts, to attribute the total between the two.
  * `--mode configure` -- configure the FPGA over JTAG, timed. Volatile: this
                         writes SRAM only and never touches flash.
  * `--mode sink-test` -- push a bitstream-sized payload at the FPGA's own bulk
                         endpoint (ecp5-test/loader/bitstream_sink.py) to show
                         how fast that leg is, and hence that it is not the
                         constraint.

All three are safe. Nothing here erases or programs the configuration flash.

    ./scripts/fast_loader.py --mode measure
    ./scripts/fast_loader.py --mode configure --bitstream tmp/.../top.bit
    ./scripts/fast_loader.py --mode sink-test --bitstream tmp/.../top.bit
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from logging_utils import setup_logging


LOG_DIR = ROOT / "tmp" / "logs"

# The SAMD11's JTAG staging buffer, from firmware/src/jtag.c:
#
#     uint8_t jtag_in_buffer[256]  __attribute__((aligned(4)));
#     uint8_t jtag_out_buffer[256] __attribute__((aligned(4)));
#
# `jtag_scan` rejects any scan longer than this outright, so it is a hard
# ceiling on the host side too -- `max_bits_per_scan` is read back from the
# device during JTAG init and will not exceed it.
SAMD11_JTAG_BUFFER = 256

# USB schedules at most one control transfer per 125 us microframe. Two
# transfers per chunk means a chunk cannot complete faster than 250 us however
# little data it carries.
MICROFRAME_US = 125


def open_debugger(logger):
    """Opens the Apollo debugger, or explains why it could not."""
    from apollo_fpga import ApolloDebugger

    try:
        debugger = ApolloDebugger()
    except Exception as exc:
        logger.error(f"no Apollo debugger: {exc}")
        logger.error("expected 1d50:615c (Apollo) or 1d50:615b (Cynthion) on the CONTROL port")
        return None

    logger.info(f"device: {debugger.get_compatibility_string()} "
                f"rev {debugger.detect_connected_version()}")
    return debugger


def time_raw_scans(logger, debugger, total_bytes, sizes):
    """Times raw JTAG data scans at several chunk sizes.

    The point is to separate two costs that the single `configure` figure
    conflates: the per-transaction USB latency, which scales with the *number*
    of chunks, and the SPI clocking, which scales with the number of *bytes*.
    Holding the byte count fixed and varying the chunk size separates them --
    if halving the chunk size nearly doubles the time, the cost is
    per-transaction; if it barely moves, the cost is the wire.

    Scans go to DRPAUSE with the TAP in whatever state configure would use, but
    no configuration command is enabled, so the shifted bits are inert. This
    measures transport only and cannot alter the FPGA's configuration.
    """
    from apollo_fpga.jtag import JTAGChain

    results = []
    payload = bytes(range(256)) * (total_bytes // 256 + 1)

    with JTAGChain(debugger) as chain:
        chain.initialize()
        logger.info(f"JTAG chain reports max_bits_per_scan="
                    f"{chain.max_bits_per_scan} ({chain.max_bits_per_scan // 8} bytes)")

        for size in sizes:
            if size > SAMD11_JTAG_BUFFER:
                logger.warning(f"chunk {size} B exceeds the SAMD11 buffer "
                               f"({SAMD11_JTAG_BUFFER} B); jtag_scan would reject it")
                continue

            # Force the chunking we want to measure, then put it back.
            saved = chain.max_bits_per_scan
            chain.max_bits_per_scan = size * 8
            data = payload[:total_bytes]

            start = time.perf_counter()
            chain.shift_data(tdi=data, length=total_bytes * 8,
                             ignore_response=True, state_after='DRPAUSE')
            elapsed = time.perf_counter() - start
            chain.max_bits_per_scan = saved

            chunks = -(-total_bytes // size)
            per_chunk_us = elapsed / chunks * 1e6
            rate_kbs = total_bytes / elapsed / 1024

            results.append((size, chunks, elapsed, per_chunk_us, rate_kbs))
            logger.info(f"  chunk {size:4d} B: {chunks:5d} chunks  "
                        f"{elapsed * 1000:8.1f} ms  "
                        f"{per_chunk_us:7.1f} us/chunk  {rate_kbs:8.1f} KB/s")

    return results


def analyse(logger, results, total_bytes):
    """Attributes the measured time between per-chunk overhead and per-byte cost.

    A straight line through (chunks, time) gives both: the slope is the cost of
    a chunk and the intercept is what the bytes cost regardless of how they are
    divided. Two points are enough for a line, but using the extremes of the
    range gives the longest lever arm and so the least noise-sensitive fit.
    """
    if len(results) < 2:
        logger.warning("need at least two chunk sizes to separate the costs")
        return None

    small = max(results, key=lambda r: r[1])   # most chunks
    large = min(results, key=lambda r: r[1])   # fewest chunks

    d_chunks = small[1] - large[1]
    d_time = small[2] - large[2]
    if d_chunks == 0:
        return None

    per_chunk_s = d_time / d_chunks
    per_byte_s = (large[2] - per_chunk_s * large[1]) / total_bytes

    logger.info("")
    logger.info("cost attribution")
    logger.info(f"  per chunk:  {per_chunk_s * 1e6:7.1f} us "
                f"({per_chunk_s * 1e6 / MICROFRAME_US:.1f} microframes)")
    logger.info(f"  per byte:   {per_byte_s * 1e9:7.1f} ns "
                f"({1 / per_byte_s / 1e6:.2f} MB/s clocking rate)")

    # What a 294 KB image costs under this model at the current chunk size.
    image = 294 * 1024
    at_256 = per_chunk_s * -(-image // 256) + per_byte_s * image
    floor = per_byte_s * image
    logger.info("")
    logger.info(f"  294 KB at 256 B/chunk: {at_256 * 1000:7.1f} ms predicted")
    logger.info(f"  294 KB clocking only:  {floor * 1000:7.1f} ms "
                f"(if per-chunk overhead were zero)")
    logger.info(f"  removable:             {(at_256 - floor) * 1000:7.1f} ms "
                f"({(at_256 - floor) / at_256 * 100:.0f}% of the transfer)")

    return per_chunk_s, per_byte_s


def decompose_transfers(logger, debugger):
    """Separates the two USB control transfers that make up every JTAG chunk.

    This is the measurement that identifies the bottleneck, because it varies
    payload size and JTAG work independently:

      * `SET_OUT_BUFFER` carries data but does no JTAG work, so its slope
        against payload size is the cost of getting a byte *into* the SAMD11.
      * `SCAN` carries no payload but clocks `wValue` bits, so its slope against
        bit count is the cost of getting a byte *out* onto the JTAG wire.

    Comparing the two slopes says which side is limiting. Timing only a whole
    configure cannot distinguish them.

    Both requests are issued directly rather than through JTAGChain so that no
    chunking logic sits in the way. `SET_OUT_BUFFER` only fills a staging
    buffer, and `SCAN` here shifts inert bits with no configuration command
    enabled, so neither alters the FPGA.
    """
    from apollo_fpga.jtag import (REQUEST_JTAG_SET_OUT_BUFFER,
                                  REQUEST_JTAG_SCAN)

    dev = debugger.device
    reps = 400

    logger.info("")
    logger.info("SET_OUT_BUFFER: data in, no JTAG work")
    points = []
    for size in (8, 16, 32, 64, 128, 192, 256):
        payload = bytes(size)
        dev.ctrl_transfer(0x40, REQUEST_JTAG_SET_OUT_BUFFER, 0, 0, payload)
        start = time.perf_counter()
        for _ in range(reps):
            dev.ctrl_transfer(0x40, REQUEST_JTAG_SET_OUT_BUFFER, 0, 0, payload)
        us = (time.perf_counter() - start) / reps * 1e6
        points.append((size, us))
        logger.info(f"  {size:4d} B: {us:8.1f} us")

    # Least squares over the payload sweep: the intercept is what a transfer
    # costs before any byte moves, the slope is the marginal cost of a byte.
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    denom = sum((p[0] - mx) ** 2 for p in points)
    slope = sum((p[0] - mx) * (p[1] - my) for p in points) / denom
    intercept = my - slope * mx
    logger.info(f"  fit: {intercept:.0f} us fixed + {slope:.2f} us/byte "
                f"({1 / slope:.2f} MB/s)")

    logger.info("")
    logger.info("SCAN: no payload, pure JTAG clocking")
    scan_points = []
    for bits in (8, 256, 512, 1024, 2048):
        dev.ctrl_transfer(0x40, REQUEST_JTAG_SCAN, bits, 0, None)
        start = time.perf_counter()
        for _ in range(reps):
            dev.ctrl_transfer(0x40, REQUEST_JTAG_SCAN, bits, 0, None)
        us = (time.perf_counter() - start) / reps * 1e6
        scan_points.append((bits // 8, us))
        logger.info(f"  {bits:5d} bits ({bits // 8:3d} B): {us:8.1f} us")

    lo, hi = scan_points[0], scan_points[-1]
    scan_slope = (hi[1] - lo[1]) / (hi[0] - lo[0])
    logger.info(f"  marginal: {scan_slope:.2f} us/byte "
                f"({1 / scan_slope:.2f} MB/s JTAG clocking)")

    logger.info("")
    if slope > scan_slope:
        logger.info(f"VERDICT: feeding the SAMD11 ({slope:.2f} us/B) costs more than "
                    f"clocking JTAG ({scan_slope:.2f} us/B).")
        logger.info("The bottleneck is the USB control path into the debug controller,")
        logger.info("not the JTAG wire. A faster JTAG clock would not help.")
    else:
        logger.info(f"VERDICT: JTAG clocking ({scan_slope:.2f} us/B) dominates "
                    f"ingest ({slope:.2f} us/B).")

    return intercept, slope, scan_slope


def report_ceiling(logger, debugger, intercept, slope, scan_slope, image=294 * 1024):
    """Reports the speed of the link the bitstream actually crosses.

    The 388 Mbps in issue #100 is the FPGA's USB PHY. The bitstream does not go
    that way -- it goes through the debug controller, whose negotiated speed is
    read here rather than assumed, because that single number decides whether
    the issue's ~6 ms target is reachable at all.
    """
    speed = None
    try:
        for node in Path("/sys/bus/usb/devices").glob("*/idProduct"):
            if node.read_text().strip() in ("615c", "615b"):
                speed_file = node.parent / "speed"
                if speed_file.exists():
                    speed = float(speed_file.read_text().strip())
                    break
    except OSError:
        pass

    logger.info("")
    if speed:
        logger.info(f"debug controller negotiated {speed:.0f} Mbps "
                    f"({'full' if speed <= 12 else 'high'} speed)")
        wire_mbs = speed / 8
        logger.info(f"  raw wire rate:        {wire_mbs:.2f} MB/s")
        logger.info(f"  measured data rate:   {1 / slope:.2f} MB/s "
                    f"({(1 / slope) / wire_mbs * 100:.0f}% of wire)")
    else:
        logger.warning("could not read negotiated speed from sysfs")
        wire_mbs = None

    chunks = -(-image // SAMD11_JTAG_BUFFER)
    predicted = (intercept * chunks + slope * image) / 1e6
    clocking = scan_slope * image / 1e6

    logger.info("")
    logger.info(f"for a {image} byte image:")
    logger.info(f"  ingest, as structured today: {predicted * 1000:7.0f} ms "
                f"({chunks} chunks x {intercept:.0f} us + {slope:.2f} us/B)")
    logger.info(f"  JTAG clocking alone:         {clocking * 1000:7.0f} ms")
    if wire_mbs:
        logger.info(f"  floor at 100% of the wire:   {image / wire_mbs / 1e6 * 1000:7.0f} ms "
                    f"<- bound for ANY path through this MCU")
    logger.info(f"  issue #100 target:           {6:7.0f} ms")


def mode_measure(logger, args):
    """Attributes the load time between USB ingest and JTAG clocking."""
    debugger = open_debugger(logger)
    if debugger is None:
        return 1

    total = args.bytes
    logger.info(f"timing raw JTAG scans of {total} bytes at several chunk sizes")
    logger.info("(inert: no configuration command is enabled, so the bits go nowhere)")

    sizes = [s for s in (256, 128, 64, 32, 16) if s <= SAMD11_JTAG_BUFFER]
    results = time_raw_scans(logger, debugger, total, sizes)
    if not results:
        logger.error("no scans completed")
        return 1

    analyse(logger, results, total)

    # The chunk sweep above shows *that* per-chunk cost exists; this shows what
    # it is made of, which is what says where to fix it.
    fit = decompose_transfers(logger, debugger)
    if fit:
        report_ceiling(logger, debugger, *fit)

    return 0


def mode_sink_test(logger, args):
    """Measures the FPGA's own bulk endpoint, the leg issue #100 assumed was slow.

    Requires ecp5-test/loader/bitstream_sink.py to be loaded. The result is
    expected to be fast and, precisely because it is fast, to demonstrate that
    this leg is not what makes loading slow -- the constraint is upstream, in
    the debug controller.
    """
    import usb.core

    if not args.bitstream:
        logger.error("--mode sink-test needs --bitstream (used as a payload)")
        return 1

    path = Path(args.bitstream)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        logger.error(f"no bitstream at {path}")
        return 1
    payload = path.read_bytes()

    dev = usb.core.find(idVendor=0x1209, idProduct=0x000e)
    if dev is None:
        logger.error("no bitstream sink at 1209:000e")
        logger.error("load it first:")
        logger.error("  ./ecp5-test/loader/bitstream_sink.py --build --program")
        return 1

    dev.set_configuration()
    logger.info(f"sink found; sending {len(payload)} bytes to EP 0x01")

    start = time.perf_counter()
    written = dev.write(0x01, payload, timeout=10000)
    elapsed = time.perf_counter() - start

    logger.info(f"wrote {written} bytes in {elapsed * 1000:.2f} ms "
                f"({written / elapsed / 1e6:.1f} MB/s, "
                f"{written * 8 / elapsed / 1e6:.0f} Mbps)")

    # The design also counts bytes and clocks internally (JTAG registers 2, 3
    # and 4). Reading them back requires taking the JTAG chain while the FPGA
    # design is live, which contends with the running USB device, so it is left
    # to manual inspection rather than done here. The host-side figure is the
    # one that matters for the comparison being drawn.

    logger.info("")
    logger.info("Compare against --mode configure on the same image: that path runs")
    logger.info("through the SAMD11 at full speed and takes ~1000x longer. This leg")
    logger.info("was never the bottleneck, so moving it onto the FPGA cannot help --")
    logger.info("and the ECP5 cannot configure its own SRAM regardless. See")
    logger.info("docs/luna_ecp5_fpga/fast-bitstream-loading.md")
    return 0


def mode_configure(logger, args):
    """Configures the FPGA's SRAM over JTAG, timed by phase.

    Volatile by construction: `ECP5_JTAGProgrammer.configure` issues ISC_ENABLE,
    ISC_ERASE, LSC_BITSTREAM_BURST and ISC_DISABLE, all of which act on
    configuration SRAM. No flash opcode is issued anywhere in that path, so this
    cannot brick the board -- a power cycle restores whatever is in flash.
    """
    if not args.bitstream:
        logger.error("--mode configure needs --bitstream")
        return 1

    path = Path(args.bitstream)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        logger.error(f"no bitstream at {path}")
        return 1

    bitstream = path.read_bytes()
    logger.info(f"bitstream: {path} ({len(bitstream)} bytes)")

    debugger = open_debugger(logger)
    if debugger is None:
        return 1

    # Use the debugger's own chain and programmer factory, as `apollo configure`
    # does. Constructing ECP5_JTAGProgrammer against a bare JTAGChain skips the
    # board-specific setup and leaves LSC_REFRESH timing out, because the FPGA
    # is still driving the shared CONTROL port.
    with debugger.jtag as chain:
        chunks = -(-len(bitstream) // (chain.max_bits_per_scan // 8))
        logger.info(f"chunking: {chain.max_bits_per_scan // 8} B/chunk, "
                    f"{chunks} chunks, {chunks * 2} USB control transfers")

        programmer = debugger.create_jtag_programmer(chain)

        start = time.perf_counter()
        programmer.configure(bitstream)
        elapsed = time.perf_counter() - start

    # Hand the shared CONTROL port to the freshly loaded gateware, exactly as
    # `apollo configure` does; without this the new design cannot enumerate.
    debugger.allow_fpga_takeover_usb()

    logger.info("")
    logger.info(f"configure: {elapsed * 1000:.0f} ms "
                f"({len(bitstream) / elapsed / 1024:.1f} KB/s)")
    logger.info("SRAM only -- flash untouched; power-cycle to revert")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("measure", "configure", "sink-test"),
                        default="measure")
    parser.add_argument("--bitstream", help="path to a .bit file, for --mode configure")
    parser.add_argument("--bytes", type=int, default=64 * 1024,
                        help="payload size for --mode measure (default 65536)")
    args = parser.parse_args()

    logger = setup_logging("fast_loader", log_dir=LOG_DIR)

    if args.mode == "measure":
        return mode_measure(logger, args)
    if args.mode == "sink-test":
        return mode_sink_test(logger, args)
    return mode_configure(logger, args)


if __name__ == "__main__":
    sys.exit(main())
