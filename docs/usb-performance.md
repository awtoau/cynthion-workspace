# LUNA USB gateware: measured performance

LUNA USB2 stack on Cynthion r1.4: 91% of the USB 2.0 protocol maximum, one-cycle token response, zero NAKs. Shortfalls traced to the harness or topology, not the gateware. Hub-depth effect on the SAMD11 link: [apollo_samd11_mcu/apollo-configure-speed-investigation.md](apollo_samd11_mcu/apollo-configure-speed-investigation.md).

## Results

All on r1.4, high speed negotiated, 512-byte bulk packets, ULPI PHY.

| configuration | Mbps | MB/s | % of 426 |
|---|---|---|---|
| USB 2.0 line rate | 480.0 | 60.0 | — |
| Protocol maximum (13 × 512 B per microframe) | 426.0 | 53.2 | 100% |
| **Bulk IN, direct root port** | **388.0** | **48.5** | **91%** |
| Bulk OUT, direct root port | 338.8 | 42.3 | 80% |
| Bulk IN, four hub levels deep | 292.2 | 36.5 | 69% |
| Bulk loopback with a FIFO | 244.2 | 30.5 | 57% |
| CDC-ACM loopback, combinational | 195.4 | 24.4 | 46% |

The protocol maximum was derived from the spec rather than quoted: a 512-byte
bulk transaction is 4512 bit times at 2.0833 ns = 9.40 µs, so 13 fit in a 125 µs
microframe. That matches the commonly cited `1000 × 8 × 512 × 13 ≈ 53 MB/s`.

## The device is not the constraint

Instrumented in gateware over **284,306 transactions** at 388 Mbps:

| measurement | value | what it means |
|---|---|---|
| ACKs per IN token | **1.0000** | zero NAKs, zero retries |
| bytes per token | **512.0** | every packet full length |
| token → first data byte | **1 cycle, 17 ns** | LUNA responds immediately |
| last byte → ACK | 9.30 µs minimum | bus turnaround, host side |
| ACK → next token | 317 ns minimum | host asking again |

Against a ~10.6 µs transaction period, the device contributes **0.16% of the
budget**. The kernel logged no errors, resets, stalls or babble during the run.

A device that NAKs makes the host back off, which from the host is
indistinguishable from a slow host. The ratio being exactly 1.0000 rules that
out.

## Choosing an endpoint type

CDC-ACM and raw bulk, measured back to back with the same loopback:

| | Mbps |
|---|---|
| CDC-ACM, combinational loopback | 195.4 |
| Raw bulk, combinational loopback | 188.0 |
| Raw bulk, FIFO loopback | 244.2 |

CDC costs essentially nothing. LUNA's `USBSerialDevice` is `USBStreamInEndpoint`
plus `USBStreamOutEndpoint` plus descriptors — no added data path. Swapping CDC
for bulk while holding the loopback constant changed nothing (bulk was marginally
*slower*); swapping the wire for a FIFO gained 26%. Choose CDC when a tty is
wanted, bulk when it is not; how the endpoints are connected is what matters.

`max_packet_size` does matter, and defaults wrongly for high speed: it is 64, the
full-speed bulk limit. Leaving it there enumerates at high speed while running at
roughly an eighth of the achievable rate.

## What limits throughput, in order of size

**1. USB topology — 33%.** Four hub levels cost 292.2 Mbps against 388.0 on a
direct root port. Confirmed with the PHY held constant: AUX measured 388.0 direct
and 292.2 through hubs, TARGET measured 387.8 direct — the two PHYs differ by
0.05%, so the entire gain is hub depth.

**2. Combinational loopback — 26%.** Wiring `rx.ready` to `tx.ready` defeats
LUNA's double buffering: while a packet awaits ACK, `tx.ready` drops, which drops
`rx.ready`, which NAKs the host's next OUT packet. Every packet then pays a full
bus round trip. A packet-sized FIFO between the streams removes it.

**3. Host controller scheduling — the remaining 9%.** Outside the device, not
recoverable from this side.

## Things that turned out not to matter

Recorded so they are not re-investigated:

- **Host language.** Five implementations — pyserial, pyusb synchronous, libusb1
  async at four queue depths, and native C with no interpreter — all landed
  between 287 and 297 Mbps through the same hub chain. C beat the best Python by
  1.2%.
