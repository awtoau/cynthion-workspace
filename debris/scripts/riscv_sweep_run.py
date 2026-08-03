#!/usr/bin/env python3
#
# Build every configuration in the matrix and find each one's real Fmax.
# SPDX-License-Identifier: BSD-3-Clause

"""
Runs the VexiiRiscv sweep: generate, synthesise, place and route, per profile.

Fmax is found by binary search on the routing target rather than read off a
single relaxed run. nextpnr reports the frequency it achieved, but that number
depends on how hard it was asked to try: routing at a 25 MHz target and
reporting 146 MHz does not mean the design closes at 146 MHz, it means the
router stopped caring once it cleared 25. The archived sweep worked that way and
its timing results were discarded because of it.

So each configuration is routed repeatedly, tightening the target until it stops
meeting timing. The answer is the highest target the design actually closes at,
which is what "how fast will this run" means.

Core builds are wrapped by `riscv_core_wrapper.py` -- block RAM on both buses,
real handshakes -- because a bare core has ~29 top-level bus ports that nextpnr
cannot place, and the obvious workaround of tying them off deletes the design.

Results land in `tmp/riscv_sweep/`, one directory per profile, and are consumed
by `riscv_sweep_report.py`.

    ./scripts/riscv_sweep_run.py
    ./scripts/riscv_sweep_run.py --jobs 16
    ./scripts/riscv_sweep_run.py --profiles x32_rva_soc_014 x32_rva_soc_015
"""

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "riscv" / "config" / "profile_matrix_baremetal_x32.json"
VEXII = ROOT / "repos" / "vexiiriscv"
OUT = ROOT / "tmp" / "riscv_sweep"
LOG = ROOT / "tmp" / "logs" / "riscv_sweep_run.log"

# Binary search bounds, MHz. The low end is below anything this device will
# fail at and the high end above anything it will reach, so the search always
# brackets the answer.
FMAX_LOW = 20
FMAX_HIGH = 260
# Stop when the bracket is this narrow. 2 MHz is finer than the run-to-run
# placement noise, so searching further would report noise as signal.
FMAX_RESOLUTION = 2

# The Scala generator writes a fixed filename into its own working directory,
# so generation cannot run concurrently with itself.
GENERATE_LOCK = threading.Lock()


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def run(command, cwd=None, capture=True):
    """Run a command, returning (ok, output)."""
    result = subprocess.run(command, cwd=cwd, capture_output=capture,
                            text=True)
    return result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def generate(profile, work):
    """Run the Scala generator for one profile.

    Serialised: the generator writes VexiiRiscv.v or MicroSoc.v into its own
    tree with a fixed name, so two concurrent runs would overwrite each other.
    The lock is held only for generation, not for the synthesis and routing
    that follow, which are the slow parts and are safe in parallel.
    """
    main = profile["sbt_main"]
    flags = " ".join(profile["sbt_args"])
    top = profile.get("top_module", "VexiiRiscv")
    emitted = VEXII / f"{top}.v"

    with GENERATE_LOCK:
        ok, output = run(["sbt", "--batch", "--no-server",
                          f"runMain {main} {flags}"], cwd=VEXII)
        if not ok:
            errors = [l for l in output.splitlines() if l.startswith("[error]")]
            return None, (errors[-1][:90] if errors else "generate failed")
        if not emitted.exists():
            return None, f"no {top}.v emitted"
        target = work / f"{top}.v"
        target.write_bytes(emitted.read_bytes())

    return target, None


def synthesise(profile, work, verilog):
    """Synthesise to JSON, wrapping bare cores in memory first."""
    top = profile.get("top_module", "VexiiRiscv")
    sources = [verilog]

    if profile["kind"] == "core_dev":
        # A bare core cannot be placed: its bus appears as top-level ports and
        # the package has nowhere near enough pins. The wrapper attaches block
        # RAM so the design is both placeable and still a CPU.
        wrapper = work / "wrap.v"
        ok, output = run([sys.executable,
                          str(ROOT / "scripts" / "riscv_core_wrapper.py"),
                          "--rtl", str(verilog), "--out", str(wrapper)])
        if not ok:
            return None, f"wrapper failed: {output.strip().splitlines()[-1][:70]}"
        sources.append(wrapper)
        top = "VexiiRiscvWrap"

    script = work / "synth.ys"
    reads = "\n".join(f"read_verilog {s}" for s in sources)
    script.write_text(f"{reads}\n"
                      f"hierarchy -top {top}\n"
                      f"synth_ecp5 -top {top} -json {work}/design.json\n")

    ok, output = run(["yosys", str(script)], cwd=ROOT)
    if not ok:
        tail = output.strip().splitlines()
        return None, f"synthesis failed: {tail[-1][:70] if tail else ''}"

    return work / "design.json", None


