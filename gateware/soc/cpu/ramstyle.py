# Memory mapping of the generated VexiiRiscv core.
# SPDX-License-Identifier: BSD-3-Clause

"""Force named core memories onto block RAM by text edit of the generated Verilog.

yosys maps a memory to distributed RAM when its LUTRAM cost (4 per TRELLIS_DPR16X4) is under
the block cost (128 per DP16KD): anything of 32 DPR16X4 or fewer. A `ram_style` attribute
overrides that, which the Scala generator only emits for a few memories. Mapping change only:
widths, depths and the RTL read/write behaviour are untouched.

`BLOCK` rows: (name, declaration regex, flag that legitimately removes the memory or None,
flag the memory needs or None). A row is skipped when its removing flag is present or its
needed flag is absent (cacheless cores have no L1 tags).
"""

import re

BLOCK = [
    ("regfile", r"asMem_ram", "--regfile-reg-based", None),
    ("fetch_tags", r"FetchL1Plugin_logic_ways_\d+_mem", None, "--with-fetch-l1"),
    ("lsu_tags", r"LsuL1Plugin_logic_ways_\d+_mem", None, "--with-lsu-l1"),
    ("csr_ram", r"CsrRamPlugin_logic_mem", None, None),
]


class MissingMemory(Exception):
    """A memory listed for block RAM is not in the generated core."""


def force_block(text, patterns):
    """`text` with `(* ram_style = "block" *)` on each declaration matching a pattern."""
    for pattern in patterns:
        declaration = re.compile(
            rf"^(\s*)(?:\(\*[^)]*\*\)\s*)?(reg \[\d+:0\] {pattern} \[0:\d+\];)", re.M)
        text, count = declaration.subn(r'\1(* ram_style = "block" *) \2', text)
        if not count:
            raise MissingMemory(f"no declaration matches {pattern!r}")
    return text


def apply(text, flags):
    """`force_block` over `BLOCK`, skipping rows whose removing flag is in `flags`."""
    return force_block(text, [regex for _, regex, removed_by, needs in BLOCK
                              if removed_by not in flags
                              and (needs is None or needs in flags)])
