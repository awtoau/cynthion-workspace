# USB device mode: full design

Draft for review (2026-11-08). Pieces built so far live on unmerged branches; this page is the target design they converge on. Detail: [usb-device-mode.md](usb-device-mode.md) (increments, budgets), [chips/hyperram/soc-peripheral.md](chips/hyperram/soc-peripheral.md) (HyperRAM controller), `docs/aux-link.md` (AUX link spec, on its branch).

## Goal

- A Linux PC runs stock gadget functions (serial, network, storage, ...) through a kernel UDC driver; the Cynthion's TARGET port presents them to a remote USB host.
- Data moves in gateware at high-speed line rate; the RISC-V is the control plane; the shell stays on the SoC.

## Picture

```
 Linux PC                         Cynthion (device-mode bitstream)                      remote host
 gadget functions                                                                      (PC, phone, ...)
   |                     AUX USB                       HyperRAM 8 MiB
 cynthion_udc  <==== console + control channel ====> RISC-V (shell, control plane)
 (UDC driver)  <==== one pipe per endpoint =========> AUX engine <-> rings <-> TARGET engine <==== TARGET USB ====>
```

## Two bitstreams

- SoC image: unchanged (USB host engine, PD, power, HyperRAM for the CPU, capture).
- Device-mode image: the SoC with every peripheral kept, plus the TARGET device engine; HyperRAM is dedicated to the endpoint rings (the CPU stores no data there, but still manages the controller).
- Fit (place and route, rings in HyperRAM): 52-53 of 56 block RAMs, about 81% logic. Endpoint FIFOs in block RAM do not fit (61-62).

## Who does what

| part | does | does not |
|---|---|---|
| Linux: `cynthion_udc` + AUX backend | gadget framework, descriptors, class logic, data URBs | timing-critical work |
| AUX engine (gateware) | console, control channel, endpoint pipes, NAK on full/empty | decide anything |
| RISC-V firmware | relays control transfers, answers SET_ADDRESS locally, assigns and sizes rings, enables/halts/flushes endpoints, calibrates HyperRAM, shell | touch bulk data |
| TARGET engine (gateware) | chirp, EP0 packets, bulk/interrupt packets, toggle, ZLP, PING/NYET, STALL, suspend/resume, SOF | policy |
| HyperRAM controller (existing SoC peripheral) | calibration, command ops, 4 KiB window, errors/IRQ, stream rings | own the rings' layout (the RISC-V sets it) |

## Endpoint types and where their data lives

| type | buffer | path | why |
|---|---|---|---|
| control (EP0) | no ring: 64 B IN + 64 B OUT in the TARGET engine; whole data stage (<= 4096 B) in RISC-V RAM | remote host -> engine -> RISC-V -> AUX control channel -> Linux, and back | low rate, latency-critical, must work before HyperRAM is calibrated |
| bulk | HyperRAM ring per endpoint per direction | AUX pipe <-> ring <-> TARGET engine, all gateware | the bandwidth |
| interrupt | small FPGA FIFO (2 x 64 B, LUT RAM) | AUX pipe <-> FIFO <-> TARGET engine | tiny packets, latency over depth |
| isochronous | HyperRAM ring (later) | as bulk, scheduled per microframe | audio/video; engine support not built |

### Control transfers in detail

- SETUP arrives: the engine ACKs it, NAKs the data/status stages, raises a SETUP event.
- SET_ADDRESS: answered by the RISC-V without a PC round trip; the address applies after the status ACK.
- Everything else: the RISC-V sends `SETUP` on the control channel; Linux answers with the IN data stage, or receives the OUT data stage; the RISC-V feeds the engine 64 B at a time; the engine NAKs the host while it waits.
- A new SETUP aborts the old transfer; each command carries the counter it last saw, stale ones are dropped and counted.
- Limit: data stage <= 4096 B; longer requests are STALLed (no common class needs more).

## Rings

- Count: a build setting, default 8 (4 endpoints x 2 directions), up to 16; each extra ring costs a few registers and counters in logic, no HyperRAM.
- Assigned at enumeration: on SET_CONFIGURATION / SET_INTERFACE the RISC-V maps each active bulk endpoint to a ring and sets its base and size (registers in the HyperRAM controller). Unused rings stay off.
- Sizing: from the active configuration; one high-speed serial port can take two rings of up to 4 MiB; a composite device shares the 8 MiB.
- Built from the existing controller's stream client (one ring today, reserved and not connected), grown to N rings; one HyperRAM core, round-robin among command ops, the stager and the rings.
- Packet-level: start, end, ZLP flag; a packet is readable only once fully written; flush on endpoint disable, bus reset, halt.
- Credit = free ring space: a full ring NAKs the writer's leg, an empty ring NAKs the reader's leg; nothing else signals flow.
- Fairness: one IN endpoint nobody reads must not starve the others (staged packets can be handed back to the ring: to build).

