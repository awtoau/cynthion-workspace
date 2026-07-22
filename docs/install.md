# Cynthion Workspace Installation & Build Guide

## Overview

This workspace uses forked repositories under `${REPOS_ROOT:-$HOME/git/awtoau}`. Use workspace-relative or `$HOME`-based paths rather than machine-specific `/home/dan/...` paths.

## Documentation Ownership

- This document is the canonical install, toolchain, and build-system reference.
- Phase tracking and success criteria live in [apollo_serial_architecture_redesign_plan.md](apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md).
- Patch and change history lives in [patchset_overview.md](patchset/patchset_overview.md).

## Version and Update Checks

Run these early during setup and whenever the environment drifts:

```bash
./scripts/install.py versions
./scripts/install.py versions-check
```

Use this to:
- Capture local versions for system tools, FPGA toolchain, Python packages, and repo commits
- Compare local versions with upstream releases
- Keep a baseline for later diff checks

Suggested workflow:

```bash
# Save baseline after a known-good setup
./scripts/install.py versions > tmp/versions-baseline.txt

# Compare later state
./scripts/install.py versions > tmp/versions-current.txt
diff tmp/versions-baseline.txt tmp/versions-current.txt

# Check upstream updates
./scripts/install.py versions-check
```

---

## Prerequisites

### System Requirements
- **OS**: Linux x86_64 (tested on Fedora 44)
- **Disk Space**: 5+ GB free (repos + builds)
- **RAM**: 8+ GB (16 GB recommended for synthesis)

### Required System Packages

**Install all at once (Fedora/RHEL):**
```bash
sudo dnf install -y \
  arm-none-eabi-gcc-cs \
  binutils \
  bison \
  boost-devel \
  clang \
  cmake \
  curl \
  dfu-util \
  eigen-devel \
  flex \
  gawk \
  gcc gcc-c++ \
  git \
  jq \
  libreadline-devel \
  make \
  openocd \
  pkg-config \
  python3.14 python3.14-devel \
  readline-devel \
  tcl tcl-devel \
  zlib-devel
```

**Or on Debian/Ubuntu:**
```bash
sudo apt-get update && sudo apt-get install -y \
  arm-none-eabi-gcc \
  binutils \
  bison \
  build-essential \
  clang \
  cmake \
  curl \
  dfu-util \
  flex \
  gawk \
  git \
  jq \
  libeigen3-dev \
  libboost-all-dev \
  libreadline-dev \
  openocd \
  pkg-config \
  python3.14 python3.14-dev \
  tcl tcl-dev \
  zlib1g-dev
```

### Core Prerequisites (Detailed)

| Tool | Version | Purpose | Check | Note |
|------|---------|---------|-------|------|
| **Build Tools** | | | | |
| GCC/G++ | 15+ | C/C++ compilation | `gcc --version` | Comes with build-essential |
| Make | 4.0+ | Build system | `make --version` | Standard |
| CMake | 3.10+ | Cross-platform builds | `cmake --version` | For some components |
| Clang | 18+ | LLVM compiler | `clang --version` | Optional, used by Yosys |
| Binutils | latest | Binary utilities | `ld --version` | Part of build toolchain |
| **Hardware Dev** | | | | |
| Git | 2.30+ | Version control | `git --version` | Required for repos |
| ARM GCC | 15.x | ARM Cortex-M | `arm-none-eabi-gcc --version` | Apollo firmware |
| **HDL/FPGA Tools** | | | | |
| Flex | 2.6+ | Lexer generator | `flex --version` | For Yosys/nextpnr |
| Bison | 3.0+ | Parser generator | `bison --version` | For Yosys/nextpnr |
| pkg-config | 0.29+ | Library finder | `pkg-config --version` | Build configuration |
| **Libraries** | | | | |
| boost-devel | 1.70+ | C++ libraries | `dpkg -l \| grep boost` | Needed for some tools |
| libreadline | 7.0+ | Interactive input | Check: `/usr/include/readline` | For CLI tools |
| zlib | latest | Compression | Check: `/usr/include/zlib.h` | Bitstream compression |
| Eigen3 | 3.3+ | Linear algebra | Check: `/usr/include/eigen3` | Optional, for analysis |
| **Programming** | | | | |
| Python | 3.14.x | Gateware framework | `python3.14 --version` | Amaranth, cynthion |
| Rustup | latest | RISC-V firmware | `rustc --version` | moondancer |
| **Utilities** | | | | |
| curl | 7.50+ | HTTP download | `curl --version` | Fetch releases |
| jq | 1.5+ | JSON parser | `jq --version` | Parse GitHub API |
| dfu-util | latest | USB DFU flashing | `dfu-util --version` | Optional: for flashing |
| OpenOCD | 0.10+ | JTAG debugging | `openocd --version` | Optional: for debugging |
| TCL | 8.5+ | Scripting | `tclsh --version` | For some EDA tools |

