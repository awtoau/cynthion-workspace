#!/usr/bin/env python3
#
# The CPU configurations `soc_occupancy_timing.py` samples as arms.
# SPDX-License-Identifier: BSD-3-Clause

"""One CPU configuration per arm, applied at elaboration.

The core is generated from Scala on every build (`gateware/soc/cpu/cpu.py`), so
its configuration is a sweepable axis rather than a fixed input. Each arm here
names one change to `GENERATE_FLAGS` or to `top.CACHE_SETS`/`CACHE_WAYS`; the
arm is a NETLIST, and `soc_occupancy_timing.py` samples the placement
distribution of that netlist over nextpnr seeds.

    import soc_cpu_arms
    soc_cpu_arms.apply("pc0")      # what an arm snippet runs

    ./scripts/soc_cpu_arms.py list      # the arms and what each changes
    ./scripts/soc_cpu_arms.py digest    # every arm's generated Verilog, hashed

`digest` is the check that the axis is real: two arms whose generated core is
byte-identical are one arm reported twice, and nothing else in the pipeline
would say so. It generates each arm's core and compares SHA-256.

## Reading a result

- One build a side settles nothing here. The placement distribution at FIXED
  occupancy on this design is 9 MHz wide (#467), so an arm is a distribution
  over seeds and a paired delta against `base`, or it is noise.
- Cell counts are not conserved under a source change (#467): a change that
  adds carry chains can cut LUT4s. Read the arm's own netlist, do not subtract.
"""

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each arm: `flags` replaces the value after a generator option, `drop` removes
# an option (and its value if `takes_value`), `add` appends, `sets`/`ways` move
# `top.py`'s cache geometry. `base` changes nothing.
ARMS = {
    "base": {},

    # PERFORMANCE COUNTERS. `PerformanceCounterPlugin_logic_ignoreNextCommit`
    # appears in the net names on the measured `clk` critical path, which runs
    # entirely inside TrapPlugin's FSM. Whether the counters are what widens it
    # is a measurement.
    #
    # `--performance-counters 0` keeps the plugin (mcycle + minstret) with no
    # additional counters; dropping the option removes zicntr/zihpm from the
    # ISA, and `withPerformanceCounters` is that ISA check
    # (`Param.scala:590,1174`), so the plugin is not instantiated at all.
    #
    # DROPPING THE OPTION IS NOT REMOVAL, and `digest` is what said so:
    # `--performance-counters` dropped generates a core byte-identical to `pc0`.
    # `--with-rdtime` adds zicntr (`Param.scala:802`) and
    # `withPerformanceCounters` is `zihpm || zicntr` (`:590`), so rdtime and the
    # plugin are ONE switch. `pc-none` therefore drops rdtime too, and with it
    # the `PrivilegedPlugin_logic_rdtime` port -- diagnostic only, since the SoC
    # requires `rdtime` and the CLINT's `mtime` to be one counter (`cpu.py`).
    "pc0": {"flags": {"--performance-counters": "0"}},
    "pc2": {"flags": {"--performance-counters": "2"}},
    "pc8": {"flags": {"--performance-counters": "8"}},
    "pc-none": {"drop": [("--performance-counters", True),
                         ("--with-rdtime", False)],
                "drop_ports": ["i_PrivilegedPlugin_logic_rdtime"]},

    # THE DEBUG MODULE, which is the other tenant of the trap FSM: a halt
    # request enters through TrapPlugin. Diagnostic, not a proposal -- the SoC
    # needs the debug tap (`cpu.py`: a CPU that stopped printing is otherwise
    # indistinguishable from one that stopped running).
    "dbg-none": {"drop": [("--debug-jtag-instruction", False)],
                 "drop_ports": [
                     "i_EmbeddedRiscvJtag_logic_jtagInstruction_tck",
                     "i_EmbeddedRiscvJtag_logic_jtagInstruction_tdi",
                     "i_EmbeddedRiscvJtag_logic_jtagInstruction_enable",
                     "i_EmbeddedRiscvJtag_logic_jtagInstruction_capture",
                     "i_EmbeddedRiscvJtag_logic_jtagInstruction_shift",
                     "i_EmbeddedRiscvJtag_logic_jtagInstruction_update",
                     "i_EmbeddedRiscvJtag_logic_jtagInstruction_reset",
                     "o_EmbeddedRiscvJtag_logic_jtagInstruction_tdo",
                     "i_EmbeddedRiscvJtag_logic_debug_reset"]},

    # L1 GEOMETRY. `base` is `top.py`'s 64x2. 3 ways does not exist: SpinalHDL's
    # PLRU asserts isPow2 on the way count.
    "l1-64x1": {"sets": 64, "ways": 1},
    "l1-128x1": {"sets": 128, "ways": 1},
    "l1-256x1": {"sets": 256, "ways": 1},
    "l1-128x2": {"sets": 128, "ways": 2},
    "l1-32x4": {"sets": 32, "ways": 4},
    "l1-32x2": {"sets": 32, "ways": 2},

    # The two caches moved independently, which one `CACHE_SETS` cannot express:
    # whether the cost is the fetch side or the load/store side. `sets`/`ways`
    # None leaves `generate()`'s substitution off, so these flags stand.
    "l1-fetch64x2-lsu128x1": {"sets": None, "ways": None,
                              "flags": {"--fetch-l1-sets": "64",
                                        "--fetch-l1-ways": "2",
                                        "--lsu-l1-sets": "128",
                                        "--lsu-l1-ways": "1"}},
    "l1-fetch128x1-lsu64x2": {"sets": None, "ways": None,
                              "flags": {"--fetch-l1-sets": "128",
                                        "--fetch-l1-ways": "1",
                                        "--lsu-l1-sets": "64",
                                        "--lsu-l1-ways": "2"}},

    # PIPELINE RELAXATIONS. Each moves work into a later stage: a cycle of
    # latency for a shorter combinational path. `--relaxed-btb` and
    # `--relaxed-branch` are already on in `base`.
    "relaxed-src-shift": {"add": ["--relaxed-src", "--relaxed-shift"]},
    "relaxed-all": {"add": ["--relaxed-src", "--relaxed-shift", "--relaxed-div",
                            "--relaxed-mul-inputs", "--relaxed-btb-hit"]},

    # BTB size. 512 sets is `Param.scala`'s default and `base`.
    "btb-128": {"add": ["--btb-sets", "128"]},

    # NO `buffers` ARM. `--with-aligner-buffer --with-dispatcher-buffer`
    # generates a core byte-identical to `base`: `--with-rvc` already forces the
    # aligner buffer on (`Param.scala:618`), and `withDispatcherBuffer` appears
    # nowhere in this checkout outside `Param.scala` -- it names a config and
    # builds nothing. `digest` is what said so; the flags differ, the core does
    # not.

    # The register file as registers instead of dual-port block RAM. Trades
    # block RAM for LUTs and takes the RAM's clock-to-out off the read path.
    # `--regfile-async` comes with it, not optionally: `RegFileMem.scala:76`
    # asserts `!syncRead` on the register-based path, and sync is the default.
    "regfile-registers": {"add": ["--regfile-reg-based", "--regfile-async"]},

    # An ALU in a later stage, which also turns the bypass network on
    # (`allowBypassFrom` 100 = disabled by default, 0 with late ALU).
    "late-alu": {"add": ["--with-late-alu"]},

    # The tag read taken out of the same cycle as the compare, both caches.
    "tags-async": {"add": ["--fetch-l1-tags-read-async",
                           "--lsu-l1-tags-read-async"]},
}


