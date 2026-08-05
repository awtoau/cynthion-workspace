#!/usr/bin/env python3
#
# Is our verbatim copy of an upstream module still verbatim?
# SPDX-License-Identifier: BSD-3-Clause

"""Check `ObservablePHY` against the `luna_soc` PHY it copies.

`ecp5-test/riscv/vexii_flash.py`'s `ObservablePHY` re-implements
`luna_soc.gateware.core.spiflash.phy.SPIPHYController.elaborate` line for line,
adding only assignments that expose internal signals to `FlashILA`. Its own
docstring states the obligation:

> RE-IMPLEMENTED RATHER THAN PATCHED, and the copy is deliberate: the point of
> this class is to observe what the real PHY does, so any behavioural difference
> between this and upstream would invalidate the measurement. [...] If upstream
> changes, this must be re-synced or it is measuring a different circuit than
> the one that ships.

Nothing checked that. `luna_soc` is a pinned fork and pins move, and the failure
is silent by construction: a re-synced upstream and a stale copy both elaborate,
both pass timing, and the ILA reports a waveform from a circuit that is no
longer the one on the pins.

This compares the two statement by statement, ignoring whitespace, comments and
quote style, and ignoring the `self.o_*` assignments that are the entire
intended difference. It exits non-zero on any other difference.

    ./scripts/soc_upstream_copy_check.py

Output is mirrored to ./tmp/logs/soc_upstream_copy_check.log.
"""

import difflib
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_upstream_copy_check.log"
OURS = ROOT / "ecp5-test" / "riscv" / "vexii_flash.py"


def _canonical(text):
    """One statement with every formatting choice removed.

    Both trees align `.eq(` calls into columns and wrap long expressions at
    different points, so whitespace next to punctuation is noise. Quote style
    is normalised for the same reason: upstream writes `'XFER'` where the copy
    writes `"XFER"`, and those are the same string.

    Nothing here removes anything that could change behaviour -- an identifier
    cannot lose a space without becoming a different identifier, and this only
    strips space that is ADJACENT to a delimiter.
    """
    text = re.sub(r"\s+", " ", text).replace("'", '"')
    return re.sub(r"\s*([().,\[\]+])\s*", r"\1", text)


def statements(lines):
    """Comparable statements: no blanks, no comments, canonical spacing.

    Continuation lines are joined onto the statement they continue, because the
    copy wraps differently from upstream and a line break is not a behavioural
    difference. A line is a continuation when the brackets opened so far have
    not been closed.
    """
    out, buffer, depth = [], "", 0
    for line in lines:
        text = line.split("#", 1)[0].strip() if "#" in line else line.strip()
        if not text:
            continue
        buffer = f"{buffer} {text}".strip() if buffer else text
        depth += text.count("(") + text.count("[") - text.count(")") - text.count("]")
        if depth > 0:
            continue
        depth = 0
        out.append(_canonical(buffer))
        buffer = ""
    if buffer:
        out.append(_canonical(buffer))
    return out


def ours_elaborate():
    """`ObservablePHY.elaborate`'s body, read from the source file.

    From the file rather than by `inspect.getsource` on the class, so this
    checks the source in the tree rather than whatever happens to be imported.
    """
    lines = OURS.read_text().splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("class ObservablePHY"))
    begin = next(i for i, l in enumerate(lines[start:], start)
                 if l.strip().startswith("def elaborate"))
    end = next(i for i, l in enumerate(lines[begin:], begin)
               if l.strip() == "return m")
    return lines[begin:end + 1]


def main():
    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    from luna_soc.gateware.core.spiflash.phy import SPIPHYController
    import luna_soc

    upstream = statements(
        inspect.getsource(SPIPHYController.elaborate).splitlines())
    copy = statements(ours_elaborate())

    # The whole intended difference: the `fsm` handle the exposure needs, and
    # the assignments that expose. Everything else must match.
    # Every statement in the copy that only assigns `self.o_*` is instrumentation
    # and is meant to be there. `as fsm:` on the FSM is the one further change --
    # a handle, so `fsm.ongoing(...)` can be read; it adds no logic of its own.
    exposed = re.compile(r"^m\.d\.comb\+=\[(self\.o_\w+\.eq\(.*?\),)+\]$")
    fsm_handle = "with m.FSM(domain=self._domain)as fsm:"
    added = [s for s in copy if s not in upstream]
    unexplained = [s for s in added
                   if not exposed.match(s) and s != fsm_handle]
    missing = [s for s in upstream if s not in copy
               and s != "with m.FSM(domain=self._domain):"]

    version = getattr(luna_soc, "__version__", "unknown")
    emit(f"luna_soc {version} at {Path(luna_soc.__file__).parent}")
    emit(f"upstream SPIPHYController.elaborate: {len(upstream)} statements")
    emit(f"ObservablePHY.elaborate:             {len(copy)} statements")
    emit()

    if not unexplained and not missing:
        emit("IN SYNC: every statement upstream has, the copy has, and every")
        emit("statement the copy adds is an instrumentation assignment.")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text("\n".join(out) + "\n")
        return 0

    emit("OUT OF SYNC -- the ILA is measuring a different circuit than ships.")
    for statement in missing:
        emit(f"  upstream has, copy does not:  {statement}")
    for statement in unexplained:
        emit(f"  copy has, upstream does not:  {statement}")
    emit()
    emit("Full diff:")
    for line in difflib.unified_diff(upstream, copy, "upstream", "copy",
                                     lineterm="", n=1):
        emit(f"  {line}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