## Limits

- USB: EP0 plus up to 15 IN and 15 OUT endpoints.
- TARGET engine today: EP1-4 bulk each direction; widen as a separate engine change.
- AUX pipes, one per endpoint: AUX also has 15 + 15; console (2 IN, 1 OUT) and control channel (1 + 1) leave 12 AUX IN (for TARGET OUT) and 13 AUX OUT (for TARGET IN). Beyond that, multiplex several endpoints on one pipe (the hybrid fallback).
- Bandwidth: high-speed bus <= ~53 MB/s total; each byte is written to and read from HyperRAM once (~106 MB/s); HyperRAM pin rate 2 B per clock; default clock 140 MHz (2.3x margin in simulation).

| function | bulk rings | interrupt FIFOs |
|---|---|---|
| serial (ACM) | 2 | 1 |
| network (NCM) | 2 | 1 |
| mass storage | 2 | 0 |
| ADB / MTP | 2 | 1 |
| serial + network | 4 | 2 |

## Power in device mode

- TARGET is upstream-facing: the remote host supplies VBUS; the Cynthion presents Rd on TARGET_C and never sources there.
- The TARGET passthrough switches stay open; VBUS on TARGET only drives the engine's VBUS-session input.
- The board is powered from CONTROL/AUX as today; the pass FETs are back-to-back pairs, so an open switch blocks both ways.

## Startup

1. Bitstream loads; the RISC-V boots from flash (this image has no HyperRAM boot header).
2. HyperRAM calibrates (automatic sweep of 128 input delays); bulk rings are refused until it succeeds.
3. The PC sends `HELLO` on the control channel; the Cynthion resets all link state on every `HELLO`.
4. The gadget binds on Linux; the UDC connects (pull-up on TARGET); the remote host enumerates over EP0.
5. SET_CONFIGURATION: the RISC-V assigns rings, enables endpoints; data flows in gateware.

## Failures

- HyperRAM not calibrated: EP0 still works (the device enumerates and reports the error); bulk endpoint enables are refused.
- PC link lost or `cynthion_udc` unloaded: the RISC-V drops the TARGET pull-up, so the gadget disappears from the remote host instead of hanging.
- Firmware stuck: the engine keeps NAKing (no data lost); the watchdog resets the CPU.
- Bus reset from the remote host: all endpoints disabled, rings flushed, Linux told; it re-enables after the next SET_CONFIGURATION.

## Shell and device interface

- The shell stays on the SoC; in this image the HyperRAM commands report that the controller is dedicated to rings.
- The binary device interface reports the image type and ring state (capability discovery).

## Test plan

- VM: kernel backend against a software model of the Cynthion (passing: serial, network, storage-style cases).
- Simulation: TARGET engine (EP0, bulk, PING, suspend, mutation checks), HyperRAM rings with the chip model, AUX side (to build).
- Bench without recabling: the S20 is dual-role; with the Cynthion presenting Rd on TARGET_C the S20 becomes the host and should enumerate a serial or network gadget. Cost: the S20 then powers the port from its battery.
- Bench later: a second PC on TARGET for `lsusb -v`, throughput, FS fallback.

## Status

| piece | state |
|---|---|
| HyperRAM controller with stream client | on main |
| TARGET engine EP0 + bulk + PING + suspend | built on a branch, simulation only |
| HyperRAM rings | built on a branch as a separate controller: to be reworked onto the existing one |
| AUX link spec + Linux AUX backend | built on a branch, VM only |
| Device-mode bitstream switches | built on a branch, place and route only |
| AUX engine (console + control channel + pipes) | to build |
| RISC-V control-plane firmware | to build |
| Interrupt FIFOs, ring hand-back fairness, isochronous | to build |

## Decisions needed

1. AUX side: replace today's LUNA serial device on AUX with our own device engine (console + control channel + pipes in one composite). Recommended: yes (one engine design used on both ports, no LUNA).
2. Ring count: default 8, build setting up to 16. Recommended.
3. Control data stage limit 4096 B, longer requests STALLed. Recommended.
4. Interrupt endpoints in 2 x 64 B LUT-RAM FIFOs, not HyperRAM. Recommended.
5. HyperRAM clock 140 MHz in this image. Recommended (simulation margin 2.3x; board check pending).
6. Block-RAM FIFOs kept only as a build option for simulation and small configurations. Recommended.
7. First bench test with the S20 as the host on TARGET_C. Recommended (no recabling).
