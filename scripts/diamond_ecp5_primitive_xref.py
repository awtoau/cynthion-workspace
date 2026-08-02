#!/usr/bin/env python3
"""Cross-reference Diamond's authoritative ECP5 primitive list against the open flow.

The ECP5 primitive list comes from webhelp `Reference Guides/FPGA Libraries/
ecp5u_um.htm`, which is device-family-specific -- unlike cae_library/simulation/
verilog/ecp5u/, which carries models Diamond's own mapper will reject. So this
list is the better statement of "what the ECP5 actually has".

Compares against:
  - yosys ECP5 blackbox cells (cells_bb.v)
  - nextpnr-ecp5 recognised cell type strings
  - prjtrellis ECP5 database

Output:
  tmp/diamond-mine/ecp5_primitive_xref.json
  tmp/logs/diamond_ecp5_primitive_xref.log
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINE = ROOT / "tmp/diamond-mine"
LOGS = ROOT / "tmp/logs"

YOSYS_BB_CANDIDATES = [
    Path.home() / "opt/oss-cad-suite/share/yosys/ecp5/cells_bb.v",
    Path.home() / ".local/share/yosys/ecp5/cells_bb.v",
    Path("/usr/share/yosys/ecp5/cells_bb.v"),
]
NEXTPNR = Path.home() / ".local/bin/nextpnr-ecp5"
TRELLIS_DB = Path.home() / "opt/oss-cad-suite/share/trellis/database/ECP5"


def setup_logging(name: str) -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOGS / f"{name}.log", mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


# Soft macros: logic yosys builds from LUTs by inference/techmap. Their absence
# from cells_bb.v is expected and is not a capability gap.
SOFT_PATTERNS = [
    r"^(AND|OR|ND|NR|XOR|XNOR)\d+$",      # gate primitives
    r"^INV$", r"^V(HI|LO)$",
    r"^(FD|FL)\d[PS]3[A-Z]{1,2}$",        # generic flip-flops
    r"^(IFS|OFS)1[PS][13][A-Z]{1,2}$",    # PIC flip-flops / latches
    r"^ROM\d+X1A$",
    r"^MUX(21|41|81|161|321)$", r"^L6MUX21$", r"^PFUMX$",
    r"^LUT[4-8]$", r"^CCU2[A-D]?$",
    r"^(SPR|DPR)16X4C$",
]


def is_soft(name: str) -> bool:
    return any(re.fullmatch(p, name) or re.match(p, name) for p in SOFT_PATTERNS)


def diamond_primitives(log: logging.Logger) -> dict[str, str]:
    """Parse primitive name -> description from the extracted ecp5u_um page tables."""
    src = MINE / "webhelp_pages.jsonl"
    prims: dict[str, str] = {}
    with src.open() as fh:
        for line in fh:
            rec = json.loads(line)
            if not rec["path"].endswith("ecp5u_um.htm"):
                continue
            for tbl in rec["tables"]:
                for row in tbl:
                    if len(row) >= 2 and re.fullmatch(r"[A-Z][A-Z0-9_]{1,20}", row[0]):
                        prims[row[0]] = row[1]
    log.info("Diamond ECP5 primitives (ecp5u_um.htm): %d", len(prims))
    return prims


def yosys_cells(log: logging.Logger) -> set[str]:
    for p in YOSYS_BB_CANDIDATES:
        if p.exists():
            txt = p.read_text(errors="replace")
            cells = set(re.findall(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_]*)", txt, re.M))
            log.info("yosys cells_bb.v %s: %d cells", p, len(cells))
            return cells
    log.warning("no yosys cells_bb.v found in %s", YOSYS_BB_CANDIDATES)
    return set()


def nextpnr_strings(log: logging.Logger) -> set[str]:
    """Uppercase tokens in the nextpnr-ecp5 binary.

    Note the binary on PATH may be a wrapper; the real ELF lives in libexec.
    A stripped or wrapped binary yields almost nothing, which would silently
    make every primitive look unsupported -- so warn loudly if the token count
    is implausibly low rather than reporting a bogus gap.
    """
    for cand in (Path.home() / "opt/oss-cad-suite/libexec/nextpnr-ecp5", NEXTPNR):
        if not cand.exists():
            continue
        p = subprocess.run(["strings", str(cand)], capture_output=True, text=True, errors="replace")
        toks = set(re.findall(r"\b[A-Z][A-Z0-9_]{2,20}\b", p.stdout))
        log.info("nextpnr-ecp5 %s: %d uppercase tokens", cand, len(toks))
        if len(toks) > 100:
            return toks
        log.warning("  implausibly few tokens from %s (wrapper or stripped?), trying next", cand)
    log.warning("no usable nextpnr-ecp5 binary found; nextpnr column is unreliable")
    return set()


def trellis_tokens(log: logging.Logger) -> set[str]:
    toks: set[str] = set()
    if not TRELLIS_DB.is_dir():
        log.warning("trellis db not found at %s", TRELLIS_DB)
        return toks
    for p in TRELLIS_DB.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".txt", ".db"):
            try:
                toks |= set(re.findall(r"\b[A-Z][A-Z0-9_]{2,20}\b", p.read_text(errors="replace")))
            except OSError:
                pass
    log.info("prjtrellis ECP5 db tokens: %d", len(toks))
    return toks


def main() -> int:
    log = setup_logging("diamond_ecp5_primitive_xref")
    MINE.mkdir(parents=True, exist_ok=True)

    prims = diamond_primitives(log)
    ycells = yosys_cells(log)
    npnr = nextpnr_strings(log)
    tdb = trellis_tokens(log)

    rows = []
    for name, desc in sorted(prims.items()):
        rows.append({
            "primitive": name,
            "description": desc,
            "soft": is_soft(name),
            "in_yosys_bb": name in ycells,
            "in_nextpnr": name in npnr,
            "in_trellis_db": name in tdb,
        })

    # Only hard primitives are meaningful gaps. Soft macros (gates, generic
    # flip-flops, ROMs, muxes) are produced by yosys techmap/inference and
    # never need a blackbox declaration, so counting them as "missing"
    # massively overstates the gap.
    missing_yosys = [r for r in rows if not r["in_yosys_bb"] and not r["soft"]]
    missing_all = [r for r in rows
                   if not (r["in_yosys_bb"] or r["in_nextpnr"] or r["in_trellis_db"])
                   and not r["soft"]]

    log.info("=" * 70)
    log.info("Diamond ECP5 primitives NOT in yosys cells_bb.v: %d", len(missing_yosys))
    for r in missing_yosys:
        log.info("  %-14s npnr=%-5s trellis=%-5s  %s",
                 r["primitive"], r["in_nextpnr"], r["in_trellis_db"], r["description"][:70])
    log.info("=" * 70)
    log.info("Diamond ECP5 primitives in NO open-flow component: %d", len(missing_all))
    for r in missing_all:
        log.info("  %-14s %s", r["primitive"], r["description"][:80])

    out = MINE / "ecp5_primitive_xref.json"
    out.write_text(json.dumps({
        "n_diamond_primitives": len(rows),
        "n_missing_yosys": len(missing_yosys),
        "n_missing_all": len(missing_all),
        "rows": rows,
    }, indent=2))
    log.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
