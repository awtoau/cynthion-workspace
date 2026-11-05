# SPDX-License-Identifier: BSD-3-Clause
"""The one FPGA toolchain: yosys main + our abc9 fix, nextpnr and ecppack from the 2026-11-04 suite.

- Built by `scripts/yosys/build_patched_yosys.py` into `YOSYS_PREFIX`; its `environment` file
  puts that yosys ahead of the suite's on PATH.
- Every script that runs yosys, nextpnr or ecppack sources `SOURCE` (or `soc_run.oss_cad_suite()`).
- Decision: docs/yosys-070-upgrade.md, #605.
"""

YOSYS_PIN = "8f3cc3d0c"
YOSYS_PREFIX = f"$HOME/opt/yosys-patched/{YOSYS_PIN}"
BASE_SUITE = "$HOME/opt/oss-cad-suite-20261104/oss-cad-suite"
SOURCE = f'source "{YOSYS_PREFIX}/environment"'
