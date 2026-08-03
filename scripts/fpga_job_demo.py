#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause

"""Build and check the hardware-free queue demonstration artifact."""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "run"))
    args = parser.parse_args()

    job_dir = Path(os.environ.get("FPGA_JOB_DIR", ".")).resolve()
    artifact = job_dir / "simulation-result.json"
    if args.action == "build":
        artifact.write_text(json.dumps({"sum": 42}) + "\n", encoding="utf-8")
        print(f"built {artifact.name}")
        return 0

    measured = json.loads(artifact.read_text(encoding="utf-8"))
    expected = json.loads(os.environ["FPGA_JOB_EXPECTED"])
    print(f"measured={measured} expected={expected}")
    return 0 if measured == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
