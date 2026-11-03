#!/usr/bin/env python3
#
# `usb` from the oscillator; `sync` and the HyperRAM clocks from one PLL.
# SPDX-License-Identifier: BSD-3-Clause

"""SoC clock domains. Two PLLs on this die (docs/chips/ecp5/lfe5u-12f.md):

- PLL A (here): CLKOP `hr` (feedback), CLKOS `hr90` (+90 degrees), CLKOS2 `sync`. One VCO,
  so `sync` must divide it: at HyperRAM CK 180 the VCO is 720 MHz and `sync` 60 = /12.
- PLL B: the config flash (`peripherals/flash.py`, SCK 190 MHz, runtime capture phase),
  #593. 190 shares no VCO with 60 or 180 in 400-800 MHz, so it needs its own PLL.
- `usb` IS the 60 MHz oscillator (A8): the FPGA sources the ULPI clock, so `usb` is exact
  by construction and no PLL constrains it.
- Phase detector >= 10 MHz (`PFD_MIN_MHZ`, DS-02012 Table 3.23 note 3): CLKI_DIV <= 6.
"""

import os

from amaranth import (ClockDomain, ClockSignal, Elaboratable, Instance, Module,
                      ResetSignal, Signal)

__all__ = ["SocClocks", "solve_pll", "reachable", "phase_params"]

VCO_MIN_MHZ = 400.0
VCO_MAX_MHZ = 800.0
DIV_MAX = 128
PFD_MIN_MHZ = 10.0
USB_PHY_MHZ = 60.0

# What the fabric has closed at, as distinct from what the PLL can produce: a request above
# it is refused before a two-minute build. `CYNTHION_SYNC_CEILING_MHZ` raises it (#470).
SYNC_CEILING_MHZ = float(os.environ.get("CYNTHION_SYNC_CEILING_MHZ") or 90.0)

# USB334x datasheet rev 1.2: RESETB low >= 1 us (5.6.2); 128 cycles = 2.133 us.
PHY_PAD_RESET_CYCLES = 128
# TPREP 1.0..1.2 ms (Table 4.3); 72000 cycles = the 1.2 ms maximum. Also used by
# `peripherals/ulpi_window.py`.
PHY_PREP_CYCLES = 72_000


def solve_pll(hr_mhz, sync_mhz, input_mhz=USB_PHY_MHZ, *, tolerance=1e-6):
    """`(vco, clki_div, clkfb_div, hr_div, sync_div)` or None.

    CLKOP = `hr` closes the loop, so `hr = input / CLKI_DIV * CLKFB_DIV` and the VCO is
    `hr * hr_div`. The largest `hr_div` in the VCO window wins (finest static phase, and the
    720 MHz VCO #588 ran on at CK 180); `sync` must divide that VCO exactly.
    """
    for clki_div in range(1, DIV_MAX + 1):
        if input_mhz / clki_div < PFD_MIN_MHZ:
            break
        for clkfb_div in range(1, DIV_MAX + 1):
            if abs(input_mhz / clki_div * clkfb_div - hr_mhz) > tolerance:
                continue
            for hr_div in range(DIV_MAX, 0, -1):
                vco = hr_mhz * hr_div
                if not (VCO_MIN_MHZ <= vco <= VCO_MAX_MHZ):
                    continue
                sync_div = round(vco / sync_mhz)
                if 1 <= sync_div <= DIV_MAX and abs(vco / sync_div - sync_mhz) <= tolerance:
                    return vco, clki_div, clkfb_div, hr_div, sync_div
    return None


def reachable(low, high, hr_mhz, input_mhz=USB_PHY_MHZ):
    """Every `sync` in `low..high` reachable beside HyperRAM CK `hr_mhz`."""
    out = set()
    for hr_div in range(1, DIV_MAX + 1):
        vco = hr_mhz * hr_div
        if not (VCO_MIN_MHZ <= vco <= VCO_MAX_MHZ):
            continue
        for d in range(1, DIV_MAX + 1):
            f = vco / d
            if low <= f <= high and solve_pll(hr_mhz, f, input_mhz) is not None:
                out.add(round(f, 6))
    return sorted(out)


