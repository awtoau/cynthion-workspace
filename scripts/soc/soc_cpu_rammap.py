#!/usr/bin/env python3
# Which VexiiRiscv memories map to distributed RAM, and what a block-RAM mapping costs.
# SPDX-License-Identifier: BSD-3-Clause

"""Core-alone `synth_ecp5` with `ram_style` forced per memory.

    ./scripts/soc/soc_cpu_rammap.py CORE.v                 # baseline
    ./scripts/soc/soc_cpu_rammap.py CORE.v regfile cache_tags csr   # one run per name
    ./scripts/soc/soc_cpu_rammap.py CORE.v all

- Cells only, core alone (not the whole SoC); no place-and-route.
- Names are keys of `MEMORIES`; `all` forces every one.
- Log: ./tmp/logs/soc_cpu_rammap.log
"""

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "tmp" / "ram"
LOG = ROOT / "tmp" / "logs" / "soc_cpu_rammap.log"
CELLS = ("TRELLIS_DPR16X4", "DP16KD", "PDPW16KD", "LUT4", "PFUMX", "L6MUX21", "CCU2C",
         "TRELLIS_FF")
# Same synthesis options as the SoC build (soc_run.py TOOLCHAINS["yosys070"]).
SYNTH = "synth_ecp5 -top VexiiRiscv -nowidelut -cmp2softlogic"

# name -> regex of the memory declarations in the generated Verilog it forces.
MEMORIES = {
    "regfile":    r"asMem_ram",
    "fetch_tags": r"FetchL1Plugin_logic_ways_\d+_mem",
    "lsu_tags":   r"LsuL1Plugin_logic_ways_\d+_mem",
    "lsu_shared": r"LsuL1Plugin_logic_shared_mem",
    "victim":     r"LsuL1Plugin_logic_writeback_victimBuffer",
    "csr":        r"CsrRamPlugin_logic_mem",
    "progbuf":    r"logic_progbufX_mem",
}


def force(source, names, style="block"):
    """Verilog text with `(* ram_style = style *)` on the named memories."""
    for name in names:
        pattern = re.compile(rf"^(\s*)(?:\(\*[^)]*\*\)\s*)?(reg \[\d+:0\] {MEMORIES[name]} \[0:\d+\];)",
                             re.M)
        source, count = pattern.subn(rf'\1(* ram_style = "{style}" *) \2', source)
        if not count:
            raise SystemExit(f"{name}: no memory declaration matched")
    return source


def run(source, label, names):
    verilog = OUT / f"{label}.forced.v"
    verilog.write_text(force(source, names))
    log = OUT / f"{label}.yosys.log"
    subprocess.run(["yosys", "-q", "-l", str(log), "-p",
                    f"read_verilog -sv {verilog}; {SYNTH}; stat"],
                   check=True, stdout=subprocess.DEVNULL)
    text = log.read_text().split("design hierarchy ===")[-1].split("cells")[-1]
    cells = {c: int(m.group(1)) for c in CELLS
             if (m := re.search(rf"^\s*(\d+)\s+{c}\b", text, re.M))}
    cells["COMB"] = sum(cells.get(c, 0) for c in ("LUT4", "PFUMX", "L6MUX21", "CCU2C"))
    mapped = re.findall(r"^mapping memory VexiiRiscv\.(\S+) via (\S+)", log.read_text(), re.M)
    return cells, mapped


def main(args):
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    source = Path(args[0]).read_text()
    runs = {"base": []}
    for arg in args[1:]:
        runs[arg] = list(MEMORIES) if arg == "all" else arg.split("+")
    with ThreadPoolExecutor(4) as pool:
        jobs = {label: pool.submit(run, source, label, names) for label, names in runs.items()}
    with LOG.open("a") as log:
        for label, job in jobs.items():
            cells, mapped = job.result()
            row = f"{label:<24} " + " ".join(f"{c}={v}" for c, v in cells.items())
            print(row, flush=True)
            log.write(row + "\n")
            for memory, kind in mapped:
                print(f"    {memory:<60} {kind}")


if __name__ == "__main__":
    main(sys.argv[1:])
