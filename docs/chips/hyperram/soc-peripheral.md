# HyperRAM SoC peripheral (#588)

**Status: proposed, not built.** The register map is under review on [#588](https://github.com/awtoau/cynthion-workspace/issues/588), revision 2 comment. This page becomes its authority once it is approved and built. Until then the SoC runs the old stack.

## Shape
- Core, PHY and PLL from [`gateware/hyperram_baseline/`](../../../gateware/hyperram_baseline/README.md) (#581).
- CK 180 MHz, on its own `hr`/`hr90` PLL from the 60 MHz oscillator.
- The RV has no memory-mapped HyperRAM window. Access is through registers only.
- Byte port: `ADDR` (byte address, auto-increment) + `DATA8`/`DATA16`/`DATA32`.
  - Any alignment.
  - Writes are masked HyperBus writes merged into bursts.
  - Reads are served from prefetched lines.
- Ops at bus rate, any byte range: FILL (constant / ramp / test pattern), TEST (write / read / write+read with the checker), CALIBRATE (read-delay sweep, centre of the widest clean run; runs automatically after lock).
- Every failure reaches the RV:
  - an op code in STATUS
  - sticky rw1c ERRORS
  - IRQ 18 on done/error
  - bounded watchdogs, each 1.25x its derived expectation
  - counters read as one consistent snapshot
- Optional: a block-RAM window on the RV bus with COPY to and from HyperRAM (decision on #588).

## Boot and staging
- Kept, re-based onto the byte port. Kept parts: bootloader header read + CRC, console `load`, the JTAG ER1 stager (a second requester on the write path), `cynthion-payload`.
- Programming paths: [`../ecp5/programming-paths.md`](../ecp5/programming-paths.md).

## Reference
- Measured envelope of the same core: [`sequential-baseline.md`](sequential-baseline.md).
- Part facts: [`w956a8.md`](w956a8.md).
