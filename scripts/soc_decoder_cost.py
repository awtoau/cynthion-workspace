#!/usr/bin/env python3
#
# What drives the Wishbone decoder's cell count. #453.
# SPDX-License-Identifier: BSD-3-Clause

"""What an `amaranth_soc.wishbone.Decoder` costs, and which knob moves it.

`top.decoder` is 2,149 COMB and 0 FF in the shipping netlist (#446) -- larger
than the whole USB device -- and that issue could not say what drives it. This
synthesises the decoder ALONE and varies one thing at a time:

  * window count, at a fixed window size
  * the read-data gather (subordinates driving `dat_r` live, or tied to zero)
  * window geometry: the shipping map's own bases and sizes, against the same
    number of windows packed contiguously
  * the decoder's address width
  * optional bus features, which decide whether a `_FeatureShim` is built

Out of context, so the absolute numbers are an upper bound in the sense
`scripts/soc_peripheral_area.py` means. The SLOPE is what survives, and the
`geometry` case is the control that says whether this model describes the real
decoder at all: configured with the shipping map's 12 windows it must land near
the netlist's 2,149, and if it does not, nothing else here is worth reading.

    ./scripts/soc_decoder_cost.py
    ./scripts/soc_decoder_cost.py --only count

Output is mirrored to ./tmp/logs/soc_decoder_cost.log.
"""

import argparse
import re
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "tmp" / "decoder-cost"
LOG = ROOT / "tmp" / "logs" / "soc_decoder_cost.log"

sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth import Module, Signal                       # noqa: E402
from amaranth.back import verilog                         # noqa: E402
from amaranth.lib import wiring                           # noqa: E402
from amaranth.lib.wiring import In, Out                   # noqa: E402
from amaranth_soc import wishbone                         # noqa: E402
from amaranth.hdl import UnusedElaboratable               # noqa: E402
from amaranth_soc.memory import MemoryMap                 # noqa: E402

# A resource is a Component here only because `add_resource` demands one; none
# of them is elaborated, so "never used" is the expected outcome, not news.
warnings.filterwarnings("ignore", category=UnusedElaboratable)

# The shipping map, read out of `scripts/soc_map_audit.py`'s window table:
# (name, base, size in bytes). Two 4 MiB windows, two multi-MiB memories and
# eight small CSR windows.
SHIPPING = [
    ("ram",            0x00000000, 64 * 1024),
    ("spiflash",       0x10000000, 4 * 1024 * 1024),
    ("hyperram",       0x20000000, 8 * 1024 * 1024),
    ("console",        0xf0000000, 8),
    ("spi0",           0xf0000100, 64),
    ("flash_probe",    0xf0000200, 32),
    ("hyperram_probe", 0xf0000280, 64),
    ("bootram",        0xf0000400, 32),
    ("apollo_uart",    0xf0000500, 8),
    ("board",          0xf0000600, 128),
    ("plic",           0xf0400000, 4 * 1024 * 1024),
    ("clint",          0xf0800000, 64 * 1024),
]

# What `top.py` builds: 30-bit word address, 32-bit data, byte granularity,
# and the three optional signals.
FEATURES = frozenset({"cti", "bte", "err"})


class Sub(wiring.Component):
    """A subordinate that ACKs and drives `dat_r` from a port, or from zero."""

    def __init__(self, size, *, live):
        self._live = live
        addr_width = max((size - 1).bit_length() - 2, 0)
        signature = {
            "bus": In(wishbone.Signature(addr_width=addr_width, data_width=32,
                                         granularity=8)),
        }
        if live:
            signature["data"] = In(32)
        super().__init__(signature)
        memory_map = MemoryMap(addr_width=max(addr_width + 2, 1), data_width=8)
        memory_map.add_resource(_Held(), name=("held",), size=size)
        self.bus.memory_map = memory_map

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.bus.ack.eq(self.bus.cyc & self.bus.stb)
        if self._live:
            m.d.comb += self.bus.dat_r.eq(self.data)
        return m


