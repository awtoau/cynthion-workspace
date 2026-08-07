#!/usr/bin/env python3
#
# A CSR peripheral in front of LUNA's ULPI register window.
# See awtoau/cynthion-workspace#120.
# SPDX-License-Identifier: BSD-3-Clause

"""
Read and write a USB3343's registers from the CPU.

The three PHYs on this board are **parallel ULPI, not I2C**. They have no bus
address; the FPGA drives an 8-bit data bus with `dir`/`nxt`/`stp` handshaking and
a register is reached by putting a command byte on that bus during an idle turn.
Nothing about the board's I2C topology applies to them --
`docs/chips/usb3343-ulpi-phy.md`.

The transaction itself is `luna.gateware.interface.ulpi.ULPIRegisterWindow`,
imported rather than rewritten. Per `docs/upstream-boundary.md` that sits inside
the USB-stack exception: this design already runs `USBSerialDevice` over the same
layer on `aux_phy`, so the ULPI transaction encoding is upstream code we are
already trusting with the console. What this file adds is the part that is ours:
a register map, a clock-domain crossing, and a bound on the wait.

## The domain crossing, and why it is a handshake rather than a strobe

The register window runs in `usb`, because that is the ULPI clock -- 60 MHz,
sourced by the FPGA (`clk_dir='o'`), and shared by every PHY. The CPU and its
CSR bus run in `sync`, which is 60 MHz today and is a free parameter: raising
`SYNC_MHZ` makes them unrelated clocks, and a design that only worked because two
PLL outputs happened to be the same frequency is a design that breaks silently
later. The same argument already forced the console's elastic buffers to be
asynchronous (`peripherals/stream_buffer.py`), after raising `sync` to 80 MHz produced a
stream with correct counter values and dropped characters.

So the crossing is a **four-phase handshake on two toggles**, not a pulse
synchroniser:

    sync: flip `request`            -->  synchronised into usb
    usb:  see it change, run the transaction, latch the result,
          then flip `acknowledge`   -->  synchronised into sync
    sync: see it change; the result has been stable for several usb
          cycles by then, so it is sampled with ordinary flip-flops

A pulse synchroniser would be shorter and would lose a request issued while the
previous one was in flight. A toggle cannot be lost, and `busy` -- which is
`request != acknowledge` -- is exactly the flag firmware must poll before issuing
another. The data needs no synchroniser of its own because it is written before
the acknowledge toggles and read after it arrives, which is two synchroniser
stages of separation.

## Every wait is bounded

`ULPIRegisterWindow` waits for `dir` to fall before it may drive the bus, and
`dir` is the PHY's. A PHY that is absent, held in reset, or receiving leaves that
wait outstanding forever, and the window has no abort input -- so without help,
one read of a missing PHY would leave `busy` set for the rest of the session and
every later read would report "busy" rather than "absent". That is the same class
of failure as an unbounded spin in firmware, and it gets the same answer: a
timeout.

`TIMEOUT_CYCLES` counts `usb` cycles from the start of a transaction. On expiry
the window is held in reset for a cycle -- which is what returns its FSM to IDLE,
since it has no other way back -- the acknowledge is toggled so firmware is not
left waiting, and `status.timeout` is set. Firmware then reports "no answer from
the PHY" instead of "the peripheral is stuck", and the next read starts clean.

## Registers

    +0  ADDRESS  rw  6-bit ULPI register address
    +1  DATA     rw  byte to write; on read, the byte the last read returned
    +2  CONTROL  w   bit 0 start a read, bit 1 start a write,
                     bit 2 reset the PHY
    +3  STATUS   r   bit 0 busy, bit 1 timeout on the last transaction,
                     bit 2 a PHY reset is in progress

**No read here has a side effect.** DATA is a plain latch: reading it twice gives
the same byte and advances nothing, so a widened, prefetched or replayed read
cannot consume a result -- the hazard `peripherals/uart16550.py` exists to eliminate. STATUS
is a wire from three flags. The only side-effecting address is CONTROL, and it is
side-effecting on write, where speculation cannot reach it. `timeout` is cleared
by the START of the next transaction rather than by reading STATUS, for the same
reason.

## Resetting the PHY

`CONTROL` bit 2 pulses TARGET's RESETB and then waits out the part's preparation
time; `STATUS` bit 2 is set for the whole of it, so firmware polls one bit rather
than counting a delay it would have to get right. Both durations come from the
datasheet and live in `clocks.py`, which uses the same two constants for the
power-on sequence.

It resets **TARGET alone**, deliberately. AUX carries the console, so a command
that reset it would take away the terminal printing the result, and CONTROL is
shared with Apollo. Before this existed the PHYs had no reset at all under
firmware control -- the pads were tied de-asserted, and the only way to recover a
PHY that had glitched was to reconfigure the FPGA (#241).
"""

