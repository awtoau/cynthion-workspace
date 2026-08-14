#!/usr/bin/env python3
#
# Watch the RISC-V SoC console. Run this in your own terminal and leave it.
# SPDX-License-Identifier: BSD-3-Clause

"""
Attaches to the SoC console and prints it.

    ./scripts/tio_user.py

Meant to be run by hand and left running, unlike the scripts around it that agents
invoke one-shot. `ROOT` must resolve to the repo root from here, or `import
usb_ids` fails from every working directory.

## This owns the tty, and shares it over a socket

**Run this, and nothing else opens the tty.** Only one process can read one.

**It serves on TCP 9000 by default**, so that is not a problem: anything else
that wants the console -- `soc_run.py`, `soc_shell.py`, an agent -- reads AND
writes through the socket while this terminal stays attached. One process owns
the tty; everyone else goes through the port.

    ./scripts/tio_user.py              # attach, and serve on 9000
    ./scripts/tio_user.py --no-serve   # attach, exclusively

Only one process can read a tty, and the failures when two try are quiet:

  * Two readers interleave the stream, each taking bytes the other never sees --
    output like `ivlive0alive`.
  * Every steal makes the other drop and reattach, which reads as the board
    reconfiguring in a loop when nothing of the sort is happening.
  * `scripts/soc_run.py` and `scripts/soc_shell.py` check for the service on port
    9000 and read through it instead of competing. That service is on by default,
    so this works unless you passed `--no-serve`.

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
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# The workspace root, not `scripts/`. One `.parent` short pointed this at
# `scripts/gateware`, which does not exist, so `import usb_ids` failed from every
# working directory rather than only from some.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

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


def keyboard(state, lock):
    """Relay what YOU type to the device, so this terminal is a console and not a log.

    Without this the terminal is read-only: keystrokes are echoed by the tty layer,
    so typing LOOKS like it worked, and then nothing answers -- which reads as a dead
    shell rather than as a missing relay.

    RAW, one byte at a time, because the shell's TAB completion and history (#171)
    need the keystroke AS IT IS TYPED. Line-buffered, a TAB sits in the terminal's
    own buffer and arrives after Enter, by which time there is nothing to complete,
    and arrow keys never arrive as arrows at all -- a firmware feature verified on
    the board and invisible from this terminal.

    Ctrl-C reaches the BOARD rather than this process, which is what a raw terminal
    means. **Ctrl-] quits**, the same escape telnet and tio use, and the banner
    says so.

    The handle comes from `state` under the lock rather than being captured, because a
    reconfigure replaces the port object and a thread holding the old one writes to a
    closed file. The write itself happens outside the lock so a blocking write cannot
    stall the reader loop's fan-out.
    """
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    print("[raw mode: keys go to the board as you type. Ctrl-] to quit]",
          flush=True)
    try:
        tty.setraw(fd)
        while True:
            byte = os.read(fd, 1)
            if not byte:
                break
            # Ctrl-]. The one key this terminal keeps, because a raw terminal
            # hands Ctrl-C to the board and something has to end the session.
            if byte == b"\x1d":
                break
            with lock:
                device = state.get("port")
            if device is None:
                continue
            try:
                device.write(byte)
                device.flush()
            except OSError:
                break
    finally:
        # Always, including on an exception: a terminal left in raw mode has no
        # echo and no line editing, and the shell it returns to looks broken.
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        print("\r\n[detached]", flush=True)


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


def already_running():
    """Another instance with the console, or None. Two checks, not one.

    The PORT catches the ordinary case: a serving instance holds 9000. The TTY
    catches `--no-serve`, which serves nothing but still owns the device.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        probe.connect(("127.0.0.1", SERVE_PORT))
        return f"another tio_user.py is already serving on 127.0.0.1:{SERVE_PORT}"
    except OSError:
        pass
    finally:
        probe.close()

    # `fuser` names the pid; without it, say only that it is held.
    node = Path("/dev/serial/by-id")
    for link in sorted(node.glob("*riscv_console*")) if node.is_dir() else []:
        target = link.resolve()
        out = subprocess.run(["fuser", str(target)], capture_output=True, text=True)
        pids = out.stdout.split()
        if pids:
            return f"{target} is already held by pid {' '.join(pids)}"
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # ON BY DEFAULT. Opt-in, the port is held by a terminal with no way in:
    # every script wanting the console competes for the tty and gets interleaved
    # bytes or silence. A local socket costs nothing and removes that.
    parser.add_argument("--no-serve", dest="serve", action="store_false",
                        help=f"do NOT fan out on TCP {SERVE_PORT} (serving is the "
                             f"default; this makes the tty exclusive to you)")
    parser.add_argument("--verbose", action="store_true",
                        help="timestamp each status line, and report bytes seen "
                             "per attach -- for watching a board that keeps "
                             "reconfiguring")
    parser.set_defaults(serve=True)
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("pyserial is not installed: pip install pyserial", file=sys.stderr)
        return 1

    import usb_ids

    # ONE READER, ENFORCED. Two instances on the same tty steal it from each
    # other continuously -- each steal makes the other drop and reattach, the
    # byte stream is split between them, and the result reads as a board that
    # keeps disappearing. It has cost hours twice, once diagnosed as a marginal
    # flash clock and once as a firmware fault.
    #
    # Same shape as the build directory's `.build.lock`: refuse, name the
    # holder, and say what to do instead. The port is the test because serving
    # on it is what a running instance does; the tty check catches `--no-serve`.
    if (holder := already_running()) is not None:
        print(f"REFUSED: {holder}\n"
              f"  One process owns the console. Talk to the running one through "
              f"127.0.0.1:{SERVE_PORT}, or stop it first.", file=sys.stderr)
        return 1

    # The live device handle, shared with the writer threads. A dict because it is
    # rebound on every reattach and the threads must see the replacement.
    clients, lock, state = [], threading.Lock(), {"port": None}
    if args.serve:
        threading.Thread(target=serve, args=(SERVE_PORT, clients, lock, state),
                         daemon=True).start()

    # Only when a person is actually at a terminal. Under a pipe or a background
    # job stdin is not a keyboard, and reading it would either consume a script's
    # input or sit on a closed handle forever.
    interactive = sys.stdin.isatty()
    if interactive:
        threading.Thread(target=keyboard, args=(state, lock), daemon=True).start()

    # The quit key differs by mode, and saying the wrong one strands the session:
    # interactive is RAW, so Ctrl-C is a byte for the BOARD and cannot end this.
    # Ctrl-T is tio's escape and this is not tio, so that does nothing either.
    if interactive:
        print("watching the Cynthion RISC-V console", flush=True)
        print("  Ctrl-]  quits (Ctrl-C goes to the BOARD -- raw mode, so TAB and "
              "arrows reach the shell's editor)", flush=True)
        print("  type commands here — they go to the board", flush=True)
    else:
        print("watching the Cynthion RISC-V console (Ctrl-C to stop)", flush=True)

    # RAW tty: `\n` is line feed with NO carriage return, so a bare `print`
    # starts at whatever column the board's last byte left the cursor on. That
    # is the staircase these messages used to make when the board disappeared.
    def say(text, *, detail=False):
        if detail and not args.verbose:
            return
        stamp = f"{time.strftime('%H:%M:%S')} " if args.verbose else ""
        sys.stdout.write(f"\r\n{stamp}{text}\r\n" if interactive
                         else f"{stamp}{text}\n")
        sys.stdout.flush()

    port = None
    waiting_announced = False
    bytes_seen = 0
    try:
        while True:
            if port is None:
                node = usb_ids.wait_for_tty("riscv_console")
                if node is None:
                    if not waiting_announced:
                        say("waiting for a bitstream...")
                        waiting_announced = True
                    continue
                try:
                    port = serial.Serial(node, 115200, timeout=3)
                except Exception:
                    say(f"{node} is busy — another reader has it. Stop that one, "
                        f"or read via the socket if it is serving.")
                    subprocess.run(["udevadm", "settle"], capture_output=True)
                    continue
                with lock:
                    state["port"] = port
                # Repeated per attach, not once at startup: a reconfigure floods
                # the screen with the boot report, so the one line saying how to
                # get out has scrolled away by the time anyone wants it.
                say(f"attached: {node}"
                    + ("   [Ctrl-] quits]" if interactive else ""))
                waiting_announced = False

            try:
                # Take what has arrived, not a fixed count. `read(256)` blocks until
                # 256 bytes OR the port timeout, so a short reply -- a prompt, a one
                # line event -- sat here for up to three seconds before being echoed
                # or fanned out. That is invisible when tailing a chatty boot and
                # fatal for anything interactive: a client that sends a command and
                # reads for two seconds gets nothing, because the answer is still
                # inside this call waiting for bytes that will never come.
                #
                # `in_waiting` is what has already landed. When nothing has, fall back
                # to a blocking read of ONE byte so the loop parks in the kernel
                # instead of spinning -- the port timeout then bounds how long we sit
                # there with no data to deliver.
                waiting = port.in_waiting
                data = port.read(waiting) if waiting else port.read(1)
            except (serial.SerialException, OSError):
                # Almost always a reconfigure. Reattach rather than exit; that is the
                # reason this exists rather than `cat`.
                say(f"[device went away — waiting]  "
                    f"{bytes_seen} bytes this attach")
                bytes_seen = 0
                with lock:
                    state["port"] = None
                try:
                    port.close()
                except Exception:
                    pass
                port = None
                continue

            if data:
                bytes_seen += len(data)
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
        say("stopped")
    finally:
        if port is not None:
            try:
                port.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
