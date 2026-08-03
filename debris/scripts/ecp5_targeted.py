#!/usr/bin/env python3.15t
"""
Targeted experiments on the specific open questions, beyond the blanket sweep.

The sweep establishes what every opcode does with generic payloads. These are the cases
where a generic payload is not enough: the opcode needs its documented preconditions, an
address argument, or a before/after comparison against a known-good reference.

Experiments
-----------
jump        JUMP (0x7E). A Trellis opcode absent from Lattice's ECP5 procedure entirely.
            Tried bare and with 8/16/24/32-bit address arguments, checking whether DONE
            drops or the device reboots -- which is what runtime boot selection would look
            like.

ebr         LSC_EBR_READ (0xB0) / LSC_EBR_WRITE (0xB2). Declared by the vendor file but
            never issued by it. If block RAM were readable over JTAG with no fabric
            involvement, it would be genuinely useful for the soft-CPU work. Tested with
            an address set first via LSC_WRITE_BUS_ADDR / LSC_INIT_ADDRESS, since a
            read with no address is the one case guaranteed to look inert whether or not
            the opcode works.

iscan       LSC_ISCAN (0xDF). The internal-scan / readback-shaped opcode. Swept over
            lengths well past a status word, because a readback path would return a long
            stream rather than a register.

sedcrc      LSC_PROG_SED_CRC (0xA2) then LSC_READ_SED_CRC (0xA4), which is the documented
            pairing. Relevant to the separate SEDGA work.

crc         LSC_RESET_CRC (0x3B) then LSC_READ_CRC (0x60). If READ_CRC sources a real
            value, resetting the CRC first should change what comes back.

burst       LSC_BITSTREAM_BURST (0x7A) and LSC_PROG_INCR_RTI (0x82) issued to a *running*
            device with DONE=1. This is the crux of whether partial or background
            reconfiguration is reachable at all. Deliberately sends a small, harmless
            payload -- the question is whether the engine accepts the command at all, not
            whether a real bitstream lands.

spi         LSC_PROG_SPI (0x3A, Apollo's LSC_ENTER_BACKGROUND_SPI) with the unlock code
            Apollo uses, against a running design. Reads status after, then reconfigures.

Every experiment reads status before and after, and re-reads DONE at the end, so a
disturbed device is noticed immediately rather than silently poisoning later results.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "tmp" / "logs"
LOG_NAME = "ecp5_targeted"
log = logging.getLogger(LOG_NAME)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ecp5_cmd_probe import decode_status, read_status, status_delta  # noqa: E402


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.FileHandler(LOG_DIR / f"{LOG_NAME}.log")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def step(p, opcode, data_or_len, note, results, bits_per_size_unit=8):
    """One command with full before/after status capture."""
    before = read_status(p)
    rec = {"note": note, "opcode": f"0x{opcode:02X}",
           "status_before": f"0x{before:08X}"}
    try:
        if isinstance(data_or_len, int):
            resp = p._execute_command(opcode, data_or_len, check_status=False,
                                      never_print=True,
                                      bits_per_size_unit=bits_per_size_unit)
            rec["read_len"] = data_or_len
        else:
            resp = p._execute_command(opcode, data_or_len, check_status=False,
                                      never_print=True,
                                      bits_per_size_unit=bits_per_size_unit)
            rec["payload"] = data_or_len.hex()
        rec["response"] = resp.hex() if resp else ""
        rec["ok"] = True
    except Exception as e:
        rec["ok"] = False
        rec["exception"] = repr(e)
    # Required. _execute_command parks the TAP in DRPAUSE/IRPAUSE, and several ECP5
    # configuration commands only take effect once it passes through RUN-TEST/IDLE --
    # verified here with ISC_ENABLE, which reads back as completely inert without this
    # and sets four status bits with it. Omitting it makes a working command
    # indistinguishable from an unimplemented one.
    try:
        p.chain.run_test(2)
    except Exception as e:
        rec["idle_exception"] = repr(e)
    after = read_status(p)
    rec["status_after"] = f"0x{after:08X}"
    rec["bits_after"] = decode_status(after)
    d = status_delta(before, after)
    if d:
        rec["delta"] = d
    results.append(rec)
    log.info("%-42s %s -> %s resp=%r%s", note, rec["status_before"],
             rec["status_after"], rec.get("response", ""),
             f" delta={d}" if d else "")
    return rec


# --------------------------------------------------------------------------

def exp_jump(p, results):
    """JUMP (0x7E): does it exist on this part, and does it take an address?"""
    step(p, 0x7E, 0, "JUMP bare (no DR)", results)
    for n in (1, 2, 3, 4):
        step(p, 0x7E, n, f"JUMP read {n}B", results)
    # Address-shaped arguments. If this were runtime boot selection, an address would
    # be the argument it takes. 0x000000 is the primary boot image; a non-zero address
    # is what a golden-image jump would use.
    for addr in (b"\x00\x00\x00", b"\x00\x00\x00\x00",
                 b"\x00\x02\x00\x00", b"\x00\x00\x02\x00"):
        step(p, 0x7E, addr, f"JUMP addr={addr.hex()}", results)


def exp_ebr(p, results):
    """Block RAM read/write over JTAG -- the most useful thing here if it works."""
    for n in (1, 4, 8, 16, 32):
        step(p, 0xB0, n, f"LSC_EBR_READ read {n}B (no address set)", results)

    # Set an address first. Without one, an inert result proves nothing.
    step(p, 0x46, b"\x00\x00\x00\x00", "LSC_INIT_ADDRESS = 0", results)
    for n in (4, 16, 32):
        step(p, 0xB0, n, f"LSC_EBR_READ read {n}B after INIT_ADDRESS", results)

    step(p, 0xF6, b"\x00\x00\x00\x00", "LSC_WRITE_BUS_ADDR = 0", results)
    for n in (4, 16, 32):
        step(p, 0xB0, n, f"LSC_EBR_READ read {n}B after WRITE_BUS_ADDR", results)


def exp_iscan(p, results):
    """LSC_ISCAN (0xDF): readback-shaped? A readback returns a stream, not a register."""
    for n in (1, 4, 8, 16, 32, 64, 128):
        step(p, 0xDF, n, f"LSC_ISCAN read {n}B", results)


def exp_sedcrc(p, results):
    """PROG_SED_CRC then READ_SED_CRC -- the documented pairing."""
    for n in (1, 2, 4, 8):
        step(p, 0xA4, n, f"LSC_READ_SED_CRC read {n}B (before prog)", results)
    step(p, 0xA2, b"\x00\x00\x00\x00", "LSC_PROG_SED_CRC payload=0", results)
    for n in (1, 2, 4, 8):
        step(p, 0xA4, n, f"LSC_READ_SED_CRC read {n}B (after prog)", results)


def exp_crc(p, results):
    """RESET_CRC then READ_CRC. If READ_CRC is real, the reset should be visible."""
    for n in (1, 2, 4, 8):
        step(p, 0x60, n, f"LSC_READ_CRC read {n}B (before reset)", results)
    step(p, 0x3B, b"\x00", "LSC_RESET_CRC", results)
    for n in (1, 2, 4, 8):
        step(p, 0x60, n, f"LSC_READ_CRC read {n}B (after reset)", results)


def exp_burst(p, results):
    """
    Configuration writes to a device that is already running.

    The crux question. A small payload is deliberate: what is being measured is whether
    the engine accepts the command with DONE=1, not whether a real bitstream lands. If
    the device drops DONE or sets FAIL here, that is the answer either way.
    """
    step(p, 0x7A, b"\xFF\xFF\xFF\xFF", "LSC_BITSTREAM_BURST while DONE=1", results)
    step(p, 0x82, b"\x00\x00\x00\x00", "LSC_PROG_INCR_RTI while DONE=1", results)
    step(p, 0xB8, b"\x00\x00\x00\x00", "LSC_PROG_INCR_CMP while DONE=1", results)
    step(p, 0x46, b"\x00\x00\x00\x00", "LSC_INIT_ADDRESS while DONE=1", results)
    step(p, 0x82, b"\x00\x00\x00\x00", "LSC_PROG_INCR_RTI after INIT_ADDRESS", results)


def exp_spi(p, results):
    """Background SPI against a running design. Apollo's own unlock code."""
    step(p, 0x3A, b"\x68\xFE", "LSC_PROG_SPI unlock (0x68FE) while DONE=1", results)
    for n in (1, 4):
        step(p, 0x3A, n, f"LSC_PROG_SPI read {n}B", results)


EXPERIMENTS = {
    "jump": exp_jump,
    "ebr": exp_ebr,
    "iscan": exp_iscan,
    "sedcrc": exp_sedcrc,
    "crc": exp_crc,
    "burst": exp_burst,
    "spi": exp_spi,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("experiment", choices=sorted(EXPERIMENTS) + ["all"])
    ap.add_argument("--out")
    args = ap.parse_args()

    setup_logging()

    from apollo_fpga import ApolloDebugger
    d = ApolloDebugger()
    results = []
    try:
        with d.jtag as jtag:
            p = d.create_jtag_programmer(jtag)
            start = read_status(p)
            log.info("start status 0x%08X %s", start, decode_status(start))

            names = sorted(EXPERIMENTS) if args.experiment == "all" else [args.experiment]
            for n in names:
                log.info("--- experiment: %s", n)
                EXPERIMENTS[n](p, results)

            end = read_status(p)
            log.info("end status 0x%08X %s", end, decode_status(end))
            log.info("DONE still set: %s", bool(end & (1 << 8)))
    finally:
        try:
            d.close()
        except Exception:
            pass

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2))
        log.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
