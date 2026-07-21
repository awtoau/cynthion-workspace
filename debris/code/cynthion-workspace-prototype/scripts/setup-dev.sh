#!/usr/bin/env bash
# One-time dev environment setup for the Cynthion workspace.
# Installs pinned Python toolchain via uv, creates venv, checks C/Rust tools.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> cynthion-workspace setup"

# --- Python (uv) ---
if ! command -v uv &>/dev/null; then
    echo "  installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

PYTHON_VERSION="3.12"
echo "  python $PYTHON_VERSION via uv..."
uv python install "$PYTHON_VERSION"

echo "  creating venv..."
uv venv "$ROOT/.venv" --python "$PYTHON_VERSION"
source "$ROOT/.venv/bin/activate"

echo "  installing packages (editable)..."
uv pip install -e "$ROOT/repos/cynthion/cynthion/python"
uv pip install -e "$ROOT/repos/facedancer"
uv pip install pyserial prompt_toolkit anthropic 2>/dev/null || true

# --- Rust ---
if ! command -v cargo &>/dev/null; then
    echo "  ERROR: cargo not found. Install rustup: https://rustup.rs"
    exit 1
fi
RUST_TARGET="riscv32imac-unknown-none-elf"
if ! rustup target list --installed | grep -q "$RUST_TARGET"; then
    echo "  adding rust target $RUST_TARGET..."
    rustup target add "$RUST_TARGET"
fi
echo "  rust: $(rustc --version)"

# --- ARM C toolchain ---
if ! command -v arm-none-eabi-gcc &>/dev/null; then
    echo "  WARNING: arm-none-eabi-gcc not found (needed for Apollo firmware)"
fi

# --- FPGA toolchain (optional) ---
if command -v yosys &>/dev/null; then
    echo "  yosys: $(yosys --version 2>&1 | head -1)"
else
    echo "  yosys: not found (only needed for full synthesis)"
fi

echo ""
echo "Setup complete. Activate with: source .venv/bin/activate"
echo "Run checks with:              ./scripts/check-fast.sh"
