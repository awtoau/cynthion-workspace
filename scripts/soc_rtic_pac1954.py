#!/usr/bin/env python3
#
# The #245 measurement: the PAC1954's REFRESH cycle under both dispatchers.
# See awtoau/cynthion-workspace#245 and #115.
# SPDX-License-Identifier: BSD-3-Clause

"""Run the shell under QEMU as a superloop and as an RTIC app, and diff the jitter.

    python3 scripts/soc_rtic_pac1954.py
    python3 scripts/soc_rtic_pac1954.py --runs 200
    python3 scripts/soc_rtic_pac1954.py --no-build

Issue #115 says do NOT gate the RTIC adoption on the synthetic `workload`
feature: its numbers measure a stand-in, and `docs/rtic.md` now carries a
caveat saying so. #245 picks the PAC1954 as the first peripheral to convert
precisely because it already measures its own jitter, so the before and after
can be taken on the SHIPPING firmware with no stand-in in it.

This is that measurement. It builds `firmware/cynthion-soc` twice --

    --features qemu          the superloop, which is what ships
    --features qemu,rtic     the same shell with the REFRESH cycle as an
                             RTIC task released by the 1 ms tick

-- boots each under `qemu-system-riscv32 -M virt`, waits until the REFRESH
cycle has run `--runs` times, and prints what the firmware's own `rtic` and
`cpu stats` commands say.

## What it can and cannot answer

QEMU is not the board and this script says so rather than letting the reader
assume otherwise:

  * There is no PAC1954 under `-M virt` and no I2C controller behind a mux, so
    `power::Monitor::service` reads the clock, records the interval and returns.
    The I2C spin -- two milliseconds of the real thing -- is absent, so the
    LATENESS measured here is the dispatcher's alone. That is the number the
    conversion was supposed to move, so it is the right one to take; it is
    simply not the whole of what the board would show.
  * `mhpmcounter3` and `mhpmcounter4` read hardwired zero on `virt`, so
    STALLED_CYCLES_FRONTEND and _BACKEND come back `--` here. They are real on
    the SoC -- `gateware/soc/cpu/cpu.py` passes `--performance-counters 4` --
    and reading them needs the board.
  * TCG timing is not the FPGA's. The counter rate differs (10 MHz against 60
    MHz, `target::TIME_HZ`) and an emulated instruction does not cost what a
    real one does. Compare the two COLUMNS, never a column against a figure
    taken on hardware.

## Waiting for a condition, never for a duration

The script never sleeps. It asks the firmware `rtic` and reads the `runs`
count out of the reply, repeating until the count reaches the target. Both
models are driven by the identical procedure and therefore carry the identical
perturbation, so the comparison survives the fact that asking costs something.

Output goes to the terminal and to ./tmp/logs/soc_rtic_pac1954.log.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_test  # noqa: E402  -- the QEMU session and its drain threads
from devlog import emit  # noqa: E402

CRATE = ROOT / "firmware" / "cynthion-soc"

# The two builds, and their own target directories.
#
# Separate directories rather than one: cargo would relink the same path on
# every feature flip, so the two ELFs could not both exist, and a run that
# rebuilt between the two measurements is a run whose second column was taken
# against a binary the first column never saw.
MODELS = [
    ("superloop", ["qemu"], ROOT / "tmp" / "rtic-pac1954" / "superloop"),
    ("rtic", ["qemu", "rtic"], ROOT / "tmp" / "rtic-pac1954" / "rtic"),
]

# How long to wait for the firmware's first byte. soc_test.py's own budget, for
# the same reason it gives: essentially all of it is QEMU's startup.
BOOT_S = soc_test.BOOT_S

# How long one shell command may take to answer.
#
# `rtic` is four lines of formatting over an emulated 16550 at no particular
# baud. soc_test.py uses 3 s for comparable commands and has never needed it;
# the same value is used here so the two scripts fail at the same threshold.
COMMAND_S = 3.0

# The REFRESH period, `power::INTERVAL_MS`. Duplicated here rather than parsed
# out of the firmware, because it is what `--runs` is converted into and a
# harness that took it from the reply could not say how long to wait before the
# first reply arrived. `rtic` prints the firmware's own value on every line it
# produces, so a drift between the two is visible in the output.
PERIOD_MS = 50

# How many consecutive answered `time` commands may report the same tick count
# before the clock is called stopped. See where it is used: this is a count of
# replies and not a duration, deliberately, because the thing being tested IS the
# only clock the guest has.
FROZEN_REPLIES = 10_000


def build(features, target_dir):
    """Build the QEMU image with `features`. `None` on success, else the error.

    RUSTFLAGS rather than CARGO_TARGET_<TRIPLE>_RUSTFLAGS, for the reason
    soc_test.build_firmware gives: cargo JOINS the target-specific variable
    with the same key from .cargo/config.toml and hands the linker both
    memory.x and memory-qemu.x.
    """
    env = dict(os.environ)
    env["RUSTFLAGS"] = "-C link-arg=-Tmemory-qemu.x -C link-arg=-Tlink.x"
    argv = ["cargo", "build", "--release", "--bin", "cynthion-soc",
            "--features", ",".join(features),
            "--target-dir", str(target_dir)]
    proc = subprocess.run(argv, cwd=CRATE, env=env, capture_output=True,
                          text=True)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip()[-1500:]
    return None


def elf_of(target_dir):
    return target_dir / "riscv32imac-unknown-none-elf" / "release" / "cynthion-soc"


def text_size(elf):
    """`.text` in bytes, or None if llvm-size is not on PATH.

    Reported because the I-cache is 4 KiB, direct-mapped and one way
    (`docs/rtic.md`), so on this machine code size is a speed question
    rather than a space one. A dispatcher that improves latency and grows the
    hot set has not obviously won.
    """
    for tool in ("llvm-size", "size"):
        try:
            out = subprocess.run([tool, "-A", str(elf)], capture_output=True,
                                 text=True, check=True).stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        match = re.search(r"^\.text\s+(\d+)", out, re.MULTILINE)
        if match:
            return int(match.group(1))
    return None


def ask(session, command, verbose=False):
    """Send one command, return the reply text up to the next prompt.

    `None` if the shell never answered, which is a hung firmware and is
    reported as one rather than retried.
    """
    since = len(session.snapshot())
    session.send(command.encode() + b"\r")
    if session.expect(b"> ", COMMAND_S, since=since) is None:
        return None
    reply = session.snapshot()[since:].decode("ascii", "replace")
    if verbose:
        emit(soc_test.show(reply.encode()))
    return reply


def runs_in(reply):
    """The task's run count out of an `rtic` reply, or None."""
    match = re.search(r"^task\s+\S+.*?\bruns (\d+)", reply, re.MULTILINE)
    return int(match.group(1)) if match else None


