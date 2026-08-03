#!/usr/bin/env python3
#
# DQS bring-up: the same clock the non-DQS path already reaches, byte-exact. See awtoau/cynthion-workspace#92.
# SPDX-License-Identifier: BSD-3-Clause

"""
Brings the DQS read path up at a rate already proven, before pushing anything.

    python3 ecp5-test/hyperram/hyperram_dqs_top.py            # what this is
    python3 ecp5-test/hyperram/hyperram_dqs_top.py --build    # a bitstream

**`--build` does not touch the board** -- `do_program=False`, so it runs yosys,
nextpnr and ecppack and stops. It is worth running for its own sake even with no
board attached: whether nextpnr accepts `DQSBUFM` on this pin group, and whether
`DELAYG` packs where it is put here, are questions no simulation can answer and
they are what stalled #92.

## Why this design exists separately from the SoC

Step 2 of #92 is "bring it up at the rate the non-DQS path already reaches, to
confirm equivalence before pushing". Equivalence is only meaningful against
something with nothing else in it: a comparison run inside `vexii_hello_soc.py`
would be comparing two designs that also differ by a CPU, a USB stack and a
bus. So this is HyperRAM, a clock and six LEDs.

It also keeps the shared SoC file out of the way while seven other changes are
in flight.

## What it does, once per reset

Write a pattern, read it back, count what disagrees. The pattern is derived from
the word address rather than being constant, for the reason
`hyperram_fifo.py` gives: a controller that failed to advance the address would
return the first word forever and a constant fill would call that a pass.

The LEDs report, by colour:

| LED | meaning |
|---|---|
| red | the DDRDLL never locked -- nothing below this is meaningful |
| orange | BURSTDET never asserted: the read strobe was not found |
| yellow | running |
| green | finished with zero mismatches |
| blue | finished with mismatches |
| violet | heartbeat, so a dead clock is distinguishable from a dead design |

BURSTDET has its own LED because it is the one signal that separates "DQS is
working" from "the fixed-latency count happened to land right anyway". A design
that passes with BURSTDET low is not testing what it claims to.

## The recovery gap is held here, not in the controller

`HyperRAMDQSInterface`'s `RECOVERY` state carries `# TODO: implement recovery`
and falls straight through to `IDLE`, so the next transaction may assert CS on
the following cycle. HyperBus requires CS# high between transactions for tCSHI,
which the W956A8 specifies as 10 ns minimum -- longer than one 120 MHz cycle
(8.33 ns) and longer than one 60 MHz cycle only by chance.

So the gap is enforced here, by a counter, and the count is a constant with a
reason rather than a guess:

    RECOVERY_CYCLES = ceil(tCSHI * f_sync)

At 60 MHz that is 1 cycle (16.7 ns), at 120 MHz it is 2 (16.7 ns). The counter
is not an optimisation to remove later -- it is the difference between a
transaction that starts legally and one that starts 8.33 ns early and mostly
gets away with it.
"""

import sys
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "riscv"))

from amaranth import Cat, Elaboratable, Module, Signal

from luna.gateware.interface.psram import HyperRAMDQSInterface

from hyperram_dqs_phy import HyperRAMDQSPHY


# The rate the non-DQS path is already verified at is 120 MHz. This starts at 60
# because equivalence has to be established before margin is spent: a first
# bring-up that fails at 120 cannot distinguish "DQS is wired wrong" from "120
# is too fast for this arrangement", and those need different work.
#
# `--sync-mhz` moves it. 120 is the number to reach before believing anything.
SYNC_MHZ = 60

# tCSHI, CS# high between transactions, from the W956A8 datasheet. The one
# HyperBus timing parameter this design can violate without any symptom other
# than an occasional wrong word.
T_CSHI_NS = 10.0

# 32-bit words. `HyperBusDQSPHY` moves 32 bits per `sync` cycle where the
# non-DQS `HyperBusPHY` moves 16 -- a test written to the 16-bit width reads
# back looking bit-shifted, which is the trap recorded in
# `docs/luna_ecp5_fpga/hyperram-detailed.md`.
WORD_BITS = 32

