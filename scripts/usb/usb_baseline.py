#!/usr/bin/env python3
#
# Build, load and measure LUNA's USB device stack on Cynthion r1.4 (#612 S0).
# SPDX-License-Identifier: BSD-3-Clause

"""LUNA USB2 device throughput: bulk IN, bulk OUT, bulk loopback (FIFO), CDC-ACM loopback.

    ./scripts/usb/usb_baseline.py build                 # all probes, parallel, cached
    ./scripts/usb/usb_baseline.py run                   # configure + measure each, 3 runs
    ./scripts/usb/usb_baseline.py run --probe oneway --runs 1 --no-configure
    ./scripts/usb/usb_baseline.py topology              # lsusb -t + where the probe sits

- Gateware: `gateware/probes/usb_bulk/` (`usb_oneway.py`, `usb_bulk.py`), `gateware/probes/usb_serial/`.
- Load: `soc_confirm.configure_and_confirm` (volatile; `scripts/soc_run.py --no-build` restores the SoC).
- Host: pyusb (bulk), pyserial (CDC). Data verified after the timed region, never inside it.
- Loopback figures: `payload` = bytes returned per second; `bus` = 2x payload (each byte crosses twice).
- Every run has a negative control: one corrupted byte must be reported by the same checker.
- Log: tmp/logs/usb_baseline.log; results: tmp/usb-baseline/results.jsonl.
"""

import argparse
import json
import os
import re
import selectors
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
GW = ROOT / "gateware"
OUT = ROOT / "tmp" / "usb-baseline"
LOG = ROOT / "tmp" / "logs" / "usb_baseline.log"
RESULTS = OUT / "results.jsonl"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(GW))
sys.path.insert(0, str(GW / "probes"))

import _paths  # noqa: E402,F401  (scripts/ subdirectories on sys.path)
import usb_ids  # noqa: E402
from toolchain_env import SOURCE  # noqa: E402

STALL_S = 60                 # dev-box rule: no build output for 60 s = hung (awto-dan#53)
CHUNK = 64 * 1024            # host transfer size, as the old harness (docs/usb-performance.md)
RUN_S = 3.0                  # timed window per one-way run
LOOP_BYTES = 32 << 20        # bytes per loopback run

# Per-transfer limit: 1.25x one CHUNK at a floor rate below every recorded figure
# (lowest recorded: CDC loopback, ~98 Mbps payload), plus 10 ms for host scheduling.
# Expiry: the run is marked failed with the operation, limit and elapsed time.
FLOOR_BPS = 50e6
XFER_MS = int(1.25 * CHUNK * 8 / FLOOR_BPS * 1000) + 10

# Waits for: the probe to enumerate after configure. Measured on the SoC console:
# 0.5-2 s (soc_confirm.CONSOLE_ENUMERATE_S = 2.5 s). Bounded by udevadm-settle ticks.
ENUMERATE_S = 2.5

PROBES = {
    # name: (module, class, kwargs, usb_ids name, what it measures)
    "oneway": ("usb_bulk.usb_oneway", "USBOneWay", {}, "usb_oneway", "bulk IN, bulk OUT"),
    "bulk_fifo": ("usb_bulk.usb_bulk", "USBBulkLoopback", {"buffered": True}, "usb_bulk",
                  "bulk loopback, FIFO"),
    "serial": ("usb_serial.usb_serial", "USBSerialTerminal", {}, "usb_serial",
               "CDC-ACM loopback, combinational"),
}

# `--pidcodes`: the bulk probes as 1209:000a, which 54-cynthion.rules grants uaccess, for a host
# whose 55-cynthion-test.rules is the pre-ede08a0 file udev ignores. CDC needs none (tty, dialout).
PIDCODES = (0x1209, 0x000A)
PIDCODES_PROBES = ("oneway", "bulk_fifo")
USE_PIDCODES = False


def device_id(name):
    if USE_PIDCODES and name in PIDCODES_PROBES:
        return PIDCODES
    return usb_ids.VENDOR_ID, usb_ids.product_id(PROBES[name][3])


def bdir(name):
    return OUT / (name + ("-pidcodes" if USE_PIDCODES and name in PIDCODES_PROBES else ""))


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def say(text):
    line = f"{now()} {text}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


