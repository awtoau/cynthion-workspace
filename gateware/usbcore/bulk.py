#
# Packet FIFOs for bulk endpoints: slot memory, producer / consumer stream adapters.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Storage between the bus engine (`device.Ep0Device`) and whatever feeds an endpoint (AUX pipe, CPU).

- `PacketStream`: byte stream with packet boundaries. A beat is `valid` + `data`; `last` marks the final
  byte of a packet / transfer; a zero-length packet is one beat with `zlp` and `last`, `data` ignored.
  `ready` is the only flow control (credit = FIFO space, NAK on the bus when out of it).
- `PacketFifo`: `slots` packet buffers of `slot_bytes` in one block RAM; bus side writes / reads bytes
  by index and commits / pops whole packets. This port list is the contract: another storage
  (HyperRAM-backed) can implement the same ports behind the adapters.
- `PacketWriter`: stream -> FIFO. Closes a packet at `maxp` bytes or at `last`; `auto_zlp` appends a ZLP
  when a transfer ends exactly on `maxp`.
- `PacketReader`: FIFO -> stream, one packet at a time, one byte per cycle.
"""

from amaranth import Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

__all__ = ["PacketStream", "PacketFifo", "PacketWriter", "PacketReader"]


class PacketStream(wiring.Signature):
    """Source's view."""

    def __init__(self):
        super().__init__({"data": Out(8), "valid": Out(1), "last": Out(1), "zlp": Out(1), "ready": In(1)})


class PacketFifo(wiring.Component):
    def __init__(self, slots=2, slot_bytes=512):
        assert slots in (1, 2, 4, 8)
        self.slots, self.slot_bytes = slots, slot_bytes
        self.iw, self.lw = (slot_bytes - 1).bit_length(), slot_bytes.bit_length()
        super().__init__(wiring.Signature({
            "flush": In(1),
            "w_idx": In(self.iw), "w_data": In(8), "w_en": In(1),
            "w_commit": In(1), "w_len": In(self.lw), "w_free": Out(1), "w_count": Out(4),
            "r_have": Out(1), "r_len": Out(self.lw), "r_idx": In(self.iw), "r_data": Out(8), "r_pop": In(1),
        }))

    def elaborate(self, platform):
        m = Module()
        slots = self.slots
        mem = Memory(shape=8, depth=slots * self.slot_bytes, init=[])
        m.submodules.mem = mem
        wr, rd = mem.write_port(domain="usb"), mem.read_port(domain="usb")
        wslot, rslot = Signal(range(slots)), Signal(range(slots))
        count = Signal(range(slots + 1))
        lens = [Signal(self.lw, name=f"len{i}") for i in range(slots)]

        m.d.comb += [
            wr.addr.eq(wslot << self.iw | self.w_idx), wr.data.eq(self.w_data), wr.en.eq(self.w_en),
            rd.addr.eq(rslot << self.iw | self.r_idx), self.r_data.eq(rd.data),
            self.w_free.eq(count != slots), self.r_have.eq(count != 0), self.w_count.eq(count),
        ]
        for i in range(slots):
            with m.If(rslot == i):
                m.d.comb += self.r_len.eq(lens[i])

        commit = self.w_commit & self.w_free
        nxt = lambda s: Mux(s == slots - 1, 0, s + 1)       # noqa: E731
        with m.If(self.flush):
            m.d.usb += [count.eq(0), wslot.eq(0), rslot.eq(0)]
        with m.Else():
            for i in range(slots):
                with m.If(commit & (wslot == i)):
                    m.d.usb += lens[i].eq(self.w_len)
            with m.If(commit):
                m.d.usb += wslot.eq(nxt(wslot))
            with m.If(self.r_pop & self.r_have):
                m.d.usb += rslot.eq(nxt(rslot))
            with m.If(commit & ~(self.r_pop & self.r_have)):
                m.d.usb += count.eq(count + 1)
            with m.Elif(~commit & self.r_pop & self.r_have):
                m.d.usb += count.eq(count - 1)
        return m


class PacketWriter(wiring.Component):
    def __init__(self, fifo):
        self.fifo = fifo
        super().__init__(wiring.Signature({
            "s": In(PacketStream()), "maxp": In(10), "auto_zlp": In(1), "flush": In(1)}))

    def elaborate(self, platform):
        m = Module()
        f, s = self.fifo, self.s
        idx = Signal(f.lw)
        owe_zlp = Signal()
        nxt = idx + 1
        full = nxt >= self.maxp
        m.d.comb += s.ready.eq((f.w_free | (idx != 0)) & ~owe_zlp)
        beat = s.valid & s.ready
        m.d.comb += [f.w_idx.eq(idx), f.w_data.eq(s.data), f.w_en.eq(beat & ~s.zlp)]
        with m.If(owe_zlp):
            m.d.comb += [f.w_commit.eq(f.w_free), f.w_len.eq(0)]
            with m.If(f.w_free):
                m.d.usb += owe_zlp.eq(0)
        with m.Elif(beat & s.zlp):
            m.d.comb += [f.w_commit.eq(1), f.w_len.eq(idx)]
            m.d.usb += idx.eq(0)
        with m.Elif(beat):
            with m.If(s.last | full):
                m.d.comb += [f.w_commit.eq(1), f.w_len.eq(nxt)]
                m.d.usb += [idx.eq(0), owe_zlp.eq(s.last & full & self.auto_zlp)]
            with m.Else():
                m.d.usb += idx.eq(nxt)
        with m.If(self.flush):
            m.d.usb += [idx.eq(0), owe_zlp.eq(0)]
        return m


class PacketReader(wiring.Component):
    def __init__(self, fifo):
        self.fifo = fifo
        super().__init__(wiring.Signature({"s": Out(PacketStream()), "flush": In(1)}))

    def elaborate(self, platform):
        m = Module()
        f, s = self.fifo, self.s
        active, primed = Signal(), Signal()
        idx = Signal(f.lw)
        zlp = f.r_len == 0
        valid = active & primed
        adv = valid & s.ready
        nxt = idx + 1
        last = zlp | (nxt == f.r_len)
        m.d.comb += [
            s.valid.eq(valid), s.data.eq(f.r_data), s.zlp.eq(zlp), s.last.eq(last),
            f.r_idx.eq(Mux(adv, nxt, idx)), f.r_pop.eq(adv & last),
        ]
        with m.If(~active):
            with m.If(f.r_have):
                m.d.usb += [active.eq(1), primed.eq(0), idx.eq(0)]
        with m.Else():
            m.d.usb += primed.eq(1)
            with m.If(adv):
                with m.If(last):
                    m.d.usb += [active.eq(0), primed.eq(0)]
                with m.Else():
                    m.d.usb += idx.eq(nxt)
        with m.If(self.flush):
            m.d.usb += [active.eq(0), primed.eq(0)]
        return m
