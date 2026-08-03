#!/usr/bin/env python3
"""Find out whether a yosys ECP5 netlist can reach Diamond's place-and-route.

The `--mode yosys` experiment -- yosys synthesis into Diamond PAR -- is the
one that would separate synthesis from place-and-route. It only works if
Diamond's ngdbuild will accept the primitives yosys emits, and it does not:

    ERROR - ngdbuild: INITVAL string not allowed on single-port or dual-port
    block cpu...asMem_ram.1.9(TRELLIS_DPR16X4)
    ERROR - ngdbuild: Block ...: missing INITSTATE property on ROM .

This script tries synthesis variants and reports which, if any, produce a
netlist ngdbuild accepts. It exists so the conclusion is measured rather than
asserted -- and so it can be re-checked against a newer yosys.

Each variant that avoids a primitive also changes the design, so a variant
that gets through is not automatically a valid basis for comparison. That
judgement is recorded in the output rather than assumed.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diamond_flow import ARCH, DEVICE, diamond_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOGDIR = ROOT / "tmp" / "logs"

# Each variant is (label, extra synth_ecp5 flags, whether it preserves the
# design as the open flow builds it).
VARIANTS = [
    ("baseline", [], True),
    ("nolutram", ["-nolutram"], False),
    ("nolutram-nobram", ["-nolutram", "-nobram"], False),
]

# Passes needed regardless, from docs/upstream-yosys-edif-notes.md.
FIXUPS = ["flatten", "delete t:$scopeinfo", "splitnets -ports", "opt_clean"]


def log(msg, handle):
    print(msg, flush=True)
    handle.write(msg + "\n")
    handle.flush()


def try_variant(label, flags, il, extra_v, outdir, handle, yosys="yosys"):
    """Synthesise with these flags, write EDIF, and see if ngdbuild takes it."""
    work = outdir / label
    work.mkdir(parents=True, exist_ok=True)
    edf = work / "out.edf"

    script = "".join(f"read_verilog {v}\n" for v in extra_v)
    script += f"read_rtlil {il}\n"
    script += f"synth_ecp5 -top top {' '.join(flags)}\n"
    script += "".join(f"{p}\n" for p in FIXUPS)
    script += f"write_edif {edf.name}\n"
    ys = work / "run.ys"
    ys.write_text(script)

    log(f"--- {label}: synth_ecp5 {' '.join(flags) or '(no flags)'}", handle)
    proc = subprocess.run([yosys, "-q", "-s", ys.name], cwd=str(work),
                          capture_output=True, text=True)
    handle.write(proc.stdout + proc.stderr + "\n")
    if proc.returncode != 0:
        log(f"{label}: yosys failed", handle)
        return {"variant": label, "yosys": "failed"}

    proc = subprocess.run(
        ["edif2ngd", "-l", ARCH, "-d", DEVICE, edf.name, "out.ngo"],
        cwd=str(work), env=diamond_env(), capture_output=True, text=True)
    handle.write(proc.stdout + proc.stderr + "\n")
    errs = [ln.strip() for ln in (proc.stdout + proc.stderr).splitlines()
            if ln.startswith("ERROR")]
    if errs:
        log(f"{label}: edif2ngd rejected -- {errs[0]}", handle)
        return {"variant": label, "edif2ngd": "rejected", "error": errs[0]}

    proc = subprocess.run(
        ["ngdbuild", "-a", ARCH, "-d", DEVICE, "out.ngo", "out.ngd"],
        cwd=str(work), env=diamond_env(), capture_output=True, text=True)
    handle.write(proc.stdout + proc.stderr + "\n")
    errs = [ln.strip() for ln in (proc.stdout + proc.stderr).splitlines()
            if ln.startswith("ERROR")]
    if errs:
        # Report the distinct error kinds, not every repetition -- the same
        # two messages appear on both stdout and stderr.
        kinds = sorted({e.split("(")[0][:110] for e in errs})
        log(f"{label}: ngdbuild rejected ({len(kinds)} distinct):", handle)
        for k in kinds:
            log(f"    {k}", handle)
        return {"variant": label, "ngdbuild": "rejected", "errors": kinds}

    log(f"{label}: ACCEPTED by ngdbuild", handle)
    return {"variant": label, "ngdbuild": "accepted"}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--il", required=True, type=Path)
    ap.add_argument("--extra-verilog", type=Path, nargs="*", default=[])
    ap.add_argument("--outdir", required=True, type=Path)
    ap.add_argument("--name", default="diamond_par_probe")
    ap.add_argument("--yosys", default="yosys")
    args = ap.parse_args()

    LOGDIR.mkdir(parents=True, exist_ok=True)
    logpath = LOGDIR / f"{args.name}.log"
    results = []
    with open(logpath, "w") as handle:
        log(f"rtlil {args.il}", handle)
        for label, flags, faithful in VARIANTS:
            res = try_variant(label, flags, args.il, args.extra_verilog,
                              args.outdir, handle, args.yosys)
            res["preserves_design"] = faithful
            results.append(res)

        log("\n=== can a yosys netlist reach Diamond PAR? ===", handle)
        for r in results:
            state = r.get("ngdbuild") or r.get("edif2ngd") or r.get("yosys")
            note = "" if r["preserves_design"] else \
                "  (changes the design -- not a like-for-like comparison)"
            log(f"  {r['variant']:18s} {state}{note}", handle)

        out = args.outdir / f"{args.name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2))
        log(f"\nwrote {out}\nlog {logpath}", handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
