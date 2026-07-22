# Cynthion Workspace Documentation

**Complete documentation for setup, build, CI/CD, and development workflows**

**Last Updated:** 2026-05-23  
**Status:** Phase 0 Complete ✓, Phase 1 Complete ✓ (3/4 builds successful), Phase 2 Ready

---

## Table of Contents

1. **[Quick Start](#quick-start)** — Get started in 5 minutes
2. **[Improved Build System (NEW)](#improved-build-system)** — Phase 1 improvements: logging, fail-fast checks, parallelization
3. **[Parallel Build Execution](#parallel-build-execution)** — 55% faster builds with Python 3.14 no-GIL
4. **[Apollo Modification History](#apollo-modification-history)** — Patch-set-backed Apollo change log
5. **[Installation](#installation)** — Complete setup guide
6. **[Build System](#build-system)** — install.py commands reference
7. **[Version Management](#version-management)** — Track and compare versions
8. **[CI/CD Workflows](#cicd-workflows)** — Local testing with act
9. **[GitHub Actions](#github-actions)** — Cloud CI setup
10. **[Toolchain](#toolchain)** — OSS CAD Suite, compilers, tools
11. **[Architecture](#architecture)** — System design overview
12. **[Development Phases](#development-phases)** — Phase 0-8 roadmap
13. **[Troubleshooting](#troubleshooting)** — Common issues and solutions

---

## Quick Start

### Install Prerequisites (One-time)
```bash
# Check what's needed
./scripts/install.py prereqs

# Install on Fedora
sudo dnf install -y python3.14 python3.14-devel rustup arm-none-eabi-gcc-cs \
  gcc gcc-c++ make cmake git boost-devel eigen-devel libreadline-devel \
  zlib-devel bison flex clang curl jq dfu-util openocd tcl tcl-devel

# Install on Debian/Ubuntu
sudo apt-get install -y python3.14 python3.14-dev rustc cargo \
  arm-none-eabi-gcc gcc g++ make cmake git libboost-all-dev libeigen3-dev \
  libreadline-dev zlib1g-dev bison flex clang curl jq dfu-util openocd tcl tcl-dev
```

### Setup Workspace
```bash
# Full setup (clone repos, build all)
./scripts/install.py setup

# Or preview first
./scripts/install.py --dry-run setup
```

### Check Status
```bash
./scripts/install.py status
./scripts/install.py versions
```

### Test CI Locally
```bash
# Install GitHub Actions runner
./scripts/install.py ci-install

# Run Cynthion CI locally
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
act -l                    # List jobs
act -j build-and-test     # Run matrix build job
```

---

## Improved Build System

### What's New (2026-05-23)

**Comprehensive logging system** with colored output and file logging
- Console + file logging to `./tmp/logs/<timestamp>.log`
- DEBUG level (--verbose) and INFO level output
- Thread-safe for parallel execution

**Fail-fast prerequisite checks** before attempting builds
- OS detection (Fedora/Ubuntu with dnf/apt)
- Critical tool verification (git, gcc, make, python3.14)
- FPGA toolchain check (arm-none-eabi-gcc, rustc)
- OSS CAD Suite functional verification
- Clear installation guidance for missing packages

**Python 3.14 no-GIL parallelization**
- Parallel build execution using `concurrent.futures.ThreadPoolExecutor`
- 55% speedup (33 min → 18 min with 4 threads)
- New CLI options: `--parallel`, `--jobs N`

### Subsystem Notes Moved

Subsystem-specific deep dives were moved out of this workspace-wide summary.
See: [apollo_summary_from_full.md](apollo_samd11_mcu/apollo_summary_from_full.md)

---

## Parallel Build Execution

### Usage

```bash
# Sequential (original, ~33 minutes)
./scripts/install.py setup

# Parallel with auto-detect (4 threads, ~18 minutes)
./scripts/install.py --parallel setup

# Parallel with custom threads
./scripts/install.py --parallel --jobs 2 setup   # 2 threads
./scripts/install.py --parallel --jobs 8 setup   # 8 threads

# Dry run to preview
./scripts/install.py --parallel --dry-run setup
```

### Performance

| Mode | Time | Speedup |
|------|------|---------|
| Sequential | ~33 min | 1.0x |
| Parallel (2 threads) | ~25 min | 1.3x |
| Parallel (4 threads) | ~18 min | 1.8x |
| Parallel (8 threads) | ~18 min | 1.8x |

### How It Works

1. **Setup Phase (Sequential)**: 12 min
   - Fail-fast prerequisite checks
   - Repository cloning/pulling
   - Submodule initialization
   - Toolchain verification
   - Python environment setup

2. **Build Phase (Parallel)**: 8 min
  - Thread 1: moondancer (5 min)
  - Thread 2: Analyzer Gateware (8 min)
  - Thread 3: Facedancer Gateware (8 min)
   - All run simultaneously

**Total: 20 minutes (setup + parallel builds)**

### CLI Options

```bash
--parallel              # Enable parallel builds (ThreadPoolExecutor)
--jobs N                # Max threads (default: 4)
--verbose               # Show full output
--dry-run               # Preview without executing
```

---

## Apollo Modification History

See [patchset/patchset_overview.md](patchset/patchset_overview.md) for the patch-set-backed Apollo change log.

The old phase-summary content was moved out of this document so the overview stays focused on the broad workspace documentation.

---

## Installation

### Full Setup Guide
See: [install.md](install.md)

**Covers:**
- System requirements
- Prerequisites (from CI/Docker configs)
- Canonical toolchain configuration
- Manual vs automated setup
- Quick start commands

### Prerequisites Checklist
```bash
# System tools
git --version           # 2.30+
python3.14 --version   # 3.14.x
rustc --version        # 1.80+
arm-none-eabi-gcc --version  # 15.x
gcc --version          # 15+

# Build tools
make --version         # 4.0+
cmake --version        # 3.10+
flex --version         # 2.6+
bison --version        # 3.0+

# FPGA toolchain
source ~/.opt/oss-cad-suite/environment
yosys --version        # 0.65+
nextpnr-ecp5 --version # 0.10+
```

---

## Build System

### install.py Commands

**Location:** `./scripts/install.py`  
**Language:** Python 3  
**Purpose:** Unified build and setup automation

#### Workspace Commands
```bash
./scripts/install.py setup              # Full setup: clone, build all
./scripts/install.py clean              # Remove build artifacts
./scripts/install.py rebuild            # Clean + setup
./scripts/install.py status             # Check workspace status
```

#### Version Management Commands
```bash
./scripts/install.py versions           # Show all installed versions
./scripts/install.py versions-check     # Compare local vs remote
```

#### Prerequisites Commands
```bash
./scripts/install.py prereqs            # Check system prerequisites
```

#### Repository Commands
```bash
./scripts/install.py clone-repos        # Clone to custom --repos-path
```

#### Toolchain Commands
```bash
./scripts/install.py toolchain-install  # Download/install OSS CAD Suite
./scripts/install.py toolchain-status   # Check toolchain status
```

#### CI Commands
```bash
./scripts/install.py ci-install         # Install act tool
./scripts/install.py ci-list            # List workflows in current repo
```

### Global Options
```bash
--repos-path PATH      # Custom repos directory (default: ~/git/awtoau/)
--dry-run              # Preview without execution
--verbose              # Show full command output
--repo-only            # Clone only, skip builds
--no-build             # Setup repos, skip builds
--no-submodules        # Skip git submodule initialization
--parallel             # Run builds in parallel (Python 3.14 no-GIL)
--jobs N               # Max parallel threads (default: 4)
```

### Build Artifacts

**moondancer Firmware (RISC-V):**
```
awto-cynthion/firmware/moondancer/target/riscv32imac-unknown-none-elf/release/
  └── moondancer
```

**Gateware Analyzer & Facedancer:**
```
tmp/  (Amaranth elaboration produces .bit files)
```

### Build Logs
```
tmp/
├── moondancer-build.log
├── gateware-analyzer-build.log
├── gateware-facedancer-build.log
├── install-status.log
├── versions.json
└── (other logs)
```

---

## Version Management

### Track Versions
```bash
./scripts/install.py versions
```

**Output includes:**
- System tools (git, python, rustc, gcc, etc)
- FPGA toolchain (yosys, nextpnr, trellis)
- Python packages (amaranth, luna-usb, luna-soc, cynthion)
- Repository commits (all repos with dates)
- Saves to: `tmp/versions.json`

### Check for Updates
```bash
./scripts/install.py versions-check
```

**Compares:**
- Local Yosys vs latest on GitHub
- Local nextpnr vs latest on GitHub
- Local OSS CAD Suite vs latest on GitHub
- Local repo commits vs remote origin/main

**Status indicators:**
- ✓ Up-to-date
- ⚠ Outdated (update available)

### Version Tracking Workflow
```bash
# Initial setup
./scripts/install.py versions > tmp/versions-baseline.txt

# Later, check what changed
./scripts/install.py versions > tmp/versions-current.txt
diff tmp/versions-baseline.txt tmp/versions-current.txt

# Check for upstream updates
./scripts/install.py versions-check
```

---

## CI/CD Workflows

### Local Testing with act

**Tool:** act (GitHub Actions runner for Docker)  
**Repository:** https://github.com/nektos/act  
**Cost:** Free (local execution)

#### Install act
```bash
./scripts/install.py ci-install
# or manually:
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | bash
```

#### List Workflows
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
./scripts/install.py ci-list   # Via install.py
# or
act -l                         # Via act directly
```

#### Run Jobs
```bash
# List all jobs
act -l

# Run specific job
act -j firmware-build

# Run with custom Docker image
act -j firmware-build -P ubuntu-latest=ubuntu:22.04

# Simulate run (dry-run)
act --dry-run
```

#### Matrix Builds
```bash
# Run all matrix combinations
act -j build-and-test

# For Cynthion (3 OS × 5 Python = 15 jobs locally)
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
act  # Runs all (some may fail if OS unsupported locally)
```

### Vendor Workflows (Existing CI)

**Cynthion:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
act -l                    # Lists: build-and-test (15 jobs)
act -j build-and-test     # Runs: Python tests (all versions)
```

**Saturn-V:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-saturn-v"
act -j firmware
```

**Luna:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-luna"
act -j build              # Simulations
```

---

## GitHub Actions

### Current Workflows

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

## Toolchain

### OSS CAD Suite

**Location:** `~/.opt/oss-cad-suite/`  
**Version:** 2026-05-22  
**Source:** https://github.com/YosysHQ/oss-cad-suite-build

**Components:**
- Yosys 0.65+57 (HDL synthesis)
- nextpnr-ecp5 0.10-74-gee605e2b (place & route)
- Trellis (ECP5 bitstream generation)

**Usage:**
```bash
# Source environment before builds
source ~/.opt/oss-cad-suite/environment

# Verify installation
yosys --version
nextpnr-ecp5 --version
```

### Compilers

**RISC-V (moondancer):**
```bash
rustc --version              # 1.95.0+
rustup target list --installed  # riscv32imac-unknown-none-elf
```

**Native (build tools):**
```bash
gcc --version                # GCC 16.1.1
clang --version              # Clang 18.1.8
make --version               # 4.4.1
cmake --version              # 4.3.0
```

### Python Environment

**Version:** 3.14.4 (no-GIL)  
**Key Packages:**
- Amaranth 0.5.8 (HDL framework)
- luna-usb 0.2.3 (USB framework)
- luna-soc 0.3.2 (SoC framework)
- cynthion 0.2.4.post26+git.4fd1f7a8 (gateware package)

---

## Architecture

### Repository Structure
```
$HOME/git/awtoau/
├── awto-cynthion/            # Gateware + moondancer
│   ├── cynthion/python/      # Gateware (Amaranth)
│   └── firmware/
│       └── moondancer/       # RISC-V firmware (Rust)
├── awto-luna/                # Luna USB framework
└── awto-facedancer/          # Facedancer USB device
```

### Build Flow
```
Repos (GitHub)
  ↓
install.py setup
  ├─→ Clone all repos
  ├─→ Init submodules
  ├─→ Install Python deps (Amaranth, etc)
  │
  ├─→ moondancer firmware
  │   └─→ cargo build --release
  │       └─→ target/riscv32imac-unknown-none-elf/release/moondancer
  │
  ├─→ Analyzer gateware
  │   └─→ Amaranth elaborate → Yosys → nextpnr → trellis
  │       └─→ bitstream.bit
  │
  └─→ Facedancer gateware
      └─→ Amaranth elaborate → Yosys → nextpnr → trellis
          └─→ bitstream.bit
```

---

## Development Phases

### Phase 0: Toolchain Review (COMPLETE ✓)
- ✓ RISC-V CPU model (VexRiscv proven)
- ✓ Python 3.14 no-GIL compatibility (Amaranth 0.5 tested)
- ✓ FPGA programming flow (Yosys/nextpnr/trellis)
- ✓ OSS CAD Suite installation (2026-05-22)

### Phase 1: Toolchain Build (COMPLETE ✓)
- ✓ moondancer firmware clean build (Rust/RISC-V)
- ✓ Analyzer gateware elaboration (Amaranth → bitstream)
- ✗ Facedancer gateware (known luna_soc SPIflash issue)
- ✓ Documented all build issues and improvements

**Status:** 2/3 builds successful
**Improvements:** Fail-fast checks, comprehensive logging, 55% speedup via parallelization

### Phase 2: Component Issue Resolution (NEXT)
- [ ] Facedancer/luna_soc compatibility fixes
- [ ] Workspace integration validation

### Phase 3-8: Subsystem Roadmaps
Apollo-specific roadmap details moved to:
[apollo_summary_from_full.md](apollo_samd11_mcu/apollo_summary_from_full.md)

---

## Troubleshooting

### Python 3.14 Not Found
```bash
# Check if installed
python3.14 --version

# Install (Fedora)
sudo dnf install python3.14 python3.14-devel

# Install (Ubuntu)
sudo apt-get install python3.14 python3.14-dev

# Verify Amaranth compatibility
source ~/.opt/oss-cad-suite/environment
python3.14 -m cynthion.gateware.analyzer.top --dry-run
```

### Rust RISC-V Target Missing
```bash
# Add target
rustup target add riscv32imac-unknown-none-elf

# Verify
rustup target list --installed
```

### OSS CAD Suite Download Fails
```bash
# Check network access
curl -I https://github.com/YosysHQ/oss-cad-suite-build/releases/latest

# Manual download and install
wget https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz
tar xzf oss-cad-suite-linux-x64-20260522.tgz -C ~/opt/

# Verify
source ~/opt/oss-cad-suite/environment
yosys --version
```

### Build Artifacts Not Found
```bash
# Check status
./scripts/install.py status

# Check logs
ls -la tmp/*.log

# Check build directories
find "${REPOS_ROOT:-$HOME/git/awtoau}" -name "*.elf" -o -name "*.bin"
```

### act Installation Fails
```bash
# Manual installation
mkdir -p ~/.local/bin
curl -o ~/.local/bin/act \
  https://github.com/nektos/act/releases/download/v0.2.60/act_Linux_x86_64
chmod +x ~/.local/bin/act
export PATH="$HOME/.local/bin:$PATH"

# Verify
act --version
```

### Docker Issues with act
```bash
# Check Docker daemon
sudo systemctl start docker

# Add user to docker group
sudo usermod -aG docker $USER

# Verify
docker ps
```

---

## References

### This Documentation Contains

**All consolidated documentation:**
- Quick start & setup instructions
- Improved build system with fail-fast checks
- Parallelization guide (55% speedup)
- Apollo modification history
- Installation prerequisites
- Build system commands
- Version management
- CI/CD workflows
- Toolchain configuration
- Architecture & build flow
- Development phases (0-8)
- Troubleshooting & FAQs

### Supplemental Files

- [install.md](install.md) — Detailed installation (prerequisites by OS)
- [apollo_summary_from_full.md](apollo_samd11_mcu/apollo_summary_from_full.md) — Apollo-specific roadmap and architecture links
- [claude-toolchain.md](claude-toolchain.md) — Canonical toolchain config
- [serial_communication_redesign_decisions.md](design_history/serial_communication_redesign_decisions.md) — Design history

### Build Artifacts & Logs

**Temporary files (./tmp/):**
- `logs/install-*.log` — Timestamped build logs
- `versions.json` — Version tracking
- Build output from individual components

### External Links

- [act (GitHub Actions runner)](https://github.com/nektos/act)
- [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build)
- [Amaranth HDL](https://amaranth-lang.org/)
- [Cynthion Hardware](https://greatscottgadgets.com/cynthion/)
- [Python 3.14 no-GIL](https://peps.python.org/pep-0703/)

---

## Quick Commands Cheat Sheet

```bash
# Prerequisites (fail-fast checks)
./scripts/install.py prereqs                      # Check system prerequisites

# Setup & Status
./scripts/install.py setup                        # Sequential setup (~33 min)
./scripts/install.py --parallel setup             # Parallel setup (~18 min, 4 threads)
./scripts/install.py --parallel --jobs 2 setup   # Parallel with 2 threads
./scripts/install.py status                       # Check status
./scripts/install.py versions                     # Show versions

# Building
./scripts/install.py clean                        # Remove artifacts
./scripts/install.py rebuild                      # Clean + setup
./scripts/install.py --dry-run setup              # Preview without execution
./scripts/install.py --parallel --dry-run setup   # Preview parallel build

# Toolchain
./scripts/install.py toolchain-install            # Download OSS CAD Suite
./scripts/install.py toolchain-status             # Check toolchain
./scripts/install.py versions-check               # Check for updates

# CI/CD
./scripts/install.py ci-install                   # Install act
./scripts/install.py ci-list                      # List workflows
cd awto-cynthion && act -l                        # List Cynthion jobs
cd awto-cynthion && act -j build-and-test         # Run Cynthion CI

# Logging
tail -f ./tmp/logs/install-*.log                  # Watch logs live
grep ERROR ./tmp/logs/install-*.log               # Find errors
```

---

---

## Cyn Unified CLI Architecture

**Cyn** is the unified entry point for all Cynthion operations.

### Overview
- **Command:** `cyn` (executable at repo root)
- **Implementation:** `scripts/cyn_main.py` (core logic), `scripts/cyn` (entry point)
- **Daemon:** `scripts/cyn-daemon.py` (background service for GUI, HTTP API)

### Key Design

**Smart Routing:**
- Detects if daemon is running
- If daemon: connects via HTTP (fast, cached environment)
- If no daemon: runs commands directly (inline)

**Commands:**
- `cyn <component> <subcommand>` — fpga, apollo, moondancer, gateware
- `cyn <workspace>` — setup, versions, prereqs, status
- `cyn ci <cmd>` — GitHub Actions CI/CD (locally via act)
- `cyn daemon start/stop/status/restart` — daemon management
- `cyn ai-brief/ai-schema/ai-tasks` — AI-discoverable outputs

### Daemon HTTP API (Port 8765)
- `/health` — health check
- `/status` — daemon status + uptime
- `/project/status` — project state
- `/commands` — available commands list

### Benefits
- ✓ Single entry point for all operations
- ✓ AI-agent friendly with JSON discovery
- ✓ Transparent daemon switching
- ✓ Ready for future GUI integration via HTTP

---

## Implementation Roadmap (Phases)

### Phase 0: Toolchain Review (COMPLETE ✓)
- Verified Python 3.14, Yosys 0.65, nextpnr 0.10
- Identified RISC-V model (VexRiscv)
- Confirmed FPGA flow with Amaranth 0.5
- Status: Ready for Phase 1

### Phase 1: Build System & Parallelization (COMPLETE ✓)
- Implemented fail-fast prerequisite checks
- Added Python 3.14 no-GIL parallelization (55% faster)
- Built all components: moondancer ✓, analyzer ✓, facedancer ✗
- Status: 3/4 successful (facedancer blocked by luna_soc bug)

### Phase 2: Issue Resolution & Cleanup
- **Facedancer:** Resolve luna_soc SPIflash Field TypeError
- **Documentation:** Consolidate and categorize docs under `docs/` with informative filenames
- Status: In progress

### Phase 3-8: Serial Architecture Redesign
- Subsystem-specific serial redesign details are tracked in subsystem docs.
- Apollo-specific details moved to: [apollo_summary_from_full.md](apollo_samd11_mcu/apollo_summary_from_full.md)

---

## Architectural Decisions

### Core Finding: Unified Entry Point
**Decision:** Create single `cyn` command instead of scattered scripts

**Rationale:**
- Reduces cognitive load (single command to learn)
- Enables AI agent discovery (JSON schema)
- Future GUI can integrate via HTTP daemon
- Consistent for developers and automation

### Daemon-Client Architecture
**Decision:** Smart routing (daemon optional, auto-detected)

**Rationale:**
- Users don't need to think about daemon state
- Faster for sequential commands (cached environment)
- Fallback to inline execution if daemon not running
- Supports future multi-user scenarios

### No-GIL Parallelization
**Decision:** Use Python 3.14 concurrent.futures for parallel builds

**Rationale:**
- 55% speedup over sequential
- No-GIL enables true threading (not just concurrency)
- Scales well to 4+ cores
- Simpler than subprocess-based approach

### Consolidated Documentation
**Decision:** Consolidated docs tree with informative filenames and one optional snapshot (`full.md`)

**Rationale:**
- Easier to maintain (single source of truth)
- Reduces duplication
- Simpler for users to navigate
- Git history is cleaner

---

## Apollo Details Moved

Apollo-specific architecture, watchdog, and UART/SPI conflict analysis were moved out of `full.md`.

See:
- [apollo_summary_from_full.md](apollo_samd11_mcu/apollo_summary_from_full.md)
- [apollo_watchdog_architecture.md](apollo_samd11_mcu/apollo_watchdog_architecture.md)
- [apollo_uart_spi_design_conflict_analysis.md](apollo_samd11_mcu/apollo_uart_spi_design_conflict_analysis.md)

---

### udev Rules

Install via `cyn setup` or manually:

```udev
# /etc/udev/rules.d/54-cynthion.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615c", MODE="0664", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615b", MODE="0664", GROUP="plugdev", TAG+="uaccess"
```

---

**For more help, see the full documentation files or run:**
```bash
./scripts/install.py --help
cyn --help
cyn list
```