### Rust RISC-V Target

```bash
# Required for moondancer firmware build
rustup target add riscv32imac-unknown-none-elf
rustup target list --installed  # Verify
```

### OSS CAD Suite (FPGA Toolchain)

**Option 1: Automatic Installation (Recommended)**
```bash
./scripts/install.py toolchain-install
```

**Option 2: Manual Installation**
```bash
# Download latest release
wget https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz

# Extract
tar xzf oss-cad-suite-linux-x64-20260522.tgz -C ~/opt/

# Verify
source ~/opt/oss-cad-suite/environment
yosys --version         # → 0.65+57
nextpnr-ecp5 --version  # → 0.10-74-gee605e2b
```

### Pre-Flight Checklist

```bash
# Check Python 3.14
python3.14 --version  # → 3.14.4

# Check Rust
rustc --version && rustup target list --installed | grep riscv

# Check ARM toolchain
arm-none-eabi-gcc --version  # → GCC 15.x

# Check build tools
gcc --version && make --version && cmake --version

# Check toolchain
source ~/opt/oss-cad-suite/environment && yosys --version
```

### Early Failure Recovery (Run Before `setup`)

If pre-flight checks fail, fix these first instead of continuing:

#### Python 3.14 Not Found
```bash
python3.14 --version

# Fedora/RHEL
sudo dnf install python3.14 python3.14-devel

# Debian/Ubuntu
sudo apt-get install python3.14 python3.14-dev
```

#### Rust RISC-V Target Missing
```bash
rustup target add riscv32imac-unknown-none-elf
rustup target list --installed
```

#### OSS CAD Suite Download/Install Failure
```bash
# Check connectivity to release endpoint
curl -I https://github.com/YosysHQ/oss-cad-suite-build/releases/latest

# Manual fallback install
wget https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz
tar xzf oss-cad-suite-linux-x64-20260522.tgz -C ~/opt/

# Verify tools
source ~/opt/oss-cad-suite/environment
yosys --version
nextpnr-ecp5 --version
```

#### Build Artifacts Not Found
```bash
./scripts/install.py status
ls -la tmp/*.log
find "${REPOS_ROOT:-$HOME/git/awtoau}" -name "*.elf" -o -name "*.bin"
```

#### Optional CI Tooling (`act`) Fails

Only needed if you run local GitHub Actions workflows.

```bash
mkdir -p ~/.local/bin
curl -o ~/.local/bin/act \
  https://github.com/nektos/act/releases/download/v0.2.60/act_Linux_x86_64
chmod +x ~/.local/bin/act
export PATH="$HOME/.local/bin:$PATH"
act --version
```

If `act` fails due to Docker:

```bash
sudo systemctl start docker
sudo usermod -aG docker $USER
docker ps
```

If you still need a deeper diagnosis after these first-pass fixes, see [troubleshooting.md](troubleshooting.md).

### Reference: CI/Docker Configuration

The following are sourced from official CI workflows and Docker configurations:

**GitHub Actions Workflows:**
- `awto-apollo/.github/workflows/firmware.yml` — Apollo firmware CI
- `awto-cynthion/.github/workflows/python.yml` — Gateware CI (Python 3.9-3.13)
- `awto-saturn-v/.github/workflows/build.yml` — Saturn-V bootloader CI

**Docker Image (Ubuntu 22.04):**
- Source: `awto-luna/Dockerfile`
- Includes: Full Ubuntu build-essential, boost, Eigen3, OpenOCD, DFU-util
- Installs OSS CAD Suite automatically from latest GitHub release

See respective `.github/workflows/*.yml` files for exact CI setup details.

---

## Repository Structure

```
${REPOS_ROOT:-$HOME/git/awtoau}/
├── awto-apollo/              # Debug controller firmware (ARM)
│   ├── firmware/
│   │   ├── Makefile          # Build via: make APOLLO_BOARD=cynthion [target]
│   │   ├── src/
│   │   │   ├── boards/       # Board-specific code
│   │   │   │   ├── cynthion_d11/
│   │   │   │   ├── cynthion_d21/
│   │   │   │   └── ...
│   │   │   └── ...
│   │   └── lib/
│   │       └── tinyusb/      # Vendored (submodule, requires init)
│   └── apollo_fpga/          # Host-side Python module
│
├── awto-cynthion/            # Gateware (Python/Amaranth) + moondancer firmware (Rust)
│   ├── cynthion/python/      # Gateware project
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   ├── cynthion/     # Python package root
│   │   │   │   ├── gateware/
│   │   │   │   │   ├── analyzer/
│   │   │   │   │   ├── facedancer/
│   │   │   │   │   ├── platform/
│   │   │   │   │   │   ├── cynthion_r0_2.py
│   │   │   │   │   │   └── ... (other revisions)
│   │   │   │   │   └── ...
│   │   │   │   └── ...
│   │   │   └── ...
│   │   └── ...
│   ├── firmware/
│   │   └── moondancer/       # RISC-V firmware (Rust/Cargo)
│   │       └── Cargo.toml
│   └── ...
│
├── awto-luna/                # Luna USB framework (dependency)
├── awto-facedancer/          # Facedancer USB device framework (dependency)
└── ...
```

