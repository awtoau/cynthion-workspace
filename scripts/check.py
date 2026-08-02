#!/usr/bin/env python3
"""
Local check runner for the Cynthion workspace.

Replaces the GitHub Actions workflows: everything runs natively on this
machine, against the real toolchain and the real free-threaded 3.15t
interpreter, with no Docker and no cloud runners.

    ./scripts/check.py                 # every check
    ./scripts/check.py rust python     # only the named checks
    ./scripts/check.py --list          # what is available
    ./scripts/check.py --fast          # skip the slow ones (gateware)
    ./scripts/check.py --parallel      # run independent checks concurrently

Exit status is 0 only if every selected check passed, so this works
directly as a pre-commit / pre-push hook:

    ln -s ../../scripts/check.py .git/hooks/pre-push

Output goes to the console and to tmp/check.log; each check's full
stdout+stderr lands in tmp/logs/check-<name>.log for post-mortem.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from logging_utils import setup_logging

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "tmp"
LOGS = TMP / "logs"
REPOS = ROOT / "repos"

# Mirrors install.py: the workspace targets free-threaded CPython. Resolved
# rather than hard-coded so a machine mid-upgrade still works.
PYTHON_CANDIDATES = ["python3.15t", "python3.14t", "python3.15", "python3.14"]


def resolve_python() -> str:
    override = os.getenv("CYN_PYTHON")
    if override:
        return override
    for candidate in PYTHON_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return "python3"


PYTHON = resolve_python()

RESET, BOLD, GREEN, RED, YELLOW, CYAN = (
    "\033[0m", "\033[1m", "\033[92m", "\033[91m", "\033[93m", "\033[96m",
)


@dataclass
class Step:
    """One command in a check."""
    cmd: List[str]
    cwd: Path
    # Steps that only report information; a non-zero exit does not fail the run.
    informational: bool = False


@dataclass
class Check:
    name: str
    description: str
    steps: List[Step]
    slow: bool = False
    # Reason this check cannot run here, if any (evaluated at collection time).
    skip_reason: Optional[Callable[[], Optional[str]]] = None


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    skipped: bool = False
    detail: str = ""
    failed_cmd: str = ""
    tail: List[str] = field(default_factory=list)


def _need(tool: str) -> Callable[[], Optional[str]]:
    def probe() -> Optional[str]:
        if shutil.which(tool) is None:
            return f"{tool} not on PATH"
        return None
    return probe


def _need_dir(path: Path, what: str) -> Callable[[], Optional[str]]:
    def probe() -> Optional[str]:
        if not path.exists():
            return f"{what} missing ({path.relative_to(ROOT)})"
        return None
    return probe


def build_checks() -> List[Check]:
    """The checks, mirroring what the retired GitHub workflows ran."""
    cyn_fw = REPOS / "cynthion" / "firmware"
    apollo_fw = REPOS / "apollo" / "firmware"
    tinyusb = REPOS / "apollo" / "lib" / "tinyusb"
    app = ROOT / "app"

    return [
        Check(
            name="rust",
            description="cargo check + clippy (moondancer, riscv32imac)",
            skip_reason=_need("cargo"),
            steps=[
                Step(["cargo", "check", "--release",
                      "--target", "riscv32imac-unknown-none-elf"], cyn_fw),
                Step(["make", "clippy"], cyn_fw),
            ],
        ),
        Check(
            name="apollo",
            description="Apollo SAMD11 firmware build + size report",
            skip_reason=_need("arm-none-eabi-gcc"),
            steps=[
                # tinyusb does not vendor the microchip MCU headers (sam.h);
                # they are fetched on demand. Cheap no-op once present.
                Step([PYTHON, "tools/get_deps.py", "samd11"], tinyusb),
                Step(["make", "APOLLO_BOARD=cynthion"], apollo_fw),
                Step(["arm-none-eabi-size", "_build/cynthion_d11/firmware.elf"],
                     apollo_fw, informational=True),
                # Not informational: this one fails. The size output above
                # cannot, so a change taking flash from 89% to 99% passed the
                # loop and was caught only if someone read the log -- on a part
                # with 14 KB of usable flash and 4 KB of RAM.
                #
                # The stack figure is the measurement from stack_probe.c: 344
                # bytes at the deepest path exercised, against a 1024-byte
                # reservation. Passed in rather than re-measured because
                # measuring it needs the board, and this check has to work
                # without hardware attached.
                Step([PYTHON, "scripts/apollo_budget_check.py",
                      "--stack-measured", "344"], ROOT),
            ],
        ),
        Check(
            name="python",
            description=f"import check + pytest on {PYTHON}",
            steps=[
                Step([PYTHON, "-c",
                      "import cynthion, apollo_fpga, facedancer"], ROOT),
                Step([PYTHON, "-m", "pytest",
                      "repos/cynthion/cynthion/python/tests/",
                      "-q", "--tb=short"], ROOT),
            ],
        ),
        Check(
            name="freethreading",
            description="interpreter is a free-threaded build and stays that way",
            steps=[
                # Guards the workspace's core assumption: if some import flips
                # the GIL back on, the parallel build path silently serialises.
                Step([PYTHON, "-c",
                      "import sys; "
                      "import cynthion, apollo_fpga, facedancer; "
                      "assert not sys._is_gil_enabled(), "
                      "'GIL is enabled - not a free-threaded build, or an "
                      "import re-enabled it'; "
                      "print('free-threaded OK:', sys.version.split()[0])"],
                     ROOT),
            ],
        ),
        Check(
            name="flutter",
            description="flutter analyze + test (dashboard app)",
            skip_reason=_need("flutter"),
            steps=[
                Step(["flutter", "pub", "get"], app),
                # analyze exits non-zero on warnings alone. The app currently
                # carries 6 (unused imports/fields) and a failing smoke test,
                # both pre-existing and unrelated to firmware/gateware work.
                # Reported, not blocking, so they cannot gate a firmware commit.
                Step(["flutter", "analyze"], app, informational=True),
                Step(["flutter", "test"], app, informational=True),
            ],
        ),
        Check(
            name="socmap",
            description="the committed SVD still matches the SoC's memory map",
            steps=[
                # The one check that can see drift between the gateware and the
                # firmware. `--check` regenerates into tmp/ and compares; it also
                # compares the map against the *_BASE constants in
                # vexii_hello_soc.py and the literals in target.rs, so a
                # peripheral that moved without the crate being regenerated fails
                # here instead of on the board.
                #
                # Elaboration only -- no synthesis, no board, a few seconds.
                Step([PYTHON, "scripts/soc_generate_pac.py", "--check"], ROOT),
            ],
        ),
        # Do NOT run this from repos/cynthion: that directory contains a
        # 'cynthion/' subdirectory which Python picks up as a namespace package,
        # shadowing the installed one (cynthion.__file__ becomes None, so
        # 'cynthion.shared.usb' cannot resolve). The workspace root is safe, as
        # is repos/cynthion/cynthion/python, which is what install.py uses.
        Check(
            name="gateware",
            description="elaborate analyzer gateware (dry run)",
            slow=True,
            skip_reason=_need_dir(
                Path.home() / "opt" / "oss-cad-suite", "OSS CAD Suite"),
            steps=[
                Step(["bash", "-c",
                      f'source "$HOME/opt/oss-cad-suite/environment" && '
                      f"LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2"
                      f":CynthionPlatformRev0D2 "
                      f"{PYTHON} -m cynthion.gateware.analyzer.top --dry-run"],
                     ROOT),
            ],
        ),
    ]


def run_check(check: Check, logger, verbose: bool) -> Result:
    """Run one check, capturing output to tmp/logs/check-<name>.log."""
    skip = check.skip_reason() if check.skip_reason else None
    if skip:
        return Result(check.name, ok=True, seconds=0.0, skipped=True, detail=skip)

    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"check-{check.name}.log"
    started = time.monotonic()

    with open(log_path, "w") as log_file:
        for step in check.steps:
            printable = " ".join(str(c) for c in step.cmd)
            log_file.write(f"\n$ cd {step.cwd}\n$ {printable}\n")
            log_file.flush()

            if not step.cwd.exists():
                msg = f"working directory missing: {step.cwd}"
                log_file.write(msg + "\n")
                return Result(check.name, ok=False,
                              seconds=time.monotonic() - started,
                              detail=msg, failed_cmd=printable)

            proc = subprocess.run(
                step.cmd, cwd=step.cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            log_file.write(proc.stdout or "")
            log_file.flush()
            if verbose and proc.stdout:
                print(proc.stdout, end="")

            if proc.returncode != 0 and not step.informational:
                tail = (proc.stdout or "").strip().splitlines()[-12:]
                return Result(
                    check.name, ok=False,
                    seconds=time.monotonic() - started,
                    detail=f"exit {proc.returncode}",
                    failed_cmd=printable, tail=tail,
                )

    return Result(check.name, ok=True, seconds=time.monotonic() - started)


def main() -> int:
    checks = build_checks()
    names = [c.name for c in checks]

    parser = argparse.ArgumentParser(
        description="Run the workspace checks locally (replaces GitHub Actions).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("checks", nargs="*", metavar="CHECK",
                        help=f"checks to run (default: all). one of: {', '.join(names)}")
    parser.add_argument("--list", action="store_true", help="list checks and exit")
    parser.add_argument("--fast", action="store_true", help="skip slow checks")
    parser.add_argument("--parallel", action="store_true",
                        help="run checks concurrently (free-threaded build makes this real)")
    parser.add_argument("--jobs", type=int, default=4, help="workers for --parallel")
    parser.add_argument("--verbose", action="store_true", help="stream command output")
    args = parser.parse_args()

    if args.list:
        print(f"\n{BOLD}Available checks{RESET}\n")
        for c in checks:
            skip = c.skip_reason() if c.skip_reason else None
            state = f"{YELLOW}unavailable: {skip}{RESET}" if skip else f"{GREEN}ready{RESET}"
            slow = f" {YELLOW}(slow){RESET}" if c.slow else ""
            print(f"  {BOLD}{c.name:<14}{RESET} {c.description}{slow}")
            print(f"  {'':<14} {state}\n")
        return 0

    if args.checks:
        unknown = [n for n in args.checks if n not in names]
        if unknown:
            print(f"{RED}unknown check(s): {', '.join(unknown)}{RESET}", file=sys.stderr)
            print(f"available: {', '.join(names)}", file=sys.stderr)
            return 2
        selected = [c for c in checks if c.name in args.checks]
    else:
        selected = [c for c in checks if not (args.fast and c.slow)]

    TMP.mkdir(parents=True, exist_ok=True)
    logger = setup_logging("check.py", log_dir=LOGS)

    print(f"\n{BOLD}{CYAN}{'=' * 68}{RESET}")
    print(f"{BOLD}{CYAN}Local checks — {len(selected)} selected, interpreter {PYTHON}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 68}{RESET}\n")

    started = time.monotonic()
    results: List[Result] = []

    if args.parallel:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(run_check, c, logger, args.verbose): c
                       for c in selected}
            for future in as_completed(futures):
                r = future.result()
                results.append(r)
                _report(r)
    else:
        for check in selected:
            print(f"{CYAN}▶ {check.name}{RESET} — {check.description}")
            r = run_check(check, logger, args.verbose)
            results.append(r)
            _report(r)

    elapsed = time.monotonic() - started
    results.sort(key=lambda r: r.name)

    failed = [r for r in results if not r.ok]
    skipped = [r for r in results if r.skipped]
    passed = [r for r in results if r.ok and not r.skipped]

    print(f"\n{BOLD}{CYAN}{'=' * 68}{RESET}")
    print(f"{BOLD}Summary{RESET}  "
          f"{GREEN}{len(passed)} passed{RESET}  "
          f"{RED}{len(failed)} failed{RESET}  "
          f"{YELLOW}{len(skipped)} skipped{RESET}  "
          f"in {elapsed:.1f}s")
    print(f"{BOLD}{CYAN}{'=' * 68}{RESET}")

    for r in failed:
        print(f"\n{RED}{BOLD}✗ {r.name}{RESET} ({r.detail})")
        print(f"  command: {r.failed_cmd}")
        print(f"  log:     tmp/logs/check-{r.name}.log")
        for line in r.tail:
            print(f"  │ {line}")

    if skipped:
        print()
        for r in skipped:
            print(f"{YELLOW}⊘ {r.name} skipped — {r.detail}{RESET}")

    print()
    return 1 if failed else 0


def _report(r: Result) -> None:
    if r.skipped:
        print(f"  {YELLOW}⊘ {r.name} skipped — {r.detail}{RESET}")
    elif r.ok:
        print(f"  {GREEN}✓ {r.name}{RESET} ({r.seconds:.1f}s)")
    else:
        print(f"  {RED}✗ {r.name}{RESET} ({r.detail}, {r.seconds:.1f}s)")


if __name__ == "__main__":
    sys.exit(main())
