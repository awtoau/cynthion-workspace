#!/usr/bin/env python3
"""Benchmark yosys + nextpnr flow variants on an existing Amaranth build dir.

Answers "where does the ~57 s FPGA build go, and which flags actually help".
Reuses the .il/.v/.lpf an Amaranth build already emitted, so it never
re-elaborates and never touches the original build directory.

Two knobs turned out to matter on ECP5 (see docs):
  * yosys `synth_ecp5 -run :check` skips the `autoname` pass, which is ~30% of
    synthesis and purely cosmetic (it renames $abc$1234$ nets for readability).
    The `check` label's other passes are replayed manually because the JSON
    backend needs `blackbox =A:whitebox`.
  * nextpnr `--parallel-refine` replaces the serial SA refinement that runs
    after the HeAP placer. This is the only genuinely threaded phase; plain
    `--threads` does nothing without it.

Usage:
    scripts/fpga_flow_bench.py --build-dir tmp/vexii_hello/build [--repeat 3]

Logs to ./tmp/logs/fpga_flow_bench.log and to stdout.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

OSS = Path.home() / "opt" / "oss-cad-suite" / "bin"
YOSYS = OSS / "yosys"
NEXTPNR = OSS / "nextpnr-ecp5"

READ = "read_verilog VexiiRiscv.v; read_rtlil top.il;"
# The `check` label of synth_lattice is: autoname; hierarchy -check; stat;
# check -noinit; blackbox =A:whitebox. We want all of it except autoname.
CHECK_MINUS_AUTONAME = "hierarchy -check; check -noinit; blackbox =A:whitebox;"

SYNTH = {
    "baseline": f"{READ} synth_ecp5 -top top; write_json out.json",
    "no-autoname": (
        f"{READ} synth_ecp5 -top top -run :check; "
        f"{CHECK_MINUS_AUTONAME} write_json out.json"
    ),
}

# ECP5 device flags for CynthionPlatformRev1D4 (LFE5U-12F, CABGA256, speed 8).
PNR_DEVICE = ["--12k", "--package", "CABGA256", "--speed", "8"]

PNR = {
    "baseline": [],
    "parallel-refine": ["--parallel-refine", "--threads", "31"],
    "parallel-refine+router2": [
        "--parallel-refine", "--threads", "31", "--router", "router2"
    ],
}


def timed(cmd: list[str], cwd: Path, log: logging.Logger) -> tuple[float, int]:
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        log.error("command failed (rc=%d): %s", proc.returncode, " ".join(cmd))
        log.error("%s", (proc.stderr or proc.stdout)[-2000:])
    return elapsed, proc.returncode


def fmax(timfile: Path) -> str:
    """Lowest reported post-route Fmax, as a quality-of-result sanity check."""
    if not timfile.is_file():
        return "n/a"
    freqs = []
    for line in timfile.read_text(errors="replace").splitlines():
        if "Max frequency for clock" in line and "MHz" in line:
            try:
                freqs.append(float(line.split(":")[-1].strip().split()[0]))
            except (ValueError, IndexError):
                pass
    return f"{min(freqs):.1f} MHz" if freqs else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build-dir", type=Path, default=Path("tmp/vexii_hello/build"),
                    help="existing Amaranth build dir to take sources from")
    ap.add_argument("--work-dir", type=Path, default=Path("tmp/fpga_flow_bench"),
                    help="scratch dir; never the source build dir")
    ap.add_argument("--repeat", type=int, default=1, help="runs per variant")
    args = ap.parse_args()

    logdir = Path("tmp/logs")
    logdir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[
            logging.FileHandler(logdir / "fpga_flow_bench.log", mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("fpga_flow_bench")

    src = args.build_dir
    work = args.work_dir
    if work.resolve() == src.resolve():
        log.error("refusing to run inside the source build dir")
        return 1

    work.mkdir(parents=True, exist_ok=True)
    needed = ["top.il", "VexiiRiscv.v", "top.lpf"]
    for f in needed:
        if not (src / f).is_file():
            log.error("missing %s -- run an Amaranth build first", src / f)
            return 1
        shutil.copy2(src / f, work / f)

    log.info("sources from %s, working in %s", src, work)
    log.info("")

    results: dict[str, list[float]] = {}

    log.info("=== yosys synthesis ===")
    for name, script in SYNTH.items():
        times = []
        for i in range(args.repeat):
            elapsed, rc = timed(
                [str(YOSYS), "-q", "-l", f"syn_{name}.rpt", "-p", script], work, log
            )
            if rc == 0:
                times.append(elapsed)
        if times:
            shutil.copy2(work / "out.json", work / f"syn_{name}.json")
            results[f"synth:{name}"] = times
            log.info("  %-26s %6.2f s  (best of %d)", name, min(times), len(times))

    log.info("")
    log.info("=== nextpnr place & route (on baseline netlist) ===")
    netlist = work / "syn_baseline.json"
    if netlist.is_file():
        for name, flags in PNR.items():
            times = []
            for i in range(args.repeat):
                elapsed, rc = timed(
                    [str(NEXTPNR), "--quiet", "--log", f"pnr_{name}.tim",
                     *PNR_DEVICE, "--json", netlist.name, "--lpf", "top.lpf",
                     "--textcfg", f"pnr_{name}.config", *flags],
                    work, log,
                )
                if rc == 0:
                    times.append(elapsed)
            if times:
                results[f"pnr:{name}"] = times
                log.info("  %-26s %6.2f s  (best of %d)  Fmax %s",
                         name, min(times), len(times), fmax(work / f"pnr_{name}.tim"))

    log.info("")
    log.info("=== combined best vs baseline ===")
    sb = min(results.get("synth:baseline", [0])) or 0
    pb = min(results.get("pnr:baseline", [0])) or 0
    sf = min(results.get("synth:no-autoname", [0])) or 0
    pf = min(results.get("pnr:parallel-refine+router2", [0])) or 0
    if sb and pb and sf and pf:
        log.info("  baseline   synth %5.1f + pnr %5.1f = %5.1f s", sb, pb, sb + pb)
        log.info("  fast flow  synth %5.1f + pnr %5.1f = %5.1f s", sf, pf, sf + pf)
        log.info("  speedup    %.2fx  (%.1f s saved)", (sb + pb) / (sf + pf),
                 (sb + pb) - (sf + pf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
