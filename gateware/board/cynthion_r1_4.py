#
# Board description for Cynthion r1.4, vendored from the upstream cynthion
# package.
#
# Copyright (c) 2020-2023 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

"""
The r1.4 pin map, copied byte-for-byte from upstream below the imports.

This is board wiring. It describes where nets land on the BGA and it changes
when the *hardware revision* changes, which for r1.4 is never. Vendoring it
costs nothing to maintain and removes the `cynthion` package -- and the `luna` /
`luna-soc` stack behind it -- from every design in `gateware/`.

Only the three import lines differ from upstream: `LEDResources` and
`ULPIResource` come from the local `.resources` (they are unobtainable
otherwise, see that module), and `CynthionPlatform` from the local reduced
`.core`.

**Plus the `ram` resource's electrical attributes** -- the one deliberate
divergence, see `HYPERRAM_*` below and #311. Pin locations are untouched.

Everything else -- pin map, connectors, `default_clk`, device, package, speed
grade -- is upstream's. Do not "improve" it. A pin map that differs from the
board is worse than no change at all: the build succeeds and the wrong ball
drives the wrong net.

`gateware/soc/top.py` does NOT build against this file. It imports the platform
from the installed `cynthion` package (`repos/cynthion/...`), so the attributes
below reach the probe bitstreams under `gateware/probes/` and not the shipping
SoC. `scripts/hyperram_pin_patch.py` is what reaches a built bitstream either
way.

Upstream: repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py
"""

import os

from amaranth.build import (Attrs, Clock, Connector, DiffPairs, Pins, PinsN,
                            Resource, Subsignal)

from .core import CynthionPlatform
from .resources import LEDResources, ULPIResource

__all__ = ["CynthionPlatformRev1D4"]

# ---- HyperRAM pin drive, explicit rather than inherited. See #311. -----------
#
# There was no DRIVE here at all, so every pin ran at the silicon default and the
# value in use was written down nowhere. This is the decision.
#
# Sweep order 8 -> 4 -> 12 -> 16 mA: 8 is the impedance match, 4 is the NEGATIVE
# CONTROL (the rung that should violate the part's minimum input slew), 16
# overshoots the W956A8's absolute maximum unterminated. Numbers in #311.
HYPERRAM_DRIVE_LADDER = ("8", "4", "12", "16")

# From the environment so walking a rung does not dirty a tracked file.
# `scripts/hyperram_pin_patch.py` reaches the same rungs in a BUILT bitstream in
# ~1 s, which is what makes these axes affordable; this is the default it starts
# from, not the only way to move.
HYPERRAM_CK_DRIVE = os.getenv("CYNTHION_HYPERRAM_CK_DRIVE", "8")
HYPERRAM_DQ_DRIVE = os.getenv("CYNTHION_HYPERRAM_DQ_DRIVE", "8")
# CS# and RESET# are static during a burst, so drive buys nothing there.
HYPERRAM_CTRL_DRIVE = os.getenv("CYNTHION_HYPERRAM_CTRL_DRIVE", "4")


