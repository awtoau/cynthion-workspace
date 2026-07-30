#!/usr/bin/env python3.15t
"""Inventory Lattice Diamond 3.14 examples/ for ECP5-relevant content.

Classifies every example project by target device family, extracts primitive
instantiations from HDL sources, and collects constraint/preference (.lpf/.prf)
settings and synthesis strategy (.sty) options.

Outputs:
  tmp/diamond-mine/examples_inventory.json
  tmp/diamond-mine/examples_primitives.json
  tmp/diamond-mine/examples_constraints.json
Log: tmp/logs/diamond_examples_scan.log
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

WORKSPACE = Path("/mnt/2tb/git/cynthion-workspace/.claude/worktrees/agent-a2366741da283904f")
EXAMPLES = Path("/home/dan/lscc/diamond/3.14/examples")
SIMLIB = Path("/home/dan/lscc/diamond/3.14/cae_library/simulation/verilog")
OUT = WORKSPACE / "tmp" / "diamond-mine"
LOGDIR = WORKSPACE / "tmp" / "logs"

# Device string -> family. Order matters (longest/most specific first).
FAMILY_PATTERNS = [
    (re.compile(r"LFE5UM5G", re.I), "ecp5um5g"),
    (re.compile(r"LFE5UM", re.I), "ecp5um"),
    (re.compile(r"LFE5U", re.I), "ecp5u"),
    (re.compile(r"\bECP5UM\b", re.I), "ecp5um"),
    (re.compile(r"\bECP5U?\b", re.I), "ecp5u"),
    (re.compile(r"LFE3", re.I), "ecp3"),
    (re.compile(r"LFE2M", re.I), "ecp2m"),
    (re.compile(r"LFE2", re.I), "ecp2"),
    (re.compile(r"LCMXO3D", re.I), "machxo3d"),
    (re.compile(r"LCMXO3L", re.I), "machxo3l"),
    (re.compile(r"LCMXO2", re.I), "machxo2"),
    (re.compile(r"LCMXO", re.I), "machxo"),
    (re.compile(r"LFXP2", re.I), "xp2"),
    (re.compile(r"LFXP", re.I), "xp"),
    (re.compile(r"LFSCM", re.I), "scm"),
    (re.compile(r"LFSC", re.I), "sc"),
    (re.compile(r"LFEC", re.I), "ec"),
]

HDL_EXT = {".v", ".sv", ".vhd", ".vhdl"}
CONSTRAINT_EXT = {".lpf", ".prf", ".sdc", ".fdc", ".ldc"}

# Verilog instantiation: TYPE [#(...)] instname ( ... )
V_INST = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+"          # module type
    r"(?:#\s*\([^;]*?\)\s*)?"                    # optional param map
    r"([A-Za-z_\\][A-Za-z0-9_$\[\].\\]*)\s*"     # instance name
    r"\(",
    re.M,
)
# VHDL component instantiation with entity work/lib binding
VHD_INST = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*:\s*(?:entity\s+)?(?:\w+\.)?([A-Za-z_]\w*)",
    re.M | re.I,
)

VERILOG_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "assign",
    "always", "initial", "begin", "end", "if", "else", "case", "endcase",
    "for", "while", "parameter", "localparam", "defparam", "function",
    "endfunction", "task", "endtask", "generate", "endgenerate", "posedge",
    "negedge", "integer", "genvar", "signed", "unsigned", "and", "or", "not",
    "nand", "nor", "xor", "xnor", "buf", "bufif0", "bufif1", "notif0",
    "notif1", "supply0", "supply1", "tri", "wand", "wor", "real", "time",
    "specify", "endspecify", "primitive", "endprimitive", "table", "endtable",
    "default", "return", "automatic", "static", "logic", "bit", "byte",
    "typedef", "struct", "union", "enum", "package", "endpackage", "import",
    "export", "class", "endclass", "interface", "endinterface", "modport",
    "include", "define", "ifdef", "ifndef", "endif", "timescale", "celldefine",
    "endcelldefine", "attribute", "synthesis", "translate_off", "translate_on",
}


def setup_logging() -> logging.Logger:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("examples_scan")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOGDIR / "diamond_examples_scan.log", mode="w")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


def known_primitives() -> dict[str, set[str]]:
    """Map family dir -> set of primitive names from the Diamond sim library."""
    out: dict[str, set[str]] = {}
    for d in sorted(SIMLIB.iterdir()):
        if not d.is_dir():
            continue
        out[d.name] = {p.stem.upper() for p in d.glob("*.v")}
    return out


def classify_device(text: str) -> set[str]:
    fams = set()
    for pat, fam in FAMILY_PATTERNS:
        if pat.search(text):
            fams.add(fam)
    return fams


def strip_comments(src: str, vhdl: bool) -> str:
    if vhdl:
        return re.sub(r"--[^\n]*", "", src)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def scan_hdl(path: Path, prim_universe: set[str]) -> list[dict]:
    try:
        src = path.read_text(errors="replace")
    except OSError:
        return []
    vhdl = path.suffix.lower() in {".vhd", ".vhdl"}
    body = strip_comments(src, vhdl)
    found: list[dict] = []
    if vhdl:
        for m in VHD_INST.finditer(body):
            typ = m.group(2).upper()
            if typ in prim_universe:
                found.append({"type": typ, "inst": m.group(1), "lang": "vhdl"})
    else:
        # locally defined modules must not count as primitives
        local = {m.upper() for m in re.findall(r"^\s*module\s+(\w+)", body, re.M)}
        for m in V_INST.finditer(body):
            typ = m.group(1)
            if typ.lower() in VERILOG_KEYWORDS:
                continue
            tu = typ.upper()
            if tu in local or tu not in prim_universe:
                continue
            # capture the parameter map if present
            seg = body[m.start(): m.start() + 4000]
            pm = re.search(r"#\s*\((.*?)\)\s*[A-Za-z_\\]", seg, re.S)
            params = {}
            if pm:
                for pk, pv in re.findall(r"\.\s*(\w+)\s*\(\s*([^),]*)\)", pm.group(1)):
                    params[pk] = pv.strip()
            found.append({"type": tu, "inst": m.group(2), "lang": "verilog",
                          "params": params})
    # defparam settings
    for tgt, val in re.findall(r"defparam\s+([\w.\\\[\]]+)\s*=\s*([^;]+);", body):
        found.append({"defparam": tgt.strip(), "value": val.strip(),
                      "lang": "vhdl" if vhdl else "verilog"})
    return found


def scan_constraints(path: Path) -> list[str]:
    try:
        txt = path.read_text(errors="replace")
    except OSError:
        return []
    lines = []
    for ln in txt.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        lines.append(s)
    return lines


def main() -> int:
    log = setup_logging()
    OUT.mkdir(parents=True, exist_ok=True)

    prims = known_primitives()
    prim_universe: set[str] = set()
    for s in prims.values():
        prim_universe |= s
    log.info("Diamond sim-library families: %d, total distinct primitives: %d",
             len(prims), len(prim_universe))
    log.info("ecp5u primitives: %d", len(prims.get("ecp5u", set())))

    inventory = []
    all_prims: dict[str, Counter] = defaultdict(Counter)
    prim_details: dict[str, list] = defaultdict(list)
    constraints: dict[str, dict] = {}

    for proj in sorted(EXAMPLES.iterdir()):
        if not proj.is_dir():
            continue
        files = [p for p in proj.rglob("*") if p.is_file()]
        devtext = []
        for p in files:
            if p.suffix.lower() in {".ldf", ".sty", ".xcf", ".lpf", ".prf", ".syn"}:
                try:
                    devtext.append(p.read_text(errors="replace"))
                except OSError:
                    pass
        fams = classify_device("\n".join(devtext))
        # also the directory name is a hint
        fams |= classify_device(proj.name)
        entry = {
            "project": proj.name,
            "families": sorted(fams),
            "is_ecp5": any(f.startswith("ecp5") for f in fams),
            "file_count": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "hdl_files": [],
            "constraint_files": [],
            "strategy_files": [],
        }
        for p in files:
            ext = p.suffix.lower()
            rel = str(p.relative_to(EXAMPLES))
            if ext in HDL_EXT:
                entry["hdl_files"].append(rel)
                hits = scan_hdl(p, prim_universe)
                for h in hits:
                    if "type" in h:
                        all_prims[proj.name][h["type"]] += 1
                        prim_details[h["type"]].append({"project": proj.name,
                                                        "file": rel, **h})
            elif ext in CONSTRAINT_EXT:
                entry["constraint_files"].append(rel)
                constraints.setdefault(proj.name, {})[rel] = scan_constraints(p)
            elif ext == ".sty":
                entry["strategy_files"].append(rel)
        inventory.append(entry)
        log.info("%-28s fams=%-16s hdl=%3d constr=%2d prims=%d",
                 proj.name, ",".join(sorted(fams)) or "-",
                 len(entry["hdl_files"]), len(entry["constraint_files"]),
                 len(all_prims[proj.name]))

    (OUT / "examples_inventory.json").write_text(json.dumps(inventory, indent=2))
    (OUT / "examples_primitives.json").write_text(json.dumps(
        {"per_project": {k: dict(v) for k, v in all_prims.items()},
         "details": prim_details}, indent=2))
    (OUT / "examples_constraints.json").write_text(json.dumps(constraints, indent=2))

    ecp5_projects = [e["project"] for e in inventory if e["is_ecp5"]]
    log.info("ECP5-targeting example projects: %s", ecp5_projects or "NONE")
    total = Counter()
    for c in all_prims.values():
        total.update(c)
    log.info("Primitives instantiated across ALL examples (%d distinct):", len(total))
    for name, n in total.most_common():
        fams = sorted(f for f, s in prims.items() if name in s)
        in_ecp5 = "ecp5u" in fams
        log.info("  %-20s x%-4d ecp5u=%s", name, n, in_ecp5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
