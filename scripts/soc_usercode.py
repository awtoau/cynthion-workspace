#!/usr/bin/env python3
#
# What is actually on the part: IDCODE and USERCODE, over JTAG. #447, #450.
# SPDX-License-Identifier: BSD-3-Clause

"""
Read the part's own two identifiers, and say which build they are.

    ./scripts/soc_usercode.py                 # read the board, resolve the build
    ./scripts/soc_usercode.py --expect <dir>  # compare against a build directory

## Why the host

The identity left the fabric (#447): no CSR carries it, so the CPU cannot answer
"which bitstream am I?" at all. USERCODE can -- it is a command in the
configuration stream, read back with JTAG opcode 0xc0 -- and the party that
loaded the bitstream is the one that knows what it loaded.

`repos/apollo/apollo_fpga/ecp5.py` has had `READ_USERCODE`, `USERCODE_LENGTH`
and `_read_usercode()` since before this; nothing called it.

## What it does NOT do

No `ISC_ENABLE`, no erase, no configure. `_read_usercode` and `_read_status` are
reads, so a running design survives being asked -- but JTAG_START does drive the
pins the design's second UART sits on, which is why `soc_confirm.py` asks this
question at a moment when JTAG has just been driven anyway.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

import usercode_map  # noqa: E402


def read_identity(debugger=None):
    """`(idcode, usercode)` from the part, either of them None if unread.

    `debugger` is injectable so the decode can be tested without a board; the
    default opens Apollo.
    """
    if debugger is None:
        import apollo_fpga

        debugger = apollo_fpga.ApolloDebugger()
        close = True
    else:
        close = False

    idcode = usercode = None
    try:
        with debugger.jtag as chain:
            codes = chain.enumerate(return_idcodes=True)
            idcode = codes[0] if codes else 0
            if idcode not in (0, 0xFFFFFFFF):
                programmer = debugger.create_jtag_programmer(chain)
                usercode = programmer._read_usercode()
    finally:
        if close:
            try:
                debugger.close()
            except Exception:
                pass
    return idcode, usercode


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect", type=Path,
                        help="a build directory whose usercode.json this must "
                             "match")
    args = parser.parse_args()

    idcode, usercode = read_identity()
    print(f"idcode   {idcode:#010x}" if idcode is not None else "idcode   unread")
    if usercode is None:
        print("usercode unread -- the part did not answer JTAG")
        return 1
    record = usercode_map.lookup(usercode)
    print(f"usercode {usercode_map.describe(usercode, record)}")

    if args.expect:
        want = usercode_map.read_build_record(args.expect)
        if want is None:
            print(f"no {usercode_map.RECORD_NAME} in {args.expect}")
            return 1
        if want["usercode"] != usercode:
            print(f"STALE: the part carries {usercode:#010x}, "
                  f"{args.expect} was built as {want['usercode']:#010x}")
            return 1
        print(f"matches {args.expect}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
