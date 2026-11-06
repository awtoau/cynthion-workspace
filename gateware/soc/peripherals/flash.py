#!/usr/bin/env python3
#
# Config flash: the #590 clean-room core behind XIP, a command port and CSRs (#593).
# SPDX-License-Identifier: BSD-3-Clause

"""SoC config-flash peripheral. Map, codes and errors: docs/chips/flash-soc-peripheral.md.

- Core: `gateware/flash_baseline/spiflash.py` (TxEngine, FlashPHY, FlashPins, desc): one
  transaction per descriptor, /CS held for the whole of it, gated MCLK.
- Domains: `sync` (CSRs, XIP line buffer, bus), `fsck` (engine, core, pin registers),
  `fcap`/`fcapn` (read capture, runtime PHASESTEP). Clocks from `clocks.SocClocks`.
- XIP: a miss fetches the aligned 64 B line with one EBh 1-4-4 transaction; words are
  acked as they land. Stores, and loads while a write-class command or calibration
  runs (the part answers nothing but SR1 then), are answered ERR, never held.
- Command port: optional WREN, one transaction (descriptor, address, <= 256 B through
  `buf`), optional BUSY poll bounded by `wait_us`. Every wait is bounded; expiry is a
  code, a sticky error and an IRQ.
- Calibration after lock and on command: phase x lat x xfer scan on the JEDEC ID (IO1),
  then on EBh against a 0Bh reference read (all four lines); centre of the widest run.
"""

import importlib.util
from pathlib import Path

from amaranth             import (Cat, Const, Elaboratable, Instance, Module, Mux,
                                  ResetInserter, Signal, DomainRenamer)
from amaranth.lib         import io, memory, wiring
from amaranth.lib.cdc     import FFSynchronizer
from amaranth.lib.fifo    import AsyncFIFO
from amaranth.lib.wiring  import In, Out
from amaranth_soc         import wishbone
from amaranth_soc.memory  import MemoryMap

