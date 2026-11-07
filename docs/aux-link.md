# AUX link specification (version 1)

Wire contract between the PC (Linux UDC driver, [`linux/cynthion_udc/`](../linux/cynthion_udc/README.md)) and the Cynthion (firmware relay + gateware FIFOs) for USB device mode. Product and decisions: [#612](https://github.com/awtoau/cynthion-workspace/issues/612) (option A, USB-native framing, Dan 2026-11-07); design: [`usb-device-mode.md`](usb-device-mode.md); device engine: [`gateware/usbcore/device.py`](../gateware/usbcore/device.py) (`Ep0Device`).

- One document, three implementers: **kernel backend** (`cudc_aux`), **firmware relay**, **gateware FIFOs**. Section 12 splits the duties.
- Words: **PC** = AUX host (xHCI). **Cynthion** = AUX device. **remote host** = whatever is cabled to TARGET. **TARGET endpoint** = an endpoint the remote host sees. **pipe** = an AUX endpoint carrying one TARGET endpoint's data.
- Numbers little-endian. `u8/u16/u32`. All sizes in bytes.
- Terms: "MUST" = interop requirement; "SHOULD" = recommended.

## 1. Topology

```
remote host ==TARGET== [Ep0Device | EPn FIFOs] --gateware-- [AUX device] ==AUX== PC xHCI -> cudc_aux -> gadget core
                        \__ firmware relay (control plane) __/
```

- Control plane (EP0 transfers, endpoint config, bus events) = firmware, through the **control channel** (framed messages).
- Data plane (TARGET EPn) = gateware only: TARGET EPn FIFO <-> AUX pipe; the CPU never touches data.
- Device mode is a separate bitstream ([#612](https://github.com/awtoau/cynthion-workspace/issues/612) decision); the AUX composite is fixed.

## 2. AUX descriptors

Composite device on the AUX port; the bridge is one vendor interface next to the console.

| item | value |
|---|---|
| interfaces 0, 1 | console CDC-ACM (unchanged: comm interface, data interface) |
| interface 2 | bridge: `bInterfaceClass 0xFF`, `bInterfaceSubClass 0x43`, `bInterfaceProtocol 0x01` (= link version 1), `bAlternateSetting 0` only, 10 endpoints, `iInterface` string "Cynthion UDC link" |
| match key (driver) | the interface triple `FF/43/01` on the Cynthion VID/PID of the device-mode bitstream ([`gateware/usb_ids.py`](../gateware/usb_ids.py) owns the PID); the interface number is NOT assumed |
| protocol bump | a different `bInterfaceProtocol` = different, incompatible link; the driver does not bind it |

Endpoint set of interface 2 (all bulk; `wMaxPacketSize` 512 at HS, 64 at FS = the AUX link's own speed):

| AUX endpoint | dir | carries | gateware |
|---|---|---|---|
| `0x83` | IN | **control channel**, Cynthion -> PC (events, replies) | firmware TX queue |
| `0x03` | OUT | **control channel**, PC -> Cynthion (commands) | firmware RX queue |
| `0x84`..`0x87` | IN | pipe for TARGET `0x01`..`0x04` (TARGET OUT -> PC) | FIFO n, TARGET OUT side writes |
| `0x04`..`0x07` | OUT | pipe for TARGET `0x81`..`0x84` (PC -> TARGET IN) | FIFO n, AUX OUT side writes |

- Pipe address rule: `aux_addr = (target_addr ^ 0x80) + 3`. Direction flips because the data direction relative to the PC flips: remote-host OUT is AUX IN.
- Console endpoints stay `0x81` (bulk IN), `0x01` (bulk OUT), `0x82` (interrupt IN). The bridge endpoints do not overlap them. Totals: 7 IN + 6 OUT of 15 each.
- Interrupt TARGET endpoints use a bulk pipe (the TARGET-side interval is the remote host's business). `wMaxPacketSize` <= 512 (HS) / 64 (FS); no high-bandwidth.
- Isochronous: not carried in version 1 (EP_CONFIG answers `NAK UNSUPPORTED`).
- No alternate settings. The link starts with `HELLO` (section 5) and is reset by sending `HELLO` again.

## 3. Console coexistence

- Console = interfaces 0/1, own driver (`cdc_acm`), own endpoints. The bridge driver never touches them; the console never carries link frames.
- AUX bus is shared: bulk traffic of both uses leftover bandwidth; the console interrupt endpoint has its periodic reservation, so the bridge cannot starve it.
- Console on the other side of a firmware crash: link frames are only the control channel; a stalled firmware leaves the data pipes' FIFOs in whatever state gateware holds (section 8).
- The console stays in the AUX composite (answers open question 5 of [`usb-device-mode.md`](usb-device-mode.md#open-questions) for version 1).

## 4. Message framing (control channel)

```
 0        1        2   3     4   5    6 ...
 type u8 | ep u8 | seq u16 | len u16 | payload[len]
```

- One **message** = one USB transfer on a control-channel endpoint. Messages are not split across transfers, not concatenated.
- A transfer is a sequence of full `maxp` packets ended by a short packet. A message whose total size (6 + len) is a multiple of `maxp` MUST be followed by a zero-length packet (the PC sets `URB_ZERO_PACKET`; the Cynthion TX queue sends the ZLP).
- `len` = payload length, <= 4096 (`USB_COMP_EP0_BUFSIZ`). A message with `6 + len != transfer size` is `BAD_LEN`.
- PC keeps >= 4 IN transfers of 4608 bytes (9 x 512 and 72 x 64) outstanding on `0x83` at all times; no outstanding URB = the Cynthion NAKs (nothing is lost).
- `ep`: a TARGET endpoint address for `EP_*` messages (`0x00` = no endpoint). On `CTRL_DATA_IN` it carries flags (bit0 = `ZERO`, section 7).
- `seq`: see section 6 (which counter, who checks).
- Reserved bits and unknown payload bytes MUST be zero on send and ignored on receive. Payload longer than the type's definition: extra bytes ignored (forward compatible within version 1).

## 5. Message types

Direction: **C>P** = Cynthion to PC, **P>C** = PC to Cynthion. Numbers are type codes.

| code | name | dir | `ep` | `seq` | payload |
|---|---|---|---|---|---|
| `0x01` | `HELLO` | both | 0 | 0 | P>C `{u8 version=1, u8 max_speed, u16 reserved}`; C>P `{u8 version=1, u8 aux_speed, u16 max_payload=4096, u8 n_pipes, u8 features, u16 build, u16 fifo_pkts}` |
| `0x02` | `SETUP` | C>P | 0 | control epoch | 8 B setup packet, as received |
| `0x03` | `CTRL_DATA_OUT` | C>P | 0 | control epoch | complete OUT data stage (`wLength` bytes <= 4096) |
| `0x04` | `CTRL_DATA_IN` | P>C | flags | control epoch | IN data stage reply, 0..4096 B (0 = data stage with a ZLP) |
| `0x05` | `STATUS` | P>C | 0 | control epoch | `{u8 result}`: 0 = ACK (complete the status stage), 1 = STALL |
| `0x06` | `RESET` | C>P | 0 | new reset epoch | `{u8 speed}`: 1 = FS, 2 = HS (bus reset and speed negotiation done) |
| `0x07` | `BUS_STATE` | C>P | 0 | 0 | `{u8 event, u8 arg, u16 flags}` (below) |
| `0x08` | `DEV_CONFIG` | P>C | 0 | 0 | `{u8 connect, u8 max_speed, u8 self_powered, u8 reserved}`: `connect` 1 = attach to TARGET (pullup), 0 = detach; `max_speed` 1 FS / 2 HS |
| `0x09` | `WAKEUP` | P>C | 0 | 0 | none. Remote wakeup request |
| `0x0A` | `EP_CONFIG` | P>C | target addr | reset epoch | `{u8 enable, u8 xfer, u16 maxp}`: `xfer` 2 bulk / 3 interrupt; `enable 0` disables (other fields ignored) |
| `0x0B` | `EP_HALT` | both | target addr | reset epoch (P>C) / 0 (C>P) | P>C `{u8 flags}` bit0 = wedge: STALL the endpoint. C>P: the remote host halted it (SET_FEATURE ENDPOINT_HALT), no payload |
| `0x0C` | `EP_CLEAR` | both | target addr | reset epoch (P>C) / 0 (C>P) | P>C: clear halt and wedge, toggle to DATA0 (function-initiated). C>P: the remote host cleared a non-wedged halt. No payload |
| `0x0D` | `EP_FLUSH` | P>C | target addr | reset epoch | `{u8 flags}` bit0 = `DISCARD`: drop the FIFO contents. Without `DISCARD`: notify only (counted) |
| `0x0E` | `EP_DATA` | both | target addr | 0 | **Reserved for option C** (interrupt endpoints on the control channel). Version 1 answers `NAK UNSUPPORTED` |
| `0x10` | `ACK` | C>P | 0 | current control epoch | `{u8 cmd_type, u8 cmd_ep, u16 cmd_seq}`: command accepted |
| `0x11` | `NAK` | C>P | 0 | current control epoch | `{u8 cmd_type, u8 cmd_ep, u16 cmd_seq, u8 code}`: command refused (section 10) |
| `0x12` | `ERROR` | C>P | 0 | current control epoch | `{u8 code, u8 detail, u16 reserved}`: asynchronous fault, not tied to a command |
| `0x13` | `GET_STATS` | P>C | 0 | 0 | none |
| `0x14` | `STATS` | C>P | 0 | 0 | 8 x `u32` counters, order in section 10 |

`BUS_STATE` events:

| `event` | name | `arg` | meaning |
|---|---|---|---|
| 1 | `SUSPEND` | 0 | TARGET idle >= 3 ms (USB 2.0 7.1.7.6; gateware detects) |
| 2 | `RESUME` | 0 | resume signalling seen, or remote wakeup done |
| 3 | `VBUS_ON` | 0 | TARGET VBUS present |
| 4 | `VBUS_OFF` | 0 | TARGET VBUS gone: the PC treats the device as detached |
| 5 | `ADDRESS` | address | SET_ADDRESS completed (applied after the status stage); 0 = back to default state |
| 6 | `FLAGS` | 0 | `flags` changed |

- `flags` (sent in every `BUS_STATE`): bit0 = DEVICE_REMOTE_WAKEUP enabled by the remote host, bit1 = self-powered (mirror of `DEV_CONFIG`), bit2 = TEST_MODE entered. The PC caches them (answers `wakeup()` and `GET_STATUS`-dependent logic without a round trip).
- `GET_STATUS`, `SET/CLEAR_FEATURE`, `SET_ADDRESS` never reach the PC; their effects arrive as `ADDRESS`, `FLAGS`, `EP_HALT`, `EP_CLEAR` ([`usb-device-mode.md`](usb-device-mode.md#locally-answered)).

## 6. Sequence rules

Two counters, both owned by the Cynthion, both `u16`, compared **for equality only** (no ordering, so wraparound needs no handling):

| counter | name | increments on | carried in | checked on |
|---|---|---|---|---|
| control epoch | `ctl_seq` | every SETUP token and every bus reset (= `Ep0Device.seq`; its low 8 bits are `Ep0Device.seq`/`cmd_seq`) | `SETUP`, `CTRL_DATA_OUT`, `RESET`, and P>C `CTRL_DATA_IN`, `STATUS` | `CTRL_DATA_IN`, `STATUS` |
| reset epoch | `rst_seq` | every bus reset (value of `ctl_seq` right after the reset) | `RESET` (as its `seq`) and P>C `EP_*` | `EP_CONFIG`, `EP_HALT`, `EP_CLEAR`, `EP_FLUSH` |

- **Stale = dropped and counted.** A P>C message whose `seq` differs from the current counter is not executed. The Cynthion answers `NAK STALE`, increments `stale_ctl` (control) or `stale_ep` (endpoint), and sends nothing to TARGET. `Ep0Device` already does this for `cmd_seq`.
- **New SETUP aborts the old.** On a SETUP token (USB 2.0 8.5.3) the Cynthion discards any armed data/status of the old transfer, bumps `ctl_seq`, sends `SETUP`. The PC, on receiving a `SETUP`, completes all queued EP0 requests with `-ECONNRESET` before calling the gadget. Late replies for the old epoch hit the stale rule.
- **Every PC command carries the counter it last saw** (the `seq` of the last `SETUP`/`RESET` it received, or the `rst_seq` for `EP_*`). `HELLO`, `DEV_CONFIG`, `WAKEUP`, `GET_STATS`, `EP_DATA` carry `seq 0` and are never stale.
- `EP_*` commands are checked against `rst_seq`, NOT `ctl_seq`: a function enabling, halting or clearing an endpoint from its own thread races with the remote host's SETUPs, and a SETUP must not drop the command. A bus reset invalidates endpoint state, so `rst_seq` is the right staleness boundary.
- Aliasing: a stale command is mistaken for current only after exactly 65536 epochs inside one PC round trip. At HS a control transfer takes >= 3 transactions, so 65536 take >> 1 s against a round-trip limit of 50 ms (section 9): not reachable.
- Cynthion -> PC messages are never reordered or lost (USB). `SETUP` events can arrive back to back; the PC handles them in order.
- `ACK`/`NAK` are sent in the order the commands were processed (= received).

## 7. Control transfers

The Cynthion (gateware + firmware) behaves like a UDC: it ACKs every SETUP, NAKs the data and status stages until the PC answers.

States per transfer, identified by `ctl_seq`:

| remote host | Cynthion | PC |
|---|---|---|
| SETUP token + 8 B | gateware ACKs, `ctl_seq++`; firmware answers locally if it can ([`usb-device-mode.md`](usb-device-mode.md#locally-answered)), else sends `SETUP` | `driver->setup(ctrl)`; a return `< 0` -> `STATUS(STALL)` |
| IN data stage tokens | NAK until the data arrives | the function queues one EP0 IN request -> `CTRL_DATA_IN(len, ZERO=req->zero)` |
| OUT data stage | gateware accepts and collects; after the last packet, `CTRL_DATA_OUT` (whole stage) | the function queued an EP0 OUT request: filled from `CTRL_DATA_OUT`, completed, then `STATUS(ACK)` (or `STATUS(STALL)` if the completion halted EP0) |
| status stage | NAK until released; after `STATUS(ACK)`: ACK (OUT-status for IN transfers, ZLP for OUT/no-data); after `STATUS(STALL)`: STALL | no-data request: the function queues a 0-length request -> `STATUS(ACK)` |

- IN data stage (Cynthion side): firmware cuts `CTRL_DATA_IN` into EP0 packets (64 B, `Ep0Device` `CMD_IN`). The last packet is short; a ZLP follows iff `ZERO` is set, `len % 64 == 0`, `len > 0` and `len < wLength`. A reply longer than `wLength` is truncated to `wLength`.
- ZLP rule (USB 2.0 8.5.3): a data stage shorter than `wLength` whose length is a non-zero multiple of 64 needs a ZLP. The PC sets `ZERO` from `req->zero`; a function that omits it leaves the remote host waiting, exactly as on a hardware UDC. The PC driver logs that case (`len` multiple of 64, `< wLength`, `ZERO` clear).
- A reply for a `wLength == 0` transfer is `STATUS`, never `CTRL_DATA_IN`. `CTRL_DATA_IN` for a no-data or OUT transfer -> `NAK STATE`.
- `CTRL_DATA_OUT` when `wLength > 4096` cannot exist: the firmware STALLs such an OUT transfer (`ERROR TOO_BIG`) and never sends `SETUP`.
- `CTRL_DATA_OUT` may arrive before the function has queued its EP0 request: the PC holds the data (one buffer, discarded at the next `SETUP`).
- Exactly one `STATUS` per transfer is effective; later ones are stale-by-state (`NAK STATE`).
- `SET_ADDRESS`: firmware ACKs the status itself; the gateware applies the address after the status stage ([`device.py`](../gateware/usbcore/device.py) `cmd_addr`); the PC gets `BUS_STATE ADDRESS`.
- After `STATUS(STALL)` the gateware STALLs EP0 until the next SETUP (USB 2.0 8.5.3).

## 8. Data pipes

One pipe per TARGET endpoint (section 2). **No framing**: bytes on the pipe are the payload of TARGET packets, nothing else.

- Rationale: a 6-byte header on a 512-byte packet breaks the 1:1 packet mapping and costs gateware logic; the pipe is a FIFO of packets.
- **Packet boundaries.** The FIFO stores packets with an end-of-packet marker. A TARGET short packet and a TARGET ZLP are AUX short packets/ZLPs and vice versa.
- **HS (TARGET and AUX both HS): 512-byte packets.** TARGET bulk `maxp` = 512 = AUX pipe `maxp`. A transfer on the pipe is any number of packets ended by a short packet/ZLP or by the URB's length.
- **FS TARGET: 64-byte packets.** AUX `maxp` may be larger (HS AUX) so a 64-byte TARGET packet looks like a short AUX packet. Rule: **one AUX transfer = one TARGET packet** in FS mode; the PC submits per-packet URBs (OUT: <= 64 B each; IN: 64 B each) and a received 0-byte URB result is a TARGET ZLP.
- TARGET speed MUST NOT exceed AUX speed (`HELLO.aux_speed`): AUX at FS forces `max_speed` FS.
- **TARGET IN endpoint (PC -> remote host), AUX OUT pipe.**
  - Gateware accepts an AUX OUT packet only if the FIFO has room for `maxp` (else NAK on AUX: flow control).
  - IN token from the remote host: send the next packet; empty FIFO -> NAK (the toggle does not advance on NAK).
  - `req->zero` -> the PC appends a zero-length packet (URB_ZERO_PACKET) after a full-size final packet. An explicit zero-length request -> one ZLP on the pipe.
  - Request completion on the PC = the AUX OUT transfer completed = the data is in the Cynthion FIFO (not necessarily on TARGET yet).
- **TARGET OUT endpoint (remote host -> PC), AUX IN pipe.**
  - OUT token: gateware accepts if FIFO room >= `maxp` (HS: PING/NYET per USB 2.0 8.5.1 once the engine supports it), else NAKs the remote host.
  - AUX IN token: next packet; empty -> NAK. Packets stay in the FIFO until the PC reads them (no PC request queued = no URB = the FIFO fills = the remote host sees NAK).
  - The PC submits IN URBs of `roundup(req->length, maxp)` bytes; a short packet or ZLP ends the request; a full buffer ends it.
- **Toggle and handshake** are the gateware's, per TARGET endpoint: DATA0 after `EP_CONFIG`, after `EP_CLEAR`, after a host CLEAR_FEATURE(ENDPOINT_HALT); not advanced on NAK/STALL; ACKed packet advances.
- **Halt.** `EP_HALT`: TARGET tokens get STALL; the FIFO contents are kept. `EP_CLEAR`/host clear: halt off, toggle DATA0, FIFO kept. Wedge: the endpoint ignores host CLEAR_FEATURE until the PC sends `EP_CLEAR`.
- **`EP_CONFIG` enable** flushes the FIFO, clears halt/wedge, toggle DATA0, arms the endpoint. **Disable**: TARGET tokens get no response (NAK for the remote host's retries), FIFO flushed. Pipes of unconfigured endpoints NAK on AUX.
- **`EP_FLUSH`**: with `DISCARD`, drop everything in the FIFO (partial request data). The PC sends `DISCARD` when it dequeues or disables a TARGET-IN request, plain notification when it dequeues a TARGET-OUT request.
- **Bus reset**: gateware drops all endpoint configuration and flushes all FIFOs; AUX pipes then NAK until the next `EP_CONFIG`. The PC kills its URBs on `RESET`.
- **Per-endpoint FIFOs are first-class.** Backpressure is credit-by-room: a packet is accepted only if the FIFO has room for `maxp`, otherwise USB NAK on the leg that is full (AUX OUT NAKs the PC, TARGET OUT NAKs the remote host). The room is the credit; no credit messages exist and none are needed.
- **Depth is advertised, not framed.** `HELLO.fifo_pkts` = packets of `maxp` each pipe holds (planned 4 = 2 KiB per pipe). A deeper buffer backed by HyperRAM ([open question 4](usb-device-mode.md#open-questions)) is the same pipe with a larger `fifo_pkts` (`HELLO.features` bit0 = deep buffer); packets, EOP markers, NAK rules, `EP_FLUSH` and the PC driver are unchanged. The PC never depends on the depth beyond "room for `maxp`".
- **Per-endpoint mapping limits** (version 1): TARGET `0x01`..`0x04` OUT and `0x81`..`0x84` IN. EP_CONFIG for another address -> `NAK BAD_EP`; for a missing pipe -> `NAK NO_PIPE` (option C fallback keeps the interrupt endpoints on `EP_DATA`).

## 9. Timers

Every timer: what it waits for, value, source, multiplier, on expiry. "Limit" values are host-imposed USB deadlines: beyond them the remote host has already given up, so waiting longer buys nothing; they are not safety factors. The relay round trip is expected at about 1 ms (section 11), so none of these may fire in normal operation; multiplier vs that estimate is stated.

| id | owner | waits for | value | source | multiplier | on expiry |
|---|---|---|---|---|---|---|
| `T_SETUP_NODATA` | firmware | PC `STATUS` after `SETUP`, no data stage | 50 ms | USB 2.0 9.2.6.4 (status within 50 ms) | 1.0 x limit (50 x the ~1 ms estimate) | STALL EP0, `ERROR TIMEOUT`, bump `answer_timeouts` |
| `T_SETUP_IN` | firmware | PC `CTRL_DATA_IN` after `SETUP`, IN data stage | 500 ms | USB 2.0 9.2.6.4 (first data packet 500 ms) | 1.0 x limit | as above |
| `T_SETUP_OUT` | firmware | PC `STATUS` after the OUT data stage | 5000 ms | USB 2.0 9.2.6.4 (data to device + status 5 s) | 1.0 x limit | as above |
| `T_CMD` | PC driver | one command transfer on `0x03` to complete (accepted by the Cynthion) | 50 ms | tightest EP0 limit (`T_SETUP_NODATA`): a command later than that already failed the transfer it belongs to | 1.0 x limit | unlink the URB, log operation + elapsed, mark the link dead, detach the UDC |
| `T_HELLO` | PC driver | `HELLO` reply after the `HELLO` command at probe | 50 ms | same bound; probe-time only | 1.0 x limit | probe fails with an error naming the elapsed time |
| `T_SUSPEND` | gateware | TARGET idle before `SUSPEND` | 3 ms | USB 2.0 7.1.7.6 | 1.0 | `BUS_STATE SUSPEND` |
| `T_RESUME` | gateware | duration of device-driven resume K for `WAKEUP` | gateware decides within the USB 2.0 7.1.7.7 window | the spec | n/a | `BUS_STATE RESUME` when done |

- Firmware timers run on the firmware's own tick; the exact tick is the firmware agent's choice but MUST be fine enough that the value is met within 10 %.
- To be re-derived from the board: the ~1 ms estimate and with it every multiplier above ([#612](https://github.com/awtoau/cynthion-workspace/issues/612) relay round trip, UNMEASURED on hardware).

## 10. Error codes and counters

`NAK.code` and `ERROR.code`:

| code | name | meaning |
|---|---|---|
| 1 | `STALE` | `seq` did not match the current counter; command dropped |
| 2 | `BAD_TYPE` | unknown `type` for this direction/version |
| 3 | `BAD_LEN` | `len` inconsistent with the transfer size, or too long for the type |
| 4 | `BAD_EP` | `ep` is not a TARGET endpoint of the mapping |
| 5 | `NO_PIPE` | no pipe/FIFO available for that endpoint |
| 6 | `NOT_CONFIGURED` | the endpoint is not enabled |
| 7 | `NOT_ENABLED` | `WAKEUP` while the remote host has not enabled remote wakeup |
| 8 | `STATE` | command not legal now (e.g. `CTRL_DATA_IN` for a transfer with no IN data stage, or after the status) |
| 9 | `TIMEOUT` | (`ERROR`) the PC missed a `T_SETUP_*` deadline; EP0 was STALLed |
| 10 | `OVERRUN` | (`ERROR`) the Cynthion lost coalescable events (`BUS_STATE`) because the PC did not read the control channel |
| 11 | `UNSUPPORTED` | isochronous, `EP_DATA`, bad `maxp`, FS/HS combination not possible |
| 12 | `VERSION` | `HELLO.version` not supported |
| 13 | `TOO_BIG` | (`ERROR`) OUT data stage with `wLength` > 4096; the transfer was STALLed |

`STATS` counters (`u32`, saturating, order fixed): `stale_ctl`, `stale_ep`, `bad_msg` (any `BAD_*`/`UNSUPPORTED`), `setup_aborts` (a SETUP arrived while a transfer was armed), `answer_timeouts`, `dequeues` (`EP_FLUSH` received), `resets` (bus resets), `events_dropped`.

- Event queue depth 16. On overflow the Cynthion drops `BUS_STATE` first (coalescing: the last one wins), counts `events_dropped`, and sends `ERROR OVERRUN` when the queue drains. `SETUP`, `CTRL_DATA_OUT`, `RESET` are never dropped.
- Unknown `type` from the PC: `NAK BAD_TYPE`, the link keeps running. Unknown `type` from the Cynthion: the PC logs and ignores it.

## 11. Relay budget

Per control transfer, one round trip when firmware holds the whole IN reply and pushes 64-byte packets to the engine locally. Limits: [`usb-device-mode.md`](usb-device-mode.md#timing-budget).

| stage | estimate | basis | status |
|---|---|---|---|
| TARGET SETUP -> engine ACK -> CPU sees `seq` | microseconds | gateware ACKs; CPU polls `seq` | UNMEASURED |
| firmware -> AUX IN FIFO -> xHCI poll | <= 125 us | one microframe between bulk IN retries | UNMEASURED |
| URB completion -> `driver->setup` -> function -> `usb_ep_queue(ep0)` | <= 0.4 ms | VM with the in-kernel stub (ACM/NCM paths 0.1-0.6 ms) | measured in a VM (stub) |
| `CTRL_DATA_IN` URB -> xHCI -> firmware -> `CMD_IN` | <= 125 us + firmware | microframe + one poll | UNMEASURED |
| total | about 1 ms | 50 x inside the tightest limit | UNMEASURED on hardware |

- Bytes per control transfer on the link: `SETUP` 14, `CTRL_DATA_IN` 6 + n, `ACK` 10, `STATUS` 7: GET_DESCRIPTOR(device, 18 B reply) is 14 + 24 + 10 = 48 bytes on AUX plus USB overhead.
- Model-only numbers (software model of the AUX side in a VM) are labelled MODEL wherever reported; they bound driver and gadget cost, not USB timing.

## 12. Who does what

| duty | kernel backend | firmware relay | gateware |
|---|---|---|---|
| AUX descriptors (section 2) | binds by triple | owns the descriptor set, `HELLO` | endpoint FIFOs for control `0x03`/`0x83` + 8 pipes |
| `HELLO` | sends first, waits `T_HELLO` | replies, resets all link state on every `HELLO` | n/a |
| `SETUP` relay | `driver->setup`, EP0 request mapping, `STATUS`/`CTRL_DATA_IN` | local answers, `SETUP`, epoch bookkeeping, `T_SETUP_*`, `CMD_IN` chunking | `Ep0Device`: ACK SETUP, `seq++`, NAK until released, stale-`cmd_seq` drop |
| `EP_CONFIG`/`HALT`/`CLEAR`/`FLUSH` | issues, mirrors host halts | validates (`rst_seq`), programs endpoint regs, answers `ACK`/`NAK` | per-endpoint enable, toggle, halt/wedge, STALL, flush |
| data | URBs on pipes, bounce buffers, ZLP, per-packet URBs in FS | none (never in the path) | FIFO + EOP marker, NAK on full/empty, PING/NYET, toggle |
| bus events | `RESET`/`BUS_STATE` -> udc-core | reports, bumps `rst_seq`, coalesces | reset/chirp/speed, suspend detect, VBUS |
| stale/abort | drops late EP0 data, `-ECONNRESET` on new `SETUP` | drops + counts, `NAK STALE`, `STATS` | `stale` counter exists in `Ep0Device` |

Conformance checks (each party can run its half against the software model in `scripts/linux/cudc_aux_model.py`):

- Descriptor + `HELLO` handshake; unknown type -> `NAK BAD_TYPE`.
- Stale `CTRL_DATA_IN`/`STATUS` dropped, `stale_ctl` +1; stale `EP_*` after a bus reset dropped, `stale_ep` +1.
- A new `SETUP` aborts the armed transfer (`setup_aborts` +1); the late reply is stale.
- Message of exactly 512 - 6 = 506 payload bytes is followed by a ZLP and parsed; the same at 4096.
- IN pipe: 1024-byte transfer gives packets 512, 512; with `ZERO` a third zero-length packet.
- OUT pipe: packets 512, 100 end a 1024-byte URB early with 612 bytes.

## 13. Examples

All hex, header first. Epoch `ctl_seq = 0x0007` (low byte 07 = `Ep0Device.seq`). Link at HS: `maxp` 512.

### 13.1 GET_DESCRIPTOR(device, wLength 64), 18-byte reply

| # | where | frame / event | bytes |
|---|---|---|---|
| 1 | TARGET, remote host | SETUP token + DATA0: `80 06 00 01 00 00 40 00` | gateware ACKs, `ctl_seq` 6 -> 7 |
| 2 | AUX IN `0x83` | `SETUP` (14 B, short packet) | `02 00 07 00 08 00  80 06 00 01 00 00 40 00` |
| 3 | PC | `driver->setup`; composite queues an 18-byte EP0 IN request, `zero 0` | - |
| 4 | AUX OUT `0x03` | `CTRL_DATA_IN`, `ep` flags 0, 24 B (short packet) | `04 00 07 00 12 00  <18 descriptor bytes>` |
| 5 | AUX IN `0x83` | `ACK` echoing type 04, ep 00, seq 0007 (10 B) | `10 00 07 00 04 00  04 00 07 00` |
| 6 | TARGET | IN tokens: `Ep0Device` sends 18 B DATA1 (short, ends the data stage); remote host's zero-length OUT status is ACKed | - |
| 7 | PC | EP0 request completes when step 4's URB completes | - |

- 18 < 64 and not a multiple of 64: no ZLP. If the function's reply were 64 bytes against `wLength` 255, `CTRL_DATA_IN` would set `ep = 01` (ZERO) and the firmware would end with a ZLP.
- Stale case: if a second SETUP (epoch 0008) arrived between 3 and 4, step 4 would be `NAK` code 1 (`STALE`): `11 00 08 00 05 00  04 00 07 00 01`, `stale_ctl` +1, nothing sent to TARGET. The PC saw `SETUP` 0008 (completing its old request `-ECONNRESET`) before or after; either order is safe.

### 13.2 Bulk IN (PC -> remote host), 1024 bytes with `req->zero`, endpoint TARGET `0x81`

Preceded by `EP_CONFIG` for the endpoint (`rst_seq = 0x0003`):

| # | where | frame / event | bytes |
|---|---|---|---|
| 1 | AUX OUT `0x03` | `EP_CONFIG` ep `81`, enable 1, bulk (2), maxp 512 (10 B) | `0A 81 03 00 04 00  01 02 00 02` |
| 2 | AUX IN `0x83` | `ACK` | `10 00 07 00 04 00  0A 81 03 00` |
| 3 | PC | `usb_ep_queue(ep1in, 1024 B, zero 1)` | - |
| 4 | AUX OUT `0x04` | one URB, 1024 B, `URB_ZERO_PACKET`: packets 512, 512, then a zero-length packet | no header, raw bytes |
| 5 | gateware | FIFO 1 stores 3 packets with EOP markers (512, 512, 0) | - |
| 6 | TARGET | IN tokens `0x81`: DATA0 512, DATA1 512, DATA0 ZLP (a ZLP is a data packet with a zero-length payload and its own toggle); a fourth IN token is NAKed | - |
| 7 | PC | the request completes when step 4's URB completes (data accepted into the FIFO); `actual` = 1024 | - |

- Without `zero`, step 4 has two packets and the remote host's third IN token is NAKed; the 1024-byte transfer is then (correctly) not terminated and the remote host keeps waiting for the next data.
- TARGET OUT reverse example: TARGET `0x01` packets 512 and 100 -> FIFO -> AUX IN `0x84`: the PC's IN URB of 1024 B (queued before) completes with 612 bytes at the short packet.
