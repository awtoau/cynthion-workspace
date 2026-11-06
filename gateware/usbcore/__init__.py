#
# Our USB 2.0 low layers, shared by the host engine and (later) the device engine.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
USB low layers on `amaranth.lib.wiring` (no `Record`, no luna except the register window).
Plan and stages: #612. All logic runs in the 60 MHz `usb` domain.

| layer | module | owns |
|---|---|---|
| consts | `pid` | PIDs and check nibble, speeds, UTMI OpMode / TermSelect |
| L0 | `utmi` | UTMI+ signatures, transmit mux |
| L0 | `ulpi` | ULPI pins -> UTMI: `ulpi_rxcmd`, `ulpi_tx`, `ulpi_ctrl`, `ulpi_regwin` (luna's) |
| L1 | `crc` | CRC5, CRC16 (derived from the LFSR), running CRC16 + residual check |
| L1 | `token` | token generate / detect (CRC5, check nibble) |
| L1 | `data` | DATA0/1/2/MDATA generate / receive (CRC16) |
| L1 | `handshake` | ACK / NAK / STALL / NYET generate / detect |
| L1 | `timer` | inter-packet turnaround windows (HS, FS) |

- No bit stuffing, NRZI, SYNC or EOP here: the PHY does them. EOP = `valid` / `active` low.
- Simulation equivalence against luna: `scripts/soc/usbcore_equiv_sim.py`.
"""
