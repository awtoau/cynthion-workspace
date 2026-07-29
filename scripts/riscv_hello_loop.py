#!/usr/bin/env python3
#
# Build, flash and run the RISC-V hello-world, then read its console.
# SPDX-License-Identifier: BSD-3-Clause

"""
The edit-build-run loop for the block-RAM SoC of issue #91.

Firmware lives in block RAM, which is initialised from the bitstream, so
changing a line of C means rebuilding the bitstream. That is the loop this
automates: compile, synthesise, write to flash at full speed, reconfigure, and
read what the CPU says.

Flash rather than volatile JTAG configuration, because the result survives a
power cycle -- the board comes back running the same firmware instead of
whatever was there before. `flash-fast` switches the Apollo-to-ECP5 link into
SPI mode and writes the flash through that, rather than bit-banging the image
down the much slower JTAG path.

Reconfiguration is triggered by Apollo rather than by the design asserting its
own PROGRAMN pin. A design that self-programs on reset cannot be recovered by
loading a different bitstream: it would reconfigure out from under whatever was
just loaded.

    ./scripts/riscv_hello_loop.py                # build, flash, run, read
    ./scripts/riscv_hello_loop.py --read-only    # just read the console
    ./scripts/riscv_hello_loop.py --skip-build   # flash what is already built
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Re-exec under the real interpreter if the oss-cad-suite environment has been
# sourced. That environment exports PYTHONHOME pointing at its own bundled
# Python, which has no usb1 and a partial standard library -- so this script
# would fail to read the console and report it as a silent CPU. Yosys still
# needs that environment, so the fix is to escape it here rather than ask the
# caller not to source it.
_REAL_PYTHON = "/home/dan/opt/cpython-315t/bin/python3.15t"
if os.environ.get("PYTHONHOME") and sys.executable != _REAL_PYTHON:
    _clean = {k: v for k, v in os.environ.items()
              if k not in ("PYTHONHOME", "PYTHONPATH")}
    os.execve(_REAL_PYTHON, [_REAL_PYTHON, *sys.argv], _clean)

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "tmp" / "riscv_hello" / "build" / "top.bit"
LOG = ROOT / "tmp" / "logs" / "riscv_hello_loop.log"
APOLLO = ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"

# Must match the descriptor in ecp5-test/riscv/hello_soc.py. 000e is one of the
# IDs 54-cynthion.rules already grants uaccess to; an unlisted PID enumerates
# but cannot be opened without root, which looks exactly like a dead CPU.
VID, PID = 0x1209, 0x000e

# An absolute path, not the bare name. Sourcing the oss-cad-suite environment
# puts its own bundled Python first on PATH and exports PYTHONHOME to point at
# it, so a subprocess launched as "python3.15t" gets an interpreter with no
# standard library -- it fails on `import __future__`, which looks like a
# broken Apollo rather than a hijacked interpreter.
PYTHON = "/home/dan/opt/cpython-315t/bin/python3.15t"


def clean_env():
    """The environment minus the oss-cad-suite Python overrides.

    Yosys and nextpnr need that environment; the Apollo CLI needs its own
    interpreter's standard library. Stripping PYTHONHOME/PYTHONPATH lets both
    run from one shell.
    """
    import os
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    return env


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def run(command, handle, label):
    """Run a step, streaming failure output rather than swallowing it."""
    started = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            env=clean_env())
    elapsed = time.perf_counter() - started

    if result.returncode != 0:
        emit(handle, f"  {label}: failed after {elapsed:.1f}s")
        for line in (result.stderr or result.stdout).strip().splitlines()[-12:]:
            emit(handle, f"      {line}")
        return False

    emit(handle, f"  {label}: {elapsed:.1f}s")
    return True


def read_console(handle, seconds):
    """Read the CPU's console endpoint.

    Returns the bytes read. An empty result is a real answer -- it means the
    USB stack is alive (the device enumerated) but the CPU is not writing,
    which points at the core or its memory rather than at USB.
    """
    try:
        import usb1
    except ImportError:
        emit(handle, "  python usb1 not available; cannot read the console")
        return b""

    collected = b""
    with usb1.USBContext() as context:
        # Enumeration is not instant after reconfiguration: the FPGA has to
        # configure, the PLL lock and the host complete its own enumeration.
        # Poll rather than assume, so a slow appearance is not misreported as
        # a dead CPU.
        device = None
        appear_by = time.monotonic() + 5.0
        while device is None and time.monotonic() < appear_by:
            device = context.openByVendorIDAndProductID(VID, PID)

        if device is None:
            emit(handle, f"  no {VID:04x}:{PID:04x} device -- the FPGA did not "
                         f"enumerate")
            return b""

        speed = {1: "low", 2: "full", 3: "high", 4: "super"}.get(
            device.getDevice().getDeviceSpeed(), "unknown")
        emit(handle, f"  enumerated at {speed} speed")

        device.claimInterface(0)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                # 200 ms per read: long enough to catch a packet, short enough
                # that the loop notices the deadline promptly.
                collected += device.bulkRead(0x81, 512, timeout=200)
            except usb1.USBErrorTimeout:
                continue
            except usb1.USBError as error:
                emit(handle, f"  read stopped: {error}")
                break

    return collected


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--read-only", action="store_true",
                        help="read the console without flashing anything")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="how long to read the console for")
    parser.add_argument("--target", default="hello",
                        help="firmware target for riscv_firmware.py")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)

    with LOG.open("w") as handle:
        if not args.read_only:
            if not args.skip_build:
                emit(handle, "building")
                if not run([sys.executable,
                            str(ROOT / "scripts" / "riscv_firmware.py"),
                            "--target", args.target], handle, "firmware"):
                    return 1
                if not run([PYTHON,
                            str(ROOT / "ecp5-test" / "riscv" / "hello_soc.py"),
                            "--build"], handle, "gateware"):
                    return 1

            if not BUILD.exists():
                emit(handle, f"no bitstream at {BUILD}")
                return 1

            emit(handle)
            emit(handle, f"flashing {BUILD.stat().st_size // 1024} KiB")
            # `flash-fast` on this Apollo; newer builds spell it
            # `flash --fast`. It puts the Apollo-to-ECP5 interface into SPI
            # mode and writes the flash over that, instead of bit-banging the
            # image through JTAG.
            if not run([PYTHON, str(APOLLO), "flash-fast", str(BUILD)],
                       handle, "flash-fast"):
                emit(handle)
                emit(handle, "Falling back to volatile configuration. The "
                             "design will run but")
                emit(handle, "will not survive a power cycle.")
                emit(handle)
                emit(handle, "flash-fast loads its own bridge bitstream over "
                             "JTAG, replacing this")
                emit(handle, "design, then waits for it to enumerate as "
                             "1209:000f. On r1.4 the")
                emit(handle, "bridge uses control_phy -- the CONTROL port -- "
                             "not the TARGET port")
                emit(handle, "this console runs on. It cannot appear unless a "
                             "host cable is in")
                emit(handle, "CONTROL.")
                if not run([PYTHON, str(APOLLO), "configure", str(BUILD)],
                           handle, "configure"):
                    return 1

        emit(handle)
        emit(handle, f"reading the console for {args.seconds:.0f}s")
        output = read_console(handle, args.seconds)

        emit(handle)
        if output:
            emit(handle, f"{len(output)} bytes from the CPU:")
            emit(handle, "-" * 60)
            emit(handle, output.decode("ascii", "replace").rstrip())
            emit(handle, "-" * 60)
        else:
            emit(handle, "no output.")
            emit(handle, "The device enumerating without producing bytes means "
                         "USB is alive")
            emit(handle, "and the CPU is not writing -- look at the core, the "
                         "reset vector,")
            emit(handle, "or the firmware image in block RAM, not at USB.")

        emit(handle)
        emit(handle, f"log: {LOG}")

    return 0 if output else 2


if __name__ == "__main__":
    sys.exit(main())
