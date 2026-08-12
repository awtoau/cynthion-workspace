#!/usr/bin/env python3
#
# The arbiter, proved on the real board. Four proofs, three of them negative. #430
# SPDX-License-Identifier: BSD-3-Clause

"""Drive the board arbiter against real hardware and check what it claims.

    ./scripts/board_arbiter_proof.py            # all four
    ./scripts/board_arbiter_proof.py --only preempt

| proof     | what it demonstrates                                            |
|-----------|-----------------------------------------------------------------|
| `cold`    | a job from a cold start starts the server and returns provenance |
| `queue`   | two concurrent submissions serialise, each on its OWN bitstream  |
| `blank`   | a deliberately blanked FPGA is NAMED, not reported as a pass     |
| `preempt` | an idle sweep is abandoned mid-command by a real submission      |

`blank` deliberately goes around the arbiter -- `apollo force-offline` -- which
is the point: the state has to be staged for the arbiter to catch it. It is the
only place in this repo that is allowed to, and it restores the board after.

Every proof asserts a NAMED outcome (`blank-fpga`, `preempted`, a differing
sha256), never merely "did not crash". Output to `tmp/logs/board-proof.log`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import board as client  # noqa: E402
import board_arbiter as arbiter  # noqa: E402
import soc_confirm  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "board-proof.log"

# Two variants that differ in what they elaborate, so proof `queue` compares two
# real bitstreams rather than two copies of one.
VARIANTS = ("bist1-ck120-dqs1-mirror0-mirrordiv4",
            "bist0-ck100-dqs1-mirror0-mirrordiv4")

# `bist all 8` measured 4.59 s over 4096 cells on this board (2026-08-12).
# Long enough to be preempted mid-command, and its budget is 1.3x the measure.
SWEEP = "bist all 8"
SWEEP_BUDGET_S = 6.0

# How long after the idle job is submitted the preempting job goes in.
#
# Waits for: the idle job to be mid-SWEEP, not mid-configure. Expected: a
# configure and confirm measured ~2.5 s, then 5 sweeps of 4.59 s = 23 s of
# command time. 4 s lands about 1.5 s into the first sweep, and anything up to
# 25 s would do. On expiry: the submission goes in regardless, and the proof
# FAILS if the idle job was not preempted -- which is the outcome under test.
PREEMPT_AFTER_S = 4.0


def say(line=""):
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{arbiter.now()} {line}\n")


def submit(payload: dict) -> dict:
    """One job, submitted and waited on. The client's own path."""
    code, body = client.post_job(payload)
    if code != 202:
        return {"status": "rejected", "error": body.get("error"), "id": None}
    return client.wait(body["id"], quiet=True)


def run_job(variant, commands, **extra) -> dict:
    payload = {"kind": "run", "variant": variant, "commands": commands,
               "client": {"proof": True}}
    payload.update(extra)
    return submit(payload)


def report(name: str, ok: bool, detail: str) -> bool:
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


# --- the proofs ------------------------------------------------------------


def proof_cold() -> bool:
    """A job from a cold start: no server, no assumption about what is loaded."""
    say("\n=== cold: submit with no server running ===")
    if arbiter.reachable():
        try:
            client.request("POST", "/shutdown", {})
        except OSError:
            pass                    # already going: the state this wants anyway
        while arbiter.reachable():
            time.sleep(0.05)                    # the listener closing, not a wait
    ok = report("port down before submitting", not arbiter.reachable(),
                f"nothing on {arbiter.PORT}")
    record = run_job(VARIANTS[0], ["bist smoke"], label="proof-cold")
    provenance = record.get("provenance") or {}
    bitstream = provenance.get("bitstream") or {}
    board = provenance.get("board") or {}
    ok &= report("status", record.get("status") == "passed",
                 f"{record.get('status')} {record.get('error') or ''}")
    ok &= report("bitstream identity", bool(bitstream.get("sha256")),
                 f"{bitstream.get('path')} sha256 {bitstream.get('sha256', '')[:12]}")
    ok &= report("configured by this job",
                 provenance.get("configured_by_this_job") is True,
                 "a cold arbiter knows nothing, so it configures")
    ok &= report("board's own commit", bool(board.get("image")),
                 f"image {board.get('image')} gateware {board.get('gateware')} "
                 f"die {board.get('die_c')} C")
    ok &= report("confirm ran", provenance.get("confirm", {}).get("verdict") == "ok",
                 str(provenance.get("confirm")))
    return ok


def proof_queue() -> bool:
    """Two clients, two variants, at the same moment. #430's own check."""
    say("\n=== queue: two concurrent submissions, different variants ===")
    results = {}

    def submit_one(variant):
        results[variant] = run_job(variant, ["info"], label="proof-queue")

    threads = [threading.Thread(target=submit_one, args=(variant,))
               for variant in VARIANTS]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    first, second = (results[variant] for variant in VARIANTS)
    ok = report("both passed",
                all(record.get("status") == "passed" for record in results.values()),
                ", ".join(f"{v.split('-')[0]}={r.get('status')}"
                          for v, r in results.items()))
    shas = [((record.get("provenance") or {}).get("bitstream") or {}).get("sha256")
            for record in (first, second)]
    ok &= report("each on its own bitstream", len(set(shas)) == 2 and all(shas),
                 " vs ".join((sha or "?")[:12] for sha in shas))
    # One at a time: the second job may not start before the first finished.
    order = sorted((first, second), key=lambda record: record["started"])
    ok &= report("serialised", order[0]["finished"] <= order[1]["started"],
                 f"{order[0]['finished']} -> {order[1]['started']}")
    return ok


