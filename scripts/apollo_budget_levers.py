#!/usr/bin/env python3
#
# What each way of getting Apollo back under budget actually buys. See #199.
# SPDX-License-Identifier: BSD-3-Clause

"""Costs the levers available on the d11 flash and RAM budget, by building each.

The d11 is a knife-edge on both regions, and the choice between cutting
something, re-deriving a ceiling and #182's single-purpose profiles is only
arguable with numbers (#199, #404).

So each lever is built and measured rather than estimated. A clean build is
0.6 s, which makes measuring cheaper than guessing.

Every lever is reverted after it is measured, and the baseline is rebuilt last.
Nothing here is a recommendation: what a lever costs in function is a judgement,
and only the bytes are measured.

    ./scripts/apollo_budget_levers.py
    ./scripts/apollo_budget_levers.py --only oz,dfu

Results go to `tmp/logs/apollo_budget_levers.log` as well as the terminal.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import apollo_budget_check  # noqa: E402
from devlog import emit, log  # noqa: E402

APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
ELF = FIRMWARE / "_build" / "cynthion_d11" / "firmware.elf"

TUSB_CONFIG = FIRMWARE / "src/boards/cynthion_d11/tusb_config.h"
JTAG_H = FIRMWARE / "src/jtag.h"
VENDOR_C = FIRMWARE / "src/vendor.c"
MAKEFILE = FIRMWARE / "Makefile"

# A clean serial build of this firmware is 0.61 s (measured 2026-08-10, GCC
# 15.2.0, this machine). 5 s is ~8x that: loose enough that a loaded machine does
# not flake, short enough that a wedged link is named in seconds rather than
# waited on. On expiry the lever, the limit and the elapsed time are logged and
# the sweep stops -- a half-built tree must not be measured.
BUILD_LIMIT_S = 5.0

# name -> (what it costs in function, [(file, old, new)], {make var: value})
#
# `null` is the control and runs first: a comment-only edit, so it must measure
# +0/+0. Any other answer means the harness is measuring itself and every row
# below it is void. It is not optional -- see `version_string()` for the
# contamination it caught.
LEVERS = {
    "null": (
        "CONTROL -- a comment-only edit. Must be +0/+0",
        [(TUSB_CONFIG, "#ifndef _TUSB_CONFIG_H_",
          "#ifndef _TUSB_CONFIG_H_ // budget-lever control, no-op")],
        {},
    ),
    "oz": (
        "-Oz instead of -Os: GCC's size-at-all-costs level, no feature lost. "
        "Expect +0 -- the two produce a byte-identical image here, and the knob "
        "is live: -O0 overflows rom by 34160 bytes",
        [],
        {"CFLAGS_OPTIMIZED": "-Oz"},
    ),
    "dfu": (
        "drops the DFU runtime interface -- the host can no longer ask the "
        "firmware to reboot into the Saturn-V bootloader; the button still can. "
        "Expect a BUILD FAILURE: usb_descriptors.c names the DFU descriptors "
        "unconditionally, so this is not a one-define change",
        [(TUSB_CONFIG, "#define CFG_TUD_DFU_RUNTIME      1",
          "#define CFG_TUD_DFU_RUNTIME      0")],
        {},
    ),
    "bench": (
        "drops the synthetic JTAG benchmark (0xb9) -- the only instrument that "
        "measures the JTAG path with USB out of the way, and so the regression "
        "check for it. The largest flash lever there is, and the only one that "
        "clears flash; wants a build-time switch in apollo rather than deletion",
        [(VENDOR_C, "return handle_jtag_benchmark(rhport, request);",
          "return false;")],
        {},
    ),
    "altbuf": (
        "halves the second JTAG transmit buffer, 512 -> 256. Costs configure "
        "throughput: it is what makes overlapped scans possible",
        [(JTAG_H, "#define JTAG_ALT_BUFFER_SIZE 512",
          "#define JTAG_ALT_BUFFER_SIZE 256")],
        {},
    ),
    "cdcfifo": (
        "halves both CDC FIFOs, 64 -> 32. Costs console throughput and makes "
        "an overrun likelier on a busy host",
        [(TUSB_CONFIG, "#define CFG_TUD_CDC_RX_BUFSIZE   64",
          "#define CFG_TUD_CDC_RX_BUFSIZE   32"),
         (TUSB_CONFIG, "#define CFG_TUD_CDC_TX_BUFSIZE   64",
          "#define CFG_TUD_CDC_TX_BUFSIZE   32")],
        {},
    ),
    "stack": (
        "cuts the stack reservation 704 -> 640, margin over the 344-byte "
        "measurement 2.05x -> 1.86x. Spends safety margin, not slack",
        [(MAKEFILE, "STACK_SIZE=0x2C0", "STACK_SIZE=0x280")],
        {},
    ),
}


def version_string():
    """The `git describe` the Makefile would compute, with `--dirty` withheld.

    Pinned for every build in the sweep. A lever that patches a source file makes
    the submodule dirty, which appends `-dirty` to a string compiled into flash:
    the first run of this script reported `stack` -- a linker `--defsym` change
    that cannot touch flash -- as +8 bytes of it. A lever measuring its own
    bookkeeping is worse than no measurement.
    """
    described = subprocess.run(
        ["git", "describe", "--abbrev=7", "--always", "--tags"],
        cwd=APOLLO, capture_output=True, text=True)
    return described.stdout.strip() or "unknown"


def build(overrides):
    """A clean build, so a CFLAGS change is not ignored by up-to-date objects."""
    subprocess.run(["rm", "-rf", str(FIRMWARE / "_build")], check=True)
    command = ["make", "APOLLO_BOARD=cynthion", "-j8",
               f"VERSION_STRING={version_string()}"]
    command += [f"{key}={value}" for key, value in overrides.items()]
    try:
        result = subprocess.run(command, cwd=FIRMWARE, capture_output=True,
                                text=True, timeout=BUILD_LIMIT_S)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT build: killed at {BUILD_LIMIT_S}s -- {' '.join(command)}",
            "ERROR")
        return None
    if result.returncode != 0:
        log(f"build failed: {(result.stderr or result.stdout)[-400:]}", "ERROR")
        return None
    return apollo_budget_check.account(ELF)


def patch(edits):
    for path, old, new in edits:
        text = path.read_text()
        if old not in text:
            raise SystemExit(f"{path}: cannot find {old!r} -- lever is stale")
        path.write_text(text.replace(old, new))


def restore(edits):
    for path in {p for p, _, _ in edits}:
        subprocess.run(["git", "checkout", "--", str(path.relative_to(APOLLO))],
                       cwd=APOLLO, check=True)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated subset of "
                                       f"{','.join(LEVERS)}")
    args = parser.parse_args()

    chosen = list(LEVERS) if not args.only else args.only.split(",")
    unknown = [name for name in chosen if name not in LEVERS]
    if unknown:
        print(f"unknown lever(s) {unknown}; have {sorted(LEVERS)}")
        return 1
    if "null" not in chosen:
        chosen.insert(0, "null")

    base = build({})
    if base is None:
        return 1
    rom_limit = int(apollo_budget_check.ROM_CEILING * base["rom"][1])
    ram_limit = int(apollo_budget_check.RAM_CEILING * base["ram"][1])

    emit("Apollo budget levers -- each built, not estimated")
    emit()
    emit(f"  baseline  flash {base['rom_used']} of {base['rom'][1]} "
         f"(ceiling {rom_limit}, over by {base['rom_used'] - rom_limit})")
    emit(f"            RAM   {base['ram_used']} of {base['ram'][1]} "
         f"(ceiling {ram_limit}, over by {base['ram_used'] - ram_limit})")
    emit()
    emit(f"  {'lever':<10} {'flash':>7} {'d':>6}  {'RAM':>6} {'d':>6}  clears")
    emit("  " + "-" * 52)

    results = {}
    for name in chosen:
        _cost, edits, overrides = LEVERS[name]
        patch(edits)
        try:
            book = build(overrides)
        finally:
            restore(edits)
        if book is None:
            emit(f"  {name:<10} BUILD FAILED")
            continue
        results[name] = book
        rom_d = book["rom_used"] - base["rom_used"]
        ram_d = book["ram_used"] - base["ram_used"]
        if name == "null" and (rom_d or ram_d):
            emit(f"  {name:<10} {book['rom_used']:>7} {rom_d:>+6}  "
                 f"{book['ram_used']:>6} {ram_d:>+6}  CONTROL FAILED")
            emit()
            emit("The control edit changes no code and must measure zero. It "
                 "did not, so this")
            emit("harness is measuring its own bookkeeping and every other row "
                 "would be void.")
            return 1
        clears = []
        if book["rom_used"] <= rom_limit:
            clears.append("flash")
        if book["ram_used"] <= ram_limit:
            clears.append("RAM")
        emit(f"  {name:<10} {book['rom_used']:>7} {rom_d:>+6}  "
             f"{book['ram_used']:>6} {ram_d:>+6}  "
             f"{'+'.join(clears) if clears else 'neither'}")

    emit()
    for name in chosen:
        if name in results:
            emit(f"  {name}: {LEVERS[name][0]}")

    # Leave the tree holding the real firmware, not the last experiment.
    emit()
    if build({}) is None:
        return 1
    emit("  baseline rebuilt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
