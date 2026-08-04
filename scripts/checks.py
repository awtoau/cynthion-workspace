#!/usr/bin/env python3
#
# One PASS/FAIL harness, and every check is timed. See #176.
# SPDX-License-Identifier: BSD-3-Clause

"""
The shared check harness for the simulations and the shell suite.

## Why every check is timed

`dev.log`'s timestamps showed `soc_test` emitting a PASS every **20.1 ms**, to
the tenth of a millisecond, regardless of what the check did. That is not work,
it is a poll interval -- and at 98 checks it is two seconds of a six-second run
spent asleep. `soc_jtag_stage_sim` was worse at 46.7 ms per check,
`soc_serial_sim` 12.5 ms; the genuinely computational simulations sit at 0.1 ms.

Nothing reported this because nothing measured it. The suite's own summary gave
one wall-clock number per simulation, which cannot separate "this simulation
does a lot" from "this simulation waits a lot" -- and those want opposite fixes.
Throwing cores at the second one just buys more sleeping in parallel.

So each check carries its own elapsed time, and the summary names the slowest.
The number to watch is not the total, it is whether a harness's per-check cost
is FLAT: a flat cost is a timeout, a varying one is work.

That distinction paid immediately. Of the three, only `soc_test` was actually
asleep -- 20.1 ms of poll, now 0.2 ms of event-driven wait. The other two were
doing the work they looked like they were doing, in Amaranth's simulator, and
the one fixed interval among them was a 400-cycle FIFO drain worth 12%. Guessing
from wall clock would have got all three wrong; see #175 and their docstrings.

## Nine copies became one

`class Checks` was defined nine times across `scripts/`, plus a tenth private
`check()` inside `soc_test.py` -- same shape, no shared format, and no place to
put a change like this one. Same disease `devlog` cured for logging.

## The interface is unchanged

`checks.check(what, ok, detail="")` is what every call site already used, so
conversion is an import rather than an edit.
"""

from __future__ import annotations

import time

# A per-check cost this flat is a timer rather than work, so the summary calls
# it out. Chosen as "well above the 0.1 ms the computational simulations show,
# and well below the 12.5 ms of the cheapest harness that is actually waiting" --
# it separates the two populations that were measured, and is not tuned finer
# than that evidence supports.
SUSPICIOUS_FLAT_MS = 2.0

# How many of the slowest checks to name. Enough to see a pattern, few enough
# that a passing run stays readable.
SLOWEST_SHOWN = 5


class Checks:
    """PASS/FAIL reporting, one line per assertion, each one timed.

    `emit` is any single-argument printer -- `devlog.emit` in the converted
    scripts. Passing it in rather than importing keeps this usable from a
    harness that wants its output somewhere else.
    """

    def __init__(self, emit):
        self._emit = emit
        self.passed = 0
        self.failures = []
        # (elapsed_ms, description) for every check, in order.
        self.timings = []
        self._last = time.perf_counter()

    def check(self, what, ok, detail="", measurement=""):
        """Record one assertion. Returns `ok`, so it can be used in a condition.

        `detail` says why it FAILED and is printed only then. `measurement` is a
        number worth seeing either way -- "240 cycles against 240 predicted" --
        and prints on a pass too. `qspi_burst_sim` had 27 of those and its local
        class printed them unconditionally; folding them into `detail` would
        have silently dropped every one on a green run.
        """
        now = time.perf_counter()
        elapsed_ms = (now - self._last) * 1000
        self._last = now
        self.timings.append((elapsed_ms, what))

        line = f"  {'PASS' if ok else 'FAIL'}  {what}"
        if not ok and detail:
            line += f"\n        {detail}"
        if measurement:
            line += f"\n        {measurement}"
        self._emit(line)

        if ok:
            self.passed += 1
        else:
            self.failures.append(what)
        return ok

    def note(self, text):
        """Diagnostic output that is not an assertion.

        Kept off the timing list: a printed measurement is not a check, and
        counting it would make the per-check cost look like work.
        """
        self._emit(f"        {text}")
        self._last = time.perf_counter()

    def summary(self, emit=None):
        """Totals, the slowest checks, and a flat-cost warning. Returns the exit code."""
        emit = emit or self._emit
        emit("")
        if self.failures:
            emit(f"  {len(self.failures)} FAILED: {', '.join(self.failures)}")
        else:
            emit(f"  all {self.passed} checks passed")

        if not self.timings:
            return 1 if self.failures else 0

        total_ms = sum(ms for ms, _ in self.timings)
        emit(f"  {len(self.timings)} checks in {total_ms / 1000:.2f}s")

        ordered_ms = sorted(ms for ms, _ in self.timings)
        emit(f"  median {ordered_ms[len(ordered_ms) // 2]:.1f}ms per check")

        for elapsed_ms, what in sorted(self.timings, reverse=True)[:SLOWEST_SHOWN]:
            if elapsed_ms >= 1.0:
                emit(f"    {elapsed_ms:7.1f}ms  {what}")

        # The flat-cost test: if the median and the spread agree closely, the
        # harness is waiting on a fixed interval rather than doing varying work.
        # It never fired for `soc_test`, whose 20.1 ms poll was real but whose
        # tail (two 1.7 s idle waits) widened the spread past the gate -- which
        # is why the median above is printed unconditionally, and why the poll
        # was found by reading that median rather than by this warning. The
        # three intervals are gone as of #175; the gate stays for the next one.
        ordered = ordered_ms
        median = ordered[len(ordered) // 2]
        if median >= SUSPICIOUS_FLAT_MS:
            lower = ordered[len(ordered) // 4]
            upper = ordered[3 * len(ordered) // 4]
            if upper - lower < median * 0.5:
                emit(f"    NOTE {median:.1f}ms per check and barely varying -- "
                     f"that is a poll interval, not work.")

        return 1 if self.failures else 0
