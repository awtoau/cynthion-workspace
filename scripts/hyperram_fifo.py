#!/usr/bin/env python3
#
# Build, flash and read back the HyperRAM FIFO chunk-size sweep.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reports HyperRAM throughput under an alternating write/read access pattern.

The gateware sweeps chunk size and times each phase with a counter next to the
controller; this script only builds it, loads it, and reads the results out
afterwards. Nothing here is timed from the host -- a JTAG register read takes
~35 ms against transactions measured in microseconds, so a host-side stopwatch
would measure the debugger and not the RAM.

The comparison that matters is against the streaming figure from
the retired hyperram_speed.py, so it is printed alongside every row: the
question is not
what the alternating pattern achieves in isolation but how much of the
streaming rate survives turning the bus around.

    ./scripts/hyperram_fifo.py
    ./scripts/hyperram_fifo.py --skip-build     # read back an existing load
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from subprocess_timeout_from_history import run_bounded

ROOT = Path(__file__).resolve().parent.parent
GATEWARE = ROOT / "gateware" / "probes" / "hyperram" / "hyperram_fifo.py"
BUILD_DIR = ROOT / "gateware" / "probes" / "hyperram" / "fifo_build"
BITSTREAM = BUILD_DIR / "top.bit"
LOG = ROOT / "tmp" / "hyperram_fifo.log"

BYTES_PER_WORD = 2

# The streaming baseline this is measured against, from
# docs/luna_ecp5_fpga/hyperram-speed.md: 2048 words in 2071 cycles read /
# 2067 write at 120 MHz.
STREAM_READ_MBPS  = 237.3
STREAM_WRITE_MBPS = 237.8

# The fastest USB direction measured on this board, for the margin question.
USB_MBPS = 36.1

REG_ID, REG_STATUS, REG_RESULT_ADDR = 1, 2, 3
REG_WRITE_CYCLES, REG_READ_CYCLES, REG_ERRORS, REG_TOTAL = 4, 5, 6, 7

APPLET_ID = 0x48524649


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def gateware_constants():
    """Read CHUNK_SIZES and WORDS_PER_PHASE from the gateware source.

    Taken from the source rather than duplicated here so the two cannot drift:
    a sweep that reported the wrong chunk size against a cycle count would be
    worse than no measurement at all.
    """
    text = GATEWARE.read_text()
    chunks = re.search(r"CHUNK_SIZES = \[([^\]]+)\]", text)
    words = re.search(r"WORDS_PER_PHASE = (\d+)", text)
    clock = re.search(r"CLOCK_MHZ = (\d+)", text)
    return ([int(v) for v in chunks.group(1).split(",")],
            int(words.group(1)), int(clock.group(1)))


def build():
    script = (
        'import sys; sys.path.insert(0,"gateware"); '
        'sys.path.insert(0,"repos/apollo")\n'
        'from hyperram.hyperram_fifo import HyperRAMFIFOTest\n'
        'from board.cynthion_r1_4 import '
        'CynthionPlatformRev1D4\n'
        'CynthionPlatformRev1D4().build(HyperRAMFIFOTest(), do_program=False, '
        f'build_dir="{BUILD_DIR.relative_to(ROOT)}")\n'
    )
    result = run_bounded(
        ["bash", "-c",
         f'source "$HOME/opt/oss-cad-suite/environment" && '
         f'python3.15t -c {shell_quote(script)}'],
        family="ecp5-hyperram-fifo", cwd=ROOT)

    if result is None:
        return False, "build exceeded its measured bound (stuck)"
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return False, tail[-1][:200] if tail else "build failed"

    timing = "unknown"
    report = BUILD_DIR / "top.tim"
    if report.exists():
        matches = re.findall(r"Max frequency for clock '\$glbnet\$clk': "
                             r"([\d.]+) MHz \((\w+) at ([\d.]+) MHz\)",
                             report.read_text())
        if matches:
            achieved, verdict, target = matches[0]
            timing = f"{verdict} {float(achieved):.0f}/{float(target):.0f} MHz"

    # `expect="design"`: counters are read over JTAG registers, no console, so
    # the part's DONE bit is the confirmation rather than an exit code (#360).
    import soc_confirm

    if soc_confirm.configure_and_confirm(BITSTREAM, expect="design") != 0:
        return False, "no design running; the verdict above names why"
    return True, timing


