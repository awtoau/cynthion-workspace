#!/usr/bin/env python3
# What luna / luna_soc code does our gateware pull in, and what in it breaks on amaranth main?
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/tools/luna_closure.py [--only soc]

- Roots: every `luna*` import in `gateware/` (excluding `gateware/tmp`).
- Closure: modules of the installed `luna` / `luna_soc` packages reachable from those roots
  by their own `luna*` imports (static, via ast; imports inside functions count).
- Per module: lines of code, uses of APIs removed in amaranth main (`Record`, `amaranth.hdl.rec`,
  `Layout`-as-Record, `DIR_*`), reported so the port can be sized.
- Output: tmp/logs/luna_closure.log (all of gateware/) or luna_closure_soc.log (`--only soc`, which also follows the GUH engine under probes/); stdout too. Scope: #222.
"""

import ast
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
# `gateware/soc` imports the GUH host engine from `gateware/probes/usb_host/guh`, which imports luna.
SOC_EXTRA_DIRS = (("probes", "usb_host", "guh"),)
PACKAGES = ("luna", "luna_soc")
REMOVED = ("Record", "DIR_FANIN", "DIR_FANOUT", "DIR_NONE", "amaranth.hdl.rec")


def package_root(name):
    spec = importlib.util.find_spec(name)
    return Path(spec.origin).parent


ROOTS = {name: package_root(name) for name in PACKAGES}


def module_path(dotted):
    """File for a dotted luna module name, or None (a name inside a module, not a module)."""
    head = dotted.split(".")[0]
    if head not in ROOTS:
        return None
    parts = dotted.split(".")[1:]
    base = ROOTS[head].joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def dotted_name(path):
    """The module name of a file inside one of the luna packages."""
    for name, root in ROOTS.items():
        if root in path.parents or path.parent == root:
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            return ".".join([name, *parts]), path.name == "__init__.py"
    return None, False


def imports_of(path):
    """Dotted luna module names a file imports (relative imports resolved; `from pkg import
    submodule` counted as the submodule too)."""
    found = set()
    here, is_package = dotted_name(path)
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                if here is None:
                    continue
                package = here.split(".") if is_package else here.split(".")[:-1]
                package = package[:len(package) - (node.level - 1)]
                module = ".".join(package + ([module] if module else []))
            names = [module] + [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        found.update(name for name in names if name.split(".")[0] in ROOTS)
    return found


def used_names(path):
    """(used, imported_only): luna names a gateware file imports, split by whether anything
    other than the import statement refers to them."""
    tree = ast.parse(path.read_text())
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 \
                and node.module.split(".")[0] in ROOTS:
            for alias in node.names:
                if alias.name != "*":
                    imported[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in ROOTS:
                    imported[(alias.asname or alias.name).split(".")[0]] = alias.name
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    referenced |= {n.value.id for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    used = {full for name, full in imported.items() if name in referenced}
    return used, set(imported.values()) - used


def removed_uses(path):
    text = path.read_text()
    return {name: text.count(name) for name in REMOVED if name in text}


def code_lines(path):
    return sum(1 for line in path.read_text().splitlines()
               if line.strip() and not line.strip().startswith("#"))


def main():
    # `--only soc` limits the roots to gateware/soc (the production SoC), not probes/baselines.
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    roots = defaultdict(set)
    for source in sorted((ROOT / "gateware").rglob("*.py")):
        relative = source.relative_to(ROOT / "gateware")
        if "tmp" in relative.parts:
            continue
        if only and relative.parts[0] != only and not (
                only == "soc" and any(relative.parts[:len(d)] == d for d in SOC_EXTRA_DIRS)):
            continue
        for name in imports_of(source):
            roots[name].add(source.relative_to(ROOT).as_posix())

    seen, queue = {}, [name for name in roots if module_path(name)]
    while queue:
        name = queue.pop()
        path = module_path(name)
        if path is None or path in seen.values():
            continue
        seen[name] = path
        queue.extend(imports_of(path))

    # a package's __init__ is pulled in with its submodules; count each file once
    files = sorted(set(seen.values()))
    lines = []
    total_loc, broken, broken_loc = 0, [], 0
    for path in files:
        loc = code_lines(path)
        removed = removed_uses(path)
        total_loc += loc
        if removed:
            broken.append(path)
            broken_loc += loc
        rel = path.relative_to(ROOTS[path.relative_to(ROOTS["luna"].parent).parts[0]].parent)
        lines.append(f"{loc:5d}  {rel.as_posix():58s} {removed or ''}")

    out = [f"roots: {len(roots)} luna modules imported by gateware/ files "
           f"({len({f for fs in roots.values() for f in fs})} files)",
           f"closure: {len(files)} files, {total_loc} code lines",
           f"of which use APIs removed in amaranth main: {len(broken)} files, {broken_loc} lines", ""]
    out += lines
    out += ["", "gateware files that import luna, and what they import:"]
    by_file = defaultdict(list)
    for name, files_ in roots.items():
        for file in files_:
            by_file[file].append(name)
    for file in sorted(by_file):
        out.append(f"  {file}: {', '.join(sorted(by_file[file]))}")
    out += ["", "per gateware file: luna names actually USED / imported but never referenced:"]
    for file in sorted(by_file):
        used, idle = used_names(ROOT / file)
        out.append(f"  {file}")
        if used:
            out.append(f"      used: {', '.join(sorted(used))}")
        if idle:
            out.append(f"      IMPORT ONLY: {', '.join(sorted(idle))}")
    text = "\n".join(out)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / (f"luna_closure_{only}.log" if only else "luna_closure.log")
    log.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
