#!/usr/bin/env python3
#
# Refuse to let an interrupt handler print.
# See awtoau/cynthion-workspace#122.
# SPDX-License-Identifier: BSD-3-Clause

"""
Fails if any module containing an interrupt handler can reach a console.

    python3 scripts/soc_irq_log_check.py
    python3 scripts/soc_irq_log_check.py -v      # list every file inspected

Exit status 0 if every rule holds. Output goes to the terminal and to
`tmp/logs/soc_irq_log_check.log`. Run as the `irqlog` check in
`scripts/check.py`.

## Why a grep rather than the compiler

`Uart::put` waits for `LSR.THRE`, so printing from an interrupt handler blocks
it for as long as the host takes to drain a FIFO. On a level-sensitive shared
source that is not a delay -- the line is still asserted, the interrupt is taken
again the instant the handler returns, and the board presents as a dead CPU with
a running clock. This project has mistaken that for dead gateware more than
once.

Most of the rule is enforced by ownership, and that is where it belongs: `main`
owns the `Uart` values, a handler is a free function with nothing to be handed
one from, and `src/irq.rs` uses `UartRx`, which has no transmit method and no
`core::fmt::Write`. A `write!` in that file does not compile.

What ownership cannot do is stop the next person importing `Uart` into a handler
module. Rust's privacy is per-module-tree: a private item in the crate root is
visible to every descendant, so there is no way to make `Uart` unnameable from a
sibling module without splitting the crate in two -- which would be a large
change to prevent a small one. So the remainder is checked here.

## What is checked, and what it does not cover

Handler modules are DISCOVERED rather than listed: any file carrying a
`#[riscv_rt::core_interrupt]`, `#[riscv_rt::exception]` or `#[riscv_rt::external]`
attribute is one. A new handler in a new file is therefore covered the day it is
written, which a hardcoded list would not be.

This is a textual check and it says so. It cannot see through a re-export, a
type alias or a macro that expands to a `Uart`, and it is not trying to: those
are all things somebody would have to go out of their way to do, and the point
of a guard like this is to catch the shortcut taken in a hurry, not to be a
proof. The proof is the ownership above.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_irq_log_check.log"
SRC = ROOT / "firmware" / "cynthion-soc" / "src"

# What makes a file a handler module.
HANDLER_ATTRIBUTES = (
    "riscv_rt::core_interrupt",
    "riscv_rt::exception",
    "riscv_rt::external_interrupt",
)

# What a handler module may not contain. Each is (pattern, why).
#
# `Uart` is matched as a whole word so that `UartRx` -- the receive-only view
# that has no transmit method at all -- is allowed. That distinction is the
# entire mechanism, so a rule that banned both would force the receive path back
# onto the writable type and defeat itself.
FORBIDDEN = [
    (r"\bwrite!", "formats and transmits; use log_from_irq!"),
    (r"\bwriteln!", "formats and transmits; use log_from_irq!"),
    (r"\bprint!", "there is no global console, and there must not be"),
    (r"\bprintln!", "there is no global console, and there must not be"),
    (r"fmt::Write", "the trait whose only method transmits"),
    (r"\bUart\b", "the writable console type; irq code uses UartRx"),
]

# Rules that hold for the whole crate, not only for handler modules.
CRATE_FORBIDDEN = [
    (r"\bstatic\s+mut\b",
     "a static mut is reachable from a handler, which is exactly how a "
     "console becomes reachable from one"),
    (r"macro_rules!\s*print\b",
     "a global print macro is a console reachable from anywhere"),
    (r"macro_rules!\s*println\b",
     "a global print macro is a console reachable from anywhere"),
]

# Files exempt from the crate-wide rules, with the reason.
#
# `events.rs` is the deferred log itself: its drain runs in normal context, is
# handed a `Uart` by the caller, and does all the formatting a handler is not
# allowed to do. That is the whole design, so the file that implements it cannot
# be subject to the rule it implements. It is NOT a handler module -- it carries
# no handler attribute -- so only the crate-wide rules would otherwise apply, and
# none of those actually match it; the entry is here so that adding one later
# does not silently exempt it.
EXEMPT = {}


def strip_comments(text):
    """Remove `//` line comments so prose about `write!` is not a violation.

    Crude on purpose: it does not understand strings or block comments. A false
    NEGATIVE here would need `//` inside a string literal on the same line as a
    real violation, which is not a thing this codebase does; a false positive
    would be worse, because a guard that cries wolf gets disabled.
    """
    return re.sub(r"//.*", "", text)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every file inspected")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        if not SRC.is_dir():
            emit(f"no firmware source at {SRC.relative_to(ROOT)}")
            return 1

        problems = []
        handlers = []

        for path in sorted(SRC.glob("*.rs")):
            raw = path.read_text()
            code = strip_comments(raw)
            name = path.name

            is_handler = any(attribute in code
                             for attribute in HANDLER_ATTRIBUTES)
            if is_handler:
                handlers.append(name)

            rules = list(CRATE_FORBIDDEN)
            if is_handler:
                rules += FORBIDDEN

            for pattern, why in rules:
                if name in EXEMPT and pattern in EXEMPT[name]:
                    continue
                for number, line in enumerate(code.splitlines(), 1):
                    if re.search(pattern, line):
                        problems.append(
                            f"{path.relative_to(ROOT)}:{number}: "
                            f"{pattern} -- {why}\n"
                            f"      {raw.splitlines()[number - 1].strip()}")

            if args.verbose:
                emit(f"  {name:<12} {'handler' if is_handler else ''}")

        # A check that finds nothing because it looked at nothing is worse than
        # no check: it reports success forever after a rename. So the discovery
        # itself is asserted.
        if not handlers:
            emit("no interrupt handler found in "
                 f"{SRC.relative_to(ROOT)} -- this check inspected nothing.")
            emit("Either the handler attribute changed name, or the handler "
                 "moved out of this crate. Fix HANDLER_ATTRIBUTES; do not "
                 "leave a guard that passes because it is looking nowhere.")
            return 1

        emit(f"handler module(s): {', '.join(handlers)}")
        emit(f"{len(list(SRC.glob('*.rs')))} file(s) checked")

        if problems:
            emit()
            emit("A handler that prints spins on a UART FIFO inside an "
                 "interrupt. Use `log_from_irq!` and let the main loop format "
                 "it -- see firmware/cynthion-soc/src/events.rs.")
            emit()
            for problem in problems:
                emit(f"  {problem}")
            emit()
            emit(f"{len(problems)} violation(s)")
            emit(f"log: {LOG.relative_to(ROOT)}")
            return 1

        emit("no interrupt handler can reach a console")
        emit(f"log: {LOG.relative_to(ROOT)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
