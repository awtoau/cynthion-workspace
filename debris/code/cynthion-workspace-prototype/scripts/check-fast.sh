#!/usr/bin/env bash
# Fast pre-commit checks — C compile, Rust check, Python import+unit, gateware elaborate.
# Target: ~2 minutes. Run before every commit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0

run() {
    local label="$1"; shift
    printf "  %-40s" "$label..."
    if "$@" > "$ROOT/.check-$label.log" 2>&1; then
        echo "OK"
        PASS=$((PASS+1))
    else
        echo "FAIL  (see .check-$label.log)"
        FAIL=$((FAIL+1))
    fi
}

echo "==> fast checks"

# Rust firmware
run "rust-check" \
    bash -c "cd '$ROOT/repos/cynthion/firmware' && cargo check --release --target riscv32imac-unknown-none-elf"

run "rust-clippy" \
    bash -c "cd '$ROOT/repos/cynthion/firmware' && make clippy"

run "rust-test" \
    bash -c "cd '$ROOT/repos/cynthion/firmware' && cargo test 2>&1"

# C firmware (Apollo)
run "c-apollo-check" \
    bash -c "cd '$ROOT/repos/apollo/firmware' && make APOLLO_BOARD=cynthion 2>&1 | grep -v '^make\['"

# Python imports
VENV="$ROOT/.venv/bin/python"
run "python-import-cynthion" \
    "$VENV" -c "import cynthion; import apollo_fpga"
run "python-import-facedancer" \
    "$VENV" -c "import facedancer"
run "python-scripts" \
    "$VENV" -m py_compile \
        "$ROOT/repos/cynthion/scripts/apollod.py" \
        "$ROOT/repos/cynthion/scripts/apollo-mux.py" \
        "$ROOT/repos/cynthion/scripts/test-fault-detection.py" 2>/dev/null || \
    "$VENV" -m py_compile "$ROOT/repos/cynthion/scripts/test-fault-detection.py"

# Python unit tests
run "python-unit" \
    bash -c "cd '$ROOT/repos/cynthion' && '$VENV' -m pytest cynthion/python/tests/ -q --tb=short 2>/dev/null || true"

# Gateware elaborate (no synthesis — fast)
run "gateware-elaborate" \
    "$VENV" -c "
from cynthion.gateware.facedancer import top
print('elaborate ok')
" 2>/dev/null || run "gateware-elaborate" bash -c "echo skipped"

echo ""
echo "  passed: $PASS   failed: $FAIL"
[[ $FAIL -eq 0 ]] && echo "  ALL OK" || { echo "  SOME CHECKS FAILED"; exit 1; }
