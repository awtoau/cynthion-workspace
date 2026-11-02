#!/usr/bin/env python3
#
# PC end of the USB data path: blocks over libusb to the Cynthion's vendor interface.
# SPDX-License-Identifier: BSD-3-Clause

"""Reference PC tool for `docs/data-path.md` (#580). Not yet run on hardware (#578).

    ./scripts/datapath_pc.py selftest                 # framing only, no device
    ./scripts/datapath_pc.py out --size 65536 --ep 1  # one OUT block to phone EP1
    ./scripts/datapath_pc.py in --size 4096 --ep 1 -o tmp/in.bin
    ./scripts/datapath_pc.py echo --size 1500 --ep 1  # OUT then IN, compare
    ./scripts/datapath_pc.py ctl 01 02 03             # bytes to the RV's control port

- Device: `gateware/usb_ids.py` VENDOR_ID : product `riscv_console`, interface 2.
- EP1 OUT/IN: blocks (8-byte header from `gateware/soc/datapath/header.py`). EP2: control bytes.
- The board side must be serving: `usb target_c dp serve` in the shell.
- Log: `tmp/logs/datapath_pc.log`.
"""

import argparse
import logging
import os
import random
import subprocess
import sys
import time
from pathlib import Path


def repo_root():
    """$CYNTHION_WORKSPACE_REPO, else git, else file-relative (awto-dan#35)."""
    env = os.environ.get("CYNTHION_WORKSPACE_REPO")
    if env:
        return Path(env)
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, cwd=Path(__file__).parent, check=True).stdout.strip()
        return Path(out)
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent


ROOT = repo_root()
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from datapath.header import (  # noqa: E402
    DIR_IN, HEADER_BYTES, MAX_LEN, STATUS_NAMES, Header, in_request, out_block)

LOG = ROOT / "tmp" / "logs" / "datapath_pc.log"
INTERFACE, DATA_EP, CTL_EP = 2, 1, 2
IN_SEGMENT = 2048                      # IN FIFO depth: the largest IN result block (#577)

# Result wait. The RV's longest wait for one arm is datapath.rs `arm_limit_us`: for 64 KiB
# at NAK_LIMIT 1000, 1.25 x (128 x (1000 x 10 + 35) us + 3 x 1.6 ms) = 1.61 s; + its 0.1 s
# send wait = 1.71 s; x1.25 here = 2.2 s. On expiry: log the op, limit, elapsed; exit 1.
RESULT_TIMEOUT_MS = 2200
# Block write: the device takes bytes as fast as the phone does; same bound as above.
WRITE_TIMEOUT_MS = 2200

log = logging.getLogger("datapath_pc")


def setup_logging():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG)):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


class Link:
    """libusb transport: interface 2 of the Cynthion's AUX device."""

    def __init__(self):
        import usb.core
        import usb.util
        from usb_ids import VENDOR_ID, product_id
        self.usb = usb
        pid = product_id("riscv_console")
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=pid)
        if self.dev is None:
            raise SystemExit(f"no device {VENDOR_ID:04x}:{pid:04x} (is the data path bitstream "
                             f"loaded? #578)")
        usb.util.claim_interface(self.dev, INTERFACE)

    def write(self, ep, data, op):
        started = time.monotonic()
        try:
            n = self.dev.write(ep, data, timeout=WRITE_TIMEOUT_MS)
        except self.usb.core.USBTimeoutError:
            raise SystemExit(f"{op}: write timed out, limit {WRITE_TIMEOUT_MS} ms, elapsed "
                             f"{(time.monotonic() - started) * 1000:.0f} ms")
        if n != len(data):
            raise SystemExit(f"{op}: wrote {n} of {len(data)} bytes")

    def read(self, ep, size, op):
        started = time.monotonic()
        try:
            return bytes(self.dev.read(0x80 | ep, size, timeout=RESULT_TIMEOUT_MS))
        except self.usb.core.USBTimeoutError:
            raise SystemExit(f"{op}: read timed out, limit {RESULT_TIMEOUT_MS} ms, elapsed "
                             f"{(time.monotonic() - started) * 1000:.0f} ms")


