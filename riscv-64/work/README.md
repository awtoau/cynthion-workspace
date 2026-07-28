# `riscv-64/work/` — scratch, not source

**This directory is gitignored.** Anything you put here is invisible to git and
will be lost the next time it is cleaned. Do not put hand-written source here.

## What belongs here

Toolchain clones and build scratch, all re-creatable from scratch:

| Contents | Upstream |
|---|---|
| `nextpnr/` | YosysHQ/nextpnr |
| `prjtrellis/` | YosysHQ/prjtrellis |
| `vexiiriscv/` | SpinalHDL/VexiiRiscv |
| `trellis-install/` | prjtrellis build output (`make install` prefix) |

Clone from a local mirror if one is configured, otherwise from upstream.

## What does not belong here

Hand-written scripts, gateware, test harnesses, notes, or anything you could not
regenerate by re-running a build. Put those somewhere tracked:

- gateware / hardware test harnesses → `ecp5-test/`
- workspace tooling → `scripts/`
- documentation → `docs/`

This warning exists because it already happened once: a HyperRAM burst-test
harness (five files, ~830 lines, including its own README) sat here uncommitted
and was one `rm -rf` away from being lost. It now lives in
[`ecp5-test/hyperram/`](../../ecp5-test/hyperram/).
