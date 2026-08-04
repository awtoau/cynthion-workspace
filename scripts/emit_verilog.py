#!/usr/bin/env python3
"""Emit Verilog and constraints from an Amaranth design, for the Diamond comparison.

Amaranth normally drives the whole open flow itself: it elaborates, writes
RTLIL, and shells out to yosys/nextpnr/ecppack. Diamond cannot read RTLIL, so
the comparison needs the design stopped one step earlier, at Verilog, plus the
.lpf that carries the pin assignment.

The platform's build() with do_build=False produces exactly that build plan --
every file the flow would have used -- without running any of the tools. That
is better than re-elaborating by hand, because it keeps the platform's own
clock-domain and pin handling, so the design Diamond sees is the design the
open flow sees.

Two Verilog forms come out of this, and they answer different questions:

  behavioural  Amaranth RTLIL -> yosys write_verilog with no synthesis.
               Diamond's LSE then does its own synthesis. This compares
               whole toolchains.

  structural   Amaranth RTLIL -> yosys synth_ecp5 -> write_verilog.
               Already mapped to ECP5 primitives, so Diamond only places and
               routes it. This isolates place-and-route.

    ./scripts/emit_verilog.py --design analyzer --outdir tmp/diamond/analyzer
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402


def emit_from_rtlil(il_path, outdir, yosys="yosys", extra_v=()):
    """Produce both Verilog forms from an existing Amaranth .il file.

    Reusing the .il that the open-flow build already wrote guarantees the two
    toolchains start from byte-identical RTL. Re-elaborating the Amaranth
    design would risk a different result from a different library version and
    quietly invalidate the comparison.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    behav = outdir / "behavioural.v"
    struct = outdir / "structural.v"

    # Some designs instantiate a pre-generated core as a separate Verilog file
    # rather than elaborating it -- vexii_hello does this with VexiiRiscv.v,
    # and its Amaranth-generated top.ys reads that file before the RTLIL.
    # Without it, synthesis stops with
    #   ERROR: Module `\VexiiRiscv' ... is not part of the design.
    # The order matters: the module must be defined before the RTLIL that
    # instantiates it is read.
    reads = "".join(f"read_verilog {v}\n" for v in extra_v)

    # Behavioural: read the RTLIL and write Verilog without synthesising.
    #
    # `proc` is needed because raw RTLIL keeps processes, which write_verilog
    # cannot express. `memory_collect` then leaves memories as $mem_v2 cells,
    # which write_verilog renders as ordinary Verilog reg arrays -- 18 of them
    # for the analyzer -- which is the form a vendor synthesiser infers block
    # RAM from.
    #
    # `memory_map` must NOT be used here, even though it also produces reg
    # arrays. It lowers each memory into explicit flip-flops and address
    # decode logic, and LSE then faithfully builds that out of fabric: the
    # analyzer came back with 78348 LUT4 and 87892 register bits on a part
    # with 24288 LUTs and 12687 register bits, and zero DP16KD. That is not a
    # measurement of anything, it is a destroyed design.
    #
    # DP16KD count is the check that this stayed honest. If a Diamond LSE run
    # reports 0 block RAMs where the open flow used 9, the memories did not
    # survive the handoff and the utilisation numbers must be discarded rather
    # than compared.
    ys1 = outdir / "emit_behavioural.ys"
    ys1.write_text(
        f"{reads}read_rtlil {il_path}\n"
        "proc\n"
        "opt_clean\n"
        "memory_collect\n"
        f"write_verilog -noattr {behav}\n")
    emit(f"yosys -> {behav}")
    p = subprocess.run([yosys, "-q", "-s", str(ys1)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        emit("behavioural emit failed")
        emit((p.stdout + p.stderr)[-3000:])
        behav = None

    # Structural: full ECP5 synthesis, then the mapped netlist in two forms.
    # This is the netlist nextpnr would have placed, handed to Diamond's
    # place-and-route instead -- the experiment that separates synthesis from
    # PAR.
    #
    # EDIF rather than Verilog is what Diamond can actually ingest here:
    # ngdbuild only reads .ngo/.edif, and feeding structural Verilog back
    # through LSE would let LSE re-synthesise it, defeating the point. The
    # Verilog is still written because it is far easier to read when checking
    # which primitives yosys chose.
    #
    # `flatten` plus an explicit delete of $scopeinfo is required before
    # write_edif. Flattening leaves behind one $scopeinfo cell per original
    # module boundary -- 246 of them in the analyzer -- and write_edif emits
    # an instance for each referencing a cell it never declares:
    #
    #   ERROR - edif2ngd: Reference to an unknown cell id00013.
    #
    # $scopeinfo is a debug-only annotation that records where hierarchy used
    # to be; it has no ports and no logic, so deleting it changes nothing
    # electrically. See docs/upstream-yosys-write-edif-hierarchy.md -- the
    # real fix is for write_edif to skip these itself.
    edif = outdir / "structural.edf"
    ys2 = outdir / "emit_structural.ys"
    ys2.write_text(
        f"{reads}read_rtlil {il_path}\n"
        "synth_ecp5 -top top\n"
        f"write_verilog -noattr {struct}\n"
        "flatten\n"
        "delete t:$scopeinfo\n"
        # Diamond's NGD model has no vector pin. yosys writes a bus port as
        # `(port (array d 8) ...)`, which edif2ngd expands into eight pins
        # that all keep the base name:
        #
        #   ERROR - edif2ngd: block '': multiple pins named 'd'
        #
        # `splitnets -ports` turns it into eight scalar ports named "d[7]"
        # .. "d[0]" instead, which edif2ngd accepts -- and those names are
        # exactly what the .lpf already uses for pin assignment, so the
        # constraints keep matching.
        "splitnets -ports\n"
        "opt_clean\n"
        # No -attrprop. It attaches yosys attributes as EDIF properties,
        # including an `hdlname` that is identical across every bit of a bus.
        # edif2ngd takes hdlname as the pin name, so an 8-bit inout bus
        # becomes eight pins with one name:
        #
        #   ERROR - edif2ngd: block '': multiple pins named
        #   'aux_phy_0__data__io'
        #
        # The properties are for human readability, not connectivity, so
        # dropping them costs nothing here.
        f"write_edif {edif}\n")
    emit(f"yosys synth_ecp5 -> {struct}, {edif}")
    p = subprocess.run([yosys, "-q", "-s", str(ys2)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        emit("structural emit failed")
        emit((p.stdout + p.stderr)[-3000:])
        struct = None
        edif = None

    return behav, struct, edif


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--il", required=True, type=Path,
                    help="Amaranth-emitted top.il from an open-flow build")
    ap.add_argument("--outdir", required=True, type=Path)
    ap.add_argument("--name", default="emit_verilog",
                    help="label for this run; the sweep gives each "
                         "frequency its own so the shared log can be "
                         "read back per configuration")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--extra-verilog", type=Path, nargs="*", default=[],
                    help="Verilog read before the RTLIL, eg a pre-generated "
                         "CPU core the design instantiates")
    args = ap.parse_args()

    emit(f"{args.name}: rtlil {args.il}")
    behav, struct, edif = emit_from_rtlil(args.il, args.outdir,
                                          args.yosys, args.extra_verilog)
    for label, path in (("behavioural", behav), ("structural", struct),
                        ("edif", edif)):
        if path and path.exists():
            size = path.stat().st_size
            lines = sum(1 for _ in path.open(errors="replace"))
            emit(f"{label}: {path} ({size} bytes, {lines} lines)")
        else:
            emit(f"{label}: FAILED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
