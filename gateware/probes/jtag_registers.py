#!/usr/bin/env python3
#
# A JTAG register file clocked BY TCK, so no clock ratio can slip the readback.
# SPDX-License-Identifier: BSD-3-Clause

"""The register transport every bring-up applet measures through.

Wire-compatible with `luna.gateware.interface.jtag.JTAGRegisterInterface` --
same two taps, same widths, same bit order, same `add_register` API -- and
therefore still read by Apollo's `debugger.registers` with no host change. What
differs is where the shift registers are clocked. See #204.

## The fault this removes

luna's version does not clock its shift register from TCK. It samples TCK into
`sync` and shifts on a detected edge:

    m.d.sync += jtag_clk_delayed.eq(jtag_clk)
    jtag_strobe = rising_edge_detected(m, jtag_clk_delayed)
    with m.If(jtag_strobe):
        m.d.sync += data_register.eq(Cat(data_register[1:], jtag_tdi))

That is an oversampler, and an oversampler has a required ratio: two flops plus
the shift is three `sync` edges, and the answer to the bit the host is clocking
now has to be ready before the host samples TDO. With enough ratio it is; below,
edges are missed or counted twice and the readback slips a bit -- which is how a
ceiling rung read its applet id back as `0x48a48663` where `0x48524331` was
expected. The board was clean at 60:12 and slipped at 30:12; the simulation,
which cannot show a flop settling late, puts the boundary lower still at 2x. The
number is not the point. Having one is.

**Why a slipped readback is worse than a dead one.** Word counts, cycle counts
and *error counts* come back through this same register file. A slip can turn a
non-zero error count into a zero one -- a failing measurement into a passing
one -- and nothing in the protocol says so. It was only caught because the
applet id is a known constant. Retrying does not help: the slip is deterministic
at a given ratio, so two reads agree and a retry loop returns the wrong value
with confidence.

## The fix, and why it has no ratio at all

Everything that touches the JTAG wire is clocked by TCK: captured on the falling
edge, presented on the rising one, in two local domains driven by the same pin.
`soc/bus/jtag_stage.py` already proves the construction on this board. A shift
register advances once per TCK because TCK is its clock, so the sync:TCK ratio
cannot enter the answer at all -- 130:20 and 20:20 alike, and 10:20 too.

**The falling edge is not a style choice.** JTAGG's JTDI, JSHIFT and JCE all
change on the rising edge of TCK; sampling them there is a hold-time race with
no timing arc to close, and it fails per placement rather than cleanly. On the
falling edge every JTAGG output has been stable for half a TCK.

The two directions get different edges, and this is the whole alignment:

  * **Input** (TDI into the shift register) captures on the FALLING edge under
    `shifting`, one edge behind `active`. At a falling edge JSHIFT describes the
    bit being clocked NOW while JTDI still holds the one before it; capturing
    under `active` would take a garbage bit ahead of the payload and shift the
    entire frame.
  * **Output** (shift register to TDO) advances on the RISING edge, enabled by
    that same falling-edge copy. Nothing here samples a JTAGG output at the
    rising edge -- the enable was captured half a period earlier -- so the race
    the falling edge exists to avoid is still avoided.

So the in and out paths cannot share one register the way luna's do, and they do
not: `ir_in`/`dr_in` capture, `ir_out`/`dr_out` present.

**Why the output moved to the rising edge, which looks like the wrong one.**
There are two readings of when the host gets a bit, and they differ by one slot:
TDO registered into the pad at the falling edge and sampled at the next rising
one, or TDO taken combinationally at the rising edge. A design that advances TDO
on the falling edge is one bit early under the second reading and correct under
the first; one that advances a falling edge later is correct under the second
and one bit late under the first. Advancing on the RISING edge is the only
choice that hands the host the same bit under BOTH -- and since this transport's
host is Apollo's, already written and not under test here, matching luna under
either reading is the difference between a fix and a new bug of the same shape.
`soc_jtag_registers_sim.py` runs both readings for exactly this reason.

## Crossing to `sync`, without a ratio and without a torn word

A read is a 32-bit value from a free-running `sync` counter. Sampling it bit by
bit from `jtck` would be a multi-bit crossing -- half the pre-increment value and
half the post -- so the value is FROZEN in `sync` and handed over whole:

    jtck: command shift ends -> latch `command`, flip `command_toggle`
    sync: sees the flip -> latches `snapshot` from the addressed register,
                           flips `ack_toggle`
    jtck: sees that flip -> copies `snapshot` into `hold`, its own register

Data is stable before the flag in both directions, so this is the ordinary
two-flop crossing and not a race. `hold` is what a data shift presents, so by the
time TDO moves, nothing about the value is still crossing.

**The cost is an edge count, not a ratio.** The handshake needs a few TCK edges
between the command shift and the data shift. Apollo's `register_transaction`
spends `run_test(32)` plus an 8-bit IR shift there -- some forty edges -- and
`scripts/soc_jtag_registers_sim.py` measures the requirement rather than
assuming it. A host that did not wait would read the PREVIOUS command's value:
wrong, but deterministic and never torn.

Writes cross the same way: the word is latched in `jtck` when the data shift
ends, then a toggle carries the event into `sync` where `write_strobe` fires for
one cycle with the word stable.

## What else #204 asked for

`decode_error` is sticky and set by a read or write of an address this file does
not implement. `default_read_value` is still returned on the wire -- it has to
be, the host expects 32 bits -- but `0xDEADBEEF` is a legal value for a counter
to hold, so "no such register" now has somewhere to be read from that is not the
data itself. Wire it into an applet's status word.

Addresses are checked when they are added, not silently dropped: out of range,
duplicate, and a read value too wide for `register_size` are all build-time
errors. A register that quietly does not exist -- address 29 accepted, never
readable -- is the third failure in #204 and it cost a session.
"""