class _Held(wiring.Component):
    def __init__(self):
        super().__init__({})


class OrDecoder(wishbone.Decoder):
    """Upstream's decoder with the read-data MUX replaced by an OR fan-in.

    `csr.Decoder` already gathers this way: each subordinate's `r_data` is ORed
    because the bus is defined to output zero when idle. The Wishbone decoder
    instead assigns `dat_r` inside the `m.Case`, which is a 32-bit N:1 mux with
    a 30-bit select -- and the mapper fuses the window compare into every one of
    the 32 bits. Gating each subordinate's data with its own select and ORing is
    the same function.
    """

    def elaborate(self, platform):
        from amaranth import Cat, Signal
        from amaranth.utils import exact_log2
        from amaranth.lib.wiring import connect
        from amaranth_soc.wishbone.bus import _FeatureShim

        m = Module()
        granularity_bits = exact_log2(self.bus.data_width // self.bus.granularity)
        dat_r, ack, err = 0, 0, 0

        for index, (sub_map, _name, (pattern, ratio)) in \
                enumerate(self.bus.memory_map.window_patterns()):
            sub_bus = self._subs[sub_map]
            shim = _FeatureShim(sub_bus.addr_width, sub_bus.data_width,
                                sub_bus.granularity,
                                intr_features=self.bus.features,
                                sub_features=sub_bus.features)
            m.submodules[f"shim{index}"] = shim
            connect(m, shim.sub_bus, sub_bus)

            select = Signal(name=f"sel{index}")
            m.d.comb += select.eq(
                self.bus.adr.matches(pattern[:-granularity_bits or None]))
            m.d.comb += [
                shim.intr_bus.adr.eq(self.bus.adr << exact_log2(ratio)),
                shim.intr_bus.dat_w.eq(self.bus.dat_w),
                shim.intr_bus.sel.eq(Cat(one.replicate(ratio)
                                         for one in self.bus.sel)),
                shim.intr_bus.we.eq(self.bus.we),
                shim.intr_bus.stb.eq(self.bus.stb),
                shim.intr_bus.cyc.eq(self.bus.cyc & select),
            ]
            for optional in ("lock", "cti", "bte"):
                if hasattr(self.bus, optional):
                    m.d.comb += getattr(shim.intr_bus, optional).eq(
                        getattr(self.bus, optional))
            dat_r = dat_r | shim.intr_bus.dat_r.replicate(1) & select.replicate(32)
            ack = ack | (shim.intr_bus.ack & select)
            if hasattr(self.bus, "err"):
                err = err | (shim.intr_bus.err & select)

        m.d.comb += [self.bus.dat_r.eq(dat_r), self.bus.ack.eq(ack)]
        if hasattr(self.bus, "err"):
            m.d.comb += self.bus.err.eq(err)
        return m


class Harness(wiring.Component):
    """One decoder, `windows` subordinates, everything else at the boundary."""

    def __init__(self, windows, *, addr_width=30, live=True, features=FEATURES,
                 decoder=wishbone.Decoder):
        self._windows = windows
        self._addr_width = addr_width
        self._live = live
        self._features = features
        self._decoder = decoder
        super().__init__({
            "bus": In(wishbone.Signature(addr_width=addr_width, data_width=32,
                                         granularity=8, features=features)),
            **{f"data{index}": In(32) for index in range(len(windows) if live else 0)},
        })

    def elaborate(self, platform):
        m = Module()
        decoder = self._decoder(addr_width=self._addr_width, data_width=32,
                                granularity=8, features=self._features)
        m.submodules.decoder = decoder
        for index, (name, base, size) in enumerate(self._windows):
            sub = Sub(size, live=self._live)
            m.submodules[f"sub{index}"] = sub
            decoder.add(sub.bus, addr=base, name=name)
            if self._live:
                m.d.comb += sub.data.eq(getattr(self, f"data{index}"))
        wiring.connect(m, wiring.flipped(self.bus), decoder.bus)
        return m


def synth(name, dut):
    BUILD.mkdir(parents=True, exist_ok=True)
    source = BUILD / f"{name}.v"
    source.write_text(verilog.convert(dut, name=name))
    proc = subprocess.run(
        ["yosys", "-p", f"read_verilog {source}; synth_ecp5 -top {name}; stat"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip()[-2000:])
    cells = {}
    for line in proc.stdout.rsplit(f"=== {name} ===", 1)[-1].splitlines():
        match = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if match:
            cells[match.group(2)] = int(match.group(1))
    comb = sum(cells.get(kind, 0)
               for kind in ("LUT4", "PFUMX", "L6MUX21", "CCU2C"))
    return comb, cells


def packed(count, size=8, base=0xf0000000):
    """`count` windows of `size` bytes, back to back and aligned."""
    return [(f"w{index}", base + index * size, size) for index in range(count)]


def spread(count, size=8, base=0xf0000000, stride=0x100):
    """`count` windows of `size` bytes, one per `stride` -- the shipping shape."""
    return [(f"w{index}", base + index * stride, size) for index in range(count)]


CASES = {
    "count": lambda emit: sweep(emit, "windows of 8 B, packed", [
        (str(count), Harness(packed(count))) for count in (1, 2, 4, 8, 12, 16, 24)]),
    "gather": lambda emit: sweep(emit, "12 windows, read-data gather on/off", [
        ("live dat_r", Harness(packed(12))),
        ("dat_r tied 0", Harness(packed(12), live=False))]),
    "geometry": lambda emit: sweep(emit, "12 windows, where they sit", [
        ("shipping map", Harness(SHIPPING)),
        ("same, packed 8 B", Harness(packed(12))),
        ("same, 0x100 apart", Harness(spread(12))),
        ("same, 4 KiB each packed", Harness(packed(12, size=4096)))]),
    # Based at 0 so the same twelve windows fit in every space.
    "addr_width": lambda emit: sweep(emit, "12 packed windows, decoder width", [
        (f"{width} bits", Harness(packed(12, base=0), addr_width=width))
        for width in (30, 26, 20, 14)]),
    "gather_shape": lambda emit: sweep(
        emit, "the shipping map, MUX gather against OR gather", [
            ("upstream Decoder", Harness(SHIPPING)),
            ("OR fan-in", Harness(SHIPPING, decoder=OrDecoder)),
            ("upstream, 6 windows", Harness(SHIPPING[:6])),
            ("OR fan-in, 6 windows", Harness(SHIPPING[:6], decoder=OrDecoder))]),
    "features": lambda emit: sweep(emit, "12 packed windows, optional signals", [
        ("cti+bte+err", Harness(packed(12))),
        ("err only", Harness(packed(12), features=frozenset({"err"}))),
        ("none", Harness(packed(12), features=frozenset()))]),
}


def sweep(emit, title, rows):
    emit(f"  {title}")
    emit(f"    {'case':<24} {'COMB':>7} {'LUT4':>7} {'MUX':>7} {'FF':>5}")
    emit("    " + "-" * 54)
    first = None
    for index, (label, dut) in enumerate(rows):
        comb, cells = synth(f"dec{abs(hash(title)) % 9973}_{index}", dut)
        first = comb if first is None else first
        emit(f"    {label:<24} {comb:>7} {cells.get('LUT4', 0):>7} "
             f"{cells.get('PFUMX', 0) + cells.get('L6MUX21', 0):>7} "
             f"{cells.get('TRELLIS_FF', 0):>5}")
    emit()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=sorted(CASES), action="append")
    args = parser.parse_args()

    out = []

    def emit(line=""):
        print(line, flush=True)
        out.append(line)

    emit("  decoder alone, out of context. COMB = LUT4 + PFUMX + L6MUX21 + CCU2C")
    emit()
    for name in (args.only or list(CASES)):
        CASES[name](emit)

    emit("  CONTROL: one window must be near zero, and the `shipping map` row of")
    emit("  `geometry` must land near the 2,149 COMB the real netlist reports for")
    emit("  `top.decoder` (#446). A model that misses that is not this decoder.")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
