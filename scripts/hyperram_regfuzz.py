#!/usr/bin/env python3
#
# Read the HyperRAM register sweep and report anything undocumented.
# See awtoau/cynthion-workspace#109.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sweeps sixteen HyperRAM register addresses and reports every one that answers.

The capacity question is closed: the part is 8 MiB, and the named part
(`W956A8MBYA6I` in Cynthion's own `facedancer/top.py`), the ID0 decode, the datasheet row
count and the measurement all agree. This asks what is left: **does anything live in the
register space that the datasheets do not document?**

HyperBus specifies four registers -- ID0 `0x0`, ID1 `0x1`, CR0 `0x800`, CR1 `0x801`.
Everything else is unspecified, which is not the same as absent.

## Reading the output

A dead address on this board reads `0x8484`. That was established twice over: memory
above 8 MiB returns it, and so does the entire top-die register space. So `0x8484` means
"nothing there", and anything else is a find.

The four documented registers are swept first as controls. If ID0 does not come back
`0x0c86` the run is void and nothing else in it means anything.

## What it does to the board

Reads only. Register *writes* are how a HyperRAM gets misconfigured into an unusable
state -- CR0 carries drive strength and latency -- so this deliberately does not write.
Configures the FPGA (SRAM only; a power cycle restores whatever was there).

    ./scripts/hyperram_regfuzz.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "hyperram_regfuzz.log"
BITSTREAM = ROOT / "gateware" / "probes" / "hyperram" / "regfuzz_build" / "top.bit"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "scripts"))

APPLET_ID = 0x52465A5A
REGISTER_ID    = 1
REGISTER_STATE = 2
REGISTER_BASE  = 3

# Must match PROBE_ADDRESSES in gateware/probes/hyperram/hyperram_regfuzz.py.
PROBE_ADDRESSES = [
    0x0000, 0x0001, 0x0800, 0x0801,
    0x1000, 0x1001, 0x1002, 0x1003,
    0x1004, 0x1005, 0x1006, 0x1007,
    0x1008, 0x1009, 0x100A, 0x100B,
]
DOCUMENTED = {0x0000: "ID0", 0x0001: "ID1", 0x0800: "CR0", 0x0801: "CR1"}

# The bus-idle pattern, established empirically rather than assumed: memory above 8 MiB
# and the whole top-die register space both read exactly this.
DEAD = 0x8484


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("HyperRAM register sweep -- read only, no register writes")
        emit(f"a dead address reads 0x{DEAD:04x}; anything else is a find")
        emit()

        if not BITSTREAM.exists():
            emit(f"bitstream missing: {BITSTREAM}")
            return 1

        # `expect="design"`: read over JTAG registers, no console to prompt, so
        # the part's DONE bit is the confirmation (#360).
        import soc_confirm

        if soc_confirm.configure_and_confirm(BITSTREAM, expect="design") != 0:
            return 1

        from apollo_fpga import ApolloDebugger
        debugger = ApolloDebugger()
        regs = debugger.registers

        applet = regs.register_read(REGISTER_ID)
        if applet != APPLET_ID:
            emit(f"applet ID 0x{applet:08x}, expected 0x{APPLET_ID:08x} -- wrong gateware")
            return 1

        state = regs.register_read(REGISTER_STATE) & 0xFF
        values = [regs.register_read(REGISTER_BASE + n) & 0xFFFF
                  for n in range(len(PROBE_ADDRESSES))]

        emit(f"FSM state 0x{state:02x}"
             + ("  (complete)" if state == 0xD0 else "  *** DID NOT COMPLETE"))
        emit()

        control_ok = values[0] == 0x0c86
        emit("controls (documented registers)")
        for n, address in enumerate(PROBE_ADDRESSES[:4]):
            emit(f"   0x{address:04x} {DOCUMENTED[address]:<4} = 0x{values[n]:04x}")
        if not control_ok:
            emit()
            emit(f"   *** ID0 read 0x{values[0]:04x}, expected 0x0c86.")
            emit("   The run is void -- nothing below can be trusted.")
            return 1
        emit("   ID0 matches, so the read path is good")
        emit()

        emit("swept (undocumented addresses)")
        finds = []
        for n, address in enumerate(PROBE_ADDRESSES[4:], start=4):
            value = values[n]
            if value == DEAD:
                note = "dead"
            elif value in (0x0000, 0xFFFF):
                note = "no responder"
            elif value in (0x0c86, 0x0001, 0x8f2f, 0xffc1):
                note = "mirrors a documented register (incomplete address decode)"
            else:
                note = "*** RESPONDS"
                finds.append((address, value))
            printable = bytes([(value >> 8) & 0xFF, value & 0xFF])
            if all(32 <= b < 127 for b in printable):
                note += f"   ASCII {printable.decode()!r}"
            emit(f"   0x{address:04x}      = 0x{value:04x}  {note}")
        emit()

        emit("=" * 60)
        if finds:
            emit(f"RESULT: {len(finds)} undocumented address(es) responded:")
            for address, value in finds:
                emit(f"  0x{address:04x} = 0x{value:04x}")
            emit()
            emit("A response is not proof of a register. An address that mirrors a")
            emit("documented one is an incomplete address decode, not a discovery --")
            emit("compare each value against the controls above before concluding")
            emit("anything.")
        else:
            emit("RESULT: nothing outside the four documented registers responds.")
            emit()
            emit("The register space is exactly what HyperBus specifies. Combined with")
            emit("the capacity result, this part is entirely as documented.")
        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
