#!/usr/bin/env python3
#
# Wrap a bare VexiiRiscv core in real memory so it can be placed and routed.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds a synthesisable top level around a generated VexiiRiscv core.

A bare core cannot be placed: its instruction and data buses appear as ~29
top-level ports, and nextpnr tries to allocate a pin for every bit. The
CABGA256 package does not have them, so place-and-route fails before it starts.

The archived sweep worked around this with a wrapper that tied every core
output to an unconnected wire and drove the instruction bus with a constant
`32'h00000013` -- a `nop`. That places, but it does not measure a CPU:

  * every output path drives nothing, so synthesis deletes it as dead logic
    (the generator log reports "567 signals were pruned")

KNOWN DEFECT, unfixed: the input-driving logic below recognises only the
`FetchCachelessPlugin_*` and `LsuCachelessPlugin_*` port names. A core
generated WITH caches names them `FetchL1Plugin_*` and `LsuL1Plugin_*`, which
fall through to the catch-all and are tied to zero -- `cmd_ready` included, so
the core can never complete a fetch and the pipeline is pruned exactly as the
tie-off wrapper this file replaces did.

That invalidated all 66 cached `core_dev` rows in the 2026-07-29 sweep. The
tell is in the data: cached cores measured 676-3185 LUT against 3954-4072 for
cacheless ones, and a core with caches cannot be smaller than one without. The
`microsoc_direct` rows are unaffected -- they are real SoCs and do not use this
wrapper.
  * the instruction bus never misses and never stalls, because it answers
    every request in the same cycle with the same word
  * the data bus responses are constants, so load-use paths fold away

The resulting area and Fmax describe whatever survived that pruning. This
wrapper instead attaches block RAM to both buses and answers them the way a
real memory does -- one cycle of latency, `ready` deasserted while busy -- so
the timing paths that decide Fmax are present and the logic that drives them
is not optimised out.

It is still not a full SoC: no peripherals, no interrupt sources, no debug
module. It is a core plus its memory, which is what makes core rows comparable
with each other. The MicroSoC rows remain the ones to quote for a real system.

    ./scripts/riscv_core_wrapper.py --rtl VexiiRiscv.v --out wrap.v
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "riscv_core_wrapper.log"

# Words of instruction and data memory. 4096 words is 16 KiB, which is four
# DP16KD blocks per bus -- enough that the memory is real block RAM rather than
# distributed LUT RAM, and small enough to leave the device mostly free for the
# core being measured.
MEM_WORDS = 4096


def parse_ports(text):
    """Pull the port list out of the generated module header."""
    match = re.search(r"module\s+VexiiRiscv\s*\((.*?)\);", text, flags=re.S)
    if not match:
        raise RuntimeError("could not find the VexiiRiscv module header")

    header = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    ports = []
    for raw in header.splitlines():
        line = raw.split("//", 1)[0].strip().rstrip(",")
        if not line:
            continue
        parsed = re.match(
            r"^(input|output)\s+(?:wire|reg)?\s*(\[[^\]]+\])?\s*"
            r"([A-Za-z0-9_]+)$", line)
        if parsed:
            ports.append((parsed.group(1), parsed.group(2) or "",
                          parsed.group(3)))
    return ports


def width_of(spec):
    """Bit width from a `[msb:lsb]` declaration."""
    if not spec:
        return 1
    match = re.match(r"\[(\d+)\s*:\s*(\d+)\]", spec.strip())
    if not match:
        return 1
    return abs(int(match.group(1)) - int(match.group(2))) + 1


