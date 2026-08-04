#!/usr/bin/env python3
"""Probe the three ULPI PHYs directly over the Apollo debug link.

Requires the selftest bitstream to be loaded. For each PHY: read the four ID
registers, then walk a single bit across the eight scratch-register data lines,
repeating to distinguish a hard fault from an intermittent one.

Logs to tmp/logs/dev.log.
"""

import sys
from pathlib import Path

from apollo_fpga import ApolloDebugger
from cynthion.selftest.registers import (
    REGISTER_ID, REGISTER_TARGET_ADDR, REGISTER_AUX_ADDR, REGISTER_CONTROL_ADDR,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from devlog import emit, log  # noqa: E402

EXPECTED_ID = 0x54455354
SCRATCH_REG = 0x16
EXPECTED_PHY_ID = (0x24, 0x04, 0x09, 0x00)   # USB3343: vendor 2404, product 0900
PHYS = (("TARGET", REGISTER_TARGET_ADDR), ("AUX", REGISTER_AUX_ADDR), ("CONTROL", REGISTER_CONTROL_ADDR))
ROUNDS = 3


def phy_reg_read(dut, base, reg):
    dut.registers.register_write(base, reg)
    dut.registers.register_write(base, reg)
    return dut.registers.register_read(base + 1)


def scratch_roundtrip(dut, base, value):
    """Write value to the PHY scratch register, read it back."""
    dut.registers.register_write(base, SCRATCH_REG)
    dut.registers.register_write(base + 1, value)
    dut.registers.register_write(base, SCRATCH_REG)
    return dut.registers.register_read(base + 1)


def probe(dut, name, base):
    emit(f"--- {name} PHY (base {base}) ---")

    try:
        ids = tuple(phy_reg_read(dut, base, r) for r in range(4))
    except Exception as e:
        log(f"  ID read raised: {e}", "ERROR")
        return

    ok = ids == EXPECTED_PHY_ID
    emit(f"  ID regs: {[hex(v) for v in ids]} "
         f"expected {[hex(v) for v in EXPECTED_PHY_ID]}  "
         f"{'ok' if ok else 'MISMATCH'}")

    # Walk one bit across each data line, several rounds.
    for rnd in range(ROUNDS):
        bad = []
        for bit in range(8):
            value = 1 << bit
            try:
                actual = scratch_roundtrip(dut, base, value)
            except Exception as e:
                log(f"  round {rnd} bit {bit}: raised {e}", "ERROR")
                bad.append((bit, "exception"))
                continue
            if actual != value:
                bad.append((bit, actual))
        if bad:
            detail = ", ".join(f"D{b}: wrote {1 << b:#04x} read {a if isinstance(a, str) else hex(a)}"
                               for b, a in bad)
            log(f"  round {rnd}: {len(bad)}/8 data lines bad -- {detail}",
                "ERROR")
        else:
            emit(f"  round {rnd}: all 8 data lines ok")


def main():
    dut = ApolloDebugger()

    device_id = dut.registers.register_read(REGISTER_ID)
    if device_id != EXPECTED_ID:
        log(f"ID register {device_id:#x}, expected {EXPECTED_ID:#x} -- "
            "selftest gateware not loaded?", "ERROR")
        return 1
    emit(f"selftest gateware present (ID {device_id:#x})")

    for name, base in PHYS:
        probe(dut, name, base)

    return 0


if __name__ == "__main__":
    sys.exit(main())
