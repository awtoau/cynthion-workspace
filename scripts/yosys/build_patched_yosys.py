#!/usr/bin/env python3
# Build the pinned yosys + our abc9 patches into ~/opt/yosys-patched/<sha>/ (the yosys070 default).
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/yosys/build_patched_yosys.py

- Worktree of the shared store at PIN (`tmp/yosys-main`), `patches/yosys-abc9-unlisted-ports-*.patch`
  applied, built with cmake, installed under `~/opt/yosys-patched/<PIN>/`.
- Writes `<prefix>/environment`: the stock 0.70 suite's environment, then this yosys first on PATH.
  `soc_run.py` sources it as CYNTHION_OSS_CAD_SUITE.
- Log: ./tmp/logs/build_patched_yosys.log. Decision and results: docs/yosys-070-upgrade.md (#605).
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from toolchain_env import BASE_SUITE, YOSYS_PIN, YOSYS_PREFIX  # noqa: E402
LOG = ROOT / "tmp" / "logs" / "build_patched_yosys.log"
STORE = Path("/mnt/2tb/git_mirror/YosysHQ/yosys")
SOURCE = ROOT / "tmp" / "yosys-main"
BUILD = ROOT / "tmp" / "yosys-build-main"
PIN = YOSYS_PIN
PREFIX = Path(os.path.expandvars(YOSYS_PREFIX))
PATCHES = [ROOT / "patches" / "yosys-abc9-unlisted-ports-fix.patch",
           ROOT / "patches" / "yosys-abc9-unlisted-ports-test.patch"]
# Stall limit, not an overall one: a link or compile that prints nothing for this long is hung.
# Measured: ninja prints a line per object, the slowest single step is a link at under 60 s.
STALL_S = 120


def run(command, cwd=None):
    """Run with output to the log; fail if nothing is printed for STALL_S."""
    import selectors
    import time
    logging.info("$ %s", " ".join(map(str, command)))
    proc = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    last = time.monotonic()
    tail = ""
    while proc.poll() is None:
        if selector.select(timeout=1):
            line = proc.stdout.readline()
            if line:
                tail = line.rstrip()
                last = time.monotonic()
                with LOG.open("a") as handle:
                    handle.write(line)
        elif time.monotonic() - last > STALL_S:
            proc.kill()
            logging.error("no output for %d s (last: %s): killed", STALL_S, tail)
            sys.exit(1)
    for line in proc.stdout:
        tail = line.rstrip()
        with LOG.open("a") as handle:
            handle.write(line)
    if proc.returncode != 0:
        logging.error("%s failed rc=%d: %s", command[0], proc.returncode, tail)
        sys.exit(proc.returncode)


def ensure_source():
    if not SOURCE.exists():
        run(["git", "-C", str(STORE), "worktree", "add", "--detach", str(SOURCE), PIN])
    for patch in PATCHES:
        applied = subprocess.run(["git", "-C", str(SOURCE), "apply", "--check", "-R", str(patch)],
                                 capture_output=True).returncode == 0
        if applied:
            logging.info("already applied: %s", patch.name)
        else:
            run(["git", "-C", str(SOURCE), "apply", str(patch)])


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    ensure_source()
    cc = "/usr/lib64/ccache/clang" if Path("/usr/lib64/ccache/clang").exists() else shutil.which("clang")
    cxx = "/usr/lib64/ccache/clang++" if Path("/usr/lib64/ccache/clang++").exists() else shutil.which("clang++")
    run(["cmake", "-S", str(SOURCE), "-B", str(BUILD), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
         "-DYOSYS_WITHOUT_SLANG=ON", f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}"])
    run(["cmake", "--build", str(BUILD), "-j", str(os.cpu_count())])
    run(["cmake", "--install", str(BUILD), "--prefix", str(PREFIX)])
    (PREFIX / "environment").write_text(
        f'source "{BASE_SUITE}/environment"\nexport PATH="{PREFIX / "bin"}:$PATH"\n')
    logging.info("installed %s", PREFIX)
    return 0


if __name__ == "__main__":
    sys.exit(main())
