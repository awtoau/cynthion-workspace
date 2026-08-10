#!/usr/bin/env python3
#
# Measure the two flaky assertions in soc_test.py instead of re-running them.
# SPDX-License-Identifier: BSD-3-Clause

"""
Repeats the exact sequences behind `a modified tree is reported as dirty` (#370)
and `work moves the busy cycle count` (#363), N times, and reports the numbers
each one turns on. Nothing is asserted here: this is the instrument that says
what the assertions are actually measuring.

    ./scripts/soc_test_flake_probe.py dirty --runs 10
    ./scripts/soc_test_flake_probe.py busy  --runs 10
    ./scripts/soc_test_flake_probe.py busy  --runs 10 --load 8

`--load N` runs N spinning host processes for the duration, which is how #363
was reproduced. They are started and reaped by this script; nothing survives it.

## dirty

Boots a fresh image, sends `info`, and times the arrival of `image ` (the
sentinel soc_test waits for) against the arrival of the CRLF that ends that
line (the event that makes `clean`/`dirty` readable). The gap between the two
is the whole of #370: the assertion reads the buffer in that gap.

## busy

Runs `cpu stats`, then `cpu log 20`, then `cpu stats`, and prints the window
and busy figures either side. `WINDOW_CYCLES` halves at 2^30
(`firmware/cynthion-soc/src/metrics.rs`), and under `-M virt` `mcycle` is the
host TSC, so that window is a fraction of a second of wall clock rather than
the 17.9 s it is on a 60 MHz board. A window that shrank between the two
readings is a halving, and `window * busy` is not comparable across one.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit                                          # noqa: E402
from soc_test import (ELF, PROMPT, REPLY_S, Session,              # noqa: E402
                      build_firmware, expect_line, show)

# 2^30, from `HALVE_AT` in firmware/cynthion-soc/src/metrics.rs. Duplicated
# rather than imported because it lives in Rust; a mismatch shows up as a
# halving this script cannot explain.
HALVE_AT = 1 << 30


def spinners(count):
    """`count` host processes burning a core each, and a callable to reap them.

    The load #363 was observed under. `--load 0` returns an empty list, so the
    unloaded run is the same code path.
    """
    procs = [subprocess.Popen([sys.executable, "-c", "while True: pass"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
             for _ in range(count)]

    def reap():
        for proc in procs:
            proc.kill()
            proc.wait()
    return procs, reap


def probe_dirty(runs):
    """Time `image ` against the end of the line it starts."""
    emit(f"dirty: {runs} run(s), sentinel `image ` vs the CRLF that ends its line")
    emit()
    truncated = 0
    for run in range(1, runs + 1):
        session = Session(ELF)
        try:
            first = session.expect(b"Cynthion RISC-V SoC", 5.0)
            if first is None:
                emit(f"  run {run}: never booted")
                continue
            mark = len(session.snapshot())
            session.send(b"info\r")

            started = time.monotonic()
            at = session.expect(b"image ", REPLY_S, mark)
            sentinel_s = time.monotonic() - started
            # The buffer at the instant the sentinel matched, which is what
            # #370 says the assertion reads. `dirty`/`clean` is the next token
            # on the same line, so whether it is here at all is a race.
            early = session.snapshot()[mark:]

            eol = expect_line(session, b"image ", REPLY_S, mark)
            line_s = time.monotonic() - started
            whole = session.snapshot()[mark:]

            has_word_early = b"dirty" in early or b"clean" in early
            has_word_late = b"dirty" in whole or b"clean" in whole
            if not has_word_early:
                truncated += 1
            emit(f"  run {run}: `image ` at {sentinel_s * 1000:7.1f} ms"
                 f"  line whole at {'never' if eol is None else f'{line_s * 1000:7.1f} ms'}"
                 f"  word present: sentinel {has_word_early}, line {has_word_late}")
            if not has_word_early:
                emit(f"           at the sentinel: {show(early).strip()!r}")
        finally:
            session.close()
    emit()
    emit(f"dirty: {truncated}/{runs} run(s) had no clean/dirty word at the "
         f"sentinel")
    return truncated


def read_stats(session):
    """`(window, busy_basis_points)` from one `cpu stats`, or None."""
    import re
    mark = len(session.snapshot())
    session.send(b"cpu stats\r")
    if expect_line(session, b"poll     every", REPLY_S, mark) is None:
        return None
    reply = session.snapshot()[mark:]
    found = re.search(rb"window (\d+)\s+busy (\d+)\.(\d\d)%", reply)
    if not found:
        return None
    return (int(found.group(1)),
            int(found.group(2)) * 100 + int(found.group(3)))


def wait_for_window(session, margin, tries=400):
    """Poll `cpu stats` until the window is within `margin` of the halving.

    `tries` bounds the search rather than timing it: each try is one `cpu
    stats` round trip (~5 ms) and the window sweeps its whole range in about
    six seconds, so 400 covers several sweeps. Returns False if it never
    lands, which is a fact about the build, not a timeout to tune.
    """
    for _ in range(tries):
        reading = read_stats(session)
        if reading is None:
            return False
        if HALVE_AT - reading[0] <= margin:
            return True
    return False


def probe_busy(runs, idle_s, straddle):
    """The two `cpu stats` readings either side of `cpu log 20`.

    `idle_s` reproduces the state soc_test asks in: the suite reaches this
    check ~100 s into a session, by which time the window has been through
    many halvings and sits somewhere in [HALVE_AT/2, HALVE_AT]. A run that
    asks a freshly booted shell never sees one.

    `straddle` waits for a window within `straddle` cycles of HALVE_AT before
    asking, so the halving lands BETWEEN the two readings by construction.
    That is the failing case, on demand, rather than by waiting for load to
    produce it.
    """
    emit(f"busy: {runs} run(s), `cpu stats` / `cpu log 20` / `cpu stats`, "
         f"{idle_s}s idle first"
         + (f", straddling the halving (within {straddle} cycles)"
            if straddle else ""))
    emit(f"      halving threshold {HALVE_AT} cycles")
    emit()
    held = halved = 0
    for run in range(1, runs + 1):
        session = Session(ELF)
        try:
            if session.expect(b"Cynthion RISC-V SoC", 5.0) is None:
                emit(f"  run {run}: never booted")
                continue
            # Not a timeout: an idle period whose length is the variable under
            # study. The shell is not asked anything during it.
            if idle_s:
                session.expect(b"nothing this can ever match", idle_s)
            if straddle and not wait_for_window(session, straddle):
                emit(f"  run {run}: window never came within {straddle} of "
                     f"the halving")
                continue
            started = time.monotonic()
            before = read_stats(session)
            mark = len(session.snapshot())
            session.send(b"cpu log 20\r")
            expect_line(session, b"log pushed 15 of 20", REPLY_S, mark)
            after = read_stats(session)
            elapsed = time.monotonic() - started
            if before is None or after is None:
                emit(f"  run {run}: `cpu stats` did not parse")
                continue
            product_before = before[0] * before[1]
            product_after = after[0] * after[1]
            a_halving = after[0] < before[0]
            halved += a_halving
            held += product_after > product_before
            emit(f"  run {run}: window {before[0]:>10} -> {after[0]:>10}"
                 f"  busy {before[1] / 100:5.2f}% -> {after[1] / 100:5.2f}%"
                 f"  product {product_before:>14} -> {product_after:>14}"
                 f"  {'HALVED' if a_halving else '      '}"
                 f"  {'holds' if product_after > product_before else 'FAILS'}"
                 f"  {elapsed * 1000:6.0f} ms")
        finally:
            session.close()
    emit()
    emit(f"busy: the assertion held in {held}/{runs} run(s); the window "
         f"shrank in {halved}/{runs}")
    return held


def probe_prompt(runs):
    """How long `command()`'s completion sentinel takes to match, if it does.

    `soc_test.PROMPT` is `\\r\\n> `, and the shell's prompt carries a timestamp
    and a console name in front of the `> `. If it never matches, every
    `command()` pays REPLY_S for nothing -- and the two `cpu stats` readings
    #363 compares end up seconds apart.
    """
    emit(f"prompt: {runs} command(s), PROMPT={PROMPT!r}, budget {REPLY_S}s")
    emit()
    matched = 0
    session = Session(ELF)
    try:
        if session.expect(b"Cynthion RISC-V SoC", 5.0) is None:
            emit("never booted")
            return 0
        session.send(b"\r")
        for run in range(1, runs + 1):
            mark = len(session.snapshot())
            session.send(b"cpu stats\r")
            started = time.monotonic()
            needle = expect_line(session, b"poll     every", REPLY_S, mark)
            needle_s = time.monotonic() - started
            at = session.expect(PROMPT, REPLY_S, mark)
            prompt_s = time.monotonic() - started
            matched += at is not None
            emit(f"  command {run}: last line at {needle_s * 1000:7.1f} ms, "
                 f"PROMPT {'at' if at is not None else 'NOT FOUND after'} "
                 f"{prompt_s * 1000:7.1f} ms")
        emit()
        emit(f"  tail of the reply: {show(session.snapshot()[-40:])!r}")
    finally:
        session.close()
    emit()
    emit(f"prompt: matched in {matched}/{runs} command(s)")
    return matched


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("mode", choices=["dirty", "busy", "prompt"])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--load", type=int, default=0,
                        help="host processes spinning for the duration")
    parser.add_argument("--idle", type=float, default=3.0,
                        help="busy mode: seconds of idle shell before asking")
    parser.add_argument("--straddle", type=int, default=0, metavar="CYCLES",
                        help="busy mode: wait for a window this close to the "
                             "halving, so it lands between the two readings")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    if not args.no_build:
        failed = build_firmware()
        if failed is not None:
            emit("cargo build (qemu) failed:")
            emit(failed)
            return 1
    if not ELF.exists():
        emit(f"no QEMU image at {ELF.relative_to(ROOT)}; drop --no-build")
        return 1

    _, reap = spinners(args.load)
    if args.load:
        emit(f"load: {args.load} spinning process(es) on {os.cpu_count()} cpu(s)")
    try:
        if args.mode == "dirty":
            probe_dirty(args.runs)
        elif args.mode == "prompt":
            probe_prompt(args.runs)
        else:
            probe_busy(args.runs, args.idle, args.straddle)
    finally:
        reap()
    return 0


if __name__ == "__main__":
    sys.exit(main())
