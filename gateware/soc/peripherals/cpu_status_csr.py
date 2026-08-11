# The CPU's own liveness, watched by the fabric. #411.
# SPDX-License-Identifier: BSD-3-Clause

"""A watchdog the firmware kicks, so the fabric can say whether the CPU is alive.

    +0  kick   W    any write reloads the countdown
    +1  flags  RW   bit 0  error   -- firmware says it has faulted
    +2  busy   RW   8      percent busy, for the sideband

## Why bus activity cannot answer this

The obvious liveness signal is `cpu.ibus.cyc` -- flash while instructions are
fetched. It does not work: **a CPU spinning in a poll loop fetches
continuously**, and #409 is exactly that shape, a status verb that starts
printing and then spins. Bus activity reports that board as healthy.

Liveness has to be asserted by firmware reaching a point it can only reach by
making progress. So the firmware kicks, and the fabric times it out.

## What `alive` means, precisely

`alive` is high while kicks keep arriving and falls when one is late. It is a
statement about the FIRMWARE, not the core: a wedged core stops kicking, and so
does a core running firmware stuck in a loop that never reaches the kick site.
Both are the answer wanted -- "is the software making progress".

## It does not reset anything

Expiry drives an indicator and nothing else. A watchdog that reboots destroys
the evidence it exists to preserve, and every wedge seen on this board so far
has been diagnosed from the state it was left in.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, connect, flipped
from amaranth_soc import csr


class CpuStatus(wiring.Component):
    """Kick register, fault flag, and a busy figure the sideband can carry.

    `period_cycles` is the bound: what it waits for is one firmware kick, the
    expected period is the caller's kick interval, and the multiplier is the
    caller's to state. On expiry `alive` falls -- nothing else happens.
    """

    def __init__(self, *, period_cycles):
        if period_cycles < 2:
            raise ValueError("period_cycles must leave room to count")
        self._period_cycles = period_cycles

        self._kick = csr.Register({"data": csr.Field(csr.action.W, 8)},
                                  access="w")
        self._flags = csr.Register({"error": csr.Field(csr.action.RW, 1),
                                    "reserved": csr.Field(csr.action.RW, 7)},
                                   access="rw")
        self._busy = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                  access="rw")

        builder = csr.Builder(addr_width=2, data_width=8)
        builder.add("kick", self._kick)
        builder.add("flags", self._flags)
        builder.add("busy", self._busy)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":   In(csr.Signature(addr_width=2, data_width=8)),
            # High while kicks keep arriving. The fabric's answer to "is the
            # firmware making progress", available when the console is not.
            "alive": Out(1),
            # Firmware's own account of itself, held until firmware clears it.
            "error": Out(1),
            "busy":  Out(8),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    @property
    def period_cycles(self):
        return self._period_cycles

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        # Counts DOWN from the bound and stops at zero, rather than counting up
        # and wrapping: a wrapping counter re-arms itself and reports a dead CPU
        # as alive once per period.
        remaining = Signal(range(self._period_cycles + 1),
                           init=self._period_cycles)
        with m.If(self._kick.f.data.w_stb):
            m.d.sync += remaining.eq(self._period_cycles)
        with m.Elif(remaining != 0):
            m.d.sync += remaining.eq(remaining - 1)

        m.d.comb += [
            self.alive.eq(remaining != 0),
            self.error.eq(self._flags.f.error.data),
            self.busy.eq(self._busy.f.data.data),
        ]
        return m
