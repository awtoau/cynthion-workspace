#!/usr/bin/env python3
#
# Drive the vendored USB host engine against a LUNA device, and check the wire.
# SPDX-License-Identifier: BSD-3-Clause

"""
The first increment of USB host mode: the transaction engine, exercised.

    ./scripts/usb_host_sie_sim.py        # every check
    ./scripts/usb_host_sie_sim.py -v     # and print every packet on the wire

Exit status 0 if every assertion held. Output goes to the terminal and to
`tmp/logs/dev.log`.

## What is under test

`ecp5-test/usb_host/guh/` -- GUH's `USBSIE` and `USBResetController`, vendored at
`923c8490` and byte-identical to upstream. `docs/usb-host-options.md` section 18
recommends taking exactly those and writing enumeration in firmware, and this is
the layer that recommendation rests on: if the engine cannot run a control
transfer against a real device stack, nothing above it is worth building.

The other end is LUNA's own `USBDevice` with a control endpoint and a bulk IN
endpoint (`ecp5-test/usb_host/model.py`). Both ends are upstream gateware that
was not written with the other in mind, which is what makes the result evidence
rather than a tautology.

## What it asserts, in three groups

**The bus comes up.** The host half of the high-speed chirp handshake is the one
piece nobody else in this ecosystem has written (`docs/usb-host-options.md`
section 1.3), so it is checked in both directions: a high-speed device is
detected as high speed, and a full-speed-only device falls back to full speed.
A chirp that lands on the wrong answer would still enumerate -- slowly, and with
no error anywhere -- so the negative case matters as much as the positive one.

**The wire is right, independently of the engine that drove it.** Tokens and data
packets are captured at the device's UTMI and decoded here, with CRC5 and CRC16
recomputed in Python from the USB 2.0 polynomials. GUH's own token test compares
against LUNA's reference packet library; this compares against arithmetic, so the
two are independent of each other and of the gateware.

**Five control transfers enumerate a device.** GET_DESCRIPTOR, SET_ADDRESS,
GET_DESCRIPTOR at the new address, SET_CONFIGURATION, then a 512-byte bulk IN.
That is section 16's claim -- that enumeration is five control transfers in
firmware and does not need the 830 LUT enumerator -- executed rather than
asserted. The transfers here are issued the way firmware would issue them: set
the fields, strobe start, wait for idle, read the response.

The negative control is the transfer to the *old* address after SET_ADDRESS. It
must time out, and without it "the device answered at 0x12" says nothing, since
the model answered at 0 a moment earlier.

## The three traps, now executable

`docs/usb-host-options.md` section 15.2 lists four traps in this interface that
a CPU-facing shim has to handle. Three of them are checked here, so a pin bump
that changes any of them fails this file rather than the shim:

- `rx_len` is 8 bits and wraps on a 512-byte high-speed packet. The check
  receives 512 bytes and asserts `rx_len` reads 0.
- completion is a level, not a strobe. `status.idle` is asserted for as long as
  the engine is idle, so a driver has to detect its edge; there is nothing to
  miss and nothing to clear.
- the data toggle is the caller's. The PID on the wire is whatever `xfer.data_pid`
  said, including when that is wrong.

The fourth -- NYET reported as ACK -- needs a device that returns NYET, which
LUNA's device stack does not do, so it stays a written warning.

## What this cannot say

Nothing about bit-level framing: the model wire hands over bytes, so bit
stuffing, NRZI and EOP are not exercised (`model.py` says where the line is
drawn). Nothing about the ULPI PHY, since the engine is instantiated on a bare
UTMI interface. Nothing about clock domain crossing, because the bench runs the
engine in `sync`. And nothing about hardware -- no board is touched here.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth.sim import Simulator  # noqa: E402

from usb_host.model import (  # noqa: E402
    BULK_IN_ENDPOINT, BULK_MAX_PACKET_SIZE, CONTROL_MAX_PACKET_SIZE,
    PRODUCT_ID, VENDOR_ID, Bench, capture_host_packets,
)
from usb_host.guh.sie import (  # noqa: E402
    DataPID, TransferResponse, TransferType, USBSOFController,
)
from usb_host.guh.types import USBHostSpeed  # noqa: E402

CLOCK_HZ = 60e6

# Packet identifiers as they appear on the wire, PID nibble plus complement.
PID_OUT, PID_IN, PID_SOF, PID_SETUP = 0xE1, 0x69, 0xA5, 0x2D
PID_DATA0, PID_DATA1, PID_ACK, PID_NAK = 0xC3, 0x4B, 0xD2, 0x5A

PID_NAMES = {
    PID_OUT: "OUT", PID_IN: "IN", PID_SOF: "SOF", PID_SETUP: "SETUP",
    PID_DATA0: "DATA0", PID_DATA1: "DATA1", PID_ACK: "ACK", PID_NAK: "NAK",
}

# The engine's own enums, used directly: the simulation should read the way a
# driver writing to this interface reads.
SETUP, IN, OUT = TransferType.SETUP, TransferType.IN, TransferType.OUT
DATA0, DATA1 = DataPID.DATA0, DataPID.DATA1
ACK, TIMEOUT = TransferResponse.ACK, TransferResponse.TIMEOUT

DEVICE_ADDRESS = 0x12  # the address enumeration assigns, arbitrary and non-zero


def name_of(value):
    """Enum member to its name; `ctx.get` returns members, not integers."""
    return getattr(value, "name", str(value))

# Cycle budgets. Every one of these is an upper bound on a wait whose expected
# duration is known, not a guess: exceeding one is a hang, and the check that
# follows reports it rather than the simulation running forever.
BRINGUP_CYCLES = 40_000     # reset (MAX_RESET_TIME/200) plus settle and chirps
TRANSACTION_CYCLES = 12_000  # a frame is 3750 cycles here; a timeout takes one
IDLE_HOLD_CYCLES = 200       # long enough that a one-cycle strobe would be over


def crc5(value, bits=11):
    """USB token CRC5: x^5 + x^2 + 1, seeded ones, inverted result."""
    crc = 0x1F
    for index in range(bits):
        if (crc & 1) ^ ((value >> index) & 1):
            crc = (crc >> 1) ^ 0x14
        else:
            crc >>= 1
    return crc ^ 0x1F


def crc16(payload):
    """USB data CRC16: x^16 + x^15 + x^2 + 1, seeded ones, inverted result."""
    crc = 0xFFFF
    for byte in payload:
        for index in range(8):
            if (crc & 1) ^ ((byte >> index) & 1):
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc ^ 0xFFFF


def setup_packet(request_type, request, value, index, length):
    return [request_type, request, value & 0xFF, value >> 8,
            index & 0xFF, index >> 8, length & 0xFF, length >> 8]


GET_DESCRIPTOR_DEVICE = setup_packet(0x80, 0x06, 0x0100, 0, 64)
SET_CONFIGURATION_1 = setup_packet(0x00, 0x09, 1, 0, 0)


def describe(packet):
    name = PID_NAMES.get(packet.pid, f"0x{packet.pid:02x}")
    return f"{name}[{len(packet.data)}]"


def token_fields(packet):
    """Address, endpoint and CRC5 out of a captured token packet."""
    value = packet.data[1] | (packet.data[2] << 8)
    return value & 0x7F, (value >> 7) & 0xF, (value >> 11) & 0x1F


async def put_bytes(ctx, stream, payload):
    for byte in payload:
        ctx.set(stream.payload, byte)
        ctx.set(stream.valid, 1)
        await ctx.tick().until(stream.ready == 1)
    ctx.set(stream.valid, 0)


async def transact(ctx, ctrl, *, type, address, endpoint, data_pid=DATA0,
                   payload=None, limit=TRANSACTION_CYCLES):
    """Run one transaction the way a driver would, and return what came back.

    Deliberately written as issue-then-observe rather than as a coroutine that
    owns the engine: set the fields, strobe `start`, watch `idle`, drain the RX
    stream. Nothing here decides *who* does the watching, which is the point --
    a superloop poll, an interrupt handler and an RTIC task all fit this shape.
    """
    if payload:
        await put_bytes(ctx, ctrl.txs, payload)

    ctx.set(ctrl.xfer.type, type)
    ctx.set(ctrl.xfer.dev_addr, address)
    ctx.set(ctrl.xfer.ep_addr, endpoint)
    ctx.set(ctrl.xfer.data_pid, data_pid)
    ctx.set(ctrl.rxs.ready, 1)

    ctx.set(ctrl.xfer.start, 1)
    await ctx.tick()
    ctx.set(ctrl.xfer.start, 0)

    received = []
    started = False
    for cycle in range(limit):
        await ctx.tick()
        if ctx.get(ctrl.rxs.valid):
            received.append(int(ctx.get(ctrl.rxs.payload)))
        if not ctx.get(ctrl.status.idle):
            started = True
        elif started:
            break
    else:
        return received, None, None, False

    # rx_len is sampled here, while the engine is idle and before the next
    # transfer clears it -- the same instant a driver would read it.
    rx_len = int(ctx.get(ctrl.status.rx_len))
    response = ctx.get(ctrl.status.response)

    # Anything still in the RX FIFO belongs to this transfer; the next one
    # discards it in DRAIN_RX.
    quiet = 0
    while quiet < 8:
        await ctx.tick()
        if ctx.get(ctrl.rxs.valid):
            received.append(int(ctx.get(ctrl.rxs.payload)))
            quiet = 0
        else:
            quiet += 1
    ctx.set(ctrl.rxs.ready, 0)
    return received, response, rx_len, True


async def wait_for_bringup(ctx, ctrl, limit=BRINGUP_CYCLES):
    """Wait for the reset controller to finish and the engine to reach idle."""
    for _ in range(limit):
        await ctx.tick()
        if ctx.get(ctrl.status.idle):
            return True
    return False


def run_high_speed(checks, verbose):
    bench = Bench(full_speed_only=False)
    ctrl = bench.ctrl
    packets = []
    # The microframe interval, read from the engine after the simulation scaling
    # rather than written down here, so the cadence check follows the constant.
    microframe = USBSOFController._SOF_CYCLES_HS
    state = {"microframe": microframe,
             "idle_window": 3 * microframe + IDLE_HOLD_CYCLES}

    async def testbench(ctx):
        state["up"] = await wait_for_bringup(ctx, ctrl)
        state["speed"] = ctx.get(ctrl.status.detected_speed)
        state["pulldowns"] = (int(ctx.get(bench.host.utmi.dp_pulldown)),
                              int(ctx.get(bench.host.utmi.dm_pulldown)))

        # --- GET_DESCRIPTOR(device) from the default address -----------------
        mark = len(packets)
        _, response, _, done = await transact(
            ctx, ctrl, type=SETUP, address=0, endpoint=0, data_pid=DATA0,
            payload=GET_DESCRIPTOR_DEVICE)
        state["setup"] = (response, done)
        state["setup_packets"] = packets[mark:]

        descriptor, _, _, _ = await transact(
            ctx, ctrl, type=IN, address=0, endpoint=0)
        state["descriptor"] = descriptor
        state["descriptor_packets"] = packets[mark:]

        mark = len(packets)
        _, response, _, _ = await transact(
            ctx, ctrl, type=OUT, address=0, endpoint=0, data_pid=DATA1)
        state["status_stage"] = response
        state["status_packets"] = packets[mark:]

        # --- SET_ADDRESS ------------------------------------------------------
        _, response, _, _ = await transact(
            ctx, ctrl, type=SETUP, address=0, endpoint=0, data_pid=DATA0,
            payload=setup_packet(0x00, 0x05, DEVICE_ADDRESS, 0, 0))
        state["set_address"] = response
        _, response, _, _ = await transact(
            ctx, ctrl, type=IN, address=0, endpoint=0)
        state["set_address_status"] = response

        # The negative control: nothing should answer on address 0 now.
        _, response, _, _ = await transact(
            ctx, ctrl, type=SETUP, address=0, endpoint=0, data_pid=DATA0,
            payload=GET_DESCRIPTOR_DEVICE)
        state["old_address"] = response

        # --- GET_DESCRIPTOR at the assigned address --------------------------
        await transact(ctx, ctrl, type=SETUP, address=DEVICE_ADDRESS, endpoint=0,
                       data_pid=DATA0, payload=GET_DESCRIPTOR_DEVICE)
        descriptor, _, _, _ = await transact(
            ctx, ctrl, type=IN, address=DEVICE_ADDRESS, endpoint=0)
        state["readdressed"] = descriptor
        await transact(ctx, ctrl, type=OUT, address=DEVICE_ADDRESS, endpoint=0,
                       data_pid=DATA1)

        # --- SET_CONFIGURATION(1) --------------------------------------------
        await transact(ctx, ctrl, type=SETUP, address=DEVICE_ADDRESS, endpoint=0,
                       data_pid=DATA0, payload=SET_CONFIGURATION_1)
        _, response, _, _ = await transact(
            ctx, ctrl, type=IN, address=DEVICE_ADDRESS, endpoint=0)
        state["set_configuration"] = response

        # --- a full high-speed bulk packet -----------------------------------
        bulk, response, rx_len, _ = await transact(
            ctx, ctrl, type=IN, address=DEVICE_ADDRESS,
            endpoint=BULK_IN_ENDPOINT)
        state["bulk"] = bulk
        state["bulk_response"] = response
        state["bulk_rx_len"] = rx_len

        # --- the shape of completion, and the frame clock ---------------------
        # Long enough to contain three microframes, so the SOF cadence is
        # measurable, and far longer than any strobe could survive.
        held = 0
        for _ in range(state["idle_window"]):
            await ctx.tick()
            held += int(ctx.get(ctrl.status.idle))
        state["idle_held"] = held
        state["response_held"] = ctx.get(ctrl.status.response)

    sim = Simulator(bench.module)
    sim.add_clock(1 / CLOCK_HZ)
    sim.add_process(capture_host_packets(bench.device.utmi, packets))
    sim.add_testbench(testbench)
    sim.run()

    if verbose:
        for packet in packets:
            emit(f"        {packet.cycle:7d}  {describe(packet)} "
                 + " ".join(f"{b:02x}" for b in packet.data))

    # A note rather than a check, so the harness charges the simulation to the
    # simulation instead of to whichever assertion happens to be read first.
    checks.note(f"{len(packets)} packets over "
                f"{packets[-1].cycle} cycles of the 60 MHz clock")
    check_high_speed(checks, state, packets)
    return state


def check_high_speed(checks, state, packets):
    checks.check(
        "the engine drives both host pull-downs",
        state["pulldowns"] == (1, 1),
        f"dp/dm pulldown = {state['pulldowns']}; LUNA's device stack drives "
        "these to 0, and a host that does the same never sees a connection")

    checks.check(
        "bus reset completes and the engine reaches idle",
        state["up"],
        f"no idle within {BRINGUP_CYCLES} cycles of power-on")

    checks.check(
        "a high-speed device is detected as high speed",
        state["speed"] == USBHostSpeed.HIGH,
        f"detected_speed = {state['speed']}, wanted {USBHostSpeed.HIGH} (HIGH); the "
        "host chirp K/J handshake is what decides this")

    sofs = [p for p in packets if p.pid == PID_SOF]
    intervals = [b.cycle - a.cycle for a, b in zip(sofs, sofs[1:])]
    microframe = state["microframe"]
    checks.check(
        "SOFs keep coming, one per microframe",
        len(sofs) >= 3 and all(i == microframe for i in intervals),
        f"{len(sofs)} SOFs at intervals {intervals}, wanted {microframe}; a "
        "device that stops seeing SOFs suspends the bus",
        measurement=f"{len(sofs)} SOFs, every {microframe} cycles "
                    f"({microframe} is 125 us scaled for simulation)")

    setup_tokens = [p for p in state["setup_packets"] if p.pid == PID_SETUP]
    ok = bool(setup_tokens)
    if ok:
        address, endpoint, crc = token_fields(setup_tokens[0])
        ok = (address, endpoint, crc) == (0, 0, crc5(0))
    checks.check(
        "the SETUP token carries the address, the endpoint and a valid CRC5",
        ok,
        f"tokens seen: {[describe(p) for p in state['setup_packets']]}")

    data_packets = [p for p in state["setup_packets"] if p.pid in (PID_DATA0, PID_DATA1)]
    ok = bool(data_packets)
    if ok:
        packet = data_packets[0].data
        body, trailer = packet[1:-2], packet[-2:]
        expected = crc16(body)
        ok = (packet[0] == PID_DATA0
              and body == GET_DESCRIPTOR_DEVICE
              and trailer == [expected & 0xFF, expected >> 8])
    checks.check(
        "the SETUP payload goes out as DATA0 with a CRC16 that checks",
        ok,
        f"data packets: {[describe(p) for p in data_packets]}")

    response, done = state["setup"]
    checks.check(
        "the device ACKs the SETUP transaction",
        done and response == ACK,
        f"response {name_of(response)}, completed={done}")

    descriptor = state["descriptor"]
    identity = (descriptor[:2], descriptor[8:12]) if len(descriptor) >= 12 else None
    checks.check(
        "the device descriptor comes back byte for byte",
        len(descriptor) == 18
        and descriptor[0] == 18 and descriptor[1] == 1
        and descriptor[7] == CONTROL_MAX_PACKET_SIZE
        and descriptor[8] | (descriptor[9] << 8) == VENDOR_ID
        and descriptor[10] | (descriptor[11] << 8) == PRODUCT_ID,
        f"{len(descriptor)} bytes: {identity}",
        measurement=f"{len(descriptor)} bytes, "
                    f"VID:PID {VENDOR_ID:04x}:{PRODUCT_ID:04x}")

    acks = [p for p in state["descriptor_packets"] if p.pid == PID_ACK]
    checks.check(
        "the host ACKs the data stage itself",
        len(acks) >= 1,
        "no ACK from the host on the wire; the device would retransmit forever")

    status_data = [p for p in state["status_packets"]
                   if p.pid in (PID_DATA0, PID_DATA1)]
    checks.check(
        "the data toggle is the caller's: DATA1 was asked for and DATA1 went out",
        bool(status_data) and status_data[0].pid == PID_DATA1,
        f"status stage packets: {[describe(p) for p in state['status_packets']]}")

    checks.check(
        "the status stage is accepted",
        state["status_stage"] == ACK,
        f"response {name_of(state['status_stage'])}")

    checks.check(
        "SET_ADDRESS is accepted and acknowledged",
        state["set_address"] == ACK and state["set_address_status"] == ACK,
        f"setup {name_of(state['set_address'])}, "
        f"status {name_of(state['set_address_status'])}")

    checks.check(
        "nothing answers on the old address once the new one is assigned",
        state["old_address"] == TIMEOUT,
        f"response {name_of(state['old_address'])}, wanted TIMEOUT; "
        "without this the next check proves nothing")

    checks.check(
        "the device answers on the address the token asked for",
        state["readdressed"] == state["descriptor"] and state["descriptor"],
        f"{len(state['readdressed'])} bytes at 0x{DEVICE_ADDRESS:02x} against "
        f"{len(state['descriptor'])} at address 0")

    checks.check(
        "SET_CONFIGURATION completes",
        state["set_configuration"] == ACK,
        f"response {name_of(state['set_configuration'])}")

    bulk = state["bulk"]
    expected = [(i) & 0xFF for i in range(len(bulk))]
    checks.check(
        "a 512-byte high-speed bulk packet arrives intact",
        len(bulk) == BULK_MAX_PACKET_SIZE and bulk == expected,
        f"{len(bulk)} bytes, first mismatch at "
        f"{next((i for i, (a, b) in enumerate(zip(bulk, expected)) if a != b), None)}",
        measurement=f"{len(bulk)} bytes of {BULK_MAX_PACKET_SIZE}")

    checks.check(
        "rx_len wraps at 256, so a shim has to count the FIFO itself",
        state["bulk_rx_len"] == BULK_MAX_PACKET_SIZE & 0xFF,
        f"rx_len reads {state['bulk_rx_len']} after {len(bulk)} bytes",
        measurement=f"rx_len={state['bulk_rx_len']} after {len(bulk)} bytes "
                    "-- the field is 8 bits (sie.py:616)")

    checks.check(
        "completion is a level, not a strobe",
        state["idle_held"] == state["idle_window"],
        f"idle held for {state['idle_held']} of {state['idle_window']} cycles",
        measurement=f"idle high for all {state['idle_window']} cycles after "
                    "completion; the shim edge-detects it for an interrupt")

    checks.check(
        "the response holds until the next transfer is started",
        state["response_held"] == state["bulk_response"],
        f"response drifted from {name_of(state['bulk_response'])} "
        f"to {name_of(state['response_held'])}")


def run_full_speed(checks, verbose):
    """The other half of the chirp: a device that refuses high speed."""
    bench = Bench(full_speed_only=True)
    ctrl = bench.ctrl
    packets = []
    state = {}

    async def testbench(ctx):
        state["up"] = await wait_for_bringup(ctx, ctrl)
        state["speed"] = ctx.get(ctrl.status.detected_speed)

        _, response, _, _ = await transact(
            ctx, ctrl, type=SETUP, address=0, endpoint=0, data_pid=DATA0,
            payload=GET_DESCRIPTOR_DEVICE)
        state["setup"] = response
        descriptor, response, _, _ = await transact(
            ctx, ctrl, type=IN, address=0, endpoint=0)
        state["descriptor"] = descriptor

    sim = Simulator(bench.module)
    sim.add_clock(1 / CLOCK_HZ)
    sim.add_process(capture_host_packets(bench.device.utmi, packets))
    sim.add_testbench(testbench)
    sim.run()

    if verbose:
        for packet in packets:
            emit(f"        {packet.cycle:7d}  {describe(packet)} "
                 + " ".join(f"{b:02x}" for b in packet.data))

    checks.note(f"{len(packets)} packets over "
                f"{packets[-1].cycle} cycles of the 60 MHz clock")
    checks.check(
        "a full-speed-only device is detected as full speed",
        state["up"] and state["speed"] == USBHostSpeed.FULL,
        f"detected_speed = {state['speed']}, wanted {USBHostSpeed.FULL} (FULL); a host "
        "that reads this wrong talks 480 Mbps at a device that cannot hear it")

    descriptor = state["descriptor"]
    checks.check(
        "a control transfer completes at full speed",
        state["setup"] == ACK and len(descriptor) == 18
        and descriptor[8] | (descriptor[9] << 8) == VENDOR_ID,
        f"setup {name_of(state['setup'])}, "
        f"{len(descriptor)} descriptor bytes")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every packet the device saw")
    args = parser.parse_args()

    checks = Checks(emit)
    emit("USB host engine (guh USBSIE, vendored at 923c8490) "
         "against a LUNA device")
    emit("  high speed")
    run_high_speed(checks, args.verbose)
    emit("  full speed")
    run_full_speed(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
