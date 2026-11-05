"""trellis_ff_without_m.v drops M and adds no other connection (CE tied to 0 froze flip-flops on the board)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TECHMAP = Path(os.environ.get("TRELLIS_FF_TECHMAP", REPO / "scripts" / "yosys" / "trellis_ff_without_m.v"))
NEW_SUITE_YOSYS = Path.home() / "opt" / "oss-cad-suite-20261104" / "oss-cad-suite" / "bin" / "yosys"

# One async-reset flip-flop with no enable (CEMUX "1": CE not connected) and one with an enable.
DESIGN = """
module t(input clk, input rst_n, input d, input ce, output reg q_plain, output reg q_ce);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q_plain <= 1'b0; else q_plain <= d;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q_ce <= 1'b0; else if (ce) q_ce <= d;
endmodule
"""

# Expected ~1 s; a stall on the shared box is a hang.
YOSYS_LIMIT_S = 30

YOSYS = [pytest.param(shutil.which("yosys"), id="yosys-on-PATH"),
         pytest.param(str(NEW_SUITE_YOSYS), id="yosys-0.70")]


@pytest.mark.parametrize("yosys", YOSYS)
def test_m_is_gone_and_ce_matches_cemux(yosys, tmp_path):
    if not yosys or not Path(yosys).exists():
        pytest.skip("yosys not available")
    (tmp_path / "t.v").write_text(DESIGN)
    subprocess.run([yosys, "-q", "-p",
                    f"read_verilog t.v; synth_ecp5 -top t; techmap -max_iter 1 -map {TECHMAP}; "
                    "write_json t.json"],
                   cwd=tmp_path, check=True, capture_output=True, timeout=YOSYS_LIMIT_S)
    cells = json.loads((tmp_path / "t.json").read_text())["modules"]["t"]["cells"]
    ffs = [c for c in cells.values() if c["type"] == "TRELLIS_FF"]
    assert len(ffs) == 2
    for ff in ffs:
        assert "M" not in ff["connections"], ff["connections"]
        cemux = ff["parameters"]["CEMUX"].strip()
        # CE is connected exactly when the enable is used; a constant on it freezes the register.
        assert ("CE" in ff["connections"]) == (cemux in ("CE", "INV")), (cemux, ff["connections"])