def phase_params(div, degrees):
    """CPHASE/FPHASE for a static shift (prjtrellis `ecppll`: VCO steps of 1/8)."""
    count = degrees / 360.0 * div
    coarse = int(count)
    fine = int(round((count - coarse) * 8))
    if fine == 8:
        coarse, fine = coarse + 1, 0
    return div - 1 + coarse, fine


class SocClocks(Elaboratable):
    """`usb` off the oscillator; `sync`, `hr`, `hr90` on PLL A.

    `hr_mhz` is the HyperRAM CK (`peripherals/hyperram.py` takes these domains with
    `ext_clocks=True`). `locked` is PLL A's LOCK; every domain on it is reset until lock.
    """

    def __init__(self, *, sync_mhz, hr_mhz, input_mhz=USB_PHY_MHZ,
                 ceiling_mhz=SYNC_CEILING_MHZ):
        if ceiling_mhz is not None and sync_mhz > ceiling_mhz:
            raise ValueError(
                f"sync = {sync_mhz:g} MHz is above the {ceiling_mhz:g} MHz this fabric has "
                f"been observed to close at; pass `ceiling_mhz=` (or "
                f"CYNTHION_SYNC_CEILING_MHZ) to try anyway.")
        if abs(input_mhz - USB_PHY_MHZ) > 1e-9:
            raise ValueError(f"`usb` is the oscillator and the ULPI PHY needs exactly "
                             f"{USB_PHY_MHZ:g} MHz, not {input_mhz:g}")
        solved = solve_pll(hr_mhz, sync_mhz, input_mhz)
        if solved is None:
            near = reachable(sync_mhz - 15, sync_mhz + 15, hr_mhz, input_mhz)
            raise ValueError(
                f"no PLL gives sync = {sync_mhz:g} MHz beside HyperRAM CK {hr_mhz:g} MHz "
                f"(one VCO, {VCO_MIN_MHZ:g}..{VCO_MAX_MHZ:g} MHz). Reachable sync: {near}")
        self.sync_mhz, self.hr_mhz, self.input_mhz = sync_mhz, hr_mhz, input_mhz
        (self.vco_mhz, self.clki_div, self.clkfb_div, self.hr_div, self.sync_div) = solved
        # Recomputed from the dividers, not echoed from the request.
        self.actual_hr_mhz = input_mhz / self.clki_div * self.clkfb_div
        self.actual_sync_mhz = self.vco_mhz / self.sync_div
        self.actual_usb_mhz = input_mhz
        self.locked = Signal()
        # ULPI PHY reset pad (active high here); `top.py` ORs it with the CSR reset.
        self.phy_reset = Signal()
        self.phy_ready = Signal()

    def elaborate(self, platform):
        m = Module()
        m.domains.usb = ClockDomain()
        m.domains.sync = ClockDomain()
        m.domains.hr = ClockDomain()
        m.domains.hr90 = ClockDomain()
        # The power-on counter that releases `usb` cannot be held by `usb`'s own reset.
        m.domains.usb_por = ClockDomain(reset_less=True)

        osc = Signal()
        if platform is not None:
            m.d.comb += osc.eq(platform.request("clk_60MHz", 0).i)

        clk_hr, clk_hr90, clk_sync, locked = Signal(), Signal(), Signal(), Signal()
        cph90, fph90 = phase_params(self.hr_div, 90.0)
        m.submodules.pll = Instance(
            "EHXPLLL",
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8",
            a_MFG_ENABLE_FILTEROPAMP="1", a_MFG_GMCREF_SEL="2",
            p_PLLRST_ENA="DISABLED", p_INTFB_WAKE="DISABLED",
            p_STDBY_ENABLE="DISABLED", p_DPHASE_SOURCE="DISABLED",
            p_OUTDIVIDER_MUXA="DIVA", p_OUTDIVIDER_MUXB="DIVB",
            p_OUTDIVIDER_MUXC="DIVC", p_OUTDIVIDER_MUXD="DIVD",
            p_CLKI_DIV=self.clki_div, p_CLKFB_DIV=self.clkfb_div, p_FEEDBK_PATH="CLKOP",
            p_CLKOP_ENABLE="ENABLED", p_CLKOP_DIV=self.hr_div,
            p_CLKOP_CPHASE=self.hr_div - 1, p_CLKOP_FPHASE=0,
            p_CLKOS_ENABLE="ENABLED", p_CLKOS_DIV=self.hr_div,
            p_CLKOS_CPHASE=cph90, p_CLKOS_FPHASE=fph90,
            p_CLKOS2_ENABLE="ENABLED", p_CLKOS2_DIV=self.sync_div,
            p_CLKOS2_CPHASE=self.sync_div - 1, p_CLKOS2_FPHASE=0,
            i_CLKI=osc,
            # FEEDBK_PATH CLKOP: CLKFB must be CLKOP, or the loop is open and never locks.
            i_CLKFB=clk_hr,
            i_RST=0, i_STDBY=0, i_PHASESEL0=0, i_PHASESEL1=0,
            i_PHASEDIR=1, i_PHASESTEP=1, i_PHASELOADREG=1,
            i_PLLWAKESYNC=0, i_ENCLKOP=0,
            o_CLKOP=clk_hr, o_CLKOS=clk_hr90, o_CLKOS2=clk_sync, o_LOCK=locked)
        m.d.comb += self.locked.eq(locked)

        # ULPI PHY power-on reset: pad low PHY_PAD_RESET_CYCLES, then `usb` held through
        # TPREP. `usb` does not wait for the PLL; warm reconfiguration does not power-cycle
        # the PHYs, so this pulse is their only reset.
        settled = PHY_PAD_RESET_CYCLES + PHY_PREP_CYCLES
        por = Signal(range(settled + 1))
        with m.If(por != settled):
            m.d.usb_por += por.eq(por + 1)
        m.d.comb += [
            self.phy_reset.eq(por < PHY_PAD_RESET_CYCLES),
            self.phy_ready.eq(por == settled),
            ClockSignal("usb_por").eq(osc),
            ClockSignal("usb").eq(osc),
            ResetSignal("usb").eq(~self.phy_ready),
            ClockSignal("sync").eq(clk_sync),
            ClockSignal("hr").eq(clk_hr),
            ClockSignal("hr90").eq(clk_hr90),
        ]
        # LOCK is asynchronous to every domain it resets: synchronised per domain.
        for name in ("sync", "hr", "hr90"):
            lock_d = Signal(name=f"lock_{name}")
            m.submodules[f"lock_{name}_ff"] = _LockSync(locked, lock_d, name)
            m.d.comb += ResetSignal(name).eq(~lock_d)

        if platform is not None:
            platform.add_clock_constraint(clk_sync, self.actual_sync_mhz * 1e6)
            platform.add_clock_constraint(clk_hr, self.actual_hr_mhz * 1e6)
            platform.add_clock_constraint(clk_hr90, self.actual_hr_mhz * 1e6)
        return m


class _LockSync(Elaboratable):
    """2-FF synchroniser of LOCK into `domain`, itself reset-less (it drives the reset)."""

    def __init__(self, i, o, domain):
        self.i, self.o, self.domain = i, o, domain

    def elaborate(self, platform):
        m = Module()
        a = Signal(reset_less=True, name=f"lock_{self.domain}_a")
        b = Signal(reset_less=True, name=f"lock_{self.domain}_b")
        m.d[self.domain] += [a.eq(self.i), b.eq(a)]
        m.d.comb += self.o.eq(b)
        return m
