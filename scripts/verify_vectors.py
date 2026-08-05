#!/usr/bin/env python3
"""Verify the SAMD11 vector table in a built Apollo firmware.elf resolves to the
real interrupt handlers rather than Dummy_Handler.

This exists because enabling -flto on a Cortex-M image whose vector table is
built from weak aliases is a known way to produce a binary that links cleanly
and then does nothing: the LTO plugin can resolve a weak alias before the
linker script's KEEP() applies, silently binding a slot to Dummy_Handler.
Checking the linked image is the only way to know it did not happen.

Writes to ./tmp/logs/dev.log as well as stdout.
"""
import re
import struct
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
ELF = WORKSPACE / "repos/apollo/firmware/_build/cynthion_d11/firmware.elf"

sys.path.insert(0, str(WORKSPACE / "scripts"))

import armtools  # noqa: E402
from devlog import emit  # noqa: E402

# Vector table layout for the SAMD11, in slot order from the base of .text.
# Index is the word offset from the start of exception_table.
# Only the slots this firmware actually arms are asserted on; the rest are
# reported for eyeballing.
SLOTS = {
    0: "_estack (initial SP)",
    1: "Reset_Handler",
    2: "NonMaskableInt_Handler",
    3: "HardFault_Handler",
    11: "SVCall_Handler",
    14: "PendSV_Handler",
    15: "SysTick_Handler",
    16: "PM_Handler",
    17: "SYSCTRL_Handler",
    18: "WDT_Handler",
    19: "RTC_Handler",
    20: "EIC_Handler",
    21: "NVMCTRL_Handler",
    22: "DMAC_Handler",
    23: "USB_Handler",
    24: "EVSYS_Handler",
    25: "SERCOM0_Handler",
    26: "SERCOM1_Handler",
    27: "SERCOM2_Handler",
    28: "TCC0_Handler",
    29: "TC1_Handler",
    30: "TC2_Handler",
    31: "ADC_Handler",
    32: "AC_Handler",
    33: "DAC_Handler",
    34: "PTC_Handler",
}

# Handlers this firmware provides a real implementation for. Each of these
# MUST NOT resolve to Dummy_Handler, or the corresponding feature is dead.
REQUIRED = {
    "Reset_Handler":   "boot",
    "SysTick_Handler": "board_millis timebase",
    "USB_Handler":     "USB device stack (CDC console + vendor requests)",
    "EIC_Handler":     "FPGA_ADV edge counting (EIC advertisement mode)",
    "SERCOM1_Handler": "FPGA_ADV UART receive (heartbeat + command responses)",
    "TC1_Handler":     "FPGA_ADV transmit bit clock (sideband master)",
}


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def main():
    if not ELF.exists():
        emit(f"FAIL: {ELF} does not exist - build first")
        return 1

    # Resolved beside the compiler rather than by PATH order: the ELF is from
    # GCC 15.2.0 and the PATH nm here is from 2023. See #191.
    nm = armtools.tool("nm")
    if nm is None:
        emit("FAIL: no arm-none-eabi-nm anywhere")
        return 1
    armtools.report(emit, "nm")
    emit()

    # Map address -> symbol names.
    addr_to_names = {}
    name_to_addr = {}
    for line in sh(nm, ELF.as_posix()).splitlines():
        m = re.match(r"^([0-9a-fA-F]+)\s+(\S)\s+(\S+)$", line)
        if not m:
            continue
        addr, _kind, name = int(m.group(1), 16), m.group(2), m.group(3)
        addr_to_names.setdefault(addr, []).append(name)
        name_to_addr[name] = addr

    base = name_to_addr.get("exception_table")
    if base is None:
        emit("FAIL: no exception_table symbol in the ELF")
        return 1

    dummy = name_to_addr.get("Dummy_Handler")

    # Pull the raw bytes of the vector table out of the .bin, which is a flat
    # image of flash starting at the same base as exception_table.
    binpath = ELF.with_suffix(".bin")
    image = binpath.read_bytes()
    nwords = max(SLOTS) + 1
    words = struct.unpack_from("<%dI" % nwords, image, 0)

    emit(f"ELF:            {ELF}")
    emit(f"exception_table 0x{base:08x}")
    emit(f"Dummy_Handler   0x{dummy:08x}" if dummy else "Dummy_Handler   <absent>")
    emit()
    emit(f"{'slot':>4}  {'name':<24} {'value':>10}  resolves to")
    emit("-" * 72)

    failures = []
    for idx in sorted(SLOTS):
        name = SLOTS[idx]
        val = words[idx]
        if idx == 0:
            emit(f"{idx:>4}  {name:<24} 0x{val:08x}  (stack top)")
            continue
        target = val & ~1  # strip the Thumb bit
        if val == 0:
            emit(f"{idx:>4}  {name:<24} 0x{val:08x}  <reserved/zero>")
            continue
        names = addr_to_names.get(target, [])
        # Prefer the exact expected name if it lives at this address.
        if name in names:
            shown = name
        elif names:
            shown = "/".join(sorted(names))
        else:
            shown = "<unknown address>"

        is_dummy = dummy is not None and target == dummy
        mark = ""
        if name in REQUIRED:
            if is_dummy:
                mark = "  <== FAIL: bound to Dummy_Handler"
                failures.append((name, REQUIRED[name]))
            elif name not in names:
                mark = "  <== FAIL: does not resolve to its own symbol"
                failures.append((name, REQUIRED[name]))
            else:
                mark = "  OK"
        emit(f"{idx:>4}  {name:<24} 0x{val:08x}  {shown}{mark}")

    emit()
    if failures:
        emit(f"RESULT: FAIL - {len(failures)} required handler(s) not wired up:")
        for name, why in failures:
            emit(f"  {name}: {why}")
        rc = 1
    else:
        emit(f"RESULT: PASS - all {len(REQUIRED)} required handlers wired to real code")
        rc = 0

    return rc


if __name__ == "__main__":
    sys.exit(main())