def read_results(read, tag):
    """Result blocks for `tag` until one without `more`. Returns (headers, payload)."""
    headers, payload = [], bytearray()
    while True:
        raw = read(DATA_EP, HEADER_BYTES + IN_SEGMENT, "result")
        hdr = Header.unpack(raw)
        body = raw[HEADER_BYTES:]
        if hdr.tag != tag:
            raise SystemExit(f"result tag {hdr.tag:#x}, expected {tag:#x}")
        if hdr.dir == DIR_IN and len(body) != hdr.length:
            raise SystemExit(f"result says {hdr.length} B, carried {len(body)}")
        headers.append(hdr)
        payload += body
        if not hdr.more:
            return headers, bytes(payload)


def describe(headers):
    last = headers[-1]
    total = sum(h.length for h in headers)
    return (f"{STATUS_NAMES.get(last.status, last.status)} {total} B in {len(headers)} block(s)"
            f"{' short' if last.short else ''}")


def do_out(link, data, ep, tag, zlp=False):
    link.write(DATA_EP, out_block(data, ep=ep, tag=tag, zlp=zlp), "out block")
    headers, _ = read_results(link.read, tag)
    log.info("out ep %d %d B -> %s", ep, len(data), describe(headers))
    return headers


def do_in(link, size, ep, tag):
    link.write(DATA_EP, in_request(size, ep=ep, tag=tag), "in request")
    headers, payload = read_results(link.read, tag)
    log.info("in ep %d want %d B -> %s", ep, size, describe(headers))
    return headers, payload


def selftest():
    """Framing round trip and IN reassembly over a fake transport; no device."""
    ok = True
    for n in (0, 1, 511, 512, MAX_LEN):
        blk = out_block(bytes(n), tag=n & 0xFF, zlp=True)
        h = Header.unpack(blk)
        ok &= len(blk) == HEADER_BYTES + n and h.length == n and h.zlp and h.tag == n & 0xFF
    data = bytes(random.Random(1).randrange(256) for _ in range(5000))
    chunks = [data[i:i + IN_SEGMENT] for i in range(0, len(data), IN_SEGMENT)]
    fake = [Header(dir=DIR_IN, tag=7, more=i < len(chunks) - 1, short=i == len(chunks) - 1,
                   length=len(c)).pack() + c for i, c in enumerate(chunks)]
    headers, payload = read_results(lambda ep, size, op: fake.pop(0), 7)
    ok &= payload == data and len(headers) == 3 and headers[-1].short
    try:
        Header.unpack(b"\x00" * 8)
        ok = False
    except ValueError:
        pass
    log.info("selftest %s", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    for name in ("out", "in", "echo"):
        p = sub.add_parser(name)
        p.add_argument("--size", type=int, default=512)
        p.add_argument("--ep", type=int, default=1, help="phone endpoint number")
        p.add_argument("--tag", type=int, default=1)
        p.add_argument("--zlp", action="store_true", help="OUT: ZLP after a full last packet")
        p.add_argument("--file", type=Path, help="OUT payload from a file")
        p.add_argument("-o", "--output", type=Path, help="IN payload to a file")
    p = sub.add_parser("ctl")
    p.add_argument("bytes", nargs="+")
    args = parser.parse_args()
    setup_logging()
    if args.cmd == "selftest":
        return selftest()
    link = Link()
    if args.cmd == "ctl":
        data = bytes(int(b, 16) for b in args.bytes)
        link.write(CTL_EP, data, "ctl write")
        log.info("ctl reply %s", link.read(CTL_EP, 512, "ctl read").hex())
        return 0
    data = (args.file.read_bytes() if getattr(args, "file", None)
            else bytes(random.Random(args.size).randrange(256) for _ in range(args.size)))
    if args.cmd in ("out", "echo"):
        headers = do_out(link, data, args.ep, args.tag & 0xFF, args.zlp)
        if headers[-1].status != 0:
            return 1
    if args.cmd in ("in", "echo"):
        headers, payload = do_in(link, args.size, args.ep, (args.tag + 1) & 0xFF)
        if args.output:
            args.output.write_bytes(payload)
        if args.cmd == "echo":
            same = payload == data
            log.info("echo %s", "byte exact" if same else f"MISMATCH ({len(payload)} B back)")
            return 0 if same else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
