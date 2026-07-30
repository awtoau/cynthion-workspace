# `patches/` — what these files are

**Short answer:** almost all of it is dead weight. 20 of the 23 patch files are a
`git format-patch` export of commits that are *already in the submodule
histories*, taken on 2026-05-22 and never cleaned up. Three files are real
unlanded work, and one of those three is substantive.

Established on 2026-07-30 by `scripts/patch_provenance.py`. Full evidence in
`tmp/logs/patch_provenance.log`.

## Method

Titles were deliberately not trusted. In order of strength:

1. **`git patch-id --stable`** on the patch, compared against the patch-id of
   every commit in the target repo. An exact match is title-blind proof the same
   diff landed, whatever the commit message says. This resolved 18 of 23 files
   outright.
2. **`git apply --check --reverse`** in the target worktree — a clean reverse
   apply means the content is present *now*.
3. **`git apply --check`** forward — a clean forward apply means the content is
   absent and the patch is still live.
4. **`git log -S<line>`** on the longest, most distinctive added lines, over
   `--all`. This is what catches content that landed reworded or folded into a
   larger commit.
5. **Existence of the touched paths**, for the obsolete case.

Two files needed manual follow-up because the automated verdict was misleading;
both are noted in the table.

## The one thing worth reading

`patches/apollo/0000-wip-issue22-apollo-fixes-20260722.diff` is **genuinely
unlanded and substantive.** It is two independent changes in one file:

- **A 256-byte UART RX ring buffer in `console.c`.** `uart_byte_received_cb()`
  currently calls `tud_cdc_write_char()` + `tud_cdc_write_flush()` *directly from
  the UART RX interrupt*, which can fire while TinyUSB is inside a critical
  section. The patch pushes to a ring in the ISR and drains it from
  `console_task()` in thread context. This is the reentrancy bug, not a
  refactor.
- **Removing the forced `hand_off_usb()` / `allow_fpga_takeover_usb(true)` from
  `main()`.** Makes USB hand-off explicit policy rather than unconditional at
  boot. This one is a behaviour change with real consequences — it is why the
  board would stay in Apollo mode after reset — and it is the half that needs a
  decision rather than just review.

It does not apply with plain `git apply` (upstream added an `apollo_mode.h`
include, so the context drifted) but **`git apply -3` applies both files
cleanly** — verified. This is the "UART-DMA patch unapplied" item that a previous
session left outstanding.

## Per-file table

Target repo is `repos/cynthion` for everything under `patches/cynthion/` and
`patches/moondancer/`; `repos/apollo` for `patches/apollo/`.

### `patches/apollo/` — 1 file

| File | Status | How determined | Recommendation |
|---|---|---|---|
| `0000-wip-issue22-apollo-fixes-20260722.diff` | **UNLANDED** | No patch-id match. `grep uart_rx_ring firmware/src/console.c` → absent; `git log --all -S uart_rx_ring` → no commits. Plain apply fails on context only; `git apply -3` applies both files cleanly. | **Keep pending.** Split it: the ISR ring buffer is a bug fix worth applying; the `hand_off_usb()` removal is a policy change needing a decision. Do not apply as one blob. |

### `patches/cynthion/` — 21 files

