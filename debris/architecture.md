# Cynthion Stack — Hardware Architecture and Patches

> **Note:** Detailed hardware architecture, device state diagrams, and patch documentation have been consolidated into [hardware_architecture.md](../docs/hardware_architecture.md) as the canonical reference. This document focuses on architectural decisions and implementation status.


## Hardware Architecture

See [**hardware_architecture.md**](../docs/hardware_architecture.md) for detailed block diagrams, device states, CONTROL_SWITCH design, and udev setup.

**Key references:**
- [Issue #15](https://github.com/awtoau/cynthion-workspace/issues/15) — Apollo supervisor architecture (watchdog, halt notification, UART forwarding)

## Development Setup

```bash
git clone --recurse-submodules https://github.com/awtoau/cynthion-workspace
cd cynthion-workspace
cyn setup                     # full setup (sequential)
cyn setup --parallel          # setup with parallelization (55% faster)
cyn check                     # run checks before every commit
cyn list                      # list all available commands (target-based architecture)
```

**Documentation:**
- [Cyn CLI Architecture](../docs/cyn_cli_architecture.md) — target-based command structure
- [udev rules / USB permissions](../docs/apollo_uart_spi_design_conflict_analysis.md#udev-rules) — device access setup

## Patches

All patches are tracked in source — no installed package files are edited directly.

### facedancer patches (`vendor/facedancer/` — `awto` branch)

| Issue | File | Description |
|-------|------|-------------|
| [#8](https://github.com/awtoau/cynthion-workspace/issues/8)   | configuration.py | Skip pre-interface descriptors (e.g. IAD) that appear before any interface |
| [#9](https://github.com/awtoau/cynthion-workspace/issues/9)   | backends/base.py | Downgrade duplicate endpoint address from exception to warning (UVC alt settings) |
| [#10](https://github.com/awtoau/cynthion-workspace/issues/10) | backends/moondancer.py | Deduplicate endpoints by address before calling `configure_endpoints` |

### firmware patches

| Issue | File | Description |
|-------|------|-------------|
| [#43](https://github.com/awtoau/cynthion-workspace/issues/43) | firmware/moondancer/src/gcp/moondancer.rs | Clamp endpoint `max_packet_size` to `EP_MAX_PACKET_SIZE` (512) instead of returning `EINVAL` for SuperSpeed devices |

## Isochronous support (issue [#11](https://github.com/awtoau/cynthion-workspace/issues/11), in progress)

Full isochronous support requires changes at three layers:

### Gateware (`vendor/cynthion/cynthion/python/src/gateware/facedancer/`)

`ep_iso_in.py` — Amaranth CSR peripheral wrapping LUNA's `USBIsochronousStreamInEndpoint`.

| Register | Access | Description |
|----------|--------|-------------|
| `bytes_in_frame` | W | Arm the next frame: set to payload byte count before each SOF |
| `status.frame_pending` | R | Set on each USB SOF; cleared when `bytes_in_frame` is written |
| `status.overflow` | R | Set if DATA was written while FIFO was full |
| `reset.fifo` | W | Clear FIFO and reset frame state |
| `data` | W | Payload byte FIFO — write one byte per access |

`top.py` — `ep_iso_in` wired into usb0 at CSR `0x00001700`, IRQ 14, endpoint 1, `max_packet_size=128`.

**Status:** code complete, requires bitstream rebuild.

### Firmware (`vendor/cynthion/firmware/moondancer/src/gcp/moondancer.rs`)

- GCP verb `0x10` (`iso_in_write`): stub only until bitstream is rebuilt

### Python (`vendor/facedancer/` — `awto` branch)

- `proxy.py`: `handle_nak` routes isochronous IN to `_proxy_iso_in_transfer`
- `proxy.py`: `_proxy_iso_in_transfer` reads one frame via libusb1 isochronous transfer
- `backends/moondancer.py`: `send_iso_in_frame` calls GCP verb `0x10`

### Next steps

1. Rebuild the facedancer bitstream (requires yosys, nextpnr-ecp5)
2. Regenerate `moondancer-pac` from the new SoC description
3. Wire `iso_in_write` firmware stub to the real `ep_iso_in` CSR registers
4. Test end-to-end: camera → isoRead → iso_in_write → ep_iso_in FIFO → host
