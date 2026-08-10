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
import re
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
# The single log every script in this repo writes to. See `devlog`.
LOG = LOGS / "dev.log"

PY = sys.executable


def script(name: str) -> str:
    return str(REPO / "scripts" / name)


# ---------------------------------------------------------------------------
# STEPS -- the only project-specific part.
#
# name -> (summary, argv, accepts_extra_args)
#
# Each argv is a script that already owns its own logic. They all log through
# `devlog` into the one file, so a step's output is here rather than beside it.
# Nothing here reimplements a build; where a step needed a narrower mode than
# the script offered, the mode was added to that script.
# ---------------------------------------------------------------------------
STEPS: dict[str, tuple[str, list[str], bool]] = {
    # THE HyperRAM GATE. Liveness, then latency per CK rung, then the matrix
    # twice and diffed -- in that order, because a sweep over an axis that does
    # not reach the part produces rows that mean nothing (#334, #335). Needs a
    # BIST bitstream on the board.
    "hyperram-verify": ("verify the HyperRAM BIST rig end to end (needs the board)",
                 [PY, script("hyperram_verify.py")], True),

    # No board needed: builds firmware, bootloader and gateware, compares the
    # bitstream against the firmware just built, then stops.
    "build": ("build firmware, bootloader and gateware (no hardware touched)",
              [PY, script("soc_run.py"), "--build-only"], True),

    # N builds at once, one per variant. Also no board: a CK ladder is one
    # bitstream per rung, and since #351 every artifact of a build lives under
    # its own directory, so the rungs are concurrent rather than sequential.
    "build-many": ("build several variants concurrently (no hardware touched)",
                   [PY, script("soc_build_fanout.py")], True),

    # The QEMU shell gate. Seconds, and it is what makes a bad build cheap to
    # find: the alternative is a ~60 s synthesis and a reconfigure.
    "test": ("boot the firmware under QEMU and assert what its shell says",
             [PY, script("soc_test.py")], True),

    # The workspace checks -- rust, apollo, python, freethreading, socmap,
    # irqlog, paths. `--parallel` is real here: the default interpreter is
    # free-threaded, though the whole set now runs in under a second.
    "lint": ("workspace checks: clippy, layout, generated-map drift, paths",
             [PY, script("check.py"), "--parallel"], True),

    # fmt and fmt-check are NOT here: they need one cargo invocation per crate.
    # See `cargo_fmt` below.

    # Regenerating the PAC is a build step, not a chore: the SoC's memory map is
    # the source of every peripheral address the firmware uses, and a stale PAC
    # means firmware reading the wrong registers and reporting the numbers.
    "pac": ("regenerate the PAC and SVD from the SoC's own memory map",
            [PY, script("soc_generate_pac.py")], True),

    "sim": ("run the gateware simulations, with their repetition",
            [PY, script("soc_sims.py")], True),

    # EVERY simulation, one cycle of each -- so `gate` gets the whole suite's
    # coverage rather than the nine cheapest of fifteen. What `sim` adds over
    # this is repetition, not tests: burst counts above one, the parameter
    # sweeps, the long payloads. See #175 and soc_sims.py.
    "sim-once": ("run every gateware simulation, one cycle each",
                 [PY, script("soc_sims.py"), "--tier", "once"], True),

    # A working link checker that nothing invoked. `./dev.py audit` reported it
    # as an orphan alongside genuinely dead probes, which is the argument for
    # wiring it in rather than archiving it: the tool was fine, the reachability
    # was not.
    "docs": ("check every relative link in the tracked Markdown resolves",
             [PY, script("check_doc_links.py")], True),
}

# Hardware steps are NOT in STEPS: they are commands below, so that `gate` and
# `ci` cannot acquire one by accident.

# gate = fail-fast, blocks a commit. The first failure is the one to fix, and the
# order is cheapest-first so a formatting slip does not cost a gateware build.
#
# `fmt-check` is NOT here, and that is a live debt rather than a decision.
# The firmware has never been rustfmt'd -- 1,671 diff lines across ~25 files --
# so including it makes the gate red on every tree state, which is the failure
# mode this file has already fixed twice today. Reformatting is its own commit
# and its own review; `./dev.py fmt-check` runs on demand until then. See #165.
GATE = ["test", "lint", "sim-once", "build"]

# ci = run-all-collect-all -> one GO/NO-GO. Every leg runs even after a failure:
# a gateware build is about a minute, and finding the second problem on the next
# run rather than this one is what makes a red tree take an afternoon.
CI = ["test", "lint", "build", "sim", "docs"]

# ---------------------------------------------------------------------------
# Logging -- Tier A: local time, stderr + ./tmp/logs/dev.log, colour on a TTY
# only, the file copy always plain so a later grep is not full of escapes.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))

