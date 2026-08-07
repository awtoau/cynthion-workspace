# Tracker and documentation audit — 2026-08-07

-

### F1. `origin/hyperram-bist` is 28 commits of unlanded work that redoes today's branch

`git rev-list --count origin/main..origin/hyperram-bist` = **28**, 0 behind, last
commit 2026-08-06, **no PR, no issue**. It contains the *same clock rework* that
`soc-clocks` landed independently today:

| `origin/hyperram-bist` | `soc-clocks` (today) |
|---|---|
| `c09997d hyperram: usb is the oscillator, so sync stops being constrained by it` | `cce87ec soc: take usb off the PLL, and the CPU clock stops being one of three values` |
| `01e675b docs: one clocks file, and the CPU ceiling in it is withdrawn` | `4d23fd2` (on main) |
| `7d3c75f hyperram: a second PLL, so device CK stops being the CPU clock` | — not on this branch |

It also **deletes [`docs/soc-clocking.md`](../../docs/soc-clocking.md)** and replaces it with
`docs/chips/ecp5/clocks.md`, and it **adds [`gateware/soc/hyperram_clocks.py`](../../gateware/soc/hyperram_clocks.py)**,
plus a whole HyperRAM BIST stack that exists nowhere else:

    gateware/soc/hyperram_clocks.py
    gateware/soc/peripherals/hyperram_bist.py
    gateware/soc/peripherals/bist_csr.py
    firmware/cynthion-soc/src/bist.rs
    scripts/bist_sim.py, soc_bist_cdc_sim.py, soc_bist_transport_sim.py
    tests/test_bist_constants.py
    docs/chips/ecp5/clocks.md
    docs/upstream-reproduction.md

