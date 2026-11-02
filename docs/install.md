# Prerequisites and toolchain

- Setup and build procedure: [`START.md`](START.md). Machine setup is `./dev.py setup` (`scripts/machine_setup.py`: distro packages, submodules, Rust target, Python packages, udev). Check with `./dev.py doctor`.
- Versions and constraints: [`toolchain-versions.md`](toolchain-versions.md) (the only place versions live).
- Retired install guide (the `cyn` CLI, `awto-apollo` paths, `apollod`, `act`, `install.py setup`): [`history/upstream-toolchain.md`](history/upstream-toolchain.md).
- Paths: workspace-relative or `$HOME`-based, never one machine's absolute path; `scripts/check.py paths` enforces it.

## System

- Linux x86_64 (tested on Fedora 44); 5+ GB disk (repos + builds); 8+ GB RAM (16 GB recommended for synthesis).

### Packages

Fedora/RHEL:

```bash
sudo dnf install -y arm-none-eabi-gcc-cs binutils bison boost-devel clang cmake curl \
  dfu-util eigen-devel flex gawk gcc gcc-c++ git jq libreadline-devel make openocd \
  pkg-config readline-devel tcl tcl-devel zlib-devel
```

Debian/Ubuntu:

```bash
sudo apt-get update && sudo apt-get install -y arm-none-eabi-gcc binutils bison \
  build-essential clang cmake curl dfu-util flex gawk git jq libeigen3-dev \
  libboost-all-dev libreadline-dev openocd pkg-config tcl tcl-dev zlib1g-dev
```

Minimums:

| Tool | Version | Purpose |
|---|---|---|
| GCC/G++ | 15+ | C/C++ |
| Make / CMake | 4.0+ / 3.10+ | builds |
| Clang | 18+ | optional, used by Yosys |
| Git | 2.30+ | repos |
| ARM GCC | 15.x | Apollo firmware |
| Flex / Bison / pkg-config | 2.6+ / 3.0+ / 0.29+ | Yosys/nextpnr builds |
| boost / readline / zlib / Eigen3 | 1.70+ / 7.0+ / any / 3.3+ | tool libraries (Eigen3 optional) |
| Python | 3.15t | free-threaded: [`toolchain-versions.md`](toolchain-versions.md#python) |
| Rustup | latest | moondancer / SoC firmware |
| curl 7.50+, jq 1.5+, dfu-util, OpenOCD 0.10+, TCL 8.5+ | | fetch, JSON, DFU flashing (optional), JTAG debug (optional), some EDA tools |

### Rust target

```bash
rustup target add riscv32imac-unknown-none-elf
rustup target list --installed
```

### OSS CAD Suite

- Automatic: `./scripts/install.py toolchain-install` (`toolchain-status` to check).
- Manual: download `https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz`, `tar xzf ... -C ~/opt/`.
- Use: `source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"`, then `yosys --version`, `nextpnr-ecp5 --version`.
- Installed versions: [`toolchain-versions.md`](toolchain-versions.md#fpga-toolchain).
- `./scripts/install.py versions` / `versions-check` capture local versions and compare with upstream releases; `scripts/install.py` otherwise is superseded by `machine_setup.py` and kept for `versions`, `versions-check`, `toolchain-install`, `toolchain-status` ([#157](https://github.com/awtoau/cynthion-workspace/issues/157)).

## Pre-flight

```bash
python3 --version                                   # 3.15.x
python3 -c "import sys; print(sys._is_gil_enabled())"   # False
rustc --version && rustup target list --installed | grep riscv
arm-none-eabi-gcc --version                         # 15.x
source ~/opt/oss-cad-suite/environment && yosys --version
```

- Python 3.15t missing: `uv python install 3.15t`, or build from source with `--disable-gil`. Override the interpreter with `CYN_PYTHON=/path/to/python`.
- Console scripts (`cynthion`, `apollo`) need the free-threaded `bin/` ahead of `~/.local/bin` on PATH: [`../README.md`](../README.md#path-ordering-matters).

## Build facts

- Apollo flashing uses the Saturn-V DFU bootloader. The device must enumerate as `1d50:60e6` for `make APOLLO_BOARD=cynthion dfu`; `1d50:615b` is analyzer/facedancer mode, not the Apollo flash target.
- `APOLLO_BOARD=cynthion` maps to `cynthion_d11`; `luna` is an alias. `BOARD_REVISION_MAJOR` / `BOARD_REVISION_MINOR` override auto-detection (needed for r0.5 and older).
- `make APOLLO_BOARD=cynthion get-deps` initialises `lib/tinyusb/`; missing it fails with `lib/tinyusb/examples/make.mk: No such file or directory`.
- Moondancer's cargo target is defined in `.cargo/config.toml`: do not pass `--target`.
- Upstream gateware dry-run uses `LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2`; `ECP5_SPEED_GRADE` defaults to `8`.
- `ModuleNotFoundError: No module named 'cynthion'`: install `python3 -m pip install -e repos/cynthion/cynthion/python`, check `python3 -c "import cynthion; print(cynthion.__file__)"` resolves under the same interpreter, source the OSS CAD Suite environment.
- Logs and artifacts go to `./tmp/` (`./tmp/logs/`), never `/tmp`.
