#!/usr/bin/env python3
"""dev.py -- the one entry point for cynthion-workspace (awto dev.py convention).

AI agents: run `./dev.py describe` for machine-readable JSON help.
Humans: run `./dev.py --help`.

All output is timestamped in LOCAL time and mirrored to ./tmp/logs/dev.log.

## Why this exists, given the repo already had scripts

It had about sixty of them, each correct and none discoverable. The build recipe
for the firmware is genuinely non-trivial -- two cargo targets with different
linker scripts, two objcopy splits by destination, a flash program at a specific
offset, a QEMU gate that must run first, a stale-bitstream comparison that must
run last -- and every one of those steps was being invoked by hand, in a
different order each time, with the flags remembered or forgotten. Three separate
stale-image incidents came out of that.

So this file owns NO build logic. It is a door, and `scripts/soc_run.py`,
`scripts/soc_test.py`, `scripts/check.py` and `scripts/soc_generate_pac.py` are
the rooms. `STEPS` below is the whole map. If a step needs a new flag, the flag
belongs on the script that owns the work, not here -- `--build-only` was added to
`soc_run.py` for exactly that reason rather than reimplementing cargo here.

## Hardware, and what is safe to run without any

The steps in `GATE` and `CI` touch no hardware: they build, run QEMU, and compare
files. Everything that reaches the board is a separate command (`run`, `console`,
`flash`) and says so in its summary. That split is what lets the gate run on a
machine with nothing plugged in.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT = "cynthion-workspace"

# `scripts/dev.py`, so the repo is one level up. Python is support tooling here --
# the products are Rust firmware, Amaranth gateware and C -- so per the awto
# layout rule the logic lives under scripts/ and `./dev.py` at the root is a shim.
REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
LOGS = TMP / "logs"
LOG = LOGS / "dev.log"

PY = sys.executable


def script(name: str) -> str:
    return str(REPO / "scripts" / name)


# ---------------------------------------------------------------------------
# STEPS -- the only project-specific part.
#
# name -> (summary, argv, accepts_extra_args)
#
# Each argv is a script that already owns its own logic and its own log file.
# Nothing here reimplements a build; where a step needed a narrower mode than
# the script offered, the mode was added to that script.
# ---------------------------------------------------------------------------
STEPS: dict[str, tuple[str, list[str], bool]] = {
    # No board needed: builds firmware, bootloader and gateware, compares the
    # bitstream against the firmware just built, then stops.
    "build": ("build firmware, bootloader and gateware (no hardware touched)",
              [PY, script("soc_run.py"), "--build-only"], True),

    # The QEMU shell gate. Seconds, and it is what makes a bad build cheap to
    # find: the alternative is a ~60 s synthesis and a reconfigure.
    "test": ("boot the firmware under QEMU and assert what its shell says",
             [PY, script("soc_test.py")], True),

    # The workspace checks -- rust, apollo, python, freethreading, flutter,
    # socmap, irqlog, paths, gateware. `--parallel` is real here: the default
    # interpreter is free-threaded.
    "lint": ("workspace checks: clippy, layout, generated-map drift, paths",
             [PY, script("check.py"), "--parallel"], True),

    "fmt": ("reformat the Rust crates in place",
            ["cargo", "fmt", "--all"], True),
    "fmt-check": ("formatting check, writes nothing",
                  ["cargo", "fmt", "--all", "--check"], False),

    # Regenerating the PAC is a build step, not a chore: the SoC's memory map is
    # the source of every peripheral address the firmware uses, and a stale PAC
    # means firmware reading the wrong registers and reporting the numbers.
    "pac": ("regenerate the PAC and SVD from the SoC's own memory map",
            [PY, script("soc_generate_pac.py")], True),

    "sim": ("run the gateware simulations",
            [PY, script("soc_sims.py")], True),
}

# Hardware steps are NOT in STEPS: they are commands below, so that `gate` and
# `ci` cannot acquire one by accident.

# gate = fail-fast, blocks a commit. The first failure is the one to fix, and the
# order is cheapest-first so a formatting slip does not cost a gateware build.
GATE = ["fmt-check", "test", "lint", "build"]

# ci = run-all-collect-all -> one GO/NO-GO. Every leg runs even after a failure:
# a gateware build is about a minute, and finding the second problem on the next
# run rather than this one is what makes a red tree take an afternoon.
CI = ["fmt-check", "test", "lint", "build", "sim"]

# ---------------------------------------------------------------------------
# Logging -- Tier A: local time, stderr + ./tmp/logs/dev.log, colour on a TTY
# only, the file copy always plain so a later grep is not full of escapes.
# ---------------------------------------------------------------------------
_COLOR = {
    "FATAL": "\033[1;91m", "ERROR": "\033[31m", "WARN": "\033[33m",
    "INFO": "\033[97m", "DEBUG": "\033[94m", "ALERT": "\033[92m",
}
_RESET = "\033[0m"
_USE_COLOR = (
    sys.stderr.isatty()
    and not os.environ.get("NO_COLOR")
    and os.environ.get("CLICOLOR") != "0"
)


def _stamp() -> str:
    # Local zone including DST. Never a hardcoded +10:00 -- Selwyn observes DST,
    # so a fixed offset is an hour wrong every summer -- and never UTC, because a
    # human reads these live.
    return datetime.now().astimezone().strftime("%H:%M:%S.%f%z")


def log(msg: str, level: str = "INFO") -> None:
    line = f"{_stamp()}  {level:<5} [dev.py] {msg}"
    if _USE_COLOR:
        sys.stderr.write(f"{_COLOR.get(level, '')}{line}{_RESET}\n")
    else:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass            # never let logging kill the run


SKIPPED = 125           # distinct from any real tool's exit code


def run_step(name: str, extra: list[str] | None = None) -> int:
    """Run one STEPS entry. Returns its exit code, or SKIPPED."""
    _summary, argv, takes_extra = STEPS[name]
    if not argv:
        log(f"{name}: not configured for this project - skipped", "WARN")
        return SKIPPED
    if argv[0] != PY and not shutil.which(argv[0]):
        log(f"{name}: {argv[0]} not on PATH - skipped", "WARN")
        return SKIPPED
    cmd = argv + (list(extra) if (extra and takes_extra) else [])
    log("run: " + " ".join(cmd))
    rc = subprocess.call(cmd, cwd=REPO)
    log(f"rc={rc}: {name}", "INFO" if rc == 0 else "ERROR")
    return rc


def run_tool(argv: list[str], extra: list[str] | None = None) -> int:
    """Run a one-off tool that is not a STEPS entry."""
    cmd = argv + list(extra or [])
    log("run: " + " ".join(cmd))
    rc = subprocess.call(cmd, cwd=REPO)
    log(f"rc={rc}", "INFO" if rc == 0 else "ERROR")
    return rc


# ---------------------------------------------------------------------------
# Command registry -- ONE table, driving dispatch, --help and describe, so the
# three cannot drift apart.
# ---------------------------------------------------------------------------
COMMANDS: dict[str, dict] = {}


def command(summary: str, *, args: str = "", kind: str = "action"):
    def deco(fn):
        COMMANDS[fn.__name__.replace("cmd_", "").replace("_", "-")] = {
            "summary": summary, "args": args, "kind": kind, "fn": fn,
        }
        return fn
    return deco


@command("machine-readable command list (JSON, for AI agents)", kind="meta")
def cmd_describe(_extra: list[str]) -> int:
    print(json.dumps({
        "project": PROJECT,
        "schema": 1,
        "entrypoint": "./dev.py",
        "log": str(LOG.relative_to(REPO)),
        "exit_codes": {"0": "success", "2": "usage error",
                       "125": "step skipped (tool absent)", "other": "failure"},
        # Which commands reach the board, so an agent can tell what is safe to
        # run unattended from what needs hardware present.
        "needs_hardware": ["run", "console", "flash"],
        "commands": {
            name: {k: v for k, v in meta.items() if k != "fn"}
            for name, meta in COMMANDS.items()
        },
    }, indent=2))
    return 0


@command("check the toolchain is present; run this first when stuck", kind="meta")
def cmd_doctor(_extra: list[str]) -> int:
    log(f"project    : {PROJECT}")
    log(f"repo       : {REPO}")
    log(f"python     : {sys.version.split()[0]}")
    if hasattr(sys, "_is_gil_enabled"):
        state = "disabled (free-threaded)" if not sys._is_gil_enabled() else "enabled"
        log(f"gil        : {state}")
    else:
        log("gil        : n/a (<3.13)", "WARN")

    missing = []
    for name, (_s, argv, _e) in STEPS.items():
        if not argv:
            log(f"{name:10s} : not configured", "WARN")
        elif argv[0] == PY or shutil.which(argv[0]):
            log(f"{name:10s} : {argv[0]} ok")
        else:
            log(f"{name:10s} : {argv[0]} MISSING", "ERROR")
            missing.append(argv[0])

    # The cross toolchain and the FPGA flow are not in STEPS -- the scripts find
    # them themselves -- but their absence is the usual reason a build stops, so
    # doctor reports them rather than leaving it to a failure deep in a log.
    for tool in ("cargo", "riscv64-linux-gnu-objcopy", "yosys", "nextpnr-ecp5",
                 "qemu-system-riscv32"):
        if shutil.which(tool):
            log(f"{tool:26s} : ok", "DEBUG")
        else:
            log(f"{tool:26s} : MISSING", "ERROR")
            missing.append(tool)

    if missing:
        log(f"missing tools: {', '.join(sorted(set(missing)))}", "ERROR")
        return 1
    log("doctor ok", "ALERT")
    return 0


@command("remove tmp/ (scratch and logs); build output is left alone",
         kind="action")
def cmd_clean(_extra: list[str]) -> int:
    # tmp/ only. `build/` holds bitstreams that take a minute each to regenerate,
    # and cargo's target/ is cargo's business -- neither is scratch.
    if TMP.exists():
        shutil.rmtree(TMP)
        log(f"removed {TMP.relative_to(REPO)}")
    else:
        log("tmp/ already absent")
    return 0


@command("build, then configure the board and read its console",
         args="[soc_run.py flags]", kind="action")
def cmd_run(extra: list[str]) -> int:
    """The full loop, and the only command here that configures the FPGA."""
    return run_tool([PY, script("soc_run.py")], extra)


@command("read every script in scripts/ and report what still reaches it",
         args="[--markdown] [--only live|cited|orphan]", kind="action")
def cmd_audit(extra: list[str]) -> int:
    return run_tool([PY, script("audit_scripts.py")], extra)


@command("open the RISC-V console on the board", args="[flags]", kind="action")
def cmd_console(extra: list[str]) -> int:
    return run_tool([PY, script("riscv_console.py")], extra)


@command("stage firmware over JTAG without rebuilding the bitstream",
         args="[flags]", kind="action")
def cmd_stage(extra: list[str]) -> int:
    return run_tool([PY, script("soc_jtag_stage.py")], extra)


@command("fail-fast pre-commit gate: " + " + ".join(GATE), kind="aggregate")
def cmd_gate(_extra: list[str]) -> int:
    for name in GATE:
        rc = run_step(name)
        if rc not in (0, SKIPPED):
            log(f"GATE FAILED at {name} (rc={rc})", "FATAL")
            return rc
    log("GATE PASSED", "ALERT")
    return 0


@command("run every leg, collect all results, one GO/NO-GO", kind="aggregate")
def cmd_ci(_extra: list[str]) -> int:
    results: dict[str, int] = {}
    for name in CI:
        results[name] = run_step(name)      # no early exit -- that is the point
    failed = {k: v for k, v in results.items() if v not in (0, SKIPPED)}
    skipped = [k for k, v in results.items() if v == SKIPPED]
    log("-" * 52)
    for name, rc in results.items():
        state = "SKIP" if rc == SKIPPED else ("ok" if rc == 0 else f"FAIL rc={rc}")
        log(f"  {name:12s} {state}", "INFO" if rc in (0, SKIPPED) else "ERROR")
    if skipped:
        log(f"{len(skipped)} step(s) skipped: {', '.join(skipped)}", "WARN")
    if failed:
        log(f"NO-GO - {len(failed)} of {len(results)} failed: "
            f"{', '.join(failed)}", "FATAL")
        return 1
    log("GO", "ALERT")
    return 0


@command("verify dev.py's own contract (help/describe/exit codes)", kind="meta")
def cmd_selftest(_extra: list[str]) -> int:
    failures = []

    def check(label: str, cond: bool) -> None:
        log(f"  {label}: {'ok' if cond else 'FAIL'}", "INFO" if cond else "ERROR")
        if not cond:
            failures.append(label)

    out = subprocess.run([PY, __file__, "describe"],
                         capture_output=True, text=True, cwd=REPO)
    check("describe exits 0", out.returncode == 0)
    try:
        doc = json.loads(out.stdout)
        check("describe emits valid JSON", True)
        check("describe has project+commands", {"project", "commands"} <= doc.keys())
        check("every command documented", all(
            m.get("summary") for m in doc.get("commands", {}).values()))
        # The hardware split is load-bearing: `gate` and `ci` must stay runnable
        # on a machine with no board, so no step may also be a hardware command.
        check("no gate/ci step needs hardware",
              not (set(GATE + CI) & set(doc.get("needs_hardware", []))))
    except json.JSONDecodeError:
        check("describe emits valid JSON", False)

    h = subprocess.run([PY, __file__, "--help"],
                       capture_output=True, text=True, cwd=REPO)
    check("--help exits 2 (usage)", h.returncode == 2)
    check("--help lists commands", all(c in h.stdout for c in COMMANDS))

    u = subprocess.run([PY, __file__, "no-such-command"],
                       capture_output=True, text=True, cwd=REPO)
    check("unknown command exits 2", u.returncode == 2)

    check("registry has no duplicate handlers",
          len({m["fn"] for m in COMMANDS.values()}) == len(COMMANDS))

    # Every script named in STEPS must exist. A renamed script would otherwise
    # surface as a step that fails deep inside a subprocess.
    for name, (_s, argv, _e) in STEPS.items():
        if argv and argv[0] == PY:
            check(f"{name} script exists", Path(argv[1]).exists())

    if failures:
        log(f"selftest FAILED: {', '.join(failures)}", "FATAL")
        return 1
    log("selftest ok", "ALERT")
    return 0


def _register_steps() -> None:
    """Expose each STEPS entry as a first-class command."""
    for name, (summary, _argv, takes_extra) in STEPS.items():
        if name in COMMANDS:
            continue
        COMMANDS[name] = {
            "summary": summary,
            "args": "[extra args passed through]" if takes_extra else "",
            "kind": "step",
            "fn": (lambda n: lambda extra: run_step(n, extra))(name),
        }


_register_steps()


def usage() -> int:
    print(__doc__.split("## Why this exists")[0].rstrip())
    print("\nUsage: ./dev.py <command> [args]\n")
    # Iterate the kinds actually present. A hardcoded list silently drops any new
    # kind from --help, which selftest is what catches.
    order = ["aggregate", "step", "action", "meta"]
    kinds = sorted({m["kind"] for m in COMMANDS.values()},
                   key=lambda k: (order.index(k) if k in order else len(order), k))
    for kind in kinds:
        group = {n: m for n, m in COMMANDS.items() if m["kind"] == kind}
        if not group:
            continue
        print(f"  {kind}:")
        for name, meta in sorted(group.items()):
            print(f"    {name:<12} {meta['summary']}")
        print()
    print("  run, console and stage reach the board; nothing else does.")
    print(f"\nLog: {LOG.relative_to(REPO)}   JSON help: ./dev.py describe")
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        return usage()
    name, *extra = argv
    meta = COMMANDS.get(name)
    if meta is None:
        print(f"unknown command: {name!r} - try ./dev.py describe", file=sys.stderr)
        return 2
    rc = meta["fn"](extra)
    return 0 if rc == SKIPPED else rc


if __name__ == "__main__":
    sys.exit(main())
