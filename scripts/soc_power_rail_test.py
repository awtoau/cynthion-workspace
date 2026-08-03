#!/usr/bin/env python3
"""Compile and run the firmware rail verdict's synthetic negative control."""

import datetime
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "firmware" / "cynthion-soc" / "src" / "power_rails.rs"


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = ROOT / "tmp" / "logs" / f"soc_power_rail_test-{stamp}.log"
    binary = ROOT / "tmp" / f"soc_power_rail_test-{stamp}"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []

    def emit(message):
        print(message, flush=True)
        lines.append(message)
        log_path.write_text("\n".join(lines) + "\n")

    emit(f"source: {SOURCE.relative_to(ROOT)}")
    emit("compiling the firmware tolerance function as a Rust unit test")
    built = subprocess.run(
        ["rustc", "--edition=2021", "--test", str(SOURCE), "-o", str(binary)],
        cwd=ROOT, capture_output=True, text=True)
    for line in (built.stdout + built.stderr).splitlines():
        emit(line)
    if built.returncode:
        emit(f"compile: FAIL ({built.returncode})")
        return built.returncode

    emit("running in-tolerance and deliberately out-of-tolerance samples")
    tested = subprocess.run([str(binary), "--nocapture"], cwd=ROOT,
                            capture_output=True, text=True)
    for line in (tested.stdout + tested.stderr).splitlines():
        emit(line)
    emit(f"verdict: {'PASS' if tested.returncode == 0 else 'FAIL'}")
    emit(f"log: {log_path.relative_to(ROOT)}")
    return tested.returncode


if __name__ == "__main__":
    sys.exit(main())
