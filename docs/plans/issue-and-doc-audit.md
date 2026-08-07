# Tracker and documentation audit — 2026-08-07

Branch `soc-clocks`, pushed. Annotate this file directly -- it is a working
document, not a record.
107 open issues, 0 open PRs anywhere.

Everything below was verified against the working tree, not against the issue or
document text. Where a claim could not be settled it says so.

---


## STATUS — updated 2026-08-07 afternoon

Sections 6a and 6b are **done**. What follows below them is the audit as
written; this header is what has changed since.

**13 closed:** [#172](https://github.com/awtoau/cynthion-workspace/issues/172),
[#178](https://github.com/awtoau/cynthion-workspace/issues/178),
[#189](https://github.com/awtoau/cynthion-workspace/issues/189),
[#190](https://github.com/awtoau/cynthion-workspace/issues/190),
[#191](https://github.com/awtoau/cynthion-workspace/issues/191),
[#201](https://github.com/awtoau/cynthion-workspace/issues/201),
[#204](https://github.com/awtoau/cynthion-workspace/issues/204),
[#207](https://github.com/awtoau/cynthion-workspace/issues/207),
[#209](https://github.com/awtoau/cynthion-workspace/issues/209),
[#211](https://github.com/awtoau/cynthion-workspace/issues/211),
[#215](https://github.com/awtoau/cynthion-workspace/issues/215),
[#221](https://github.com/awtoau/cynthion-workspace/issues/221),
[#229](https://github.com/awtoau/cynthion-workspace/issues/229).

Each closed with the evidence, and each verified against the tree rather than
taken from this document — two of the audit's claims were wrong on detail
(#191's guards call a module function, not a method; #189's "duplicates" are
host-runner/gateware-top pairs).

**9 filed, so closing lost nothing:**

| | why it exists |
|---|---|
| [#241](https://github.com/awtoau/cynthion-workspace/issues/241) [#242](https://github.com/awtoau/cynthion-workspace/issues/242) [#243](https://github.com/awtoau/cynthion-workspace/issues/243) | the nine peripheral defects from #229, which lived only in a doc |
| [#244](https://github.com/awtoau/cynthion-workspace/issues/244) | the prior-art research, the one live third of #207 |
| [#245](https://github.com/awtoau/cynthion-workspace/issues/245) [#246](https://github.com/awtoau/cynthion-workspace/issues/246) [#247](https://github.com/awtoau/cynthion-workspace/issues/247) | RTIC adoption, per-peripheral, under the new `rtic` label |
| [#232](https://github.com/awtoau/cynthion-workspace/issues/232) | build metrics — **implemented and recording** |
| [#234](https://github.com/awtoau/cynthion-workspace/issues/234) | preload a bitstream and switch, instead of a full configure |

**F1 is resolved.** `origin/hyperram-bist` is salvaged: `hyperram_clocks.py`, the
CDC simulation and the upstream-reproduction notes came across in
[#231](https://github.com/awtoau/cynthion-workspace/pull/231); the five
BIST-specific files did not. Salvaging found the same open-loop PLL bug
(`i_CLKFB` undriven) that cost a bisect on `clocks.py`.

**F3 is resolved.** `docs/soc-clocking.md` no longer says the three-frequency
limit holds —
[#233](https://github.com/awtoau/cynthion-workspace/pull/233).

**F2 is PARTLY done.** The void figures are gone from the part doc, the plan,
the drafts and `gateware/README.md`. Still to check: whether any survive
elsewhere — `gateware/soc/top.py:622` was found restating one after the first
pass.

**Still unstarted:** §4 (documents asserting something false) and §5 (nobody
tracking these). §2/6c — the 55 stale bodies — is with an agent.

Open issues: **107 → 109**, which is the point. Closing thirteen and filing nine
better-scoped ones is not a reduction exercise.


## 0. The three findings that matter most

### F1. `origin/hyperram-bist` is 28 commits of unlanded work that redoes today's branch

`git rev-list --count origin/main..origin/hyperram-bist` = **28**, 0 behind, last
commit 2026-08-06, **no PR, no issue**. It contains the *same clock rework* that
`soc-clocks` landed independently today:

| `origin/hyperram-bist` | `soc-clocks` (today) |
|---|---|
| `c09997d hyperram: usb is the oscillator, so sync stops being constrained by it` | `cce87ec soc: take usb off the PLL, and the CPU clock stops being one of three values` |
| `01e675b docs: one clocks file, and the CPU ceiling in it is withdrawn` | `4d23fd2` (on main) |
| `7d3c75f hyperram: a second PLL, so device CK stops being the CPU clock` | — not on this branch |

It also **deletes `docs/soc-clocking.md`** and replaces it with
`docs/chips/ecp5/clocks.md`, and it **adds `gateware/soc/hyperram_clocks.py`**,
plus a whole HyperRAM BIST stack that exists nowhere else:

    gateware/soc/hyperram_clocks.py
    gateware/soc/peripherals/hyperram_bist.py
    gateware/soc/peripherals/bist_csr.py
    firmware/cynthion-soc/src/bist.rs
    scripts/bist_sim.py, soc_bist_cdc_sim.py, soc_bist_transport_sim.py
    tests/test_bist_constants.py
    docs/chips/ecp5/clocks.md
    docs/upstream-reproduction.md

**Consequence:** `gateware/soc/clocks.py:44` tells the reader to "see
`hyperram_clocks.py`" for the second PLL, and issues **#228** and **#230** both
cite `gateware/soc/hyperram_clocks.py` as a live path. **That file does not exist
on this branch.** Those issues were written against `hyperram-bist`. Merging the
two branches will conflict in `gateware/soc/top.py`, `docs/soc-clocking.md`
(deleted on one side, stale on the other) and the whole clock story.

This is not a documentation problem — it is two parallel implementations of the
same design decision, and nothing in the tracker records that.

### F2. The "void" HyperRAM figures were only partly deleted, and a partial deletion left two of them *uncaveated*

`docs/chips/hyperram/w956a8.md:462` asserts:

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

    `scripts/hyperram_ceiling.py`, CPU-free: the pattern is generated and verified in
    gateware, so nothing here goes through the CPU, the cache or `BootRAM`.

    |---|---|---|---|---|
    | 120 | 204.8 | 0 | passed | **pass** |
    | **140** | **238.9** | **0** | **passed** | **pass — the verified ceiling** |

    Write is consistently ~5% above read at every rung; both sit at 85.3% of
    theoretical.

Two void numbers now read as *the verified ceiling*, with the warning removed.
A second orphaned table exists at `w956a8.md:213-214` — header and separator, zero
rows.

### F3. `docs/soc-clocking.md` leads with a standing finding that today's branch abolished

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

## 1-2. Issue triage — DONE

Executed 2026-08-07. 19 closed, 14 filed, 60 bodies corrected, 81 references
rewritten. The per-issue detail is in the issues themselves, which is where it
belongs — each closure carries its evidence and each correction is a dated note
on the issue.

Two of this document's own claims were wrong and were caught by checking the
tree rather than trusting the report: [#191](https://github.com/awtoau/cynthion-workspace/issues/191)'s
guards call a module function rather than a method, and
[#189](https://github.com/awtoau/cynthion-workspace/issues/189)'s "three files
existing twice" are host-runner/gateware-top pairs. The automated correction pass
found four more, including that [#116](https://github.com/awtoau/cynthion-workspace/issues/116)
and [#224](https://github.com/awtoau/cynthion-workspace/issues/224) cite
*pluribus's* files and say so in the surrounding sentence.

It also caught a real leak: [#63](https://github.com/awtoau/cynthion-workspace/issues/63)
carried a private filesystem path, published on a public tracker. Now a
repo-relative one.

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

> **Partly done, 2026-08-07.** `docs/soc-clocking.md` is corrected
> ([#233](https://github.com/awtoau/cynthion-workspace/pull/233)), and so are
> `architecture.md`, `usb-host-options.md` and the `w956a8.md` clocking passage.
> Correcting them turned up the same `fPFD` violation in
> `hyperram_ceiling_top.py`'s own PLL solver, now fixed.
>
> **Remaining:** the mangled tables in `w956a8.md` (an orphaned separator with no
> header, and a header with zero rows, both from `13f2c81`), several dead script
> references, and `docs/README.md`'s broken sentence where the same link appears
> twice while contrasting two files.


| file:line | the stale text | why it is false |
|---|---|---|
| `docs/soc-clocking.md:5-7` | *"Only three are safe. **This still holds** and will cost a day if rediscovered."* | `gateware/soc/clocks.py:31-33`: 63–130 MHz all reachable within 0.5% bar eight values |
| `docs/soc-clocking.md:28` | heading *"## 1. Only three frequencies in 60..130 MHz can work at all"* | same |
| `docs/soc-clocking.md:47-48` | *"**only 60, 100 and 120 land on an exact 60 MHz `usb` clock.** Every other value ships a PHY clock that is wrong by 1-5%."* | `usb` is now the A8 oscillator passed through, `clocks.py:264-266` |
| `docs/soc-clocking.md:68-69` | *"`variable_clock.py` now **refuses to build** outside 0.5%"* | the SoC no longer instantiates `VariableClockDomainGenerator`; `SocClocks` raises instead (`clocks.py:129-134`) |
| `docs/soc-clocking.md:144-145` | *"only the first -- the three exact-60 PLL solutions -- is a **standing result**"* | it is now a withdrawal too |
| `docs/soc-clocking.md:83, 149-151` | `./scripts/nextpnr_allow_fail_ladder.py`, `./scripts/riscv_verify_bitstream.py`, `./scripts/diamond_riscv_ladder.py` | none of the three exist |
| `docs/chips/hyperram/w956a8.md:165-169` | *"`VariableClockDomainGenerator` narrows it further … leaving **60, 100 and 120** as the only `sync` values below 130"* | same as above |
| `docs/chips/hyperram/w956a8.md:178-180` | *"Anything with a ULPI must keep using `VariableClockDomainGenerator` — a wrong `usb` presents as a dead board"* | `SocClocks` serves exactly that case, better; `clocks.py:19-25` |
| `docs/chips/hyperram/w956a8.md:141,192-197,316-318,502-511,538,549,566,575,581` | live MB/s figures | contradicted by `w956a8.md:462` in the same file — see **F2** |
| `docs/chips/hyperram/w956a8.md:192` | orphaned `\|---\|---\|---\|---\|---\|` with no header row | table mangled by `13f2c81` |
| `docs/chips/hyperram/w956a8.md:213-214` | header + separator, **zero rows** | same commit |
| `docs/chips/hyperram/w956a8.md:457-458` | `scripts/hyperram_ladder.py`, `scripts/fetch_winbond_hyperram.py` | neither exists |
| `docs/chips/hyperram/w956a8.md:452` | `gateware/probes/hyperram/hyperram_dqs_top.py` | does not exist |
| `docs/architecture.md:31` | `\| clocks \| VariableClockDomainGenerator \| written \| soc-clocking.md, #111 \|` | it is `SocClocks` (`gateware/soc/clocks.py`); #111 is CLOSED |
| `docs/upstream-boundary.md:76` | *"Ours solves `sync` and `usb` together so `usb` lands on exactly 60 MHz. #111"* | no longer how it works; and the cited path `repos/apollo/apollo_fpga/gateware/variable_clock.py` is no longer what the SoC builds |
| `docs/chips/ecp5/lfe5u-12f.md:72-77` | *"The PLL is driven by `VariableClockDomainGenerator` … Ours solves for `sync` **and** `usb` together"* | same |
| `docs/usb-host-options.md:612` | *"Clocking is `VariableClockDomainGenerator(sync_mhz=60)`"* | same |
| `docs/upstream-boundary.md:174-175` | *"It **used to be** held outside the controller by `hyperram_ceiling_top.py`"* | past tense is wrong — it **still is**, deliberately and in addition to the controller: `hyperram_ceiling_top.py:583` computes `recovery_cycles`, `:731,769,890` gate on it, and `:190-195` says the double-hold is intentional. tCSHI is now paid twice in that harness. Flagged as a doc contradiction, **not** as a code bug. |
| `docs/README.md:38-39` | *"[`architecture.md`] is what the system is made of; [`architecture.md`] is what is still open."* | the same link twice — the sentence is broken; one of them was meant to be a different file |
| `docs/README.md` §"Moondancer / the SoC" | **empty section**, no entries | |
| `docs/README.md` index | 25 `.md` files under `docs/` are not indexed, despite the file opening *"Index of every file under `docs/`"*. Most are `docs/drafts/**` (23), plus **`docs/chips/hyperram/bist-plan.md`** (added today) and **`docs/soc-memory-bus.md`** | |
| `docs/toolchain-simplification.md:381` | *"Nothing in `gateware/` does, except `riscv/vexii_hello_soc.py`"* | `gateware/soc/top.py` |
| `docs/soc-memory-bus.md:218` | `(vexii_hello_soc.py:689)` | `gateware/soc/top.py` |
| `docs/usb-host-options.md:683,689,765,806,939` | five `vexii_hello_soc.py:NNN` line citations | `gateware/soc/top.py`, and the line numbers will not have survived the move |
| `docs/drafts/gsg-scenarios-master.md:32` | `vexii_hello_soc.py:1175` | same |

There are **~90 further dead file references across `docs/`** (backtick-quoted
paths that resolve to nothing). The worst offenders by count:
`scripts/soc_timing_sweep.py` (6 sites), `gateware/probes/i2c/multiplexed.py` (4 —
deleted by `fe7d0bf`), `scripts/flash_capacity_probe.py` (4),
`scripts/usb-host-area.py` (3), `scripts/patch_amaranth_soc_annotations.py` (3).
Full list reproducible with the script in §7.

**Documents that are correct and current** (checked, no action): 
`docs/chips/ecp5/peripheral-clock-audit.md` — it names the stale prose elsewhere
in the tree at `:433-448` rather than adding to it; `docs/upstream-boundary.md`
§HyperRAM controllers (`:145-180`) apart from the one past-tense line above;
`README.md` at the repo root; `docs/chips/hyperram/bist-plan.md` apart from its
`#204` precondition line.

---

## 5. Nobody is tracking these

> **Updated 2026-08-07.** Items 1, 4, 6 and 7 are resolved: `hyperram-bist` is
> salvaged and its useful parts landed ([#231](https://github.com/awtoau/cynthion-workspace/pull/231));
> the nine peripheral defects are now
> [#241](https://github.com/awtoau/cynthion-workspace/issues/241)–[#243](https://github.com/awtoau/cynthion-workspace/issues/243);
> everything is pushed and the tree is clean. Item 2 was **wrong** — the fabric
> suite landed in `scripts/` on 2026-08-06 and is not stranded on
> `origin/codex/issues-101-116`.
>
> **Still true, and worth acting on:** three stale remote branches
> (`hyperram-bist` now redundant, `codex/issues-101-116`,
> `chore/retire-flutter-and-facedancer` at 0 ahead of `main`), the six upstream
> drafts in `docs/drafts/upstream/`, and the patchsets in `docs/patchset/` and
> `docs/apollo/pending-patches/`.


1. **`origin/hyperram-bist` — 28 commits, no PR, no issue.** See **F1**. This is
   the single largest untracked thing in the repo, and it collides head-on with
   `soc-clocks`. The longer both live, the worse the merge.

2. **`origin/codex/issues-101-116` — abandoned, and it holds the only copy of the
   fabric coverage suite.** 1 ahead / 203 behind `main`, last touched 2026-08-03,
   commit message *"fabric: the coverage suite as codex left it, committed before
   /tmp took it"*. It carries `scripts/fabric_arcs.py`, `fabric_build.py`,
   `fabric_golden.py`, `fabric_placement.py`, `fabric_run.py`, `fabric_sim.py`,
   `fabric_sweep.py`, `tests/test_fabric_coverage.py`. Issues **#116** and **#224**
   depend on that work and reference those paths as if they were on `main`.
   It is also 203 commits behind and still edits `ecp5-test/fabric/`, so a plain
   merge will not apply.

3. **`origin/chore/retire-flutter-and-facedancer` — 0 ahead of `main`.** Fully
   merged; safe to delete. Pure noise in `git branch -a`.

4. **The 9 defects from the peripheral clock audit exist only in a document.**
   `docs/chips/ecp5/peripheral-clock-audit.md:450-458` — 9 defects, 4 live today
   (1, 2, 3, 4), 5 latent. Defect 1 is called *"the most serious finding"*
   (`:62`): `gateware/soc/peripherals/ulpi_window.py:231` passes a bare `Value`
   to `ResetInserter`, which Amaranth reads as `{"sync": …}`, so the timeout reset
   its own docstring calls *"the only way back to IDLE"* reaches no logic at all,
   in a module that is entirely `usb`. **There is no issue for any of the nine.**
   Closing #229 without filing them loses all nine — which is exactly the failure
   `docs/README.md:26-28` warns about (*"A closed issue is not documentation"*).

5. **`docs/soc-clocking.md` is deleted on one branch and stale on the other.**
   No issue records which version wins.

6. **107 open issues, 0 open PRs, and 17 unpushed commits on `soc-clocks`.**
   `git rev-parse --abbrev-ref @{u}` → *no upstream configured*. Local `main` is
   also 2 commits ahead of `origin/main`. Today's entire body of work exists on
   one machine.

7. **Uncommitted change in the tree:** `scripts/soc_jtag_stage_sim.py` is modified
   and unstaged.

8. **`docs/drafts/upstream/pr-1..pr-6`** are six drafts addressed to upstream GSG.
   `ac8a575`'s message says two of them *"would have published"* void figures and
   were corrected. They are not indexed in `docs/README.md` and no issue tracks
   whether any were ever sent. `docs/drafts/upstream/cynthion-147-comment.md`
   relates to **#200** (*"unblock cynthion#147"*), which is open — **UNSURE**
   whether the comment was posted.

9. **`docs/patchset/`** (3 files) and **`docs/apollo/pending-patches/`** (3 patches
   + README) describe patches whose applied/unapplied state is not recorded in any
   open issue. The memory index notes a "UART-DMA patch unapplied" from 28 Jul;
   `docs/apollo/code-test/apollo-uart-dma.patch` is still sitting there. **UNSURE**
   whether it has since been applied.

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
