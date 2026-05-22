#!/usr/bin/env python3
"""
awto — unified Cynthion workspace CLI

Subcommands:
  build   [rust|apollo|gateware|app|all]   build firmware / gateware / app
  check   [fast|rust|c|gateware|all]       pre-commit checks
  test    [--destructive]                  hardware self-tests
  clean   [rust|apollo|gateware|app|all]   clean build artefacts
  flash   [rust|apollo|gateware]           flash to connected device
  deploy                                   full build + flash cycle
  reset                                    reset Cynthion to Apollo mode

All output is written to tmp/awto-<cmd>.log in the workspace root.
"""

import argparse
import subprocess
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT  = Path(__file__).resolve().parent.parent
REPOS = ROOT / "repos"
FW    = REPOS / "cynthion/firmware"
APL   = REPOS / "apollo/firmware"
GW    = REPOS / "cynthion/cynthion/python"
APP   = ROOT / "app"
VENV  = ROOT / ".venv/bin/python"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _log_path(cmd: str) -> Path:
    log_dir = ROOT / "tmp"
    log_dir.mkdir(exist_ok=True)
    return log_dir / f"awto-{cmd}.log"


def run(label: str, args_: list, *, cwd=None, env=None, log=None, check=True):
    """Run a subprocess, tee output to log file and stdout. Return exit code."""
    cmd_str = " ".join(str(a) for a in args_)
    print(f"  {label}: {cmd_str}")
    if log:
        log.write(f"\n=== {label} ===\n{cmd_str}\n")
        log.flush()
    try:
        proc = subprocess.Popen(
            args_,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        lines = []
        for line in proc.stdout:
            sys.stdout.write("    " + line)
            if log:
                log.write(line)
            lines.append(line)
        proc.wait()
        if log:
            log.flush()
        if check and proc.returncode != 0:
            print(f"\n  FAIL (exit {proc.returncode})")
            sys.exit(proc.returncode)
        return proc.returncode
    except FileNotFoundError as e:
        print(f"  ERROR: {e}")
        if check:
            sys.exit(1)
        return 1


def check_venv():
    if not VENV.exists():
        print(f"ERROR: venv not found at {VENV}")
        print("  Run:  ./scripts/awto.py setup")
        sys.exit(1)


# ── build ──────────────────────────────────────────────────────────────────────

def _build_rust(log, release=False):
    profile = ["--release"] if release else []
    run("rust firmware", ["make", "bin"] + (["PROFILE=release"] if release else []),
        cwd=FW, log=log)


def _build_apollo(log, release=False):
    run("apollo C firmware",
        ["make", "APOLLO_BOARD=cynthion"],
        cwd=APL, log=log)
    elf = APL / "_build/cynthion_d11/firmware.elf"
    if elf.exists():
        run("apollo size", ["arm-none-eabi-size", str(elf)], cwd=APL, log=log, check=False)


def _build_gateware(log, release=False):
    run("facedancer gateware", ["make", "facedancer"], cwd=GW, log=log)


def _build_app(log, release=False):
    profile = "--release" if release else "--debug"
    run("flutter app", ["flutter", "build", "linux", profile], cwd=APP, log=log)


def cmd_build(args):
    target  = getattr(args, "target", "all")
    release = getattr(args, "release", False)
    log_path = _log_path("build")
    print(f"==> build {target}  (log: {log_path.relative_to(ROOT)})")
    with log_path.open("w") as log:
        targets = ["rust", "apollo", "gateware", "app"] if target == "all" else [target]
        dispatch = {
            "rust":     _build_rust,
            "apollo":   _build_apollo,
            "gateware": _build_gateware,
            "app":      _build_app,
        }
        for t in targets:
            dispatch[t](log, release)
    print("\nBuild complete.")


# ── check ──────────────────────────────────────────────────────────────────────

def _check_rust(log):
    run("rust check",
        ["cargo", "check", "--release", "--target", "riscv32imac-unknown-none-elf"],
        cwd=FW, log=log)
    run("rust clippy", ["make", "clippy"], cwd=FW, log=log)
    run("rust test", ["cargo", "test"], cwd=FW, log=log)


def _check_c(log):
    run("apollo C build",
        ["make", "APOLLO_BOARD=cynthion"],
        cwd=APL, log=log)


def _check_gateware(log):
    check_venv()
    run("gateware elaborate",
        [str(VENV), "-c",
         "from cynthion.gateware.facedancer import top; print('elaborate ok')"],
        log=log, check=False)


def _check_python(log):
    check_venv()
    run("python imports",
        [str(VENV), "-c", "import cynthion; import apollo_fpga; import facedancer"],
        log=log)
    scripts = [
        ROOT / "repos/cynthion/scripts/apollod.py",
        ROOT / "repos/cynthion/scripts/apollo-mux.py",
        ROOT / "repos/cynthion/scripts/test-fault-detection.py",
    ]
    existing = [str(s) for s in scripts if s.exists()]
    if existing:
        run("python syntax", [str(VENV), "-m", "py_compile"] + existing, log=log)
    run("python unit tests",
        [str(VENV), "-m", "pytest",
         "repos/cynthion/cynthion/python/tests/", "-q", "--tb=short"],
        cwd=ROOT, log=log, check=False)


def cmd_check(args):
    target   = getattr(args, "target", "fast")
    log_path = _log_path("check")
    print(f"==> check {target}  (log: {log_path.relative_to(ROOT)})")
    results: dict[str, str] = {}

    def checked(label, fn, log):
        try:
            fn(log)
            results[label] = "OK"
        except SystemExit:
            results[label] = "FAIL"

    with log_path.open("w") as log:
        if target in ("fast", "all", "rust"):
            checked("rust", _check_rust, log)
        if target in ("fast", "all", "c"):
            checked("c",    _check_c, log)
        if target in ("fast", "all"):
            checked("python", _check_python, log)
        if target in ("fast", "all", "gateware"):
            checked("gateware", _check_gateware, log)

    print()
    failed = []
    for label, status in results.items():
        mark = "✓" if status == "OK" else "✗"
        print(f"  {mark}  {label:<20} {status}")
        if status == "FAIL":
            failed.append(label)
    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}  (see {log_path.relative_to(ROOT)})")
        sys.exit(1)
    print("All checks passed.")


