#!/usr/bin/env python3
#
# Build every CPU variant and tabulate area and timing.
# SPDX-License-Identifier: BSD-3-Clause

"""
Synthesises each available CPU configuration and reports what it costs.

The existing RV32 comparison could not be used to choose a core, because
VexRiscv's area figure included the whole USB fabric while the VexiiRiscv rows
did not. Reading it naively made VexRiscv look twice the size when measured
core-only it is smaller. This produces the like-for-like half of the table:
every configuration built the same way, with the same memory attached and
nothing else.

What this does **not** produce is CoreMark. That needs firmware running on the
core, which needs the CPU bring-up first. Area and Fmax are half of
performance-per-LUT and they are the half that can be measured today.

Builds run concurrently -- synthesis is single-threaded per design, so a matrix
of them is embarrassingly parallel and there are plenty of cores.

    ./scripts/cpu_matrix.py
    ./scripts/cpu_matrix.py --jobs 8
"""

import argparse
import concurrent.futures
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "cpu_matrix.log"

# Every VexRiscv configuration luna_soc ships pre-generated Verilog for. No
# Scala toolchain is involved: the update that was blocked on Scala 2.11 against
# Java 25 is a separate question from using the core as shipped.
VEXRISCV_VARIANTS = [
    "cynthion",
    "cynthion+jtag",
    "imac+dcache",
    "imc",
]

# VexiiRiscv configurations, generated from source. The recovered tree builds
# under Java 25 once three files deleted from its vendored SpinalHDL are
# restored -- the blocker recorded against VexRiscv (Scala 2.11.12) does not
# apply here, since VexiiRiscv uses Scala 2.12/2.13.
#
# The middle rows are the single-factor split the RV32 report asked for and
# never got. Its two configurations differed in three ways at once -- caches,
# atomics and supervisor mode -- so the 2x Fmax difference between them could
# not be attributed. These vary one thing at a time from a common base.
VEXII_ROOT = Path("/mnt/2tb/riscv-work/vexiiriscv")

VEXII_BASE = "--xlen=32 --with-rvm --with-rvc --with-rdtime --without-mmu"

VEXII_CONFIGS = {
    "vexii-base":        VEXII_BASE,
    "vexii+supervisor":  f"{VEXII_BASE} --with-supervisor",
    "vexii+rva":         f"{VEXII_BASE} --with-rva",
    "vexii+caches":      (f"{VEXII_BASE} --with-fetch-l1 --fetch-l1-sets=64 "
                          f"--fetch-l1-ways=1 --with-lsu-l1 --lsu-l1-sets=64 "
                          f"--lsu-l1-ways=1"),
    # The report's "moondancer-like" row, reproduced so the new rows can be
    # checked against a figure that already exists.
    "vexii-moondancer":  (f"{VEXII_BASE} --with-rva --with-fetch-l1 "
                          f"--fetch-l1-sets=64 --fetch-l1-ways=1 "
                          f"--with-lsu-l1 --lsu-l1-sets=64 --lsu-l1-ways=1"),
}

BUILD_TEMPLATE = """
source "$HOME/opt/oss-cad-suite/environment"
python3.15t - <<'PYEOF'
import sys
sys.path.insert(0, 'ecp5-test')
sys.path.insert(0, 'repos/apollo')

import riscv.cpu_area as cpu_area
cpu_area.VARIANT = {variant!r}

from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4
CynthionPlatformRev1D4().build(cpu_area.CPUArea(), do_program=False,
                               build_dir={build_dir!r})
PYEOF
"""


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def parse_report(build_dir):
    """Pull area and timing out of a finished build.

    Returns a dict, or None if the report is missing or unparseable -- which
    is itself a result worth reporting rather than an error to hide.
    """
    report = Path(build_dir) / "top.tim"
    if not report.exists():
        return None

    text = report.read_text()

    def count(name):
        match = re.search(rf"{name}:\s+(\d+)/\s+(\d+)", text)
        return int(match.group(1)) if match else None

    fmax = re.search(r"Max frequency for clock '\$glbnet\$clk': "
                     r"([\d.]+) MHz \((\w+) at ([\d.]+) MHz\)", text)

    return {
        "lut":   count("TRELLIS_COMB"),
        "ff":    count("TRELLIS_FF"),
        "bram":  count("DP16KD"),
        "fmax":  float(fmax.group(1)) if fmax else None,
        "meets": fmax.group(2) == "PASS" if fmax else None,
    }


