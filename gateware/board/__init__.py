#
# Local Cynthion board definitions.
# SPDX-License-Identifier: BSD-3-Clause

"""
The Cynthion r1.4 board definition, vendored so gateware here needs no
`cynthion` package.

    from board.cynthion_r1_4 import CynthionPlatformRev1D4

with `gateware/` on `sys.path`, which every design here already arranges.

Named `board`, not `platform`: `gateware/` goes on `sys.path`, and
a package called `platform` there shadows the *standard library* `platform`
module for the whole process. `amaranth/tracer.py` imports it, so the shadow
breaks signal-name tracing in every build. Do not rename this back.

Depends only on `amaranth`. See `core.py` for what was dropped from the upstream
LUNA inheritance chain and why.
"""

from .cynthion_r1_4 import CynthionPlatformRev1D4

__all__ = ["CynthionPlatformRev1D4"]
