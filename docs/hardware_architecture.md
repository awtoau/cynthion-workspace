## Hardware Architecture

### Block Diagram

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
- USB interface subclass: 0x10 = analyzer, 0x20 = moondancer/facedancer

Apollo firmware flashing uses the Saturn-V DFU bootloader state (`1d50:60e6`).
If the board enumerates as `1d50:615b`, it is in analyzer/facedancer mode and
is not ready for Apollo DFU flashing.

**UTi261M** — UNI-T thermal imaging camera
- USB VID:PID: 0bda:5830 (Realtek UVC chip)
- Proxied through Cynthion TARGET-C port

### Device States & Transitions

```
Power on (gateware flashed)  →  1d50:615b  analyzer or facedancer mode
Power on (no gateware)       →  1d50:60e6  Apollo bootloader

cyn riscv build && cyn fpga build  →  builds moondancer + gateware
cyn deploy --release              →  full build + flash cycle
cyn reset                         →  soft reset to Apollo mode
```

**Recovery**: If Cynthion becomes stuck at Apollo level after a proxy crash:
```bash
cyn reset  # soft reset via Apollo
```

If Apollo has ceded CONTROL USB to hung firmware:
- Power cycle required (see [Issue #15](https://github.com/awtoau/cynthion-workspace/issues/15))

### CONTROL_SWITCH Architecture

Apollo controls a USB mux between itself and the FPGA PHY:

| Operation | Control | Effect |
|-----------|---------|--------|
| Boot | Apollo holds | CONTROL USB accessible, FPGA in reset |
| moondancer loads | Apollo asserts PROGRAM_B | FPGA configures from flash |
| Configuration done | Apollo cedes CONTROL | CONTROL USB switches to FPGA |
| Hung firmware | N/A | Power cycle required to recover |

**Multi-TTY Plan** (Issue [#15](https://github.com/awtoau/cynthion-workspace/issues/15)):
- `ttyACM0` (rv0) — UART bridge to VexRiscv
- `ttyACM1` (fpg) — FPGA event stream
- `ttyACM2` (apl) — Apollo console / GDB RSP

### Firmware Patches

All patches are tracked in source, applied to the vendored dependency trees:

| Issue | Component | File | Description |
|-------|-----------|------|-------------|
| [#8](https://github.com/awtoau/cynthion-workspace/issues/8) | facedancer | configuration.py | Skip pre-interface descriptors (e.g. IAD) before first interface |
| [#9](https://github.com/awtoau/cynthion-workspace/issues/9) | facedancer | backends/base.py | Downgrade duplicate endpoint address exception to warning (UVC alt settings) |
| [#10](https://github.com/awtoau/cynthion-workspace/issues/10) | facedancer | backends/moondancer.py | Deduplicate endpoints by address before configure_endpoints |
| [#43](https://github.com/awtoau/cynthion-workspace/issues/43) | moondancer | firmware/moondancer/src/gcp/moondancer.rs | Clamp endpoint max_packet_size to 512 bytes (HS limit) instead of rejecting SuperSpeed devices |

### Isochronous Support (Issue [#11](https://github.com/awtoau/cynthion-workspace/issues/11))

Full isochronous support requires changes at three layers:

**Gateware** ✅ Complete
- `cynthion/python/src/gateware/facedancer/ep_iso_in.py` — Amaranth CSR peripheral for isochronous IN transfers
- Wired into usb0 at CSR 0x00001700, IRQ 14, endpoint 1 (max_packet_size=128)
- Awaiting bitstream rebuild

**Firmware** 🟡 Stubbed
- GCP verb 0x10 (`iso_in_write`) defined but not yet wired to CSR registers

**Python** ✅ Ready
- `proxy.py`: routes isochronous IN to `_proxy_iso_in_transfer`
- `backends/moondancer.py`: `send_iso_in_frame` calls GCP verb 0x10

See [Issue #11](https://github.com/awtoau/cynthion-workspace/issues/11) for detailed implementation status.

---

