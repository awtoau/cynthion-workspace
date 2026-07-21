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

