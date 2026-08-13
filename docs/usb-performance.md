# LUNA USB gateware: measured performance

LUNA's USB2 stack on Cynthion r1.4. It reaches 91% of the USB 2.0 protocol
maximum, responds to a token in one clock cycle, and never NAKs. Every figure
below that fell short traced to the measurement harness or the physical
topology, not to the gateware.

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

Instrumented in gateware over **284,306 transactions** at 388 Mbps
(`gateware/probes/usb_bulk/usb_timing.py`):

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

Three of the numbers above were initially wrong, all because the instrument was
doing work inside the timed region.

**Per-byte verification in Python.** Checking a counting sequence byte by byte as
it arrived cost 3.4 ms per 64 KiB — a ceiling of ~150 Mbps, *below the figure it
then reported as the link speed*. It applied to IN only, producing a 56% IN/OUT
asymmetry that looked exactly like a gateware defect and was investigated as one.
Buffering the capture and verifying afterwards raised IN by 53% and collapsed the
asymmetry to 9%.

**Reporting payload instead of bus traffic.** A loopback carries every byte in
both directions, so the bus figure is double the payload one. Quoting payload
alone understated the link by exactly 2×.

**Comparing along a diagonal.** Measuring AUX-through-hubs against TARGET-direct
changes two variables at once. The conclusion happened to be right, but it was an
inference dressed as a measurement until the fourth cell of the matrix was filled
in.

Rule: do nothing to the data while the clock is running, and when a result
surprises, suspect the instrument before the device.

## Files

| path | purpose |
|---|---|
| `gateware/probes/usb_serial/usb_serial.py` | CDC-ACM device, host sees a tty |
| `gateware/probes/usb_bulk/usb_bulk.py` | bulk loopback, with and without a FIFO |
| `gateware/probes/usb_bulk/usb_oneway.py` | one direction at a time, selectable PHY |
| `gateware/probes/usb_bulk/usb_timing.py` | per-transaction timing from inside the FPGA |
| `scripts/usb_serial_speed.py` | CDC throughput, payload and bus columns |
| `scripts/usb_oneway_speed.py` | one-way throughput, verification after timing |
| `scripts/usb_async_speed.py` | queue-depth sweep with libusb1 |
| `debris/scripts/usb_speed_native.c` | native C, to rule out the host language |

## In context

The capture path is bounded by the slower of USB and HyperRAM:

| | MB/s |
|---|---|
| HyperRAM, FIFO access at 512-byte granularity | 220.2 |
| USB bulk, direct port | 48.5 |

4.5× headroom. USB is the constraint.