GW_PATHS = ["gateware/probes/usb_bulk", "gateware/probes/usb_serial",
            "gateware/probes/usb_domains.py", "gateware/probes/jtag_registers.py",
            "gateware/board", "gateware/usb_ids.py"]


def gateware_rev():
    sha = git("log", "-1", "--format=%h", "--", *GW_PATHS)
    return sha + ("-dirty" if git("status", "--porcelain", "--", *GW_PATHS) else "")


def record(row):
    OUT.mkdir(parents=True, exist_ok=True)
    row = dict(run_id=RUN_ID, gateware=gateware_rev(), commit=git("rev-parse", "--short=10", "HEAD"),
               time=now(), **row)
    with RESULTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


RUN_ID = time.strftime("%Y%m%d-%H%M%S")

# ---- build -------------------------------------------------------------------

BUILD_PY = """
import sys, importlib
sys.path.insert(0, {gw!r})
sys.path.insert(0, {probes!r})
from board import CynthionPlatformRev1D4
cls = getattr(importlib.import_module({module!r}), {cls!r})
CynthionPlatformRev1D4().build(cls(**{kwargs!r}), build_dir={out!r}, do_program=False)
print("BUILD OK")
"""


def comb_count(build_dir):
    """TRELLIS_COMB used, from nextpnr's utilisation report (top.tim)."""
    tim = build_dir / "top.tim"
    found = re.findall(r"TRELLIS_COMB:\s+(\d+)/\s*(\d+)", tim.read_text()) if tim.exists() else []
    return (int(found[-1][0]), int(found[-1][1])) if found else (None, None)


def build_one(name):
    module, cls, kwargs, _, _ = PROBES[name]
    if USE_PIDCODES and name in PIDCODES_PROBES:
        kwargs = dict(kwargs, vid_pid=PIDCODES)
    out = bdir(name)
    out.mkdir(parents=True, exist_ok=True)
    blog = out / "build.log"
    env = dict(os.environ)
    # The board judges, not the placer: a missed constraint must still yield a bitstream.
    env["AMARANTH_nextpnr_opts"] = "--timing-allow-fail"
    code = BUILD_PY.format(gw=str(GW), probes=str(GW / "probes"), module=module, cls=cls,
                           kwargs=kwargs, out=str(out))
    cmd = ["bash", "-c", f'{SOURCE} && exec "{sys.executable}" -c "$0"', code]
    start = time.monotonic()
    with blog.open("w") as f:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        last = ""
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            if not sel.select(timeout=STALL_S):
                proc.kill()
                say(f"{name}: build STALLED {STALL_S} s silent; last line: {last[:160]}; "
                    f"loadavg {os.getloadavg()}")
                return False
            line = proc.stdout.readline()
            if not line:
                break
            f.write(line)
            last = line.strip()
        proc.wait()
    ok = proc.returncode == 0 and "BUILD OK" in blog.read_text()
    used, total = comb_count(out)
    say(f"{name}: build {'ok' if ok else 'FAILED'} in {time.monotonic() - start:.0f} s; "
        f"TRELLIS_COMB {used}/{total}; {blog}")
    if ok:
        (out / "gateware_rev").write_text(gateware_rev() + "\n")
        record(dict(kind="build", probe=name, comb=used, comb_total=total))
    return ok


def cached(name):
    stamp = bdir(name) / "gateware_rev"
    return ((bdir(name) / "top.bit").exists() and stamp.exists()
            and stamp.read_text().strip() == gateware_rev())


def build(names):
    stale = [n for n in names if not cached(n)]
    say(f"gateware {gateware_rev()}; building {stale or 'nothing (cached)'}")
    with ThreadPoolExecutor(max_workers=len(stale) or 1) as ex:
        return all(ex.map(build_one, stale))


# ---- board -------------------------------------------------------------------

def configure(name):
    from soc_confirm import configure_and_confirm
    bit = bdir(name) / "top.bit"
    rc = configure_and_confirm(bit, expect="design")
    say(f"configure {name}: rc {rc}")
    return rc == 0


