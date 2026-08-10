#!/usr/bin/env python3
#
# Settle #186 in simulation: where a HyperRAM's data phase starts, and whether the
# DQS controller's 4:1 gearing can land on it.
# SPDX-License-Identifier: BSD-3-Clause

"""Two measurements, one runner.

    scripts/hyperram_dqs_model_sim.py                 # both stages, open twin
    scripts/hyperram_dqs_model_sim.py --stage probe   # the device alone
    scripts/hyperram_dqs_model_sim.py --sim both      # ...and the vendor model too
    scripts/hyperram_dqs_model_sim.py --controller-sweep   # the shim's own parameters

**probe** drives a textbook CA -- six bytes on the six clock edges after CS#
falls -- and reports the edge index at which the device first drives DQ, for every
`CR0[7:4]` and both `CR0[3]` settings. No controller and no gearing take part, so
nothing in the measurement can supply the answer it is looking for. The edge index
modulo 4 IS the parity question: the ECP5 4:1 gearing packs four consecutive
device edges into one 32-bit fabric word.

**controller** elaborates `HyperRAMDQSController` through `amaranth.back.verilog`
and drives the same device model with it, through a behavioural PHY built from
`hyperram_dqs_phy.py`'s own primitive mapping. The primitives' SCLK-to-pin
latencies have no open model, so the relative offset is a swept parameter and the
run reports which settings let the device decode the address it was asked for.

Log: `tmp/logs/hyperram_dqs_model_sim.log`. Exit status 0 if every stage ran and
the twin answered.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBES = ROOT / "gateware" / "probes" / "hyperram"
PROBE_TB = PROBES / "dqs_latency_probe_tb.sv"
CTRL_TB = PROBES / "dqs_model_tb.sv"
OPEN_MODEL = PROBES / "hyperram_model.v"
MODEL_ZIP = ROOT / "sources" / "models" / "W956X8MBY_verilog_p.zip"

# `sources/**` is gitignored, so a git worktree does not carry it. Fall back to
# the main checkout rather than telling the user to fetch vendor IP they already
# have. `HYPERRAM_MODEL_ZIP` overrides both.
if not MODEL_ZIP.exists():
    _env = os.environ.get("HYPERRAM_MODEL_ZIP")
    if _env:
        MODEL_ZIP = Path(_env)
    else:
        _common = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                                  "--git-common-dir"], cwd=ROOT,
                                 capture_output=True, text=True)
        if _common.returncode == 0:
            _main = Path(_common.stdout.strip()).parent
            if (_main / "sources" / "models" / MODEL_ZIP.name).exists():
                MODEL_ZIP = _main / "sources" / "models" / MODEL_ZIP.name
WORKDIR = ROOT / "tmp" / "hyperram-dqs-model"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_dqs_model_sim.log"

DIAMOND = Path(os.environ.get("DIAMOND_ROOT", Path.home() / "lscc" / "diamond" / "3.14"))

# One `sync` cycle is 20 ns in `dqs_model_tb.sv` (2 CK at 100 MHz), so the
# controller must be built for 50 MHz or its tCSHI count is derived from a clock
# the simulation does not run.
SYNC_MHZ = 50.0

# The ceiling on `latency_clocks`, which sets that input's width. 14 covers every
# code the device can be put into, including the reserved ones.
MAX_LATENCY_CLOCKS = 14

# Measured on this machine: iverilog 0.4 s, vvp 1.5 s for the controller sweep,
# vlog/vsim about 1 s each. 30 s is ~20x the slowest step, spent almost entirely
# on an unknown cold FlexLM checkout. On expiry the step, its limit and its elapsed
# time are logged and the run exits non-zero -- a hung vsim otherwise reads as slow.
STEP_TIMEOUT_S = 30

log = logging.getLogger("dqs-model")


def setup_logging(verbose: bool) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def run(step: str, argv: list[str], cwd: Path) -> str:
    log.debug("%s: %s", step, " ".join(argv))
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=STEP_TIMEOUT_S, env=dict(os.environ))
    except subprocess.TimeoutExpired as exc:
        log.error("%s exceeded its %d s limit (ran %.1f s) -- treating as hung",
                  step, STEP_TIMEOUT_S, exc.timeout)
        raise SystemExit(2) from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        log.debug("  %s", line)
    if proc.returncode != 0:
        log.error("%s failed (exit %d):", step, proc.returncode)
        for line in out.splitlines()[-20:]:
            log.error("  %s", line)
        raise SystemExit(proc.returncode)
    return out


def elaborate_controller() -> Path:
    """`HyperRAMDQSController` as Verilog, with the PHY record brought out flat."""
    sys.path.insert(0, str(ROOT / "gateware"))
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))

    from amaranth import Elaboratable, Module, Signal
    from amaranth.back import verilog
    from luna.gateware.interface.psram import HyperBusDQSPHY

    from peripherals.hyperram_dqs_controller import HyperRAMDQSController

    class Flat(Elaboratable):
        """The controller with every record member as a port a testbench can wire."""

        def __init__(self):
            self.phy = HyperBusDQSPHY()
            self.ctl = HyperRAMDQSController(
                phy=self.phy, sync_mhz=SYNC_MHZ,
                max_latency_clocks=MAX_LATENCY_CLOCKS)
            self.phy_cs = Signal()
            self.phy_clk_en = Signal(2)
            self.phy_dq_o = Signal(32)
            self.phy_dq_e = Signal()
            self.phy_rwds_o = Signal(4)
            self.phy_rwds_e = Signal()
            self.phy_read = Signal(2)
            self.phy_dq_i = Signal(32)
            self.phy_rwds_i = Signal(4)
            self.phy_datavalid = Signal()
            self.phy_burstdet = Signal()

        def elaborate(self, platform):
            m = Module()
            m.submodules.ctl = self.ctl
            m.d.comb += [
                self.phy_cs.eq(self.phy.cs),
                self.phy_clk_en.eq(self.phy.clk_en),
                self.phy_dq_o.eq(self.phy.dq.o),
                self.phy_dq_e.eq(self.phy.dq.e),
                self.phy_rwds_o.eq(self.phy.rwds.o),
                self.phy_rwds_e.eq(self.phy.rwds.e),
                self.phy_read.eq(self.phy.read),
                self.phy.dq.i.eq(self.phy_dq_i),
                self.phy.rwds.i.eq(self.phy_rwds_i),
                self.phy.datavalid.eq(self.phy_datavalid),
                self.phy.burstdet.eq(self.phy_burstdet),
            ]
            return m

    dut = Flat()
    ctl = dut.ctl
    ports = [
        ctl.address, ctl.register_space, ctl.perform_write, ctl.single_page,
        ctl.start_transfer, ctl.final_word, ctl.latency_clocks, ctl.fixed_latency,
        ctl.write_data, ctl.idle, ctl.read_ready, ctl.write_ready, ctl.timed_out,
        ctl.state, ctl.read_data,
        dut.phy_cs, dut.phy_clk_en, dut.phy_dq_o, dut.phy_dq_e, dut.phy_rwds_o,
        dut.phy_rwds_e, dut.phy_read, dut.phy_dq_i, dut.phy_rwds_i,
        dut.phy_datavalid, dut.phy_burstdet,
    ]
    src = verilog.convert(dut, name="dqs_controller", ports=ports)
    out = WORKDIR / "dqs_controller.v"
    out.write_text(src)
    log.info("elaborated %s (%d lines)", out.name, src.count("\n"))
    return out


def need(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not on PATH")


def stage_probe_icarus() -> list[str]:
    need("iverilog", "vvp")
    run("iverilog(probe)", ["iverilog", "-g2012", "-DDUT_MODULE=hyperram_model",
                            "-o", "probe.vvp", str(PROBE_TB), str(OPEN_MODEL)], WORKDIR)
    return run("vvp(probe)", ["vvp", "probe.vvp"], WORKDIR).splitlines()


def questa_bin(name: str) -> Path:
    path = DIAMOND / "questasim" / "bin" / name
    if not path.exists():
        raise SystemExit(f"{name} not under {DIAMOND}/questasim/bin -- set DIAMOND_ROOT")
    return path


def stage_probe_questa(part: str, grade: str) -> list[str]:
    """The same stimulus against Winbond's own model, which only Questa can read."""
    if not MODEL_ZIP.exists():
        raise SystemExit(f"{MODEL_ZIP} is missing -- gitignored vendor IP, see sources/README.md")
    vendor_dir = WORKDIR / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    inner = f"{part}_verilog_p.zip"
    with zipfile.ZipFile(MODEL_ZIP) as outer:
        outer.extract(inner, vendor_dir)
    with zipfile.ZipFile(vendor_dir / inner) as z:
        z.extractall(vendor_dir)
    model = vendor_dir / f"{part}.modelsim.vp"
    shutil.copy(PROBE_TB, vendor_dir / PROBE_TB.name)
    env_lic = DIAMOND / "license" / "license.dat"
    os.environ.setdefault("LM_LICENSE_FILE", str(env_lic))
    if (vendor_dir / "work").exists():
        shutil.rmtree(vendor_dir / "work")
    run("vlib", [str(questa_bin("vlib")), "work"], vendor_dir)
    run("vlog", [str(questa_bin("vlog")), "-sv", f"+define+{grade}",
                 f"+define+DUT_MODULE={part}", model.name, PROBE_TB.name], vendor_dir)
    return run("vsim", [str(questa_bin("vsim")), "-c", "-voptargs=+acc", "tb",
                        "-do", "run -all; quit -f"], vendor_dir).splitlines()