def spec(name):
    if name not in ARMS:
        raise SystemExit(f"no such CPU arm: {name}; `list` shows them")
    return ARMS[name]


def flags_for(name):
    """The generator flag list this arm produces, without importing anything
    that elaborates."""
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    from cpu.cpu import GENERATE_FLAGS

    arm = spec(name)
    flags = list(GENERATE_FLAGS)
    for option, value in arm.get("flags", {}).items():
        if option not in flags:
            raise SystemExit(f"{name}: {option} is not in GENERATE_FLAGS")
        flags[flags.index(option) + 1] = value
    for option, takes_value in arm.get("drop", []):
        if option not in flags:
            raise SystemExit(f"{name}: {option} is not in GENERATE_FLAGS")
        at = flags.index(option)
        del flags[at:at + (2 if takes_value else 1)]
    flags += list(arm.get("add", []))
    return flags


def apply(name):
    """Patch the CPU configuration in this process. Call before elaboration."""
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    import cpu.cpu as vexii
    import top

    # In place: `generate()` copies `GENERATE_FLAGS` when it is called, which is
    # during elaboration, so rebinding the name here would not be seen.
    vexii.GENERATE_FLAGS[:] = flags_for(name)

    arm = spec(name)
    if "sets" in arm:
        top.CACHE_SETS = arm["sets"]
    if "ways" in arm:
        top.CACHE_WAYS = arm["ways"]

    # A port that a dropped plugin no longer has. yosys rejects the connection
    # rather than ignoring it, so the arm has to drop the wire as well as the
    # flag.
    if arm.get("drop_ports"):
        real = vexii.Instance

        def instance(kind, *args, **kwargs):
            for port in arm["drop_ports"]:
                kwargs.pop(port, None)
            return real(kind, *args, **kwargs)

        vexii.Instance = instance
    return name


