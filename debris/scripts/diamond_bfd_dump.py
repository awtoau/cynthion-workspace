#!/usr/bin/env python3
"""Dump Diamond's ECP5 bitstream frame database (BFD) to ASCII via bstool.

The BFD is the tile-to-bit mapping that prjtrellis reverse-engineered by
experiment. Diamond ships `bstool`, which reads and writes it in ASCII -- so
the vendor's own version is available directly.

Two things the usage text does not tell you:
  - the real invocation is `-a -b <arch> <in.bfd> <out.asc>`, not the
    `-b <arch> <asc> <bin>` the help implies;
  - bstool wants the input in the working directory, so we copy it in.

Device trees are a trap: `ep5c00` is LatticeECP3 despite the name. The ECP5
tree is `sa5p00` (per data/DiamondDevFile.xml: Family name="ECP5U"
text="sa5p00"). This script defaults to the ECP5 tree and prints the bitstream
status line, which differs per device and is a cheap sanity check.

Output:
  tmp/diamond-mine/bfd/<arch>_bfd.asc
  tmp/diamond-mine/bfd/tile_comparison.json   BFD tiles vs prjtrellis tiles
  tmp/logs/diamond_bfd_dump.log
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

DIAMOND = Path.home() / "lscc/diamond/3.14"
BINDIR = DIAMOND / "bin/lin64"
FPGADIR = DIAMOND / "ispfpga"
FPGABIN = FPGADIR / "bin/lin64"
TRELLIS_TILEDATA = Path.home() / "opt/oss-cad-suite/share/trellis/database/ECP5/tiledata"

ROOT = Path(__file__).resolve().parent.parent
MINE = ROOT / "tmp/diamond-mine"
LOGS = ROOT / "tmp/logs"

# tree -> (arch name bstool wants, description). ECP5U is the Cynthion part.
TREES = {
    "sa5p00": ("ECP5U", "ECP5U -- LFE5U-12F/25F, the Cynthion part"),
    "sa5p00m": ("ECP5UM", "ECP5UM"),
    "sa5p00g": ("ECP5UM5G", "ECP5UM5G"),
}


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


def diamond_env() -> dict[str, str]:
    """Explicit Diamond environment, per bin/lin64/diamond_env.

    Built as a dict rather than by sourcing, so the oss-cad-suite environment
    cannot leak in and shadow Diamond's own libraries.
    """
    return {
        "LSC_DIAMOND": "true",
        "QT_PLUGIN_PATH": "",
        "NEOCAD_MAXLINEWIDTH": "32767",
        "FOUNDRY": str(FPGADIR),
        "TCL_LIBRARY": str(DIAMOND / "tcltk/lib/tcl8.5"),
        "PATH": f"{BINDIR}:{FPGABIN}:/usr/bin:/bin",
        "LD_LIBRARY_PATH": f"{BINDIR}:{FPGABIN}",
        "LM_LICENSE_FILE": str(DIAMOND / "license/license.dat"),
        "HOME": str(Path.home()),
        "DISPLAY": "",
    }


def dump_tree(tree: str, arch: str, desc: str, outdir: Path, log: logging.Logger) -> dict | None:
    src = FPGADIR / tree / "data" / f"{tree}.bfd"
    if not src.exists():
        log.warning("no BFD for tree %s at %s", tree, src)
        return None

    local = outdir / src.name
    shutil.copy2(src, local)
    local.chmod(0o644)
    out = outdir / f"{arch}_bfd.asc"

    log.info("=== %s [%s] %s (%.1f MB)", tree, arch, desc, src.stat().st_size / 1e6)
    p = subprocess.run(
        [str(FPGABIN / "bstool"), "-v", "-a", "-b", arch, local.name, out.name],
        cwd=outdir, env=diamond_env(), capture_output=True, text=True, errors="replace",
    )
    blob = (p.stdout or "") + (p.stderr or "")
    status = re.search(r"Bitstream Status:\s*(.+)", blob)
    if status:
        log.info("    bitstream status: %s", status.group(1).strip())
    if not out.exists():
        log.error("    no output produced; bstool said:\n%s", blob[-1500:])
        local.unlink(missing_ok=True)
        return None

    text = out.read_text(errors="replace")
    tiles = sorted(set(re.findall(r'^Tile "([A-Za-z0-9_]+)"', text, re.M)))
    log.info("    wrote %s (%.1f MB), %d tile types", out.name, out.stat().st_size / 1e6, len(tiles))
    local.unlink(missing_ok=True)
    return {"tree": tree, "arch": arch, "desc": desc,
            "status": status.group(1).strip() if status else None,
            "n_tiles": len(tiles), "tiles": tiles}


def main() -> int:
    log = setup_logging("diamond_bfd_dump")
    outdir = MINE / "bfd"
    outdir.mkdir(parents=True, exist_ok=True)

    results = {}
    for tree, (arch, desc) in TREES.items():
        r = dump_tree(tree, arch, desc, outdir, log)
        if r:
            results[tree] = r

    # The comparison that matters: is prjtrellis missing tiles the vendor has?
    if TRELLIS_TILEDATA.is_dir() and "sa5p00" in results:
        trellis = {p.name for p in TRELLIS_TILEDATA.iterdir() if p.is_dir()}
        bfd = set(results["sa5p00"]["tiles"])
        cmp = {
            "n_bfd": len(bfd),
            "n_trellis": len(trellis),
            "in_both": len(bfd & trellis),
            "bfd_only": sorted(bfd - trellis),
            "trellis_only": sorted(trellis - bfd),
        }
        log.info("=" * 66)
        log.info("ECP5 tile types: vendor BFD %d, prjtrellis %d, in both %d",
                 cmp["n_bfd"], cmp["n_trellis"], cmp["in_both"])
        log.info("  in vendor BFD only (%d): %s", len(cmp["bfd_only"]), cmp["bfd_only"])
        log.info("  in trellis only    (%d): %s", len(cmp["trellis_only"]), cmp["trellis_only"])
        if not cmp["trellis_only"]:
            log.info("  -> prjtrellis is a strict subset; it invented nothing.")
        (outdir / "tile_comparison.json").write_text(json.dumps(cmp, indent=2))

    (outdir / "bfd_dumps.json").write_text(json.dumps(results, indent=2))
    log.info("done -> %s", outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
