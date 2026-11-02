# USB data path (stage 1)

- Status: built and simulated; not in `top.py` ([#578](https://github.com/awtoau/cynthion-workspace/issues/578)); no hardware run.
- Spec: [#580](https://github.com/awtoau/cynthion-workspace/issues/580) (supersedes [#574](https://github.com/awtoau/cynthion-workspace/issues/574): no DMA, no memory, no generator).
- Stage 1 is uncompressed: both codec slots are pass-through.

## Shape

```
PC (libusb, interface 2)                      Cynthion                                   phone
EP1 OUT ─► HeaderParser ─► desc queue ─► RV (CSR, IRQ)
                   └─ payload ─► CodecStage ─► OUT ring ─► PacketSequencer ◄─► UsbPort engine port ─► SIE ─► TARGET
EP1 IN  ◄─ BlockBuilder ◄─ CodecStage ◄─ IN FIFO ◄────────────┘   (header = RV's result descriptor)
EP2 OUT/IN ◄─► CTL byte port (CSR) ◄─► RV
```

- RV: control only, per block. Pops a descriptor, arms the sequencer, reads the result, writes the result header, sends. Never touches payload.
- Gateware, per packet: MPS slices between the FIFOs and the host engine, NAK retry, toggle, stop conditions.
- The CPU pump (`usb_host` CSRs `TX_DATA`/`RX_DATA`, `firmware/cynthion-soc/src/usb_host.rs`) is unchanged; it does enumeration and control transfers.
- Host side is general: any address/endpoint, bulk and interrupt. Nothing AOA-specific.

| Piece | File |
|---|---|
| block header (gateware, sims, PC tool) | [`gateware/soc/datapath/header.py`](../gateware/soc/datapath/header.py) |
| parser | [`gateware/soc/datapath/parser.py`](../gateware/soc/datapath/parser.py) |
| OUT ring / IN FIFO (`RewindFifo`) | [`gateware/soc/datapath/ring.py`](../gateware/soc/datapath/ring.py) |
| packet sequencer | [`gateware/soc/datapath/sequencer.py`](../gateware/soc/datapath/sequencer.py) |
| block builder | [`gateware/soc/datapath/builder.py`](../gateware/soc/datapath/builder.py) |
| codec slot | [`gateware/soc/datapath/codec_stage.py`](../gateware/soc/datapath/codec_stage.py) |
| CSR block, top of the path | [`gateware/soc/datapath/core.py`](../gateware/soc/datapath/core.py) |
| AUX composite device | [`gateware/soc/datapath/device.py`](../gateware/soc/datapath/device.py) |
| engine port on the host shim | [`gateware/soc/peripherals/usb_host.py`](../gateware/soc/peripherals/usb_host.py) (`engine_port=True`) |
| simulation | [`scripts/soc/datapath_sim.py`](../scripts/soc/datapath_sim.py) |
| cells | [`scripts/soc/datapath_area.py`](../scripts/soc/datapath_area.py) |
| RV driver, shell verb | [`firmware/cynthion-soc/src/datapath.rs`](../firmware/cynthion-soc/src/datapath.rs), [`src/shell/datapath.rs`](../firmware/cynthion-soc/src/shell/datapath.rs) |
| PC reference tool | [`scripts/datapath_pc.py`](../scripts/datapath_pc.py) |

## Device port (AUX)

- Today: LUNA `USBSerialDevice` `1d50:6180`: EP0, EP3 IN interrupt (unused), EP4 bulk pair (console). Finding: [#580 comment](https://github.com/awtoau/cynthion-workspace/issues/580#issuecomment-6590095005).
- `DataPathDevice` replaces it: interfaces 0/1 byte-identical (sim checks it), plus interface 2 class 0xFF:
  - EP1 OUT/IN bulk 512: data blocks.
  - EP2 OUT/IN bulk 512: control bytes to/from the RV. Cost question: [#576](https://github.com/awtoau/cynthion-workspace/issues/576).
- `cdc_acm` binds 0/1 through the IAD; interface 2 has no kernel driver; libusb claims it.

## Block header

8 bytes, little endian, at the start of every block in both directions (`header.py` is the one definition).

| Byte | Field | Request (PC -> Cynthion) | Result (Cynthion -> PC) |
|---|---|---|---|
| 0 | MAGIC | `0xDB` | `0xDB` |
| 1 | FLAGS | [3:0] endpoint, [4] dir (0 OUT to phone, 1 IN from phone), [6:5] type (bmAttributes: 0 ctrl 1 iso 2 bulk 3 int), [7] zlp | copied from the request |
| 2 | TAG | PC's choice | echoed |
| 3 | AUX | [3:0] codec (0 = none) | [3:0] codec, [4] more, [5] short |
| 4 | STATUS | 0 | result code, below |
| 5-7 | LENGTH | OUT: payload bytes that follow; IN: most bytes wanted | bytes moved (OUT: accepted by the phone; IN: payload that follows) |

- One USB transfer = one block. LENGTH frames the block, not the USB transfer end: a block may be split across transfers.
- Stage 1 limit: LENGTH <= 65536 (`MAX_LEN`).
- OUT block of length 0: one ZLP. `zlp` set and LENGTH a multiple of MPS: a ZLP after the data.
- Every request gets exactly one result, except IN, which gets one per segment (below).
- STATUS: 0 ok, 1 stall, 2 nak_limit, 3 error, 4 bad_header, 5 rejected, 6 overrun.
- Bad MAGIC or LENGTH > MAX: the parser drops bytes through the end of that USB transfer (`last`), then queues one descriptor with `D_ERR` set; the RV answers `bad_header`. This is the resync.
- `short` (IN): the phone ended its transfer with a short packet or ZLP.
- `overrun` (IN): the phone sent more than LENGTH in the last packet; the extra bytes are returned (MPS must match the endpoint's `wMaxPacketSize`).

### IN segments

- The result header precedes its payload and holds the byte count, so the payload waits in the IN FIFO (2048 B) until the segment stops.
- The sequencer stops an IN segment when another MPS would not fit (`R_FLAGS.seg`). The RV sends it with `more` = 1 and re-arms for the remainder.
- The PC concatenates blocks with the same TAG until `more` = 0. A 64 KiB request is 32 blocks.
- Decision record: [#577](https://github.com/awtoau/cynthion-workspace/issues/577).

## Packet sequencer

Per armed descriptor (`sequencer.py`):

- OUT: packets of min(MPS, left) from the OUT ring. ACK commits the ring read; anything else rewinds it, so a NAKed packet is re-sent from the same bytes (the SIE consumes its TX FIFO on send and keeps no copy).
- IN: an IN token only when the IN FIFO has MPS free (back-pressure to the phone). Data with the expected DATA0/1 is kept and toggles; a repeated PID (lost ACK) is dropped.
- Toggle: RV supplies the start toggle; `R_RESP.toggle` is the next one; the RV keeps it per (endpoint, direction).
- Stops: LENGTH moved; short packet (IN); STALL; NAK limit; 3 errors in a row; abort; IN segment full.

### Bounds

| Wait | Bound | From | On expiry |
|---|---|---|---|
| NAKs in a row | `NAK_LIMIT`, reset 1000, programmable | min HS NAKed IN ~60 usb cycles (token 8 + turnaround + handshake 6 + SIE IPD 24 + shim CDC/drain ~20) = 1 us, so 1000 = one FS frame (1 ms); a NAKed 512 B OUT also re-pushes the packet (~8.5 us at sync 60 MHz), so ~10 ms | descriptor stops, `R_FLAGS.nak_limit`, bytes so far kept, IRQ; RV reports `nak_limit` |
| transaction errors in a row | 3 (`ERR_LIMIT`) | USB 2.0 8.6.4 three-strike retry | `R_FLAGS.error`, IRQ |
| one transaction | `usb_host` `XFER_LIMIT` (unchanged) | 1 FS frame + SIE IPD, x1.25 | SIE reset, response NONE: counts as an error |
| RV waits | firmware `datapath.rs` constants | per-wait arithmetic in the code | `Timeout(op)` with elapsed |

- `NAK_LIMIT` = 0 or 1: stop on the first NAK.
- An IN from a phone with nothing to send ends `nak_limit` with 0 bytes; the PC re-requests.

## Register map

`core.py` docstring is the source; 8-bit CSR, 4 per 32-bit word, read side effects alone in a word. Window 64 bytes.

| Off | Name | Access | Bits |
|---|---|---|---|
| 0x00 | IRQ_EN | rw | 0 desc 1 done 2 sent 3 ctl_rx |
| 0x01 | EVENTS | rw1c | 0 done 1 sent |
| 0x02 | CTRL | w | 0 pop 1 arm 2 send 3 abort 4 flush (strobes) |
| 0x03 | STATUS | r | 0 desc_ready 1 seq_busy 2 tx_busy [5:3] desc count 6 present |
| 0x04 | D_FLAGS | r | head descriptor header byte 1 |
| 0x05 | D_TAG | r | byte 2 |
| 0x06 | D_AUX | r | byte 3 |
| 0x07 | D_ERR | r | 0 bad magic 1 length > MAX |
| 0x08-0x0a | D_LEN | r | 17 bit |
| 0x0c | ADDR | rw | [6:0] device address |
| 0x0d | EP | rw | [3:0] endpoint 4 dir_in 5 start toggle 6 zlp 7 discard |
| 0x0e-0x0f | MPS | rw | 10 bit, reset 512 |
| 0x10-0x12 | LEN | rw | 17 bit |
| 0x14-0x15 | NAK_LIMIT | rw | 16 bit, reset 1000 |
| 0x18 | R_RESP | r | [2:0] last response (`usb_host` RESP codes) 3 next toggle |
| 0x19 | R_FLAGS | r | 0 short 1 nak_limit 2 stall 3 error 4 overrun 5 seg 6 abort |
| 0x1a-0x1b | R_NAKS | r | NAKs seen, saturating |
| 0x1c-0x1e | R_MOVED | r | 18 bit |
| 0x20-0x23 | H_FLAGS H_TAG H_AUX H_STATUS | rw | result header bytes 1-4 |
| 0x24-0x26 | H_LEN | rw | 18 bit |
| 0x28 | CTL_RX | r | POPS one control OUT byte |
| 0x2c | CTL_STATUS | r | 0 rx_valid 1 rx_last 2 tx_ready |
| 0x2d | CTL_TX | w | push a byte |
| 0x2e | CTL_TX_LAST | w | push the transfer's last byte |

- IRQ: level, `(IRQ_EN & {ctl_rx, sent, done, desc_ready})`; one per descriptor (`desc`) and one per finish (`done`).
- Arm registers and H_* must not change while `seq_busy` / `tx_busy`.
- `flush`: empties parser, descriptor queue, both FIFOs. Only with `seq_busy` clear.
- `discard` (EP.7, OUT): LEN bytes leave the OUT ring unsent.

## RV sequence (per descriptor)

The Python `Rv` class in `datapath_sim.py` is the reference; `datapath.rs` follows it.

1. `STATUS.desc_ready` (or IRQ): read D_*, write CTRL.pop.
2. `D_ERR` != 0: result `bad_header`, length 0. Done.
3. codec != 0, or type not bulk/interrupt: OUT with LENGTH > 0 is armed with `discard`; result `rejected`. Done.
4. Before the first arm on a port: `usb_host` CTRL.clr_rx (the RX FIFO must not hold pump bytes).
5. Write ADDR, EP (endpoint, dir, toggle for (ep, dir), zlp), MPS, LEN = left; CTRL.arm.
6. Wait `seq_busy` clear (or `done`). Read R_RESP, R_FLAGS, R_MOVED, R_NAKS. Store the toggle.
7. Wait `tx_busy` clear. Write H_* (flags, tag, aux | more | short, status, moved); CTRL.send.
8. OUT stopped early (status != ok, moved < LENGTH): arm `discard` for LENGTH - moved.
9. IN with `seg` and ok and not short and moved < left: left -= moved, back to 5.

## Codec stage

- `CodecStage`: `sink`/`source` byte streams in `capture.codec.byte_stream_signature()` shape (data, valid, last, ready), plus `codec_id`.
- The capture codecs (#563, `gateware/soc/capture/codec.py`) frame 1 KiB blocks with 12-bit lengths; data-path blocks are up to 64 KiB. Their stream shape fits; their block signature does not. A later codec drops in behind an adapter that splits a block into codec blocks.
- With compression, OUT LENGTH would be the coded length; the raw length for the sequencer is an open item for that stage.

## Wiring (not done; [#578](https://github.com/awtoau/cynthion-workspace/issues/578))

- `DataPathDevice` on AUX in place of `USBSerialDevice` (`usb` domain).
- 9-bit (`last` + byte) `StreamBuffer`s usb <-> sync for EP1 OUT/IN and EP2 OUT/IN.
- `DataPath` in `sync`; its `eng_*` to `UsbPort(engine_port=True)` `eng_*` (same names, flipped directions).
- CSR window 64 B; one IRQ source.

## Cells

Module alone, yosys `synth_ecp5`, no place and route (`scripts/soc/datapath_area.py`, 2026-11-03, commit 80e62b0):

| Config | LUT4 | CCU2C | FF | DP16KD | DPR16X4 |
|---|---|---|---|---|---|
| `DataPath` | 885 | 200 | 483 | 2 | 11 |
| `UsbPort` engine port (delta) | +65 | 0 | 0 | 0 | 0 |
| `DataPathDevice` - `USBSerialDevice` | +922 | +63 | +360 | +6 | 0 |

## Simulation

`scripts/soc/datapath_sim.py` (in `scripts/soc_sims.py`):

- Model engine (Python UsbPort engine port + scripted phone): OUT/IN byte exact 1 B .. 64 KiB at MPS 512 and 64, packet-boundary sizes, ZLP, toggles across blocks, NAK retry and limit, error retry and stop, STALL, bad header, length > MAX, rejected codec, back-pressure both ways, control byte port.
- `mutation`: the OUT ring with rewind disconnected; the NAK check must fail, and does.
- `engine`: real `UsbPort` + SIE + ULPI PHY model + a LUNA sink/source device; enumeration by the CPU pump, then blocks through the data path, real NAKs from a held sink.
- `device`: `DataPathDevice` enumerated by a host SIE; descriptors, all four new endpoints, console EP4.
- Not covered: silicon, the usb <-> sync crossings of the wiring, a real phone, compression.