# ── test ───────────────────────────────────────────────────────────────────────

def cmd_test(args):
    """Run hardware self-tests against a connected Cynthion."""
    destructive = getattr(args, "destructive", False)
    log_path    = _log_path("test")
    check_venv()
    test_script = ROOT / "repos/cynthion/scripts/test-fault-detection.py"
    if not test_script.exists():
        print(f"ERROR: test script not found: {test_script}")
        sys.exit(1)
    cmd = [str(VENV), str(test_script)]
    if destructive:
        cmd.append("--destructive")
    print(f"==> test  (log: {log_path.relative_to(ROOT)})")
    with log_path.open("w") as log:
        run("fault-detection", cmd, cwd=ROOT, log=log)
    print("Tests complete.")


# ── clean ──────────────────────────────────────────────────────────────────────

def cmd_clean(args):
    target   = getattr(args, "target", "all")
    log_path = _log_path("clean")
    print(f"==> clean {target}  (log: {log_path.relative_to(ROOT)})")
    with log_path.open("w") as log:
        if target in ("rust", "all"):
            run("rust clean", ["cargo", "clean"], cwd=FW, log=log, check=False)
        if target in ("apollo", "all"):
            run("apollo clean", ["make", "clean", "APOLLO_BOARD=cynthion"],
                cwd=APL, log=log, check=False)
        if target in ("gateware", "all"):
            run("gateware clean", ["make", "clean"], cwd=GW, log=log, check=False)
        if target in ("app", "all"):
            run("flutter clean", ["flutter", "clean"], cwd=APP, log=log, check=False)
    print("Clean complete.")


# ── flash ──────────────────────────────────────────────────────────────────────

def _flash_rust(log):
    elf_candidates = list(FW.glob("target/**/firmware.elf"))
    if not elf_candidates:
        elf_candidates = list(FW.glob("target/**/*.elf"))
    if not elf_candidates:
        print("  ERROR: no ELF found — run 'awto.py build rust' first")
        sys.exit(1)
    elf = sorted(elf_candidates, key=lambda p: p.stat().st_mtime)[-1]
    run("probe-rs flash", ["probe-rs", "download", "--chip", "riscv", str(elf)], log=log)