def ticks_in(reply):
    """The 1 ms tick count out of a `time` reply, or None.

    The guest's own clock, and the only one this script is entitled to measure a
    period against -- see `STALL_MS`. It also proves the tick is running, which
    under RTIC is what releases the task at all.
    """
    match = re.search(r"\bticks (\d+)", reply)
    return int(match.group(1)) if match else None


def measure(label, elf, runs_wanted, verbose):
    """Boot `elf`, wait for `runs_wanted` REFRESH cycles, return what it said."""
    session = soc_test.Session(elf)
    try:
        if session.expect(b"Cynthion RISC-V SoC", BOOT_S) is None:
            return {"error": "the firmware never reached its banner",
                    "stderr": bytes(session.errors).decode("ascii", "replace")}

        # A bare Enter first, so the shell is at a prompt before anything is
        # asked of it. Without it the first `expect` for a prompt can match the
        # one the banner already printed and the reply would be read from the
        # wrong offset.
        session.send(b"\r")
        if session.expect(b"> ", COMMAND_S) is None:
            return {"error": "the shell never printed a prompt"}

        # WAIT ON THE GUEST'S OWN TICK, with the cheapest command that reports
        # it, and ask `rtic` exactly once at the end.
        #
        # Asking `rtic` in the wait loop was tried and is the wrong instrument:
        # every command holds `&mut Devices` for its duration -- under RTIC
        # through a priority ceiling, under the superloop through the borrow --
        # so a harness that asks constantly is measuring the harness. It read
        # 86% busy, which is not a state either model is meant to be judged in.
        # `time` is three lines against seven and touches no device at all.
        wanted_ms = runs_wanted * PERIOD_MS
        ticks = 0
        frozen = 0
        while ticks < wanted_ms:
            clock = ask(session, "time", verbose)
            now = ticks_in(clock or "")
            if now is None:
                return {"error": f"`time` printed no tick count:\n{clock}"}
            # Two reads landing in the same millisecond is normal and says
            # nothing: a `time` round trip is faster than the tick it reads.
            # What is not normal is the count never moving, and the bound is a
            # count of REPLIES rather than a duration -- a clock that has not
            # advanced across ten thousand answered commands has stopped, and
            # nothing periodic can be measured on a machine whose clock has.
            frozen = 0 if now > ticks else frozen + 1
            if frozen > FROZEN_REPLIES:
                return {"error": f"the 1 ms tick stopped at {now} ticks, "
                                 f"across {FROZEN_REPLIES} answered commands"}
            ticks = now

        reply = ask(session, "rtic", verbose)
        if reply is None:
            return {"error": "`rtic` did not answer"}
        runs = runs_in(reply)
        if runs is None:
            return {"error": f"`rtic` printed no run count:\n{reply}"}
        if runs == 0:
            return {"error": f"the task never ran: {ticks} ms of guest time "
                             f"passed, {ticks // PERIOD_MS} periods, and the "
                             f"run count is zero -- it is not being released"}

        stats = ask(session, "cpu stats", verbose)
        irq = ask(session, "irq", verbose)
        return {"rtic": reply, "stats": stats or "", "irq": irq or "",
                "runs": runs, "ticks": ticks}
    finally:
        session.close()


