# SPDX-License-Identifier: BSD-3-Clause
"""Make every script in `scripts/` importable by its bare module name.

- Scripts import each other as `import soc_stream`, not `import soc.soc_stream`.
- `import _paths` appends `scripts/` and each tool subdirectory to `sys.path`.
- A script in a subdirectory first puts `scripts/` itself on the path, then
  imports this (see any file under `scripts/hyperram/` for the 3 lines).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBDIRS = ("apollo", "board", "fabric", "hyperram", "issues", "linux", "riscv", "sideband", "soc", "tools", "usb")

for _dir in (HERE, *(HERE / name for name in SUBDIRS)):
    if _dir.is_dir() and str(_dir) not in sys.path:
        sys.path.append(str(_dir))


def script_files():
    """Every tool script: `scripts/*.py` plus the tool subdirectories (not `diamond/`)."""
    found = list(HERE.glob("*.py"))
    for name in SUBDIRS:
        found += (HERE / name).glob("*.py")
    return sorted(found)


def find_script(name):
    """Path of the script called `name` (e.g. "soc_bus_sim.py"), wherever it lives."""
    for directory in (HERE, *(HERE / sub for sub in SUBDIRS)):
        if (directory / name).is_file():
            return directory / name
    raise FileNotFoundError(f"no script {name} under {HERE}")