## Canonical Toolchain

### Environment Setup

Before any build, source the OSS CAD Suite environment:

```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
```

**Location**: `${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}`  
**Version**: 2026-05-22  

| Tool | Version | Commit |
|------|---------|--------|
| Yosys | 0.65+57 | 9d0cdb855 |
| nextpnr-ecp5 | 0.10-74-gee605e2b | ee605e2b |
| Trellis | (bundled) | - |

Verify:
```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
yosys --version        # → 0.65+57
nextpnr-ecp5 --version # → 0.10-74-gee605e2b
```

### Python & Dependencies

- **Python**: 3.14.4 (system, no-GIL)
- **Amaranth**: 0.5.x (gateware HDL framework)
  - ✅ Tested compatible with Python 3.14
  - Elaboration of analyzer gateware succeeds
- **RISC-V Toolchain**: riscv32-unknown-elf (for moondancer firmware)
- **ARM Toolchain**: arm-none-eabi-gcc (GCC 15.2.0, for Apollo)

Verify Python and gateware:
```bash
python3.14 --version  # → 3.14.4
which riscv32-unknown-elf-gcc
which arm-none-eabi-gcc
```

## Quick Operations with install.py

Use `install.py` for common setup and recovery flows:

```bash
# Fail-fast prerequisite check (recommended first step)
./scripts/install.py prereqs

# Full setup: clone repos, init submodules, install deps, build all
./scripts/install.py setup

# Parallel setup (faster on multi-core hosts)
./scripts/install.py --parallel setup
./scripts/install.py --parallel --jobs 2 setup

# Just clone repos without building
./scripts/install.py --repo-only setup

# Rebuild everything (clean + setup)
./scripts/install.py rebuild

# Check workspace status
./scripts/install.py status

# Clone repos to custom location
./scripts/install.py --repos-path /path/to/repos clone-repos

# CI helpers
./scripts/install.py ci-install
./scripts/install.py ci-list

# Live troubleshooting
tail -f ./tmp/logs/install-*.log
grep ERROR ./tmp/logs/install-*.log
```

**Options:**
- `--dry-run` — Show what would happen without executing
- `--verbose` — Show full command output
- `--no-build` — Setup repos but skip builds
- `--no-submodules` — Skip git submodule initialization
- `--repos-path /path` — Use custom repos directory

### Local CI Smoke Test (Apollo)

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo"
act -l
act -j firmware-build
```

---

## Manual Build Steps

If you prefer to build components individually:

### Phase 1.1: Apollo Firmware

**Board**: Cynthion d11 (ATSAMD11)  
**Compiler**: arm-none-eabi-gcc (GCC 15.2.0)  
**Dependencies**: TinyUSB (vendored in `lib/tinyusb/`)

#### Setup (one-time)

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo/firmware"
# Initialize submodules (TinyUSB)
make APOLLO_BOARD=cynthion get-deps
```

The `get-deps` target will:
- Initialize `lib/tinyusb/` submodule
- Download any external dependencies
- Prepare build artifacts directory

#### Build

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo/firmware"

# Clean build
make APOLLO_BOARD=cynthion clean
make APOLLO_BOARD=cynthion

# Install to connected Cynthion (requires Saturn-V)
make APOLLO_BOARD=cynthion dfu
```

**Output**: `build/cynthion_d11/apollo_debug_soc.elf` and `.bin`

#### Build Configuration

- `APOLLO_BOARD=cynthion` → Maps to `cynthion_d11` internally
- Alternative: `APOLLO_BOARD=luna` (legacy name, same as cynthion)
- `BOARD_REVISION_MAJOR=X BOARD_REVISION_MINOR=Y` → Override auto-detection (needed for r0.5 and older)

### Phase 1.2: moondancer Firmware (Rust/RISC-V)

**Target**: RISC-V RV32IM (VexRiscv on FPGA)  
**Compiler**: Rust + riscv32-unknown-elf-gcc  
**Build System**: Cargo

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion/firmware/moondancer"

# Build release
cargo build --release

# Output: target/riscv32imac-unknown-none-elf/release/moondancer
```

