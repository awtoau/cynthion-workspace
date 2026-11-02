# Apollo firmware: the SoC needs the fork

**Index:** [`hardware.md`](hardware.md) · [`upstream-boundary.md`](upstream-boundary.md)

- The SoC loop (`soc_run.py`, the sideband, JTAG configure, flash writes) runs against **our Apollo fork** (`repos/apollo`, `awtoau/awto-apollo`), not upstream's.
- Upstream Apollo lacks what the SoC uses: the FPGA_ADV sideband command layer, DMA JTAG, fast configure, quad flash reads.
- `soc_run.py` refuses to configure against upstream firmware (verdict `stock-apollo`, [`scripts/soc_confirm.py`](../scripts/soc_confirm.py)).

## Which firmware is on the board

| `cynthion info` → Firmware version | meaning |
|---|---|
| `v1.1.1-64-g90c8b7b` (commits after the tag) | the fork |
| `v1.1.1` (bare tag) | upstream's release — wrong for the SoC |

## What replaces the fork with upstream's

- `cynthion update`, `cynthion update --mcu-firmware`: flash the `apollo.bin` shipped in the `cynthion` wheel.
- `cynthion update` also writes upstream's analyzer bitstream to the FPGA flash.
- Last time this happened: 2026-11-02, to rule the fork out of a Packetry capture.

## Restore the fork

```bash
make -C repos/apollo/firmware APOLLO_BOARD=cynthion clean
make -C repos/apollo/firmware APOLLO_BOARD=cynthion -j8
python3 scripts/apollo/verify_vectors.py        # LTO can link a 4-byte binary; this catches it
fwup-util --device 1d50:615c repos/apollo/firmware/_build/cynthion_d11/firmware.bin
cynthion info --force-offline            # expect v1.1.1-<n>-g<sha>
```

- Needs Apollo on the bus (`1d50:615c`). Replug first if the board shows `1d50:615b`.
- Size on 2026-11-02 at `90c8b7b`: 13,688 B of 14,336 B. Budget check: [`scripts/apollo/apollo_budget_check.py`](../scripts/apollo/apollo_budget_check.py).

## After `cynthion update`: the board boots the analyzer

- The FPGA flash then holds upstream's analyzer, so a replug enumerates `1d50:615b`, not Apollo.
- `apollo force-offline` brings Apollo back (the stub stays alive behind the analyzer).
- `soc_run.py` configures the FPGA's RAM only; a replug drops the SoC and boots the flash image.

## Open

- Packetry capture of the DJI link was tried on upstream firmware only (no packets). Not tried on the fork.
- [#549](https://github.com/awtoau/cynthion-workspace/issues/549): UART framing errors seen while upstream firmware was on the board; recheck on the fork.
