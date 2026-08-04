#!/usr/bin/env python3
#
# Build a payload image and send it to the running shell over the console.
# SPDX-License-Identifier: BSD-3-Clause

"""
Stages a firmware image through HyperRAM and boots into it, with no FPGA rebuild.

## Why this exists

Every firmware change so far has cost a full bitstream rebuild -- about 60 s -- because
the Rust binary is baked into the gateware as block RAM init. The image *is* part of the
bitstream, so editing one byte re-runs synthesis and place-and-route.

The shell in `firmware/cynthion-soc` stages an image into HyperRAM from the console and
reboots; the resident bootloader at 0x0 checks it and runs it. That turns the loop into
a few seconds of serial transfer. Same CPU, same gateware, new code.

## What it does NOT do

Nothing is written to flash and nothing touches block RAM init, so the bootloader and
the bitstream's own image are both untouched. A bad image costs
`./scripts/soc_jtag_stage.py --clear` and a reset, not a reflash: the bootloader falls
back to what the bitstream placed the moment the header is gone.

## Port contention

The console is a single tty and two readers corrupt each other -- each takes bytes the
other never sees. If `tio_user.py --serve` is running, this talks through its socket
rather than opening the port. Never both.

    ./scripts/soc_payload.py <image.bin>          # send and run
    ./scripts/soc_payload.py <image.bin> --no-go  # send only
"""

import argparse
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# Must match MAX_IMAGE in firmware/cynthion-soc/src/hyperram.rs -- the length of the
# image region, which is all of block RAM but the kilobyte the bootloader keeps. The
# firmware range-checks too and answers with its own limit; this is the earlier, clearer
# error. `scripts/soc_generate_pac.py --check` holds the two together.
PAYLOAD_SIZE = 63 * 1024

# Pacing for the transfer.
#
# There is no per-chunk ack, so this cannot detect a dropped byte as it happens -- and it
# does not have to. The firmware CRC32s the image on the way out of HyperRAM and refuses
# to boot a mismatch, so a truncated transfer is reported rather than executed. Pacing is
# about keeping the RX FIFO fed, not about correctness.
#
# CHUNK matches the 64-byte RX FIFO so a burst cannot exceed what it holds even if the
# CPU stalls. The pause is deliberately generous -- far longer than the ~64 us the
# firmware needs to drain 64 bytes at 60 MHz -- because the cost of being too slow is a
# few seconds and the cost of being too fast is a corrupt image. An 8 KB image takes
# about 0.3 s.
CHUNK = 64
CHUNK_PAUSE_S = 0.002


class Link:
    """The console, reached either through the serve socket or the tty directly."""

    def __init__(self, sock=None, port=None):
        self.sock, self.port = sock, port

    @classmethod
    def open(cls):
        try:
            sock = socket.create_connection(("127.0.0.1", 9000), timeout=3)
            sock.settimeout(5)
            return cls(sock=sock), "service on 9000"
        except OSError:
            pass

        import serial
        import usb_ids

        node = usb_ids.wait_for_tty("riscv_console")
        if not node:
            return None, "no console tty"
        return cls(port=serial.Serial(node, 115200, timeout=5)), node

    def write(self, data):
        if self.sock:
            self.sock.sendall(data)
        else:
            self.port.write(data)
            self.port.flush()

    def read(self, count):
        if self.sock:
            try:
                return self.sock.recv(count)
            except OSError:
                return b""
        return self.port.read(count)

    def close(self):
        (self.sock or self.port).close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path, help="raw binary to load")
    parser.add_argument("--no-go", action="store_true",
                        help="accepted for compatibility; the firmware always reboots "
                             "into a staged image once its CRC checks out")
    args = parser.parse_args()

    if not args.image.exists():
        emit(f"no such image: {args.image}")
        return 1

    data = args.image.read_bytes()
    if len(data) > PAYLOAD_SIZE:
        emit(f"image is {len(data)} bytes; the image region is {PAYLOAD_SIZE}")
        return 1
    emit(f"{args.image.name}: {len(data)} bytes")

    link, how = Link.open()
    if link is None:
        emit(how)
        return 1
    emit(f"console: {how}")

    # A bare newline first, to land at a clean prompt whatever was half-typed.
    link.write(b"\r")
    link.read(200)

    link.write(f"load {len(data):x}\r".encode())
    reply = link.read(200).decode("ascii", "replace")
    if "send" not in reply:
        emit("the shell did not acknowledge `load`; got:")
        emit(f"  {reply.strip()!r}")
        emit("Is the resident shell running? `reset` returns to it.")
        link.close()
        return 1

    for offset in range(0, len(data), CHUNK):
        link.write(data[offset:offset + CHUNK])
        time.sleep(CHUNK_PAUSE_S)  # pace to the one-packet-per-byte console

    # The firmware stages, CRCs, writes its header and reboots itself, so there is
    # no separate `go` step: the bootloader runs the image on the way back up.
    reply = link.read(600).decode("ascii", "replace")
    emit(reply.strip())
    if "staged" not in reply:
        emit("no staging confirmation -- the shell may not have accepted `load`")
        link.close()
        return 1

    emit("--- after reboot ---")
    emit(link.read(600).decode("ascii", "replace").strip())

    link.close()
    emit()

    return 0


if __name__ == "__main__":
    sys.exit(main())
