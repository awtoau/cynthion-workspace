#!/usr/bin/env python3
"""Where two firmware images differ in `.text`, symbol by symbol.

`.text` is this design's binding constraint: code executes from SPI flash
through an 8 KiB two-way I-cache, and #245 measured +1,700 bytes moving frontend
stalls from 44 to 452 per 1,000 cycles. So "it grew by N bytes" is only half an
answer -- the other half is which symbols, and whether they are code someone
asked for or a formatting path something dragged in.

`readelf -S` gives the totals; this gives the attribution. It reads the symbol
table of two ELFs and reports, per symbol, what appeared, what vanished and what
changed size, rolled up by crate.

It builds nothing. Point it at two ELFs you already have:

    ./scripts/soc_size_delta.py tmp/elf/before.elf tmp/elf/after.elf
    ./scripts/soc_size_delta.py before.elf after.elf --top 40

Written for #171 -- pricing `embedded-cli` as the shell's line editor -- where
the first measurement was +9,448 bytes and this said 2,632 of them were
`core::str::slice_error_fail` and `<char as Debug>::fmt`, reached from ONE `str`
range index in the autocompletion. That is the kind of thing a total cannot tell
you.

Results go to `tmp/logs/soc_size_delta.log` as well as the terminal.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_size_delta.log"

# `llvm-nm` demangles Rust v0 symbols; GNU `nm` does not. Both are usually
# present, so try the one that answers usefully first rather than making the
# caller care.
NM_CANDIDATES = ("llvm-nm", "rust-nm", "nm")

# Which crate a demangled symbol belongs to. The first path component of a Rust
# symbol is its crate; anything else -- `MachineExternal`, `memcpy` -- is C ABI
# and gets grouped under its own heading so it does not vanish into "other".
SYMBOL = re.compile(r"^(?:<)?([A-Za-z_][A-Za-z0-9_]*)")


def emit(line: str, handle) -> None:
    print(line)
    handle.write(line + "\n")


def nm_tool() -> str:
    for tool in NM_CANDIDATES:
        try:
            subprocess.run([tool, "--version"], capture_output=True, check=True)
            return tool
        except (OSError, subprocess.CalledProcessError):
            continue
    sys.exit(f"none of {', '.join(NM_CANDIDATES)} is on PATH")


def symbols(tool: str, elf: Path) -> dict[str, int]:
    """Every code symbol in `elf`, demangled, with its size in bytes.

    Sizes are summed rather than assigned, because a name can appear more than
    once -- local symbols from different objects -- and dropping the duplicates
    would silently under-count.
    """
    if not elf.exists():
        sys.exit(f"no such ELF: {elf}")
    out = subprocess.run(
        [tool, "--print-size", "--radix=d", "--demangle", str(elf)],
        capture_output=True, text=True, check=True,
    ).stdout

    sizes: dict[str, int] = {}
    for line in out.splitlines():
        # `<addr> <size> <type> <name>`; entries with no size are skipped, since
        # they carry no information about how big anything is.
        parts = line.split(None, 3)
        if len(parts) != 4 or parts[2] not in ("t", "T"):
            continue
        try:
            size = int(parts[1])
        except ValueError:
            continue
        sizes[parts[3]] = sizes.get(parts[3], 0) + size
    return sizes


def crate_of(symbol: str) -> str:
    match = SYMBOL.match(symbol)
    return match.group(1) if match else "?"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("before", type=Path, help="the ELF to measure against")
    parser.add_argument("after", type=Path, help="the ELF to measure")
    parser.add_argument("--top", type=int, default=25,
                        help="how many individual symbols to list each way")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = LOG.open("w")
    tool = nm_tool()

    emit(f"soc_size_delta  {datetime.now().astimezone().isoformat(timespec='seconds')}",
         handle)
    emit(f"tool   {tool}", handle)
    emit(f"before {args.before}", handle)
    emit(f"after  {args.after}", handle)
    emit("", handle)

    before = symbols(tool, args.before)
    after = symbols(tool, args.after)

    deltas = {}
    for name in set(before) | set(after):
        change = after.get(name, 0) - before.get(name, 0)
        if change:
            deltas[name] = change

    total = sum(deltas.values())
    emit(f"net {total:+} bytes of code across {len(deltas)} symbols "
         f"({sum(before.values())} -> {sum(after.values())})", handle)
    emit("", handle)

    # By crate first: the question is usually "what did the new dependency
    # cost", and that is a per-crate question.
    by_crate: dict[str, int] = {}
    for name, change in deltas.items():
        by_crate[crate_of(name)] = by_crate.get(crate_of(name), 0) + change
    emit(f"{'crate':40} {'delta':>9}", handle)
    emit("-" * 50, handle)
    for crate, change in sorted(by_crate.items(), key=lambda kv: -abs(kv[1])):
        emit(f"{crate:40} {change:+9}", handle)
    emit("", handle)

    ordered = sorted(deltas.items(), key=lambda kv: -kv[1])
    emit(f"largest {args.top} increases", handle)
    emit("-" * 50, handle)
    for name, change in ordered[:args.top]:
        emit(f"{change:+9}  {name}", handle)
    emit("", handle)
    emit(f"largest {args.top} decreases", handle)
    emit("-" * 50, handle)
    for name, change in ordered[::-1][:args.top]:
        if change >= 0:
            break
        emit(f"{change:+9}  {name}", handle)

    emit("", handle)
    emit(f"log {LOG.relative_to(ROOT)}", handle)
    handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
