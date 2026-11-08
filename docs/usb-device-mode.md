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
| 4 | bulk data path: EPn FIFOs, AUX pipes, `queue`/`dequeue`/`halt` | packet boundaries, ZLP, NAK backpressure both legs, halt + toggle | ACM loopback with data check; ZLP on 512-multiple writes |
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

### AUX backend status

`cudc_aux` ([`linux/cynthion_udc/cudc_aux.c`](../linux/cynthion_udc/cudc_aux.c)) implements `struct cudc_backend_ops` over the AUX link ([`aux-link.md`](aux-link.md)); the stub stays for tests.

- Test: `cudc_vm_test.py --backend aux`, VM only; a Python model of the Cynthion (virtual USB device on `vhci_hcd`) stands in for firmware relay, FIFOs and `Ep0Device`. Every stub group passes through the real backend; added groups: 512 B / 4102 B control messages, stale control reply, stale endpoint command, AUX console coexistence.
- Differences from the stub that the groups accommodate: commands reach the Cynthion asynchronously (state is polled), and the endpoint FIFO absorbs 4 packets before the remote host sees a NAK.
- Relay timing in the VM is a MODEL: SETUP sent -> reply received median 0.2 ms, max 21 ms at host loadavg ~30 (Python model and vhci; not USB timing).
- Not done: FS per-packet pipes, CSR/IRQ frames (reserved in the spec), HELLO-reset on reconnect, board.

### Increments 1 and 2: sim status

Device engine: [`gateware/usbcore/device.py`](../gateware/usbcore/device.py) (`Ep0Device`), our L1 codecs plus EP0 and a CPU command port. luna stays a sim oracle only (the harness runs the same case table against luna's model device and against this engine).

- Engine: bus-reset detect, device chirp K, three host K-J pairs -> HS else FS; EP0 SETUP/IN/OUT/status with 64 B in/out buffers; STALL; SET_ADDRESS applied after the status ACK. Other endpoints get no response.
- CPU port: `seq` bumps on every SETUP token and bus reset; every command carries `cmd_seq`, a stale one is dropped and counted (`stale`). Increment 1 = firmware answers from a table; increment 2 = firmware relays the SETUP and answers with what the PC stub returns.
- Directed sim, [`scripts/soc/usb_device_ep0_sim.py`](../scripts/soc/usb_device_ep0_sim.py): speed negotiation, handshakes, toggles, retransmit, STALL, address, OUT data, stale seq, new SETUP aborts the armed transfer; `--negative` proves the stale checks can fail.
- Closed loop, [`scripts/soc/usb_target_sim.py`](../scripts/soc/usb_target_sim.py) `own_*` groups: GUH host engine + shim + PHY model against `OwnDevice` ([`own_device.py`](../gateware/probes/usb_host/own_device.py)) with the firmware stand-in [`usb_own_device.py`](../scripts/soc/usb_own_device.py). `own_relay_*` adds a modelled AUX control channel and a stub (delay in clock cycles only; framing not modelled). `--sabotage` serves a wrong descriptor byte and must FAIL.
- Not built: CSR wrapper and firmware, PING, SOF/suspend, EP1-4 bulk FIFOs, the AUX device side. The sim cannot say anything about milliseconds.

Cells ([`scripts/soc/usb_device_area.py`](../scripts/soc/usb_device_area.py), yosys `synth_ecp5`, engine alone, no place and route):

| block | LUT4 | CCU2C | FF | DPR16X4 | DP16KD |
|---|---|---|---|---|---|
| `Ep0Device` (whole, incl. the codecs below) | 598 | 73 | 322 | 16 | 0 |
| token detector | 50 | 0 | 46 | 0 | 0 |
| data receiver | 49 | 0 | 45 | 0 | 0 |
| data generator | 119 | 0 | 33 | 0 | 0 |
| handshake detector + generator | 23 | 0 | 16 | 0 | 0 |

- The two 64 B buffers are distributed RAM (16 x DPR16X4), no block RAM. One DP16KD for both buffers is the alternative; the SoC has 2 spare ([`soc-size-review.md`](soc-size-review.md)).
- Not counted: CSR wrapper, `usb`/`sync` crossing, EPn FIFOs. Against 24288 LUT4 the engine is about 2.5 % (LUT4 + 2 x CCU2C); the SoC is routing-bound at 92 % ([#619](https://github.com/awtoau/cynthion-workspace/issues/619)), so whether it fits depends on the host engine leaving a device-mode build (open question 6).

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
