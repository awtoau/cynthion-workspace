#!/usr/bin/env python3
#
# What the FPGA_ADV sideband costs the shipping bitstream, in ECP5 cells.
# SPDX-License-Identifier: BSD-3-Clause

"""
Synthesises the sideband and prints the cell counts, so the case for trimming it
rests on a number rather than an impression.

    ./scripts/sideband_cost.py

| variant | what it is |
|---|---|
| `link` | `SidebandLink` -- PING, STATUS, a byte each way |
| `advertise` | `SidebandAdvertiser` -- the CONTROL port request |
| `shipping` | both, as `sideband_debug.py` wires them |
| `responder` | `SidebandResponder`, as the SoC used to drive it |
| `responder_full` | the same with flash, HyperRAM and power actually sourced |

`responder` against `link` is what the trim saves. `responder_full` against
`responder` is what the removed commands would have cost had anything ever driven
them -- worth reporting separately, because the SoC never did, so the fields were
already constant-folded and the two numbers are not the same measurement.

The `responder*` variants need `repos/apollo` populated and are skipped when it is
not, so this runs in a worktree and reports what it could measure.

Synthesis only -- no place and route, no board. Counts are `synth_ecp5` cell
counts after optimisation, which is the level at which constant propagation has
happened and a tied-off input is visibly free.

Output goes to the console and to tmp/logs/sideband_cost.log.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from amaranth import Elaboratable, Module, Signal
from amaranth.back import rtlil
from amaranth.hdl import Fragment

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "ecp5-test"))

from sideband_advertise import SidebandAdvertiser  # noqa: E402
from sideband_link      import SidebandLink        # noqa: E402

try:
    from apollo_fpga.gateware.sideband import SidebandResponder
except ImportError:                     # repos/apollo not populated
    SidebandResponder = None

LOG = ROOT / "tmp" / "logs" / "sideband_cost.log"
WORK = ROOT / "tmp" / "sideband_cost"

CLK_HZ = 60e6

# The four things `synth_ecp5` puts in a slice's LUT positions, summed as
# "logic". TRELLIS_FF is reported separately. `$scopeinfo` is metadata, not a
# cell, and is in neither.
LOGIC = ("LUT4", "CCU2C", "PFUMX", "L6MUX21")

# LUT4 positions in the LFE5U-12F on r1.4. A cell count means nothing without the
# denominator: the difference between "expensive" and "a rounding error" is this
# number, and it is the one the trim argument turns on.
DEVICE_LOGIC = 12144


class Variant(Elaboratable):
    """One sideband configuration, with every input driven from a module port.

    Ports rather than constants is the whole method: a port cannot be
    constant-folded, so the difference between two variants is exactly the logic
    that sourcing a field requires.
    """

    def __init__(self, *, link=False, advertise=False, responder=False,
                 sources=False):
        self.want_link = link
        self.want_advertise = advertise
        self.want_responder = responder
        self.sources = sources

        self.rx = Signal(init=1)
        self.pad_o = Signal()
        self.pad_oe = Signal()
        self.state = Signal(2)
        self.events = Signal()
        self.error = Signal()
        self.reconfigured = Signal()
        self.message = Signal(8)
        self.received = Signal(7)
        self.received_strobe = Signal()
        self.power_data = Signal(128)
        self.flash_id = Signal(24)
        self.flash_valid = Signal()
        self.hyperram_present = Signal()
        self.want_port = Signal()

    def elaborate(self, platform):
        m = Module()
        drivers = []
        hold = Signal()

        if self.want_link:
            m.submodules.link = dut = SidebandLink(clk_freq_hz=CLK_HZ)
            m.d.comb += [
                dut.rx.eq(self.rx),
                dut.state.eq(self.state),
                dut.events.eq(self.events),
                dut.error.eq(self.error),
                dut.reconfigured.eq(self.reconfigured),
                dut.message.eq(self.message),
                self.received.eq(dut.received),
                self.received_strobe.eq(dut.received_strobe),
                hold.eq(dut.tx_active),
            ]
            drivers.append(dut)

        if self.want_responder:
            m.submodules.responder = dut = SidebandResponder(clk_freq_hz=CLK_HZ)
            m.d.comb += [
                dut.rx.eq(self.rx),
                dut.state.eq(self.state),
                dut.events.eq(self.events),
                dut.error.eq(self.error),
                dut.reconfigured.eq(self.reconfigured),
            ]
            if self.sources:
                m.d.comb += [
                    dut.power_data.eq(self.power_data),
                    dut.flash_manufacturer.eq(self.flash_id[0:8]),
                    dut.flash_memory_type.eq(self.flash_id[8:16]),
                    dut.flash_capacity.eq(self.flash_id[16:24]),
                    dut.flash_valid.eq(self.flash_valid),
                    dut.hyperram_present.eq(self.hyperram_present),
                ]
            drivers.append(dut)

        if self.want_advertise:
            m.submodules.advertiser = adv = SidebandAdvertiser(clk_freq_hz=CLK_HZ)
            m.d.comb += [
                adv.rx.eq(self.rx),
                adv.enable.eq(self.want_port),
                adv.hold.eq(hold),
            ]
            drivers.append(adv)

        # Open-drain everywhere, so sharing the pad is an OR.
        pad_o = pad_oe = 0
        for driver in drivers:
            pad_o = pad_o | driver.pad_o
            pad_oe = pad_oe | driver.pad_oe
        m.d.comb += [self.pad_o.eq(pad_o), self.pad_oe.eq(pad_oe)]

        return m


def synthesise(name, dut, ports):
    WORK.mkdir(parents=True, exist_ok=True)
    il = WORK / f"{name}.il"
    il.write_text(rtlil.convert(Fragment.get(dut, None), name=name,
                                ports=ports))

    proc = subprocess.run(
        ["yosys", "-p", f"read_rtlil {il}; synth_ecp5 -top {name}; stat"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"yosys failed for {name}:\n{proc.stdout[-4000:]}")

    # The LAST `=== name ===` block is the flattened design; earlier ones are
    # per-pass reports and would double-count.
    blocks = proc.stdout.split(f"=== {name} ===")
    if len(blocks) < 2:
        raise SystemExit(f"no statistics for {name}; yosys printed:\n"
                         f"{proc.stdout[-2000:]}")

    counts = {}
    for line in blocks[-1].splitlines():
        found = re.match(r"\s+(\d+)\s+([A-Z][A-Z0-9_]*)\s*$", line)
        if found:
            counts[found.group(2)] = int(found.group(1))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []

    def emit(text=""):
        print(text)
        lines.append(text)

    variants = {
        "link":      Variant(link=True),
        "advertise": Variant(advertise=True),
        "shipping":  Variant(link=True, advertise=True),
    }
    if SidebandResponder is not None:
        variants["responder"] = Variant(responder=True)
        variants["responder_full"] = Variant(responder=True, sources=True)

    results = {}
    for name, dut in variants.items():
        ports = [dut.rx, dut.pad_o, dut.pad_oe, dut.state, dut.events,
                 dut.error, dut.reconfigured, dut.message, dut.received,
                 dut.received_strobe, dut.power_data, dut.flash_id,
                 dut.flash_valid, dut.hyperram_present, dut.want_port]
        results[name] = synthesise(name, dut, ports)

    def logic(counts):
        return sum(counts.get(cell, 0) for cell in LOGIC)

    def ff(counts):
        return counts.get("TRELLIS_FF", 0)

    emit(f"ECP5 cells after synth_ecp5, sideband at {CLK_HZ/1e6:.0f} MHz")
    emit()
    emit(f"  {'variant':<16}{'logic':>8}{'FF':>8}   " + "  ".join(LOGIC))
    for name, counts in results.items():
        row = f"  {name:<16}{logic(counts):>8}{ff(counts):>8}   "
        row += "  ".join(f"{counts.get(cell, 0):>{len(cell)}}" for cell in LOGIC)
        emit(row)

    emit()
    if "responder" in results:
        emit(f"  the trim saves                "
             f"{logic(results['responder']) - logic(results['link']):+5} logic, "
             f"{ff(results['responder']) - ff(results['link']):+5} FF")
        emit(f"  sourcing the removed fields would have cost "
             f"{logic(results['responder_full']) - logic(results['responder']):+5}"
             f" logic, "
             f"{ff(results['responder_full']) - ff(results['responder']):+5} FF")
    else:
        emit("  repos/apollo not populated -- the `responder` variants were "
             "skipped,")
        emit("  so what the trim saves was not measured in this tree.")
    emit(f"  the port request adds         "
         f"{logic(results['shipping']) - logic(results['link']):+5} logic, "
         f"{ff(results['shipping']) - ff(results['link']):+5} FF")
    emit()
    emit(f"  shipping sideband, net        "
         f"{logic(results['shipping']):>5} logic, "
         f"{ff(results['shipping']):>5} FF")
    emit(f"                                "
         f"{100 * logic(results['shipping']) / DEVICE_LOGIC:>5.1f}% of an "
         f"LFE5U-12F")

    LOG.write_text("\n".join(lines) + "\n")
    print(f"\nlog: {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
