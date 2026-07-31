#!/usr/bin/env python3
#
# Watch the RISC-V console, and expose it on a TCP socket.
# SPDX-License-Identifier: BSD-3-Clause

"""
Tails the SoC's CDC console, optionally serving it on a TCP port.

The console is a real `/dev/ttyACM*` node, so `screen` or `minicom` work on it directly.
This exists for two things those do not do:

- **Finds the port by identity.** `usb_ids.wait_for_tty` resolves by VID:PID from sysfs.
  This workstation has eleven `ttyACM` nodes across four vendors, and an investigation
  into a silent SoC once spent hours reading `/dev/ttyACM1`, an **ST-LINK**. Never index
  by node number.
- **Serves it on a socket**, so more than one thing can watch at once -- an editor, a log
  tail, and a test harness -- without fighting over an exclusive tty open.

## Socket mode

    ./scripts/riscv_console.py --serve 9000
    nc localhost 9000

Read-only by design: bytes flow device to client, never back. The console is an output
FIFO and there is no receive path in the SoC to write to, so accepting input would give a
socket that silently swallowed whatever was typed.

Multiple clients each get their own copy from the point they connect. Nothing is buffered
for late joiners -- the banner prints once at reset, so a client that connects afterwards
sees ticks and no header. Reconfigure the FPGA to see the banner again.

## Reading the output

    sum  acf13568        0x12345678 + 0x9abcdef0
    prod 369d0368        0x12345678 * 3 -- proves real multiplication, not a stored constant
    tick 00000000        a counter, roughly one per second

The product is the useful one. A CPU with marginal timing does not stop; it computes the
wrong answer, so a correct product means more than output merely arriving.

    ./scripts/riscv_console.py                 # tail to stdout
    ./scripts/riscv_console.py --serve 9000    # also serve on TCP 9000
    ./scripts/riscv_console.py --once          # read one burst and exit
"""

import argparse
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "riscv_console.log"

sys.path.insert(0, str(ROOT / "ecp5-test"))

# One burst read. Big enough to catch the banner plus several ticks, small enough that a
# quiet console still returns promptly.
BURST_BYTES = 512


def serve(port, clients, lock):
    """Accept connections forever, adding each to the fan-out list."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(8)
    print(f"serving on 127.0.0.1:{port}  (nc localhost {port})", flush=True)
    while True:
        conn, _ = listener.accept()
        with lock:
            clients.append(conn)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", type=int, metavar="PORT",
                        help="also serve the stream on this TCP port")
    parser.add_argument("--once", action="store_true",
                        help="read one burst and exit")
    args = parser.parse_args()

    import serial
    import usb_ids

    node = usb_ids.wait_for_tty("riscv_console")
    if not node:
        print("no RISC-V console found.", file=sys.stderr)
        print("Is a SoC bitstream loaded? Check `lsusb -d 1d50:6180`.", file=sys.stderr)
        return 1

    print(f"console: {node}  ({usb_ids.product_string('riscv_console')})", flush=True)

    clients = []
    lock = threading.Lock()
    if args.serve:
        threading.Thread(target=serve, args=(args.serve, clients, lock),
                         daemon=True).start()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    port = serial.Serial(node, 115200, timeout=3)
    try:
        with LOG.open("a") as handle:
            while True:
                data = port.read(BURST_BYTES)
                if data:
                    text = data.decode("ascii", "replace")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    handle.write(text)
                    handle.flush()

                    # Fan out to any socket clients, dropping those that have gone.
                    with lock:
                        for conn in list(clients):
                            try:
                                conn.sendall(data)
                            except OSError:
                                clients.remove(conn)
                                conn.close()
                if args.once:
                    break
    except KeyboardInterrupt:
        print("\n(stopped)", flush=True)
    finally:
        port.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
