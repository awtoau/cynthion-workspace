#!/usr/bin/env python3.15t
"""
The authoritative ECP5 opcode table, parsed from Lattice's own programming procedure.

Source:
    ~/lscc/diamond/3.14/data/vmdata/database/xpga/ecp5/LatticeECP5.svp

That file declares 104 opcodes in its <Instruction> block, of which 81 do not appear in
Apollo's `ecp5.py` at all. Apollo's enum was assembled from Project Trellis and covers
only what a configure()/flash() flow needs; the vendor file is the full instruction set.

Two things this parse establishes that matter for the probe:

1. Names and codes are authoritative -- no more guessing from Trellis.
2. The procedure *body* shows how Lattice sequences each command (`SIR Instruction_Length
   TDI(NAME)` then `SDR <n>`), which gives the expected payload length. Where the body
   uses an opcode, PAYLOAD_BITS below records the length Lattice uses.

The interesting negative: LSC_ISCAN (0xDF), LSC_EBR_READ (0xB0), LSC_EBR_WRITE (0xB2),
LSC_READ_CRC (0x60) and LSC_READ_SED_CRC (0xA4) are *declared but never used* anywhere in
the procedure body. Lattice's own tooling never issues them. So for precisely the opcodes
that bear on the open questions here, the vendor file supplies a code and a name and
nothing else -- what they do is an empirical question, which is the point of the probe.

Safety classification
---------------------
The SAFETY field is the gate on what the sweep is allowed to issue. Some ECP5 commands
write one-time-programmable fuses or security cells, and a wrong write there is permanent
and unrecoverable on this part -- there is no erase. Those are marked FORBIDDEN and the
sweep must never issue them, regardless of payload.
"""

import re
from pathlib import Path

SVP_PATH = Path.home() / "lscc/diamond/3.14/data/vmdata/database/xpga/ecp5/LatticeECP5.svp"

# --------------------------------------------------------------------------
# Safety classification
# --------------------------------------------------------------------------
# FORBIDDEN: never issue. Writes OTP fuses, security cells, feature rows, trim, or
#   manufacturing data. Permanent and unrecoverable -- these cells have no erase path.
#   LSC_PROG_OTP (0xF9) is the sharpest example but every one of these can brick or
#   permanently lock the part.
FORBIDDEN_NAMES = {
    "LSC_PROG_OTP",            # one-time-programmable fuses. Irreversible.
    "LSC_PROG_FEABITS",        # feature row: can permanently disable JTAG/readback.
    "LSC_PROG_FEATURE",
    "LSC_PROG_PASSWORD",       # writes a password fuse; locks the device out.
    "LSC_SHIFT_PASSWORD",
    "LSC_PROG_CIPHER_KEY",     # writes the AES key fuse. Irreversible.
    "ISC_PROGRAM_SECURITY",    # sets the security bit: disables readback permanently.
    "LSC_PROGRAM_SECPLUS",     # security plus: same class, stronger.
    "LSC_PROG_TRIM",           # analog trim -- factory calibration. Breaks the part.
    "LSC_PROG_PES",            # programs the electronic signature fuses.
    "LSC_PROG_MES",
    "LSC_PROG_MAIN_RED", "LSC_PROG_MAIN_RCR", "LSC_PROG_MAIN_RMR",
    "LSC_PROG_NV_RED", "LSC_PROG_NV_RMR",
    "LSC_PROG_INCR_NV",        # writes non-volatile config.
    "LSC_PROG_UFM", "LSC_ERASE_UFM",
    "LSC_MANUFACTURE_SHIFT",   # manufacturing mode. Undefined territory.
    "LSC_MFG_MTEST", "LSC_MFG_MTRIM", "LSC_MFG_MDATA",
    "LSC_STORE",               # aliases 0xD0 with LSC_PROG_PES -- non-volatile.
    "ISC_PROGRAM",             # generic non-volatile program.
    "ISC_ERASE_DONE",

    # 0x7D. Named "device control", which sounds inert, but OpenOCD shows it is an
    # erase-*arm*. src/pld/certus.c:lattice_certus_erase_device() issues it at IRPAUSE
    # with an 8-bit DR of 8 (arm), then again at IDLE with 0 (confirm), and only then
    # ISC_ERASE. So it is the first half of a two-step erase handshake, not a register
    # read, and sweeping it with arbitrary payloads is not the same class of risk as
    # reading status.
    #
    # The exposure on an LFE5U-12F is bounded but not fully known. ECP5 configuration is
    # SRAM and `configure` restores it, but the part also carries non-SRAM fuses --
    # BOOTADDR/Multiboot_cfg_Address, and the security/OTP cells, which are permanent.
    # Which fuse banks an armed ISC_ERASE reaches is not documented: the vendor .svp
    # declares 0x7D and never issues it, and OpenOCD's *ECP5* path (src/pld/ecp5.c)
    # never uses it either -- only the Certus path does, and Certus has internal
    # non-volatile config that the ECP5 does not.
    #
    # Two independent implementations both declining to use it on this part, for an
    # operation whose blast radius is unknown, is enough. Not swept.
    "LSC_DEVICE_CTRL",
}

