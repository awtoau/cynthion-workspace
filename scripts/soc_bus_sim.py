#!/usr/bin/env python3
#
# Simulate the Wishbone response register, and measure what it costs.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves `wishbone_pipe.RegisteredResponse` is transparent, and prices it.

    ./scripts/soc_bus_sim.py           # run every check
    ./scripts/soc_bus_sim.py -v        # and print every bus cycle

Exit status 0 if every assertion held. Output goes to the terminal and to
`tmp/logs/dev.log`.

## Why this is worth a file

`RegisteredResponse` was added to break a 16.45 ns combinational path that ran
from the arbiter's grant register, through the address decode, back through
eight subordinates' acknowledgements and into VexiiRiscv's commit logic. It buys
that by delaying the acknowledgement one cycle, and delaying an acknowledgement
on a full-handshake bus is exactly the change that can quietly make a
subordinate perform every access twice -- the initiator holds STB one cycle
longer than the subordinate expects.

On this SoC a repeated access is not a harmless repeat. The 16550's RBR pops a
byte off the receive FIFO when read and the PLIC's claim register takes an
interrupt when read, so a duplicated strobe would lose console input and
interrupts at random under load, while every register still read correctly by
hand. That failure is essentially undebuggable from a console, and it is what
the `no_duplicate_strobe` check below exists to rule out before a bitstream is
built.

The rest is arithmetic that should be measured rather than asserted: the same
run reports how many cycles a transfer and a burst beat take with and without
the stage, which is where the "1.5x on bus-bound work" figure in
`bus/wishbone_pipe.py` comes from.

## What this cannot say

The initiator here is this file, not VexiiRiscv. It says the stage obeys
Wishbone; it does not say the CPU's bridge does. The evidence for the pair is a
bitstream that runs the shell -- `scripts/soc_run.py` and `scripts/soc_shell.py`.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth              import Module, Signal            # noqa: E402
from amaranth.hdl          import Fragment                  # noqa: E402
from amaranth.lib          import wiring                    # noqa: E402
from amaranth.lib.wiring   import In                        # noqa: E402
from amaranth.sim          import Simulator                 # noqa: E402

from amaranth_soc          import wishbone                  # noqa: E402

# The import order the SoC needs, and for the same reason: amaranth_soc is
# vendored inside luna_soc, and importing a luna_soc peripheral is what aliases
# it onto sys.modules under the bare name.
from luna_soc.gateware.core import blockram                 # noqa: E402

from amaranth_soc.memory   import MemoryMap                    # noqa: E402
from amaranth_soc.wishbone import Decoder                      # noqa: E402

from bus.wishbone_pipe import RegisteredResponse                # noqa: E402
from bus.fault import BusFault, worst_ack_cycles                # noqa: E402
from peripherals.flash import READ_MODES                        # noqa: E402

# A small memory with distinct, non-zero words.
#
# Non-zero matters. `luna_soc`'s block RAM drives its read port address only
# while it is being strobed, so a stage that forwarded DAT_R combinationally
# instead of registering it would return word zero -- and against an all-zero
# or zero-based memory that is indistinguishable from working.
RAM_WORDS = [0xa5a50000 + n for n in range(16)]


class SideEffectTarget(wiring.Component):
    """A subordinate that counts how many times it is asked to do something.

    `requests` counts cycles in which an unanswered request is being presented,
    which is the condition every subordinate in this SoC uses to decide to act:
    pop a FIFO, take an interrupt, start a flash transfer. One transfer must
    produce exactly one of them.

    That is the number a mis-designed response register changes. Delay ACK by a
    cycle without also withholding STB and this counts two, because the
    initiator is still strobing while the delayed answer is in flight.
    """

    bus: In(wishbone.Signature(addr_width=4, data_width=32, granularity=8,
                               features={"cti", "bte", "err"}))

    def __init__(self, *, fail=False):
        # `fail` answers with ERR instead of ACK, which terminates a Wishbone
        # cycle just as ACK does. A stage that captured only ACK would leave a
        # faulting access outstanding forever, so this is checked rather than
        # assumed.
        self.fail = fail
        self.requests = Signal(8)
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        answering = self.bus.ack | self.bus.err
        asked = self.bus.cyc & self.bus.stb & ~answering

        with m.If(asked):
            m.d.sync += self.requests.eq(self.requests + 1)

        # One cycle of latency, like every registered subordinate here.
        if self.fail:
            m.d.sync += self.bus.err.eq(asked)
        else:
            m.d.sync += self.bus.ack.eq(asked)
            m.d.comb += self.bus.dat_r.eq(0xdecafbad)

        return m


