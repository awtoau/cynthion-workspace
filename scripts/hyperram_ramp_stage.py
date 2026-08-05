#!/usr/bin/env python3
#
# Stage a 0-255 byte ramp into HyperRAM over JTAG, so a read fault can be told
# from a write fault. See #186.
# SPDX-License-Identifier: BSD-3-Clause

"""
Writes a known ramp through the JTAG sink, then the shell's `hr ramp` verifies it.

    ./scripts/hyperram_ramp_stage.py          # stage, release reset, verify

## Why this exists

`hr ramp w` writes the ramp through the memory window and reads it back, so a
mismatch could be either direction. This writes it through the JTAG sink instead:
a different arbiter owner, a different data source, single transactions rather
than bursts, and the CPU held in reset throughout.

**IT DOES NOT FULLY SEPARATE READ FROM WRITE, and an earlier version of this
docstring claimed it did.** JTAG staging goes through the same `BootRAM` arbiter
and the same `psram.write_data` assignment as the window, so a fault on that line
corrupts both. What the comparison does show is whether two writers that differ
in `streaming`, in `wide`, and in burst length produce the SAME corruption -- and
they do, byte for byte, which points at what they share rather than at either
one. Every earlier attempt wrote and read through
paths sharing the same code, and a shared fault cancels: a cross-check that
writes `a5c31234` and reads `a5c31234` proves the two agree, not that either is
right. The same cancellation hid an inverted half-swap for most of a day.

## Why a ramp rather than a pattern

Every byte names its own position. A displacement, a duplication, a dropped half
and a byte-order error each leave a different, directly readable signature --
where a single 32-bit value gives four bytes and every conclusion is inferred.
`hr ramp w` produced `00 00 06 07 | 00 00 0a 0b` on the first run, which says
"low half dropped, high half displaced one word" at a glance.

## The address

`hr ramp` reads at byte offset 0x4000 into the window, which is word address
0x2000 in the part's 16-bit units. Both constants are stated in the two places
that use them and must agree; there is no shared header because one side is Rust
and the other is Python.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))

from devlog import emit, log  # noqa: E402

# Must match `RAMP_AT` / `RAMP_LEN` in `firmware/cynthion-soc/src/main.rs`.
RAMP_BYTES = 256
RAMP_WORD = 0x4000 // 2          # the part addresses 16-bit words


def main():
    from apollo_fpga import ApolloDebugger                     # noqa: E402
    from soc_jtag_stage import Sink, SIGNATURE, describe       # noqa: E402

    ramp = bytes(range(RAMP_BYTES))
    log(f"staging a {RAMP_BYTES}-byte ramp at word {RAMP_WORD:#x} over JTAG")

    debugger = ApolloDebugger()
    with debugger.jtag as chain:
        sink = Sink(chain)
        status = sink.status()
        emit(f"sink: {describe(status)}")
        if status["signature"] != SIGNATURE:
            emit("the sink did not answer on ER1 -- is this a build with JTAG "
                 "staging?")
            return 1

        # Hold the CPU across the write, exactly as image staging does: the
        # point is that nothing of this SoC's own write path is involved.
        sink.set_reset(True)
        sink.write(RAMP_WORD, ramp)
        after = sink.status()
        emit(f"staged: {describe(after)}")
        sink.set_reset(False)

    emit("")
    emit("verifying through the READ path only (`hr ramp`, no write):")
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "soc_shell.py"), "hr ramp"],
        capture_output=True, text=True,
    )
    for line in done.stdout.splitlines():
        if "ramp" in line or "want" in line or "correct" in line or "wrong" in line:
            emit(f"  {line.strip()}")

    if "correct -- the path is clean" in done.stdout:
        emit("")
        emit("  a JTAG-written ramp reads back clean, so the WINDOW's write path")
        emit("  is the difference -- JTAG and the window share the read path and")
        emit("  the `psram.write_data` line, and differ in burst behaviour")
    else:
        emit("")
        emit("  same corruption from a different writer. The fault is in what")
        emit("  they SHARE: the read path, or the one write-data assignment both")
        emit("  go through. This run does not distinguish those two.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
