#
# ULPI register window: still luna's, imported from this one place.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
- `ULPIRegisterWindow` is `luna.gateware.interface.ulpi.ULPIRegisterWindow` (BSD-3-Clause),
  unchanged. The class uses no Record; its module does (`amaranth.hdl.rec`).
- This module is the only luna import in `usbcore`; replacing the window is a later stage
  of #612. Ports used: `ulpi_data_in`, `ulpi_dir`, `ulpi_next`, `ulpi_data_out`,
  `ulpi_stop`, `busy`, `done`, `address`, `read_request`, `read_data`, `write_request`,
  `write_data`.
"""

from luna.gateware.interface.ulpi import ULPIRegisterWindow

__all__ = ["ULPIRegisterWindow"]