def _flash_apollo(log):
    elf = APL / "_build/cynthion_d11/firmware.elf"
    if not elf.exists():
        print("  ERROR: Apollo ELF not found — run 'awto.py build apollo' first")
        sys.exit(1)
    run("apollo flash",
        ["arm-none-eabi-gdb", "-batch",
         "-ex", f"target extended-remote | openocd -f interface/cmsis-dap.cfg -c 'gdb_port pipe'",
         "-ex", f"load {elf}",
         "-ex", "detach"],
        log=log, check=False)
    print("  note: Apollo flash requires SWD connection — check device docs if this failed")


def _flash_gateware(log):
    check_venv()
    run("gateware upload",
        [str(VENV), "-m", "apollo_fpga.cli", "--", "configure",
         str(GW / "build/top.bit")],
        log=log, check=False)
    print("  note: ensure device is in Apollo mode (run 'awto.py reset' first if needed)")


def cmd_flash(args):
    target   = getattr(args, "target", "rust")
    log_path = _log_path("flash")
    print(f"==> flash {target}  (log: {log_path.relative_to(ROOT)})")
    with log_path.open("w") as log:
        if target == "rust":
            _flash_rust(log)
        elif target == "apollo":
            _flash_apollo(log)
        elif target == "gateware":
            _flash_gateware(log)
    print("Flash complete.")


# ── deploy ─────────────────────────────────────────────────────────────────────

def cmd_deploy(args):
    """Full release cycle: build rust + gateware, then flash both."""
    log_path = _log_path("deploy")
    print(f"==> deploy  (log: {log_path.relative_to(ROOT)})")
    with log_path.open("w") as log:
        _build_rust(log, release=True)
        _build_gateware(log, release=True)
        _flash_rust(log)
        _flash_gateware(log)
    print("Deploy complete.")


# ── reset ──────────────────────────────────────────────────────────────────────

def cmd_reset(args):
    """Reset Cynthion to Apollo mode (force_offline handoff)."""
    check_venv()
    log_path = _log_path("reset")
    print(f"==> reset  (log: {log_path.relative_to(ROOT)})")
    reset_script = ROOT / "repos/cynthion/scripts/reset-cynthion.sh"
    if reset_script.exists():
        with log_path.open("w") as log:
            run("reset-cynthion", [str(reset_script)], cwd=ROOT, log=log)
    else:
        # Inline fallback
        with log_path.open("w") as log:
            run("force-offline",
                [str(VENV), "-c", """
import sys
try:
    import usb.core
    from apollo_fpga import ApolloDebugger
    FPGA_VID, FPGA_PID = 0x1d50, 0x615b
    if usb.core.find(idVendor=FPGA_VID, idProduct=FPGA_PID) is None:
        print("Device already in Apollo mode (or not connected).")
        sys.exit(0)
    d = ApolloDebugger(force_offline=True)
    d.soft_reset()
    d.allow_fpga_takeover_usb()
    d.close()
    print("Reset complete.")
except Exception as e:
    print(f"Reset failed: {e}")
    print("If moondancer has hung: power-cycle the device.")
    sys.exit(1)
"""],
                log=log)
    print("Reset complete.")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        prog="awto",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # build
    pb = sub.add_parser("build", help="build firmware, gateware, app")
    pb.add_argument("target", nargs="?", default="all",
                    choices=["rust", "apollo", "gateware", "app", "all"])
    pb.add_argument("--release", action="store_true", help="release profile")

    # check
    pc = sub.add_parser("check", help="run pre-commit checks")
    pc.add_argument("target", nargs="?", default="fast",
                    choices=["fast", "rust", "c", "gateware", "all"])

    # test
    pt = sub.add_parser("test", help="run hardware self-tests")
    pt.add_argument("--destructive", action="store_true",
                    help="include fault-injection tests (device must be power-cycled between each)")

    # clean
    pcl = sub.add_parser("clean", help="clean build artefacts")
    pcl.add_argument("target", nargs="?", default="all",
                     choices=["rust", "apollo", "gateware", "app", "all"])

    # flash
    pf = sub.add_parser("flash", help="flash to connected device")
    pf.add_argument("target", nargs="?", default="rust",
                    choices=["rust", "apollo", "gateware"])

    # deploy
    sub.add_parser("deploy", help="build --release + flash (full cycle)")

    # reset
    sub.add_parser("reset", help="reset Cynthion to Apollo mode")

    args = ap.parse_args()
    {
        "build":  cmd_build,
        "check":  cmd_check,
        "test":   cmd_test,
        "clean":  cmd_clean,
        "flash":  cmd_flash,
        "deploy": cmd_deploy,
        "reset":  cmd_reset,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
