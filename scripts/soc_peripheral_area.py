#!/usr/bin/env python3
#
# What each SoC peripheral costs, synthesised on its own.
# SPDX-License-Identifier: BSD-3-Clause

"""Synthesise every peripheral in `gateware/soc/` alone and report its area.

Written for `docs/soc-size-review.md`, which needs a per-module number for
every "this could be deleted" claim. The alternative -- building the whole SoC
once per candidate -- is about two minutes a build and cannot be done at all
for a module the shipping top does not instantiate.

**These are OUT-OF-CONTEXT numbers and they are an UPPER BOUND, not the
delta.** Synthesised alone, a peripheral keeps logic that would be optimised
away in the SoC: constant inputs are not folded in, unread outputs are not
pruned, and the CSR read multiplexer is not merged with its neighbours. A
module that measures 300 LUT4 here does NOT mean the SoC shrinks by 300 when it
is removed. It means it cannot shrink by more.

The one number here that is exact is DFF: a register is a register wherever it
is placed, and nothing about the surrounding design changes how many of them a
module declares. Read that column first. `scripts/soc_timer_area_probe.py`
makes the same distinction for the hypothetical timer shapes it measures, and
this shares its `synth()` technique.

For a real delta, build the SoC both ways and diff `Info: Device utilisation`
in `tmp/awto_soc/build/<variant>/top.tim` -- and run `scripts/pnr_noise.py` before
believing any Fmax difference, because this design has closed anywhere between
71.45 and 76.99 MHz across identical builds.

    ./scripts/soc_peripheral_area.py
    ./scripts/soc_peripheral_area.py --only uart16550,flash_ila

Output is mirrored to ./tmp/logs/soc_peripheral_area.log.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RISCV = ROOT / "gateware" / "soc"
BUILD = ROOT / "tmp" / "peripheral-area"
LOG = ROOT / "tmp" / "logs" / "soc_peripheral_area.log"

sys.path.insert(0, str(RISCV))
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth.back import verilog                        # noqa: E402
from amaranth.hdl import Fragment                        # noqa: E402


# Every peripheral the SoC instantiates, plus the two it carries and does not.
#
# The constructor is a thunk rather than the class, because importing all of
# them eagerly costs a luna_soc import even when `--only` names one module.
def _peripherals():
    """(name, thunk, note) for each module, in the order the review reads them."""
    def uart16550():
        from peripherals.uart16550 import Uart16550
        return Uart16550()

    def plic():
        from cpu.plic import Plic
        return Plic(sources=5)

    def clint():
        from cpu.clint import Clint
        return Clint()

    def i2c_master():
        from peripherals.i2c_master import I2CMaster
        return I2CMaster()

    def i2c_mux():
        from peripherals.i2c_mux import I2CBusMux
        return I2CBusMux()

    def sideband_csr():
        from peripherals.sideband_csr import SidebandControl
        return SidebandControl()

    def vbus_csr():
        from peripherals.vbus_csr import VbusControl
        return VbusControl()

    def gateware_id():
        from peripherals.gateware_id import GatewareId
        return GatewareId(sync_hz=60_000_000, usb_hz=60_000_000,
                          cache_sets=64)

    def ulpi_window():
        from peripherals.ulpi_window import UlpiRegisters
        return UlpiRegisters()

    def serial_line():
        from peripherals.serial_line import SerialLine
        return SerialLine(divisor=520, data_bits=8)

    def stream_buffer_sync():
        from peripherals.stream_buffer import StreamBuffer
        return StreamBuffer(depth=64)

    def stream_buffer_async():
        from peripherals.stream_buffer import StreamBuffer
        return StreamBuffer(depth=16, i_domain="sync", o_domain="usb")

    def wishbone_pipe():
        from bus.wishbone_pipe import RegisteredResponse
        return RegisteredResponse(addr_width=30, data_width=32,
                                  granularity=8,
                                  features={"cti", "bte", "err"})

    def flash_probe():
        from peripherals.flash import FlashPinProbe
        return FlashPinProbe()

    def flash_ila():
        from peripherals.flash import FlashILA
        return FlashILA(sample_depth=1024)

    def hyperram_probe():
        from peripherals.hyperram_probe import HyperRAMProbe
        return HyperRAMProbe()

    return [
        ("uart16550",          uart16550,          "x2 in the SoC"),
        ("plic",               plic,               "5 sources"),
        ("clint",              clint,              ""),
        ("i2c_master",         i2c_master,         ""),
        ("i2c_mux",            i2c_mux,            ""),
        ("sideband_csr",       sideband_csr,       ""),
        ("vbus_csr",           vbus_csr,           ""),
        ("gateware_id",        gateware_id,        "no DTR without a platform"),
        ("ulpi_window",        ulpi_window,        ""),
        ("serial_line",        serial_line,        ""),
        ("stream_buffer_sync", stream_buffer_sync, "depth 64"),
        ("stream_buffer_async", stream_buffer_async, "depth 16, CDC"),
        ("wishbone_pipe",      wishbone_pipe,      ""),
        ("flash_probe",        flash_probe,        "instrumentation"),
        ("flash_ila",          flash_ila,          "instrumentation, 1 DP16KD"),
        ("hyperram_probe",     hyperram_probe,     "instrumentation"),
    ]


def synth(name, dut):
    """Yosys `synth_ecp5` on one component, and the cells it reports.

    The component is converted through `Fragment.get(...).prepare()` with no
    explicit port list, so `wiring.Component` members become top-level ports and
    nothing is optimised away for being unconnected.
    """
    BUILD.mkdir(parents=True, exist_ok=True)
    source = BUILD / f"{name}.v"
    source.write_text(verilog.convert(dut, name=name))

    proc = subprocess.run(
        ["yosys", "-p", f"read_verilog {source}; synth_ecp5 -top {name}; stat"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-1500:])

    cells = {}
    # `stat` prints one block per module; the last "=== <top> ===" is the whole
    # design including submodules, and the earlier blocks would double-count.
    tail = proc.stdout.rsplit(f"=== {name} ===", 1)[-1]
    for line in tail.splitlines():
        match = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if match:
            cells[match.group(2)] = int(match.group(1))
    return cells


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated module names")
    args = parser.parse_args()

    wanted = set(args.only.split(",")) if args.only else None
    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    emit(f"  {'module':<20} {'LUT4':>6} {'CCU2C':>6} {'LUT4 eq':>8} "
         f"{'DFF':>6} {'DP16KD':>7}  note")
    emit("  " + "-" * 76)

    total_eq = total_dff = 0
    for name, thunk, note in _peripherals():
        if wanted is not None and name not in wanted:
            continue
        try:
            cells = synth(name, thunk())
        except Exception as exc:                      # noqa: BLE001
            emit(f"  {name:<20} {'--':>6} {'--':>6} {'--':>8} {'--':>6} "
                 f"{'--':>7}  {type(exc).__name__}: {str(exc).splitlines()[-1][:40]}")
            continue
        luts = cells.get("LUT4", 0)
        # A CCU2C is the carry-chain cell and occupies two LUT4 positions, which
        # is how nextpnr's own utilisation line counts it.
        ccu = cells.get("CCU2C", 0)
        equivalent = luts + 2 * ccu
        dffs = cells.get("TRELLIS_FF", 0)
        bram = cells.get("DP16KD", 0)
        total_eq += equivalent
        total_dff += dffs
        emit(f"  {name:<20} {luts:>6} {ccu:>6} {equivalent:>8} {dffs:>6} "
             f"{bram:>7}  {note}")

    emit("  " + "-" * 76)
    emit(f"  {'sum':<20} {'':>6} {'':>6} {total_eq:>8} {total_dff:>6}")
    emit()
    emit("OUT OF CONTEXT: an UPPER BOUND on what removing a module saves, not")
    emit("the delta. DFF is exact; LUT4 is mapping done without the neighbours")
    emit("it would be merged with. For a delta, build the SoC both ways.")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