def proof_blank() -> bool:
    """A blanked FPGA must be NAMED. This is #360, staged deliberately."""
    say("\n=== blank: a deliberately bad board state ===")
    say("  staging: apollo force-offline (the one place that goes around the "
        "arbiter, so there is something to catch)")
    offline = subprocess.run(
        [sys.executable, str(soc_confirm.apollo_cli()), "force-offline"],
        cwd=ROOT, capture_output=True, text=True)
    if offline.returncode != 0:
        return report("staging", False,
                      f"force-offline exited {offline.returncode}: "
                      f"{offline.stderr.strip()[:160]}")

    record = submit({"kind": "confirm", "commands": [], "client": {"proof": True}})
    shell = submit({"kind": "shell", "commands": ["info"],
                    "client": {"proof": True}})
    named = f"{record.get('error') or ''} {shell.get('error') or ''}"
    ok = report("a blank FPGA is not a pass", record.get("status") != "passed",
                f"{record.get('status')}: {str(record.get('error'))[:100]}")
    ok &= report("a shell job on it is refused too",
                 shell.get("status") != "passed",
                 f"{shell.get('status')}: {str(shell.get('error'))[:100]}")
    # The staged state IS a blank FPGA, so that verdict has to appear. Any of
    # the nine would be a refusal, but only this one is the right diagnosis, and
    # accepting the others would let a vague verdict pass as a named one.
    ok &= report("and it is named blank-fpga", "blank-fpga" in named,
                 named.strip()[:200])
    ok &= report("with no transcript to mistake for one",
                 not (shell.get("transcript") or []),
                 f"{len(shell.get('transcript') or [])} command(s) recorded")

    say("  restoring the board")
    restored = run_job(VARIANTS[0], ["bist smoke"], label="proof-blank-restore")
    ok &= report("recovered", restored.get("status") == "passed",
                 str(restored.get("status")))
    return ok


def proof_preempt() -> bool:
    """An idle sweep, abandoned mid-command by a real submission."""
    say("\n=== preempt: an idle sweep gives way to a real job ===")
    idle_result, real_result = {}, {}

    def idle():
        idle_result.update(submit({
            "kind": "run", "priority": "idle", "variant": VARIANTS[0],
            "commands": [SWEEP], "budget_s": SWEEP_BUDGET_S, "repeat": 5,
            "label": "proof-idle-soak", "client": {"proof": True}}))

    thread = threading.Thread(target=idle)
    thread.start()
    started = time.monotonic()
    while time.monotonic() - started < PREEMPT_AFTER_S:
        time.sleep(0.05)
    real_result.update(run_job(VARIANTS[0], ["info"], label="proof-preemptor"))
    thread.join()

    ok = report("the idle job was preempted",
                idle_result.get("status") == "preempted",
                f"{idle_result.get('status')} after "
                f"{len(idle_result.get('transcript') or [])} command(s)")
    ok &= report("the real job passed", real_result.get("status") == "passed",
                 str(real_result.get("status")))
    ok &= report("the real job reconfigured after the preemption",
                 (real_result.get("provenance") or {})
                 .get("configured_by_this_job") is True,
                 "a preempted sweep leaves the board unknown")
    path = idle_result.get("path") or ""
    ok &= report("the idle record is in the idle directory",
                 "board-arbiter/idle" in path, path or "not written")
    ok &= report("with the idle schema",
                 idle_result.get("schema") == arbiter.SCHEMA_IDLE,
                 str(idle_result.get("schema")))
    ok &= report("and no measurement keys",
                 not any(key in idle_result for key in
                         ("failures", "summary", "pins", "ck_mhz")),
                 "nothing here can be quoted as a row")
    for line in idle_result.get("events") or []:
        say(f"    idle event: {line}")
    return ok


PROOFS = {"cold": proof_cold, "queue": proof_queue, "blank": proof_blank,
          "preempt": proof_preempt}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=sorted(PROOFS), action="append",
                        help="run one proof; repeatable")
    args = parser.parse_args()

    say(f"\nboard arbiter proof {arbiter.now()}")
    results = {}
    for name in (args.only or list(PROOFS)):
        try:
            results[name] = PROOFS[name]()
        except Exception as failure:            # a proof that dies has failed
            results[name] = report(name, False, f"raised {failure!r}")
    say("\n--- summary ---")
    for name, ok in results.items():
        say(f"  {name:8s} {'PASS' if ok else 'FAIL'}")
    say(f"  -> {LOG.relative_to(ROOT)}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
