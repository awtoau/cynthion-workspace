#!/usr/bin/env python3
#
# Build cynthion_udc and test it in a throwaway virtme-ng VM (increment 3, stub backend).
# SPDX-License-Identifier: GPL-3.0-or-later

"""cynthion_udc VM test: build the module, boot `vng`, run cudc_vm_guest.py inside as root.

    ./scripts/linux/cudc_vm_test.py            # build + VM test
    ./scripts/linux/cudc_vm_test.py --no-build # reuse tmp/cudc-build/cynthion_udc.ko

- NEVER loads the module on the host: the host is a shared dev box. Only the VM does.
- Kernel: the running host kernel (`vng --run`), headers /usr/src/kernels/$(uname -r).
- Build output: tmp/cudc-build/ (Kbuild MO=), source tree stays clean.
- Log: tmp/logs/cudc_vm_test.log (build + full guest output incl. dmesg).
"""

import argparse
import os
import selectors
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def repo_root():
    env = os.environ.get("CYNTHION_WORKSPACE_REPO")
    if env:
        return Path(env)
    try:
        return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                   text=True, check=True, cwd=Path(__file__).parent).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent.parent


ROOT = repo_root()
SRC = ROOT / "linux" / "cynthion_udc"
BUILD = ROOT / "tmp" / "cudc-build"
KO = BUILD / "cynthion_udc.ko"
GUEST = Path(__file__).resolve().parent / "cudc_vm_guest.py"
LOG = ROOT / "tmp" / "logs" / "cudc_vm_test.log"
SENTINEL = "CUDC_VM_TEST RESULT: "

STALL_S = 60        # dev-box rule: no output line for 60 s = hung (awto-dan#53)
# Backstop per step: 3x measured cold worst case (awto-dan#53), rounded up to whole seconds;
# measurements in docs/usb-device-mode.md#increment-3-status. Expiry: kill, log last line.
BUILD_LIMIT_S = 2
VM_LIMIT_S = 6

_log = None


def log(msg):
    line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    _log.write(line + "\n")
    _log.flush()


def stream(cmd, limit_s, what):
    """Runs cmd, logs every output line; kills on STALL_S silence or limit_s total."""
    log(f"$ {' '.join(map(str, cmd))}")
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, cwd=ROOT)
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ)
    lines, last, buf = [], time.monotonic(), b""
    reason = None
    while True:
        now = time.monotonic()
        wait = min(STALL_S - (now - last), limit_s - (now - t0))
        if wait <= 0:
            reason = (f"stalled: no output for {STALL_S} s" if now - last >= STALL_S
                      else f"over the {limit_s} s limit")
            break
        if not sel.select(wait):
            continue
        chunk = os.read(p.stdout.fileno(), 65536)
        if not chunk:
            break
        last = time.monotonic()
        buf += chunk
        *done, buf = buf.split(b"\n")
        for raw in done:
            text = raw.decode(errors="replace").rstrip("\r")
            lines.append(text)
            log(f"  | {text}")
    if reason:
        p.kill()
        p.wait()
        load = os.getloadavg()
        log(f"{what}: killed, {reason}; elapsed {time.monotonic() - t0:.1f} s; "
            f"last line: {lines[-1] if lines else '(none)'}; loadavg {load[0]:.1f}")
        return None, lines
    rc = p.wait()
    log(f"{what}: exit {rc} after {time.monotonic() - t0:.1f} s")
    return rc, lines


def build():
    BUILD.mkdir(parents=True, exist_ok=True)
    kdir = Path("/usr/src/kernels") / os.uname().release
    if not kdir.is_dir():
        log(f"no kernel headers at {kdir}")
        return False
    rc, _ = stream(["make", "-C", SRC, f"KDIR={kdir}", f"MO={BUILD}", "W=1"],
                   BUILD_LIMIT_S, "build")
    if rc != 0 or not KO.is_file():
        return False
    vermagic = subprocess.run(["modinfo", "-F", "vermagic", KO], capture_output=True,
                              text=True).stdout.split()
    ok = bool(vermagic) and vermagic[0] == os.uname().release
    log(f"vermagic {vermagic[:1]} vs running {os.uname().release}: {'ok' if ok else 'MISMATCH'}")
    return ok


def vm_test():
    cmd = ["vng", "--run", "--user", "root", "--cpus", "2", "--rodir", str(ROOT),
           "--cwd", str(ROOT), "--exec", f"python3 -I {GUEST} --ko {KO}"]
    rc, lines = stream(cmd, VM_LIMIT_S, "vm")
    results = [ln.split(SENTINEL, 1)[1].strip() for ln in lines if SENTINEL in ln]
    passed = rc == 0 and results == ["PASS"]
    log(f"vm test: rc {rc}, guest result {results or 'missing'}")
    return passed


def main():
    global _log
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-build", action="store_true", help="reuse the built module")
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _log = LOG.open("a")
    log(f"=== cudc_vm_test start, root {ROOT} ===")
    if not args.no_build and not build():
        log("RESULT: FAIL (build)")
        return 1
    ok = vm_test()
    log(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
