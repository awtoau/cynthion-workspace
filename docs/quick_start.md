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

# Run Apollo CI locally
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo"
act -l                    # List jobs
act -j firmware-build     # Run firmware build
```

---

