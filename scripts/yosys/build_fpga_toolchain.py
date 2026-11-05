#!/usr/bin/env python3
# Build yosys (+ our abc9 patches), prjtrellis and nextpnr-ecp5 from the pinned main commits.
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/yosys/build_fpga_toolchain.py

- Worktrees of the shared stores under /mnt/2tb/git_mirror/YosysHQ at the pins in
  `scripts/toolchain_env.py` (`tmp/yosys-main`, `tmp/trellis-main`, `tmp/nextpnr-main`).
- yosys gets `patches/yosys-abc9-unlisted-ports-*.patch` (#605).
- Everything installs to `~/opt/fpga-main/` (yosys, ecppack, nextpnr-ecp5, ecppll, ...).
  `<prefix>/environment` sources the 2026-11-04 suite (runtime libraries) and puts that bin first.
- To move to a newer main: bump the pins in `scripts/toolchain_env.py`, rerun this.
- Log: ./tmp/logs/build_fpga_toolchain.log.
"""

import logging
import os
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from toolchain_env import (BASE_SUITE, FPGA_PREFIX, NEXTPNR_PIN, TRELLIS_DB_PIN,  # noqa: E402
                           TRELLIS_PIN, YOSYS_PIN)

LOG = ROOT / "tmp" / "logs" / "build_fpga_toolchain.log"
STORES = Path("/mnt/2tb/git_mirror/YosysHQ")
PREFIX = Path(os.path.expandvars(FPGA_PREFIX))
PATCHES = [ROOT / "patches" / "yosys-abc9-unlisted-ports-fix.patch",
           ROOT / "patches" / "yosys-abc9-unlisted-ports-test.patch"]
# Stall limit, not an overall one: a compile or link that prints nothing for this long is hung.
# ninja prints a line per object; the slowest single step is a link, well under this.
STALL_S = 180


def run(command, cwd=None):
    """Run with output to the log; kill and fail if nothing is printed for STALL_S."""
    logging.info("$ %s", " ".join(map(str, command)))
    proc = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    last, tail = time.monotonic(), ""
    with LOG.open("a") as handle:
        while proc.poll() is None:
            if selector.select(timeout=1):
                line = proc.stdout.readline()
                if line:
                    tail, last = line.rstrip(), time.monotonic()
                    handle.write(line)
            elif time.monotonic() - last > STALL_S:
                proc.kill()
                logging.error("no output for %d s (last: %s): killed", STALL_S, tail)
                sys.exit(1)
        for line in proc.stdout:
            tail = line.rstrip()
            handle.write(line)
    if proc.returncode != 0:
        logging.error("%s failed rc=%d: %s", command[0], proc.returncode, tail)
        sys.exit(proc.returncode)


def worktree(store, path, pin):
    """A detached worktree of `store` at `pin`; an existing one is moved to the pin."""
    if not path.exists():
        run(["git", "-C", str(STORES / store), "worktree", "add", "--detach", str(path), pin])
    else:
        run(["git", "-C", str(path), "checkout", "--detach", pin])


def apply_patches(source):
    for patch in PATCHES:
        applied = subprocess.run(["git", "-C", str(source), "apply", "--check", "-R", str(patch)],
                                 capture_output=True).returncode == 0
        if applied:
            logging.info("already applied: %s", patch.name)
        else:
            run(["git", "-C", str(source), "apply", str(patch)])


def cmake(source, build, *options):
    cc = "/usr/lib64/ccache/clang" if Path("/usr/lib64/ccache/clang").exists() else shutil.which("clang")
    cxx = "/usr/lib64/ccache/clang++" if Path("/usr/lib64/ccache/clang++").exists() else shutil.which("clang++")
    run(["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
         f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}",
         f"-DCMAKE_INSTALL_PREFIX={PREFIX}", *options])
    run(["cmake", "--build", str(build), "-j", str(os.cpu_count())])
    run(["cmake", "--install", str(build)])


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    tmp = ROOT / "tmp"

    yosys = tmp / "yosys-main"
    worktree("yosys", yosys, YOSYS_PIN)
    apply_patches(yosys)
    cmake(yosys, tmp / "yosys-build-main", "-DYOSYS_WITHOUT_SLANG=ON")

    trellis = tmp / "trellis-main"
    worktree("prjtrellis", trellis, TRELLIS_PIN)
    database = trellis / "database"
    if not (database / "ECP5").exists():
        shutil.rmtree(database, ignore_errors=True)
        run(["git", "-C", str(STORES / "prjtrellis-db"), "worktree", "add", "--detach", str(database),
             TRELLIS_DB_PIN])
    # pytrellis is needed to generate nextpnr's chip database; the distro python, not the
    # free-threaded one the repo runs on.
    cmake(trellis / "libtrellis", tmp / "trellis-build-main", "-DPython3_EXECUTABLE=/usr/bin/python3")

    nextpnr = tmp / "nextpnr-main"
    worktree("nextpnr", nextpnr, NEXTPNR_PIN)
    cmake(nextpnr, tmp / "nextpnr-build-main", "-DARCH=ecp5", f"-DTRELLIS_INSTALL_PREFIX={PREFIX}",
          f"-DTRELLIS_LIBDIR={PREFIX / 'lib64' / 'trellis'}",
          "-DPython3_EXECUTABLE=/usr/bin/python3",
          "-DBUILD_GUI=OFF", "-DUSE_OPENMP=ON")

    (PREFIX / "environment").write_text(
        f'source "{BASE_SUITE}/environment"\nexport PATH="{PREFIX / "bin"}:$PATH"\n')
    logging.info("installed %s", PREFIX)
    return 0


if __name__ == "__main__":
    sys.exit(main())
