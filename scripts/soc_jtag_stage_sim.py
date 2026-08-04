#!/usr/bin/env python3
#
# Simulate the JTAG staging sink: frames in, HyperRAM words out.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves `jtag_stage.JTAGStager` decodes ER1 frames and lands them where the bootloader
looks.

    ./scripts/soc_jtag_stage_sim.py           # run every check
    ./scripts/soc_jtag_stage_sim.py -v        # and print every frame

Exit status 0 if every assertion held. Output goes to the terminal and to
`tmp/logs/dev.log`.

## Why this is worth a file

The sink is driven by a clock the design does not own. TCK runs at 12 MHz from a
SAMD11 while `sync` runs at 60 MHz, the two are unrelated, and a JTAG shift cannot
be stalled -- so every framing and clock-crossing error here presents on the board
as a corrupt image and nothing else. That is a CRC failure at the far end of a
staging run, which says a byte is wrong and nothing about which byte or why.

The three properties worth stating outright:

  * **The edges are modelled, not assumed.** `Chain` reproduces the half-cycle
    relationship between JTCK, JTDI, JSHIFT and the TDO pad, because that is where
    this design's real bug was: JTAGG's outputs all move on the rising edge, and a
    sink clocked there decodes correctly in simulation and races on silicon. A
    testbench that clocked everything together would have passed the broken design.
  * **A frame is self-delimiting.** The decoder is reset by any JTCK edge outside a
    shift, so it depends on nothing about how the ECP5 asserts JCE1 in CAPTURE-DR or
    UPDATE-DR -- only that JCE1 and JSHIFT are both high exactly while ER1 payload
    bits are on the wire.
  * **The drain outruns the fill.** 16 TCK per word at 12 MHz is 1.33 us; one
    HyperRAM write is ~22 `sync` cycles, 367 ns at 60 MHz. `no_overflow` runs the
    real ratio and checks the sticky flag, which is the only report the hardware can
    give if that margin is ever wrong.

## Where the time goes, and why most of it is not removable

#175 read ~42 ms a check off `dev.log` and took it for a poll interval. Part of
it was one -- `drain` sat out a flat 400 sync cycles after every frame, ~12% of
the run, and now stops when the stager goes quiet. The rest is not a wait at
all: a profile puts the whole cost inside `Simulator.advance`, 105,743 deltas,
because a 512-byte image is 4,096 TCK bits and each one is two `ctx.delay` half
periods with a 60 MHz clock running underneath. Tens of milliseconds a check IS
the simulator's rate at that ratio; the only lever left is simulating fewer bits,
which would mean testing less.

## What this cannot say