| File | Status | How determined | Recommendation |
|---|---|---|---|
| `0000-wip-issue22-cyn-process-and-reset-20260722.diff` | **SUPERSEDED** | Targets `scripts/cyn_main.py`, which no longer exists in the submodule — the file moved to the **workspace** `scripts/cyn_main.py`. 28 of its 31 added lines are byte-present there; the other 3 are present in extended form (`choices=["normal","hold-apollo","boot-dfu"]` at line 1432 vs the patch's two choices). Landed as workspace commit `be075ab`, then extended with a `boot-dfu` mode. | **Delete.** Recoverable from git history; the current code is a superset. (The script's initial "obsolete" verdict was wrong — it only searched the submodule.) |
| `0001-gitignore-add-venv-and-tmp...` | LANDED | patch-id `f5f53a3fcb79` == `c8b591e` | Delete |
| `0002-gitignore-track-tmp-and-pin-ci-env...` | LANDED | patch-id `8c22963ebf2f` == `1d08505`; also reverse-applies | Delete |
| `0003-awto-add-hardware-notes-and-proxy-scripts` | LANDED | patch-id `3564854c97ae` == `1d24249` | Delete |
| `0004-scripts-add-reset-cynthion.sh...` | LANDED | patch-id `2810ca709fc8` == `efd5009` | Delete |
| `0005-moondancer-clamp-endpoint-max_packet_size-to-EP_MAX_` | LANDED | patch-id `17e8545bf5c9` == `6fa3e0a`. (Flagged as "substantive" in the brief — it is, but it landed.) | Delete |
| `0006-iso-add-isochronous-IN-skeleton...` | LANDED | Landed as `4ab5361`. patch-id differs *only* because the patch's `.gitignore` hunk overlaps later commits. Verified directly: the 164 added lines of `ep_iso_in.py` are **byte-identical** to `4ab5361:.../ep_iso_in.py`. The file was later amended by `53d3ea4` (Amaranth 0.5.x `__init__`) and `56a64c2` (duplicate-instantiation fix), so the tree is now a superset. | **Delete.** The iso skeleton is not lost work — it is in the tree and has since been fixed twice. |
| `0007-gitignore-add-tmp-workspace-scratch-directory` | LANDED | patch-id == `24af0a4` | Delete |
| `0008-awto-document-current-state-patches-and-iso-13-progr` | LANDED | patch-id == `5745112`. The "iso #13 progress" text the brief could not find elsewhere is in `repos/cynthion/awto.md`, not the superproject — which is why a workspace-wide grep missed it. | Delete |
| `0009-firmware-enable-integer-overflow-checks...` | LANDED | patch-id == `482bcf2`; `overflow-checks = true` present at `firmware/Cargo.toml:24,33` | Delete |
| `0010-firmware-event-queue-overflow-drops-event...` | LANDED | patch-id == `7378797` | Delete |
| `0011-firmware-add-stack-canary-for-overflow-detection-14` | LANDED | patch-id == `131927a`; `canary.rs` present, 84 lines | Delete |
| `0012-firmware-scripts-fault-injection-verbs...` | LANDED | patch-id `c55b57ea8561` == `4d8688c` | Delete |
| `0013-firmware-fix-wrong-function-name-in-write_endpoint-l` | LANDED | patch-id `28babb2a9340` == `c0144f4`; reverse-applies | Delete |
| `0014-awto.md-add-USB-topology-diagram...` | LANDED | patch-id `88e70191e845` == `6863fd6`; reverse-applies | Delete |
| `0015-firmware-add-make-bin-target...` | LANDED | patch-id `3eaceb0bb1a9` == `b0c06d7`; reverse-applies | Delete |
| `0016-firmware-update-moondancer.bin...` | LANDED | Binary patch, so no usable patch-id. Reverse-applies cleanly against the current `cynthion/python/assets/moondancer.bin`, which proves the current blob is the patch's post-image. Corresponds to `d036598`. | Delete |
| `0017-scripts-reset-cynthion.sh-explain-hung-firmware-path` | LANDED | patch-id `e6aa721a72e9` == `593cf4a`; reverse-applies | Delete |
| `0018-scripts-apollod.py-Cynthion-device-daemon...` | LANDED | patch-id `8f562235a659` == `5848814`; reverse-applies | Delete |
| `0019-scripts-apollo-mux.py-interactive-REPL...` | LANDED | patch-id `5109d85cd03f` == `4fd1f7a`; reverse-applies | Delete |
| `0020-scripts-apollod.py-graceful-shutdown-via-cmd-shutdow` | **UNLANDED** | No patch-id match. `grep shutdown repos/cynthion/scripts/apollod.py` → **zero hits**. Applies forward cleanly. Claims "Closes #35" but `#35` in the submodule is the clippy commit `fc81a06`, so the reference is stale. | **Keep pending, low priority.** Real work (57 lines: per-client reader thread, `{"cmd":"shutdown"}`, `shutdown_ack`, socket cleanup) but only useful if `apollod.py` is still in use — the workspace has since moved to `scripts/cyn-daemon.py`. Decide whether `apollod.py` has a future first; if not, delete both. |

### `patches/moondancer/` — 1 file

| File | Status | How determined | Recommendation |
|---|---|---|---|
| `0000-wip-uart0-log-port-20260722.diff` | **UNLANDED** | Applies forward cleanly. Current `moondancer.rs:137` still reads `Port::Both`. | **Delete, do not apply.** It is a one-line debug flip (`Port::Both` → `Port::Uart0`) that *removes* a log destination. `Port::Uart0` already exists in `log.rs:56`, so nothing is lost — this is a temporary local debugging change, not work. Trivially reproducible. |

## Counts

| Status | Count |
|---|---|
| Landed | 19 |
| Superseded | 1 |
| Unlanded | 3 |
| Obsolete | 0 |
| **Total** | **23** |

(The directory holds 23 patch/diff files. Earlier notes said "32 files"; that
count does not match what is on disk.)

## Recommendation for the directory as a whole

**Delete `patches/` entirely, after extracting the three unlanded files.**

- 20 of 23 files are a `format-patch` export of landed commits. That is
  regenerable from git history by definition — `git format-patch` reproduces it
  exactly. Per the repo's own rule, regenerable content gets deleted, not
  archived. **Nothing here belongs in `debris/`.**
- The three unlanded files should move somewhere that signals they are pending
  work rather than history: the apollo one is the only one that matters, and it
  wants to become a branch in `repos/apollo` or an entry in
  `docs/upstream-patch-plan.md`, not a loose `.diff`.
- A directory of 20 already-landed patches is worse than no directory, because
  it invites exactly the question that prompted this audit and costs a
  patch-id sweep to answer each time.

## Reproducing this

```bash
python3 scripts/patch_provenance.py     # → tmp/logs/patch_provenance.log
```
