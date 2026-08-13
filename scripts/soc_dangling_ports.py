#!/usr/bin/env python3
"""Find SoC ports that exist in gateware and are wired to nothing, or to a constant.

Three verdicts, worst first:

* `constant` -- an input port whose only driver is a literal. #327's shape one
  level up: it elaborates, places, routes and lies. `register_space` is the case
  that prompted [#531](https://github.com/awtoau/cynthion-workspace/issues/531).
* `unread` -- an output port nothing outside the module reads. A diagnostic that
  cannot report.
* `undriven` -- an input port with no driver at all; sits at its `init`.

Method and its limits:

* Ports are `self.x = Signal(...)` and `wiring.Signature({"x": In/Out(...)})`
  under `gateware/soc/**`. Local signals inside `elaborate` are not ports and
  are out of scope.
* Matching is by NAME, not by object: `obj.x.eq(` drives, any other `.x` reads.
  Names collide across classes, so every hit is a candidate to confirm by hand.
* It UNDER-reports one shape: a mux that forwards a field counts as a driver,
  so a chain ending at an undriven face reads as wired. `latency_clocks` is the
  live example -- `hyperram_share.py:191` drives the controller from
  `stage`/`bist`, and neither face is driven by anything. Object-level, and out
  of this scan's reach.
* `--check` asserts known-dangling ports are found AND known-wired ports are
  not, so a pass carries information ([`docs/instruments.md`](../docs/instruments.md)).

Log: `tmp/logs/soc_dangling_ports.log`. Audit: [`docs/gateware-vs-firmware.md`](../docs/gateware-vs-firmware.md).
"""

from __future__ import annotations

import argparse
import ast
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_dangling_ports.log"

DECLARE_ROOTS = ("gateware/soc",)
# The verdict is about the SHIPPING SoC -- what the RISC-V can reach. A lever
# only a JTAG probe drives, or only a simulation sets, is still baked as far as
# firmware is concerned, so those two are reported beside the verdict, not
# folded into it.
DESIGN_ROOTS = ("gateware/soc",)
PROBE_ROOTS = ("gateware/probes",)
SIM_ROOTS = ("scripts", "tests")

# Amaranth / amaranth-soc plumbing, not ports of ours.
IGNORE = {
    "bus", "signature", "memory_map", "name", "width", "depth", "shape",
    "init", "reset", "clk", "domain", "granularity", "addr_width", "data_width",
    "payload", "valid", "ready", "data", "we", "en", "addr",
}

CONSTANT = re.compile(r"\.eq\(\s*(?:0|1|-1|0[xb][0-9a-fA-F_]+|\d+|C\(|Const\()")

# --check expectations. A control in each direction.
MUST_FIND = {
    "register_space": "constant",   # #319/#531: the bit is allocated, tied to 0
    "armed": "unread",              # serial_line.py, declared a diagnostic
    "frame_errors": "unread",       # serial_line.py, declared a diagnostic
    "phy_ready": "unread",          # clocks.py, the 1.2 ms ULPI preparation
    "recovery_cycles": "undriven",  # #341's tCSHI lever, added and never wired
    "read_stall_cycles": "undriven",  # bootram.py, the sink for `sel` bits 5:4
}
MUST_NOT_FIND = {
    "readclksel",     # bootram.py <- hyper_probe.sel: the exemplar, wired
    "start_transfer", # driven every transaction
    "final_word",
    "write_ready",
    "perform_write",
}