from devlog import log, spawn  # noqa: E402

SKIPPED = 125           # distinct from any real tool's exit code


def shorten(cmd: list[str]) -> str:
    """A command line worth reading.

    The literal argv is two absolute paths -- an interpreter under the user's
    home and a script under the repo root -- which is 90 characters of noise
    around the one word that identifies what is running. Both are constants for
    the whole session, so printing them every time says nothing.

    It also means the log does not carry the machine's directory layout, which on
    a public repo is worth not writing down in the first place.
    """
    parts = []
    for item in cmd:
        if item == PY:
            parts.append("python3")
        elif item.startswith(str(REPO) + "/"):
            parts.append(item[len(str(REPO)) + 1:])
        else:
            parts.append(item)
    return " ".join(parts)


def crates() -> list[Path]:
    """Every Rust crate, found rather than listed.

    There is NO workspace manifest above `firmware/` -- the four crates are
    independent, and deliberately so: they target different linker scripts and
    `cynthion-payload` is not built by the same flow at all. So `cargo fmt --all`
    from the repo root has no manifest to act on and fails on any tree state,
    which is exactly what it did until this replaced it.

    Discovered by glob so that adding a crate does not require editing this file
    and silently leaving the new one unformatted.
    """
    return sorted(p.parent for p in (REPO / "firmware").glob("*/Cargo.toml"))


def cargo_fmt(check: bool) -> int:
    """`cargo fmt` over each crate, since there is no workspace to do it once."""
    if not shutil.which("cargo"):
        log("cargo not on PATH - skipped", "WARN")
        return SKIPPED
    worst = 0
    for crate in crates():
        cmd = ["cargo", "fmt", "--manifest-path", str(crate / "Cargo.toml")]
        if check:
            cmd.append("--check")
        rc = spawn(cmd)
        if rc != 0:
            log(f"rc={rc}: {crate.name}", "ERROR")
            worst = rc
    if worst == 0:
        log(f"formatting ok across {len(crates())} crates")
    return worst


def run_step(name: str, extra: list[str] | None = None) -> int:
    """Run one step. Returns its exit code, or SKIPPED.

    Falls through to the command registry for steps that are not a single argv --
    `fmt` and `fmt-check` each need one cargo invocation per crate, and a
    `STEPS` table of flat argv lists cannot express that.
    """
    if name not in STEPS:
        return COMMANDS[name]["fn"](list(extra or []))
    _summary, argv, takes_extra = STEPS[name]
    if not argv:
        log(f"{name}: not configured for this project - skipped", "WARN")
        return SKIPPED
    if argv[0] != PY and not shutil.which(argv[0]):
        log(f"{name}: {argv[0]} not on PATH - skipped", "WARN")
        return SKIPPED
    cmd = argv + (list(extra) if (extra and takes_extra) else [])
    rc = spawn(cmd)
    if rc not in (0,):
        log(f"{name} failed", "ERROR")
    return rc


def run_tool(argv: list[str], extra: list[str] | None = None) -> int:
    """Run a one-off tool that is not a STEPS entry."""
    cmd = argv + list(extra or [])
    rc = spawn(cmd)
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
        "needs_hardware": ["run", "console", "flash", "hyperram", "probe",
                           "test-board", "fw", "stage", "capture", "typec",
                           "optlevel"],
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


@command("install what a fresh machine needs: packages, Rust, Python, udev",
         args="[--system] [--dry-run]", kind="action")
def cmd_setup(extra: list[str]) -> int:
    return run_tool([PY, script("machine_setup.py")], extra)


@command("drive Lattice Diamond: probe, ladder, flow, compare",
         args="<probe|ladder|flow|compare> [-- args]", kind="action")
def cmd_diamond(extra: list[str]) -> int:
    return run_tool([PY, script("diamond.py")], extra)


@command("build at each opt-level, flash, and measure IPC on the board (#167)",
         args="[--levels z s 3] [--no-board]", kind="action")
def cmd_optlevel(extra: list[str]) -> int:
    return run_tool([PY, script("opt_level_sweep.py")], extra)


@command("the HyperRAM harnesses: what each measures, whether it ran, and run one",
         args="[identify|regfuzz|ceiling|readclksel|fifo|ladder|measure|dqs-pins] "
              "[-- args]",
         kind="action")
def cmd_hyperram(extra: list[str]) -> int:
    """Bare `./dev.py hyperram` prints the inventory and touches nothing.

    Named in `needs_hardware` because every harness but `dqs-pins` configures the
    FPGA, so `gate` and `ci` must never acquire it.
    """
    return run_tool([PY, script("hyperram_harnesses.py")], extra)


@command("build area, timing and configuration in Postgres: status, report, graph",
         args="[status|record|report|graph|flush|show|sql|selftest|collect] "
              "[-- args]",
         kind="action")
