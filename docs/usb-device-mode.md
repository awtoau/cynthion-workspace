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

## Device-mode bitstream

Decision (Dan, 2026-11-07, [#612](https://github.com/awtoau/cynthion-workspace/issues/612)): a SEPARATE bitstream, the current SoC plus the device engine. Nothing is removed. The SoC bitstream is unchanged with the variables below unset.

- Build: `CYNTHION_DEVICE_MODE=1 [CYNTHION_AUX_BRIDGE=1] [CYNTHION_CORE=lean|min] ./scripts/soc_run.py --build-only --skip-tests`; variant dir `tmp/awto_soc/build/ck180-sync60-devmode1[-bridge1][-core<name>]` (table: `gateware/soc/variant.py`). Utilisation: `scripts/soc/soc_util_report.py`.
- `CYNTHION_DEVICE_MODE`: adds `usb_dev` ([`peripherals/usb_device.py`](../gateware/soc/peripherals/usb_device.py): `Ep0Device` + ULPI translator + CSR wrapper) beside `usb_host`. Pins chain PHY -> `usb_dev` -> `usb_host` -> register window; `ROLE` hands the PHY over only when `dir` is quiet.
- `CYNTHION_AUX_BRIDGE`: adds [`AuxCsrBridge`](../gateware/soc/peripherals/aux_csr_bridge.py), a 4th bus master: Linux reads and writes every peripheral over AUX. `aux_select` bit 0 hands the one AUX CDC pipe from the console (default) to the bridge until the AUX device has a second interface.
- `CYNTHION_CORE`: core profile, [`riscv-core-build.md`](riscv-core-build.md#core-profiles-cynthion_core-612).
- Bulk endpoint FIFOs and the AUX device engine belong to another agent; they are not in these numbers.
- Sims: `scripts/soc/usb_device_csr_sim.py` (11 checks), `scripts/soc/aux_csr_bridge_sim.py` (6 checks).

### Memory and CSR map (additions to the SoC's map)

| window | base | size | what |
|---|---|---|---|
| `usb_dev` | `0xf0001040` | 64 B | device engine CSRs (map in the `usb_device.py` docstring) |
| `aux_select` | `0xf0001080` | 4 B | bit 0: AUX pipe owner (0 console, 1 bridge); bridge builds only |
| IRQ 11 | | | `usb_dev` events, level, via `intc` |

Everything else is as in `gateware/soc/top.py`: RAM `0x0` (32 KiB), flash XIP `0x10000000`, CSR block `0xf0000000` (console `+0x0`, apollo_uart `+0x500`, board `+0x600`, hyperram `+0x800`, intc `+0xc00`, usb_host `+0x1000`, CLINT `+0x10000`, hyperram window `+0x20000`, flash `+0x21000`).

### Fit (synthesis + place and route, no board)

nextpnr `Device utilisation` (`scripts/soc/soc_util_report.py`), 2026-11-07, this worktree, `--build-only`; every build below routed and wrote a bitstream. COMB under ~200 is mapper noise ([`soc-size-review.md`](soc-size-review.md)). Of 24,288 COMB / 24,288 FF / 56 DP16KD.

| build | COMB | FF | DP16KD | LUTRAM (DPR16X4) |
|---|---:|---:|---:|---:|
| (a) current SoC (unchanged by this work: rebuilt after the changes, same four numbers) | 18,945 | 11,485 | 54 | 20 |
| device engine on the current core | 20,081 | 12,113 | 54 | 36 |
| (b) + AUX CSR bridge, current core (`full`) | 20,273 | 12,230 | 54 | 36 |
| (c) + AUX CSR bridge, `lean` core (runs the shipping image) | 20,412 | 12,227 | 53 | 34 |
| (c') + AUX CSR bridge, `min` core (rv32imc, I-cache only; firmware rebuild) | 19,296 | 11,554 | 49 | 24 |

- Device engine, wrapper and translator: +1,136 COMB, +628 FF, +16 LUTRAM, 0 DP16KD. Bridge + `aux_select`: +192 COMB, +117 FF.
- (b) is 83% COMB, 50% FF, 54/56 DP16KD: it fits, with the same 2 spare blocks as the SoC.
- `lean` frees 1 DP16KD (53). `min` frees 5 (49) and ~1,000 COMB but does not run today's image ([core cuts](#core-cuts)).
- Shortfall: the bulk endpoint FIFOs are not in these builds. At one DP16KD per pipe ([Block RAM](#block-ram)), 8 pipes need 8 blocks: (b) 62 of 56 (6 short), (c) 61 (5 short), (c') 57 (1 short).
- Options if the FIFOs must fit, nothing removed, none applied (Dan decides):
  1. `min` core (-5, firmware rebuild without A, uncached `.rodata`); 1 block still short.
  2. Two pipes per DP16KD (1 KiB each = 2 packets): 8 pipes in 4 blocks: (b) 58, (c) 57, (c') 53.
  3. Fewer mapped pipes first (4 pipes = 4 blocks, same totals as 2); 4 pipes at two per block = 2 blocks: (b) 56, (c) 55, (c') 51.
  4. Register file in registers: -2 DP16KD, +1,753 COMB, +847 FF measured on the core alone; COMB would reach ~22,000 (91%), where [#619](https://github.com/awtoau/cynthion-workspace/issues/619) says routing binds.
  5. Per-endpoint buffers in HyperRAM (open question 4): 0 blocks, costs latency; not measured here.
  6. RAM 32 -> 24 KiB frees 4 blocks but the image needs `.data` + `.bss` + `.uninit` + stack = 31.7 KB, so `.bss` (20 KB) must shrink first; firmware work.
  7. USB host SIE FIFOs (2 blocks, #619) to LUTRAM: cell cost not measured.

### Core cuts

What the existing firmware needs (`firmware/cynthion-soc` release ELF, `llvm-objdump`, this worktree):

- 51,831 instructions: 40 AMO (RTIC runtime, `MachineExternal`, `MachineTimer`, ...), 186 `mul*`, 154 `div*`/`rem*`. So A and M are needed.
- Image: `.text` 157,012 B and `.rodata` 61,556 B in flash (XIP); `.data` 1,320, `.bss` 20,256, `.uninit` 4,408, stack 5,760 in the 32 KiB RAM.
- XIP `.text` needs the I-cache; A needs the D-cache (`LsuCachelessBridge` asserts `!withAmo`, `cpu.py`). No cache can go without changing the firmware.

Core alone, `yosys synth_ecp5` (`scripts/soc/soc_cpu_knobs.py`, 2026-11-07, no place and route; COMB under ~200 is mapper noise, [`soc-size-review.md`](soc-size-review.md)):

| core | COMB | FF | DP16KD | LUTRAM | DSP | runs existing firmware |
|---|---:|---:|---:|---:|---:|---|
| `full` (shipping, 4 KiB I + 4 KiB D, 1 way) | 5702 | 3353 | 11 | 20 | 4 | yes |
| `lean` (2 KiB I + 2 KiB D) | 5843 | 3350 | 10 | 18 | 4 | yes |
| no A (cached) | 5322 | 3235 | 11 | 20 | 4 | no (AMO) |
| `min`-like `nd`: no A, no D-cache, 4 KiB I | 4796 | 2677 | 6 | 8 | 4 | no (AMO), needs rv32imc rebuild |
| `nd`, 2 KiB I | 4970 | 2675 | 5 | 8 | 4 | same |
| no caches (`nl`), for reference | 4663 | 2445 | 3 | 17 | 4 | no (XIP .text) |

Single knobs from `nl` (each a delta against 4663 COMB / 2445 FF): perf counters 4 -> 0: -30 / -79 (noise in COMB); debug JTAG module: -347 / -633 and 8 LUTRAM; M: -261 / -377 and 4 DSP; divider only: -107 / -171; C: -214 / -99; iterative shifter: +153 (worse); register file in registers: +1753 / +847 and -2 DP16KD.

- Shipping 8 KiB L1 figures in older docs are not the current core (`top.py` `CACHE_SETS` 64 x 1 way).
- `lean` is the smallest core that runs the shipping image unchanged: it frees 1 DP16KD and no logic. Cells did not move with half the cache; only the data and tag arrays shrank.
- `min` (rv32imc, I-cache kept, no D-cache) frees 5 DP16KD and ~900 COMB in the core, at the price of a firmware rebuild without A and uncached `.rodata`.
- Code size: dropping C costs 25% of `.text` (18.8 KB of 75 KB, measured earlier, `chips/vexiiriscv-cpu.md`), so C stays. Dropping A or the D-cache changes no instruction bytes except the AMO sites.
- Speed effect of `lean`: not measured (needs the CPU's own counters on a board). `rtic.md` measured +1,700 B of hot `.text` moving frontend stalls from 44 to 452 per 1,000 cycles through a 4 KiB direct-mapped I-cache, so a 2 KiB I-cache is expected to stall more.

### Firmware crate skeleton plan (relay firmware is not written)

- Same crate (`firmware/cynthion-soc`), feature `device`; shell, PD and RTIC stay.
- `src/usb_dev.rs`: driver over the `usb_dev` CSRs: `snapshot()`, `command(cmd, seq, len, last, addr)`, `push_in(buf)`, `read_out(buf)`, `events()`; stage operands, write `OP`, poll `STATUS.op_busy` (bound 40 sync cycles, from `usb_device_csr_sim.py`).
- `src/relay.rs` (after the AUX link spec lands): SETUP / EP0 data / status messages <-> `usb_dev`; `seq` rules as in [Increments 1 and 2](#increments-1-and-2-sim-status).
- PAC: `soc_generate_pac.py` under `CYNTHION_DEVICE_MODE=1` emits the device map; second SVD beside `soc.svd`, selected by the `device` feature; `target.rs` gets the two window addresses.
- Shell command `usbdev` (syntax only; logic in the driver).

### Risks

- Entering the device role: PHY function control (0x04) must be restored through the register window first (`usb_device.py`); unproven on a PHY.
- Leaving the host role: `usb_dev` checks `target_ulpi.usb_idle` only; firmware sets the `usb_host` ROLE off before `usb_dev` ROLE.
- Chirp and turnaround timing of `Ep0Device` on a real USB3343: sim only.
- `min` core has no A: the RTIC runtime and atomic RMW (40 AMO instructions in today's image) need a `riscv32imc` rebuild (target not installed here; `portable-atomic` or critical-section), and `.rodata` reads from flash go uncached.
- The AUX bridge framing is a stand-in for the AUX link spec.

### Bench steps for later (not done: loading a device bitstream drops the S20's power and PD)

1. Put the S20 on its own supply or accept the PD drop; load the bitstream with `board.py` as for any SoC variant.
2. `info`: USERCODE and `misa` match the build record; read the PHY ID through the ULPI window.
3. `usb_dev` ROLE=1: `STATUS.role` set, `EVENTS.reset` on a bus reset from a second host cabled to TARGET.
4. Second host: `lsusb` sees the device once relay firmware exists; before that, the EP0 table firmware of increment 1.
5. Core cut: `ICACHE_MISS` and `STALLED_CYCLES_FRONTEND` under the shell workload, `full` vs `lean` ([`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md)).
6. Bridge: set `aux_select`, drive `R`/`W` frames over AUX, compare with the shell's `peek`.

## Open questions

Each becomes an issue.

1. Link protocol: 1:1 AUX pipe per endpoint (proposed) vs one multiplexed bulk pair; reuse of [`binary-protocol.md`](binary-protocol.md) framing for the control channel or not.
2. Kernel driver distribution: out-of-tree module (DKMS?) vs in-tree submission; module location in this repo.
3. luna's role: sim oracle only (no luna in `gateware/soc`, [#612](https://github.com/awtoau/cynthion-workspace/issues/612)); confirm it is not used on AUX during increments 1-2.
4. HyperRAM role, if any: deep per-endpoint buffering (mass storage bursts, PC scheduling jitter) vs BRAM only.
5. Console on AUX while the bridge runs: keep CDC-ACM in the AUX composite or move it.
6. Device-mode build: decided, a separate bitstream ([Device-mode bitstream](#device-mode-bitstream)).
7. Non-Linux remote hosts with control timeouts below the spec: measure, then decide whether a cache is ever needed.
8. Isochronous: AUX iso pipes or framed bulk; UAC/UVC gadgets depend on it.
9. Name.
