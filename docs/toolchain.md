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

### Python Environment

**Version:** 3.14.4 (no-GIL)  
**Key Packages:**
- Amaranth 0.5.8 (HDL framework)
- luna-usb 0.2.3 (USB framework)
- luna-soc 0.3.2 (SoC framework)
- cynthion 0.2.4.post26+git.4fd1f7a8 (gateware package)

---