def cmd_metrics(extra: list[str]) -> int:
    """The reading end of the build record (#232).

    Recording is NOT a command here and deliberately so: `soc_run.py` does it on
    every gateware build, unconditionally, so no path produces a bitstream and no
    row. What this offers is the other direction -- is the server up, what did
    this build cost against the last one and against the best ever, and the
    trend graphs.
    """
    return run_tool([PY, script("build_metrics.py")], extra or ["status"])


@command("read every script in scripts/ and report what still reaches it",
         args="[--markdown] [--only live|cited|orphan]", kind="action")
def cmd_audit(extra: list[str]) -> int:
    return run_tool([PY, script("audit_scripts.py")], extra)


@command("reformat the Rust crates in place", kind="step")
def cmd_fmt(_extra: list[str]) -> int:
    return cargo_fmt(check=False)


@command("formatting check, writes nothing", kind="step")
def cmd_fmt_check(_extra: list[str]) -> int:
    return cargo_fmt(check=True)


@command("after a load: is the FPGA alive, is it OURS, is what it reports true",
         args="[--no-console]", kind="action")
def cmd_probe(extra: list[str]) -> int:
    """Eleven checks in the order that isolates the layer, so the FIRST failure
    names it.

    Reachable here rather than only as a script because it is what to run when
    the board goes quiet, and "the board is dead" is not one state -- a lost
    configuration, a stale bitstream, an unlocked PLL and a wedged CPU are four
    different problems that look identical from a terminal.
    """
    return run_tool([PY, script("soc_probe.py")], extra)


@command("run the shell suite against the BOARD instead of QEMU",
         args="[-v] [--features ...]", kind="action")
def cmd_test_board(extra: list[str]) -> int:
    """The same assertions, on real silicon.

    Not in `gate` or `ci`: it needs a board, already configured and already
    running this firmware. `./dev.py fw` puts it there.

    `--features` has to match what was flashed. It builds nothing here -- it
    tells the suite what to expect of the image already on the board, and a
    mismatch is a real failure worth keeping: it means the board is not running
    what you think.
    """
    return run_tool([PY, script("soc_test.py"), "--board"], extra)


@command("firmware change only: rebuild, write flash, reconfigure (no synthesis)",
         args="[soc_run.py flags]", kind="action")
def cmd_fw(extra: list[str]) -> int:
    """The fast loop. Seconds rather than the ~90 s `run` costs.

    Only correct because `.text` lives in flash: nothing of the firmware travels
    in the bitstream any more, so the ~60 s of synthesis `run` spends would
    produce a byte-identical bitstream to the one already on the board.
    `soc_run.py` refuses this if any section still loads via the bitstream.
    """
    return run_tool([PY, script("soc_run.py"), "--firmware-only"], extra)


@command("open the RISC-V console on the board", args="[flags]", kind="action")
def cmd_console(extra: list[str]) -> int:
    return run_tool([PY, script("riscv_console.py")], extra)


@command("decode the board's binary record stream (--self-test needs no board)",
         args="[flags]", kind="action")
def cmd_stream(extra: list[str]) -> int:
    """docs/binary-protocol.md. No firmware emits records yet -- `--self-test`
    and `tests/test_soc_stream.py` are what exercise the decoder today."""
    return run_tool([PY, script("soc_stream.py")], extra)


@command("stage firmware over JTAG without rebuilding the bitstream",
         args="[flags]", kind="action")
def cmd_stage(extra: list[str]) -> int:
    return run_tool([PY, script("soc_jtag_stage.py")], extra)


@command("watch the Type-C controllers across a cable attach or detach",
         args="[flags]", kind="action")
def cmd_typec(extra: list[str]) -> int:
    return run_tool([PY, script("typec_watch.py")], extra)


@command("read and verify the board's SPI flash", args="[flags]", kind="action")
def cmd_flash(extra: list[str]) -> int:
    return run_tool([PY, script("riscv_flash_check.py")], extra)


@command("capture the board's console to a file", args="[flags]", kind="action")
def cmd_capture(extra: list[str]) -> int:
    return run_tool([PY, script("riscv_console_capture.py")], extra)


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
    # `./dev.py test -- -v` is the habitual shape for "these flags are the inner
    # tool's, not yours", and it reached the inner tool as a literal `--` that
    # argparse then rejected. Both forms work now; the separator is consumed here.
    if extra and extra[0] == "--":
        extra = extra[1:]
    meta = COMMANDS.get(name)
    if meta is None:
        print(f"unknown command: {name!r} - try ./dev.py describe", file=sys.stderr)
        return 2
    rc = meta["fn"](extra)
    return 0 if rc == SKIPPED else rc


if __name__ == "__main__":
    sys.exit(main())