def build_wrapper(ports, mem_words):
    """Emit a top level with block RAM on both buses.

    Only `clk` and `reset` become pins. Everything else is driven by, or drives,
    real logic inside the wrapper.
    """
    names = {name for _, _, name in ports}
    has_fetch = any(n.startswith("FetchCachelessPlugin") for n in names)
    has_lsu = any(n.startswith("LsuCachelessPlugin") for n in names)

    # A core generated with caches names its buses differently and splits the
    # LSU into separate read and write channels. Handling only the cacheless
    # names ties L1 `cmd_ready` low through the catch-all, so the core can
    # never complete a fetch and synthesis prunes the pipeline -- which is
    # what invalidated 66 rows of the first sweep.
    has_fetch_l1 = any(n.startswith("FetchL1Plugin") for n in names)
    has_lsu_l1 = any(n.startswith("LsuL1Plugin") for n in names)

    # The fetch L1 bus returns a whole cache line, so its response is wider
    # than a word and arrives over several beats.
    fetch_l1_width = next(
        (width_of(w) for d, w, n in ports
         if n == "FetchL1Plugin_logic_bus_rsp_payload_data"), 32)

    lines = [
        "`timescale 1ns/1ps",
        "",
        "// Generated by scripts/riscv_core_wrapper.py -- do not edit.",
        "//",
        "// A VexiiRiscv core with block RAM on its instruction and data buses.",
        "// Exists so the core can be placed and routed for area and timing;",
        "// see the script for why a tie-off wrapper cannot measure either.",
        "",
        "module VexiiRiscvWrap(",
        "  input wire clk,",
        "  input wire reset",
        ");",
        "",
        f"  localparam MEM_WORDS = {mem_words};",
        "  localparam ADDR_BITS = $clog2(MEM_WORDS);",
        "",
        "  // rdtime is a real free-running counter: the CSR read path is a",
        "  // timing path, and tying it to a constant would delete it.",
        "  reg [63:0] rdtime_counter = 64'd0;",
        "  always @(posedge clk) rdtime_counter <= rdtime_counter + 1'b1;",
        "",
    ]

    # Declare every core-facing signal as an internal wire or reg.
    declared = []
    for direction, width, name in ports:
        if name in ("clk", "reset"):
            continue
        spec = f"{width} " if width else ""
        keyword = "wire" if direction == "output" else "reg"
        declared.append((direction, width, name, keyword))
        lines.append(f"  {keyword} {spec}{name};")

    lines.append("")

    if has_fetch:
        lines += [
            "  // ---- instruction memory ----------------------------------",
            "  // Initialised to `addi x0, x0, 0` so the core fetches a valid",
            "  // stream. Written by nothing, but inferred as block RAM.",
            "  reg [31:0] imem [0:MEM_WORDS-1];",
            "  integer i;",
            "  initial for (i = 0; i < MEM_WORDS; i = i + 1)",
            "    imem[i] = 32'h00000013;",
            "",
            "  reg [31:0] imem_word;",
            "  always @(posedge clk) begin",
            "    imem_word <= imem[FetchCachelessPlugin_logic_bus_cmd_payload"
            "_address[ADDR_BITS+1:2]];",
            "  end",
            "",
            "  // One cycle of latency, and `ready` follows the core's own",
            "  // request rather than being tied high -- so the handshake is a",
            "  // real path.",
            "  reg fetch_pending;",
            "  always @(posedge clk) begin",
            "    if (reset) fetch_pending <= 1'b0;",
            "    else fetch_pending <=",
            "      FetchCachelessPlugin_logic_bus_cmd_valid &&",
            "      FetchCachelessPlugin_logic_bus_cmd_ready;",
            "  end",
            "",
        ]

    if has_fetch_l1:
        lines += [
            "  // ---- instruction memory, cache-line fills -----------------",
            "  // A cached core asks for a whole line, so the response is wider",
            "  // than a word and arrives over several beats. `rsp_valid` is",
            "  // held until the core accepts each one.",
            "  reg [31:0] il1mem [0:MEM_WORDS-1];",
            "  integer fi;",
            "  initial for (fi = 0; fi < MEM_WORDS; fi = fi + 1)",
            "    il1mem[fi] = 32'h00000013;",
            "",
            f"  localparam FETCH_BEATS = {max(1, fetch_l1_width // 32)};",
            "  reg [$clog2(FETCH_BEATS+1)-1:0] fetch_beat;",
            "  reg fetch_l1_active;",
            f"  reg [{fetch_l1_width - 1}:0] fetch_l1_data;",
            "  reg [ADDR_BITS-1:0] fetch_l1_word;",
            "",
            "  always @(posedge clk) begin",
            "    if (reset) begin",
            "      fetch_l1_active <= 1'b0;",
            "      fetch_beat <= 0;",
            "    end else if (!fetch_l1_active) begin",
            "      if (FetchL1Plugin_logic_bus_cmd_valid) begin",
            "        fetch_l1_active <= 1'b1;",
            "        fetch_beat <= 0;",
            "        fetch_l1_word <=",
            "          FetchL1Plugin_logic_bus_cmd_payload_address"
            "[ADDR_BITS+1:2];",
            "      end",
            "    end else if (FetchL1Plugin_logic_bus_rsp_ready) begin",
            "      // Read a fresh word per beat so the memory stays in the",
            "      // timing path rather than being read once and replayed.",
            "      fetch_l1_word <= fetch_l1_word + 1'b1;",
            "      if (fetch_beat + 1 == FETCH_BEATS) begin",
            "        fetch_l1_active <= 1'b0;",
            "        fetch_beat <= 0;",
            "      end else begin",
            "        fetch_beat <= fetch_beat + 1'b1;",
            "      end",
            "    end",
            "  end",
            "",
            "  integer fb;",
            "  always @(posedge clk) begin",
            "    for (fb = 0; fb < FETCH_BEATS; fb = fb + 1)",
            "      fetch_l1_data[fb*32 +: 32] <= il1mem[fetch_l1_word + fb];",
            "  end",
            "",
        ]

    if has_lsu_l1:
        lines += [
            "  // ---- data memory, cached core -----------------------------",
            "  // The cached LSU has separate read and write channels; writes",
            "  // are a burst terminated by `last`.",
            "  reg [31:0] dl1mem [0:MEM_WORDS-1];",
            "  integer di;",
            "  initial for (di = 0; di < MEM_WORDS; di = di + 1) dl1mem[di] = 0;",
            "",
            "  reg [31:0] dl1_rdata;",
            "  reg dl1_read_active;",
            "  always @(posedge clk) begin",
            "    if (reset) dl1_read_active <= 1'b0;",
            "    else if (LsuL1Plugin_logic_bus_read_cmd_valid &&",
            "             !dl1_read_active) dl1_read_active <= 1'b1;",
            "    else if (LsuL1Plugin_logic_bus_read_rsp_ready)",
            "      dl1_read_active <= 1'b0;",
            "    dl1_rdata <= dl1mem[LsuL1Plugin_logic_bus_read_cmd_payload"
            "_address[ADDR_BITS+1:2]];",
            "  end",
            "",
            "  // Writes are accepted every cycle and actually stored, so the",
            "  // store path is exercised rather than optimised away.",
            "  reg [ADDR_BITS-1:0] dl1_waddr;",
            "  always @(posedge clk) begin",
            "    if (LsuL1Plugin_logic_bus_write_cmd_valid) begin",
            "      dl1mem[LsuL1Plugin_logic_bus_write_cmd_payload_fragment"
            "_address[ADDR_BITS+1:2]] <=",
            "        LsuL1Plugin_logic_bus_write_cmd_payload_fragment_data;",
            "    end",
            "  end",
            "",
            "  reg dl1_write_rsp;",
            "  always @(posedge clk) begin",
            "    if (reset) dl1_write_rsp <= 1'b0;",
            "    else dl1_write_rsp <= LsuL1Plugin_logic_bus_write_cmd_valid &&",
            "                          LsuL1Plugin_logic_bus_write_cmd_payload"
            "_last;",
            "  end",
            "",
        ]

    if has_lsu:
        lines += [
            "  // ---- data memory -----------------------------------------",
            "  // Written as well as read, so the store path is not pruned.",
            "  reg [31:0] dmem [0:MEM_WORDS-1];",
            "  integer j;",
            "  initial for (j = 0; j < MEM_WORDS; j = j + 1) dmem[j] = 32'd0;",
            "",
            "  reg [31:0] dmem_word;",
            "  wire [ADDR_BITS-1:0] dmem_addr =",
            "    LsuCachelessPlugin_logic_bus_cmd_payload_address"
            "[ADDR_BITS+1:2];",
            "",
            "  always @(posedge clk) begin",
            "    if (LsuCachelessPlugin_logic_bus_cmd_valid &&",
            "        LsuCachelessPlugin_logic_bus_cmd_ready &&",
            "        LsuCachelessPlugin_logic_bus_cmd_payload_write) begin",
            "      // Byte lanes honoured, so the mask logic is exercised.",
            "      if (LsuCachelessPlugin_logic_bus_cmd_payload_mask[0])",
            "        dmem[dmem_addr][7:0]   <=",
            "          LsuCachelessPlugin_logic_bus_cmd_payload_data[7:0];",
            "      if (LsuCachelessPlugin_logic_bus_cmd_payload_mask[1])",
            "        dmem[dmem_addr][15:8]  <=",
            "          LsuCachelessPlugin_logic_bus_cmd_payload_data[15:8];",
            "      if (LsuCachelessPlugin_logic_bus_cmd_payload_mask[2])",
            "        dmem[dmem_addr][23:16] <=",
            "          LsuCachelessPlugin_logic_bus_cmd_payload_data[23:16];",
            "      if (LsuCachelessPlugin_logic_bus_cmd_payload_mask[3])",
            "        dmem[dmem_addr][31:24] <=",
            "          LsuCachelessPlugin_logic_bus_cmd_payload_data[31:24];",
            "    end",
            "    dmem_word <= dmem[dmem_addr];",
            "  end",
            "",
            "  reg lsu_pending;",
            "  always @(posedge clk) begin",
            "    if (reset) lsu_pending <= 1'b0;",
            "    else lsu_pending <=",
            "      LsuCachelessPlugin_logic_bus_cmd_valid &&",
            "      LsuCachelessPlugin_logic_bus_cmd_ready;",
            "  end",
            "",
        ]

    # Drive the core's inputs from the memories above.
    lines.append("  always @(*) begin")
    for direction, width, name, _ in declared:
        if direction != "input":
            continue
        bits = width_of(width)
        zero = "1'b0" if bits == 1 else f"{bits}'b0"

        # Cached core: the L1 buses. These must come before the catch-all --
        # falling through to it ties cmd_ready low, and a core that can never
        # complete a fetch is pruned to nothing.
        if name == "FetchL1Plugin_logic_bus_cmd_ready":
            value = "!fetch_l1_active"
        elif name == "FetchL1Plugin_logic_bus_rsp_valid":
            value = "fetch_l1_active"
        elif name == "FetchL1Plugin_logic_bus_rsp_payload_data":
            value = "fetch_l1_data"
        elif name == "LsuL1Plugin_logic_bus_read_cmd_ready":
            value = "!dl1_read_active"
        elif name == "LsuL1Plugin_logic_bus_read_rsp_valid":
            value = "dl1_read_active"
        elif name == "LsuL1Plugin_logic_bus_read_rsp_payload_data":
            value = "dl1_rdata"
        elif name == "LsuL1Plugin_logic_bus_write_cmd_ready":
            # Writes are always accepted; back-pressure here would need a
            # queue, and the point is to keep the store path alive.
            value = "1'b1"
        elif name == "LsuL1Plugin_logic_bus_write_rsp_valid":
            value = "dl1_write_rsp"
        elif name.endswith("FetchCachelessPlugin_logic_bus_cmd_ready"):
            value = "!fetch_pending"
        elif name.endswith("FetchCachelessPlugin_logic_bus_rsp_valid"):
            value = "fetch_pending"
        elif name.endswith("FetchCachelessPlugin_logic_bus_rsp_payload_word"):
            value = "imem_word"
        elif name.endswith("LsuCachelessPlugin_logic_bus_cmd_ready"):
            value = "!lsu_pending"
        elif name.endswith("LsuCachelessPlugin_logic_bus_rsp_valid"):
            value = "lsu_pending"
        elif name.endswith("LsuCachelessPlugin_logic_bus_rsp_payload_data"):
            value = "dmem_word" if bits == 32 else f"{{2{{dmem_word}}}}"
        elif name.endswith("_rsp_payload_id"):
            peer = name.replace("_rsp_payload_id", "_cmd_payload_id")
            value = peer if peer in names else zero
        elif name.endswith("_rsp_payload_error"):
            value = "1'b0"
        elif "PrivilegedPlugin_logic_rdtime" in name:
            value = ("rdtime_counter" if bits == 64
                     else f"rdtime_counter[{bits - 1}:0]")
        elif "_int_" in name:
            # Interrupts held low: an always-asserted interrupt would trap
            # every cycle and change what the timing paths look like.
            value = "1'b0"
        else:
            value = zero

        lines.append(f"    {name} = {value};")
    lines.append("  end")
    lines.append("")

    # Instantiate the core.
    lines.append("  VexiiRiscv core (")
    connections = [f"    .{name}({name})" for _, _, name in ports]
    lines.append(",\n".join(connections))
    lines.append("  );")
    lines.append("")
    lines.append("endmodule")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rtl", type=Path, required=True,
                        help="generated VexiiRiscv.v")
    parser.add_argument("--out", type=Path, required=True,
                        help="wrapper to write")
    parser.add_argument("--mem-words", type=int, default=MEM_WORDS)
    args = parser.parse_args()

    text = args.rtl.read_text(errors="replace")
    ports = parse_ports(text)
    wrapper = build_wrapper(ports, args.mem_words)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(wrapper)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(f"{args.rtl} -> {args.out}: {len(ports)} ports, "
                     f"{args.mem_words} words\n")

    print(f"wrote {args.out} ({len(ports)} core ports, "
          f"{args.mem_words}-word memories)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
