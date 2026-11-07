#
# Our USB 2.0 device engine, EP0 only: speed negotiation, control endpoint, CPU command port.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
`Ep0Device`: a USB device that owns the wire and EP0; everything above SETUP is the CPU's. #612 S2/S3.

- Wire: the L1 codecs of this package (`token`, `data`, `handshake`, `timer`) on one UTMI link side.
- Speed: SE0 reset detect, device chirp K, three host K-J pairs -> HS, else FS.
- EP0 (max packet 64): SETUP data is ACKed always; IN/OUT/status are NAKed until the CPU answers.
  Other endpoints get no response.
- Not here: SOF/suspend/resume, PING, EP1+ (bulk FIFOs), ISO.

CPU side (all in the `usb` domain; a CSR wrapper crosses to `sync`):
- `seq` (8 bit) increments on every SETUP token and every bus reset. Every command carries `cmd_seq`;
  a command whose seq is not the current one is dropped and counted in `stale` (new SETUP aborts
  the old transfer, USB 2.0 8.5.3).
- `setup_valid`/`setup_data` (LSB = bmRequestType): the 8 SETUP bytes, valid from the ACK to the next SETUP token.
- IN data stage: write up to 64 bytes through `i_addr`/`i_data`/`i_we`, then `CMD_IN` with `cmd_len`
  and `cmd_last`. One packet per command; a ZLP is `cmd_len=0`. After the last packet is ACKed the
  status OUT is accepted by the engine alone.
- OUT data stage: `o_valid` high = a packet is in the buffer (`o_len` bytes at `o_addr`/`o_data`,
  async read); `CMD_OACK` frees it. A new OUT packet is NAKed while it is held.
- Status IN (no-data, or after an OUT data stage): `CMD_STATUS` releases the zero-length DATA1;
  with `cmd_addr_valid` the address is applied after the host ACKs it (SET_ADDRESS, 9.2.6.3).