def read_results(phase_count):
    """Pull every phase's counters out over JTAG, after the sweep has finished."""
    script = (
        'import sys; sys.path.insert(0,"repos/apollo")\n'
        'from apollo_fpga import ApolloDebugger\n'
        'd = ApolloDebugger()\n'
        f'print(d.registers.register_read({REG_ID}))\n'
        f'print(d.registers.register_read({REG_STATUS}))\n'
        f'print(d.registers.register_read({REG_TOTAL}))\n'
        f'for a in range({phase_count}):\n'
        f'    d.registers.register_write({REG_RESULT_ADDR}, a)\n'
        f'    w = d.registers.register_read({REG_WRITE_CYCLES})\n'
        f'    r = d.registers.register_read({REG_READ_CYCLES})\n'
        f'    e = d.registers.register_read({REG_ERRORS})\n'
        '    print(w, r, e)\n'
    )
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout)[-300:]
    lines = result.stdout.strip().splitlines()
    applet_id = int(lines[0])
    status = int(lines[1])
    total = int(lines[2])
    rows = [tuple(int(v) for v in line.split()) for line in lines[3:]]
    return (applet_id, status, total, rows), None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-build", action="store_true",
                        help="read back whatever is already loaded")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    chunk_sizes, words_per_phase, clock_mhz = gateware_constants()
    phase_bytes = words_per_phase * BYTES_PER_WORD

    with LOG.open("w") as handle:
        emit(handle, "HyperRAM FIFO-pattern sweep: alternating write/read, "
                     f"{words_per_phase} words each way per chunk size, "
                     f"{clock_mhz} MHz sync")
        emit(handle)

        timing = "not rebuilt"
        if not args.skip_build:
            ok, timing = build()
            if not ok:
                emit(handle, f"BUILD/LOAD FAILED: {timing}")
                emit(handle, f"log: {LOG}")
                return 1
            emit(handle, f"nextpnr timing report: {timing}")
            emit(handle)

        result, error = read_results(len(chunk_sizes))
        if result is None:
            emit(handle, f"READBACK FAILED: {error}")
            emit(handle, f"log: {LOG}")
            return 1

        applet_id, status, total, rows = result
        if applet_id != APPLET_ID:
            emit(handle, f"wrong applet loaded: id register reads "
                         f"0x{applet_id:08X}, expected 0x{APPLET_ID:08X}")
            emit(handle, f"log: {LOG}")
            return 1

        phase = status & 0xF
        done = (status >> 8) & 1
        emit(handle, f"sweep {'complete' if done else f'INCOMPLETE (stopped in phase {phase})'}; "
                     f"{total} total cycles "
                     f"({total / (clock_mhz * 1e6) * 1e3:.2f} ms)")
        emit(handle)

        header = (f"  {'chunk':>6} {'bytes':>7} {'wr cyc':>9} {'rd cyc':>9} "
                  f"{'write':>9} {'read':>9} {'combined':>9} "
                  f"{'% stream':>9} {'errors':>8}")
        emit(handle, header)
        emit(handle, "  " + "-" * (len(header) - 2))

        table = []
        for size, (wcyc, rcyc, errs) in zip(chunk_sizes, rows):
            # Bytes moved per phase in each direction; the combined figure is
            # the FIFO-relevant one, since a capture buffer must sustain both
            # halves through the same bus.
            wr_mbps = (phase_bytes / (wcyc / (clock_mhz * 1e6)) / 1e6
                       if wcyc else 0)
            rd_mbps = (phase_bytes / (rcyc / (clock_mhz * 1e6)) / 1e6
                       if rcyc else 0)
            total_cyc = wcyc + rcyc
            # Combined: total bytes through the bus over total time. This is
            # what a FIFO sees -- it is not the average of the two rates.
            comb_mbps = (2 * phase_bytes / (total_cyc / (clock_mhz * 1e6)) / 1e6
                         if total_cyc else 0)
            pct = comb_mbps / STREAM_READ_MBPS * 100
            table.append((size, wr_mbps, rd_mbps, comb_mbps, pct, errs))

            flag = "" if errs == 0 else "  <-- DATA ERRORS"
            emit(handle, f"  {size:>6} {size * BYTES_PER_WORD:>7} "
                         f"{wcyc:>9} {rcyc:>9} "
                         f"{wr_mbps:>7.1f}MB {rd_mbps:>7.1f}MB "
                         f"{comb_mbps:>7.1f}MB {pct:>8.1f}% {errs:>8}{flag}")

        emit(handle)
        emit(handle, f"streaming baseline: {STREAM_READ_MBPS} MB/s read, "
                     f"{STREAM_WRITE_MBPS} MB/s write (2048-word burst)")

        # Where the pattern stops costing much. Reported as the smallest
        # measured chunk that clears each bar, which is the number a FIFO
        # designer needs -- anything larger also clears it.
        emit(handle)
        for bar in (80, 90):
            clears = [row for row in table if row[4] >= bar and row[5] == 0]
            if clears:
                size, _, _, comb, pct, _ = clears[0]
                emit(handle, f"{bar}% of streaming: reached at {size} words "
                             f"({size * BYTES_PER_WORD} bytes), "
                             f"{comb:.1f} MB/s = {pct:.1f}%")
            else:
                emit(handle, f"{bar}% of streaming: not reached at any "
                             f"measured chunk size")

        usb_row = next((row for row in table if row[0] == 256), None)
        if usb_row:
            size, wr, rd, comb, pct, errs = usb_row
            emit(handle)
            emit(handle, f"USB bulk packet case (256 words = 512 bytes): "
                         f"{comb:.1f} MB/s combined, {pct:.1f}% of streaming, "
                         f"{errs} errors")
            emit(handle, f"  margin over fastest measured USB direction "
                         f"({USB_MBPS} MB/s): {comb / USB_MBPS:.1f}x")

        bad = [row for row in table if row[5] != 0]
        emit(handle)
        if bad:
            emit(handle, f"DATA ERRORS at {len(bad)} chunk size(s) -- "
                         f"throughput figures above are not trustworthy where "
                         f"errors are non-zero")
        else:
            total_words = len(chunk_sizes) * words_per_phase
            emit(handle, f"all {total_words} words verified across "
                         f"{len(chunk_sizes)} chunk sizes, zero mismatches")

        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
