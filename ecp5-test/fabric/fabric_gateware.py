#!/usr/bin/env python3
#
# Self-checking large-utilisation design, to ask whether an LFE5U-12F's
# fabric beyond the advertised 12,288 LUTs actually computes correctly.
# See awtoau/pluribus#98.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Fills ~80% of the LFE5U-25F die on a part marked LFE5U-12F, and checks the
result against a golden value computed on the host.

The question this answers is narrow. LFE5U-12F and LFE5U-25F are the same die
and the open flow already hands a `--12k` target all 24,288 LUTs. Three
explanations remain: the die is whole and the extra fabric works; 12F parts are
salvage that failed test in the extra region; or something is fused off
separately. Only the second is dangerous, and it is dangerous specifically
because it would fail *intermittently* -- so this design is built to be
self-checking and to run indefinitely, not to blink an LED once.

Structure
---------

`BLOCKS` independent blocks. Each block holds a 32-bit state and advances it
every cycle by:

  1. a Galois LFSR step with a per-block polynomial, then
  2. a fixed combinational mix: XOR of three rotations of the state, then
     AND and OR of two more pairs of rotations, then a per-block constant.

Step 1 alone would be nearly free -- 32 flops and a handful of LUTs -- which is
exactly why it is not sufficient. Step 2 is the part that costs fabric: every
output bit depends on seven state bits, which no single LUT4 can do, so each bit
becomes a small tree. That, times 32 bits times `BLOCKS`, is what pushes the
design past 12,288 LUTs.

Both steps are pure integer arithmetic on a 32-bit word, so `block_step()` below
is simultaneously the specification, the Python golden model, and a readable
description of the hardware. There is no second implementation to drift.

Rounds, and why the states restart
----------------------------------

A round is `ROUND_CYCLES` advances of every block, after which the XOR of all
block states is latched as the round's signature -- and every block is reloaded
with its seed, so the next round recomputes the *same* function.

That reload is the design's central compromise, and it is deliberate. The
hardware advances 100 blocks per cycle at 60 MHz; the host's golden model
manages about 86,000 cycles per second. The hardware is roughly 700 times
faster, so a scheme where round N's value depends on rounds 1..N-1 gives a
golden model that can verify only a brief prefix and then falls permanently
behind -- the part would run for an hour unchecked, which is precisely the case
this test exists to catch.

With the reload, one golden value covers every round forever. The hardware can
therefore check itself, against a build-time constant, on every round without
the host present, and it can do so for as long as it is powered. The cost is
that a fault is not *accumulated* across rounds; it is caught in the round in
which it occurs, and latched stickily so it survives.

Intermittency is still covered: a bad bit anywhere in a round changes that
round's signature, because the signature is an XOR over all 32 bits of all
blocks and the mix diffuses any single-bit error across the word within a few
cycles. What is lost is only the ability to say *which* cycle went wrong, which
was never the question.

Why it cannot be optimised away
-------------------------------

  * Every block's state is XORed into the signature, which is read over JTAG
    and also drives the LEDs. Nothing is dangling, so yosys cannot prune a
    block.
  * Every block has a distinct polynomial *and* a distinct seed *and* a distinct
    mix constant. Identical blocks would be merged by resource sharing; these
    cannot be, because no two compute the same function of the same inputs.
  * The reload is to the seed, not to zero, so the states never collapse to a
    common value that would let the tools share logic between blocks.
  * The post-synthesis LUT count is checked by the build script against a
    floor, so "it used the extra fabric" is a measured number, and a build that
    quietly shrank below the target fails rather than passing quietly.

What the host sees
------------------

  REG_ID          APPLET_ID, so a stale bitstream is not mistaken for this one
  REG_SIGNATURE   XOR of all block states, latched at the end of each round
  REG_ROUNDS      completed rounds, 32-bit wrapping
  REG_MISMATCHES  rounds whose signature did not match the golden constant
  REG_STATUS      bit 0 busy, bit 1 done, bit 2 sticky mismatch,
                  bit 3 negative control, bits 15:8 the block count,
                  bits 31:16 the sync clock in MHz
  REG_GOLDEN      the golden constant the gateware was built with
  REG_DIE         bits 7:0 the DTR temperature code, bit 8 a DTR is present
  REG_CONTROL     bit 0 go, bit 1 compare against the complemented golden

