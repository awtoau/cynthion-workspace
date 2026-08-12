#!/usr/bin/env python3
#
# Which SoC windows nothing reads or writes. #447.
# SPDX-License-Identifier: BSD-3-Clause

"""Find the CSR windows on this SoC's bus that no software reaches.

A PAC binding is NOT a user: `firmware/cynthion-soc-pac/` is generated from the
memory map by `scripts/soc_generate_pac.py`, so every window has one whether or
not a line of firmware ever names it. Three peripherals were already found on
the bus with bindings and no driver (`FlashILA`, `FlashPinProbe`,
`HyperRAMProbe`), which is what this exists to make repeatable.

## The method, and how it can be wrong

For each `pub const <NAME>` in `cynthion-soc-pac/src/base.rs` -- the generated
address map, one entry per decoder window:

  1. **Direct.** `cynthion_soc_pac::base::<NAME>` outside the PAC crate.
  2. **Aliased.** Occurrences inside `firmware/cynthion-soc/src/target.rs` are
     re-exports, not uses. The binding they create -- `pub const X` or a struct
     field `x:` -- is extracted and searched for in turn. `BOARD_ULPI` is named
     nowhere but `target.rs`, and reaches `src/ulpi.rs` as `BOARD.ulpi`.
  3. **Raw.** The window's literal hex address, anywhere in `firmware/` or
     `scripts/` -- for a caller that hardcodes rather than importing.
  4. **Host.** The generated C firmware (`scripts/riscv_firmware.py`) keeps its
     own `#define`s, so a window only that file names is reported as such: a
     user, but not one the shipping Rust firmware has.

A window with no hit at any level is reported NO USER.

**FALSE "DEAD" IS THE FAILURE THAT MATTERS**, so `--control` is run first and
fails the whole script if it does not pass:

  * every window in `KNOWN_USED` must come back USED -- a detector that cannot
    see a real driver would report the whole SoC dead;
  * `KNOWN_ABSENT`, a name in no source file, must come back NO USER -- a
    detector that says USED for everything cannot report anything.

    ./scripts/soc_dead_peripherals.py
    ./scripts/soc_dead_peripherals.py --control-only

Output is mirrored to ./tmp/logs/soc_dead_peripherals.log.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_RS = ROOT / "firmware" / "cynthion-soc-pac" / "src" / "base.rs"
TARGET_RS = ROOT / "firmware" / "cynthion-soc" / "src" / "target.rs"
PAC_DIR = ROOT / "firmware" / "cynthion-soc-pac"
LOG = ROOT / "tmp" / "logs" / "soc_dead_peripherals.log"

# Where a user could live. `repos/` is upstream and does not build against this
# SoC's map; `tmp/` and `target/` are build output.
SEARCH_ROOTS = ("firmware", "scripts", "tests", "gui")
SKIP = ("/repos/", "/tmp/", "/target/", "/debris/", "cynthion-soc-pac/")

# The generated C firmware is a user, and a different kind: it is not built by
# `soc_run.py` unless `--c-firmware` is passed, so a window only it reaches has
# no user in the shipping image.
C_GENERATOR = "scripts/riscv_firmware.py"

# Windows whose driver is not in doubt. If the detector calls one of these dead
# it is the detector that is broken -- see the docstring.
KNOWN_USED = ("CONSOLE", "APOLLO_UART", "BOARD_GPIO", "BOARD_I2C",
              "BOARD_CLOCKS", "BOARD_ULPI", "BOARD_VBUS", "BOOTRAM",
              "PLIC", "CLINT")

# A name and an address that appear in no source file. The detector must report
# it dead; one that cannot is a detector that reports every window used.
KNOWN_ABSENT = "BOARD_NOSUCHWINDOW_XYZZY"
KNOWN_ABSENT_ADDR = 0x5a5a1234

CONST_RE = re.compile(r"^pub const ([A-Z][A-Z0-9_]*): usize = (0x[0-9a-fA-F]+);",
                      re.M)


def grep(pattern):
    """`git grep -n` for a fixed string, minus generated and vendored trees."""
    proc = subprocess.run(
        ["git", "grep", "-nF", pattern, "--"] + list(SEARCH_ROOTS),
        capture_output=True, text=True, cwd=ROOT)
    lines = []
    for line in proc.stdout.splitlines():
        path = line.split(":", 1)[0]
        if any(skip.strip("/") in path for skip in SKIP):
            continue
        lines.append(line)
    return lines


def aliases_in_target(name):
    """Identifiers `target.rs` binds this window's address to.

    Three shapes, all present: `pub const FLASH_BASE: usize = ...::SPIFLASH;`,
    the `Board` struct literal's `ulpi: ...::BOARD_ULPI,`, and an element of the
    `UART_BASES` slice, which is several lines below its own declaration.
    """
    if not TARGET_RS.exists():
        return []
    found = []
    lines = TARGET_RS.read_text().splitlines()
    for index, line in enumerate(lines):
        if f"base::{name}" not in line:
            continue
        const = re.search(r"pub const ([A-Z][A-Z0-9_]*)\s*:", line)
        if const:
            found.append(const.group(1))
            continue
        member = re.match(r"\s*([a-z][a-z0-9_]*)\s*:\s*cynthion_soc_pac", line)
        if member:
            found.append("." + member.group(1))
            continue
        # An element of a multi-line initialiser: the binding is the nearest
        # `pub const` above it.
        for above in range(index, -1, -1):
            const = re.search(r"pub const ([A-Z][A-Z0-9_]*)\s*:", lines[above])
            if const:
                found.append(const.group(1))
                break
    return found


def users(name, address):
    """(verdict, evidence) for one window.

    `verdict` is one of "used", "c-only", "dead".
    """
    evidence = []

    def outside_target(lines):
        return [line for line in lines
                if "cynthion-soc/src/target.rs" not in line]

    direct = outside_target(grep(f"base::{name}"))
    evidence += [("direct", line) for line in direct]

    for alias in aliases_in_target(name):
        hits = [line for line in grep(alias)
                if "cynthion-soc/src/target.rs" not in line]
        evidence += [(f"alias {alias}", line) for line in hits]

    # A raw address, for a caller that hardcodes. `base.rs` itself is the
    # definition and `soc_generate_pac.py` writes it, so neither is a user.
    for literal in (f"{address:#010x}", f"{address:#x}"):
        for line in grep(literal):
            if "soc_generate_pac.py" in line or "/base.rs" in line:
                continue
            evidence += [("literal", line)]

    if not evidence:
        return "dead", []
    if all(C_GENERATOR in line for _kind, line in evidence):
        return "c-only", evidence
    return "used", evidence


def windows():
    """(name, address) for every window in the generated map, in map order."""
    text = BASE_RS.read_text()
    out = []
    for match in CONST_RE.finditer(text):
        name, address = match.group(1), int(match.group(2), 16)
        if name.endswith("_SIZE"):
            continue
        out.append((name, address))
    return out


def control(emit):
    """Prove the detector can say both things. Returns True if it can."""
    emit("CONTROL -- the detector must be able to be wrong in both directions")
    ok = True

    for name in KNOWN_USED:
        address = dict(windows()).get(name)
        if address is None:
            emit(f"  {'?':<8} {name:<22} not in the generated map")
            ok = False
            continue
        verdict, evidence = users(name, address)
        good = verdict == "used"
        ok &= good
        first = evidence[0][1].split(":", 2)[0] if evidence else "--"
        emit(f"  {'PASS' if good else 'FAIL':<8} {name:<22} {verdict:<7} "
             f"{len(evidence):>3} hits, first in {first}")

    verdict, _ = users(KNOWN_ABSENT, KNOWN_ABSENT_ADDR)
    good = verdict == "dead"
    ok &= good
    emit(f"  {'PASS' if good else 'FAIL':<8} {KNOWN_ABSENT:<22} {verdict:<7} "
         f"(must be dead)")
    emit()
    return bool(ok)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--control-only", action="store_true",
                        help="run the controls and stop")
    parser.add_argument("--evidence", action="store_true",
                        help="print every matching line, not just the count")
    args = parser.parse_args()

    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    passed = control(emit)
    if not passed:
        emit("CONTROL FAILED -- no verdict below is worth reading")
    if args.control_only or not passed:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text("\n".join(out) + "\n")
        return 0 if passed else 1

    emit(f"  {'window':<22} {'address':>12} {'verdict':<8} hits  where")
    emit("  " + "-" * 74)
    dead, c_only = [], []
    for name, address in windows():
        verdict, evidence = users(name, address)
        where = ", ".join(sorted({line.split(":", 1)[0].split("/")[-1]
                                  for _kind, line in evidence})[:3])
        emit(f"  {name:<22} {address:>#12x} {verdict:<8} "
             f"{len(evidence):>4}  {where[:34]}")
        if args.evidence:
            for kind, line in evidence:
                emit(f"      [{kind}] {line[:150]}")
        if verdict == "dead":
            dead.append(name)
        elif verdict == "c-only":
            c_only.append(name)

    emit()
    emit(f"  NO USER: {', '.join(dead) if dead else '(none)'}")
    emit(f"  C GENERATOR ONLY, not in the shipping Rust image: "
         f"{', '.join(c_only) if c_only else '(none)'}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
