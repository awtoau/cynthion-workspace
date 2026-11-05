# SPDX-License-Identifier: BSD-3-Clause
"""The one FPGA toolchain: yosys (+ our abc9 fix), prjtrellis and nextpnr-ecp5, all from main.

- Built by `scripts/yosys/build_fpga_toolchain.py` into `FPGA_PREFIX` at the pins below; its
  `environment` file puts that bin ahead of the 2026-11-04 suite's (kept for runtime libraries).
- Every script that runs yosys, nextpnr or ecppack sources `SOURCE` (or `soc_run.oss_cad_suite()`).
- Bump a pin to follow main, then rerun the build script. Decision: docs/yosys-070-upgrade.md, #605.
"""

YOSYS_PIN = "8f3cc3d0c"
TRELLIS_PIN = "2d12005"
TRELLIS_DB_PIN = "9754ff4"
NEXTPNR_PIN = "937c770c"
# Python packages installed from git (the system environment, no venv): `pip install --no-deps`
# "<name> @ git+<url>@<sha>"; `tests/test_python_stack_pins.py` fails if the installed versions drift.
LUNA_PIN = "352cdbfc284465b346e66b851eaf3ab68635060a"
LIBGREAT_PIN = "699d26766fd5"
FPGA_PREFIX = "$HOME/opt/fpga-main"
BASE_SUITE = "$HOME/opt/oss-cad-suite-20261104/oss-cad-suite"
SOURCE = f'source "{FPGA_PREFIX}/environment"'
