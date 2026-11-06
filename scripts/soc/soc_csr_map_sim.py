#!/usr/bin/env python3
#
# Simulate the one CSR bridge: every window reachable, every hole an ERR.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks `bus/csr_bridge.CsrBridge` behind a `csr.Decoder`. #619.

    python3 scripts/soc/soc_csr_map_sim.py
    python3 scripts/soc/soc_csr_map_sim.py -v

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

- Windows are stubs that answer `value(window, byte)` on reads and count every
  strobe they see, so a wrong window, a lane swap or a double strobe is visible.
- The window list is the SoC's own CSR map (`AwtoSoc.csr_decoder`), the same
  object `soc_generate_pac.py` documents; a synthetic map is checked first.
- The initiator is a classic Wishbone master, as VexiiRiscv's is.

## What this cannot say

The peripherals' registers are not exercised here (their own sims do that), and
neither is the CPU: this says the bridge puts each access on the right window.
"""

import argparse
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: F401  (scripts/ subdirectories on sys.path)
from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth import Module, Signal           # noqa: E402
from amaranth.hdl import Fragment, UnusedElaboratable  # noqa: E402
from amaranth.lib import wiring               # noqa: E402
from amaranth.lib.wiring import In, Out       # noqa: E402
from amaranth.sim import Simulator            # noqa: E402
from amaranth.utils import exact_log2         # noqa: E402
from amaranth_soc import csr, wishbone        # noqa: E402
from amaranth_soc.memory import MemoryMap     # noqa: E402

from bus.csr_bridge import CsrBridge          # noqa: E402

# `add_resource` wants a Component as the resource; it is never elaborated.
warnings.filterwarnings("ignore", category=UnusedElaboratable)

FEATURES = {"cti", "bte", "err"}

# A mapped access is four byte cycles and the ack: `cycle` counts 0..4.
ACK_CYCLES = 5
# Waiting for an answer: ERR in 1, ACK in 5. 8 = 5 + slack to see a late one;
# on expiry the check fails, naming the access.
GIVE_UP = 8

# (name, byte offset, byte size) -- sizes and alignments like the real map's:
# sub-word windows, a mid-size one, a big one far from the rest.
SYNTHETIC = [("a", 0x000, 8), ("b", 0x500, 8), ("c", 0x624, 2),
             ("d", 0x620, 2), ("e", 0x800, 128), ("f", 0x1000, 32),
             ("g", 0x10000, 0x10000)]
SYNTHETIC_ADDR_WIDTH = 17


def value(index, offset):
    """What window `index` returns for byte `offset`: never 0, distinct per window."""
    return ((index + 1) * 0x2b + offset * 0x11 + 1) & 0xff or 0x5a


class Stub(wiring.Component):
    """A CSR window that reads `value()` one cycle after the strobe and counts."""

    def __init__(self, index, size):
        width = max(exact_log2(size), 1)
        super().__init__({
            "bus": In(csr.Signature(addr_width=width, data_width=8)),
            "reads": Out(16), "writes": Out(16),
            "last_w_addr": Out(width + 1), "last_w_data": Out(8),
        })
        self._index = index
        self.bus.memory_map = MemoryMap(addr_width=width, data_width=8)
        self.bus.memory_map.add_resource(_Anchor(), name=("reg",), size=size)

    def elaborate(self, platform):
        m = Module()
        bus = self.bus
        with m.If(bus.r_stb):
            m.d.sync += self.reads.eq(self.reads + 1)
        with m.If(bus.w_stb):
            m.d.sync += [self.writes.eq(self.writes + 1),
                         self.last_w_addr.eq(bus.addr),
                         self.last_w_data.eq(bus.w_data)]
        m.d.sync += bus.r_data.eq(0)
        with m.If(bus.r_stb):
            with m.Switch(bus.addr):
                for off in range(1 << len(bus.addr)):
                    with m.Case(off):
                        m.d.sync += bus.r_data.eq(value(self._index, off))
        return m


class _Anchor(wiring.Component):
    def __init__(self):
        super().__init__({})


class Dut(wiring.Component):
    """Stubs on a `csr.Decoder`, behind one `CsrBridge`, behind a Wishbone window."""

    def __init__(self, windows, addr_width):
        super().__init__({"wb": In(wishbone.Signature(
            addr_width=addr_width - 2, data_width=32, granularity=8,
            features=FEATURES))})
        self._windows = windows
        self.csr_decoder = csr.Decoder(addr_width=addr_width, data_width=8)
        self.stubs = []
        for index, (name, base, size) in enumerate(windows):
            stub = Stub(index, size)
            self.stubs.append(stub)
            self.csr_decoder.add(stub.bus, addr=base, name=name)
        self.bridge = CsrBridge(self.csr_decoder.bus)

    def elaborate(self, platform):
        m = Module()
        m.submodules.csr_decoder = self.csr_decoder
        m.submodules.bridge = self.bridge
        for index, stub in enumerate(self.stubs):
            m.submodules[f"stub{index}"] = stub
        wiring.connect(m, wiring.flipped(self.wb), self.bridge.wb_bus)
        return m


class Master:
    """A classic Wishbone initiator: hold CYC/STB until ACK or ERR."""

    def __init__(self, ctx, dut, verbose):
        self.ctx, self.dut, self.verbose = ctx, dut, verbose

    async def access(self, byte_addr, write=False, data=0):
        """(outcome, read data, cycles to the answer); outcome is ack/err/none."""
        ctx, wb = self.ctx, self.dut.wb
        ctx.set(wb.adr, byte_addr >> 2)
        ctx.set(wb.sel, 0xf)
        ctx.set(wb.we, int(write))
        ctx.set(wb.dat_w, data)
        ctx.set(wb.cyc, 1)
        ctx.set(wb.stb, 1)
        outcome, cycles = "none", 0
        while cycles < GIVE_UP:
            if ctx.get(wb.err):
                outcome = "err"
                break
            await ctx.tick()
            cycles += 1
            if ctx.get(wb.ack):
                outcome = "ack"
                break
        got = ctx.get(wb.dat_r)
        ctx.set(wb.cyc, 0)
        ctx.set(wb.stb, 0)
        await ctx.tick()
        await ctx.tick()
        if self.verbose:
            print(f"      {'write' if write else 'read '} {byte_addr:#07x} "
                  f"-> {outcome} {got:#010x} in {cycles}")
        return outcome, got, cycles

    def strobes(self):
        ctx = self.ctx
        return sum(ctx.get(s.reads) + ctx.get(s.writes) for s in self.dut.stubs)


def expected_word(index, base, size, word_addr):
    """The 32-bit value a read at `word_addr` returns from window `index`."""
    out = 0
    for lane in range(4):
        offset = word_addr + lane - base
        if 0 <= offset < size:
            out |= value(index, offset) << (8 * lane)
    return out


def sample_words(base, size):
    """First eight words, the last two, and the middle: all of a small window."""
    first = base & ~3
    last = (base + size - 1) & ~3
    words = list(range(first, min(first + 32, last + 4), 4))
    words += [w for w in (last - 4, last, (first + last) // 8 * 4) if first <= w <= last]
    return sorted(set(words))


def holes(windows, limit):
    """Word addresses below `limit` that no window overlaps, one per gap edge."""
    taken = sorted((b & ~3, (b + s - 1) & ~3) for _, b, s in windows)
    found, cursor = [], 0
    for first, last in taken:
        if cursor < first:
            found += [cursor, first - 4, (cursor + first) // 8 * 4]
        cursor = max(cursor, last + 4)
    if cursor < limit:
        found += [cursor, limit - 4]
    return sorted(set(found))


def run_map_checks(checks, label, windows, addr_width, verbose):
    emit(f"{label}: {len(windows)} windows, {1 << addr_width:#x} bytes of CSR space")
    dut = Dut(windows, addr_width)
    results = {}

    async def testbench(ctx):
        master = Master(ctx, dut, verbose)
        for index, (name, base, size) in enumerate(windows):
            bad, strobes_ok, cycles_seen = [], True, set()
            for word in sample_words(base, size):
                before = master.strobes()
                outcome, got, cycles = await master.access(word)
                cycles_seen.add(cycles)
                if outcome != "ack" or got != expected_word(index, base, size, word):
                    bad.append((hex(word), outcome, hex(got)))
                # Only bytes inside a window are strobed into a stub.
                inside = sum(1 for lane in range(4) if base <= word + lane < base + size)
                strobes_ok &= master.strobes() - before == inside
            results[name] = (bad, strobes_ok, cycles_seen)

        write_results = {}
        for index, (name, base, size) in enumerate(windows):
            stub = dut.stubs[index]
            word = base & ~3
            before_all, before = master.strobes(), ctx.get(stub.writes)
            outcome, _, _ = await master.access(word, write=True, data=0xa1b2c3d4)
            inside = [l for l in range(4) if base <= word + l < base + size]
            wrote = ctx.get(stub.writes) - before
            last = (ctx.get(stub.last_w_addr), ctx.get(stub.last_w_data))
            lane = inside[-1]
            want = (word + lane - base, (0xa1b2c3d4 >> (8 * lane)) & 0xff)
            write_results[name] = (outcome == "ack" and wrote == len(inside)
                                   and master.strobes() - before_all == len(inside)
                                   and last == want, outcome, wrote, last, want)
        results["__writes"] = write_results

        gap = {}
        for word in holes(windows, 1 << addr_width):
            for write in (False, True):
                before = master.strobes()
                outcome, got, cycles = await master.access(word, write=write)
                gap[(word, write)] = (outcome, cycles, master.strobes() - before)
        results["__holes"] = gap

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    for name, _, _ in windows:
        bad, strobes_ok, cycles_seen = results[name]
        checks.check(f"{label}: {name} reads back at its address", not bad,
                     detail=f"{bad[:3]}")
        checks.check(f"{label}: {name} strobed once per byte", strobes_ok)
        checks.check(f"{label}: {name} answers in {ACK_CYCLES} cycles",
                     cycles_seen == {ACK_CYCLES}, detail=f"{sorted(cycles_seen)}")
    for name, (ok, outcome, wrote, last, want) in results["__writes"].items():
        checks.check(f"{label}: {name} write reaches its window", ok,
                     detail=f"{outcome} wrote={wrote} last={last} want={want}")
    gap = results["__holes"]
    bad = {k: v for k, v in gap.items() if v[0] != "err" or v[1] != 0 or v[2] != 0}
    checks.check(f"{label}: all {len(gap)} hole accesses answer ERR, strobe nothing",
                 not bad and bool(gap),
                 detail=f"{[(hex(k[0]), k[1], v) for k, v in list(bad.items())[:4]]}")


def real_soc_windows():
    """(name, base, size) of the SoC's own CSR decoder, and its address width."""
    import soc_generate_pac as pac
    soc, _ = pac.build_soc()
    csr_map = soc.csr_decoder.bus.memory_map
    windows = []
    for _, name, (start, end, _) in csr_map.windows():
        windows.append(("_".join(str(p) for p in name), start, end - start))
    return windows, soc.csr_decoder.bus.addr_width


def run_soc_checks(checks, verbose):
    try:
        windows, addr_width = real_soc_windows()
    except AttributeError:
        checks.check("the SoC exposes its csr_decoder", False,
                     detail="AwtoSoc has no csr_decoder")
        return
    run_map_checks(checks, "SoC map", windows, addr_width, verbose)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--synthetic-only", action="store_true",
                        help="skip the SoC's own map (no SoC elaboration)")
    args = parser.parse_args()

    checks = Checks(emit)
    run_map_checks(checks, "synthetic", SYNTHETIC, SYNTHETIC_ADDR_WIDTH, args.verbose)
    if not args.synthetic_only:
        emit()
        run_soc_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
