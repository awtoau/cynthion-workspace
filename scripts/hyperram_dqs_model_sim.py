#!/usr/bin/env python3
#
# Settle #186 in simulation: where a HyperRAM's data phase starts, and whether the
# DQS controller's 4:1 gearing can land on it.
# SPDX-License-Identifier: BSD-3-Clause

"""Three measurements, one runner.

    scripts/hyperram_dqs_model_sim.py                 # probe + controller, open twin
    scripts/hyperram_dqs_model_sim.py --stage probe   # the device alone
    scripts/hyperram_dqs_model_sim.py --stage order   # byte and word order, #206
    scripts/hyperram_dqs_model_sim.py --stage config  # the config path, #349 #366
    scripts/hyperram_dqs_model_sim.py --stage all     # all four
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

**order** settles #206: which byte of a 32-bit fabric word lands at which device
address, for reads and writes independently. Writes are checked against the
model's memory array read hierarchically, reads against an array preloaded
directly, so neither direction can cancel an error in the other. Every run also
repeats the measurement with the data beats deliberately rewired and requires
the checks to fire. The convention is in `docs/chips/hyperram/byte-order.md`.

**config** runs the ceiling engine's own configuration sequence -- CR0 write,
CR1 write, the register verifies, then a data burst -- through the real
controller. It settles which address space each burst named (#349) and whether
the configuration the engine would REPORT is the one the part holds (#366), with
two fault injections and a negative control that is the engine as filed.

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
sys.path.insert(0, str(ROOT / "scripts"))

from shared_paths import resolve_shared  # noqa: E402

PROBES = ROOT / "gateware" / "probes" / "hyperram"
PROBE_TB = PROBES / "dqs_latency_probe_tb.sv"
CTRL_TB = PROBES / "dqs_model_tb.sv"
ORDER_TB = PROBES / "dqs_order_tb.sv"
CONFIG_TB = PROBES / "dqs_config_tb.sv"
OPEN_MODEL = PROBES / "hyperram_model.v"

# `sources/**` is gitignored, so a worktree does not carry it. `resolve_shared`
# falls back to the main checkout; `HYPERRAM_MODEL_ZIP` overrides both. #365.
_MODEL_REL = Path("sources/models/W956X8MBY_verilog_p.zip")
MODEL_ZIP = (resolve_shared(ROOT, _MODEL_REL, env_var="HYPERRAM_MODEL_ZIP")
             or ROOT / _MODEL_REL)
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


def elaborate_controller(round_trip: int | None = None,
                         stem: str = "dqs_controller") -> Path:
    """`HyperRAMDQSController` as Verilog, with the PHY record brought out flat.

    `round_trip` overrides `phy_round_trip_cycles`, which is what decides the
    cycle the extra-latency RWDS sample is taken in. A wrong one is the control
    for #381: the sample lands on a pin cycle that is not the CA's.
    """
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
            extra = ({} if round_trip is None
                     else {"phy_round_trip_cycles": round_trip})
            self.ctl = HyperRAMDQSController(
                phy=self.phy, sync_mhz=SYNC_MHZ,
                max_latency_clocks=MAX_LATENCY_CLOCKS, **extra)
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
        ctl.low_latency_clocks, ctl.write_data, ctl.idle, ctl.read_ready,
        ctl.write_ready, ctl.timed_out, ctl.state, ctl.read_data,
        ctl.extra_latency,
        dut.phy_cs, dut.phy_clk_en, dut.phy_dq_o, dut.phy_dq_e, dut.phy_rwds_o,
        dut.phy_rwds_e, dut.phy_read, dut.phy_dq_i, dut.phy_rwds_i,
        dut.phy_datavalid, dut.phy_burstdet,
    ]
    src = verilog.convert(dut, name="dqs_controller", ports=ports)
    out = WORKDIR / f"{stem}.v"
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


# The behavioural PHY's `(dq_pipe, ck_pipe, dq_ph, rd_slip)`.
#
# The primitives' SCLK-to-pin latencies have no open model, so the relative
# offset between the CK path and the DQ path is a searched parameter. The
# controller stage starts at zero and sweeps; `--controller-sweep` at
# 57a9a99 found (0, 1, 1, 0) to be the ONLY one of the sixteen in which the
# device resolves the address the controller asked for, so the order stage
# starts there rather than measuring in a setting where nothing decodes.
CTRL_SHIM = (0, 0, 0, 0)
ORDER_SHIM = (0, 1, 1, 0)


def shim(args, default: tuple) -> tuple:
    """The shim offsets for a stage: its own default where the caller said nothing."""
    given = (args.dq_pipe, args.ck_pipe, args.dq_ph, args.rd_slip)
    return tuple(d if g is None else g for g, d in zip(given, default))


def build_order() -> None:
    need("iverilog", "vvp")
    ctl = elaborate_controller()
    run("iverilog(order)", ["iverilog", "-g2012", "-DDUT_MODULE=hyperram_model",
                            "-o", "order.vvp", str(ORDER_TB), str(OPEN_MODEL),
                            ctl.name], WORKDIR)


def stage_order(extra: list[str]) -> list[str]:
    return run("vvp(order)", ["vvp", "order.vvp"] + extra, WORKDIR).splitlines()


def build_config(round_trip: int | None = None) -> str:
    """Compile the config testbench, optionally against a controller whose RWDS
    sample cycle is derived from another round trip. Returns the image name."""
    need("iverilog", "vvp")
    stem = "dqs_controller" if round_trip is None else f"dqs_controller_r{round_trip}"
    ctl = elaborate_controller(round_trip, stem)
    image = "config.vvp" if round_trip is None else f"config_r{round_trip}.vvp"
    run("iverilog(config)", ["iverilog", "-g2012", "-DDUT_MODULE=hyperram_model",
                             "-o", image, str(CONFIG_TB), str(OPEN_MODEL),
                             ctl.name], WORKDIR)
    return image


def stage_config(extra: list[str], image: str = "config.vvp") -> list[str]:
    return run("vvp(config)", ["vvp", image] + extra, WORKDIR).splitlines()


# tCSHI at the grade fitted -- a `6I` = T166, 6 ns in Winbond's `Config-AC.v`.
# Was 10.0, the T100 column (#341). The testbench measures the real gap; this is
# what it is judged against.
T_CSHI_NS = 6.0


def sequences(rows: list[dict]) -> list[list[dict]]:
    """The rows split into the engine sequences that produced them."""
    out: list[list[dict]] = []
    for row in rows:
        if row.get("txn") == "wr_cr0" or not out:
            out.append([])
        out[-1].append(row)
    return out


def _word(row: dict) -> int | None:
    try:
        return int(row["got"], 16)
    except (KeyError, ValueError):
        return None


def verdict_readback(rows: list[dict]) -> list[str]:
    """Is the CR0/CR1 the engine would report the CR0/CR1 the part holds? #366.

    The readback captures through the same window as the data path, so at a
    phase outside it the reported configuration is a word from another
    transaction -- and `bist status` decoded one into "the part is asleep".

    Judged against `dev_cr0`/`dev_cr1`, read out of the device model itself. Not
    against what was written: the part is allowed to discard a CR1 write it does
    not support (#334), and that difference is the measurement.

    The three rules are the engine's own (`REG_CTRL_STATE` bits 8..10). A wrong
    word that fires none of them is a fabricated configuration reported as fact.
    """
    bad: list[str] = []
    wrong_seen = fired_seen = 0
    log.info("--- #366: does the reported configuration match the part's own ---")
    for seq in sequences(rows):
        reads = {row["txn"]: _word(row) for row in seq
                 if row.get("txn", "").startswith("rd_cr")}
        if "rd_cr0" not in reads or "rd_cr1" not in reads:
            continue
        held = {name: int(seq[-1][f"dev_{name}"], 16) for name in ("cr0", "cr1")}
        cr0, cr1, again = reads["rd_cr0"], reads["rd_cr1"], reads.get("rd_cr0b")
        if cr0 is None or cr1 is None:
            bad.append("a register read returned no word at all")
            continue

        # A register read arrives in BOTH halves of the fabric word, so what the
        # part holds predicts all 32 bits.
        wrong = [name for name, word, want in
                 (("CR0", cr0, held["cr0"]), ("CR1", cr1, held["cr1"]))
                 if word != want * 0x10001]

        fired = []
        if again is not None and again != cr0:
            fired.append("reread")
        elif again is None:
            fired.append("reread UNAVAILABLE")
        if cr0 == cr1:
            fired.append("distinct")
        if any((w >> 16) != (w & 0xffff) for w in (cr0, cr1)):
            fired.append("halves")
        withheld = [rule for rule in fired if not rule.endswith("UNAVAILABLE")]

        wrong_seen += bool(wrong)
        fired_seen += bool(withheld)
        log.info("  part holds CR0=%04x CR1=%04x  engine reports %08x %08x  %s",
                 held["cr0"], held["cr1"], cr0, cr1,
                 ("WRONG: " + ", ".join(wrong) + "  " if wrong else "")
                 + ("withheld by " + ", ".join(fired) if fired else "reported"))
        if wrong and not withheld:
            bad.append(f"the engine reports CR0={cr0 & 0xffff:04x} CR1={cr1 & 0xffff:04x} "
                       f"where the part holds {held['cr0']:04x}/{held['cr1']:04x}, "
                       f"and no rule marks it -- a fabricated configuration "
                       f"reported as fact ({', '.join(fired) or 'no rule fired'})")
        if not wrong and withheld:
            bad.append(f"a correct readback was withheld by {', '.join(withheld)} "
                       f"-- a rule that fires on good data withholds every run")
    if rows and not wrong_seen:
        log.info("  every reported configuration matched the part's own")
    elif wrong_seen and not fired_seen:
        log.info("  no rule fired on any of the %d wrong readbacks", wrong_seen)
    return bad


def verdict_config(rows: list[dict]) -> list[str]:
    """Did a DATA burst address register space, and what did it return?

    Two mechanisms produce the board's `0xffc1ffc1`, and they want opposite
    fixes: the transaction addressed register space (a CA fault, or a CS# that
    never rose), or it addressed memory and the read path handed back the last
    word it captured. The device model decodes the CA itself, so the first is
    settled by asking the DEVICE.
    """
    bad: list[str] = []
    log.info("--- #349: which space did each transaction address ---")
    control_caught = 0
    stale = []
    last_reg_read = {}
    for row in rows:
        tag, dv = row.get("txn", ""), row.get("dv", "?")
        if tag == "rd_cr1":
            last_reg_read[dv] = row.get("got")
        elif tag == "mem_rd" and row.get("got") == last_reg_read.get(dv):
            stale.append((dv, row.get("got")))
    for row in rows:
        tag = row.get("txn", "")
        dev_reg = row.get("dev_register", "x")
        forced = row.get("space") == "1" and tag.startswith("mem")
        note = ""
        if tag.startswith("mem"):
            if dev_reg == "1" and not forced:
                note = "  <-- DATA BURST DECODED AS REGISTER SPACE"
                bad.append(f"{tag}: the device decoded a data burst as register "
                           f"space (served {row.get('served')})")
            elif dev_reg == "1" and forced:
                control_caught += 1
                note = "  (control: register space forced, and seen)"
        if row.get("hung") == "1":
            bad.append(f"{tag}: the controller never returned to idle")
        try:
            gap = float(row.get("csh_ns", "-1"))
        except ValueError:
            gap = -1.0
        if 0.0 <= gap < T_CSHI_NS:
            note += f"  <-- CS# High only {gap:g} ns, tCSHI is {T_CSHI_NS:g}"
            bad.append(f"{tag}: CS# High {gap:g} ns, under tCSHI {T_CSHI_NS:g} ns")
        log.info("  dv=%s %-7s asked space %s, DEVICE decoded register=%s "
                 "read=%s served %-6s got %s  CS# high %s ns%s",
                 row.get("dv"), tag, row.get("space"), dev_reg,
                 row.get("dev_read"), row.get("served"), row.get("got"),
                 row.get("csh_ns"), note)

    if rows and not control_caught:
        bad.append("the register-space CONTROL was not caught: a data burst "
                   "issued with `register_space` forced high was not seen as "
                   "register space, so this stage cannot exonerate the CA")

    # THE FINGERPRINT. A memory read returning the LAST REGISTER READ's word,
    # with the CA correctly naming memory space, is the board's symptom without
    # any leak in the CA -- the read path handed back a word it did not capture.
    if stale:
        log.info("  a memory read returned the CR1 read's own word at dv=%s: %s",
                 ",".join(sorted({d for d, _ in stale})),
                 ",".join(sorted({g for _, g in stale})))
        log.info("  the CA named MEMORY in every one of them, so this is not a "
                 "register-space leak -- it is a read that returned what the "
                 "data registers were still holding")
    return bad


# The word `mem_wr` writes and `mem_rd` must read back. The DQS path packs two
# device words per beat, so a read landing one CK early returns them rotated --
# `10011000` -- and one CK late loses the first entirely.
MEM_WORD = "10001001"

# The round trip the DQS controller is built for, read from the controller so the
# sweep below reports against the shipped value rather than a copy of it. (#381)
def _dqs_round_trip() -> int:
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    from peripherals import hyperram_dqs_controller
    return hyperram_dqs_controller.PHY_ROUND_TRIP_CYCLES


DQS_ROUND_TRIP = _dqs_round_trip()


def verdict_latency(rows: list[dict], label: str) -> list[str]:
    """VARIABLE latency: did the controller wait what the device actually took?

    The only sequences in this file where the election is live. `elected` is the
    controller's own `extra_latency`; `dev_long` is what the DEVICE decided at
    CS# falling, so the two are independent statements about the same
    transaction and disagreeing is the #381 failure. `lat_ck` is the CK the
    device waited, and a `mem_rd` that is not `MEM_WORD` is the #380 one.
    """
    bad: list[str] = []
    log.info("--- #380/#381: variable latency, %s ---", label)
    log.info("  %-9s %-9s %-8s %-9s %-11s %s", "CR0", "device CK", "elected",
             "dev_long", "READ/data", "got")
    seen = 0
    for row in rows:
        if row.get("txn") != "mem_rd":
            continue
        seen += 1
        cr0, got = row.get("dev_cr0"), row.get("got")
        elected, dev_long = row.get("elected"), row.get("dev_long")
        lat = int(row.get("lat_ck", -1))
        read_e, data_e = int(row.get("read_edge", -1)), int(row.get("data_edge", -1))
        note = ""
        if elected != dev_long:
            note += "  <-- ELECTION DISAGREES WITH THE DEVICE"
            bad.append(f"CR0={cr0}: the controller elected long={elected} where "
                       f"the device took long={dev_long}")
        # The window against the data, in device CK edges. `READ_DATA` must be
        # entered before the device drives -- late loses words outright -- and by
        # no more than the gearing's own quantum: the wait moves in 2 CK = 4
        # edges, so at an ODD L the closest reachable lead is 3.
        if not 0 <= data_e - read_e <= 3:
            note += f"  <-- window {data_e - read_e} edges off"
            bad.append(f"CR0={cr0} ({lat} CK): READ_DATA entered at edge "
                       f"{read_e} and the device drove at {data_e} -- "
                       f"{data_e - read_e} edges, wanted 0..3")
        # An ODD device latency puts the first data edge mid-beat, which 4:1
        # gearing cannot express: the word arrives rotated by one CK whatever
        # count is waited. That is #186's missing bitslip, not the count.
        if got != MEM_WORD and lat % 2 == 0:
            note += f"  <-- read {got}, not {MEM_WORD}"
            bad.append(f"CR0={cr0} ({lat} CK, EVEN): the memory read returned "
                       f"{got}, not {MEM_WORD}")
        elif got != MEM_WORD:
            note += f"  (odd L: rotated, #186)"
        log.info("  %-9s %-9s %-8s %-9s %-11s %s%s", cr0, lat, elected, dev_long,
                 f"{read_e}/{data_e}", got, note)
    if not seen:
        bad.append(f"no variable-latency memory read in {label} -- the "
                   "sequence set did not run")
    return bad


def elections(rows: list[dict]) -> list[str]:
    """The election disagreements alone, out of `verdict_latency`'s findings.

    The window and the word move with the round trip whatever RWDS did, so a
    float experiment graded on all three would report the build, not the float.
    """
    return [b for b in verdict_latency(rows, "float") if "elected long" in b]


def float_witness(lines: list[str]) -> tuple[int, int]:
    """`(floats met, floats read High)` from the testbench's own counters. (#400)"""
    for line in lines:
        m = re.search(r"floats=(\d+) float_high=(\d+)", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return (0, 0)


def stage_float(failures: list[str], extra: list[str]) -> None:
    """#400: a float given a value, and what the election does with it.

    Four runs, one experiment. `var` is `latency_mode=1` with the device
    DECLINING, which is the only shape in which a float read High invents a
    request nobody made; `fix` is the same float with `fixed_latency` on, where
    the election is gated off. The pre-#381 build samples at cycle 2, before CS#
    has fallen, and the shipped one samples inside the driven CA.
    """
    var = extra + ["+dv_from_read=0", "+latency_mode=1", "+refresh_every=100"]
    pre = build_config(round_trip=-1)          # sample cycle 2, where #381 found it
    now = "config.vvp"                         # the shipped round trip

    def run_float(image, args, pct, tag):
        lines = stage_config(args + [f"+rwds_float_pct={pct}"], image)
        met, high = float_witness(lines)
        return elections(report(lines, tag)), met, high

    # 1. THE CONTROL, and it is #400 itself: the same defective build with the
    #    float read as a firm 0 -- which is what every testbench here did. It must
    #    NOT fire, or the float is not what the run below is measuring.
    blind, met, high = run_float(pre, var, 0, "pre/0")
    if high:
        failures.append(f"#400 control: {high} floats read High at pct=0 -- the "
                        "default is not deterministic")
    if not met:
        failures.append("#400 control: the pre-#381 build met no float at all, so "
                        "nothing below injected anything")
    if blind:
        failures.append("#400 control: the pre-#381 sample cycle was ALREADY "
                        f"caught with the float read as 0 ({blind[0]}), so the "
                        "float is not what the checks below see")

    # 2. THE MECHANISM. Same build, same device, float read High.
    caught, met_hi, high_hi = run_float(pre, var, 100, "pre/100")
    if not high_hi:
        failures.append("#400: pct=100 injected no float High, so the knob is inert")
    reproduces = bool(caught)

    # 3. THE ASYMMETRY. Fixed latency, same build, same float: `fixed_latency`
    #    gates the election, so a float High must be inert here.
    fix, _, _ = run_float(pre, extra + ["+dv_from_read=0", "+latency_mode=0"],
                          100, "pre/fix")
    if fix:
        failures.append(f"#400: a float read High moved a FIXED-latency election "
                        f"({fix[0]}) -- the election is meant to be gated off there")

    # 4. #381's FIX, against the mechanism rather than consistent with it: the
    #    shipped sample cycle sits inside the driven CA, so the same float must
    #    change nothing.
    shipped, _, _ = run_float(now, var, 100, "now/100")

    log.info("--- #400: the RWDS float given a value ---")
    log.info("  pre-#381 build (sample cycle 2), device DECLINES:")
    log.info("    float read as 0   -- %d election(s) fired, %d floats met",
             len(blind), met)
    log.info("    float read High   -- %d election(s) fired, %d of %d floats High",
             len(caught), high_hi, met_hi)
    log.info("    ...same float, FIXED latency -- %d election(s) fired", len(fix))
    log.info("  shipped build (round trip %d, sample cycle %d), same float High "
             "-- %d election(s) fired", DQS_ROUND_TRIP, DQS_ROUND_TRIP + 3,
             len(shipped))
    for line in dict.fromkeys(caught):
        log.info("    reproduced: %s", line)

    if reproduces:
        log.info("  VERDICT: the marginality mechanism REPRODUCES -- a float read "
                 "High elects the long count on a `var` transaction the device "
                 "declined, and is inert on a `fix` one")
    else:
        failures.append("#400: a float read High at every sample changed no "
                        "election even at the pre-#381 sample cycle, so the "
                        "mechanism does not reproduce and the corpus's rate has "
                        "no support here")
    if shipped:
        failures.append(f"#400: the SHIPPED sample cycle elected off a float "
                        f"({shipped[0]}) -- #381's fix does not remove the "
                        "mechanism")


def report(lines: list[str], tag: str) -> list[dict]:
    """The measurement lines, wherever the simulator put its own prefix."""
    rows = []
    for line in lines:
        text = line.lstrip("# ").rstrip()
        if text.startswith(("[probe]", "[dqs]", "[edge]", "[order]", "[cfg]")):
            log.info("%-6s %s", tag, text)
        if text.startswith(("[probe] cr0=", "[probe] reserved=", "[dqs] code=",
                            "[order] test=", "[cfg] txn=")):
            rows.append(dict(kv.split("=", 1) for kv in text.split()[1:] if "=" in kv))
    return rows


# CR0[7:4] is sparse and sign-extended: 0..2 give 5..7 CK and 14..15 give 3..4.
# Stated here from the datasheet, not from either model, so a model that decodes a
# code wrongly is caught rather than agreed with.
BASE_CK = {0: 5, 1: 6, 2: 7, 14: 3, 15: 4}


def verdict_probe(twin: list[dict], vendor: list[dict]) -> list[str]:
    """What the probe settles, checked rather than asserted in prose."""
    bad = []
    twin, twin_res = split_reserved(twin)
    vendor, vendor_res = split_reserved(vendor)
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
    bad += verdict_reserved(twin_res, vendor_res)
    return bad


def split_reserved(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """The sweep's legal-code rows, and the reserved-predecessor rows (#350)."""
    return ([r for r in rows if "reserved" not in r],
            [r for r in rows if "reserved" in r])


def verdict_reserved(twin: list[dict], vendor: list[dict]) -> list[str]:
    """Reserved CR0[7:4]: undefined on the part, so the ONLY reference is the vendor.

    Nothing is asserted about what the number should be -- only that the twin does
    not invent a different one, and that it varies with the predecessor rather than
    with the code. (#350)
    """
    bad = []
    if not twin:
        return bad
    log.info("--- reserved codes: what is held, and after what ---")
    by_key = {(r["code"], r["after"], r["fixed"]): r for r in vendor}
    for row in twin:
        other = by_key.get((row["code"], row["after"], row["fixed"]))
        log.info("  code %2s after %2s fixed=%s: twin edge %3s, vendor edge %3s",
                 row["code"], row["after"], row["fixed"], row["read_first"],
                 other["read_first"] if other else "n/a")
        if other and row["read_first"] != other["read_first"]:
            bad.append(f"reserved code {row['code']} after {row['after']} "
                       f"fixed={row['fixed']}: twin edge {row['read_first']}, "
                       f"vendor edge {other['read_first']} -- the twin invents a "
                       f"latency where the part is undefined")
    for code, fixed in sorted({(r["code"], r["fixed"]) for r in twin}):
        edges = {r["read_first"] for r in twin
                 if (r["code"], r["fixed"]) == (code, fixed)}
        if len(edges) == 1:
            bad.append(f"reserved code {code} fixed={fixed} answered edge "
                       f"{edges.pop()} whatever preceded it -- it is deriving a "
                       f"latency from the code, not holding the last legal one")
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


# THE MEASURED CONVENTION (#206), as a check rather than as prose.
#
# Both are "fabric byte position f -> device byte offset", where f = 0 is
# `dq.o[31:24]` / `dq.i[31:24]` and offset 0 is the high byte of the
# lower-addressed 16-bit device word. (0, 1, 2, 3) is therefore:
#
#   dq[31:24] -> device byte 0, the HIGH byte of the LOWER-addressed word
#   dq[23:16] -> device byte 1, the low  byte of the lower-addressed word
#   dq[15:8]  -> device byte 2, the high byte of the higher-addressed word
#   dq[7:0]   -> device byte 3, the low  byte of the higher-addressed word
#
# Measured by `--stage order`; see docs/chips/hyperram/byte-order.md. A change
# here is a change to the wire format, so the run fails rather than reporting a
# new convention quietly.
ORDER_WRITE = (0, 1, 2, 3)
ORDER_READ = (0, 1, 2, 3)

# What each 8-byte `wire=` field says when a slot was never driven.
_UNSET = ("xx", "zz")

_PERM_NAME = {0: "as wired", 1: "bytes swapped inside each half",
              2: "16-bit halves swapped", 3: "full reverse"}


def _wire_bytes(field: str) -> list[int | None]:
    return [None if field[i:i + 2] in _UNSET else int(field[i:i + 2], 16)
            for i in range(0, len(field), 2)]


def _fabric_to_device(fabric: list[int], device: list[int]) -> tuple | None:
    """For each fabric byte position, where in the device pair it turned up.

    Returns None unless the two lists are the same four distinct values -- which
    is the whole reason the patterns are chosen with four distinct bytes. A
    pattern that cannot do this cannot answer the question and says so.
    """
    if len(set(fabric)) != 4 or sorted(fabric) != sorted(device):
        return None
    return tuple(device.index(b) for b in fabric)


def verdict_order(rows: list[dict]) -> list[str]:
    bad: list[str] = []
    log.info("--- #206: what order the 32-bit path uses ---")

    #
    # 1. The CA, which the device decodes into an address.
    #
    ca = [r for r in rows if r.get("test") == "ca"]
    identity_ok = False
    discriminated = 0
    for row in ca:
        perm, want, got = int(row["perm"]), row["addr"], row["served"]
        same = int(want, 16) == int(got, 16)
        log.info("  ca    perm %d (%-30s) asked %6s, device resolved %6s  %s",
                 perm, _PERM_NAME[perm], want, got,
                 "MATCH" if same else "differs")
        if perm == 0:
            identity_ok = same
            if not same:
                bad.append(f"the PHY's own byte order does not decode a CA: "
                           f"asked {want}, device resolved {got}")
        elif same:
            bad.append(f"CA permutation {perm} resolved the SAME address -- the "
                       f"CA test cannot tell the byte orders apart")
        else:
            discriminated += 1
    if ca and identity_ok and discriminated == 3:
        log.info("  ca    -> only the wiring as written decodes the address the "
                 "controller asked for; the other three orders name other "
                 "transactions. The data path shares this mapping.")

    #
    # 2. Writes, read back out of the model's array and not through the controller.
    #
    writes = [r for r in rows if r.get("test") == "write"]
    # A pattern with a repeated byte, or with equal halves, leaves two of the
    # candidate orders producing the same memory image -- it would look like a
    # measurement and settle nothing. Checked, not asserted in the comment.
    for pat in sorted({r["pat"] for r in writes}):
        pb = [(int(pat, 16) >> s) & 0xFF for s in (24, 16, 8, 0)]
        if len(set(pb)) != 4:
            bad.append(f"write pattern {pat} repeats a byte: it cannot tell the "
                       f"candidate orders apart")
    # ON THE WIRE, before the device has interpreted anything: the last four
    # bytes the controller drove are the data word, and their order is the
    # answer for the write direction. Checked on every row, so a change shows up
    # here as well as in the memory image.
    for row in writes:
        pb = [(int(row["pat"], 16) >> s) & 0xFF for s in (24, 16, 8, 0)]
        drove = [b for b in _wire_bytes(row["wire"]) if b is not None]
        if drove[-4:] != pb:
            seq = "".join(f"{b:02x}" for b in drove[-4:])
            bad.append(f"the data word did not leave MSB-first: {row['pat']} went "
                       f"out on the wire as {seq}")
            break
    aligned_w = []
    for row in writes:
        pat = int(row["pat"], 16)
        pb = [(pat >> s) & 0xFF for s in (24, 16, 8, 0)]
        m0, m1 = int(row["m0"], 16), int(row["m1"], 16)
        spill = [row[k] for k in ("mm2", "mm1", "m2", "m3") if int(row[k], 16) != 0xEEEE]
        dev = [m0 >> 8, m0 & 0xFF, m1 >> 8, m1 & 0xFF]
        sig = _fabric_to_device(pb, dev)
        if sig is not None and not spill:
            aligned_w.append((row, sig))
    if writes and not aligned_w:
        got = {(r["pat"], r["m0"], r["m1"]) for r in writes}
        bad.append("no write landed the four pattern bytes in the addressed "
                   f"device word pair at any latency: saw {sorted(got)}")
    seen_w: dict[tuple, list[str]] = {}
    for row, sig in aligned_w:
        seen_w.setdefault((row["pat"], row["m0"], row["m1"], sig), []).append(
            f"code {row['code']} n={row['n']}")
    for (pat, m0, m1, sig), where in seen_w.items():
        log.info("  write %s -> memory[+0]=%s memory[+1]=%s, fabric->device %s "
                 "(%d settings, %s .. %s)%s", pat, m0, m1, sig, len(where),
                 where[0], where[-1],
                 "" if sig == ORDER_WRITE else "  <-- NOT the recorded order")
        if sig != ORDER_WRITE:
            bad.append(f"write order changed: pattern {pat} landed as {sig}, "
                       f"recorded {ORDER_WRITE} (docs/chips/hyperram/byte-order.md)")

    #
    # 3. Reads, out of a preloaded array the controller never wrote.
    #
    reads = [r for r in rows if r.get("test") == "read"]
    # The premise of the read test: the device serves the preloaded ramp from the
    # addressed word, so a `read_data` byte's value IS its device byte offset.
    # If the wire does not carry 00..07 the reference is gone and nothing below
    # means anything.
    for row in reads:
        if _wire_bytes(row["wire"]) != list(range(8)):
            bad.append(f"the device did not serve the ramp from the addressed "
                       f"word: wire {row['wire']}, expected 0001020304050607")
            break
    aligned_r = []
    for row in reads:
        if row["gv"] != "1":
            continue
        got = int(row["got"], 16)
        # The ramp makes every byte in the window equal to its own device byte
        # offset, so the four bytes of `read_data` name their own positions.
        off = [(got >> s) & 0xFF for s in (24, 16, 8, 0)]
        if len(set(off)) != 4 or max(off) - min(off) != 3:
            continue                      # not four contiguous device bytes
        aligned_r.append((row, tuple(o - min(off) for o in off), min(off)))
    if reads and not aligned_r:
        bad.append("no read returned four contiguous ramp bytes at any latency: "
                   f"saw {sorted({r['got'] for r in reads})}")
    seen_r: dict[tuple, list[str]] = {}
    for row, sig, first in aligned_r:
        seen_r.setdefault((row["got"], sig, first), []).append(
            f"code {row['code']} n={row['n']}")
    for (got, sig, first), where in seen_r.items():
        log.info("  read  read_data=%s, first device byte %+d from the addressed "
                 "word, fabric->device %s (%d settings, %s .. %s)%s",
                 got, first, sig, len(where), where[0], where[-1],
                 "" if sig == ORDER_READ else "  <-- NOT the recorded order")
        if sig != ORDER_READ:
            bad.append(f"read order changed: read_data {got} maps as {sig}, "
                       f"recorded {ORDER_READ} (docs/chips/hyperram/byte-order.md)")

    # The slip is a SEPARATE fault from the order and is reported, not merged
    # into it: #186 owns where the read grouping is anchored.
    slips = {first for _, _, first in aligned_r}
    if slips:
        log.info("  read  grouping starts %s device bytes into the addressed "
                 "word (0 = aligned; this is #186's slip, not an order)",
                 sorted(slips))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="both",
                    choices=("probe", "controller", "order", "config",
                             "both", "all"))
    ap.add_argument("--sim", default="icarus", choices=("icarus", "questa", "both"),
                    help="which device model the probe stage runs against")
    ap.add_argument("--grade", default="T166")
    ap.add_argument("--part", default="W956A8MBYA")
    ap.add_argument("--controller-sweep", action="store_true",
                    help="run the controller stage at every shim pipeline offset")
    # Unset means each stage's own default: the controller stage starts from
    # zero offset and sweeps, the order stage starts from the ONE combination
    # that combination sweep found the device decoding a CA in.
    for name in ("--dq-pipe", "--ck-pipe", "--dq-ph", "--rd-slip"):
        ap.add_argument(name, type=int, default=None)
    ap.add_argument("--data-perm", type=int, default=0, choices=(0, 1, 2, 3),
                    help="order stage: rewire the DATA beats (0 = the PHY as "
                         "written; 1 bytes-in-half, 2 halves, 3 reversed)")
    ap.add_argument("--no-control", action="store_true",
                    help="order stage: skip the negative control run")
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

    if args.stage in ("probe", "both", "all"):
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

    if args.stage in ("controller", "both", "all"):
        log.info("=== controller through a behavioural 4:1 PHY ===")
        build_controller()
        combos = [shim(args, CTRL_SHIM)]
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

    if args.stage in ("config", "all"):
        log.info("=== the engine's CONFIG path, then a data burst (#349) ===")
        build_config()
        dq, ck, phs, slip = shim(args, ORDER_SHIM)
        extra = [f"+dq_pipe={dq}", f"+ck_pipe={ck}",
                 f"+dq_ph={phs}", f"+rd_slip={slip}"]
        if args.verbose_edges:
            extra.append("+verbose=1")
        # BOTH handshakes. `dv_from_read=0` is the generous one and settles
        # whether the CA ever names register space; `=1` is DQSBUFM's shape --
        # DATAVALID from the READ window rather than from data arriving -- and is
        # the only setting in which a read can hand back a word it did not
        # capture. The comparison between them IS the result.
        rows = []
        for dv in (0, 1):
            log.info("--- DATAVALID from %s ---",
                     "the device driving DQ (generous)" if dv == 0
                     else "the controller's READ window (DQSBUFM's shape)")
            rows += report(stage_config(extra + [f"+dv_from_read={dv}"]), "cfg")
        if not rows:
            failures.append("config stage produced no measurement")
        failures += verdict_config(rows)
        failures += verdict_readback(rows)

        # THE READ PATH ONE TRANSACTION BEHIND, which is #366's board
        # fingerprint: every register read returns a plausible register word and
        # it is the previous transaction's. Nothing about any one word is wrong.
        log.info("--- the capture lagging by one transaction (#366) ---")
        lagged = extra + ["+dv_from_read=1", "+cap_lag=1"]
        failures += verdict_readback(report(stage_config(lagged), "lag"))

        # THE NEGATIVE CONTROL, and it is the engine AS FILED: two register
        # reads, so the lag returns one word to CR0's read and CR0's own word to
        # CR1's, and no rule computable from those two can see it. The checks
        # MUST fail here, or they cannot see the fault they claim to catch.
        log.info("--- negative control: the same lag, and no re-read (#366) ---")
        caught = verdict_readback(
            report(stage_config(lagged + ["+verify_reread=0"]), "asfiled"))
        if caught:
            log.info("  control: %d check(s) fired on the two-read sequence",
                     len(caught))
            for line in dict.fromkeys(caught):
                log.info("    would fail: %s", line)
        else:
            failures.append("the negative control PASSED: a read path one "
                            "transaction behind was not caught without the "
                            "re-read, so the readback checks prove nothing")

        # VARIABLE LATENCY. The election is inert in every sequence above --
        # CR0[3] is 1 in all four -- so the two defects #380 and #381 name are
        # unreachable there. Both answers the device can give are driven:
        # `refresh_every=1` makes it ask on every transaction, the default makes
        # it decline on all of them. (#380, #381)
        var = extra + ["+dv_from_read=0", "+latency_mode=1"]
        for asks, name in ((0, "the device DECLINES the extra latency"),
                           (1, "the device ASKS for it on every transaction")):
            log.info("--- variable latency: %s ---", name)
            failures += verdict_latency(
                report(stage_config(var + [f"+refresh_every={1 if asks else 100}"]),
                       "var"), name)

        # THE CONTROL for #380: the short count forced back to the class
        # constant, at every code. It must FAIL, or the checks above cannot see
        # a short count that matches no latency code.
        log.info("--- negative control: the short count welded to 3 (#380) ---")
        caught = verdict_latency(
            report(stage_config(var + ["+refresh_every=100", "+low_const=3"]),
                   "welded"), "the short count welded to 3")
        if caught:
            log.info("  control: %d check(s) fired on the welded short count",
                     len(caught))
            for line in dict.fromkeys(caught):
                log.info("    would fail: %s", line)
        else:
            failures.append("the #380 control PASSED: a short count welded to 3 "
                            "at every latency code read correctly, so the "
                            "variable-latency checks prove nothing")

        # WHERE THE SAMPLE MAY SIT, measured rather than derived. The controller
        # is rebuilt at each round trip, which moves `rwds_sample_cycle` with it,
        # and the device asks on every transaction so a sample that misses the
        # request elects the short count. At least one setting must FAIL, or the
        # cycle is not load-bearing and the checks above prove nothing. (#381)
        log.info("--- where the RWDS sample may sit, per round trip (#381) ---")
        # -1 is not a physical round trip: it puts the sample at cycle 2, which
        # is where `SHIFT_COMMAND0` had it, so the sweep spans the old location
        # as well as the new one.
        survives = []
        for trip in range(-1, 5):
            caught = verdict_latency(
                report(stage_config(var + ["+refresh_every=1"],
                                    build_config(round_trip=trip)), f"r{trip}"),
                f"round trip {trip}, sample at cycle {trip + 3}")
            (survives if not caught else []).append(trip)
            log.info("  round trip %d, sample cycle %d: %d check(s) fired",
                     trip, trip + 3, len(caught))
        log.info("  the election survives at round trip(s) %s of -1..4, i.e. "
                 "sample cycle(s) %s; the controller is built for %d",
                 survives, [t + 3 for t in survives], DQS_ROUND_TRIP)
        if len(survives) == 6:
            failures.append("the RWDS sample read the device's request at EVERY "
                            "round trip from -1 to 4, so the sample cycle is "
                            "not load-bearing and these checks prove nothing")
        if DQS_ROUND_TRIP not in survives:
            failures.append(f"the controller's own round trip {DQS_ROUND_TRIP} "
                            "is not one the election survives at")

        # THE FLOAT GIVEN A VALUE, which is the only thing here that can show a
        # sample INVENTING a request rather than missing one. (#400)
        stage_float(failures, extra)

    if args.stage in ("order", "all"):
        log.info("=== byte and word order through the 32-bit path (#206) ===")
        build_order()
        dq, ck, phs, slip = shim(args, ORDER_SHIM)
        extra = [f"+dq_pipe={dq}", f"+ck_pipe={ck}",
                 f"+dq_ph={phs}", f"+rd_slip={slip}"]
        if args.verbose_edges:
            extra.append("+verbose=1")
        rows = report(stage_order(extra + [f"+data_perm={args.data_perm}"]), "order")
        if not rows:
            failures.append("order stage produced no measurement")
        failures += verdict_order(rows)

        # The control. A new instrument's first run is against a known answer:
        # rewiring the data beats to the halves-swapped order -- the transform
        # #206 removed from `bootram` -- must make the checks above fail. If it
        # does not, they were never watching the thing they claim to watch.
        if not args.no_control and args.data_perm == 0:
            log.info("--- negative control: the data path rewired halves-swapped ---")
            rows = report(stage_order(extra + ["+data_perm=2"]), "ctrl")
            caught = verdict_order(rows)
            if caught:
                log.info("  control: %d check(s) fired, so the order tests can "
                         "see a permutation", len(caught))
                for f in dict.fromkeys(caught):
                    log.info("    would fail: %s", f)
            else:
                failures.append("the negative control PASSED: a halves-swapped "
                                "data path was not caught, so the order checks "
                                "prove nothing")

    for f in failures:
        log.error("FAIL %s", f)
    if not args.keep:
        shutil.rmtree(WORKDIR, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
