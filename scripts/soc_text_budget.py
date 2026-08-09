#!/usr/bin/env python3
#
# Where the firmware's .text actually goes, and who pulls in the expensive
# compiler builtins.
# SPDX-License-Identifier: BSD-3-Clause

"""
Rank the firmware's symbols by size, and name the callers of the soft-arithmetic
builtins.

    ./scripts/soc_text_budget.py              # top 20 symbols + builtin callers
    ./scripts/soc_text_budget.py --top 40
    ./scripts/soc_text_budget.py --elf path/to/other.elf

Output is mirrored to ./tmp/logs/soc_text_budget.log.

## Why

`.text` is this design's binding constraint and every conversation about it has
been conducted from memory. Three separate size claims in one session were
wrong, each in the same way: a cause inferred from proximity rather than
measured.

  * "forking embedded-cli to drop its UTF-8 accumulator is the win" -- 934 bytes,
    against 27 KB sitting in one function.
  * "`clock::millis` pulls in the 64-bit divide" -- every caller passes a
    constant; it folds. The real callers are `shell::run`, `PowerAlert` and
    `power::ua_to_code`.
  * "`<char as Debug>::fmt` comes from `&str[a..b]` in the shell" -- rewritten as
    `.get()`, and both symbols came back byte-for-byte identical. It was the
    panic handler formatting `PanicInfo` with `{}`.

Each was answerable in one command. This is that command.

## The builtins, and why they are worth naming

`__divdi3`, `__udivdi3`, `__moddi3`, `__umoddi3` are 64-bit divide and modulo on
a 32-bit core: `compiler_builtins`' `u64_div_rem` is ~950 bytes and every call
site is a routine that could not be done in a register. `memcpy`/`memmove`/
`memset` are cheaper but appear where a copy was not intended.

A caller here is not automatically a defect -- some arithmetic genuinely needs
64 bits. It is a question worth asking, and the answer is usually that the
operand range fits in 32.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "soc_text_budget.log"
ELF = (ROOT / "firmware" / "cynthion-soc" / "target"
       / "riscv32imac-unknown-none-elf" / "release" / "cynthion-soc")

# Soft arithmetic and bulk memory, in the order they are worth worrying about.
BUILTINS = ["__divdi3", "__udivdi3", "__moddi3", "__umoddi3",
            "__muldi3", "memcpy", "memmove", "memset"]

# Disassemblers that understand riscv32, best first.
#
# The host `objdump` on an x86 box does NOT, and it fails by printing nothing
# rather than by refusing -- which reads as "no call sites" instead of "wrong
# tool". Every one of these ships with either Rust or a distro llvm package.
DISASSEMBLERS = ["rust-objdump", "llvm-objdump", "riscv64-linux-gnu-objdump"]


def tool(names):
    """The first of `names` on PATH, or None."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def symbols(elf, nm):
    """(size, kind, name) for every sized symbol, largest first."""
    out = subprocess.run(
        [nm, "--print-size", "--size-sort", "--radix=d", "--demangle", str(elf)],
        capture_output=True, text=True)
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) < 4:
            continue
        _addr, size, kind, name = parts
        if not size.isdigit():
            continue
        rows.append((int(size), kind, name))
    rows.sort(reverse=True)
    return rows


def builtin_callers(elf, objdump):
    """{(caller, builtin): call count} across the whole image."""
    out = subprocess.run([objdump, "-d", "--demangle", str(elf)],
                         capture_output=True, text=True)
    if not out.stdout.strip():
        return None

    callers = {}
    current = None
    for line in out.stdout.splitlines():
        header = re.match(r"^[0-9a-f]+ <(.+)>:$", line)
        if header:
            current = header.group(1)
            continue
        if current is None:
            continue
        for name in BUILTINS:
            # The angle brackets matter: `<__divdi3>` is the call target, while a
            # bare match also hits the routine's own label and its internal jumps.
            if f"<{name}>" in line and current != name:
                callers[(current, name)] = callers.get((current, name), 0) + 1
    return callers


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--elf", default=str(ELF))
    parser.add_argument("--top", type=int, default=20,
                        help="how many symbols to rank (default 20)")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    elf = Path(args.elf)
    if not elf.exists():
        emit(f"no such image: {elf}")
        emit("build it first: cd firmware/cynthion-soc && cargo build --release")
        return 1

    nm = tool(["rust-nm", "llvm-nm", "nm"])
    objdump = tool(DISASSEMBLERS)
    if nm is None:
        emit("no nm on PATH")
        return 1

    emit(f"image: {elf.relative_to(ROOT) if elf.is_relative_to(ROOT) else elf}")

    rows = symbols(elf, nm)
    text = sum(size for size, kind, _ in rows if kind in ("t", "T"))
    emit(f"code in sized symbols: {text} bytes")
    emit("")
    emit(f"{'bytes':>7}  {'share':>6}  symbol")
    for size, kind, name in rows[:args.top]:
        if kind not in ("t", "T"):
            continue
        emit(f"{size:>7}  {size / text * 100:>5.1f}%  {name[:96]}")

    emit("")
    if objdump is None:
        emit("no riscv-capable disassembler found; tried: "
             + ", ".join(DISASSEMBLERS))
        emit("install one: rustup component add llvm-tools-preview, "
             "or the distro llvm package")
        return 0

    callers = builtin_callers(elf, objdump)
    if not callers:
        # An empty disassembly means the tool did not understand the target,
        # NOT that the image is free of these calls. Saying so is the point:
        # silence here previously read as a clean result.
        emit(f"{Path(objdump).name} produced no disassembly -- it likely does "
             "not know riscv32.")
        emit("That is not the same as 'no call sites'. Try another of: "
             + ", ".join(DISASSEMBLERS))
        return 1

    emit(f"soft-arithmetic and bulk-memory callers (via {Path(objdump).name}):")
    if not callers:
        emit("  none")
    for (caller, name), count in sorted(callers.items(), key=lambda kv: -kv[1]):
        emit(f"  {count:>3}x  {name:<10} <- {caller[:90]}")
    emit("")
    emit("A caller is a question, not a defect: some arithmetic needs 64 bits.")
    emit("The usual answer is that the operand range fits in 32.")
    emit(f"log -> {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