def sysfs_device(vid, pid):
    """(devpath, speed Mbps, product) of <vid>:<pid> from sysfs, or None."""
    for dev in Path("/sys/bus/usb/devices").iterdir():
        try:
            if (int((dev / "idVendor").read_text(), 16) == vid
                    and int((dev / "idProduct").read_text(), 16) == pid):
                product = (dev / "product").read_text().strip() if (dev / "product").exists() else ""
                return dev.name, float((dev / "speed").read_text()), product
        except (OSError, ValueError):
            continue
    return None


def wait_enumerated(vid, pid):
    start = time.monotonic()
    while time.monotonic() - start < ENUMERATE_S:
        found = sysfs_device(vid, pid)
        if found:
            subprocess.run(["udevadm", "settle"], capture_output=True)   # ACLs applied
            return found
        subprocess.run(["udevadm", "settle"], capture_output=True)
    say(f"{vid:04x}:{pid:04x} did not enumerate within {ENUMERATE_S} s "
        f"(elapsed {time.monotonic() - start:.2f} s)")
    return None


def hubs_between(devpath):
    """Hub levels between root port and device: '1-1.3.1.3.4' -> 4."""
    return devpath.split("-", 1)[1].count(".")


def registers():
    from apollo_fpga import ApolloDebugger
    dbg = ApolloDebugger()
    return dbg, dbg.registers


def read_regs(addrs):
    dbg, regs = registers()
    try:
        return {a: regs.register_read(a) for a in addrs}
    finally:
        dbg.close()


_OPEN = {}


def pyusb_device(ident):
    """Configured once per enumeration: a repeat SET_CONFIGURATION resets data toggles."""
    import usb.core
    if ident not in _OPEN:
        dev = usb.core.find(idVendor=ident[0], idProduct=ident[1])
        if dev is None:
            raise RuntimeError(f"no {ident[0]:04x}:{ident[1]:04x} on the bus")
        dev.set_configuration()
        _OPEN[ident] = dev
    return _OPEN[ident]


class Expired(Exception):
    pass


def bounded(op, fn, *args):
    """One libusb transfer with XFER_MS; on expiry name the op, limit and elapsed."""
    import usb.core
    t0 = time.perf_counter()
    try:
        return fn(*args, timeout=XFER_MS)
    except usb.core.USBTimeoutError:
        raise Expired(f"{op}: no completion in {XFER_MS} ms "
                      f"(elapsed {1000 * (time.perf_counter() - t0):.1f} ms)") from None