The sticky mismatch bit and the mismatch counter are set by the *gateware*, not
the host. A failure that happens between two JTAG polls is still recorded, and
the count says how many rounds were bad rather than merely that one was.
"""

from amaranth import (Cat, ClockDomain, ClockSignal, Const, Elaboratable,
                      Instance, Module, Signal)

from luna.gateware.architecture.car import LunaECP5DomainGenerator

from bist import BISTAddresses, BISTHarness


# The clock this runs at. 60 MHz is the rate the board's PLL already produces
# for `sync` in every other design here, so it is the rate whose timing closure
# is a known quantity -- a fabric result confounded by a clock nobody has run
# before would be a worse experiment, not a better one.
CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}
SYNC_MHZ = 60

# How many 32-bit blocks.
#
# This number was measured, not guessed. Two trial builds gave 1,527 LUT4s at
# 10 blocks and 3,619 at 30, so the marginal cost is 104.6 LUT4s per block over
# a fixed 481 for the PLL, the JTAG register interface and the LEDs. 185 blocks
# therefore lands near 19,900 of the 24,288 the die offers -- about 82%.
#
# The target is roughly 80% rather than as high as it will go. Past that,
# routing congestion starts to be the thing that fails, and a build that will
# not place is evidence about nextpnr, not about whether the silicon computes
# correctly. Leaving headroom keeps the experiment pointed at the question.
BLOCKS = 185

# Cycles per round, as a power of two so the boundary is one counter bit.
#
# This is a counter width, not a delay: nothing waits on it. The value trades
# two things off. Larger means more fabric activity per checked result, and a
# host golden model that takes longer to compute once at build time. Smaller
# means more checks per second but a larger share of each round spent in the
# signature tree's pipeline latency.
#
# 2**18 = 262,144 cycles is 4.4 ms of hardware time at 60 MHz, and about 3
# seconds of host time to compute the golden value once. So the hardware
# performs roughly 228 fully-checked rounds per second, and a minute of running
# is on the order of 13,000 independent verdicts.
ROUND_BITS = 18
ROUND_CYCLES = 1 << ROUND_BITS

APPLET_ID = 0x46414252   # "FABR"

# Register 0 is reserved by JTAGRegisterInterface for size auto-negotiation.
REG_ID         = 1
REG_SIGNATURE  = 2
REG_ROUNDS     = 3
REG_STATUS     = 4
REG_GOLDEN     = 5
REG_MISMATCHES = 6
REG_DIE        = 7
REG_CONTROL    = 8

MASK = 0xFFFFFFFF

# How often a temperature conversion is started, as a power of two `sync`
# cycles. This is not a delay and nothing waits on it: it is the width of a
# free-running counter whose wrap issues STARTPULSE. 2**19 at 60 MHz is 8.7 ms,
# which is far slower than the block's 8-cycle conversion and far faster than
# the die's thermal time constant, so the reading is never stale and never
# retriggered mid-conversion. The same figure `riscv/gateware_id.py` uses.
DTR_PERIOD_BITS = 19

# In REG_DIE: this build contains a DTR block at all. Without it, "0" from a
# simulation build and "0" from a conversion that never completed would read
# the same, and a temperature of 0 would be indistinguishable from no
# temperature.
DIE_PRESENT = 1 << 8


def rotl(value, amount):
    """32-bit rotate left."""
    amount &= 31
    if amount == 0:
        return value & MASK
    return ((value << amount) | (value >> (32 - amount))) & MASK


def signature_layout(blocks, topology_seed=0):
    """Return ``(block order, per-block rotations)`` for one topology.

    Zero deliberately preserves the original design. Non-zero seeds use a
    tiny, specified 32-bit generator rather than Python's ``random`` module so
    a manifest remains reproducible across Python releases and machines.

    Reordering changes which block states meet at each registered XOR node;
    rotating changes which physical state bit reaches each bit of that tree.
    Both operations are wiring, not new test logic, so they diversify routing
    without reducing the 185 independent nonlinear blocks or making a quick
    go/no-go result weaker.
    """
    if blocks < 1:
        raise ValueError("blocks must be positive")
    if topology_seed == 0:
        return list(range(blocks)), [0] * blocks

    state = topology_seed & MASK

    def next_u32():
        nonlocal state
        # Numerical Recipes LCG. The constants and 32-bit truncation are part
        # of the file format: do not replace this with an implementation whose
        # sequence can vary between runtimes.
        state = (1664525 * state + 1013904223) & MASK
        return state

    rotations = [next_u32() & 31 for _ in range(blocks)]
    order = list(range(blocks))
    for index in range(blocks - 1, 0, -1):
        other = next_u32() % (index + 1)
        order[index], order[other] = order[other], order[index]
    return order, rotations


# Maximal-length Galois tap sets for 32-bit LFSRs. Any of these gives period
# 2**32-1; using several means no two blocks share a recurrence.
POLYNOMIALS = [
    0x80000057, 0x80000062, 0x8000006A, 0x80000091, 0x800000B8,
    0x800000C2, 0x800000D6, 0x800000E1, 0x8000012D, 0x80000108,
    0x8000015D, 0x80000162, 0x8000018E, 0x800001A6, 0x800001B4,
    0x800001DC, 0x800001EA, 0x8000021C, 0x8000022C, 0x80000232,
]


def block_params(index):
    """(polynomial, seed, mix constant) for one block.

    All three differ per block. The polynomials come from the table above so
    every block has full period; the seeds and constants are derived by an
    odd-multiplier hash of the index, which makes them distinct and non-zero
    without another table.

    Distinctness is a correctness requirement, not decoration: two blocks with
    the same polynomial and the same seed compute the same sequence, and yosys
    will happily keep one copy and wire it to both. The design would then report
    a full LUT count in source and a fraction of it in silicon.
    """
    poly = POLYNOMIALS[index % len(POLYNOMIALS)]
    # 0x9E3779B9 is the golden-ratio odd constant; multiplying an index by an
    # odd number mod 2**32 is a bijection, so no two indices collide, and the
    # `| 1` guarantees a non-zero seed (a Galois LFSR at zero is stuck there).
    seed = (((index + 1) * 0x9E3779B9) & MASK) | 1
    mix = ((index + 1) * 0x85EBCA6B) & MASK
    return poly, seed, mix


def block_step(state, poly, mix):
    """One cycle of one block. The specification, and the golden model.

    Galois step: shift right, and XOR the polynomial in when the bit shifted
    out was set. Then the mix, whose only job is to cost LUTs while staying
    exactly reproducible in 32-bit integer arithmetic.
    """
    lsb = state & 1
    state >>= 1
    if lsb:
        state ^= poly

    # Seven state bits reach every output bit. Rotations are free in fabric
    # (pure wiring); it is the AND/OR/XOR combining of them that occupies logic,
    # and seven inputs cannot fit one LUT4, so each bit becomes a small tree.
    mixed = rotl(state, 7) ^ rotl(state, 13) ^ rotl(state, 23)
    mixed ^= rotl(state, 3) & rotl(state, 17)
    mixed ^= rotl(state, 11) | rotl(state, 29)
    return (state ^ mixed ^ mix) & MASK


class FabricBlock(Elaboratable):
    """One 32-bit block: Galois LFSR plus the LUT-hungry mix.

    `reload` returns the state to its seed, which is how a round restarts.
    """

    def __init__(self, poly, seed, mix):
        self.poly = poly
        self.seed = seed
        self.mix = mix
        self.state = Signal(32, init=seed)
        self.reload = Signal()

    def elaborate(self, platform):
        m = Module()
        state = self.state

        # Galois step, written so it is visibly the same operation as
        # `block_step` above: shift right, conditionally XOR the polynomial.
        shifted = Signal(32)
        m.d.comb += shifted.eq((state >> 1) ^ (Const(self.poly, 32) & state[0].replicate(32)))

        def rot(amount):
            amount &= 31
            if amount == 0:
                return shifted
            return Cat(shifted[32 - amount:], shifted[:32 - amount])

        mixed = Signal(32)
        m.d.comb += mixed.eq(
            (rot(7) ^ rot(13) ^ rot(23))
            ^ (rot(3) & rot(17))
            ^ (rot(11) | rot(29))
        )

        with m.If(self.reload):
            m.d.sync += state.eq(self.seed)
        with m.Else():
            m.d.sync += state.eq(shifted ^ mixed ^ Const(self.mix, 32))
        return m


class FabricTest(Elaboratable):
    """The whole design: BLOCKS blocks, a signature, and a JTAG window."""

    def __init__(self, blocks=BLOCKS, round_bits=ROUND_BITS, golden=None,
                 simulate=False, topology_seed=0, tree_fanin=4):
        self.blocks = blocks
        self.round_bits = round_bits
        if tree_fanin not in (2, 3, 4):
            raise ValueError("tree_fanin must be 2, 3 or 4")
        self.topology_seed = topology_seed & MASK
        self.tree_fanin = tree_fanin
        # `simulate` omits the PLL and the JTAG primitive, which are ECP5
        # hard blocks that need a platform. Everything under test -- the
        # blocks, the signature tree, the round timing and the self-check --
        # is the same logic either way, so what the simulator verifies is what
        # the hardware runs. It is not a separate description.
        self.simulate = simulate
        # The golden signature for one round, baked in so the gateware can
        # latch its own mismatch without the host being present. None disables
        # the self-check, which is only useful for a utilisation-only build.
        self.golden = golden

        # Exposed so a simulation can observe the result without shifting JTAG.
        # These are the same signals the registers below read, not a parallel
        # copy: a testbench that watched a duplicate could pass while the
        # register the host reads showed something else.
        self.signature = Signal(32)
        self.rounds = Signal(32)
        self.mismatches = Signal(32)
        self.mismatch = Signal()

    def elaborate(self, platform):
        m = Module()

        if not self.simulate:
            m.submodules.clocking = LunaECP5DomainGenerator(
                clock_frequencies=CLOCK_FREQUENCIES)

        harness = BISTHarness(
            applet_id=APPLET_ID,
            addresses=BISTAddresses(
                ident=REG_ID, control=REG_CONTROL, status=REG_STATUS,
                checks=REG_ROUNDS, errors=REG_MISMATCHES,
                actual=REG_SIGNATURE, golden=REG_GOLDEN),
            negative_control=False, simulate=self.simulate)
        m.submodules.harness = harness

        #
        # Round timing.
        #
        # The counter runs from 0 to ROUND_CYCLES-1. `last` is the final cycle,
        # so the state written on that clock edge is the seed again -- meaning
        # each block performs exactly ROUND_CYCLES-1 advances from its seed
        # before the reload, and the signature must be sampled from the state
        # *as it was on that cycle*, before the reload takes effect.
        #
        cycle = Signal(self.round_bits)
        m.d.sync += cycle.eq(cycle + 1)

        last = Signal()
        m.d.comb += last.eq(cycle == (1 << self.round_bits) - 1)

        #
        # The blocks.
        #
        states = []
        for index in range(self.blocks):
            poly, seed, mix = block_params(index)
            block = FabricBlock(poly, seed, mix)
            m.submodules[f"block_{index}"] = block
            m.d.comb += block.reload.eq(last)
            states.append(block.state)

        #
        # Signature. A balanced XOR tree over every bit of every block, so no
        # block is dangling and none can be pruned. Registered in stages
        # because a 100-input XOR per bit is deep enough to be the critical
        # path otherwise, and a design that fails timing would then be telling
        # us about the tree rather than about the fabric under test.
        #
        order, rotations = signature_layout(self.blocks, self.topology_seed)
        layer = []
        for index in order:
            amount = rotations[index]
            state = states[index]
            if amount:
                state = Cat(state[32 - amount:], state[:32 - amount])
            layer.append(state)
        depth = 0
        while len(layer) > 1:
            nxt = []
            for i in range(0, len(layer), self.tree_fanin):
                group = layer[i:i + self.tree_fanin]
                node = Signal(32, name=f"xor_s{depth}_{i // self.tree_fanin}")
                acc = group[0]
                for extra in group[1:]:
                    acc = acc ^ extra
                m.d.sync += node.eq(acc)
                nxt.append(node)
            layer = nxt
            depth += 1
        live = layer[0]

        # The tree is `depth` registered stages, so `live` this cycle is the
        # XOR of the states as they were `depth` cycles ago. To latch the
        # signature of the intended cycle, the sample pulse is `last` delayed by
        # the same `depth`. Getting this wrong would produce a stable but wrong
        # signature -- a mismatch caused by the measurement rather than by the
        # silicon, which is the single most misleading failure this design
        # could have.
        pipe = Signal(depth)
        m.d.sync += pipe.eq(Cat(last, pipe[:-1]))
        sample = pipe[-1]

        signature = self.signature
        with m.If(sample):
            m.d.sync += signature.eq(live)

        # The recurrence computes both sides of its own measurement. The
        # harness owns the sticky verdict, counters, control and JTAG view.
        m.d.comb += [
            harness.busy.eq(1),
            harness.done.eq(sample),
            harness.check.eq(sample & (self.golden is not None)),
            harness.actual.eq(live),
            harness.golden.eq(self.golden if self.golden is not None else 0),
            harness.status_extra.eq(Cat(Const(0, 4), Const(self.blocks, 8),
                                        Const(SYNC_MHZ, 16))),
            self.rounds.eq(harness.checks),
            self.mismatches.eq(harness.errors),
            self.mismatch.eq(harness.error),
        ]

        #
        # Host window.
        #
        #
        # Die temperature.
        #
        # The result of this test is "one part, one operating point, one
        # moment", and without this the operating point is the part that has to
        # be taken on trust. Reading the DTR makes it data: a sweep of
        # configurations then carries the temperature each one ran at, and a
        # mismatch that correlates with temperature is a marginal part, while a
        # mismatch that does not is a hard defect. The current test cannot tell
        # those apart at all.
        #
        # It costs a counter, a latch and a hard block that consumes no fabric,
        # against ~19,900 LUTs -- and it is entirely outside the signature path,
        # so it cannot change the answer the test is checking.
        #
        # FPGA-TN-02210 Table 4.3 maps the 6-bit code to a temperature range.
        # The code is reported raw; converting it here would present an
        # uncalibrated lookup as a measurement in degrees.
        #
        if platform is not None and not self.simulate:
            dtr_counter = Signal(DTR_PERIOD_BITS)
            m.d.sync += dtr_counter.eq(dtr_counter + 1)

            dtr_bits = [Signal(name=f"dtrout{index}") for index in range(8)]
            m.submodules.dtr = Instance(
                "DTR",
                i_STARTPULSE=(dtr_counter == 0),
                **{f"o_DTROUT{index}": bit
                   for index, bit in enumerate(dtr_bits)})

            # DTROUT[7] is the valid flag. Sampling without it latches whatever
            # the outputs hold part-way through a conversion, which is a number
            # that looks like a temperature and is not one.
            die = Signal(8)
            with m.If(dtr_bits[7]):
                m.d.sync += die.eq(Cat(*dtr_bits))

            harness.add_read_only_register(
                REG_DIE, read=Cat(die, Const(1, 1), Const(0, 23)))
        elif not self.simulate:
            harness.add_read_only_register(REG_DIE, read=Const(0, 32))

        #
        # LEDs. Not the evidence -- the JTAG registers are -- but a board that
        # can be looked at is a board whose state can be sanity-checked without
        # a host, and a mismatch that only exists in a register nobody read is
        # not much of an alarm.
        #
        # Red alone, steady: sticky mismatch latched.
        # Green walking across the six: running, no mismatch yet.
        #
        # LEDs are declared invert=True on this platform, so 1 lights them.
        #
        if platform is not None:
            leds = Cat(platform.request("led", n).o for n in range(6))
            # A plain counter, not signature bits: the display then reports
            # "the clock is running" independently of whether the data is right,
            # so a wedged design and a wrong-answer design look different.
            # A free-running counter divides the 60 MHz clock down to something
            # an eye can follow: bit 22 toggles at about 7 Hz, so `step` advances
            # the walk roughly seven times a second.
            tick = Signal(23)
            m.d.sync += tick.eq(tick + 1)
            step = Signal()
            m.d.sync += step.eq(tick[-1])

            # An explicit 0..5 phase, not the top bits of the counter. A 3-bit
            # slice counts to 7, and the two extra phases light nothing once the
            # position is decoded -- giving two dark steps in every eight. A dark
            # LED bank is exactly how a wedged design looks, so the display must
            # not have a resting state that mimics failure.
            phase = Signal(range(6))
            with m.If(tick[-1] & ~step):
                with m.If(phase == 5):
                    m.d.sync += phase.eq(0)
                with m.Else():
                    m.d.sync += phase.eq(phase + 1)

            walk = Signal(6)
            for position in range(6):
                m.d.comb += walk[position].eq(phase == position)

            with m.If(harness.error):
                m.d.comb += leds.eq(0b000001)   # red alone, steady
            with m.Else():
                m.d.comb += leds.eq(walk)

        return m
