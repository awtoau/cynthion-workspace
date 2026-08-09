#!/usr/bin/env python3
#
# What the resident runtime displaces in a 4 KiB direct-mapped I-cache, modelled.
# SPDX-License-Identifier: BSD-3-Clause

"""Drive a model of the SoC's I-cache from a QEMU execution trace.

Issue #115 asks what 1,552 resident bytes cost in a 4 KiB, direct-mapped,
one-way I-cache under a real load rather than an idle one. The CPU's own
`mhpmcounter` answer that (`ICACHE_MISS 0x11`, `docs/riscv-core-build.md`) and
they are on the board. This is the board-free substitute, and it is a **model**:

  * the instruction stream is QEMU's, which is the same program but a different
    machine -- no speculative fetch, no prefetch, no branch predictor
  * the addresses are the QEMU build's, linked at 0x80000000 into `virt`'s DRAM,
    not the board's, linked into the flash window at 0x100B0000. `.text` differs
    by about 5 KB between the two, so the SET a given function lands in differs
  * a translation block is assumed to execute end to end. QEMU takes interrupts
    between blocks, so the only blocks that exit early are the faulting ones,
    and this firmware takes no faults

What it IS exact about: the cache geometry, the line sequence each block
touches, and the order blocks executed in.

    ./scripts/soc_icache_model.py tmp/logs/trace-workload.log
    ./scripts/soc_icache_model.py --from-symbol tmp/logs/trace-preempt.log

Geometry from `gateware/soc/cpu/cpu.py`'s `GENERATE_FLAGS`:
`--fetch-l1-sets 128 --fetch-l1-ways 1`, 8 KiB total, so a 64-byte line.

Output is mirrored to ./tmp/logs/soc_icache_model.log.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_icache_model.log"

# 64 sets, 1 way, 4 KiB -> 64 bytes a line. See the docstring.
SETS = 64
WAYS = 1
LINE = 64

TRACE_RE = re.compile(r"^Trace \d+: 0x[0-9a-f]+ \[[0-9a-f]+/0*([0-9a-f]+)/")
INSN_RE = re.compile(r"^0x([0-9a-f]{8}):\s+([0-9a-f]{4,8})\s")


def parse(path):
    """Return (block extents keyed by pc, the execution sequence of pcs).

    A block's extent is [first instruction address, last address + its width),
    taken from the `IN:` disassembly QEMU emits once per translation. Blocks are
    keyed by their start pc and the widest extent seen wins -- a block that is
    flushed and retranslated is the same code.
    """
    extent = {}
    order = []
    current = None
    with path.open(errors="replace") as fh:
        for line in fh:
            if line.startswith("Trace "):
                match = TRACE_RE.match(line)
                if match:
                    order.append(int(match.group(1), 16))
                current = None
                continue
            if line.startswith("IN:"):
                current = []
                continue
            if current is not None:
                match = INSN_RE.match(line)
                if match:
                    address = int(match.group(1), 16)
                    width = len(match.group(2)) // 2
                    current.append((address, width))
                elif current:
                    start = current[0][0]
                    end = current[-1][0] + current[-1][1]
                    if extent.get(start, (0, 0))[1] < end:
                        extent[start] = (start, end)
                    current = None
    return extent, order


def lines_of(extent):
    """Each block's cache lines, in the order it touches them."""
    table = {}
    for pc, (start, end) in extent.items():
        first = start // LINE
        last = (end - 1) // LINE
        table[pc] = list(range(first, last + 1))
    return table


def model(order, table):
    """Replay the line sequence through a direct-mapped cache."""
    tags = [None] * SETS
    accesses = 0
    misses = 0
    unknown = 0
    for pc in order:
        seq = table.get(pc)
        if seq is None:
            unknown += 1
            continue
        for line in seq:
            index = line % SETS
            tag = line // SETS
            accesses += 1
            if tags[index] != tag:
                misses += 1
                tags[index] = tag
    return accesses, misses, unknown


def footprint(order, table):
    """Distinct lines touched, and how many sets are contended."""
    seen = set()
    for pc in order:
        for line in table.get(pc, ()):
            seen.add(line)
    per_set = {}
    for line in seen:
        per_set.setdefault(line % SETS, set()).add(line // SETS)
    return seen, per_set


def symbol(elf, needle):
    """The address of the first `.text` symbol whose name contains `needle`."""
    import subprocess
    out = subprocess.run(["rust-objdump", "-t", str(elf)],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if needle in line and ".text" in line:
            return int(line.split()[0], 16)
    return None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace", type=Path)
    ap.add_argument("--skip", type=float, default=0.0,
                    help="drop this fraction of the trace from the front, to "
                         "leave the boot out of the steady state")
    ap.add_argument("--elf", type=Path,
                    help="resolve --start-symbol against this ELF")
    ap.add_argument("--start-symbol", default="workload6source3arm",
                    help="window the trace from the first execution of the "
                         "block at this symbol; the default is the workload's "
                         "`source::arm`, which the `usb` command calls once "
                         "before anything else it does")
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = LOG.open("a")

    def say(line=""):
        print(line)
        out.write(line + "\n")
        out.flush()

    extent, order = parse(args.trace)
    if not order:
        say(f"no `Trace` lines in {args.trace} -- was -d exec,nochain passed?")
        return 1
    if args.elf is not None:
        start = symbol(args.elf, args.start_symbol)
        if start is None:
            say(f"no symbol matching {args.start_symbol} in {args.elf}")
            return 1
        try:
            first = order.index(start)
        except ValueError:
            say(f"{args.start_symbol} at {start:#x} never executed")
            return 1
        say(f"# windowed from {args.start_symbol} at {start:#x}: "
            f"dropped {first} of {len(order)} block executions (the boot)")
        order = order[first:]
    if args.skip:
        order = order[int(len(order) * args.skip):]

    table = lines_of(extent)
    accesses, misses, unknown = model(order, table)
    seen, per_set = footprint(order, table)
    contended = sum(1 for tags in per_set.values() if len(tags) > 1)
    conflicts = sum(len(tags) - 1 for tags in per_set.values())

    say(f"\n## {args.trace.name}")
    say(f"  blocks      {len(extent)} translated, {len(order)} executed"
        f"{f', {unknown} with no disassembly' if unknown else ''}")
    say(f"  cache       {SETS} sets x {WAYS} way x {LINE} B = "
        f"{SETS * WAYS * LINE // 1024} KiB")
    say(f"  accesses    {accesses}")
    say(f"  misses      {misses}  ({100.0 * misses / max(accesses, 1):.2f}%)")
    say(f"  footprint   {len(seen)} distinct lines = {len(seen) * LINE} B "
        f"over {len(per_set)} of {SETS} sets")
    say(f"  contention  {contended} sets hold more than one line, "
        f"{conflicts} evicting pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