_spec = importlib.util.spec_from_file_location(
    "flash_baseline_spiflash",
    Path(__file__).resolve().parents[2] / "flash_baseline" / "spiflash.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)
desc, W1, W4, LAT_MAX, MBYTE = core.desc, core.W1, core.W4, core.LAT_MAX, core.MBYTE

__all__ = ["Flash", "SCK_MHZ", "FLASH_BYTES", "xip_worst_sync_cycles"]

# Flash SCK, MHz: 180 shares PLL0's 720 MHz VCO with sync 60; 190 (#590) cannot (#593).
SCK_MHZ = 120.0
FLASH_BYTES = 4 << 20
LINE_BYTES = 64
BUF_BYTES = 256
JEDEC_ID = 0xEF4016             # W25Q32JV-IQ (docs/chips/w25q32-config-flash.md)
# Calibration reference: the firmware image (`.text` LMA), which every SoC boot needs.
CAL_REF_ADDR = 0x0B0000
CAL_REF_BYTES = 64
CAL_LATS = 8                    # lat 0..7 for the x1 scan (#590 harness range)
CAL_MIN_WIDTH = 2               # clean points; fewer is no eye

OP_RUN, OP_CALIBRATE = 1, 2
(CODE_OK, CODE_BUSY, CODE_ARG, CODE_NOT_READY, CODE_WDOG, CODE_ABORT,
 CODE_CAL_NONE) = 0, 1, 2, 3, 7, 11, 13
(ERR_OP, ERR_WDOG, ERR_CAL, ERR_XIP_BLOCKED, ERR_XIP_WRITE, ERR_XIP_FILL,
 ERR_PLL_LOST) = range(7)
N_ERR = 7
# Register words (`bus`, byte offset = 4 x word); fields in docs/chips/flash-soc-peripheral.md.
(REG_STATUS, REG_CMD, REG_CTRL, REG_IRQ_EN, REG_DESC, REG_ADDR, REG_LEN, REG_WAIT_US,
 REG_POLL_US, REG_CAL, REG_CAL_US) = range(11)
REG_BYTES = 64
# Command-port descriptors carry no QPI opcode (bit 9), no dual address (bit 16) and no
# dual/DTR data (bit 23): x1 and x4 only, so the core's other width paths fold away.
DESC_MASK = 0xFFFFFFFF & ~(1 << 9 | 1 << 16 | 1 << 23)

XIP_DESC = desc(0xEB, abits=32, aw=W4, dummy=4, dw=W4, read=True, tail=1)
JEDEC_DESC = desc(0x9F, dw=W1, read=True)
REF_DESC = desc(0x0B, abits=24, aw=W1, dummy=8, dw=W1, read=True, tail=1)
WREN_DESC = desc(0x06)
POLL_DESC = desc(0x05, dw=W1, read=True, poll=True)

# The longest non-poll transaction, sck cycles: IDLE/LOAD/SETUP 3, cmd 8, address 32,
# dummy 31, BUF_BYTES x1, tail 15, HOLD 1, GAP max(csh, lat+2) <= LAT_MAX + 2.
TX_MAX_CYCLES = 3 + 8 + 32 + 31 + 8 * BUF_BYTES + 15 + 1 + LAT_MAX + 2
TX_LIMIT = -(-5 * TX_MAX_CYCLES // 4)
# One XIP line: 3 + cmd 8 + address/mode 8 + dummy 4 + 2 per byte + tail 1 + 1 + gap.
XIP_TX_CYCLES = 3 + 8 + 8 + 4 + 2 * LINE_BYTES + 1 + 1 + LAT_MAX + 2
# sync-side crossing allowance: 2-FF toggle each way + FIFO pointer sync, sync cycles.
CDC_SYNC = 12
# One JEDEC probe (3 bytes x1) and one PHASESTEP pulse (8) plus the FSM's 2.
JEDEC_TX_CYCLES = 3 + 8 + 24 + 1 + LAT_MAX + 2
PS_CYCLES = 8 + 2


def cal_cycles(n_steps):
    """Longest calibration, sck cycles: x1 scan, x4 scan (3 lats), reference, two
    placements of up to a turn each, two verify probes."""
    x1 = 2 * CAL_LATS * n_steps * (JEDEC_TX_CYCLES + PS_CYCLES)
    x4 = 2 * 3 * n_steps * (XIP_TX_CYCLES + PS_CYCLES)
    ref = 3 + 8 + 24 + 8 + 8 * CAL_REF_BYTES + 1 + 1 + LAT_MAX + 2
    return x1 + x4 + ref + 2 * n_steps * PS_CYCLES + JEDEC_TX_CYCLES + XIP_TX_CYCLES + 64


def cal_us(n_steps, sck_mhz):
    return -(-cal_cycles(n_steps) // int(sck_mhz))


def csh_cycles(sck_mhz, ns):
    """/CS high after a transaction: tSHSL1 10 ns (reads), tSHSL2 50 ns (writes), rev G 9.6."""
    return -(-int(ns * sck_mhz) // 1000)


def xip_worst_sync_cycles(*, sync_mhz, sck_mhz=SCK_MHZ):
    """Longest a legitimate XIP load waits, sync cycles: behind one maximal command
    transaction, then its own line (write-class commands answer ERR instead)."""
    sck = TX_MAX_CYCLES + XIP_TX_CYCLES
    return -(-int(sck * sync_mhz) // int(sck_mhz)) + CDC_SYNC


class _Engine(Elaboratable):
    """Everything on the flash side. Written in `sync`; renamed to `fsck` by `Flash`."""

    def __init__(self, *, n_steps, sck_mhz, jedec_id=JEDEC_ID, cal_ref_addr=CAL_REF_ADDR):
        self.n_steps, self.sck_mhz = n_steps, sck_mhz
        self.jedec_id, self.cal_ref_addr = jedec_id, cal_ref_addr
        self.pins = core.FlashPins()
        self.tx = core.TxEngine(self.pins)
        self.xfer = Signal()
        self.phasestep = Signal(init=1)
        # command, static while `start` is pending
        self.start, self.abort = Signal(), Signal()
        self.op, self.desc, self.addr = Signal(4), Signal(32), Signal(24)
        self.len, self.wren, self.wait, self.wait_us = Signal(9), Signal(), Signal(), Signal(24)
        # results, static once `done` pulses
        self.done = Signal()
        self.r_code, self.r_poll_us, self.r_saw_busy = Signal(4), Signal(24), Signal()
        # XIP: request (payload static until the next), words out to the sync FIFO
        self.xip_start, self.xip_line, self.xip_id = Signal(), Signal(16), Signal(2)
        self.xip_we, self.xip_word, self.xip_idx = Signal(), Signal(32), Signal(4)
        self.xip_wid = Signal(2)
        # command buffers: TX read port (1-cycle), RX write port
        self.tx_addr, self.tx_data = Signal(6), Signal(32)
        self.rx_addr, self.rx_data, self.rx_we = Signal(6), Signal(32), Signal()
        # status
        self.ready, self.cal_valid, self.cal_x4, self.busy = Signal(), Signal(), Signal(), Signal()
        self.cal_step, self.cal_lat, self.cal_xfer = Signal(8), Signal(4), Signal()
        self.width_x1, self.width_x4 = Signal(8), Signal(8)

    def elaborate(self, platform):
        m = Module()
        s = m.d.sync
        tx, n = self.tx, self.n_steps
        tx_rst = Signal()
        m.submodules.tx = ResetInserter(tx_rst)(tx)
        lat = Signal(4)
        m.d.comb += [tx.lat.eq(lat), tx.half.eq(0)]
        csh_r, csh_w = csh_cycles(self.sck_mhz, 10), csh_cycles(self.sck_mhz, 50)
        us_cycles = int(round(self.sck_mhz))

        # ---- requests
        cmd_pending, xip_pending, cal_pending, prefer_cmd = Signal(), Signal(), Signal(init=1), Signal()
        with m.If(self.start):
            s += cmd_pending.eq(1)
        with m.If(self.xip_start):
            s += xip_pending.eq(1)

        # ---- transaction issue, watchdog, received bytes
        RX_NONE, RX_BUF, RX_XIP, RX_JEDEC, RX_REF, RX_X4, RX_POLL = range(7)
        rx_mode = Signal(3)
        wd = Signal(range(TX_LIMIT + 1))
        nbytes, acc, wcnt = Signal(9), Signal(32), Signal(9)
        res24, mismatch, saw_busy = Signal(24), Signal(), Signal()
        ref_and, ref_or = Signal(8), Signal(8)
        ref = memory.Memory(shape=8, depth=CAL_REF_BYTES, init=[])
        m.submodules.ref = ref
        ref_wp, ref_rp = ref.write_port(), ref.read_port(domain="comb")
        cur_id = Signal(2)

        def issue(dsc, addr, length, csh, mode):
            dsc = Const(dsc, 32) if isinstance(dsc, int) else dsc
            a24 = Const(addr, 24) if isinstance(addr, int) else addr
            m.d.comb += [tx.start.eq(1), tx.desc.eq(dsc), tx.length.eq(length),
                         tx.csh.eq(csh),
                         tx.addr.eq(Mux(dsc[10:16] == 32, Cat(Const(MBYTE, 8), a24), a24))]
            s.__iadd__([rx_mode.eq(mode), wd.eq(0), nbytes.eq(0), wcnt.eq(0),
                        mismatch.eq(0), acc.eq(0)])

        m.d.comb += [self.tx_addr.eq(wcnt[2:8]), tx.wbyte.eq(self.tx_data.word_select(wcnt[0:2], 8))]
        with m.If(tx.wnext):
            s += wcnt.eq(wcnt + 1)
        with m.If(~tx.idle & (wd != TX_LIMIT)):
            s += wd.eq(wd + 1)
        wd_expired = (wd == TX_LIMIT) & (rx_mode != RX_POLL)

        b = tx.rx_byte
        k = nbytes[0:2]
        new_word = Signal(32)
        # byte k lands in lane k; lane 0 zero-extends, so lanes above k read 0
        m.d.comb += new_word.eq(Mux(k == 0, b, acc))
        with m.Switch(k):
            for lane in range(1, 4):
                with m.Case(lane):
                    m.d.comb += new_word.word_select(lane, 8).eq(b)
        m.d.comb += ref_rp.addr.eq(nbytes[0:6])
        with m.If(tx.rx_valid):
            s += [nbytes.eq(nbytes + 1), acc.eq(new_word)]
            with m.Switch(rx_mode):
                with m.Case(RX_BUF):
                    with m.If(k == 3):
                        m.d.comb += [self.rx_we.eq(1), self.rx_addr.eq(nbytes[2:8]),
                                     self.rx_data.eq(new_word)]
                with m.Case(RX_XIP):
                    with m.If(k == 3):
                        m.d.comb += [self.xip_we.eq(1), self.xip_word.eq(new_word),
                                     self.xip_idx.eq(nbytes[2:6]), self.xip_wid.eq(cur_id)]
                with m.Case(RX_JEDEC):
                    s += res24.eq(Cat(b, res24[0:16]))
                with m.Case(RX_REF):
                    m.d.comb += [ref_wp.en.eq(nbytes < CAL_REF_BYTES), ref_wp.addr.eq(nbytes[0:6]),
                                 ref_wp.data.eq(b)]
                    s += [ref_and.eq(ref_and & b), ref_or.eq(ref_or | b)]
                with m.Case(RX_X4):
                    with m.If((nbytes >= CAL_REF_BYTES) | (b != ref_rp.data)):
                        s += mismatch.eq(1)
                with m.Case(RX_POLL):
                    with m.If(b[0]):
                        s += saw_busy.eq(1)

        # ---- PHASESTEP: low 4 / high 4 cycles (>= 4 VCO cycles each, TN-02200 18.4.1)
        ps_go, ps_busy, ps_cnt = Signal(), Signal(), Signal(3)
        pos = Signal(range(n))
        s += self.phasestep.eq(1)
        with m.If(ps_busy):
            s += [self.phasestep.eq(ps_cnt[2]), ps_cnt.eq(ps_cnt + 1)]
            with m.If(ps_cnt == 7):
                s += ps_busy.eq(0)
        with m.Elif(ps_go):
            s += [ps_busy.eq(1), ps_cnt.eq(0), pos.eq(Mux(pos == n - 1, 0, pos + 1))]

        # ---- calibration scan state
        X1, X4 = 0, 1
        stage, from_cmd = Signal(), Signal()
        lat_lo, lat_hi, step_i = Signal(4), Signal(4), Signal(range(n + 1))
        key, run_s, run_l, best_s, best_l = (Signal(range(CAL_LATS * n + 1)) for _ in range(5))
        best_x, target, lat_off = Signal(), Signal(range(n)), Signal(4)

        def scan_init(lo, hi):
            s.__iadd__([lat_lo.eq(lo), lat_hi.eq(hi), lat.eq(lo), self.xfer.eq(0),
                        step_i.eq(0), key.eq(0), run_l.eq(0), best_l.eq(0), best_s.eq(0),
                        best_x.eq(0)])

        def probe():
            with m.If(stage == X1):
                issue(JEDEC_DESC, 0, 3, csh_r, RX_JEDEC)
            with m.Else():
                issue(XIP_DESC, self.cal_ref_addr, CAL_REF_BYTES, csh_r, RX_X4)

        probe_ok = Mux(stage == X1, res24 == self.jedec_id,
                       ~mismatch & (nbytes == CAL_REF_BYTES))

        # ---- command results
        poll_us, us_div = Signal(24), Signal(range(us_cycles))
        res_out = Signal()

        def complete(code):
            s.__iadd__([self.r_code.eq(code), self.r_poll_us.eq(poll_us),
                        self.r_saw_busy.eq(saw_busy), res_out.eq(1), cmd_pending.eq(0)])

        with m.If(res_out):
            s += res_out.eq(0)
            m.d.comb += self.done.eq(1)

        def cal_finish(valid, x4):
            s.__iadd__([self.cal_valid.eq(valid), self.cal_x4.eq(x4), self.ready.eq(1),
                        self.cal_step.eq(pos), self.cal_lat.eq(lat),
                        self.cal_xfer.eq(self.xfer)])
            with m.If(from_cmd):
                complete(Mux(valid, CODE_OK, CODE_CAL_NONE))

        def end(code):
            """End the command now: core reset, result, back to PICK."""
            m.d.comb += tx_rst.eq(1)
            complete(code)
            m.next = "PICK"

        with m.FSM() as fsm:
            with m.State("PICK"):
                with m.If(self.abort & cmd_pending):
                    complete(CODE_ABORT)
                with m.Elif(cal_pending):
                    s += [cal_pending.eq(0), from_cmd.eq(0), stage.eq(X1)]
                    scan_init(0, CAL_LATS - 1)
                    m.next = "CAL_PROBE"
                with m.Elif(cmd_pending & (prefer_cmd | ~xip_pending)):
                    s += [prefer_cmd.eq(0), saw_busy.eq(0), poll_us.eq(0)]
                    with m.If(self.op == OP_CALIBRATE):
                        s += [from_cmd.eq(1), stage.eq(X1), self.cal_valid.eq(0)]
                        scan_init(0, CAL_LATS - 1)
                        m.next = "CAL_PROBE"
                    with m.Elif(self.wren):
                        m.next = "CMD_WREN"
                    with m.Else():
                        m.next = "CMD_OP"
                with m.Elif(xip_pending):
                    s += [prefer_cmd.eq(1), xip_pending.eq(0), cur_id.eq(self.xip_id)]
                    m.next = "XIP"

            with m.State("XIP"):
                with m.If(tx.idle):
                    issue(XIP_DESC, Cat(Const(0, 6), self.xip_line, Const(0, 2)), LINE_BYTES,
                          csh_r, RX_XIP)
                    m.next = "XIP_WAIT"
            with m.State("XIP_WAIT"):
                with m.If(tx.done | wd_expired):
                    m.d.comb += tx_rst.eq(wd_expired)
                    m.next = "PICK"

            # ---- command; every state ends on abort
            with m.State("CMD_WREN"):
                with m.If(self.abort):
                    end(CODE_ABORT)
                with m.Elif(tx.idle):
                    issue(WREN_DESC, 0, 0, csh_w, RX_NONE)
                    m.next = "CMD_WREN_WAIT"
            with m.State("CMD_WREN_WAIT"):
                with m.If(self.abort):
                    end(CODE_ABORT)
                with m.Elif(tx.done):
                    m.next = "CMD_OP"
                with m.Elif(wd_expired):
                    end(CODE_WDOG)
            with m.State("CMD_OP"):
                with m.If(self.abort):
                    end(CODE_ABORT)
                with m.Elif(tx.idle):
                    issue(self.desc, self.addr, self.len,
                          Mux(self.wren | self.wait, csh_w, csh_r), RX_BUF)
                    m.next = "CMD_OP_WAIT"
            with m.State("CMD_OP_WAIT"):
                with m.If(self.abort):
                    end(CODE_ABORT)
                with m.Elif(tx.done):
                    with m.If(k != 0):
                        m.d.comb += [self.rx_we.eq(1), self.rx_addr.eq(nbytes[2:8]),
                                     self.rx_data.eq(acc)]
                    with m.If(self.wait):
                        m.next = "CMD_POLL"
                    with m.Else():
                        complete(CODE_OK)
                        m.next = "PICK"
                with m.Elif(wd_expired):
                    end(CODE_WDOG)
            with m.State("CMD_POLL"):
                with m.If(self.abort):
                    end(CODE_ABORT)
                with m.Elif(tx.idle):
                    issue(POLL_DESC, 0, 0xFFFFFFFF, csh_w, RX_POLL)
                    s += us_div.eq(0)
                    m.next = "CMD_POLL_WAIT"
            with m.State("CMD_POLL_WAIT"):
                s += us_div.eq(Mux(us_div == us_cycles - 1, 0, us_div + 1))
                with m.If(us_div == us_cycles - 1):
                    s += poll_us.eq(poll_us + 1)
                with m.If(self.abort):
                    end(CODE_ABORT)
                with m.Elif(tx.done):
                    complete(CODE_OK)
                    m.next = "PICK"
                with m.Elif(poll_us >= self.wait_us):
                    end(CODE_WDOG)

            # ---- calibration
            with m.State("CAL_PROBE"):
                with m.If(tx.idle):
                    probe()
                    m.next = "CAL_WAIT"
            with m.State("CAL_WAIT"):
                with m.If(tx.done | wd_expired):
                    m.d.comb += tx_rst.eq(wd_expired)
                    with m.If(probe_ok & ~wd_expired):
                        s += run_l.eq(run_l + 1)
                        with m.If(run_l == 0):
                            s += run_s.eq(key)
                        with m.If(run_l + 1 > best_l):
                            s += [best_l.eq(run_l + 1), best_x.eq(self.xfer),
                                  best_s.eq(Mux(run_l == 0, key, run_s))]
                    with m.Else():
                        s += run_l.eq(0)
                    m.d.comb += ps_go.eq(1)
                    s += [key.eq(key + 1), step_i.eq(step_i + 1)]
                    m.next = "CAL_STEP"
            with m.State("CAL_STEP"):
                # ps_busy rose on the edge that left CAL_WAIT
                with m.If(~ps_busy):
                    with m.If(step_i == n):
                        s += step_i.eq(0)
                        with m.If(lat == lat_hi):
                            s += [lat.eq(lat_lo), run_l.eq(0), key.eq(0)]
                            with m.If(self.xfer):
                                m.next = "CAL_PICK"
                            with m.Else():
                                s += self.xfer.eq(1)
                                m.next = "CAL_PROBE"
                        with m.Else():
                            s += lat.eq(lat + 1)
                            m.next = "CAL_PROBE"
                    with m.Else():
                        m.next = "CAL_PROBE"
            with m.State("CAL_PICK"):
                with m.If(best_l < CAL_MIN_WIDTH):
                    with m.If(stage == X1):
                        s += self.width_x1.eq(best_l)
                    with m.Else():
                        s += self.width_x4.eq(best_l)
                    cal_finish(0, 0)
                    m.next = "PICK"
                with m.Else():
                    s += [key.eq(best_s + (best_l >> 1)), lat_off.eq(0), self.xfer.eq(best_x)]
                    m.next = "CAL_SPLIT"
            with m.State("CAL_SPLIT"):
                with m.If(key >= n):
                    s += [key.eq(key - n), lat_off.eq(lat_off + 1)]
                with m.Else():
                    s += [target.eq(key), lat.eq(lat_lo + lat_off)]
                    m.next = "CAL_PLACE"
            with m.State("CAL_PLACE"):
                with m.If(~ps_busy):
                    with m.If(pos != target):
                        m.d.comb += ps_go.eq(1)
                    with m.Elif(tx.idle):
                        probe()
                        m.next = "CAL_VERIFY"
            with m.State("CAL_VERIFY"):
                with m.If(tx.done | wd_expired):
                    m.d.comb += tx_rst.eq(wd_expired)
                    with m.If(~probe_ok | wd_expired):
                        cal_finish(0, 0)
                        m.next = "PICK"
                    with m.Elif(stage == X1):
                        s += [self.width_x1.eq(best_l), ref_and.eq(0xFF), ref_or.eq(0)]
                        m.next = "CAL_REF"
                    with m.Else():
                        s += self.width_x4.eq(best_l)
                        cal_finish(1, 1)
                        m.next = "PICK"
            with m.State("CAL_REF"):
                with m.If(tx.idle):
                    issue(REF_DESC, self.cal_ref_addr, CAL_REF_BYTES, csh_r, RX_REF)
                    m.next = "CAL_REF_WAIT"
            with m.State("CAL_REF_WAIT"):
                with m.If(tx.done | wd_expired):
                    m.d.comb += tx_rst.eq(wd_expired)
                    # A blank or constant reference cannot tell lanes apart: keep x1.
                    with m.If(wd_expired | (ref_and != 0) | (ref_or != 0xFF)):
                        s += self.width_x4.eq(0)
                        cal_finish(1, 0)
                        m.next = "PICK"
                    with m.Else():
                        s += stage.eq(X4)
                        scan_init(Mux(lat == 0, 0, lat - 1),
                                  Mux(lat == LAT_MAX, LAT_MAX, lat + 1))
                        m.next = "CAL_PROBE"

        m.d.comb += [self.busy.eq(~fsm.ongoing("PICK") | cmd_pending | cal_pending)]
        return m


class Flash(wiring.Component):
    """Registers `bus`, XIP `xip` (read-only), command data `buf` (all Wishbone), `irq`,
    `boot_ready`.

    - `bus`: 32-bit word registers, `REGS`; plain Wishbone, not amaranth-soc CSRs (a
      byte-wide CSR multiplexer cost more cells than the registers, #593).
    - `boot_ready` (sync) holds the CPU until the first calibration has finished.
    - `sim=True` drops pins, DCCA and USRMCLK: the bench drives `fsck`/`fcap`/`fcapn`
      and models the part on the `pad_*` signals.
    """

    def __init__(self, *, sck_mhz=SCK_MHZ, sync_mhz=60.0, n_steps=32, sim=False,
                 jedec_id=JEDEC_ID, cal_ref_addr=CAL_REF_ADDR):
        self.sck_mhz, self.sync_mhz, self.n_steps, self.sim = sck_mhz, sync_mhz, n_steps, sim
        self.jedec_id, self.cal_ref_addr = jedec_id, cal_ref_addr
        self.fill_limit = -(-5 * xip_worst_sync_cycles(sync_mhz=sync_mhz, sck_mhz=sck_mhz) // 4)
        # clocks.SocClocks: lock, PHASESTEP out, MCLK in
        self.locked = Signal()
        self.phasestep = Signal(init=1)
        self.mclk = Signal()
        # pads, registered in fsck by the core PHY
        self.pad_o, self.pad_oe, self.pad_i = Signal(4), Signal(4), Signal(4)
        self.pad_cs, self.pad_ck_en = Signal(), Signal()

        # Block RAM, not LUT RAM: logic cells are the scarce resource (#593).
        ebr = {"ram_style": "block"}
        self._line = memory.Memory(shape=32, depth=LINE_BYTES // 4, init=[], attrs=ebr)
        self._txbuf = memory.Memory(shape=32, depth=BUF_BYTES // 4, init=[], attrs=ebr)
        self._rxbuf = memory.Memory(shape=32, depth=BUF_BYTES // 4, init=[], attrs=ebr)

        super().__init__({
            "bus":  In(wishbone.Signature(addr_width=4, data_width=32, granularity=8)),
            "xip":  In(wishbone.Signature(addr_width=20, data_width=32, granularity=8,
                                          features={"err"})),
            "buf":  In(wishbone.Signature(addr_width=6, data_width=32, granularity=8)),
            "irq":  Out(1),
            "boot_ready": Out(1),
        })
        rmap = MemoryMap(addr_width=6, data_width=8)
        rmap.add_resource(name=("regs",), size=REG_BYTES, resource=self._line)
        self.bus.memory_map = rmap
        xmap = MemoryMap(addr_width=22, data_width=8)
        xmap.add_resource(name=("flash",), size=FLASH_BYTES, resource=self._line)
        self.xip.memory_map = xmap
        bmap = MemoryMap(addr_width=8, data_width=8)
        bmap.add_resource(name=("buf",), size=BUF_BYTES, resource=self._txbuf)
        self.buf.memory_map = bmap

    def elaborate(self, platform):
        m = Module()

        eng = _Engine(n_steps=self.n_steps, sck_mhz=self.sck_mhz, jedec_id=self.jedec_id,
                      cal_ref_addr=self.cal_ref_addr)
        m.submodules.engine = DomainRenamer("fsck")(eng)
        phy = core.FlashPHY(eng.pins, pad_o=self.pad_o, pad_oe=self.pad_oe, pad_i=self.pad_i,
                            pad_cs=self.pad_cs, pad_ck_en=self.pad_ck_en)
        m.submodules.phy = DomainRenamer({"sync": "fsck", "cap": "fcap", "capn": "fcapn"})(phy)
        m.d.comb += [phy.xfer.eq(eng.xfer), self.phasestep.eq(eng.phasestep)]

        if not self.sim:
            pads = platform.request("qspi_flash", 0, dir="-")
            for i in range(4):
                buf = m.submodules[f"dq{i}"] = io.Buffer("io", pads.dq[i])
                m.d.comb += [buf.o.eq(self.pad_o[i]), buf.oe.eq(self.pad_oe[i]),
                             self.pad_i[i].eq(buf.i)]
            m.submodules.cs = cs = io.Buffer("o", pads.cs)
            m.d.comb += cs.o.eq(self.pad_cs)
            # CE changes on fsck edges = MCLK falling, so the gated clock has no runt.
            gated = Signal()
            m.submodules.mclk_gate = Instance("DCCA", i_CLKI=self.mclk, i_CE=self.pad_ck_en,
                                              o_CLKO=gated)
            m.submodules.usrmclk = Instance("USRMCLK", i_USRMCLKI=gated, i_USRMCLKTS=0)

        # ---- command buffers: CPU side sync, engine side fsck
        for mem, name in ((self._line, "line"), (self._txbuf, "txbuf"), (self._rxbuf, "rxbuf")):
            m.submodules[name] = mem
        tx_wp = self._txbuf.write_port(domain="sync", granularity=8)
        tx_rp = self._txbuf.read_port(domain="fsck")
        rx_wp = self._rxbuf.write_port(domain="fsck")
        rx_rp = self._rxbuf.read_port(domain="sync")
        bw = self.buf
        m.d.comb += [tx_wp.addr.eq(bw.adr), tx_wp.data.eq(bw.dat_w),
                     tx_wp.en.eq(Mux(bw.cyc & bw.stb & bw.we & ~bw.ack, bw.sel, 0)),
                     rx_rp.addr.eq(bw.adr), bw.dat_r.eq(rx_rp.data),
                     tx_rp.addr.eq(eng.tx_addr), eng.tx_data.eq(tx_rp.data),
                     rx_wp.addr.eq(eng.rx_addr), rx_wp.data.eq(eng.rx_data),
                     rx_wp.en.eq(eng.rx_we)]
        m.d.sync += bw.ack.eq(bw.cyc & bw.stb & ~bw.ack)

        # ---- crossings
        def to_sync(name, sig):
            out = Signal.like(sig, name=f"{name}_s")
            m.submodules[f"{name}_ff"] = FFSynchronizer(sig, out)
            return out

        def toggle_in(name, pulse_sync):
            """sync pulse -> fsck pulse, via a toggle (pulses >= 4 sync cycles apart)."""
            t, h, p = Signal(name=f"{name}_t"), Signal(name=f"{name}_h"), Signal(name=f"{name}_p")
            with m.If(pulse_sync):
                m.d.sync += t.eq(~t)
            m.submodules[f"{name}_ff"] = FFSynchronizer(t, h, o_domain="fsck")
            m.d.fsck += p.eq(h)
            return h ^ p

        def toggle_out(name, pulse_fsck):
            t, h, p = Signal(name=f"{name}_t"), Signal(name=f"{name}_h"), Signal(name=f"{name}_p")
            with m.If(pulse_fsck):
                m.d.fsck += t.eq(~t)
            m.submodules[f"{name}_ff"] = FFSynchronizer(t, h)
            m.d.sync += p.eq(h)
            return h ^ p

        ready_s, cal_valid_s, cal_x4_s = (to_sync("ready", eng.ready),
                                          to_sync("calv", eng.cal_valid),
                                          to_sync("calx4", eng.cal_x4))
        locked_s = to_sync("locked", self.locked)
        cal_s = to_sync("cal", Cat(eng.cal_step, eng.cal_lat, eng.cal_xfer, Const(0, 3),
                                   eng.width_x1, eng.width_x4))
        done_s = toggle_out("done", eng.done)
        m.d.comb += self.boot_ready.eq(ready_s)

        # ---- registers
        rb = self.bus
        desc_r, addr_r, wait_r = Signal(32), Signal(24), Signal(24)
        len_r, wren_r, waitf_r = Signal(9), Signal(), Signal()
        irq_en, events, errors = Signal(2), Signal(2), Signal(N_ERR)
        pending, w_class = Signal(), Signal()
        code_r, op_r = Signal(4), Signal(4)
        saw_busy_r, poll_us_r = Signal(), Signal(24)
        ev_set, err_set = Signal(2), Signal(N_ERR)
        m.d.comb += [ev_set[1].eq(err_set.any()), self.irq.eq((events & irq_en).any())]

        wr = rb.cyc & rb.stb & rb.we & ~rb.ack
        m.d.sync += rb.ack.eq(rb.cyc & rb.stb & ~rb.ack)
        status = Cat(pending, ready_s & locked_s, locked_s, cal_valid_s, cal_x4_s, saw_busy_r,
                     Const(0, 2), code_r, op_r, events, Const(0, 6), errors)
        with m.Switch(rb.adr):
            for word, value in ((REG_STATUS, status), (REG_IRQ_EN, irq_en), (REG_DESC, desc_r),
                                (REG_ADDR, addr_r), (REG_LEN, Cat(len_r, Const(0, 7), wren_r,
                                                                  waitf_r)),
                                (REG_WAIT_US, wait_r), (REG_POLL_US, poll_us_r),
                                (REG_CAL, cal_s),
                                (REG_CAL_US, cal_us(self.n_steps, self.sck_mhz))):
                with m.Case(word):
                    m.d.sync += rb.dat_r.eq(value)
            with m.Default():
                m.d.sync += rb.dat_r.eq(0)
        d = rb.dat_w
        with m.If(wr):
            with m.Switch(rb.adr):
                with m.Case(REG_IRQ_EN):
                    m.d.sync += irq_en.eq(d[0:2])
                with m.Case(REG_DESC):
                    m.d.sync += desc_r.eq(d)
                with m.Case(REG_ADDR):
                    m.d.sync += addr_r.eq(d[0:24])
                with m.Case(REG_LEN):
                    m.d.sync += [len_r.eq(d[0:9]), wren_r.eq(d[16]), waitf_r.eq(d[17])]
                with m.Case(REG_WAIT_US):
                    m.d.sync += wait_r.eq(d[0:24])
        clear = wr & (rb.adr == REG_CTRL)
        m.d.sync += [events.eq((events & ~Mux(clear, d[16:18], 0)) | ev_set),
                     errors.eq((errors & ~Mux(clear, d[24:24 + N_ERR], 0)) | err_set)]

        # ---- commands
        start = Signal()
        m.d.comb += [eng.start.eq(toggle_in("start", start)),
                     eng.abort.eq(toggle_in("abort", clear & d[0]))]

        def finish(code, op):
            m.d.sync += [code_r.eq(code), op_r.eq(op), pending.eq(0)]
            m.d.comb += ev_set[0].eq(1)
            with m.If(code != CODE_OK):
                m.d.comb += err_set[ERR_OP].eq(1)

        def refuse(code, op):
            m.d.sync += [code_r.eq(code), op_r.eq(op)]
            m.d.comb += err_set[ERR_OP].eq(1)
            with m.If(~pending):
                m.d.comb += ev_set[0].eq(1)

        w = d[0:4]
        arg_bad = (((w != OP_RUN) & (w != OP_CALIBRATE)) | (len_r > BUF_BYTES)
                   | ((w == OP_RUN) & waitf_r & (wait_r == 0)))
        with m.If(wr & (rb.adr == REG_CMD)):
            with m.If(pending):
                refuse(CODE_BUSY, w)
            with m.Elif(~(ready_s & locked_s)):
                refuse(CODE_NOT_READY, w)
            with m.Elif(arg_bad):
                refuse(CODE_ARG, w)
            with m.Else():
                m.d.sync += [eng.op.eq(w), eng.desc.eq(desc_r & DESC_MASK), eng.addr.eq(addr_r),
                             eng.len.eq(len_r), eng.wren.eq(wren_r), eng.wait.eq(waitf_r),
                             eng.wait_us.eq(wait_r), pending.eq(1), op_r.eq(w),
                             code_r.eq(CODE_OK),
                             w_class.eq((w == OP_CALIBRATE) | wren_r | waitf_r)]
                m.d.comb += start.eq(1)

        # The engine bounds every wait it has, so a done always comes; the CPU's own
        # bound (flash.rs) covers an fsck clock that stops.
        with m.If(done_s):
            m.d.sync += [saw_busy_r.eq(eng.r_saw_busy), poll_us_r.eq(eng.r_poll_us)]
            with m.If(pending):
                finish(eng.r_code, op_r)
            for code, bit in ((CODE_WDOG, ERR_WDOG), (CODE_CAL_NONE, ERR_CAL)):
                with m.If(eng.r_code == code):
                    m.d.comb += err_set[bit].eq(1)

        # ---- calibration result and lock loss as errors
        ready_prev, locked_prev = Signal(), Signal()
        m.d.sync += [ready_prev.eq(ready_s), locked_prev.eq(locked_s)]
        with m.If(ready_s & ~ready_prev & ~cal_valid_s):
            m.d.comb += err_set[ERR_CAL].eq(1)
        with m.If(locked_prev & ~locked_s):
            m.d.comb += err_set[ERR_PLL_LOST].eq(1)

        # ---- XIP: line buffer, fill from the engine, ack per word
        fifo = m.submodules.xip_fifo = AsyncFIFO(width=38, depth=LINE_BYTES // 4,
                                                 w_domain="fsck", r_domain="sync")
        m.d.comb += [fifo.w_en.eq(eng.xip_we),
                     fifo.w_data.eq(Cat(eng.xip_word, eng.xip_idx, eng.xip_wid))]
        line_wp = self._line.write_port(domain="sync")
        line_rp = self._line.read_port(domain="sync")
        xb = self.xip
        tag, valid, filling, fill_id = Signal(16), Signal(16), Signal(), Signal(2)
        inv_pending, fill_age = Signal(), Signal(range(self.fill_limit + 1))
        xip_go = Signal()
        req_line, req_word = xb.adr[4:20], xb.adr[0:4]
        f_word, f_idx, f_id = fifo.r_data[0:32], fifo.r_data[32:36], fifo.r_data[36:38]
        m.d.comb += [fifo.r_en.eq(1), line_rp.addr.eq(req_word),
                     eng.xip_line.eq(tag), eng.xip_id.eq(fill_id),
                     eng.xip_start.eq(toggle_in("xip", xip_go))]
        landed = Signal(16)
        m.d.comb += landed.eq(valid | (Const(1, 16) << f_idx))
        with m.If(fifo.r_rdy & filling & (f_id == fill_id)):
            m.d.comb += [line_wp.en.eq(1), line_wp.addr.eq(f_idx), line_wp.data.eq(f_word)]
            m.d.sync += valid.eq(landed)
            with m.If(landed == 0xFFFF):
                m.d.sync += filling.eq(0)
        with m.If(filling & (fill_age != self.fill_limit)):
            m.d.sync += fill_age.eq(fill_age + 1)

        # A command's done invalidates the line (erase/program); deferred past a fill.
        with m.If(done_s):
            m.d.sync += inv_pending.eq(1)
        with m.If(inv_pending & ~filling & ~done_s):
            m.d.sync += [valid.eq(0), inv_pending.eq(0)]

        blocked = ~cal_valid_s | (pending & w_class)
        asking = xb.cyc & xb.stb & ~xb.ack & ~xb.err
        hit = (tag == req_line) & valid.bit_select(req_word, 1) & ~inv_pending
        # the read port answers the cycle after the address, with the registered ack
        m.d.sync += [xb.ack.eq(0), xb.err.eq(0)]
        m.d.comb += xb.dat_r.eq(line_rp.data)
        with m.If(asking):
            with m.If(xb.we):
                m.d.sync += xb.err.eq(1)
                m.d.comb += err_set[ERR_XIP_WRITE].eq(1)
            with m.Elif(blocked):
                m.d.sync += xb.err.eq(1)
                m.d.comb += err_set[ERR_XIP_BLOCKED].eq(1)
            with m.Elif(hit):
                m.d.sync += xb.ack.eq(1)
            with m.Elif(filling & (fill_age == self.fill_limit)):
                m.d.sync += [xb.err.eq(1), filling.eq(0), valid.eq(0)]
                m.d.comb += err_set[ERR_XIP_FILL].eq(1)
            with m.Elif(~filling & ~inv_pending):
                m.d.sync += [tag.eq(req_line), valid.eq(0), filling.eq(1), fill_age.eq(0),
                             fill_id.eq(fill_id + 1)]
                m.d.comb += xip_go.eq(1)

        return m
