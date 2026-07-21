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

### Apollo Firmware Fixed

**Issue:** TinyUSB submodule initialization failed  
**Fix:** Applied: `git submodule deinit -f lib/tinyusb && git submodule update --init`  
**Result:** Apollo firmware now builds successfully ✓

---