# How many words the pass moves. 1024 is four device pages (512 words) and is
# enough for a wrap bug to show, while staying inside one burst's worth of
# addresses so a failure is about the data path and not about paging.
TRANSFER_WORDS = 1024

# Where in the 8 MiB it works. Well away from 0 so that a controller that
# ignored the address entirely -- returning whatever the last transaction left
# -- reads the wrong thing rather than the right thing by luck.
BASE_ADDRESS = 0x0004_0000


def pattern(index):
    """The word expected at `index`. Address-derived, so a stuck address shows."""
    return Cat(index[:16], ~index[:16])


class HyperRAMDQSBringup(Elaboratable):
    """Write a pattern over DQS, read it back, and light an LED for the answer."""

    def __init__(self, *, sync_mhz=SYNC_MHZ, words=TRANSFER_WORDS):
        self.sync_mhz = sync_mhz
        self.words = words

        # Brought out so a simulation can drive this without a platform.
        self.done = Signal()
        self.errors = Signal(16)
        self.burstdet_seen = Signal()

    def elaborate(self, platform):
        m = Module()

        from apollo_fpga.gateware.variable_clock import VariableClockDomainGenerator

        # `with_fast=True` is the whole clocking change DQS needs, and it is not
        # free: it costs a PLL output and forces CLKOP_DIV to be even, which
        # restricts which sync frequencies are reachable. It also solves `usb` to
        # exactly 60 MHz in the same pass -- required here even though this
        # design has no USB, because the constraint is what makes the solver's
        # answer the same one the SoC would get.
        m.submodules.car = VariableClockDomainGenerator(
            sync_mhz=self.sync_mhz, with_fast=True)

        # `dir="-"` on the platform's OWN `ram` resource. The pin map is not
        # copied: this PHY needs the pads unbuffered, not different pins, and it
        # reads the resource's `invert` for the active-low CS and RESET#.
        bus = platform.request("ram", 0, dir="-")

        m.submodules.phy = phy = HyperRAMDQSPHY(bus=bus)
        m.submodules.psram = psram = HyperRAMDQSInterface(phy=phy.phy)

        # Held for the whole transfer, never pulsed. Both of these are traps this
        # workspace has already paid for on the non-DQS path
        # (`docs/upstream-boundary.md`): `perform_write` and `write_data` must
        # stay asserted until the controller finishes, and `final_word` must be
        # held rather than strobed. Pulsing either produced plausible wrong
        # answers rather than failures.
        writing = Signal()
        last = Signal()

        index = Signal(range(self.words + 1))
        errors = Signal(16)
        burstdet_seen = Signal()
        heartbeat = Signal(24)

        m.d.sync += heartbeat.eq(heartbeat + 1)
        with m.If(phy.phy.burstdet):
            m.d.sync += burstdet_seen.eq(1)

        # tCSHI, in whole `sync` cycles, rounded up. See the module docstring:
        # the controller's RECOVERY state does nothing, so this is where the gap
        # between transactions comes from.
        recovery_cycles = max(1, ceil(T_CSHI_NS * self.sync_mhz / 1000.0))
        recovery = Signal(range(recovery_cycles + 1))

        start = Signal()
        expected = Signal(WORD_BITS)
        m.d.comb += expected.eq(pattern(index))

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(0),
            psram.address.eq(BASE_ADDRESS + index),
            psram.perform_write.eq(writing),
            psram.write_data.eq(expected),
            psram.final_word.eq(last),
            psram.start_transfer.eq(start),
        ]

        with m.FSM() as fsm:
            with m.State("RESET"):
                # RESET# asserted for the first half of this state, released for
                # the second, and the DDRDLL settle has to have finished too.
                #
                # The part wants RESET# low for tRP >= 200 ns and then tRPH >=
                # 400 ns before the first transaction. Halves of 2^16 cycles at
                # 60 MHz are ~546 us each -- three orders of magnitude past
                # both. Deliberately generous: it happens once per
                # configuration, nothing is measured across it, and a tight
                # value here would be a number chosen to look careful.
                m.d.comb += phy.phy.reset.eq(~heartbeat[15])
                with m.If(heartbeat[16] & phy.dll_ready):
                    m.next = "WRITE_START"

            with m.State("WRITE_START"):
                m.d.sync += [writing.eq(1), last.eq(1)]
                m.d.comb += start.eq(1)
                m.next = "WRITE_WAIT"

            with m.State("WRITE_WAIT"):
                with m.If(psram.write_ready):
                    m.d.sync += index.eq(index + 1)
                    m.next = "WRITE_RECOVER"

            with m.State("WRITE_RECOVER"):
                m.d.sync += recovery.eq(recovery + 1)
                with m.If(psram.idle & (recovery == recovery_cycles)):
                    m.d.sync += [recovery.eq(0), writing.eq(0)]
                    with m.If(index == self.words):
                        m.d.sync += index.eq(0)
                        m.next = "READ_START"
                    with m.Else():
                        m.next = "WRITE_START"

            with m.State("READ_START"):
                m.d.sync += [writing.eq(0), last.eq(1)]
                m.d.comb += start.eq(1)
                m.next = "READ_WAIT"

            with m.State("READ_WAIT"):
                with m.If(psram.read_ready):
                    # Count every mismatch rather than stopping at the first.
                    # One wrong word in a thousand and a thousand wrong words are
                    # different faults, and a design that halts on the first
                    # cannot tell them apart.
                    with m.If(psram.read_data != expected):
                        m.d.sync += errors.eq(errors + 1)
                    m.d.sync += index.eq(index + 1)
                    m.next = "READ_RECOVER"

            with m.State("READ_RECOVER"):
                m.d.sync += recovery.eq(recovery + 1)
                with m.If(psram.idle & (recovery == recovery_cycles)):
                    m.d.sync += recovery.eq(0)
                    with m.If(index == self.words):
                        m.next = "DONE"
                    with m.Else():
                        m.next = "READ_START"

            with m.State("DONE"):
                pass

        m.d.comb += [
            self.done.eq(fsm.ongoing("DONE")),
            self.errors.eq(errors),
            self.burstdet_seen.eq(burstdet_seen),
        ]

        if platform is not None and hasattr(platform, "request"):
            leds = [platform.request("led", n, dir="o") for n in range(6)]
            done = fsm.ongoing("DONE")
            m.d.comb += [
                # red: the DDRDLL never locked. Nothing below it means anything.
                leds[0].o.eq(~phy.dll_locked),
                # orange: no BURSTDET. The read strobe was never found, so a
                # green LED next to it would be a fixed-latency count that
                # happened to land right.
                leds[1].o.eq(done & ~burstdet_seen),
                leds[2].o.eq(~done),                      # yellow: running
                leds[3].o.eq(done & (errors == 0)),       # green: clean
                leds[4].o.eq(done & (errors != 0)),       # blue: mismatches
                leds[5].o.eq(heartbeat[23]),              # violet: heartbeat
            ]

        return m


def main(argv):
    import argparse

    root = Path(__file__).resolve().parent.parent.parent
    parser = argparse.ArgumentParser(
        description="DQS bring-up for HyperRAM. Never programs the board.")
    parser.add_argument("--build", action="store_true",
                        help="run yosys, nextpnr and ecppack; do not program")
    parser.add_argument("--sync-mhz", type=float, default=SYNC_MHZ,
                        help=f"sync clock; `fast` is twice it (default {SYNC_MHZ})")
    args = parser.parse_args(argv)

    if not args.build:
        print(__doc__)
        return 0

    from cynthion_platform import CynthionPlatformRev1D4
    out = root / "tmp" / "hyperram-dqs" / f"sync-{args.sync_mhz:g}"
    CynthionPlatformRev1D4().build(
        HyperRAMDQSBringup(sync_mhz=args.sync_mhz),
        build_dir=str(out),
        do_program=False)
    print(f"built into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