class SlowTarget(wiring.Component):
    """A subordinate that answers after `latency` cycles, or never.

    `latency=None` is the fault the address compare cannot see: a peripheral the
    decoder claims, that has stopped answering. Nothing in the SoC's fabric
    notices, and the CPU waits for it forever.
    """

    bus: In(wishbone.Signature(addr_width=4, data_width=32, granularity=8,
                               features={"cti", "bte", "err"}))

    def __init__(self, *, latency):
        self.latency = latency
        super().__init__()
        # A window is a window whatever is behind it: the decoder needs a map to
        # place one, and that map is what `BusFault` compares against.
        memory_map = MemoryMap(addr_width=6, data_width=8)
        memory_map.add_resource(name=("slow",), size=64, resource=self)
        self.bus.memory_map = memory_map

    def elaborate(self, platform):
        m = Module()
        if self.latency is None:
            return m

        # The counter must NOT depend on ACK, or `ack = f(~ack)` is a
        # combinational loop and the simulator hangs rather than failing.
        active = self.bus.cyc & self.bus.stb
        waited = Signal(range(self.latency + 1))
        with m.If(~active):
            m.d.sync += waited.eq(0)
        with m.Elif(waited != self.latency):
            m.d.sync += waited.eq(waited + 1)

        m.d.comb += [
            self.bus.ack.eq(active & (waited == self.latency)),
            self.bus.dat_r.eq(0x5107_0000),
        ]
        return m


# Where the two windows sit in the harness decoder, and one address between
# them that neither claims. Byte addresses, as `Decoder.add` takes them; the
# initiator drives word addresses, hence the >> 2.
RAM_WINDOW = 0x0000
SLOW_WINDOW = 0x1000
UNMAPPED = 0x0800


def decoder_harness(*, guarded, timeout=8, latency=1):
    """Two windows behind a decoder, optionally behind `BusFault` as well."""
    m = Module()

    ram = blockram.Peripheral(size=64, init=RAM_WORDS)
    m.submodules.ram = ram
    slow = SlowTarget(latency=latency)
    m.submodules.slow = slow

    signature = dict(addr_width=16, data_width=32, granularity=8,
                     features={"cti", "bte", "err"})
    decoder = Decoder(**signature)
    m.submodules.decoder = decoder
    decoder.add(ram.bus, addr=RAM_WINDOW, name="ram")
    decoder.add(slow.bus, addr=SLOW_WINDOW, name="slow")

    if not guarded:
        return m, decoder.bus, None

    fault = BusFault(decoder=decoder, timeout=timeout, **signature)
    m.submodules.fault = fault
    wiring.connect(m, fault.sub_bus, decoder.bus)
    return m, fault.intr_bus, fault


