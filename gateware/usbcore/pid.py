#
# USB 2.0 packet identifiers, speeds and UTMI mode constants.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Plain-Python constants shared by every usbcore layer (USB 2.0 8.3.1, UTMI+ 1.05).

- `PID`: the 4-bit packet ID; `PID.X.byte()` is the wire byte with its check nibble.
- `USBSpeed`: UTMI `XcvrSelect` encoding.
- `OpMode`, `TermSelect`: UTMI `OpMode` / `TermSelect` encodings (aliases share a value).
"""

from enum import IntEnum

__all__ = ["PID", "USBSpeed", "OpMode", "TermSelect", "pid_byte", "pid_check_ok"]


def pid_byte(pid):
    """Wire byte for a 4-bit PID: check nibble (ones' complement) in [7:4]."""
    return ((~pid & 0xF) << 4) | (pid & 0xF)


def pid_check_ok(byte):
    """True iff the byte's check nibble is the complement of its PID nibble."""
    return (byte & 0xF) == (~byte >> 4) & 0xF


class PID(IntEnum):
    # Token group: [1:0] = 0b01.
    OUT   = 0b0001
    IN    = 0b1001
    SOF   = 0b0101
    SETUP = 0b1101
    # Data group: [1:0] = 0b11.
    DATA0 = 0b0011
    DATA1 = 0b1011
    DATA2 = 0b0111
    MDATA = 0b1111
    # Handshake group: [1:0] = 0b10.
    ACK   = 0b0010
    NAK   = 0b1010
    STALL = 0b1110
    NYET  = 0b0110
    # Special group: [1:0] = 0b00. PRE and ERR share 0b1100.
    PRE   = 0b1100
    SPLIT = 0b1000
    PING  = 0b0100

    def byte(self):
        return pid_byte(int(self))


# Low two PID bits: the packet group.
PID_GROUP_TOKEN, PID_GROUP_DATA, PID_GROUP_HANDSHAKE, PID_GROUP_SPECIAL = 0b01, 0b11, 0b10, 0b00

# DATA0/1/2/MDATA indexed by the 2-bit `data_pid` select (toggle in bit 0).
DATA_PIDS = (PID.DATA0, PID.DATA1, PID.DATA2, PID.MDATA)


class USBSpeed(IntEnum):
    HIGH = 0b00
    FULL = 0b01
    LOW  = 0b10


class OpMode:
    NORMAL                    = 0
    NON_DRIVING               = 1
    RAW_DRIVE                 = 2
    DISABLE_BITSTUFF_AND_NRZI = 2
    CHIRP                     = 2
    NO_SYNC_OR_EOP            = 3


class TermSelect:
    HS_NORMAL    = 0
    HS_CHIRP     = 1
    LS_FS_NORMAL = 1
