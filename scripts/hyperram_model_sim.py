#!/usr/bin/env python3
#
# Run OUR HyperRAM controller against the open HyperRAM model, in Icarus.
# SPDX-License-Identifier: BSD-3-Clause

"""`HyperRAMController` against `hyperram_model.v`, which is not our model.

#342 calls this the obvious use and nobody had done it. `soc_hyperram_sim.py`
already drives the controller against `ModelHyperRAM16`, but that model was
written from the same datasheet reading as the controller, so the two can be
wrong together -- its own comments say the latency count is made to "agree by
construction". `hyperram_model.v` is independent: it is held equal to Winbond's
encrypted model over `vendor_model_tb.sv`, and that independence is the whole
value here.

What runs:

  1. `HyperRAMController` is elaborated to Verilog through Amaranth's yosys
     backend, with its `HyperBusPHY` record brought out as top-level ports.
  2. `controller_model_tb.sv` gears those 16-bit ports down to the 8-bit DDR bus
     and instantiates `hyperram_model.v` on the other side.
  3. The testbench grades itself; this script decides whether the run counts.

Four cases: register read/write against the model's own CR0/CR1, memory write and
read back with an address-derived pattern, then CR0[7:4] swept at 0/1/2/6/14/15
in fixed latency and in variable latency. The whole thing runs three times over
`REFRESH_EVERY`, so the variable path's SHORT and LONG branches are each reached
on purpose rather than by where a transaction counter happened to land.

`HyperRAMPHY` is deliberately NOT in the loop -- see the testbench header. What
is tested is the CA encoding, the byte order and the latency arithmetic; what is
not is the ECP5 IOLOGIC skew or anything electrical.

Reads start one edge later than writes at the same latency, because the device
has to turn the bus around. `READ_TURNAROUND_EDGES` in the testbench states that
as an assumption so a model that moves it fails here instead of being followed.
The consequence is worth knowing: with the device's word straddling two sync
cycles, every read is taken by `READ_DATA`'s clock-inversion branch, and the
testbench reports which branch took each burst.

Usage:

    scripts/hyperram_model_sim.py                     # the whole matrix
    scripts/hyperram_model_sim.py --negative-control  # prove it can see a 1 CK error
    scripts/hyperram_model_sim.py --keep              # leave tmp/hyperram-model-sim
    scripts/hyperram_model_sim.py --dump              # write a VCD next to it
    scripts/hyperram_model_sim.py --sync-mhz 165      # a different CK

Log: `tmp/logs/hyperram_model_sim.log`.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from amaranth import Elaboratable, Module, Signal          # noqa: E402
from amaranth.back import verilog                          # noqa: E402
from luna.gateware.interface.psram import HyperBusPHY       # noqa: E402

from peripherals.hyperram_controller import HyperRAMController  # noqa: E402

MODEL = ROOT / "gateware" / "probes" / "hyperram" / "hyperram_model.v"
TESTBENCH = ROOT / "gateware" / "probes" / "hyperram" / "controller_model_tb.sv"
WORKDIR = ROOT / "tmp" / "hyperram-model-sim"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_model_sim.log"

# The controller's own name for its top level, and the one the testbench binds.
TOP = "hyperram_ctrl_top"

# Ceiling for `latency_clocks`. CR0[7:4] = 6 asks for 2 x 11 = 22 CK, which the
# shipped default of 14 cannot express, so the sweep needs a wider signal. This
# only widens `latency_clocks`/`low_latency_clocks` and the counter behind them;
# every value the shipped build can drive behaves identically.
MAX_LATENCY_CLOCKS = 32

# Longest step is vvp, at 1.34 s over three runs on this machine (iverilog is 0.03).
# 4 s is 3x that, the margin covering a cold page cache and a loaded machine. On
# expiry the step, its limit and its elapsed time are logged and the run exits
# non-zero -- a hung vvp is otherwise indistinguishable from a slow one.
STEP_TIMEOUT_S = 4

# tCSM and the fraction of it `HyperRAMController` gives one burst, restated here
# because `wait_idle` has to outlast the controller's own watchdog to see it work.
T_CSM_NS = 4000.0
T_CSM_MARGIN = 0.9

# Lines a good run must contain. The testbench grades itself; these are the ones
# whose ABSENCE would mean a case silently stopped being run.
REQUIRED_MARKERS = (
    "PASS ID0 = 0c86",
    "PASS ID1 = 0001",
    "PASS CR0 (POR) = 8f2f",
    "PASS CR1 (POR) = ffc1",
    "PASS CR0 read back = af2f",
    "PASS CR1 read back = ff81",
    "PASS mem[0x3fffff]",
    "eight single-word writes land in the model's array",
    "8-word burst write and read back agree",
    "PASS CR0 restored = 8f2f",
)

# Every latency code the sweep must have reached, in both modes.
LATENCY_CODES = (0, 1, 2, 6, 14, 15)

# The model elects the 2x count on a refresh collision, counted in transactions.
# At the shipped 100 whether a sweep cell collides depends on how many transactions
# ran before it -- which is the nondeterminism #338 is trying to pin down, not a way
# to test it. Both branches get a pass of their own, and the shipped cadence gets one
# too so the default configuration is still exercised.
REFRESH_PASSES = (
    (0, "no refresh collision -- variable latency takes the SHORT count"),
    (1, "a collision every transaction -- variable latency takes the LONG count"),
    (100, "the model's shipped cadence"),
)

log = logging.getLogger("hyperram-model-sim")


def setup_logging(verbose: bool) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"), logging.StreamHandler(sys.stdout)],
    )


class ControllerTop(Elaboratable):
    """`HyperRAMController` with its `HyperBusPHY` record brought out as ports.

    The record is the contract the controller is written against, so cutting here
    keeps the DUT exactly the gateware that ships. The 2:1 gearing that turns these
    16-bit ports into an 8-bit DDR bus lives in the testbench.
    """

    def __init__(self, *, sync_mhz: float, max_latency_clocks: int):
        self.phy = HyperBusPHY()
        self.ctl = HyperRAMController(phy=self.phy, sync_mhz=sync_mhz,
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

        # Device side.
        self.cs      = Signal()
        self.clk_en  = Signal()
        self.dq_o    = Signal(16)
        self.dq_i    = Signal(16)
        self.dq_e    = Signal()
        self.rwds_o  = Signal(2)
        self.rwds_i  = Signal(2)
        self.rwds_e  = Signal()

    def elaborate(self, platform):
        m = Module()
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

            self.cs          .eq(self.phy.cs),
            self.clk_en      .eq(self.phy.clk_en),
            self.dq_o        .eq(self.phy.dq.o),
            self.dq_e        .eq(self.phy.dq.e),
            self.rwds_o      .eq(self.phy.rwds.o),
            self.rwds_e      .eq(self.phy.rwds.e),
            self.phy.dq.i    .eq(self.dq_i),
            self.phy.rwds.i  .eq(self.rwds_i),
        ]
        return m

    def ports(self):
        return [
            self.start_transfer, self.register_space, self.perform_write,
            self.single_page, self.final_word, self.address, self.write_data,
            self.latency_clocks, self.low_latency_clocks, self.fixed_latency,
            self.idle, self.read_ready, self.write_ready, self.timed_out,
            self.read_data, self.state,
            self.cs, self.clk_en, self.dq_o, self.dq_i, self.dq_e,
            self.rwds_o, self.rwds_i, self.rwds_e,
        ]


def emit_controller(outdir: Path, sync_mhz: float, max_latency_clocks: int) -> tuple[Path, int]:
    """Elaborate the controller to Verilog and return the file and the port width."""
    top = ControllerTop(sync_mhz=sync_mhz, max_latency_clocks=max_latency_clocks)
    width = len(top.latency_clocks)
    text = verilog.convert(top, name=TOP, ports=top.ports())
    path = outdir / f"{TOP}.v"
    path.write_text(text)
    log.info("elaborated %s (%d lines, latency_clocks is %d bits, sync %.0f MHz)",
             path.name, text.count("\n") + 1, width, sync_mhz)
    return path, width


def latency_decode_probe(outdir: Path) -> dict[int, tuple[int, int]]:
    """Evaluate `hyperram_model.v`'s own `latency_ck` at every CR0[7:4].

    The function is LIFTED OUT OF THE MODEL FILE rather than restated, so this
    cannot drift into checking a copy. It answers a question the main testbench
    cannot: when a latency code produces no data phase at all, is the controller
    silent or is the device?
    """
    text = MODEL.read_text()
    m = re.search(r"^  function \[7:0\] latency_ck;.*?^  endfunction$", text,
                  re.S | re.M)
    if not m:
        raise SystemExit("could not lift `latency_ck` out of hyperram_model.v -- "
                         "the function was renamed or reshaped, fix this parser "
                         "before believing anything it would have reported")
    probe = outdir / "latency_decode_probe.v"
    probe.write_text(
        "`timescale 1ns/1ps\n"
        "// `latency_ck`, lifted verbatim from hyperram_model.v by\n"
        "// scripts/hyperram_model_sim.py. Do not edit; it is regenerated per run.\n"
        "module latency_decode_probe;\n"
        f"{m.group(0)}\n"
        "  integer c;\n"
        "  initial for (c = 0; c < 16; c = c + 1)\n"
        "    $display(\"decode %0d %0d %0d\", c,\n"
        "             latency_ck({8'h80, c[3:0], 4'h0}),\n"
        "             latency_ck({8'h80, c[3:0], 4'h8}));\n"
        "endmodule\n")
    run("iverilog(probe)", ["iverilog", "-g2012", "-o", "probe.vvp", probe.name])
    out = run("vvp(probe)", ["vvp", "probe.vvp"])

    decoded = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "decode":
            decoded[int(parts[1])] = (int(parts[2]), int(parts[3]))
    if len(decoded) != 16:
        raise SystemExit(f"latency decode probe produced {len(decoded)} of 16 rows -- "
                         f"suspect this parser before the model")
    return decoded


def decode_defects(decoded: dict[int, tuple[int, int]]) -> list[int]:
    """Codes where the model's decode disagrees with the table it documents."""
    bad = []
    for code, (variable, fixed) in decoded.items():
        want = 5 + (code - 16 if code >= 14 else code)
        if variable != want or fixed != want * 2:
            bad.append(code)
            log.error("model latency decode: code %d gives %d CK variable / %d CK "
                      "fixed, its own comment says %d / %d",
                      code, variable, fixed, want, want * 2)
    return bad


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
        for line in out.splitlines()[-25:]:
            log.error("  %s", line)
        raise SystemExit(proc.returncode)
    return out


