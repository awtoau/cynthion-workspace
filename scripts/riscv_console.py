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

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

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

    # Wait rather than exit. During a build-and-test cycle the board spends much of its
    # time with no bitstream loaded, so "not there yet" is the normal startup state, not a
    # failure. --once keeps the old behaviour, since a one-shot read has nothing to wait
    # for.
    clients = []
    lock = threading.Lock()
    if args.serve:
        # Listen FIRST, before waiting on hardware. Otherwise the service refuses
        # connections for the whole time the board is between bitstreams -- which during
        # active development is most of a build, about a minute at a time -- and a client
        # attaching then sees "connection refused" rather than a service that is waiting.
        threading.Thread(target=serve, args=(args.serve, clients, lock),
                         daemon=True).start()

    node = usb_ids.wait_for_tty("riscv_console")
    if not node and args.once:
        print("no RISC-V console found.", file=sys.stderr)
        print("Is a SoC bitstream loaded? Check `lsusb -d 1d50:6180`.", file=sys.stderr)
        return 1
    while not node:
        print("waiting for a SoC bitstream...", flush=True)
        node = usb_ids.wait_for_tty("riscv_console", settles=60)

    print(f"console: {node}  ({usb_ids.product_string('riscv_console')})", flush=True)

    # The console arrives in whatever chunks the tty hands over, so it is
    # reassembled into lines before being emitted: a log line that is half
    # a word is not searchable, and the cost is that a prompt with no
    # newline waits for the next one.
    pending = ""
    try:
        port = serial.Serial(node, 115200, timeout=3)
        while True:
            # Reconfiguring the FPGA tears the tty down, and that is ROUTINE here --
            # every build-and-test cycle does it. pyserial raises SerialException
            # ("device reports readiness to read but returned no data"), which is not
            # an error worth exiting on: the device is coming back in a moment.
            #
            # So reattach instead of dying. wait_for_tty settles and confirms the port
            # opens, so this waits for the NEW node rather than racing the old one.
            try:
                data = port.read(BURST_BYTES)
            except (serial.SerialException, OSError):
                # Distinguish "the board went away" from "another process stole the
                # port". If the USB device is still on the bus, this is contention,
                # not a reconfigure -- reattaching in that case produces a stream of
                # went-away/reattached messages while the CPU never restarts, which
                # is what happened when soc_run.py also opened the tty.
                still_present = usb_ids.find_usb("riscv_console") is not None
                if still_present:
                    print("\n[port taken by another reader -- reclaiming]",
                          flush=True)
                else:
                    print("\n[device went away -- reattaching]", flush=True)
                try:
                    port.close()
                except Exception:
                    pass
                # Keep waiting rather than exiting. Between an agent tearing down one
                # bitstream and configuring the next, the SoC is legitimately absent
                # from the bus for the length of a build -- about a minute. Giving up
                # after one timeout means the watcher dies during ordinary
                # development and has to be restarted by hand.
                node = None
                waited = 0
                while not node:
                    node = usb_ids.wait_for_tty("riscv_console")
                    if not node:
                        waited += 1
                        print(f"[no device -- still waiting, attempt {waited}]",
                              flush=True)
                print(f"[reattached: {node}]", flush=True)
                port = serial.Serial(node, 115200, timeout=3)
                continue
            if data:
                pending += data.decode("ascii", "replace")
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    emit(line)

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
