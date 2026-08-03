#!/usr/bin/env python3
"""Probe the Diamond installation and check the yosys -> Diamond EDIF handoff.

Two jobs, both diagnostic rather than part of the measurement:

  --check      report which Diamond engines run and what device names they
               accept, so a broken install is obvious before a long build.

  --edif-repro build the two minimal cases that show yosys's EDIF output
               being rejected by Diamond, and verify the workarounds. These
               back the bug reports in docs/upstream-yosys-edif-notes.md;
               keeping them runnable means the claims can be re-checked
               against a newer yosys rather than taken on trust.

Diamond's environment comes from flow.diamond_env(), an env dict
rather than a sourced shell script. That matters: the oss-cad-suite
environment the open flow needs sets PYTHONHOME and prepends its own
libstdc++, and sourcing both in one shell is what breaks Diamond's engines.
Passing an explicit env to each subprocess keeps them from ever meeting.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flow import ARCH, DEVICE, diamond_env  # noqa: E402

# scripts/diamond/<name>.py, so the repo root is three levels up. These lived in
# scripts/ and were moved; a stale `.parent.parent` put every log and artifact
# under scripts/tmp/ instead of tmp/, which is silent and wrong.
ROOT = Path(__file__).resolve().parent.parent.parent
LOGDIR = ROOT / "tmp" / "logs"
TMP = ROOT / "tmp" / "diamond"

# A bare inout bus. yosys writes it as an EDIF array port, which edif2ngd
# expands into eight pins that all keep the base name, and then rejects.
MINIBUS_V = """\
// Minimal case for: ERROR - edif2ngd: block '': multiple pins named 'd'
module top(inout [7:0] d, input clk, input oe);
  reg [7:0] q;
  always @(posedge clk) q <= d;
  assign d = oe ? q : 8'bzzzzzzzz;
endmodule
"""

# One submodule, which survives synthesis as a $scopeinfo annotation that
# write_edif emits as an instance of a cell it never declares.
MINIHIER_V = """\
// Minimal case for: ERROR - edif2ngd: Reference to an unknown cell id00008.
module sub(input a, output b); assign b = ~a; endmodule
module top(input clk, input a, output reg b);
  wire w; sub u(.a(a), .b(w));
  always @(posedge clk) b <= w;
endmodule
"""


def log(msg, handle):
    print(msg, flush=True)
    handle.write(msg + "\n")
    handle.flush()


def run(cmd, handle, env=None, cwd=None, label=None):
    """Run a command, logging it and its output. Returns CompletedProcess."""
    if label:
        log(f"--- {label}", handle)
    proc = subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, env=env, cwd=str(cwd) if cwd else None)
    handle.write(proc.stdout + proc.stderr + "\n")
    handle.flush()
    return proc


def check_install(handle):
    """Confirm the engines run and report the device names they accept."""
    env = diamond_env()
    ok = True
    for engine in ("synthesis", "map", "par", "edif2ngd", "trce", "bitgen"):
        proc = run([engine], handle, env=env)
        # These engines exit non-zero when invoked with no arguments; what
        # matters is that they loaded their shared libraries and printed a
        # usage banner rather than failing to start at all.
        started = "version" in (proc.stdout + proc.stderr).lower() or \
                  "usage" in (proc.stdout + proc.stderr).lower()
        log(f"{engine:12s} {'ok' if started else 'FAILED TO START'}", handle)
        ok = ok and started

    proc = run(["map", "-h"], handle, env=env)
    arches = [ln.strip() for ln in (proc.stdout + proc.stderr).splitlines()
              if "ECP5" in ln]
    log(f"ECP5 architectures map accepts: {arches}", handle)
    log(f"using ARCH={ARCH} DEVICE={DEVICE}", handle)
    return ok


def edif_case(name, source, extra_passes, handle, yosys="yosys"):
    """Synthesise one minimal case, write EDIF, and try to read it into Diamond.

    Returns True if edif2ngd accepted the netlist. Run once without the
    workaround passes and once with them, and the pair shows both that the
    bug is real and that the fix works.
    """
    work = TMP / "repro" / name
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src.v"
    src.write_text(source)
    edf = work / "out.edf"

    script = f"read_verilog {src.name}\nsynth_ecp5 -top top\n"
    script += "".join(f"{p}\n" for p in extra_passes)
    script += f"write_edif {edf.name}\n"
    ys = work / "run.ys"
    ys.write_text(script)

    proc = run([yosys, "-q", "-s", ys.name], handle, cwd=work,
               label=f"{name}: yosys ({', '.join(extra_passes) or 'no fix'})")
    if proc.returncode != 0:
        log(f"{name}: yosys failed", handle)
        return False

    proc = run(["edif2ngd", "-l", ARCH, "-d", DEVICE, edf.name, "out.ngo"],
               handle, env=diamond_env(), cwd=work, label=f"{name}: edif2ngd")
    text = proc.stdout + proc.stderr
    errors = [ln.strip() for ln in text.splitlines()
              if ln.startswith("ERROR")]
    if errors:
        log(f"{name}: REJECTED -- {errors[0]}", handle)
        return False
    log(f"{name}: accepted", handle)
    return True


def edif_repro(handle):
    """Show both EDIF blockers and confirm the workarounds fix them."""
    results = {}
    # Blocker 2: vector ports. splitnets -ports makes them scalar.
    results["bus, unfixed"] = edif_case(
        "bus_unfixed", MINIBUS_V, [], handle)
    results["bus, splitnets -ports"] = edif_case(
        "bus_fixed", MINIBUS_V, ["splitnets -ports", "opt_clean"], handle)
    # Blocker 1: $scopeinfo. flatten creates them; delete removes them.
    results["hier, unfixed"] = edif_case(
        "hier_unfixed", MINIHIER_V, ["flatten"], handle)
    results["hier, delete $scopeinfo"] = edif_case(
        "hier_fixed", MINIHIER_V,
        ["flatten", "delete t:$scopeinfo", "opt_clean"], handle)

    log("\n=== EDIF handoff reproducers ===", handle)
    for key, accepted in results.items():
        log(f"  {key:32s} {'accepted' if accepted else 'rejected'}", handle)
    log("\nExpected: the 'unfixed' rows are rejected and the fixed rows are "
        "accepted.\nIf an unfixed row is now accepted, yosys has been fixed "
        "upstream and\ndocs/upstream-yosys-edif-notes.md should be updated.",
        handle)
    return results


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="report which Diamond engines run")
    ap.add_argument("--edif-repro", action="store_true",
                    help="build the minimal EDIF rejection cases")
    ap.add_argument("--name", default="diamond_probe")
    args = ap.parse_args()
    if not (args.check or args.edif_repro):
        ap.error("pass --check and/or --edif-repro")

    LOGDIR.mkdir(parents=True, exist_ok=True)
    logpath = LOGDIR / f"{args.name}.log"
    with open(logpath, "w") as handle:
        if args.check:
            check_install(handle)
        if args.edif_repro:
            edif_repro(handle)
        log(f"\nlog {logpath}", handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
