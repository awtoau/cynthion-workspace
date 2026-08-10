#!/usr/bin/env python3
#
# The WHOLE boot report, across a reconfigure. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Reconfigure the FPGA with a listener already attached, and keep every byte.

    ./scripts/soc_boot_capture.py             # reconfigure, then capture
    ./scripts/soc_boot_capture.py --no-configure   # just prompt and capture

Capture -> `tmp/logs/soc-boot-capture.txt`.

## Why this exists

`soc_run.py` reads the console after a configure with a **400-byte cap**, and
the bring-up report is longer than that. A boot that stops part-way through
therefore looks identical to a boot that ran to completion: both show the same
first 400 bytes and nothing after. That is not a hypothetical -- it cost an
afternoon reading a truncated report as a hang, and then a hang as a truncation.

Two things are captured, separately, because they answer different questions:

* **unprompted** -- what the board says of its own accord after a reset. The
  banner is on demand, so this is normally empty; anything here is a panic or a
  log line that beat the shell to the port.
* **prompted** -- everything a CR draws out: the banner, the retained bring-up
  report, and the prompt. A report that ENDS PART WAY is the interesting case:
  `init::bringup` prints one line per peripheral in a fixed order, so the last
  line printed names the step before the one that hung.

Reads through `soc_shell.Link`, so it goes via the console service on port 9000
when one is running rather than fighting it for the tty.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402
import soc_shell  # noqa: E402

CAPTURE = ROOT / "tmp" / "logs" / "soc-boot-capture.txt"
# The same artifact `soc_run.py` configures, in the same per-variant directory:
# the environment selects which build this is, here as there (#351).
BITSTREAM = variant.build_dir(ROOT) / "top.bit"
APOLLO = ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"

# The order `init::bringup` runs in, so a truncated report names the step after
# the last line printed. From `firmware/cynthion-soc/src/init.rs`.
BRINGUP = ["uart", "i2c", "pac1954", "fusb302b", "hyperram", "w25q32",
           "usb3343", "vbus"]


def drain(link, seconds, poke=False):
    """Everything that arrives inside `seconds`, optionally after a CR.

    Not `read_until_prompt`: the question here is what the board says in total,
    including after a prompt, and a capture that stops at the first prompt would
    cut the report in half.
    """
    link.settle(0.05)
    if poke:
        link.write(b"\r")
    deadline = time.monotonic() + seconds
    got = b""
    while time.monotonic() < deadline:
        chunk = link.read_available()
        if chunk:
            got += chunk
    return got.decode("ascii", "replace")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-configure", action="store_true",
                        help="do not reconfigure; just prompt the running image")
    # **Waits for**: whatever the board emits unprompted after a reset.
    # **Expected**: nothing -- the banner is on demand. A panic message would
    #   appear within milliseconds of the CPU starting.
    # **Multiplier**: 3s is ~2x the ~1.5 s the tty takes to bind after a
    #   reconfigure, which is the real lower bound on being able to hear anything.
    # **On expiry**: an empty unprompted capture, which is the ordinary case.
    parser.add_argument("--quiet-window", type=float, default=3.0,
                        help="seconds to listen before poking (default 3)")
    # **Waits for**: the banner plus the retained bring-up report, ~1.5 kB at
    #   115200 = ~130 ms, plus the shell's prompt.
    # **Multiplier**: ~15x, because the interesting case is a report that stops
    #   part way and a short window could not tell that from a slow one.
    # **On expiry**: whatever arrived is printed and the bring-up step after the
    #   last line is named.
    parser.add_argument("--reply-window", type=float, default=2.0,
                        help="seconds to listen after the CR (default 2)")
    args = parser.parse_args()

    if not args.no_configure:
        if not BITSTREAM.exists():
            emit(f"no bitstream at {BITSTREAM.relative_to(ROOT)}")
            return 1
        # ATTACHED FIRST. A configure resets the CPU, and a listener opened
        # afterwards has already missed whatever the CPU said on its way up.
        link = soc_shell.Link.open(None)
        emit(f"console: {link.how}")
        emit("configuring...")
        # `expect="console"`: it must enumerate and must NOT be prompted -- this
        # script is capturing what the CPU says on its way up, and the first
        # received byte flushes the banner (#360).
        import soc_confirm

        if soc_confirm.configure_and_confirm(BITSTREAM, expect="console") != 0:
            return 1
        # The tty is destroyed and recreated by the reconfigure, so the link
        # taken before it is stale whatever it was. Reopened rather than reused.
        link.close()
        link = soc_shell.Link.open(None)
    else:
        link = soc_shell.Link.open(None)
        emit(f"console: {link.how}")

    unprompted = drain(link, args.quiet_window)
    prompted = drain(link, args.reply_window, poke=True)
    link.close()

    CAPTURE.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE.write_text(f"--- unprompted ({args.quiet_window:g} s) ---\n"
                       f"{unprompted}\n"
                       f"--- prompted ({args.reply_window:g} s) ---\n"
                       f"{prompted}\n")

    emit(f"unprompted: {len(unprompted)} bytes")
    for line in unprompted.splitlines():
        emit(f"  {line}")
    emit(f"prompted: {len(prompted)} bytes")
    for line in prompted.splitlines():
        emit(f"  {line}")

    # WHICH STEP IS MISSING. The report is ordered, so the first name that never
    # appears is the step that did not finish -- which is the whole reason for
    # capturing past 400 bytes.
    seen = [name for name in BRINGUP if name in prompted]
    missing = [name for name in BRINGUP if name not in prompted]
    emit("")
    emit(f"bring-up steps reported: {seen}")
    if missing:
        emit(f"NOT reported: {missing}")
        emit(f"  the report stops at `{seen[-1] if seen else '(nothing)'}`, so "
             f"`{missing[0]}` is where it did not return")
    else:
        emit("  every bring-up step reported")
    emit(f"capture -> {CAPTURE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
