#!/usr/bin/env python3
#
# Generate an SVD and a Rust PAC from the SoC's own memory map.
# SPDX-License-Identifier: BSD-3-Clause

"""
Emits `soc.svd` from the Amaranth SoC, then a Peripheral Access Crate from that.

## Why generate rather than hand-write

The register map is not written down anywhere today -- it is *implied* by the
`csr.Builder` calls in the gateware. Firmware currently rediscovers it by hand:
`riscv_firmware.py` hardcodes `CONSOLE_BASE 0xf0000000` with offsets `+0` and `+1`,
and its SPI section carries the comment *"byte offsets from luna_soc's csr.Builder
layout"* -- a human reading gateware and transcribing addresses.

That is the class of error that cost most of a day: firmware sent `0x9f << 24` because
a comment asserted the PHY did not left-justify, when it does. The hardware and the
comment disagreed and nothing could catch it.

A generated PAC cannot drift. Move a register and code referencing it fails to
compile; add a peripheral and it appears. It is also how `moondancer-pac` was built,
so this is the same road, not a parallel one.

## The chain

    Amaranth SoC  ->  luna_soc.generate.svd  ->  soc.svd  ->  svd2rust  ->  PAC

`svd2rust` and `form` come from cargo. SVD is the ARM-standard register description
format, so nothing here is bespoke.

## What it does NOT do

It does not touch the FPGA and does not build a bitstream. It elaborates the SoC far
enough to read its memory map, which needs no toolchain.

    ./scripts/soc_generate_pac.py            # svd + crate
    ./scripts/soc_generate_pac.py --svd-only
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_generate_pac.log"
OUT = ROOT / "firmware" / "cynthion-soc-pac"

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))


def build_soc():
    """Elaborate the SoC far enough to expose its memory map.

    Imports rather than builds: the memory map is decided during elaboration, so no
    synthesis or place-and-route is involved and no board is touched.
    """
    import vexii_hello_soc

    firmware = [0] * 16  # contents are irrelevant; only the map is read
    return vexii_hello_soc.HelloSoC(firmware=firmware)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--svd-only", action="store_true",
                        help="stop after writing soc.svd")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("Generating a PAC from the SoC's own memory map")
        emit()

        soc = build_soc()

        # The SoC must expose a memory map and an interrupt map for the generator.
        # Which attributes carry them differs between SoC shapes, so find them rather
        # than assume -- a wrong guess here produces an empty SVD, which looks like a
        # working run.
        memory_map = getattr(soc, "memory_map", None)
        if memory_map is None:
            for attr in ("wb_decoder", "decoder", "bus"):
                candidate = getattr(soc, attr, None)
                if candidate is not None and hasattr(candidate, "bus"):
                    memory_map = candidate.bus.memory_map
                    break
                if candidate is not None and hasattr(candidate, "memory_map"):
                    memory_map = candidate.memory_map
                    break
        if memory_map is None:
            emit("could not find a memory map on the SoC.")
            emit("The generator needs one; check what the SoC exposes.")
            return 1

        emit("memory map found. resources:")
        for res in memory_map.all_resources():
            name = "_".join(str(p) for p in res.path[0]) if res.path else "?"
            emit(f"  {name:<24} 0x{res.start:08x} .. 0x{res.end:08x}")
        emit()

        from luna_soc.generate import svd as svd_gen

        interrupts = getattr(soc, "interrupts", {})
        svd_path = OUT / "soc.svd"
        with svd_path.open("w") as out:
            svd_gen.SVD(memory_map, interrupts).generate(
                file=out, vendor="awtoau", name="cynthion_soc",
                description="Cynthion r1.4 RISC-V SoC")
        emit(f"wrote {svd_path.relative_to(ROOT)} ({svd_path.stat().st_size} bytes)")

        if args.svd_only:
            emit("stopping after the SVD, as asked")
            return 0

        # svd2rust emits into the working directory, so run it there.
        result = subprocess.run(
            ["svd2rust", "-i", "soc.svd", "--target", "riscv"],
            cwd=OUT, capture_output=True, text=True)
        if result.returncode != 0:
            emit("svd2rust failed:")
            emit((result.stderr or result.stdout).strip()[-800:])
            return 1
        emit("svd2rust ok")

        # `form` splits the single generated lib.rs into a module tree, which is what
        # makes the result readable and is what moondancer-pac does.
        src = OUT / "src"
        if (OUT / "lib.rs").exists():
            src.mkdir(exist_ok=True)
            result = subprocess.run(
                ["form", "-i", "lib.rs", "-o", "src/"],
                cwd=OUT, capture_output=True, text=True)
            emit(f"form -> rc={result.returncode}")

        emit()
        emit(f"crate skeleton in {OUT.relative_to(ROOT)}")
        emit("still needed: Cargo.toml, build.rs, and a device.x linker fragment")
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
