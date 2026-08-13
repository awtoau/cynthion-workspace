"""Block RAM whose SIZE is not forced to a power of two.

`luna_soc.gateware.core.blockram.Peripheral` refuses anything else:

    if not isinstance(size, int) or size <= 0 or size & size-1:
        raise ValueError("Size must be an integer power of two, ...")

That is `exact_log2` on the address width, not a property of the hardware. An
ECP5 EBR is 16 Kbit and they combine in any multiple, so 48 KiB is 24 of them.
The restriction cost 16 EBRs -- the region was 64 KiB against 12,824 B of
`.data` and `.bss` -- on a design at 50 of 56 (#527).

**The WINDOW is still a power of two, because `amaranth_soc`'s decoder requires
it.** The EBRs come from `memory.Memory(depth=...)`, so a 48 KiB memory inside a
64 KiB window frees the blocks while the map stays legal. Addresses above the
memory are decoded and read zero; nothing is linked there.

Otherwise a drop-in for the luna_soc peripheral: same Wishbone signature with
`cti`/`bte`, same burst increment, same one-cycle ack.
"""

from amaranth            import Module, Signal
from amaranth.lib        import wiring, memory
from amaranth.lib.wiring import In
from amaranth.utils      import ceil_log2

from amaranth_soc        import wishbone
from amaranth_soc.memory import MemoryMap


class BlockRAM(wiring.Component):
    """`size` bytes of block RAM, in a window rounded up to a power of two.

    Parameters
    ----------
    size : int
        Memory in bytes. Any multiple of `data_width // granularity`.
    init : list[int]
        Initial contents, in words.
    """

    def __init__(self, *, size, data_width=32, granularity=8, init=(),
                 name="blockram"):
        stride = data_width // granularity
        if not isinstance(size, int) or size <= 0 or size % stride:
            raise ValueError(f"size must be a positive multiple of {stride} "
                             f"bytes, not {size!r}")

        self.size        = size
        self.granularity = granularity
        self.name        = name

        depth  = (size * granularity) // data_width
        window = 1 << ceil_log2(size)
        self._mem = memory.Memory(shape=data_width, depth=depth, init=list(init))

        super().__init__({
            "bus": In(wishbone.Signature(
                addr_width=ceil_log2(window * granularity // data_width),
                data_width=data_width, granularity=granularity,
                features={"cti", "bte"})),
        })

        memory_map = MemoryMap(addr_width=ceil_log2(window),
                               data_width=granularity)
        memory_map.add_resource(name=("memory", self.name,), size=window,
                                resource=self)
        self.bus.memory_map = memory_map

    @property
    def init(self):
        return self._mem.init

    @init.setter
    def init(self, init):
        self._mem.init = init

    def elaborate(self, platform):
        m = Module()
        m.submodules.mem = self._mem

        incr = Signal.like(self.bus.adr)
        with m.Switch(self.bus.bte):
            with m.Case(wishbone.BurstTypeExt.LINEAR):
                m.d.comb += incr.eq(self.bus.adr + 1)
            with m.Case(wishbone.BurstTypeExt.WRAP_4):
                m.d.comb += incr[:2].eq(self.bus.adr[:2] + 1)
                m.d.comb += incr[2:].eq(self.bus.adr[2:])
            with m.Case(wishbone.BurstTypeExt.WRAP_8):
                m.d.comb += incr[:3].eq(self.bus.adr[:3] + 1)
                m.d.comb += incr[3:].eq(self.bus.adr[3:])
            with m.Case(wishbone.BurstTypeExt.WRAP_16):
                m.d.comb += incr[:4].eq(self.bus.adr[:4] + 1)
                m.d.comb += incr[4:].eq(self.bus.adr[4:])

        depth   = self._mem.depth
        mem_rp  = self._mem.read_port()
        mem_wp  = self._mem.write_port(granularity=self.granularity)

        # Reads past the memory answer zero rather than aliasing. `depth` is not
        # a power of two now, so the address does not wrap on its own.
        inside = Signal()
        m.d.comb += inside.eq(self.bus.adr < depth)
        m.d.comb += self.bus.dat_r.eq(mem_rp.data & inside.replicate(
            len(self.bus.dat_r)))

        with m.If(self.bus.cyc & self.bus.stb
                  & (self.bus.cti == wishbone.CycleType.INCR_BURST)
                  & self.bus.ack):
            m.d.comb += mem_rp.addr.eq(incr)
        with m.Else():
            m.d.comb += mem_rp.addr.eq(self.bus.adr)

        m.d.comb += [
            mem_wp.addr.eq(self.bus.adr),
            mem_wp.data.eq(self.bus.dat_w),
            mem_wp.en.eq(self.bus.sel
                         & self.bus.cyc.replicate(len(self.bus.sel))
                         & self.bus.stb.replicate(len(self.bus.sel))
                         & self.bus.we.replicate(len(self.bus.sel))
                         & inside.replicate(len(self.bus.sel))),
        ]

        # One cycle per transaction, acked the cycle after the request.
        m.d.sync += self.bus.ack.eq(self.bus.cyc & self.bus.stb & ~self.bus.ack)
        return m
