#!/usr/bin/env python3
#
# What wedged the QSPI burst sequencer. See awtoau/cynthion-workspace#89.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the burst sequencer in `ecp5-test/qspi/qspi_gateware.py`.

    python3 scripts/qspi_burst_sim.py
    python3 scripts/qspi_burst_sim.py -v     # every stream beat

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/qspi_burst_sim.log`.

## The fault this is about

The sequencer was committed marked KNOWN BROKEN, with the diagnosis "`start` is
asserted again on the completion edge, but the reader is not yet back in IDLE, so
the strobe is missed". That diagnosis is wrong. `QuadFlashReader` sets `done` and
moves to IDLE in the same clock edge, so on the rising edge of `done` the reader
*is* in IDLE and does take the strobe. Section 1 shows the handshake working.

What actually wedges it is a stale length. `bursting` selects between the 32 KiB
streaming length and the burst length, and it is set by `m.d.sync` on the same
cycle `start` is strobed -- so the first read of a burst is armed while `length`
still reads 32768. `QuadFlashReader` latches that into `bytes_left` and requests
against it, but decides it has finished by comparing its received count against
the *live* `length`, which is 4 by the next cycle. It therefore requests more
bytes than it consumes. The surplus is still in the controller's pipeline when
the reader stops draining `o_stream`; the deframer holds `frames.ready` low, the
IOStreamer's skid buffer fills, and `i_stream.ready` goes low and stays there.
The next read's HEADER waits on a `ready` that will never come.

That is the same class as the two faults in `docs/decisions.md` -- a signal whose
temporal semantics do not match how it is used -- and it is the CS-hold one
almost exactly, inverted: there a latch was declared as a one-cycle pulse, here a
value needed at one instant is produced one cycle late.

## Why it did not show up in a first simulation

Pipeline depth. Glasgow's `IOStreamer` reads its buffer latency off the platform:
2 for a bare simulation, 4 for a `LatticePlatform`. At 2 the surplus request is
never issued -- the fourth byte returns just before the fifth would be accepted --
and the burst completes cleanly. At 4, which is what the ECP5 has, one surplus
request is issued and the design wedges. So these checks run against a platform
object that reports itself as Lattice, with `io.Buffer`'s simulation model put
back for the simulation ports. Running them at the default latency of 2 makes
section 1 fail, which is the point worth remembering: this bug is invisible
unless the simulation carries the real pipeline depth.

## What is checked, and what each check is evidence for

The reader and the QSPI controller are the real ones -- `QuadFlashReader` and
Glasgow's `Controller`, wired as `qspi_gateware.py` wires them. Only the flash
pins are absent, which the checks do not depend on: everything here is observed
on the controller's `i_stream` and `o_stream`, so what is being measured is the
sequence of commands the reader emits, not the bytes a flash would return.

Sections 1-3 run the OLD arrangement and the NEW one in the same simulation and
assert the old one fails. A check that only demonstrates the fix passing is a
check that would have passed before the fix.

  1. **The run completes.** Old: one read finishes, the second never starts,
     `i_stream.ready` is low with an undrained byte waiting on `o_stream`, and
     the reader's cycle counter climbs until the simulation gives up -- which is
     the "cycles past 2 billion with done=0" that was seen on the board. New:
     every read completes.

  2. **Requests balance returns.** The mechanism, stated directly: the old
     arrangement's first read accepts more `GetX4` beats than the bytes it
     consumes. One surplus beat is enough, and one is what it issues.

  3. **`length` and `address` hold still across the strobe.** The property that
     makes the reader safe, and the one the old arrangement breaks. `length` is
     sampled every cycle from `start` to `done` and must not move.

  4. **The command stream is right.** Opcode, address bytes in
     most-significant-first order at the expected stride, the right number of
     data beats, and a deselect at the end of every read. This is what makes the
     measurement mean anything -- a wedge is obvious, a burst quietly reading the
     same page is not.

  5. **A trigger arriving mid-run is not lost.** Old: sampled with `run &
     ~busy`, a single cycle, so a write landing during a read does nothing and
     the host then reads a stale cycle count as though it were the answer. New:
     latched.

  6. **The measurement is the point.** A burst's cycle count must scale with the
     bytes asked for, must be reported only once the run is over, and must not
     be the streaming read's figure in disguise.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "qspi_burst_sim.log"

sys.path.insert(0, str(ROOT / "ecp5-test" / "qspi"))

from amaranth import Elaboratable, Module, Mux, Signal
from amaranth.hdl import Fragment
from amaranth.lib import io, wiring
from amaranth.sim import Simulator
from amaranth.vendor import LatticePlatform

from apollo_fpga.gateware.qspi_flash import (OPCODE_QUAD_READ,
                                             OPCODE_QUAD_IO_READ,
                                             QuadFlashReader)
from glasgow.gateware.qspi import Controller, Operation
from glasgow.gateware.ports import PortGroup

from qspi_gateware import BURST_STRIDE, READ_BYTES, BurstSequencer


# Divisor 1 is what scripts/qspi_burst.py asks for: SCK at half the sync rate,
# 48 MHz on the shipped bitstream. It also keeps SCK a whole number of sync
# cycles, so nothing here depends on the DDR half-cycle.
DIVISOR = 1

# Stands in for the bitstream's READ_BYTES of 32768. The fault needs only that
# the streaming length differ from the burst length -- the surplus is one beat
# either way -- and 512 bytes is 2,130 sync cycles against 131,160, which is the
# difference between a simulation that runs in a second and one that does not.
SINGLE_LENGTH = 512

# Enough for two full single reads and far more than any burst here. A wedged
# run consumes all of it; a working 4x4 burst finishes in 420.
CYCLE_BUDGET = 8000


class ECP5Sim(LatticePlatform):
    """ A platform that is Lattice for latency purposes and simulation for I/O.

    `IOStreamer` reads its buffer latency off the platform: 4 for Lattice, 2 for
    no platform at all. That number is what decides whether this bug appears, so
    it has to be the board's. Nothing else about the platform is used -- the
    ports are simulation ports and build their own buffers.
    """

    device, package, speed = "LFE5U-12F", "BG256", "6"
    resources, connectors = [], []

    def get_io_buffer(self, buffer):
        if isinstance(buffer.port, io.SimulationPort):
            # io.Buffer's own model, which it uses when there is no platform.
            return io.Buffer.elaborate(buffer, None)
        return super().get_io_buffer(buffer)


class SimController(wiring.Component):
    """ Glasgow's QSPI controller on simulation ports.

    Presents what `QuadFlashReader` uses -- `i_stream`, `o_stream`, `divisor` --
    so the reader is the real one, unmodified. `QSPIFlashController` cannot be
    used directly: it instantiates `USRMCLK`, a black box the simulator has no
    model for.
    """

    def __init__(self, *, offset=0):
        self.cs_port  = io.SimulationPort("o", 1, name="cs")
        self.sck_port = io.SimulationPort("o", 1, name="sck")
        self.io_port  = io.SimulationPort("io", 4, name="dq")

        self._inner = Controller(
            PortGroup(cs=self.cs_port, sck=self.sck_port, io=self.io_port),
            offset=offset)

        super().__init__({
            "i_stream": wiring.In(self._inner.i_stream.signature),
            "o_stream": wiring.Out(self._inner.o_stream.signature),
            "divisor":  wiring.In(16),
        })

    def elaborate(self, platform):
        m = Module()
        m.submodules.inner = self._inner
        m.d.comb += [
            self._inner.i_stream.payload.eq(self.i_stream.payload),
            self._inner.i_stream.valid  .eq(self.i_stream.valid),
            self.i_stream.ready         .eq(self._inner.i_stream.ready),

            self.o_stream.payload       .eq(self._inner.o_stream.payload),
            self.o_stream.valid         .eq(self._inner.o_stream.valid),
            self._inner.o_stream.ready  .eq(self.o_stream.ready),

            self._inner.divisor         .eq(self.divisor),
        ]
        return m


class LegacySequencer(Elaboratable):
    """ The sequencer as committed, lifted out of `QSPITest.elaborate`.

    A transcription, not a rewrite: the assignments, their order and the domain
    each is in are what was in the tree at c154bc3. It is here rather than in the
    gateware so that both arrangements can be driven by the same testbench in the
    same simulation.
    """

    def __init__(self, *, single_length, stride=BURST_STRIDE):
        self.single_length = single_length
        self.stride        = stride

        self.trigger     = Signal()
        self.burst_len   = Signal(16)
        self.burst_count = Signal(16)
        self.reader_done = Signal()
        self.reader_busy = Signal()

        self.start    = Signal()
        self.length   = Signal(16)
        self.address  = Signal(24)
        self.busy     = Signal()
        self.cycles   = Signal(24)
        self.reads    = Signal(16)
        self.bursting = Signal()

    def elaborate(self, platform):
        m = Module()

        burst_done = Signal(16)
        burst_addr = Signal(24)

        m.d.comb += self.length.eq(Mux(self.bursting, self.burst_len,
                                       self.single_length))
        m.d.comb += self.address.eq(Mux(self.bursting, burst_addr, 0))
        m.d.comb += self.reads.eq(burst_done)
        m.d.comb += self.busy.eq(self.bursting)

        started = Signal()
        with m.If(~started):
            m.d.sync += started.eq(1)
            m.d.comb += self.start.eq(1)
        with m.Elif(self.trigger & ~self.reader_busy):
            m.d.sync += [
                self.bursting.eq(self.burst_len != 0),
                burst_done   .eq(0),
                self.cycles  .eq(0),
                burst_addr   .eq(0),
            ]
            m.d.comb += self.start.eq(1)

        done_prev = Signal()
        done_edge = Signal()
        m.d.sync += done_prev.eq(self.reader_done)
        m.d.comb += done_edge.eq(self.reader_done & ~done_prev)

        with m.If(self.bursting):
            m.d.sync += self.cycles.eq(self.cycles + 1)
            with m.If(done_edge & (burst_done < self.burst_count)):
                m.d.sync += [
                    burst_done.eq(burst_done + 1),
                    burst_addr.eq(burst_addr + self.stride),
                ]
                with m.If(burst_done + 1 < self.burst_count):
                    m.d.comb += self.start.eq(1)
                with m.Else():
                    m.d.sync += self.bursting.eq(0)

        return m


class Harness(Elaboratable):
    """ One sequencer, the real reader, the real controller. """

    def __init__(self, sequencer):
        self.seq     = sequencer
        self.ctrl    = SimController()
        self.reader  = QuadFlashReader(controller=self.ctrl)
        self.divisor = Signal(16, init=DIVISOR)

    def elaborate(self, platform):
        m = Module()
        m.submodules.ctrl   = self.ctrl
        m.submodules.reader = self.reader
        m.submodules.seq    = self.seq

        m.d.comb += [
            self.reader.divisor  .eq(self.divisor),
            self.seq.reader_done .eq(self.reader.done),
            self.seq.reader_busy .eq(self.reader.busy),

            self.reader.start    .eq(self.seq.start),
            self.reader.length   .eq(self.seq.length),
            self.reader.address  .eq(self.seq.address),
        ]
        return m


class Run:
    """ What one burst did, as seen on the controller's streams. """

    def __init__(self):
        self.beats      = []    # (chip, Operation, data) accepted on i_stream
        self.octets     = 0     # bytes accepted off o_stream
        self.reads      = 0     # rising edges of reader.done
        self.cycles     = 0     # the sequencer's own count
        self.wedged     = False
        self.ready_low  = False # i_stream.ready at the end
        self.o_pending  = False # o_stream.valid at the end, undrained
        self.lengths    = []    # every distinct reader.length seen in a read
        self.reader_cycles = 0

    def commands(self):
        """The beats split into one list per read, on the deselect beat."""
        reads, current = [], []
        for chip, oper, data in self.beats:
            if chip == 0 and oper == Operation.Idle:
                reads.append(current)
                current = []
            else:
                current.append((oper, data))
        if current:
            reads.append(current)
        return reads


def simulate(sequencer, *, burst_len, burst_count, quad_io=False,
             latency_platform=True, trigger_during_run=False, verbose=False):
    """Run one burst against `sequencer` and report what the streams saw."""
    dut = Harness(sequencer)
    out = Run()

    async def testbench(ctx):
        ctx.set(dut.reader.read_mode, 1 if quad_io else 0)
        ctx.set(dut.seq.burst_len, burst_len)
        ctx.set(dut.seq.burst_count, burst_count)

        # The power-on read. Both arrangements start one unasked, so let it
        # finish before the burst is triggered -- that is what the board does
        # while the host is still opening its JTAG connection.
        for _ in range(CYCLE_BUDGET):
            await ctx.tick()
            if ctx.get(dut.reader.done) and not ctx.get(dut.seq.busy):
                break

        prev_done = ctx.get(dut.reader.done)
        for cycle in range(CYCLE_BUDGET):
            # The trigger goes up inside the loop so the cycle carrying it is
            # one of the cycles sampled. In the committed arrangement `start` is
            # combinational off the trigger, so that cycle is the only one in
            # which its stale length is visible.
            ctx.set(dut.seq.trigger, 1 if cycle == 0 else 0)

            # Everything between the strobe and the end of the transaction. The
            # reader latches `length` at the strobe and compares against it live
            # for the rest of the read, so this is the window over which it must
            # not move.
            if ctx.get(dut.reader.start) or ctx.get(dut.reader.busy):
                out.lengths.append(ctx.get(dut.reader.length))

            if ctx.get(dut.ctrl.i_stream.valid) and \
                    ctx.get(dut.ctrl.i_stream.ready):
                beat = (ctx.get(dut.ctrl.i_stream.payload.chip),
                        Operation(ctx.get(dut.ctrl.i_stream.payload.oper)),
                        ctx.get(dut.ctrl.i_stream.payload.data))
                out.beats.append(beat)
                if verbose:
                    print(f"    {cycle:>5} i_stream chip={beat[0]} "
                          f"{beat[1].name} {beat[2]:#04x}")
            if ctx.get(dut.ctrl.o_stream.valid) and \
                    ctx.get(dut.ctrl.o_stream.ready):
                out.octets += 1

            if trigger_during_run and cycle == 40:
                ctx.set(dut.seq.trigger, 1)

            await ctx.tick()

            done = ctx.get(dut.reader.done)
            if done and not prev_done:
                out.reads += 1
            prev_done = done

            if not ctx.get(dut.seq.busy) and out.reads:
                break
        else:
            out.wedged = True

        out.cycles        = ctx.get(dut.seq.cycles)
        out.reader_cycles = ctx.get(dut.reader.cycles)
        out.ready_low     = not ctx.get(dut.ctrl.i_stream.ready)
        out.o_pending     = bool(ctx.get(dut.ctrl.o_stream.valid))

    platform = ECP5Sim() if latency_platform else None
    sim = Simulator(Fragment.get(dut, platform))
    sim.add_clock(1 / (96e6))
    sim.add_testbench(testbench)
    sim.run()
    return out


def old(**kwargs):
    return simulate(LegacySequencer(single_length=SINGLE_LENGTH), **kwargs)


def new(**kwargs):
    return simulate(BurstSequencer(single_length=SINGLE_LENGTH), **kwargs)


class Checks:
    """PASS/FAIL reporting, one line per assertion."""

    def __init__(self, emit):
        self.emit = emit
        self.passed = 0
        self.failures = []

    def __call__(self, ok, what, detail=""):
        if ok:
            self.passed += 1
            self.emit(f"  PASS {what}")
        else:
            self.failures.append(what)
            self.emit(f"  FAIL {what}")
        if detail:
            self.emit(f"       {detail}")
        return bool(ok)


def section_completes(check, old_run, new_run):
    check.emit("\n1. the run completes\n")

    check(old_run.wedged,
          "the committed sequencer wedges",
          f"{old_run.reads} of 4 reads done, reader.cycles "
          f"{old_run.reader_cycles} still climbing at the {CYCLE_BUDGET}-cycle "
          f"budget")
    check(old_run.ready_low,
          "wedged with i_stream.ready low",
          "the controller is refusing the next read's first header beat")
    check(old_run.o_pending,
          "wedged with a byte undrained on o_stream",
          "which is what is holding i_stream.ready low")

    check(not new_run.wedged,
          "the new sequencer completes")
    check(new_run.reads == 4,
          "all four reads complete",
          f"reads={new_run.reads}")
    check(not new_run.o_pending,
          "nothing is left on o_stream",
          f"{new_run.octets} bytes accepted for 4 reads of 4")


def section_balance(check, old_run, new_run):
    check.emit("\n2. requests balance returns\n")

    def data_beats(run):
        first = run.commands()[0] if run.commands() else []
        return sum(1 for oper, _ in first
                   if oper in (Operation.GetX4, Operation.GetX1))

    old_first = data_beats(old_run)
    new_first = data_beats(new_run)

    check(old_first > 4,
          "the committed first read over-requests",
          f"{old_first} data beats accepted for a 4-byte read; the surplus "
          f"never leaves the pipeline")
    check(new_first == 4,
          "the new first read requests exactly what it reads",
          f"{new_first} data beats for 4 bytes")
    check(new_run.octets == 16,
          "every requested byte is consumed",
          f"{new_run.octets} bytes off o_stream for 4 reads of 4")


def section_stability(check, old_run, new_run):
    check.emit("\n3. length holds still across the strobe\n")

    old_seen = sorted(set(old_run.lengths))
    new_seen = sorted(set(new_run.lengths))

    check(len(old_seen) > 1,
          "the committed length moves after start",
          f"reader.length took {old_seen} across one read -- {SINGLE_LENGTH} "
          f"is what gets latched into bytes_left, 4 is what the exit condition "
          f"compares against")
    check(new_seen == [4],
          "the new length is constant for the whole read",
          f"reader.length took {new_seen}")


def section_commands(check, run, *, count, size, opcode, stride):
    check.emit(f"\n4. the command stream, {count} reads of {size} bytes\n")

    reads = run.commands()
    if not check(len(reads) == count,
                 "one deselected transaction per read",
                 f"{len(reads)} transactions"):
        return

    opcodes = [read[0][1] for read in reads if read]
    check(all(value == opcode for value in opcodes),
          f"every read opens with {opcode:#04x}",
          f"opcodes {[hex(v) for v in opcodes]}")

    # Address bytes are most-significant first, immediately after the opcode.
    addresses = []
    for read in reads:
        hi, md, lo = read[1][1], read[2][1], read[3][1]
        addresses.append((hi << 16) | (md << 8) | lo)
    expected = [index * stride for index in range(count)]
    check(addresses == expected,
          "addresses stride by the prime, most-significant byte first",
          f"{addresses} against {expected}")

    check(len(set(addresses)) == count,
          "no read repeats another's address",
          "a burst at one address would measure the sequential path")

    data = [sum(1 for oper, _ in read if oper is Operation.GetX4)
            for read in reads]
    check(all(value == size for value in data),
          f"every read asks for exactly {size} bytes",
          f"data beats {data}")

    check(run.octets == count * size,
          "and receives them",
          f"{run.octets} bytes for {count} * {size}")


def section_trigger(check):
    check.emit("\n5. a trigger arriving mid-run\n")

    old_run = old(burst_len=4, burst_count=2, trigger_during_run=True)
    new_run = new(burst_len=4, burst_count=2, trigger_during_run=True)

    # The old arrangement is already wedged by the time the second trigger
    # lands, so what it does with the trigger cannot be observed there. Its
    # `run & ~reader.busy` is a single-cycle sample either way, and the
    # single-read case is enough to show it dropped: burst_len 0 means the reader
    # runs one long read and is busy throughout.
    old_single = old(burst_len=0, burst_count=1, trigger_during_run=True)
    new_single = new(burst_len=0, burst_count=1, trigger_during_run=True)

    check(old_single.reads == 1,
          "the committed sequencer drops a trigger raised while busy",
          f"{old_single.reads} read for two triggers; the host then polls, sees "
          f"the previous run's done, and reads a stale cycle count")
    check(new_single.reads == 2,
          "the new sequencer latches it and runs it after",
          f"{new_single.reads} reads for two triggers")

    check(old_run.wedged and not new_run.wedged,
          "and neither changes what section 1 showed")


def section_measurement(check):
    check.emit("\n6. the cycle count is a measurement\n")

    small = new(burst_len=4, burst_count=4)
    large = new(burst_len=64, burst_count=4)
    single = new(burst_len=0, burst_count=1)

    check(small.cycles > 0 and large.cycles > 0,
          "a run reports a nonzero cycle count",
          f"{small.cycles} for 4x4, {large.cycles} for 4x64")
    check(large.cycles > small.cycles,
          "and it grows with the bytes asked for",
          f"{large.cycles} against {small.cycles} for 16x the payload")

    # 4x(64-4) = 240 extra bytes, each 2*(divisor+1) sync cycles on four lanes.
    expected = 4 * (64 - 4) * 2 * (DIVISOR + 1)
    measured = large.cycles - small.cycles
    check(abs(measured - expected) <= 8,
          "by the number of clocks those bytes cost",
          f"{measured} cycles against {expected} predicted from "
          f"2*(divisor+1) per byte on four lanes")

    check(small.cycles < single.cycles,
          "a burst is not the streaming read wearing a different length",
          f"{small.cycles} for 4x4 against {single.cycles} for one "
          f"{SINGLE_LENGTH}-byte read")

    per_read = (small.cycles - small.reads) / small.reads
    check(20 <= per_read <= 200,
          "per-read cost is in the range the header accounts for",
          f"{per_read:.0f} sync cycles per read, arming excluded; a 5-byte "
          f"single-lane header alone is 8*(divisor+1)*5 = "
          f"{8 * (DIVISOR + 1) * 5}")


def section_latency(check):
    check.emit("\n7. why a first simulation missed it\n")

    shallow = old(burst_len=4, burst_count=4, latency_platform=False)
    deep    = old(burst_len=4, burst_count=4, latency_platform=True)

    check(not shallow.wedged and shallow.reads == 4,
          "at the simulator's default buffer latency of 2, the old one passes",
          f"{shallow.reads} of 4 reads, no wedge -- and the bug is still there")
    check(deep.wedged,
          "at the ECP5's latency of 4, the same design wedges",
          f"{deep.reads} of 4 reads; IOStreamer reads the number off the "
          f"platform, and a simulation without one is a different machine")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every stream beat")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []

    def emit(text=""):
        print(text, flush=True)
        lines.append(text)

    check = Checks(emit)
    emit("\nQSPI burst sequencer\n")

    old_run = old(burst_len=4, burst_count=4, verbose=args.verbose)
    new_run = new(burst_len=4, burst_count=4, verbose=args.verbose)

    section_completes(check, old_run, new_run)
    section_balance(check, old_run, new_run)
    section_stability(check, old_run, new_run)
    section_commands(check, new_run, count=4, size=4,
                     opcode=OPCODE_QUAD_READ, stride=BURST_STRIDE)
    section_commands(check, new(burst_len=16, burst_count=3, quad_io=True),
                     count=3, size=16, opcode=OPCODE_QUAD_IO_READ,
                     stride=BURST_STRIDE)
    section_trigger(check)
    section_measurement(check)
    section_latency(check)

    emit()
    if check.failures:
        emit(f"{len(check.failures)} FAILED: {', '.join(check.failures)}")
    else:
        emit(f"{check.passed} checks passed")
    emit(f"log: {LOG.relative_to(ROOT)}")
    emit()

    LOG.write_text("\n".join(lines))
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())
