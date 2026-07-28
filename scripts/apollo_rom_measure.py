#!/usr/bin/env python3
"""Rebuild the cynthion_d11 Apollo firmware and report ROM/RAM usage.

Used for issue #73 (d11 flash headroom). Forces a relink so the linker's
memory-usage report is printed, then extracts the numbers so successive
changes can be attributed precisely.

Output goes to ./tmp/apollo_rom_measure.log as well as stdout.
"""

import pathlib
import re
import subprocess
import sys

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = WORKSPACE / "repos" / "apollo" / "firmware"
LOG = WORKSPACE / "tmp" / "apollo_rom_measure.log"

# Prepend the pinned CPython used by this workspace's toolchain.
EXTRA_PATH = "/home/dan/opt/cpython-315t/bin"


def build(label: str) -> tuple[int, int, str]:
    import os

    env = dict(os.environ)
    env["PATH"] = EXTRA_PATH + ":" + env.get("PATH", "")

    # Touch main.c so the link step always reruns and prints the memory report.
    (FIRMWARE / "src" / "main.c").touch()

    proc = subprocess.run(
        ["make", "APOLLO_BOARD=cynthion"],
        cwd=FIRMWARE,
        env=env,
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        return -1, -1, out

    rom = ram = -1
    for line in out.splitlines():
        m = re.match(r"\s*rom:\s+(\d+) B", line)
        if m:
            rom = int(m.group(1))
        m = re.match(r"\s*ram:\s+(\d+) B", line)
        if m:
            ram = int(m.group(1))
    return rom, ram, out


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "measure"
    rom, ram, out = build(label)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(f"=== {label} ===\n{out}\n")

    if rom < 0:
        print(f"{label}: BUILD FAILED (see {LOG})")
        print(out[-3000:])
        return 1

    print(f"{label}: rom={rom} ram={ram}  (rom free={14336 - rom})")
    # Surface any warning lines; the build must stay warning-clean.
    warns = [l for l in out.splitlines() if "warning:" in l.lower()]
    if warns:
        print("WARNINGS:")
        for w in warns:
            print("  " + w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
