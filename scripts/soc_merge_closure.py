#!/usr/bin/env python3
#
# Does the MERGED SoC close at SYNC_MHZ 60? #432 stage 1, and the gate on the rest of it.
# SPDX-License-Identifier: BSD-3-Clause

"""Fmax for BootRAM + the second PLL + the BIST engine, in one elaboration.

    ./scripts/soc_merge_closure.py            # controls, then 60 x3, then 50 x3
    ./scripts/soc_merge_closure.py --runs 1   # one build per point
    ./scripts/soc_merge_closure.py --elaborate  # preflight only, no synthesis

## The question

`SYNC_MHZ` is 50 in the BIST variant because the four-domain build stopped
closing at 60. The merged design (#432) is that same build plus `BootRAM` --
strictly more logic -- so its `clk` Fmax decides whether the merge is worth
building. Nothing else in #432 is built until this number exists.

## What is measured, and what it is not

`CYNTHION_HYPERRAM_MERGED=1` (see `gateware/soc/top.py`) elaborates both halves
of the fork. The two masters do NOT share the part here: the engine keeps `ram`
0, `BootRAM` is given a shadow `ram` 1 on the mezzanine pins. So this build
carries two PHYs and two controllers where the merged design will carry one
behind a mode mux, and its HyperRAM pins are not the product's.

Every domain is present and nothing is pruned, which is what makes the Fmax
real. The direction of the error is stated in the issue.

## Determinism, and proving the instrument can fail

Every build here passes `--no-parallel-refine`: nextpnr's threaded SA refinement
is the whole of this design's Fmax spread (#361), and `soc_run.py` overwrote the
documented `AMARANTH_nextpnr_opts` override so the flag had no effect until #429.
The check is the generated `build_top.sh`, which carries the nextpnr command
line the build actually ran -- read back per run rather than assumed.

Two negative controls run FIRST, because an instrument that cannot fail reports
success either way (#402 and the ten before it):

  * `SYNC_MHZ` 91 -- above `clocks.SYNC_CEILING_MHZ`, refused before synthesis.
    No `top.tim` at all, so this is the control for "the parser found nothing".
  * `SYNC_MHZ` 90 -- reachable by the PLL, far above what this fabric closes at.
    A `top.tim` full of FAIL, so this is the control for "closes" versus "built".

If either control comes back CLOSES, the run stops without reporting a number.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp" / "merge-closure"
SPREAD = ROOT / "tmp" / "build-spread"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

from devlog import emit, spawn  # noqa: E402
from soc import variant  # noqa: E402

# The constraint as well as the achieved rate: `(PASS at 60.00 MHz)`. Which
# domain BINDS is the ratio of the two, and the achieved figure alone cannot say
# -- `usb` at 78 MHz against a 60 MHz constraint has more margin than `clk` at 62.
FMAX_RE = re.compile(
    r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz\s+"
    r"\((PASS|FAIL) at ([\d.]+) MHz\)")

CLOSES, MISSES, NO_RESULT = "CLOSES", "MISSES TIMING", "NO RESULT"


def env_for(sync_mhz):
    """The variant this script measures, at one CPU clock."""
    return {"CYNTHION_HYPERRAM_MERGED": "1", "CYNTHION_SYNC_MHZ": f"{sync_mhz:g}"}


def apply_env(env):
    """Children inherit the environment; `spawn` has no env parameter."""
    for name, _default, _tag, _kind in variant.VARIANT_ENV:
        os.environ.pop(name, None)
    os.environ.update(env)


def timing(path):
    """{clock: {mhz, target, status, margin}} from a nextpnr log."""
    if not path.exists():
        return {}
    out = {}
    for clock, mhz, status, target in FMAX_RE.findall(path.read_text()):
        out[clock.split("$")[-1]] = {
            "mhz": float(mhz), "target": float(target), "status": status,
            "margin": round(float(mhz) / float(target) - 1, 4)}
    return out


def verdict(clocks):
    if not clocks:
        return NO_RESULT
    return CLOSES if all(c["status"] == "PASS" for c in clocks.values()) else MISSES


def binds(clocks):
    """The domain with the least margin -- the one that decides the verdict."""
    if not clocks:
        return None
    return min(clocks, key=lambda name: clocks[name]["margin"])


def pnr_flags(build_dir):
    """The nextpnr command line the build ACTUALLY ran (#429)."""
    script = build_dir / "build_top.sh"
    if not script.exists():
        return None
    for line in script.read_text().splitlines():
        if "NEXTPNR_ECP5" in line and "--json" in line:
            return line.strip()
    return None


def point(label, sync_mhz, runs):
    """`runs` builds of the merged design at one `SYNC_MHZ`. Returns the rows.

    Unbounded on purpose: `soc_run.py` bounds each synthesis itself from this
    machine's own history (`synthesis_floor`), and a second bound here sized by
    guesswork would kill a build that was working.
    """
    env = env_for(sync_mhz)
    apply_env(env)
    build_dir = variant.build_dir(ROOT, env)
    emit(f"\n=== {label}: SYNC_MHZ {sync_mhz:g}, {runs} build(s) -> "
         f"{build_dir.name} ===")
    rc = spawn([sys.executable, str(ROOT / "scripts" / "soc_build_spread.py"),
                "--runs", str(runs), "--label", label,
                "--", "--no-parallel-refine"], quiet=True)

    flags = pnr_flags(build_dir)
    if flags is None:
        emit("  no build_top.sh: nothing verified the placer flags")
    else:
        parallel = "--parallel-refine" in flags
        emit(f"  nextpnr: {'--parallel-refine PRESENT' if parallel else 'deterministic'}"
             f" (build_top.sh)")
        if parallel:
            emit("  *** #429 REGRESSION: --no-parallel-refine did not reach nextpnr;")
            emit("      every Fmax below is non-deterministic and not evidence.")

    rows = []
    for index in range(1, runs + 1):
        clocks = timing(SPREAD / label / str(index) / "top.tim")
        row = {"label": label, "sync_mhz": sync_mhz, "run": index,
               "verdict": verdict(clocks), "binds": binds(clocks),
               "clocks": clocks, "nextpnr": flags,
               "deterministic": bool(flags) and "--parallel-refine" not in flags}
        rows.append(row)
        emit(f"  run {index}: {row['verdict']:13s} binds={row['binds']}  "
             + "  ".join(f"{name}={c['mhz']:.2f}/{c['target']:.0f}"
                         for name, c in sorted(clocks.items())))
    if rc != 0 and not rows:
        emit(f"  spread rc={rc} and no timing report; "
             f"see tmp/logs/synthesis-{variant.slug()}.log")
    return rows


def elaborate(sync_mhz):
    """Amaranth only: resource conflicts and refusals, in seconds not minutes."""
    apply_env(env_for(sync_mhz))
    return spawn([sys.executable, str(Path(__file__).resolve()),
                  "--elaborate-child", f"{sync_mhz:g}"], quiet=True)


def elaborate_child(sync_mhz):
    """The child of `elaborate`: build files, no yosys, no nextpnr."""
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    from board.cynthion_r1_4 import CynthionPlatformRev1D4  # noqa: PLC0415
    import top as soc_top                                   # noqa: PLC0415

    assert soc_top.HYPERRAM_MERGED, "CYNTHION_HYPERRAM_MERGED did not reach top.py"
    assert soc_top.SYNC_MHZ == float(sync_mhz), soc_top.SYNC_MHZ
    where = OUT / f"elaborate-{sync_mhz:g}"
    plan = CynthionPlatformRev1D4().build(
        soc_top.AwtoSoc(firmware=[0] * 16), do_build=False)
    # Written but NOT run: the pin map and the RTLIL are what a preflight reads,
    # and yosys is the ~60 s this exists to get ahead of.
    plan.execute_local(str(where), run_script=False)
    print(f"elaborated: {where}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3,
                        help="builds per point; 3 is the floor for movement")
    parser.add_argument("--elaborate", action="store_true",
                        help="preflight only: elaborate at 60 and stop")
    parser.add_argument("--elaborate-child", type=float, default=None,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.elaborate_child is not None:
        return elaborate_child(args.elaborate_child)

    OUT.mkdir(parents=True, exist_ok=True)

    # PREFLIGHT. Both submodules requesting `ram` is the obvious way for this to
    # be a 3-minute failure instead of a 3-second one.
    emit("preflight: elaborating the merged design at SYNC_MHZ 60")
    if elaborate(60) != 0:
        emit("merged elaboration FAILED; see tmp/logs/dev.log")
        return 1
    emit("  elaborates: BootRAM, HyperRAMDomains and the engine in one design")
    if args.elaborate:
        return 0

    # THE CONTROLS, before the measurement. A harness that reports CLOSES for a
    # refused build or a FAIL-riddled one has no result to report at 60 either.
    emit("\ncontrol A: SYNC_MHZ 91, above clocks.SYNC_CEILING_MHZ -- "
         "expect a refusal and NO RESULT")
    refused = elaborate(91) != 0
    emit(f"  elaboration refused: {refused}")

    controls = point("merge-control-90", 90, 1)
    results = {"control_refusal_at_91": refused, "rows": controls}

    if not refused or any(row["verdict"] == CLOSES for row in controls):
        emit("\nHARNESS NOT TRUSTED: a control that must fail did not.")
        emit("  91 MHz must be refused before synthesis; 90 MHz must miss timing.")
        (OUT / "closure.json").write_text(json.dumps(results, indent=2))
        return 1
    emit("\ncontrols behaved: the harness distinguishes CLOSES, MISSES and "
         "NO RESULT")

    # The question, and the control that separates "not at 60" from "not at all".
    results["rows"] += point("merge-sync60", 60, args.runs)
    results["rows"] += point("merge-sync50", 50, args.runs)

    emit("\n=== #432 stage 1 ===")
    for row in results["rows"]:
        clk = row["clocks"].get("clk")
        rate = f"clk={clk['mhz']:.2f} MHz" if clk else "no timing report"
        emit(f"  SYNC_MHZ {row['sync_mhz']:>2g} run {row['run']}: "
             f"{row['verdict']:13s} {rate}  binds={row['binds']}")
    (OUT / "closure.json").write_text(json.dumps(results, indent=2))
    emit(f"wrote {(OUT / 'closure.json').relative_to(ROOT)}")

    at60 = [r for r in results["rows"] if r["sync_mhz"] == 60]
    return 0 if at60 and all(r["clocks"] for r in at60) else 1


if __name__ == "__main__":
    sys.exit(main())
