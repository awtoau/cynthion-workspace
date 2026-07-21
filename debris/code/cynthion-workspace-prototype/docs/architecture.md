# Cynthion Stack — Hardware Architecture and Patches

## Hardware block diagram

```
  HOST PC
  ├─ CONTROL USB ──(1d50:615c)──► Apollo ARM MCU ──UART(R14/T14)──► ECP5 FPGA
  │                                     │   │                              │
  │                                  int│   └──JTAG──► ECP5 fabric         │
  │                               (T6)  │                    │             │
  │                                     └──────────── VexRiscv soft core ◄─┘
  │
  ├─ TARGET-A USB ─(1d50:615b)──► ECP5 FPGA ── moondancer gateware (facedancer mode)
  │                                                  subclass 0x20
  └─ TARGET-C USB ──────────────► UTi261M thermal camera (0bda:5830, UVC)
                                  (proxied by facedancer → TARGET-A → host)
```

**Cynthion** — Great Scott Gadgets USB test instrument
- USB VID:PID: 1d50:615b (all gateware modes: analyzer, facedancer)
- Apollo bootloader: 1d50:60e6 (shown when no gateware is loaded)
- The VID:PID alone does not tell you which gateware is running —
  check the USB interface subclass: 0x10 = analyzer, 0x20 = moondancer/facedancer

**UTi261M** — UNI-T thermal imaging camera, controlled by UNIT Android app
- USB VID:PID: 0bda:5830 (Realtek UVC chip)
- Presents as USB Video Class (UVC) device
- Connects to Cynthion TARGET-C port for proxy

## Device states and transitions

```
Power on (gateware flashed)  →  1d50:615b  analyzer or facedancer mode
Power on (no gateware)       →  1d50:60e6  Apollo bootloader

cynthion run facedancer      →  loads facedancer.bit + moondancer.bin via Apollo
                                device reappears at 1d50:615b with subclass 0x20

cynthion update              →  flashes analyzer.bit to config flash
                                device comes back as analyzer (subclass 0x10) after power cycle
```

After a proxy crash the Cynthion can become stuck at the Apollo stub level.
`./scripts/reset-cynthion.sh` recovers via soft reset. If Apollo has already ceded
CONTROL USB to the FPGA and the firmware is hung, a power cycle is required
(see issue [#15](https://github.com/awtoau/cynthion-workspace/issues/15)).

## CONTROL_SWITCH architecture

Apollo controls a USB mux between itself and the FPGA PHY. On boot Apollo holds
CONTROL. When moondancer loads, Apollo asserts PROGRAM_B to configure the FPGA
then cedes CONTROL USB to the FPGA. Once ceded, Apollo cannot be reached over
USB until a power cycle or until PROGRAM_B is used to reset the FPGA.

Multi-TTY plan: three CDC-ACM TTYs on CONTROL —
- `ttyACM0` (rv0) UART bridge to VexRiscv
- `ttyACM1` (fpg) FPGA event stream
- `ttyACM2` (apl) Apollo console / GDB RSP

See issue [#15](https://github.com/awtoau/cynthion-workspace/issues/15).

## Environment

### Quick start (workspace)

```bash
git clone --recurse-submodules https://github.com/awtoau/cynthion-workspace
cd cynthion-workspace
./scripts/machine-setup.sh    # one-time: OS packages + toolchains + venv
./scripts/check-fast.sh       # run before every commit
./cynthion_control.py --help  # unified CLI
```

### udev / permissions

udev rule: `/etc/udev/rules.d/54-cynthion.rules` (installed by `machine-setup.sh`)

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615c", MODE="0664", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615b", MODE="0664", GROUP="plugdev", TAG+="uaccess"
```

## Patches

All patches are tracked in source — no installed package files are edited directly.

### facedancer patches (`repos/facedancer/` — `awto` branch)

| Issue | File | Description |
|-------|------|-------------|
| [#8](https://github.com/awtoau/cynthion-workspace/issues/8)   | proxy.py | Catch `USBErrorIO` on isochronous `bulkRead` instead of crashing |
| [#9](https://github.com/awtoau/cynthion-workspace/issues/9)   | configuration.py | Skip pre-interface descriptors (e.g. IAD) that appear before any interface |
| [#10](https://github.com/awtoau/cynthion-workspace/issues/10) | backends/base.py | Downgrade duplicate endpoint address from exception to warning (UVC alt settings) |
| [#11](https://github.com/awtoau/cynthion-workspace/issues/11) | backends/moondancer.py | Deduplicate endpoints by address before calling `configure_endpoints` |

### firmware patch

| Issue | File | Description |
|-------|------|-------------|
| [#11](https://github.com/awtoau/cynthion-workspace/issues/11) | firmware/moondancer/src/gcp/moondancer.rs | Clamp endpoint `max_packet_size` to `EP_MAX_PACKET_SIZE` (512) instead of returning `EINVAL` for SS devices |

## Isochronous support (issue [#11](https://github.com/awtoau/cynthion-workspace/issues/11), in progress)

Full isochronous support requires changes at three layers:

### Gateware (`repos/cynthion/cynthion/python/src/gateware/facedancer/`)

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

### Firmware (`repos/cynthion/firmware/moondancer/src/gcp/moondancer.rs`)

- GCP verb `0x10` (`iso_in_write`): stub only until bitstream is rebuilt

### Python (`repos/facedancer/` — `awto` branch)

- `proxy.py`: `handle_nak` routes isochronous IN to `_proxy_iso_in_transfer`
- `proxy.py`: `_proxy_iso_in_transfer` reads one frame via libusb1 isochronous transfer
- `backends/moondancer.py`: `send_iso_in_frame` calls GCP verb `0x10`

### Next steps

1. Rebuild the facedancer bitstream (requires yosys, nextpnr-ecp5)
2. Regenerate `moondancer-pac` from the new SoC description
3. Wire `iso_in_write` firmware stub to the real `ep_iso_in` CSR registers
4. Test end-to-end: camera → isoRead → iso_in_write → ep_iso_in FIFO → host
