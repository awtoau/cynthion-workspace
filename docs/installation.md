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

