"""The build's yosys flow skips `autoname` and keeps the `check` label's passes (#602)."""

from __future__ import annotations

import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import fast_build_env  # noqa: E402

# A distributed RAM (async read -> DPR16X4C) is the case that broke when the check
# label was skipped without replaying its passes: "Module DPR16X4C contains processes".
DESIGN = """
module top(input clk, input [3:0] a, input [3:0] d, input we,
           output [3:0] q, output reg [3:0] cnt);
  reg [3:0] mem [0:15];
  always @(posedge clk) begin
    if (we) mem[a] <= d;
    cnt <= cnt + 1;
  end
  assign q = mem[a] ^ cnt;
endmodule
"""

# Expected ~1 s for this design; a stall on the shared box is a hang, not a slow run.
YOSYS_LIMIT_S = 20

needs_yosys = pytest.mark.skipif(shutil.which("yosys") is None, reason="no yosys on PATH")


def _synth(tmp_path, name, opts, after):
    (tmp_path / "top.v").write_text(DESIGN)
    script = tmp_path / f"{name}.ys"
    script.write_text(
        "read_verilog top.v\n"
        f"synth_ecp5 {opts} -top top\n"
        f"{after}\n"
        f"write_json {name}.json\n"
    )
    log = tmp_path / f"{name}.log"
    done = subprocess.run(["yosys", "-q", "-l", str(log), str(script)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=YOSYS_LIMIT_S)
    assert done.returncode == 0, done.stderr + log.read_text()[-1500:]
    cells = json.loads((tmp_path / f"{name}.json").read_text())["modules"]["top"]["cells"]
    return log.read_text(), collections.Counter(c["type"] for c in cells.values())


def test_both_overrides_are_in_the_environment_the_builders_inherit():
    assert fast_build_env.OVERRIDES["AMARANTH_synth_opts"]
    assert fast_build_env.OVERRIDES["AMARANTH_script_after_synth"]


@needs_yosys
def test_autoname_is_skipped_and_the_netlist_is_the_same(tmp_path):
    opts = fast_build_env.OVERRIDES["AMARANTH_synth_opts"]
    after = fast_build_env.OVERRIDES["AMARANTH_script_after_synth"]
    stock_log, stock = _synth(tmp_path, "stock", "", "")
    fast_log, fast = _synth(tmp_path, "fast", opts, after)
    assert "Executing AUTONAME" in stock_log
    assert "Executing AUTONAME" not in fast_log
    assert stock == fast and stock["TRELLIS_DPR16X4"] > 0


@needs_yosys
def test_the_check_labels_passes_still_run(tmp_path):
    opts = fast_build_env.OVERRIDES["AMARANTH_synth_opts"]
    after = fast_build_env.OVERRIDES["AMARANTH_script_after_synth"]
    log, _ = _synth(tmp_path, "fast", opts, after)
    # `blackbox` writes no header line, so it is covered by the build still
    # succeeding on a distributed RAM (test above), not by the log. `stat` feeds
    # the pre-pack LUT/FF counts the build records (top.rpt); without it they read 0.
    for executed in ("Executing HIERARCHY", "Executing CHECK", "Printing statistics"):
        assert executed in log, executed
