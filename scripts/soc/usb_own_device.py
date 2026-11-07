#
# Stand-in for firmware (and the AUX relay) driving usbcore.device.Ep0Device in simulation.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
CPU-side models of the device engine's command port, for `usb_target_sim.py` (own_* groups).

- `TableFirmware`: increment 1. Answers every SETUP itself from a fixed table (the model
  device's descriptors), `latency` usb cycles after the SETUP.
- `RelayFirmware`: increment 2. Forwards each SETUP to a `Stub` (the PC userspace side) over a
  modelled AUX control channel and answers with what comes back. The link is a delay in usb
  cycles per direction plus a message log; the framing is not modelled
  (docs/usb-device-mode.md, "AUX link protocol").

Replies always carry the `seq` captured when the SETUP was read, so a reply that overtakes a
newer SETUP is dropped by the engine and counted in `stale`.
"""

from usb_host import target_device as td
from usbcore.device import CMD_EP_CLEAR, CMD_EP_DISABLE, CMD_EP_ENABLE, CMD_EP_HALT, ep_code

GET_STATUS, CLEAR_FEATURE, SET_FEATURE, SET_ADDRESS = 0, 1, 3, 5
GET_DESCRIPTOR, GET_CONFIGURATION, SET_CONFIGURATION = 6, 8, 9
GET_INTERFACE, SET_INTERFACE = 10, 11
DT_DEVICE, DT_CONFIG, DT_STRING = 1, 2, 3
CMD_IN, CMD_STATUS, CMD_STALL, CMD_OACK = 1, 2, 3, 4
MAXP = 64


def string_descriptor(text):
    body = text.encode("utf-16-le")
    return bytes([2 + len(body), DT_STRING]) + body


class Table:
    """Fixed answers: what a firmware table (or the relay's PC stub) holds."""

    def __init__(self, bulk_mps=512):
        d = td.descriptors(bulk_mps)
        self.dev = bytes(d.get_descriptor_bytes(DT_DEVICE))
        self.cfg = bytes(d.get_descriptor_bytes(DT_CONFIG))
        self.strings = {1: td.MANUFACTURER, 2: td.PRODUCT, 3: td.SERIAL}
        self.configuration = 0

    def answer(self, bm, req, val, idx, length):
        """-> ("data", bytes) | ("ok", None) | ("stall", None) | ("silent", None) (never answered)."""
        std = (bm & 0x60) == 0
        in_ = bool(bm & 0x80)
        if bm & 0x60 == 0x40 and req == td.REQ_NAK_FOREVER:
            return "silent", None
        if not std:
            return "stall", None
        if in_ and req == GET_DESCRIPTOR and bm == 0x80:
            kind, index = val >> 8, val & 0xFF
            if kind == DT_DEVICE:
                return "data", self.dev
            if kind == DT_CONFIG and index == 0:
                return "data", self.cfg
            if kind == DT_STRING and index == 0:
                return "data", bytes([4, DT_STRING, 0x09, 0x04])
            if kind == DT_STRING and idx == 0x0409 and index in self.strings:
                return "data", string_descriptor(self.strings[index])
            return "stall", None
        if in_ and req == GET_STATUS and bm in (0x80, 0x81, 0x82):
            return "data", bytes(2)
        if in_ and req == GET_CONFIGURATION and bm == 0x80:
            return "data", bytes([self.configuration])
        if in_ and req == GET_INTERFACE and bm == 0x81:
            return "data", bytes([0])
        if not in_ and req == SET_CONFIGURATION and bm == 0x00:
            if val > 1:
                return "stall", None
            self.configuration = val
            return "ok", None
        if not in_ and req == SET_INTERFACE and bm == 0x01:
            return ("ok", None) if val == 0 else ("stall", None)
        if not in_ and req in (SET_FEATURE, CLEAR_FEATURE) and bm in (0x00, 0x02):
            return "ok", None
        return "stall", None


class Port:
    """Testbench handle on the engine's CPU port."""

    def __init__(self, ctx, engine):
        self.ctx, self.e = ctx, engine

    async def tick(self):
        await self.ctx.tick("usb")

    def get(self, sig):
        return int(self.ctx.get(sig))

    async def write_buf(self, data):
        for i, b in enumerate(data):
            self.ctx.set(self.e.i_addr, i)
            self.ctx.set(self.e.i_data, b)
            self.ctx.set(self.e.i_we, 1)
            await self.tick()
        self.ctx.set(self.e.i_we, 0)

    async def command(self, seq, cmd, **kw):
        e, c = self.e, self.ctx
        c.set(e.cmd_seq, seq)
        c.set(e.cmd, cmd)
        c.set(e.cmd_len, kw.get("length", 0))
        c.set(e.cmd_last, kw.get("last", 0))
        c.set(e.cmd_addr, kw.get("addr", 0))
        c.set(e.cmd_addr_valid, 1 if "addr" in kw else 0)
        c.set(e.cmd_ep, kw.get("ep", 0))
        c.set(e.cmd_arg, kw.get("arg", 0))
        c.set(e.cmd_valid, 1)
        await self.tick()
        c.set(e.cmd_valid, 0)

    def setup(self):
        raw = self.get(self.e.setup_data).to_bytes(8, "little")
        return raw[0], raw[1], raw[2] | raw[3] << 8, raw[4] | raw[5] << 8, raw[6] | raw[7] << 8

    async def send_in(self, seq, data, length):
        """IN data stage: packets of 64, a ZLP when the data ends on a packet boundary short of wLength."""
        data = data[:length]
        packets = [data[i:i + MAXP] for i in range(0, len(data), MAXP)] or [b""]
        if data and len(data) % MAXP == 0 and len(data) < length:
            packets.append(b"")
        for n, pkt in enumerate(packets):
            if self.get(self.e.seq) != seq:
                return                      # the engine would drop it anyway; stop early
            await self.write_buf(pkt)
            await self.command(seq, CMD_IN, length=len(pkt), last=int(n == len(packets) - 1))
            while self.get(self.e.in_busy) and self.get(self.e.seq) == seq:
                await self.tick()

    async def reply(self, seq, setup, verdict, data):
        bm, req, val, _idx, length = setup
        if verdict == "silent":
            return
        if verdict == "stall":
            await self.command(seq, CMD_STALL)
        elif verdict == "data":
            await self.send_in(seq, data, length)
        else:
            addr = {"addr": val & 0x7F} if (bm, req) == (0x00, SET_ADDRESS) else {}
            await self.command(seq, CMD_STATUS, **addr)


PATTERN_LEN = 2 * 512 + 37           # EP3 IN source: one transfer of this many bytes, repeated


def pattern(n):
    return bytes((i * 5 + 1) & 255 for i in range(n))


class BulkModel:
    """What sits behind EP1-4 on the AUX side: the stream ports of the engine, driven cycle by cycle.

    - EP1 OUT -> EP1 IN: echo, packet boundaries and ZLPs kept.
    - EP2 IN: 8-byte counter packets, always refilled (interrupt endpoint).
    - EP3 IN: PATTERN_LEN-byte transfers (`pattern`), repeated.
    - EP4 OUT -> EP4 IN: echo through a consumer that is ready 700 cycles of every 1400 (backpressure).
    The CSR-master side (enable, halt, clear) is `configure` / `feature`.
    """

    THROTTLE_ON, THROTTLE_PERIOD = 700, 1400

    def __init__(self, engine):
        self.e = engine
        self.reset()

    def reset(self):
        self.echo = {1: {"rx": [], "tx": None, "i": 0}, 4: {"rx": [], "tx": None, "i": 0}}
        self.ctr, self.pat_i = 0, 0
        self.enabled = False

    async def configure(self, port, seq, on):
        e, speed_hs = self.e, port.get(self.e.speed) == 0
        mp = 512 if speed_hs else 64
        for n, is_in, arg in ((1, 0, mp), (1, 1, mp), (2, 1, 8), (3, 1, mp), (4, 0, mp), (4, 1, mp)):
            await port.command(seq, CMD_EP_ENABLE if on else CMD_EP_DISABLE, ep=ep_code(n, is_in), arg=arg)
            await port.tick()
        self.reset()
        self.enabled = on

    async def feature(self, port, seq, set_, idx):
        await port.command(seq, CMD_EP_HALT if set_ else CMD_EP_CLEAR, ep=ep_code(idx & 15, bool(idx & 0x80)))
        await port.tick()

    async def run(self, ctx):
        e = self.e
        cycle = 0
        while True:
            cycle += 1
            gate = int(cycle % self.THROTTLE_PERIOD < self.THROTTLE_ON)
            on = self.enabled
            drive = []
            for n, st in self.echo.items():
                o, i = e.ep_out[n], e.ep_in[n]
                receiving = on and st["tx"] is None
                ctx.set(o.ready, int(receiving and (gate if n == 4 else 1)))
                tx = st["tx"]
                if on and tx is not None:
                    d, l, z = tx[st["i"]]
                    ctx.set(i.data, d); ctx.set(i.last, l); ctx.set(i.zlp, z); ctx.set(i.valid, 1)
                else:
                    ctx.set(i.valid, 0)
            i2, i3 = e.ep_in[2], e.ep_in[3]
            ctx.set(i2.valid, int(on)); ctx.set(i2.data, self.ctr & 255); ctx.set(i2.last, int(self.ctr % 8 == 7)); ctx.set(i2.zlp, 0)
            ctx.set(i3.valid, int(on)); ctx.set(i3.data, pattern(PATTERN_LEN)[self.pat_i]); ctx.set(i3.zlp, 0)
            ctx.set(i3.last, int(self.pat_i == PATTERN_LEN - 1))
            sig = [(e.ep_out[n].valid, e.ep_out[n].data, e.ep_out[n].last, e.ep_out[n].zlp, e.ep_out[n].ready) for n in (1, 4)]
            flat = [x for grp in sig for x in grp] + [e.ep_in[n].ready for n in (1, 4, 2, 3)]
            vals = (await ctx.tick("usb").sample(*flat))[2:]
            if not on:
                continue
            for k, n in enumerate((1, 4)):
                v, d, l, z, rdy = vals[5 * k:5 * k + 5]
                st = self.echo[n]
                if v and rdy:
                    st["rx"].append((d, l, z))
                    if l:
                        st["tx"], st["i"], st["rx"] = st["rx"], 0, []
            base = 10
            for k, n in enumerate((1, 4)):
                st = self.echo[n]
                if st["tx"] is not None and vals[base + k]:
                    st["i"] += 1
                    if st["i"] == len(st["tx"]):
                        st["tx"] = None
            if vals[base + 2]:
                self.ctr += 1
            if vals[base + 3]:
                self.pat_i = (self.pat_i + 1) % PATTERN_LEN


class TableFirmware:
    """Increment 1: SETUP answered from `Table` after `latency` cycles."""

    def __init__(self, engine, latency=40, table=None, sabotage=False):
        self.engine, self.latency = engine, latency
        self.bulk = BulkModel(engine)
        self.table = table or Table()
        if sabotage:
            self.table.dev = self.table.dev[:8] + bytes([self.table.dev[8] ^ 1]) + self.table.dev[9:]
        self.handled = None
        self.log = []

    async def run(self, ctx):
        port = Port(ctx, self.engine)
        e = self.engine
        # SET_ADDRESS needs its own answer: the table cannot know the address
        while True:
            await port.tick()
            if port.get(e.reset_seen):
                ctx.set(e.reset_ack, 1)
                await port.tick()
                ctx.set(e.reset_ack, 0)
                self.table.configuration = 0
                self.bulk.reset()
            seq = port.get(e.seq)
            if not port.get(e.setup_valid) or seq == self.handled:
                continue
            self.handled = seq
            setup = port.setup()
            for _ in range(self.latency):
                await port.tick()
            bm, req, _val, _idx, _length = setup
            if (bm, req) == (0x00, SET_ADDRESS):
                verdict, data = "ok", None
            else:
                verdict, data = self.table.answer(*setup)
            self.log.append((seq, setup, verdict))
            await self.bulk_effects(port, seq, setup, verdict)
            await port.reply(seq, setup, verdict, data)

    async def bulk_effects(self, port, seq, setup, verdict):
        """The endpoint side of SET_CONFIGURATION and ENDPOINT_HALT, as the UDC firmware would do it."""
        bm, req, val, idx, _ = setup
        if verdict != "ok":
            return
        if (bm, req) == (0x00, SET_CONFIGURATION):
            await self.bulk.configure(port, seq, val == 1)
        elif bm == 0x02 and req in (SET_FEATURE, CLEAR_FEATURE) and val == 0:
            await self.bulk.feature(port, seq, req == SET_FEATURE, idx)

    async def data_run(self, ctx):
        await self.bulk.run(ctx)


class Stub:
    """The PC userspace side: answers a relayed SETUP from the same table, `think` cycles later."""

    def __init__(self, think=0, table=None):
        self.table = table or Table()
        self.think = think

    def handle(self, setup):
        if (setup[0], setup[1]) == (0x00, SET_ADDRESS):
            return "ok", None
        return self.table.answer(*setup)


class RelayFirmware:
    """Increment 2: SETUP -> AUX control channel -> `Stub` -> reply, each way `link` cycles.

    `reorder`: hold the reply of SETUP number `reorder` until the next SETUP has been read, to
    produce a stale reply.
    """

    def __init__(self, engine, stub, link=100, reorder=None):
        self.engine, self.stub, self.link, self.reorder = engine, stub, link, reorder
        self.handled = None
        self.messages = []          # ("setup"|"reply", seq, detail): the AUX control channel log
        self.count = 0
        self.dropped_by_engine = 0

    async def run(self, ctx):
        port = Port(ctx, self.engine)
        e = self.engine
        while True:
            await port.tick()
            if port.get(e.reset_seen):
                ctx.set(e.reset_ack, 1)
                await port.tick()
                ctx.set(e.reset_ack, 0)
            seq = port.get(e.seq)
            if not port.get(e.setup_valid) or seq == self.handled:
                continue
            self.handled = seq
            setup = port.setup()
            self.count += 1
            self.messages.append(("setup", seq, setup))
            for _ in range(self.link):
                await port.tick()
            verdict, data = self.stub.handle(setup)
            for _ in range(self.stub.think + self.link):
                await port.tick()
            self.messages.append(("reply", seq, verdict))
            if self.reorder == self.count:
                while port.get(e.seq) == seq:       # wait for the next SETUP to overtake it
                    await port.tick()
            await port.reply(seq, setup, verdict, data)
