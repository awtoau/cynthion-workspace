#!/usr/bin/env python3
#
# Settle #186 in simulation: where a HyperRAM's data phase starts, and whether the
# DQS controller's 4:1 gearing can land on it.
# SPDX-License-Identifier: BSD-3-Clause

"""Three measurements, one runner.

    scripts/hyperram_dqs_model_sim.py                 # probe + controller, open twin
    scripts/hyperram_dqs_model_sim.py --stage probe   # the device alone
    scripts/hyperram_dqs_model_sim.py --stage order   # byte and word order, #206
    scripts/hyperram_dqs_model_sim.py --stage all     # all three
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
ORDER_TB = PROBES / "dqs_order_tb.sv"
CONFIG_TB = PROBES / "dqs_config_tb.sv"
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


def build_config() -> None:
    need("iverilog", "vvp")
    ctl = elaborate_controller()
    run("iverilog(config)", ["iverilog", "-g2012", "-DDUT_MODULE=hyperram_model",
                             "-o", "config.vvp", str(CONFIG_TB), str(OPEN_MODEL),
                             ctl.name], WORKDIR)


def stage_config(extra: list[str]) -> list[str]:
    return run("vvp(config)", ["vvp", "config.vvp"] + extra, WORKDIR).splitlines()


# tCSHI, W956A8 rev A01-006 Table 24. The testbench measures the real gap; this
# is what it is judged against.
T_CSHI_NS = 10.0


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
