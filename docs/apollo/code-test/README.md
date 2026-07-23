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
[../../apollo_samd11_mcu/apollo_moondancer_uart_watchdog_design.md](../../apollo_samd11_mcu/apollo_moondancer_uart_watchdog_design.md)
and the serial-architecture docs under
[../../apollo_samd11_mcu/](../../apollo_samd11_mcu/).

> **Note (2026-07-23):** this implements the **UART watchdog** branch of the
> serial redesign. Per [awtoau/cynthion-workspace#62](https://github.com/awtoau/cynthion-workspace/issues/62)
> the project's chosen Apollo↔FPGA path is **SPI on SERCOM2 (PA08/PA09/PA10)**,
> *not* UART — so this code documents a deselected branch. It also drives the
> UART on **PA11/PA14**, which are the JTAG bit-bang pins (TMS/TDI), so this RX
> path is mutually exclusive with JTAG-over-USB and JTAG-over-SPI. Review the
> tracking issues before applying:
> [#66](https://github.com/awtoau/cynthion-workspace/issues/66) (this driver +
> review findings) and [#65](https://github.com/awtoau/cynthion-workspace/issues/65)
> (three-way pin exclusivity — PA08/PA09 are **not** free on d11, so the doc's
> "move UART to PA08/PA09" is not viable here).

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

## Review status — do NOT apply verbatim

A 2026-07-23 hardware review (against the SAMD11D14 CMSIS headers) found the
DMA driver **non-functional as written**. Two blocking bugs must be fixed first:

1. **Wrong DMA trigger source.** `uart_dma.c` hardcodes
   `SERCOM2_DMAC_ID_RX = 0x08` from the **SAMD21** table. On SAMD11D14 the real
   value is **5** (0x08 is `TCC0_DMAC_ID_MC_0`). As written the channel gets
   zero triggers and never receives a byte. Delete the local `#define` and use
   the CMSIS `SERCOM2_DMAC_ID_RX` symbol.
2. **Non-idempotent init.** `uart_dma_init()` runs a **global** `DMAC SWRST` and
   is called from `uart_initialize()`, which the TinyUSB CDC callbacks invoke
   repeatedly (every host line-coding / DTR change). Each call resets the whole
   DMAC mid-stream and drops in-flight data. Guard it to init once.

Also recommended before production:

3. The completed-half selection uses a free-running software `_active_half`
   flag that permanently desyncs (silent corruption) on a single missed/
   coalesced `TCMPL`. Derive the completed half from the hardware write-back
   descriptor (`WRBADDR`) instead.

Correct as-is: DSTADDR increment math, ring-buffer SPSC concurrency, DMAC
clock/power enables, circular-chain auto-continue. Two comments are mislabeled
(name SRCADDR / PM_BUS_AHB while the code acts on DSTADDR / APBB) — code is right.
