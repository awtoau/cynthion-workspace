# Patches that were never applied

The three survivors of `patches/`, which held 23 files and has been deleted.

That directory was a `git format-patch` export of commits from 2026-05-19/21 that
was never cleaned up afterwards. **19 of its 23 files were already landed as
commits** — the same change existing twice, once in git history and once as stale
text. One was superseded. Provenance was established with
`git patch-id --stable` against every commit in each target repo, which is
title-blind and resolved 18 outright; the rest by reverse-apply and `git log -S`.

The 19 landed files were deleted rather than moved to `debris/`: an export of
commits that exist in history is regenerable by definition, and `debris/` is for
content a user could not recreate.

Three files had no corresponding commit. They are kept here.

## 1. `apollo-uart-rx-ring-buffer.diff` — a real bug fix, worth applying

The substantive one.

**The bug:** `uart_byte_received_cb()` calls `tud_cdc_write_char()` and flushes
**directly from the UART RX ISR**. That can fire while TinyUSB is inside a
critical section, which is a reentrancy hazard rather than a style problem.

**The fix:** a 256-byte RX ring buffer, so the ISR only enqueues and the console
task drains it.

**Not the DMA driver from #66.** Verified: zero DMA/DMAC references. That issue
tracks a *ping-pong DMA* driver on SERCOM2 that was reviewed and deliberately
**deselected**. This is a plain ISR-safe ring buffer, a much smaller change, and
its deselection does not apply.

**It bundles a second, unrelated change**: removal of the forced
`hand_off_usb()` / `allow_fpga_takeover_usb(true)` at boot. That is a **policy
change**, not a bug fix, and needs a decision rather than review. Split before
applying.

**Apply state:** plain `git apply` fails on context only — upstream added an
`apollo_mode.h` include. `git apply -3` applies both files cleanly. That is an
apply check and nothing more: **no firmware was built and none was flashed**, so
whether the ring buffer fixes the reentrancy in practice is untested.

## 2. `0020-scripts-apollod.py-graceful-shutdown-...patch` — stranded

57 lines adding graceful shutdown via a `cmd shutdown` path to `apollod.py`.

Real work, but the target moved: `grep shutdown apollod.py` returns nothing. The
`cyn` daemon this once pointed at as the replacement is itself retired — it
imported a module that had moved to `debris/`, so it raised `ModuleNotFoundError`
on every run, and both it and the `./cyn` wrapper are now deleted. Only worth
applying if `apollod.py` has a future. Otherwise delete.

## 3. `moondancer-uart0-log-port.diff` — recommend deleting

A one-line debug flip, `Port::Both` → `Port::Uart0`. It **removes** a log
destination, so it is a local debugging convenience rather than a fix.

## Three things that looked valuable and had all landed

Recorded because they were flagged as possible unlanded work and were not, so the
next person does not re-investigate:

| looked like | actually |
|---|---|
| isochronous IN skeleton across gateware and firmware | landed as `4ab5361`; the 164 added `ep_iso_in.py` lines are byte-identical, and the file has since been fixed twice on top (`53d3ea4`, `56a64c2`) |
| endpoint `max_packet_size` clamp to `EP_MAX` | landed as `6fa3e0a`, exact patch-id match |
| "iso-13 progress" text found nowhere in the repo | in `repos/cynthion/awto.md` — a superproject grep misses it because it is inside a submodule |

## What "carrying patches" means here

Worth separating, because the two get conflated:

- **`repos/apollo` is 34 commits ahead of upstream.** Those are live: they are in
  the code that gets built and flashed. Submitting the upstreamable ones is
  issue #102.
- **These three files are not carried anywhere.** They are unapplied text. Nothing
  builds them.
