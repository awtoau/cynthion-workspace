#!/usr/bin/env python3
"""Run an RV64 Linux smoke boot in QEMU and capture serial logs."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "riscv-64" / "out"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "qemu_boot.log"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="QEMU RV64 Linux smoke boot")
    p.add_argument("--kernel", required=True, help="Path to RV64 Linux Image")
    p.add_argument("--initrd", help="Optional initramfs image")
    p.add_argument("--dtb", help="Optional DTB for QEMU run")
    p.add_argument(
        "--append",
        default="console=ttyS0 earlycon=sbi panic=-1",
        help="Kernel command line",
    )
    p.add_argument(
        "--machine",
        default="virt",
        help="QEMU machine type (default: virt)",
    )
    p.add_argument(
        "--memory",
        default="512M",
        help="Guest RAM for QEMU validation run (default: 512M)",
    )
    p.add_argument(
        "--cpus",
        default="1",
        help="vCPU count for QEMU validation run (default: 1)",
    )
    p.add_argument(
        "--timeout-sec",
        type=int,
        default=60,
        help="Maximum run time before QEMU is terminated",
    )
    return p


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"Missing required tool: {name}")
    return path


def main() -> int:
    args = build_parser().parse_args()
    qemu = require_tool("qemu-system-riscv64")

    kernel = Path(args.kernel)
    if not kernel.exists():
        raise SystemExit(f"Kernel not found: {kernel}")

    cmd = [
        qemu,
        "-machine",
        args.machine,
        "-cpu",
        "rv64",
        "-m",
        args.memory,
        "-smp",
        args.cpus,
        "-nographic",
        "-serial",
        "mon:stdio",
        "-bios",
        "default",
        "-kernel",
        str(kernel),
        "-append",
        args.append,
    ]

    if args.initrd:
        initrd = Path(args.initrd)
        if not initrd.exists():
            raise SystemExit(f"Initrd not found: {initrd}")
        cmd.extend(["-initrd", str(initrd)])

    if args.dtb:
        dtb = Path(args.dtb)
        if not dtb.exists():
            raise SystemExit(f"DTB not found: {dtb}")
        cmd.extend(["-dtb", str(dtb)])

    LOG.write_text("", encoding="ascii")
    print("$ " + " ".join(cmd))
    print(f"Logging QEMU output to {LOG}")

    with LOG.open("a", encoding="utf-8", errors="replace") as logf:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(line, end="")
                logf.write(line)
            rc = proc.wait(timeout=args.timeout_sec)
            print(f"QEMU exited with code {rc}")
            return rc
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            msg = f"QEMU timed out after {args.timeout_sec}s and was terminated.\n"
            print(msg, end="")
            logf.write(msg)
            return 124


if __name__ == "__main__":
    raise SystemExit(main())
