#!/usr/bin/env python3
#
# Resolve ARM binutils beside the compiler, not by PATH order. See #191.
# SPDX-License-Identifier: BSD-3-Clause

"""
The one place that answers "which `arm-none-eabi-nm` / `size` should we run".

`/opt/arm-gnu-toolchain/...-13.2.Rel1/bin` precedes `/usr/bin` on this machine
and holds binutils **2.41.0.20231009 with no gcc**. Compiling and linking are
unaffected -- gcc finds its own `ld` through `-print-prog-name` regardless of
PATH -- but every guard that shelled out by bare name read a GCC 15.2.0 ELF with
2023 tools. Those guards are the flash budget and the vector table, so a
mismatch there fails toward false reassurance rather than toward an error.

## How a tool is found

`arm-none-eabi-gcc -print-prog-name=nm` returns the absolute path of the binutil
gcc itself would invoke, which is the definition of "beside the compiler".

`size` is the exception and the reason this is not a one-liner: gcc never
invokes it, so `-print-prog-name=size` answers with the bare name `size` rather
than failing. Fedora also does not ship `size` in the internal
`<prefix>/arm-none-eabi/bin/` directory the other binutils live in. So the
install prefix is derived from a tool gcc *does* know -- `<prefix>/arm-none-eabi/
bin/ld` -- and `<prefix>/bin/arm-none-eabi-size` is used.

PATH is the last resort, not the first.

## Why the resolution is printed

A silent correction is half a fix. `report()` names the tool that was chosen
and, when PATH would have given a different one, names that too with both
versions -- the next person's PATH is still misleading and nothing else says so.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GCC = "arm-none-eabi-gcc"
PREFIX = "arm-none-eabi-"


@dataclass(frozen=True)
class Resolved:
    """One tool, where it came from, and what PATH would have given instead."""
    name: str
    path: str | None
    source: str
    # The PATH candidate, when it differs from `path`. None means PATH agreed.
    shadowed: str | None


@functools.cache
def _print_prog_name(name: str) -> str | None:
    """The absolute path gcc would invoke for `name`, or None if it has none.

    gcc answers with the bare name for a tool it does not know, so a non-absolute
    or non-existent answer is treated as "no answer".
    """
    gcc = shutil.which(GCC)
    if not gcc:
        return None
    result = subprocess.run([gcc, f"-print-prog-name={name}"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    candidate = Path(result.stdout.strip())
    if not candidate.is_absolute() or not candidate.exists():
        return None
    # gcc emits `/usr/lib/gcc/arm-none-eabi/15.2.0/../../../../arm-none-eabi/bin/nm`.
    return str(Path(candidate.resolve()))


@functools.cache
def _install_prefix() -> Path | None:
    """`<prefix>` such that `<prefix>/bin/arm-none-eabi-*` are the compiler's own.

    Derived from a binutil gcc knows about, whose path is
    `<prefix>/arm-none-eabi/bin/<tool>`.
    """
    for probe in ("ld", "nm", "objcopy"):
        found = _print_prog_name(probe)
        if not found:
            continue
        directory = Path(found).parent
        if directory.name == "bin" and directory.parent.name == "arm-none-eabi":
            return directory.parent.parent
    return None


@functools.cache
def resolve(name: str) -> Resolved:
    """Locate one binutil. `name` is unprefixed: "nm", "size", "objcopy"."""
    on_path = shutil.which(PREFIX + name) or shutil.which(name)

    path = _print_prog_name(name)
    source = f"{GCC} -print-prog-name={name}"

    if path is None:
        prefix = _install_prefix()
        if prefix is not None:
            candidate = prefix / "bin" / (PREFIX + name)
            if candidate.exists():
                path, source = str(candidate), f"the compiler's prefix {prefix}"

    if path is None:
        path, source = on_path, "PATH"

    shadowed = None
    if path and on_path and Path(on_path).resolve() != Path(path).resolve():
        shadowed = on_path

    return Resolved(name, path, source, shadowed)


def tool(name: str) -> str | None:
    """The path to use, or None if the tool could not be found at all."""
    return resolve(name).path


@functools.cache
def version(path: str) -> str:
    """The `--version` first line, which is where the release number lives."""
    try:
        result = subprocess.run([path, "--version"], capture_output=True,
                                text=True)
    except OSError as error:
        return f"<unrunnable: {error}>"
    line = (result.stdout or result.stderr).splitlines()
    return line[0].strip() if line else "<no version>"


def report(emit, *names: str) -> None:
    """Say which binutils were used, and warn when PATH disagrees."""
    for name in names:
        found = resolve(name)
        if found.path is None:
            emit(f"  {PREFIX}{name}: NOT FOUND")
            continue
        emit(f"  {PREFIX}{name}: {found.path}")
        emit(f"    {version(found.path)}  (via {found.source})")
        if found.shadowed:
            emit(f"    WARNING: PATH would have given {found.shadowed}")
            emit(f"             {version(found.shadowed)}")
            emit("             That directory precedes the compiler's own on "
                 "PATH. Nothing")
            emit("             here used it, but anything calling the bare name "
                 "will.")


if __name__ == "__main__":
    report(print, "nm", "size", "objcopy")
