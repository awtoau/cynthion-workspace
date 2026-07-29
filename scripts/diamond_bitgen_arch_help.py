#!/usr/bin/env python3
"""Ask Diamond's own bitgen for its per-architecture option list.

The webhelp prose tags each bitgen option with the architectures it applies to,
but the authoritative answer is `bitgen -h <architecture>`, which the tool
generates from its internal tables. This runs it for every architecture bitgen
admits to knowing, so ECP5 options can be separated from other families without
relying on prose.

Diamond needs its own environment (FOUNDRY, LD_LIBRARY_PATH, LM_LICENSE_FILE)
per bin/lin64/diamond_env. We build that explicitly rather than sourcing it, so
the oss-cad-suite environment cannot leak in.

Output:
  tmp/diamond-mine/bitgen_arch_help/<arch>.txt
  tmp/diamond-mine/bitgen_arch_options.json
  tmp/logs/diamond_bitgen_arch_help.log
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

DIAMOND = Path.home() / "lscc/diamond/3.14"
BINDIR = DIAMOND / "bin/lin64"
FPGADIR = DIAMOND / "ispfpga"
FPGABIN = FPGADIR / "bin/lin64"
ROOT = Path(__file__).resolve().parent.parent
MINE = ROOT / "tmp/diamond-mine"
LOGS = ROOT / "tmp/logs"


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
    """Replicate bin/lin64/diamond_env in an explicit dict."""
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
    }


def run(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    try:
        p = subprocess.run(args, env=env, capture_output=True, text=True, errors="replace")
    except OSError as exc:
        return -1, f"<exec failed: {exc}>"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_options(text: str) -> list[str]:
    """Pull -g Option:Value names and bare flags out of bitgen help text."""
    opts = set()
    for m in re.finditer(r"-g\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", text):
        opts.add(m.group(1))
    return sorted(opts)


def main() -> int:
    log = setup_logging("diamond_bitgen_arch_help")
    env = diamond_env()
    outdir = MINE / "bitgen_arch_help"
    outdir.mkdir(parents=True, exist_ok=True)

    bitgen = FPGABIN / "bitgen"
    if not bitgen.exists():
        log.error("bitgen not found at %s", bitgen)
        return 1

    rc, bare = run([str(bitgen), "-h"], env)
    (outdir / "_architectures.txt").write_text(bare)
    log.info("bitgen -h rc=%d, %d chars", rc, len(bare))
    log.info("bare help:\n%s", bare[:4000])

    # Architecture tokens bitgen lists. Grab plausible identifiers from the
    # bare help, plus the ECP5 spellings we care about regardless.
    cands = set(re.findall(r"\b(?:ep5c00a?|ecp5u?m?|ECP5[A-Za-z0-9]*)\b", bare))
    cands |= set(re.findall(r"^\s{2,}([a-z][a-z0-9_]{2,})\s*$", bare, re.M))
    cands |= {"ep5c00", "ep5c00a", "ecp5u", "ecp5um", "ECP5U", "ECP5UM"}
    log.info("architecture candidates: %s", sorted(cands))

    results: dict[str, dict] = {}
    for arch in sorted(cands):
        rc, text = run([str(bitgen), "-h", arch], env)
        opts = parse_options(text)
        (outdir / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', arch)}.txt").write_text(text)
        results[arch] = {"rc": rc, "n_chars": len(text), "options": opts}
        log.info("arch %-12s rc=%d chars=%5d options=%d %s",
                 arch, rc, len(text), len(opts), opts if opts else "")

    (MINE / "bitgen_arch_options.json").write_text(json.dumps(results, indent=2))
    log.info("wrote %s", MINE / "bitgen_arch_options.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
