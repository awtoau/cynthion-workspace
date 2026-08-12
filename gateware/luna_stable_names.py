#!/usr/bin/env python3
#
# Deterministic submodule names for luna's USB endpoints. #441.
# SPDX-License-Identifier: BSD-3-Clause

"""
Names luna's endpoint submodules after the endpoint, not after its address.

`USBDevice.elaborate` names each endpoint after its class, and disambiguates a
second endpoint of the same class with `id()`:

    name = endpoint.__class__.__name__
    if hasattr(m.submodules, name):
        name = f"{name}_{id(endpoint)}"       # luna/gateware/usb/usb2/device.py

`id()` is the object's address, so the name changes every process. CDC-ACM has
two `USBStreamInEndpoint`s -- the status endpoint and the data endpoint -- so
this design hit it, and yosys iterates over module names: four RTLIL lines
differed between any two elaborations of one tree, which is half of why no build
of this project was reproducible (#441).

The endpoint NUMBER is the discriminator luna wanted. It is distinct by
construction among one device's endpoints of one class and one direction, and it
is the same on every run. Two that were not distinct would now collide, and
Amaranth refuses a duplicate submodule name -- a loud failure, where `id()`
guaranteed a unique name for a design that was wrong.

Upstream fix would be `enumerate(self._endpoints)`. Until then this replaces
`id` in luna's own module namespace, which is the whole of the patch surface.

    from luna_stable_names import stable_endpoint_names
    stable_endpoint_names()          # before anything elaborates a USBDevice
"""

import inspect
import re

from luna.gateware.usb.usb2 import device as _usb2_device


# Guards the shim against a luna that grew a second, real use of `id()`.
# Shadowing the builtin is safe only while the naming line is the only caller,
# so this fails the build rather than quietly breaking whatever the new one does.
_ID_CALL = re.compile(r"(?<![\w.])id\(")
_EXPECTED_ID_CALLS = 1


def _endpoint_name_id(endpoint):
    """What luna's naming needs from `id()`, without the address."""
    return getattr(endpoint, "_endpoint_number", 0)


def stable_endpoint_names():
    """Make `USBDevice`'s endpoint submodule names a function of the design."""
    calls = len(_ID_CALL.findall(inspect.getsource(_usb2_device)))
    if calls != _EXPECTED_ID_CALLS:
        raise RuntimeError(
            f"{_usb2_device.__name__} now calls id() {calls} times, expected "
            f"{_EXPECTED_ID_CALLS}. This shim replaces id() wholesale in that "
            f"module, so a second caller would get an endpoint number instead "
            f"of an address. Re-read the module and narrow the patch (#441).")
    _usb2_device.id = _endpoint_name_id


if __name__ == "__main__":
    stable_endpoint_names()
    print(f"luna {_usb2_device.__name__}: endpoint submodule names pinned to "
          f"the endpoint number")