**Note**: The target is defined in `.cargo/config.toml` — do NOT pass `--target` manually.

### Phase 1.3: Gateware (Analyzer)

**Framework**: Amaranth (Python HDL)  
**Synthesis**: Yosys → nextpnr-ecp5 → trellis → bitstream  
**Platform**: Cynthion r0.2 (ECP5 LFE5U-12F)

#### Setup (one-time)

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion/cynthion/python"

# Install cynthion package in editable mode
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
pip install --user -e .
```

#### Build

```bash
# Test elaboration (dry-run)
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 \
  python3.14 -m cynthion.gateware.analyzer.top --dry-run

# Full synthesis (produces .bit file)
LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 \
  python3.14 -m cynthion.gateware.analyzer.top
```

**Output**: Bitstream file (location depends on Amaranth build output)

### Phase 1.4: Gateware (Facedancer)

Same as Analyzer, but with facedancer module:

```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 \
  python3.14 -m cynthion.gateware.facedancer.top --dry-run
```

## Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `LUNA_PLATFORM` | `cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2` | Gateware build target |
| `APOLLO_BOARD` | `cynthion` | Apollo firmware board selection |
| `BOARD_REVISION_MAJOR` | (auto-detect for r0.6+) | Hardware revision override |
| `BOARD_REVISION_MINOR` | (auto-detect for r0.6+) | Hardware revision override |
| `ECP5_SPEED_GRADE` | `8` (default) | ECP5 speed grade |

## Temporary Files

All build artifacts and logs go to `./tmp/` per CLAUDE.md rules:

```bash
# Logs
./tmp/apollo-build.log
./tmp/apollo-get-deps.log
./tmp/amaranth-py314-test.log
./tmp/moondancer-build.log
./tmp/gateware-analyzer-build.log
./tmp/gateware-facedancer-build.log
```

## Known Issues & Workarounds

### TinyUSB Submodule Not Initialized

**Error**: `lib/tinyusb/examples/make.mk: No such file or directory`

**Solution**: Run `make APOLLO_BOARD=cynthion get-deps` before building.

### LUNA_PLATFORM Not Found

**Error**: `ModuleNotFoundError: No module named 'cynthion'`

**Solutions**:
1. Ensure cynthion package is installed: `pip install --user -e .`
2. Use full platform path: `cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2`
3. Source OSS CAD Suite environment first

### apollo-mux Runtime Context Trap (REPL works, `riscv` fails)

**Symptom pattern**:
- `apollo-mux` starts and connects to socket
- REPL responds to `help`
- `riscv` commands fail with `No module named 'cynthion'`

This usually means runtime context mismatch, not hardware failure.

**Distinguish the three common causes**:
1. **Package missing**: `python -c "import cynthion"` fails in all terminals.
2. **Wrong interpreter**: package is installed in one Python, but `apollo-mux` runs under another.
3. **Wrong cwd/PYTHONPATH context**: `apollo-mux` starts from a context where the expected package path is not visible.

**Minimal reproducible failure sequence**:
```bash
# Example of a context mismatch that can fail on riscv command paths.
python3 /path/to/awto-cynthion/scripts/apollo-mux.py \
  --socket /path/to/awto-cynthion/tmp/apollod.sock --no-spinner -v
```

**Known-good sequence**:
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"

# Use the same interpreter that has the editable cynthion package.
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" -m pip install -e cynthion/python

# Validate import path before launching REPL.
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" - <<'PY'
import cynthion
print(cynthion.__file__)
PY

# Launch with the same interpreter used for the import check.
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" scripts/apollo-mux.py \
  --socket "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion/tmp/apollod.sock" --no-spinner -v
```

**Validation checklist**:
1. `apollo-mux` shows socket connected.
2. Import check prints a valid `cynthion` module path.
3. `riscv canary` executes without import-path errors.
4. If command still fails, check device mode/API compatibility separately from Python environment.

### Facedancer Bitstream Build Blocked

If local facedancer asset build is blocked, a prebuilt `facedancer.bit` fallback can be used for temporary bring-up continuity.

Use the detailed runbook in [troubleshooting.md](troubleshooting.md) before applying this fallback:
- verify failure signatures first,
- use explicit artifact path provenance,
- validate USB mode and command sanity after load,
- return to canonical local build flow when toolchain is restored.

## Next Steps

Track current phase completion criteria and phase 2+ planning in:
- [apollo_serial_architecture_redesign_plan.md](apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md)

## References

- [Apollo README](https://github.com/greatscottgadgets/apollo) - Official build instructions
- [Cynthion Hardware](https://github.com/greatscottgadgets/cynthion-hardware) - Board schematics
- [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) - FPGA toolchain releases
- [Amaranth HDL](https://amaranth-lang.org/) - Python HDL framework

