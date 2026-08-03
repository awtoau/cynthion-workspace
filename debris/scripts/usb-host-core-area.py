#!/usr/bin/env python3
"""Measure the fabric cost of a *bare* GUH host core on Cynthion r1.4.

Why this exists
---------------
`scripts/usb-host-area.py` measures GUH's finished examples -- `msc_host` at
4103 LUT is a transaction engine *plus* a mass-storage class engine plus a
hexdump plus a UART. `docs/usb-host-proposal.md` had to guess at how much of
that is the host itself.

The design in `docs/usb-host-proposal.md` section 11 does not want a class
engine: it wants `USBSIE` (and optionally `USBHostEnumerator`) behind a CPU
register interface, so firmware issues the transfers. So the number that
matters for the SoC budget is the cost of those two blocks alone, and that is
what this script measures.

Method
------
Each configuration is wrapped in a top level that keeps synthesis from
optimising the core away, without adding meaningful area of its own:

  * every input of `USBSIEInterface` is driven from a free-running LFSR, so no
    input is a constant and no state machine is unreachable;
  * every output is XOR-reduced onto one LED pin, so no output is dangling.

Clocking is `VariableClockDomainGenerator(sync_mhz=60)` -- the same generator
the SoC uses (`ecp5-test/riscv/vexii_hello_soc.py`), not LUNA's -- so the
`sync`/`usb` split and the 60 MHz constraint match the design this would be
integrated into. The CRG's own cost is measured separately as a baseline and
subtracted, so the reported delta is the host core.

No hardware is touched: synthesis and place-and-route only, nothing is
programmed. Progress and the full toolchain output land in
`tmp/logs/usb-host-core-area.log`.

Usage
-----
    scripts/usb-host-core-area.py
    scripts/usb-host-core-area.py --configs baseline sie
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AEST = timezone(timedelta(hours=10))

WORKSPACE = Path(__file__).resolve().parent.parent
LOG_DIR = WORKSPACE / "tmp" / "logs"
LOG_PATH = LOG_DIR / "usb-host-core-area.log"
BUILD_ROOT = WORKSPACE / "tmp" / "host-core-area"
GUH_DIR = WORKSPACE / "tmp" / "host-research" / "guh"

log = logging.getLogger("usb-host-core-area")


def setup_logging(verbose: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(handler)


def now() -> str:
    return datetime.now(AEST).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# The designs under measurement
# --------------------------------------------------------------------------

def build_top(config: str):
    """Return an Elaboratable for one configuration.

    Imported lazily so `--help` works without amaranth/luna/guh present.
    """
    from amaranth import Module, Signal, Cat, ClockDomain, ClockSignal
    from amaranth.hdl import Elaboratable
    from apollo_fpga.gateware.variable_clock import VariableClockDomainGenerator

    class Top(Elaboratable):
        def elaborate(self, platform):
            m = Module()
            m.submodules.car = VariableClockDomainGenerator(sync_mhz=60)

            led = platform.request("led", 0)
            observed = Signal()

            if config == "baseline":
                # Clock generator plus the observation plumbing only. Every
                # other configuration is reported as a delta against this, so
                # the CRG and the LED are not charged to the host core.
                counter = Signal(24)
                m.d.usb += counter.eq(counter + 1)
                m.d.comb += observed.eq(counter[-1])
                m.d.comb += led.o.eq(observed)
                return m

            from guh.usbh.sie import USBSIE
            from guh.usbh.enumerator import USBHostEnumerator
            from guh.usbh.descriptor import USBDescriptorParser, EndpointFilter
            from guh.protocol.descriptors import (
                EndpointTransferType, InterfaceClass,
            )

            ulpi = platform.request("target_phy")

            # HS bulk packets are 512 bytes, and the SIE's TX FIFO has to hold
            # a whole preloaded packet, so 512 is the size any real integration
            # would use. 64 (the GUH default) would flatter the result.
            fifo_depth = 512

            if config == "sie":
                core = USBSIE(bus=ulpi, fifo_depth=fifo_depth)
                m.submodules.core = core
                ctrl = core.ctrl
            elif config == "enumerator":
                core = USBHostEnumerator(
                    bus=ulpi,
                    fifo_depth=fifo_depth,
                    parser=USBDescriptorParser(
                        endpoint_filter=EndpointFilter.IN_AND_OUT,
                        transfer_type=EndpointTransferType.BULK,
                        interface_class=InterfaceClass.MASS_STORAGE,
                    ),
                )
                m.submodules.core = core
                ctrl = core.ctrl
            else:
                raise ValueError(f"unknown config {config!r}")

            # Stimulus: a free-running LFSR, so nothing the core does is
            # reachable-only-from-a-constant and nothing gets folded away.
            lfsr = Signal(32, init=1)
            m.d.usb += lfsr.eq(Cat(lfsr[1:], lfsr[0] ^ lfsr[10] ^ lfsr[30] ^ lfsr[31]))

            m.d.comb += [
                ctrl.bus_reset.eq(lfsr[0]),
                ctrl.xfer.start.eq(lfsr[1]),
                ctrl.xfer.type.eq(lfsr[2:4]),
                ctrl.xfer.dev_addr.eq(lfsr[4:11]),
                ctrl.xfer.ep_addr.eq(lfsr[11:15]),
                ctrl.xfer.data_pid.eq(lfsr[15]),
                ctrl.txs.valid.eq(lfsr[16]),
                ctrl.txs.payload.eq(lfsr[17:25]),
                ctrl.rxs.ready.eq(lfsr[25]),
            ]

            # Sink: every status bit and every received byte folded onto one
            # pin, so no output is dangling.
            sink = Signal(32)
            outs = Cat(ctrl.status.as_value(), ctrl.txs.ready,
                       ctrl.rxs.valid, ctrl.rxs.payload)
            if config == "enumerator":
                outs = Cat(outs, core.status.as_value())
            m.d.usb += sink.eq(sink ^ outs)
            m.d.comb += [
                observed.eq(sink.xor()),
                led.o.eq(observed),
            ]
            return m

    return Top()


# --------------------------------------------------------------------------
# Report parsing -- same shapes nextpnr prints for scripts/usb-host-area.py
# --------------------------------------------------------------------------

import re  # noqa: E402  (kept next to the patterns it serves)

_UTIL_RE = re.compile(
    r"^\s*Info:\s+(?P<cell>[A-Z0-9_]+):\s+(?P<used>\d+)\s*/\s*(?P<total>\d+)\s+(?P<pct>\d+)%",
    re.MULTILINE,
)

_CELLS_OF_INTEREST = (
    "TRELLIS_COMB", "TRELLIS_FF", "TRELLIS_RAMW", "DP16KD", "TRELLIS_IO",
)

_FMAX_RE = re.compile(
    r"Max frequency for clock\s+'(?P<clock>[^']+)':\s+(?P<fmax>[\d.]+)\s+MHz"
    r"\s+\((?P<verdict>PASS|FAIL) at (?P<target>[\d.]+) MHz\)"
)


def parse_utilisation(text: str) -> dict:
    found = {}
    for match in _UTIL_RE.finditer(text):
        cell = match.group("cell")
        if cell in _CELLS_OF_INTEREST:
            found[cell] = int(match.group("used"))
    return found


def parse_timing(text: str) -> dict:
    clocks = {}
    for match in _FMAX_RE.finditer(text):
        clocks[match.group("clock")] = {
            "fmax_mhz": float(match.group("fmax")),
            "target_mhz": float(match.group("target")),
            "verdict": match.group("verdict"),
        }
    return clocks


def measure(config: str) -> dict:
    from cynthion_platform import CynthionPlatformRev1D4

    log.info("[%s] building for Cynthion r1.4 ...", config)
    build_dir = BUILD_ROOT / config
    build_dir.mkdir(parents=True, exist_ok=True)

    platform = CynthionPlatformRev1D4()
    try:
        platform.build(
            build_top(config),
            do_program=False,
            build_dir=str(build_dir),
        )
    except Exception as exc:  # noqa: BLE001 -- report, do not abort the sweep
        log.warning("[%s] build failed: %s", config, exc)
        return {"config": config, "ok": False, "error": str(exc)}

    report = build_dir / "top.tim"
    text = report.read_text(errors="replace") if report.is_file() else ""
    util = parse_utilisation(text)
    result = {
        "config": config,
        "ok": bool(util),
        "utilisation": util,
        "timing": parse_timing(text),
    }
    if util:
        log.info("[%s] LUT %s  FF %s  BRAM %s  LUTRAM %s",
                 config, util.get("TRELLIS_COMB"), util.get("TRELLIS_FF"),
                 util.get("DP16KD"), util.get("TRELLIS_RAMW"))
        for clock, t in result["timing"].items():
            log.info("[%s]   %-34s %6.2f MHz vs %.2f -- %s",
                     config, clock, t["fmax_mhz"], t["target_mhz"], t["verdict"])
    else:
        result["error"] = "no utilisation table in top.tim"
        log.warning("[%s] no utilisation table; see %s", config, LOG_PATH)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", nargs="*",
                        default=["baseline", "sie", "enumerator"])
    parser.add_argument("--guh-dir", default=str(GUH_DIR))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    log.info("usb-host-core-area starting at %s", now())
    log.info("log file: %s", LOG_PATH)

    guh_dir = Path(args.guh_dir).resolve()
    if not guh_dir.is_dir():
        log.error("GUH checkout not found: %s", guh_dir)
        log.error("clone it with: git clone https://github.com/apfaudio/guh")
        return 2

    # GUH is not installed; it is a checkout. The vendored platform lives in
    # ecp5-test/ and is likewise not on the default path.
    sys.path.insert(0, str(guh_dir))
    sys.path.insert(0, str(WORKSPACE / "ecp5-test"))

    results = [measure(c) for c in args.configs]

    log.info("")
    log.info("=== Bare host core area (LFE5U-12F: 24288 LUT / 56 BRAM) ===")
    base = next((r for r in results
                 if r["config"] == "baseline" and r["ok"]), None)
    for r in results:
        if not r["ok"]:
            log.info("  %-12s FAILED: %s", r["config"], r.get("error", "?"))
            continue
        lut = r["utilisation"].get("TRELLIS_COMB", 0)
        bram = r["utilisation"].get("DP16KD", 0)
        if base and r is not base:
            d_lut = lut - base["utilisation"].get("TRELLIS_COMB", 0)
            d_bram = bram - base["utilisation"].get("DP16KD", 0)
            log.info("  %-12s %6d LUT  %3d BRAM   (delta %+d LUT, %+d BRAM)",
                     r["config"], lut, bram, d_lut, d_bram)
        else:
            log.info("  %-12s %6d LUT  %3d BRAM", r["config"], lut, bram)

    out = BUILD_ROOT / "core-area-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated": now(), "results": results}, indent=2))
    log.info("")
    log.info("results written to %s", out)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
