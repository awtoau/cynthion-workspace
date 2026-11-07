#
# Our USB 2.0 device engine, EP0 only: speed negotiation, control endpoint, CPU command port.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
`Ep0Device`: a USB device that owns the wire, EP0 and bulk endpoints EP1-4; the rest is the CPU's. #612.

- Wire: the L1 codecs of this package (`token`, `data`, `handshake`, `timer`) on one UTMI link side.
- Speed: SE0 reset detect, device chirp K, three host K-J pairs -> HS, else FS.
- Link states: HS idle (SE0) / FS idle (J) for `SUSPEND_CYCLES` -> suspend (HS probes with FS
  terminations first, SE0 there = reset); K = resume; `CMD_WAKEUP` drives K; `vbus & connect` low =
  detached (no pull-up, not driving).
- EP0 (max packet 64): SETUP data is ACKed always; IN/OUT/status are NAKed until the CPU answers.
- EP1-4 bulk / interrupt, one FIFO per direction (`bulk.PacketFifo`, `fifo_slots` packets of
  `slot_bytes`): OUT data is committed on a good CRC and the right toggle, then ACK / NYET (HS, last
  slot) / NAK (full) / STALL (halt); a duplicate toggle is ACKed and dropped; PING (HS) ACK / NAK /
  STALL. IN sends the oldest packet with the endpoint toggle; the packet is freed and the toggle
  advances only on the host's ACK. Unconfigured endpoints do not answer.
- Not here: ISO, EP5+.

CPU side (all in the `usb` domain; a CSR wrapper crosses to `sync`):
- `seq` (8 bit) increments on every SETUP token and every bus reset. Every command carries `cmd_seq`;
  a command whose seq is not the current one is dropped and counted in `stale` (new SETUP aborts
  the old transfer, USB 2.0 8.5.3). Endpoint commands follow the same rule.
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
- Endpoint commands select an endpoint with `cmd_ep` (`ep_code`): ENABLE (`cmd_arg` = wMaxPacketSize,
  bit 10 = auto-ZLP; toggle 0, unhalted, FIFO flushed), DISABLE, HALT, CLEAR (unhalt, toggle 0),
  FLUSH (FIFO only). Bus reset and detach disable every endpoint.
- Status: `ep_sel` -> `ep_enabled/halted/toggle/level/maxp`; `n_nak/n_nyet/n_dup/n_crc` (8 bit
  saturating); `frame/uframe/sof_count`; `suspended`; sticky `events` (EV_*, cleared by `ev_clr`).
- Data ports: `ep_in{n}` (producer -> host IN) and `ep_out{n}` (host OUT -> consumer) are
  `bulk.PacketStream`s.
