#!/usr/bin/env python3.15t
"""
Dynamic probe of the ECP5 configuration engine's *command interface*.

Everything else in this project fuzzes the ECP5 statically: Verilog -> Diamond ->
bitstream -> diff the bits. Nothing executes, and a bit's meaning is inferred from
where it lands. This does the opposite: it issues real opcodes to the configuration
engine on live silicon and records what the hardware does.

The point is to test *capability*, not encoding. A documented absence ("the primitive
library has no such thing", "the datasheet does not mention it") is an argument from
absence. A hardware refusal is evidence. Only one of those can be obtained here.

Method
------
Every opcode in Apollo's Opcode enum -- including the four marked "known to Project
Trellis, but unused" -- is issued in each of several device states, at several payload
lengths. The status register is read before and after every single command so that any
state change is attributable to the command that caused it.

Nothing is pruned. A command that returns all zeros is data. A command that changes an
undocumented status bit is a finding. A command that hangs is data too -- which is why
each probe runs in its own subprocess (see runner.py's --single mode): a wedged JTAG
state machine kills one probe and gets reported, rather than taking the sweep with it.

Safety
------
No FlashOpcode is ever issued and nothing here writes flash. Another agent has a
partition table and a boot image at offset 0 that must survive. SRAM configuration is
volatile -- the worst case is that the FPGA stops running a design, which `configure`
fixes.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "tmp" / "logs"
LOG_NAME = "ecp5_cmd_probe"

log = logging.getLogger(LOG_NAME)


def setup_logging(verbose=True):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    fh = logging.FileHandler(LOG_DIR / f"{LOG_NAME}.log")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(sh)
    return log


# --------------------------------------------------------------------------
# Status register decoding
# --------------------------------------------------------------------------

# Bits Apollo names in ecp5.py. Everything not in here is undocumented as far as
# this codebase is concerned, and is exactly what the sweep is looking for.
DOCUMENTED_BITS = {
    4:  "JTAG_ACTIVE",
    8:  "DONE",
    9:  "ISC_ENABLE",
    10: "WRITEABLE",
    11: "READABLE",
    12: "BUSY",
    13: "FAIL",
    21: "STANDARD_PRE",
    22: "SPI_FAIL",
    26: "EXECUTION_FAIL",
    27: "ID_ERROR",
    28: "INVALID_COMMAND",
}

# Bits OpenOCD's ECP5 driver (src/pld/ecp5.c) names but Apollo does not. A second
# implementation's name for a bit is stronger evidence than "nobody documents it", so
# these are labelled rather than reported as UNDOC_n. Kept visibly distinct with an
# (openocd) suffix, because these come from a different codebase's reading of the part
# and are not independently confirmed here.
OPENOCD_BITS = {
    6:  "ERROR_BIT_6(openocd)",     # part of STATUS_ERROR_BITS 0x00020040
    14: "FEA_OTP(openocd)",         # STATUS_FEA_OTP 0x00004000
    17: "ERROR_BIT_17(openocd)",    # part of STATUS_ERROR_BITS
    24: "BSE_ERROR_24(openocd)",    # "BSE Error" mask 0x0f000000
    25: "BSE_ERROR_25(openocd)",
}

# Bits 23..25 are the 3-bit error code field.
ERROR_SHIFT = 23
ERROR_MASK = 0b111
ERROR_CODES = {
    0: "error unknown",
    1: "part ID mismatch",
    2: "illegal command issued",
    3: "CRC check failed",
    4: "preamble error",
    5: "user aborted configuration",
    6: "data overflow",
    7: "bitstream past SRAM array",
}


def bit_name(bit):
    """Apollo's name, else OpenOCD's, else genuinely undocumented."""
    if bit in DOCUMENTED_BITS:
        return DOCUMENTED_BITS[bit]
    if bit in OPENOCD_BITS:
        return OPENOCD_BITS[bit]
    return f"UNDOC_{bit}"


def decode_status(status):
    """Render a status word as a list of set-bit names, so diffs read in English."""
    names = []
    for bit in range(32):
        if not (status >> bit) & 1:
            continue
        if ERROR_SHIFT <= bit <= ERROR_SHIFT + 2:
            continue  # part of the error field, reported separately
        names.append(bit_name(bit))
    err = (status >> ERROR_SHIFT) & ERROR_MASK
    if err:
        names.append(f"ERR={err}({ERROR_CODES.get(err, '?')})")
    return names


def status_delta(before, after):
    """Which bits went 0->1 and 1->0. This is the actual measurement."""
    set_bits = after & ~before
    clr_bits = before & ~after
    out = {}
    if set_bits:
        out["set"] = [bit_name(b) for b in range(32) if (set_bits >> b) & 1]
    if clr_bits:
        out["cleared"] = [bit_name(b) for b in range(32) if (clr_bits >> b) & 1]
    return out


# --------------------------------------------------------------------------
# Device access
# --------------------------------------------------------------------------

def open_programmer():
    from apollo_fpga import ApolloDebugger
    d = ApolloDebugger()
    return d


def read_status(p):
    """Raw status read. Deliberately not p._read_status(), so byte order is explicit."""
    raw = p._execute_command(p.Opcode.LSC_READ_STATUS, 4, check_status=False,
                             never_print=True)
    return int.from_bytes(raw, byteorder="big")


def probe_one(p, opcode, length, payload=None, label="", idle=True):
    """
    Issue one opcode and record everything observable around it.

    Status is read before and after so that any change is attributable. check_status is
    always False -- Apollo's own validator raises IOError on FAIL/INVALID bits, and here
    those bits are the result being measured, not an error to abort on.

    `idle` controls whether the TAP is driven through RUN-TEST/IDLE after the command,
    and it is not optional in practice. _execute_command leaves the TAP parked in
    DRPAUSE/IRPAUSE; several ECP5 configuration commands only take effect once the TAP
    passes through RUN-TEST/IDLE, so an opcode issued without it reads back as having
    done nothing at all.

    This was established empirically here, and it matters more than it sounds: ISC_ENABLE
    issued without a following run_test() leaves status at 0x00200100 (unchanged), while
    the identical command followed by run_test(2) gives 0x00200F10 -- ISC_ENABLE,
    WRITEABLE and READABLE all set. Same opcode, same payload, same device state; the
    only difference is the TAP walk.

    The consequence for this whole investigation is that "the opcode did nothing" and
    "the harness never let the opcode take effect" produce identical observations. Any
    sweep run without this is measuring its own plumbing. Apollo's configure() calls
    chain.run_test(2) after each configuration command for exactly this reason.
    """
    rec = {
        "opcode": opcode,
        "opcode_hex": f"0x{opcode:02X}",
        "label": label,
        "length": length,
        "payload": payload.hex() if payload else None,
    }

    try:
        before = read_status(p)
    except Exception as e:
        rec["error"] = f"status-read-before failed: {e!r}"
        return rec
    rec["status_before"] = f"0x{before:08X}"

    t0 = time.monotonic()
    try:
        if payload is not None:
            resp = p._execute_command(opcode, payload, check_status=False,
                                      never_print=True)
        else:
            resp = p._execute_command(opcode, length, check_status=False,
                                      never_print=True)
        rec["response"] = resp.hex() if resp else ""
        rec["ok"] = True
    except Exception as e:
        rec["ok"] = False
        rec["exception"] = repr(e)

    # Walk the TAP through RUN-TEST/IDLE so the command can actually take effect.
    # See the docstring: without this, commands that work read back as inert.
    if idle:
        try:
            p.chain.run_test(2)
            rec["idled"] = True
        except Exception as e:
            rec["idle_exception"] = repr(e)
    rec["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 3)

    try:
        after = read_status(p)
    except Exception as e:
        rec["error"] = f"status-read-after failed: {e!r}"
        return rec
    rec["status_after"] = f"0x{after:08X}"
    rec["status_after_bits"] = decode_status(after)

    delta = status_delta(before, after)
    if delta:
        rec["delta"] = delta

    return rec


# --------------------------------------------------------------------------
# Opcode table
# --------------------------------------------------------------------------

def all_opcodes(P):
    """
    Every opcode in the enum, tagged by whether Apollo actually uses it.

    The 'unused' four are the reason this script exists.
    """
    O = P.Opcode
    unused = {O.JUMP, O.LSC_WRITE_COMP_DIC, O.LSC_PROG_SED_CRC,
              O.ISC_PROGRAM_SECURITY}
    # Implemented in Apollo but never exercised by a normal configure().
    unexercised = {O.LSC_ENTER_BACKGROUND_SPI}

    out = []
    for op in O:
        tag = "unused" if op in unused else (
            "unexercised" if op in unexercised else "used")
        out.append((int(op), op.name, tag))
    # Stable order, deterministic logs.
    return sorted(set(out))


# Payload lengths to sweep per opcode. Read-lengths first (a receive transaction),
# then write payloads. Some opcodes only reveal themselves with an argument.
READ_LENGTHS = [0, 1, 4, 8]
WRITE_PAYLOADS = [
    b"\x00",
    b"\x00\x00\x00\x00",
]


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------

def reconfigure(bitstream_path):
    """
    Reload a design over JTAG. This is the recovery path: SRAM config is volatile, so
    a wedged FPGA is fixed by putting a bitstream back into it.
    """
    from apollo_fpga import ApolloDebugger
    d = ApolloDebugger()
    with open(bitstream_path, "rb") as f:
        bs = f.read()
    with d.jtag as jtag:
        p = d.create_jtag_programmer(jtag)
        p.configure(bs)
    d.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--single", type=lambda x: int(x, 0),
                    help="probe exactly one opcode (used by the subprocess runner)")
    ap.add_argument("--length", type=int, default=4)
    ap.add_argument("--payload", help="hex payload; if given, a write transaction")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", action="store_true", help="emit one JSON record on stdout")
    ap.add_argument("--status-only", action="store_true",
                    help="just read and decode the status register")
    ap.add_argument("--no-idle", action="store_true",
                    help="do NOT walk the TAP through RUN-TEST/IDLE after the command. "
                         "Commands generally will not take effect; use only to "
                         "demonstrate that difference.")
    ap.add_argument("--configure", help="load this bitstream (recovery / state setup)")
    args = ap.parse_args()

    setup_logging()

    if args.configure:
        log.info("configuring with %s", args.configure)
        reconfigure(args.configure)
        log.info("configure complete")
        return 0

    d = open_programmer()
    try:
        with d.jtag as jtag:
            p = d.create_jtag_programmer(jtag)

            if args.status_only:
                st = read_status(p)
                rec = {"status": f"0x{st:08X}", "bits": decode_status(st)}
                log.info("status 0x%08X %s", st, rec["bits"])
                if args.json:
                    print(json.dumps(rec))
                return 0

            if args.single is not None:
                payload = bytes.fromhex(args.payload) if args.payload else None
                rec = probe_one(p, args.single, args.length, payload, args.label,
                                idle=not args.no_idle)
                log.info("%s", json.dumps(rec))
                if args.json:
                    print(json.dumps(rec))
                return 0

            ap.error("nothing to do: pass --single, --status-only or --configure")
    finally:
        try:
            d.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