class Initiator:
    """A Wishbone classic initiator, driven from a testbench.

    Deliberately strict about the handshake: CYC and STB are asserted together
    and held until ACK or ERR is seen, and the address for the next beat of a
    burst is presented only after the previous beat has been answered. That is
    what VexiiRiscv's bridge does, and a looser model here would pass a stage
    that a real CPU hangs on.
    """

    def __init__(self, ctx, bus, verbose=False, limit=32):
        self.ctx = ctx
        self.bus = bus
        self.verbose = verbose
        self.limit = limit

    def _present(self, adr, we, dat_w, cti, bte):
        ctx = self.ctx
        ctx.set(self.bus.cyc, 1)
        ctx.set(self.bus.stb, 1)
        ctx.set(self.bus.adr, adr)
        ctx.set(self.bus.we, we)
        ctx.set(self.bus.dat_w, dat_w)
        ctx.set(self.bus.sel, 0b1111)
        ctx.set(self.bus.cti, cti)
        ctx.set(self.bus.bte, bte)

    async def _await_answer(self):
        """Tick until the bus answers, and say how many ticks that took."""
        ctx = self.ctx
        cycles = 0
        # Bounded so a stage that never answers fails the run instead of
        # hanging it. 32 is far beyond anything correct here: the deepest path
        # is one subordinate cycle plus one stage cycle. `limit` raises it for
        # the bus-timeout checks, which have to outlast the bound being tested.
        while cycles < self.limit:
            await ctx.tick()
            cycles += 1
            errored = hasattr(self.bus, "err") and ctx.get(self.bus.err)
            if ctx.get(self.bus.ack) or errored:
                return cycles, ctx.get(self.bus.dat_r), bool(errored)
        return cycles, None, False

    async def _release(self):
        ctx = self.ctx
        ctx.set(self.bus.cyc, 0)
        ctx.set(self.bus.stb, 0)
        await ctx.tick()

    async def single(self, adr, *, we=0, dat_w=0):
        """One classic cycle. Returns (cycles to answer, data, errored)."""
        self._present(adr, we, dat_w, cti=0, bte=0)
        answer = await self._await_answer()
        await self._release()
        if self.verbose:
            print(f"      {'write' if we else 'read '} {adr:#04x} -> "
                  f"{answer[1]} in {answer[0]} cycles")
        return answer

    async def burst(self, adr, beats):
        """An incrementing-address burst. Returns (per-beat cycles, words).

        CTI is INCR_BURST (0b010) for every beat but the last, which is
        END_OF_BURST (0b111); BTE is LINEAR. That is what the block RAM's
        prefetch looks for, so a burst that got this wrong would silently
        measure a sequence of classic cycles instead.
        """
        cycles, words = [], []
        for beat in range(beats):
            cti = 0b111 if beat == beats - 1 else 0b010
            self._present(adr + beat, we=0, dat_w=0, cti=cti, bte=0)
            taken, data, _ = await self._await_answer()
            cycles.append(taken)
            words.append(data)
        await self._release()
        if self.verbose:
            print(f"      burst {adr:#04x} x{beats} -> {words} in {cycles}")
        return cycles, words


def run(dut, testbench):
    sim = Simulator(Fragment.get(dut, None))
    # 1 us is arbitrary and irrelevant: nothing here is a timing simulation,
    # and every measurement below is in cycles.
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def ram_harness(piped):
    """A block RAM, optionally behind the stage. Returns (module, bus, ram)."""
    m = Module()
    ram = blockram.Peripheral(size=64, init=RAM_WORDS)
    m.submodules.ram = ram

    if not piped:
        return m, ram.bus, ram

    # The RAM has no ERR, so the stage is built without one here. In the SoC it
    # has ERR, and `bus/fault.BusFault` is what raises it -- the decoder itself
    # raises ERR for nothing at all, which is what the checks below establish.
    pipe = RegisteredResponse(addr_width=4, data_width=32, granularity=8,
                              features={"cti", "bte"})
    m.submodules.pipe = pipe
    wiring.connect(m, pipe.sub_bus, ram.bus)
    return m, pipe.intr_bus, ram


def measure_ram(piped, verbose):
    """Cycles and data for a single read and a four-beat burst."""
    m, bus, _ = ram_harness(piped)
    result = {}

    async def testbench(ctx):
        initiator = Initiator(ctx, bus, verbose)
        # Address 3, not 0: a stage that returned word zero would pass at 0.
        result["single"] = await initiator.single(3)
        result["burst"] = await initiator.burst(4, beats=4)

    run(m, testbench)
    return result


