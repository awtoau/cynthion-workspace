# Hardware

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

# Environment

## Python venv

```
./scripts/setup-venv.sh
```

Both `cynthion` and `facedancer` are installed as editable installs from
source (not from PyPI), so changes in `cynthion/python/` and
`vendor/facedancer/` take effect immediately without reinstalling.

After cloning or pulling:

```
git submodule update --init
venv/bin/pip install -e cynthion/python/
venv/bin/pip install -e vendor/facedancer/
```

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
`./scripts/reset-cynthion.sh` recovers via soft reset. If the stub itself is
unresponsive, a power cycle is required (issue #7).

## udev / permissions

udev rule: `/etc/udev/rules.d/54-cynthion.rules`
User `dan` gets rw access via uaccess ACL — no plugdev group required.

# Running the proxy

```
./scripts/run-proxy-camera.sh
```

Loads facedancer gateware (flashes `moondancer.bin`), then starts the
UTi261M ↔ host proxy. The script handles soft reset recovery on startup.

# Firmware

Moondancer firmware is RISC-V (`riscv32imac-unknown-none-elf`), built with:

```
cd firmware && make build
```

The flat binary (`moondancer.bin`) lives at `cynthion/python/assets/moondancer.bin`
and is loaded onto Cynthion by `cynthion run facedancer`.  Our patched version
clamps SuperSpeed endpoint `max_packet_size` values to the HS maximum (512 bytes)
rather than returning `EINVAL` — see cynthion#220.

# Patches

All patches are tracked in source — no venv files are edited directly.

## facedancer patches (`vendor/facedancer/` — `awto` branch)

| Issue | File | Description |
|-------|------|-------------|
| awtoau/cynthion#9  | proxy.py | Catch `USBErrorIO` on isochronous `bulkRead` instead of crashing |
| awtoau/cynthion#10 | configuration.py | Skip pre-interface descriptors (e.g. IAD) that appear before any interface |
| awtoau/cynthion#11 | backends/base.py | Downgrade duplicate endpoint address from exception to warning (UVC alt settings) |
| awtoau/cynthion#12 | backends/moondancer.py | Deduplicate endpoints by address before calling `configure_endpoints` |
| awtoau/cynthion#13 | proxy.py, backends/moondancer.py | Isochronous IN transfer path skeleton (see below) |

## firmware patch

| Issue | File | Description |
|-------|------|-------------|
| cynthion#220 | firmware/moondancer/src/gcp/moondancer.rs | Clamp endpoint `max_packet_size` to `EP_MAX_PACKET_SIZE` (512) instead of returning `EINVAL` for SS devices |

# Isochronous support (issue #13, in progress)

Full isochronous support requires changes at three layers. Current state:

## Gateware (`cynthion/python/src/gateware/facedancer/`)

`ep_iso_in.py` — new Amaranth CSR peripheral wrapping LUNA's
`USBIsochronousStreamInEndpoint`.  Exposes a FIFO-backed write interface to
the RISC-V CPU:

| Register | Access | Description |
|----------|--------|-------------|
| `bytes_in_frame` | W | Arm the next frame: set to payload byte count before each SOF |
| `status.frame_pending` | R | Set on each USB SOF; cleared when `bytes_in_frame` is written |
| `status.overflow` | R | Set if DATA was written while FIFO was full |
| `reset.fifo` | W | Clear FIFO and reset frame state |
| `data` | W | Payload byte FIFO — write one byte per access |

`top.py` — `ep_iso_in` peripheral wired into usb0 (target PHY) at CSR address
`0x00001700`, IRQ 14, endpoint 1, `max_packet_size=128` (UTi261M alt setting 1).

**Status:** code complete, but **requires bitstream rebuild** before the hardware
path is active.  `cynthion run facedancer` still loads the old bitstream.

## Firmware (`firmware/moondancer/src/gcp/moondancer.rs`)

- `TransferType` enum tracks control/isochronous/bulk/interrupt per endpoint
- `configure_endpoints` now stores `transfer_type` for each configured endpoint
- GCP verb `0x10` (`iso_in_write`): accepts `(endpoint_number, payload)`, logs
  the call, returns OK — **stub only** until bitstream is rebuilt and PAC
  regenerated to expose the `ep_iso_in` CSR registers

## Python (`vendor/facedancer/` — `awto` branch)

- `proxy.py`: `handle_nak` routes isochronous IN endpoints to
  `_proxy_iso_in_transfer` instead of the bulk NAK path
- `proxy.py`: `_proxy_iso_in_transfer` reads one frame from the camera via
  libusb1 isochronous transfer (`LibUSB1Device.isoRead`) and calls
  `backend.send_iso_in_frame()`
- `proxy.py`: `LibUSB1Device.isoRead` — synchronous single-packet isochronous
  read using the libusb1 async API with event-loop pumping
- `backends/moondancer.py`: `send_iso_in_frame` calls GCP verb `0x10`

## Next steps for #13

1. Rebuild the facedancer bitstream (requires ECP5 toolchain: yosys, nextpnr-ecp5)
2. Regenerate `moondancer-pac` from the new SoC description
3. Wire `iso_in_write` firmware stub to the real `ep_iso_in` CSR registers
4. Test end-to-end: camera → isoRead → iso_in_write → ep_iso_in FIFO → host