def build_controller() -> None:
    need("iverilog", "vvp")
    ctl = elaborate_controller()
    run("iverilog(ctl)", ["iverilog", "-g2012", "-DDUT_MODULE=hyperram_model",
                          "-o", "ctl.vvp", str(CTRL_TB), str(OPEN_MODEL), ctl.name], WORKDIR)


def stage_controller(extra: list[str]) -> list[str]:
    return run("vvp(ctl)", ["vvp", "ctl.vvp"] + extra, WORKDIR).splitlines()


def report(lines: list[str], tag: str) -> list[dict]:
    """The measurement lines, wherever the simulator put its own prefix."""
    rows = []
    for line in lines:
        text = line.lstrip("# ").rstrip()
        if text.startswith(("[probe]", "[dqs]", "[edge]")):
            log.info("%-6s %s", tag, text)
        if text.startswith("[probe] cr0=") or text.startswith("[dqs] code="):
            rows.append(dict(kv.split("=", 1) for kv in text.split()[1:] if "=" in kv))
    return rows


# CR0[7:4] is sparse and sign-extended: 0..2 give 5..7 CK and 14..15 give 3..4.
# Stated here from the datasheet, not from either model, so a model that decodes a
# code wrongly is caught rather than agreed with.
BASE_CK = {0: 5, 1: 6, 2: 7, 14: 3, 15: 4}


