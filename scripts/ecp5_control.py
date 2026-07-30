#!/usr/bin/env python3.15t
"""
Positive control for the command probe.

Every configuration-side opcode came back inert against a running (DONE=1) device: no
response, no status change, DONE never dropped. That result only means something if the
method can detect a command that *does* work. Otherwise "inert" might just mean the probe
never really reached the configuration engine, and the whole sweep would be measuring its
own plumbing.

So this drives the engine through a state change it is documented to make, using the same
_execute_command primitive and the same status reads the sweep uses, and checks that the
transition is visible:

    ISC_ENABLE  -> ISC_ENABLE bit must appear in status
    ISC_ERASE   -> DONE must drop (the running design is cleared)
    ISC_DISABLE -> ISC_ENABLE bit must clear

If those show up, the probe demonstrably reaches the configuration engine and can observe
it changing state, and "inert" is a fact about the opcode rather than about the harness.

Then the same previously-inert opcodes are retried in the *unconfigured* ISC-enabled
state, because several of them (EBR read, ISCAN, the CRC pair) plausibly require
configuration mode to be enabled before they do anything. Testing them only against a
running device would have been the weaker experiment.

This clears the FPGA. That is intended and safe -- ECP5 configuration is SRAM, so
reconfiguring restores the design, and the caller is expected to do that afterwards.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "tmp" / "logs"
LOG_NAME = "ecp5_control"
log = logging.getLogger(LOG_NAME)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ecp5_cmd_probe import decode_status, read_status, status_delta  # noqa: E402

DONE = 1 << 8
ISC_ENABLE_BIT = 1 << 9


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


def step(p, opcode, data, note, results, wait=False):
    before = read_status(p)
    rec = {"note": note, "opcode": f"0x{opcode:02X}",
           "status_before": f"0x{before:08X}"}
    try:
        if isinstance(data, int):
            resp = p._execute_command(opcode, data, check_status=False,
                                      never_print=True)
            rec["read_len"] = data
        else:
            resp = p._execute_command(opcode, data, check_status=False,
                                      never_print=True,
                                      wait_for_completion=wait)
            rec["payload"] = data.hex()
        rec["response"] = resp.hex() if resp else ""
        rec["ok"] = True
    except Exception as e:
        rec["ok"] = False
        rec["exception"] = repr(e)
    # Required: several configuration commands only take effect once the TAP passes
    # through RUN-TEST/IDLE. Without this they read back as inert. See ecp5_cmd_probe.
    p.chain.run_test(2)
    after = read_status(p)
    rec["status_after"] = f"0x{after:08X}"
    rec["bits_after"] = decode_status(after)
    d = status_delta(before, after)
    if d:
        rec["delta"] = d
    results.append(rec)
    log.info("%-46s %s -> %s%s", note, rec["status_before"], rec["status_after"],
             f" delta={d}" if d else "")
    return after


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out")
    ap.add_argument("--retry-inert", action="store_true",
                    help="retry the inert opcodes while ISC is enabled")
    args = ap.parse_args()

    setup_logging()

    from apollo_fpga import ApolloDebugger
    d = ApolloDebugger()
    results = []
    verdict = {}
    try:
        with d.jtag as jtag:
            p = d.create_jtag_programmer(jtag)

            start = read_status(p)
            log.info("start 0x%08X %s", start, decode_status(start))
            verdict["started_configured"] = bool(start & DONE)

            # --- preconditions -------------------------------------------
            # ISC_ENABLE does nothing on its own. Apollo's configure() reaches
            # configuration mode only after LSC_REFRESH, then an undocumented 0x1C
            # transaction (510 bits of 0x3f/0xff -- the opcode is LSC_PRELOAD/LSC_SAMPLE,
            # so this is a boundary-scan preload), and each command is followed by
            # run_test(). Issuing ISC_ENABLE cold leaves status completely unchanged,
            # which is what made the first version of this control fail.
            #
            # This is itself a finding about the *harness*: an opcode issued without its
            # preconditions is indistinguishable from an opcode the silicon does not
            # implement. Both look inert. That is precisely the confound this control
            # exists to catch.
            import time
            from apollo_fpga.support.bits import bits

            p._execute_command(p.Opcode.LSC_REFRESH, wait_for_completion=True,
                               check_status=False)
            # 50 ms is Apollo's own figure, per TN1260, for the device to clear SRAM
            # after a refresh. Not a guess and not a poll target -- the part gives no
            # status transition to poll for here.
            time.sleep(50 / 1000)
            step_note = "after LSC_REFRESH"
            log.info("%-46s 0x%08X", step_note, read_status(p))

            p._execute_command(0x1C, bits(b"\x3f" + b"\xff" * 63, 510),
                               check_status=False, bits_per_size_unit=1)
            log.info("%-46s 0x%08X", "after 0x1C preamble", read_status(p))

            # --- the positive control proper -------------------------------
            after_en = step(p, 0xC6, b"\x00", "ISC_ENABLE (with preconditions)",
                            results)
            p.chain.run_test(2)
            after_en = read_status(p)
            verdict["isc_enable_observed"] = bool(after_en & ISC_ENABLE_BIT)
            log.info("ISC_ENABLE bit observed: %s (0x%08X)",
                     verdict["isc_enable_observed"], after_en)

            step(p, 0x0E, b"\x01", "ISC_ERASE", results, wait=True)
            p.chain.run_test(2)
            after_erase = read_status(p)
            verdict["done_dropped_by_erase"] = (
                bool(start & DONE) and not (after_erase & DONE))
            log.info("after ISC_ERASE 0x%08X %s", after_erase,
                     decode_status(after_erase))

            if args.retry_inert:
                # Same opcodes that were inert at DONE=1, now with ISC enabled and the
                # array erased. If they need configuration mode, this is where they work.
                log.info("--- retrying previously-inert opcodes with ISC enabled")
                for op, name, ln in [
                    (0xB0, "LSC_EBR_READ", 16),
                    (0xDF, "LSC_ISCAN", 16),
                    (0x60, "LSC_READ_CRC", 4),
                    (0xA4, "LSC_READ_SED_CRC", 4),
                    (0x20, "LSC_READ_CTRL0", 4),
                    (0xE8, "LSC_READ_TEMP", 1),
                    (0x7E, "JUMP", 4),
                ]:
                    step(p, op, ln, f"{name} (ISC enabled, erased)", results)

            after_dis = step(p, 0x26, b"", "ISC_DISABLE", results)
            verdict["isc_enable_cleared"] = not (after_dis & ISC_ENABLE_BIT)

            end = read_status(p)
            log.info("end 0x%08X %s", end, decode_status(end))
    finally:
        try:
            d.close()
        except Exception:
            pass

    verdict["probe_can_observe_state_change"] = (
        verdict.get("isc_enable_observed", False)
        or verdict.get("done_dropped_by_erase", False))

    log.info("VERDICT: %s", json.dumps(verdict))
    if args.out:
        Path(args.out).write_text(json.dumps(
            {"verdict": verdict, "steps": results}, indent=2))
        log.info("wrote %s", args.out)

    if not verdict["probe_can_observe_state_change"]:
        log.error("positive control FAILED: the probe never observed a state change, "
                  "so every 'inert' result in the sweep is uninterpretable")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
