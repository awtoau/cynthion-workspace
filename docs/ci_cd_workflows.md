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
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo"
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

**Apollo:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo"
act -l                    # Lists: firmware-build, host
act -j firmware-build     # Runs: make get-deps all
```

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

