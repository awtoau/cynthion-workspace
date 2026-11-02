#!/usr/bin/env python3
#
# Module-level cell counts for the capture codecs (yosys synth_ecp5, no place and route).
# SPDX-License-Identifier: BSD-3-Clause

"""Cells only: LUT4 / CCU2C / FF / DP16KD / LUTRAM for the LZ4 codec modules.

    python3 scripts/soc/capture_codec_area.py

Each configuration is the module(s) alone behind plain ports, `synth_ecp5`, `stat`.
No timing is reported. Output: terminal and `tmp/logs/capture_codec_area.log`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

import _paths  # noqa: F401,E402
from devlog import emit  # noqa: E402

BUILD = ROOT / "tmp" / "capture-codec-area"
CELLS = ("LUT4", "CCU2C", "TRELLIS_FF", "DP16KD", "TRELLIS_DPR16X4", "PFUMX", "L6MUX21")


def build_il(config, path):
    from amaranth import Elaboratable, Module
    from amaranth.back import rtlil

    from capture.lz4_compress import LZ4Compressor
    from capture.lz4_decompress import LZ4Decompressor

    window, which = config

    class Top(Elaboratable):
        def __init__(self):
            self.parts = []
            if which == "framer":
                from capture.framer import CaptureRing
                self.parts.append(("f", CaptureRing()))
            if which == "path":
                from capture.path import CapturePath
                self.parts.append(("p", CapturePath(window=window)))
            if which in ("compress", "both"):
                self.parts.append(("c", LZ4Compressor(window=window)))
            if which in ("decompress", "both"):
                self.parts.append(("d", LZ4Decompressor(window=window)))

        def elaborate(self, platform):
            m = Module()
            for name, part in self.parts:
                m.submodules[name] = part
            return m

    top = Top()
    ports = []
    for name, part in top.parts:
        for _, member, value in part.signature.flatten(part):
            ports.append(value)
    path.write_text(rtlil.convert(top, name="top", ports=ports))


def run(config):
    window, which = config
    BUILD.mkdir(parents=True, exist_ok=True)
    tag = f"{which}-w{window}"
    il = BUILD / f"{tag}.il"
    build_il(config, il)
    script = BUILD / f"{tag}.ys"
    stat = BUILD / f"{tag}-stat.txt"
    script.write_text(f"read_rtlil {il.name}\nsynth_ecp5 -top top\ntee -o {stat.name} stat\n")
    proc = subprocess.run(["yosys", "-q", script.name], cwd=BUILD, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-3000:])
    counts = {}
    for line in stat.read_text().splitlines():
        found = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if found and found.group(2) in CELLS:
            counts[found.group(2)] = int(found.group(1))
    return counts


def main():
    emit("capture codec cells (yosys synth_ecp5, module alone; no timing)")
    emit(f"  {'config':<22} {'LUT4':>6} {'CCU2C':>6} {'FF':>6} {'DP16KD':>7} {'LUTRAM(DPR16X4)':>16}")
    for window in (1024, 256):
        for which in ("compress", "decompress", "both"):
            c = run((window, which))
            emit(f"  {which + ' w' + str(window):<22} {c.get('LUT4', 0):>6} {c.get('CCU2C', 0):>6} "
                 f"{c.get('TRELLIS_FF', 0):>6} {c.get('DP16KD', 0):>7} {c.get('TRELLIS_DPR16X4', 0):>16}")
    for which in ("framer", "path"):
        c = run((1024, which))
        emit(f"  {which + ' w1024':<22} {c.get('LUT4', 0):>6} {c.get('CCU2C', 0):>6} "
             f"{c.get('TRELLIS_FF', 0):>6} {c.get('DP16KD', 0):>7} {c.get('TRELLIS_DPR16X4', 0):>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