**Consequence:** [`gateware/soc/clocks.py:44`](../../gateware/soc/clocks.py#L44) tells the reader to "see
`hyperram_clocks.py`" for the second PLL, and issues **[#228](https://github.com/awtoau/cynthion-workspace/issues/228)** and **[#230](https://github.com/awtoau/cynthion-workspace/issues/230)** both
cite [`gateware/soc/hyperram_clocks.py`](../../gateware/soc/hyperram_clocks.py) as a live path. **That file does not exist
on this branch.** Those issues were written against `hyperram-bist`. Merging the
two branches will conflict in [`gateware/soc/top.py`](../../gateware/soc/top.py), [`docs/soc-clocking.md`](../../docs/soc-clocking.md)
(deleted on one side, stale on the other) and the whole clock story.

This is not a documentation problem — it is two parallel implementations of the
same design decision, and nothing in the tracker records that.

### F2. The "void" HyperRAM figures were only partly deleted, and a partial deletion left two of them *uncaveated*

[`docs/chips/hyperram/w956a8.md:462`](../../docs/chips/hyperram/w956a8.md#L462) asserts:

> **Every DQS speed this workspace has recorded is void, and the tables that held
> them are deleted rather than annotated.** They were quoted, they were wrong, and
> a table with a caveat above it still gets quoted.

That is false in the same file. Commit `ac8a575` touched `w956a8.md` by **6 lines
only** (`git show ac8a575 --stat -- docs/chips/hyperram/w956a8.md` → `4 insertions(+),
2 deletions(-)`), removing one figure. ~30 survive:

* `w956a8.md:141` — `| **highest clock that survives a live negative control** | **CK 140 MHz, 238.9 MB/s** |`
* `w956a8.md:316-318` — the tCSM table, `204.8`, `238.9`, `85.3%`
* `w956a8.md:502-511` — the whole FIFO chunk-size ladder, ten rows, `68.6` → `238.8 MB/s`
* `w956a8.md:538` — `**238.9 MB/s** (334.4 at CK 192 **withdrawn**)` — the exact "restated as withdrawn stays quotable" pattern the doc condemns two hundred lines earlier
* `w956a8.md:549, 566, 575, 581` — `238.9 MB/s` used as a live baseline

Worse, an **earlier** commit `13f2c81` (2026-08-06 10:24, *"docs: the drive-strength
table, from the datasheet rather than from memory"*) deleted the `WITHDRAWN` warning
block, the section heading, the table header row and **5 of 7 data rows** — and left
2 rows behind. The result at `w956a8.md:189-197` is a broken table with no header,
no separator meaning, and no caveat:

    [`scripts/hyperram_ceiling.py`](../../scripts/hyperram_ceiling.py), CPU-free: the pattern is generated and verified in
    gateware, so nothing here goes through the CPU, the cache or `BootRAM`.

    |---|---|---|---|---|
    | 120 | 204.8 | 0 | passed | **pass** |
    | **140** | **238.9** | **0** | **passed** | **pass — the verified ceiling** |

    Write is consistently ~5% above read at every rung; both sit at 85.3% of
    theoretical.

Two void numbers now read as *the verified ceiling*, with the warning removed.
A second orphaned table exists at `w956a8.md:213-214` — header and separator, zero
rows.

### F3. [`docs/soc-clocking.md`](../../docs/soc-clocking.md) leads with a standing finding that today's branch abolished

The file's own summary, `docs/soc-clocking.md:5-7`:

> 1. A **PLL divider bug** silently breaks USB at almost every frequency in
>    60–130 MHz. Only three are safe. **This still holds** and will cost a day if
>    rediscovered.

and `:144-145`:

> Note that only the first -- the three exact-60 PLL solutions -- is a **standing
> result**; the second is a withdrawal.

`gateware/soc/clocks.py:12-16` says the opposite in as many words: that constraint
"is the origin of 'the CPU can only run at three frequencies', and it is a property
of the topology, not of the part", and `:31-33` records that with `usb` on the
oscillator "every integer MHz from 63 to 130 is reachable within 0.5% except eight".

The whole of §1 (`docs/soc-clocking.md:28-73`) is now history, including the
heading `## 1. Only three frequencies in 60..130 MHz can work at all` (`:28`) and
`:47` *"only 60, 100 and 120 land on an exact 60 MHz `usb` clock"*. `:68` also
credits the guard to `variable_clock.py`, which the SoC no longer uses.

Note `origin/hyperram-bist` **deletes this file** (see F1) — so it is stale *and*
contested.

---

## 1. Issues to close

| # | Title | Evidence |
|---|---|---|
| **215** | hyperram: vendor the non-DQS controller too | Delivered by `ac8a575`. [`gateware/soc/peripherals/hyperram_controller.py`](../../gateware/soc/peripherals/hyperram_controller.py) exists with both fixes: tCSHI at `:75,104,120,361` and `fixed_latency` at `:104`. [`docs/chips/hyperram/bist-plan.md:189`](../../docs/chips/hyperram/bist-plan.md#L189) already records `[#215](https://github.com/awtoau/cynthion-workspace/issues/215) … done 2026-08-07`. `docs/upstream-boundary.md:150-151` lists both controllers as ours. |
| **229** | clocks: review every peripheral against the new topology | Delivered by `ac8a575`. [`docs/chips/ecp5/peripheral-clock-audit.md:16`](../../docs/chips/ecp5/peripheral-clock-audit.md#L16) — *"This is the peripheral-by-peripheral review issue [#229](https://github.com/awtoau/cynthion-workspace/issues/229) asked for."* 24 modules, 15 sound, 9 defects, summary table at `:450-458`. **Do not close without first filing the 9 defects** — see §5. |
| **204** | Replace luna's JTAGRegisterInterface | Delivered by `a9ec4be` (newest commit on this branch). [`gateware/probes/jtag_registers.py`](../../gateware/probes/jtag_registers.py) exists, is wire-compatible, and **all applets already import it**: `bist.py:23`, `sideband_gateware.py:34`, `hyperram_regfuzz.py:67`, `usb_serial.py:49`, `bitstream_sink.py:60`, `pin_survey.py:52`, `fusb302_id.py:40`, `i2c_scan.py`. [`scripts/soc_jtag_registers_sim.py`](../../scripts/soc_jtag_registers_sim.py) is the sim. **Caveat:** [`docs/chips/hyperram/bist-plan.md:188`](../../docs/chips/hyperram/bist-plan.md#L188) still lists [#204](https://github.com/awtoau/cynthion-workspace/issues/204) as an unmet precondition — fix that line in the same pass. |

**Twelve more close candidates**, evidence in §6a and §6e:
**[#19](https://github.com/awtoau/cynthion-workspace/issues/19), [#22](https://github.com/awtoau/cynthion-workspace/issues/22), [#63](https://github.com/awtoau/cynthion-workspace/issues/63), [#90](https://github.com/awtoau/cynthion-workspace/issues/90), [#96](https://github.com/awtoau/cynthion-workspace/issues/96)** (done, ≤116) · **[#172](https://github.com/awtoau/cynthion-workspace/issues/172), [#189](https://github.com/awtoau/cynthion-workspace/issues/189), [#190](https://github.com/awtoau/cynthion-workspace/issues/190), [#191](https://github.com/awtoau/cynthion-workspace/issues/191), [#209](https://github.com/awtoau/cynthion-workspace/issues/209), [#211](https://github.com/awtoau/cynthion-workspace/issues/211), [#221](https://github.com/awtoau/cynthion-workspace/issues/221)** (done, >116).

**Three to close as superseded**, §6b and §6e:
**[#178](https://github.com/awtoau/cynthion-workspace/issues/178)** (duplicate of [#115](https://github.com/awtoau/cynthion-workspace/issues/115)) · **[#201](https://github.com/awtoau/cynthion-workspace/issues/201)** (superseded by [`docs/rtic.md`](../../docs/rtic.md) + [#115](https://github.com/awtoau/cynthion-workspace/issues/115)) ·
**[#207](https://github.com/awtoau/cynthion-workspace/issues/207)** (branch merged, sweep superseded — re-file the prior-art research alone).

**Plus [#30](https://github.com/awtoau/cynthion-workspace/issues/30), [#31](https://github.com/awtoau/cynthion-workspace/issues/31), [#34](https://github.com/awtoau/cynthion-workspace/issues/34)** — the Flutter GUI issues, unworkable while the app sits in
`debris/`. Close or convert to one "un-retire the GUI" issue.

Two carry a condition:
* **[#90](https://github.com/awtoau/cynthion-workspace/issues/90)** — strike its "Performance expectations" section first; those figures are
  the void class.
* **[#96](https://github.com/awtoau/cynthion-workspace/issues/96)** — its residue (HyperRAM read-back, USER button, 2 of 3 PHYs) needs a
  successor issue or it is lost.

### Counts

| category | count |
|---|---|
| open issues | 107 |
| **close — work landed** | **15** ([#19](https://github.com/awtoau/cynthion-workspace/issues/19), 22, 63, 90, 96, 172, 189, 190, 191, 204, 209, 211, 215, 221, 229) |
| **close — superseded / duplicate** | **6** ([#30](https://github.com/awtoau/cynthion-workspace/issues/30), 31, 34, 178, 201, 207) |
| **body needs correction** (excluding those being closed) | **55** |
| — of which cite `ecp5-test/` | 14 |
| — of which cite a deleted or moved doc | 22 |
| — of which cite `repos/luna` | 5 |
| — of which are factually wrong, not merely mis-pathed | 16 |
| still real, verified untouched | ~40 |
| marked UNSURE, needs human | 5 ([#54](https://github.com/awtoau/cynthion-workspace/issues/54), [#116](https://github.com/awtoau/cynthion-workspace/issues/116), [#197](https://github.com/awtoau/cynthion-workspace/issues/197), [#223](https://github.com/awtoau/cynthion-workspace/issues/223) pluribus half, [#200](https://github.com/awtoau/cynthion-workspace/issues/200) comment) |
| open PRs, all repos | **0** |
| documents asserting something false | **10 files, ~30 sites** (§4) |
| dead file references inside `docs/` | **~90** |
| unmerged branches with no PR or issue | **2** (28 and 1 commits) |

---

## 2. Issues whose body is stale

**The 55, excluding the 21 recommended for closure:**

> 8, 9, 10, 11, 24, 53, 54, 73, 81, 83, 84, 86, 87, 89, 93, 95, 97, 100, 105,
> 107, 108, 110, 115, 116, 125, 142, 143, 145, 153, 157, 162, 165, 169, 173,
> 175, 176, 179, 180, 182, 183, 184, 185, 193, 194, 200, 202, 206, 217, 218,
> 222, 223, 224, 225, 228, 230

Eight more ([#31](https://github.com/awtoau/cynthion-workspace/issues/31), 63, 90, 172, 204, 207, 209, 211) are also stale but are being
closed, so only [#90](https://github.com/awtoau/cynthion-workspace/issues/90)'s void-figures section needs an edit first.

Details for numbers >116 are in §6c, for ≤116 in §6e. The bulk categories follow.

### 2a. Issues citing `ecp5-test/` (the directory does not exist; it is `gateware/`)

Mechanically derived — every one of these has at least one file reference that
resolves to nothing in the tree:

| # | dead reference | correct path |
|---|---|---|
| 97 | `ecp5-test/pins/pin_survey.py` | [`gateware/probes/pins/pin_survey.py`](../../gateware/probes/pins/pin_survey.py) |
| 107 | `ecp5-test/CYNTHION_R14_PINMAP.md`, `docs/luna_ecp5_fpga/jtag-ceiling-reached.md`, `scripts/jtag_isr_soak.py` | first is gone entirely; check [`docs/hardware.md`](../../docs/hardware.md) |
| 110 | `ecp5-test/fabric/FABRIC_TEST.md`, `ecp5-test/riscv/vexii_cpu.py`, `scripts/hyperram_ladder.py` | [`gateware/soc/cpu/cpu.py`](../../gateware/soc/cpu/cpu.py); the other two are gone |
| 142 | `ecp5-test/riscv/i2c_master.py` | [`gateware/soc/peripherals/i2c_master.py`](../../gateware/soc/peripherals/i2c_master.py) |
| 153 | `ecp5-test/bist.py`, `ecp5-test/pins/pin_survey.py` | [`gateware/probes/bist.py`](../../gateware/probes/bist.py), [`gateware/probes/pins/pin_survey.py`](../../gateware/probes/pins/pin_survey.py) |
| 169 | `ecp5-test/bram_probe/bram_probe.py`, `ecp5-test/riscv/vexii_hello_soc.py`, `scripts/61_run_profile.py`, `scripts/phy_probe.py` | [`gateware/probes/bram_probe/bram_probe.py`](../../gateware/probes/bram_probe/bram_probe.py), [`gateware/soc/top.py`](../../gateware/soc/top.py); last two gone |
| 172 | `ecp5-test/riscv/vexii_hello_soc.py` | [`gateware/soc/top.py`](../../gateware/soc/top.py) — **and the premise is dead, see §2b** |
| 176 | `ecp5-test/riscv/sideband_csr.py`, `ecp5-test/sideband_link.py` | [`gateware/soc/peripherals/sideband_csr.py`](../../gateware/soc/peripherals/sideband_csr.py), [`gateware/probes/sideband/sideband_link.py`](../../gateware/probes/sideband/sideband_link.py) |
| 202 | `ecp5-test/riscv/jtag_stage.py`, `ecp5-test/riscv/vexii_bootram.py` | [`gateware/soc/bus/jtag_stage.py`](../../gateware/soc/bus/jtag_stage.py), [`gateware/soc/bootram.py`](../../gateware/soc/bootram.py) |
| 204 | `ecp5-test/bist.py`, `ecp5-test/riscv/jtag_stage.py` | [`gateware/probes/bist.py`](../../gateware/probes/bist.py), [`gateware/soc/bus/jtag_stage.py`](../../gateware/soc/bus/jtag_stage.py) — moot if closed |
| 206 | `ecp5-test/riscv/hyperram_dqs_phy.py` | [`gateware/soc/peripherals/hyperram_dqs_phy.py`](../../gateware/soc/peripherals/hyperram_dqs_phy.py) |
| 211 | `ecp5-test/riscv/vexii_hello_soc.py` | [`gateware/soc/top.py`](../../gateware/soc/top.py) |

### 2b. Issues stale on the *clock* facts, not just paths

* **[#172](https://github.com/awtoau/cynthion-workspace/issues/172)** — *"clock: SYNC_MHZ is constrained by the PLL, not by the ULPI PHY — and the elastic buffers are already there"*. The premise is now resolved in the code: [`gateware/soc/clocks.py`](../../gateware/soc/clocks.py) takes `usb` off the PLL entirely, so `sync` is no longer constrained by anything. Either close it as delivered by `cce87ec` or rewrite it to be about the *second* PLL (which is F1's territory).
* **[#211](https://github.com/awtoau/cynthion-workspace/issues/211)** — *"soc: SYNC_MHZ=30 and HYPERRAM_DQS=False are debug values that shipped"*. Still real, but the file moved ([`gateware/soc/top.py`](../../gateware/soc/top.py)) and `aed8127` changed how the clock is reported (measured, not declared), which changes the symptom the issue describes.
* **[#228](https://github.com/awtoau/cynthion-workspace/issues/228), [#230](https://github.com/awtoau/cynthion-workspace/issues/230)** — both cite [`gateware/soc/hyperram_clocks.py`](../../gateware/soc/hyperram_clocks.py), which exists **only on `origin/hyperram-bist`**. Not stale text so much as issues written against a different branch. See F1.

### 2c. Issues citing `repos/luna/` (the submodule was removed)

`.gitmodules` lists only `repos/cynthion`, `repos/apollo`, `repos/cynthion-hardware`,
`repos/vexiiriscv`. `repos/luna` is gone.

* **[#83](https://github.com/awtoau/cynthion-workspace/issues/83)** — `repos/luna/pyproject.toml`
* **[#125](https://github.com/awtoau/cynthion-workspace/issues/125)** — `repos/luna/luna/gateware/interface/ulpi.py`

`luna` is still a Python import dependency (`from luna.gateware...` in eight probe
files), so these issues are still *real*; only the path is wrong. Issue **[#222](https://github.com/awtoau/cynthion-workspace/issues/222)**
("luna: what still depends on it") is the right place to record that.

### 2d. Issues citing docs that were retired to `debris/`

* **[#179](https://github.com/awtoau/cynthion-workspace/issues/179), [#180](https://github.com/awtoau/cynthion-workspace/issues/180), [#182](https://github.com/awtoau/cynthion-workspace/issues/182), [#183](https://github.com/awtoau/cynthion-workspace/issues/183)** all cite `docs/sideband-review.md`. It is now
  [`debris/docs/sideband-review.md`](../../debris/docs/sideband-review.md). The live document is
  [`docs/chips/cynone-sideband.md`](../../docs/chips/cynone-sideband.md), which [`docs/README.md`](../../docs/README.md) explicitly names as the
  single owner of the FPGA_ADV subject.
* **[#184](https://github.com/awtoau/cynthion-workspace/issues/184)** cites `docs/sideband.md` — same.
* **[#185](https://github.com/awtoau/cynthion-workspace/issues/185)** cites `docs/hyperram-bursts.md`; **[#207](https://github.com/awtoau/cynthion-workspace/issues/207)** cites `docs/hyperram-implementations.md`;
  **[#90](https://github.com/awtoau/cynthion-workspace/issues/90)** cites `docs/luna_ecp5_fpga/hyperram-implementation-survey.md` and
  `…/memory-interface-options.md`; **[#173](https://github.com/awtoau/cynthion-workspace/issues/173)** cites `docs/memory-speed-options.md`.
  All four are gone. The surviving HyperRAM documents are
  [`docs/chips/hyperram/w956a8.md`](../../docs/chips/hyperram/w956a8.md), [`docs/chips/hyperram/bist-plan.md`](../../docs/chips/hyperram/bist-plan.md),
  [`docs/chips/hyperram/README.md`](../../docs/chips/hyperram/README.md).
* **[#89](https://github.com/awtoau/cynthion-workspace/issues/89)** cites `docs/luna_ecp5_fpga/spi-flash-summary.md` → [`debris/docs/spi-flash-summary.md`](../../debris/docs/spi-flash-summary.md);
  live doc is [`docs/chips/w25q32-config-flash.md`](../../docs/chips/w25q32-config-flash.md).
* **[#108](https://github.com/awtoau/cynthion-workspace/issues/108), [#225](https://github.com/awtoau/cynthion-workspace/issues/225)** cite `docs/luna_ecp5_fpga/fast-bitstream-loading.md` → `debris/docs/`.
* **[#217](https://github.com/awtoau/cynthion-workspace/issues/217), [#218](https://github.com/awtoau/cynthion-workspace/issues/218)** cite `docs/decisions.md` — does not exist; [`docs/architecture.md`](../../docs/architecture.md)
  is what [`docs/README.md:38`](../../docs/README.md#L38) says holds open decisions.
* **[#53](https://github.com/awtoau/cynthion-workspace/issues/53)** cites `docs/apollo_dfu_buffer_analysis.md`; it is
  [`docs/apollo_samd11_mcu/apollo_dfu_buffer_analysis.md`](../../docs/apollo_samd11_mcu/apollo_dfu_buffer_analysis.md).
* **[#54](https://github.com/awtoau/cynthion-workspace/issues/54)** cites `docs/apollo_race_conditions.md` — gone, no successor found. **UNSURE.**
* **[#145](https://github.com/awtoau/cynthion-workspace/issues/145)** cites `docs/usb-host-full-speed.md`; **[#116](https://github.com/awtoau/cynthion-workspace/issues/116)/#224** cite `docs/fabric-test.md`;
  **[#162](https://github.com/awtoau/cynthion-workspace/issues/162)** cites `docs/linux-on-cynthion.md` (deleted by `c01ff9b`, now the
  `linux-on-cynthion/` directory); **[#209](https://github.com/awtoau/cynthion-workspace/issues/209)** cites
  `docs/moondancer/silent-soc-investigation.md`; **[#223](https://github.com/awtoau/cynthion-workspace/issues/223)** cites
  `docs/chips/w956a8-hyperram.md` (renamed to [`docs/chips/hyperram/w956a8.md`](../../docs/chips/hyperram/w956a8.md) by
  `398a6db`), `docs/ecp5/diamond-oracle.md` and `docs/ecp5/ecp5-primitive-coverage.md`.

### 2e. Issues citing scripts that no longer exist

**[#116](https://github.com/awtoau/cynthion-workspace/issues/116)** is the most affected: `scripts/fabric_coverage_plan.py`,
`fabric_test_bridge.py`, `fabric_test_gen.py` and `docs/fabric-test.md` are all
absent — **because they live only on `origin/codex/issues-101-116`**, a branch 1
ahead / 203 behind `main`, abandoned 2026-08-03. See §5.

Others: **[#24](https://github.com/awtoau/cynthion-workspace/issues/24)** (`scripts/decode-crash.py`, `dump-crash.py`, `monitor-apollo.py`),
**[#31](https://github.com/awtoau/cynthion-workspace/issues/31)** (`scripts/extract-hardware.py`), **[#84](https://github.com/awtoau/cynthion-workspace/issues/84)** (`scripts/power_probe.py`),
**[#110](https://github.com/awtoau/cynthion-workspace/issues/110)** (`scripts/hyperram_ladder.py`), **[#175](https://github.com/awtoau/cynthion-workspace/issues/175)** (`scripts/checks.py` — it is
[`scripts/check.py`](../../scripts/check.py)), **[#194](https://github.com/awtoau/cynthion-workspace/issues/194)** (`scripts/patch_amaranth_soc_annotations.py`),
**[#225](https://github.com/awtoau/cynthion-workspace/issues/225)** (`scripts/ecp5_analyze.py`, `ecp5_opcodes.py`, `ecp5_verify_reads.py`,
`nextpnr_allow_fail_ladder.py`, `profile_shared.py`).

### 2f. Apollo-firmware issues using repo-root-relative paths for submodule files

**[#63](https://github.com/awtoau/cynthion-workspace/issues/63)** (`firmware/src/jtag.c`), **[#179](https://github.com/awtoau/cynthion-workspace/issues/179)** (`firmware/src/vendor.c`), **[#73](https://github.com/awtoau/cynthion-workspace/issues/73)**
(`tests/test_hardware.py`). These resolve under `repos/apollo/`, not the workspace
root. Low severity, but they read as dead links.

---

## 3. Open PRs

**There are none.** Checked with `gh pr list --state open`:

| repo | open PRs |
|---|---|
| `awtoau/cynthion-workspace` | 0 |
| `awtoau/awto-cynthion` (fork) | 0 |
| `awtoau/awto-apollo` (fork) | 0 |
| `awtoau/awto-luna` (fork) | 0 |
| `awtoau/awto-luna-soc` | 0 |
| `awtoau/pluribus` | 0 |
| `awtoau/awto-facedancer` (fork) | 0 |

Note the repo is `awtoau/awto-apollo`, not `awtoau/apollo`.

**What exists instead of PRs is unmerged branches** — see §5.

---

## 4. Documents asserting something false

| file:line | the stale text | why it is false |
|---|---|---|
| `docs/soc-clocking.md:5-7` | *"Only three are safe. **This still holds** and will cost a day if rediscovered."* | `gateware/soc/clocks.py:31-33`: 63–130 MHz all reachable within 0.5% bar eight values |
| [`docs/soc-clocking.md:28`](../../docs/soc-clocking.md#L28) | heading *"## 1. Only three frequencies in 60..130 MHz can work at all"* | same |
| `docs/soc-clocking.md:47-48` | *"**only 60, 100 and 120 land on an exact 60 MHz `usb` clock.** Every other value ships a PHY clock that is wrong by 1-5%."* | `usb` is now the A8 oscillator passed through, `clocks.py:264-266` |
| `docs/soc-clocking.md:68-69` | *"`variable_clock.py` now **refuses to build** outside 0.5%"* | the SoC no longer instantiates `VariableClockDomainGenerator`; `SocClocks` raises instead (`clocks.py:129-134`) |
| `docs/soc-clocking.md:144-145` | *"only the first -- the three exact-60 PLL solutions -- is a **standing result**"* | it is now a withdrawal too |
| `docs/soc-clocking.md:83, 149-151` | `./scripts/nextpnr_allow_fail_ladder.py`, `./scripts/riscv_verify_bitstream.py`, `./scripts/diamond_riscv_ladder.py` | none of the three exist |
| `docs/chips/hyperram/w956a8.md:165-169` | *"`VariableClockDomainGenerator` narrows it further … leaving **60, 100 and 120** as the only `sync` values below 130"* | same as above |
| `docs/chips/hyperram/w956a8.md:178-180` | *"Anything with a ULPI must keep using `VariableClockDomainGenerator` — a wrong `usb` presents as a dead board"* | `SocClocks` serves exactly that case, better; `clocks.py:19-25` |
| `docs/chips/hyperram/w956a8.md:141,192-197,316-318,502-511,538,549,566,575,581` | live MB/s figures | contradicted by `w956a8.md:462` in the same file — see **F2** |
| [`docs/chips/hyperram/w956a8.md:192`](../../docs/chips/hyperram/w956a8.md#L192) | orphaned `\|---\|---\|---\|---\|---\|` with no header row | table mangled by `13f2c81` |
| `docs/chips/hyperram/w956a8.md:213-214` | header + separator, **zero rows** | same commit |
| `docs/chips/hyperram/w956a8.md:457-458` | `scripts/hyperram_ladder.py`, `scripts/fetch_winbond_hyperram.py` | neither exists |
| [`docs/chips/hyperram/w956a8.md:452`](../../docs/chips/hyperram/w956a8.md#L452) | `gateware/probes/hyperram/hyperram_dqs_top.py` | does not exist |
| [`docs/architecture.md:31`](../../docs/architecture.md#L31) | `\| clocks \| VariableClockDomainGenerator \| written \| soc-clocking.md, [#111](https://github.com/awtoau/cynthion-workspace/issues/111) \|` | it is `SocClocks` ([`gateware/soc/clocks.py`](../../gateware/soc/clocks.py)); [#111](https://github.com/awtoau/cynthion-workspace/issues/111) is CLOSED |
| [`docs/upstream-boundary.md:76`](../../docs/upstream-boundary.md#L76) | *"Ours solves `sync` and `usb` together so `usb` lands on exactly 60 MHz. [#111](https://github.com/awtoau/cynthion-workspace/issues/111)"* | no longer how it works; and the cited path `repos/apollo/apollo_fpga/gateware/variable_clock.py` is no longer what the SoC builds |
| `docs/chips/ecp5/lfe5u-12f.md:72-77` | *"The PLL is driven by `VariableClockDomainGenerator` … Ours solves for `sync` **and** `usb` together"* | same |
| [`docs/usb-host-options.md:612`](../../docs/usb-host-options.md#L612) | *"Clocking is `VariableClockDomainGenerator(sync_mhz=60)`"* | same |
| `docs/upstream-boundary.md:174-175` | *"It **used to be** held outside the controller by `hyperram_ceiling_top.py`"* | past tense is wrong — it **still is**, deliberately and in addition to the controller: `hyperram_ceiling_top.py:583` computes `recovery_cycles`, `:731,769,890` gate on it, and `:190-195` says the double-hold is intentional. tCSHI is now paid twice in that harness. Flagged as a doc contradiction, **not** as a code bug. |
| `docs/README.md:38-39` | *"[`architecture.md`] is what the system is made of; [`architecture.md`] is what is still open."* | the same link twice — the sentence is broken; one of them was meant to be a different file |
| [`docs/README.md`](../../docs/README.md) §"Moondancer / the SoC" | **empty section**, no entries | |
| [`docs/README.md`](../../docs/README.md) index | 25 `.md` files under `docs/` are not indexed, despite the file opening *"Index of every file under `docs/`"*. Most are `docs/drafts/**` (23), plus **[`docs/chips/hyperram/bist-plan.md`](../../docs/chips/hyperram/bist-plan.md)** (added today) and **[`docs/soc-memory-bus.md`](../../docs/soc-memory-bus.md)** | |
| [`docs/toolchain-simplification.md:381`](../../docs/toolchain-simplification.md#L381) | *"Nothing in `gateware/` does, except `riscv/vexii_hello_soc.py`"* | [`gateware/soc/top.py`](../../gateware/soc/top.py) |
| [`docs/soc-memory-bus.md:218`](../../docs/soc-memory-bus.md#L218) | `(vexii_hello_soc.py:689)` | [`gateware/soc/top.py`](../../gateware/soc/top.py) |
| `docs/usb-host-options.md:683,689,765,806,939` | five `vexii_hello_soc.py:NNN` line citations | [`gateware/soc/top.py`](../../gateware/soc/top.py), and the line numbers will not have survived the move |
| [`docs/drafts/gsg-scenarios-master.md:32`](../../docs/drafts/gsg-scenarios-master.md#L32) | `vexii_hello_soc.py:1175` | same |

There are **~90 further dead file references across `docs/`** (backtick-quoted
paths that resolve to nothing). The worst offenders by count:
`scripts/soc_timing_sweep.py` (6 sites), `gateware/probes/i2c/multiplexed.py` (4 —
deleted by `fe7d0bf`), `scripts/flash_capacity_probe.py` (4),
`scripts/usb-host-area.py` (3), `scripts/patch_amaranth_soc_annotations.py` (3).
Full list reproducible with the script in §7.

**Documents that are correct and current** (checked, no action): 
[`docs/chips/ecp5/peripheral-clock-audit.md`](../../docs/chips/ecp5/peripheral-clock-audit.md) — it names the stale prose elsewhere
in the tree at `:433-448` rather than adding to it; [`docs/upstream-boundary.md`](../../docs/upstream-boundary.md)
§HyperRAM controllers (`:145-180`) apart from the one past-tense line above;
`README.md` at the repo root; [`docs/chips/hyperram/bist-plan.md`](../../docs/chips/hyperram/bist-plan.md) apart from its
`[#204](https://github.com/awtoau/cynthion-workspace/issues/204)` precondition line.

---

## 5. Nobody is tracking these

1. **`origin/hyperram-bist` — 28 commits, no PR, no issue.** See **F1**. This is
   the single largest untracked thing in the repo, and it collides head-on with
   `soc-clocks`. The longer both live, the worse the merge.

2. **`origin/codex/issues-101-116` — abandoned, and it holds the only copy of the
   fabric coverage suite.** 1 ahead / 203 behind `main`, last touched 2026-08-03,
   commit message *"fabric: the coverage suite as codex left it, committed before
   /tmp took it"*. It carries [`scripts/fabric_arcs.py`](../../scripts/fabric_arcs.py), `fabric_build.py`,
   `fabric_golden.py`, `fabric_placement.py`, `fabric_run.py`, `fabric_sim.py`,
   `fabric_sweep.py`, `tests/test_fabric_coverage.py`. Issues **[#116](https://github.com/awtoau/cynthion-workspace/issues/116)** and **[#224](https://github.com/awtoau/cynthion-workspace/issues/224)**
   depend on that work and reference those paths as if they were on `main`.
   It is also 203 commits behind and still edits `ecp5-test/fabric/`, so a plain
   merge will not apply.

3. **`origin/chore/retire-flutter-and-facedancer` — 0 ahead of `main`.** Fully
   merged; safe to delete. Pure noise in `git branch -a`.

4. **The 9 defects from the peripheral clock audit exist only in a document.**
   `docs/chips/ecp5/peripheral-clock-audit.md:450-458` — 9 defects, 4 live today
   (1, 2, 3, 4), 5 latent. Defect 1 is called *"the most serious finding"*
   (`:62`): [`gateware/soc/peripherals/ulpi_window.py:231`](../../gateware/soc/peripherals/ulpi_window.py#L231) passes a bare `Value`
   to `ResetInserter`, which Amaranth reads as `{"sync": …}`, so the timeout reset
   its own docstring calls *"the only way back to IDLE"* reaches no logic at all,
   in a module that is entirely `usb`. **There is no issue for any of the nine.**
   Closing [#229](https://github.com/awtoau/cynthion-workspace/issues/229) without filing them loses all nine — which is exactly the failure
   `docs/README.md:26-28` warns about (*"A closed issue is not documentation"*).

5. **[`docs/soc-clocking.md`](../../docs/soc-clocking.md) is deleted on one branch and stale on the other.**
   No issue records which version wins.

6. **107 open issues, 0 open PRs, and 17 unpushed commits on `soc-clocks`.**
   `git rev-parse --abbrev-ref @{u}` → *no upstream configured*. Local `main` is
   also 2 commits ahead of `origin/main`. Today's entire body of work exists on
   one machine.

7. **Uncommitted change in the tree:** [`scripts/soc_jtag_stage_sim.py`](../../scripts/soc_jtag_stage_sim.py) is modified
   and unstaged.

8. **`docs/drafts/upstream/pr-1..pr-6`** are six drafts addressed to upstream GSG.
   `ac8a575`'s message says two of them *"would have published"* void figures and
   were corrected. They are not indexed in [`docs/README.md`](../../docs/README.md) and no issue tracks
   whether any were ever sent. [`docs/drafts/upstream/cynthion-147-comment.md`](../../docs/drafts/upstream/cynthion-147-comment.md)
   relates to **[#200](https://github.com/awtoau/cynthion-workspace/issues/200)** (*"unblock cynthion#147"*), which is open — **UNSURE**
   whether the comment was posted.

9. **`docs/patchset/`** (3 files) and **`docs/apollo/pending-patches/`** (3 patches
   + README) describe patches whose applied/unapplied state is not recorded in any
   open issue. The memory index notes a "UART-DMA patch unapplied" from 28 Jul;
   `docs/apollo/code-test/apollo-uart-dma.patch` is still sitting there. **UNSURE**
   whether it has since been applied.

---

## 6. Per-issue triage

Three independent passes, each verifying against the tree rather than the issue
text. Spot-checked: [`gateware/board/core.py:82`](../../gateware/board/core.py#L82), [`gateware/soc/top.py:567`](../../gateware/soc/top.py#L567),
[`docs/rtic.md:14`](../../docs/rtic.md#L14), and the existence of every script cited as evidence.

### 6a. Close — work has landed

| # | Title | Evidence |
|---|---|---|
| **172** | clock: SYNC_MHZ is constrained by the PLL, not the ULPI PHY | `cce87ec`. [`gateware/soc/clocks.py:101`](../../gateware/soc/clocks.py#L101) `class SocClocks`; `:225` `ClockSignal("usb").eq(osc)`; `:29-32` "every integer MHz from 63 to 130 is reachable within 0.5% except eight". The issue's premise is resolved. |
| **189** | hyperram: all the test code in one place | `398a6db`, `aae83fb`. [`scripts/hyperram_harnesses.py`](../../scripts/hyperram_harnesses.py) is the one door (`./dev.py hyperram`); `gateware/probes/hyperram/README.md:1-25` states each harness and resolves all three alleged duplicates (runner/top pairs, not copies). |
| **190** | toolchain: amaranth-soc is undeclared | `scripts/machine_setup.py:100-105` declares `amaranth-soc`/`amaranth-stdio` at pinned commits; [`scripts/amaranth_soc_check.py:3`](../../scripts/amaranth_soc_check.py#L3) names [#190](https://github.com/awtoau/cynthion-workspace/issues/190) and is wired into `dev.py lint`. |
| **191** | toolchain: the ARM binutils on PATH shadow the flash-budget guards | [`scripts/arm_binutils_resolve.py`](../../scripts/arm_binutils_resolve.py) exists; all three guards use it — `verify_vectors.py:24,83,87`, `apollo_budget_check.py:46,69,92,100`, `apollo_memory_report.py:47,64,87,118,142` — and each calls `.report()`. |
| **204** | Replace luna's JTAGRegisterInterface | `a9ec4be`. [`gateware/probes/jtag_registers.py`](../../gateware/probes/jtag_registers.py), TCK-clocked, wire-compatible; adopted by all 9 applets. The only remaining luna JTAG import is [`scripts/soc_jtag_registers_sim.py:101`](../../scripts/soc_jtag_registers_sim.py#L101), deliberately, as the negative control. |
| **209** | console: nothing is queued on bulk endpoint 0x81 | Premise inverted. The console **is** CDC-ACM on a tty: `gateware/soc/top.py:3,19-20`, [`gateware/usb_ids.py:66`](../../gateware/usb_ids.py#L66), `scripts/tio_user.py:53-60`. The issue's claim *"no ttyACM node belongs to this SoC"* is false against the tree. |
| **211** | soc: SYNC_MHZ=30 and HYPERRAM_DQS=False are debug values that shipped | [`gateware/soc/top.py:567`](../../gateware/soc/top.py#L567) `SYNC_MHZ = 60` with ~15 lines of justification at `:545-566`; `:636` `HYPERRAM_DQS = False` with rationale at `:625-635`; `:649` `HYPERRAM_CLOCK_STOP = False` likewise at `:639-648`. Meets its own "Done when". |
| **215** | hyperram: vendor the non-DQS controller too | `ac8a575`. See §1. |
| **221** | platform: CynthionPlatformRev1D4 still inherits LUNAPlatform | [`gateware/board/core.py:82`](../../gateware/board/core.py#L82) — `class CynthionPlatform(LatticeECP5Platform)`. LUNA chain removed; rationale at `core.py:9-79`. |
| **229** | clocks: review every peripheral against the new topology | See §1 — **file the 9 defects first**. |

### 6b. Close as superseded / duplicate

| # | Superseded by |
|---|---|
| **178** | Body is an unedited GitHub template stub. The question is answered by `docs/rtic.md:1-30` and owned by open **[#115](https://github.com/awtoau/cynthion-workspace/issues/115)**. Duplicate of [#115](https://github.com/awtoau/cynthion-workspace/issues/115). |
| **201** | [`docs/rtic.md:3`](../../docs/rtic.md#L3) — *"**RTIC is decided** … This is the evidence, not a re-argument"* (`427af22`), plus [#115](https://github.com/awtoau/cynthion-workspace/issues/115). Also numerically stale: issue says 1,266 µs / 700-of-2000 missed; `docs/rtic.md:14-17` records **1,220 µs** and **600 / 2,000**. |
| **207** | Three parts, all overtaken: `rtic-workload-port` **merged** (`90f40e0`, commits reachable from `main`, branch gone); the gateware sweep superseded by [`docs/chips/hyperram/bist-plan.md`](../../docs/chips/hyperram/bist-plan.md) and [#230](https://github.com/awtoau/cynthion-workspace/issues/230). **Only the prior-art research is live** — re-file that alone. |

### 6c. Correct the body, keep the issue

Beyond the path fixes in §2, these have stale *substance*:

| # | verbatim stale text | correction |
|---|---|---|
| **143** | table row `W956A8 HyperRAM \| 166 MHz \| **192 MHz CK**, 334.4 MB/s` | withdrawn by `ac8a575`; [`docs/chips/hyperram/w956a8.md:538`](../../docs/chips/hyperram/w956a8.md#L538). The W25Q32 row still holds. |
| **157** | *"The fix is an import/subprocess-aware check and is the main remaining piece of tooling work."* | **done** — `scripts/audit_scripts.py:229-244` is now AST-based (`ast.parse`, `Import`/`ImportFrom` walk) plus a DANGLING check at `:384`. Also *"58 files still sit in one flat namespace"* → **103**. Item 4 also looks done (`scripts/soc_run.py:111-123`). Still real: log retention, `scripts/` grouping, `./dev.py ci` fmt-check (`scripts/dev.py:122-126` calls it "a live debt"). |
| **162** | *"[`firmware/cynthion-soc/memory.x`](../../firmware/cynthion-soc/memory.x) puts everything in block RAM, deliberately"*, and `8532 free of 64512` | stage two landed: `memory.x:47-50` `FLASH : ORIGIN = 0x100B0000`, `REGION_ALIAS("REGION_TEXT", FLASH)`. Still real: `.data`/`.bss`/stack remain in `RAM … LENGTH = 63K`. |
| **165** | the 43-file breakdown, *"`ecp5-test/riscv/` (8)"*, *"`scripts/` (13)"* | `gateware/soc/`; `scripts/` is now 103 files — the whole count needs re-deriving. |
| **169** | *"`repos/` still carries eight submodules"* | `.gitmodules` has **four**. Also: the `gateware` check ("~98% of wall time") was deleted from [`scripts/check.py`](../../scripts/check.py); `socmap` elaborates [`gateware/soc/top.py`](../../gateware/soc/top.py); `.gitignore:17` already fixed to anchored `/lib/`; line cites `vexii_hello_soc.py:176,181,277,312,1642` → `gateware/soc/top.py:177,182,278,313,1711`. A close-out draft already exists in-tree at [`docs/drafts/169-closeout.md`](../../docs/drafts/169-closeout.md). |
| **173** | *"part burst rate (`docs/memory-speed-options.md`) \| **334.4 MB/s**"* — the 25× arithmetic rests on it | file gone, figure withdrawn. Item 1 partly answered: [`gateware/soc/top.py:1108`](../../gateware/soc/top.py#L1108) `ck_mhz=2 * SYNC_MHZ if HYPERRAM_DQS else SYNC_MHZ` with DQS off → CK is 60, not 192, which is most of the gap. |
| **175** | proposal 0 *"a `scripts/checks.py`"*; proposal 3 *"fast tier, under 2 seconds … nine cheapest simulations"* | harness is [`scripts/sim_check_harness.py`](../../scripts/sim_check_harness.py). Proposal 3 was **deliberately rejected**: `scripts/soc_sims.py:91-103` — *"That is tiering on cost, and it gets both ends wrong… So there is no list of names here."* Tiers are `once`/`soak`, 17 sims. Proposals 1 and 2 done. Still real: 4 and 5. |
| **185** | *"See `docs/hyperram-bursts.md` for the mechanism"* | gone; mechanism is `gateware/soc/bootram.py:215-241`. Note option 1 (`ClockStopPHY`) is now editable in-tree since `ac8a575` vendored the controller. |
| **193** | — | [`docs/upstreamable-patches.md:18`](../../docs/upstreamable-patches.md#L18) still says *"20 commits ahead"*; the correction [#193](https://github.com/awtoau/cynthion-workspace/issues/193) asks for has not been made. |
| **200** | *"Create real forks — `awtoau/*` are independent repos, not GitHub forks, so a PR cannot be opened from them."* | **stale**: `awto-luna`, `awto-apollo`, `awto-cynthion` are all `isFork=true` today. Only `awto-luna-soc` is not. Steps 2–4 still real — no PRs exist against `greatscottgadgets/*`. |
| **202** | `ecp5-test/riscv/vexii_bootram.py`, `…/jtag_stage.py` | [`gateware/soc/bootram.py`](../../gateware/soc/bootram.py), [`gateware/soc/bus/jtag_stage.py`](../../gateware/soc/bus/jtag_stage.py). Defect unchanged: `bootram.py:289,342,407,752`. |
| **206** | `ecp5-test/riscv/hyperram_dqs_phy.py` and the quoted phase block | [`gateware/soc/peripherals/hyperram_dqs_phy.py:385`](../../gateware/soc/peripherals/hyperram_dqs_phy.py#L385). Substance holds — `swap_halves` is gone from gateware, so the convention is still unstated. |
| **222** | table rows *"`luna.gateware.interface.jtag` \| 18"* and *"`board/` inherits `LUNAApolloPlatform`"*; problem 4 | jtag is now effectively **0** (see [#204](https://github.com/awtoau/cynthion-workspace/issues/204)); problems 2 and 4 are **done** ([#221](https://github.com/awtoau/cynthion-workspace/issues/221), [#215](https://github.com/awtoau/cynthion-workspace/issues/215)). Real counts today: `architecture.car` 21 files, `interface.psram` 14, `luna_soc…spiflash` 16, `interface.i2c` 6, `debug.ila` 1. Remaining substance is problem 1 (fork pin, [#194](https://github.com/awtoau/cynthion-workspace/issues/194)) and problem 3 (spiflash). **Rewrite rather than close.** |
| **223** | `docs/chips/w956a8-hyperram.md:285` | [`docs/chips/hyperram/w956a8.md:285`](../../docs/chips/hyperram/w956a8.md#L285) (renamed by `398a6db`). Claim 1 still holds there; claim 2's evidence intact at [`gateware/probes/fabric/fabric_gateware.py:432`](../../gateware/probes/fabric/fabric_gateware.py#L432). The pluribus claims are in another repo — **UNSURE, needs human**. |
| **228** | *"[`gateware/soc/clocks.py`](../../gateware/soc/clocks.py), [`gateware/soc/hyperram_clocks.py`](../../gateware/soc/hyperram_clocks.py), and `variable_clock.py`"* | `hyperram_clocks.py` does not exist here (see **F1**); `variable_clock.py` is only at `repos/apollo/apollo_fpga/gateware/`. Substance holds: `clocks.py:215-216` still ties `PHASESEL0/1`, `PHASEDIR`, `PHASESTEP`, `PHASELOADREG` off. Cites [#210](https://github.com/awtoau/cynthion-workspace/issues/210) and [#226](https://github.com/awtoau/cynthion-workspace/issues/226) as motivation — **both CLOSED**. |
| **230** | *"[`gateware/soc/hyperram_clocks.py`](../../gateware/soc/hyperram_clocks.py) is written and has never been instantiated by `top.py`"* | that file does not exist on this branch (see **F1**); only a stale `gateware/soc/__pycache__/hyperram_clocks.cpython-315.pyc` remains. Also *"Today CK derives from `sync`, so moving CK moves the CPU clock"* needs restating. Preconditions **[#204](https://github.com/awtoau/cynthion-workspace/issues/204) and [#215](https://github.com/awtoau/cynthion-workspace/issues/215) are now cleared**; [#186](https://github.com/awtoau/cynthion-workspace/issues/186) and BURSTDET remain. |

### 6d. Still real, verified untouched

**[#125](https://github.com/awtoau/cynthion-workspace/issues/125)** (no RX-CMD peripheral; `grep -rln "ULPIRxEventDecoder\|rx_event"` over `gateware/ firmware/ scripts/` → nothing) ·
**[#142](https://github.com/awtoau/cynthion-workspace/issues/142)** ([`gateware/probes/pins/i2c_scan.py`](../../gateware/probes/pins/i2c_scan.py) scans addresses, not rates) ·
**[#145](https://github.com/awtoau/cynthion-workspace/issues/145)** (no serial-mode bit; `ulpi_window.py` has no reg-`07h` path) ·
**[#153](https://github.com/awtoau/cynthion-workspace/issues/153)** ([`gateware/probes/bist.py`](../../gateware/probes/bist.py) has no VBUS coverage; `pin_survey.py:242-255` still deliberately does not drive the enables) ·
**[#159](https://github.com/awtoau/cynthion-workspace/issues/159)** (`firmware/cynthion-soc/src/typec.rs:234-255` — `poll()` reads only the fault line off a CSR, never re-reads over I²C) ·
**[#160](https://github.com/awtoau/cynthion-workspace/issues/160)** (no `TARGET_FS_MONITOR`/chirp resource anywhere) ·
**[#171](https://github.com/awtoau/cynthion-workspace/issues/171)** (no line-editor crate in [`firmware/cynthion-soc/Cargo.toml`](../../firmware/cynthion-soc/Cargo.toml)) ·
**[#174](https://github.com/awtoau/cynthion-workspace/issues/174)** (`memory.x:47-50` — `REGION_TEXT` is FLASH, no HyperRAM text region) ·
**[#176](https://github.com/awtoau/cynthion-workspace/issues/176)** (`sideband_link.py:103` `CMD_WRITE_BASE = 0x80`, no revision opcode) ·
**[#179](https://github.com/awtoau/cynthion-workspace/issues/179)** (`repos/apollo/firmware/src/vendor.c:422` is the only caller of `fpga_adv_command()`) · **[#180](https://github.com/awtoau/cynthion-workspace/issues/180)** · **[#182](https://github.com/awtoau/cynthion-workspace/issues/182)** · **[#183](https://github.com/awtoau/cynthion-workspace/issues/183)** ·
**[#181](https://github.com/awtoau/cynthion-workspace/issues/181)** (`repos/apollo/apollo_fpga/gateware/advertiser.py:51` still `m.d.comb += self.pad.o.eq(clk)`, no `.oe`) ·
**[#184](https://github.com/awtoau/cynthion-workspace/issues/184)** ·
**[#186](https://github.com/awtoau/cynthion-workspace/issues/186)** (named as the open question in the new plan, [`docs/chips/hyperram/bist-plan.md:198`](../../docs/chips/hyperram/bist-plan.md#L198)) ·
**[#192](https://github.com/awtoau/cynthion-workspace/issues/192)** · **[#194](https://github.com/awtoau/cynthion-workspace/issues/194)** (`awto-luna-soc` still pinned in `docs/toolchain-versions.md:60,268`) ·
**[#195](https://github.com/awtoau/cynthion-workspace/issues/195)** (`versions.json` still `"Yosys 0.65+57"`) ·
**[#196](https://github.com/awtoau/cynthion-workspace/issues/196)** (no `rust-toolchain*` outside `repos/`/`debris/`) ·
**[#197](https://github.com/awtoau/cynthion-workspace/issues/197)** (**UNSURE** whether an upstream yosys issue now exists — needs a network check) ·
**[#198](https://github.com/awtoau/cynthion-workspace/issues/198)** (master; two children [#190](https://github.com/awtoau/cynthion-workspace/issues/190)/#191 now done) ·
**[#199](https://github.com/awtoau/cynthion-workspace/issues/199)** (`scripts/apollo_budget_check.py:104-110` still `rom = text + data`, no `.relocate`) ·
**[#203](https://github.com/awtoau/cynthion-workspace/issues/203)** · **[#212](https://github.com/awtoau/cynthion-workspace/issues/212)** (the ILA is still only on flash — `top.py:1063`, `:382`; **not** folded into the BIST plan, do not close as redundant) ·
**[#217](https://github.com/awtoau/cynthion-workspace/issues/217)** · **[#218](https://github.com/awtoau/cynthion-workspace/issues/218)** (no Renode platform, no Verilator) ·
**[#219](https://github.com/awtoau/cynthion-workspace/issues/219)** · **[#220](https://github.com/awtoau/cynthion-workspace/issues/220)** ([`firmware/cynthion-boot/src/main.rs:125`](../../firmware/cynthion-boot/src/main.rs#L125) still says *"Nothing reads these"*) ·
**[#224](https://github.com/awtoau/cynthion-workspace/issues/224)** · **[#225](https://github.com/awtoau/cynthion-workspace/issues/225)** · **[#227](https://github.com/awtoau/cynthion-workspace/issues/227)** (`gateware/soc/cpu/cpu.py:161-163` untouched).

### 6e. Issues ≤ 116

**Close — done:**

| # | Title | Evidence |
|---|---|---|
| **19** | gateware: route VexRiscv JTAG debug through ECP5 JTAGG | `gateware/soc/top.py:727-742` — one `UserJTAG()` off the die's single `JTAGG`, ER2 to the CPU debug module, ER1 to the HyperRAM staging sink (verified verbatim). `gateware/soc/cpu/cpu.py:243-256, 318-340`; `gateware/soc/bus/jtag_stage.py:116-144`. **Caveat:** the core is VexiiRiscv not VexRiscv, and the PMOD-B fallback the issue asked to preserve does not exist. |
| **22** | apollo: verify and enable UART forwarding to host via CDC-ACM | Bidirectional and implemented: `repos/apollo/firmware/src/console.c:116-121` drains UART RX into `tud_cdc_write_char()`, `:129-131` pushes CDC RX back. |
| **63** | apollo firmware: allocate JTAG buffers from a pool | `repos/apollo/firmware/src/jtag.c:67-90` — `union comms_buffers { jtag_tx[…]; console_ring[…]; }`; `jtag.h:22` describes the old `extern uint8_t jtag_out_buffer[256]` in the past tense. Mechanism differs (static union + lock, not alloc/free) but the acceptance criterion is met. |
| **90** | hyperram: Wishbone peripheral | [`gateware/soc/top.py:356`](../../gateware/soc/top.py#L356) `HYPERRAM_BASE = 0x20000000`, `:1117` `decoder.add(bootram.mmap.bus, …)`, `:721` `main=1,exe=1`. The LUNA defect it documents was fixed today at `hyperram_controller.py:293`. **Strike the "Performance expectations" section before closing** — "~1 cycle per 16-bit word", "~23 cycles fixed overhead", "measured on r1.4 at 120 MHz" are exactly the void class. |
| **96** | riscv: standalone hardware self-test bitstream | `gateware/soc/top.py:1230-1249` (`USBSerialDevice` on `aux_phy`, own PID); `firmware/cynthion-soc/src/main.rs:455,900`; [`firmware/cynthion-soc/src/selftest.rs`](../../firmware/cynthion-soc/src/selftest.rs). **Residue if you want it tracked:** HyperRAM write/read-back, USER button, and 2 of the 3 PHYs are absent (only TARGET, `selftest.rs:558-590`). |

**Close as superseded:**

* **[#30](https://github.com/awtoau/cynthion-workspace/issues/30), [#31](https://github.com/awtoau/cynthion-workspace/issues/31), [#34](https://github.com/awtoau/cynthion-workspace/issues/34)** — the Flutter GUI issues. No GUI in the tree; `connection_painter.dart` and every `pubspec.yaml` exist only under `debris/code/`. `scripts/extract-hardware.py` (the entire subject of [#31](https://github.com/awtoau/cynthion-workspace/issues/31)) does not exist. These cannot be worked without first un-retiring an app.
* **[#100](https://github.com/awtoau/cynthion-workspace/issues/100) deliverable 1** — "USB → SRAM (proposed) ~6 ms" is disproven by [#108](https://github.com/awtoau/cynthion-workspace/issues/108) and by `gateware/probes/loader/bitstream_sink.py:12-25`: the ECP5 has no fabric path into its own configuration engine. Deliverables 2 and 3 survive ([`gateware/build_helpers.py:64`](../../gateware/build_helpers.py#L64) still emits only `--compress --freq 38.8 --usercode`).

**Correct the body:**

| # | verbatim stale text | correction |
|---|---|---|
| **8, 9, 10** | `venv/lib64/python3.11/site-packages/facedancer/…` | there is **no `venv/`** (`README.md:39`), the interpreter is **3.15.0rc1**, and `facedancer` is not importable at all. Re-express against upstream `greatscottgadgets/facedancer`; the "fix applied in venv" claim is unverifiable today. |
| **11** | `luna/gateware/usb/usb2/endpoints/isochronous*.py`, *"The moondancer SoC gateware (`gateware/`)"* | `repos/luna` is gone; LUNA is a pip package. And `gateware/` here is **our** SoC — moondancer's is `repos/cynthion/cynthion/python/src/gateware/`. Firmware facts still hold (`smolusb/src/lib.rs:22` `EP_MAX_PACKET_SIZE: usize = 512`). |
| **53** | *"Derived from `docs/apollo_dfu_buffer_analysis.md`"* | → [`docs/apollo_samd11_mcu/apollo_dfu_buffer_analysis.md`](../../docs/apollo_samd11_mcu/apollo_dfu_buffer_analysis.md) |
| **54** | *"Derived from `docs/apollo_race_conditions.md`"* | does not exist anywhere. **UNSURE** what replaced it. |
| **73** | `rom: 13836 B … 96.51%` / `ram: 3544 B … 86.52%`; title says "94.4%" | measured today: `text 13608, data 80, bss 3472` → ROM **13688 B = 95.48%**, RAM **3552 B = 86.72%**. All three published numbers are wrong, in two directions. ⚠️ the ELF is a **2026-08-03 build artifact** — re-build before quoting. |
| **81** | *"On `Python 3.15.0b3 free-threading build`"*; lists `luna` as editable "from the pinned submodules" | interpreter is **3.15.0rc1**; `repos/luna` is gone. |
| **83** | `repos/luna/pyproject.toml:37`; *"\| `repos/luna/luna/gateware/` \| 18 \| fork of upstream \|"*; *"\| `scripts/`, `ecp5-test/` \| 0 \|"* | LUNA is an external package, so the "18 files are upstream's problem" section and step 2 reduce to "wait for a released LUNA". **Our** part unchanged and real: `repos/cynthion/…/gateware/analyzer/analyzer.py:11,418,636` still uses `Record`. |
| **84** | *"the [#82](https://github.com/awtoau/cynthion-workspace/issues/82) gateware instantiates `I2CRegisterInterface` with `data_bytes=1`"* | that gateware is gone; [`gateware/soc/peripherals/i2c_master.py`](../../gateware/soc/peripherals/i2c_master.py) has **no `data_bytes` parameter at all**, so the stated prerequisite describes nothing. The 4-byte frame plan also predates the protocol rewrite (see [#87](https://github.com/awtoau/cynthion-workspace/issues/87)). |
| **86** | *"instantiated standalone in `ecp5-test/sideband/`"* | `gateware/probes/sideband/`. Work still real: nothing instantiates a USB device on `target_phy` (`top.py:1602-1611` takes it only for the ULPI register window). |
| **87** | *"`0x01 PING  0x02 STATUS  0x2B POWER  0x40-0x7F LED`"* | **obsolete.** `gateware/probes/sideband/sideband_link.py:96-104`: `CMD_PING=0x01`, `CMD_STATUS=0x02`, `CMD_WRITE_BASE=0x80`/`CMD_WRITE_MASK=0x7F`. **POWER and the whole LED block no longer exist**; `:120` records the version bump "from the 0x01 of the responder that carried POWER and DEVICES". So "68 allocated, 188 free" is wrong and the capability-query argument needs restating. |
| **89** | *"`docs/luna_ecp5_fpga/spi-flash-summary.md`"* | dissolved into [`docs/chips/w25q32-config-flash.md`](../../docs/chips/w25q32-config-flash.md). Also *"This needs a soft CPU inside the FPGA"* — that CPU exists (`top.py:1015`), so the blocker is gone though the measurements are not done. |
| **93** | *"once a RISC-V can drive it"* / depends on [#91](https://github.com/awtoau/cynthion-workspace/issues/91) | **[#91](https://github.com/awtoau/cynthion-workspace/issues/91) is CLOSED**; the CPU drives the flash today (`top.py:1015`, `selftest.rs:547`, [`scripts/riscv_flash_check.py`](../../scripts/riscv_flash_check.py)). Gate satisfied, measurements not. |
| **95** | *"`fpga_adv.c` carries both mechanisms in full"*; "~94% ([#73](https://github.com/awtoau/cynthion-workspace/issues/73))" | top-level `repos/apollo/firmware/src/fpga_adv.c` is now **38 lines of weak no-op stubs**; the real file is `repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c` (`:44`, `:163` — EIC still the default, so still real). Flash → 95.48%. |
| **97** | *"A first pass exists in `ecp5-test/pins/pin_survey.py`"* | `gateware/probes/pins/pin_survey.py:251-252`. **The headline "starting with the VBUS switches" has landed**: [`gateware/soc/peripherals/vbus_csr.py`](../../gateware/soc/peripherals/vbus_csr.py), instantiated `top.py:807`, mapped `:849-850`, pins driven `:1562-1566`. Genuinely remaining: PMOD loopback, `user_mezzanine`, USER-button press, edge-counting `target_usb_dp/dm`. |
| **105** | *"vendor GUH and bring up `msc_host`"* | GUH **is vendored** (`gateware/probes/usb_host/guh/{types,reset,sie}.py`, upstream `923c8490`, BSD-3-Clause). But `guh/__init__.py` records that `engines/*` were **deliberately not taken**, so "bring up `msc_host`" as written is no longer the plan — no `msc_host` exists in `gateware/`. Still open: hardware bring-up, `target_c_vbus_en` drive, the TUSB322I/FUSB302B CC driver. |
| **107** | `ecp5-test/CYNTHION_R14_PINMAP.md` | **does not exist anywhere** — not migrated into `gateware/`. Possible content loss. Flash "94.92% of 14336" → 95.48%. |
| **108** | *"`docs/luna_ecp5_fpga/fast-bitstream-loading.md` holds the full analysis"* | path gone; negatives preserved by `a9e238b` and restated at `gateware/probes/loader/bitstream_sink.py:12-25` — **which itself still cites the dead path at `:26`**. |
| **110** | *"**this is blocked on [#91](https://github.com/awtoau/cynthion-workspace/issues/91)** … Do not start until the CPU produces output"* | **[#91](https://github.com/awtoau/cynthion-workspace/issues/91) is CLOSED** (`793e90e`) — the block is lifted. Also `ecp5-test/fabric/FABRIC_TEST.md` does not exist (surviving record [`gateware/README.md:17`](../../gateware/README.md#L17), now 20,476 LUT4 not 20,143); `ecp5-test/riscv/vexii_cpu.py` → [`gateware/soc/cpu/cpu.py:235`](../../gateware/soc/cpu/cpu.py#L235); `scripts/hyperram_ladder.py` → the analogue is [`scripts/riscv_clock_ladder.py`](../../scripts/riscv_clock_ladder.py). Work not done: `top.py:567` `SYNC_MHZ = 60`, `:724` `cache_sets=64`. |
| **115** | *"The SoC exposes only a bare `irq_external` on the CPU -- no PLIC or CLIC."* | **false.** PLIC at [`gateware/soc/cpu/plic.py`](../../gateware/soc/cpu/plic.py) (mapped `top.py:896`), CLINT at `cpu/clint.py` (`top.py:903-906`), named sources at `top.py:886-893`; firmware `plic.rs`, `irq.rs`. RTIC is an off-by-default cargo feature (`Cargo.toml:38,95`). Replace the body with: *the blocker is gone; what remains is switching the shipping image off the superloop.* |

**Still real, verified (≤116):** #1, #2, #3, #4, #5 (all four cited defects confirmed verbatim — `cynthion_setup.py:39-40,97`, `cynthion_build.py:48`, `cynthion.py:39` `serial = "TODO"`, `selftest/host.py:32-33`) · #6, #7 (note `facedancer` is not installed, so neither is reproducible without reinstalling) · [#13](https://github.com/awtoau/cynthion-workspace/issues/13) · [#17](https://github.com/awtoau/cynthion-workspace/issues/17) · [#20](https://github.com/awtoau/cynthion-workspace/issues/20) (**its prerequisite [#19](https://github.com/awtoau/cynthion-workspace/issues/19) is now satisfied**) · [#21](https://github.com/awtoau/cynthion-workspace/issues/21) · [#23](https://github.com/awtoau/cynthion-workspace/issues/23) (`moondancer.rs:559-564` TODO intact) · [#24](https://github.com/awtoau/cynthion-workspace/issues/24) · [#60](https://github.com/awtoau/cynthion-workspace/issues/60) · [#113](https://github.com/awtoau/cynthion-workspace/issues/113) (`repos/apollo/firmware/src/boards/cynthion_d11/jtag.c:38` still lazy-calls `uart_configure_pinmux()`; the gateware half is fixed by `d7ac869`) · [#116](https://github.com/awtoau/cynthion-workspace/issues/116) (**UNSURE** whether the sweep ran — [`scripts/fabric_sweep.py:3`](../../scripts/fabric_sweep.py#L3) exists and names [#116](https://github.com/awtoau/cynthion-workspace/issues/116), but no recorded result was found; and see §5 item 2).

Two cross-issue conflicts worth reconciling when either is touched:
* **[#182](https://github.com/awtoau/cynthion-workspace/issues/182) vs [#199](https://github.com/awtoau/cynthion-workspace/issues/199)** — [#182](https://github.com/awtoau/cynthion-workspace/issues/182)'s headline *"flash: 11 bytes free"* is contradicted by [#199](https://github.com/awtoau/cynthion-workspace/issues/199), which says the guard misses `.relocate` and the real figure is 95.48%, already over the ceiling.
* **[#225](https://github.com/awtoau/cynthion-workspace/issues/225)'s "one broken dependency"** is live and wider than the issue says: `docs/luna_ecp5_fpga/` does not exist but is still cited from [`gateware/probes/loader/bitstream_sink.py:25`](../../gateware/probes/loader/bitstream_sink.py#L25), [`gateware/probes/hyperram/hyperram_identify.py:11`](../../gateware/probes/hyperram/hyperram_identify.py#L11), [`scripts/hyperram_identify.py:11`](../../scripts/hyperram_identify.py#L11), [`gateware/soc/peripherals/flash.py:38`](../../gateware/soc/peripherals/flash.py#L38), [`scripts/hyperram_fifo.py:43`](../../scripts/hyperram_fifo.py#L43), `scripts/bitstream_load_time_probe.py:15,396`.

---

## 7. How to reproduce the mechanical parts

Dead file references inside `docs/`:

```python
import re, os, collections
pat = re.compile(r'`((?:ecp5-test|gateware|scripts|firmware|docs|repos|tests|fpga-jobs'
                 r'|linux-on-cynthion)/[A-Za-z0-9_./+-]*\.'
                 r'(?:py|md|rs|c|h|x|toml|json|patch|diff|ys|v|sch))`')
out = collections.defaultdict(list)
for r, d, fs in os.walk('docs'):
    for f in fs:
        if not f.endswith('.md'):
            continue
        p = os.path.join(r, f)
        for n, line in enumerate(open(p, errors='replace'), 1):
            for m in pat.findall(line):
                if not os.path.exists(m):
                    out[m].append(f'{p}:{n}')
for k in sorted(out, key=lambda k: -len(out[k])):
    print(f'{k:<58} {len(out[k]):2d}  ' + ', '.join(out[k][:6]))
```

Same idea against issue bodies, using `gh issue list --state open --limit 250
--json number,title,body`, produced the §2 tables: **49 of 107 open issues carry at
least one file reference that resolves to nothing.**

Unmerged branches:

```
git fetch origin
for b in $(git ls-remote --heads origin | awk '{print $2}' | sed 's|refs/heads/||'); do
  echo "$b  ahead=$(git rev-list --count origin/main..origin/$b)" \
       "behind=$(git rev-list --count origin/$b..origin/main)"
done
```
