#!/usr/bin/env python3
#
# Device-mode port on target_phy: the EP0 engine behind a CSR window. #612
# SPDX-License-Identifier: BSD-3-Clause

"""
`UsbDevicePort`: `usbcore.device.Ep0Device` on a ULPI PHY, driven from the CPU's CSR bus.

- Same pin hand-off as `usb_host.UsbPort`: the PHY belongs to `UlpiRegisters` (`ROLE`=0) or to
  the device engine (`ROLE`=1). While not selected, `UlpiRegisters` sees `dir=1`, the engine is in reset.
- Entering device: restore PHY function control (0x04) through the register window first; the
  translator's shadow assumes the reset value.
- Switch applies when `dir` has been low `QUIET_CYCLES` and the outgoing owner is idle; bound
  `SWITCH_LIMIT` (1.25 x a PHY reset). On expiry the role is unchanged and `STATUS.timeout` is set.
- Engine ports (`gateware/usbcore/device.py`) are in `usb`. The CPU stages operands in plain
  registers, then writes `OP`: a toggle crosses to `usb`, the op runs for one cycle, a toggle returns.
  Staged registers and results are quasi-static across the crossing; firmware does not touch them
  while `STATUS.op_busy`. An `OP` write while busy is dropped and sets `EVENTS.busy_drop`.

## CSR map (8-bit; side effects alone in a word)
    0x00 ROLE      rw  [0] 1 = device engine owns the PHY
    0x04 STATUS    r   0 role 1 switching 2 timeout 3 op_busy 4 reset_seen 5 setup_valid 6 o_valid 7 in_busy
    0x05 EVENTS    rw1c 0 reset 1 setup 2 oack 3 timeout 4 busy_drop   (0-2: rising edges of STATUS 4-6)
    0x06 IRQ_EN    rw  [4:0], one bit per EVENTS bit
    0x08 SPEED     r   [1:0] snapshot: UTMI speed of the last reset (0 HS 1 FS)
    0x09 ADDR      r   [6:0] snapshot: device address
    0x0a SEQ       r   snapshot: SETUP / reset counter
    0x0b STAGE     r   [2:0] snapshot: `Stage`
    0x0c STALE     r   snapshot: commands dropped for a stale `cmd_seq`
    0x0d OLEN      r   [6:0] snapshot: bytes in the held OUT packet
    0x0e RDBYTE    r   result of `OP_READ`
    0x10..0x17 SETUP[0..7] r  snapshot: the 8 SETUP bytes, [0] = bmRequestType
    0x20 CMD_SEQ  0x21 CMD_LEN  0x22 CMD_LAST  0x23 CMD_ADDR  0x24 CMD_ADDR_VALID  0x25 CMD
    0x26 ARG_ADDR (6 bit buffer index)  0x27 ARG_DATA   all rw, operands of `OP_CMD` / `OP_PUSH`
    0x28 OP        w   1 CMD  2 PUSH (ibuf[ARG_ADDR] = ARG_DATA)  3 READ (RDBYTE = obuf[ARG_ADDR])
                       4 SNAPSHOT  5 RESET_ACK
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from amaranth               import Cat, Module, Mux, Signal, ResetInserter
from amaranth.lib           import wiring
from amaranth.lib.cdc       import FFSynchronizer
from amaranth.lib.wiring    import In, Out

from amaranth_soc           import csr

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from usbcore.device         import Ep0Device                    # noqa: E402
from usbcore.ulpi           import ULPITranslator               # noqa: E402
from usbcore.utmi           import UTMI_FROM_PHY, UTMI_TO_PHY   # noqa: E402
from clocks                 import PHY_PAD_RESET_CYCLES, PHY_PREP_CYCLES  # noqa: E402

__all__ = ["UsbDevicePort", "ROLE_REGS", "ROLE_DEVICE",
           "OP_CMD", "OP_PUSH", "OP_READ", "OP_SNAPSHOT", "OP_RESET_ACK"]

ROLE_REGS, ROLE_DEVICE = 0, 1
OP_CMD, OP_PUSH, OP_READ, OP_SNAPSHOT, OP_RESET_ACK = 1, 2, 3, 4, 5

REG_ROLE, REG_STATUS, REG_EVENTS, REG_IRQ_EN = 0x00, 0x04, 0x05, 0x06
REG_SPEED, REG_ADDR, REG_SEQ, REG_STAGE, REG_STALE, REG_OLEN, REG_RDBYTE = (
    0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e)
REG_SETUP = 0x10
REG_CMD_SEQ, REG_CMD_LEN, REG_CMD_LAST, REG_CMD_ADDR, REG_CMD_ADDR_VALID, REG_CMD = (
    0x20, 0x21, 0x22, 0x23, 0x24, 0x25)
REG_ARG_ADDR, REG_ARG_DATA, REG_OP = 0x26, 0x27, 0x28

CSR_ADDR_WIDTH = 6

# usb cycles dir must stay low before the owner changes (same figure as usb_host).
QUIET_CYCLES = 8
# Waits for: the outgoing owner to go idle. Longest busy: a PHY reset. 1.25x. Expiry: STATUS.timeout.
SWITCH_LIMIT = (5 * (PHY_PAD_RESET_CYCLES + PHY_PREP_CYCLES)) // 4


class UsbDevicePort(wiring.Component):
    def __init__(self, *, switch_limit=SWITCH_LIMIT):
        self._switch_limit = switch_limit

        def rw(width):
            return csr.Register({"v": csr.Field(csr.action.RW, width)}, access="rw")

        def ro(width):
            return csr.Register({"v": csr.Field(csr.action.R, width)}, access="r")

        self._role = rw(1)
        self._status = csr.Register({n: csr.Field(csr.action.R, 1) for n in (
            "role", "switching", "timeout", "op_busy", "reset_seen", "setup_valid",
            "o_valid", "in_busy")}, access="r")
        self._events = csr.Register({"ev": csr.Field(csr.action.RW1C, 5)}, access="rw")
        self._irq_en = rw(5)
        self._snap = {name: ro(width) for name, width in (
            ("speed", 2), ("addr", 7), ("seq", 8), ("stage", 3), ("stale", 8),
            ("olen", 7), ("rdbyte", 8))}
        self._setup = [ro(8) for _ in range(8)]
        self._stage = {name: rw(width) for name, width in (
            ("cmd_seq", 8), ("cmd_len", 7), ("cmd_last", 1), ("cmd_addr", 7),
            ("cmd_addr_valid", 1), ("cmd", 3), ("arg_addr", 6), ("arg_data", 8))}
        self._op = csr.Register({"v": csr.Field(csr.action.W, 3)}, access="w")

        builder = csr.Builder(addr_width=CSR_ADDR_WIDTH, data_width=8)
        for name, reg, offset in (
                [("role", self._role, REG_ROLE), ("status", self._status, REG_STATUS),
                 ("events", self._events, REG_EVENTS), ("irq_en", self._irq_en, REG_IRQ_EN)]
                + [(n, self._snap[n], o) for n, o in (
                    ("speed", REG_SPEED), ("addr", REG_ADDR), ("seq", REG_SEQ),
                    ("stage", REG_STAGE), ("stale", REG_STALE), ("olen", REG_OLEN),
                    ("rdbyte", REG_RDBYTE))]
                + [(f"setup{i}", reg, REG_SETUP + i) for i, reg in enumerate(self._setup)]
                + [(n, self._stage[n], o) for n, o in (
                    ("cmd_seq", REG_CMD_SEQ), ("cmd_len", REG_CMD_LEN),
                    ("cmd_last", REG_CMD_LAST), ("cmd_addr", REG_CMD_ADDR),
                    ("cmd_addr_valid", REG_CMD_ADDR_VALID), ("cmd", REG_CMD),
                    ("arg_addr", REG_ARG_ADDR), ("arg_data", REG_ARG_DATA))]
                + [("op", self._op, REG_OP)]):
            builder.add(name, reg, offset=offset)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":          In(csr.Signature(addr_width=CSR_ADDR_WIDTH, data_width=8)),
            "irq":          Out(1),
            "data_i":       In(8),
            "dir_i":        In(1),
            "nxt_i":        In(1),
            "data_o":       Out(8),
            "data_oe":      Out(1),
            "stp_o":        Out(1),
            "regs_data_o":  In(8),
            "regs_data_oe": In(1),
            "regs_stp_o":   In(1),
            "regs_idle":    In(1),
            "regs_dir":     Out(1),
            "regs_nxt":     Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        n_cdc = [0]

        def to_domain(sig, domain):
            n_cdc[0] += 1
            out = Signal(len(sig), name=f"x{n_cdc[0]}_{domain}")
            m.submodules[f"cdc{n_cdc[0]}"] = FFSynchronizer(sig, out, o_domain=domain)
            return out

        # ---- the engine and its PHY side (usb) ----
        dev = Signal()                                  # usb: engine owns the pins
        pins = SimpleNamespace(
            data=SimpleNamespace(i=self.data_i, o=Signal(8), oe=Signal()),
            dir=SimpleNamespace(i=Mux(dev, self.dir_i, 1)),
            nxt=SimpleNamespace(i=Mux(dev, self.nxt_i, 0)),
            stp=SimpleNamespace(o=Signal()),
            rst=SimpleNamespace(o=Signal()))
        xlat = ULPITranslator(ulpi=pins, handle_clocking=False)
        engine = Ep0Device()
        m.submodules.xlat = ResetInserter({"usb": ~dev})(xlat)
        m.submodules.engine = ResetInserter({"usb": ~dev})(engine)
        for name, _width in UTMI_TO_PHY:
            m.d.comb += getattr(xlat, name).eq(getattr(engine.utmi, name))
        for name, _width in UTMI_FROM_PHY:
            m.d.comb += getattr(engine.utmi, name).eq(getattr(xlat, name))

        m.d.comb += [
            self.data_o.eq(Mux(dev, pins.data.o, self.regs_data_o)),
            self.data_oe.eq(Mux(dev, pins.data.oe, self.regs_data_oe)),
            self.stp_o.eq(Mux(dev, pins.stp.o, self.regs_stp_o)),
            self.regs_dir.eq(Mux(dev, 1, self.dir_i)),
            self.regs_nxt.eq(Mux(dev, 0, self.nxt_i)),
        ]

        # ---- role switch (usb) ----
        want = to_domain(self._role.f.v.data, "usb")
        want_prev = Signal()
        quiet = Signal(range(QUIET_CYCLES + 1))
        waited = Signal(range(self._switch_limit + 1))
        timed_out = Signal()
        m.d.usb += want_prev.eq(want)
        with m.If(self.dir_i):
            m.d.usb += quiet.eq(0)
        with m.Elif(quiet != QUIET_CYCLES):
            m.d.usb += quiet.eq(quiet + 1)
        owner_idle = Mux(dev, ~xlat.busy, self.regs_idle)
        with m.If(want != want_prev):
            m.d.usb += [waited.eq(0), timed_out.eq(0)]
        with m.Elif(want != dev):
            with m.If((quiet == QUIET_CYCLES) & owner_idle):
                m.d.usb += dev.eq(want)
            with m.Elif(waited == self._switch_limit):
                m.d.usb += timed_out.eq(1)
            with m.Else():
                m.d.usb += waited.eq(waited + 1)
        switching = Signal()
        m.d.comb += switching.eq((want != dev) & ~timed_out)

        # ---- operations: sync toggle -> usb one-cycle op -> usb toggle ----
        op_t, op_acked = Signal(), Signal()
        op_code = Signal(3)
        op_ack_t = Signal()                             # usb
        op_seen = Signal()                              # usb
        op_busy = Signal()
        busy_drop = Signal()
        op_w = self._op.f.v
        ack_sync = to_domain(op_ack_t, "sync")
        m.d.comb += op_busy.eq(op_t != op_acked)
        m.d.sync += op_acked.eq(ack_sync)
        with m.If(op_w.w_stb):
            with m.If(op_busy):
                m.d.comb += busy_drop.eq(1)
            with m.Else():
                m.d.sync += [op_code.eq(op_w.w_data), op_t.eq(~op_t)]

        op_usb = to_domain(op_t, "usb")
        run = Signal()
        m.d.comb += run.eq(op_usb != op_seen)
        stg = {n: r.f.v.data for n, r in self._stage.items()}

        snap = {n: Signal(len(r.f.v.r_data), name=f"snap_{n}") for n, r in self._snap.items()}
        snap_setup = Signal(64)
        for n, r in self._snap.items():
            m.d.comb += r.f.v.r_data.eq(snap[n])
        for i, r in enumerate(self._setup):
            m.d.comb += r.f.v.r_data.eq(snap_setup[8 * i:8 * i + 8])

        m.d.comb += [
            engine.cmd.eq(stg["cmd"]), engine.cmd_seq.eq(stg["cmd_seq"]),
            engine.cmd_len.eq(stg["cmd_len"]), engine.cmd_last.eq(stg["cmd_last"]),
            engine.cmd_addr.eq(stg["cmd_addr"]),
            engine.cmd_addr_valid.eq(stg["cmd_addr_valid"]),
            engine.i_addr.eq(stg["arg_addr"]), engine.i_data.eq(stg["arg_data"]),
            engine.o_addr.eq(stg["arg_addr"]),
            engine.cmd_valid.eq(run & (op_code == OP_CMD)),
            engine.i_we.eq(run & (op_code == OP_PUSH)),
            engine.reset_ack.eq(run & (op_code == OP_RESET_ACK)),
        ]
        with m.If(run):
            m.d.usb += [op_seen.eq(op_usb), op_ack_t.eq(~op_ack_t)]
            with m.If(op_code == OP_READ):
                m.d.usb += snap["rdbyte"].eq(engine.o_data)
            with m.If(op_code == OP_SNAPSHOT):
                m.d.usb += [snap["speed"].eq(engine.speed), snap["addr"].eq(engine.address),
                            snap["seq"].eq(engine.seq), snap["stage"].eq(engine.stage),
                            snap["stale"].eq(engine.stale), snap["olen"].eq(engine.o_len),
                            snap_setup.eq(engine.setup_data)]
        # op_code is written one sync cycle before the toggle flips, so it is stable in usb.

        # ---- status and events (sync) ----
        reset_seen = to_domain(engine.reset_seen, "sync")
        setup_valid = to_domain(engine.setup_valid, "sync")
        o_valid = to_domain(engine.o_valid, "sync")
        in_busy = to_domain(engine.in_busy, "sync")
        role_now = to_domain(dev, "sync")
        timeout_s = to_domain(timed_out, "sync")
        switching_s = to_domain(switching, "sync")
        st = self._status.f
        for name, sig in (("role", role_now), ("switching", switching_s),
                          ("timeout", timeout_s), ("op_busy", op_busy),
                          ("reset_seen", reset_seen), ("setup_valid", setup_valid),
                          ("o_valid", o_valid), ("in_busy", in_busy)):
            m.d.comb += getattr(st, name).r_data.eq(sig)

        prev = Signal(4)
        m.d.sync += prev.eq(Cat(reset_seen, setup_valid, o_valid, timeout_s))
        rise = Signal(4)
        m.d.comb += rise.eq(Cat(reset_seen, setup_valid, o_valid, timeout_s) & ~prev)
        ev = self._events.f.ev
        m.d.comb += ev.set.eq(Cat(rise, busy_drop))

        irq_en = self._irq_en.f.v.data
        m.d.comb += self.irq.eq((ev.data & irq_en).any())
        return m
