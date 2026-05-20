#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/repos/cynthion/firmware"
echo "==> rust check + clippy + test"
cargo check --release --target riscv32imac-unknown-none-elf
make clippy
cargo test
echo "OK"
