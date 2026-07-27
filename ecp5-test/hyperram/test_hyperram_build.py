#!/usr/bin/env python3
"""
Build and deploy HyperRAM burst test to Cynthion.

Usage:
  ./test_hyperram_build.py                    # Dry-run (show commands)
  ./test_hyperram_build.py --build            # Build RTL and bitstream
  ./test_hyperram_build.py --deploy           # Deploy to Cynthion
  ./test_hyperram_build.py --test             # Build, deploy, and test
"""

import subprocess
import sys
from pathlib import Path

def run(cmd, dry_run=False):
    """Run command or show it."""
    print(f"  $ {' '.join(cmd)}")
    if not dry_run:
        result = subprocess.run(cmd, cwd=Path(__file__).parent)
        if result.returncode != 0:
            print(f"ERROR: Command failed with code {result.returncode}")
            sys.exit(1)

def main():
    dry_run = "--build" not in sys.argv and "--deploy" not in sys.argv and "--test" not in sys.argv
    
    print("=" * 70)
    print("HyperRAM Burst Test - Cynthion r1.4")
    print("=" * 70)
    print()
    
    work_dir = Path(__file__).parent
    test_script = work_dir / "hyperram_burst_test.py"
    
    print("1. Generate Verilog RTL")
    run([
        sys.executable, str(test_script),
        "generate", "-t", "rtlil",
        "-o", "hyperram_burst_test.il"
    ], dry_run=dry_run)
    print()
    
    print("2. Synthesize with Yosys + nextpnr")
    print("   (This requires yosys, nextpnr-ecp5, and oss-cad-suite)")
    run([
        "nextpnr-ecp5",
        "--json", "hyperram_burst_test.json",
        "--config", "/path/to/cynthion/constraints",
        "--textcfg", "hyperram_burst_test.config"
    ], dry_run=dry_run)
    print()
    
    print("3. Generate bitstream with ecppack")
    run([
        "ecppack",
        "hyperram_burst_test.config",
        "hyperram_burst_test.bit"
    ], dry_run=dry_run)
    print()
    
    print("4. Deploy to Cynthion")
    print("   (Requires Apollo/cynthion-control)")
    run([
        "cynthion-control",
        "program", "hyperram_burst_test.bit"
    ], dry_run=dry_run)
    print()
    
    print("5. Read test results via JTAG")
    print("   (Implement via JTAG register read of test_passed/test_failed)")
    print()
    
    print("=" * 70)
    if dry_run:
        print("Dry-run complete. Use --build or --test to execute.")
    else:
        print("Build/deploy complete!")
    print("=" * 70)

if __name__ == "__main__":
    main()