"""

from enum import IntEnum

from amaranth import Array, Cat, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from .bulk import PacketFifo, PacketReader, PacketStream, PacketWriter
from .data import DataGenerator, DataReceiver
from .handshake import HandshakeDetector, HandshakeGenerator
from .pid import PID, USBSpeed
from .timer import InterpacketTimer
from .token import TokenDetector
from .utmi import UTMIRxSignature, UTMISignature, UTMITxMux, UTMITxSignature

__all__ = ["CMD_IN", "CMD_OACK", "CMD_STALL", "CMD_STATUS", "CMD_EP_ENABLE", "CMD_EP_DISABLE", "CMD_EP_HALT",
           "CMD_EP_CLEAR", "CMD_EP_FLUSH", "CMD_WAKEUP", "EV_SUSPEND", "EV_RESUME", "EV_VBUS", "EV_SETUP", "EV_RESET", "EV_SOF", "EV_FIFO", "Ep0Device",
           "Stage", "ep_code"]

CMD_IN, CMD_STATUS, CMD_STALL, CMD_OACK = 1, 2, 3, 4
CMD_EP_ENABLE, CMD_EP_DISABLE, CMD_EP_HALT, CMD_EP_CLEAR, CMD_EP_FLUSH, CMD_WAKEUP = 5, 6, 7, 8, 9, 10
EV_SUSPEND, EV_RESUME, EV_VBUS, EV_SETUP, EV_RESET, EV_SOF, EV_FIFO = 1, 2, 4, 8, 16, 32, 64
EP0_MAXP = 64


def ep_code(number, is_in):
    """`cmd_ep` / `ep_sel` encoding: bit 3 = IN (device to host), [2:0] = endpoint number."""
    return (8 if is_in else 0) | number


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
    HS_RESET_CYCLES = 180_000           # 3 ms of HS inactivity (SE0): reset or suspend (7.1.7.5, 7.1.7.6)
    FS_RESET_CYCLES = 180               # 3 us of SE0 in FS (7.1.7.5: 2.5 us)
    SUSPEND_CYCLES = 180_000            # 3 ms of FS idle (J) = suspend (7.1.7.6)
    PROBE_CYCLES = 6_000                # 100 us on FS terminations before reading SE0 (reset) / J (suspend)
    RESUME_K_CYCLES = 120_000           # 2 ms of device-driven resume K, spec 1-15 ms (7.1.7.7)
    DEBOUNCE_CYCLES = 150               # 2.5 us of K (resume) or of not-K (end of resume)
    ACK_WAIT_CYCLES = 100               # host ACK after a data packet: 92 clocks max (timer.HS_COUNTS)

    utmi: Out(UTMISignature())

    speed:      Out(2)
    reset_seen: Out(1)
    reset_ack:  In(1)
    address:    Out(7)

    vbus:       In(1, init=1)
    connect:    In(1, init=1)
    attached:   Out(1)
    suspended:  Out(1)
    events:     Out(8)
    ev_clr:     In(8)
    ev_mask:    In(8)
    irq:        Out(1)
    frame:      Out(11)
    uframe:     Out(3)
    sof_count:  Out(8)

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
    cmd:            In(4)
    cmd_seq:        In(8)
    cmd_len:        In(7)
    cmd_last:       In(1)
    cmd_addr:       In(7)
    cmd_addr_valid: In(1)
    cmd_ep:         In(4)
    cmd_arg:        In(11)

    ep_sel:      In(4)
    ep_enabled:  Out(1)
    ep_halted:   Out(1)
    ep_toggle:   Out(1)
    ep_level:    Out(4)
    ep_maxp:     Out(10)
    n_nak:       Out(8)
    n_nyet:      Out(8)
    n_dup:       Out(8)
    n_crc:       Out(8)

    ep_in1:  In(PacketStream())
    ep_in2:  In(PacketStream())
    ep_in3:  In(PacketStream())
    ep_in4:  In(PacketStream())
    ep_out1: Out(PacketStream())
    ep_out2: Out(PacketStream())
    ep_out3: Out(PacketStream())
    ep_out4: Out(PacketStream())

    def __init__(self, *, endpoints=4, fifo_slots=2, slot_bytes=512):
        self.endpoints, self.fifo_slots, self.slot_bytes = endpoints, fifo_slots, slot_bytes
        super().__init__()
        self.ep_in = {n: getattr(self, f"ep_in{n}") for n in range(1, 5)}
        self.ep_out = {n: getattr(self, f"ep_out{n}") for n in range(1, 5)}

    def elaborate(self, platform):
        m = Module()
        u = self.utmi
        N = self.endpoints

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

        # ---- bulk endpoint state and FIFOs ----
        en_o, en_i = Signal(4), Signal(4)
        halt_o, halt_i = Signal(4), Signal(4)
        tog_o, tog_i = Signal(4), Signal(4)
        maxp_o = [Signal(10, name=f"maxp_o{k}") for k in range(4)]
        maxp_i = [Signal(10, name=f"maxp_i{k}") for k in range(4)]
        zlp_i = Signal(4)
        fl_o, fl_i = Signal(4), Signal(4)           # one-cycle flush pulses (registered)
        ofifo, ififo = {}, {}
        for k in range(N):
            ofifo[k] = f = PacketFifo(self.fifo_slots, self.slot_bytes)
            m.submodules[f"ofifo{k}"] = f
            m.submodules[f"orx{k}"] = rd = PacketReader(f)
            port = self.ep_out[k + 1]
            m.d.comb += [f.flush.eq(fl_o[k]), rd.flush.eq(fl_o[k]),
                         port.data.eq(rd.s.data), port.valid.eq(rd.s.valid), port.last.eq(rd.s.last),
                         port.zlp.eq(rd.s.zlp), rd.s.ready.eq(port.ready)]
            ififo[k] = g = PacketFifo(self.fifo_slots, self.slot_bytes)
            m.submodules[f"ififo{k}"] = g
            m.submodules[f"itx{k}"] = wr = PacketWriter(g)
            port = self.ep_in[k + 1]
            m.d.comb += [g.flush.eq(fl_i[k]), wr.flush.eq(fl_i[k]), wr.maxp.eq(maxp_i[k]),
                         wr.auto_zlp.eq(zlp_i[k]),
                         wr.s.data.eq(port.data), wr.s.valid.eq(port.valid & en_i[k] & ~fl_i[k]), wr.s.last.eq(port.last),
                         wr.s.zlp.eq(port.zlp), port.ready.eq(wr.s.ready & en_i[k] & ~fl_i[k])]
        b_ep = Signal(2)
        b_sending, b_wait, b_space = Signal(), Signal(), Signal()
        b_idx, b_len, b_ack_count = Signal(10), Signal(10), Signal(8)
        b_raddr = Signal(10)
        for k in range(N):
            m.d.comb += ififo[k].r_idx.eq(b_raddr)

        # ---- reset and speed ----
        se0_count = Signal(18)
        idle_count = Signal(18)
        timer_count = Signal(18)
        k_count = Signal(8)
        pairs = Signal(2)
        seen_k = Signal()
        was_hs = Signal()
        wake_req = Signal()
        bus_reset = Signal()
        ev_set = Signal(8)
        quiet = ~u.rx_active & ~u.tx_valid
        reset_limit = Signal(18)
        with m.If(self.speed == USBSpeed.HIGH):
            m.d.comb += reset_limit.eq(self.HS_RESET_CYCLES)
        with m.Else():
            m.d.comb += reset_limit.eq(self.FS_RESET_CYCLES)

        attached = self.vbus & self.connect
        att_prev = Signal(init=1)
        lost, gained = att_prev & ~attached, ~att_prev & attached
        m.d.usb += att_prev.eq(attached)
        m.d.comb += [self.attached.eq(attached), u.xcvr_select.eq(USBSpeed.FULL), u.term_select.eq(1),
                     u.op_mode.eq(0)]
        with m.If(lost | gained):
            m.d.comb += ev_set[2].eq(1)

        def to_suspend(hs):
            m.d.usb += [self.suspended.eq(1), was_hs.eq(hs), k_count.eq(0), se0_count.eq(0)]
            m.d.comb += ev_set[0].eq(1)
            m.next = "SUSPEND"

        def start_reset():
            m.d.comb += bus_reset.eq(1)
            m.d.usb += [se0_count.eq(0), timer_count.eq(0), idle_count.eq(0)]
            m.next = "CHIRP_K"

        with m.FSM(domain="usb", name="link"):
            with m.State("FS"):
                m.d.usb += self.speed.eq(USBSpeed.FULL)
                with m.If(quiet & (u.line_state == 0)):
                    m.d.usb += se0_count.eq(se0_count + 1)
                    with m.If(se0_count >= reset_limit):
                        start_reset()
                with m.Else():
                    m.d.usb += se0_count.eq(0)
                with m.If(quiet & (u.line_state == J_STATE)):
                    m.d.usb += idle_count.eq(idle_count + 1)
                    with m.If(idle_count >= self.SUSPEND_CYCLES):
                        to_suspend(0)
                with m.Else():
                    m.d.usb += idle_count.eq(0)
                with m.If(~attached):
                    m.next = "DETACHED"
            with m.State("HS"):
                m.d.usb += self.speed.eq(USBSpeed.HIGH)
                m.d.comb += [u.xcvr_select.eq(USBSpeed.HIGH), u.term_select.eq(0)]
                with m.If(quiet & (u.line_state == 0)):
                    m.d.usb += se0_count.eq(se0_count + 1)
                    with m.If(se0_count >= reset_limit):
                        m.d.usb += [se0_count.eq(0), timer_count.eq(0)]
                        m.next = "HS_PROBE"
                with m.Else():
                    m.d.usb += se0_count.eq(0)
                with m.If(~attached):
                    m.next = "DETACHED"
            with m.State("HS_PROBE"):
                m.d.usb += timer_count.eq(timer_count + 1)
                with m.If(timer_count >= self.PROBE_CYCLES):
                    with m.If(u.line_state == 0):
                        start_reset()
                    with m.Else():
                        to_suspend(1)
                with m.If(~attached):
                    m.next = "DETACHED"
            with m.State("SUSPEND"):
                with m.If(u.line_state == K_STATE):
                    m.d.usb += k_count.eq(k_count + 1)
                    with m.If(k_count >= self.DEBOUNCE_CYCLES):
                        m.d.usb += [self.suspended.eq(0), k_count.eq(0)]
                        m.d.comb += ev_set[1].eq(1)
                        m.next = "RESUME"
                with m.Else():
                    m.d.usb += k_count.eq(0)
                with m.If(quiet & (u.line_state == 0)):
                    m.d.usb += se0_count.eq(se0_count + 1)
                    with m.If(se0_count >= self.FS_RESET_CYCLES):
                        start_reset()
                with m.Else():
                    m.d.usb += se0_count.eq(0)
                with m.If(wake_req):
                    m.d.usb += [wake_req.eq(0), self.suspended.eq(0), timer_count.eq(0)]
                    m.d.comb += ev_set[1].eq(1)
                    m.next = "WAKE"
                with m.If(~attached):
                    m.next = "DETACHED"
            with m.State("WAKE"):
                m.d.comb += [u.op_mode.eq(2), chirp_tx.valid.eq(1), chirp_tx.data.eq(0x00)]
                m.d.usb += [timer_count.eq(timer_count + 1), k_count.eq(0)]
                with m.If(timer_count >= self.RESUME_K_CYCLES):
                    m.next = "RESUME"
                with m.If(~attached):
                    m.next = "DETACHED"
            with m.State("RESUME"):
                with m.If(u.line_state != K_STATE):
                    m.d.usb += k_count.eq(k_count + 1)
                    with m.If(k_count >= self.DEBOUNCE_CYCLES):
                        m.d.usb += [se0_count.eq(0), idle_count.eq(0)]
                        m.next = "FS"
                        with m.If(was_hs):
                            m.next = "HS"
                with m.Else():
                    m.d.usb += k_count.eq(0)
                with m.If(~attached):
                    m.next = "DETACHED"
            with m.State("CHIRP_K"):
                m.d.comb += [u.xcvr_select.eq(USBSpeed.HIGH), u.term_select.eq(1), u.op_mode.eq(2),
                             chirp_tx.valid.eq(1), chirp_tx.data.eq(0x00)]
                m.d.usb += timer_count.eq(timer_count + 1)
                with m.If(timer_count >= self.CHIRP_K_CYCLES):
                    m.d.usb += [timer_count.eq(0), pairs.eq(0), seen_k.eq(0)]
                    m.next = "CHIRP_WAIT"
                with m.If(~attached):
                    m.next = "DETACHED"
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
                with m.If(~attached):
                    m.next = "DETACHED"
            with m.State("DETACHED"):
                m.d.comb += [u.term_select.eq(0), u.op_mode.eq(1)]
                m.d.usb += [self.suspended.eq(0), se0_count.eq(0), idle_count.eq(0)]
                with m.If(attached):
                    m.next = "FS"

        # ---- SOF, counters, events ----
        have_frame = Signal()
        with m.If(tokens.new_frame):
            m.d.usb += [self.frame.eq(tokens.frame), self.sof_count.eq(self.sof_count + 1), have_frame.eq(1)]
            with m.If(have_frame & (tokens.frame == self.frame)):
                m.d.usb += self.uframe.eq(Mux(self.uframe == 7, 7, self.uframe + 1))
            with m.Else():
                m.d.usb += self.uframe.eq(0)
        m.d.usb += self.events.eq((self.events & ~self.ev_clr) | ev_set)


        nak_evt, nyet_evt, dup_evt = Signal(), Signal(), Signal()
        for sig, evt in ((self.n_nak, nak_evt), (self.n_nyet, nyet_evt), (self.n_dup, dup_evt),
                         (self.n_crc, data_rx.crc_mismatch)):
            with m.If(evt & (sig != 255)):
                m.d.usb += sig.eq(sig + 1)

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
        expect = Signal(2)                   # 0 none, 1 SETUP data, 2 EP0 OUT data, 3 bulk OUT data
        rx_idx = Signal(11)
        resp = Signal(3)                     # 0 none, 1 ACK, 2 NAK, 3 STALL, 4 NYET
        sending, send_zlp, tx_idx = Signal(), Signal(), Signal(7)
        wait_ack, ack_count = Signal(), Signal(8)

        pkt_pid = data_rx.packet_id
        got_data1 = pkt_pid == PID.DATA1
        m.d.comb += o_wr.addr.eq(rx_idx[0:6]), o_wr.data.eq(data_rx.stream.payload)
        m.d.comb += o_wr.en.eq(data_rx.stream.next & (expect == 2) & (stage == Stage.DATA_OUT)
                               & ~self.o_valid & (rx_idx < EP0_MAXP))

        with m.If(data_rx.stream.next & (rx_idx != 2047)):
            m.d.usb += rx_idx.eq(rx_idx + 1)
            with m.If((expect == 1) & (rx_idx < 8)):
                m.d.usb += setup_bytes[rx_idx[0:3]].eq(data_rx.stream.payload)

        def reset_ep0():
            m.d.usb += [stage.eq(Stage.IDLE), stalled.eq(0), status_ok.eq(0), in_armed.eq(0),
                        self.o_valid.eq(0), pend_addr_valid.eq(0), expect.eq(0), sending.eq(0),
                        wait_ack.eq(0), self.setup_valid.eq(0), self.seq.eq(self.seq + 1)]

        # tokens
        dev_tok = tokens.new_token & (tokens.addr == dev_addr)
        with m.If(tokens.new_token):
            m.d.usb += expect.eq(0)
        with m.If(dev_tok & (tokens.endp == 0)):
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

        # bulk tokens
        is_hs = self.speed == USBSpeed.HIGH
        for k in range(N):
            with m.If(dev_tok & (tokens.endp == k + 1)):
                with m.If((tokens.pid == PID.OUT) & en_o[k]):
                    m.d.usb += [expect.eq(3), rx_idx.eq(0), b_ep.eq(k), b_space.eq(ofifo[k].w_free),
                                wait_ack.eq(0), b_wait.eq(0)]
                with m.Elif((tokens.pid == PID.PING) & en_o[k] & is_hs):
                    with m.If(halt_o[k]):
                        m.d.comb += hs_tx.issue_stall.eq(1)
                    with m.Elif(ofifo[k].w_free):
                        m.d.comb += hs_tx.issue_ack.eq(1)
                    with m.Else():
                        m.d.comb += [hs_tx.issue_nak.eq(1), nak_evt.eq(1)]
                with m.Elif((tokens.pid == PID.IN) & en_i[k]):
                    m.d.usb += [wait_ack.eq(0), b_wait.eq(0), b_ep.eq(k)]
                    with m.If(halt_i[k]):
                        m.d.comb += hs_tx.issue_stall.eq(1)
                    with m.Elif(ififo[k].r_have):
                        m.d.usb += [b_sending.eq(1), b_idx.eq(0), b_len.eq(ififo[k].r_len)]
                    with m.Else():
                        m.d.comb += [hs_tx.issue_nak.eq(1), nak_evt.eq(1)]

        # bulk OUT payload: written as it arrives, committed on a good CRC
        cur_maxp = Array(maxp_o)[b_ep]
        for k in range(N):
            f = ofifo[k]
            m.d.comb += [f.w_idx.eq(rx_idx[0:f.iw]), f.w_data.eq(data_rx.stream.payload),
                         f.w_en.eq(data_rx.stream.next & (expect == 3) & (b_ep == k) & b_space
                                   & (rx_idx < cur_maxp))]

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
            with m.Elif(expect == 3):
                for k in range(N):
                    f = ofifo[k]
                    data_pid_ok = (pkt_pid == PID.DATA0) | (pkt_pid == PID.DATA1)
                    with m.If(b_ep == k):
                        with m.If(halt_o[k]):
                            m.d.usb += resp.eq(3)
                        with m.Elif((rx_idx > cur_maxp) | ~data_pid_ok):
                            pass                                         # babble / wrong PID: no response
                        with m.Elif(~b_space):
                            m.d.usb += resp.eq(2)
                            m.d.comb += nak_evt.eq(1)
                        with m.Elif(got_data1 != tog_o[k]):
                            m.d.usb += resp.eq(1)                        # duplicate: ACK, drop
                            m.d.comb += dup_evt.eq(1)
                        with m.Else():
                            nyet = is_hs & (f.w_count + 1 >= self.fifo_slots)
                            m.d.comb += [f.w_commit.eq(1), f.w_len.eq(rx_idx[0:f.lw]), nyet_evt.eq(nyet)]
                            m.d.usb += [tog_o[k].eq(~tog_o[k]), resp.eq(Mux(nyet, 4, 1))]
        with m.If(data_rx.ready_for_response):
            m.d.usb += resp.eq(0)
            m.d.comb += [hs_tx.issue_ack.eq(resp == 1), hs_tx.issue_nak.eq(resp == 2),
                         hs_tx.issue_stall.eq(resp == 3), hs_tx.issue_nyet.eq(resp == 4)]

        # data to the host: EP0 buffer or a bulk FIFO
        stream = data_tx.stream
        last_byte = tx_idx == (ilen - 1)
        b_zero = b_len == 0
        b_adv = b_sending & ~b_zero & stream.ready
        b_nxt = b_idx + 1
        m.d.comb += b_raddr.eq(Mux(b_adv, b_nxt, b_idx))
        b_rdata = Array(ififo[k].r_data for k in range(N))[b_ep] if N > 1 else ififo[0].r_data
        b_tog = Array(tog_i[k] for k in range(4))[b_ep]
        m.d.comb += [
            i_rd.addr.eq(tx_idx[0:6]),
            stream.payload.eq(Mux(b_sending, b_rdata, i_rd.data)),
            stream.valid.eq(sending | b_sending),
            stream.first.eq((sending & ~send_zlp & (ilen != 0) & (tx_idx == 0))
                            | (b_sending & ~b_zero & (b_idx == 0))),
            stream.last.eq((sending & (send_zlp | (ilen == 0) | last_byte))
                           | (b_sending & (b_zero | (b_nxt == b_len)))),
            data_tx.data_pid.eq(Mux(b_sending, b_tog, Mux(send_zlp, 1, in_toggle))),
        ]
        zero_len = send_zlp | (ilen == 0)
        with m.If(sending):
            with m.If(zero_len):         # the generator latched the ZLP in the first cycle
                m.d.usb += [sending.eq(0), wait_ack.eq(1), ack_count.eq(0)]
            with m.Elif(stream.ready):
                m.d.usb += tx_idx.eq(tx_idx + 1)
                with m.If(last_byte):
                    m.d.usb += [sending.eq(0), wait_ack.eq(1), ack_count.eq(0)]
        with m.If(b_sending):
            with m.If(b_zero):
                m.d.usb += [b_sending.eq(0), b_wait.eq(1), b_ack_count.eq(0)]
            with m.Elif(stream.ready):
                m.d.usb += b_idx.eq(b_nxt)
                with m.If(b_nxt == b_len):
                    m.d.usb += [b_sending.eq(0), b_wait.eq(1), b_ack_count.eq(0)]
        with m.If(b_wait):
            m.d.usb += b_ack_count.eq(b_ack_count + 1)
            with m.If(hs_det.detected.ack):
                m.d.usb += b_wait.eq(0)
                for k in range(N):
                    with m.If(b_ep == k):
                        m.d.comb += ififo[k].r_pop.eq(1)
                        m.d.usb += tog_i[k].eq(~tog_i[k])
            with m.Elif(b_ack_count >= self.ACK_WAIT_CYCLES):
                m.d.usb += b_wait.eq(0)
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

        # ---- endpoint status port ----
        for is_in in (0, 1):
            for k in range(N):
                with m.If(self.ep_sel == ep_code(k + 1, is_in)):
                    en, ha, tg, mp = (en_i, halt_i, tog_i, maxp_i) if is_in else (en_o, halt_o, tog_o, maxp_o)
                    f = ififo[k] if is_in else ofifo[k]
                    m.d.comb += [self.ep_enabled.eq(en[k]), self.ep_halted.eq(ha[k]), self.ep_toggle.eq(tg[k]),
                                 self.ep_level.eq(f.w_count), self.ep_maxp.eq(mp[k])]

        # ---- CPU commands ----
        m.d.usb += [fl_o.eq(0), fl_i.eq(0)]
        with m.If(bus_reset | lost):
            reset_ep0()
            m.d.usb += [dev_addr.eq(0), self.suspended.eq(0), en_o.eq(0), en_i.eq(0), halt_o.eq(0),
                        halt_i.eq(0), fl_o.eq(0xF), fl_i.eq(0xF), b_sending.eq(0), b_wait.eq(0),
                        have_frame.eq(0), wake_req.eq(0)]
            with m.If(bus_reset):
                m.d.usb += self.reset_seen.eq(1)
        with m.If(self.reset_ack):
            m.d.usb += self.reset_seen.eq(0)
        with m.If(self.cmd_valid):
            with m.If(self.cmd_seq != self.seq):
                with m.If(self.stale != 255):
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
                    with m.Case(CMD_WAKEUP):
                        with m.If(self.suspended):
                            m.d.usb += wake_req.eq(1)
                for is_in in (0, 1):
                    en, ha, tg, mp, fl = ((en_i, halt_i, tog_i, maxp_i, fl_i) if is_in
                                          else (en_o, halt_o, tog_o, maxp_o, fl_o))
                    for k in range(N):
                        with m.If(self.cmd_ep == ep_code(k + 1, is_in)):
                            with m.Switch(self.cmd):
                                with m.Case(CMD_EP_ENABLE):
                                    m.d.usb += [en[k].eq(1), ha[k].eq(0), tg[k].eq(0), fl[k].eq(1),
                                                mp[k].eq(self.cmd_arg[0:10])]
                                    if is_in:
                                        m.d.usb += zlp_i[k].eq(self.cmd_arg[10])
                                with m.Case(CMD_EP_DISABLE):
                                    m.d.usb += [en[k].eq(0), fl[k].eq(1)]
                                with m.Case(CMD_EP_HALT):
                                    m.d.usb += ha[k].eq(1)
                                with m.Case(CMD_EP_CLEAR):
                                    m.d.usb += [ha[k].eq(0), tg[k].eq(0)]
                                with m.Case(CMD_EP_FLUSH):
                                    m.d.usb += fl[k].eq(1)
                            if is_in:
                                with m.If((self.cmd == CMD_EP_ENABLE) | (self.cmd == CMD_EP_DISABLE)
                                          | (self.cmd == CMD_EP_FLUSH)):
                                    with m.If(b_ep == k):
                                        m.d.usb += [b_sending.eq(0), b_wait.eq(0)]
        m.d.comb += self.irq.eq((self.events & self.ev_mask) != 0)
        with m.If(tokens.new_frame):
            m.d.comb += ev_set[5].eq(1)
        with m.If(bus_reset):
            m.d.comb += ev_set[4].eq(1)
        with m.If(data_rx.packet_complete & (expect == 1) & (rx_idx == 8) & (pkt_pid == PID.DATA0)):
            m.d.comb += ev_set[3].eq(1)
        for k in range(N):
            with m.If(ofifo[k].w_commit & ofifo[k].w_free | ififo[k].r_pop & ififo[k].r_have):
                m.d.comb += ev_set[6].eq(1)
        return m