# What `soc_occupancy_timing.ARMS` stores for each arm: run in the elaborating
# subprocess, before `import top`.
SNIPPET = "import soc_cpu_arms\nsoc_cpu_arms.apply({name!r})\n"


def snippets():
    return {name: SNIPPET.format(name=name) for name in ARMS}


def describe(name):
    arm = spec(name)
    parts = []
    for option, value in arm.get("flags", {}).items():
        parts.append(f"{option} {value}")
    for option, _takes in arm.get("drop", []):
        parts.append(f"no {option}")
    if arm.get("add"):
        parts.append(" ".join(arm["add"]))
    if arm.get("sets") or arm.get("ways"):
        parts.append(f"cache {arm.get('sets', '-')}x{arm.get('ways', '-')}")
    return ", ".join(parts) or "the shipping configuration"


def digest(names, out_dir):
    """Generate each arm's core and hash it. Distinct arms must differ."""
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    import cpu.cpu as vexii
    import top

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen = {}
    for name in names:
        original = list(vexii.GENERATE_FLAGS)
        try:
            vexii.GENERATE_FLAGS[:] = flags_for(name)
            path = vexii.generate(
                0x00000000,
                cache_sets=ARMS[name].get("sets", top.CACHE_SETS),
                cache_ways=ARMS[name].get("ways", top.CACHE_WAYS),
                output=out_dir / f"{name}.v")
        finally:
            vexii.GENERATE_FLAGS[:] = original
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        clash = seen.get(sha)
        print(f"  {name:<24} {sha[:16]}  {Path(path).stat().st_size:>9} B"
              + (f"  IDENTICAL TO {clash}" if clash else ""))
        seen.setdefault(sha, name)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("list", "flags", "digest"))
    parser.add_argument("--arm", action="append", default=[])
    parser.add_argument("--out", default=ROOT / "tmp" / "cpu-arms")
    args = parser.parse_args()

    names = args.arm or list(ARMS)
    if args.stage == "list":
        for name in names:
            print(f"  {name:<24} {describe(name)}")
        return 0
    if args.stage == "flags":
        for name in names:
            print(f"{name}: {' '.join(flags_for(name))}")
        return 0
    return digest(names, args.out)


if __name__ == "__main__":
    sys.exit(main())