def verdict_probe(twin: list[dict], vendor: list[dict]) -> list[str]:
    """What the probe settles, checked rather than asserted in prose."""
    bad = []
    log.info("--- what the probe measured ---")
    for row in twin:
        code, fixed = int(row["code"]), int(row["fixed"])
        if code not in BASE_CK:
            continue
        want = 4 + 2 * (2 * BASE_CK[code] if fixed else BASE_CK[code])
        got = int(row["read_first"])
        note = "" if got == want else f"  <-- DIVERGES from 4 + 2 x L_ck = {want}"
        log.info("  twin   code %2d %-8s first data edge %4d  phase %d%s",
                 code, "fixed" if fixed else "variable", got, got % 4, note)
        if got != want:
            bad.append(f"open twin decodes CR0[7:4]={code} as data at edge {got}, "
                       f"not {want}: the CK count it derives is wrong")
    for row in twin:
        if int(row["fixed"]) and int(row["code"]) in BASE_CK and int(row["phase"]) != 0:
            bad.append(f"fixed latency code {row['code']} did NOT land on a "
                       f"32-bit boundary (phase {row['phase']})")
    if vendor:
        by_key = {(r["code"], r["fixed"]): r for r in vendor}
        for row in twin:
            other = by_key.get((row["code"], row["fixed"]))
            if other is None:
                continue
            if row["read_first"] != other["read_first"]:
                log.info("  DIVERGE code %2s fixed=%s: twin edge %s, vendor edge %s",
                         row["code"], row["fixed"], row["read_first"],
                         other["read_first"])
                if int(row["code"]) in BASE_CK:
                    bad.append(f"twin and vendor disagree on code {row['code']} "
                               f"fixed={row['fixed']}: {row['read_first']} vs "
                               f"{other['read_first']}")
    return bad