# Status bits named by OpenOCD's ECP5 driver (src/pld/ecp5.c) that Apollo's ecp5.py does
# not name. Recorded because the sweep decodes unknown bits as UNDOC_n, and a second
# implementation's name for a bit is better evidence than "nobody documents it".
#   bit 14 (0x00004000) STATUS_FEA_OTP -- checked before programming alongside error bits
#   bits 6 and 17 (0x00020040) STATUS_ERROR_BITS -- Apollo names neither
#   bits 24..27 (0x0f000000) "BSE Error" per the certus mask comment
OPENOCD_STATUS_BITS = {
    6:  "ERROR_BIT_6(openocd)",
    14: "FEA_OTP(openocd)",
    17: "ERROR_BIT_17(openocd)",
    24: "BSE_ERROR_24(openocd)",
    25: "BSE_ERROR_25(openocd)",
}

# Opcodes that are safe to *read* but whose effects touch volatile config only.
# These may drop DONE or disturb a running design; SRAM config is volatile so the
# recovery is simply to reconfigure. Acceptable risk, deliberately taken.
VOLATILE_NAMES = {
    "ISC_ERASE", "ISC_DISCHARGE", "LSC_REFRESH", "ISC_ENABLE", "LSC_ENABLE_X",
    "ISC_DISABLE", "ISC_PROGRAM_DONE", "LSC_BITSTREAM_BURST",
    "LSC_PROG_INCR_RTI", "LSC_PROG_INCR_CMP", "LSC_PROG_INCR_ENC",
    "LSC_PROG_INCR_CNE", "LSC_WRITE_COMP_DIC", "LSC_PROG_CTRL0",
    "LSC_EBR_WRITE", "LSC_PCS_WRITE", "LSC_WRITE_ADDRESS", "LSC_WRITE_BUS_ADDR",
    "LSC_INIT_ADDRESS", "LSC_INIT_ADDR_UFM", "ISC_DATA_SHIFT", "ISC_ADDRESS_SHIFT",
    "LSC_PROG_SED_CRC", "LSC_RESET_CRC", "LSC_PROG_SPI",
    "LSC_PROG_SPI1", "ISC_PROGRAM_USERCODE", "EXTEST", "EXTEST_PULSE",
    "EXTEST_TRAIN", "INTEST", "CLAMP", "HIGHZ", "LSC_IP_A", "LSC_IP_B",
    "LSC_IPTEST_A", "LSC_IPTEST_B", "LSC_I2CI_CRBR_WT", "LSC_I2CI_TXDR_WT",
    "LSC_MANUFACTURE_SHIFT",
}

# Payload lengths (bits) Lattice's own procedure body uses for a given opcode.
# Where the body never issues the opcode, there is no entry -- and that absence is
# itself recorded in the report.
PAYLOAD_BITS = {
    "LSC_READ_TEMP":     8,     # SDR 8, after RUN_TEST IDLE TCK 2 DELAY 200
    "LSC_READ_CTRL0":    32,    # SDR_VERIFY 32
    "LSC_PROG_CTRL0":    32,    # SDR 32
    "LSC_UIDCODE_PUB":   64,    # SDR 64
    "LSC_READ_PES":      64,    # body uses both 32 and 64; 64 is the wider read
    "LSC_READ_STATUS":   32,
    "IDCODE_PUB":        32,
    "USERCODE":          32,
    "VERIFY_ID":         32,
    "LSC_CHECK_BUSY":    8,
}

