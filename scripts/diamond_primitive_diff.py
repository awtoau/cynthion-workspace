#!/usr/bin/env python3.15t
"""Diff Lattice Diamond 3.14 ECP5 primitive knowledge against the open flow.

Sources compared:
  Diamond simulation models : cae_library/simulation/verilog/{ecp5u}/*.v
  Diamond synthesis models  : cae_library/synthesis/verilog/{ecp5u,ecp5um,ecp5um5g}.v
  yosys blackbox cells      : <yosys share>/ecp5/cells_bb.v (+ cells_sim.v)
  nextpnr-ecp5              : strings on the real binary

Produces:
  tmp/diamond-mine/primitive_diff.json   full structured comparison
  tmp/diamond-mine/primitive_diff.txt    human-readable tables
Log: tmp/logs/diamond_primitive_diff.log
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
DIAMOND = Path.home() / "lscc" / "diamond" / "3.14"
SIM = DIAMOND / "cae_library/simulation/verilog"
SYN = DIAMOND / "cae_library/synthesis/verilog"
YOSYS_ECP5 = Path.home() / "opt/oss-cad-suite/share/yosys/ecp5"
NEXTPNR_BIN = Path.home() / "opt/oss-cad-suite/libexec/nextpnr-ecp5"
OUT = WORKSPACE / "tmp" / "diamond-mine"
LOGDIR = WORKSPACE / "tmp" / "logs"

MODULE_RE = re.compile(r"^\s*module\s+(\\?[\w$]+)\s*(?:#\s*\((.*?)\))?\s*\((.*?)\)\s*;",
                       re.S | re.M)
PARAM_RE = re.compile(
    r"\bparameter\s+(?:signed\s+|integer\s+|real\s+)?"
    r"(?:\[[^\]]*\]\s*)?(\w+)\s*=\s*([^;,\n]+)")
PORT_DECL_RE = re.compile(r"\b(input|output|inout)\b((?:\s*(?:wire|reg|signed|\[[^\]]*\]))*)\s*([\w\s,$]+)")


def setup_logging() -> logging.Logger:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("prim_diff")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOGDIR / "diamond_primitive_diff.log", mode="w")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def parse_verilog_modules(src: str) -> dict[str, dict]:
    """Extract module name -> {ports, params, param_values}."""
    src = strip_comments(src)
    mods: dict[str, dict] = {}
    # split on module ... endmodule
    for m in re.finditer(r"\bmodule\s+(\\?[\w$]+)(.*?)\bendmodule\b", src, re.S):
        name = m.group(1).lstrip("\\").upper()
        body = m.group(2)
        # header = up to the first ';'
        semi = body.find(";")
        header = body[:semi] if semi >= 0 else body
        rest = body[semi + 1:] if semi >= 0 else ""
        ports: set[str] = set()
        # ANSI ports in header
        for kw, _mid, names in PORT_DECL_RE.findall(header):
            for n in re.split(r"[,\s]+", names.strip()):
                if n and not re.match(r"^(wire|reg|signed|input|output|inout)$", n):
                    ports.add(n.upper())
        # non-ANSI: port list names in header parens, decls in body
        pl = re.search(r"\((.*)\)", header, re.S)
        if pl and not ports:
            for n in re.split(r"[,\s]+", pl.group(1)):
                n = n.strip().lstrip("\\")
                if n and n.isidentifier():
                    ports.add(n.upper())
        for kw, _mid, names in PORT_DECL_RE.findall(rest):
            for n in re.split(r"[,\s]+", names.strip()):
                if n and n.isidentifier() and not re.match(
                        r"^(wire|reg|signed|input|output|inout)$", n):
                    ports.add(n.upper())
        params: dict[str, str] = {}
        for pn, pv in PARAM_RE.findall(header + ";" + rest):
            params.setdefault(pn.upper(), pv.strip())
        mods[name] = {"ports": sorted(ports), "params": params}
    return mods


def legal_values_from_source(src: str, modname: str, param: str) -> list[str]:
    """Mine string comparisons against a parameter for its legal value set."""
    vals = set()
    # e.g. (PARAM == "VALUE")  or  case (PARAM) "VALUE":
    for m in re.finditer(re.escape(param) + r"\s*===?\s*\"([^\"]*)\"", src, re.I):
        vals.add(m.group(1))
    for m in re.finditer(r"\"([^\"]*)\"\s*===?\s*" + re.escape(param), src, re.I):
        vals.add(m.group(1))
    return sorted(vals)


def collect_diamond_family(famdir: Path) -> dict[str, dict]:
    """Per-file primitives from a simulation family directory."""
    out: dict[str, dict] = {}
    for f in sorted(famdir.glob("*.v")):
        src = f.read_text(errors="replace")
        mods = parse_verilog_modules(src)
        want = f.stem.upper()
        info = mods.get(want)
        if info is None and mods:
            # take the module whose name matches most closely / the last one
            info = mods.get(next(iter(mods)))
        if info is None:
            continue
        rec = dict(info)
        rec["file"] = str(f)
        rec["legal_values"] = {}
        for p in rec["params"]:
            lv = legal_values_from_source(src, want, p)
            if lv:
                rec["legal_values"][p] = lv
        out[want] = rec
    return out


def collect_syn_file(path: Path) -> dict[str, dict]:
    return parse_verilog_modules(path.read_text(errors="replace"))


def nextpnr_strings() -> set[str]:
    try:
        r = subprocess.run(["strings", "-n", "3", str(NEXTPNR_BIN)],
                           capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {s.strip().upper() for s in r.stdout.splitlines() if s.strip()}


def main() -> int:
    log = setup_logging()
    OUT.mkdir(parents=True, exist_ok=True)

    # --- Diamond: which ECP5 family dirs exist? ---
    sim_fams = sorted(d.name for d in SIM.iterdir() if d.is_dir())
    syn_files = sorted(p.name for p in SYN.glob("*.v"))
    ecp5_sim = [f for f in sim_fams if f.startswith("ecp5")]
    ecp5_syn = [f for f in syn_files if f.startswith("ecp5")]
    log.info("sim families: %s", sim_fams)
    log.info("ECP5 sim dirs: %s", ecp5_sim)
    log.info("ECP5 synthesis model files: %s", ecp5_syn)

    diamond = collect_diamond_family(SIM / "ecp5u")
    log.info("Diamond ecp5u simulation primitives: %d", len(diamond))

    syn_models: dict[str, dict[str, dict]] = {}
    for name in ecp5_syn:
        syn_models[name] = collect_syn_file(SYN / name)
        log.info("synthesis/%s: %d modules", name, len(syn_models[name]))

    # --- yosys ---
    yos: dict[str, dict] = {}
    for fn in ("cells_bb.v", "cells_sim.v"):
        p = YOSYS_ECP5 / fn
        if p.exists():
            got = parse_verilog_modules(p.read_text(errors="replace"))
            log.info("yosys %s: %d modules", fn, len(got))
            for k, v in got.items():
                if k in yos:
                    yos[k]["ports"] = sorted(set(yos[k]["ports"]) | set(v["ports"]))
                    yos[k]["params"].update(v["params"])
                else:
                    yos[k] = v
    log.info("yosys ECP5 cells total: %d", len(yos))

    npnr = nextpnr_strings()
    log.info("nextpnr-ecp5 strings: %d", len(npnr))

    # --- diff ---
    d_names = set(diamond)
    y_names = set(yos)

    only_diamond = sorted(d_names - y_names)
    only_yosys = sorted(y_names - d_names)
    both = sorted(d_names & y_names)

    # of Diamond-only, which does nextpnr at least mention?
    npnr_known = [n for n in only_diamond if n in npnr]
    npnr_unknown = [n for n in only_diamond if n not in npnr]

    richer = []
    for n in both:
        dp = set(diamond[n]["params"])
        yp = set(yos[n]["params"])
        extra_params = sorted(dp - yp)
        missing_params = sorted(yp - dp)
        dports = set(diamond[n]["ports"])
        yports = set(yos[n]["ports"])
        extra_ports = sorted(dports - yports)
        missing_ports = sorted(yports - dports)
        if extra_params or extra_ports:
            richer.append({
                "cell": n,
                "diamond_extra_params": extra_params,
                "diamond_extra_param_defaults": {
                    p: diamond[n]["params"][p] for p in extra_params},
                "legal_values": {p: diamond[n]["legal_values"][p]
                                 for p in extra_params
                                 if p in diamond[n]["legal_values"]},
                "diamond_extra_ports": extra_ports,
                "yosys_only_params": missing_params,
                "yosys_only_ports": missing_ports,
            })
    richer.sort(key=lambda r: -(len(r["diamond_extra_params"]) +
                                len(r["diamond_extra_ports"])))

    # legal values for params that yosys DOES have but with no documented range
    shared_legal = {}
    for n in both:
        lv = {p: v for p, v in diamond[n]["legal_values"].items()
              if p in yos[n]["params"] and len(v) > 1}
        if lv:
            shared_legal[n] = lv

    # synthesis vs simulation model differences (ecp5u.v vs ecp5u/ dir)
    syn_ecp5u = syn_models.get("ecp5u.v", {})
    syn_only = sorted(set(syn_ecp5u) - d_names)
    sim_only = sorted(d_names - set(syn_ecp5u))
    syn_vs_sim_params = []
    for n in sorted(set(syn_ecp5u) & d_names):
        a = set(syn_ecp5u[n]["params"])
        b = set(diamond[n]["params"])
        if a - b or b - a:
            syn_vs_sim_params.append({
                "cell": n,
                "syn_only_params": sorted(a - b),
                "sim_only_params": sorted(b - a),
            })

    # ecp5um / ecp5um5g deltas (SERDES-bearing parts)
    um = syn_models.get("ecp5um.v", {})
    um5g = syn_models.get("ecp5um5g.v", {})
    um_only = sorted(set(um) - set(syn_ecp5u))
    um5g_only = sorted(set(um5g) - set(um))

    result = {
        "diamond_sim_families": sim_fams,
        "ecp5_sim_dirs": ecp5_sim,
        "ecp5_syn_files": ecp5_syn,
        "counts": {
            "diamond_ecp5u_sim": len(diamond),
            "diamond_ecp5u_syn": len(syn_ecp5u),
            "diamond_ecp5um_syn": len(um),
            "diamond_ecp5um5g_syn": len(um5g),
            "yosys_ecp5_cells": len(yos),
            "in_both": len(both),
            "diamond_only": len(only_diamond),
            "yosys_only": len(only_yosys),
        },
        "diamond_only_nextpnr_knows": npnr_known,
        "diamond_only_nextpnr_unknown": npnr_unknown,
        "yosys_only": only_yosys,
        "diamond_richer": richer,
        "legal_values_for_shared_params": shared_legal,
        "syn_model_only_cells": syn_only,
        "sim_model_only_cells": sim_only,
        "syn_vs_sim_param_deltas": syn_vs_sim_params,
        "ecp5um_only_cells": um_only,
        "ecp5um5g_only_cells": um5g_only,
        "diamond_ecp5u_full": diamond,
    }
    (OUT / "primitive_diff.json").write_text(json.dumps(result, indent=2))

    # readable report
    lines: list[str] = []
    def w(s=""):
        lines.append(s)
        log.info(s)

    w("=" * 78)
    w("DIAMOND ECP5 PRIMITIVES vs OPEN FLOW")
    w("=" * 78)
    w(f"Diamond ecp5u sim models : {len(diamond)}")
    w(f"Diamond ecp5u syn model  : {len(syn_ecp5u)}")
    w(f"Diamond ecp5um syn model : {len(um)}   (+{len(um_only)} over ecp5u)")
    w(f"Diamond ecp5um5g syn     : {len(um5g)} (+{len(um5g_only)} over ecp5um)")
    w(f"yosys ECP5 cells         : {len(yos)}")
    w(f"in both                  : {len(both)}")
    w("")
    w(f"--- Diamond-only, nextpnr HAS the string ({len(npnr_known)}) ---")
    w("  " + ", ".join(npnr_known))
    w("")
    w(f"--- Diamond-only, nextpnr does NOT mention ({len(npnr_unknown)}) ---")
    for n in npnr_unknown:
        p = diamond[n]
        w(f"  {n:<22} ports={len(p['ports']):<3} params={list(p['params'])}")
    w("")
    w(f"--- yosys-only cells ({len(only_yosys)}) ---")
    w("  " + ", ".join(only_yosys))
    w("")
    w(f"--- Diamond param/port set RICHER than yosys ({len(richer)}) ---")
    for r in richer:
        w(f"  {r['cell']}")
        if r["diamond_extra_params"]:
            for p in r["diamond_extra_params"]:
                dv = r["diamond_extra_param_defaults"][p]
                lv = r["legal_values"].get(p)
                extra = f"  legal={lv}" if lv else ""
                w(f"      +param {p} = {dv}{extra}")
        if r["diamond_extra_ports"]:
            w(f"      +ports {r['diamond_extra_ports']}")
        if r["yosys_only_params"]:
            w(f"      (yosys-only params: {r['yosys_only_params']})")
        if r["yosys_only_ports"]:
            w(f"      (yosys-only ports: {r['yosys_only_ports']})")
    w("")
    w(f"--- Legal values Diamond documents for params yosys also has ---")
    for n, lv in sorted(shared_legal.items()):
        w(f"  {n}")
        for p, v in sorted(lv.items()):
            w(f"      {p}: {v}")
    w("")
    w(f"--- synthesis model vs simulation model ---")
    w(f"  cells only in synthesis/ecp5u.v ({len(syn_only)}): {syn_only}")
    w(f"  cells only in simulation/ecp5u/ ({len(sim_only)}): {sim_only}")
    for d in syn_vs_sim_params:
        w(f"  {d['cell']}: syn-only={d['syn_only_params']} sim-only={d['sim_only_params']}")
    w("")
    w(f"--- ecp5um adds over ecp5u ({len(um_only)}): {um_only}")
    w(f"--- ecp5um5g adds over ecp5um ({len(um5g_only)}): {um5g_only}")

    (OUT / "primitive_diff.txt").write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
