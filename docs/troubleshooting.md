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

---

