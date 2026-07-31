#!/usr/bin/env python3.15t
"""Measure whether quad-SPI boot mode speeds up ECP5 configuration from flash.

Builds one design several ways -- varying ecppack's --spimode (lane count) and
--freq (configuration clock) -- writes each to the Cynthion's SPI configuration
flash, and times how long the FPGA takes to configure from it.

Timing method
-------------
Start of configuration is a PROGRAMN pulse, issued by Apollo's
REQUEST_RECONFIGURE.  End is the FPGA's DONE pin going high, read through
Apollo vendor request 0xc4 (GET_FPGA_STATUS_PINS), which returns
bit 0 = DONE, bit 1 = INITN.  That request costs ~0.21 ms per round trip, so
the sampling resolution is far finer than the tens of milliseconds a lane-count
change is expected to move.  USB enumeration is deliberately NOT the endpoint:
host-side enumeration latency is hundreds of ms and varies by more than the
effect being measured.

KNOWN BLOCKER (2026-07-29)
--------------------------
On the r1.4 board tested, configuration-from-flash does not complete, so the
timing half of this script cannot produce numbers.  INITN (FPGA ball T9) has a
5.1 kOhm pull-DOWN to GND (R102) and no pull-up anywhere on the board; the
ECP5's INITN is open-drain and must be high for configuration to proceed.  The
only thing that drives it high is Apollo's permit_fpga_configuration(true),
which the firmware calls exclusively at MCU startup (main.c:74/81).  Once
force_fpga_offline() has run, no host-reachable vendor request re-permits
configuration -- REQUEST_RECONFIGURE pulses PROGRAMN but never re-drives INITN.
The ECP5 status register confirms the diagnosis: Fail flag set with BSE error
code 0 (no CRC, preamble or ID error), i.e. configuration was attempted and
abandoned rather than the bitstream being rejected.

Consequently `measure` currently reports a timeout for every variant.  Fixing
it needs one of:
  * a physical power cycle / RESET button between variants (Apollo's startup
    path then runs and drives INITN high), or
  * an Apollo firmware change calling permit_fpga_configuration(true) inside
    the REQUEST_RECONFIGURE handler.
`build` and `verify` are unaffected and work today.

Usage
-----
    ./scripts/qspi_boot_time.py build     # build all variants
    ./scripts/qspi_boot_time.py verify    # confirm --spimode reached ecppack
    ./scripts/qspi_boot_time.py measure   # flash + time each (needs the fix above)
    ./scripts/qspi_boot_time.py all
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
BUILD_ROOT = ROOT / "tmp" / "qspiboot"
APOLLO = ROOT / "repos" / "apollo"

OSS_CAD = Path("/home/dan/opt/oss-cad-suite")

# Apollo vendor request returning FPGA configuration pins:
#   bit 0 = DONE, bit 1 = INITN   (firmware/src/vendor.c, VENDOR_REQUEST_GET_FPGA_STATUS_PINS)
REQ_FPGA_STATUS_PINS = 0xC4

# How long to wait for DONE before calling a boot failed.  A 288 KiB bitstream
# at the slowest combination here (single lane, 2.4 MHz) needs well under 1 s;
# 3 s is generous enough that a timeout means "did not configure", not "was
# still going".
BOOT_TIMEOUT_S = 3.0

# Repeats per variant.  Boot time is deterministic hardware behaviour, so this
# is about catching outliers and quantifying jitter, not averaging noise away.
REPEATS = 5

# The matrix.  --spimode selects lane count; --freq sets the MCLK configuration
# clock in MHz.
#
# ecppack accepts exactly four --freq values -- 2.4, 19.4, 38.8 and 62.0 --
# measured by trying the whole documented MCLK table against it; everything
# else is rejected with "bad frequency option".  62.0 is also where Lattice's
# own MCLK frequency table stops (FPGA-TN-02039), so ecppack will not let you
# ask for an out-of-spec configuration clock in the first place.
VARIANTS = [
    # (label,             spimode,      freq MHz)
    ("baseline-38.8",     None,         "38.8"),   # what Cynthion ships today
    ("fast-read-38.8",    "fast-read",  "38.8"),
    ("dual-spi-38.8",     "dual-spi",   "38.8"),
    ("qspi-38.8",         "qspi",       "38.8"),

    # Clock sweep at single lane, to separate "lane count" from "clock rate".
    ("baseline-2.4",      None,         "2.4"),
    ("baseline-19.4",     None,         "19.4"),
    ("baseline-62.0",     None,         "62.0"),

    # Quad at the same clocks, so each pair differs only in lane count.
    ("qspi-2.4",          "qspi",       "2.4"),
    ("qspi-19.4",         "qspi",       "19.4"),
    ("qspi-62.0",         "qspi",       "62.0"),
]


def setup_logging(name="qspi_boot_time"):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"
    fmt = "%(asctime)s %(levelname)-7s %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S%z"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(sh)
    logging.info("logging to %s", log_path)
    return log_path


def ecppack_opts_for(spimode, freq):
    opts = ["--compress", "--freq", freq]
    if spimode:
        opts += ["--spimode", spimode]
    return " ".join(opts)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build_variant(label, spimode, freq):
    """Build the test design with the given ecppack options.

    Runs in a subprocess because the Amaranth build mutates process-wide state
    (PATH, cwd) and one failed variant should not poison the rest.
    """
    outdir = BUILD_ROOT / label
    script = BUILD_ROOT / f"_build_{label}.py"
    opts = ecppack_opts_for(spimode, freq)
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(BUILD_SCRIPT_TEMPLATE.format(
        opts=opts, outdir=str(outdir), ecp5=str(ROOT / "ecp5-test")))

    env = dict(os.environ)
    env["PATH"] = f"{OSS_CAD}/bin:{OSS_CAD}/py3bin:" + env["PATH"]
    # oss-cad-suite's environment sets PYTHONHOME, which hijacks the interpreter.
    env.pop("PYTHONHOME", None)

    logging.info("[%s] building with: ecppack %s", label, opts)
    t0 = time.perf_counter()
    r = subprocess.run(
        ["/home/dan/opt/cpython-315t/bin/python3.15t", str(script)],
        capture_output=True, text=True, env=env)
    dt = time.perf_counter() - t0
    if r.returncode != 0:
        logging.error("[%s] build FAILED in %.1f s", label, dt)
        logging.error("[%s] stderr tail:\n%s", label, r.stderr[-2000:])
        return None
    bit = outdir / "top.bit"
    if not bit.exists():
        logging.error("[%s] build reported success but %s is missing", label, bit)
        return None
    logging.info("[%s] built in %.1f s -> %s (%d bytes)",
                 label, dt, bit, bit.stat().st_size)
    return bit


BUILD_SCRIPT_TEMPLATE = '''\
import os, sys
sys.path.insert(0, {ecp5!r})
from amaranth import Elaboratable, Module, Signal
from amaranth.build.plat import TemplatedPlatform
from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4


class Blink(Elaboratable):
    """Minimal design: drives the six LEDs from a counter.

    Deliberately small and dependency-free -- the point is to hold the design
    constant across variants so the only thing changing is how ecppack packs it.
    """
    def elaborate(self, platform):
        m = Module()
        ctr = Signal(26)
        m.d.sync += ctr.eq(ctr + 1)
        for i in range(6):
            led = platform.request("led", i)
            m.d.comb += led.o.eq(ctr[20 + (i % 6)])
        return m


class Plat(CynthionPlatformRev1D4):
    """CynthionPlatform.toolchain_prepare hardcodes
        overrides = {{'ecppack_opts': '--compress --freq 38.8'}}
    and passes it as **overrides alongside **kwargs, so supplying
    ecppack_opts= to build() raises TypeError (multiple values).  Bypass that
    method entirely and call the generic implementation instead.
    """
    def toolchain_prepare(self, fragment, name, **kwargs):
        kwargs["ecppack_opts"] = {opts!r}
        return TemplatedPlatform.toolchain_prepare(self, fragment, name, **kwargs)


Plat().build(Blink(), do_program=False, build_dir={outdir!r})
'''


def cmd_build(args):
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for label, spimode, freq in VARIANTS:
        if build_variant(label, spimode, freq):
            ok += 1
        else:
            fail += 1
    logging.info("build complete: %d ok, %d failed", ok, fail)
    return 0 if fail == 0 else 1


# --------------------------------------------------------------------------
# Verify the option actually reached ecppack
# --------------------------------------------------------------------------

def cmd_verify(args):
    """Confirm --spimode reached the ecppack command line AND changed the bits.

    Checking build_top.sh alone would only prove Amaranth templated the option
    in; comparing bitstreams proves ecppack acted on it.
    """
    base_bit = None
    problems = 0
    for label, spimode, freq in VARIANTS:
        outdir = BUILD_ROOT / label
        sh = outdir / "build_top.sh"
        bit = outdir / "top.bit"
        if not sh.exists():
            logging.warning("[%s] no build_top.sh -- not built yet", label)
            problems += 1
            continue
        # The invocation line, not the ': ${ECPPACK:=ecppack}' default line.
        line = [l for l in sh.read_text().splitlines()
                if '"$ECPPACK"' in l]
        cmd = line[0].strip() if line else "<no ecppack invocation line>"
        want = f"--spimode {spimode}" if spimode else None
        got_mode = "--spimode" in cmd
        if spimode and want not in cmd:
            logging.error("[%s] expected %r in ecppack line, got: %s", label, want, cmd)
            problems += 1
        elif not spimode and got_mode:
            logging.error("[%s] expected no --spimode, got: %s", label, cmd)
            problems += 1
        else:
            logging.info("[%s] ecppack: %s", label, cmd)
        if f"--freq {freq}" not in cmd:
            logging.error("[%s] expected --freq %s in: %s", label, freq, cmd)
            problems += 1

        if bit.exists():
            data = bit.read_bytes()
            if label == "baseline-38.8":
                base_bit = data
            elif base_bit is not None and spimode:
                if data == base_bit:
                    logging.error("[%s] bitstream identical to baseline -- "
                                  "--spimode had no effect on the output!", label)
                    problems += 1
                else:
                    logging.info("[%s] bitstream differs from baseline "
                                 "(%d vs %d bytes) -- option took effect",
                                 label, len(data), len(base_bit))
    if problems:
        logging.error("verify found %d problem(s)", problems)
    else:
        logging.info("verify: all variants carry the intended ecppack options")
    return 1 if problems else 0


# --------------------------------------------------------------------------
# Measure
# --------------------------------------------------------------------------

def _apollo():
    if str(APOLLO) not in sys.path:
        sys.path.insert(0, str(APOLLO))
    from apollo_fpga import ApolloDebugger
    return ApolloDebugger


def read_status_pins(dev):
    """Returns (done, initn) from Apollo vendor request 0xc4."""
    r = dev.device.ctrl_transfer(0xC0, REQ_FPGA_STATUS_PINS, 0, 0, 1, timeout=500)
    return r[0] & 1, (r[0] >> 1) & 1


def flash_and_verify(dev, bitstream):
    """Write the bitstream to configuration flash and read it back.

    Verifying every write is not optional here: the whole experiment is about
    booting from flash, and an unverified write would turn a bad flash into a
    bogus timing result.
    """
    with dev.jtag as jtag:
        prog = dev.create_jtag_programmer(jtag)
        prog.flash(bitstream, offset=0)
        readback = bytes(prog.read_flash(len(bitstream), offset=0))
    return readback == bitstream


def time_one_boot(dev):
    """Pulse PROGRAMN, then poll DONE.  Returns milliseconds, or None on timeout."""
    dev.soft_reset()                      # REQUEST_RECONFIGURE -> PROGRAMN pulse
    t0 = time.perf_counter()
    polls = 0
    while time.perf_counter() - t0 < BOOT_TIMEOUT_S:
        polls += 1
        try:
            done, _ = read_status_pins(dev)
        except Exception:
            # The FPGA may seize the shared USB port as it comes alive; that is
            # itself evidence it configured, but it costs us the endpoint.
            return None, polls
        if done:
            return (time.perf_counter() - t0) * 1e3, polls
    return None, polls


def cmd_measure(args):
    ApolloDebugger = _apollo()
    from apollo_fpga import DebuggerNotFound
    try:
        ApolloDebugger()
    except DebuggerNotFound:
        logging.error(
            "No Apollo debugger found.  If the board enumerates as "
            "'Cynthion Bootloader' it is sitting in the Saturn-V DFU "
            "bootloader: unplug and replug it.  Saturn-V jumps to the "
            "application only on a power-on reset, so a USB-level reset will "
            "not bring it back.")
        return 1
    results = []
    for label, spimode, freq in VARIANTS:
        bit = BUILD_ROOT / label / "top.bit"
        if not bit.exists():
            logging.warning("[%s] not built, skipping", label)
            continue
        data = bit.read_bytes()

        dev = ApolloDebugger()
        done, initn = read_status_pins(dev)
        logging.info("[%s] pre-flash state: DONE=%d INITN=%d", label, done, initn)
        if initn == 0:
            logging.warning(
                "[%s] INITN is low.  The board pulls INITN down through R102 "
                "(5.1k to GND) with no pull-up, and only Apollo's startup path "
                "drives it high, so configuration from flash cannot complete. "
                "See the KNOWN BLOCKER note at the top of this file.", label)

        logging.info("[%s] writing %d bytes to configuration flash", label, len(data))
        if not flash_and_verify(dev, data):
            logging.error("[%s] flash verify MISMATCH -- refusing to time this "
                          "variant", label)
            continue
        logging.info("[%s] flash verified byte-exact", label)

        times = []
        for i in range(REPEATS):
            dev = ApolloDebugger()
            ms, polls = time_one_boot(dev)
            if ms is None:
                logging.warning("[%s] run %d: DONE never asserted within %.1f s "
                                "(%d polls)", label, i + 1, BOOT_TIMEOUT_S, polls)
            else:
                logging.info("[%s] run %d: %.2f ms (%d polls)", label, i + 1, ms, polls)
                times.append(ms)
        if times:
            results.append((label, spimode, freq, min(times),
                            sum(times) / len(times), max(times), len(times)))
        else:
            results.append((label, spimode, freq, None, None, None, 0))

    logging.info("")
    logging.info("%-18s %-10s %-6s %9s %9s %9s %5s",
                 "variant", "spimode", "freq", "min ms", "mean ms", "max ms", "n")
    for label, spimode, freq, lo, mean, hi, n in results:
        if n:
            logging.info("%-18s %-10s %-6s %9.2f %9.2f %9.2f %5d",
                         label, spimode or "-", freq, lo, mean, hi, n)
        else:
            logging.info("%-18s %-10s %-6s %9s %9s %9s %5d",
                         label, spimode or "-", freq, "TIMEOUT", "-", "-", 0)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["build", "verify", "measure", "all"])
    args = ap.parse_args()
    setup_logging()
    logging.info("command: %s", args.command)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "measure":
        return cmd_measure(args)
    rc = cmd_build(args)
    rc |= cmd_verify(args)
    rc |= cmd_measure(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
