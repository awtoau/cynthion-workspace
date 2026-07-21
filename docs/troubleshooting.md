## Troubleshooting

### Python 3.14 Not Found
```bash
# Check if installed
python3.14 --version

# Install (Fedora)
sudo dnf install python3.14 python3.14-devel

# Install (Ubuntu)
sudo apt-get install python3.14 python3.14-dev

# Verify Amaranth compatibility
source ~/.opt/oss-cad-suite/environment
python3.14 -m cynthion.gateware.analyzer.top --dry-run
```

### Rust RISC-V Target Missing
```bash
# Add target
rustup target add riscv32imac-unknown-none-elf

# Verify
rustup target list --installed
```

### OSS CAD Suite Download Fails
```bash
# Check network access
curl -I https://github.com/YosysHQ/oss-cad-suite-build/releases/latest

# Manual download and install
wget https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz
tar xzf oss-cad-suite-linux-x64-20260522.tgz -C ~/opt/

# Verify
source ~/opt/oss-cad-suite/environment
yosys --version
```

### Build Artifacts Not Found
```bash
# Check status
./scripts/install.py status

# Check logs
ls -la tmp/*.log

# Check build directories
find "${REPOS_ROOT:-$HOME/git/awtoau}" -name "*.elf" -o -name "*.bin"
```

### act Installation Fails
```bash
# Manual installation
mkdir -p ~/.local/bin
curl -o ~/.local/bin/act \
  https://github.com/nektos/act/releases/download/v0.2.60/act_Linux_x86_64
chmod +x ~/.local/bin/act
export PATH="$HOME/.local/bin:$PATH"

# Verify
act --version
```

### Docker Issues with act
```bash
# Check Docker daemon
sudo systemctl start docker

# Add user to docker group
sudo usermod -aG docker $USER

# Verify
docker ps
```

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

---

