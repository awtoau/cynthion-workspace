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
LOG = ROOT / "tmp" / "logs" / "cpu_matrix.log"

# Every VexRiscv configuration luna_soc ships pre-generated Verilog for. No
# Scala toolchain is involved: the update that was blocked on Scala 2.11 against
# Java 25 is a separate question from using the core as shipped.
VEXRISCV_VARIANTS = [
    "cynthion",
    "cynthion+jtag",
    "imac+dcache",
    "imc",
]

# VexiiRiscv configurations, generated from source. The tree is a submodule at
# repos/vexiiriscv, pinned to v0.0.0-1297-gf8774d4, and builds under Java 25 --
# the blocker recorded against VexRiscv (Scala 2.11.12) does not apply here,
# since VexiiRiscv uses Scala 2.12/2.13.
#
# An earlier note here claimed the tree needed three files restored to its
# vendored SpinalHDL. It did not: they were missing because the nested
# submodules had never been initialised, and a clean clone with
# `submodule update --init --recursive` builds without any fix.
#
# The middle rows are the single-factor split the RV32 report asked for and
# never got. Its two configurations differed in three ways at once -- caches,
# atomics and supervisor mode -- so the 2x Fmax difference between them could
# not be attributed. These vary one thing at a time from a common base.
VEXII_ROOT = ROOT / "repos" / "vexiiriscv"

VEXII_BASE = "--xlen=32 --with-rvm --with-rvc --with-rdtime --without-mmu"

# The same cache geometry on every cached row, so cache size is never the
# variable. 64 sets is also the most the MMU permits (Param.scala:845) -- a
# wider cache and an MMU cannot be had together without more ways.
VEXII_CACHES = ("--with-fetch-l1 --fetch-l1-sets=64 --fetch-l1-ways=1 "
                "--with-lsu-l1 --lsu-l1-sets=64 --lsu-l1-ways=1")

# The CPU this SoC actually builds: ecp5-test/riscv/vexii_cpu.py's
# GENERATE_FLAGS, minus --xlen, which the rows below supply. scopt rejects an
# option given twice, so it cannot simply be overridden.
VEXII_SOC = ("--with-rvm --with-rvc --with-rva --with-rdtime "
             f"{VEXII_CACHES} "
             "--fetch-wishbone --lsu-wishbone --lsu-l1-wishbone "
             "--debug-jtag-instruction "
             "--with-btb --relaxed-btb --relaxed-branch")

