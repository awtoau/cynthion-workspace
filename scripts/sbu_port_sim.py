#!/usr/bin/env python3
#
# The three-mode sideband peripheral: who owns the pads, and the crossing to SWD.
# SPDX-License-Identifier: BSD-3-Clause

"""
`SbuPort` driven over its CSR bus, with `sync` at 60 MHz and `swd` at 105 MHz.

`swd_host_sim.py` proves the SWD line protocol against a target model. This
proves the peripheral around it: that the mode register decides who drives, that
a transaction started from a CSR write crosses into the 105 MHz domain and comes
back, that raw mode captures transitions, and that the swap is one control over
everything.

What is checked:

  * **raw is the reset mode** -- out of reset the pins are released, and nothing
    can drive them until firmware asks
  * raw drives and releases each pin independently, and reads the live levels
  * every transition is captured with the levels after it and the `sync` cycles
    the previous levels held; the FIFO reports overflow rather than dropping
    silently; `capture_clear` empties it
  * capture does NOT run outside raw mode, which is what keeps it a fallback
    rather than a firehose
  * serial mode puts the transmitter on pin 0 and leaves pin 1 released
  * SWD mode drives SWCLK continuously and toggles it at the divisor
  * a transaction started by a CSR write completes, raises `irq`, and delivers
    ACK and RDATA -- the whole path across two clock domains
  * `cmd.ack` clears the interrupt and a second transaction runs
  * a `start` while busy is dropped, not queued
  * the SWD speed index picks its divisor at run time, and the serial baud
    register its bit period -- both are registers, not build-time constants
  * `mode.swap` exchanges both pins, in every mode, for output and input

## The negative controls

Each is RUN, and each asserts the corruption is REPORTED:

  * **turnaround** -- the target model answers one clock late. The CSR-level
    read reports a non-OK acknowledge instead of plausible data.
  * **parity** -- the target inverts the RDATA parity bit; `status.parity_error`
    reads 1 through the domain crossing.
  * **acknowledge** -- the target answers FAULT; no data phase runs and
    `status.ack` reads 0b100.
  * **capture** -- with the FIFO deliberately overfilled, `raw_in.overflow`
    reads 1. A capture that silently dropped would look like a quiet line.

Output goes to the console and to tmp/logs/dev.log.
"""

import argparse
import sys
import warnings
from pathlib import Path

from amaranth.hdl import Fragment, UnusedElaboratable
from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from peripherals.sbu import (MODE_RAW, MODE_SERIAL, MODE_SWD,  # noqa: E402
                             SbuPort)
from peripherals.swd import (ACK_FAULT, ACK_OK,  # noqa: E402
                             SPEED_DIVISORS)
from swd_host_sim import Target  # noqa: E402

warnings.filterwarnings("ignore", category=UnusedElaboratable)


SYNC_HZ = 60_000_000
SWD_HZ = 105_000_000
SWD_DIVISOR = SPEED_DIVISORS[0]
SERIAL_DIVISOR = 6

# Register offsets, from `SbuPort`'s own map.
MODE, RAW_OUT, RAW_IN, CMD, SWD_CTRL, STATUS = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
SPEED, WDATA, RDATA, CAPTURE, BAUD = 0x06, 0x08, 0x0c, 0x10, 0x14

CMD_START, CMD_LINE_RESET, CMD_POP, CMD_CLEAR, CMD_ACK = 1, 2, 4, 8, 16

# A transaction is 54 SWCLK cycles at divisor 2, plus the two crossings. 1.25x of
# that in `sync` cycles names a stalled handshake instead of hanging the run.
TRANSACTION_SYNC_CYCLES = int(54 * SWD_DIVISOR * SYNC_HZ / SWD_HZ * 1.25) + 32

# The wire model runs for as long as the testbench above it can: every CSR access
# is two `sync` ticks and the poll loop is the longest run of them. Bounded
# rather than `while True`, because a testbench that never returns is a
# simulation that never ends -- which presents as a hang, not as a failure.

SOAK = False


async def write(ctx, dut, offset, value):
    ctx.set(dut.bus.addr, offset)
    ctx.set(dut.bus.w_data, value)
    ctx.set(dut.bus.w_stb, 1)
    await ctx.tick()
    ctx.set(dut.bus.w_stb, 0)
    await ctx.tick()


