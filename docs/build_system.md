## Build System

This page now covers both build-system commands and the local toolchain environment they depend on.

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

### Toolchain Environment

#### OSS CAD Suite

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

#### Compilers

**ARM (Apollo):**
```bash
arm-none-eabi-gcc --version  # GCC 15.2.0
arm-none-eabi-g++
arm-none-eabi-ar
```

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

#### Python Environment

**Version:** 3.14.4 (no-GIL)  
**Key Packages:**
- Amaranth 0.5.8 (HDL framework)
- luna-usb 0.2.3 (USB framework)
- luna-soc 0.3.2 (SoC framework)
- cynthion 0.2.4.post26+git.4fd1f7a8 (gateware package)

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

**Apollo Firmware (ARM):**
```
awto-apollo/firmware/build/cynthion_d11/
  ├── apollo_debug_soc.elf
  └── apollo_debug_soc.bin
```

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
├── apollo-build.log
├── moondancer-build.log
├── gateware-analyzer-build.log
├── gateware-facedancer-build.log
├── install-status.log
├── versions.json
└── (other logs)
```

---