# Opcodes never issued anywhere in the vendor procedure body. Declared only.
# These are the empirically-open ones.
DECLARED_BUT_UNUSED = {
    "LSC_ISCAN", "LSC_EBR_READ", "LSC_EBR_WRITE",
    "LSC_READ_CRC", "LSC_READ_SED_CRC",
}


def parse_svp(path=SVP_PATH):
    """
    Parse the `NAME = 0xNN;` declarations out of the vendor <Instruction> block.

    Returns a list of (code:int, name:str) sorted by name. Note that several names share
    a code -- LSC_PRELOAD and LSC_SAMPLE are both 0x1C, LSC_READ_TAG and LSC_READ_UFM are
    both 0xCA, LSC_STORE and LSC_PROG_PES are both 0xD0, and LSC_READ_TRIM/LSC_PROG_TRIM/
    LSC_PROG_MES all share 0xD1. Aliasing is real in the vendor file, not a parse bug, so
    it is preserved rather than deduplicated.
    """
    text = Path(path).read_text(errors="replace")
    pat = re.compile(r"^\s*([A-Z][A-Z_0-9]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;", re.M)
    out = []
    for m in pat.finditer(text):
        name, code = m.group(1), int(m.group(2), 16)
        out.append((code, name))
    return sorted(set(out), key=lambda t: (t[1], t[0]))


def safety(name):
    if name in FORBIDDEN_NAMES:
        return "FORBIDDEN"
    if name in VOLATILE_NAMES:
        return "VOLATILE"
    return "READ"


# Severity order for resolving aliases: the most dangerous name wins.
_RANK = {"READ": 0, "VOLATILE": 1, "FORBIDDEN": 2}


def build_table(path=SVP_PATH):
    """
    Full table: code, name, safety class, vendor payload length, used-in-body flag.

    Safety is resolved *per opcode code*, not per name, because the vendor file aliases
    several codes to more than one name and the aliases do not agree on danger. The
    sharpest case is 0xD1, which is LSC_READ_TRIM (harmless-sounding) but also
    LSC_PROG_TRIM and LSC_PROG_MES -- issuing it in the hope of reading trim could
    instead write analog trim fuses, which is permanent and would ruin the part. 0xD0 is
    LSC_STORE and LSC_PROG_PES likewise.

    So a code inherits the *worst* classification among all names that share it. Trusting
    the friendly name on an aliased opcode is exactly the mistake that bricks silicon.
    """
    parsed = parse_svp(path)

    # First pass: worst-case safety per code.
    worst = {}
    aliases = {}
    for code, name in parsed:
        s = safety(name)
        if code not in worst or _RANK[s] > _RANK[worst[code]]:
            worst[code] = s
        aliases.setdefault(code, []).append(name)

    rows = []
    for code, name in parsed:
        own = safety(name)
        eff = worst[code]
        rows.append({
            "code": code,
            "hex": f"0x{code:02X}",
            "name": name,
            "safety": eff,
            "safety_by_name": own,
            # True when this name looks safer than the opcode actually is.
            "alias_downgraded": own != eff,
            "aliases": sorted(set(aliases[code])),
            "vendor_bits": PAYLOAD_BITS.get(name),
            "declared_only": name in DECLARED_BUT_UNUSED,
        })
    return rows


def safe_table(path=SVP_PATH):
    """Everything the sweep is permitted to issue: FORBIDDEN removed."""
    return [r for r in build_table(path) if r["safety"] != "FORBIDDEN"]


if __name__ == "__main__":
    import json
    import sys

    rows = build_table()
    forb = [r for r in rows if r["safety"] == "FORBIDDEN"]
    print(f"parsed {len(rows)} opcode declarations "
          f"({len(set(r['code'] for r in rows))} distinct codes)", file=sys.stderr)
    print(f"  READ-safe:  {sum(1 for r in rows if r['safety']=='READ')}", file=sys.stderr)
    print(f"  VOLATILE:   {sum(1 for r in rows if r['safety']=='VOLATILE')}", file=sys.stderr)
    print(f"  FORBIDDEN:  {len(forb)} -> {sorted(r['name'] for r in forb)}",
          file=sys.stderr)
    print(json.dumps(rows, indent=2))