from amaranth               import Module, Signal, ResetInserter
from amaranth.lib           import wiring
from amaranth.lib.cdc       import FFSynchronizer
from amaranth.lib.wiring    import In, Out

from amaranth_soc           import csr

from clocks                             import (PHY_PAD_RESET_CYCLES,
                                                PHY_PREP_CYCLES)
from peripherals.uart16550              import SplitRW


__all__ = ["UlpiRegisters", "TIMEOUT_CYCLES"]


# How long a ULPI register transaction is allowed to take, in `usb` cycles.
#
# A register read is a command byte, a turnaround and a data byte -- under ten
# cycles of the 60 MHz ULPI clock once the bus is idle. 4096 cycles is 68 us,
# nearly three orders of magnitude of headroom, so expiry cannot mean "the PHY
# was slow". It means `dir` never fell: the PHY is absent, held in reset, or
# driving the bus at us continuously.
#
# The cost of being generous is how long a firmware command takes to report an
# absent PHY, which is 68 us -- below anything a person can notice. The cost of
# being tight would be a transaction aborted while it was working, which on a
# ULPI bus means a half-issued command byte.
TIMEOUT_CYCLES = 4096

CONTROL_READ  = 0
CONTROL_WRITE = 1


class UlpiRegisters(wiring.Component):
    """A CSR window onto one PHY's ULPI registers.

    Attributes
    ----------
    bus : csr.Interface(addr_width=2, data_width=8)
        The four registers in the module docstring. Add through a
        `WishboneCSRBridge`, or behind a `csr.Decoder` with other peripherals.
    data_i, data_o, data_oe : Signal(8), Signal(8), Signal()
        The ULPI data bus, to be wired to the platform pins. `data_oe` is
        `~dir`, per ULPI: the PHY owns the bus whenever it asserts `dir`.
    dir_i, nxt_i : Signal(), in
        From the PHY.
    stp_o : Signal(), out
        To the PHY.

    Everything on the ULPI side is in the `usb` domain; the CSR bus is in
    `sync`. Do not connect the two anywhere but here.
    """

    def __init__(self, *, pad_reset_cycles=PHY_PAD_RESET_CYCLES,
                 prep_cycles=PHY_PREP_CYCLES):
        # The two PHY-reset durations, overridable ONLY so that a simulation can
        # exercise the sequence without running 72128 usb cycles of it. The
        # hardware never passes these; `scripts/soc_board_sim.py` checks the
        # defaults against the datasheet arithmetic separately, so a shortened
        # simulation cannot make a wrong constant pass.
        self._pad_reset_cycles = pad_reset_cycles
        self._prep_cycles = prep_cycles

        # +0. The ULPI register address. Six bits: ULPI's immediate address
        # space is 0x00-0x3f, and the extended space (0x2f as an escape) is not
        # implemented here because nothing on this board lives there.
        self._address = csr.Register({"address": csr.Field(csr.action.RW, 6)},
                                     access="rw")
        # +1. Write data on write, read data on read. Two registers at one
        # address, exactly as the 16550's RBR/THR is: a driver that stages a
        # byte to write must not thereby destroy the byte the last read
        # returned, because the two are used in the same breath by a
        # write-then-verify.
        self._data = csr.Register({"data": csr.Field(SplitRW, 8)}, access="rw")
        # +2. Write-only. A start bit that read back would be a status bit, and
        # a status bit that shares an address with a command is the arrangement
        # this project keeps finding at the bottom of its longest bugs.
        self._control = csr.Register({"start":     csr.Field(csr.action.W, 2),
                                      "phy_reset": csr.Field(csr.action.W, 1)},
                                     access="w")
        # +3. Pure. All three bits are wires from flags; reading it changes
        # nothing, which is what makes it safe to poll.
        self._status = csr.Register({"busy":      csr.Field(csr.action.R, 1),
                                     "timeout":   csr.Field(csr.action.R, 1),
                                     "resetting": csr.Field(csr.action.R, 1)},
                                    access="r")

        builder = csr.Builder(addr_width=2, data_width=8)
        builder.add("address", self._address)
        builder.add("data",    self._data)
        builder.add("control", self._control)
        builder.add("status",  self._status)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":     In(csr.Signature(addr_width=2, data_width=8)),
            "data_i":  In(8),
            "data_o":  Out(8),
            "data_oe": Out(1),
            "dir_i":   In(1),
            "nxt_i":   In(1),
            "stp_o":   Out(1),
            "phy_rst": Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        from luna.gateware.interface.ulpi import ULPIRegisterWindow

        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        # ---- the sync side ---------------------------------------------------
        #
        # `request` and `acknowledge` are toggles, and `busy` is the statement
        # that they disagree. Nothing here is a pulse, so nothing here can be
        # missed by a crossing.
        request = Signal()
        acknowledge_sync = Signal()
        # What the `sync` side has ALREADY taken account of. `busy` is measured
        # against this rather than against the raw synchronised acknowledge, so
        # that busy falling and the result appearing are the same clock edge --
        # otherwise there is one cycle in which firmware is told the
        # transaction finished and shown the previous byte.
        acknowledged = Signal()
        busy = Signal()
        timeout = Signal()
        write_data = Signal(8)
        read_data = Signal(8)
        is_write = Signal()

        m.d.comb += [
            busy.eq(request != acknowledged),
            self._status.f.busy.r_data.eq(busy),
            self._status.f.timeout.r_data.eq(timeout),
            self._data.f.data.r_data.eq(read_data),
        ]

        data_field = self._data.f.data
        with m.If(data_field.w_stb):
            m.d.sync += write_data.eq(data_field.w_data)

        start = self._control.f.start
        with m.If(start.w_stb & (start.w_data != 0) & ~busy):
            # A start issued while busy is DROPPED, not queued.
            #
            # Queueing would need a depth and a policy for what happens when it
            # fills; dropping needs neither, and firmware already has to poll
            # `busy` before it may read the result. The failure mode of the
            # dropped write is that the caller sees `busy` and waits, which is
            # what it was going to do anyway.
            m.d.sync += [
                request.eq(~request),
                is_write.eq(start.w_data[CONTROL_WRITE]),
                # Cleared at the START of a transaction, not by reading STATUS.
                # Clear-on-read here would be a state-changing read in the same
                # 32-bit word as the command register.
                timeout.eq(0),
            ]

        # ---- the usb side ----------------------------------------------------
        window_reset = Signal()
        # `{"usb": ...}`, NOT a bare signal. `ResetInserter` given a plain Value
        # means `{"sync": value}`, and `on_fragment` skips every domain not in
        # that dict -- so on a module that is entirely `usb` (18 x `m.d.usb` and
        # `m.FSM(domain="usb")`) the bare form inserts nothing at all and the
        # timeout reset below reaches no logic. It was written that way, and the
        # consequence was that one timeout parked the window for ever: every
        # later transaction also timed out, and firmware reported a working PHY
        # as absent until the FPGA was reconfigured.
        window = ResetInserter({"usb": window_reset})(ULPIRegisterWindow())
        m.submodules.window = window

        request_usb = Signal()
        m.submodules.request_cdc = FFSynchronizer(request, request_usb,
                                                 o_domain="usb")

        acknowledge = Signal()
        m.submodules.ack_cdc = FFSynchronizer(acknowledge, acknowledge_sync,
                                              o_domain="sync")

        # The command latched on the usb side. `is_write` and `write_data` are
        # written in `sync` before `request` toggles and are not touched again
        # until the transaction completes, so they are stable across the
        # crossing without synchronisers of their own -- the toggle is the only
        # thing that has to be treated as asynchronous.
        m.d.comb += [
            window.address.eq(self._address.f.address.data),
            window.write_data.eq(write_data),

            self.data_o.eq(window.ulpi_data_out),
            window.ulpi_data_in.eq(self.data_i),
            window.ulpi_dir.eq(self.dir_i),
            window.ulpi_next.eq(self.nxt_i),
            self.stp_o.eq(window.ulpi_stop),
            # ULPI's rule, and the whole of the bus arbitration: the PHY owns
            # the data bus whenever it asserts `dir`, and the link must stop
            # driving in the same cycle. Anything cleverer here is contention.
            self.data_oe.eq(~self.dir_i),
        ]

        elapsed = Signal(range(TIMEOUT_CYCLES + 1))
        timeout_usb = Signal()
        read_data_usb = Signal(8)

        # The result crosses domains as DATA WITH A HANDSHAKE, not as a
        # synchronised signal.
        #
        # `read_data_usb` and `timeout_usb` are written on the usb side and then
        # the acknowledge toggles; the toggle takes two `sync` flip-flops to
        # arrive, by which time both have been stable for several cycles. They
        # are therefore copied with plain flip-flops here and never sampled
        # while they could be moving. Putting a synchroniser on each of the nine
        # bits would cost nine more flops and would not make it any safer: a
        # synchroniser resolves metastability, and the reason there is none here
        # is the ordering, not the flops.
        with m.If(acknowledge_sync != acknowledged):
            m.d.sync += [
                acknowledged.eq(acknowledge_sync),
                read_data.eq(read_data_usb),
                timeout.eq(timeout_usb),
            ]

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                m.d.usb += elapsed.eq(0)
                with m.If(request_usb != acknowledge):
                    m.d.comb += [
                        window.read_request.eq(~is_write),
                        window.write_request.eq(is_write),
                    ]
                    m.next = "RUN"

            with m.State("RUN"):
                m.d.usb += elapsed.eq(elapsed + 1)
                with m.If(window.done):
                    # Latched here, one cycle BEFORE the acknowledge toggles, so
                    # the `sync` side cannot observe the toggle and a stale
                    # byte in the same cycle.
                    m.d.usb += [
                        read_data_usb.eq(window.read_data),
                        timeout_usb.eq(0),
                        acknowledge.eq(request_usb),
                    ]
                    m.next = "IDLE"
                with m.Elif(elapsed == TIMEOUT_CYCLES):
                    # The window has no abort input and its FSM is waiting on a
                    # signal the PHY drives, so the only way back is a reset.
                    # One cycle is enough: its whole state is the FSM and two
                    # latched arguments, all in `usb`.
                    m.d.comb += window_reset.eq(1)
                    m.d.usb += [
                        timeout_usb.eq(1),
                        acknowledge.eq(request_usb),
                    ]
                    m.next = "IDLE"

        # ---- the deliberate PHY reset ----------------------------------------
        #
        # A second toggle handshake, for the same reason as the first: the write
        # arrives in `sync` and the pulse has to be counted in `usb`, which is
        # where the 60.000 MHz that makes the durations real lives.
        #
        # `SocClocks` holds this pad at power-up; this is the path back
        # afterwards, and it is the only one -- the FPGA cannot be reconfigured
        # by the firmware running on it. It resets TARGET alone. AUX carries the
        # console, so a firmware command that reset it would take away the
        # terminal printing the result, and CONTROL belongs to Apollo.
        reset_request = Signal()
        reset_ack_sync = Signal()
        reset_acked = Signal()
        resetting_sync = Signal()

        m.d.comb += self._status.f.resetting.r_data.eq(resetting_sync)

        phy_reset_start = self._control.f.phy_reset
        with m.If(phy_reset_start.w_stb & (phy_reset_start.w_data != 0)
                  & ~resetting_sync):
            m.d.sync += [reset_request.eq(~reset_request), resetting_sync.eq(1)]
        with m.Elif(reset_ack_sync != reset_acked):
            m.d.sync += [reset_acked.eq(reset_ack_sync), resetting_sync.eq(0)]

        reset_request_usb = Signal()
        reset_ack = Signal()
        m.submodules.reset_req_cdc = FFSynchronizer(reset_request,
                                                   reset_request_usb,
                                                   o_domain="usb")
        m.submodules.reset_ack_cdc = FFSynchronizer(reset_ack, reset_ack_sync,
                                                   o_domain="sync")

        settled = self._pad_reset_cycles + self._prep_cycles
        reset_elapsed = Signal(range(settled + 1))
        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                m.d.usb += reset_elapsed.eq(0)
                with m.If(reset_request_usb != reset_ack):
                    m.next = "RESETTING"

            with m.State("RESETTING"):
                m.d.usb += reset_elapsed.eq(reset_elapsed + 1)
                # The pad, and the window with it. A register transaction in
                # flight across a PHY reset would wait on a `dir` that is not
                # coming and would be reported as a timeout, which is a true
                # statement about the wrong thing.
                m.d.comb += [
                    self.phy_rst.eq(reset_elapsed < self._pad_reset_cycles),
                    window_reset.eq(1),
                ]
                with m.If(reset_elapsed == settled - 1):
                    m.d.usb += reset_ack.eq(reset_request_usb)
                    m.next = "IDLE"

        return m
