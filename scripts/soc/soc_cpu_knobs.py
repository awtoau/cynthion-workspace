#!/usr/bin/env python3
# Synthesis-only cell counts of the VexiiRiscv core, one soc_cpu_arms arm each.
# SPDX-License-Identifier: BSD-3-Clause

"""Generate each arm's core and run `synth_ecp5` on the core module alone.

    ./scripts/soc/soc_cpu_knobs.py base no-btb ref      # arms from soc_cpu_arms.py

- Cells only: no place-and-route, no timing.
- Mapper noise is ~200 COMB (docs/soc-size-review.md); smaller deltas mean nothing.
- Log: ./tmp/logs/soc_cpu_knobs.log
"""

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "soc"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
import soc_cpu_arms  # noqa: E402

OUT = ROOT / "tmp" / "cpu-knobs"
LOG = ROOT / "tmp" / "logs" / "soc_cpu_knobs.log"
CELLS = ("LUT4", "PFUMX", "L6MUX21", "CCU2C", "TRELLIS_FF", "DP16KD",
         "TRELLIS_DPR16X4", "MULT18X18D")


def synth(name):
    verilog = OUT / f"{name}.v"
    log = OUT / f"{name}.yosys.log"
    subprocess.run(["yosys", "-q", "-l", str(log), "-p",
                    f"read_verilog -sv {verilog}; synth_ecp5 -top VexiiRiscv; stat"],
                   check=True, stdout=subprocess.DEVNULL)
    text = log.read_text().split("design hierarchy ===")[-1]
    cells = {c: int(m.group(1)) for c in CELLS
             if (m := re.search(rf"^\s*(\d+)\s+{c}\b", text.split("cells")[-1], re.M))}
    # COMB model per soc_module_area.py: LUT4 + PFUMX + L6MUX21 + CCU2C.
    cells["COMB"] = sum(cells.get(c, 0) for c in ("LUT4", "PFUMX", "L6MUX21", "CCU2C"))
    return cells


def main(names):
    import cpu.cpu as vexii
    import top
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    pool, jobs = ThreadPoolExecutor(4), {}
    reuse = "--reuse" in names
    names = [n for n in names if n != "--reuse"]
    for name in names:
        if reuse:
            jobs[name] = pool.submit(synth, name)
            continue
        original = list(vexii.GENERATE_FLAGS)
        try:
            vexii.GENERATE_FLAGS[:] = soc_cpu_arms.flags_for(name)
            arm = soc_cpu_arms.ARMS[name]
            vexii.generate(0, cache_sets=arm.get("sets", top.CACHE_SETS),
                           cache_ways=arm.get("ways", top.CACHE_WAYS),
                           output=OUT / f"{name}.v")
        finally:
            vexii.GENERATE_FLAGS[:] = original
        jobs[name] = pool.submit(synth, name)
        print(f"generated {name}", flush=True)
    with LOG.open("a") as log:
        for name, job in jobs.items():
            row = f"{name:<24} " + " ".join(f"{c}={v}" for c, v in job.result().items())
            print(row, flush=True)
            log.write(row + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
