#!/usr/bin/env python3.15t
"""Cross-reference Diamond ECP5 primitive parameters against the prjtrellis
bitstream database and the nextpnr-ecp5 binary.

Three-way classification for every Diamond ecp5u primitive parameter:
  YOSYS      - yosys cells_bb.v declares the parameter (open flow full path)
  TRELLIS    - not in yosys, but the trellis tiledata has a matching config
               enum/word, so the bit exists and is documented
  NEITHER    - Diamond knows it, the open flow has no representation at all

Also lists trellis config enums that have NO Diamond ECP5 primitive parameter
(open flow knows something Diamond's models do not expose).

Outputs: tmp/diamond-mine/trellis_gap.json / .txt
Log:     tmp/logs/diamond_trellis_gap.log
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

WORKSPACE = Path("/mnt/2tb/git/cynthion-workspace/.claude/worktrees/agent-a2366741da283904f")
TILEDATA = Path("/home/dan/opt/oss-cad-suite/share/trellis/database/ECP5/tiledata")
YOSYS_BB = Path("/home/dan/opt/oss-cad-suite/share/yosys/ecp5/cells_bb.v")
NEXTPNR_BIN = Path("/home/dan/opt/oss-cad-suite/libexec/nextpnr-ecp5")
DIFF_JSON = WORKSPACE / "tmp" / "diamond-mine" / "primitive_diff.json"
OUT = WORKSPACE / "tmp" / "diamond-mine"
LOGDIR = WORKSPACE / "tmp" / "logs"


def setup_logging() -> logging.Logger:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("trellis_gap")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOGDIR / "diamond_trellis_gap.log", mode="w")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


def load_trellis_enums(log: logging.Logger) -> dict[str, dict[str, set[str]]]:
    """tile-type -> {config setting name -> set of legal option strings}.

    prjtrellis stores tile bit definitions in plain-text bits.db files:
        .config_enum <SETTING>
        <OPTION> <bitspec...>
        .config <SETTING> <default-bits>      (a word, not an enum)
    """
    enums: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    files = sorted(TILEDATA.rglob("bits.db"))
    log.info("trellis tiledata bits.db files: %d", len(files))
    for f in files:
        tt = f.parent.name
        cur: str | None = None
        is_enum = False
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("."):
                parts = line.split()
                directive = parts[0]
                if directive == ".config_enum" and len(parts) > 1:
                    cur, is_enum = parts[1], True
                    enums[tt].setdefault(cur, set())
                elif directive == ".config" and len(parts) > 1:
                    cur, is_enum = parts[1], False
                    enums[tt].setdefault(cur, set()).add("<word>")
                else:  # .mux and anything else: not a config setting
                    cur, is_enum = None, False
                continue
            if cur and is_enum and not line[0].isspace():
                enums[tt][cur].add(line.split()[0])
    return {k: dict(v) for k, v in enums.items()}


def yosys_params() -> dict[str, set[str]]:
    src = YOSYS_BB.read_text(errors="replace")
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"\bmodule\s+(\w+)(.*?)\bendmodule\b", src, re.S):
        out[m.group(1).upper()] = {
            p.upper() for p in re.findall(r"\bparameter\s+(?:\[[^\]]*\]\s*)?(\w+)",
                                          m.group(2))}
    return out


def nextpnr_str() -> set[str]:
    r = subprocess.run(["strings", "-n", "3", str(NEXTPNR_BIN)],
                       capture_output=True, text=True, check=True)
    return {s.strip() for s in r.stdout.splitlines()}


def main() -> int:
    log = setup_logging()
    OUT.mkdir(parents=True, exist_ok=True)

    diff = json.loads(DIFF_JSON.read_text())
    diamond = diff["diamond_ecp5u_full"]
    yos = yosys_params()
    npnr = nextpnr_str()
    enums = load_trellis_enums(log)

    # flatten all trellis setting names, plus per-tile provenance
    trellis_names: dict[str, set[str]] = defaultdict(set)   # setting -> tiles
    trellis_opts: dict[str, set[str]] = defaultdict(set)    # setting -> options
    for tt, settings in enums.items():
        for nm, opts in settings.items():
            trellis_names[nm].add(tt)
            trellis_opts[nm] |= opts
    log.info("trellis distinct config settings: %d across %d tile types",
             len(trellis_names), len(enums))

    # Config settings are named like "CELLTYPE.PARAM" or "BEL.PARAM"
    suffix_index: dict[str, set[str]] = defaultdict(set)
    for nm in trellis_names:
        parts = nm.split(".")
        suffix_index[parts[-1].upper()].add(nm)

    rows = []
    for cell, info in sorted(diamond.items()):
        yp = yos.get(cell.upper(), set())
        for param, default in sorted(info["params"].items()):
            in_yosys = param in yp
            tmatch = sorted(suffix_index.get(param, set()))
            # prefer a match qualified by the cell name
            cellq = [t for t in tmatch if cell.upper() in t.upper()]
            state = ("YOSYS" if in_yosys
                     else "TRELLIS" if tmatch
                     else "NEITHER")
            rows.append({
                "cell": cell,
                "param": param,
                "diamond_default": default,
                "diamond_legal": info["legal_values"].get(param, []),
                "state": state,
                "trellis_settings": cellq or tmatch,
                "trellis_options": sorted(
                    set().union(*[trellis_opts[t] for t in (cellq or tmatch)])
                ) if tmatch else [],
                "in_nextpnr_strings": param in npnr,
                "cell_in_yosys": cell.upper() in yos,
            })

    # trellis settings with no Diamond primitive parameter counterpart
    diamond_params = {p for i in diamond.values() for p in i["params"]}
    orphan = sorted(nm for nm in trellis_names
                    if nm.split(".")[-1].upper() not in diamond_params)

    by_state = defaultdict(list)
    for r in rows:
        by_state[r["state"]].append(r)

    result = {
        "summary": {k: len(v) for k, v in by_state.items()},
        "rows": rows,
        "trellis_settings_without_diamond_param": orphan,
        "trellis_setting_count": len(trellis_names),
        "tile_types": sorted(enums),
    }
    (OUT / "trellis_gap.json").write_text(json.dumps(result, indent=2))

    lines: list[str] = []
    def w(s=""):
        lines.append(s)
        log.info(s)

    w("=" * 78)
    w("DIAMOND ECP5 PARAM -> OPEN FLOW REPRESENTABILITY")
    w("=" * 78)
    w(f"summary: {dict(result['summary'])}")
    w("")
    w("--- TRELLIS knows the bits but YOSYS does not declare the param ---")
    for r in by_state["TRELLIS"]:
        w(f"  {r['cell']}.{r['param']:<22} default={r['diamond_default']:<16} "
          f"trellis={r['trellis_settings'][:3]}")
        if r["diamond_legal"]:
            w(f"        Diamond legal: {r['diamond_legal']}")
        if r["trellis_options"] and r["trellis_options"] != ["<word>"]:
            w(f"        trellis opts : {r['trellis_options'][:12]}")
    w("")
    w("--- NEITHER yosys nor trellis (cell is in yosys => real gap) ---")
    for r in by_state["NEITHER"]:
        if not r["cell_in_yosys"]:
            continue
        w(f"  {r['cell']}.{r['param']:<22} default={r['diamond_default']:<16} "
          f"legal={r['diamond_legal']} nextpnr_string={r['in_nextpnr_strings']}")
    w("")
    w("--- NEITHER, and the cell itself is unknown to yosys ---")
    unk = defaultdict(list)
    for r in by_state["NEITHER"]:
        if not r["cell_in_yosys"]:
            unk[r["cell"]].append(r["param"])
    for c, ps in sorted(unk.items()):
        w(f"  {c:<20} {ps}")
    w("")
    w(f"--- trellis settings with no Diamond primitive param ({len(orphan)}) ---")
    for nm in orphan[:120]:
        w(f"  {nm}")
    if len(orphan) > 120:
        w(f"  ... and {len(orphan)-120} more (see JSON)")

    (OUT / "trellis_gap.txt").write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
