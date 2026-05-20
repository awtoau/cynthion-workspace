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

| Path | Repo | Upstream | Contents |
|------|------|----------|----------|
| `repos/cynthion` | awtoau/awto-cynthion | greatscottgadgets/cynthion | Firmware (Rust), gateware (Python/Amaranth), Python host library |
| `repos/apollo` | awtoau/awto-apollo | greatscottgadgets/apollo | Apollo ARM MCU firmware (C/TinyUSB) |
| `repos/luna` | awtoau/awto-luna | greatscottgadgets/luna | LUNA USB gateware library |
| `repos/saturn-v` | awtoau/awto-saturn-v | greatscottgadgets/saturn-v | Apollo DFU bootloader |
| `repos/facedancer` | awtoau/awto-facedancer | greatscottgadgets/facedancer | Patched Facedancer host library |
| `repos/packetry` | awtoau/awto-packetry | greatscottgadgets/packetry | USB capture + analysis tool |
| `repos/cynthion-hardware` | awtoau/awto-cynthion-hardware | greatscottgadgets/cynthion-hardware | KiCad schematics and PCB layout |
| `app/` | *(in-tree)* | — | Flutter dashboard — topology graph, TTY log, power rails |

## Local mirrors

Upstream repos are mirrored at `~/git_mirror/greatscottgadgets/` for offline access and
reference (KiCad files, upstream history). The `cynthion-hardware` schematics also have a
standalone copy at `~/git_mirror/cynthion-hardware/`.

```
~/git_mirror/greatscottgadgets/
  apollo/               ARM MCU firmware upstream
  cynthion/             gateware + Python lib upstream
  cynthion-hardware/    KiCad schematics + PCB
  facedancer/           Facedancer upstream
  luna/                 LUNA gateware upstream
  packetry/             USB capture tool upstream
  saturn-v/             DFU bootloader upstream
~/git_mirror/cynthion-hardware/   standalone KiCad copy
~/git_mirror/packetry/            standalone packetry copy
```

## CI levels

| Level | Trigger | Time | What |
|-------|---------|------|------|
| **fast** | every commit + PR | ~2 min | C/Rust check, Python import+unit, gateware elaborate |
| **full** | nightly + release | ~30 min | Full synthesis, firmware build, artifact generation |

## Python strategy

- **Required**: 3.12 (stable, known-good with full dependency stack)
- **Target**: 3.14t (free-threaded, no-GIL) — in CI as `allowed-to-fail`, promoted once stack proves stable
- Note: free-threaded builds introduced in 3.13t, 3.14t is current
- Pinned in `scripts/setup-dev.sh` via `uv`