def declared_ports(path: Path) -> dict[str, int]:
    """`self.x = Signal(...)` and `Signature({"x": In/Out(...)})`, name -> line."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return {}
    found: dict[str, int] = {}

    def keep(name: str, lineno: int) -> None:
        if name not in IGNORE and not name.startswith("_"):
            found.setdefault(name, lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if callee in ("Signal", "Array"):
                for target in node.targets:
                    if (isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"):
                        keep(target.attr, node.lineno)
        # Signature({...}), wiring.Signature({...}) and a Component's
        # super().__init__({...}) -- the same port dict under three spellings.
        if isinstance(node, ast.Call):
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if callee not in ("Signature", "__init__") or not node.args:
                continue
            if not isinstance(node.args[0], ast.Dict):
                continue
            for key, value in zip(node.args[0].keys, node.args[0].values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                vfunc = value.func if isinstance(value, ast.Call) else None
                vname = (vfunc.id if isinstance(vfunc, ast.Name)
                         else getattr(vfunc, "attr", ""))
                if vname in ("In", "Out"):
                    keep(key.value, key.lineno)
    return found


def indirect_drives(path: Path) -> set[str]:
    """Drives a textual `.x.eq(` scan cannot see.

    * `for field in PORT.CONTROL: getattr(a, field).eq(...)` -- the share mux.
    * `FFSynchronizer(src, dst)` -- `dst` is driven by the instance, and
      `top.py` wires the DQS tap that way.
    """
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return set()
    tuples: dict[str, list[str]] = {}
    driven: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
            members = [e.value for e in node.value.elts
                       if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            for target in node.targets:
                if isinstance(target, ast.Name) and members:
                    tuples[target.id] = members
                elif isinstance(target, ast.Attribute) and members:
                    tuples[target.attr] = members

    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            iter_name = (node.iter.id if isinstance(node.iter, ast.Name)
                         else getattr(node.iter, "attr", None))
            body = "".join(ast.unparse(stmt) for stmt in node.body)
            if iter_name in tuples and ".eq(" in body:
                driven.update(tuples[iter_name])
        if isinstance(node, ast.Call):
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if not callee.endswith("Synchronizer") or len(node.args) < 2:
                continue
            for sub in ast.walk(node.args[1]):
                if isinstance(sub, ast.Attribute):
                    driven.add(sub.attr)
    return driven


def sources(roots: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        out.extend(sorted((ROOT / root).rglob("*.py")))
    return out


def scan(names: set[str], files: list[Path]) -> tuple[dict, dict, dict, dict]:
    """Per name: files that drive it, literal drive sites, readers, live drivers.

    A file that drives a port from a literal AND from an expression -- a default
    overridden inside an `m.If`, as `top.py:2040` does for `sda_i` -- is a live
    driver, so the two sets are kept apart rather than counted.
    """
    drives: dict[str, set[Path]] = defaultdict(set)
    const: dict[str, set[str]] = defaultdict(set)
    reads: dict[str, set[Path]] = defaultdict(set)
    live: dict[str, set[Path]] = defaultdict(set)
    patterns = {n: (re.compile(rf"\.{n}\b\s*(?:\.\w+\s*)?(\.eq\()"),
                    re.compile(rf"\.{n}\b")) for n in names}
    for path in files:
        text = path.read_text()
        lines = text.splitlines()
        for name in indirect_drives(path) & names:
            drives[name].add(path)
            live[name].add(path)
        for name, (drive_re, use_re) in patterns.items():
            if name not in text:
                continue
            for lineno, line in enumerate(lines, 1):
                if not use_re.search(line):
                    continue
                match = drive_re.search(line)
                if not match:
                    reads[name].add(path)
                    continue
                drives[name].add(path)
                if CONSTANT.match(line, match.start(1)):
                    const[name].add(f"{path.relative_to(ROOT)}:{lineno}")
                else:
                    live[name].add(path)
    return drives, const, reads, live


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="self-test against the known-dangling set")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler()])
    log = logging.getLogger("dangling")

    declared: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    for path in sources(DECLARE_ROOTS):
        for name, lineno in declared_ports(path).items():
            declared[name].append((path, lineno))
    log.info("%d port names declared across %d files",
             len(declared), len({p for v in declared.values() for p, _ in v}))

    names = set(declared)
    drives, const, reads, live = scan(names, sources(DESIGN_ROOTS))
    probe_drives, _, probe_reads, _ = scan(names, sources(PROBE_ROOTS))
    sim_drives, _, sim_reads, _ = scan(names, sources(SIM_ROOTS))

    verdicts: dict[str, str] = {}
    rows: list[tuple[str, str, str, list[tuple[Path, int]]]] = []
    for name, sites in sorted(declared.items()):
        own = {p for p, _ in sites}
        outside_drive = drives[name] - own
        outside_read = reads[name] - own
        inside_drive = drives[name] & own
        outside_live = live[name] - own
        outside_const = {c for c in const[name]
                         if ROOT / c.rsplit(":", 1)[0] not in own}
        if outside_drive and outside_const and not outside_live:
            verdict, detail = "constant", " ".join(sorted(outside_const))
        elif not outside_drive and inside_drive and not outside_read:
            # An output the module computes and nothing consumes.
            verdict, detail = "unread", "driven, read by nothing else in the SoC"
        elif not outside_drive and not inside_drive:
            # An input at its `init` for the life of the bitstream.
            verdict, detail = "undriven", "nothing in the SoC drives it"
        else:
            continue
        elsewhere = []
        if probe_drives[name] or probe_reads[name]:
            elsewhere.append("probes use it")
        if sim_drives[name] or sim_reads[name]:
            elsewhere.append("sims use it")
        verdicts[name] = verdict
        rows.append((name, verdict, "; ".join([detail] + elsewhere), sites))

    for verdict in ("constant", "unread", "undriven"):
        for name, this, detail, sites in rows:
            if this != verdict:
                continue
            where = " ".join(f"{p.relative_to(ROOT)}:{n}" for p, n in sites)
            log.info("%-9s %-22s %s | %s", verdict, name, where, detail)
    log.info("%d candidates of %d port names", len(rows), len(declared))

    if args.check:
        bad = 0
        for name, want in sorted(MUST_FIND.items()):
            got = verdicts.get(name)
            if got != want:
                log.error("known-dangling %s: expected %s, got %s -- the check "
                          "cannot fail", name, want, got or "no finding")
                bad += 1
        for name in sorted(MUST_NOT_FIND & set(verdicts)):
            log.error("known-wired %s reported %s -- false positive",
                      name, verdicts[name])
            bad += 1
        if bad:
            return 1
        log.info("check: %d known-dangling found with the right verdict, "
                 "%d known-wired not reported", len(MUST_FIND), len(MUST_NOT_FIND))
    return 0


if __name__ == "__main__":
    sys.exit(main())
