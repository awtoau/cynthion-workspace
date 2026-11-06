# Licensing

- **The project is GPL-3.0-or-later.** Text: [`LICENSE`](../LICENSE). Decision: [#613](https://github.com/awtoau/cynthion-workspace/issues/613) (Dan, 2026-11-06): reuse as much outside code as possible, no concern for how others use ours.
- **New files** carry `SPDX-License-Identifier: GPL-3.0-or-later`.
- **Existing files keep their header.** 228 files say `BSD-3-Clause`; BSD-3 code is GPL-compatible and stays BSD-3 inside the GPL work. Nothing was rewritten.
- **Third-party files keep their notice and licence** (never strip a copyright line):
  - `gateware/probes/usb_host/guh/`: GUH, S. Holzapfel, BSD-3-Clause (`LICENSE` beside the files; pin in [`upstream-boundary.md`](upstream-boundary.md)).
  - `gateware/board/{core,cynthion_r1_4,resources}.py`, `gateware/usb_ids.py`, parts of `soc/top.py`: Great Scott Gadgets, BSD-3-Clause.
  - `patches/luna-*.patch`: apply to luna (BSD-3-Clause); the added `record.py` / `coding.py` are Amaranth's BSD-2-Clause code.
- **Submodules** (`repos/*`) keep their own licences.
- **Linux kernel modules** (`linux/`): `GPL-2.0`, the kernel's licence; GPL-3 code cannot be linked into the kernel.
- **What we may take in:** GPL-3, LGPL-2.1 / 3, Apache-2.0, MPL-2.0, MIT, BSD, ISC.
- **What stays out:** code with no licence, GPL-2.0-only, CERN-OHL-S (GUH's early files carried it by mistake, `apfaudio/guh#1`), proprietary / vendor IP, no-commercial or no-derivatives terms.
- Distributing a bitstream built from GPL source means offering that source. This repo is public, which does it.
- Not legal advice.
