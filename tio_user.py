#!/usr/bin/env python3
#
# Watch the RISC-V SoC console. Run this in your own terminal and leave it.
# SPDX-License-Identifier: BSD-3-Clause

"""
Attaches to the SoC console and prints it.

    ./tio_user.py

At the repo root because it is meant to be run by hand and left running, unlike the
scripts under `scripts/` that agents invoke.

## This owns the port

**Run this, and nothing else opens the port.** Only one process can read a tty.

  * Two readers interleave the stream, each taking bytes the other never sees --
    output like `ivlive0alive`.
  * Every steal makes the other drop and reattach, which reads as the board
    reconfiguring in a loop when nothing of the sort is happening.
  * `scripts/soc_run.py` checks for a service on port 9000 and reads through that
    instead of competing. Pass `--serve` here to provide it.

## What it does that `tio` does not

  * **Finds the port by identity**, resolving VID:PID `1d50:6180` through sysfs
    rather than by node number. This machine has eleven `/dev/ttyACM*` nodes
    across four vendors, and one earlier investigation spent hours reading
    `/dev/ttyACM1`, an ST-LINK.
  * **Waits for a board.** During a gateware rebuild the SoC is legitimately
    absent for about a minute, which is not an error worth exiting on.

`tio` is fine if you prefer it -- it reconnects too, and the by-id path is stable:

    tio '/dev/serial/by-id/usb-Great_Scott_Gadgets_Cynthion_AUX:_VexiiRiscv_console-if00'

## What you will see

    RISC-V on Cynthion: Rust, block RAM, USB console.
    sum  acf13568
    prod 369d0368
    alive 00000000

The banner prints once at reset, so it only appears if you are attached when the FPGA is
configured. `alive` counts once a second after that.

`prod` is the line that matters: `0x12345678 * 3 = 0x369d0368`, computed on the CPU
rather than stored, so a correct value means the core is genuinely executing rather than
replaying a buffer.

Ctrl-C to stop.
"""

import argparse
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "ecp5-test"))

SERVE_PORT = 9000


def client_writer(conn, state, lock):
    """Relay bytes FROM a socket client TO the device.

    Without this the socket is read-only, and a client that sends a command sees it
    silently vanish -- `scripts/soc_payload.py` would issue `load` and report that the
    shell never acknowledged, which reads as a firmware fault rather than a missing
    relay.

    The device handle lives in `state` rather than being passed by value because it is
    replaced on every reattach: a reconfigure closes the port and opens a new one, and a
    thread holding the old object would write to a closed file.
    """
    while True:
        try:
            data = conn.recv(4096)
        except OSError:
            return
        if not data:
            return
        # Take the handle under the lock, but do the WRITE outside it. Holding the lock
        # across a blocking write would stall the main loop's fan-out, freezing the
        # terminal's output for as long as the device took to accept bytes.
        with lock:
            device = state.get("port")
        if device is None:
            continue  # mid-reattach; the client will retry
        try:
            device.write(data)
            device.flush()
        except OSError:
            return  # the port was replaced under us; this thread's client will reconnect


def serve(port, clients, lock, state):
    """Fan the stream out to socket clients, so other tools need not open the tty.

    Bidirectional: clients read the console AND write to it, which is what lets a script
    drive the shell while this terminal stays attached. One process owns the tty.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(8)
    print(f"serving on 127.0.0.1:{port} — other tools read and write here, not the tty",
          flush=True)
    while True:
        conn, _ = listener.accept()
        with lock:
            clients.append(conn)
        threading.Thread(target=client_writer, args=(conn, state, lock),
                         daemon=True).start()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", action="store_true",
                        help=f"also fan out on TCP {SERVE_PORT}, so scripts can read "
                             f"without taking the port")
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("pyserial is not installed: pip install pyserial", file=sys.stderr)
        return 1

    import usb_ids

    # The live device handle, shared with the writer threads. A dict because it is
    # rebound on every reattach and the threads must see the replacement.
    clients, lock, state = [], threading.Lock(), {"port": None}
    if args.serve:
        threading.Thread(target=serve, args=(SERVE_PORT, clients, lock, state),
                         daemon=True).start()

    print("watching the Cynthion RISC-V console (Ctrl-C to stop)", flush=True)

    port = None
    waiting_announced = False
    try:
        while True:
            if port is None:
                node = usb_ids.wait_for_tty("riscv_console")
                if node is None:
                    if not waiting_announced:
                        print("waiting for a bitstream...", flush=True)
                        waiting_announced = True
                    continue
                try:
                    port = serial.Serial(node, 115200, timeout=3)
                except Exception:
                    print(f"{node} is busy — another reader has it. Stop that one, or "
                          f"read via the socket if it is serving.", flush=True)
                    subprocess.run(["udevadm", "settle"], capture_output=True)
                    continue
                with lock:
                    state["port"] = port
                print(f"attached: {node}", flush=True)
                waiting_announced = False

            try:
                data = port.read(256)
            except (serial.SerialException, OSError):
                # Almost always a reconfigure. Reattach rather than exit; that is the
                # reason this exists rather than `cat`.
                print("\n[device went away — waiting]", flush=True)
                with lock:
                    state["port"] = None
                try:
                    port.close()
                except Exception:
                    pass
                port = None
                continue

            if data:
                sys.stdout.write(data.decode("ascii", "replace"))
                sys.stdout.flush()
                with lock:
                    for conn in list(clients):
                        try:
                            conn.sendall(data)
                        except OSError:
                            clients.remove(conn)
                            conn.close()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        if port is not None:
            try:
                port.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
