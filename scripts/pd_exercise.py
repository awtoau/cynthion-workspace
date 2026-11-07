#!/usr/bin/env python3
# Send every legal PD message to the port partner on TARGET_C and record the replies (#633).
# SPDX-License-Identifier: BSD-3-Clause

"""Drive `pd target_c send <name>` for each catalogue row that has a firmware verb.

    ./scripts/pd_exercise.py                      # legal, non-destructive
    ./scripts/pd_exercise.py --destructive        # + resets, swaps, BIST, GotoMin, Enter_USB
    ./scripts/pd_exercise.py --include-illegal    # + messages a source shall not send
    ./scripts/pd_exercise.py --only sink-cap status --adb 192.168.25.117:36847

- The catalogue is `docs/pd/messages.toml` (schema in its header); rows with an empty
  `verb` are not sendable and are listed as skipped.
- The board is driven ONLY through `scripts/board/board.py` (never the tty).
- Each step is judged by BOTH ends: the Cynthion's reply (`pdx` lines) and, after it, the
  rail (`usb target_c power status`) and, with `--adb`, the phone's battery state.
- The first step is the control (`sink-cap`): it must get a Sink_Capabilities reply
  before anything else is believed. The run stops at the first unexplained error.
- Output: `tmp/pd_exercise_<timestamp>.md` and `.json`; log `tmp/logs/pd_exercise.log`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "docs" / "pd" / "messages.toml"
BOARD = ROOT / "scripts" / "board" / "board.py"
LOG = ROOT / "tmp" / "logs" / "pd_exercise.log"

# Waits for: one shell command's reply through the arbiter. Expected: a send takes
# the exchange budget, 40 ms to ~300 ms; a hard reset ~0.8 s tSrcRecover + restart
# (spec 7.1.5) ~1.7 s measured firmware-side limit 3 s. x1.25 -> 1 s and 4 s. On
# expiry: the arbiter records `timeout`, the run stops (unexplained).
COMMAND_BUDGET_S = 1.0
RESET_BUDGET_S = 4.0

# Waits for: a contract after a reset-class message. Polled by `pd target_c status`.
# Expected: S20 answered the capabilities 7.7 s after a Hard Reset (46 unacknowledged
# 167 ms capability sends, 2026-11-07); x1.25 -> 10 s. On expiry: "no contract after
# reset" and the run stops.
RENEGOTIATE_S = 10.0

# Waits for: `adb` to answer one dumpsys. Expected 0.3 s over wifi. x5 for a link
# that just lost VBUS. On expiry: phone fields are recorded as null.
ADB_S = 2.0

DESTRUCTIVE_RESET = {"soft-reset", "hard-reset", "dr-swap", "pr-swap", "data-reset"}

_log_file = None


def say(line: str = "") -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    text = f"{stamp} {line}"
    print(text, flush=True)
    if _log_file:
        _log_file.write(text + "\n")
        _log_file.flush()


def board(commands: list[str], budget_s: float) -> dict:
    """One arbiter job: the transcript record, parsed. Never opens the tty."""
    run = subprocess.run(
        [sys.executable, str(BOARD), "--json", "shell", "--budget", str(budget_s),
         "--label", "pd_exercise", *commands],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    try:
        record = json.loads(run.stdout)
    except json.JSONDecodeError:
        record = {"verdict": "unparsed", "stdout": run.stdout[-2000:], "stderr": run.stderr[-2000:]}
    record["returncode"] = run.returncode
    return record


def steps(record: dict) -> list[dict]:
    return record.get("commands") or record.get("transcript") or record.get("steps") or []


def reply_of(record: dict, index: int = 0) -> str:
    """All replies of the job joined (`index` kept for callers).

    An arbiter server started before the #632 fix attributes a late reply to the next
    slot, so every parser reads the whole job and finds its own `pdx` or status block.
    """
    return "\n".join(step.get("reply", "") for step in steps(record))


def adb_address(adb: str) -> str:
    """Wireless debugging re-advertises on a new port after a link drop: find it by mDNS."""
    host = adb.split(":")[0]
    try:
        found = subprocess.run(["adb", "mdns", "services"], check=False, capture_output=True,
                               text=True, timeout=ADB_S).stdout
        match = re.search(rf"{re.escape(host)}:(\d+)", found)
        if match:
            address = f"{host}:{match.group(1)}"
            subprocess.run(["adb", "connect", address], check=False, capture_output=True,
                           text=True, timeout=ADB_S)
            return address
    except (subprocess.TimeoutExpired, OSError):
        pass
    return adb


def phone(adb: str | None) -> dict | None:
    """The phone end: battery state from `dumpsys battery` and the sysfs current."""
    if not adb:
        return None
    adb = adb_address(adb)
    out: dict = {"level": None, "ac": None, "usb": None, "status": None, "current_ma": None}
    try:
        text = subprocess.run(["adb", "-s", adb, "shell", "dumpsys battery"],
                              check=False, capture_output=True, text=True, timeout=ADB_S).stdout
        for key, pattern in (("level", r"level: (\d+)"), ("status", r"status: (\d+)")):
            match = re.search(pattern, text)
            out[key] = int(match.group(1)) if match else None
        out["ac"] = "AC powered: true" in text
        out["usb"] = "USB powered: true" in text
        current = subprocess.run(
            ["adb", "-s", adb, "shell", "cat /sys/class/power_supply/battery/current_now"],
            check=False, capture_output=True, text=True, timeout=ADB_S).stdout.strip()
        if re.fullmatch(r"-?\d+", current):
            out["current_ma"] = int(current)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return out


def parse_pdx(reply: str) -> dict:
    """The `pdx` block a send prints: header fields, sent frame, replies."""
    result: dict = {"outcome": None, "replies": [], "sent": "", "note": "", "refused": ""}
    for raw in reply.splitlines():
        line = raw.strip()
        if line.startswith("pdx refused"):
            result["refused"] = line[len("pdx refused"):].strip()
        elif line.startswith("pdx name="):
            for key, value in re.findall(r'(\w+)=("[^"]*"|\S+)', line):
                result["reply_count" if key == "replies" else key] = value.strip('"')
            result["outcome"] = result.get("outcome")
        elif line.startswith("pdx sent "):
            result["sent"] = line[len("pdx sent "):]
        elif line.startswith("pdx reply "):
            match = re.match(r"pdx reply (\d+) \+(\d+)ms (.*)", line)
            if match:
                result["replies"].append({"index": int(match.group(1)),
                                          "at_ms": int(match.group(2)), "text": match.group(3)})
        elif line.startswith("pdx note "):
            result["note"] = line[len("pdx note "):]
    return result


def status_of(reply: str) -> dict:
    """Last `pd target_c status` block found in `reply`; `known` is False when none is whole."""
    runs = re.findall(r"session (running|stopped)\s+ready (\d)", reply)
    contracts = re.findall(r"contract  (none|pos\d)", reply)
    known = bool(runs) and bool(contracts)
    return {
        "known": known,
        "running": bool(runs) and runs[-1][0] == "running",
        "ready": bool(runs) and runs[-1][1] == "1",
        "contract": bool(contracts) and contracts[-1] != "none",
        "line": f"session {runs[-1][0]} ready {runs[-1][1]}" if runs else "",
    }


def settled_status(text: str) -> dict:
    """Status from the job's text, or from fresh jobs when the reply was cut off."""
    found = status_of(text)
    for _ in range(6):
        if found["known"]:
            break
        found = status_of(reply_of(board(["pd target_c status", "pd target_c status"],
                                         COMMAND_BUDGET_S)))
    return found