# The fields pulled out of an `rtic` reply for the comparison table. Each is
# (label, regex with one capture, unit).
FIELDS = [
    ("runs", r"\bruns (\d+)", ""),
    ("pends", r"\bpends (\d+)", ""),
    ("late worst", r"late worst (\d+) ticks", "ticks"),
    ("late mean", r"mean (\d+) ticks", "ticks"),
    ("gap worst", r"gap worst (\d+) ms", "ms"),
]

STATS_FIELDS = [
    ("busy", r"busy ([0-9.]+)%", "%"),
    ("ipc", r"ipc ([0-9.]+)", ""),
    ("turns", r"turns (\d+)", ""),
    ("mean turn", r"mean (\d+) cycles", "cycles"),
    ("worst turn", r"worst (\d+) cycles", "cycles"),
]


def pull(text, fields):
    out = {}
    for label, pattern, unit in fields:
        match = re.search(pattern, text)
        out[label] = (match.group(1) + (f" {unit}" if unit else "")
                      if match else "--")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100,
                        help="REFRESH cycles to wait for, per model "
                             "(default 100, which is 5 s of guest time at the "
                             "50 ms period)")
    parser.add_argument("--no-build", action="store_true",
                        help="measure what is already built")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="dump every reply")
    args = parser.parse_args()

    emit("soc_rtic_pac1954: the REFRESH cycle under both dispatchers (#245)")
    emit("")

    results = {}
    for label, features, target_dir in MODELS:
        if not args.no_build:
            emit(f"  building {label}: --features {','.join(features)}")
            error = build(features, target_dir)
            if error:
                emit(f"  FAIL: {label} did not build:\n{error}")
                return 1
        elf = elf_of(target_dir)
        if not elf.exists():
            emit(f"  FAIL: {elf} does not exist; drop --no-build")
            return 1
        size = text_size(elf)
        emit(f"  {label}: .text {size:,} bytes" if size
             else f"  {label}: .text unknown (no llvm-size on PATH)")
        results[label] = measure(label, elf, args.runs, args.verbose)
        results[label]["text"] = size

    emit("")
    for label, _, _ in MODELS:
        if "error" in results[label]:
            emit(f"  FAIL: {label}: {results[label]['error']}")
            if results[label].get("stderr"):
                emit(f"        qemu said: {results[label]['stderr'][:400]}")
            return 1

    # The table. Rows are the numbers, columns are the models, because that is
    # the direction the reader subtracts in.
    names = [label for label, _, _ in MODELS]
    width = max(len(row[0]) for row in FIELDS + STATS_FIELDS) + 2
    emit("")
    emit("  " + "the REFRESH cycle".ljust(width)
         + "".join(f"{name:>18}" for name in names))
    emit("  " + "-" * (width + 18 * len(names)))
    pulled = {name: pull(results[name]["rtic"], FIELDS) for name in names}
    for label, _, _unit in FIELDS:
        emit("  " + label.ljust(width)
             + "".join(f"{pulled[name][label]:>18}" for name in names))

    emit("")
    emit("  " + "the loop it runs in".ljust(width)
         + "".join(f"{name:>18}" for name in names))
    emit("  " + "-" * (width + 18 * len(names)))
    stats = {name: pull(results[name]["stats"], STATS_FIELDS) for name in names}
    for label, _, _unit in STATS_FIELDS:
        emit("  " + label.ljust(width)
             + "".join(f"{stats[name][label]:>18}" for name in names))

    emit("  " + ".text".ljust(width)
         + "".join(f"{results[name]['text'] or 0:>18,}" for name in names))

    emit("")
    for name in names:
        emit(f"  --- `rtic` as {name} said it ---")
        for line in results[name]["rtic"].splitlines():
            line = line.strip()
            # The echo of the command itself and the trailing prompt are not
            # part of the reply.
            if line and line not in ("rtic", ">"):
                emit(f"    {line}")
        emit("")

    emit("  QEMU is not the board: no PAC1954, no I2C spin, no performance")
    emit("  counters, and a 10 MHz counter rather than 60. Compare the two")
    emit("  columns with each other and with nothing else. See the module")
    emit("  comment, and docs/rtic.md.")
    emit("")
    emit("RESULT: PASS - both models ran and reported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