class CynthionPlatformRev1D4(CynthionPlatform):
    """ Board description for Cynthion r1.4 """

    name        = "Cynthion r1.4"
    version     = (1, 4)
    device      = "LFE5U-12F"
    package     = "BG256"
    speed       = os.getenv("ECP5_SPEED_GRADE", "8")

    # By default, assume we'll be connecting via our control PHY.
    default_usb_connection = "aux_phy"

    #
    # Preferred DRAM bus I/O (de)-skewing constants.
    #
    ram_timings = dict(
        # Set max skew to meet IO setup times
        # TODO: remove this & use the PLL to produce a 90degree clock signal instead.
        clock_skew = 127
    )

    #
    # I/O resources.
    #
    resources   = [

        # Pseudo-supply pins
        #
        # These I/O pins are connected to VCCIO or GND and are intended to be
        # driven as outputs in order to source or sink additional supply
        # current.
        Resource("pseudo_vccio", 0,
                 Pins("E6 E7 D10 E10 E11 F12 J12 K12 L12 N13 P13 M11 P11 P12 L4 M4 R5 M5 N5 P4 M6 F5 G5 H5 H4 J4 J5 J3 J1 J2 R6", dir="o"),
                 Attrs(IO_TYPE="LVCMOS33")),
        Resource("pseudo_gnd", 0,
                 Pins("E5 E8 E9 E12 F13 M13 M12 N12 N11 L5 L3 M3 N6 P5 P6 F4 G2 G3 H3 H2", dir="o"),
                 Attrs(IO_TYPE="LVCMOS33")),

        # Primary, discrete 60MHz oscillator.
        Resource("clk_60MHz", 0, Pins("A8", dir="i"),
            Clock(60e6), Attrs(IO_TYPE="LVCMOS33")),

        # Connection to our SPI flash; can be used to work with the flash
        # from e.g. a bootloader.
        Resource("spi_flash", 0,

            # SCK is on pin 9; but doesn't have a traditional I/O buffer.
            # Instead, we'll need to drive a clock into a USRMCLK instance.
            # See interfaces/flash.py for more information.
            Subsignal("sdi",  Pins("T8",  dir="o")),
            Subsignal("sdo",  Pins("T7",  dir="i")),
            Subsignal("cs",   PinsN("N8", dir="o")),
            Attrs(IO_TYPE="LVCMOS33")
        ),

        # Connection to our SPI flash but using quad mode (QSPI)
        Resource("qspi_flash", 0,
            # SCK is on pin 9; but doesn't have a traditional I/O buffer.
            # Instead, we'll need to drive a clock into a USRMCLK instance.
            # See interfaces/flash.py for more information.
            Subsignal("dq",  Pins("T8 T7 M7 N7",  dir="io")),
            Subsignal("cs",  PinsN("N8", dir="o")),
            Attrs(IO_TYPE="LVCMOS33")
        ),

        # Note: UART pins R14 and T14 are connected to JTAG pins R11 (TDI)
        # and T11 (TMS) respectively, so the microcontroller can use either
        # function but not both simultaneously.

        # UART connected to the debug controller; can be routed to a host via CDC-ACM.
        Resource("uart", 0,
            Subsignal("rx",  Pins("R14",  dir="i")),
            Subsignal("tx",  Pins("T14",  dir="oe"), Attrs(PULLMODE="UP")),
            Attrs(IO_TYPE="LVCMOS33")
        ),

        # interrupt output to send signal to microcontroller
        # FPGA_ADV. Bidirectional: the sideband command protocol has the FPGA
        # answering Apollo on this wire, so it must release the line when idle
        # rather than driving it push-pull forever.
        #
        # PULLMODE=UP is required, not cosmetic. The ECP5 defaults to pull-DOWN
        # on an unconfigured IO, while Apollo pulls PA09 UP -- opposing pulls
        # leave the line at a mid-rail divider voltage when neither end drives,
        # which a UART reads as a permanent break. Both ends must agree on
        # idle-high.
        Resource("int", 0, Pins("T6", dir="io"),
                 Attrs(IO_TYPE="LVCMOS33", PULLMODE="UP")),

        # USER button
        Resource("button_user", 0, PinsN("M14", dir="i"), Attrs(IO_TYPE="LVCMOS33", PULLMODE="NONE")),

        # output signal connected to PROGRAMN to trigger FPGA reconfiguration
        Resource("self_program", 0, PinsN("T13", dir="o"), Attrs(IO_TYPE="LVCMOS33", PULLMODE="UP")),

        # FPGA LEDs
        *LEDResources(pins="E13 C13 B14 A15 D12 C11", attrs=Attrs(IO_TYPE="LVCMOS33"), invert=True),

        # USB PHYs
        ULPIResource("control_phy", 0,
            data="N16 N14 P16 P15 R16 R15 T15 P14", clk="L14", clk_dir='o',
            dir="M16", nxt="M15", stp="L15", rst="L16", rst_invert=True,
            attrs=Attrs(IO_TYPE="LVCMOS33", SLEWRATE="FAST")),
        ULPIResource("aux_phy", 0,
            data="F16 G15 G16 H15 J15 J16 K15 K16", clk="D16", clk_dir='o',
            dir="E16", nxt="F15", stp="E15", rst="J13", rst_invert=True,
            attrs=Attrs(IO_TYPE="LVCMOS33", SLEWRATE="FAST")),
        ULPIResource("target_phy", 0,
            data="R2 R1 P2 P1 N3 N1 M2 M1", clk="T4", clk_dir='o',
            dir="R3", nxt="T2", stp="T3", rst="R4", rst_invert=True,
            attrs=Attrs(IO_TYPE="LVCMOS33", SLEWRATE="FAST")),

        # direct connection to TARGET USB D+/D-
        Resource("target_usb_diff", 0, DiffPairs("N4", "P3", dir="i"), Attrs(IO_TYPE="LVDS", PULLMODE="NONE")),
        Resource("target_usb_dp", 0, Pins("N4", dir="i"), Attrs(IO_TYPE="LVCMOS33", PULLMODE="NONE")),
        Resource("target_usb_dm", 0, Pins("P3", dir="i"), Attrs(IO_TYPE="LVCMOS33", PULLMODE="NONE")),
        Resource("target_usb_dp_chirp", 0, Pins("N4", dir="i"), Attrs(IO_TYPE="LVCMOS12", PULLMODE="NONE")),
        Resource("target_usb_dm_chirp", 0, Pins("P3", dir="i"), Attrs(IO_TYPE="LVCMOS12", PULLMODE="NONE")),

        # USB Type-C controllers and pins
        Resource("target_type_c", 0,
            Subsignal("scl",   Pins( "A4", dir="o" ), Attrs(PULLMODE="NONE")),
            Subsignal("sda",   Pins( "C4", dir="io"), Attrs(PULLMODE="NONE")),
            Subsignal("int",   PinsN("A3", dir="i" ), Attrs(PULLMODE="UP")),
            Subsignal("fault", PinsN("D4", dir="i" ), Attrs(PULLMODE="UP")),
            Subsignal("sbu1",  Pins( "A2", dir="io")),
            Subsignal("sbu2",  Pins( "E4", dir="io")),
            Attrs(IO_TYPE="LVCMOS33")
        ),
        Resource("aux_type_c", 0,
            Subsignal("scl",   Pins( "H12", dir="o" ), Attrs(PULLMODE="NONE")),
            Subsignal("sda",   Pins( "G14", dir="io"), Attrs(PULLMODE="NONE")),
            Subsignal("int",   PinsN("H14", dir="i" ), Attrs(PULLMODE="UP")),
            Subsignal("fault", PinsN("J14", dir="i" ), Attrs(PULLMODE="UP")),
            Subsignal("sbu1",  Pins( "H13", dir="io")),
            Subsignal("sbu2",  Pins( "K14", dir="io")),
            Attrs(IO_TYPE="LVCMOS33")
        ),

        # power input shutoff
        Resource("control_vbus_in_en", 0, PinsN("K13", dir="o"), Attrs(IO_TYPE="LVCMOS33")),
        Resource("aux_vbus_in_en",     0, PinsN("L13", dir="o"), Attrs(IO_TYPE="LVCMOS33")),

        # VBUS passthrough
        #
        # VBUS on each of the Type-C ports can be connected to TARGET A through
        # a bidirectional switch. If any of these switches is enabled, TARGET A
        # is considered an output. An additional switch can be enabled to pass
        # VBUS through to another port in addition to TARGET A.

        Resource("target_c_vbus_en",   0, Pins("K5", dir="o"), Attrs(IO_TYPE="LVCMOS33")),
        Resource("control_vbus_en",    0, Pins("L1", dir="o"), Attrs(IO_TYPE="LVCMOS33")),
        Resource("aux_vbus_en",        0, Pins("L2", dir="o"), Attrs(IO_TYPE="LVCMOS33")),
        Resource("target_a_discharge", 0, Pins("K4", dir="o"), Attrs(IO_TYPE="LVCMOS33")),

        # voltage and current monitor
        Resource("power_monitor", 0,
            Subsignal("scl",   Pins( "D7", dir="o" ), Attrs(PULLMODE="NONE")),
            Subsignal("sda",   Pins( "C7", dir="io"), Attrs(PULLMODE="NONE")),
            Subsignal("pwrdn", PinsN("D5", dir="o" )),
            Subsignal("slow",  Pins( "C6", dir="io")),
            Subsignal("gpio",  Pins( "D6", dir="io")),
            Attrs(IO_TYPE="LVCMOS33", PULLMODE="UP")
        ),

        # HyperRAM
        #
        # Per-subsignal attributes; the resource-level set below is the fallback
        # for anything they do not name. Direction decides where DRIVE matters --
        # CK always, DQ/RWDS on writes only, CS#/RESET# never. #311.
        Resource("ram", 0,
            # DRIVE is mirrored onto CK# by nextpnr, unlike SLEWRATE. #311.
            Subsignal("clk",   DiffPairs("C3", "D3", dir="o"),
                      Attrs(IO_TYPE="LVCMOS33D", DRIVE=HYPERRAM_CK_DRIVE)),
            Subsignal("dq",    Pins("F2 B1 C2 E1 E3 E2 F3 G4", dir="io"),
                      Attrs(DRIVE=HYPERRAM_DQ_DRIVE)),
            Subsignal("rwds",  Pins( "D1", dir="io"),
                      Attrs(DRIVE=HYPERRAM_DQ_DRIVE)),
            Subsignal("cs",    PinsN("B2", dir="o"),
                      Attrs(DRIVE=HYPERRAM_CTRL_DRIVE)),
            Subsignal("reset", PinsN("C1", dir="o"),
                      Attrs(DRIVE=HYPERRAM_CTRL_DRIVE)),
            Attrs(IO_TYPE="LVCMOS33", SLEWRATE="FAST")
        ),

        # User I/O connections.
        Resource("user_pmod", 0, Pins("1 2 3 4 7 8 9 10", conn=("pmod", 0), dir="io"), Attrs(IO_TYPE="LVCMOS33")),
        Resource("user_pmod", 1, Pins("1 2 3 4 7 8 9 10", conn=("pmod", 1), dir="io"), Attrs(IO_TYPE="LVCMOS33")),
        Resource("user_mezzanine", 0,
                Pins("3 4 5 6 7 8 9 10 11 12 13 18 19 20 21 22 23 24 25 26 27 28", conn=("mezzanine", 0), dir="io"),
                Attrs(IO_TYPE="LVCMOS33", SLEWRATE="FAST")),
    ]

    connectors = [
        Connector("pmod", 0, "C9 B9 D11 C12 - - C8 D8 D9 C10 - -"), # PMOD A
        Connector("pmod", 1, "B4 B5 B6 B7 - - C5 A5 A6 A7 - -"), # PMOD B
        Connector("mezzanine", 0,
            "- - B8 A9 B10 A10 B11 D14 C14 F14 E14 G13 G12 - - - - C16 C15 B16 B15 A14 B13 A13 D13 A12 B12 A11 - -"),
    ]

    apollo_port_sharing = {'control_phy': 'advertising'}
