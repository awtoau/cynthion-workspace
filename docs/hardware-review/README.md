# Hardware / misc work rescued from standalone fork clones (for review)

Uncommitted work found in standalone clones under `/mnt/2tb/git/awtoau/` during
a 2026-07-23 drive sweep, preserved here before those clones were deleted. None
of it existed in the canonical workspace submodules (which were all clean).

## cynthion-hardware

Source: `/mnt/2tb/git/awtoau/awto-cynthion-hardware` (base `13aa71c`, r1.4.0).

- **`cynthion.kicad_pcb` — NOT captured as a diff.** The change was a whole-file
  rewrite (152,578 → 98,626 lines) from KiCad format `version 20221018` to a
  newer format header — i.e. a **KiCad-version format migration**, not design
  work. `cynthion.kicad_pcb.reformat-sample.txt` holds the hunk header + first
  lines as evidence. If real design edits are suspected inside the reformat,
  re-open the PCB in the matching KiCad version and diff there; a raw text diff
  is not meaningful across a format bump.
- **`cynthion.kicad_pro.diff`** — the settings/project-file diff (small; largely
  KiCad default-key and float-precision churn, same class as the mirror's).
- **`cynthion-hardware-exports/`** — untracked artifacts the user generated
  (dated 2026-05-24): `cynthion.step` (4.3 MB 3D export), `cynthion.png`,
  `cynthion2.png` (renders). Regenerable from the PCB, kept here for review.

## moondancer (awto-cynthion)

Source: `/mnt/2tb/git/awtoau/awto-cynthion` (base `ef9addb`, on origin/main).

- **`moondancer-logport.diff`** — a 1-line uncommitted change:
  `log::set_port(Port::Both)` → `Port::Uart0` in
  `firmware/moondancer/src/bin/moondancer.rs`. Switches logging from both
  outputs to UART0 only. Trivial; preserved for completeness.

## apollo

The apollo boot-to-DFU / JTAG / USB-handoff work (2 commits + a vendor.c
rewrite) from `/mnt/2tb/git/awtoau/awto-apollo` is preserved separately under
[../apollo/code-test-2-boot-dfu/](../apollo/code-test-2-boot-dfu/). The earlier
UART-DMA clone work is under [../apollo/code-test/](../apollo/code-test/).

## Status

All preserved for **review only** — not adopted into any submodule, not pushed
to any fork. The source clones were deleted after this capture.
