#!/usr/bin/env python3
#
# Verify that upstream amaranth-soc replaces luna_soc's vendored copy cleanly.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves the de-vendoring by elaborating real designs to a netlist.

Background: ``luna_soc`` does not depend on ``amaranth_soc``, it **vendors** a
fork of it under ``luna_soc/gateware/vendor/``. ``luna_soc/__init__.py`` appends
that vendor directory to ``sys.path`` only when ``import amaranth_soc`` fails:

    try:
        import amaranth_soc
        import amaranth_stdio
    except:
        sys.path.append(.../gateware/vendor)

So installing real ``amaranth-soc`` is sufficient to de-vendor: the ``try``
succeeds, the vendor path is never appended, and both our designs *and*
``luna_soc``'s own peripherals bind to upstream. Nothing needs patching out.

This script checks three things that matter, in order of increasing strength:

1. ``amaranth_soc`` imports standalone, with no ``luna_soc`` import first, and
   resolves to site-packages rather than the vendor directory. That kills the
   load-bearing import-order comments.
2. Every ``luna_soc`` module that imports ``amaranth_soc`` still imports. This
   is what would break if upstream had dropped API the fork relied on.
3. The real designs **elaborate to RTLIL**. An import proves the names exist;
   only elaboration proves the shapes and signature flows still connect. RTLIL
   conversion is the netlist step of a build without invoking yosys/nextpnr, so
   it exercises the gateware without needing a board or a toolchain run.

Deliberately does NOT touch the FPGA and does NOT build into
``tmp/vexii_hello/build`` -- another agent owns the flash gateware and that
build directory. Everything here is in-process elaboration writing only to
``tmp/logs/``.

    ./scripts/amaranth_soc_devendor_verify.py
"""

import importlib
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "amaranth_soc_devendor_verify.log"
AEST = timezone(timedelta(hours=10))

LINES: list[str] = []


def emit(msg: str = "") -> None:
    print(msg, flush=True)
    LINES.append(msg)


# luna_soc modules that import amaranth_soc. If upstream had dropped anything
# the vendored fork relied on, these are where it would surface.
LUNA_SOC_MODULES = [
    "luna_soc.gateware.core.blockram",
    "luna_soc.gateware.core.uart",
    "luna_soc.gateware.core.timer",
    "luna_soc.gateware.core.ila",
    "luna_soc.gateware.core.spiflash.controller",
    "luna_soc.gateware.core.spiflash.mmap",
    "luna_soc.gateware.cpu.vexriscv",
    "luna_soc.generate.svd",
    "luna_soc.generate.c",
    "luna_soc.generate.rust",
    "luna_soc.generate.introspect",
]


def check_standalone_import() -> bool:
    """amaranth_soc must import with no luna_soc import first."""
    emit("--- 1. standalone import (no luna_soc first)")
    if "luna_soc" in sys.modules:
        emit("    SKIP: luna_soc already imported in this process")
        return False
    try:
        import amaranth_soc
        import amaranth_soc.csr
        import amaranth_soc.wishbone
        import amaranth_soc.csr.wishbone
    except Exception as exc:
        emit(f"    FAIL: {type(exc).__name__}: {exc}")
        return False

    path = Path(amaranth_soc.__file__)
    version = getattr(amaranth_soc, "__version__", "(none)")
    emit(f"    amaranth_soc {version}")
    emit(f"    at {path}")
    if "vendor" in path.parts:
        emit("    FAIL: resolved to the VENDORED copy, not upstream")
        return False
    emit("    PASS: upstream, csr/wishbone/csr.wishbone all import")
    return True


def check_luna_soc_modules() -> bool:
    """luna_soc's own peripherals must still bind against upstream."""
    emit()
    emit("--- 2. luna_soc modules against upstream amaranth_soc")
    ok = True
    for name in LUNA_SOC_MODULES:
        try:
            importlib.import_module(name)
            emit(f"    OK   {name}")
        except Exception as exc:
            emit(f"    FAIL {name} -> {type(exc).__name__}: {exc}")
            ok = False
    # After importing luna_soc, amaranth_soc must STILL be upstream: if the
    # vendor path had been appended, a submodule imported later could resolve
    # into the vendor tree and silently mix two copies.
    import amaranth_soc.csr
    path = Path(amaranth_soc.csr.__file__)
    if "vendor" in path.parts:
        emit(f"    FAIL: after luna_soc, csr resolves to vendored {path}")
        ok = False
    else:
        emit(f"    OK   after luna_soc, csr still upstream")
    emit(f"    => {'PASS' if ok else 'FAIL'}")
    return ok


def elaborate(label: str, build) -> bool:
    """Convert a design to RTLIL. The real proof: shapes and flows must connect."""
    from amaranth.back import rtlil
    try:
        dut = build()
        text = rtlil.convert(dut, name=label.replace("/", "_").replace(".", "_"))
    except Exception as exc:
        emit(f"    FAIL {label} -> {type(exc).__name__}: {exc}")
        for line in traceback.format_exc().strip().splitlines()[-6:]:
            emit(f"         {line}")
        return False
    emit(f"    OK   {label}  ({len(text.splitlines())} RTLIL lines)")
    return True


