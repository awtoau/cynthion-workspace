#!/usr/bin/env python3.15t
"""
Drive ecp5_cmd_probe.py across the vendor opcode set, in a given device state.

Opcodes come from Lattice's own programming procedure (see ecp5_opcodes.py), not from
Apollo's enum: 104 declarations, 98 distinct codes, of which 81 names are unknown to
Apollo's ecp5.py. Apollo's enum only ever covered what a configure()/flash() flow needs.

Each individual probe runs in its own subprocess. That is the reason this file is
separate from the probe: issuing an arbitrary opcode to a configuration engine can wedge
the JTAG state machine or block on a device that never answers, and a wedged probe must
cost one data point rather than the entire sweep. A subprocess that has to be killed is
itself recorded -- "this opcode hangs" is a finding, not a failure.

Ordering is deliberate: READ-class opcodes run before VOLATILE ones. Reads are
inherently safer and several of them answer standing questions directly, so if the sweep
does wedge the part partway through, the results that matter most are already banked.

FORBIDDEN opcodes are never issued. Safety is resolved per opcode *code*, not per name,
because the vendor file aliases 0xD1 to both LSC_READ_TRIM and LSC_PROG_TRIM -- see the
note in ecp5_opcodes.build_table(). Issuing that one hoping to read trim could write
analog trim fuses instead, which is permanent.

States:
    unconfigured -- DONE=0, no design running
    configured   -- DONE=1, a real bitstream loaded and running

The configured state carries the open question: what the configuration engine will accept
from a *running* device, which decides whether partial or background reconfiguration is
reachable on this part at all.

Usage:
    ecp5_cmd_sweep.py --state configured --bitstream ecp5-test/led_patterns.bit
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "tmp" / "logs"
RESULTS = REPO / "tmp" / "ecp5_cmd_sweep"
PROBE = Path(__file__).resolve().parent / "ecp5_cmd_probe.py"
PYTHON = str(Path.home() / "opt" / "cpython-315t" / "bin" / "python3.15t")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ecp5_opcodes import build_table  # noqa: E402

LOG_NAME = "ecp5_cmd_sweep"
log = logging.getLogger(LOG_NAME)


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.FileHandler(LOG_DIR / f"{LOG_NAME}.log")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)


# Per-probe wall-clock cap. Every period in this project needs a stated reason:
# a JTAG transaction over Apollo's USB bridge is milliseconds; Apollo's own control
# transfers use 500 ms and its _wait_for_completion caps at 1 s. Python startup plus
# device enumeration dominates and measures ~2 s. 25 s is an order of magnitude above the
# worst legitimate case, so anything reaching it is stuck rather than slow -- which is the
# distinction the cap exists to draw. It is a subprocess reap, not a sleep: nothing waits
# on it unless the child has already hung.
PROBE_TIMEOUT_S = 25

# Read lengths swept per opcode, in bits-as-bytes (the probe multiplies by 8).
# 0 exercises the opcode with no data register access at all, which distinguishes
# "instruction rejected" from "instruction accepted, no payload".
READ_LENGTHS = [0, 1, 4, 8]


def run_probe(opcode, length=4, payload=None, label=""):
    """Run one probe in a subprocess. A hang is caught and recorded, never propagated."""
    cmd = [PYTHON, str(PROBE), "--single", hex(opcode), "--length", str(length),
           "--json", "--label", label]
    if payload:
        cmd += ["--payload", payload.hex()]

    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"opcode_hex": f"0x{opcode:02X}", "label": label, "length": length,
                "payload": payload.hex() if payload else None, "hung": True,
                "elapsed_ms": round((time.monotonic() - t0) * 1000, 1)}

    rec = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                pass
    if rec is None:
        rec = {"opcode_hex": f"0x{opcode:02X}", "label": label, "length": length,
               "no_json": True, "returncode": r.returncode,
               "stderr_tail": r.stderr.strip()[-400:]}
    return rec


def device_present():
    """True if the debugger enumerates and answers a status read."""
    try:
        r = subprocess.run([PYTHON, str(PROBE), "--status-only", "--json"],
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    for line in r.stdout.splitlines():
        if line.strip().startswith("{"):
            return json.loads(line)
    return None


def configure(bitstream):
    r = subprocess.run([PYTHON, str(PROBE), "--configure", str(bitstream)],
                       capture_output=True, text=True, timeout=180)
    return r.returncode == 0, r.stderr.strip()[-500:]


def sweep_plan(only=None, include_volatile=True):
    """
    One entry per distinct opcode code, READ class first.

    Deduplicated by code -- aliases share a code and issuing it twice under two names
    would measure the same thing twice. All alias names are kept on the record so the
    report can say which vendor names map to the code that was probed.
    """
    rows = build_table()
    by_code = {}
    for r in rows:
        c = r["code"]
        if c not in by_code:
            by_code[c] = dict(r)
            by_code[c]["names"] = set()
        by_code[c]["names"].add(r["name"])
        # Keep any vendor payload length found under any alias.
        if r.get("vendor_bits") and not by_code[c].get("vendor_bits"):
            by_code[c]["vendor_bits"] = r["vendor_bits"]

    plan = []
    for c, r in by_code.items():
        if r["safety"] == "FORBIDDEN":
            continue
        if r["safety"] == "VOLATILE" and not include_volatile:
            continue
        if only and c not in only:
            continue
        r["names"] = sorted(r["names"])
        plan.append(r)

    # READ before VOLATILE; within a class, by opcode for reproducibility.
    plan.sort(key=lambda r: (0 if r["safety"] == "READ" else 1, r["code"]))
    return plan


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", required=True,
                    choices=["unconfigured", "configured"])
    ap.add_argument("--bitstream", help="load this before sweeping")
    ap.add_argument("--only", type=lambda x: int(x, 0), action="append")
    ap.add_argument("--reads-only", action="store_true",
                    help="skip VOLATILE opcodes entirely")
    ap.add_argument("--recover-with", help="bitstream to reload if the device wedges")
    ap.add_argument("--out")
    args = ap.parse_args()

    setup_logging()

    if args.bitstream:
        log.info("loading %s to reach state=%s", args.bitstream, args.state)
        ok, err = configure(args.bitstream)
        log.info("configure ok=%s %s", ok, err if not ok else "")
        if not ok:
            log.error("could not reach requested state; aborting")
            return 1

    baseline = device_present()
    log.info("baseline: %s", baseline)
    if baseline is None:
        log.error("device did not answer a status read; aborting")
        return 1

    plan = sweep_plan(only=args.only, include_volatile=not args.reads_only)
    log.info("sweeping %d distinct opcodes (%d READ, %d VOLATILE) in state=%s",
             len(plan), sum(1 for r in plan if r["safety"] == "READ"),
             sum(1 for r in plan if r["safety"] == "VOLATILE"), args.state)

    results = {"state": args.state, "baseline": baseline,
               "bitstream": args.bitstream, "probes": []}

    for r in plan:
        code, names, cls = r["code"], r["names"], r["safety"]
        log.info("=== 0x%02X %s [%s]%s", code, "/".join(names), cls,
                 " (declared-only in vendor file)" if r["declared_only"] else "")

        lengths = list(READ_LENGTHS)
        # If Lattice's own procedure uses a specific width for this opcode, probe it too.
        vb = r.get("vendor_bits")
        if vb and (vb // 8) not in lengths:
            lengths.append(vb // 8)

        for length in lengths:
            tag = f"{names[0]}:read{length}"
            rec = run_probe(code, length=length, label=tag)
            rec.update(names=names, safety=cls,
                       declared_only=r["declared_only"],
                       vendor_bits=vb)
            results["probes"].append(rec)
            log.info("  read len=%-3d -> %s", length, _summarize(rec))

        st = device_present()
        if st is None:
            log.error("device stopped answering after 0x%02X (%s)", code,
                      "/".join(names))
            results["wedged_after"] = {"opcode": f"0x{code:02X}", "names": names}
            if args.recover_with:
                log.info("attempting recovery via reconfigure")
                ok, err = configure(args.recover_with)
                log.info("recovery ok=%s %s", ok, err if not ok else "")
                results["recovered"] = ok
                if not ok:
                    break
            else:
                break
        else:
            results["probes"][-1]["post_state"] = st.get("status")

    out = Path(args.out) if args.out else RESULTS / f"sweep-{args.state}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    log.info("wrote %d probe records to %s", len(results["probes"]), out)
    return 0


def _summarize(rec):
    if rec.get("hung"):
        return "HUNG"
    if rec.get("no_json"):
        return f"NO-RESULT rc={rec.get('returncode')} {rec.get('stderr_tail','')[-120:]}"
    bits = f" delta={rec['delta']}" if rec.get("delta") else ""
    return (f"resp={rec.get('response','')!r} "
            f"{rec.get('status_before')}->{rec.get('status_after')}{bits}")


if __name__ == "__main__":
    sys.exit(main())