# What the privilege flags actually mean, read off Param.scala rather than
# assumed from their names:
#
#   --with-supervisor  ->  addISA("s", "u")                       (line 724)
#   --without-mmu      ->  disableMmu = true                      (line 726)
#   withMmu            =   checkISA("s") && !disableMmu           (line 584)
#   withSupervisor     =   checkISA("s")                          (line 586)
#
# So there is no `--with-mmu`. The MMU arrives as a side effect of asking for
# supervisor mode, and the only way to name it separately is to ask for
# supervisor and then take the MMU away again. Two consequences:
#
#   * `--without-mmu` on a configuration that never asked for supervisor is a
#     no-op -- there is no "s" in the ISA, so withMmu was already false. It is
#     left on the rows below that had it, so those rows stay byte-identical to
#     the ones already published, but it is doing nothing there.
#   * Supervisor is separable from the MMU (S-mode with `--without-mmu`), but
#     the MMU is *not* separable from supervisor: no "s", no translation. Linux
#     wants both, so that direction never needed separating.
#
# xlen picks the translation scheme on its own: sv32 at 32, sv39 at 64
# (Param.scala:855). "+mmu" therefore means a different page table at each
# width, which is inherent and not a second variable.
VEXII_CONFIGS = {
    "vexii-base":        VEXII_BASE,
    "vexii+supervisor":  f"{VEXII_BASE} --with-supervisor",
    "vexii+rva":         f"{VEXII_BASE} --with-rva",
    "vexii+caches":      f"{VEXII_BASE} {VEXII_CACHES}",
    # The report's "moondancer-like" row, reproduced so the new rows can be
    # checked against a figure that already exists.
    "vexii-moondancer":  f"{VEXII_BASE} --with-rva {VEXII_CACHES}",

    # The rv64 + MMU question. Every row below differs from the row named in
    # its comment by exactly one flag, so each line of the table costs one
    # thing. `vexii+caches` is the common root.
    #
    # +caches, then supervisor            (+ --with-supervisor)
    "vexii+caches+sv":   f"{VEXII_BASE} --with-supervisor {VEXII_CACHES}",
    # +caches+sv, then the MMU            (- --without-mmu)
    "vexii+caches+mmu":  ("--xlen=32 --with-rvm --with-rvc --with-rdtime "
                          f"--with-supervisor {VEXII_CACHES}"),
    # +caches, then 64 bits               (--xlen=64)
    "vexii64+caches":    ("--xlen=64 --with-rvm --with-rvc --with-rdtime "
                          f"--without-mmu {VEXII_CACHES}"),
    # 64+caches, then supervisor          (+ --with-supervisor)
    "vexii64+caches+sv": ("--xlen=64 --with-rvm --with-rvc --with-rdtime "
                          f"--without-mmu --with-supervisor {VEXII_CACHES}"),
    # 64+caches+sv, then the MMU          (- --without-mmu)  <- the Linux core
    "vexii64+caches+mmu": ("--xlen=64 --with-rvm --with-rvc --with-rdtime "
                           f"--with-supervisor {VEXII_CACHES}"),
    # 64+caches+mmu, then atomics         (+ --with-rva). Linux needs A.
    "vexii64+mmu+rva":   ("--xlen=64 --with-rvm --with-rvc --with-rdtime "
                          f"--with-supervisor --with-rva {VEXII_CACHES}"),
    # ... then hardware floating point    (+ --with-rvd, which is F and D at
    # once -- the only two-letter step here, and unavoidable: a distribution
    # built for rv64gc needs both, and --with-rvd adds "f" then "d").
    "vexii64+mmu+rvad":  ("--xlen=64 --with-rvm --with-rvc --with-rdtime "
                          f"--with-supervisor --with-rva --with-rvd "
                          f"{VEXII_CACHES}"),

    # 64+mmu+rva, then two ways instead of one   (--*-l1-ways=2). Not part of
    # the MMU question: it is the block RAM question. The MMU caps L1 sets at
    # 64 (Param.scala:845), so the only way to grow a 4 KiB cache is ways, and
    # ways are what block RAM is spent on. 4 KiB of I-cache is small for a
    # kernel.
    "vexii64+mmu+2way":  ("--xlen=64 --with-rvm --with-rvc --with-rdtime "
                          "--with-supervisor --with-rva "
                          "--with-fetch-l1 --fetch-l1-sets=64 "
                          "--fetch-l1-ways=2 --with-lsu-l1 --lsu-l1-sets=64 "
                          "--lsu-l1-ways=2"),

    # The rows above are a clean lattice but none of them is the CPU
    # this SoC actually contains, which has Wishbone buses, the JTAG debug
    # module and a BTB -- all of which cost area and none of which are in
    # VEXII_BASE. These three are `ecp5-test/riscv/vexii_cpu.py`'s own
    # GENERATE_FLAGS, then the same core widened, then the MMU added. The
    # difference between the first and the last is what swapping the SoC's
    # core for a Linux-capable one would cost, measured rather than inferred
    # by adding up rows that were built from a different base.
    "soc-cpu":           f"--xlen=32 {VEXII_SOC}",
    "soc-cpu+64":        f"--xlen=64 {VEXII_SOC}",
    "soc-cpu+64+mmu":    f"--xlen=64 {VEXII_SOC} --with-supervisor",
}

