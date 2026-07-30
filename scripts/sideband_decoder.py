#!/usr/bin/env python3
#
# Encode and decode the FPGA_ADV sideband protocol, host side.
# SPDX-License-Identifier: BSD-3-Clause

"""
The sideband protocol as a library: bytes in one direction, meaning in the other.

Apollo relays the sideband without interpreting it -- the microcontroller shifts
bytes, checks a CRC, and hands the buffer to the host unchanged. So every field
has to be understood on this end, and until now that understanding lived in
whichever script happened to need it. A self-test that prints `41ef401602bd` and
calls it a pass proves the link works and says nothing about the board.

This module is that understanding, in one place, in both directions.

## Decode: replies

A reply is `status | payload | CRC-8`, so the length to request is
**payload + 2**. Asking for the payload length alone truncates the reply before
its CRC and the check then fails on a working link -- a mistake made twice from
the command line before this module existed. `reply_length()` exists so it
cannot be made a third time.

Payload sizes come from the gateware's own `PAYLOAD_SIZE` table rather than being
restated, so they cannot drift.

## Encode: commands

The LED command is the only thing the host sends beyond a bare opcode, and it was
being built inline at each call site. `encode_leds()` and `encode_led_release()`
name the format once. The opcode range `0x40-0x7F` carries the pattern in its low
six bits, which is why a stray byte in that range used to hijack the display.

## What is NOT reachable over the sideband

Worth stating, because "decode all the chips" is only possible for chips the
responder can see. Today the responder answers four commands: PING, STATUS,
POWER and DEVICES. That covers the PAC1954 power monitor, the configuration
flash JEDEC ID, and a HyperRAM presence bit.

Not reachable, and needing new commands in the gateware first:

  * **FUSB302B USB-PD controllers** (two, I2C 0x22 on separate buses). Read
    today only by `ecp5-test/pins/fusb302_id.py` over JTAG registers.
  * **Die temperature.** The ECP5 `DTR` primitive is not instantiated in the
    sideband gateware. `LSC_READ_TEMP` (0xE8) reads it over JTAG instead, and is
    not in Apollo's opcode set.
  * **Board serial / USERCODE.** `USERCODE` is never set on builds, so it reads
    as zeros and identifies nothing.

Decoders for those are stubbed here with the register layouts already
established, so adding the gateware command is the only remaining work.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "ecp5-test"))

from apollo_fpga.gateware import sideband as gw


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

def encode_leds(pattern):
    """Opcode for a six-LED pattern.

    The whole command is one byte: base 0x40 with the pattern in the low six
    bits, which keeps the protocol stateless. Bit 0 is the first LED.

    Colours, not indices, are how these are referred to on this board: index
    0-5 is red, orange, yellow, green, blue, violet.
    """
    if not 0 <= pattern <= gw.CMD_LED_MASK:
        raise ValueError(f"pattern must be 0..{gw.CMD_LED_MASK}, got {pattern}")
    return gw.CMD_LED_BASE | pattern


LED_COLOURS = ("red", "orange", "yellow", "green", "blue", "violet")


def encode_led_colours(*colours):
    """Opcode lighting the named colours, e.g. encode_led_colours("red", "blue")."""
    pattern = 0
    for colour in colours:
        try:
            pattern |= 1 << LED_COLOURS.index(colour.lower())
        except ValueError:
            raise ValueError(f"unknown LED colour {colour!r}; "
                             f"expected one of {LED_COLOURS}") from None
    return encode_leds(pattern)


def encode_led_release():
    """Opcode returning the LEDs to whatever the design drives.

    Without this the override is permanent -- it latches on any opcode in
    0x40-0x7F and nothing else clears it.
    """
    return gw.CMD_LED_RELEASE


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
    version, reserved = payload
    note = "" if version == gw.PROTOCOL_VERSION else \
        f" (expected v{gw.PROTOCOL_VERSION})"
    return f"protocol v{version}{note}, reserved 0x{reserved:02x}"


# JEDEC manufacturer IDs, so a self-test reads as a name rather than a byte.
JEDEC_MANUFACTURERS = {0xEF: "Winbond", 0xC2: "Macronix", 0x20: "Micron",
                       0x1F: "Adesto/Atmel", 0xBF: "SST", 0x01: "Spansion"}


def decode_devices(payload):
    """Flash JEDEC ID (manufacturer, type, capacity) then a presence-flags byte."""
    manufacturer, memory_type, capacity, flags = payload
    vendor = JEDEC_MANUFACTURERS.get(manufacturer,
                                     f"unknown 0x{manufacturer:02x}")
    # For these parts JEDEC capacity is log2 of the byte count.
    size = (f"{(1 << capacity) // (1024 * 1024)} MiB" if 16 <= capacity <= 32
            else f"code 0x{capacity:02x}")
    return (f"flash {vendor} type 0x{memory_type:02x} {size}, "
            f"hyperram {'present' if flags & 1 else 'absent'}")


# The fixed pattern `ecp5-test/sideband/sideband_gateware.py` drives into
# power_data:
#
#   power = 0xF001_DDEE_BBCC_99AA_7788_5566_3344_1100 | pmon_value
#
# Per-byte distinguishable, so a transposition shows as a wrong value rather
# than a plausible one -- and the LIVE PAC1954 manufacturer ID is ORed into the
# low byte, so a POWER reply does prove the I2C read happened.
#
# The consequence for decoding: scaling these to volts and amps produces
# confident nonsense. 0x3344 scales to 6.4 V and 3667 mA on a bus-powered
# board. Detected structurally rather than by exact equality, because the low
# byte varies with the live read and an equality check silently stops matching.
TEST_PATTERN_HIGH_BYTES = (0x11, 0x33, 0x55, 0x77, 0x99, 0xBB, 0xDD, 0xF0)


def looks_like_test_pattern(values):
    """True when the payload is the gateware's fixed pattern.

    Matches on the high byte of each word, which the pattern fixes, ignoring the
    low byte, which carries the live I2C value. Requiring all eight to match
    makes a false positive on real data implausible.
    """
    if len(values) != 8:
        return False
    return all((value >> 8) == expected
               for value, expected in zip(values, TEST_PATTERN_HIGH_BYTES))


def decode_power(payload):
    """VBUS[0..3] then VSENSE[0..3], little-endian 16 bits each.

    Scaled with the helpers `power_probe.py` uses rather than restating
    488.3 uV/LSB and the 0.02 ohm sense resistors, so two tools cannot report
    different volts for one raw count.
    """
    values = [int.from_bytes(payload[i:i + 2], "little")
              for i in range(0, len(payload), 2)]
    if not any(values):
        return "all zero -- no power monitor in this bitstream"

    if looks_like_test_pattern(values):
        live = values[0] & 0xFF
        note = (f"live PAC1954 manufacturer ID 0x{live:02x}"
                + (" (Microchip, expected)" if live == 0x54
                   else " -- expected 0x54"))
        return (f"TEST PATTERN, not measurements -- the gateware drives a fixed "
                f"pattern with {note} ORed into the low byte. "
                f"Scaling these would report volts and amps that do not exist.")

    from power_monitor.registers import (raw_to_volts, raw_to_amps,
                                         CHANNEL_PORTS)
    vbus, vsense = values[:4], values[4:]
    return " | ".join(
        f"{CHANNEL_PORTS.get(ch, f'ch{ch}')} {raw_to_volts(vbus[ch]):.3f}V "
        f"{raw_to_amps(vsense[ch]) * 1000:.1f}mA" for ch in range(4))


DECODERS = {
    gw.CMD_PING: decode_ping,
    gw.CMD_STATUS: lambda payload: "",     # status byte carries everything
    gw.CMD_DEVICES: decode_devices,
    gw.CMD_POWER: decode_power,
}

NAMES = {gw.CMD_PING: "PING", gw.CMD_STATUS: "STATUS",
         gw.CMD_POWER: "POWER", gw.CMD_DEVICES: "DEVICES"}


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
    print()
    print("encode:")
    print(f"  all LEDs off      0x{encode_leds(0):02x}")
    print(f"  all LEDs on       0x{encode_leds(gw.CMD_LED_MASK):02x}")
    print(f"  red + blue        0x{encode_led_colours('red', 'blue'):02x}")
    print(f"  release           0x{encode_led_release():02x}")
