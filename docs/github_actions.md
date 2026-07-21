## GitHub Actions

### Current Workflows

**awto-apollo/.github/workflows/firmware.yml**
- Builds: Apollo firmware for 6 board variants
- Triggers: push, pull_request, merge_group
- Runs on: ubuntu-latest

**awto-cynthion/.github/workflows/python.yml**
- Tests: Python package on 3 OS × 5 Python versions
- Triggers: push, pull_request, weekly schedule
- Matrix: 15 jobs
- Runs on: ubuntu-latest, macos-latest, windows-latest

**awto-saturn-v/.github/workflows/build.yml**
- Builds: Saturn-V bootloader on 2 platforms
- Triggers: push, pull_request, weekly schedule

**awto-luna/.github/workflows/simulate.yml**
- Runs: HDL simulations
- Triggers: push, pull_request, weekly schedule

### Missing from GitHub Actions
- ✗ No FPGA bitstream generation (no Yosys/nextpnr)
- ✗ No moondancer firmware build
- ✗ No analyzer/facedancer gateware build

### Enhancement: Full Build Workflow

Create `.github/workflows/full-build.yml` that calls install.py:

```yaml
name: Complete Build Pipeline

on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'
      - run: |
          sudo apt-get update && sudo apt-get install -y \
            arm-none-eabi-gcc binutils bison boost-dev clang cmake \
            curl dfu-util flex gawk git jq libeigen3-dev libreadline-dev \
            openocd pkg-config tcl tcl-dev zlib1g-dev
      - run: ./scripts/install.py setup
      - uses: actions/upload-artifact@v4
        with:
          name: artifacts
          path: |
            **/*.elf
            **/*.bin
            tmp/versions.json
```

Then test locally:
```bash
act -j build -P ubuntu-latest=ubuntu:22.04
```

---

