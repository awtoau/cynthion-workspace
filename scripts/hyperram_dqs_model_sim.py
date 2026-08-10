#!/usr/bin/env python3
#
# The DQS controller and PHY, in ECP5 primitives, driving Winbond's own model.
# SPDX-License-Identifier: BSD-3-Clause

"""Full-stack HyperRAM simulation: our gateware against the real part's model.

`hyperram_vendor_model_sim.py` drives the part from a hand-written testbench.
This drives it from **our own controller and PHY**, elaborated to Verilog and
simulated with Diamond's ECP5 primitive models -- `DQSBUFM`, `IDDRX2DQA`,
`ODDRX2DQA`, `DDRDLLA` and the rest -- so the gearing is the real gearing and
not an idealisation of it.

Why it exists: #186 reports that the DQS path reads one word late, and says the
two candidate explanations cannot be separated by any write-here-read-there
experiment through a window that serves both directions. They can be separated
here, because the vendor model narrates the bus:

    pos clk, addr_cmd_count : 1, adq_in: (0xa0)
    CA[47:0]=48'ha00000000000 ... the address in is : 'h000100
    read  mem : neg clock:  addr: 'h000101, memory data: 'h1234

Three facts, one transaction: what address our CA asked for, what address the
device served, and what our controller returned. Reads-late and writes-early
predict different rows.

Needs Diamond (for Questa and for the ECP5 simulation library) and the vendor
model zip. Log: `tmp/logs/hyperram_dqs_model_sim.log`.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKDIR = ROOT / "tmp" / "hyperram-dqs-model"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_dqs_model_sim.log"
TESTBENCH = ROOT / "gateware" / "probes" / "hyperram" / "dqs_model_tb.sv"

DIAMOND = Path(os.environ.get("DIAMOND_ROOT", Path.home() / "lscc" / "diamond" / "3.14"))
ECP5_SIM_LIB = DIAMOND / "cae_library" / "simulation" / "verilog" / "ecp5u"


def _sources_root() -> Path:
    """The vendor model is gitignored, so it is never inside a worktree."""
    if ROOT.parent.name == "worktrees" and ROOT.parent.parent.name == ".claude":
        main = ROOT.parent.parent.parent
        if (main / "sources" / "models").is_dir():
            return main
    return ROOT


MODEL_ZIP = _sources_root() / "sources" / "models" / "W956X8MBY_verilog_p.zip"

# Elaboration is ~3 s, vlog over the ECP5 library ~20 s the first time, and the
# run is 200 us of model time with DDRDLL settling in it. 180 s is ~3x the
# slowest observed end-to-end; on expiry we log the step and its elapsed time.
STEP_TIMEOUT_S = 180

log = logging.getLogger("dqs-model")


def setup_logging(verbose: bool) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def emit_verilog(out_path: Path, sync_mhz: float, read_phase: int) -> None:
    """Elaborate controller + PHY with fabricated pads, so no platform is needed."""
    sys.path.insert(0, str(ROOT / "gateware"))
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))

    from amaranth import ClockDomain, Elaboratable, Module, Signal
    from amaranth.back import verilog
    from amaranth.hdl import IOPort
    from amaranth.lib.io import DifferentialPort, SingleEndedPort

    from peripherals.hyperram_dqs_controller import HyperRAMDQSController
    from peripherals.hyperram_dqs_phy import HyperRAMDQSPHY

    class Pads:
        """What `platform.request("ram", 0, dir="-")` would have handed the PHY."""

        def __init__(self):
            self.clk_p = IOPort(1, name="ram_clk_p")
            self.clk_n = IOPort(1, name="ram_clk_n")
            self.cs_io = IOPort(1, name="ram_cs")
            self.rwds_io = IOPort(1, name="ram_rwds")
            self.dq_io = IOPort(8, name="ram_dq")
            self.reset_io = IOPort(1, name="ram_reset")
            self.clk = DifferentialPort(self.clk_p, self.clk_n)
            self.cs = SingleEndedPort(self.cs_io)
            self.rwds = SingleEndedPort(self.rwds_io)
            self.dq = SingleEndedPort(self.dq_io)
            self.reset = SingleEndedPort(self.reset_io)

    class Top(Elaboratable):
        def __init__(self):
            self.pads = Pads()
            self.cd_sync = ClockDomain("sync")
            self.cd_fast = ClockDomain("fast")
            for name, width in (("address", 32), ("write_data", 32), ("read_data", 32),
                                ("register_space", 1), ("perform_write", 1),
                                ("single_page", 1), ("start_transfer", 1),
                                ("final_word", 1), ("idle", 1), ("read_ready", 1),
                                ("write_ready", 1), ("state", 4), ("dll_ready", 1)):
                setattr(self, name, Signal(width, name=name))

        def elaborate(self, platform):
            m = Module()
            m.domains += self.cd_sync, self.cd_fast
            m.submodules.phy = phy = HyperRAMDQSPHY(bus=self.pads, read_phase=read_phase)
            m.submodules.ctl = ctl = HyperRAMDQSController(phy=phy.phy, sync_mhz=sync_mhz)
            m.d.comb += [
                ctl.address.eq(self.address),
                ctl.register_space.eq(self.register_space),
                ctl.perform_write.eq(self.perform_write),
                ctl.single_page.eq(self.single_page),
                ctl.start_transfer.eq(self.start_transfer),
                ctl.final_word.eq(self.final_word),
                ctl.write_data.eq(self.write_data),
                self.read_data.eq(ctl.read_data),
                self.idle.eq(ctl.idle),
                self.read_ready.eq(ctl.read_ready),
                self.write_ready.eq(ctl.write_ready),
                self.state.eq(ctl.state),
                self.dll_ready.eq(phy.dll_ready),
            ]
            return m

    top = Top()
    ports = [top.cd_sync.clk, top.cd_sync.rst, top.cd_fast.clk, top.cd_fast.rst,
             top.address, top.register_space, top.perform_write, top.single_page,
             top.start_transfer, top.final_word, top.read_data, top.write_data,
             top.idle, top.read_ready, top.write_ready, top.state, top.dll_ready,
             top.pads.clk_p, top.pads.clk_n, top.pads.cs_io, top.pads.rwds_io,
             top.pads.dq_io, top.pads.reset_io]
    out_path.write_text(verilog.convert(top, name="dqs_top", ports=ports))
    log.info("elaborated %s (%d lines), read_phase=%d, sync %.1f MHz",
             out_path.name, len(out_path.read_text().splitlines()), read_phase, sync_mhz)


def extract_model() -> Path:
    if not MODEL_ZIP.exists():
        raise SystemExit(f"{MODEL_ZIP} is missing -- gitignored vendor IP, see sources/README.md")
    with zipfile.ZipFile(MODEL_ZIP) as outer:
        outer.extract("W956A8MBYA_verilog_p.zip", WORKDIR)
    with zipfile.ZipFile(WORKDIR / "W956A8MBYA_verilog_p.zip") as inner:
        inner.extractall(WORKDIR)
    return WORKDIR / "W956A8MBYA.modelsim.vp"


def run(step: str, argv: list[str]) -> str:
    log.debug("%s: %s", step, " ".join(argv))
    env = dict(os.environ)
    env.setdefault("LM_LICENSE_FILE", str(DIAMOND / "license" / "license.dat"))
    try:
        proc = subprocess.run(argv, cwd=WORKDIR, env=env, capture_output=True,
                              text=True, timeout=STEP_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        log.error("%s exceeded its %d s limit (ran %.1f s)", step, STEP_TIMEOUT_S, exc.timeout)
        raise SystemExit(2) from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        log.debug("  %s", line)
    if proc.returncode != 0:
        log.error("%s failed (exit %d):", step, proc.returncode)
        for line in out.splitlines()[-25:]:
            log.error("  %s", line)
        raise SystemExit(proc.returncode)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sync-mhz", type=float, default=50.0,
                    help="sync clock; fast is 2x it and CK follows fast (default 50)")
    ap.add_argument("--read-phase", type=int, default=0,
                    help="HyperRAMDQSPHY read_phase; 1 shifts the DQSBUFM window "
                         "half a sync cycle, which is one 16-bit word on the wire")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    if not ECP5_SIM_LIB.is_dir():
        raise SystemExit(f"{ECP5_SIM_LIB} not found -- needed for DQSBUFM and friends")

    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    WORKDIR.mkdir(parents=True)

    emit_verilog(WORKDIR / "dqs_top.v", args.sync_mhz, args.read_phase)
    model = extract_model()
    shutil.copy(TESTBENCH, WORKDIR / TESTBENCH.name)

    qbin = DIAMOND / "questasim" / "bin"
    run("vlib", [str(qbin / "vlib"), "work"])
    run("vlog", [str(qbin / "vlog"), "-sv", "+define+T166",
                 "-y", str(ECP5_SIM_LIB), "+libext+.v",
                 model.name, "dqs_top.v", TESTBENCH.name])
    out = run("vsim", [str(qbin / "vsim"), "-c", "-voptargs=+acc", "dqs_tb",
                       "-do", "run -all; quit -f"])

    for line in out.splitlines():
        if "[dqs]" in line or "the address in is" in line or "read  mem" in line:
            log.info("%s", line.replace("# ", "", 1).rstrip())

    if "GLOBAL TIMEOUT" in out:
        log.error("the design hung -- see the log")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
