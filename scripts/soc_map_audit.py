#!/usr/bin/env python3
#
# How much of the address space this SoC decodes is actually mapped. #452.
# SPDX-License-Identifier: BSD-3-Clause

"""What the SoC decodes, what it maps, and what an unmapped read does.

Reads the elaborated `AwtoSoc.decoder.bus.memory_map` and the region list handed
to VexiiRiscv, and sorts every byte of the 4 GiB space into one of four classes:

  * **NO REGION** -- outside every PMA region. VexiiRiscv refuses it in the LSU
    and traps; no bus cycle happens.
  * **ERR** -- in a PMA region, in no decoder window. `amaranth_soc`'s decoder
    answers nothing at all here (its `m.Switch` has no `m.Default()`), so this
    would hang the initiator forever; `bus/fault.BusFault` is what turns it into
    an ERR in the cycle of the request.
  * **SILENT** -- inside a decoder window, behind no resource. The window claims
    it, so `BusFault` sees a claimed address and the timeout never fires, and
    `WishboneCSRBridge` acks unconditionally after its byte cycles. A read
    returns zero and a write is dropped, with no error anywhere. This is the
    class nothing catches.
  * **MAPPED** -- a resource answers.

    ./scripts/soc_map_audit.py
    ./scripts/soc_map_audit.py --json      # for scripts/soc_review.py

## Controls, run before any figure is printed

  1. A synthetic decoder with three windows at known addresses, whose mapped,
     unmapped and per-gap figures are asserted against hand-computed constants.
  2. The same decoder with one window removed -- the analyser must report the
     window gone and the unmapped total larger. An enumerator that silently
     misses a window reports a tidy map, and this is the case that catches it.
  3. A simulated read of a hole inside a CSR window, which must ACK with zero
     and no ERR -- the SILENT class above, demonstrated rather than asserted.

Output is mirrored to ./tmp/logs/soc_map_audit.log.
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_map_audit.log"

sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

SPACE = 1 << 32

# A resource must be a Component and none of the synthetic ones is elaborated,
# so "never used" is the expected outcome, not news.
from amaranth.hdl import UnusedElaboratable                # noqa: E402
warnings.filterwarnings("ignore", category=UnusedElaboratable)


# --- the map ---------------------------------------------------------------

def windows_of(memory_map):
    """(name, start, end) for every TOP-LEVEL window, in address order.

    Top level only: these are the decoder's own `m.Case` patterns, which is what
    decides whether an address is claimed at all.
    """
    out = []
    for window, name, (start, end, ratio) in memory_map.windows():
        if ratio != 1:
            raise NotImplementedError(f"window {name} is sparse (ratio {ratio})")
        out.append(("/".join(str(part) for part in name) if name else "<anon>",
                    start, end, window))
    return sorted(out, key=lambda entry: entry[1])


def resources_in(window, base):
    """Absolute (start, end) of every resource under `window`."""
    return [(base + info.start, base + info.end)
            for info in window.all_resources()]


def merge(spans):
    """Sorted, non-overlapping spans."""
    out = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def total(spans):
    return sum(end - start for start, end in spans)


def analyse(memory_map, regions=(), space=SPACE):
    """Everything this script prints, as numbers, from one memory map.

    `regions` is a list of (base, size) the CPU will issue a bus cycle for.
    """
    windows, mapped = [], []
    for name, start, end, sub in windows_of(memory_map):
        held = merge(resources_in(sub, start))
        windows.append({"name": name, "base": start, "end": end,
                        "size": end - start, "held": total(held),
                        "silent": (end - start) - total(held)})
        mapped += held
    claimed = merge([(entry["base"], entry["end"]) for entry in windows])
    mapped = merge(mapped)
    region_span = merge([(base, base + size) for base, size in regions])

    # Every byte, once: in a window and held; in a window and not; in a region
    # and in no window; in no region at all.
    in_window = total(claimed)
    held = total(mapped)
    reachable = total(merge([(max(a, c), min(b, d))
                             for a, b in region_span for c, d in claimed
                             if max(a, c) < min(b, d)]))
    return {
        "windows": windows,
        "space": space,
        "claimed": in_window,
        "mapped": held,
        "silent": in_window - held,
        "region": total(region_span),
        # In a region, in no window: the decoder answers nothing, BusFault ERRs.
        "err": total(region_span) - reachable,
        "no_region": space - total(region_span),
        "gaps": [(windows[i]["end"], windows[i + 1]["base"])
                 for i in range(len(windows) - 1)
                 if windows[i + 1]["base"] > windows[i]["end"]],
    }


# --- the real design -------------------------------------------------------

def build_soc():
    """The elaborated SoC and the regions its CPU was told about."""
    import cpu.cpu as vexii_cpu

    seen = []
    real_init = vexii_cpu.VexiiRiscv.__init__

    def record(self, *args, regions=None, **kwargs):
        seen.append(list(regions or vexii_cpu.DEFAULT_REGIONS))
        return real_init(self, *args, regions=regions, **kwargs)

    vexii_cpu.VexiiRiscv.__init__ = record
    try:
        warnings.filterwarnings("ignore")
        import soc_generate_pac
        soc, _platform = soc_generate_pac.build_soc()
    finally:
        vexii_cpu.VexiiRiscv.__init__ = real_init
    if not seen:
        raise SystemExit("no VexiiRiscv was constructed -- the region list is "
                         "this script's only authority on what the CPU issues")
    return soc, parse_regions(seen[-1])


def parse_regions(regions):
    """`base=...,size=...,main=..` strings -> [(base, size, flags)]."""
    out = []
    for text in regions:
        fields = dict(part.split("=", 1) for part in text.split(","))
        out.append((int(fields["base"], 16), int(fields["size"], 16),
                    {key: value for key, value in fields.items()
                     if key not in ("base", "size")}))
    return out


# --- controls --------------------------------------------------------------

def synthetic(drop=None):
    """A decoder with three windows at addresses whose arithmetic is known."""
    from amaranth.lib import wiring
    from amaranth_soc import wishbone
    from amaranth_soc.memory import MemoryMap

    class _Dummy(wiring.Component):
        # `add_resource` demands a Component; nothing here is elaborated.
        def __init__(self):
            super().__init__({})

    decoder = wishbone.Decoder(addr_width=14, data_width=32, granularity=8)
    plan = [("a", 0x0000, 8, 0x10), ("b", 0x1000, 8, 0x100), ("c", 0x8000, 12, None)]
    for name, addr, bits, held in plan:
        if name == drop:
            continue
        sub = wishbone.Interface(addr_width=bits - 2, data_width=32,
                                 granularity=8)
        sub_map = MemoryMap(addr_width=bits, data_width=8)
        sub_map.add_resource(_Dummy(), name=(f"r_{name}",),
                             size=held or (1 << bits))
        sub.memory_map = sub_map
        decoder.add(sub, addr=addr, name=name)
    return decoder.bus.memory_map


def control(emit):
    """The analyser must get a known map right, and must notice a missing one."""
    ok = True
    emit("CONTROL -- a map whose answer is known, and the same map with a hole")

    # Windows 0x0000+0x100, 0x1000+0x100, 0x8000+0x1000 in a 64 KiB space, with
    # 0x10 / 0x100 / 0x1000 bytes of resource behind them.
    full = analyse(synthetic(), regions=[(0, 0x10000)], space=0x10000)
    want = {"claimed": 0x100 + 0x100 + 0x1000, "mapped": 0x10 + 0x100 + 0x1000,
            "silent": 0xf0, "err": 0x10000 - (0x100 + 0x100 + 0x1000),
            "no_region": 0}
    for key, value in want.items():
        good = full[key] == value
        ok &= good
        emit(f"  {'PASS' if good else 'FAIL':<6} {key:<12} "
             f"{full[key]:>8} (want {value})")
    good = len(full["windows"]) == 3
    ok &= good
    emit(f"  {'PASS' if good else 'FAIL':<6} {'windows':<12} "
         f"{len(full['windows']):>8} (want 3)")

    # NEGATIVE: one window removed. The analyser must lose it AND report more
    # unmapped space -- an enumerator that misses a window reports a tidy map.
    holed = analyse(synthetic(drop="b"), regions=[(0, 0x10000)], space=0x10000)
    checks = [("window b gone", "b" not in {w["name"] for w in holed["windows"]}),
              ("claimed falls by 0x100", full["claimed"] - holed["claimed"] == 0x100),
              ("err rises by 0x100", holed["err"] - full["err"] == 0x100)]
    for label, good in checks:
        ok &= good
        emit(f"  {'PASS' if good else 'FAIL':<6} {label}")

    emit()
    return bool(ok)


def silent_read_control(emit):
    """Simulate a read of a hole inside a CSR window. It must ACK with zero."""
    from amaranth import Module
    from amaranth.lib import wiring
    from amaranth.sim import Simulator
    from amaranth_soc import csr
    from amaranth_soc.csr.wishbone import WishboneCSRBridge

    class Block(wiring.Component):
        def __init__(self):
            builder = csr.Builder(addr_width=5, data_width=8)
            self._reg = csr.Register({"v": csr.Field(csr.action.R, 32)}, access="r")
            builder.add("live", self._reg, offset=0)
            self._bridge = csr.Bridge(builder.as_memory_map())
            self._wb = WishboneCSRBridge(self._bridge.bus, data_width=32)
            super().__init__({})
            self.bus = self._wb.wb_bus

        def elaborate(self, platform):
            m = Module()
            m.submodules.bridge = self._bridge
            m.submodules.wb = self._wb
            m.d.comb += self._reg.f.v.r_data.eq(0xdecafbad)
            return m

    dut = Block()
    seen = {}

    async def testbench(ctx):
        for label, word in (("register at +0x00", 0), ("hole at +0x10", 4)):
            ctx.set(dut.bus.adr, word)
            ctx.set(dut.bus.sel, 0xf)
            ctx.set(dut.bus.cyc, 1)
            ctx.set(dut.bus.stb, 1)
            for cycle in range(16):
                await ctx.tick()
                if ctx.get(dut.bus.ack):
                    seen[label] = (cycle + 1, ctx.get(dut.bus.dat_r))
                    break
            else:
                seen[label] = (None, None)
            ctx.set(dut.bus.cyc, 0)
            ctx.set(dut.bus.stb, 0)
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    emit("CONTROL -- what a read of a hole INSIDE a window does, simulated")
    ok = True
    for label, (cycles, data) in seen.items():
        emit(f"    {label:<20} ack after {cycles} cycles, dat_r "
             f"{data:#010x}" if cycles else f"    {label:<20} NO ACK")
    good = seen["register at +0x00"][1] == 0xdecafbad
    ok &= good
    emit(f"  {'PASS' if good else 'FAIL':<6} the mapped register reads back")
    good = seen["hole at +0x10"][0] is not None and seen["hole at +0x10"][1] == 0
    ok &= good
    emit(f"  {'PASS' if good else 'FAIL':<6} the hole ACKs with zero -- no ERR, "
         f"no timeout, nothing to see")
    emit()
    return bool(ok)


# --- report ----------------------------------------------------------------

def si(count):
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if count >= size:
            exact = count // size if count % size == 0 else round(count / size, 2)
            return f"{exact} {unit}"
    return f"{count} B"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true",
                        help="machine-readable, for scripts/soc_review.py")
    parser.add_argument("--control-only", action="store_true")
    args = parser.parse_args()

    out = []

    def emit(line=""):
        if not args.json:
            print(line)
        out.append(line)

    passed = control(emit) and silent_read_control(emit)
    if not passed:
        emit("CONTROL FAILED -- no figure below is worth reading")
        return 1
    if args.control_only:
        return 0

    soc, regions = build_soc()
    facts = analyse(soc.decoder.bus.memory_map,
                    regions=[(base, size) for base, size, _f in regions])

    # Independent path: the decoder switches on `window_patterns()`, and
    # `BusFault` compares the same list. A window this script missed would show
    # up as a disagreement here rather than as a tidy map.
    patterns = len(list(soc.decoder.bus.memory_map.window_patterns()))
    if patterns != len(facts["windows"]):
        raise SystemExit(f"enumerated {len(facts['windows'])} windows but the "
                         f"decoder switches on {patterns} -- the enumerator is "
                         f"wrong, not the design")

    emit(f"  PMA regions handed to VexiiRiscv ({len(regions)}):")
    for base, size, flags in sorted(regions):
        emit(f"    {base:#010x}..{base + size - 1:#010x}  {si(size):>8}  "
             f"{','.join(f'{k}={v}' for k, v in flags.items())}")
    emit()

    emit(f"  decoder windows ({len(facts['windows'])}), "
         f"cross-checked against {patterns} decoder cases:")
    emit(f"    {'window':<16} {'base':>10} {'end':>10} {'size':>9} "
         f"{'held':>8} {'silent':>8}  gap to next")
    emit("    " + "-" * 78)
    entries = facts["windows"]
    for index, entry in enumerate(entries):
        after = (entries[index + 1]["base"] - entry["end"]
                 if index + 1 < len(entries) else None)
        emit(f"    {entry['name']:<16} {entry['base']:>#10x} "
             f"{entry['end'] - 1:>#10x} {si(entry['size']):>9} "
             f"{si(entry['held']):>8} {si(entry['silent']):>8}  "
             f"{si(after) if after else '--'}")
    emit()

    emit("  every byte of the 32-bit space, once:")
    for label, key, what in (
            ("MAPPED    ", "mapped", "a resource answers"),
            ("SILENT    ", "silent", "in a window, behind nothing: ACK + zero"),
            ("ERR       ", "err", "in a PMA region, in no window: BusFault ERR"),
            ("NO REGION ", "no_region", "outside every PMA region: CPU traps")):
        value = facts[key]
        emit(f"    {label} {si(value):>10}  {100 * value / facts['space']:>7.3f}%"
             f"  {what}")
    emit()
    emit(f"    windows claim {si(facts['claimed'])}; resources hold "
         f"{si(facts['mapped'])} of it "
         f"({100 * facts['mapped'] / facts['claimed']:.1f}%)")
    emit(f"    the CPU may issue a cycle to {si(facts['region'])}; "
         f"{100 * facts['claimed'] / facts['region']:.3f}% of that is decoded")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    if args.json:
        print(json.dumps({key: value for key, value in facts.items()
                          if key != "windows"} | {"windows": facts["windows"]},
                         indent=2))
    else:
        print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
