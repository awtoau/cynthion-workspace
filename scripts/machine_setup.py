#!/usr/bin/env python3
#
# Bring a fresh machine to the point where ./dev.py ci can run.
# SPDX-License-Identifier: BSD-3-Clause

"""
Install what this workspace needs: distro packages, Rust, the Python packages.

    ./dev.py setup                 # everything except the distro packages
    ./dev.py setup -- --system     # also install distro packages (needs sudo)
    ./dev.py setup -- --dry-run    # print what would run, change nothing

Safe to re-run: every stage checks before it acts, so a second run is a series of
"already present" lines.

## Replaces two shell scripts, and does not carry their mistakes forward

`machine-setup.sh` and `setup-dev.sh` did this before. Both were removed -- the
project is Python-only -- but the reason to rewrite rather than transliterate is
that they had drifted into being wrong:

  * `machine-setup.sh` ended by telling you to `source .venv/bin/activate`. There
    is no venv, deliberately: the workspace runs on the system free-threaded
    interpreter so that plain `python3` and the installed console scripts agree
    about which packages exist. `setup-dev.sh` said so correctly in its own
    header while the script that called it said the opposite.
  * It also pointed at `scripts/check-fast.sh`, which does not exist. The check
    runner is `./dev.py ci`.
  * It wrote a hand-maintained udev rules file. Those rules are GENERATED from
    `ecp5-test/usb_ids.py` by `scripts/install_udev.py`, so that adding a test
    bitstream cannot leave the rules behind. A second hand-written copy in
    `/etc/udev/rules.d` is exactly the drift that generator exists to prevent,
    so this delegates instead.

## What is deliberately NOT here

The FPGA toolchain. Yosys and nextpnr come from the OSS CAD Suite, which is a
downloaded tarball pinned to a version in `.claude-toolchain.md`, not a distro
package -- installing a distro yosys would shadow it and silently synthesise with
a different version. `./dev.py doctor` reports whether it is on PATH.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "machine_setup.log"

RUST_TARGET = "riscv32imac-unknown-none-elf"

# Distro packages, by package manager. The lists differ in more than naming --
# Fedora splits the ARM toolchain into gcc/binutils/newlib and Debian does not --
# so they are written out rather than mapped.
PACKAGES = {
    "dnf": [
        "arm-none-eabi-gcc-cs", "arm-none-eabi-binutils-cs", "arm-none-eabi-newlib",
        "llvm", "clang", "cmake", "ninja-build",
        "gtk3-devel", "libudev-devel", "libusb1-devel", "udev",
        "git", "curl",
    ],
    "apt-get": [
        "gcc-arm-none-eabi", "binutils-arm-none-eabi",
        "llvm", "clang", "cmake", "ninja-build",
        "libgtk-3-dev", "libudev-dev", "libusb-1.0-0-dev",
        "git", "curl",
    ],
}

# Editable installs from the submodules, so an edit under repos/ is live without
# a reinstall. These are the two that firmware tooling imports by name.
EDITABLE = [
    "repos/cynthion/cynthion/python",
    "repos/facedancer",
]

# Everything else, from the index.
PACKAGES_PIP = ["pytest", "pyserial", "prompt_toolkit"]


class Runner:
    """Runs a command, or prints it under --dry-run. Logs either way."""

    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.failures = []
        LOG.parent.mkdir(parents=True, exist_ok=True)
        self.log = LOG.open("w", encoding="utf-8")

    def say(self, text):
        print(text, flush=True)
        self.log.write(text + "\n")

    def run(self, argv, what):
        if self.dry_run:
            self.say(f"  would run: {' '.join(argv)}")
            return True
        self.say(f"  {' '.join(argv)}")
        result = subprocess.run(argv, cwd=ROOT)
        if result.returncode != 0:
            self.say(f"  FAILED ({what}), rc={result.returncode}")
            self.failures.append(what)
            return False
        return True


def system_packages(runner):
    """Distro packages. Needs sudo, so it is opt-in rather than the default."""
    runner.say("==> system packages")
    for manager, packages in PACKAGES.items():
        if shutil.which(manager):
            runner.say(f"  package manager: {manager}")
            if manager == "apt-get":
                runner.run(["sudo", "apt-get", "update", "-q"], "apt update")
            runner.run(["sudo", manager, "install", "-y", *packages],
                       f"{manager} install")
            return
    runner.say("  no dnf or apt-get found -- install the equivalents by hand:")
    runner.say(f"  {', '.join(PACKAGES['dnf'])}")
    runner.failures.append("no supported package manager")


def rust(runner):
    """The toolchain and the bare-metal target the firmware links for."""
    runner.say("==> rust")
    if not shutil.which("cargo"):
        runner.say("  cargo NOT FOUND. Install rustup from https://rustup.rs and")
        runner.say("  re-run; this does not pipe a downloaded script into a shell.")
        runner.failures.append("cargo missing")
        return

    version = subprocess.run(["rustc", "--version"], capture_output=True, text=True)
    runner.say(f"  {version.stdout.strip()}")

    if not shutil.which("rustup"):
        runner.say("  rustup not present; cannot check the target or clippy")
        return

    installed = subprocess.run(["rustup", "target", "list", "--installed"],
                               capture_output=True, text=True).stdout
    if RUST_TARGET in installed:
        runner.say(f"  target {RUST_TARGET} already present")
    else:
        runner.run(["rustup", "target", "add", RUST_TARGET], "rustup target add")

    components = subprocess.run(["rustup", "component", "list", "--installed"],
                                capture_output=True, text=True).stdout
    if "clippy" in components:
        runner.say("  clippy already present")
    else:
        runner.run(["rustup", "component", "add", "clippy"], "rustup component add")


def python_packages(runner):
    """Into the DEFAULT environment, never a venv.

    The workspace runs on the system free-threaded interpreter so that plain
    `python3`, the console scripts installed beside it, and everything `dev.py`
    spawns all agree about which packages exist. A venv here would mean tooling
    run outside it silently sees a different set.
    """
    runner.say("==> python packages")

    gil = getattr(sys, "_is_gil_enabled", None)
    if gil is None:
        runner.say("  WARNING: this interpreter predates free-threading (<3.13)")
    elif gil():
        runner.say(f"  WARNING: {sys.executable} has the GIL ENABLED.")
        runner.say("  The workspace expects free-threaded 3.15t; parallel checks")
        runner.say("  will still run, just without the concurrency they assume.")
    else:
        runner.say(f"  python {sys.version.split()[0]}, free-threaded")

    for relative in EDITABLE:
        path = ROOT / relative
        if not (path / "pyproject.toml").exists() and not (path / "setup.py").exists():
            runner.say(f"  {relative}: no package there -- run submodules first")
            runner.failures.append(f"{relative} missing")
            continue
        runner.run([sys.executable, "-m", "pip", "install", "-e", str(path)],
                   f"pip install -e {relative}")

    runner.run([sys.executable, "-m", "pip", "install", *PACKAGES_PIP], "pip install")


def submodules(runner):
    runner.say("==> submodules")
    runner.run(["git", "submodule", "update", "--init", "--recursive"], "submodules")


def udev(runner):
    """Delegate to the generator; do not write a second copy of the rules."""
    runner.say("==> udev rules")
    runner.run([sys.executable, str(ROOT / "scripts" / "install_udev.py")], "udev")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--system", action="store_true",
                        help="also install distro packages (invokes sudo)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would run and change nothing")
    args = parser.parse_args()

    runner = Runner(args.dry_run)
    runner.say("==> cynthion-workspace machine setup")

    if args.system:
        system_packages(runner)
    else:
        runner.say("==> system packages SKIPPED (--system installs them, via sudo)")

    submodules(runner)
    rust(runner)
    python_packages(runner)
    udev(runner)

    runner.say("")
    if runner.failures:
        runner.say(f"INCOMPLETE: {len(runner.failures)} stage(s) failed: "
                   f"{', '.join(runner.failures)}")
        runner.say(f"log: {LOG.relative_to(ROOT)}")
        return 1

    runner.say("Setup complete. There is no venv to activate -- plain `python3`")
    runner.say("has the packages, which is the point.")
    runner.say("")
    runner.say("  ./dev.py doctor     confirm every tool is on PATH")
    runner.say("  ./dev.py ci         run every check")
    runner.say(f"log: {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