BUILD_TEMPLATE = """
source "$HOME/opt/oss-cad-suite/environment"
python3.15t - <<'PYEOF'
import sys
sys.path.insert(0, 'ecp5-test')
sys.path.insert(0, 'repos/apollo')

import riscv.cpu_area as cpu_area
cpu_area.VARIANT = {variant!r}

from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4
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
        # These rows are placed and routed, so both columns are nextpnr's
        # packed-slice count; there is no separate yosys LUT4 figure.
        "lut":    count("TRELLIS_COMB"),
        "comb":   count("TRELLIS_COMB"),
        "ff":     count("TRELLIS_FF"),
        "bram":   count("DP16KD"),
        "lutram": count("TRELLIS_RAMW") or 0,
        "fmax":   float(fmax.group(1)) if fmax else None,
        "meets":  fmax.group(2) == "PASS" if fmax else None,
    }


def generate_vexii(name, flags):
    """Run the Scala generator and keep the Verilog it emits.

    Generation is separate from synthesis here, unlike the VexRiscv path where
    the Verilog already exists. The generator has no output-directory option --
    it writes VexiiRiscv.v into its working directory -- so configurations are
    generated one at a time and the result moved aside, rather than run
    concurrently where they would overwrite each other.
    """
    output_dir = ROOT / "ecp5-test" / "riscv" / "matrix" / name
    output_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["sbt", f"runMain vexiiriscv.Generate {flags}"],
        cwd=VEXII_ROOT, capture_output=True, text=True)

    if result.returncode != 0:
        tail = [l for l in result.stdout.splitlines() if l.startswith("[error]")]
        return None, (tail[-1][:70] if tail else "generate failed")

    emitted = VEXII_ROOT / "VexiiRiscv.v"
    if not emitted.exists():
        return None, "no verilog emitted"

    verilog = output_dir / "VexiiRiscv.v"
    verilog.write_bytes(emitted.read_bytes())
    return verilog, None


def inspect_vexii(verilog):
    """Read back what the generator actually built, from the Verilog.

    The flags are not self-evidently effective: `--without-mmu` on a core with
    no supervisor mode is a no-op, and a row whose MMU silently failed to
    appear would read as a free MMU. So each row is checked against the RTL
    rather than against the command line that asked for it.
    """
    text = verilog.read_text()

    # The integer register file's write port is xlen wide.
    xlen = re.search(r"\[(\d+):0\]\s+"
                     r"integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_data",
                     text)

    return {
        "xlen": int(xlen.group(1)) + 1 if xlen else None,
        "mmu":  "MmuPlugin_logic" in text,
        # S-mode brings its own external interrupt line in.
        "sv":   "int_s_external" in text,
        "fpu":  "FpuPackerPlugin" in text or "FpuAddPlugin" in text,
    }


def write_fmax_harness(verilog, path):
    """Wrap the core in a top level small enough to place, for timing.

    A bare VexiiRiscv has around 535 top-level port bits and the LFE5U-25F in
    CABGA381 has 197 usable pins, so the core cannot be placed as its own top
    level -- which is why these rows have read "synth only" until now. This
    writes a harness with four pins: every core input is driven by a flip-flop
    in a shift chain, and every core output is captured by a flip-flop and then
    XOR-reduced in two registered stages down to one pin.

    Both properties matter. Driving inputs from registers rather than constants
    stops synthesis folding the core away, and terminating outputs in registers
    keeps the harness out of the core's paths: what is left between flip-flops
    is the core's own logic. The reduction tree is split into two stages so
    that the harness itself is roughly three LUT levels deep and does not
    become the reported critical path.

    The area of this build is *not* the core's area -- it includes ~500 harness
    flip-flops. Area comes from the bare-core synthesis; only Fmax comes from
    here.
    """
    text = verilog.read_text()
    ports = re.search(r"module VexiiRiscv \((.*?)\n\);", text, re.S).group(1)

    inputs, outputs, clocks = [], [], []
    for line in ports.splitlines():
        match = re.match(r"\s*(input|output)\s+wire\s*(?:\[(\d+):(\d+)\])?\s*"
                         r"(\w+)", line)
        if not match:
            continue
        kind, high, low, signal = match.groups()
        width = int(high) - int(low) + 1 if high is not None else 1
        if signal in ("clk", "reset"):
            continue
        # A second clock -- the JTAG debug module's TCK -- gets a pin of its
        # own rather than a bit of the shift chain. A clock driven by a
        # flip-flop is not a clock as far as timing analysis is concerned, and
        # the whole debug domain would drop out of the report.
        if signal.endswith("tck"):
            clocks.append(signal)
            continue
        (inputs if kind == "input" else outputs).append((signal, width))

    in_bits = sum(w for _, w in inputs)
    out_bits = sum(w for _, w in outputs)

    lines = ["// Generated by scripts/cpu_matrix.py -- timing harness only.",
             "module fmax_top (",
             "  input  wire clk,",
             "  input  wire rst,",
             "  input  wire din,"]
    lines += [f"  input  wire {signal}," for signal in clocks]
    lines += ["  output wire dout",
              ");",
              f"  reg [{in_bits - 1}:0] stim;",
              f"  wire [{out_bits - 1}:0] obs;",
              f"  reg [{out_bits - 1}:0] obs_r;",
              "  always @(posedge clk) stim <= {stim[%d:0], din};"
              % (in_bits - 2),
              "  always @(posedge clk) obs_r <= obs;"]

    # First reduction stage: fixed-size chunks, so its depth does not grow
    # with the design. 16 bits is four LUT4 levels at worst.
    chunk = 16
    stage1 = (out_bits + chunk - 1) // chunk
    lines.append(f"  reg [{stage1 - 1}:0] red;")
    for index in range(stage1):
        high = min((index + 1) * chunk, out_bits) - 1
        lines.append(f"  always @(posedge clk) red[{index}] <= "
                     f"^obs_r[{high}:{index * chunk}];")
    lines.append("  reg dout_r;")
    lines.append(f"  always @(posedge clk) dout_r <= ^red;")
    lines.append("  assign dout = dout_r;")

    lines.append("  VexiiRiscv dut (")
    connections = ["    .clk(clk)", "    .reset(rst)"]
    connections += [f"    .{signal}({signal})" for signal in clocks]
    offset = 0
    for signal, width in inputs:
        slice_ = (f"stim[{offset + width - 1}:{offset}]" if width > 1
                  else f"stim[{offset}]")
        connections.append(f"    .{signal}({slice_})")
        offset += width
    offset = 0
    for signal, width in outputs:
        slice_ = (f"obs[{offset + width - 1}:{offset}]" if width > 1
                  else f"obs[{offset}]")
        connections.append(f"    .{signal}({slice_})")
        offset += width
    lines.append(",\n".join(connections))
    lines.append("  );")
    lines.append("endmodule")

    path.write_text("\n".join(lines) + "\n")


def synth_vexii(name, verilog, want_fmax):
    """Synthesise the bare core for area, then place the harness for Fmax."""
    output_dir = verilog.parent
    started = time.perf_counter()

    # Area: the core alone, to the same device as the VexRiscv rows so the
    # numbers are comparable -- same device, package and speed grade.
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
    (output_dir / "synth.log").write_text(synth.stdout + synth.stderr)

    if synth.returncode != 0:
        return None, time.perf_counter() - started, "synthesis failed"

    # Count from the last "design hierarchy" block, not from the whole log.
    # yosys prints a stat section per module, and a core with the debug module
    # has BufferCC submodules whose sections come first alphabetically -- so a
    # plain search finds a clock-crossing pair's two flip-flops and reports
    # them as the core's flip-flop count.
    totals = synth.stdout.rsplit("=== design hierarchy ===", 1)
    if len(totals) != 2:
        return None, time.perf_counter() - started, "no stat produced"

    def count(cell, text=totals[1]):
        match = re.search(rf"\s+(\d+)\s+{cell}\b", text)
        return int(match.group(1)) if match else 0

    report = {
        "lut":    count("LUT4") + count("CCU2C"),
        "ff":     count("TRELLIS_FF"),
        "bram":   count("DP16KD"),
        # An MMU's TLB is a small asynchronously-read memory, which maps to
        # distributed LUT RAM rather than a block RAM. Counted separately
        # because it is not in the LUT4 figure and is easy to lose.
        "lutram": count("TRELLIS_DPR16X4"),
        "fmax":   None,
        "meets":  None,
    }
    report.update(inspect_vexii(verilog))

    if want_fmax:
        harness = output_dir / "fmax_top.v"
        write_fmax_harness(verilog, harness)

        script = output_dir / "fmax.ys"
        script.write_text(
            f"read_verilog {verilog} {harness}\n"
            f"synth_ecp5 -top fmax_top -json {output_dir}/fmax.json\n")

        pnr = subprocess.run(
            ["bash", "-c",
             f'source "$HOME/opt/oss-cad-suite/environment" && '
             f'yosys {script} > {output_dir}/fmax_synth.log 2>&1 && '
             f'nextpnr-ecp5 --25k --package CABGA381 --speed 8 '
             f'--json {output_dir}/fmax.json --freq 60 --timing-allow-fail '
             f'--lpf-allow-unconstrained --textcfg {output_dir}/fmax.cfg'],
            cwd=ROOT, capture_output=True, text=True)
        (output_dir / "fmax_pnr.log").write_text(pnr.stdout + pnr.stderr)

        # A core with the debug module has two clock domains, and TCK is
        # constrained at 20 MHz and runs at 200 -- taking whichever line
        # printed first would report the JTAG tap's speed as the CPU's.
        pnr_log = pnr.stderr + pnr.stdout

        # nextpnr's own utilisation, which is the figure to compare against a
        # whole-SoC report: TRELLIS_COMB counts packed slices, so it already
        # includes the LUT RAM and counts a carry cell as what it occupies,
        # while the yosys LUT4 column does neither. The harness adds about 40
        # of these and 600 flip-flops, the same in every row, so differences
        # between rows are the core's.
        used = re.search(r"TRELLIS_COMB:\s+(\d+)/", pnr_log)
        if used:
            report["comb"] = int(used.group(1))

        clocks = re.findall(r"Max frequency for clock\s+'([^']*)': ([\d.]+) MHz"
                            r" \((\w+) at ([\d.]+) MHz\)", pnr_log)
        cpu_clock = [c for c in clocks if "tck" not in c[0]] or clocks
        if cpu_clock:
            report["fmax"] = float(cpu_clock[0][1])
            report["meets"] = float(cpu_clock[0][1]) >= 60.0
        elif pnr.returncode != 0:
            # A core too big to place is a result, not an error to hide: it is
            # the answer to whether it fits.
            report["fmax_error"] = ("does not place"
                                    if "Failed to place" in pnr.stderr
                                    else "pnr failed")

    return report, time.perf_counter() - started, None


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
    parser.add_argument("--variants", nargs="*", default=VEXRISCV_VARIANTS)
    parser.add_argument("--skip-vexii", action="store_true",
                        help="VexRiscv rows only; skips the Scala generator")
    parser.add_argument("--vexii-configs", nargs="*", default=None,
                        help="which VexiiRiscv rows to build (default: all)")
    parser.add_argument("--skip-fmax", action="store_true",
                        help="area only; skips place-and-route of the harness")
    args = parser.parse_args()

    configs = {name: flags for name, flags in VEXII_CONFIGS.items()
               if args.vexii_configs is None or name in args.vexii_configs}

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
    # filename into its own tree, so concurrent runs would race. Synthesis and
    # place-and-route afterwards are per-directory and run concurrently.
    if not args.skip_vexii:
        sources = {}
        for name, flags in configs.items():
            verilog, error = generate_vexii(name, flags)
            if error:
                results[name] = (None, 0, error)
                print(f"  generate {name:<20} {error}", flush=True)
            else:
                sources[name] = verilog

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) \
                as pool:
            futures = {pool.submit(synth_vexii, name, verilog,
                                   not args.skip_fmax): name
                       for name, verilog in sources.items()}
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                report, elapsed, error = future.result()
                results[name] = (report, elapsed, error)
                status = error if error else "ok"
                print(f"  built {name:<20} {elapsed:>6.1f}s  {status}",
                      flush=True)

    with LOG.open("w") as handle:
        emit(handle)
        emit(handle, "CPU area and timing, core plus block RAM, nothing else")
        emit(handle)
        emit(handle, f"  {'variant':<21}{'LUT4':>7}{'LUTRAM':>7}{'COMB':>7}"
                     f"{'FF':>7}{'BRAM':>6}{'Fmax':>9}{'closes':>8}  built")

        ordered = list(args.variants)
        if not args.skip_vexii:
            ordered += list(configs)

        for variant in ordered:
            report, elapsed, error = results.get(variant, (None, 0, "not run"))
            if error:
                emit(handle, f"  {variant:<21}  {error}")
                continue
            if report is None:
                emit(handle, f"  {variant:<21}  no timing report produced")
                continue

            if report["fmax"] is not None:
                timing = (f"{report['fmax']:>8.1f}M"
                          f"{('yes' if report['meets'] else 'no'):>8}")
            else:
                # Said explicitly rather than left blank, so the gap is not
                # mistaken for a failure.
                timing = f"{report.get('fmax_error', 'no timing'):>17}"

            # What the RTL turned out to contain, not what was asked for.
            built = ""
            if "xlen" in report:
                built = f"rv{report['xlen']}"
                built += "".join(part for part, on in
                                 (("+s", report["sv"]), ("+mmu", report["mmu"]),
                                  ("+fpu", report["fpu"])) if on)

            comb = report.get("comb")
            emit(handle, f"  {variant:<21}{report['lut']:>7}"
                         f"{report['lutram']:>7}"
                         f"{(comb if comb is not None else '-'):>7}"
                         f"{report['ff']:>7}"
                         f"{report['bram']:>6}{timing}  {built}")

        emit(handle)
        emit(handle, "Block RAM counts are low because a CPU with no firmware "
                     "never drives its")
        emit(handle, "bus, so synthesis prunes most of the attached memory. "
                     "The LUT figures")
        emit(handle, "reflect the core; these are not complete systems.")
        emit(handle)
        emit(handle, "LUTRAM is TRELLIS_DPR16X4, distributed RAM. It is not "
                     "part of the LUT4")
        emit(handle, "count and it is where an MMU's TLB lands, so an MMU row "
                     "read on LUT4")
        emit(handle, "alone understates itself. Each cell occupies a slice, "
                     "which is two LUT4")
        emit(handle, "sites; the die has 24288 of those and 56 DP16KD.")
        emit(handle)
        emit(handle, "COMB is nextpnr's packed-slice count for the same "
                     "design: LUT4 + carry +")
        emit(handle, "LUT RAM, in the unit a whole-SoC utilisation report "
                     "uses, and the only")
        emit(handle, "column that can be compared with one. It carries about "
                     "40 cells of")
        emit(handle, "harness, identically in every row. On the VexRiscv rows "
                     "LUT4 and COMB")
        emit(handle, "are the same number, because those rows are placed "
                     "rather than synthesised.")
        emit(handle)
        emit(handle, "The 'built' column is read back out of the generated "
                     "Verilog, not from")
        emit(handle, "the flags: --without-mmu is a no-op without supervisor "
                     "mode, so a row")
        emit(handle, "could otherwise claim an MMU it never got.")
        emit(handle)
        emit(handle, "VexiiRiscv Fmax comes from placing the core in a "
                     "four-pin timing harness")
        emit(handle, "(flip-flops on every port), because the bare core has "
                     "535 port bits and")
        emit(handle, "the package has 197 pins. Comparable between VexiiRiscv "
                     "rows; the")
        emit(handle, "VexRiscv rows are measured inside a platform with a PLL "
                     "and are not.")
        emit(handle)
        emit(handle, "CoreMark is not here. It needs firmware on the core, "
                     "which needs the")
        emit(handle, "CPU brought up first -- see the riscv bring-up issue.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
