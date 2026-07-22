## Troubleshooting

For first-pass setup blockers (Python, Rust target, OSS CAD, `act`, Docker), use the Early Failure Recovery section in [install.md](install.md).

This page keeps advanced runtime and fallback runbooks that are too specific for the main install path.

### apollo-mux: REPL works but `riscv` commands fail with `No module named 'cynthion'`
```bash
# Typical symptom:
# - apollo-mux connects to socket
# - REPL accepts commands
# - riscv command path throws ModuleNotFoundError
```

This is usually an execution-context problem. Treat diagnosis in this order:

1. Package installed?
```bash
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" -c "import cynthion; print(cynthion.__file__)"
```

2. Interpreter mismatch?
```bash
which python3
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" -m pip show cynthion
```

3. Launch context mismatch (cwd/PYTHONPATH)?
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" scripts/apollo-mux.py \
  --socket "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion/tmp/apollod.sock" --no-spinner -v
```

Known-good runtime pattern:
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" -m pip install -e cynthion/python
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" -c "import cynthion; print(cynthion.__file__)"
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/python" scripts/apollo-mux.py --socket "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion/tmp/apollod.sock" --no-spinner -v
```

Fast validation checklist:
1. Socket connected message appears.
2. Import check resolves `cynthion` in the same interpreter used to run `apollo-mux`.
3. `riscv canary` runs without `ModuleNotFoundError`.
4. If failure remains, investigate device mode/API state separately (not Python packaging).

### Facedancer Prebuilt-Bitstream Fallback Runbook

Use this only when local facedancer gateware build/toolchain path is blocked and you need bring-up continuity.

Prechecks:
```bash
# 1) Confirm toolchain/build-path failure signature first.
# Typical signatures:
# - missing FPGA toolchain binaries
# - facedancer asset build fails

# 2) Confirm baseline USB mode before switching.
lsusb | rg -i '1d50:615b|1d50:615c'

# 3) Confirm a candidate prebuilt bitstream exists.
find "${REPOS_ROOT:-$HOME/git/awtoau}" -path '*/assets/*' -name 'facedancer.bit'
```

Known fallback command pattern:
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/cynthion" run \
  --bitstream /absolute/path/to/facedancer.bit facedancer
```

Postchecks:
```bash
# 1) Verify USB identity/mode changed as expected.
lsusb | rg -i '1d50:615b|1d50:615c'

# 2) Verify command path sanity.
"${REPOS_ROOT:-$HOME/git/awtoau}/cynthion-workspace/.venv/bin/cynthion" run -h
```

When this fallback is appropriate:
1. You need immediate debug/bring-up continuity.
2. Canonical gateware build path is temporarily unavailable.

Risks and limitations:
1. Version drift: prebuilt artifact may not match current source tree.
2. Reproducibility gap: behavior cannot be attributed to local commit build output.
3. Trust boundary: only use artifacts from known local repos with provenance.

Recovery to canonical path:
1. Restore local FPGA toolchain and successful `make assets` flow.
2. Re-run with locally built facedancer artifact.
3. Record toolchain and artifact provenance in notes/logs.

---

