# USB device mode for a Linux PC (working title: device-mode bridge)

A Linux PC gets USB device (target) mode, which ordinary PCs lack. Product and decisions: [#612](https://github.com/awtoau/cynthion-workspace/issues/612) (Dan, 2026-11-06).

- PC is USB host to the Cynthion over **AUX**.
- On the PC the Cynthion is a **UDC** (USB Device Controller); the stock gadget framework runs unchanged: configfs functions (NCM/ECM, ACM, mass storage, ...).
- A second host plugs into **TARGET** and sees that gadget.
- Linux only. Not Facedancer 2 (a userspace packet-emulation library). Name open.
- First pass: no descriptor cache. Every control request goes to the PC's gadget stack, except what UDC hardware answers by contract ([Locally answered](#locally-answered)).

```
remote host ==TARGET== [device engine | EP FIFOs | RISC-V UDC model] ==AUX== PC: xHCI -> bridge UDC driver -> gadget core -> configfs function
```

## Split of responsibilities

| layer | where | owns |
|---|---|---|
| packet layer, device engine | gateware, TARGET PHY | own stack per [#612](https://github.com/awtoau/cynthion-workspace/issues/612) (L0 ULPI, L1 codec, L2 engine shared with GUH): chirp, address match, data toggle, ACK/NAK/STALL, PING/NYET, ZLP, SOF, suspend detect |
| endpoints | gateware | EP0 SETUP/data into a CSR FIFO; EPn = BRAM FIFOs, one per mapped pipe, packet boundaries kept (end-of-packet marker, so short packets and ZLPs survive) |
| AUX device | gateware + firmware | same own stack on `aux_phy`: fixed composite (console CDC-ACM + bridge vendor interface), descriptors in firmware |
| UDC hardware model | RISC-V firmware | endpoint enable/disable/halt, SETUP delivery to the PC, EP0 data and status release, STALL, bus events (reset + speed, suspend, resume, VBUS), local answers below |
| link protocol | firmware + PC driver | control channel (framed messages) plus one AUX bulk pipe per TARGET data endpoint ([AUX link protocol](#aux-link-protocol)) |
| Linux UDC driver | PC kernel | a `usb_driver` bound to the bridge interface that also registers a UDC (`usb_add_gadget_udc`), implementing `struct usb_gadget_ops` / `struct usb_ep_ops` over URBs |

- The CPU is never in the bulk path: TARGET EPn FIFO <-> AUX EPn FIFO is gateware only; the CPU handles EP0 and events.
- `target_phy` today hosts `UsbPort` (GUH host engine, [`usb-host-options.md`](usb-host-options.md)); device mode is another role on that PHY, one role at a time.

## Kernel references

Checked in the shared Linux store at `2b895e613280` (2026-05-29), read-only. No USB-attached UDC exists in [`drivers/usb/gadget/udc/`](https://github.com/torvalds/linux/tree/master/drivers/usb/gadget/udc).

| reference | why |
|---|---|
| [`dummy_hcd.c`](https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/udc/dummy_hcd.c) | semantic oracle: EP0 handling, which requests a UDC answers itself (`handle_control_request`), request queue rules; also a test bed: the same configfs gadget on dummy_hcd and on the bridge must behave alike |
| [`max3420_udc.c`](https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/udc/max3420_udc.c) | SPI-attached UDC: "registers" behind a bus that sleeps (`spi_sync` from a kthread); small (EP0 + 3); local SET_ADDRESS / GET_STATUS / SET_FEATURE in `max3420_handle_setup` |
| [`usbip/vudc_*.c`](https://github.com/torvalds/linux/tree/master/drivers/usb/usbip) | UDC whose hardware is remote over a transport (TCP socket, rx/tx kthreads); local standard requests in `vudc_transfer.c` |

- Bridge driver = max3420/vudc structure with URBs as the transport; URB completions are async, so no kthread is needed for the data path.
- Gadget API: [`include/linux/usb/gadget.h`](https://github.com/torvalds/linux/blob/master/include/linux/usb/gadget.h); `USB_GADGET_DELAYED_STATUS` in [`composite.h`](https://github.com/torvalds/linux/blob/master/include/linux/usb/composite.h).

## Linux ops onto the link

`usb_gadget_ops`:

| op | link |
|---|---|
| `udc_start` / `udc_stop` | bind / unbind gadget driver; `udc_stop` sends `DISCONNECT`, flushes all pipes |
| `pullup(on)` | `CONNECT` / `DISCONNECT`: firmware sets TARGET termination via ULPI function control |
| `udc_set_speed` | `MAX_SPEED` (HS, or FS for increment 6) |
| `wakeup` | `REMOTE_WAKEUP` (firmware refuses unless the host enabled it) |
| `set_selfpowered` | `SELF_POWERED`: firmware state for the local GET_STATUS answer |
| `get_frame` | first pass `-EOPNOTSUPP` |
| VBUS | event `VBUS(on/off)` -> `usb_udc_vbus_handler()` (not an op) |

`usb_ep_ops`:

| op | link |
|---|---|
| `enable(desc)` | `EP_ENABLE(ep, dir, type, wMaxPacketSize)`: gateware endpoint on, toggle DATA0; driver starts URBs on the mapped AUX pipe |
| `disable` | `EP_DISABLE`: NAK, flush FIFO; driver kills URBs, completes requests `-ESHUTDOWN` |
| `queue` ep0 | `EP0_DATA(seq, bytes)` (IN data, or 0-length = status); OUT data stage arrives as event `EP0_OUT(seq, bytes)` |
| `queue` epN IN | bulk OUT URB on the AUX pipe; `req->zero` -> trailing ZLP URB |
| `queue` epN OUT | bulk IN URB of `req->length`; short packet ends it |
| `dequeue` | `usb_unlink_urb`; data already in the Cynthion FIFO needs `EP_FLUSH` |
| `set_halt` / `set_wedge` | `EP_HALT(ep, wedge)`: gateware STALLs |
| `fifo_flush` | `EP_FLUSH` |
| `fifo_status` | first pass `-EOPNOTSUPP` |
| `alloc_request` / `free_request` | local; one URB per request |

Events Cynthion -> PC: `BUS_RESET(speed)` -> `usb_gadget_udc_reset`; `SUSPEND` / `RESUME` -> `driver->suspend/resume`; `SETUP(seq, 8 B)` -> `driver->setup`; `ADDRESS(n)` -> `usb_gadget_set_state`; `EP0_OUT(seq, bytes)`; `HALT_CLEARED(ep)`.

## AUX link protocol

First-pass proposal (choice is open question 1):

- **Control channel**: one AUX bulk IN/OUT pair. One message per bulk transfer, ended by a short packet. Header `{u8 type, u8 ep, u16 seq, u16 len}` LE + payload. Max payload 4096 (`USB_COMP_EP0_BUFSIZ`).
- **Data**: one AUX bulk pipe per TARGET data endpoint, same direction toward the PC side, no framing. Remote host's OUT -> TARGET OUT FIFO -> AUX IN pipe; PC's IN data -> AUX OUT pipe -> TARGET IN FIFO.
- **Flow control is USB's own NAK** on both legs: full TARGET OUT FIFO NAKs the remote host; empty AUX IN FIFO NAKs the PC. No credits, no head-of-line blocking between endpoints.
- Packet boundaries: AUX bulk max is 512 at HS = TARGET HS bulk max, so packets map 1:1; short packets and ZLPs pass through.
- `seq`: increments per SETUP. A new SETUP aborts the old control transfer (USB 2.0 8.5.3); firmware drops PC answers with a stale `seq`.
- Rejected for the first pass: one multiplexed bulk pair for everything (usbip style): needs credits per endpoint, gateware demux, and a stalled endpoint blocks the others.
- Endpoint-count cost: AUX has 15 IN + 15 OUT besides EP0; a CDC-ACM console uses 2 IN + 1 OUT, control channel 1 + 1, leaving 12 IN + 13 OUT for data pipes.

## Timing budget

Host-imposed limits (USB 2.0 chapter 9):

| clause | limit | who meets it |
|---|---|---|
| 8.4.6.4 / 8.5.3 | SETUP: device must ACK, never NAK or STALL | gateware, always |
| ch. 7 bus turnaround | token -> response within the inter-packet delay (HS: 192 bit times) | gateware: ACK/NAK/STALL/data from FIFO state |
| 9.2.6.1 | 5 s upper limit for any request | PC (vendor requests too) |
| 9.2.6.2 | 10 ms reset/resume recovery (host side) | — |
| 9.2.6.3 | SetAddress: status stage within 50 ms, then 2 ms recovery | firmware, locally |
| 9.2.6.4 | no data stage: status within 50 ms | PC answers, gateware NAKs status meanwhile |
| 9.2.6.4 | data to host: first packet 500 ms, each next 500 ms, status 50 ms after last | PC answers, data stage NAKs meanwhile |
| 9.2.6.4 | data to device: 5 s for all data + status | PC |
| 9.2.6.5 | class requests: same as standard unless the class exempts | PC |

- Linux's own host-side control timeout is 5 s (`USB_CTRL_GET_TIMEOUT`, `USB_CTRL_SET_TIMEOUT` in [`include/linux/usb.h`](https://github.com/torvalds/linux/blob/master/include/linux/usb.h)).
- Data and status stages may NAK indefinitely within those limits; that is what makes relaying possible.
- Relay round trip per stage: TARGET SETUP -> firmware -> AUX IN poll -> xHCI -> URB completion -> `driver->setup` -> `usb_ep_queue(ep0)` -> AUX OUT -> firmware -> gateware. UNMEASURED; increment 2 measures it. Expected well under 50 ms; if it is not, the design changes, not the limit.
- A remote host may time out below the spec limits; increment 3 tests against Linux; other hosts are out of scope until measured.

### Locally answered

What a hardware UDC answers by contract (dummy_hcd `handle_control_request`, max3420, vudc); not a cache, the gadget stack never sees these:

- `SET_ADDRESS`: firmware ACKs status itself; gateware applies the address only after the status stage completes (9.2.6.3, 9.4.6). PC gets `ADDRESS(n)`.
- `GET_STATUS` device / endpoint: self-powered, remote-wakeup-enabled, halt bits held in firmware.
- `SET_FEATURE` / `CLEAR_FEATURE`: `ENDPOINT_HALT` (clear resets the toggle to DATA0, 9.4.5; wedged endpoints stay halted), `DEVICE_REMOTE_WAKEUP`, `TEST_MODE`.
- STALL of EP0 when the PC's `setup()` returns `< 0`; ZLP at end of an IN data stage that is a multiple of wMaxPacketSize and shorter than wLength (from `req->zero`).
- Bus reset, chirp, suspend (3 ms idle), resume: gateware detects, firmware reports.

Why the first pass needs no cache: every other request is NAK-tolerant for 50-500 ms, and the relay round trip is expected to be orders of magnitude inside that. A cache only buys margin against a slow PC or an impatient non-Linux host; neither is a first-pass target.

## Throughput

Measured AUX figures, luna gateware, 512-byte bulk, AUX on a direct root port, 2026-11-06, commit `fb22981` ([`usb-performance.md`](usb-performance.md#same-day-aux-on-a-direct-root-port)):

| direction | AUX leg (median) | TARGET leg |
|---|---|---|
| remote host -> TARGET OUT -> AUX IN -> PC | AUX bulk IN 374.8 Mbps | old TARGET bulk IN 387.8 direct; old OUT 338.8 (AUX PHY) |
| PC -> AUX OUT -> TARGET IN -> remote host | AUX bulk OUT 310.0 Mbps | |
| protocol maximum, HS bulk | 426 Mbps | 426 Mbps |

- Four hubs deep the AUX figures drop to IN 288.4 / OUT 290.6 ([same doc](usb-performance.md#re-measurement-2026-11-06-612-s0)).
- Bridge ceiling per direction is the slower leg, then the PC gadget function's own cost (UNMEASURED).
- Control channel shares the AUX bus; EP0 traffic is small.
- These are luna's numbers; our stack must match them ([#612](https://github.com/awtoau/cynthion-workspace/issues/612)).

## Endpoint limits (first pass)

- TARGET: EP0 + EP1-EP4 IN + EP1-EP4 OUT; types control, bulk, interrupt. Covers ACM + NCM composite (2 bulk IN, 2 bulk OUT, 2 interrupt IN).
- Interrupt wMaxPacketSize <= 512 (one AUX bulk packet per TARGET packet); HS high-bandwidth interrupt (> 1 per microframe) not supported.
- Isochronous: later. Needs AUX iso pipes or framing; NAK flow control does not exist for iso.
- Advertised to the gadget core as `usb_ep_caps` per endpoint, so `usb_ep_autoconfig` only picks what exists.

### Block RAM

Hard constraint. [`chips/ecp5/bram-budget.md`](chips/ecp5/bram-budget.md): 56 `DP16KD` blocks; whole SoC uses 54/56 (build [#456](https://github.com/awtoau/cynthion-workspace/issues/456), main 948d481, board-tested 2026-11-06), so 2 spare.

- Cost: one DP16KD (2 KiB = 4 x 512 B packets) per mapped pipe; 8 pipes = 8 blocks. EP0 FIFO in block RAM too (FIFOs belong in block RAM).
- Pipes shared between legs: one FIFO serves both TARGET EPn and AUX EPn (both in fabric), so no second copy per leg.
- Freed in a device-mode build: GUH host SIE FIFOs (2 blocks, [#619](https://github.com/awtoau/cynthion-workspace/issues/619)) and luna's AUX USB buffers (3 blocks), both replaced by our stack.
- Still short -> next lever: main RAM (`RAM_SIZE`). The instruction cache does not shrink.

## Increments

Smallest first; each has a sim and a board check.

| # | step | sim | board check |
|---|---|---|---|
| 1 | TARGET enumerates as a fixed device from firmware alone (our stack, descriptors in firmware) | own device engine vs GUH host engine, closed loop; luna as oracle | `lsusb -v` on a host cabled to TARGET; [`target-test-harness.md`](target-test-harness.md) control rows |
| 2 | SETUP relayed over AUX control channel to a PC userspace stub (libusb) that answers from a fixed table | GUH host on TARGET + sim AUX host; stale-`seq` and new-SETUP-aborts cases | enumeration through the stub; relay round trip logged per stage |
| 3 | Linux UDC driver skeleton, out-of-tree module, registers a gadget; configfs ACM enumerates | same configfs gadget on dummy_hcd as the reference | remote `lsusb -v` matches dummy_hcd's descriptors; tty opens |
| 4 | bulk data path: EPn FIFOs, AUX pipes, `queue`/`dequeue`/`halt` (gateware done: [status](#increment-4-bulk-endpoints-events-link-states)) | packet boundaries, ZLP, NAK backpressure both legs, halt + toggle | ACM loopback with data check; ZLP on 512-multiple writes |
| 5 | NCM Ethernet | — (no new gateware) | `iperf3` both directions against the throughput table |
| 6 | FS fallback | FS chirp-less reset, 64-byte bulk | remote host forced to FS (FS hub); gadget re-enumerates with FS descriptors |

- Board check topology: TARGET may be cabled back to the same PC, so the PC is host and gadget at once.

### Increment 3 status

Skeleton with the Cynthion **stubbed**: [`linux/cynthion_udc/`](../linux/cynthion_udc/README.md), out-of-tree, GPL-2.0.

- Core: `usb_gadget_ops` / `usb_ep_ops` over `struct cudc_backend_ops` (connect, disconnect, ep enable/disable, queue, dequeue, set_halt, setup_reply); the AUX transport implements the same table later.
- Stub backend: in-kernel fake host. Pullup -> HS attach -> reset, GET_DESCRIPTOR(device, 64), reset, SET_ADDRESS (local, never reaches the gadget), GET_DESCRIPTOR device / config 9 / config full, SET_CONFIGURATION, SET_INTERFACE for bulk alts; then bulk IN -> OUT loopback per interface.
- Test: [`scripts/linux/cudc_vm_test.py`](../scripts/linux/cudc_vm_test.py), virtme-ng VM only.
- Result 2026-11-06, kernel 7.2.15-300.fc45, vng 1.41, 6 runs: PASS. ACM, NCM, ACM+NCM each reach state `configured`; ACM 2000 B tty loopback and NCM raw-frame loopback return in <= 0.4 ms; unbind + rmmod clean; dmesg has only the unsigned-module taint lines.
- Timings behind the test's limits: module build <= 0.5 s cold (2.0 s at loadavg 30); whole VM run <= 2.2 s with the three loopback cases, <= 5.7 s with all cases (6 runs, loadavg ~30).
- Stub covers (second pass, [#612](https://github.com/awtoau/cynthion-workspace/issues/612)): GET_STATUS / SET_FEATURE / CLEAR_FEATURE answered locally (incl. ENDPOINT_HALT, wedge stays halted, TEST_MODE, remote wakeup bit); string descriptors and class requests (CDC-ACM line coding, mass-storage reset); packet-level data path (512-multiples with and without ZLP, short packets, EP0 `req->zero` ZLP, data toggle across NAK, toggle reset on halt clear); halt, dequeue, wedge; suspend / resume / remote wakeup (refused unless enabled); VBUS off/on re-enumeration; function STALL on EP0; delayed status. Host control and contract: [`linux/cynthion_udc/README.md`](../linux/cynthion_udc/README.md#stub-host-control-debugfs-ctl).
- dummy_hcd reference: ACM, NCM, ACM+NCM present identical device and configuration descriptors on the stub and on dummy_hcd, except `bEndpointAddress` numbers (the stub has ep1-4 in/out, dummy_hcd 15 endpoints); directions and positions match.
- Still not covered: AUX link / URBs (backend is in-kernel); FS fallback; isochronous; real bus timing (the stub has no inter-packet delays).

### Increments 1 and 2: sim status

Device engine: [`gateware/usbcore/device.py`](../gateware/usbcore/device.py) (`Ep0Device`), our L1 codecs plus EP0 and a CPU command port. luna stays a sim oracle only (the harness runs the same case table against luna's model device and against this engine).

- Engine: bus-reset detect, device chirp K, three host K-J pairs -> HS else FS; EP0 SETUP/IN/OUT/status with 64 B in/out buffers; STALL; SET_ADDRESS applied after the status ACK. Other endpoints get no response.
- CPU port: `seq` bumps on every SETUP token and bus reset; every command carries `cmd_seq`, a stale one is dropped and counted (`stale`). Increment 1 = firmware answers from a table; increment 2 = firmware relays the SETUP and answers with what the PC stub returns.
- Directed sims: [`usb_device_ep0_sim.py`](../scripts/soc/usb_device_ep0_sim.py) (EP0, speed, SOF, suspend / resume / wakeup, VBUS, events; `--negative` proves the stale checks can fail) and [`usb_device_bulk_sim.py`](../scripts/soc/usb_device_bulk_sim.py) (bulk EP1-4, FS and HS).
- Closed loop, [`scripts/soc/usb_target_sim.py`](../scripts/soc/usb_target_sim.py) `own_*` groups: GUH host engine + shim + PHY model against `OwnDevice` ([`own_device.py`](../gateware/probes/usb_host/own_device.py)) with the firmware stand-in [`usb_own_device.py`](../scripts/soc/usb_own_device.py). `own_relay_*` adds a modelled AUX control channel and a stub (delay in clock cycles only; framing not modelled). `--sabotage` serves a wrong descriptor byte and must FAIL.
- Not built: CSR wrapper, the AUX device side, ISO. The sim cannot say anything about milliseconds.

Cells ([`scripts/soc/usb_device_area.py`](../scripts/soc/usb_device_area.py), yosys `synth_ecp5`, no place and route; measured with the engine at commit of increment 4, 2026-11-07):

| block | LUT4 | CCU2C | FF | DPR16X4 | DP16KD |
|---|---|---|---|---|---|
| token detector | 50 | 0 | 46 | 0 | 0 |
| data receiver | 49 | 0 | 45 | 0 | 0 |
| data generator | 119 | 0 | 33 | 0 | 0 |
| handshake detector + generator | 23 | 0 | 16 | 0 | 0 |
| `PacketFifo` 1 x 512 B (one FIFO) | 32 | 0 | 38 | 0 | 1 |
| `PacketFifo` 2 x 512 B | 45 | 0 | 52 | 0 | 1 |
| `PacketFifo` 4 x 512 B | 75 | 0 | 76 | 0 | 1 |
| `PacketFifo` 2 x 64 B (FS only) | 75 | 0 | 26 | 16 | 0 |

- The two EP0 64 B buffers are distributed RAM (16 x DPR16X4), no block RAM.
- Whole engine rows are in [Increment 4](#increment-4-bulk-endpoints-events-link-states) below. The previous EP0-only engine was 598 LUT4 / 73 CCU2C / 322 FF.
- Not counted: CSR wrapper, `usb`/`sync` crossing, AUX-side stream adapters. Against 24288 LUT4 the engine is LUT4 + 2 x CCU2C of that; the SoC is routing-bound at 92 % ([#619](https://github.com/awtoau/cynthion-workspace/issues/619)).

### Increment 4: bulk endpoints, events, link states

Engine: [`device.py`](../gateware/usbcore/device.py) (`Ep0Device`), FIFOs: [`bulk.py`](../gateware/usbcore/bulk.py). Everything below runs in the `usb` domain with no CPU timing: every NAK / ACK / NYET / STALL / ZLP / toggle / PING decision is made from FIFO state inside the inter-packet window. A CSR master (PC over AUX, or firmware) only configures and moves data.

**Bulk behaviour (EP1-4, each direction its own FIFO)**

- OUT: data is written into the FIFO while it arrives and committed on good CRC16 + `<= maxp` + DATA0/1.
  - Right toggle and room: commit, toggle flips, ACK; HS and no room left for another packet: NYET.
  - Right toggle, FIFO full: NAK, toggle unchanged, nothing stored.
  - Duplicate toggle: ACK, dropped (`n_dup`).
  - Oversize, wrong PID, bad CRC: no response.
  - Halted: STALL after the data.
  - PING (HS only, ignored in FS): ACK with room, NAK when full, STALL when halted.
- IN: token with a committed packet sends it with the endpoint toggle; the packet is freed and the toggle flips only on the host ACK. No ACK within `ACK_WAIT_CYCLES` or a repeated IN: same packet, same toggle again. Empty: NAK (toggle unchanged). Halted: STALL, queued data kept.
- Producer side splits a stream at `maxp`; a ZLP is one `zlp` beat; `auto-ZLP` (bit 10 of `cmd_arg` at ENABLE) appends a ZLP when a transfer (`last`) ends exactly on `maxp` (512 HS / 64 FS multiples).
- Unconfigured endpoints do not answer. Bus reset, detach and DISABLE flush and disable; ENABLE and CLEAR reset the toggle to DATA0.

**Command port** (all commands carry `cmd_seq`; a stale one is dropped and counted in `stale`, endpoint commands included; the CSR-master model is `Port` in [`usb_own_device.py`](../scripts/soc/usb_own_device.py) and `Bus.cpu` in the directed sim):

| `cmd` | name | operands | effect |
|---|---|---|---|
| 1 | `CMD_IN` | `cmd_len`, `cmd_last` | arm one EP0 IN packet from `i_buf` |
| 2 | `CMD_STATUS` | `cmd_addr`, `cmd_addr_valid` | release EP0 status ZLP; address applied after its ACK |
| 3 | `CMD_STALL` | | STALL EP0 until the next SETUP |
| 4 | `CMD_OACK` | | free the EP0 OUT buffer |
| 5 | `CMD_EP_ENABLE` | `cmd_ep`, `cmd_arg` = maxp[9:0] + auto-ZLP[10] | enable, unhalt, toggle 0, flush |
| 6 | `CMD_EP_DISABLE` | `cmd_ep` | disable + flush |
| 7 | `CMD_EP_HALT` | `cmd_ep` | STALL the endpoint |
| 8 | `CMD_EP_CLEAR` | `cmd_ep` | unhalt, toggle 0 |
| 9 | `CMD_EP_FLUSH` | `cmd_ep` | drop queued packets (toggle kept) |
| 10 | `CMD_WAKEUP` | | drive resume K `RESUME_K_CYCLES` if suspended |

`cmd_ep` = `ep_code(n, is_in)`: bit 3 = IN, [2:0] = n (1-4).

**Status registers (read side)**

| signal | width | meaning |
|---|---|---|
| `speed` | 2 | 0 HS, 1 FS |
| `address` | 7 | device address (applied after SET_ADDRESS status ACK) |
| `seq`, `stale` | 8, 8 | SETUP / reset counter; dropped stale commands |
| `stage`, `setup_valid`, `setup_data`, `in_busy`, `o_valid`, `o_len` | | EP0 as before |
| `ep_sel` -> `ep_enabled`, `ep_halted`, `ep_toggle`, `ep_level`, `ep_maxp` | 4 -> 1,1,1,4,10 | per endpoint (`ep_level` = committed packets in the FIFO) |
| `n_nak`, `n_nyet`, `n_dup`, `n_crc` | 8 each, saturating | bulk NAKs sent, NYETs, duplicate OUTs dropped, CRC errors |
| `frame`, `uframe`, `sof_count` | 11, 3, 8 | last SOF frame number, microframe (0-7, counts repeats of the frame number), SOF count |
| `suspended`, `attached` | 1 | |
| `reset_seen` / `reset_ack` | | bus reset latch (as before) |

**Events** (sticky bits in `events`, cleared by writing the bit to `ev_clr`; `irq` = `events & ev_mask != 0`; `ev_mask` resets to 0):

| bit | name | set when |
|---|---|---|
| 0 | `EV_SUSPEND` | 3 ms idle |
| 1 | `EV_RESUME` | host K seen (debounced), or `CMD_WAKEUP` started |
| 2 | `EV_VBUS` | `vbus & connect` changed |
| 3 | `EV_SETUP` | SETUP data ACKed (read `seq`, `setup_data`) |
| 4 | `EV_RESET` | bus reset detected (read `speed`) |
| 5 | `EV_SOF` | any SOF (tick) |
| 6 | `EV_FIFO` | an OUT packet was committed or an IN packet freed (threshold = one packet; read `ep_level`) |

**Data ports** (`ep_in1..4` = producer -> host, `ep_out1..4` = host -> consumer): `bulk.PacketStream`: `data`, `valid`, `last`, `zlp`, `ready`. `ready` is the credit: FIFO space on IN, consumer readiness on OUT; the bus NAKs when it is missing. Another storage can implement the `PacketFifo` ports (`w_idx/w_data/w_en/w_commit/w_len/w_free`, `r_have/r_len/r_idx/r_data/r_pop`, `flush`) behind the same adapters ([HyperRAM-backed option](#fifo-options)).

**Link states**: FS idle J / HS idle SE0 for `SUSPEND_CYCLES` (3 ms): suspend. HS first goes to FS terminations for `PROBE_CYCLES` (100 us): SE0 = reset (chirp), J = suspend. K for `DEBOUNCE_CYCLES` (2.5 us) = resume; end of K -> prior speed. `vbus` or `connect` low: detached (no pull-up, `op_mode` non-driving), address and endpoints cleared. All constants are class attributes divided down in the sims.

**Sim results** (2026-11-07, box load 30-60, so wall times are upper bounds):

- `usb_device_ep0_sim.py`: 111 checks (56 before this increment), 4 s. Sections added: sof, suspend, suspend_hs, vbus, events.
- `usb_device_bulk_sim.py`: 168 checks, 146 s (the `large` section dominates; its throttled consumer lives 400k cycles). Sections: out, in, in_zlp, backpressure, ping, halt, control, large, rate; FS and HS except ping and rate.
- Closed loop (GUH host engine -> engine, stand-ins in [`usb_own_device.py`](../scripts/soc/usb_own_device.py) `BulkModel`: EP1 echo, EP2 interrupt counter, EP3 2*512+37 pattern source, EP4 throttled echo): `own_ctrl_*`, `own_address`, `own_relay_*` unchanged and green (14, 19, 5, 19, 5); `own_bulk` runs the LUNA-model `bulk` group (18 of 19 cases, `bulk_zlp_out` not applicable: the model drops a ZLP OUT, ours echoes it); `own_bulk_x` adds ZLP echo, short packets, halt both legs, a 3-packet 2*512+37 IN transfer, and a throttled-consumer echo (5 cases).
- Mutation proof: [`usb_device_mutants.py`](../scripts/soc/usb_device_mutants.py) breaks one line of the gateware per mutant (26 mutants: toggles, NYET, PING, halt, flush, auto-ZLP, split, ACK window, SOF, suspend, wakeup, resume speed, probe, VBUS, events); the named sections must FAIL. 26 of 26 killed, 2026-11-07, 8 workers, wall time ~25 min at load ~60.
- GUH host has no PING ([`guh/sie.py`](../gateware/probes/usb_host/guh/sie.py) TODO, tracked in [#572](https://github.com/awtoau/cynthion-workspace/issues/572); NYET is reported as ACK): PING and NYET are covered by the directed sim only. The closed-loop groups reset the device only at bring-up (a reset costs ~12 s of sim), so reset / suspend cases are directed-sim only.

Whole engine, including FIFOs and the codecs (yosys `synth_ecp5`, `usb_device_area.py`):

| config (EP1..n, both directions) | LUT4 | CCU2C | FF | DPR16X4 | DP16KD |
|---|---|---|---|---|---|
| 1 EP, 1 x 512 B per FIFO | 1129 | 171 | 592 | 16 | 2 |
| 1 EP, 2 x 512 B | 1158 | 171 | 620 | 16 | 2 |
| 4 EP, 1 x 512 B | 2136 | 219 | 978 | 16 | 8 |
| 4 EP, 2 x 512 B | 2261 | 219 | 1090 | 16 | 8 |
| 4 EP, 4 x 512 B | 2568 | 219 | 1282 | 16 | 8 |
| 4 EP, 2 x 64 B (FS only) | 2555 | 207 | 858 | 144 | 0 |

#### FIFO options

Rates: `usb_device_bulk_sim.py rate`, 2026-11-07, AUX side at 1 byte / cycle, 12 packets, wire = 512 B + token + handshake + lead-in = ~530 cycles in the sim's model (no inter-packet gaps; protocol maximum is in [Throughput](#throughput)).

One DP16KD is 2 KiB; one FIFO needs one block whatever its depth up to 4 x 512 B, so depth up to 2 KiB per FIFO costs LUTs and FFs, not block RAM. 4 endpoints x 2 directions = 8 FIFOs.

| option | per FIFO | DP16KD (8 FIFOs) | engine LUT4 + 2 x CCU2C | HS bulk, sim cycles per 512 B packet (OUT / IN) | notes |
|---|---|---|---|---|---|
| A: 1 x 512 B | 512 B | 8 | 2574 | 1049 / 1002 | one packet in flight: every OUT ends in NYET, host PINGs until the consumer has drained 512 B |
| B: 2 x 512 B | 1 KiB | 8 | 2699 | 530 / 535 | double buffering: wire rate while the AUX side runs at 1 byte / cycle |
| C: 4 x 512 B | 2 KiB | 8 | 3006 | 530 / 535 | same rate as B at full speed; buys 3 packets of AUX-side latency slack |
| D: 2 x 64 B, FS only | 128 B | 0 (144 LUTRAM) | 2969 | n/a | FS-only build, no block RAM |
| E: HyperRAM-backed deep buffer | MiBs | 0-2 (staging) | estimate +300..500 | bounded by HyperRAM | not built, see below |

- Recommendation: B (2 x 512 B). It reaches the wire rate in the sim at the same 8 blocks as A and costs 125 LUT4 more; C adds 307 LUT4 for no throughput. Dan decides depth; B is the smallest that does not stall the HS pipeline.
- Packing several FIFOs into one block would save block RAM (2 FIFOs of 1 KiB per DP16KD -> 4 blocks) but the AUX-side port of a shared block serialises two endpoints; not built.
- E (open question 4): the `PacketFifo` ports stay; a HyperRAM variant keeps one staging slot per active FIFO in block RAM (or a shared pair) and moves whole packets to and from a ring in HyperRAM. Arithmetic, not built: one 426 Mbps stream is 53 MB/s; a FIFO leg writes and reads it once (106 MB/s); both directions at line rate ~212 MB/s against the 311 MB/s measured read rate at CK 165 ([`sequential-baseline.md`](chips/hyperram/sequential-baseline.md)), shared with the SoC, so HyperRAM suits a few deep endpoints (mass storage), not all four at line rate.

**What the CSR master / AUX side must provide**

- On `EV_RESET`: read `speed`; after enumeration SET_CONFIGURATION / SET_INTERFACE: `CMD_EP_ENABLE` per endpoint with the descriptor's wMaxPacketSize (<= 512 HS, <= 64 FS) and the current `seq`.
- On `EV_SETUP`: read `seq`, `setup_data`, relay; answer EP0 with `CMD_IN` / `CMD_STATUS` / `CMD_STALL` tagged with that `seq`; endpoint halt / clear / flush per SET_FEATURE / CLEAR_FEATURE / dequeue.
- Local answers (GET_STATUS halt bits, remote-wakeup enable) and `CMD_WAKEUP` policy stay with the master; the engine drives K whenever told and suspended.
- Streams: consume `ep_out` with `ready`, produce `ep_in` with `last` / `zlp`; same 60 MHz `usb` domain or a CDC FIFO in front.
- `vbus` input from the PHY's VbusValid; `connect` = soft pull-up.

**Needs the real ULPI PHY / board** (sim has none of it): chirp, reset and suspend timing against real hosts; FS terminations switch (`xcvr_select`/`term_select` via ULPI function control) latency inside the 100 us probe; resume K drive with `op_mode` 2 in FS; `line_state` from RXCMD for K / J / SE0 and VbusValid; PING / NYET turnaround against a real HS host; the 3 ms idle with a real SOF-less bus; stream CDC to the AUX PHY domain.

## Open questions

Each becomes an issue.

1. Link protocol: 1:1 AUX pipe per endpoint (proposed) vs one multiplexed bulk pair; reuse of [`binary-protocol.md`](binary-protocol.md) framing for the control channel or not.
2. Kernel driver distribution: out-of-tree module (DKMS?) vs in-tree submission; module location in this repo.
3. luna's role: sim oracle only (no luna in `gateware/soc`, [#612](https://github.com/awtoau/cynthion-workspace/issues/612)); confirm it is not used on AUX during increments 1-2.
4. HyperRAM role, if any: deep per-endpoint buffering (mass storage bursts, PC scheduling jitter) vs BRAM only.
5. Console on AUX while the bridge runs: keep CDC-ACM in the AUX composite or move it.
6. Device-mode build: SoC role on `target_phy` vs separate bitstream; decides which BRAM is freed.
7. Non-Linux remote hosts with control timeouts below the spec: measure, then decide whether a cache is ever needed.
8. Isochronous: AUX iso pipes or framed bulk; UAC/UVC gadgets depend on it.
9. Name.
