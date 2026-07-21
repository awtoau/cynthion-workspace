## Version Management

### Track Versions
```bash
./scripts/install.py versions
```

**Output includes:**
- System tools (git, python, rustc, gcc, etc)
- FPGA toolchain (yosys, nextpnr, trellis)
- Python packages (amaranth, luna-usb, luna-soc, cynthion)
- Repository commits (all repos with dates)
- Saves to: `tmp/versions.json`

### Check for Updates
```bash
./scripts/install.py versions-check
```

**Compares:**
- Local Yosys vs latest on GitHub
- Local nextpnr vs latest on GitHub
- Local OSS CAD Suite vs latest on GitHub
- Local repo commits vs remote origin/main

**Status indicators:**
- ✓ Up-to-date
- ⚠ Outdated (update available)

### Version Tracking Workflow
```bash
# Initial setup
./scripts/install.py versions > tmp/versions-baseline.txt

# Later, check what changed
./scripts/install.py versions > tmp/versions-current.txt
diff tmp/versions-baseline.txt tmp/versions-current.txt

# Check for upstream updates
./scripts/install.py versions-check
```

---