- `in_busy`: an IN packet is armed and not yet ACKed by the host.
- `CMD_STALL`: STALL every EP0 token until the next SETUP.
"""

from enum import IntEnum

from amaranth import Array, Cat, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from .data import DataGenerator, DataReceiver
from .handshake import HandshakeDetector, HandshakeGenerator
from .pid import PID, USBSpeed
from .timer import InterpacketTimer
from .token import TokenDetector
from .utmi import UTMIRxSignature, UTMISignature, UTMITxMux, UTMITxSignature

__all__ = ["CMD_IN", "CMD_OACK", "CMD_STALL", "CMD_STATUS", "Ep0Device", "Stage"]

CMD_IN, CMD_STATUS, CMD_STALL, CMD_OACK = 1, 2, 3, 4
EP0_MAXP = 64

K_STATE, J_STATE = 0b10, 0b01       # UTMI line_state, as the model wire and the PHYs drive it


class Stage(IntEnum):
    IDLE = 0
    SETUP = 1           # SETUP token seen, data not yet complete
    DATA_IN = 2
    DATA_OUT = 3
    STATUS_IN = 4       # no-data request: host's IN is answered with a ZLP once released
    STATUS_OUT = 5      # last IN data packet ACKed: host ends with a zero-length OUT


class Ep0Device(wiring.Component):
    # Cycle counts at 60 MHz; the simulations divide them (gateware/probes/usb_host/model.py).
    CHIRP_K_CYCLES = 120_000            # 2.0 ms of chirp K, spec 1.0-7.0 ms (7.1.7.5)
    CHIRP_WAIT_CYCLES = 150_000         # 2.5 ms for three host K-J pairs, then FS (7.1.7.5)
    HS_RESET_CYCLES = 180_000           # 3 ms of SE0 in HS = reset (7.1.7.5)
    FS_RESET_CYCLES = 180               # 3 us of SE0 in FS (7.1.7.5: 2.5 us)
    ACK_WAIT_CYCLES = 100               # host ACK after a data packet: 92 clocks max (timer.HS_COUNTS)

    utmi: Out(UTMISignature())

    speed:      Out(2)
    reset_seen: Out(1)
    reset_ack:  In(1)
    address:    Out(7)

    seq:        Out(8)
    stage:      Out(3)
    stale:      Out(8)
    setup_valid: Out(1)
    setup_data: Out(64)
    in_busy:    Out(1)

    i_addr: In(6)
    i_data: In(8)
    i_we:   In(1)
    o_addr: In(6)
    o_data: Out(8)
    o_len:  Out(7)
    o_valid: Out(1)

    cmd_valid:      In(1)
    cmd:            In(3)
    cmd_seq:        In(8)
    cmd_len:        In(7)
    cmd_last:       In(1)
    cmd_addr:       In(7)
    cmd_addr_valid: In(1)

    def elaborate(self, platform):
        m = Module()
        u = self.utmi

        rx = UTMIRxSignature().create()
        m.d.comb += [rx.data.eq(u.rx_data), rx.valid.eq(u.rx_valid), rx.active.eq(u.rx_active)]

        m.submodules.tokens = tokens = TokenDetector()
        m.submodules.data_rx = data_rx = DataReceiver()
        m.submodules.hs_det = hs_det = HandshakeDetector()
        m.submodules.data_tx = data_tx = DataGenerator()
        m.submodules.hs_tx = hs_tx = HandshakeGenerator()
        m.submodules.timer = timer = InterpacketTimer()
        m.submodules.txmux = txmux = UTMITxMux()
        timer.add_interface(data_rx.timer)
        chirp_tx = UTMITxSignature().create()
        for tx in (data_tx.tx, hs_tx.tx, chirp_tx):
            txmux.add_input(tx)
        for sink in (tokens.rx, data_rx.rx, hs_det.rx):
            m.d.comb += [sink.data.eq(rx.data), sink.valid.eq(rx.valid), sink.active.eq(rx.active)]
        m.d.comb += [
            u.tx_data.eq(txmux.output.data), u.tx_valid.eq(txmux.output.valid),
            txmux.output.ready.eq(u.tx_ready),
            timer.speed.eq(self.speed),
        ]

        ibuf = Memory(shape=8, depth=EP0_MAXP, init=[])
        obuf = Memory(shape=8, depth=EP0_MAXP, init=[])
        m.submodules.ibuf, m.submodules.obuf = ibuf, obuf
        i_wr, i_rd = ibuf.write_port(domain="usb"), ibuf.read_port(domain="comb")
        o_wr, o_rd = obuf.write_port(domain="usb"), obuf.read_port(domain="comb")
        m.d.comb += [i_wr.addr.eq(self.i_addr), i_wr.data.eq(self.i_data), i_wr.en.eq(self.i_we),
                     o_rd.addr.eq(self.o_addr), self.o_data.eq(o_rd.data)]

        # ---- reset and speed ----
        se0_count = Signal(18)
        timer_count = Signal(18)
        pairs = Signal(2)
        seen_k = Signal()
        bus_reset = Signal()
        quiet = ~u.rx_active & ~u.tx_valid
        reset_limit = Signal(18)
        with m.If(self.speed == USBSpeed.HIGH):
            m.d.comb += reset_limit.eq(self.HS_RESET_CYCLES)
        with m.Else():
            m.d.comb += reset_limit.eq(self.FS_RESET_CYCLES)

        m.d.comb += [u.xcvr_select.eq(USBSpeed.FULL), u.term_select.eq(1), u.op_mode.eq(0)]
        with m.FSM(domain="usb", name="link"):
            with m.State("FS"):
                m.d.usb += self.speed.eq(USBSpeed.FULL)
                with m.If(quiet & (u.line_state == 0)):
                    m.d.usb += se0_count.eq(se0_count + 1)
                    with m.If(se0_count >= reset_limit):
                        m.d.comb += bus_reset.eq(1)
                        m.d.usb += [se0_count.eq(0), timer_count.eq(0)]
                        m.next = "CHIRP_K"
                with m.Else():
                    m.d.usb += se0_count.eq(0)
            with m.State("HS"):
                m.d.usb += self.speed.eq(USBSpeed.HIGH)
                m.d.comb += [u.xcvr_select.eq(USBSpeed.HIGH), u.term_select.eq(0)]
                with m.If(quiet & (u.line_state == 0)):
                    m.d.usb += se0_count.eq(se0_count + 1)
                    with m.If(se0_count >= reset_limit):
                        m.d.comb += bus_reset.eq(1)
                        m.d.usb += [se0_count.eq(0), timer_count.eq(0)]
                        m.next = "CHIRP_K"
                with m.Else():
                    m.d.usb += se0_count.eq(0)
            with m.State("CHIRP_K"):
                m.d.comb += [u.xcvr_select.eq(USBSpeed.HIGH), u.term_select.eq(1), u.op_mode.eq(2),
                             chirp_tx.valid.eq(1), chirp_tx.data.eq(0x00)]
                m.d.usb += timer_count.eq(timer_count + 1)
                with m.If(timer_count >= self.CHIRP_K_CYCLES):
                    m.d.usb += [timer_count.eq(0), pairs.eq(0), seen_k.eq(0)]
                    m.next = "CHIRP_WAIT"
            with m.State("CHIRP_WAIT"):
                m.d.comb += [u.xcvr_select.eq(USBSpeed.HIGH), u.term_select.eq(1), u.op_mode.eq(2)]
                m.d.usb += timer_count.eq(timer_count + 1)
                with m.If(u.line_state == K_STATE):
                    m.d.usb += seen_k.eq(1)
                with m.Elif((u.line_state == J_STATE) & seen_k):
                    m.d.usb += [seen_k.eq(0), pairs.eq(pairs + 1)]
                with m.If(pairs == 3):
                    m.next = "HS"
                with m.Elif(timer_count >= self.CHIRP_WAIT_CYCLES):
                    m.next = "FS"

        # ---- EP0 ----
        dev_addr = self.address
        pend_addr = Signal(7)
        pend_addr_valid = Signal()
        stage = self.stage
        stalled, status_ok, in_armed, ilen, ilast = Signal(), Signal(), Signal(), Signal(7), Signal()
        m.d.comb += self.in_busy.eq(in_armed)
        in_toggle, out_toggle = Signal(), Signal()
        setup_bytes = Array(Signal(8, name=f"setup{i}") for i in range(8))
        m.d.comb += self.setup_data.eq(Cat(*setup_bytes))
        expect = Signal(2)                   # 0 none, 1 SETUP data, 2 OUT data
        rx_idx = Signal(7)
        resp = Signal(2)                     # 0 none, 1 ACK, 2 NAK, 3 STALL
        sending, send_zlp, tx_idx = Signal(), Signal(), Signal(7)
        wait_ack, ack_count = Signal(), Signal(8)

        pkt_pid = data_rx.packet_id
        got_data1 = pkt_pid == PID.DATA1
        m.d.comb += o_wr.addr.eq(rx_idx[0:6]), o_wr.data.eq(data_rx.stream.payload)
        m.d.comb += o_wr.en.eq(data_rx.stream.next & (expect == 2) & (stage == Stage.DATA_OUT)
                               & ~self.o_valid & (rx_idx < EP0_MAXP))

        with m.If(data_rx.stream.next & (rx_idx != 127)):
            m.d.usb += rx_idx.eq(rx_idx + 1)
            with m.If((expect == 1) & (rx_idx < 8)):
                m.d.usb += setup_bytes[rx_idx[0:3]].eq(data_rx.stream.payload)

        def reset_ep0():
            m.d.usb += [stage.eq(Stage.IDLE), stalled.eq(0), status_ok.eq(0), in_armed.eq(0),
                        self.o_valid.eq(0), pend_addr_valid.eq(0), expect.eq(0), sending.eq(0),
                        wait_ack.eq(0), self.setup_valid.eq(0), self.seq.eq(self.seq + 1)]

        # tokens
        with m.If(tokens.new_token & (tokens.addr == dev_addr) & (tokens.endp == 0)):
            with m.If(tokens.pid == PID.SETUP):
                reset_ep0()
                m.d.usb += [stage.eq(Stage.SETUP), expect.eq(1), rx_idx.eq(0)]
            with m.Elif(tokens.pid == PID.OUT):
                m.d.usb += [expect.eq(2), rx_idx.eq(0), wait_ack.eq(0)]
            with m.Elif(tokens.pid == PID.IN):
                m.d.usb += wait_ack.eq(0)
                with m.If(stalled):
                    m.d.comb += hs_tx.issue_stall.eq(1)
                with m.Elif((stage == Stage.DATA_IN) & in_armed):
                    m.d.usb += [sending.eq(1), send_zlp.eq(0), tx_idx.eq(0)]
                with m.Elif(((stage == Stage.STATUS_IN) | (stage == Stage.DATA_OUT)) & status_ok):
                    m.d.usb += [sending.eq(1), send_zlp.eq(1), tx_idx.eq(0)]
                with m.Else():
                    m.d.comb += hs_tx.issue_nak.eq(1)

        # data packets from the host
        with m.If(data_rx.crc_mismatch):
            m.d.usb += expect.eq(0)
        with m.If(data_rx.packet_complete):
            m.d.usb += expect.eq(0)
            with m.If(expect == 1):
                with m.If((rx_idx == 8) & (pkt_pid == PID.DATA0)):
                    wlen = Cat(setup_bytes[6], setup_bytes[7])
                    m.d.usb += [self.setup_valid.eq(1), resp.eq(1), in_toggle.eq(1), out_toggle.eq(1)]
                    with m.If(wlen == 0):
                        m.d.usb += stage.eq(Stage.STATUS_IN)
                    with m.Elif(setup_bytes[0][7]):
                        m.d.usb += stage.eq(Stage.DATA_IN)
                    with m.Else():
                        m.d.usb += stage.eq(Stage.DATA_OUT)
            with m.Elif(expect == 2):
                with m.If(stalled):
                    m.d.usb += resp.eq(3)
                with m.Elif((rx_idx == 0) & ((stage == Stage.DATA_IN) | (stage == Stage.STATUS_OUT))):
                    m.d.usb += [resp.eq(1), stage.eq(Stage.IDLE)]       # host ends the transfer
                with m.Elif(stage == Stage.DATA_OUT):
                    with m.If(self.o_valid | (rx_idx > EP0_MAXP)):
                        m.d.usb += resp.eq(2)
                    with m.Elif(got_data1 == out_toggle):
                        m.d.usb += [self.o_len.eq(rx_idx), self.o_valid.eq(1),
                                    out_toggle.eq(~out_toggle), resp.eq(1)]
                    with m.Else():
                        m.d.usb += resp.eq(1)                            # duplicate: ACK, drop
                with m.Elif(stage == Stage.IDLE):
                    m.d.usb += resp.eq(2)
                with m.Else():
                    m.d.usb += resp.eq(3)
        with m.If(data_rx.ready_for_response):
            m.d.usb += resp.eq(0)
            m.d.comb += [hs_tx.issue_ack.eq(resp == 1), hs_tx.issue_nak.eq(resp == 2),
                         hs_tx.issue_stall.eq(resp == 3)]

        # data to the host
        stream = data_tx.stream
        last_byte = tx_idx == (ilen - 1)
        m.d.comb += [
            i_rd.addr.eq(tx_idx[0:6]),
            stream.payload.eq(i_rd.data),
            stream.valid.eq(sending),
            stream.first.eq(sending & ~send_zlp & (ilen != 0) & (tx_idx == 0)),
            stream.last.eq(sending & (send_zlp | (ilen == 0) | last_byte)),
            data_tx.data_pid.eq(Mux(send_zlp, 1, in_toggle)),
        ]
        zero_len = send_zlp | (ilen == 0)
        with m.If(sending):
            with m.If(zero_len):         # the generator latched the ZLP in the first cycle
                m.d.usb += [sending.eq(0), wait_ack.eq(1), ack_count.eq(0)]
            with m.Elif(stream.ready):
                m.d.usb += tx_idx.eq(tx_idx + 1)
                with m.If(last_byte):
                    m.d.usb += [sending.eq(0), wait_ack.eq(1), ack_count.eq(0)]
        with m.If(wait_ack):
            m.d.usb += ack_count.eq(ack_count + 1)
            with m.If(hs_det.detected.ack):
                m.d.usb += wait_ack.eq(0)
                with m.If(send_zlp):
                    m.d.usb += [stage.eq(Stage.IDLE), status_ok.eq(0)]
                    with m.If(pend_addr_valid):
                        m.d.usb += [dev_addr.eq(pend_addr), pend_addr_valid.eq(0)]
                with m.Else():
                    m.d.usb += [in_armed.eq(0), in_toggle.eq(~in_toggle)]
                    with m.If(ilast):
                        m.d.usb += stage.eq(Stage.STATUS_OUT)
            with m.Elif(ack_count >= self.ACK_WAIT_CYCLES):
                m.d.usb += wait_ack.eq(0)

        # CPU commands
        with m.If(bus_reset):
            reset_ep0()
            m.d.usb += [dev_addr.eq(0), self.reset_seen.eq(1)]
        with m.If(self.reset_ack):
            m.d.usb += self.reset_seen.eq(0)
        with m.If(self.cmd_valid):
            with m.If(self.cmd_seq != self.seq), m.If(self.stale != 255):
                m.d.usb += self.stale.eq(self.stale + 1)
            with m.Else():
                with m.Switch(self.cmd):
                    with m.Case(CMD_IN):
                        with m.If((stage == Stage.DATA_IN) & ~in_armed & ~stalled):
                            m.d.usb += [ilen.eq(self.cmd_len), ilast.eq(self.cmd_last), in_armed.eq(1)]
                    with m.Case(CMD_STATUS):
                        m.d.usb += status_ok.eq(1)
                        with m.If(self.cmd_addr_valid):
                            m.d.usb += [pend_addr.eq(self.cmd_addr), pend_addr_valid.eq(1)]
                    with m.Case(CMD_STALL):
                        m.d.usb += stalled.eq(1)
                    with m.Case(CMD_OACK):
                        m.d.usb += self.o_valid.eq(0)
        return m

