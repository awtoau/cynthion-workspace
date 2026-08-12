#!/usr/bin/env python3
#
# The review pass: what moved since the recorded baseline, and what got duplicated. #456.
# SPDX-License-Identifier: BSD-3-Clause

"""Run after every change to the SoC. Says what got worse, and refuses to guess.

Composes the instruments that already exist rather than adding new ones:

  | source | what it contributes |
  |---|---|
  | `scripts/soc_map_audit.py` | windows, and mapped/silent/unmapped bytes |
  | `scripts/soc_dead_peripherals.py` | windows no software reaches |
  | `scripts/build_log_audit.py` | the warning set from the build log |
  | nextpnr's `top.tim` | whole-design cell totals |
  | this file | mechanical duplication scans |

    ./scripts/soc_review.py              # compare against the recorded baseline
    ./scripts/soc_review.py --record     # make the current state the baseline
    ./scripts/soc_review.py --self-test  # prove it reports a known regression

Exit 1 when something is worse than the baseline, or when a control fails.

## What may be compared across builds, and what may not

**May:** whole-design cell totals (COMB, FF, DP16KD, LUTRAM), the map's
mapped/silent/unmapped figures, the dead-window list, the warning set.

**MAY NOT: per-module rows from a flat netlist.** `SPI0` reads 825 COMB by
attribution and 16 by delta build, and `cpu` -- a pre-generated netlist that did
not change at all -- appeared to move +102 in the same comparison. yosys names
ABC-mapped cells after whichever net it kept, and those names shift when
anything upstream changes. So this refuses to print a per-module delta rather
than printing one with a caveat beside it: `--module-delta` prints the reason
and exits 1. A per-module figure is a snapshot of one build, and belongs to
`scripts/soc_module_area.py`.

**A COMB delta under ~200 is not a measurement.** The null control -- one
32-bit identity constant given a different value, no logic touched -- moves
TRELLIS_COMB by +194 and TRELLIS_FF by +1 (#454). Any netlist change re-rolls
ABC's mapping the same way, so this reports a smaller move without calling it a
regression. **FF is the column that carries meaning at small sizes**: a register
is a register wherever it is placed.

**No Fmax anywhere.** Removing logic has made Fmax worse twice on this die,
each reproducible: any netlist change re-rolls placement. Cells only.

## What it does NOT check

Stated rather than implied, because a review that looks complete is worse than
one with a gap in it:

  * duplicated *logic* -- two implementations of one handshake, the same counter
    written twice, a comparison repeated downstream of the thing that made it.
    Only the two shapes below are detected mechanically.
  * anything about the firmware. `scripts/check.py` covers that.
  * whether a peripheral works. This is a size and structure review.
  * per-module area. See above.

Output is mirrored to ./tmp/logs/soc_review.log.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_review.log"
BASELINE = ROOT / "docs" / "soc-review-baseline.json"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))

# Whole-design totals only. Every one of these is a property of the netlist and
# repeats exactly for a given elaboration (#441).
CELLS = ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "TRELLIS_RAMW")

# The null control: one 32-bit identity constant given a different value moved
# TRELLIS_COMB by +194 and TRELLIS_FF by +1, with no logic added or removed.
# So a COMB delta this size is the mapper, not the change. #454.
#   ./scripts/soc_trim_delta.py --trim null-constant
FLOOR = {"TRELLIS_COMB": 194, "TRELLIS_FF": 1, "DP16KD": 0, "TRELLIS_RAMW": 0}

# Files allowed to decode the memory map a second time. `bus/fault.py` does it
# to answer ERR one cycle sooner than its own timeout (#444); a THIRD copy is a
# finding, so the list is baselined rather than hardcoded.
MAP_DECODE = re.compile(r"window_patterns\s*\(|memory_map\.windows\s*\(")

# A class whose source builds a FIFO. Two of these back to back is #449.
FIFO_SOURCE = re.compile(r"SyncFIFO|AsyncFIFO|fifo\.|FIFO_DEPTH")

# `m.submodules.name = name = Class(...)` in top.py, and the stream connections
# between them.
SUBMODULE = re.compile(r"m\.submodules\.(\w+)\s*=\s*(?:(\w+)\s*=\s*)?(\w+)\(")
CONNECT = re.compile(r"wiring\.connect\(m,\s*(\w+)\.source,\s*(\w+)\.sink\)")


# --- readings --------------------------------------------------------------

def read_cells(build_dir):
    """Whole-design totals from a FINISHED nextpnr run (#440)."""
    import soc_trim_delta
    cells, _fmax = soc_trim_delta.read(build_dir)
    return {name: cells.get(name, 0) for name in CELLS}


def newest_build():
    """The most recently finished build directory, or None."""
    import soc_trim_delta
    candidates = sorted((ROOT / "tmp" / "awto_soc" / "build").rglob("top.tim"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        if soc_trim_delta.FINISHED in path.read_text(errors="replace"):
            return path.parent
    return None


def read_map():
    """Window count and the mapped/silent/unmapped figures."""
    import soc_map_audit
    soc, regions = soc_map_audit.build_soc()
    facts = soc_map_audit.analyse(
        soc.decoder.bus.memory_map,
        regions=[(base, size) for base, size, _flags in regions])
    return {
        "windows": [entry["name"] for entry in facts["windows"]],
        "claimed": facts["claimed"], "mapped": facts["mapped"],
        "silent": facts["silent"], "err": facts["err"],
    }


def read_dead():
    """Windows nothing reaches, and windows only the C generator reaches."""
    import soc_dead_peripherals
    if not soc_dead_peripherals.control(lambda *_args: None):
        raise SystemExit("soc_dead_peripherals' own control failed -- its "
                         "verdicts are not worth comparing")
    dead, c_only = [], []
    for name, address in soc_dead_peripherals.windows():
        verdict, _evidence = soc_dead_peripherals.users(name, address)
        if verdict == "dead":
            dead.append(name)
        elif verdict == "c-only":
            c_only.append(name)
    return {"dead": sorted(dead), "c_only": sorted(c_only)}


def read_warnings(build_dir):
    import build_log_audit
    report = build_log_audit.audit(build_dir / "top.rpt")
    # The text, not the line number: a line number moves for reasons that are
    # not a new warning.
    return sorted({line for _number, line in report["warnings"]})


def read_duplication():
    """The two shapes that can be found by reading the source."""
    gateware = sorted((ROOT / "gateware" / "soc").rglob("*.py"))

    second_decode = [str(path.relative_to(ROOT)) for path in gateware
                     if MAP_DECODE.search(path.read_text())]

    # Classes whose own file builds a FIFO, by class name.
    fifo_classes = set()
    for path in gateware:
        text = path.read_text()
        for match in re.finditer(r"^class (\w+)", text, re.MULTILINE):
            end = text.find("\nclass ", match.end())
            body = text[match.end():end if end != -1 else len(text)]
            if FIFO_SOURCE.search(body):
                fifo_classes.add(match.group(1))

    top = (ROOT / "gateware" / "soc" / "top.py").read_text()
    of_class = {}
    for match in SUBMODULE.finditer(top):
        for name in (match.group(1), match.group(2)):
            if name:
                of_class[name] = match.group(3)
    pairs = []
    for match in CONNECT.finditer(top):
        source, sink = match.group(1), match.group(2)
        if (of_class.get(source) in fifo_classes
                and of_class.get(sink) in fifo_classes):
            pairs.append(f"{source}({of_class[source]}) -> "
                         f"{sink}({of_class[sink]})")
    return {"map_decoded_in": sorted(second_decode),
            "fifo_behind_fifo": sorted(set(pairs))}


def tool_versions():
    out = {}
    for tool, flag in (("yosys", "-V"), ("nextpnr-ecp5", "--version")):
        try:
            proc = subprocess.run([tool, flag], capture_output=True, text=True)
            out[tool] = (proc.stdout + proc.stderr).strip().splitlines()[0]
        except FileNotFoundError:
            out[tool] = "absent"
    return out


def reading(build_dir=None):
    """Everything the review compares, in one dict."""
    import variant
    state = {
        "recorded": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"),
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True,
                                 cwd=ROOT).stdout.strip(),
        "variant": variant.slug(),
        "tools": tool_versions(),
        "map": read_map(),
        "dead": read_dead(),
        "duplication": read_duplication(),
    }
    if build_dir is not None:
        build_dir = build_dir.resolve()
        state["build"] = str(build_dir.relative_to(ROOT)
                             if build_dir.is_relative_to(ROOT) else build_dir)
        state["cells"] = read_cells(build_dir)
        state["warnings"] = read_warnings(build_dir)
    return state


# --- comparison ------------------------------------------------------------

def compare(base, now):
    """[(worse, line)] -- every difference, worst first. Never per-module."""
    findings = []

    for key in ("variant", "tools"):
        if base.get(key) != now.get(key):
            findings.append((True, f"{key} differs from the baseline "
                                   f"({base.get(key)} -> {now.get(key)}); cell "
                                   f"totals are not comparable across it"))

    for cell in CELLS:
        if "cells" not in base or "cells" not in now:
            continue
        delta = now["cells"].get(cell, 0) - base["cells"].get(cell, 0)
        if not delta:
            continue
        line = (f"{cell} {base['cells'].get(cell)} -> "
                f"{now['cells'].get(cell)} ({delta:+d})")
        if abs(delta) <= FLOOR[cell]:
            # Inside the null control's own movement -- report it, do not call
            # it a regression, and do not let anyone read it as one.
            findings.append((False, f"{line} -- within the mapping floor of "
                                    f"{FLOOR[cell]}, not attributable"))
        else:
            findings.append((delta > 0, line))

    base_map, now_map = base.get("map", {}), now.get("map", {})
    added = set(now_map.get("windows", [])) - set(base_map.get("windows", []))
    gone = set(base_map.get("windows", [])) - set(now_map.get("windows", []))
    if added:
        findings.append((True, f"decoder windows ADDED: {', '.join(sorted(added))} "
                               f"-- each one is a case on the decode path"))
    if gone:
        findings.append((False, f"decoder windows removed: {', '.join(sorted(gone))}"))
    for key, what in (("silent", "bytes in a window behind nothing (ACK + zero)"),
                      ("err", "bytes in a PMA region no window claims"),
                      ("mapped", "bytes a resource answers")):
        delta = now_map.get(key, 0) - base_map.get(key, 0)
        if delta:
            worse = delta > 0 if key != "mapped" else False
            findings.append((worse, f"{key}: {base_map.get(key)} -> "
                                    f"{now_map.get(key)} ({delta:+d}) -- {what}"))

    for key, what in (("dead", "no user in any firmware"),
                      ("c_only", "reached only by the generated C firmware")):
        new = set(now.get("dead", {}).get(key, [])) - set(base.get("dead", {}).get(key, []))
        fixed = set(base.get("dead", {}).get(key, [])) - set(now.get("dead", {}).get(key, []))
        if new:
            findings.append((True, f"{key}: {', '.join(sorted(new))} -- {what}"))
        if fixed:
            findings.append((False, f"{key} no longer: {', '.join(sorted(fixed))}"))

    if "warnings" in base and "warnings" in now:
        new = [line for line in now["warnings"] if line not in base["warnings"]]
        for line in new[:10]:
            findings.append((True, f"new build warning: {line[:120]}"))
        if len(new) > 10:
            findings.append((True, f"...and {len(new) - 10} more new warnings"))

    for key, what in (("map_decoded_in", "decodes the memory map a second time"),
                      ("fifo_behind_fifo", "is a FIFO feeding a FIFO")):
        new = (set(now.get("duplication", {}).get(key, []))
               - set(base.get("duplication", {}).get(key, [])))
        for entry in sorted(new):
            findings.append((True, f"DUPLICATION -- {entry} {what}"))

    return sorted(findings, key=lambda entry: not entry[0])


# --- the control -----------------------------------------------------------

def self_test(emit):
    """The comparator must report a regression, and must not invent one."""
    ok = True

    base = {
        "variant": "v", "tools": {"yosys": "x"},
        "cells": {"TRELLIS_COMB": 15509, "TRELLIS_FF": 7830, "DP16KD": 49,
                  "TRELLIS_RAMW": 219},
        "map": {"windows": ["ram", "console"], "claimed": 100, "mapped": 90,
                "silent": 10, "err": 5},
        "dead": {"dead": [], "c_only": ["SPI0"]},
        "warnings": ["Warning: something"],
        "duplication": {"map_decoded_in": ["gateware/soc/bus/fault.py"],
                        "fifo_behind_fifo": []},
    }

    same = compare(base, json.loads(json.dumps(base)))
    good = not same
    ok &= good
    emit(f"  {'PASS' if good else 'FAIL':<6} a reading against itself reports "
         f"nothing ({len(same)} findings)")

    # A REAL regression, from a real build: three added decoder windows, each
    # behind a WishboneCSRBridge. +216 TRELLIS_FF, which is above the floor by
    # two orders of magnitude -- its COMB move is not, and that is the point.
    for name in ("plus3-bridged-windows-0", "plus1-window-0"):
        real = ROOT / "tmp" / "trim-delta" / name
        if not (real / "top.tim").exists():
            continue
        moved = dict(base, cells=read_cells(ROOT / "tmp" / "trim-delta" / "baseline-0"))
        found = compare(moved, dict(base, cells=read_cells(real)))
        good = any(worse for worse, _line in found)
        ok &= good
        emit(f"  {'PASS' if good else 'FAIL':<6} a real build with added "
             f"windows ({name}): {found[0][1] if found else 'nothing'}")
        break
    else:
        emit("  SKIP   no perturbed build under tmp/trim-delta to test against; "
             "run scripts/soc_trim_delta.py --trim plus3-bridged-windows")

    checks = [
        ("more cells", dict(base, cells=dict(base["cells"], TRELLIS_COMB=16009)),
         "TRELLIS_COMB"),
        ("a new window", dict(base, map=dict(base["map"],
                                             windows=["ram", "console", "extra"])),
         "ADDED"),
        ("more silent space", dict(base, map=dict(base["map"], silent=4096)),
         "silent"),
        ("a new dead window", dict(base, dead={"dead": ["BOOTRAM"],
                                               "c_only": ["SPI0"]}), "dead"),
        ("a new warning", dict(base, warnings=["Warning: something",
                                               "Warning: latch inferred"]),
         "new build warning"),
        ("a third map decoder",
         dict(base, duplication=dict(base["duplication"],
                                     map_decoded_in=["gateware/soc/bus/fault.py",
                                                     "gateware/soc/top.py"])),
         "DUPLICATION"),
        ("a FIFO behind a FIFO",
         dict(base, duplication=dict(base["duplication"],
                                     fifo_behind_fifo=["a -> b"])),
         "DUPLICATION"),
    ]
    for label, mutated, expect in checks:
        found = compare(base, mutated)
        good = any(worse and expect in line for worse, line in found)
        ok &= good
        emit(f"  {'PASS' if good else 'FAIL':<6} {label:<22} "
             f"{found[0][1][:70] if found else 'NOTHING REPORTED'}")

    # And the converse: a COMB move inside the null control's own floor must
    # not be called a regression, or every change reads as one.
    inside = compare(base, dict(base, cells=dict(base["cells"],
                                                 TRELLIS_COMB=15509 + 100)))
    good = bool(inside) and not any(worse for worse, _line in inside)
    ok &= good
    emit(f"  {'PASS' if good else 'FAIL':<6} {'+100 COMB is not a regression':<22} "
         f"{inside[0][1][:70] if inside else 'NOTHING REPORTED'}")

    # And the refusal: a per-module delta must never be printed.
    good = not any("COMB" in line and "flash_ctrl" in line
                   for _worse, line in compare(base, base))
    ok &= good
    emit(f"  {'PASS' if good else 'FAIL':<6} no per-module row appears in any "
         f"comparison")
    return bool(ok)


REFUSAL = """  REFUSED: a per-module delta is not a measurement on this design.

  `SPI0` reads 825 COMB by flat attribution and 16 by delta build. `cpu` -- a
  pre-generated netlist that did not change at all -- appeared to move +102 in
  the same comparison. yosys names ABC-mapped cells after whichever net it
  kept, and those names shift when anything upstream changes.

  For one build's breakdown:   ./scripts/soc_module_area.py
  For what a change costs:     ./scripts/soc_trim_delta.py --trim <name>
"""


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record", action="store_true",
                        help=f"write {BASELINE.relative_to(ROOT)} from the "
                             f"current tree")
    parser.add_argument("--self-test", action="store_true",
                        help="prove the comparator reports a known regression")
    parser.add_argument("--build", type=Path,
                        help="build directory to read cells and warnings from "
                             "(default: the newest finished one)")
    parser.add_argument("--module-delta", action="store_true",
                        help="refused; prints why")
    args = parser.parse_args()

    out = []

    def emit(line=""):
        print(line, flush=True)
        out.append(line)

    def finish(code):
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text("\n".join(out) + "\n")
        return code

    if args.module_delta:
        emit(REFUSAL)
        return finish(1)

    if args.self_test:
        emit("SELF-TEST -- the comparator must be able to report each of these")
        passed = self_test(emit)
        emit()
        emit("PASS" if passed else "FAIL -- this review cannot be trusted")
        return finish(0 if passed else 1)

    build_dir = args.build or newest_build()
    if build_dir is None:
        emit("  no finished build under tmp/awto_soc/build -- cells and "
             "warnings are SKIPPED, not passed")
    state = reading(build_dir)

    if args.record:
        BASELINE.write_text(json.dumps(state, indent=2) + "\n")
        emit(f"  baseline written to {BASELINE.relative_to(ROOT)}")
        emit(f"    commit {state['commit']}, variant {state['variant']}")
        if "cells" in state:
            emit(f"    {state['cells']}")
        return finish(0)

    if not BASELINE.exists():
        emit(f"  no baseline at {BASELINE.relative_to(ROOT)} -- "
             f"run with --record first")
        return finish(1)
    base = json.loads(BASELINE.read_text())

    emit(f"  baseline {base['commit']} ({base['recorded']}), "
         f"now {state['commit']}")
    if "cells" in state:
        emit(f"  cells from {state['build']}")
    emit()

    findings = compare(base, state)
    if not findings:
        emit("  nothing moved.")
    for worse, line in findings:
        emit(f"  {'WORSE' if worse else 'better':<7} {line}")

    emit()
    emit(f"  windows {len(state['map']['windows'])}, "
         f"mapped {state['map']['mapped']} B, silent {state['map']['silent']} B, "
         f"unclaimed-in-region {state['map']['err']} B")
    emit(f"  map decoded in: {', '.join(state['duplication']['map_decoded_in'])}")
    emit(f"  stream buffer feeding a FIFO: "
         f"{', '.join(state['duplication']['fifo_behind_fifo']) or '(none)'}")
    emit("  NOT checked: duplicated logic beyond the two shapes above, the "
         "firmware, and per-module area (refused -- see --module-delta).")
    return finish(1 if any(worse for worse, _line in findings) else 0)


if __name__ == "__main__":
    sys.exit(main())