def run_checks(checks, verbose):
    plain = measure_ram(piped=False, verbose=verbose)
    piped = measure_ram(piped=True, verbose=verbose)

    # --- the stage is transparent ---------------------------------------
    checks.check(
        "a single read returns the same word through the stage",
        piped["single"][1] == plain["single"][1] == RAM_WORDS[3],
        f"plain {plain['single'][1]!r}, piped {piped['single'][1]!r}, "
        f"expected {RAM_WORDS[3]:#010x}. A word of {RAM_WORDS[0]:#010x} means "
        f"DAT_R was forwarded combinationally and sampled while the block RAM "
        f"was not being strobed, so it addressed word zero.")

    plain_cycles, plain_words = plain["burst"]
    piped_cycles, piped_words = piped["burst"]

    checks.check(
        "a cti/bte burst returns consecutive words in order through the stage",
        piped_words == plain_words == RAM_WORDS[4:8],
        f"plain {plain_words!r}, piped {piped_words!r}, "
        f"expected {RAM_WORDS[4:8]!r}")

    # --- and it costs exactly one cycle ---------------------------------
    #
    # The first beat of a burst is faster than the rest because the block RAM
    # starts from an idle bus, so the steady-state figure is the later beats.
    plain_beat = plain_cycles[-1]
    piped_beat = piped_cycles[-1]

    checks.check(
        "a single transfer costs one more cycle, and only one",
        piped["single"][0] == plain["single"][0] + 1,
        f"{plain['single'][0]} -> {piped['single'][0]} cycles")
    checks.check(
        "a burst beat costs one more cycle, and only one",
        piped_beat == plain_beat + 1,
        f"{plain_beat} -> {piped_beat} cycles per beat")

    checks.note(f"single transfer {plain['single'][0]} -> "
                f"{piped['single'][0]} cycles")
    checks.note(f"burst, per beat {plain_beat} -> {piped_beat} cycles "
                f"(all beats: {plain_cycles} -> {piped_cycles})")

    # --- the hazard the stage exists to avoid ---------------------------
    for piped_flag, label in ((False, "without the stage"), (True, "through the stage")):
        m = Module()
        target = SideEffectTarget()
        m.submodules.target = target
        if piped_flag:
            pipe = RegisteredResponse(addr_width=4, data_width=32, granularity=8,
                                      features={"cti", "bte", "err"})
            m.submodules.pipe = pipe
            wiring.connect(m, pipe.sub_bus, target.bus)
            bus = pipe.intr_bus
        else:
            bus = target.bus

        seen = {}

        async def testbench(ctx, bus=bus, target=target, seen=seen):
            initiator = Initiator(ctx, bus, verbose)
            await initiator.single(1)
            await initiator.single(2)
            await initiator.burst(0, beats=3)
            seen["requests"] = ctx.get(target.requests)

        run(m, testbench)

        # Two single transfers and a three-beat burst is five transfers, so a
        # subordinate that acts once per transfer has acted five times.
        checks.check(
            f"no duplicate strobe: five transfers, five accesses {label}",
            seen["requests"] == 5,
            f"the subordinate saw {seen['requests']} unanswered requests for 5 "
            f"transfers. More than five means STB was left asserted while a "
            f"delayed answer was in flight, and every side-effecting register "
            f"read on this SoC would happen twice.")

    # --- ERR terminates a cycle, exactly as ACK does --------------------
    m = Module()
    target = SideEffectTarget(fail=True)
    m.submodules.target = target
    pipe = RegisteredResponse(addr_width=4, data_width=32, granularity=8,
                              features={"cti", "bte", "err"})
    m.submodules.pipe = pipe
    wiring.connect(m, pipe.sub_bus, target.bus)

    seen = {}

    async def err_testbench(ctx):
        initiator = Initiator(ctx, pipe.intr_bus, verbose)
        seen["answer"] = await initiator.single(1)
        seen["requests"] = ctx.get(target.requests)

    run(m, err_testbench)

    cycles, _, errored = seen["answer"]
    checks.check(
        "an ERR reaches the initiator and ends the cycle",
        errored and cycles == piped["single"][0],
        f"errored={errored} after {cycles} cycles; a stage that captured only "
        f"ACK would leave this outstanding until the initiator's bound expired")
    checks.check(
        "an ERR is not retried",
        seen["requests"] == 1,
        f"the subordinate saw {seen['requests']} requests for one faulting "
        f"transfer")

    fault_checks(checks, verbose)


def one_access(*, guarded, adr, timeout=8, latency=1, limit=32, verbose=False):
    """One single transfer through the harness. Returns (answer, fault unit)."""
    m, bus, fault = decoder_harness(guarded=guarded, timeout=timeout,
                                    latency=latency)
    seen = {}

    async def testbench(ctx):
        initiator = Initiator(ctx, bus, verbose, limit=limit)
        seen["answer"] = await initiator.single(adr)
        if fault is not None:
            seen["unclaimed"] = ctx.get(fault.unclaimed_count)
            seen["timeouts"] = ctx.get(fault.timeout_count)
            seen["worst"] = ctx.get(fault.worst_wait)

    run(m, testbench)
    return seen


