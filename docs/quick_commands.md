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
cd awto-apollo && act -l                          # List Apollo jobs
cd awto-apollo && act -j firmware-build           # Run Apollo CI

# Logging
tail -f ./tmp/logs/install-*.log                  # Watch logs live
grep ERROR ./tmp/logs/install-*.log               # Find errors
```

---

---

