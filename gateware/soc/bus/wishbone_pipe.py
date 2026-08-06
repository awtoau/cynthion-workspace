#!/usr/bin/env python3
#
# A Wishbone stage that puts a flip-flop in the response path.
# SPDX-License-Identifier: BSD-3-Clause

"""
One register between a Wishbone initiator and its subordinates, on the way back.

## The path this exists to cut

Wishbone classic has no pipeline stage anywhere in it, so on this SoC a single
clock edge had to propagate all of:

    arbiter.grant (FF)
      -> the 3:1 address mux it selects
      -> the decoder's window compare, fanned out to eight subordinates
      -> each subordinate's ACK, gathered back into one OR tree
      -> the arbiter's ACK mux, back to the granted master
      -> VexiiRiscv's LSU: PMA check, fault, commit
      -> the register file write enable

nextpnr measured that at 16.45 ns -- 2.82 ns of logic and 13.64 ns of routing.
It is routing-dominated because the two ends are physically far apart and there
is nothing in between to stop at: the grant register sat at tile (47,9) and the
address mux it drives at (19,10), a single net costing 3.63 ns, and the return
trip then crossed the die twice more before reaching the CPU.

Deeper logic optimisation cannot help a path that is 83% wire. The only thing
that shortens a wire is having somewhere to stop, and that is what this is.

The path splits into two roughly equal halves, each ending or starting at a
flip-flop here: grant to captured response, and captured response to commit.
Both were around 8 ns of the original 16.45.

## Why the response and not the request

The request side is already registered -- `adr`, `dat_w` and the rest come
straight out of the CPU's own pipeline registers, and `grant` is a flip-flop in
the arbiter. Registering them again would add latency and cut nothing, because
the long combinational run is the round trip: address out, decode, acknowledge
back. A register only helps where it interrupts that.

## The duplicate-request hazard, which is the whole subtlety

Wishbone classic is a full handshake: the initiator holds CYC and STB asserted
until it sees ACK, and drops them the cycle after. Delay ACK by one cycle
naively and the initiator holds STB one cycle longer than the subordinate
expects -- so the subordinate, which is still being strobed, performs the access
a second time.

For a RAM that is invisible. For this SoC it would be a catastrophe of exactly
the kind the register maps here were designed to avoid: a second strobe on a
16550's RBR pops another byte off the receive FIFO, and a second strobe on the
PLIC's claim register takes a second interrupt and never completes it. Both
would present as data disappearing at random under load, with every register
read looking correct in isolation.

So STB is withheld downstream for the one cycle in which the captured response
is being presented upstream. The subordinate sees exactly one strobe per
transfer, as it did before.

## Cost

One cycle per transfer, and a transfer is one bus beat -- including each beat of
a `cti`/`bte` burst, because a classic-mode initiator cannot issue the next beat
until it has seen the acknowledgement for this one. There is no way around that
short of a pipelined (Wishbone B4) initiator, and VexiiRiscv's Wishbone bridge
is not one.

Measured against `luna_soc`'s block RAM, in cycles from the strobe to the
acknowledgement (`scripts/soc_bus_sim.py`, which reports these rather than
asserting them):

    single transfer    1 cycle  -> 2
    burst, per beat    2 cycles -> 3

The block RAM already needed two cycles a beat -- it gates its own ACK with
`~ack` -- so a burst, which is how both caches refill, goes to 1.5x. Cache hits,
which is nearly all of what this CPU does, never reach the bus and are untouched.
The same run also checks that a burst still returns consecutive words in order,
that ERR still terminates a cycle, and that no subordinate is strobed twice.

Bursts keep working for a reason worth stating: in a Wishbone registered-feedback
burst the INITIATOR supplies the address of every beat. The block RAM's internal
increment is only a speculative prefetch to hide its read latency, so losing the
prefetch (this stage drops STB for a cycle, and the RAM falls back to the address
on the bus) costs a cycle and cannot corrupt anything.
"""

from amaranth              import Module, Signal
from amaranth.lib          import wiring
from amaranth.lib.wiring   import In, Out

from amaranth_soc          import wishbone


