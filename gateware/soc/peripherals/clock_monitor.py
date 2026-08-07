#!/usr/bin/env python3
#
# Measure the clocks rather than declare them. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""What the clocks ARE doing, counted in fabric, not what they were asked to do.

## Why a declared frequency is not evidence

`gateware_id` reports `sync` and `usb` as constants baked in at elaboration --
what the generator was *asked* for. That is not the same as what the silicon
produces, and the difference is not academic:

    gateware a9627d4  sync 30000000 usb 60000000 Hz

was printed by a board whose PLL had **no feedback connection at all**. It never
locked, `sync` never oscillated, and the CPU was held in reset -- while the
firmware cheerfully reported 30 MHz, because 30 MHz is what the constant said.
Finding that took a bisect across two rebuilds. A measured number would have read
zero and named it in one line.

The same applies to every failure in this class: a PLL that locks at the wrong
ratio, a bitstream that does not match the firmware reading it, a divider that
was changed in one place and not another. All of them are invisible to a constant
and obvious to a counter.

## How it measures

The 60 MHz oscillator is the one clock on this board that cannot be wrong -- it
is a discrete part, not a PLL output, and `usb` is it, passed through. So it is
the reference, and everything else is counted against it:

    usb domain    a free-running window counter, WINDOW cycles long
    sync domain   a cycle counter, latched and cleared on each window

With `WINDOW` one millisecond of oscillator, the latched count IS the frequency
in kHz. No arithmetic in firmware, no calibration, no constant to keep in step.

The window pulse crosses `usb` -> `sync` as a TOGGLE with edge detection, not as
a level. A level is lost whenever the source cycle is shorter than the
destination's and duplicated when it is longer, and these two domains are
deliberately unrelated -- that is the whole point of the clocking rework.
`stream_buffer.py` records what the level version of this mistake cost.

## What it cannot tell you

If `sync` is stopped the CPU cannot read this, or anything else. The value here
is the case where `sync` RUNS but at the wrong rate -- which is the quiet one,
and the one a declared constant actively hides.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, connect, flipped
from amaranth_soc import csr

__all__ = ["ClockMonitor"]

# One millisecond of the 60 MHz oscillator, so a latched `sync` count is that
# domain's frequency in kHz directly.
#
# A millisecond is the shortest window that makes the quantisation error smaller
# than the thing being measured: one count of slack in 1 ms is 1 kHz out of tens
# of thousands, about 0.003%. A shorter window is cheaper in counter width and
# proportionally coarser, and nothing here needs to be fast -- the firmware reads
# this once at boot and when asked.
OSCILLATOR_KHZ = 60_000
WINDOW_CYCLES = OSCILLATOR_KHZ          # 60_000 cycles = 1 ms at 60 MHz


class ClockMonitor(wiring.Component):
    """Counts `sync` cycles per millisecond of oscillator, and reports PLL lock.

    Parameters
    ----------
    lock : Signal or None
        The PLL's LOCK output, if there is one to report. It exists on every
        design here and has never been visible to firmware, so a PLL that failed
        to lock could only be inferred from its consequences.
    """

    def __init__(self, *, lock=None):
        self._lock = lock

        builder = csr.Builder(addr_width=4, data_width=8)
        self._sync_khz = builder.add(
            "sync_khz", csr.Register({"value": csr.Field(csr.action.R, 32)},
                                     access="r"), offset=0)
        self._status = builder.add(
            "status", csr.Register({"value": csr.Field(csr.action.R, 32)},
                                   access="r"), offset=4)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({"bus": In(csr.Signature(addr_width=4, data_width=8))})
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        # -- the window, in the one domain that cannot be wrong ---------------
        window = Signal(range(WINDOW_CYCLES))
        toggle = Signal()
        with m.If(window == WINDOW_CYCLES - 1):
            m.d.usb += [window.eq(0), toggle.eq(~toggle)]
        with m.Else():
            m.d.usb += window.eq(window + 1)

        # Toggle plus edge detect, not a level: `usb` and `sync` are unrelated
        # by design, so a level crossing would lose or duplicate the pulse
        # depending on which way the ratio happens to fall.
        synced = Signal()
        last = Signal()
        m.submodules.window_cdc = FFSynchronizer(toggle, synced, o_domain="sync")
        m.d.sync += last.eq(synced)
        tick = Signal()
        m.d.comb += tick.eq(synced ^ last)

        # -- the count, in the domain being measured --------------------------
        counter = Signal(32)
        measured = Signal(32)
        with m.If(tick):
            m.d.sync += [measured.eq(counter), counter.eq(1)]
        with m.Else():
            m.d.sync += counter.eq(counter + 1)

        lock = Signal()
        if self._lock is not None:
            # LOCK is asynchronous to everything; synchronise it before the CPU
            # reads it, or a read during its transition returns a metastable bit.
            m.submodules.lock_cdc = FFSynchronizer(self._lock, lock,
                                                   o_domain="sync")

        m.d.comb += [
            self._sync_khz.f.value.r_data.eq(measured),
            # bit 0 -- the PLL says it is locked
            # bit 1 -- a window has completed, so `sync_khz` means something.
            #          Before the first one it is zero, which is indistinguishable
            #          from a stopped clock unless this bit says otherwise.
            self._status.f.value.r_data.eq(
                (lock) | ((measured != 0) << 1)),
        ]
        return m
