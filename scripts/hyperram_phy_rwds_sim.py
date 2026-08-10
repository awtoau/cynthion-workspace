#!/usr/bin/env python3
#
# The controller WITH `HyperRAMPHY` in the loop, against the open HyperRAM model.
# SPDX-License-Identifier: BSD-3-Clause

"""Where the extra-latency sample lands once the PHY's pipeline is in the loop.

`hyperram_model_sim.py` drives `HyperRAMController` through an IDEAL 2:1 gearbox --
its own header says so: zero pipeline latency, zero skew between CS#, OE, CK and
DQ, and `HyperRAMPHY` deliberately not instantiated. That is the one assumption
[#338](https://github.com/awtoau/cynthion-workspace/issues/338) turns on, because
upstream's PHY is not ideal:

  * `cs`, `dq.e`, `rwds.e` go through `FFSynchronizer(stages=3)` -- 3 cycles.
  * `clk_en`, `dq.o`, `rwds.o` go through `ODDRX1F` -- **also 3 cycles**, measured
    here against Lattice's own simulation model rather than assumed.
  * `rwds.i`, `dq.i` come back through `IDDRX1F` -- **1 cycle**, same measurement.

So the OUTPUT path is 3 cycles and the INPUT path is 1. What the controller emits
in cycle N reaches the pin in N+3; what the device answers on pin cycle M reaches
the controller in M+1. The round trip is 4 cycles, and nothing in the controller
accounts for it.

This runs the real PHY -- ECP5 primitives included, from
`$DIAMOND_ROOT/cae_library/simulation/verilog/ecp5u` -- so the question can be
measured instead of argued.

Three things it reports:

  1. **The sample window.** The pin cycle CS# falls, the pin cycles the controller's
     RWDS samples come from, and the pin cycle the device first drives RWDS.
  2. **Elections.** How many variable-latency transactions took the LONG branch, per
     cell, which is the counter #338 asks for -- in simulation, where the answer the
     device gave is also known.
  3. **The float experiment.** `+float=0` and `+float=1` weakly drive RWDS *while
     CS# is High* and nowhere else, which is the idle line and nothing more. If the
     controller samples the device's CA answer, this changes nothing. If it samples
     the idle line, it changes every election.
  4. **Early strobes.** Read strobes taken before the device drove DQ. Graded to
     zero: the CA-period RWDS request is a level, and entering `READ_DATA` before it
     has fallen latches the fall as a strobe over a tristate bus. (#353)

`+float=0` is the control: it must be indistinguishable from the part.

Usage:

    scripts/hyperram_phy_rwds_sim.py              # the whole thing
    scripts/hyperram_phy_rwds_sim.py --keep       # leave tmp/hyperram-phy-rwds
    scripts/hyperram_phy_rwds_sim.py --dump       # write a VCD next to it
    scripts/hyperram_phy_rwds_sim.py --sync-mhz 100

Log: `tmp/logs/hyperram_phy_rwds_sim.log`.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from amaranth import Elaboratable, Module, Signal          # noqa: E402
from amaranth.back import verilog                          # noqa: E402
from luna.gateware.interface.psram import HyperBusPHY, HyperRAMPHY   # noqa: E402

from peripherals.hyperram_controller import (                   # noqa: E402
    HyperRAMController, PHY_ROUND_TRIP_CYCLES)

# Smallest `latency_clocks` a READ may take. The device releases the CA-period RWDS
# request in pin cycle 6+P and the IDDR shows the fall at R+6, so the first clean
# cycle is R+7; `READ_DATA` is entered at `xact_age` 4 + `latency_clocks`. (#353)
MIN_READ_LATENCY_CLOCKS = PHY_ROUND_TRIP_CYCLES + 3

MODEL = ROOT / "gateware" / "probes" / "hyperram" / "hyperram_model.v"
TESTBENCH = ROOT / "gateware" / "probes" / "hyperram" / "phy_rwds_tb.sv"
WORKDIR = ROOT / "tmp" / "hyperram-phy-rwds"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_phy_rwds_sim.log"

TOP = "hyperram_phy_top"

# The ECP5 primitives upstream's PHY instantiates. Diamond ships the only
# simulation models for them; without these the PHY is a black box and the whole
# point of this harness is gone, so a missing library is a hard stop.
DIAMOND = Path(os.environ.get("DIAMOND_ROOT", Path.home() / "lscc" / "diamond" / "3.14"))
ECP5U = DIAMOND / "cae_library" / "simulation" / "verilog" / "ecp5u"
PRIMITIVES = ("ODDRX1F.v", "IDDRX1F.v", "DELAYF.v", "GSR.v", "PUR.v")

# Ceiling for `latency_clocks`; code 6 asks for 2 x 11 CK, which the shipped
# default of 14 cannot express. Same value `hyperram_model_sim.py` uses.
MAX_LATENCY_CLOCKS = 32

# The clock the board matrix in #338 ran at, so the tDSV window this measures is
# the one those failures were measured under.
DEFAULT_SYNC_MHZ = 80.0

# Longest step is vvp. Measured at 1.5 s for the full sweep on this machine
# (iverilog 0.4 s); 4 s is ~2.6x, covering a cold page cache and a loaded box. On
# expiry the step, its limit and its elapsed time are logged and the run exits
# non-zero -- a hung vvp is otherwise indistinguishable from a slow one.
STEP_TIMEOUT_S = 6

# Lines a good run must contain, so a section that silently stopped running fails
# the run rather than passing it by absence.
REQUIRED_MARKERS = (
    "[phy] ODDR output latency",
    "[phy] IDDR input latency",
    "[win] sample window",
    "[tb] elections",
    "[tb] data",
)

# Both directions of the device's answer, so a controller that gets one right by
# accident is caught. 0: the part never asks for the extra latency under variable
# latency, so the SHORT count is the right one every time. 1: a refresh collision
# on every transaction, so the LONG count is.
REFRESH_PASSES = (
    (0, "the device never asks for the extra latency -- SHORT is right every time"),
    (1, "the device asks on every transaction -- LONG is right every time"),
)

log = logging.getLogger("hyperram-phy-rwds-sim")


def setup_logging(verbose: bool) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"), logging.StreamHandler(sys.stdout)],
    )


class FakePin:
    """The handful of attributes `HyperRAMPHY` touches on a `platform.request` pin.

    Stands in for the platform's `ram` resource so the PHY can be elaborated without
    a board file. Only `.o`, `.i` and `.oe` are ever touched.
    """

    def __init__(self, name: str, width: int = 1, *, i: bool = False, oe: bool = False):
        self.o = Signal(width, name=f"{name}_o")
        if i:
            self.i = Signal(width, name=f"{name}_i")
        if oe:
            self.oe = Signal(name=f"{name}_oe")


class PhyTop(Elaboratable):
    """`HyperRAMController` + upstream `HyperRAMPHY`, cut at the PIN, not the record.

    `hyperram_model_sim.py` cuts at the `HyperBusPHY` record and gears it in the
    testbench. That is the right cut for the controller's arithmetic and the wrong
    one for #338: the pipeline between the record and the pin is what the question
    is about, so here the DUT includes it.
    """

    class Bus:
        """`platform.request("ram")` reduced to what the PHY reads off it."""

    def __init__(self, *, sync_mhz: float, max_latency_clocks: int):
        self.cs   = FakePin("cs")
        self.clk  = FakePin("clk")
        self.rwds = FakePin("rwds", i=True, oe=True)
        self.dq   = FakePin("dq", 8, i=True, oe=True)

        bus = self.Bus()
        bus.cs, bus.clk, bus.rwds, bus.dq = self.cs, self.clk, self.rwds, self.dq
        self.bus = bus

        self.phy = HyperRAMPHY(bus=bus)
        self.ctl = HyperRAMController(phy=self.phy.phy, sync_mhz=sync_mhz,
                                      max_latency_clocks=max_latency_clocks)

        # Control in.
        self.start_transfer     = Signal()
        self.register_space     = Signal()
        self.perform_write      = Signal()
        self.single_page        = Signal()
        self.final_word         = Signal()
        self.address            = Signal(32)
        self.write_data         = Signal(16)
        self.latency_clocks     = Signal.like(self.ctl.latency_clocks)
        self.low_latency_clocks = Signal.like(self.ctl.low_latency_clocks)
        self.fixed_latency      = Signal()

        # Status out.
        self.idle        = Signal()
        self.read_ready  = Signal()
        self.write_ready = Signal()
        self.timed_out   = Signal()
        self.read_data   = Signal(16)
        self.state       = Signal(4)
        # The controller's own view of RWDS, which is the pin one IDDR cycle ago.
        # Brought out so the sample instant is measured rather than inferred.
        self.rwds_i_seen = Signal(2)

    def elaborate(self, platform):
        m = Module()
        m.submodules.phy = self.phy
        m.submodules.ctl = self.ctl
        m.d.comb += [
            self.ctl.start_transfer     .eq(self.start_transfer),
            self.ctl.register_space     .eq(self.register_space),
            self.ctl.perform_write      .eq(self.perform_write),
            self.ctl.single_page        .eq(self.single_page),
            self.ctl.final_word         .eq(self.final_word),
            self.ctl.address            .eq(self.address),
            self.ctl.write_data         .eq(self.write_data),
            self.ctl.latency_clocks     .eq(self.latency_clocks),
            self.ctl.low_latency_clocks .eq(self.low_latency_clocks),
            self.ctl.fixed_latency      .eq(self.fixed_latency),

            self.idle        .eq(self.ctl.idle),
            self.read_ready  .eq(self.ctl.read_ready),
            self.write_ready .eq(self.ctl.write_ready),
            self.timed_out   .eq(self.ctl.timed_out),
            self.read_data   .eq(self.ctl.read_data),
            self.state       .eq(self.ctl.state),
            self.rwds_i_seen .eq(self.phy.phy.rwds.i),
        ]
        return m

    def ports(self):
        return [
            self.start_transfer, self.register_space, self.perform_write,
            self.single_page, self.final_word, self.address, self.write_data,
            self.latency_clocks, self.low_latency_clocks, self.fixed_latency,
            self.idle, self.read_ready, self.write_ready, self.timed_out,
            self.read_data, self.state, self.rwds_i_seen,
            self.cs.o, self.clk.o,
            self.dq.o, self.dq.i, self.dq.oe,
            self.rwds.o, self.rwds.i, self.rwds.oe,
        ]


def emit_top(outdir: Path, sync_mhz: float) -> tuple[Path, int]:
    top = PhyTop(sync_mhz=sync_mhz, max_latency_clocks=MAX_LATENCY_CLOCKS)
    width = len(top.latency_clocks)
    text = verilog.convert(top, name=TOP, ports=top.ports())
    path = outdir / f"{TOP}.v"
    path.write_text(text)
    log.info("elaborated %s (%d lines, latency_clocks is %d bits, sync %.1f MHz)",
             path.name, text.count("\n") + 1, width, sync_mhz)
    return path, width


def state_index(name: str) -> int:
    """The FSM encoding the testbench decodes, taken from the controller itself."""
    return HyperRAMController.STATES.index(name)


def run(step: str, argv: list[str]) -> str:
    log.debug("%s: %s", step, " ".join(argv))
    try:
        proc = subprocess.run(argv, cwd=WORKDIR, capture_output=True, text=True,
                              timeout=STEP_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        log.error("%s exceeded its %d s limit (ran %.1f s) -- treating as hung",
                  step, STEP_TIMEOUT_S, exc.timeout)
        raise SystemExit(2) from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        log.error("%s failed (exit %d):", step, proc.returncode)
        for line in out.splitlines()[-30:]:
            log.error("  %s", line)
        raise SystemExit(proc.returncode)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sync-mhz", type=float, default=DEFAULT_SYNC_MHZ)
    ap.add_argument("--keep", action="store_true", help="leave the work directory")
    ap.add_argument("--dump", action="store_true", help="write a VCD")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)

    missing = [p for p in PRIMITIVES if not (ECP5U / p).is_file()]
    if missing:
        log.error("no ECP5 simulation primitives at %s (missing %s)", ECP5U,
                  ", ".join(missing))
        log.error("set DIAMOND_ROOT; without these the PHY is a black box and this "
                  "harness measures nothing it claims to")
        return 2

    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    WORKDIR.mkdir(parents=True)

    _, lat_w = emit_top(WORKDIR, args.sync_mhz)
    shutil.copy(MODEL, WORKDIR / MODEL.name)
    shutil.copy(TESTBENCH, WORKDIR / TESTBENCH.name)
    for prim in PRIMITIVES:
        shutil.copy(ECP5U / prim, WORKDIR / prim)

    defines = [
        f"-DLAT_W={lat_w}",
        f"-DTCK_NS={1000.0 / args.sync_mhz:.4f}",
        f"-DST_SHIFT_COMMAND1={state_index('SHIFT_COMMAND1')}",
        f"-DST_SHIFT_COMMAND2={state_index('SHIFT_COMMAND2')}",
        f"-DST_HANDLE_LATENCY={state_index('HANDLE_LATENCY')}",
        f"-DST_READ_DATA={state_index('READ_DATA')}",
        f"-DST_IDLE={state_index('IDLE')}",
        f"-DMIN_READ_LATENCY={MIN_READ_LATENCY_CLOCKS}",
    ]

    failed = 0
    for refresh, what in REFRESH_PASSES:
        log.info("--- REFRESH_EVERY=%d: %s", refresh, what)
        run(f"iverilog(refresh={refresh})",
            ["iverilog", "-g2012", "-o", f"phy_rwds_{refresh}.vvp",
             *defines, f"-DREFRESH_EVERY={refresh}",
             TESTBENCH.name, f"{TOP}.v", MODEL.name, *PRIMITIVES])
        argv = ["vvp", f"phy_rwds_{refresh}.vvp"]
        if args.dump:
            argv.append("+dump")
        out = run(f"vvp(refresh={refresh})", argv)
        for line in out.splitlines():
            log.info("  %s", line)

        absent = [m for m in REQUIRED_MARKERS if m not in out]
        for m in absent:
            log.error("missing marker: %s", m)
        verdict = re.search(r"\[tb\] (\d+) checks?, (\d+) error", out)
        if not verdict:
            log.error("the testbench printed no verdict line")
            failed += 1
            continue
        checks, errors = int(verdict.group(1)), int(verdict.group(2))
        log.info("REFRESH_EVERY=%d: %d checks, %d errors", refresh, checks, errors)
        if errors or absent:
            failed += 1

    if not args.keep:
        shutil.rmtree(WORKDIR)
    else:
        log.info("work directory kept at %s", WORKDIR)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
