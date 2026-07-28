#!/usr/bin/env bash
# One-time dev environment setup for the Cynthion workspace.
# Installs the workspace packages into the default free-threaded Python and
# checks the C/Rust toolchains. Deliberately no venv: the workspace runs on
# system 3.15t so that plain 'python3' and the console scripts agree.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> cynthion-workspace setup"

# --- Python (uv) ---
if ! command -v uv &>/dev/null; then
    echo "  installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Free-threaded 3.15t is the workspace interpreter. Prefer whatever 'python3'
# already resolves to if it is a free-threading build; otherwise ask uv for one.
PYTHON="${CYN_PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if not sys._is_gil_enabled() else 1)' 2>/dev/null; then
    echo "  python3 is not a free-threaded build; installing 3.15t via uv..."
    uv python install 3.15t
    PYTHON="$(uv python find 3.15t)"
fi
echo "  python: $("$PYTHON" --version) ($PYTHON)"

echo "  installing packages (editable, into the default environment)..."
"$PYTHON" -m pip install -e "$ROOT/repos/cynthion/cynthion/python"
"$PYTHON" -m pip install -e "$ROOT/repos/facedancer"
"$PYTHON" -m pip install pytest pyserial prompt_toolkit anthropic 2>/dev/null || true

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
echo "Setup complete — no venv to activate; plain 'python3' has the packages."
echo "Console scripts live next to the interpreter; put that dir ahead of"
echo "~/.local/bin on PATH so stale shims cannot shadow them:"
echo "    export PATH=\"\$(dirname \"\$($PYTHON -c 'import sys; print(sys.executable)')\"):\$PATH\""
echo ""
echo "Verify:      python3 -c 'import cynthion, apollo_fpga, facedancer'"
echo "Run checks:  ./scripts/install.py prereqs"
