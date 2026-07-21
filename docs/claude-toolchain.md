# Canonical Toolchain Configuration

**Last Updated**: 2026-05-22  
**Status**: Active  
**Installed**: ${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}

## Environment Setup

Before any build (Apollo, moondancer, gateware), source the environment:

```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
```

This sets PATH and LD_LIBRARY_PATH for all FPGA tools.

## Canonical Versions

| Tool | Version | Commit/Build | Purpose |
|------|---------|--------------|---------|
| **Yosys** | 0.65+57 | 9d0cdb855 | HDL synthesis (Verilog) |
| **nextpnr-ecp5** | 0.10-74-gee605e2b | ee605e2b | Place & Route for ECP5 |
| **Trellis** | (bundled) | - | ECP5 bitstream generation |
| **OSS CAD Suite** | 2026-05-22 | - | Complete toolchain bundle |

## Verification

```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
yosys --version        # Should show 0.65+57
nextpnr-ecp5 --version # Should show 0.10-74-gee605e2b
```

## For CI/CD and Team Consistency

- **All team members** must use this same OSS CAD Suite location (or install the same version)
- **Reproducible builds** require matching Yosys and nextpnr versions
- **CI/CD pipelines** should source this environment before running gateware builds
- **Bitstream reproducibility** depends on consistent toolchain versions

## Switching Back to System Tools (Not Recommended)

If you need to use system-installed Yosys/nextpnr (not recommended), you'll lose reproducibility:
```bash
# To disable the sourced environment, you'd need to restart shell or:
# unset PATH LD_LIBRARY_PATH (but this is destructive)
```

**Recommendation**: Always use `${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}` for Cynthion builds.

## Python 3.14 no-GIL Status

- ✅ Python 3.14.4 available on system
- ⏳ Amaranth 0.5 compatibility: To be tested in Pre-Phase-1
- ✅ No Python 2 code paths (future package can be removed)
- ✅ Modern toolchain (GCC 16, Clang 18)

## Related Documentation

- [wiki.md](wiki.md) — Consolidated toolchain and build workflow summary
- [install.md](install.md) — Detailed setup and build instructions