def rail_of(reply: str) -> str:
    """The rail end: the `target_c` and `control` rows of `pac1954 status` (V, A)."""
    marker = reply.rfind("pac1954 status")
    rows = [" ".join(line.split()[:4]) for line in reply[marker:].splitlines()
            if line.strip().startswith(("target_c", "control"))]
    return " | ".join(rows)


def load_rows(args) -> tuple[list[dict], list[dict]]:
    rows = tomllib.loads(CATALOGUE.read_text())["message"]
    chosen, skipped = [], []
    for row in rows:
        verb = row.get("verb", "")
        if not verb:
            skipped.append({**row, "skip": "no firmware verb"})
            continue
        if args.only and verb not in args.only:
            continue
        if row["source_may"] == "no" and not args.include_illegal:
            skipped.append({**row, "skip": "illegal for a source (--include-illegal)"})
            continue
        if row["destructive"] and not args.destructive:
            skipped.append({**row, "skip": "destructive (--destructive)"})
            continue
        chosen.append(row)
    # Control first, queries next, disruptive last; resets go very last because
    # they restart the session.
    def order(row):
        verb = row["verb"]
        if verb == "sink-cap":
            return (0, 0)
        if verb == "source-cap":
            return (1, 9)  # leaves the partner expecting a Request: last of the probes
        if verb in ("soft-reset", "hard-reset"):
            return (3, 1 if verb == "hard-reset" else 0)
        return (2 if row["destructive"] else 1, 0)
    chosen.sort(key=order)
    return chosen, skipped


def explain(row: dict, parsed: dict, before: dict, after: dict) -> str | None:
    """None when the step is explained; else why the run must stop."""
    if parsed["refused"]:
        return f"refused: {parsed['refused']}"
    outcome = parsed.get("outcome")
    if outcome == "tx-failed" and after["running"] and (
            row["sop"] == "SOP'" or row["source_may"] == "no"):
        # SOP': nothing answers on a plain cable (no e-marker, VCONN not sourced).
        # Illegal-for-source: the partner may drop a message it must not receive.
        return None
    if outcome in (None, "tx-failed", "not-ready", "hard-reset-received"):
        return f"outcome {outcome}: {parsed.get('detail', '')}"
    if not after["running"]:
        return f"session not running after the step: {after['line']}"
    return None