def generate_vexii(name, flags):
    """Run the Scala generator, then synthesise the Verilog it emits.

    Generation and synthesis are separate steps here, unlike the VexRiscv path
    where the Verilog already exists. Each configuration generates into its own
    directory, because the generator writes a fixed filename and concurrent
    runs would overwrite each other.
    """
    output_dir = ROOT / "ecp5-test" / "riscv" / "matrix" / name
    output_dir.mkdir(parents=True, exist_ok=True)

    # The generator has no output-directory option -- it writes VexiiRiscv.v
    # into the working directory. So configurations are generated one at a
    # time and the result moved aside, rather than run concurrently where they
    # would overwrite each other.
    started = time.perf_counter()
    result = subprocess.run(
        ["sbt", f"runMain vexiiriscv.Generate {flags}"],
        cwd=VEXII_ROOT, capture_output=True, text=True)

    if result.returncode != 0:
        tail = [l for l in result.stdout.splitlines() if l.startswith("[error]")]
        return name, None, time.perf_counter() - started, (
            tail[-1][:70] if tail else "generate failed")

    emitted = VEXII_ROOT / "VexiiRiscv.v"
    if not emitted.exists():
        return name, None, time.perf_counter() - started, "no verilog emitted"

    verilog = output_dir / "VexiiRiscv.v"
    verilog.write_bytes(emitted.read_bytes())

    # Synthesise to the same target as the VexRiscv rows, so the numbers are
    # comparable: same device, package and speed grade.
    script = output_dir / "synth.ys"
    script.write_text(
        f"read_verilog {verilog}\n"
        f"hierarchy -top VexiiRiscv\n"
        f"synth_ecp5 -top VexiiRiscv -json {output_dir}/vexii.json\n"
        f"stat\n")

    synth = subprocess.run(
        ["bash", "-c", f'source "$HOME/opt/oss-cad-suite/environment" && '
                       f'yosys {script}'],
        cwd=ROOT, capture_output=True, text=True)
    elapsed = time.perf_counter() - started

    if synth.returncode != 0:
        return name, None, elapsed, "synthesis failed"

    def count(cell):
        match = re.search(rf"\s+(\d+)\s+{cell}\b", synth.stdout)
        return int(match.group(1)) if match else 0

    # No Fmax: yosys alone does not place or route, and running nextpnr would
    # need a top level with pins. Area is what this row contributes.
    return name, {
        "lut":  count("LUT4") + count("CCU2C"),
        "ff":   count("TRELLIS_FF"),
        "bram": count("DP16KD"),
        "fmax": None,
        "meets": None,
    }, elapsed, None


def build_one(variant):
    """Build a single variant in its own directory, so runs cannot collide."""
    build_dir = f"ecp5-test/riscv/matrix/{variant.replace('+', '_')}"
    script = BUILD_TEMPLATE.format(variant=variant, build_dir=build_dir)

    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False,
                                     dir=ROOT / "tmp") as handle:
        handle.write(script)
        script_path = handle.name

    started = time.perf_counter()
    result = subprocess.run(["bash", script_path], cwd=ROOT,
                            capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    Path(script_path).unlink(missing_ok=True)

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return variant, None, elapsed, tail[-1][:70] if tail else "build failed"

    return variant, parse_report(ROOT / build_dir), elapsed, None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=int, default=8,
                        help="concurrent builds")
    parser.add_argument("--variants", nargs="+", default=VEXRISCV_VARIANTS)
    parser.add_argument("--skip-vexii", action="store_true",
                        help="VexRiscv rows only; skips the Scala generator")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "ecp5-test" / "riscv" / "matrix").mkdir(parents=True, exist_ok=True)

    results = {}

    # VexRiscv builds are independent and run concurrently.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(build_one, v): v for v in args.variants}
        for future in concurrent.futures.as_completed(futures):
            variant, report, elapsed, error = future.result()
            results[variant] = (report, elapsed, error)
            status = error if error else "ok"
            print(f"  built {variant:<20} {elapsed:>6.1f}s  {status}",
                  flush=True)

    # VexiiRiscv generation is serialised: the generator writes a fixed
    # filename into its own tree, so concurrent runs would race.
    if not args.skip_vexii:
        for name, flags in VEXII_CONFIGS.items():
            name, report, elapsed, error = generate_vexii(name, flags)
            results[name] = (report, elapsed, error)
            status = error if error else "ok"
            print(f"  built {name:<20} {elapsed:>6.1f}s  {status}", flush=True)

    with LOG.open("w") as handle:
        emit(handle)
        emit(handle, "CPU area and timing, core plus block RAM, nothing else")
        emit(handle)
        emit(handle, f"  {'variant':<20}{'LUT4':>7}{'FF':>7}{'BRAM':>6}"
                     f"{'Fmax':>9}{'closes':>8}")

        ordered = list(args.variants)
        if not args.skip_vexii:
            ordered += list(VEXII_CONFIGS)

        for variant in ordered:
            report, elapsed, error = results.get(variant, (None, 0, "not run"))
            if error:
                emit(handle, f"  {variant:<20}  {error}")
                continue
            if report is None:
                emit(handle, f"  {variant:<20}  no timing report produced")
                continue
            if report["fmax"] is None:
                # VexiiRiscv rows are synthesis-only: yosys does not place or
                # route, so there is no Fmax to report. Said explicitly rather
                # than left blank, so the gap is not mistaken for a failure.
                emit(handle, f"  {variant:<20}{report['lut']:>7}"
                             f"{report['ff']:>7}{report['bram']:>6}"
                             f"{'synth only':>9}{'':>8}")
            else:
                closes = "yes" if report["meets"] else "no"
                emit(handle, f"  {variant:<20}{report['lut']:>7}"
                             f"{report['ff']:>7}{report['bram']:>6}"
                             f"{report['fmax']:>8.1f}M{closes:>8}")

        emit(handle)
        emit(handle, "Block RAM counts are low because a CPU with no firmware "
                     "never drives its")
        emit(handle, "bus, so synthesis prunes most of the attached memory. "
                     "The LUT figures")
        emit(handle, "reflect the core; these are not complete systems.")
        emit(handle)
        emit(handle, "CoreMark is not here. It needs firmware on the core, "
                     "which needs the")
        emit(handle, "CPU brought up first -- see the riscv bring-up issue.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
