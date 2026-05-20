#!/usr/bin/env python3
"""
cynthion_control.py — unified Cynthion project CLI

Usage:
  ./cynthion_control.py <target> <verb> [flags]

Targets:  riscv  apollo  fpga  app  all
Verbs:    build  flash  test  check  clean  monitor  setup

Examples:
  ./cynthion_control.py riscv build
  ./cynthion_control.py riscv build --clean
  ./cynthion_control.py apollo flash
  ./cynthion_control.py fpga build
  ./cynthion_control.py all check
  ./cynthion_control.py monitor
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
VENV = ROOT / ".venv" / "bin"


def run(cmd, cwd=None):
    print(f"  $ {cmd}", flush=True)
    result = subprocess.run(cmd, shell=True, cwd=cwd or ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)


def venv_python():
    p = VENV / "python"
    return str(p) if p.exists() else "python3"


# ---------------------------------------------------------------------------
# target handlers
# ---------------------------------------------------------------------------

def target_riscv(verb, clean):
    fw = ROOT / "repos" / "cynthion" / "firmware"
    if clean:
        run("cargo clean", cwd=fw)
    if verb == "build":
        run("make bin", cwd=fw)
    elif verb == "flash":
        bin_path = ROOT / "repos" / "cynthion" / "cynthion" / "python" / "assets" / "moondancer.bin"
        run(f"{venv_python()} -m cynthion run {bin_path}")
    elif verb in ("test", "check"):
        run("cargo test", cwd=fw)
        run("make clippy", cwd=fw)
    elif verb == "clean":
        run("cargo clean", cwd=fw)
    else:
        die(f"riscv: unknown verb '{verb}'")


def target_apollo(verb, clean):
    fw = ROOT / "repos" / "apollo" / "firmware"
    if clean:
        run("make clean APOLLO_BOARD=cynthion", cwd=fw)
    if verb == "build":
        run("make APOLLO_BOARD=cynthion", cwd=fw)
        run("arm-none-eabi-size _build/cynthion_d11/firmware.elf", cwd=fw)
    elif verb == "flash":
        run(f"{venv_python()} -m apollo dfu-flash _build/cynthion_d11/firmware.bin", cwd=fw)
    elif verb in ("test", "check"):
        run("make APOLLO_BOARD=cynthion", cwd=fw)
        run("arm-none-eabi-size _build/cynthion_d11/firmware.elf", cwd=fw)
    elif verb == "clean":
        run("make clean APOLLO_BOARD=cynthion", cwd=fw)
    else:
        die(f"apollo: unknown verb '{verb}'")


def target_fpga(verb, clean):
    gw = ROOT / "repos" / "cynthion" / "cynthion" / "python"
    if clean:
        run("make clean", cwd=gw)
    if verb == "build":
        run("make facedancer", cwd=gw)
    elif verb == "flash":
        bit = ROOT / "repos" / "cynthion" / "cynthion" / "python" / "assets" / \
              "CynthionPlatformRev1D4" / "facedancer.bit"
        run(f"{venv_python()} -m cynthion flash --bitstream {bit}")
    elif verb in ("test", "check"):
        run(f"{venv_python()} -c 'from amaranth_boards.cynthion import *; print(\"gateware import ok\")'")
    elif verb == "clean":
        run("make clean", cwd=gw)
    else:
        die(f"fpga: unknown verb '{verb}'")


def target_app(verb, clean):
    app = ROOT / "app"
    if not app.exists():
        print("  app/ submodule not initialised — run: git submodule update --init app")
        return
    if clean:
        run("flutter clean", cwd=app)
    if verb == "build":
        run("flutter pub get", cwd=app)
        run("flutter build linux --release", cwd=app)
    elif verb in ("test", "check"):
        run("flutter pub get", cwd=app)
        run("flutter analyze && flutter test", cwd=app)
    elif verb == "clean":
        run("flutter clean", cwd=app)
    else:
        die(f"app: unknown verb '{verb}'")


def target_all(verb, clean):
    for fn in [target_riscv, target_apollo, target_fpga, target_app]:
        fn(verb, clean)


# ---------------------------------------------------------------------------
# top-level verbs (no target)
# ---------------------------------------------------------------------------

def cmd_check():
    run(str(SCRIPTS / "check-fast.sh"))


def cmd_build(clean):
    if clean:
        run(str(SCRIPTS / "build-all.sh") + " --clean")
    else:
        run(str(SCRIPTS / "build-all.sh"))


def cmd_monitor():
    apollod = SCRIPTS / "apollod.py"
    mux = SCRIPTS / "apollo-mux.py"
    if not apollod.exists():
        die(f"apollod.py not found at {apollod}")
    run(f"{venv_python()} {apollod} --daemon && {venv_python()} {mux}")


def cmd_setup(full=False):
    if full:
        run(str(SCRIPTS / "machine-setup.sh"))
    else:
        run(str(SCRIPTS / "setup-dev.sh"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


TARGETS = {"riscv": target_riscv, "apollo": target_apollo,
           "fpga": target_fpga, "app": target_app, "all": target_all}
VERBS = {"build", "flash", "test", "check", "clean"}


def main():
    p = argparse.ArgumentParser(
        description="Cynthion unified project CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("target_or_cmd", metavar="TARGET|CMD",
                   help="riscv | apollo | fpga | app | all | check | build | monitor | setup")
    p.add_argument("verb", metavar="VERB", nargs="?",
                   help="build | flash | test | check | clean")
    p.add_argument("--clean", action="store_true", help="clean before building")
    p.add_argument("--full", action="store_true",
                   help="(setup) full machine setup including OS packages")
    args = p.parse_args()

    cmd = args.target_or_cmd.lower()

    if cmd == "check":
        cmd_check()
    elif cmd == "build":
        cmd_build(args.clean)
    elif cmd == "monitor":
        cmd_monitor()
    elif cmd == "setup":
        cmd_setup(args.full)
    elif cmd in TARGETS:
        if not args.verb:
            die(f"'{cmd}' requires a verb: {', '.join(sorted(VERBS))}")
        if args.verb not in VERBS:
            die(f"unknown verb '{args.verb}'. Valid: {', '.join(sorted(VERBS))}")
        TARGETS[cmd](args.verb, args.clean)
    else:
        die(f"unknown target/command '{cmd}'. Try --help.")


if __name__ == "__main__":
    main()