def check_output(out: str) -> tuple[list[str], list[str]]:
    """Split what the run found into what indicts the controller and what does not.

    Attribution is by observable fact, never by "the controller passed":

      * `CTRL-BEAT FAIL` is the controller putting its first write word on a beat its
        own contract does not allow. Nothing the device does can cause it.
      * `MODEL-SILENT` and `MODEL-BEAT` are the device never starting a data phase,
        or starting one at a beat its own documented decode does not allow. Both are
        read off the bus, not off what the controller made of it.
    """
    controller, model = [], []

    for marker in REQUIRED_MARKERS:
        if marker not in out:
            controller.append(f"missing marker {marker!r}")

    # Every latency code must have been reported in both modes, or the sweep did not
    # run -- which reads exactly like a clean pass if only failures are counted.
    for mode in ("fixed", "var"):
        for code in LATENCY_CODES:
            if not re.search(rf"^\[tb\] {mode} +code +{code}:", out, re.M):
                controller.append(f"latency sweep never reported {mode} code {code}")

    controller += re.findall(r"^\[tb\] CTRL-BEAT FAIL.*$", out, re.M)
    model += re.findall(r"^\[tb\] MODEL-(?:SILENT|BEAT).*$", out, re.M)

    # A sweep cell the model provably cannot serve. Everything downstream of one --
    # the words that never landed, the burst that never finished, the controller
    # sitting in READ_DATA until its own watchdog ends it -- follows from the device,
    # so it is attributed there and not counted twice against the controller.
    excused = {
        (mode, code)
        for mode, code in
        re.findall(r"^\[tb\] MODEL-(?:SILENT|BEAT) +(\w+) +code +(\d+)", out, re.M)
    }
    for pattern in (r"^\[tb\] DATA FAIL (\w+) code (\d+).*$",
                    r"^\[tb\] INCOMPLETE \[(\w+) code (-?\d+)\].*$",
                    r"^\[tb\] NOT-IDLE \[(\w+) code (-?\d+)\].*$"):
        for m in re.finditer(pattern, out, re.M):
            if (m.group(1), m.group(2)) not in excused:
                controller.append(m.group(0))
    controller += re.findall(r"^\[tb\] FAIL.*$", out, re.M)

    branch = re.search(r"^\[tb\] BRANCH .*$", out, re.M)
    if branch:
        log.info("%s", branch.group(0).replace("[tb] BRANCH ", ""))
    else:
        controller.append("testbench did not report which READ_DATA branch it took")

    m = re.search(r"=== done, (\d+) checks, (\d+) failures ===", out)
    if not m:
        controller.append("testbench did not reach its summary line")
        return controller, model
    checks, errors = int(m.group(1)), int(m.group(2))
    if checks == 0:
        controller.append("testbench ran zero checks")
    log.info("testbench: %d checks, %d failures", checks, errors)
    return controller, model


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sync-mhz", type=float, default=100.0,
                    help="sync clock, which is also CK on this path (default: 100)")
    ap.add_argument("--max-latency-clocks", type=int, default=MAX_LATENCY_CLOCKS,
                    help="ceiling for latency_clocks; CR0[7:4]=6 needs 22 (default: 32)")
    ap.add_argument("--negative-control", type=int, metavar="CK", nargs="?", const=1,
                    help="tell the controller to wait CK more than it is graded "
                         "against, and REQUIRE the run to fail. A pass here means "
                         "the harness is not measuring the controller's latency")
    ap.add_argument("--dump", action="store_true", help="write a VCD")
    ap.add_argument("--keep", action="store_true", help="keep the work directory")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every tool line")
    args = ap.parse_args()

    setup_logging(args.verbose)

    for tool in ("iverilog", "vvp"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not on PATH")
    for path in (MODEL, TESTBENCH):
        if not path.exists():
            raise SystemExit(f"{path.relative_to(ROOT)} is missing")

    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    WORKDIR.mkdir(parents=True)

    dut, width = emit_controller(WORKDIR, args.sync_mhz, args.max_latency_clocks)
    shutil.copy(MODEL, WORKDIR / MODEL.name)
    shutil.copy(TESTBENCH, WORKDIR / TESTBENCH.name)

    bad_codes = decode_defects(latency_decode_probe(WORKDIR))
    if bad_codes:
        log.error("hyperram_model.v decodes CR0[7:4] %s wrongly -- every sweep cell "
                  "at those codes is the model's failure, not the controller's",
                  ", ".join(str(c) for c in bad_codes))
    else:
        log.info("model latency decode agrees with its own table at all 16 codes")

    # The controller ends a burst a silent device never finishes after this many
    # cycles; the testbench waits 1.4x it, so the watchdog is observed rather than
    # assumed. Same arithmetic as `HyperRAMController._burst_cycles`.
    burst_cycles = int(T_CSM_NS * T_CSM_MARGIN * args.sync_mhz / 1000.0) - 2

    controller, model = [], []
    for refresh_every, what in REFRESH_PASSES:
        log.info("=== REFRESH_EVERY = %d -- %s ===", refresh_every, what)
        run("iverilog", ["iverilog", "-g2012", f"-DLAT_W={width}",
                         f"-DTCK_NS={1000.0 / args.sync_mhz:.4f}",
                         f"-DRECOVER_LIMIT={int(burst_cycles * 1.4)}",
                         f"-DREFRESH_EVERY={refresh_every}",
                         "-o", "tb.vvp", TESTBENCH.name, MODEL.name, dut.name])
        plusargs = ["+dump"] if args.dump else []
        if args.negative_control:
            plusargs.append(f"+skew={args.negative_control}")
        out = run("vvp", ["vvp", "tb.vvp"] + plusargs)
        for line in out.splitlines():
            log.info("  %s", line)
        pass_controller, pass_model = check_output(out)
        controller += [f"[refresh {refresh_every}] {f}" for f in pass_controller]
        model += [f"[refresh {refresh_every}] {f}" for f in pass_model]

    if args.negative_control:
        # The control passes when the harness FAILS. Only controller-attributed
        # failures count: the model's own defects are there with or without the skew.
        beat_failures = [f for f in controller if "CTRL-BEAT FAIL" in f]
        if beat_failures:
            log.info("PASS (negative control) -- %d of the sweep's cells caught a "
                     "%d CK latency error", len(beat_failures), args.negative_control)
            return 0
        log.error("FAIL (negative control) -- a %d CK latency error went undetected, "
                  "so this harness is not measuring what it claims to",
                  args.negative_control)
        return 1

    if controller:
        for f in controller:
            log.error("CONTROLLER %s", f)
        log.error("FAIL -- the controller disagrees with hyperram_model.v")
    else:
        log.info("PASS (controller) -- CA, byte order and latency arithmetic agree "
                 "with hyperram_model.v everywhere the model can answer")

    if model:
        for f in model:
            log.error("MODEL %s", f)
        log.error("FAIL -- hyperram_model.v cannot serve every configuration swept")

    if not (controller or model) and not args.keep:
        shutil.rmtree(WORKDIR, ignore_errors=True)
    return 1 if (controller or model or bad_codes) else 0


if __name__ == "__main__":
    sys.exit(main())