def route(design, work, target_mhz):
    """Route at one target. Returns (closed, achieved_mhz, area)."""
    ok, output = run([
        "nextpnr-ecp5", "--12k", "--package", "CABGA256", "--speed", "8",
        "--json", str(design), "--textcfg", str(work / "out.cfg"),
        "--timing-allow-fail", "--freq", str(target_mhz),
    ])
    if not ok:
        return False, None, None

    achieved = None
    for match in re.finditer(
            r"Max frequency for clock.*?: ([\d.]+) MHz \((PASS|FAIL)", output):
        value = float(match.group(1))
        # Several clocks may be reported; the design closes at the slowest.
        if achieved is None or value < achieved:
            achieved = value

    def count(cell):
        found = re.search(rf"{cell}:\s+(\d+)/", output)
        return int(found.group(1)) if found else None

    area = {"lut": count("TRELLIS_COMB"), "ff": count("TRELLIS_FF"),
            "bram": count("DP16KD")}
    closed = "FAIL at" not in output and achieved is not None
    return closed, achieved, area


def find_fmax(design, work):
    """Binary search the highest target the design still closes at.

    Routing at a low target and reading the reported frequency overstates what
    the design will do, because the router stops optimising once it clears the
    target. Searching for the point where it stops meeting timing gives a number
    that means something.
    """
    low, high = FMAX_LOW, FMAX_HIGH
    best, area, attempts = None, None, 0

    # Confirm the low end closes at all; if it does not, the design is broken
    # rather than slow and searching is pointless.
    closed, achieved, first_area = route(design, work, low)
    attempts += 1
    if not closed:
        return None, first_area, attempts, achieved

    best, area = low, first_area

    while high - low > FMAX_RESOLUTION:
        middle = (low + high) // 2
        closed, achieved, this_area = route(design, work, middle)
        attempts += 1
        if closed:
            low, best, area = middle, middle, this_area
        else:
            high = middle

    return best, area, attempts, None


def build(profile):
    """Generate, synthesise and time one profile."""
    name = profile["name"]
    work = OUT / name
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    verilog, error = generate(profile, work)
    if error:
        return name, None, time.perf_counter() - started, error

    design, error = synthesise(profile, work, verilog)
    if error:
        return name, None, time.perf_counter() - started, error

    fmax, area, attempts, achieved = find_fmax(design, work)
    elapsed = time.perf_counter() - started

    if fmax is None:
        return name, None, elapsed, (
            f"does not close even at {FMAX_LOW} MHz"
            + (f" (reached {achieved:.1f})" if achieved else ""))

    result = {
        "name": name, "tag": profile["tag"], "kind": profile["kind"],
        "cache_sets": profile.get("cache_sets", 0),
        "fmax": fmax, "route_attempts": attempts, "seconds": elapsed,
        **(area or {}),
    }
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return name, result, elapsed, None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--profiles", nargs="+",
                        help="build only these profile names")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()

    for tool in ("sbt", "yosys", "nextpnr-ecp5"):
        if shutil.which(tool) is None:
            print(f"{tool} not on PATH -- source the oss-cad-suite environment")
            return 1
    if not args.config.exists():
        print(f"no matrix at {args.config}; run riscv_matrix_config.py first")
        return 1

    profiles = json.loads(args.config.read_text())["profiles"]
    if args.profiles:
        wanted = set(args.profiles)
        profiles = [p for p in profiles if p["name"] in wanted]
    if not profiles:
        print("no profiles selected")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results, failures = {}, {}

    with LOG.open("w") as handle:
        emit(handle, f"{len(profiles)} profiles, {args.jobs} concurrent")
        emit(handle, f"Fmax by binary search over {FMAX_LOW}-{FMAX_HIGH} MHz "
                     f"to {FMAX_RESOLUTION} MHz")
        emit(handle)

        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            futures = {pool.submit(build, p): p["name"] for p in profiles}
            done = 0
            for future in concurrent.futures.as_completed(futures):
                name, result, elapsed, error = future.result()
                done += 1
                if error:
                    failures[name] = error
                    emit(handle, f"  [{done}/{len(profiles)}] {name:<28} "
                                 f"{elapsed:>6.1f}s  {error}")
                else:
                    results[name] = result
                    emit(handle, f"  [{done}/{len(profiles)}] {name:<28} "
                                 f"{elapsed:>6.1f}s  {result['fmax']:>5} MHz  "
                                 f"{result['lut']:>6} LUT  "
                                 f"{result['bram']:>3} BRAM")

        total = time.perf_counter() - started
        emit(handle)
        emit(handle, f"{len(results)} built, {len(failures)} failed, "
                     f"{total / 60:.1f} min")

        if failures:
            emit(handle)
            emit(handle, "Failures are reported rather than dropped -- a "
                         "configuration that")
            emit(handle, "cannot be built is a result about the device, not a "
                         "gap in the data:")
            for name, error in sorted(failures.items()):
                emit(handle, f"    {name:<28} {error}")

        emit(handle)
        emit(handle, f"results: {OUT}")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
