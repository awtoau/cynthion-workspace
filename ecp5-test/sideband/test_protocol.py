#!/usr/bin/env python3
#
# Host side of the sideband TEST bitstream's extra commands.
# SPDX-License-Identifier: BSD-3-Clause

"""
POWER, DEVICES and LED: what `sideband_gateware.py` answers and the shipping link
does not.

**These opcodes exist in one bitstream only.** `ecp5-test/sideband/` instantiates
`apollo_fpga.gateware.sideband.SidebandResponder`, sources the PAC1954, the flash
JEDEC ID and the HyperRAM presence bit, and drives the board LEDs from the
responder's own state. That is a test bitstream's job -- diagnosing a board that
will not boot far enough to have a console.

The shipping SoC does **not** implement them, and answers them as unknown
commands. Its host side is `scripts/sideband_decoder.py`, which does not know
these opcodes at all. Keeping the two apart is the point: a decoder that knows
opcodes its bitstream does not implement can only mislead, and a shipping design
that answered them with zeros would look like a working query returning nothing.

Framing -- CRC-8, `status | payload | CRC` -- is shared and comes from
`sideband_decoder`, so the two protocols cannot disagree about the envelope.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "scripts"))

from apollo_fpga.gateware import sideband as gw
from sideband_decoder import crc8, decode_status


__all__ = ["reply_length", "check", "decode", "commands", "name",
           "encode_leds", "encode_led_colours", "encode_led_release",
           "LED_COLOURS"]


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


# The fixed pattern `sideband_gateware.py` drives into power_data:
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

    sys.path.insert(0, str(ROOT / "ecp5-test"))
    from power_monitor.registers import (raw_to_volts, raw_to_amps,
                                         CHANNEL_PORTS)
    vbus, vsense = values[:4], values[4:]
    return " | ".join(
        f"{CHANNEL_PORTS.get(ch, f'ch{ch}')} {raw_to_volts(vbus[ch]):.3f}V "
        f"{raw_to_amps(vsense[ch]) * 1000:.1f}mA" for ch in range(4))


def decode_ping(payload):
    version, reserved = payload
    note = "" if version == gw.PROTOCOL_VERSION else \
        f" (expected v{gw.PROTOCOL_VERSION})"
    return f"protocol v{version}{note}, reserved 0x{reserved:02x}"


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