class RegisteredResponse(wiring.Component):
    """Pass requests through combinationally; return responses from a register.

    Connect it the way it reads: the initiator side to whatever was driving the
    subordinates, the subordinate side to what was being driven.

        m.submodules.pipe = pipe = RegisteredResponse(...)
        wiring.connect(m, arbiter.bus, pipe.intr_bus)
        wiring.connect(m, pipe.sub_bus, decoder.bus)
    """

    def __init__(self, *, addr_width, data_width, granularity=None,
                 features=frozenset()):
        # The same signature on both sides. This is a repeater, not a shim: it
        # changes when signals arrive, never what they mean, so a feature set
        # that differs between the two would be a different component.
        signature = dict(addr_width=addr_width, data_width=data_width,
                         granularity=granularity, features=features)
        super().__init__({
            "intr_bus": In(wishbone.Signature(**signature)),
            "sub_bus": Out(wishbone.Signature(**signature)),
        })

    def elaborate(self, platform):
        m = Module()

        intr = self.intr_bus
        sub  = self.sub_bus

        # The captured response. `answered` is high for exactly one cycle per
        # transfer: it is set from a subordinate response that arrived while
        # this stage was strobing, and clears immediately because withholding
        # STB (below) removes the condition that set it.
        ack_reg   = Signal()
        err_reg   = Signal() if hasattr(intr, "err") else None
        dat_r_reg = Signal(intr.dat_r.shape())

        answered = ack_reg | (err_reg if err_reg is not None else 0)

        # Whether the subordinate has answered, from the subordinate's point of
        # view. ERR terminates a cycle exactly as ACK does, so a stage that
        # captured only ACK would hold a faulting access open forever.
        sub_done = sub.ack | (sub.err if hasattr(sub, "err") else 0)

        m.d.comb += [
            # CYC frames the whole transaction and is not withheld: dropping it
            # mid-cycle would tell the subordinate the initiator went away.
            sub.cyc.eq(intr.cyc),

            # STB is. See the duplicate-request hazard above -- this single
            # `& ~answered` is the difference between a delayed acknowledgement
            # and a second access to a register whose read has side effects.
            sub.stb.eq(intr.stb & ~answered),

            sub.adr.eq(intr.adr),
            sub.sel.eq(intr.sel),
            sub.we.eq(intr.we),
            sub.dat_w.eq(intr.dat_w),

            intr.ack.eq(ack_reg),
            intr.dat_r.eq(dat_r_reg),
        ]

        # The burst descriptors, forwarded unchanged. They are `Out` on the
        # initiator side and must reach the subordinate in the same cycle as the
        # address they describe, or a burst becomes a sequence of unrelated
        # classic cycles that happen to have consecutive addresses -- which is
        # slower and, on a subordinate that treats the two differently, wrong.
        if hasattr(sub, "cti"):
            m.d.comb += sub.cti.eq(intr.cti)
        if hasattr(sub, "bte"):
            m.d.comb += sub.bte.eq(intr.bte)
        if hasattr(sub, "lock"):
            m.d.comb += sub.lock.eq(intr.lock)
        if err_reg is not None:
            m.d.comb += intr.err.eq(err_reg)

        # Capture. The strobe is part of the condition because a subordinate is
        # entitled to leave ACK asserted after the cycle it belongs to; only an
        # acknowledgement seen while this stage is actually strobing is one.
        capture = sub.cyc & sub.stb & sub_done
        m.d.sync += ack_reg.eq(sub.cyc & sub.stb & sub.ack)
        if err_reg is not None:
            m.d.sync += err_reg.eq(sub.cyc & sub.stb & sub.err)

        # DAT_R has to be registered, not forwarded.
        #
        # It is tempting to leave it combinational, since the subordinate
        # presents it alongside ACK and this stage delays only the ACK. That is
        # wrong for a subordinate whose read data is a function of the strobe:
        # `luna_soc`'s block RAM drives its read port address only inside
        # `If(cyc & stb)`, so the cycle this stage withholds STB it addresses
        # word zero, and the initiator would sample the first word of memory in
        # place of whatever it asked for. Which is data corruption that only
        # appears once something reads a location that is not zero.
        #
        # Enabled rather than free-running, so the value stays valid for the
        # whole cycle it is presented and costs a clock enable rather than a mux.
        with m.If(capture):
            m.d.sync += dat_r_reg.eq(sub.dat_r)

        return m
