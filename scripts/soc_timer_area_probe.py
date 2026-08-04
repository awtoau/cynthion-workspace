#!/usr/bin/env python3
"""Synthesise N periodic comparators for the ECP5 and report what they cost.

The gateware half of `docs/soc-concurrency-models.md`. The firmware half --
`scripts/soc_model_probe.py` -- measures what a software timer queue costs in
`.text`; this measures what replacing it with hardware costs in LUTs.

Two shapes, because they are not the same peripheral:

    mtimecmp    a 64-bit comparator against `mtime`, which is what
                `ecp5-test/riscv/vexii_clint.py` already has one of and what
                `rtic_time::Monotonic` and FreeRTOS's RISC-V port both assume
    reload      a 32-bit auto-reloading down-counter, which is what
                `firmware/cynthion-soc/src/bin/model_coop_hwtimer.rs` assumes:
                no 64-bit carry chain, and no rollover to reason about because
                the counter never reaches an absolute time

**This is AREA, not timing.** Synthesis out of context gives cell counts that
are meaningful and an Fmax that is not: the design closes 69.5 MHz against a 60
MHz target with these cells placed among 13,591 others, and nothing here places
anything. The Fmax cost of adding comparators to the SoC is NOT MEASURED and
this script cannot measure it -- see the doc.

Writes ./tmp/logs/soc_timer_area_probe.log as well as stdout.
"""
import re
import subprocess
import sys
from pathlib import Path

from amaranth import Module, Signal, unsigned
from amaranth.back import verilog
from amaranth.hdl import Elaboratable
from amaranth.lib.wiring import In, Out, Signature

WORKSPACE = Path(__file__).resolve().parent.parent
BUILD = WORKSPACE / "tmp/timer-area"
LOG = WORKSPACE / "tmp/logs/soc_timer_area_probe.log"


class Comparators(Elaboratable):
    """N 64-bit `mtimecmp` registers against one shared `mtime`.

    The shape `vexii_clint.py` already implements once. Each comparator is a
    64-bit register and a 64-bit unsigned compare, and the interrupt is the
    level `mtime >= mtimecmp` -- the specification's own definition, and what
    makes a handler that does not move the deadline re-enter forever.
    """

    def __init__(self, count):
        self.count = count
        self.mtime = Signal(64)
        self.write = Signal(range(max(2 * count, 2)))
        self.write_en = Signal()
        self.write_data = Signal(32)
        self.irq = Signal(count)

    def elaborate(self, platform):
        m = Module()
        for index in range(self.count):
            cmp = Signal(64, init=2**64 - 1, name=f"mtimecmp{index}")
            with m.If(self.write_en & (self.write == 2 * index)):
                m.d.sync += cmp[:32].eq(self.write_data)
            with m.If(self.write_en & (self.write == 2 * index + 1)):
                m.d.sync += cmp[32:].eq(self.write_data)
            m.d.comb += self.irq[index].eq(self.mtime >= cmp)
        return m


class Reloads(Elaboratable):
    """N 32-bit auto-reloading down-counters.

    No absolute time anywhere: a counter is loaded with a period, counts to
    zero, raises a level, and reloads. The level is cleared by a write to its
    own acknowledge bit, which is the only thing firmware has to do -- there is
    no deadline to compare, no period to add, and no set-in-the-past case,
    because the counter that fired is the one that is due.
    """

    def __init__(self, count):
        self.count = count
        self.write = Signal(range(max(count, 2)))
        self.write_en = Signal()
        self.write_data = Signal(32)
        self.ack = Signal(count)
        self.irq = Signal(count)

    def elaborate(self, platform):
        m = Module()
        for index in range(self.count):
            reload = Signal(32, name=f"reload{index}")
            counter = Signal(32, name=f"counter{index}")
            pending = Signal(name=f"pending{index}")
            with m.If(self.write_en & (self.write == index)):
                m.d.sync += reload.eq(self.write_data)
                m.d.sync += counter.eq(self.write_data)
            with m.Elif(counter == 0):
                m.d.sync += counter.eq(reload)
                m.d.sync += pending.eq(1)
            with m.Else():
                m.d.sync += counter.eq(counter - 1)
            with m.If(self.ack[index]):
                m.d.sync += pending.eq(0)
            m.d.comb += self.irq[index].eq(pending)
        return m


def synth(name: str, dut, ports) -> dict[str, int]:
    """Yosys `synth_ecp5` on one module, and the cell counts it reports."""
    BUILD.mkdir(parents=True, exist_ok=True)
    source = BUILD / f"{name}.v"
    source.write_text(verilog.convert(dut, name=name, ports=ports))
    proc = subprocess.run(
        ["yosys", "-p", f"read_verilog {source}; synth_ecp5 -top {name}; stat"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip()[-2000:])
    # `stat` prints "  <count>   <CELL>" under the last "=== <top> ===" block,
    # and the earlier per-pass blocks would otherwise be counted too.
    cells: dict[str, int] = {}
    tail = proc.stdout.rsplit(f"=== {name} ===", 1)[-1]
    for line in tail.splitlines():
        match = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if match and match.group(2).startswith(("LUT", "TRELLIS", "CCU")):
            cells[match.group(2)] = int(match.group(1))
    return cells


def main() -> int:
    out: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        out.append(line)

    # The design as it stands, for the fractions below.
    total_lut, total_dff = 24288, 24288
    used_lut, used_dff = 13591, 7482

    for label, factory, ports in (
        ("mtimecmp (64-bit)", Comparators, ["mtime", "write", "write_en",
                                            "write_data", "irq"]),
        ("reload (32-bit)", Reloads, ["write", "write_en", "write_data",
                                      "ack", "irq"]),
    ):
        emit()
        emit(f"{label}")
        emit(f"  {'timers':>7} {'LUT4':>6} {'CCU2C':>6} {'LUT4 eq':>8} "
             f"{'DFF':>6} {'of free LUT':>12}")
        emit("  " + "-" * 52)
        for count in (1, 2, 4, 8):
            dut = factory(count)
            cells = synth(f"{factory.__name__.lower()}{count}", dut,
                          [getattr(dut, port) for port in ports])
            luts = cells.get("LUT4", 0)
            # A CCU2C is the carry-chain cell and occupies two LUT4 positions,
            # which is how nextpnr's own utilisation line counts it.
            ccu = cells.get("CCU2C", 0)
            equivalent = luts + 2 * ccu
            dffs = cells.get("TRELLIS_FF", 0)
            share = 100.0 * equivalent / (total_lut - used_lut)
            emit(f"  {count:>7} {luts:>6} {ccu:>6} {equivalent:>8} "
                 f"{dffs:>6} {share:>11.2f}%")

    emit()
    emit(f"the design uses {used_lut}/{total_lut} LUT4 ({100 * used_lut // total_lut}%) "
         f"and {used_dff}/{total_dff} DFF ({100 * used_dff // total_dff}%)")
    emit()
    emit("Read the DFF column first: it is exact, and it is a register count that")
    emit("does not depend on how anything maps. The LUT4 column is out-of-context")
    emit("mapping and is not even monotonic in the timer count for the reload")
    emit("shape, so take its ORDER and not its value.")
    emit("The Fmax cost in context is NOT MEASURED -- nothing here is placed.")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