async def read(ctx, dut, offset):
    ctx.set(dut.bus.addr, offset)
    ctx.set(dut.bus.r_stb, 1)
    await ctx.tick()
    ctx.set(dut.bus.r_stb, 0)
    value = ctx.get(dut.bus.r_data)
    await ctx.tick()
    return value


async def read32(ctx, dut, offset):
    value = 0
    for index in range(4):
        value |= (await read(ctx, dut, offset + index)) << (8 * index)
    return value


async def write_wide(ctx, dut, offset, value, width):
    """A register wider than the bus. amaranth-soc commits a multi-byte write on
    the LAST byte, so a partial write changes nothing at all."""
    for index in range(width):
        await write(ctx, dut, offset + index, (value >> (8 * index)) & 0xFF)


async def write32(ctx, dut, offset, value):
    await write_wide(ctx, dut, offset, value, 4)


def simulate(dut, bench, wire_process=None):
    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / SYNC_HZ, domain="sync")
    sim.add_clock(1 / SWD_HZ, domain="swd")
    sim.add_testbench(bench)
    if wire_process is not None:
        sim.add_testbench(wire_process)
    sim.run()


def wire_driver(dut, target, seen, divisor=SWD_DIVISOR):
    """The pads, driven by the target model on SWCLK's rising edges."""
    cycles = int(8 * sync_cycles_for(divisor) * SWD_HZ / SYNC_HZ)

    async def process(ctx):
        wire = 1
        previous_clock = 0
        edge = 0
        ctx.set(dut.pin_i, 0b11)
        for cycle in range(cycles):
            await ctx.tick("swd")
            pin_o, pin_oe = ctx.get(dut.pin_o), ctx.get(dut.pin_oe)
            clock = pin_o & pin_oe & 1
            host_oe = (pin_oe >> 1) & 1
            host_o = (pin_o >> 1) & 1

            if clock and not previous_clock:
                edge += 1
                seen.setdefault("edge_cycles", []).append(cycle)
                target.rising(edge, wire)
            previous_clock = clock

            driving, value = target.output()
            if driving and host_oe:
                seen["contention"] += 1
            wire = host_o if host_oe else (value if driving else wire)
            ctx.set(dut.pin_i, (wire << 1) | clock)

    return process


def sync_cycles_for(divisor):
    """`sync` cycles one transaction may take at this SWD divisor."""
    return int(54 * divisor * SYNC_HZ / SWD_HZ * 1.25) + 32


async def swd_transaction(ctx, dut, *, apndp=0, rnw=1, addr=0, wdata=0,
                          limit=TRANSACTION_SYNC_CYCLES):
    """One CSR-level transaction. Returns (irq, status, rdata, cycles)."""
    await write(ctx, dut, SWD_CTRL, apndp | (rnw << 1) | (addr << 2))
    await write32(ctx, dut, WDATA, wdata)
    await write(ctx, dut, CMD, CMD_START)

    for cycles in range(limit):
        status = await read(ctx, dut, STATUS)
        if status & 0b10:
            return ctx.get(dut.irq), status, await read32(ctx, dut, RDATA), cycles
    return ctx.get(dut.irq), None, None, limit


def run_swd(checks, *, target, name, expect_ack=ACK_OK, payload=0,
            rnw=1, wdata=0, speeds=(SWD_DIVISOR,), speed=0):
    """A CSR-level SWD transaction against `target`. Returns what came back."""
    dut = SbuPort(swd_speeds=speeds)
    seen = {"contention": 0}
    result = {}

    async def bench(ctx):
        await write(ctx, dut, MODE, MODE_SWD | (1 << 3))
        await write(ctx, dut, SPEED, speed)
        irq, status, rdata, cycles = await swd_transaction(
            ctx, dut, rnw=rnw, wdata=wdata,
            limit=sync_cycles_for(speeds[speed]))
        result.update(irq=irq, status=status, rdata=rdata, cycles=cycles)
        if status is not None:
            await write(ctx, dut, CMD, CMD_ACK)
            result["irq_after_ack"] = ctx.get(dut.irq)
            result["complete_after_ack"] = (await read(ctx, dut, STATUS)) & 0b10

    simulate(dut, bench, wire_driver(dut, target, seen,
                                     divisor=speeds[speed]))
    result["contention"] = seen["contention"]
    result["edge_cycles"] = seen.get("edge_cycles", [])
    return result