from amaranth import (Cat, ClockDomain, ClockSignal, Const, Elaboratable,
                      Instance, Module, ResetSignal, Signal, Value)
from amaranth.lib.cdc import FFSynchronizer

__all__ = ["JTAGRegisterInterface", "JTCK_CONSTRAINT_HZ"]

# What Apollo clocks TCK at, plus headroom -- the same number `jtag_stage.py`
# constrains. Telling the placer stops it timing this domain against a default
# and printing a PASS about a frequency nobody chose.
JTCK_CONSTRAINT_HZ = 20e6


class JTAGRegisterInterface(Elaboratable):
    """Registers over the ECP5's user JTAG, clocked by TCK.

    Parameters
    ----------
    address_size : int
        Address bits. One less than a byte multiple, because the write flag is
        prepended to the address to form the command.
    register_size : int
        Bits in a register.
    default_read_value : int
        Answer for an address that is not implemented. See `decode_error`.
    support_size_autonegotiation : bool
        Keep address 0 reading all ones, which is how the host measures the
        address and data widths. Costs an address and is what makes the host
        work without being told the shape.
    simulate : bool
        Omit the `JTAGG` instance and expose the tap signals as ports, so a
        testbench can drive the chain. `JTAGG` is an ECP5 hard block and has no
        simulation model.

    Attributes
    ----------
    tck, tdi, ce1, ce2, shift, update, rstn, rti1 : Signal
        The tap, from `JTAGG` or from a testbench.
    tdo1, tdo2 : Signal, output
        Command tap and data tap.
    decode_error : Signal, output
        Sticky: an address that is not implemented was addressed.
    """

    def __init__(self, address_size=15, register_size=32, default_read_value=0,
                 support_size_autonegotiation=True, simulate=False):
        self.address_size = address_size
        self.register_size = register_size
        self.default_read_value = default_read_value
        self.simulate = simulate

        self.registers = {}

        # The tap. `rti1` and `update` are here because JTAGG has them and a
        # testbench models the whole block; this design deliberately depends on
        # NEITHER. luna loads its data register during RUN-TEST-IDLE, which
        # makes the value a function of TAP navigation the host is free to
        # change; here the load is a handshake and the frame is self-delimiting.
        self.tck = Signal()
        self.tdi = Signal()
        self.ce1 = Signal()
        self.ce2 = Signal()
        self.shift = Signal()
        self.update = Signal()
        self.rstn = Signal()
        self.rti1 = Signal()
        self.tdo1 = Signal()
        self.tdo2 = Signal()

        self.decode_error = Signal()

        if support_size_autonegotiation:
            self.support_size_autonegotiation()

    #
    # Registration. Same names and arguments as luna's, so an applet converts
    # by changing one import.
    #

    def support_size_autonegotiation(self):
        """Address 0 reads all ones, which is how the host measures the widths."""
        self.add_read_only_register(0, read=-1)

    def add_sfr(self, address, *, read=None, write_signal=None,
                write_strobe=None, read_strobe=None):
        """Add a register whose behaviour is the caller's, not this file's."""
        if not 0 <= address < (1 << self.address_size):
            raise ValueError(
                f"register address {address} does not fit in "
                f"{self.address_size} bits; the host cannot address it, and "
                f"accepting it here is how a register comes to silently not "
                f"exist (see #204)")
        if address in self.registers:
            raise ValueError(f"register address {address} is already in use")
        if read is not None and not isinstance(read, int):
            width = len(Value.cast(read))
            if width > self.register_size:
                raise ValueError(
                    f"the read value at address {address} is {width} bits and "
                    f"a register is {self.register_size}; truncating it "
                    f"silently would make a measurement look small")
        if isinstance(read, int) and read >= 0 and read.bit_length() > self.register_size:
            raise ValueError(
                f"the constant at address {address} needs {read.bit_length()} "
                f"bits and a register is {self.register_size}")

        self.registers[address] = {
            "read": read,
            "write_signal": write_signal,
            "write_strobe": write_strobe,
            "read_strobe": read_strobe,
            "elaborate": None,
        }

    def add_read_only_register(self, address, *, read, read_strobe=None):
        """Add one value the host can read."""
        self.add_sfr(address, read=read, read_strobe=read_strobe)

    def add_register(self, address, *, value_signal=None, size=None, name=None,
                     read_strobe=None, write_strobe=None, init=0):
        """Add one value the host can write, backed by a `sync` register."""
        name = name if name else f"register_{address:x}"
        if value_signal is None:
            value_signal = Signal(self.register_size if size is None else size,
                                  name=name, init=init)
        if write_strobe is None:
            write_strobe = Signal(name=name + "_write_strobe")
        write_value = Signal.like(value_signal, name=name + "_write_value")

        def _elaborate_memory_register(m):
            with m.If(write_strobe):
                m.d.sync += value_signal.eq(write_value)

        self.add_sfr(address, read=value_signal, write_signal=write_value,
                     write_strobe=write_strobe, read_strobe=read_strobe)
        self.registers[address]["elaborate"] = _elaborate_memory_register
        return value_signal

    #
    # The seam a negative control replaces. See `soc_jtag_registers_sim.py`.
    #

    def _present(self, m, command_bit, data_bit):
        """Drive the two TDO lines from the output registers.

        Combinationally, so the bit is on the wire for the whole TCK period
        either side samples it in. The simulation subclasses this to add one
        register stage -- a readback one slot late, the same shape as the slip
        in #204 -- because an equivalence check that cannot fail proves nothing
        by passing.
        """
        m.d.comb += [self.tdo1.eq(command_bit), self.tdo2.eq(data_bit)]

    def elaborate(self, platform):
        m = Module()

        if not self.simulate:
            m.submodules.jtagg = Instance(
                "JTAGG",
                o_JTCK=self.tck,
                o_JTDI=self.tdi,
                o_JSHIFT=self.shift,
                o_JUPDATE=self.update,
                o_JRSTN=self.rstn,
                o_JCE1=self.ce1,
                i_JTDO1=self.tdo1,
                o_JCE2=self.ce2,
                i_JTDO2=self.tdo2,
                o_JRTI1=self.rti1,
            )
            if platform is not None:
                platform.add_clock_constraint(self.tck, JTCK_CONSTRAINT_HZ)

        # The domain that removes the ratio. Asynchronously reset from `sync`,
        # because with no JTAG attached there is no edge to apply a synchronous
        # reset on.
        m.domains.jtck = ClockDomain("jtck", local=True, async_reset=True,
                                     clk_edge="neg")
        # The output half of the same clock. Everything crossing between the two
        # is a half-period path, which is the ordinary shape of a JTAG shifter
        # and what nextpnr times it as.
        m.domains.jtck_rise = ClockDomain("jtck_rise", local=True,
                                          async_reset=True)
        m.d.comb += [
            ClockSignal("jtck").eq(self.tck),
            ResetSignal("jtck").eq(ResetSignal("sync")),
            ClockSignal("jtck_rise").eq(self.tck),
            ResetSignal("jtck_rise").eq(ResetSignal("sync")),
        ]

        # The widths are luna's, deliberately. The registers are one bit wider
        # than the field they carry and the payload lands in bits 1.., because
        # the input enable is one edge behind the data; the host's idea of how
        # many bits are in each register comes from shifting ones out of them,
        # so changing either would change the wire.
        ir_bits = self.address_size + 2
        dr_bits = self.register_size + 1
        ir_ones = (1 << ir_bits) - 1
        dr_ones = (1 << dr_bits) - 1

        active1 = Signal()
        active2 = Signal()
        m.d.comb += [active1.eq(self.ce1 & self.shift),
                     active2.eq(self.ce2 & self.shift)]

        # One falling edge later, which is what lines the enable up with TDI.
        shifting1 = Signal()
        shifting2 = Signal()
        m.d.jtck += [shifting1.eq(active1), shifting2.eq(active2)]

        ir_in = Signal(ir_bits, init=ir_ones)
        dr_in = Signal(dr_bits, init=dr_ones)
        ir_out = Signal(ir_bits, init=ir_ones)
        dr_out = Signal(dr_bits, init=dr_ones)
        hold = Signal(dr_bits, init=dr_ones)

        # The bit on TDI has not been clocked in yet, so the completed field is
        # the register shifted by one with TDI on the end.
        ir_now = Cat(ir_in[1:], self.tdi)
        dr_now = Cat(dr_in[1:], self.tdi)

        command = Signal(self.address_size + 1)
        word_received = Signal(self.register_size)
        command_toggle = Signal()
        word_toggle = Signal()

        # TEST-LOGIC-RESET fills the registers with ones, which is what the host
        # counts to discover the widths. Held identical to luna's.
        in_reset = Signal()
        m.d.comb += in_reset.eq(~self.rstn)

        # The snapshot handshake, `sync` side first so the names are in scope.
        snapshot = Signal(self.register_size, init=-1)
        ack_toggle = Signal()
        ack_jtck = Signal()
        ack_seen = Signal()
        m.submodules.ack_cdc = FFSynchronizer(ack_toggle, ack_jtck,
                                              o_domain="jtck")
        m.d.jtck += ack_seen.eq(ack_jtck)

        with m.If(in_reset):
            m.d.jtck += [ir_in.eq(ir_ones), dr_in.eq(dr_ones),
                         hold.eq(dr_ones)]
        with m.Else():
            # `shifting & ~active` is the edge that clocks in the LAST bit of a
            # field: the enable still describes the bit on TDI, the TAP has
            # already left SHIFT-DR. So the completed value is `*_now`, not the
            # register, and the frame needs no JUPDATE to be delimited.
            with m.If(shifting1):
                m.d.jtck += ir_in.eq(ir_now)
                with m.If(~active1):
                    m.d.jtck += [command.eq(ir_now[1:]),
                                 command_toggle.eq(~command_toggle)]
            with m.If(shifting2):
                m.d.jtck += dr_in.eq(dr_now)
                with m.If(~active2):
                    m.d.jtck += [word_received.eq(dr_now[1:]),
                                 word_toggle.eq(~word_toggle)]

            # The value arrives whole, one flag edge after `sync` froze it.
            with m.If(ack_jtck != ack_seen):
                m.d.jtck += hold.eq(Cat(snapshot, Const(0, 1)))

        # Falling-edge copies of the two JTAGG signals the output path needs, so
        # that nothing in the rising-edge domain reads the hard block directly.
        tdi_held = Signal()
        reset_held = Signal()
        m.d.jtck += [tdi_held.eq(self.tdi), reset_held.eq(in_reset)]

        # The output path, on the rising edge: reloaded on every edge outside a
        # shift, so a data scan always begins at bit 0 of the held value, and
        # advanced by one bit during one. TDI is fed in behind it, as luna's
        # shared register does, so a host shifting past the register width sees
        # the same kind of thing; it is the held copy, so that too is a
        # half-period path rather than a race.
        with m.If(reset_held):
            m.d.jtck_rise += [ir_out.eq(ir_ones), dr_out.eq(dr_ones)]
        with m.Else():
            with m.If(shifting1):
                m.d.jtck_rise += ir_out.eq(Cat(ir_out[1:], tdi_held))
            with m.Else():
                m.d.jtck_rise += ir_out.eq(ir_in)
            with m.If(shifting2):
                m.d.jtck_rise += dr_out.eq(Cat(dr_out[1:], tdi_held))
            with m.Else():
                m.d.jtck_rise += dr_out.eq(hold)
        self._present(m, ir_out[0], dr_out[0])

        #
        # The `sync` side: the register file itself, and the two crossings.
        #

        command_sync = Signal()
        command_seen = Signal()
        m.submodules.command_cdc = FFSynchronizer(command_toggle, command_sync)
        m.d.sync += command_seen.eq(command_sync)
        command_edge = Signal()
        m.d.comb += command_edge.eq(command_sync != command_seen)

        word_sync = Signal()
        word_seen = Signal()
        m.submodules.word_cdc = FFSynchronizer(word_toggle, word_sync)
        m.d.sync += word_seen.eq(word_sync)
        word_edge = Signal()
        m.d.comb += word_edge.eq(word_sync != word_seen)

        # `command` is read here while it is stable: `jtck` writes it and only
        # then flips the toggle, and the toggle spends two `sync` flops getting
        # here. Data before flag -- the ordinary crossing, not a race.
        address = Signal(self.address_size)
        is_write = Signal()
        selected = Signal(self.register_size)
        decoded = Signal()

        # An explicit case per address. luna built an If/Elif chain and returned
        # the default from the trailing Else, which is indistinguishable from a
        # register that was never elaborated at all.
        with m.Switch(command[0:self.address_size]):
            for register_address, connections in self.registers.items():
                with m.Case(register_address):
                    m.d.comb += decoded.eq(1)
                    if connections["read"] is not None:
                        m.d.comb += selected.eq(connections["read"])
                    else:
                        m.d.comb += selected.eq(self.default_read_value)
            with m.Default():
                m.d.comb += selected.eq(self.default_read_value)

        with m.If(command_edge):
            m.d.sync += [
                address.eq(command[0:self.address_size]),
                is_write.eq(command[self.address_size]),
                snapshot.eq(selected),
                ack_toggle.eq(~ack_toggle),
            ]
            with m.If(~decoded):
                m.d.sync += self.decode_error.eq(1)

        # The write lands one crossing later, when the data shift has ended and
        # `word_received` has stopped moving.
        for register_address, connections in self.registers.items():
            hit = Signal(name=f"address_matches_{register_address:x}")
            m.d.comb += hit.eq(address == register_address)

            if connections["write_signal"] is not None:
                m.d.comb += connections["write_signal"].eq(word_received)
            if connections["write_strobe"] is not None:
                m.d.comb += connections["write_strobe"].eq(
                    word_edge & is_write & hit)
            if connections["read_strobe"] is not None:
                m.d.comb += connections["read_strobe"].eq(
                    word_edge & ~is_write & hit)
            if connections["elaborate"]:
                connections["elaborate"](m)

        return m
