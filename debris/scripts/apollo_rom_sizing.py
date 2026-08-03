#!/usr/bin/env python3
"""Measure cynthion_d11 application ROM usage across a candidate patch ordering.

Answers one question and nothing else: at which points in a proposed upstream
patch sequence does the SAMD11D14 application region overflow?

The d11 shares one 16 KB flash with the Saturn-V bootloader. board.mk carves off
BOOTLOADER_SIZE = 0x800, leaving 14336 B for the application. That budget is the
binding constraint on the whole Apollo patchset, so a purely textual "does it
apply" check is not sufficient -- patches can each apply cleanly and still be
jointly unshippable.

Runs entirely in a detached git worktree. It never touches the main checkout and
never touches hardware: it builds and reads the linked ELF, nothing more.

Usage:
    scripts/apollo_rom_sizing.py --worktree tmp/upstream-sizing/wt

Logs to tmp/logs/apollo_rom_sizing.log as well as the terminal.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
LOG_DIR = WORKSPACE / "tmp" / "logs"

# From firmware/src/boards/cynthion_d11/board.mk: the SAMD11D14 has 16 KB of
# flash and Saturn-V occupies the low 0x800, leaving this for the application.
APP_REGION_BYTES = 14336


# firmware/Makefile takes APOLLO_BOARD and derives BOARD from it. "cynthion"
# selects cynthion_d11 with revision autodetection, which is the shipping
# configuration and the one that is flash-starved.
APOLLO_BOARD = "cynthion"
BOARD = "cynthion_d11"


@dataclass
class Step:
    """One position in the candidate ordering."""

    label: str
    # Commits to cherry-pick, in order, to reach this step from the previous one.
    picks: list[str] = field(default_factory=list)
    # Paths to restrict the pick to; empty means the whole commit. Used to model
    # a decomposed commit (e.g. LTO's Makefile hunk without its feature hunks).
    paths: list[str] = field(default_factory=list)


# The chronological order the patches were developed in. Sizing this shows
# whether the historical sequence is shippable as-is upstream.
STEPS_CHRONOLOGICAL = [
    Step("+39a2213 jtag/uart pin lock", ["39a2213"]),
    Step("+df4a93b boot-to-dfu cmd", ["df4a93b"]),
    Step("+6cc219e vendor gating", ["6cc219e"]),
    Step("+973fa78 drop tinyusb vendor drv", ["973fa78"]),
    Step("+651b027 WCID to flash", ["651b027"]),
    Step("+da564f8 ISR races", ["da564f8"]),
    Step("+01ae228 reclaim 276 B", ["01ae228"]),
]

# LTO first. 4bf7691 bundles three things; only its Makefile hunk is the LTO
# switch, so the switch is modelled by taking that path alone. The feature hunks
# in that commit belong to the sideband series and are not part of this test.
STEPS_LTO_FIRST = [
    Step("+4bf7691 LTO switch only (Makefile)", ["4bf7691"], paths=["firmware/Makefile"]),
    Step("+973fa78 drop tinyusb vendor drv", ["973fa78"]),
    Step("+651b027 WCID to flash", ["651b027"]),
    Step("+01ae228 reclaim 276 B", ["01ae228"]),
    Step("+39a2213 jtag/uart pin lock", ["39a2213"]),
    Step("+df4a93b boot-to-dfu cmd", ["df4a93b"]),
    Step("+6cc219e vendor gating", ["6cc219e"]),
    Step("+da564f8 ISR races", ["da564f8"]),
]

# LTO first, with 01ae228 moved after the mode-lock series. The naive lto-first
# ordering above puts 01ae228 early and it conflicts: 01ae228 makes
# jtag_deinit()'s gpio_pins[] static const and gives vendor.c's handlers internal
# linkage, and 39a2213/df4a93b/6cc219e restructure exactly those regions. The
# fine-grained cleanup has to follow the structural changes, not precede them.
STEPS_LTO_FIRST_FIXED = [
    Step("+4bf7691 LTO switch only (Makefile)", ["4bf7691"], paths=["firmware/Makefile"]),
    Step("+973fa78 drop tinyusb vendor drv", ["973fa78"]),
    Step("+651b027 WCID to flash", ["651b027"]),
    Step("+39a2213 jtag/uart pin lock", ["39a2213"]),
    Step("+df4a93b boot-to-dfu cmd", ["df4a93b"]),
    Step("+6cc219e vendor gating", ["6cc219e"]),
    Step("+da564f8 ISR races", ["da564f8"]),
    Step("+01ae228 reclaim 276 B", ["01ae228"]),
]

ORDERINGS = {
    "chronological": STEPS_CHRONOLOGICAL,
    "lto-first": STEPS_LTO_FIRST,
    "lto-first-fixed": STEPS_LTO_FIRST_FIXED,
}

STEPS: list[Step] = []


def setup_logging(suffix: str = "") -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("apollo_rom_sizing")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    name = f"apollo_rom_sizing{'-' + suffix if suffix else ''}.log"
    fh = logging.FileHandler(LOG_DIR / name, mode="w")
    fh.setFormatter(fmt)
    log.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


def run(cmd: list[str], cwd: Path, log: logging.Logger, check: bool = True):
    log.debug("run: %s (cwd=%s)", " ".join(cmd), cwd)
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.debug("stdout:\n%s", proc.stdout)
        log.debug("stderr:\n%s", proc.stderr)
        if check:
            raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def build_size(worktree: Path, log: logging.Logger) -> int | None:
    """Build the d11 firmware and return application .text+.data bytes.

    Returns None if the build fails, which is itself a result: an ordering that
    does not compile is an ordering we cannot ship.
    """
    fw = worktree / "firmware"
    run(["make", f"APOLLO_BOARD={APOLLO_BOARD}", "clean"], fw, log, check=False)
    proc = run(["make", f"APOLLO_BOARD={APOLLO_BOARD}"], fw, log, check=False)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
        log.error("build failed:\n%s", tail)
        return None

    elf = fw / "_build" / BOARD / "firmware.elf"
    if not elf.exists():
        log.error("no ELF at %s", elf)
        return None

    # arm-none-eabi-size Berkeley format: text+data is what occupies flash.
    proc = run(["arm-none-eabi-size", str(elf)], fw, log)
    line = proc.stdout.strip().splitlines()[-1]
    fields = line.split()
    text, data = int(fields[0]), int(fields[1])
    return text + data


def vectors_ok(worktree: Path, log: logging.Logger) -> bool:
    """Guard the LTO dead-binary failure mode.

    Enabling LTO on this target can link cleanly and yield a 4-byte image: the
    linker script has no ENTRY() and builds its vector table from weak aliases,
    so the plugin prunes everything. A size figure alone would call that a great
    result, so any size below this floor is treated as a pruned image.
    """
    elf = worktree / "firmware" / "_build" / BOARD / "firmware.elf"
    if not elf.exists():
        return False
    proc = run(["arm-none-eabi-nm", str(elf)], worktree, log, check=False)
    syms = proc.stdout
    # Reset_Handler must exist and the image must contain the USB stack; a
    # pruned image has neither.
    for want in ("Reset_Handler", "tud_task_ext"):
        if want not in syms:
            log.error("vector guard: %s absent -- image looks pruned", want)
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worktree", required=True, help="detached worktree to build in")
    ap.add_argument("--base", default="upstream/main", help="ref to reset to first")
    ap.add_argument(
        "--ordering",
        choices=sorted(ORDERINGS),
        default="lto-first",
        help="which candidate ordering to size",
    )
    args = ap.parse_args()

    global STEPS
    STEPS = ORDERINGS[args.ordering]

    log = setup_logging(args.ordering)
    log.info("ordering under test: %s", args.ordering)
    worktree = Path(args.worktree)
    if not worktree.is_absolute():
        worktree = WORKSPACE / worktree
    if not worktree.exists():
        log.error("worktree %s does not exist; create it with git worktree add", worktree)
        return 2

    log.info("apollo d11 ROM sizing; app region %d B", APP_REGION_BYTES)
    log.info("worktree %s, base %s", worktree, args.base)

    run(["git", "reset", "--hard", args.base], worktree, log)
    run(["git", "clean", "-fdx", "firmware"], worktree, log, check=False)

    baseline = build_size(worktree, log)
    if baseline is None:
        log.error("baseline build failed -- cannot size anything")
        return 1
    pct = 100.0 * baseline / APP_REGION_BYTES
    log.info("BASELINE %s: %d B (%.2f%%)", args.base, baseline, pct)
    log.info("headroom at baseline: %d B", APP_REGION_BYTES - baseline)

    if not vectors_ok(worktree, log):
        log.error("baseline image failed the vector guard")
        return 1

    results: list[tuple[str, int | None]] = [(f"baseline {args.base}", baseline)]

    for step in STEPS:
        log.info("--- step: %s", step.label)
        for pick in step.picks:
            if step.paths:
                # Model a decomposed commit: take only the named paths from it.
                run(["git", "checkout", pick, "--"] + step.paths, worktree, log)
                run(["git", "commit", "-m", f"partial {pick}"], worktree, log, check=False)
            else:
                proc = run(
                    ["git", "cherry-pick", "-x", pick], worktree, log, check=False
                )
                if proc.returncode != 0:
                    log.error(
                        "CONFLICT cherry-picking %s at step '%s' -- this is an "
                        "ordering constraint, recording it and aborting the pick",
                        pick,
                        step.label,
                    )
                    run(["git", "cherry-pick", "--abort"], worktree, log, check=False)
                    results.append((f"{step.label} [CONFLICT on {pick}]", None))
                    break
        else:
            size = build_size(worktree, log)
            if size is not None and not vectors_ok(worktree, log):
                log.error("%s: image failed the vector guard", step.label)
                size = None
            if size is None:
                log.error("%s: BUILD FAILED", step.label)
                results.append((step.label, None))
            else:
                p = 100.0 * size / APP_REGION_BYTES
                free = APP_REGION_BYTES - size
                verdict = "OVERFLOW" if size > APP_REGION_BYTES else "fits"
                log.info(
                    "%s: %d B (%.2f%%), %d B free -- %s",
                    step.label,
                    size,
                    p,
                    free,
                    verdict,
                )
                results.append((step.label, size))

    log.info("")
    log.info("=== SUMMARY (app region %d B) ===", APP_REGION_BYTES)
    for label, size in results:
        if size is None:
            log.info("%-56s  FAILED/CONFLICT", label)
        else:
            log.info(
                "%-56s  %6d B  %6.2f%%  %5d free%s",
                label,
                size,
                100.0 * size / APP_REGION_BYTES,
                APP_REGION_BYTES - size,
                "  OVERFLOW" if size > APP_REGION_BYTES else "",
            )

    log.info("")
    log.info("done; this script sizes only -- it flashes nothing and tests no hardware")
    return 0


if __name__ == "__main__":
    sys.exit(main())
