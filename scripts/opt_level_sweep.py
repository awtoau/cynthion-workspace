#!/usr/bin/env python3
#
# Which opt-level is right now that .text executes from flash?  See #167.
# SPDX-License-Identifier: BSD-3-Clause

"""
Build the firmware at each `opt-level`, run it on the board, and report.

    ./dev.py optlevel               # z, s, 3 -- build, flash, measure each
    ./dev.py optlevel -- --levels z 3

## Why this is a measurement and not an argument

`opt-level = "z"` was chosen when `.text` lived in a 63 KiB block RAM region and
the reasoning was "space is scarce, speed is not". #162 stage two moved `.text`
and `.rodata` to flash. Block RAM went from 8,532 bytes free to 54,856, and IPC
went from roughly 1.0 to 0.296 because every instruction fetch is now a potential
SPI transaction. Both halves of the original argument inverted at once.

Which setting wins is genuinely unobvious, and the two effects pull opposite ways:

  * FOR "z": `.text` is ~36 KB against a 4 KiB direct-mapped I-cache, 8.8x
    oversubscribed with one way. Code size drives the miss rate almost directly,
    and each miss is a full quad-SPI command/address/dummy/data. Smaller code may
    be worth MORE now than it ever was in block RAM, where every fetch cost the
    same.
  * AGAINST "z": it suppresses inlining that would remove call overhead, and at
    IPC 0.296 the CPU is starved rather than issue-limited, so cheaper
    instructions buy little.

Nobody can settle that from first principles, so this measures it.

## What it reports, and why those three numbers

  * **`.text` size** -- the input to the cache-miss argument.
  * **IPC**, from `cpu stats` -- instructions retired per cycle. This is the one
    that answers the question: it falls when the CPU waits on fetch.
  * **`bench flash` sequential and random MB/s** -- the fetch path itself, so a
    change in IPC can be attributed to the code or to the link.

## The honest caveat

`cpu stats` measures the SHELL, which spends nearly all its time idle waiting on
a console. `busy` is under 1% at rest, so IPC over a quiet window is dominated by
the poll loop rather than by representative code. The `--work` flag issues a
command that spins first, so the window covers something. It is still a narrow
workload, and this measures that workload, not "the firmware".
"""

import argparse
import json
import re
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "firmware" / "cynthion-soc"
CARGO = CRATE / "Cargo.toml"
ELF = CRATE / "target" / "riscv32imac-unknown-none-elf" / "release" / "cynthion-soc"
RESULTS = ROOT / "tmp" / "opt_level_sweep.json"

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

CONSOLE_PORT = 9000


def sizes():
    """`.text`, `.rodata` and `.bss`, from the built ELF."""
    out = subprocess.run(["riscv64-linux-gnu-size", "-A", str(ELF)],
                         capture_output=True, text=True).stdout
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in (".text", ".rodata", ".bss"):
            found[parts[0]] = int(parts[1])
    return found


class Console:
    """The board's shell, over the console service.

    `select` at 1 ms rather than a sleep loop: a poll interval in a measuring
    instrument is a floor on what it can report, and this file exists to report
    numbers.
    """

    def __init__(self):
        self.sock = socket.create_connection(("127.0.0.1", CONSOLE_PORT), timeout=5)
        self.sock.setblocking(False)

    def drain(self, seconds=0.4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.sock], [], [], 0.05)
            if not ready:
                continue
            try:
                self.sock.recv(65536)
            except BlockingIOError:
                return

    def ask(self, command, seconds=3.0):
        self.drain()
        self.sock.sendall(b"\r")
        self.drain(0.3)
        self.sock.sendall(command.encode() + b"\r")
        deadline = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.sock], [], [], 0.001)
            if not ready:
                continue
            try:
                chunk = self.sock.recv(65536)
            except BlockingIOError:
                continue
            if chunk:
                buf += chunk
        return buf.decode(errors="replace")

    def close(self):
        self.sock.close()


