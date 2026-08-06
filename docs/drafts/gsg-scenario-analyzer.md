# Scenario 1: USB protocol analyzer (Packetry)

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §1.

## What it is

Capture Low-, Full- and High-Speed USB 2.0 traffic passing through the TARGET port pair
and stream it to Packetry on the host. This is Cynthion's headline capability and the
factory-default bitstream.

## What implements it upstream

- Gateware: `cynthion: cynthion/python/src/gateware/analyzer/top.py::USBAnalyzerApplet`
  — capture engine `analyzer/analyzer.py`, HyperRAM FIFO `analyzer/fifo.py` (2^22 × 16-bit),
  `analyzer/speed_detection.py`, `analyzer/event_detection.py`.
- Firmware: **none**. Pure gateware, no CPU.
- Host: `packetry: src/backend/cynthion.rs`.

## Hardware it needs

TARGET-C or TARGET-A carrying the traffic under observation, CONTROL carrying the capture
stream to the host. Two cables. A device and a host to watch — or scenario 2's built-in
test device instead.

## What porting it would require

The absence of a CPU is the good news: nothing here needs firmware, a PAC, or the libgreat
protocol. It is entirely a gateware problem.

Needed, and not present:

1. **P1, the ULPI packet path on `target_phy`.** Today that PHY reaches only
   `ulpi_window.UlpiRegisters`. The analyzer needs the full `luna.gateware.interface.ulpi`
   PHY and a UTMI-level receive path. The `USBSerialDevice` already running on `aux_phy`
   proves the layer works on our platform.
2. **The capture engine itself.** `USBAnalyzer` is ~400 lines of Amaranth with no luna-soc
   dependency. It should port more or less as-is; the question is whether we take it or
   write our own.
3. **A HyperRAM stream port.** Upstream's `HyperRAMPacketFIFO` instantiates luna's
   `HyperRAMInterface`/`HyperRAMPHY` directly. We have our own DQS PHY
   (`gateware/soc/peripherals/hyperram_dqs_phy.py`) behind a Wishbone map at `0x2000_0000`, tuned
   and measured. Reusing ours means writing a streaming front end for it; using luna's
   means two HyperRAM controllers in one repo. **This is the one real design decision in
   this scenario** and it should be settled before any code is written.
4. **A bulk IN endpoint on CONTROL**, plus the vendor request handler for the five
   control requests, plus the descriptors.
5. **The port request.** Already solved — `gateware/sideband_advertise.py`.
6. **VBUS switching.** `gateware/soc/peripherals/vbus_csr.py` exists but is CPU-driven; the
   analyzer has no CPU, so the switch control must be driven from gateware state instead.

Match the wire contract exactly and Packetry works unmodified:

| Item | Value |
|---|---|
| VID:PID | `1d50:615b` |
| Interface | class `0xff`, subclass `0x10`, protocol `0x01` |
| Capture stream | bulk IN `0x81`, 16 KiB reads, 4 transfers in flight |
| Vendor requests | `0` get state, `1` set state, `2` get speeds, `3` set test-device config, `4` get protocol minor version |

State byte: capture-enable bit plus a 2-bit speed selector.

## How it would be tested

- **QEMU: no.** No firmware exists to run, and `-M virt` has no ULPI.
- **Simulation: yes, and this is where the coverage has to live.** A bus-functional ULPI
  model driving known packet sequences into the capture engine, asserting the byte stream
  that comes out the other end, is a pure-`pysim` test that would sit in
  `scripts/soc_sims.py`. It needs P2 built first. The HyperRAM FIFO can be simulated
  against the existing HyperRAM model separately.
- **Hardware: required for the end-to-end claim.** Packetry connecting, negotiating speed,
  and decoding a real enumeration is not simulatable. That test belongs in
  `./dev.py test-board`, gated on hardware, not in `gate`.

The split: the capture engine and the FIFO can be covered in `gate`. Speed detection
against a real chirp, and Packetry interop, cannot.

## Verdict

**Hard.** The largest gateware build of any child, and it carries the repo's only genuine
architectural decision (whose HyperRAM controller). But it needs no firmware, no PAC and
no host tooling, and its wire contract is completely specified by Packetry's source.
