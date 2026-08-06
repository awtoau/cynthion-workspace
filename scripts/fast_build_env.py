#!/usr/bin/env python3
#
# Toolchain flags that make every FPGA build faster, for free.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sets the Amaranth toolchain overrides that cut build time, and explains each one.

Import this before building, or `source` the shell form it prints. Amaranth reads
`AMARANTH_synth_opts` and `AMARANTH_nextpnr_opts` from the environment and splices them
into the generated build script, so **nothing is patched** -- these are documented
override hooks.

## Measured, on `gateware/soc/top.py`

    baseline                        70 s
    with nextpnr flags              59 s      and Fmax 72.6 -> 80.65 MHz

Faster *and* better placed. The design was verified on hardware afterwards: identical
console output, `jedec 00ef4016`, same read values, same benchmark cycle count.

## Why `--threads` alone does nothing

This is the trap. nextpnr's ECP5 placer is already `heap`, and the time splits:

    HeAP placement       3.7 s
    SA refinement       16.0 s     <- serial unless --parallel-refine
    router1              8.7 s

`--threads` configures the thread count for passes that support it. Without
`--parallel-refine`, none of the expensive ones do -- so passing `--threads 31` on its own
changes nothing at all, which is exactly what was measured before this existed.

`--parallel-refine` alone is faster still but drops Fmax; adding `--router router2`
recovers it. The two seconds that costs are worth paying.

Note it caps itself at 16 threads regardless of what `--threads` says, and 8 gives nearly
the same result. **31 cores are not the binding constraint** -- this design is too small to
use them. Do not expect more cores to help.

## The synthesis flag, and why it is NOT enabled by default

`synth_ecp5 -run :check` skips `autoname`, which profiling showed is **27.9% of yosys
time** -- 8.68 s renaming `$abc$1234$` nets into readable ones, purely cosmetic, running
after all real synthesis is complete.

It is not enabled here because skipping the `check` label also skips `hierarchy -check`,
`check -noinit` and `blackbox =A:whitebox`, which must then be replayed by hand or the
build fails with `Module DPR16X4C contains processes`. Amaranth's `synth_opts` appends to
one command, so it cannot express "run this label but not that pass". Getting it needs a
custom build script rather than an override, and the cost is unreadable net names in the
timing report -- which matters precisely when reading a critical path.

`fpga_flow_bench.py` measured it, and was retired once it had; recover it from git if
that trade becomes worthwhile again.

    ./scripts/fast_build_env.py            # print the shell exports
    python3 -c "import fast_build_env"     # or set them in-process
"""

import os
import sys

# nextpnr flags, in the order they matter:
#
#   --parallel-refine   the actual win -- parallelises the SA refinement pass that runs
#                       after HeAP placement, which is 16 of nextpnr's 24 seconds
#   --threads 31        gives that pass threads to use; inert on its own
#   --router router2    recovers the Fmax that --parallel-refine alone gives up
NEXTPNR_OPTS = "--parallel-refine --threads 31 --router router2"

OVERRIDES = {
    "AMARANTH_nextpnr_opts": NEXTPNR_OPTS,
}


def apply():
    """Set the overrides in this process. Returns what was set."""
    os.environ.update(OVERRIDES)
    return dict(OVERRIDES)


# Applied on import, so `import fast_build_env` before a build is enough.
apply()


if __name__ == "__main__":
    for key, value in OVERRIDES.items():
        print(f'export {key}="{value}"')
    print(f"# then build as usual; measured 70s -> 59s with Fmax 72.6 -> 80.65 MHz",
          file=sys.stderr)