def measure(console, emit):
    """IPC and the two flash rates, from the running board."""
    # Something for the window to cover. An idle shell is under 1% busy, so an
    # IPC taken over silence is a measurement of the poll loop.
    console.ask("bench flash", 8.0)
    stats = console.ask("cpu stats", 3.0)
    bench = console.ask("bench flash", 8.0)

    ipc = re.search(r"ipc\s+([\d.]+)", stats)
    busy = re.search(r"busy\s+([\d.]+)%", stats)
    seq = re.search(r"flash\s+2 KiB read seq\s+[\d.]+\s+([\d.]+)", bench)
    rnd = re.search(r"flash\s+16 KiB read rnd\s+[\d.]+\s+([\d.]+)", bench)

    for label, match in (("ipc", ipc), ("busy", busy), ("seq", seq), ("rnd", rnd)):
        if match is None:
            emit(f"  could not parse {label} from the board")
    return {
        "ipc": float(ipc.group(1)) if ipc else None,
        "busy_pct": float(busy.group(1)) if busy else None,
        "flash_seq_mbs": float(seq.group(1)) if seq else None,
        "flash_rnd_mbs": float(rnd.group(1)) if rnd else None,
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels", nargs="+", default=["z", "s", "3"],
                        help="opt-level values to try")
    parser.add_argument("--no-board", action="store_true",
                        help="build and size only; do not flash or measure")
    args = parser.parse_args()

    original = CARGO.read_text()
    results = {}
    try:
        for level in args.levels:
            emit(f"=== opt-level = \"{level}\"")
            # Numeric levels are BARE in cargo, string levels are quoted.
            # `opt-level = "3"` is rejected outright -- "must be `0`, `1`, `2`,
            # `3`, `s` or `z`, but found the string" -- so quoting everything
            # silently drops the numeric rungs from the sweep.
            value = f'"{level}"' if level in ("s", "z") else level
            CARGO.write_text(
                re.sub(r"^opt-level = .*$", f"opt-level = {value}",
                       original, flags=re.M))

            build = subprocess.run(["cargo", "build", "--release"], cwd=CRATE,
                                   capture_output=True, text=True)
            if build.returncode != 0:
                emit("  build FAILED:")
                emit(build.stderr.strip()[-500:])
                continue

            entry = {"sizes": sizes()}
            emit(f"  text {entry['sizes'].get('.text')}  "
                 f"rodata {entry['sizes'].get('.rodata')}")

            if not args.no_board:
                flash = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "soc_run.py"),
                     "--firmware-only", "--skip-tests", "--no-read"],
                    cwd=ROOT, capture_output=True, text=True)
                if flash.returncode != 0:
                    emit("  flash FAILED:")
                    emit((flash.stdout or flash.stderr).strip()[-500:])
                    continue
                console = Console()
                try:
                    entry.update(measure(console, emit))
                finally:
                    console.close()
                emit(f"  ipc {entry.get('ipc')}  busy {entry.get('busy_pct')}%  "
                     f"flash seq {entry.get('flash_seq_mbs')} MB/s  "
                     f"rnd {entry.get('flash_rnd_mbs')} MB/s")

            results[level] = entry
    finally:
        # ALWAYS restore. This edits a checked-in file, and leaving someone
        # else's tree on a level they did not choose is exactly the kind of
        # silent state this repo has been bitten by.
        CARGO.write_text(original)
        emit("Cargo.toml restored")

    emit("")
    emit(f"{'level':6} {'.text':>7} {'ipc':>7} {'seq MB/s':>9} {'rnd MB/s':>9}")
    for level, entry in results.items():
        emit(f"{level:6} {entry['sizes'].get('.text', 0):7} "
             f"{entry.get('ipc') or 0:7.3f} "
             f"{entry.get('flash_seq_mbs') or 0:9.2f} "
             f"{entry.get('flash_rnd_mbs') or 0:9.2f}")

    RESULTS.write_text(json.dumps(results, indent=2))
    emit(f"\nresults {RESULTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
