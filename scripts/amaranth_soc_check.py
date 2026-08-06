#!/usr/bin/env python3
#
# Fail if amaranth_soc resolves to luna_soc's vendored copy. See #190.
# SPDX-License-Identifier: BSD-3-Clause

"""
Assert that `amaranth_soc` and `amaranth_stdio` are the real packages.

## The failure this exists to catch

`luna_soc/__init__.py` carries what its own comment calls a "mildly evil hack":

    try:
        import amaranth_soc
        import amaranth_stdio
    except:
        sys.path.append(.../luna_soc/gateware/vendor)

So when the real packages are absent, the bare names still resolve -- to a tree
last re-synced 2025-01-07, reporting `__version__ == "unknown"`, missing four
upstream fixes. Nothing raises, nothing prints a version, and the gateware that
comes out is different. That is the whole hazard: not a build that breaks, a
build that quietly means something else.

Until #190 nothing declared either dependency, so this was one `pip uninstall`
or one clean machine away at all times. `scripts/machine_setup.py` now declares
both. This is the part that notices if that declaration is ever dropped, because
a declaration is invisible and a red check is not.

## Imported the way the designs import it

`from luna_soc.gateware.core import blockram` first, then the bare name -- the
load-bearing order documented at `gateware/soc/top.py:89`. Asking
the question any other way answers a different question than the one the build
asks.

## Proving it still works

    ./scripts/amaranth_soc_check.py --simulate-vendored

hides the real packages so luna_soc's fallback engages, and passes only if this
check FAILS. A guard nobody has watched fail is a guard nobody has tested.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import sys
from importlib.machinery import PathFinder
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# module name -> distribution name on the index
PACKAGES = {"amaranth_soc": "amaranth-soc", "amaranth_stdio": "amaranth-stdio"}

VENDOR_MARK = ("luna_soc", "gateware", "vendor")


class _ForceVendored:
    """Hide the real packages so luna_soc's fallback engages, for `--simulate-vendored`.

    Two phases, because the fallback is two-sided. Before luna_soc is imported,
    `find_spec` raises so its `try` fails and it appends the vendor directory.
    After that, lookups are served from that directory explicitly -- appending it
    is not enough on its own, since site-packages still precedes it on
    `sys.path` and the real package would win.
    """

    def __init__(self):
        self.vendor: Path | None = None

    def _mine(self, fullname: str) -> bool:
        return any(fullname == name or fullname.startswith(name + ".")
                   for name in PACKAGES)

    def find_spec(self, fullname, path=None, target=None):
        if not self._mine(fullname):
            return None
        if self.vendor is None:
            raise ModuleNotFoundError(f"hidden by --simulate-vendored: {fullname}",
                                      name=fullname)
        search = [str(self.vendor)] if "." not in fullname else path
        return PathFinder.find_spec(fullname, search)


def _is_vendored(module) -> bool:
    parts = Path(module.__file__ or "").parts
    return all(mark in parts for mark in VENDOR_MARK)


def _declared_version(distribution: str) -> str | None:
    """The installed distribution's version, or None if nothing declares one.

    The vendored copy is a directory on `sys.path` with no distribution metadata
    at all, so this is the second, independent signal that it is in use.
    """
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def inspect() -> int:
    """Import both packages as the designs do, and report where they came from."""
    # The aliasing import. Its side effect is the subject of the check, so it
    # cannot be skipped even though nothing here uses blockram.
    try:
        from luna_soc.gateware.core import blockram  # noqa: F401
    except ImportError as error:
        emit(f"  luna_soc is not importable ({error})")
        emit("  -- nothing can vendor over the real packages, but nothing")
        emit("     builds either. Install it and re-run.")
        return 1

    failures = []
    for name, distribution in PACKAGES.items():
        module = importlib.import_module(name)
        origin = module.__file__
        version = _declared_version(distribution)

        if _is_vendored(module):
            emit(f"  FAIL  {name}")
            emit(f"        {origin}")
            emit(f"        That is luna_soc's vendored tree, not {distribution}.")
            failures.append(name)
            continue
        if version is None:
            emit(f"  FAIL  {name}")
            emit(f"        {origin}")
            emit(f"        No distribution metadata: {distribution} is not "
                 "installed, so")
            emit("        nothing pins what this is.")
            failures.append(name)
            continue

        emit(f"  PASS  {name} {version}")
        emit(f"        {origin}")

    emit()
    if failures:
        emit(f"RESULT: FAIL - {len(failures)} package(s) resolved to luna_soc's "
             "vendored copy")
        emit("  Declared in scripts/machine_setup.py; install with:")
        emit("    ./dev.py setup")
        return 1

    emit(f"RESULT: PASS - all {len(PACKAGES)} resolve to the declared packages")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--simulate-vendored", action="store_true",
                        help="hide the real packages and pass only if this "
                             "check fails")
    args = parser.parse_args()

    if not args.simulate_vendored:
        return inspect()

    if any(name in sys.modules for name in PACKAGES):
        emit("cannot simulate: the real packages are already imported")
        return 1

    finder = _ForceVendored()
    sys.meta_path.insert(0, finder)
    import luna_soc  # noqa: F401  (the fallback fires here)
    finder.vendor = Path(luna_soc.__file__).parent / "gateware" / "vendor"

    emit(f"simulating a clean install: serving from {finder.vendor}")
    emit()
    rc = inspect()
    emit()
    if rc == 0:
        emit("SELF-TEST FAIL - the vendored copy was NOT detected")
        return 1
    emit("SELF-TEST PASS - the vendored copy was detected, as it should be")
    return 0


if __name__ == "__main__":
    sys.exit(main())