def verdict_controller(rows: list[dict]) -> None:
    """Where the controller's own 32-bit word lands, per device latency."""
    if not rows:
        return
    log.info("--- where the controller's word landed ---")
    seen = set()
    for row in rows:
        key = (row["code"], row["fixed"])
        if key in seen or row["served"] != "100":
            continue
        seen.add(key)
        best = [r for r in rows
                if (r["code"], r["fixed"]) == key and r["slip"] == "0"]
        log.info("  code %2s %-8s device %2s CK, data edge %3s phase %s: "
                 "slip 0 at latency_clocks %s",
                 row["code"], "fixed" if row["fixed"] == "1" else "variable",
                 row["dev_ck"], row["data_edge"], row["data_ph"],
                 ",".join(r["n"] for r in best) or "NO SETTING")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="both", choices=("probe", "controller", "both"))
    ap.add_argument("--sim", default="icarus", choices=("icarus", "questa", "both"),
                    help="which device model the probe stage runs against")
    ap.add_argument("--grade", default="T166")
    ap.add_argument("--part", default="W956A8MBYA")
    ap.add_argument("--controller-sweep", action="store_true",
                    help="run the controller stage at every shim pipeline offset")
    ap.add_argument("--dq-pipe", type=int, default=0)
    ap.add_argument("--ck-pipe", type=int, default=0)
    ap.add_argument("--dq-ph", type=int, default=0)
    ap.add_argument("--rd-slip", type=int, default=0)
    ap.add_argument("--verbose-edges", action="store_true",
                    help="controller stage: one line per device clock edge")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    if WORKDIR.exists() and not args.keep:
        shutil.rmtree(WORKDIR)
    WORKDIR.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    twin_rows: list[dict] = []
    vendor_rows: list[dict] = []

    if args.stage in ("probe", "both"):
        if args.sim in ("icarus", "both"):
            log.info("=== probe, open twin ===")
            lines = stage_probe_icarus()
            twin_rows = report(lines, "twin")
            if not twin_rows:
                failures.append("probe/twin produced no measurement")
            m = [l for l in lines if "=== done" in l]
            if m and not re.search(r"done, 0 failures", m[0]):
                failures.append(f"probe/twin: {m[0].strip()}")
        if args.sim in ("questa", "both"):
            log.info("=== probe, Winbond's own model ===")
            lines = stage_probe_questa(args.part, args.grade)
            vendor_rows = report(lines, "vendor")
            if not vendor_rows:
                failures.append("probe/vendor produced no measurement")
        failures += verdict_probe(twin_rows, vendor_rows)

    if args.stage in ("controller", "both"):
        log.info("=== controller through a behavioural 4:1 PHY ===")
        build_controller()
        combos = [(args.dq_pipe, args.ck_pipe, args.dq_ph, args.rd_slip)]
        if args.controller_sweep:
            # The primitives' SCLK-to-pin latencies have no open model, so the
            # relative offset is searched rather than assumed. Whole cycles both
            # ways, and every quarter-cycle phase.
            combos = [(dq, ck, ph, 0)
                      for dq in range(2) for ck in range(2) for ph in range(4)]
        for dq, ck, ph, slip in combos:
            extra = [f"+dq_pipe={dq}", f"+ck_pipe={ck}", f"+dq_ph={ph}",
                     f"+rd_slip={slip}"]
            if args.verbose_edges:
                extra.append("+verbose=1")
            lines = stage_controller(extra)
            rows = report(lines, "ctl")
            if not rows:
                failures.append(f"controller stage produced no measurement at {extra}")
            verdict_controller(rows)

    for f in failures:
        log.error("FAIL %s", f)
    if not args.keep:
        shutil.rmtree(WORKDIR, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
