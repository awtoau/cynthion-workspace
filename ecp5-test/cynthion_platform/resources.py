#
# Resource constructors used by the Cynthion r1.4 board definition.
#
# Copyright (c) 2020-2023 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

"""
The two `amaranth_boards` helpers the r1.4 pin map needs, inlined.

The board definition calls exactly two constructors from `amaranth_boards`:
`LEDResources` and `ULPIResource`. Depending on the package for them is not an
option here, because `amaranth_boards` is not installed on this machine -- it is
vendored *inside* the `cynthion` package, which injects it into `sys.modules`
from its `__init__`:

    # Mildly evil hack to vendor in amaranth_boards if it's not installed:
    sys.modules["amaranth_boards"] = amaranth_boards_vendor

So `import amaranth_boards.resources` only succeeds once `cynthion` has been
imported. A platform that imported it would still be pulling in the whole
`cynthion` stack, just implicitly and through a path that breaks the moment
anyone reorders imports.

Both functions are a dozen lines of pure `amaranth.build` with no state, so they
are copied verbatim rather than depended upon. Copied, not rewritten: these
construct pin records that the r1.4 map is expressed in terms of, and a
"tidied" version that builds a subsignal differently would silently change the
pin map. Upstream sources are:

    amaranth_boards/resources/user.py       -- _SplitResources, LEDResources
    amaranth_boards/resources/interface.py  -- ULPIResource
"""

from amaranth.build import Attrs, Pins, PinsN, Resource, Subsignal

__all__ = ["LEDResources", "ULPIResource"]


def _SplitResources(*args, pins, invert=False, conn=None, attrs=None,
                    default_name, dir):
    assert isinstance(pins, (str, list, dict))

    if isinstance(pins, str):
        pins = pins.split()
    if isinstance(pins, list):
        pins = dict(enumerate(pins))

    resources = []
    for number, pin in pins.items():
        ios = [Pins(pin, dir=dir, invert=invert, conn=conn)]
        if attrs is not None:
            ios.append(attrs)
        resources.append(Resource.family(*args, number,
                                         default_name=default_name, ios=ios))
    return resources


def LEDResources(*args, **kwargs):
    return _SplitResources(*args, **kwargs, default_name="led", dir="o")


def ULPIResource(*args, data, clk, dir, nxt, stp, rst=None,
                 clk_dir='i', rst_invert=False, attrs=None, conn=None):
    assert clk_dir in ('i', 'o',)

    io = []
    io.append(Subsignal("data", Pins(data, dir="io", conn=conn, assert_width=8)))
    io.append(Subsignal("clk", Pins(clk, dir=clk_dir, conn=conn, assert_width=1)))
    io.append(Subsignal("dir", Pins(dir, dir="i", conn=conn, assert_width=1)))
    io.append(Subsignal("nxt", Pins(nxt, dir="i", conn=conn, assert_width=1)))
    io.append(Subsignal("stp", Pins(stp, dir="o", conn=conn, assert_width=1)))
    if rst is not None:
        io.append(Subsignal("rst", Pins(rst, dir="o", invert=rst_invert,
                                        conn=conn, assert_width=1)))
    if attrs is not None:
        io.append(attrs)
    return Resource.family(*args, default_name="usb", ios=io)
