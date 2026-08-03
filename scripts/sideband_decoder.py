#!/usr/bin/env python3
#
# Encode and decode the FPGA_ADV sideband protocol, host side.
# SPDX-License-Identifier: BSD-3-Clause

"""
The SHIPPING sideband protocol as a library: bytes one way, meaning the other.

Apollo relays the sideband without interpreting it -- the microcontroller shifts
bytes, checks a CRC, and hands the buffer to the host unchanged. So every field
has to be understood on this end, and this module is the one place that
understands it. Decode belongs here rather than in whichever script needs it: a
self-test that prints `41ef401602bd` and calls it a pass proves the link works
and says nothing about the board.

## Three commands, and that is the whole map

| CMD | Name | Response |
|---|---|---|
| `0x01` | `PING` | STATUS + protocol version + the CPU's byte + CRC8 |
| `0x02` | `STATUS` | STATUS + CRC8 |
| `0x80`-`0xFF` | write | STATUS + CRC8; the low 7 bits reach the CPU |

Opcodes and payload sizes come from `ecp5-test/sideband_link.py` -- the gateware
this decodes -- rather than being restated, so they cannot drift.

## What this decoder deliberately does not know

**POWER, DEVICES and LED.** The shipping link does not implement them, so it
answers them as unknown commands, and a decoder that still knew them could only
mislead: it would report a query as understood when the design has no such
capability. They belong to the test bitstream, and their host side lives with it
in `ecp5-test/sideband/test_protocol.py`.

`PING`'s version byte is how the two are told apart at runtime: v1 is the test
bitstream's responder, v2 is this link.

## Decode: replies

A reply is `status | payload | CRC-8`, so the length to request is
**payload + 2**. Asking for the payload length alone truncates the reply before
its CRC and the check then fails on a working link -- a mistake made twice from
the command line before this module existed. `reply_length()` exists so it
cannot be made a third time.

## Not reachable over this link, and not intended to be

The SoC reads its own hardware: `power` over I2C, `board` for the memories,
`typec` for both FUSB302Bs, `info` and `selftest` for the image. Two decode
helpers survive here because other tooling references them -- the FUSB302B
`DEVICE_ID` layout and the ECP5 `DTR` code -- and neither has, or is expected to
get, a sideband command.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ecp5-test"))

# The SHIPPING link, not the responder in `repos/apollo`. That one carries POWER,
# DEVICES and LED for the test bitstream; this one does not, and importing it
# here is what keeps the two from being confused.
import sideband_link as gw


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------

def crc8(data):
    """CRC-8/ATM: poly 0x07, init 0x00, no reflection, no final XOR.

    Standard check value asserted at import, so a broken implementation fails
    loudly rather than reporting every reply as corrupt.
    """
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


assert crc8(b"123456789") == 0xF4, "CRC-8/ATM implementation is wrong"


def reply_length(opcode):
    """Bytes to request for a command: status + payload + CRC."""
    return gw.PAYLOAD_SIZE[opcode] + 2


def check(reply, opcode):
    """(ok, reason). Distinguishes short from corrupt: different faults."""
    want = reply_length(opcode)
    if len(reply) != want:
        # Short means the firmware timed out -- bytes that never arrived, as
        # opposed to bytes that arrived wrong.
        return False, f"SHORT (got {len(reply)}, wanted {want})"
    if crc8(reply[:-1]) != reply[-1]:
        return False, "CRC BAD"
    return True, "CRC OK"


# --------------------------------------------------------------------------
# encode -- host to FPGA
# --------------------------------------------------------------------------

def encode_message(value):
    """Opcode delivering a 7-bit byte to the CPU.

    The whole command is one byte: bit 7 selects, the low seven carry the value,
    so the link stays stateless -- one byte in, one reply out, nothing held
    between commands.

    Seven bits rather than eight because the opcode and the payload share one
    byte. Widening it to eight would need a second byte and a length the FPGA
    must parse, which is what makes a responder stateful and then makes it need
    a timeout.
    """
    if not 0 <= value <= gw.CMD_WRITE_MASK:
        raise ValueError(f"value must be 0..{gw.CMD_WRITE_MASK}, got {value}")
    return gw.CMD_WRITE_BASE | value


# --------------------------------------------------------------------------
# decode -- FPGA to host
# --------------------------------------------------------------------------

def decode_status(status):
    """The status byte as flags plus the 2-bit design-defined state field.

    The heartbeat is annotated as toggling because it **toggles on every
    response by design** (`sideband.py:692`, "so it blinks under polling").
    Consecutive STATUS replies therefore differ -- `41c0` then `0107` then `41c0`
    -- and both are correct. Reporting it as a plain flag makes that alternation
    look like the responder returning inconsistent values, which cost a debugging
    detour: `0107` also happens to resemble a PING payload, so it read as a
    reply-offset bug that did not exist.
    """
    flags = []
    for bit, label in ((gw.STATUS_OK, "ok"),
                       (gw.STATUS_EVENTS, "events"),
                       (gw.STATUS_ERROR, "ERROR"),
                       (gw.STATUS_RECONFIG, "reconfigured")):
        if status & (1 << bit):
            flags.append(label)
    beat = 1 if status & (1 << gw.STATUS_HEARTBEAT) else 0
    state = (status >> gw.STATUS_STATE_SHIFT) & 0x3
    return (f"state={state} heartbeat={beat}(toggles) "
            + (",".join(flags) if flags else "no flags"))


def decode_ping(payload):
    """Protocol version, then the byte the CPU put in the `tx` register.

    The version is what makes removing commands safe from this end: v1 was the
    responder that carried POWER, DEVICES and LED, v2 is the shipping link that
    does not. A host that pings first knows which map it is talking to instead of
    inferring it from a command that failed.
    """
    version, message = payload
    note = "" if version == gw.PROTOCOL_VERSION else \
        f" (expected v{gw.PROTOCOL_VERSION} -- this may be the test bitstream, " \
        f"whose extra commands live in ecp5-test/sideband/test_protocol.py)"
    return f"protocol v{version}{note}, message 0x{message:02x}"


DECODERS = {
    gw.CMD_PING: decode_ping,
    gw.CMD_STATUS: lambda payload: "",     # status byte carries everything
}

NAMES = {gw.CMD_PING: "PING", gw.CMD_STATUS: "STATUS"}


def name(opcode):
    return NAMES.get(opcode, f"0x{opcode:02x}")


def decode(opcode, reply):
    """Human-readable interpretation of a validated reply."""
    if len(reply) < 2:
        return ""
    status, payload = reply[0], reply[1:-1]
    expected = gw.PAYLOAD_SIZE.get(opcode)
    if expected is not None and len(payload) != expected:
        return f"{decode_status(status)} | payload length {len(payload)}, " \
               f"expected {expected}"
    body = DECODERS.get(opcode, lambda _: "")(payload)
    return decode_status(status) + (f" | {body}" if body else "")


def commands():
    """(name, opcode, payload_size) for every readable command."""
    return [(name(op), op, size) for op, size in sorted(gw.PAYLOAD_SIZE.items())]


# --------------------------------------------------------------------------
# not yet reachable -- layouts recorded so the gateware side is the only work
# --------------------------------------------------------------------------

FUSB302B_ADDRESS = 0x22
FUSB302B_REG_DEVICE_ID = 0x01


def decode_fusb302_device_id(value):
    """DEVICE_ID (0x01) on the FUSB302B: version and revision nibbles.

    Not reachable over the sideband -- no command exposes the Type-C buses. Read
    today by ecp5-test/pins/fusb302_id.py over JTAG registers. Kept here so the
    decode is ready when a command exists.
    """
    version = (value >> 4) & 0xF
    revision = value & 0xF
    known = {0x8: "FUSB302B", 0x9: "FUSB302B revised"}
    return (f"{known.get(version, f'version 0x{version:x}')} "
            f"rev 0x{revision:x} (raw 0x{value:02x})")


def decode_ecp5_temperature(code):
    """ECP5 DTR output to degrees Celsius.

    The DTR primitive is not instantiated in the sideband gateware, and
    LSC_READ_TEMP (0xE8) is not in Apollo's opcode set, so this has no source of
    data yet. The transfer function is device-specific and NOT established here
    -- returning a number would invent precision. Reports the raw code.
    """
    return f"DTR code 0x{code:02x} -- transfer function not established"


if __name__ == "__main__":
    # Self-check: framing arithmetic and the encode side, no hardware needed.
    print("sideband protocol, host side")
    print()
    print("readable commands:")
    for label, opcode, size in commands():
        print(f"  {label:<8} 0x{opcode:02x}  payload {size:>2}  "
              f"request {reply_length(opcode)} bytes")
    print(f"  write    0x{gw.CMD_WRITE_BASE:02x}-0xff  "
          f"payload  0  request {reply_length(gw.CMD_STATUS)} bytes")
    print()
    print("encode:")
    print(f"  message 0          0x{encode_message(0):02x}")
    print(f"  message 0x2a       0x{encode_message(0x2A):02x}")
    print(f"  message 0x7f       0x{encode_message(gw.CMD_WRITE_MASK):02x}")
    print()
    print("POWER, DEVICES and LED are NOT here: the shipping link does not")
    print("implement them. See ecp5-test/sideband/test_protocol.py.")
