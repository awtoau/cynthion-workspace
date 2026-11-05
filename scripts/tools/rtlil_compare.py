#!/usr/bin/env python3
# Compare two RTLIL files, ignoring `src` attributes. #609.
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/tools/rtlil_compare.py BEFORE.il AFTER.il

- `src` attributes carry file:line, so any source edit moves them; everything else is the netlist.
- Names ending in an `id()` (9+ digits) are normalised: they differ between any two processes.
- Exit 0: identical apart from `src`. Exit 1: prints the first differing lines.
- Log: ./tmp/logs/rtlil_compare.log.
"""

import hashlib
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "rtlil_compare.log"
# luna 0.2.3 names endpoints `<Class>_{id(obj)}`, which differs per process (#441).
ID_SUFFIX = re.compile(r"_\d{9,}\b")


def stripped(path):
    return [ID_SUFFIX.sub("_<id>", line) for line in Path(path).read_text().splitlines()
            if not line.lstrip().startswith("attribute \\src ")]


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    before, after = sys.argv[1:3]
    left, right = stripped(before), stripped(after)
    for name, lines in ((before, left), (after, right)):
        digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        logging.info("%s  %d lines without src  %s", digest, len(lines), name)
    if left == right:
        logging.info("IDENTICAL apart from src attributes")
        return 0
    shown = 0
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            logging.info("  %d: - %s", index + 1, a[:120])
            logging.info("  %d: + %s", index + 1, b[:120])
            shown += 1
            if shown == 8:
                break
    logging.info("DIFFERENT (%d vs %d lines)", len(left), len(right))
    return 1


if __name__ == "__main__":
    sys.exit(main())
