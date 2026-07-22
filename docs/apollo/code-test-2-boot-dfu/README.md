# Apollo boot-to-DFU / JTAG / USB-handoff work (preserved for review)

Work rescued from a standalone fork clone before that clone was deleted.

## Provenance

- **Source:** `/mnt/2tb/git/awtoau/awto-apollo` (a standalone clone of the
  `awtoau/awto-apollo` fork, since deleted).
- **Base commit:** `04507df` (`origin/main`, same commit `repos/apollo` is
  pinned to and the same base as the deleted UART-DMA clone).
- **Captured:** 2026-07-23. Nothing here is in the canonical workspace yet.

## What this is

Two local commits that were on **no remote**, plus an uncommitted `vendor.c`
rewrite on top:

- `6b03aad fix: mark fpga_online as volatile to prevent race conditions`
- `211664e apollo: add boot-to-dfu, jtag programming state, and usb handoff updates`
- uncommitted: a further 353/222-line rewrite of `firmware/src/vendor.c`

Touched files (vs base `04507df`): `apollo_fpga/__init__.py`,
`firmware/src/boards/cynthion_d11/fpga_adv.c`, `firmware/src/console.c`,
`firmware/src/fpga.c`, `firmware/src/fpga.h`, `firmware/src/main.c`,
`firmware/src/vendor.c`.

## Contents

| File | What |
|------|------|
| `0001-fix-mark-fpga_online-as-volatile-...patch` | commit `6b03aad` |
| `0002-apollo-add-boot-to-dfu-...patch`           | commit `211664e` |
| `UNCOMMITTED-vendor.c.diff`                       | the working-tree vendor.c rewrite that sat on top of the 2 commits |
| `full-all-changes-vs-04507df.diff`               | everything above combined as one diff against `04507df` |

`full-all-changes-vs-04507df.diff` was verified to apply cleanly onto
`repos/apollo` (which is at `04507df`).

## To apply onto repos/apollo (for review)

```bash
cd repos/apollo
git checkout -b apollo-boot-dfu origin/main   # base 04507df
git apply /path/to/docs/apollo/code-test-2-boot-dfu/full-all-changes-vs-04507df.diff
# review, build, commit; or apply the two .patch files with `git am` to keep commit boundaries
```

## Status

Not reviewed for correctness. Preserved as-is for review before any decision to
adopt into `repos/apollo` or push to the `awto-apollo` fork.