The HyperRAM here is a testbench that acknowledges after a fixed delay, not
`HyperRAMInterface`. It says the sink issues the right writes in the right order; it
does not say the controller performs them. The evidence for that pair is a staged
image whose CRC checks out on the board -- `scripts/soc_jtag_stage.py`.
"""

import argparse
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "scripts"))

from checks import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth       import ClockDomain, Module          # noqa: E402
from amaranth.hdl   import Fragment                     # noqa: E402
from amaranth.sim   import Simulator                    # noqa: E402

from jtag_stage import (CMD_NOP, CMD_RESET, CMD_WRITE,  # noqa: E402
                        SIGNATURE, JTAGStager)

# The two clocks, as the board runs them.
#
# Not round numbers picked for convenience: 12 MHz is what `spi_init(SPI_FPGA_JTAG,
# ..., 1, ...)` in Apollo's `firmware/src/jtag.c` sets, and 60 MHz is `SYNC_MHZ` in
# `vexii_hello_soc.py`. The ratio between them is the whole margin.
SYNC_HZ = 60e6
JTCK_HZ = 12e6

# Cycles a HyperRAM word write takes, counted off `HyperRAMInterface`'s FSM: IDLE,
# LATCH_RWDS, three SHIFT_COMMAND states, thirteen of HANDLE_LATENCY, WRITE_DATA,
# RECOVERY, plus `BootRAM`'s own handshake. Used as the testbench's latency so the
# overflow check runs against a plausible drain rate rather than an instant one.
HYPERRAM_CYCLES = 22

# Where the bootloader looks, from `firmware/cynthion-soc/src/hyperram.rs`. In
# 16-bit HyperRAM words.
HDR_MAGIC  = 0
HDR_LENGTH = 2
HDR_CRC    = 4
IMAGE_WORD = 16
MAGIC = 0x4359_4e42


class Chain:
    """A JTAG TAP driving ER1, one bit at a time, the way Apollo's firmware does.

    `jtag_tap_shift` sets TDI, drives TCK low, reads TDO, then drives TCK high, so the
    TAP samples TDI on the rising edge. What this models that a naive testbench would
    not is the half cycle after that: JTAGG's JTDI, JSHIFT and JCE1 all change on the
    rising edge, and JTAGG registers JTDO1 on the falling one. So TDI is presented
    before the rising edge, TDO is sampled just before the falling edge, and the sink
    is expected to do all of its work on the falling edge.

    Driving this the other way round -- everything on the rising edge -- passes in
    simulation and races on silicon, which is exactly what happened.
    """

    def __init__(self, ctx, dut, verbose=False):
        self.ctx = ctx
        self.dut = dut
        self.verbose = verbose
        self.half = 1 / (2 * JTCK_HZ)

    async def _pulse(self, tdi_bit):
        """One TCK, in the order Apollo's firmware produces: low, read, high.

        TCK rests high. The falling edge is where the sink does its work and where
        JTAGG latches JTDO1 into the TDO pad -- taking the value from before that
        edge, which is why TDO is sampled here and not after. The rising edge is
        where the TAP samples the pin, so JTDI only carries this bit from then on:
        at the falling edge the sink sees the PREVIOUS bit, and modelling that lag is
        the difference between a testbench that catches the alignment bug and one
        that does not.
        """
        ctx = self.ctx
        out = ctx.get(self.dut.tdo)
        ctx.set(self.dut.tck, 0)
        await ctx.delay(self.half)
        ctx.set(self.dut.tck, 1)
        ctx.set(self.dut.tdi, tdi_bit)
        await ctx.delay(self.half)
        return out

    async def idle(self, pulses=4):
        """TCK with neither enable asserted, as TAP navigation produces."""
        self.ctx.set(self.dut.tck, 1)
        self.ctx.set(self.dut.ce, 0)
        self.ctx.set(self.dut.shift, 0)
        for _ in range(pulses):
            await self._pulse(0)

    async def frame(self, payload, *, bits=None, abort_after=None):
        """Shift one ER1 frame. Returns the TDO bits as an integer, LSB first.

        `payload` is the byte string the host would hand to `shift_data`, and it goes
        out low byte first, low bit first. `abort_after` drops JSHIFT that many bits
        in, which is what a TAP leaving SHIFT-DR mid-frame looks like.
        """
        ctx = self.ctx
        count = bits if bits is not None else len(payload) * 8

        # The TAP navigation and the ER1 instruction shift the host does before every
        # data shift. Clocked here rather than skipped because the status word's
        # `busy` bit crosses from `sync` through a two-stage synchroniser and needs
        # TCK edges to arrive -- edges a real host always provides and a testbench
        # that jumped straight to CAPTURE-DR would not.
        await self.idle()

        # CAPTURE-DR: the enable is up, the shift phase is not.
        ctx.set(self.dut.ce, 1)
        ctx.set(self.dut.shift, 0)
        await self._pulse(0)

        ctx.set(self.dut.shift, 1)
        received = 0
        for index in range(count):
            if abort_after is not None and index == abort_after:
                ctx.set(self.dut.shift, 0)
                await self._pulse(0)
                ctx.set(self.dut.shift, 1)
            byte = payload[index // 8] if index // 8 < len(payload) else 0
            received |= await self._pulse((byte >> (index % 8)) & 1) << index

        await self.idle()
        if self.verbose:
            print(f"      frame {payload[:6].hex()}... {count} bits "
                  f"-> status {received & 0xffff_ffff_ffff_ffff:#018x}")
        return received


def decode_status(value):
    """Split the 64-bit status word into the fields the sink documents."""
    return {
        "signature": value & 0xffff,
        "staged":    (value >> 16) & 0xffff_ffff,
        "overflow":  (value >> 48) & 1,
        "busy":      (value >> 49) & 1,
        "cpu_reset": (value >> 50) & 1,
    }


def write_frame(address, words):
    """The bytes of a `write` frame: command, start address, then the words."""
    payload = bytes([CMD_WRITE]) + address.to_bytes(4, "little")
    for word in words:
        payload += word.to_bytes(2, "little")
    return payload


def reset_frame(hold):
    return bytes([CMD_RESET]) + (1 if hold else 0).to_bytes(4, "little")


def status_frame():
    """A `nop` long enough to shift the whole status word out."""
    return bytes([CMD_NOP]) + b"\x00" * 7


class Run:
    """One simulation: a stager, a HyperRAM stand-in, and a scripted TAP."""

    def __init__(self, *, latency=HYPERRAM_CYCLES, depth=32, verbose=False):
        self.latency = latency
        self.verbose = verbose
        self.writes = []
        self.dut = JTAGStager(depth=depth)
        self.cpu_reset_seen = []

    def go(self, script):
        m = Module()
        m.domains.sync = ClockDomain()
        m.submodules.dut = dut = self.dut

        async def hyperram(ctx):
            """Acknowledge a write request after a fixed number of cycles.

            A process rather than a testbench, so it does not keep the simulation
            alive after the script has finished. `latency=None` never acknowledges,
            which is how the overflow path is reached: the FIFO fills behind a
            controller that has stopped answering.
            """
            countdown = 0
            latched = None
            # `req` is held until it is acknowledged and drops a cycle later, so a
            # model that re-arms on the same edge it acknowledges would count every
            # word twice. It re-arms only after seeing the request released.
            armed = True
            async for _clk, _rst, req, address, data in \
                    ctx.tick().sample(dut.req, dut.addr, dut.data):
                ctx.set(dut.ack, 0)
                if self.latency is None:
                    continue
                if countdown:
                    countdown -= 1
                    if not countdown:
                        self.writes.append(latched)
                        ctx.set(dut.ack, 1)
                        armed = False
                elif not req:
                    armed = True
                elif armed:
                    latched = (address, data)
                    countdown = self.latency

        async def testbench(ctx):
            chain = Chain(ctx, dut, self.verbose)
            await chain.idle()
            await script(chain, ctx)

        sim = Simulator(Fragment.get(m, None))
        sim.add_clock(1 / SYNC_HZ, domain="sync")
        sim.add_process(hyperram)
        sim.add_testbench(testbench)
        sim.run()

    def memory(self):
        """The HyperRAM contents the writes produced, as {word address: value}."""
        return dict(self.writes)


# What "the FIFO has emptied" looks like from outside, counted in sync cycles
# with `req` low. The writer's POP state dwells exactly one cycle per entry, and
# the longest run of entries that does not raise `req` is the two address words,
# so four consecutive quiet cycles cannot be a gap between two writes of a frame.
QUIET_CYCLES = 4


async def drain(ctx, dut, limit=400):
    """Let the FIFO empty into the HyperRAM, and stop as soon as it has.

    This was a flat 400 cycles, which is ~12% of this simulation and mostly spent
    watching a design that finished long ago: five words at 22 cycles each is 110.
    Waiting on the design instead of on a count costs nothing and asserts more --
    a stager that never goes quiet now runs out `limit` rather than being handed
    a fixed number of cycles that happened to be enough. See #175.

    `limit` is not a duration to sit out. It is only reached if `req` never
    settles, which the checks below would report anyway.
    """
    quiet = 0
    for _ in range(limit):
        await ctx.tick()
        quiet = 0 if ctx.get(dut.req) else quiet + 1
        if quiet >= QUIET_CYCLES:
            return


async def settle(ctx, cycles=8):
    """Cycles for the reset's two-stage FFSynchronizer, not for the FIFO.

    Two flops plus margin. Unlike `drain` there is nothing to watch: the whole
    point of the wait is that `cpu_reset` is being sampled after the crossing.
    """
    for _ in range(cycles):
        await ctx.tick()


def run_checks(checks, verbose):
    # --- the sink answers on ER1 at all --------------------------------
    run = Run(verbose=verbose)
    status = {}

    async def script(chain, ctx):
        status["idle"] = decode_status(await chain.frame(status_frame()))

    run.go(script)
    checks.check(
        "a nop frame shifts the signature back out on TDO",
        status["idle"]["signature"] == SIGNATURE,
        f"got {status['idle']['signature']:#06x}, want {SIGNATURE:#06x}. A zero "
        f"means TDO never reached the sink -- ER1 is wired to something else, or "
        f"JCE1 is not what the frame decoder is watching.")
    checks.check(
        "an idle sink reports nothing staged, no overflow, no reset",
        (status["idle"]["staged"], status["idle"]["overflow"],
         status["idle"]["busy"], status["idle"]["cpu_reset"]) == (0, 0, 0, 0),
        status["idle"])

    # --- a write frame lands where it says ------------------------------
    words = [0x1234, 0xabcd, 0x0000, 0xffff, 0x5a5a]
    run = Run(verbose=verbose)
    after = {}

    async def script(chain, ctx):
        await chain.frame(write_frame(0x40, words))
        await drain(ctx, run.dut)
        after["status"] = decode_status(await chain.frame(status_frame()))

    run.go(script)
    expected = [(0x40 + n, word) for n, word in enumerate(words)]
    checks.check(
        "a write frame stores its words at consecutive addresses",
        run.writes == expected,
        f"got {run.writes!r}\nwant {expected!r}")
    checks.check(
        "the status counts the image words, not the two address words",
        after["status"]["staged"] == len(words),
        f"staged={after['status']['staged']}, want {len(words)}")
    checks.check(
        "the sink is idle once its FIFO has drained",
        after["status"]["busy"] == 0 and after["status"]["overflow"] == 0,
        after["status"])

    # --- two frames, two addresses --------------------------------------
    run = Run(verbose=verbose)

    async def script(chain, ctx):
        await chain.frame(write_frame(0x1000, [0x0101, 0x0202]))
        await drain(ctx, run.dut)
        await chain.frame(write_frame(0x0002, [0x0303]))
        await drain(ctx, run.dut)

    run.go(script)
    checks.check(
        "a second frame starts at its own address, not where the first stopped",
        run.writes == [(0x1000, 0x0101), (0x1001, 0x0202), (0x0002, 0x0303)],
        run.writes)

    # --- frames that must do nothing ------------------------------------
    run = Run(verbose=verbose)

    async def script(chain, ctx):
        # All-zeroes and all-ones are what an idle or floating chain shifts, and
        # neither may be a command that writes.
        await chain.frame(b"\x00" * 8)
        await chain.frame(b"\xff" * 8)
        await drain(ctx, run.dut)

    run.go(script)
    checks.check(
        "an all-zero and an all-ones frame write nothing",
        run.writes == [],
        f"{run.writes!r} -- a chain shifting idle patterns must not reach HyperRAM")

    # --- the CPU reset --------------------------------------------------
    run = Run(verbose=verbose)
    reset_states = {}

    async def script(chain, ctx):
        reset_states["before"] = ctx.get(run.dut.cpu_reset)
        await chain.frame(reset_frame(True))
        await settle(ctx)
        reset_states["held"] = ctx.get(run.dut.cpu_reset)
        reset_states["reported"] = decode_status(
            await chain.frame(status_frame()))["cpu_reset"]
        await chain.frame(reset_frame(False))
        await settle(ctx)
        reset_states["released"] = ctx.get(run.dut.cpu_reset)

    run.go(script)
    checks.check(
        "reset 1 holds the CPU and reset 0 releases it",
        (reset_states["before"], reset_states["held"],
         reset_states["released"]) == (0, 1, 0),
        reset_states)
    checks.check(
        "the held reset is reported back in the status",
        reset_states["reported"] == 1,
        reset_states)

    # --- a write frame does not disturb the reset -----------------------
    run = Run(verbose=verbose)
    kept = {}

    async def script(chain, ctx):
        await chain.frame(reset_frame(True))
        await chain.frame(write_frame(0, [0x1111]))
        await drain(ctx, run.dut)
        kept["after"] = ctx.get(run.dut.cpu_reset)

    run.go(script)
    checks.check(
        "staging does not release the CPU it is staging around",
        kept["after"] == 1,
        f"cpu_reset={kept['after']}: the whole point is that the image lands while "
        f"nothing is executing")

    # --- resynchronisation ----------------------------------------------
    run = Run(verbose=verbose)

    async def script(chain, ctx):
        # JSHIFT drops in the middle of the address, so the rest of this frame is
        # decoded against a fresh command byte and cannot mean `write` any more.
        await chain.frame(write_frame(0x20, [0xdead, 0xbeef]), abort_after=20)
        await drain(ctx, run.dut)
        aborted = list(run.writes)
        await chain.frame(write_frame(0x30, [0xcafe]))
        await drain(ctx, run.dut)
        run.aborted = aborted

    run.go(script)
    checks.check(
        "a frame cut short does not leave the decoder mid-word",
        run.writes[len(run.aborted):] == [(0x30, 0xcafe)],
        f"after the abort: {run.writes!r}. The next frame must decode from its own "
        f"command byte, whatever the previous one was doing when it stopped.")

    # --- the margin, and the flag that reports its absence ---------------
    run = Run(verbose=verbose)
    margin = {}

    async def script(chain, ctx):
        await chain.frame(write_frame(0, list(range(0x100, 0x100 + 64))))
        await drain(ctx, run.dut, 2000)
        margin["status"] = decode_status(await chain.frame(status_frame()))

    run.go(script)
    checks.check(
        "64 words at 12 MHz TCK never outrun a 22-cycle HyperRAM write",
        margin["status"]["overflow"] == 0 and len(run.writes) == 64,
        f"{len(run.writes)} words written, overflow={margin['status']['overflow']}")

    run = Run(latency=None, depth=32, verbose=verbose)
    stalled = {}

    async def script(chain, ctx):
        await chain.frame(write_frame(0, list(range(64))))
        stalled["after"] = decode_status(await chain.frame(status_frame()))
        # A fresh `write` clears the flag, so a stale overflow from an earlier run
        # cannot be read as a failure of this one. Nothing drains here, so it sets
        # again immediately -- what is being checked is that it started clear.
        await chain.frame(write_frame(0, [0x0001]))
        stalled["reused"] = decode_status(await chain.frame(status_frame()))

    run.go(script)
    checks.check(
        "overflow is sticky and reported when the FIFO does fill",
        stalled["after"]["overflow"] == 1 and stalled["after"]["busy"] == 1,
        f"{stalled['after']} -- a JTAG shift cannot be stalled, so the only "
        f"alternative to this flag is losing words with nothing said")
    checks.check(
        "a new write frame reports its own overflow, not the last one's",
        stalled["reused"]["staged"] == 1,
        f"{stalled['reused']} -- `staged` must have restarted at this frame")

    # --- the layout the bootloader reads --------------------------------
    image = bytes((n * 7 + 3) & 0xff for n in range(512))
    crc = zlib.crc32(image) & 0xffff_ffff
    run = Run(verbose=verbose)

    async def script(chain, ctx):
        image_words = [int.from_bytes(image[n:n + 2], "little")
                       for n in range(0, len(image), 2)]
        await chain.frame(write_frame(IMAGE_WORD, image_words))
        await drain(ctx, run.dut)
        # Length and CRC before the magic, and the magic in its own frame, so a
        # host that dies mid-run never leaves a header describing an image that is
        # not there. Same ordering as `hyperram::write_header`.
        await chain.frame(write_frame(HDR_LENGTH, [
            len(image) & 0xffff, len(image) >> 16,
            crc & 0xffff, crc >> 16]))
        await drain(ctx, run.dut)
        await chain.frame(write_frame(HDR_MAGIC, [MAGIC & 0xffff, MAGIC >> 16]))
        await drain(ctx, run.dut)

    run.go(script)
    memory = run.memory()

    def read32(word):
        return memory.get(word, 0) | (memory.get(word + 1, 0) << 16)

    checks.check(
        "the header reads back as the firmware's `staged()` expects",
        (read32(HDR_MAGIC), read32(HDR_LENGTH), read32(HDR_CRC))
        == (MAGIC, len(image), crc),
        f"magic {read32(HDR_MAGIC):#010x}, length {read32(HDR_LENGTH)}, "
        f"crc {read32(HDR_CRC):#010x}; want {MAGIC:#010x}, {len(image)}, {crc:#010x}")

    read_back = bytearray()
    for index in range(len(image) // 2):
        read_back += memory.get(IMAGE_WORD + index, 0).to_bytes(2, "little")
    checks.check(
        "the image reads back byte for byte, and its CRC matches",
        bytes(read_back) == image
        and (zlib.crc32(bytes(read_back)) & 0xffff_ffff) == crc,
        f"{len(read_back)} bytes back, first difference at "
        f"{next((n for n, (a, b) in enumerate(zip(read_back, image)) if a != b), None)}")

    # The magic is written last, so a run cut short after the image leaves a
    # header the bootloader ignores rather than one it believes.
    order = [address for address, _ in run.writes]
    checks.check(
        "the magic is the last word written, not the first",
        order.index(HDR_MAGIC) > order.index(HDR_LENGTH) > order.index(IMAGE_WORD),
        f"write order was {order[:4]}...{order[-4:]}")

    checks.note(f"{len(image)} bytes staged in "
                f"{len(run.writes)} HyperRAM words")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every frame")
    args = parser.parse_args()

    checks = Checks(emit)
    emit("jtag_stage.JTAGStager")
    run_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
