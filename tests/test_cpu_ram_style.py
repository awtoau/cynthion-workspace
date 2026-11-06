"""The VexiiRiscv memories listed in `ramstyle.BLOCK` reach the netlist as block RAM."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateware"))

from soc.cpu import ramstyle  # noqa: E402

# The shape the generator emits for the register file: 1 write, 2 sync reads with enable.
REGFILE = """
module t(input clk, input we, input [4:0] wa, input [31:0] wd,
         input re0, input [4:0] ra0, output reg [31:0] rd0,
         input re1, input [4:0] ra1, output reg [31:0] rd1);
  reg [31:0] asMem_ram [0:31];
  always @(posedge clk) if (we) asMem_ram[wa] <= wd;
  always @(posedge clk) if (re0) rd0 <= asMem_ram[ra0];
  always @(posedge clk) if (re1) rd1 <= asMem_ram[ra1];
endmodule
"""

DECLARATION = "  reg [31:0] asMem_ram [0:31];\n"

# Expected ~1 s; a stall on the shared box is a hang.
YOSYS_LIMIT_S = 30


def test_declaration_gets_the_attribute():
    text = ramstyle.force_block(DECLARATION, [r"asMem_ram"])
    assert text == '  (* ram_style = "block" *) reg [31:0] asMem_ram [0:31];\n'


def test_existing_attribute_is_replaced_not_stacked():
    declared = '  (* ram_style = "distributed" *) reg [31:0] asMem_ram [0:31];\n'
    text = ramstyle.force_block(declared, [r"asMem_ram"])
    assert text.count("ram_style") == 1 and '"block"' in text


def test_applying_twice_changes_nothing():
    once = ramstyle.force_block(DECLARATION, [r"asMem_ram"])
    assert ramstyle.force_block(once, [r"asMem_ram"]) == once


def test_missing_memory_is_an_error():
    with pytest.raises(ramstyle.MissingMemory, match="asMem_ram"):
        ramstyle.force_block("module t; endmodule\n", [r"asMem_ram"])


def test_attribute_makes_the_regfile_shape_block_ram(tmp_path):
    yosys = shutil.which("yosys")
    if not yosys:
        pytest.skip("yosys not available")
    (tmp_path / "t.v").write_text(ramstyle.force_block(REGFILE, [r"asMem_ram"]))
    done = subprocess.run(
        [yosys, "-p", "read_verilog t.v; synth_ecp5 -top t -nowidelut -cmp2softlogic; stat"],
        cwd=tmp_path, capture_output=True, text=True, timeout=YOSYS_LIMIT_S)
    assert done.returncode == 0, done.stderr[-500:]
    assert re.search(r"^\s*\d+\s+DP16KD\b", done.stdout, re.M)
    assert "TRELLIS_DPR16X4" not in done.stdout.split("design hierarchy ===")[-1]
