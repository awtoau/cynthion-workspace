## Re-audited against `72fc7ea`. Step 1 is done, and three of the numbers above are now wrong.

### Done since this was written

- **`facedancer` is gone** — submodule, editable install, both `check.py`
  assertions, the `versions.json` line, the `install.py` manifest entry and the
  `upstream_ci.py` entry. `machine_setup.py`, `check.py` and `upstream_ci.py` all
  carry a comment citing this issue.
- **Submodules: eight → four.** apollo, cynthion, cynthion-hardware, vexiiriscv.
  luna, packetry and saturn-v went with facedancer.
- **The `gateware` check is deleted**, not repointed — question 3 answered.
  `socmap` elaborates `gateware/soc/top.py` and is the gateware
  coverage. The `flutter` check went too, with the dashboard.
- **The `.gitignore` `lib/` trap is fixed.** The block is `/lib/` and `/lib64/`,
  anchored, with a comment naming the 36 files it cost. **That bullet above is
  obsolete.**
- `scripts/submodule_patch_audit.py` exists; all four submodules report
  `safe -- every commit is on a remote`.
- #190 landed: `amaranth-soc` / `amaranth-stdio` are declared, and the new
  `amaranthsoc` check fails if the import resolves inside luna-soc's vendored
  tree. That covers half of the cynthion-side complaint here — the shadowing is
  now a red check rather than a silent fallback. It does not remove
  `repos/cynthion`'s luna-soc fork pin, which only goes when the editable install
  does. (`luna_soc` itself is already pip, not a submodule.)

### Corrections to the text above

- **"four platform imports"** → **six** sites import
  `cynthion.gateware.platform.cynthion_r1_4`:
  `scripts/soc_generate_pac.py:138`, `scripts/soc_diagram.py:171`,
  `scripts/bram_patch.py:255`, `gateware/probes/bram_probe/bram_probe.py:192`,
  `gateware/soc/top.py:1705`, and `scripts/phy_probe.py:15` —
  the last of which is not a platform import at all, but
  `cynthion.selftest.registers`, and needs upstream's selftest bitstream to be
  useful. It is a separate decision, not a conversion.
- **Still eight checks, still four touching `repos/`, but a different four:**
  `rust`, `apollo`, `python`, `freethreading`.
- `scripts/sideband_build.py:40` → **43**, and `scripts/fabric_build.py:72` does
  the same `sys.path.insert(0, "repos/apollo")` and was missed.
- Stale comment citations in `top.py`: `176, 181, 277, 312, 1642` →
  `177, 182, 278, 313, 1711`. Line **84** — the `variable_clock` import — is
  unchanged and is still what makes `repos/apollo` load-bearing.

### Two findings that change the plan

**Step 3 blocks step 4, harder than stated.** `variable_clock.py` is ours
(`upstream-boundary.md` records it as a divergence), and `sideband.py` /
`qspi_flash.py` are local additions. A published `apollo-fpga` wheel will not
contain them, so `apollo_fpga` cannot become a pip dependency until the six
gateware modules — **2,534 lines** — are vendored. Also,
`flash_bridge.py` imports `apollo_fpga.ApolloDebugger` at module scope, so
vendoring it as-is does not detach it.

**`glasgow` is an undeclared dependency**, reached through
`apollo_fpga.gateware.qspi_flash` and resolving to a checkout outside the
workspace. Same shape as #190. Filing separately.

### Proposed split

Four things here do not depend on removing a submodule and should not wait for
one:

1. **`firmware/*` is linted by nothing** — this issue's own "done when" bullet 2.
   No `[lints]` table, no clippy invocation, no workspace manifest. Zero
   dependency on `repos/`. The error counts above are months old and need
   re-measuring.
2. **pytest rootdir is the submodule's** — confirmed live in
   `tmp/logs/check-python.log`: the submodule's tests report as
   `tests/test_fpga_adv_uart.py`, colliding with ours. One file to fix, and it
   is a `pytest.ini` rather than a `pyproject.toml`, because `machine_setup.py`
   is deliberately this repo's dependency mechanism.
3. **Vendoring the six `apollo_fpga.gateware` modules** — 2,534 lines, three
   external dependencies, one host-side import to unpick.
4. **`docs/install.md` is stale beyond facedancer** — it documents the pre-`repos/`
   `awto-*` sibling layout and targets `CynthionPlatformRev0D2` throughout; line
   507 tells the reader to `pip install -e repos/facedancer`.

That leaves this issue holding **step 2** (converge the six platform imports onto
`gateware/board/`, no hardware — and
`debris/scripts/platform_vendor_compare.py` is the pin-for-pin equivalence proof,
retired but re-runnable) and **question 1**, which is the only genuine decision
here: `rust` and `apollo` build upstream's firmware and cannot be repointed, so
either the submodules stay for them or this repo stops gating upstream firmware
and leaves that to `upstream_ci.py`.

One caveat on that: **#199 says the `apollo` check is currently measuring the
budget wrong** (`.relocate` missed; both ceilings already breached). Nothing here
should propose weakening the `apollo` check until #199 closes.