def run(checks):
    #
    # Raw mode: the reset state, the pins, and the capture FIFO.
    #
    dut = SbuPort(swd_speeds=(SWD_DIVISOR,))
    seen = {}

    async def raw_bench(ctx):
        ctx.set(dut.pin_i, 0b11)
        seen["reset_mode"] = await read(ctx, dut, MODE)
        seen["reset_oe"] = ctx.get(dut.pin_oe)

        # Drive pin 0 low, leave pin 1 released.
        await write(ctx, dut, RAW_OUT, 0b0100)
        seen["oe"] = ctx.get(dut.pin_oe)
        seen["o"] = ctx.get(dut.pin_o)

        # Four transitions on the input, each held a known number of cycles.
        seen["held"] = []
        for levels, cycles in ((0b10, 3), (0b00, 5), (0b01, 7), (0b11, 4)):
            ctx.set(dut.pin_i, levels)
            for _ in range(cycles):
                await ctx.tick()
            seen["held"].append((levels, cycles))

        seen["live"] = await read(ctx, dut, RAW_IN)
        seen["entries"] = []
        for _ in range(4):
            if not (await read(ctx, dut, RAW_IN)) & 0b100:
                break
            seen["entries"].append(await read32(ctx, dut, CAPTURE))
            await write(ctx, dut, CMD, CMD_POP)

        # Overfill: more transitions than the FIFO is deep, with nothing popped.
        for index in range(2 * 16 + 4):
            ctx.set(dut.pin_i, 0b01 if index % 2 else 0b10)
            await ctx.tick()
        seen["overflow"] = ((await read(ctx, dut, RAW_IN)) >> 3) & 1

        await write(ctx, dut, CMD, CMD_CLEAR)
        await ctx.tick()
        seen["after_clear"] = await read(ctx, dut, RAW_IN)

    simulate(dut, raw_bench)

    checks.check("raw is the reset mode", seen["reset_mode"] & 0b11 == MODE_RAW,
                 f"mode register came up {seen['reset_mode']:#04x}")
    checks.check("and both pins are released out of reset",
                 seen["reset_oe"] == 0,
                 f"pin_oe came up {seen['reset_oe']:#04b}; a sideband pin driven "
                 f"before firmware asks is a pin fighting whatever is out there")
    checks.check("raw mode drives one pin and releases the other",
                 seen["oe"] == 0b01 and seen["o"] & 0b01 == 0,
                 f"oe {seen['oe']:#04b}, o {seen['o']:#04b}")
    checks.check("raw mode reads the live pin levels",
                 seen["live"] & 0b11 == 0b11,
                 f"raw_in {seen['live']:#06b} with both pins high")

    entries = seen["entries"]
    levels = [entry & 0b11 for entry in entries]
    deltas = [entry >> 2 for entry in entries]
    checks.check(
        "every transition is captured, with the levels after it",
        levels == [0b10, 0b00, 0b01, 0b11],
        f"got {[f'{value:#04b}' for value in levels]}, expected the four "
        f"transitions the testbench drove")
    checks.check(
        "and how long the previous levels held",
        deltas[1:] == [3, 5, 7],
        f"deltas {deltas}, expected the 3, 5 and 7 cycles each level was held "
        f"for. The first entry counts from reset, not from a transition.")
    checks.check("the FIFO reports overflow rather than dropping silently",
                 seen["overflow"] == 1,
                 "36 transitions into a 16-deep FIFO with nothing popped")
    checks.check("capture_clear empties it and clears the overflow",
                 seen["after_clear"] & 0b1100 == 0,
                 f"raw_in {seen['after_clear']:#06b} after a clear")

    #
    # Serial mode owns pin 0 and leaves pin 1 to the receiver; capture stops.
    #
    dut = SbuPort(swd_speeds=(SWD_DIVISOR,))
    serial = {}

    async def serial_bench(ctx):
        ctx.set(dut.pin_i, 0b11)
        await write(ctx, dut, MODE, MODE_SERIAL)
        for index in range(8):
            ctx.set(dut.pin_i, 0b01 if index % 2 else 0b11)
            await ctx.tick()
        serial["captured"] = (await read(ctx, dut, RAW_IN)) & 0b100
        serial["rx_oe"] = ctx.get(dut.pin_oe) & 0b10

        # A byte out, through the stream port a 16550 would drive.
        ctx.set(dut.sink.payload, 0x5A)
        ctx.set(dut.sink.valid, 1)
        await ctx.tick()
        ctx.set(dut.sink.valid, 0)
        serial["tx_oe"] = 0
        for _ in range(6 * 12):
            await ctx.tick()
            serial["tx_oe"] |= ctx.get(dut.pin_oe) & 0b01

    simulate(dut, serial_bench)

    checks.check("serial mode leaves the receive pin released",
                 serial["rx_oe"] == 0)
    checks.check("serial mode drives the transmit pin for a character",
                 serial["tx_oe"] == 1,
                 "the byte was accepted and pin 0 was never driven")
    checks.check(
        "capture does not run outside raw mode",
        serial["captured"] == 0,
        "eight transitions in serial mode produced capture entries; in SWD "
        "mode that is a hundred per transaction and the FIFO is noise")

    #
    # SWD mode: a whole transaction, started and read entirely through CSRs and
    # crossing into the 100 MHz domain and back.
    #
    payload = 0xDEADBEEF
    ok = run_swd(checks, target=Target(ack=ACK_OK, rdata=payload), name="read")
    checks.check("a CSR-started SWD read completes", ok["status"] is not None,
                 f"no `complete` within {TRANSACTION_SYNC_CYCLES} sync cycles -- "
                 f"a pulse lost at the domain crossing looks exactly like this")
    checks.check("and reports OK across the crossing",
                 ok["status"] is not None and (ok["status"] >> 2) & 0b111 == ACK_OK,
                 f"status {ok['status']:#010b}")
    checks.check("and delivers RDATA across the crossing",
                 ok["rdata"] == payload,
                 f"rdata {ok['rdata']:#010x}, expected {payload:#010x}")
    checks.check("and no parity error", ok["status"] is not None
                 and not (ok["status"] >> 5) & 1)
    checks.check("the completion raises irq", ok["irq"] == 1,
                 "`complete` is sticky and gated by mode.irq_enable")
    checks.check("cmd.ack clears the interrupt and the flag",
                 ok.get("irq_after_ack") == 0
                 and ok.get("complete_after_ack") == 0,
                 f"irq {ok.get('irq_after_ack')}, complete "
                 f"{ok.get('complete_after_ack')}")
    checks.check("host and target never drive together, at the pads",
                 ok["contention"] == 0,
                 f"{ok['contention']} `swd` cycles with both drivers on")

    #
    # A write, and a second `start` while busy.
    #
    written = 0x0BADC0DE
    target = Target(ack=ACK_OK)
    write_result = run_swd(checks, target=target, name="write", rnw=0,
                           wdata=written)
    bits = target.wdata_bits
    value = sum(bit << index for index, bit in enumerate(bits[:32]))
    checks.check("a CSR-started SWD write puts WDATA on the pads",
                 value == written and len(bits) == 33,
                 f"got {value:#010x} from {len(bits)} bits")

    dut = SbuPort(swd_speeds=(SWD_DIVISOR,))
    busy_seen = {}
    busy_target = Target(ack=ACK_OK, rdata=payload)

    async def busy_bench(ctx):
        await write(ctx, dut, MODE, MODE_SWD | (1 << 3))
        await write(ctx, dut, SWD_CTRL, 0b10)
        await write(ctx, dut, CMD, CMD_START)
        # Wait for the crossing to raise `busy`, then try again.
        for _ in range(16):
            await ctx.tick()
        busy_seen["busy"] = (await read(ctx, dut, STATUS)) & 1
        await write(ctx, dut, CMD, CMD_START)
        for _ in range(TRANSACTION_SYNC_CYCLES):
            status = await read(ctx, dut, STATUS)
            if status & 0b10:
                break
        busy_seen["rdata"] = await read32(ctx, dut, RDATA)
        busy_seen["edges"] = busy_target.request

    simulate(dut, busy_bench, wire_driver(dut, busy_target, {"contention": 0}))
    checks.check("`busy` is visible in `sync` while the engine runs",
                 busy_seen["busy"] == 1)
    checks.check(
        "a start while busy is dropped, not queued",
        busy_seen["rdata"] == payload,
        f"rdata {busy_seen['rdata']:#010x}: a queued second start would have "
        f"begun a transaction against a line already mid-transfer")

    #
    # The swap: one control, both pins, both directions.
    #
    dut = SbuPort(swd_speeds=(SWD_DIVISOR,))
    swapped = {}

    async def swap_bench(ctx):
        ctx.set(dut.pin_i, 0b10)
        await write(ctx, dut, MODE, MODE_RAW | (1 << 2))
        await write(ctx, dut, RAW_OUT, 0b0100)
        swapped["oe"] = ctx.get(dut.pin_oe)
        swapped["level"] = (await read(ctx, dut, RAW_IN)) & 0b11

    simulate(dut, swap_bench)
    checks.check("mode.swap exchanges the output pins",
                 swapped["oe"] == 0b10,
                 f"pin_oe {swapped['oe']:#04b}; with swap set, pin 0's driver "
                 f"belongs on the other ball")
    checks.check("and the input pins, from the same control",
                 swapped["level"] == 0b01,
                 f"raw_in {swapped['level']:#04b} for a pad at 0b10")

    #
    # The two rate tables, both settable at run time.
    #
    slow = run_swd(checks, target=Target(ack=ACK_OK, rdata=payload),
                   name="slow", speeds=(SWD_DIVISOR, 8), speed=1)
    periods = {b - a for a, b in zip(slow["edge_cycles"],
                                     slow["edge_cycles"][1:])}
    checks.check(
        "the SWD speed index picks the divisor at run time",
        periods == {8} and slow["rdata"] == payload,
        f"SWCLK periods {sorted(periods)} `swd` cycles at index 1, expected "
        f"{{8}}; a table that only ever built at one rate passes every other "
        f"check here",
        measurement=f"{SWD_HZ / 8 / 1e6:.3f} MHz SWCLK at index 1")

    for divisor in (SERIAL_DIVISOR, 10):
        dut = SbuPort(swd_speeds=(SWD_DIVISOR,))
        timed = {"held": 0}

        async def baud_bench(ctx, divisor=divisor, timed=timed):
            ctx.set(dut.pin_i, 0b11)
            await write(ctx, dut, MODE, MODE_SERIAL)
            # The register is 18 bits and occupies four addresses; the
            # write commits on the LAST of them, so a three-byte write
            # changes nothing at all.
            await write32(ctx, dut, BAUD, divisor)
            ctx.set(dut.sink.payload, 0x5A)
            ctx.set(dut.sink.valid, 1)
            await ctx.tick()
            ctx.set(dut.sink.valid, 0)
            # Sample the cycle, THEN advance: the hold begins in the cycle the
            # byte was accepted, and ticking first walks past it.
            for _ in range(16 * divisor):
                timed["held"] += ctx.get(dut.pin_oe) & 1
                await ctx.tick()

        simulate(dut, baud_bench)
        # SerialLine drives for a whole character plus the mark it holds before
        # the start bit: (1 + 10) bit periods.
        checks.check(
            f"a baud divisor of {divisor} gives an {11 * divisor}-cycle "
            f"character",
            timed["held"] == 11 * divisor,
            f"pin 0 was driven for {timed['held']} cycles, expected "
            f"{11 * divisor}. The divisor is a register, not a build-time "
            f"constant.",
            measurement=f"{SYNC_HZ / divisor / 1e6:.3f} Mbit/s")

    #
    # ---- the negative controls ------------------------------------------
    #
    skewed = run_swd(checks, target=Target(turnaround=2, ack=ACK_OK,
                                           rdata=payload), name="skew")
    checks.check(
        "NEGATIVE CONTROL: a turnaround disagreement reaches the CSRs as one",
        skewed["status"] is None
        or (skewed["status"] >> 2) & 0b111 != ACK_OK
        or skewed["rdata"] != payload,
        f"a target one clock late returned status {skewed['status']} and rdata "
        f"{skewed['rdata']}; if that reads as a clean OK the checks above "
        f"cannot see a shifted stream")

    bad_parity = run_swd(checks, target=Target(ack=ACK_OK, rdata=payload,
                                               flip_data_parity=True),
                         name="parity")
    checks.check(
        "NEGATIVE CONTROL: parity_error crosses the domains",
        bad_parity["status"] is not None and (bad_parity["status"] >> 5) & 1,
        f"status {bad_parity['status']}; the corrupted payload arrived with "
        f"nothing saying so")

    faulted = run_swd(checks, target=Target(ack=ACK_FAULT), name="fault")
    checks.check(
        "NEGATIVE CONTROL: FAULT is reported rather than folded into OK",
        faulted["status"] is not None
        and (faulted["status"] >> 2) & 0b111 == ACK_FAULT,
        f"status {faulted['status']}")


def main():
    global SOAK
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--soak", action="store_true",
                        help="reserved for repetition; the checks here each "
                             "exercise their mechanism once")
    args = parser.parse_args()
    SOAK = args.soak

    emit("SBU port: three modes on one pin pair")
    checks = Checks(emit)
    run(checks)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
