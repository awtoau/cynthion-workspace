# Apollo UART-DMA firmware work (preserved)

Uncommitted firmware work rescued from a standalone Apollo clone that was
otherwise loose working-tree state (not committed, stashed, or pushed anywhere).

## Provenance

- **Source:** `/mnt/2tb/git/apollo` (a standalone clone of
  `greatscottgadgets/apollo`, since deleted).
- **Base commit:** `04507df` (`greatscottgadgets/apollo` main, v1.1.1-2) — the
  same commit `repos/apollo` (the awtoau-fork submodule) is pinned to.
- **Captured:** 2026-07-23.

## What this is

A DMA-backed UART RX driver for the Cynthion ATSAMD11 and its integration —
almost certainly the implementation of the Apollo serial redesign described in
[../apollo_moondancer_uart_watchdog_design.md](../apollo_moondancer_uart_watchdog_design.md)
and the serial-architecture docs under
[../../apollo_samd11_mcu/](../../apollo_samd11_mcu/).

`uart_dma.c/.h` implement ping-pong DMA on SERCOM2 RX (DMAC channel 0) into a
512-byte ring buffer drained by the main task via `uart_dma_read()`.

## Contents

| File | Status vs base | Notes |
|------|----------------|-------|
| `firmware/src/boards/cynthion_d11/uart_dma.c` | new (302 lines) | DMA RX driver |
| `firmware/src/boards/cynthion_d11/uart_dma.h` | new (48 lines)  | driver interface |
| `firmware/src/boards/cynthion_d11/uart.c`     | modified (+32)  | DMA integration |
| `firmware/src/boards/cynthion_d11/fpga_adv.c` | modified (+5)   | |
| `firmware/src/console.c`                       | modified (+49)  | |
| `apollo-uart-dma.patch`                        | —               | all of the above as a diff against base `04507df` |

The `firmware/` tree here holds the **full files** as they were. The patch holds
the same changes as a diff, so the work is recoverable either way.

## To apply onto repos/apollo

```bash
cd repos/apollo
git fetch origin
git checkout -b uart-dma origin/main        # base is 04507df
git apply /path/to/docs/apollo/code-test/apollo-uart-dma.patch
# review, build, commit
```

If `repos/apollo` has moved past `04507df`, apply with `git apply --3way` or
reconstruct from the full files in `firmware/`.
