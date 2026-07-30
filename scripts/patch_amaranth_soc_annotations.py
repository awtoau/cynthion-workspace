#!/usr/bin/env python3
#
# Apply amaranth-soc d8b5892 to the vendored copy inside luna_soc.
# SPDX-License-Identifier: BSD-3-Clause

"""
Backports the Python 3.14 annotation fix into the vendored `amaranth_soc`.

`csr.Register` subclasses that declare fields as annotations are broken on Python
3.14 and later:

    class Probe(csr.Register, access="rw"):
        value: csr.Field(csr.action.RW, 8)

    TypeError: Field collection must be a dict, list, or Field, not None

The cause is `if hasattr(self, "__annotations__")` in `csr/reg.py`. On 3.14+
**every class has `__annotations__`**, so that guard stops distinguishing
anything and the code reads inherited annotations instead of the class's own.
Upstream fixed it in
`amaranth-soc` d8b58925533d9dd6be64a2ca9993bfe3a6d46ae9 (2026-01-28) by using
`annotationlib.get_annotations(type(self))` on 3.14+ and
`type(self).__dict__.get("__annotations__", {})` below.

## Why this is a script and not a dependency bump

`luna_soc` **vendors** `amaranth_soc` rather than depending on it, and upstream
`greatscottgadgets/luna-soc@main` carries the same unfixed file -- verified, not
assumed. So moving from luna-soc 0.2.0 to 0.3.2 does not fix this; the fix exists
only in `amaranth-soc` upstream, which luna-soc has not re-synced from.

`cynthion` pins luna-soc to `awtoau/awto-luna-soc@main`, so the durable home for
this patch is that fork. This script is the interim: it makes the installed copy
correct now, and documents exactly what the fork needs.

**The patch does not survive reinstalling luna-soc**, because the package is not
installed editable. Re-run it after any reinstall; `--check` says whether it is
needed.

    ./scripts/patch_amaranth_soc_annotations.py --check
    ./scripts/patch_amaranth_soc_annotations.py
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "patch_amaranth_soc_annotations.log"

OLD = '''        if hasattr(self, "__annotations__"):
            def filter_fields(src):
                if isinstance(src, Field):
                    return src
                if isinstance(src, (dict, list)):
                    items = enumerate(src) if isinstance(src, list) else src.items()
                    dst   = dict()
                    for key, value in items:
                        if new_value := filter_fields(value):
                            dst[key] = new_value
                    return list(dst.values()) if isinstance(src, list) else dst

            annot_fields = filter_fields(self.__annotations__)

            if fields is None:
                fields = annot_fields
            elif annot_fields:
                raise ValueError(f"Field collection {fields} cannot be provided in addition to "
                                 f"field annotations: {', '.join(annot_fields)}")
'''

NEW = '''        # Backported from amaranth-soc d8b5892: on Python 3.14+ every class has
        # __annotations__, so the old `hasattr` guard read inherited annotations
        # and subclasses declaring fields as annotations raised
        # "Field collection must be a dict, list, or Field, not None".
        try:
            import annotationlib  # py3.14+
        except ImportError:
            annotationlib = None  # py3.13-

        if annotationlib is not None:
            annotations = annotationlib.get_annotations(type(self))
        else:
            annotations = type(self).__dict__.get("__annotations__", {})

        def filter_fields(src):
            if isinstance(src, Field):
                return src
            if isinstance(src, (dict, list)):
                items = enumerate(src) if isinstance(src, list) else src.items()
                dst   = dict()
                for key, value in items:
                    if new_value := filter_fields(value):
                        dst[key] = new_value
                return list(dst.values()) if isinstance(src, list) else dst

        annot_fields = filter_fields(annotations)

        if fields is None:
            fields = annot_fields
        elif annot_fields:
            raise ValueError(f"Field collection {fields} cannot be provided in addition to "
                             f"field annotations: {', '.join(annot_fields)}")
'''


def target():
    """Locate the vendored reg.py through the import that actually resolves it."""
    import luna_soc.gateware.core.blockram   # noqa: F401  aliases amaranth_soc
    from amaranth_soc.csr import reg
    return Path(reg.__file__)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report whether the patch is needed, change nothing")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        path = target()
        emit(f"vendored reg.py: {path}")
        source = path.read_text()

        if "annotationlib" in source:
            emit("already patched -- annotationlib path present")
            return 0
        if OLD not in source:
            emit("the expected pre-fix block was not found")
            emit("The vendored copy differs from what this patch targets, so "
                 "applying it blindly could corrupt it. Compare against "
                 "amaranth-soc d8b5892 by hand.")
            return 1

        emit(f"needs patching: Python {sys.version_info.major}."
             f"{sys.version_info.minor} "
             + ("IS affected (3.14+)" if sys.version_info >= (3, 14)
                else "is not affected, but the patch is still correct"))

        if args.check:
            emit("--check: nothing changed")
            return 1

        backup = path.with_suffix(".py.pre-d8b5892")
        if not backup.exists():
            shutil.copy2(path, backup)
            emit(f"backup: {backup.name}")

        path.write_text(source.replace(OLD, NEW))
        emit("patched")

        # Prove it, rather than trusting the string replacement. A patch that
        # applies cleanly and still fails is worse than one that refuses.
        verify = subprocess_check()
        emit(verify)
        emit(f"log: {LOG}")
        return 0 if "OK" in verify else 1


def subprocess_check():
    """Elaborate an annotation-style Register in a fresh interpreter."""
    import subprocess
    script = (
        "import luna_soc.gateware.core.blockram\n"
        "from amaranth_soc import csr\n"
        "class Probe(csr.Register, access='rw'):\n"
        "    value: csr.Field(csr.action.RW, 8)\n"
        "Probe()\n"
        "print('annotation form OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script],
                            capture_output=True, text=True)
    if result.returncode == 0:
        return "verified: " + result.stdout.strip()
    return "VERIFICATION FAILED: " + (result.stderr.strip().splitlines() or [""])[-1]


if __name__ == "__main__":
    sys.exit(main())