def fault_checks(checks, verbose):
    """`bus/fault.BusFault`: the two ways a cycle ends that otherwise would not.

    Every check here is paired with the run that shows it failing. The first
    pair is the defect itself -- the same access, with and without the unit --
    and neither passes by accident: an unguarded decoder cannot answer an
    unmapped address at all, and there is no configuration of it that can.
    """
    # --- THE DEFECT: the decoder alone answers an unmapped address with
    # --- nothing. Not ERR. Nothing.
    bare = one_access(guarded=False, adr=UNMAPPED >> 2, verbose=verbose)
    cycles, data, errored = bare["answer"]
    checks.check(
        "the decoder ALONE never answers an unmapped address, with ACK or ERR",
        cycles == 32 and data is None and not errored,
        f"answered after {cycles} cycles, errored={errored}. This check is the "
        f"#409 defect stated positively: `amaranth_soc.wishbone.Decoder` is one "
        f"Switch with no default, so an address in no window drives neither "
        f"signal and a classic initiator waits forever. A pass here means the "
        f"initiator gave up at its own bound, not that the bus answered.")

    # --- MECHANISM 1: the same access, with the unit in front of the decoder.
    guarded = one_access(guarded=True, adr=UNMAPPED >> 2, verbose=verbose)
    cycles, _data, errored = guarded["answer"]
    checks.check(
        "an unmapped address is answered ERR, in one cycle",
        errored and cycles == 1,
        f"errored={errored} after {cycles} cycles. The run above is the control: "
        f"the same address through the same decoder without the unit answers "
        f"nothing at all.")
    checks.check(
        "an unmapped address is counted, and not as a timeout",
        guarded["unclaimed"] == 1 and guarded["timeouts"] == 0,
        f"unclaimed={guarded['unclaimed']} timeouts={guarded['timeouts']}")

    # --- and it does not fire on an address that IS claimed ----------------
    ok = one_access(guarded=True, adr=RAM_WINDOW >> 2, verbose=verbose)
    cycles, data, errored = ok["answer"]
    checks.check(
        "a claimed address still reads, and is not faulted",
        not errored and data == RAM_WORDS[0],
        f"errored={errored}, read {data!r}, expected {RAM_WORDS[0]:#010x}. A "
        f"unit that faulted everything would pass every check above this one.")

    # --- MECHANISM 2: a subordinate the decoder DOES claim, that stopped ---
    #
    # The address compare cannot see this one: the window matches, so mechanism
    # 1 is silent, and the run is the negative control for the claim that the
    # timeout is not redundant.
    silent = one_access(guarded=True, adr=SLOW_WINDOW >> 2, latency=None,
                        timeout=8, limit=64, verbose=verbose)
    cycles, _data, errored = silent["answer"]
    checks.check(
        "a DECODED subordinate that never answers is terminated by the bound",
        errored and cycles == 8,
        f"errored={errored} after {cycles} cycles, expected ERR at the 8-cycle "
        f"bound. Mechanism 1 cannot reach "
        f"this: the address is inside a window, so nothing about it is unmapped.")
    checks.check(
        "the timeout is counted as a timeout, and not as an unmapped address",
        silent["timeouts"] == 1 and silent["unclaimed"] == 0,
        f"timeouts={silent['timeouts']} unclaimed={silent['unclaimed']}")

    # --- the bound does NOT fire on a subordinate inside it ----------------
    #
    # One cycle under the bound and one cycle over it, so the check would fail
    # for a unit that fired early as well as one that never fired.
    for latency, want_err in ((7, False), (9, True)):
        slow = one_access(guarded=True, adr=SLOW_WINDOW >> 2, latency=latency,
                          timeout=8, limit=64, verbose=verbose)
        _cycles, data, errored = slow["answer"]
        checks.check(
            f"a subordinate answering in {latency} cycles "
            f"{'faults' if want_err else 'does not fault'} against a bound of 8",
            errored == want_err and (want_err or data == 0x5107_0000),
            f"errored={errored} (wanted {want_err}), data {data!r}")

    # --- the bound this SoC actually builds with ---------------------------
    #
    # Asserted rather than noted: it is derived from FLASH_MODE and
    # FLASH_DIVISOR, and a change to either that made it tight would otherwise
    # only show up as a board that faults at random.
    worst = worst_ack_cycles(mode=READ_MODES["quad"], divisor=0)
    checks.check(
        "the shipping bound is 1.25x the slowest legitimate answer",
        worst == 585 and -(-5 * worst // 4) == 732,
        f"worst legitimate answer {worst} cycles, bound {-(-5 * worst // 4)}. "
        f"The worst is a cold quad-mode flash read (69 cycles) with one maximal "
        f"SPI-controller transfer stealing the crossbar before each of its four "
        f"phases (4 x 129).")
    checks.note(f"bus timeout {-(-5 * worst // 4)} cycles "
                f"= 1.25 x {worst} (quad, divisor 0)")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every bus cycle")
    args = parser.parse_args()

    checks = Checks(emit)
    emit("wishbone_pipe.RegisteredResponse")
    run_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
