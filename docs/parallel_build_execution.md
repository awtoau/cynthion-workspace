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
   - Thread 1: Apollo Firmware (10 min)
   - Thread 2: moondancer (5 min)
   - Thread 3: Analyzer Gateware (8 min)
   - Thread 4: Facedancer Gateware (8 min)
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

