#!/usr/bin/env python3
#
# A virtual USB device for the kernel's vhci_hcd, driven from Python (USB/IP protocol).
# SPDX-License-Identifier: GPL-3.0-or-later

"""Virtual USB device on vhci_hcd: the Python process is the device, the kernel enumerates it.

- Used inside the cudc_vm_test.py VM only (needs root, vhci_hcd; never the host).
- Why not dummy_hcd + FunctionFS: dummy_hcd has 3 bulk IN + 3 bulk OUT endpoints at fixed
  addresses; the AUX link needs 5 + 5 at fixed addresses (docs/aux-link.md section 2).
- Protocol: Documentation/usb/usbip_protocol.rst, big-endian; only CMD_SUBMIT / CMD_UNLINK.
"""

import os
import socket
import struct
import threading
from pathlib import Path

VHCI = Path("/sys/devices/platform/vhci_hcd.0")
CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK = 1, 2, 3, 4
URB_ZERO_PACKET = 0x40                  # URB_ZERO_PACKET in include/linux/usb.h
ECONNRESET, EPIPE_, EOVERFLOW_ = -104, -32, -75
USB_SPEED_HIGH = 3


class Urb:
    """One URB from the kernel. `data` = OUT payload; complete() answers it exactly once."""

    def __init__(self, dev, seqnum, ep, in_, flags, length, setup, data):
        self.dev, self.seqnum, self.ep, self.is_in = dev, seqnum, ep, in_
        self.flags, self.length, self.setup, self.data = flags, length, setup, data
        self.done = False
        self.t_rx = None

    @property
    def zero_packet(self):
        return bool(self.flags & URB_ZERO_PACKET)

    def complete(self, status=0, data=b""):
        return self.dev._ret_submit(self, status, data)


class VirtualDevice:
    """Subclass and implement on_urb(urb) (called from the reader thread: must not block) and
    optionally on_unlink(urb) -> bool (True: the URB was pending and is now cancelled)."""

    def __init__(self):
        self.sock = None
        self.port = None
        self.pending = {}               # seqnum -> Urb not yet completed
        self.lock = threading.Lock()
        self.tx_lock = threading.Lock()
        self.reader = None
        self.closed = False

    # ---- kernel side ----

    def attach(self, port=0, devid=(1 << 16) | 2, speed=USB_SPEED_HIGH):
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock = mine
        self.port = port
        (VHCI / "attach").write_text(f"{port} {theirs.fileno()} {devid} {speed}")
        theirs.close()                  # vhci holds its own reference
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name="usbip-rx")
        self.reader.start()

    def detach(self):
        self.closed = True
        try:
            (VHCI / "detach").write_text(str(self.port))
        except OSError:
            pass
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
        if self.reader:
            self.reader.join(2)         # waits for: the reader to see EOF; instant after shutdown

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def _read_loop(self):
        try:
            while not self.closed:
                hdr = self._recv(48)
                cmd, seqnum, _devid, direction, ep = struct.unpack(">5I", hdr[:20])
                if cmd == CMD_SUBMIT:
                    flags, length, _sf, npk, _iv = struct.unpack(">IiiiI", hdr[20:40])
                    setup = hdr[40:48]
                    data = b"" if direction else self._recv(length)
                    if npk > 0:
                        raise RuntimeError("isochronous URB: not supported by the model")
                    urb = Urb(self, seqnum, ep, bool(direction), flags, length, setup, data)
                    with self.lock:
                        self.pending[seqnum] = urb
                    self.on_urb(urb)
                elif cmd == CMD_UNLINK:
                    (target,) = struct.unpack(">I", hdr[20:24])
                    with self.lock:
                        urb = self.pending.get(target)
                    gone = bool(urb) and self.on_unlink(urb)
                    if gone:
                        with self.lock:
                            self.pending.pop(target, None)
                        urb.done = True
                    self._ret_unlink(seqnum, ECONNRESET if gone else 0)
                else:
                    raise RuntimeError(f"usbip command {cmd}")
        except (EOFError, OSError):
            pass

    def _ret_submit(self, urb, status, data):
        with self.lock:
            if urb.done or self.pending.pop(urb.seqnum, None) is None:
                return False            # unlinked meanwhile: the kernel already has the answer
            urb.done = True
        payload = data if urb.is_in else b""
        hdr = struct.pack(">5I", RET_SUBMIT, urb.seqnum, 0, 0, 0)
        hdr += struct.pack(">iiiii", status, len(data) if urb.is_in else urb.length, 0, -1, 0)
        hdr += bytes(8)
        self._send(hdr + payload)
        return True

    def _ret_unlink(self, seqnum, status):
        hdr = struct.pack(">5I", RET_UNLINK, seqnum, 0, 0, 0) + struct.pack(">i", status) + bytes(24)
        self._send(hdr)

    def _send(self, blob):
        with self.tx_lock:
            try:
                self.sock.sendall(blob)
            except OSError:
                pass

    # ---- device side hooks ----

    def on_urb(self, urb):
        raise NotImplementedError

    def on_unlink(self, urb):
        return True


def vhci_ready():
    return (VHCI / "attach").exists()


if __name__ == "__main__":
    print(__doc__)
    print("module for cudc_aux_model.py; vhci_hcd present:", vhci_ready(), "pid", os.getpid())