def counting(n, start=0):
    pattern = bytes((start + i) & 0xFF for i in range(256))
    return (pattern * (n // 256 + 2))[:n]


def mismatches(got, want):
    """(count, first index) of differing bytes; whole-block compares, bytewise only where they differ."""
    count = abs(len(got) - len(want))
    first = min(len(got), len(want)) if count else None
    for off in range(0, min(len(got), len(want)), CHUNK):
        a, b = got[off:off + CHUNK], want[off:off + CHUNK]
        if a == b:
            continue
        bad = [off + i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        count += len(bad)
        first = bad[0] if first is None or bad[0] < first else first
    return count, first


def corrupt(data, index):
    data = bytearray(data)
    data[index] ^= 0x5A
    return bytes(data)


def mbps(nbytes, seconds):
    return nbytes * 8 / seconds / 1e6


# ---- tests -------------------------------------------------------------------

def bulk_in(ident, run):
    from usb_bulk.usb_oneway import BULK_IN_ENDPOINT
    dev = pyusb_device(ident)
    chunks = []
    start = time.perf_counter()
    while time.perf_counter() - start < RUN_S:
        chunks.append(bounded("bulk IN read", dev.read, 0x80 | BULK_IN_ENDPOINT, CHUNK))
    elapsed = time.perf_counter() - start
    got = b"".join(bytes(c) for c in chunks)
    want = counting(len(got), got[0])
    bad, first = mismatches(got, want)
    ctl, ctl_first = mismatches(got, corrupt(want, len(want) // 2))
    rate = mbps(len(got), elapsed)
    say(f"  bulk IN run {run}: {len(got)} B in {elapsed:.3f} s = {rate:.1f} Mbps; "
        f"sequence errors {bad}; negative control (expected corrupted at {len(want) // 2}): "
        f"{ctl} error(s) at {ctl_first}")
    return record(dict(kind="measure", test="bulk_in", run=run, bytes=len(got), seconds=elapsed,
                       mbps=rate, errors=bad, control_errors=ctl,
                       ok=bad == 0 and ctl == 1))


def bulk_out(ident, run):
    from usb_bulk.usb_oneway import BULK_OUT_ENDPOINT, REGISTER_ERRORS, REGISTER_OUT_COUNT
    dev = pyusb_device(ident)
    before = read_regs([REGISTER_OUT_COUNT, REGISTER_ERRORS])
    payload = counting(CHUNK)
    sent = 0
    start = time.perf_counter()
    while time.perf_counter() - start < RUN_S:
        sent += bounded("bulk OUT write", dev.write, BULK_OUT_ENDPOINT, payload)
    elapsed = time.perf_counter() - start
    after = read_regs([REGISTER_OUT_COUNT, REGISTER_ERRORS])
    counted = (after[REGISTER_OUT_COUNT] - before[REGISTER_OUT_COUNT]) & 0xFFFFFFFF
    errors = (after[REGISTER_ERRORS] - before[REGISTER_ERRORS]) & 0xFFFF
    # Negative control: the device checker must flag one deliberately corrupted chunk.
    bounded("bulk OUT control write", dev.write, BULK_OUT_ENDPOINT, corrupt(payload, CHUNK // 2))
    ctl = (read_regs([REGISTER_ERRORS])[REGISTER_ERRORS] - after[REGISTER_ERRORS]) & 0xFFFF
    rate = mbps(sent, elapsed)
    say(f"  bulk OUT run {run}: {sent} B in {elapsed:.3f} s = {rate:.1f} Mbps; device counted "
        f"{counted} B, {errors} sequence errors; negative control (1 byte corrupted): "
        f"{ctl} device error(s)")
    return record(dict(kind="measure", test="bulk_out", run=run, bytes=sent, seconds=elapsed,
                       mbps=rate, device_bytes=counted, errors=errors, control_errors=ctl,
                       ok=counted == sent and errors == 0 and ctl > 0))


def loop(write, read, total, label):
    """Concurrent writer and reader; returns (received, elapsed, error or None)."""
    payload = counting(total)
    received = []
    failure = []

    def reader():
        have = 0
        try:
            while have < total:
                data = read(min(CHUNK, total - have))
                if not len(data):
                    raise Expired(f"{label} read returned 0 bytes at {have}")
                received.append(bytes(data))
                have += len(data)
        except Exception as exc:          # reported, not swallowed
            failure.append(exc)

    thread = threading.Thread(target=reader, daemon=True)
    start = time.perf_counter()
    thread.start()
    try:
        for off in range(0, total, CHUNK):
            if failure:
                break
            write(payload[off:off + CHUNK])
    except Exception as exc:
        failure.append(exc)
    # Waits for: the reader draining what is in flight, at most a few CHUNKs. Bounded by
    # 4 transfer limits. Expiry: reported as a stalled loopback.
    thread.join(timeout=4 * XFER_MS / 1000)
    elapsed = time.perf_counter() - start
    if thread.is_alive():
        failure.append(Expired(f"{label}: reader still waiting {4 * XFER_MS} ms after the last write"))
    return b"".join(received), payload, elapsed, (failure[0] if failure else None)


def report_loop(test, run, got, want, elapsed, error, extra=None):
    bad, first = mismatches(got, want)
    ctl, ctl_first = mismatches(got, corrupt(want, len(want) // 2))
    payload = mbps(len(got), elapsed)
    say(f"  {test} run {run}: {len(got)}/{len(want)} B back in {elapsed:.3f} s = payload "
        f"{payload:.1f} Mbps, bus {2 * payload:.1f} Mbps; data errors {bad}"
        + (f" (first at {first})" if bad else "")
        + f"; negative control (expected corrupted at {len(want) // 2}): {ctl} error(s) at {ctl_first}"
        + (f"; FAILED: {error}" if error else ""))
    return record(dict(kind="measure", test=test, run=run, bytes=len(got), seconds=elapsed,
                       mbps_payload=payload, mbps=2 * payload, errors=bad, control_errors=ctl,
                       error=str(error) if error else None, **(extra or {}),
                       ok=error is None and bad == 0 and ctl == 1))


def bulk_loop(ident, run):
    dev = pyusb_device(ident)
    got, want, elapsed, error = loop(
        lambda b: bounded("loopback OUT write", dev.write, 0x01, b),
        lambda n: bounded("loopback IN read", dev.read, 0x81, n),
        LOOP_BYTES, "bulk loopback")
    return report_loop("bulk_loop_fifo", run, got, want, elapsed, error)


def cdc_loop(ident, run):
    import serial
    node = usb_ids.find_tty("usb_serial")
    if node is None:
        raise RuntimeError("no tty for usb_serial")
    with serial.Serial(node, timeout=XFER_MS / 1000, write_timeout=XFER_MS / 1000) as tty:
        tty.reset_input_buffer()

        def read(n):
            return tty.read(n)

        def write(b):
            if tty.write(b) != len(b):
                raise Expired(f"cdc write: short write within {XFER_MS} ms")

        got, want, elapsed, error = loop(write, read, LOOP_BYTES, "cdc loopback")
    return report_loop("cdc_loop", run, got, want, elapsed, error, dict(tty=node))


TESTS = {"oneway": (bulk_in, bulk_out), "bulk_fifo": (bulk_loop,), "serial": (cdc_loop,)}


def topology():
    say("lsusb -t:\n" + subprocess.run(["lsusb", "-t"], capture_output=True, text=True).stdout)


def run(args):
    if not build(args.probe):
        return 1
    rc = 0
    host = dict(kernel=os.uname().release, host=os.uname().nodename)
    for name in args.probe:
        ident = device_id(name)
        _OPEN.pop(ident, None)
        if not args.no_configure and not configure(name):
            rc = 1
            continue
        found = wait_enumerated(*ident)
        if not found:
            rc = 1
            continue
        devpath, speed, product = found
        say(f"{name}: {ident[0]:04x}:{ident[1]:04x} '{product}' at {devpath}, {speed:g} Mbps, "
            f"{hubs_between(devpath)} hub level(s) from the root port")
        record(dict(kind="device", probe=name, devpath=devpath, speed=speed, product=product,
                    hubs=hubs_between(devpath), **host))
        if speed != 480:
            say(f"{name}: not high speed ({speed:g} Mbps); refusing to measure")
            rc = 1
            continue
        for test in TESTS[name]:
            for i in range(1, args.runs + 1):
                try:
                    row = test(ident, i)
                except Exception as exc:
                    say(f"  {test.__name__} run {i}: FAILED {type(exc).__name__}: {exc}")
                    rc = 1
                    continue
                rc |= 0 if row["ok"] else 1
    summary(RUN_ID)
    return rc


def summary(run_id):
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines()]
    rows = [r for r in rows if r.get("run_id") == run_id and r.get("kind") == "measure"]
    by = {}
    for r in rows:
        by.setdefault(r["test"], []).append(r)
    say(f"summary {run_id} (Mbps; loopback figures are bus = 2x payload):")
    for test, rs in by.items():
        vals = [r["mbps"] for r in rs]
        spread = max(vals) - min(vals)
        say(f"  {test:<16} runs {', '.join(f'{v:.1f}' for v in vals)}; median "
            f"{statistics.median(vals):.1f}; spread {spread:.1f} "
            f"({100 * spread / statistics.median(vals):.1f}%); all ok {all(r['ok'] for r in rs)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("build")
    pb.add_argument("--probe", nargs="+", choices=PROBES, default=list(PROBES))
    pr = sub.add_parser("run")
    pr.add_argument("--probe", nargs="+", choices=PROBES, default=list(PROBES))
    pr.add_argument("--runs", type=int, default=3)
    pr.add_argument("--no-configure", action="store_true")
    for p in (pb, pr):
        p.add_argument("--pidcodes", action="store_true",
                       help=f"bulk probes as {PIDCODES[0]:04x}:{PIDCODES[1]:04x} (see PIDCODES)")
    sub.add_parser("topology")
    args = ap.parse_args()
    global USE_PIDCODES
    USE_PIDCODES = getattr(args, "pidcodes", False)
    say(f"usb_baseline {args.cmd} {RUN_ID}; per-transfer limit {XFER_MS} ms")
    if args.cmd == "build":
        return 0 if build(args.probe) else 1
    if args.cmd == "topology":
        topology()
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
