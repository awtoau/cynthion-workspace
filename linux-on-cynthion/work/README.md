# `riscv/work/` — scratch, not source

**This directory is gitignored.** Anything you put here is invisible to git and
will be lost the next time it is cleaned. Do not put hand-written source here.

## What belongs here

Toolchain clones and build scratch, all re-creatable from scratch:

| Contents | Upstream |
|---|---|
| `nextpnr/` | YosysHQ/nextpnr |
| `prjtrellis/` | YosysHQ/prjtrellis |
| `trellis-install/` | prjtrellis build output (`make install` prefix) |

Clone from a local mirror if one is configured, otherwise from upstream.

**VexiiRiscv is no longer one of these.** It used to be cloned here, which meant
the tree the sweep depended on existed only on whichever machine had run the
clone. It is now a submodule at [`repos/vexiiriscv`](../../repos/vexiiriscv),
pinned to a specific commit, and needs
`git submodule update --init --recursive` because its own `ext/` submodules
carry SpinalHDL.

## What does not belong here

Hand-written scripts, gateware, test harnesses, notes, or anything you could not
regenerate by re-running a build. Put those somewhere tracked:

- gateware / hardware test harnesses → `gateware/`
- workspace tooling → `scripts/`
- documentation → `docs/`

This warning exists because it already happened once: a HyperRAM burst-test
harness (five files, ~830 lines, including its own README) sat here uncommitted
and was one `rm -rf` away from being lost. It now lives in
[`gateware/probes/hyperram/`](../../gateware/probes/hyperram/).