def elaborate_on_platform(label: str, build, build_dir: Path) -> bool:
    """Elaborate a platform-dependent design all the way to a Verilog netlist.

    ``HelloSoC`` is a bare ``Elaboratable`` with no signature, and it
    instantiates ``LunaECP5DomainGenerator``, which needs real platform clock
    resources. So RTLIL conversion with ``ports=`` is not available and the
    design must go through the platform's own build path.

    ``do_build=False`` stops after emitting the netlist and constraints: the
    products are generated but yosys/nextpnr are never invoked and no hardware
    is touched. That is exactly the step this change could break -- the CSR and
    wishbone connections resolve during elaboration -- without spending a
    place-and-route or going anywhere near the board.
    """
    try:
        from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4
        plan = CynthionPlatformRev1D4().build(
            build(), do_build=False, do_program=False,
            build_dir=str(build_dir))
    except Exception as exc:
        emit(f"    FAIL {label} -> {type(exc).__name__}: {exc}")
        for line in traceback.format_exc().strip().splitlines()[-6:]:
            emit(f"         {line}")
        return False

    verilog = [name for name in plan.files if name.endswith(".v")]
    if not verilog:
        emit(f"    FAIL {label} -> build plan contains no Verilog netlist")
        return False
    lines = sum(plan.files[name].count("\n") for name in verilog)
    emit(f"    OK   {label}  ({lines} Verilog lines in {', '.join(verilog)})")
    return True


def check_elaboration() -> bool:
    """Elaborate the real designs that depend on amaranth_soc."""
    emit()
    emit("--- 3. real designs elaborated to RTLIL")
    ok = True

    sys.path.insert(0, str(ROOT / "ecp5-test" / "i2c"))
    sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
    sys.path.insert(0, str(ROOT / "ecp5-test" / "qspi"))

    # multiplexed.py -- a CSR peripheral, the simplest amaranth_soc consumer.
    try:
        import multiplexed
        ok &= elaborate("ecp5-test/i2c/multiplexed.py:MultiplexedI2C",
                        multiplexed.MultiplexedI2C)
    except Exception as exc:
        emit(f"    FAIL importing multiplexed.py -> {type(exc).__name__}: {exc}")
        ok = False

    # vexii_cpu.py -- wishbone.Interface, the class-identity trap the
    # import-order comment existed to avoid.
    try:
        import vexii_cpu
        ok &= elaborate("ecp5-test/riscv/vexii_cpu.py:VexiiRiscv",
                        lambda: vexii_cpu.VexiiRiscv())
    except Exception as exc:
        emit(f"    FAIL importing vexii_cpu.py -> {type(exc).__name__}: {exc}")
        ok = False

    # vexii_irq.py -- class-level csr.Field annotations, the py3.14 bug path.
    try:
        import vexii_irq
        ok &= elaborate("ecp5-test/riscv/vexii_irq.py:InterruptController",
                        lambda: vexii_irq.InterruptController())
    except Exception as exc:
        emit(f"    FAIL importing vexii_irq.py -> {type(exc).__name__}: {exc}")
        ok = False

    # hello_soc.py -- the full SoC: wishbone Decoder + CSR bridge + blockram +
    # VexRiscv. The strongest test of the wishbone rewrite upstream did in
    # c9cd4cd (the _FeatureShim change), because that rewrote exactly how
    # Decoder wires an optional-feature mismatch.
    #
    # Its own build dir, NOT tmp/vexii_hello/build: another agent owns the
    # flash gateware and that directory.
    try:
        import hello_soc
        ok &= elaborate_on_platform(
            "ecp5-test/riscv/hello_soc.py:HelloSoC",
            lambda: hello_soc.HelloSoC(firmware=[0] * 16),
            ROOT / "tmp" / "amaranth_soc_devendor" / "hello_soc")
    except Exception as exc:
        emit(f"    FAIL importing hello_soc.py -> {type(exc).__name__}: {exc}")
        ok = False

    # vexii_hello_soc.py -- the flash gateware SoC. Another agent owns this
    # file and it is NOT edited here; it is elaborated to prove the de-vendoring
    # does not break their work in progress. Its own build dir, never
    # tmp/vexii_hello/build.
    try:
        import vexii_hello_soc
        ok &= elaborate_on_platform(
            "ecp5-test/riscv/vexii_hello_soc.py:HelloSoC (not edited)",
            lambda: vexii_hello_soc.HelloSoC(firmware=[0] * 16),
            ROOT / "tmp" / "amaranth_soc_devendor" / "vexii_hello_soc")
    except Exception as exc:
        emit(f"    FAIL importing vexii_hello_soc.py -> {type(exc).__name__}: {exc}")
        ok = False

    emit(f"    => {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    emit(f"amaranth-soc de-vendor verification "
         f"{datetime.now(AEST).isoformat(timespec='seconds')}")
    emit(f"python: {sys.version.splitlines()[0]}")
    emit()

    results = {
        "standalone import": check_standalone_import(),
        "luna_soc modules": check_luna_soc_modules(),
        "design elaboration": check_elaboration(),
    }

    emit()
    emit("=== summary ===")
    for name, ok in results.items():
        emit(f"  {'PASS' if ok else 'FAIL'}  {name}")

    LOG.write_text("\n".join(LINES) + "\n")
    print(f"log -> {LOG.relative_to(ROOT)}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
