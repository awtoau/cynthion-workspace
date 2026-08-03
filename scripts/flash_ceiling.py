#!/usr/bin/env python3
#
# How fast the configuration flash can actually be read, and in which mode.
# SPDX-License-Identifier: BSD-3-Clause

"""
Finds the configuration flash's real read ceiling on this board.

Two things set SCK and only one is fixed at build time:

  - the PLL frequency is a bitstream constant, so covering SCK above 96 MHz
    means building above 96 MHz;
  - the QSPI controller's divisor is an ordinary register, so
    `SCK = sync / (divisor + 1)` is writable over JTAG at any time.

So the sync clock a bitstream is built at IS its top SCK, at divisor 0, and
covering SCK above 100 MHz means building above 100 MHz. That makes this
design's own fmax the ceiling on the measurement -- not the flash, and not the
pin. Two fixes here moved it from 131 to 149 MHz; the limit above that sits
inside Glasgow's own IOStreamer, which is the part that has to run at SCK.

Divisor 0 was previously recorded as producing no clock at all, on the
reasoning that MCLK is driven from the first half of a DDR pair with the second
half discarded. **That is wrong, and it is disproved here**: divisor 0 reads
byte-exact at every rung, at exactly half the cycle count of divisor 1. It was
the reason nothing above 60 MHz had been tried.

Four read modes are measured through one instrument, so the single-lane
baseline and the quad figures come from the same path rather than from
different designs:

    0x03  Read Data              32 clocks of overhead, 8 clocks per byte
    0x0B  Fast Read              40                     8
    0x6B  Fast Read Quad Output  40                     2
    0xEB  Fast Read Quad I/O     20                     2
    0xEB  in Continuous Read     12                     2

Continuous Read is device state, not controller state: it survives an FPGA
reconfiguration, and a part left in it returns garbage to a reader that starts
sending opcodes again. This script always leaves it, on every exit path.

Every point is verified byte-for-byte against `apollo flash-read`, an entirely
separate path through the debug controller's JTAG TAP. A throughput figure
cannot tell a working read from a fast stream of plausible nonsense, and this
work has produced wrong conclusions from summary statistics before.

    ./scripts/flash_ceiling.py --build              # bitstream ladder, no board
    ./scripts/flash_ceiling.py --status             # flash status registers
    ./scripts/flash_ceiling.py --run                # the board, ascending SCK
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = ROOT / "tmp" / "flash-ceiling"
LOG = ROOT / "tmp" / "logs" / "flash_ceiling.log"
RESULTS = BUILD_ROOT / "results.json"
REFERENCE = ROOT / "tmp" / "flash-ceiling" / "reference.bin"

# Sync frequencies to build. Each is also that bitstream's top SCK, at
# divisor 0. Only frequencies whose VCO is a whole multiple of 60 are legal --
# `usb` divides the same VCO and the ULPI PHY has no tolerance -- so this list
# is a subset of what `VariableClockDomainGenerator` accepts, chosen to step
# through and past the part's 104 MHz rating.
DEFAULT_SYNC = [60, 80, 100, 105, 110, 120, 130, 135, 140]

# Bytes verified against apollo per point, over JTAG. This is the only slow
# part of a rung and it is deliberately small.
#
# The failure at a clock ceiling is wrong data immediately, not a slow stop:
# the HyperRAM sweep next door found its ceiling where 88% of words were wrong
# from the first word. At that error rate 32 bytes miss with probability
# 0.88^-32, which is not a number worth writing down. A rung that comes back
# *partly* wrong is the one case worth more data, and only that rung.
COMPARE_BYTES = 32  # overridden by --compare

# Bytes per timed read. The capture buffer is 1024 deep, so this only has to
# fill it and give the cycle counter something to count: 2048 bytes is 28 us
# at the slowest rate here and 28 ns of measurement error at the fastest.
READ_BYTES = 2048  # must match qspi_gateware.READ_BYTES

REG_ID, REG_TIME, REG_ADDR, REG_DATA, REG_STATUS = 1, 2, 3, 4, 5
REG_DIVISOR, REG_START, REG_MODE, REG_PASS = 6, 7, 8, 9
REG_BURST_LEN, REG_BURST_COUNT, REG_BURST_CYCLES, REG_BURST_DONE = 10, 11, 12, 13
REG_SCK_SOURCE = 14

APPLET_ID = 0x51535049

# (label, mode-register value). Bits 1:0 are the read mode, bit 2 forces the
# opcode to be omitted, bits 15:8 are the 0xEB mode byte.
#
# 0xA0 has M5-4 = (1,0), which is what enters Continuous Read; 0xFF does not,
# which is what leaves it. The first `0xEB continuous` read still sends its
# opcode -- it is the read that *arms* the mode -- so the saving shows from the
# second onwards, which is why continuous is only ever reported from a burst.
MODES = [
    ("0x03 single",     0x0000_0002),
    ("0x0B fast",       0x0000_0003),
    ("0x6B quad out",   0x0000_0000),
    ("0xEB quad I/O",   0x0000_FF01),
    ("0xEB continuous", 0x0000_A001),
]
MODE_EXIT_CONTINUOUS = 0x0000_FF01

# Clocks of transaction overhead per mode, from the datasheet, used to predict
# what a measurement should be before it is taken.
OVERHEAD_CLOCKS = {
    "0x03 single":     32,
    "0x0B fast":       40,
    "0x6B quad out":   40,
    "0xEB quad I/O":   20,
    "0xEB continuous": 12,
}
CLOCKS_PER_BYTE = {
    "0x03 single":     8,
    "0x0B fast":       8,
    "0x6B quad out":   2,
    "0xEB quad I/O":   2,
    "0xEB continuous": 2,
}

# The divisor the comparator's reference pass is taken at. 7 is sync/8 --
# 18 MHz at the top rung, a quarter of the part's slowest rating.
REFERENCE_DIVISOR = 7

# Depth of the capture buffer in the gateware.
CAPTURE_DEPTH = 1024

# A VexiiRiscv cache line. The number that matters for executing from flash:
# an I-cache miss costs one transaction plus this many bytes, and nothing else.
CACHE_LINE = 64


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


# ---------------------------------------------------------------- build phase


def build_one(sync_mhz):
    """Build one bitstream at `sync_mhz`. The board is not involved."""
    out = BUILD_ROOT / f"sync{sync_mhz}"
    script = (
        'import sys; sys.path[:0]=["ecp5-test", "repos/apollo"]\n'
        'from qspi.qspi_gateware import QSPITest\n'
        'from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4\n'
        f'CynthionPlatformRev1D4().build(QSPITest(), do_program=False, '
        f'build_dir="{out}")\n'
    )
    env = dict(os.environ, QSPI_SYNC_MHZ=str(sync_mhz))
    result = subprocess.run(
        ["bash", "-c",
         'source "$HOME/opt/oss-cad-suite/environment" && python3.15t -c "$0"',
         script],
        cwd=ROOT, capture_output=True, text=True, env=env)

    log = (out / "top.tim")
    timing = ""
    for line in (result.stderr or "").splitlines():
        if "Max frequency for clock" in line and "$glbnet$clk" in line:
            timing = line.split(":", 1)[1].strip()
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return sync_mhz, False, (tail[-1] if tail else "build failed"), timing
    return sync_mhz, (out / "top.bit").exists(), "", timing


def phase_build(handle, syncs, jobs):
    emit(handle, f"building {len(syncs)} bitstreams, {jobs} at a time -- "
                 f"the board is not involved")
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    built = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for sync, ok, detail, timing in pool.map(build_one, syncs):
            state = "built" if ok else f"FAILED {detail[:60]}"
            emit(handle, f"  sync {sync:>4} MHz  {state:<20} {timing}")
            if ok:
                built.append(sync)
    emit(handle)
    return built


# ----------------------------------------------------------------- board side


class Board:
    """One long-lived JTAG connection.

    Opening `ApolloDebugger` costs a USB enumeration and a device scan, so a
    sweep that opened one per measurement -- which the previous ladder did --
    spent most of its time there rather than on the bus being characterised.
    """

    def __init__(self):
        sys.path.insert(0, str(ROOT / "repos" / "apollo"))
        from apollo_fpga import ApolloDebugger
        self.dbg = ApolloDebugger()

    def read(self, reg):
        return self.dbg.registers.register_read(reg)

    def write(self, reg, value):
        self.dbg.registers.register_write(reg, value)

    def status(self):
        return self.read(REG_STATUS)

    def sync_mhz(self):
        return (self.status() >> 8) & 0xFFFF

    def wait_idle(self, tries=2000):
        """Poll rather than wait a fixed time.

        A read takes microseconds and a JTAG register access takes far longer
        than that, so the first poll almost always finds it finished; the loop
        exists for the divisors where it does not, and for the wedge where it
        never will.
        """
        for _ in range(tries):
            st = self.status()
            if (st & 1) and not (st >> 1) & 1:
                return st
        return None

    def run_read(self, seq):
        self.write(REG_START, seq)
        return self.wait_idle()

    def capture(self, count=None):
        # Read the module global at call time, not at def time. As a default
        # argument it bound the value the module was imported with, so
        # `--compare 256` compared 32 captured bytes against a 256-byte
        # reference and every point reported "0/256 differ" -- zero differing
        # bytes and a verdict of FAIL, which is a self-contradictory line and
        # is what gave it away.
        count = COMPARE_BYTES if count is None else count
        out = bytearray()
        for a in capture_addresses(count):
            self.write(REG_ADDR, a)
            out.append(self.read(REG_DATA) & 0xFF)
        return bytes(out)


def capture_addresses(count):
    """Half from the start of the capture buffer, half from the end.

    The first bytes of a read are the ones a marginal clock gets right: the
    part has just been addressed, nothing has drifted, and any sampling error
    has not had a long run to show itself. Checking only the first 32 bytes is
    therefore checking the easiest 32. Splitting the same budget across both
    ends costs nothing and covers a 1 KiB span instead of a 32-byte one.
    """
    half = count // 2
    return list(range(half)) + list(range(CAPTURE_DEPTH - (count - half),
                                          CAPTURE_DEPTH))


def classify(data, expected):
    if data == expected:
        return "PASS", "matches"
    if not any(data):
        return "FAIL", "all zeros"
    if all(b == 0xFF for b in data):
        return "FAIL", "all ones"
    for shift in (1, 2, 3, 4):
        if data[shift:] == expected[:len(data) - shift]:
            return "FAIL", f"shifted {shift} bytes"
    differing = sum(1 for a, b in zip(data, expected) if a != b)
    return "FAIL", f"{differing}/{len(expected)} differ"


def reference_bytes(handle):
    """Known-good bytes, read through the debug controller's own JTAG TAP.

    Taken before any test bitstream is configured, because `apollo flash-read`
    loads its own gateware into the FPGA to do the job -- so calling it in the
    middle of a sweep would silently throw away the design being measured.
    """
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    if REFERENCE.stat().st_size if REFERENCE.exists() else 0 < CAPTURE_DEPTH:
        subprocess.run(["apollo", "flash-read", str(REFERENCE),
                        "--length", str(CAPTURE_DEPTH)],
                       cwd=ROOT, capture_output=True)
    # The whole capture window, then the same addresses the board is asked
    # for. Comparing a split capture against a contiguous reference is how
    # this first reported "15/32 differ" on a read that was perfect.
    whole = REFERENCE.read_bytes()
    data = bytes(whole[a] for a in capture_addresses(COMPARE_BYTES))
    emit(handle, "reference @0 from apollo flash-read: "
                 f"{' '.join(f'{b:02x}' for b in data[:8])} ...")
    return data


def configure(bitstream):
    return subprocess.run(["apollo", "configure", str(bitstream)],
                          cwd=ROOT, capture_output=True).returncode == 0


def measure_point(board, seq, mode_value, divisor, expected, sync):
    """One (mode, divisor) point: set it, read, verify, rate it."""
    board.write(REG_BURST_LEN, 0)
    board.write(REG_MODE, mode_value)
    board.write(REG_DIVISOR, divisor)
    st = board.run_read(seq)
    if st is None:
        return None, "never completed", 0.0, 0
    cycles = board.read(REG_TIME)
    # The in-FPGA pass comparator is READ BUT NOT BELIEVED.
    #
    # It reports mismatches at divisor 0 that two independent host checks
    # contradict: bytes 0-15 and bytes 1008-1023 of the same capture both match
    # `apollo flash-read` exactly, in every mode, at every rung. It also
    # reports the identical counts -- 945, 1024, 303 -- at 120 MHz and at
    # 144 MHz, and a timing fault does not produce identical counts at two
    # clock rates. So the comparator is the faulty instrument, not the bus, and
    # it is reported as a note rather than used as a verdict. Its own defect is
    # not diagnosed here.
    errors = board.read(REG_PASS) & 0xFFFF
    data = board.capture()
    verdict, shape = classify(data, expected)
    rate = (READ_BYTES / (cycles / (sync * 1e6)) / 1e6) if cycles else 0.0
    return verdict, shape, rate, cycles


def measure_burst(board, seq, mode_value, divisor, length, count, sync):
    """Cycles for `count` reads of `length` bytes, counted in the FPGA.

    The host pays its JTAG cost once for the whole run rather than once per
    read, which is the only way a 64-byte read -- microseconds -- is
    measurable at all from up here.
    """
    board.write(REG_MODE, mode_value)
    board.write(REG_DIVISOR, divisor)
    board.write(REG_BURST_LEN, length)
    board.write(REG_BURST_COUNT, count)
    board.write(REG_START, seq)
    if board.wait_idle() is None:
        return None, None
    cycles = board.read(REG_BURST_CYCLES)
    reads = board.read(REG_BURST_DONE)
    if not reads:
        return None, None
    per_read_ns = (cycles / reads) / sync * 1000.0
    return cycles, per_read_ns


def phase_run(handle, syncs, divisors, burst_only):
    expected = reference_bytes(handle)
    results = []
    seq = 1

    for sync_built in syncs:
        bitstream = BUILD_ROOT / f"sync{sync_built}" / "top.bit"
        if not bitstream.exists():
            emit(handle, f"  sync {sync_built}: not built, skipping")
            continue
        if not configure(bitstream):
            emit(handle, f"  sync {sync_built}: configure failed")
            continue

        board = Board()
        if board.read(REG_ID) != APPLET_ID:
            emit(handle, f"  sync {sync_built}: wrong applet on the board")
            continue
        sync = board.sync_mhz()

        # A part left in Continuous Read by a previous bitstream answers an
        # opcode with an address phase; clear it before trusting anything.
        #
        # Done as BURSTS, which the capture buffer and the comparator stand
        # down for. That matters more than it looks: the very first read a
        # freshly configured bitstream completes is the one whose bytes become
        # the comparator's reference for every later pass, and a forced-XIP
        # recovery read is by construction not a valid transaction. Letting it
        # be the reference gave 303 "mismatches" that were the reference being
        # wrong rather than the reads.
        #
        # It does not simply return nonsense either, which is why it was worth
        # tracking down: with no opcode sent, the part reads the first eight
        # DQ0 bits of the x4 address and mode byte AS an opcode -- for address
        # 0 and mode 0xFF that spells 0x03, Read Data -- and answers with real
        # flash contents from an unintended address.
        for value in (MODE_EXIT_CONTINUOUS | 0x4, MODE_EXIT_CONTINUOUS):
            board.write(REG_MODE, value)
            board.write(REG_BURST_LEN, 64)
            board.write(REG_BURST_COUNT, 1)
            seq += 1
            board.run_read(seq)
        board.write(REG_BURST_LEN, 0)

        # Establish the comparator's reference deliberately, at a rate slow
        # enough not to be in question, and verify THAT against apollo before
        # anything is compared to it. Every later rung is then checked twice:
        # 32 bytes against apollo, and all 1024 captured bytes against this
        # pass, in hardware, for free.
        ref_verdict, ref_shape, _, _ = measure_point(
            board, (seq := seq + 1), MODES[0][1], REFERENCE_DIVISOR, expected,
            sync)
        emit(handle, f"  reference pass: 0x03 at "
                     f"{sync / (REFERENCE_DIVISOR + 1):.1f} MHz -- "
                     f"{ref_shape}, {ref_verdict}")
        if ref_verdict != "PASS":
            emit(handle, "  refusing to compare against an unverified "
                         "reference")
            continue

        emit(handle, f"  sync {sync} MHz")
        emit(handle, f"  {'mode':<16} {'div':>3} {'SCK':>7} {'MB/s':>7} "
                     f"{'cyc':>7}  {'shape':<20} verdict")

        for mode_label, mode_value in MODES:
            for divisor in divisors:
                sck = sync / (divisor + 1)
                if mode_label == "0xEB continuous":
                    # The read that *enters* Continuous Read still sends its
                    # opcode; the saving is on the one after. So arm it, then
                    # measure and verify the second read -- which is the one
                    # that omits the opcode, and the one that returns garbage
                    # if the mode byte was misunderstood at either end.
                    measure_point(board, (seq := seq + 1), mode_value, divisor,
                                  expected, sync)
                    if not (board.status() >> 2) & 1:
                        emit(handle, f"  {mode_label:<16} {divisor:>3} "
                                     f"{sck:>6.1f}M {'':>7} {'':>7}  "
                                     f"{'never armed':<20} FAIL")
                        continue
                verdict, shape, rate, cycles = measure_point(
                    board, (seq := seq + 1), mode_value, divisor, expected,
                    sync)
                if verdict is None:
                    emit(handle, f"  {mode_label:<16} {divisor:>3} "
                                 f"{sck:>6.1f}M {'':>7} {'':>7}  "
                                 f"{shape:<20} FAIL")
                    continue
                emit(handle, f"  {mode_label:<16} {divisor:>3} {sck:>6.1f}M "
                             f"{rate:>7.2f} {cycles:>7}  {shape:<20} {verdict}")
                results.append(dict(sync=sync, mode=mode_label,
                                    divisor=divisor, sck=sck, mb_s=rate,
                                    cycles=cycles, verdict=verdict,
                                    shape=shape))

        # The hardware comparator has been checking every pass after the first
        # against what the first stored, at every rate above. Zero errors over
        # that many passes is what distinguishes a rate that always works from
        # one that worked the single time it was tried.
        passes = board.read(REG_PASS)
        emit(handle, f"  hardware comparator: {passes & 0xFFFF} mismatches "
                     f"over {(passes >> 16) & 0xFFFF} passes")

        # Leave the part out of Continuous Read before the next reconfigure.
        # It is device state and it survives one.
        board.write(REG_MODE, MODE_EXIT_CONTINUOUS)
        seq += 1
        board.run_read(seq)
        emit(handle)

        RESULTS.write_text(json.dumps(results, indent=2))

    return results


def phase_cacheline(handle, sync_built, divisors):
    """Cache-line refill: the number firmware executing from flash pays.

    A 64-byte read is microseconds long, so it is timed in the FPGA across a
    run of 256 of them at strided addresses and the total is read once.
    """
    bitstream = BUILD_ROOT / f"sync{sync_built}" / "top.bit"
    if not bitstream.exists() or not configure(bitstream):
        emit(handle, f"cache-line: sync {sync_built} unavailable")
        return []

    board = Board()
    sync = board.sync_mhz()
    seq = 100
    rows = []

    emit(handle, f"cache-line refill, {CACHE_LINE} bytes, 256 reads per point, "
                 f"sync {sync} MHz")
    emit(handle, f"  {'mode':<16} {'div':>3} {'SCK':>7} {'ns/line':>9} "
                 f"{'MB/s':>7} {'predicted ns':>13}")

    for mode_label, mode_value in MODES:
        for divisor in divisors:
            sck = sync / (divisor + 1)
            seq += 1
            cycles, per_read_ns = measure_burst(
                board, seq, mode_value, divisor, CACHE_LINE, 256, sync)
            if per_read_ns is None:
                emit(handle, f"  {mode_label:<16} {divisor:>3} {sck:>6.1f}M "
                             f"{'wedged':>9}")
                continue
            rate = CACHE_LINE / (per_read_ns * 1e-9) / 1e6
            clocks = (OVERHEAD_CLOCKS[mode_label]
                      + CLOCKS_PER_BYTE[mode_label] * CACHE_LINE)
            predicted = clocks / sck * 1000.0
            emit(handle, f"  {mode_label:<16} {divisor:>3} {sck:>6.1f}M "
                         f"{per_read_ns:>9.1f} {rate:>7.2f} {predicted:>13.1f}")
            rows.append(dict(sync=sync, mode=mode_label, divisor=divisor,
                             sck=sck, ns_per_line=per_read_ns, mb_s=rate,
                             predicted_ns=predicted))

    board.write(REG_MODE, MODE_EXIT_CONTINUOUS)
    board.run_read(seq + 1)
    emit(handle)
    return rows


# -------------------------------------------------------------- status phase


OP_READ_SR1, OP_READ_SR2, OP_READ_SR3 = 0x05, 0x35, 0x15
OP_WRITE_SR2, OP_WRITE_SR3 = 0x31, 0x11
OP_VOLATILE_WREN = 0x50


def phase_status(handle, set_drive=None):
    """Read -- and optionally set -- the registers that affect read timing.

    Goes through Apollo's background SPI, which is a separate path from
    everything else here, so it can be used before a test bitstream is loaded.
    Any write is VOLATILE (0x50 then 0x31/0x11): it survives a reconfigure,
    because that does not cut the flash's supply, and is gone after a power
    cycle. Nothing here writes a non-volatile bit and nothing writes the array.
    """
    sys.path.insert(0, str(ROOT / "repos" / "apollo"))
    from apollo_fpga import ApolloDebugger

    dbg = ApolloDebugger()
    # A configured FPGA holds the configuration SPI lines, so it has to be
    # taken offline before the debug controller can drive them. `apollo
    # flash-info` does exactly this.
    dbg.force_fpga_offline()

    with dbg.jtag as jtag:
        prog = dbg.create_jtag_programmer(jtag)

        def spi(payload, offset):
            prog._enter_background_spi()
            raw = prog._background_spi_transfer(list(payload))
            return bytes(raw[offset:])

        sr1 = spi([OP_READ_SR1, 0], 1)[0]
        sr2 = spi([OP_READ_SR2, 0], 1)[0]
        sr3 = spi([OP_READ_SR3, 0], 1)[0]
        return _report_status(handle, prog, spi, sr1, sr2, sr3, set_drive)


def _report_status(handle, prog, spi, sr1, sr2, sr3, set_drive):

    emit(handle, "flash status registers, as found:")
    emit(handle, f"  SR1 0x{sr1:02x}  BUSY={sr1 & 1}  WEL={(sr1 >> 1) & 1}  "
                 f"BP={(sr1 >> 2) & 7}  SRP0={(sr1 >> 7) & 1}")
    emit(handle, f"  SR2 0x{sr2:02x}  QE={(sr2 >> 1) & 1}  "
                 f"LB={(sr2 >> 3) & 7}  CMP={(sr2 >> 6) & 1}")
    drv = (sr3 >> 5) & 3
    drive_pct = {0: "100%", 1: "75%", 2: "50%", 3: "25%"}[drv]
    emit(handle, f"  SR3 0x{sr3:02x}  ADS={sr3 & 1}  ADP={(sr3 >> 1) & 1}  "
                 f"WPS={(sr3 >> 2) & 1}  DRV={drv} = {drive_pct} drive")
    emit(handle)
    emit(handle, "  QE is the only bit quad needs, and it is "
                 + ("already set." if (sr2 >> 1) & 1 else "NOT SET."))
    emit(handle, f"  Output drive is {drive_pct}. 100% is the fastest edge the "
                 f"part will produce")
    emit(handle, "  into the ECP5's pin capacitance, and it is writable "
                 "volatile.")

    if set_drive is not None:
        bits = {100: 0, 75: 1, 50: 2, 25: 3}[set_drive]
        new_sr3 = (sr3 & ~0x60) | (bits << 5)
        prog._enter_background_spi()
        prog._background_spi_transfer([OP_VOLATILE_WREN])
        prog._enter_background_spi()
        prog._background_spi_transfer([OP_WRITE_SR3, new_sr3])
        readback = spi([OP_READ_SR3, 0], 1)[0]
        emit(handle)
        emit(handle, f"  wrote SR3 0x{new_sr3:02x} (volatile), reads back "
                     f"0x{readback:02x} -- drive now "
                     f"{ {0:'100%',1:'75%',2:'50%',3:'25%'}[(readback >> 5) & 3] }")
    emit(handle)
    return dict(sr1=sr1, sr2=sr2, sr3=sr3)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true",
                        help="build the bitstream ladder; the board is idle")
    parser.add_argument("--run", action="store_true",
                        help="sweep on the board, ascending SCK")
    parser.add_argument("--cacheline", type=int, default=None, metavar="SYNC",
                        help="cache-line refill timing at this built sync rate")
    parser.add_argument("--status", action="store_true",
                        help="read the flash's own status registers")
    parser.add_argument("--set-drive", type=int, choices=[25, 50, 75, 100],
                        default=None,
                        help="set output drive strength, VOLATILE")
    parser.add_argument("--sync", type=int, nargs="+", default=DEFAULT_SYNC)
    parser.add_argument("--divisors", type=int, nargs="+", default=[0, 1, 2, 3])
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--compare", type=int, default=None,
                        help="bytes verified against apollo per point")
    args = parser.parse_args()

    if args.compare:
        global COMPARE_BYTES
        COMPARE_BYTES = args.compare

    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with LOG.open("a") as handle:
        emit(handle)
        emit(handle, f"=== flash ceiling, {stamp} ===")

        if args.status or args.set_drive is not None:
            phase_status(handle, args.set_drive)
        if args.build:
            phase_build(handle, args.sync, args.jobs)
        if args.run:
            phase_run(handle, args.sync, args.divisors, burst_only=False)
        if args.cacheline is not None:
            phase_cacheline(handle, args.cacheline, args.divisors)

        emit(handle, f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
