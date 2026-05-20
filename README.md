# cynthion-workspace

Integration workspace for the Cynthion USB proxy stack.

This repo does not contain code — it pins submodule versions, provides fast
dev-cycle check scripts, and owns CI configuration.

## Quick start

```bash
git clone --recurse-submodules https://github.com/awtoau/cynthion-workspace
cd cynthion-workspace
./scripts/setup-dev.sh        # one-time: venv + toolchain checks
./scripts/check-fast.sh       # run before every commit
```

## Repository map

| Path | Repo | Contents |
|------|------|----------|
| `repos/cynthion` | awtoau/cynthion | Firmware (Rust), gateware (Python/Amaranth), Python host library |
| `repos/apollo` | greatscottgadgets/apollo | Apollo ARM MCU firmware (C/TinyUSB) |
| `repos/luna` | greatscottgadgets/luna | LUNA USB gateware library |
| `repos/saturn-v` | greatscottgadgets/saturn-v | Apollo DFU bootloader |
| `repos/facedancer` | awtoau/facedancer | Patched Facedancer host library |
| `app/` | awtoau/cynthion-app | Flutter dashboard (QHD + laptop + mobile) |

## CI levels

| Level | Trigger | Time | What |
|-------|---------|------|------|
| **fast** | every commit + PR | ~2 min | C/Rust check, Python import+unit, gateware elaborate |
| **full** | nightly + release | ~30 min | Full synthesis, firmware build, artifact generation |

## Python strategy

- **Required**: 3.12 (stable, known-good with full dependency stack)
- **Target**: 3.15t (free-threaded) — in CI as `allowed-to-fail`, promoted once stack proves stable
- Pinned in `scripts/setup-dev.sh` via `uv`