def wait_contract(budget_s: float) -> dict:
    """Poll `pd target_c status` until a contract exists again, or the bound."""
    deadline = time.monotonic() + budget_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = settled_status("")
        if last["contract"] and last["ready"]:
            return last
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--destructive", action="store_true")
    parser.add_argument("--include-illegal", action="store_true")
    parser.add_argument("--only", nargs="*", help="verbs to send")
    parser.add_argument("--adb", help="phone address host:port for the phone-side check")
    parser.add_argument("--keep-going", action="store_true",
                        help="record an unexplained error and continue (default: stop)")
    args = parser.parse_args()

    global _log_file
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _log_file = LOG.open("a")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_md = ROOT / "tmp" / f"pd_exercise_{stamp}.md"
    out_json = ROOT / "tmp" / f"pd_exercise_{stamp}.json"

    rows, skipped = load_rows(args)
    say(f"catalogue: {len(rows)} to send, {len(skipped)} skipped")

    # The poll is off by default; without it the rail row is minutes stale.
    baseline = board(["pac1954 rate 100", "pd target_c status", "pac1954 status",
                      "pac1954 status"], COMMAND_BUDGET_S)
    start = settled_status(reply_of(baseline))
    say(f"baseline: {start['line']!r} rail: {rail_of(reply_of(baseline))}")
    if not (start["running"] and start["contract"]):
        say("STOP: no PD session with a contract; run `usb target_c power on` first")
        return 2

    results = []
    stopped = ""
    for row in rows:
        verb = row["verb"]
        name = f"{verb}!" if row["destructive"] else verb
        flags = " --illegal" if row["source_may"] == "no" else ""
        budget = RESET_BUDGET_S if verb in DESTRUCTIVE_RESET else COMMAND_BUDGET_S
        ready = settled_status("")
        if not (ready["contract"] and ready["ready"]):
            ready = wait_contract(RENEGOTIATE_S)
        if not (ready["contract"] and ready["ready"]):
            stopped = f"{verb}: no contract before the step ({ready['line']})"
            say(f"STOP: {stopped}")
            break
        phone_before = phone(args.adb)
        record = board([f"pd target_c send {name}{flags}", "pac1954 status",
                        "pd target_c status", "pd target_c status"], budget)
        text = reply_of(record)
        parsed = parse_pdx(text)
        after = (wait_contract(RENEGOTIATE_S) if verb in DESTRUCTIVE_RESET
                 else settled_status(text))
        rail = rail_of(text)
        phone_after = phone(args.adb)
        if verb in DESTRUCTIVE_RESET:
            phone_after = phone(args.adb)
        reply_types = [r["text"].split(" id")[0] for r in parsed["replies"]]
        entry = {
            "message": verb, "number": row["number"], "class": row["class"],
            "destructive": row["destructive"], "legal_for_source": row["source_may"],
            "expected": row["expected"], "spec": row["spec"],
            "sent": parsed["sent"], "outcome": parsed.get("outcome"),
            "reply_types": reply_types, "replies": parsed["replies"],
            "latency_ms": int(parsed["latency_ms"]) if parsed.get("latency_ms") else None,
            "tx_ms": int(parsed["tx_ms"]) if parsed.get("tx_ms") else None,
            "detail": parsed.get("detail", ""), "note": parsed["note"],
            "session_after": after, "rail": rail,
            "phone_before": phone_before, "phone_after": phone_after,
            "board_verdict": record.get("status"),
        }
        problem = explain(row, parsed, start, after)
        if problem:
            entry["error"] = problem
        results.append(entry)
        say(f"{verb}: {entry['outcome']} {reply_types} {entry['latency_ms']} ms"
            f" phone={phone_after} rail={rail[:80]!r}" + (f"  ERROR {problem}" if problem else ""))
        if problem and not args.keep_going:
            stopped = f"{verb}: {problem}"
            say(f"STOP at first unexplained error: {stopped}")
            break
        if verb == "sink-cap" and entry["outcome"] != "reply":
            stopped = "control failed: sink-cap got no Sink_Capabilities"
            say(f"STOP: {stopped}")
            break

    out_json.write_text(json.dumps({"when": stamp, "results": results, "skipped":
                                    [{"message": s["name"], "why": s["skip"]} for s in skipped],
                                    "stopped": stopped}, indent=2))
    lines = [f"# PD exercise {stamp}", "",
             f"Stopped: {stopped or 'no'}", "",
             "| message | sent | reply | latency ms | notes |", "|---|---|---|---|---|"]
    for r in results:
        phone_note = ""
        if r["phone_after"]:
            p = r["phone_after"]
            phone_note = f" phone {p['level']}% ac={p['ac']} {p['current_ma']} mA;"
        notes = f"{r['outcome']} {r['detail']}; {r['note']}{phone_note} {r.get('error', '')}".strip()
        reply = "; ".join(x["text"] for x in r["replies"]) or "-"
        lines.append(f"| {r['message']} | {r['sent'] or '-'} | {reply} | {r['latency_ms']} | "
                     f"{notes.replace('|', '/')} |")
    lines += ["", "## Not sent", ""] + [f"- {s['name']}: {s['skip']}" for s in skipped]
    out_md.write_text("\n".join(lines) + "\n")
    say(f"wrote {out_md.relative_to(ROOT)} and {out_json.relative_to(ROOT)}")
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
