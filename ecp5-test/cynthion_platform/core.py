#
# Base platform for the Cynthion board, vendored from the upstream cynthion
# package and reduced to what this workspace actually uses.
#
# Copyright (c) 2020-2023 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

"""
`CynthionPlatform`, with the LUNA inheritance chain removed.

Upstream this class is `CynthionPlatform(LUNAApolloPlatform, LatticeECP5Platform)`,
and `LUNAApolloPlatform` extends `LUNAPlatform`, which lives in `luna`. The
`cynthion` package that supplies the chain also pins `luna-soc` to a fork, and
that stack has cost this workspace real time:

  * `SPIControlPortCrossbar` starves the controller port
    (reproducer: `scripts/riscv_flash_crossbar_sim.py`)
  * `SPIController`'s CS hold uses `csr.action.W`, a one-cycle pulse where a
    latch is needed, so CS drops between transfers (found on the ILA, worked
    around locally as `HoldableSPIController`)
  * `luna_soc` vendors `amaranth_soc` and shadows the real package

None of those are ours, and we were inheriting all of it to obtain one pin map.

What survived the cut, and why:

  `DEFAULT_CLOCK_FREQUENCIES_MHZ`
      Read by `ecp5-test/adv_speed/adv_speed_gateware.py` to size a UART
      divisor. A live value, not decoration.

  `toolchain_prepare`
      Sets `ecppack_opts` to `--compress --freq 38.8`. The frequency is the SPI
      configuration clock the board boots at; losing it changes how the
      bitstream is read back out of flash at power-on. Load-bearing.

  `pseudo_power_supply_fragment` / `prepare`
      r1.4 has I/O balls strapped to VCCIO and GND that must be *driven* to
      source and sink supply current. Amaranth leaves an unrequested pin
      undriven, so without this fragment those balls float and the board is
      running on less supply than it was designed for. This is the least
      obviously necessary method here and the one most worth keeping.

  `port_sharing`
      Reports that `control_phy` is shared with Apollo via advertising. Read by
      gateware that has to hand the port back.

  `ulpi_extra_registers`
      USB3343 register 0x39 swaps D+/D- to match the hardware routing.

What was dropped as dead weight, verified rather than assumed:

  `toolchain_program`, `toolchain_flash`, `toolchain_erase`, `_ensure_unconfigured`
      Every `build()` call in this workspace passes `do_program=False`; the FPGA
      is configured through `apollo_fpga` directly by our own scripts. These
      methods were never reachable. They also carry the `apollo_fpga` import
      that would otherwise be a dependency of the platform.

  `clock_domain_generator = LunaECP5DomainGenerator`
      This class names no default generator; a design imports the one it wants.
      Designs here use `VariableClockDomainGenerator`, which solves for any
      `sync` rate while holding `usb` at exactly 60 MHz -- the LUNA generator
      offers 60/120/240 MHz only. See `docs/decisions.md`.

  `create_usb3_phy`, `get_led`, `request_optional`, `NullPin` (`LUNAPlatform`)
      Portability shims for designs that run on several boards. Nothing here
      uses them; this platform describes one board.

  `apollo_gateware_phy` (`LUNAApolloPlatform`)
      Unused. `port_sharing` is the part that is read.

  `ram_timings`
      Kept on the r1.4 subclass where upstream also defines it; the copy on this
      base class was shadowed by the subclass and never read.
"""

from amaranth import Fragment, Module
from amaranth.vendor import LatticeECP5Platform

__all__ = ["CynthionPlatform"]


class CynthionPlatform(LatticeECP5Platform):
    """ Board description for Cynthion """

    name = "unnamed platform"

    default_clk = "clk_60MHz"

    #
    # Default clock frequencies for each of our clock domains.
    #
    # Different revisions have different FPGA speed grades, and thus the
    # default frequencies will vary.
    #
    DEFAULT_CLOCK_FREQUENCIES_MHZ = {
        "fast": 240,
        "sync": 120,
        "usb":  60
    }

    # Provides any platform-specific ULPI registers necessary.
    # This is the spot to put any platform-specific vendor registers that need
    # to be written.
    ulpi_extra_registers = {
        0x39: 0b000110 # USB3343: swap D+ and D- to match the hardware design
    }

    def port_sharing(self, phy_name):
        """ Reports whether and how the USB port for a given PHY is shared with Apollo.

        Parameters
        ----------
        phy_name: str
            The name of a PHY resource on the platform.

        Returns
        -------
        A string identifying the sharing mechanism, or None if the port is not shared.

        Supported sharing mechanisms are:

        "advertising":
            The gateware must create an ApolloAdvertiser instance, which will
            signal to the Apollo MCU that it wishes to use the port. The FPGA
            may hand the port back to Apollo by ceasing advertising.
        """
        sharing = getattr(self, "apollo_port_sharing", {})
        return sharing.get(phy_name, None)

    def toolchain_prepare(self, fragment, name, **kwargs):
        overrides = {
            'ecppack_opts': '--compress --freq 38.8'
        }

        return super().toolchain_prepare(fragment, name, **overrides, **kwargs)

    def pseudo_power_supply_fragment(self):
        """ Fragment to assign fixed values to the pseudo power supply pins """
        m = Module()
        if ("pseudo_vccio", 0) in self.resources:
            m.d.comb += self.request("pseudo_vccio").o.eq(-1)
        if ("pseudo_gnd", 0) in self.resources:
            m.d.comb += self.request("pseudo_gnd").o.eq(0)
        return m

    # This list can be safely overriden in child classes.
    # Fragments defined in this class are always added.
    append_fragments = [ pseudo_power_supply_fragment ]

    def prepare(self, elaboratable, name="top", **kwargs):
        fragment = Fragment.get(elaboratable, self)

        # Merge base CynthionPlatform fragments with board-specific ones
        append_fragments = set(CynthionPlatform.append_fragments) | set(self.append_fragments)

        for subfragment in append_fragments:
            fragment.add_subfragment(Fragment.get(subfragment(self), self))

        return super().prepare(fragment, name, **kwargs)
