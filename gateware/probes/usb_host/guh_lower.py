#
# The luna names the vendored GUH engine imports, provided by our usbcore layers.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Adapter beside `guh/` (docs/upstream-boundary.md: additions go beside the package).

- GUH's `sie.py`, `reset.py`, `types.py` import these names from here instead of luna;
  that import line is their only change.
- Same names, ports and cycle behaviour as the luna classes GUH used (luna 352cdbf);
  `scripts/soc/usbcore_equiv_sim.py` runs them in lockstep with luna's.
- CRC16 lives inside the data generator and receiver: `USBDataPacketCRC` and the
  `crc` / `data_crc` attributes exist only so GUH's wiring calls still elaborate.
"""

import sys
from pathlib import Path

_GATEWARE = str(Path(__file__).resolve().parents[2])
if _GATEWARE not in sys.path:
    sys.path.insert(0, _GATEWARE)

from amaranth import Elaboratable, Module, Signal  # noqa: E402

from usbcore.crc import crc5  # noqa: E402
from usbcore.data import DataGenerator, DataReceiver  # noqa: E402
from usbcore.handshake import HandshakeDetector, HandshakeGenerator  # noqa: E402
from usbcore.pid import PID, OpMode, TermSelect, USBSpeed  # noqa: E402
from usbcore.timer import InterpacketTimer, InterpacketTimerSignature  # noqa: E402
from usbcore.token import TokenDetector  # noqa: E402
from usbcore.ulpi import ULPITranslator  # noqa: E402
from usbcore.utmi import UTMISignature, UTMITxMux, UTMITxSignature  # noqa: E402

__all__ = [
    "UTMIOperatingMode", "UTMITerminationSelect", "USBSpeed", "USBPacketID",
    "UTMIInterface", "UTMITransmitInterface", "UTMIInterfaceMultiplexer", "UTMITranslator",
    "InterpacketTimerInterface", "USBInterpacketTimer", "USBDataPacketCRC",
    "USBDataPacketGenerator", "USBDataPacketReceiver", "USBHandshakeGenerator",
    "USBHandshakeDetector", "USBTokenDetector",
]

UTMIOperatingMode = OpMode
UTMITerminationSelect = TermSelect
USBPacketID = PID
UTMITranslator = ULPITranslator
UTMIInterfaceMultiplexer = UTMITxMux
USBHandshakeGenerator = HandshakeGenerator


def UTMIInterface():
    """A bare UTMI bus (simulation: the SIE without a ULPI PHY)."""
    return UTMISignature().create()


def UTMITransmitInterface():
    return UTMITxSignature().create()


def InterpacketTimerInterface():
    return InterpacketTimerSignature().create()


class USBInterpacketTimer(InterpacketTimer):
    def __init__(self, domain_clock=60e6, fs_only=False):
        if domain_clock != 60e6:
            raise ValueError("only the 60 MHz ULPI clock is supported")
        super().__init__(fs_only=fs_only)


class USBDataPacketCRC(Elaboratable):
    """Placeholder: GUH feeds it bytes and registers clients; the CRCs are internal."""

    def __init__(self, initial_value=0xFFFF):
        self.rx_data, self.rx_valid = Signal(8), Signal()
        self.tx_data, self.tx_valid = Signal(8), Signal()

    def add_interface(self, interface):
        pass

    def elaborate(self, platform):
        return Module()


def _wire_rx(m, rx, utmi):
    m.d.comb += [rx.data.eq(utmi.rx_data), rx.valid.eq(utmi.rx_valid),
                 rx.active.eq(utmi.rx_active)]


class USBDataPacketGenerator(DataGenerator):
    def __init__(self, standalone=False):
        super().__init__()
        self.crc = None


class USBDataPacketReceiver(DataReceiver):
    def __init__(self, *, utmi, standalone=False, speed=None):
        self._utmi = utmi
        super().__init__()
        self.data_crc = None

    def elaborate(self, platform):
        m = super().elaborate(platform)
        _wire_rx(m, self.rx, self._utmi)
        return m


class USBHandshakeDetector(HandshakeDetector):
    def __init__(self, *, utmi):
        self._utmi = utmi
        super().__init__()

    def elaborate(self, platform):
        m = super().elaborate(platform)
        _wire_rx(m, self.rx, self._utmi)
        return m


class USBTokenDetector(TokenDetector):
    """GUH uses only `_generate_crc_for_token` (the token CRC5)."""

    _generate_crc_for_token = staticmethod(crc5)

    def __init__(self, *, utmi):
        self._utmi = utmi
        super().__init__()

    def elaborate(self, platform):
        m = super().elaborate(platform)
        _wire_rx(m, self.rx, self._utmi)
        return m