- **Queue depth.** Depths of 1, 4, 8 and 16 are indistinguishable in both
  languages. The hypothesis that unpipelined synchronous I/O was leaving the bus
  idle is false.
- **Stale submodules.** `repos/luna` is level with upstream, and the installed
  `luna_usb` is byte-identical to it across `transfer.py`, `endpoints/stream.py`,
  `acm.py` and `stream.py`.
- **Clock configuration.** The entire USB data path is `usb`-domain only — 24
  `m.d.usb` statements and zero `m.d.sync` in the transfer and endpoint code — so
  there is no clock-domain crossing between endpoint and application logic.
  Raising `sync` would not help: at 60 MHz × 8 bits the datapath already carries
  480 Mbps, above the protocol ceiling.
- **LUNA issue [#276](https://github.com/awtoau/cynthion-workspace/issues/276)**, which caps speed to full speed, applies only to custom
  UTMI PHYs. `USBDevice` sets `always_fs = False` on the ULPI path Cynthion uses.

## Measurement traps

- Do nothing to the data while the clock is running: per-byte Python verification cost 3.4 ms per 64 KiB (ceiling ~150 Mbps) and applied to IN only; buffer the capture, verify after.
- Loopback carries every byte both directions: bus figure = 2x payload.
- Vary one variable per cell (AUX-through-hubs vs TARGET-direct changes two).
- When a result surprises, suspect the instrument before the device.

## Re-measurement 2026-11-06 (#612 S0)

Everything above this section is the OLD measurement (undated, pre-cf62c6d harness). Numbers here are NEW.

- **Conditions**
  - Date 2026-11-06T11:28+11:00; commit `a5e54da` (worktree branch of #612 S0); `scripts/usb/usb_baseline.py run --runs 3 --pidcodes`.
  - Host `selwyn`: Intel i9-14900K, Fedora kernel 7.2.15-300.fc45, Intel 700-series xHCI (`00:14.0`), Python 3.15.2t, pyusb 1.3.1, pyserial 3.5.
  - Gateware: luna_usb 0.2.3.post15+git.352cdbfc, amaranth 0.5.10, `usb`/`sync` = 60 MHz oscillator direct (no PLL, unlike the old LUNA generator); AUX PHY, high speed (sysfs `speed` 480), 512-byte bulk packets.
  - Host transfer 64 KiB; one-way window 3 s; loopback 32 MiB per run; per-transfer limit 23 ms.
- **Topology: NOT a direct root port.** AUX at `1-1.3.1.3.4`, behind 4 USB 2.0 hubs (`17ef:a392`/`a394`). `lsusb -t` excerpt:
  ```
  Bus 001.Port 001: root_hub xhci_hcd/16p 480M
    Port 001: Dev 002 Hub 480M
      Port 003: Dev 005 Hub 480M
        Port 001: Dev 011 Hub 480M
          Port 003: Dev 016 Hub 480M
            Port 002: Dev 020 btusb + rtw89_8851bu (Wi-Fi) 480M   <- same hub
            Port 003: Dev 022 Hub (Apollo on CONTROL, 12M, below it)
            Port 004: Dev 110 Cynthion AUX 480M
  ```
  - A Wi-Fi adapter shares the last hub; not idle-controlled.
- **Results** (Mbps; loopback = bus figure, 2x payload; 3 runs each)

| test | run 1 | run 2 | run 3 | median | spread | old (same topology class) |
|---|---|---|---|---|---|---|
| bulk IN | 294.0 | 288.4 | 272.3 | 288.4 | 21.7 (7.5%) | 292.2 (four hubs) / 388.0 (direct) |
| bulk OUT | 297.3 | 290.6 | 284.3 | 290.6 | 13.0 (4.5%) | 338.8 (direct) |
| bulk loopback, FIFO | 265.7 | 264.7 | 265.8 | 265.7 | 1.1 (0.4%) | 244.2 (topology not recorded) |
| CDC-ACM loopback, combinational | 212.4 | 213.0 | 213.9 | 213.0 | 1.5 (0.7%) | 195.4 (topology not recorded) |

- Bulk IN through 4 hubs matches the old four-hub figure (288.4 vs 292.2); the old 388.0 direct-port figure is not re-tested (no direct port cabled).
- Bulk OUT through 4 hubs (290.6) is below the old direct figure (338.8); not comparable, different topology.
- Both loopbacks are above the old figures through 4 hubs; cause not isolated (clocking change, host, kernel, topology of the old run unknown).
- IN/OUT spread (4.5-7.5%) is larger than loopback spread (<1%); shared hub traffic is a candidate, UNVERIFIED.
- **Checks, every run**: 0 data errors; IN sequence verified host-side after timing; OUT verified by the device checker (device byte count = host byte count) over JTAG.
- **Negative controls, every run**: one byte of the expected data corrupted host-side -> checker reports exactly 1 error at that index (IN, both loopbacks); one byte corrupted in an OUT chunk -> device checker reports 2 errors (the byte and its successor).
- Build: TRELLIS_COMB oneway 2095, bulk FIFO 2095, CDC 2008 (of 24288).
- `--pidcodes`: bulk probes enumerate as `1209:000a`; the installed `55-cynthion-test.rules` predates `ede08a0` and udev ignores every rule in it (trailing comments), so `1d50:6182`/`6188` get no uaccess. Fix: `sudo ./scripts/install_udev.py`.

### Same day, AUX on a direct root port

- Date 2026-11-06T11:52+11:00; commit `fb22981`; `scripts/usb/usb_baseline.py run --runs 3 --pidcodes`; same host, gateware and packet size as above.
- **Topology: direct.** The runner reports each probe at `1-7`, 480 Mbps, 0 hub levels from the root port; `lsusb -t` shows `Bus 001 Port 007` (AUX, `1d50:6180`) on the xHCI root hub, no hub between. CONTROL (Apollo, `1d50:615c`) is still at 12M behind the hub chain.

| test | run 1 | run 2 | run 3 | median | spread | old direct |
|---|---|---|---|---|---|---|
| bulk IN | 372.7 | 374.8 | 375.3 | 374.8 | 2.7 (0.7%) | 388.0 |
| bulk OUT | 310.0 | 312.2 | 309.6 | 310.0 | 2.6 (0.8%) | 338.8 |
| bulk loopback, FIFO (bus) | 386.1 | 383.7 | 384.1 | 384.1 | 2.4 (0.6%) | 244.2 |
| CDC-ACM loopback (bus) | 265.0 | 263.9 | 262.8 | 263.9 | 2.2 (0.8%) | 195.4 |

- Against the 426 Mbps protocol maximum: bulk IN 88% (old 91%), bulk OUT 73% (old 80%). Both are about 3-8% below the old figures; cause not isolated (clocking change from the PLL generator to the 60 MHz oscillator, host, kernel).
- Direct versus four hubs: IN 374.8 vs 288.4, OUT 310.0 vs 290.6, FIFO loopback 384.1 vs 265.7, CDC loopback 263.9 vs 213.0. Spread falls from 4.5-7.5% to under 1% with the shared hub gone.
- 0 data errors in every run; the negative control (one corrupted byte) was reported each time.
- These are the baseline the own USB stack (#612) must beat or match.

## Files

| path | purpose |
|---|---|
| `scripts/usb/usb_baseline.py` | build, load, measure all four tests, negative controls; log `tmp/logs/usb_baseline.log`, rows `tmp/usb-baseline/results.jsonl` |
| `gateware/probes/usb_bulk/usb_oneway.py` | bulk IN source, bulk OUT checker, selectable PHY |
| `gateware/probes/usb_bulk/usb_bulk.py` | bulk loopback, FIFO or combinational |
| `gateware/probes/usb_serial/usb_serial.py` | CDC-ACM loopback (LUNA `USBSerialDevice`) |
| `gateware/probes/usb_domains.py` | `usb`/`sync` from the oscillator, PHY power-on reset |
| `debris/scripts/usb_serial_speed.py`, `usb_oneway_speed.py`, `usb_bulk_speed.py` | the old host scripts (superseded by `usb_baseline.py`) |
| `debris/scripts/usb_async_speed.py` | queue-depth sweep with libusb1 |
| `debris/scripts/usb_speed_native.c` | native C, to rule out the host language |
| `usb_timing.py` (cf62c6d^, not restored) | per-transaction timing from inside the FPGA |

## In context

The capture path is bounded by the slower of USB (48.5 MB/s, direct port) and HyperRAM ([chips/hyperram/soc-peripheral.md](chips/hyperram/soc-peripheral.md), #588).
