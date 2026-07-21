#!/usr/bin/env bash
# Full machine setup for Cynthion development — Fedora and Ubuntu.
# Installs system packages, Rust, uv, Flutter, then dev Python environment.
# Safe to re-run: existing installs are skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

echo "==> cynthion machine setup"

# --- detect distro ---
if command -v dnf &>/dev/null; then
    DISTRO=fedora
elif command -v apt-get &>/dev/null; then
    DISTRO=ubuntu
else
    die "unsupported distro (need dnf or apt-get)"
fi
info "distro: $DISTRO"

# --- system packages ---
echo "==> system packages"
if [ "$DISTRO" = fedora ]; then
    sudo dnf install -y \
        arm-none-eabi-gcc-cs \
        arm-none-eabi-binutils-cs \
        arm-none-eabi-newlib \
        llvm clang cmake ninja-build \
        gtk3-devel \
        libudev-devel \
        libusb1-devel \
        udev \
        git curl
elif [ "$DISTRO" = ubuntu ]; then
    sudo apt-get update -q
    sudo apt-get install -y \
        gcc-arm-none-eabi \
        binutils-arm-none-eabi \
        llvm clang cmake ninja-build \
        libgtk-3-dev \
        libudev-dev \
        libusb-1.0-0-dev \
        git curl
fi

# --- udev rules for Cynthion / Apollo ---
echo "==> udev rules"
RULES=/etc/udev/rules.d/54-cynthion.rules
if [ ! -f "$RULES" ]; then
    sudo tee "$RULES" > /dev/null <<'EOF'
# Great Scott Gadgets / Cynthion
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615c", MODE="0664", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615b", MODE="0664", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="6018", MODE="0664", GROUP="plugdev", TAG+="uaccess"
EOF
    sudo udevadm control --reload-rules
    info "udev rules installed — reconnect device if already plugged in"
else
    info "udev rules already present"
fi

# --- Rust ---
echo "==> rust"
if ! command -v rustup &>/dev/null; then
    info "installing rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
fi
# shellcheck disable=SC1090
source "$HOME/.cargo/env" 2>/dev/null || true
if ! command -v cargo &>/dev/null; then
    export PATH="$HOME/.cargo/bin:$PATH"
fi
info "rust: $(rustc --version)"
RUST_TARGET="riscv32imac-unknown-none-elf"
if ! rustup target list --installed | grep -q "$RUST_TARGET"; then
    info "adding target $RUST_TARGET..."
    rustup target add "$RUST_TARGET"
fi
if ! rustup component list --installed | grep -q clippy; then
    rustup component add clippy
fi

# --- uv ---
echo "==> uv"
if ! command -v uv &>/dev/null; then
    info "installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
info "uv: $(uv --version)"

# --- Flutter ---
echo "==> flutter"
FLUTTER_DIR="$HOME/.local/flutter"
if command -v flutter &>/dev/null; then
    info "flutter: $(flutter --version 2>&1 | head -1)"
elif [ -d "$FLUTTER_DIR" ]; then
    export PATH="$FLUTTER_DIR/bin:$PATH"
    info "flutter: $(flutter --version 2>&1 | head -1)"
else
    info "installing flutter to $FLUTTER_DIR..."
    git clone --depth 1 -b stable https://github.com/flutter/flutter.git "$FLUTTER_DIR"
    export PATH="$FLUTTER_DIR/bin:$PATH"
    flutter doctor -v 2>&1 | grep -E '(Flutter|✗|✓|!)' || true
    info "add to shell: export PATH=\"\$HOME/.local/flutter/bin:\$PATH\""
fi

# --- submodules ---
echo "==> submodules"
cd "$ROOT"
git submodule update --init --recursive
info "submodules ready"

# --- Python dev environment ---
echo "==> python dev environment"
"$ROOT/scripts/setup-dev.sh"

echo ""
echo "Machine setup complete."
echo "Activate Python env: source $ROOT/.venv/bin/activate"
echo "Run fast checks:      $ROOT/scripts/check-fast.sh"
echo "Unified CLI:          $ROOT/scripts/cynthion_control.py --help"
