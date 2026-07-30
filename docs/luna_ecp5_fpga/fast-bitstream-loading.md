# Fast bitstream loading: what the measurements say

Investigation of issue #100, deliverable 1 ("a very fast loader"). The proposal
was to load the ECP5's SRAM over a USB bulk endpoint on the FPGA instead of over
JTAG, on the expectation that a 294 KB image would take ~6 ms of transfer
against ~3.0 s today.

**The proposal cannot be built as described, and the reason it is slow today is
not the reason the issue gives.** Both conclusions are supported by measurement
and by primary sources below. The good news is that the real bottleneck is more
tractable than the one that was assumed.

Measured on Cynthion r1.4, 2026-07-29, with `scripts/fast_loader.py`.

## Summary

| claim in issue #100 | status | evidence |
|---|---|---|
| A loader design can configure the SRAM it runs from, using `--background` | **False** | ECP5 has no fabric path to the configuration engine at all |
| ~3.0 s JTAG load is bounded by the JTAG clock | **False** | JTAG clocking is ~1.4 us/byte; feeding the SAMD11 costs ~2.51 us/byte |
| 388 Mbps bulk applies to this path | **Not applicable** | That is the FPGA's USB PHY. The bitstream arrives via the SAMD11, which is **full-speed, 12 Mbps** |
| ~6 ms to move 294 KB over FPGA bulk | **Confirmed** | 7.89 ms measured at 309 Mbps -- see section 2 |
| ~3.0 s baseline | **Confirmed** | 2746 ms measured for a 304726-byte image |

The issue's transfer arithmetic is right. Its conclusion is wrong, because the
transfer it costed is not the one in the critical path. The headline ~6 ms is not
reachable, but a **3-4x** improvement is, and it needs no new gateware.

## 1. The ECP5 cannot reconfigure its own SRAM

This is the crux, and it is a hard negative.

**There is no ICAP-equivalent on ECP5.** The complete list of configuration and
miscellaneous primitives is `JTAGG`, `OSCG`, `SEDGA`, `DTR`, `USRMCLK`, `GSR` --
verified from three independent sources that agree exactly:
`prjtrellis/libtrellis/src/Chip.cpp:261-268`, `nextpnr/ecp5/arch.cc:1035`, and
the Yosys ECP5 blackbox library. None of them accepts configuration data.

Ruling out the near-misses one at a time:

- **`JTAGG`** is the primitive people expect to be a backdoor. It is not. Its
  only two parameters are `ER1`/`ER2` (`nextpnr/ecp5/bitstream.cc:1517-1521`),
  which are user-defined shift registers selected when an *external* JTAG master
  shifts their instruction into the IR. Its `TCK`/`TMS`/`TDI` ports are marked
  `iopad_external_pin` -- bonded to the pads, not drivable from fabric. The
  configuration opcodes (`ISC_ENABLE`, `LSC_BITSTREAM_BURST`, ...) are decoded by
  hard logic behind the TAP IR, and fabric never sees the IR.
- **`USRMCLK`** drives the MCLK *pin*, for a soft SPI master talking to the
  external flash. It is a path to flash, not to the configuration engine.
- **`SEDGA`** reads configuration memory for CRC checking. Read-only.
- **`PCNTR`**, which on MachXO2/XO3 *does* give fabric a limited poke at the
  config engine, **does not exist on ECP5** --
  `prjtrellis/libtrellis/src/Chip.cpp:472` instantiates it only in the MachXO2
  branch. This is the most telling result: Lattice documents such a mechanism
  where it exists, and for ECP5 it does not.

**`--background` does not do what the issue assumes.** It sets three CRAM bits in
tile `EFB0_PICB0` plus CR0 bits 29/27/26/25 (`ecppack.cpp:158-165`,
`Bitstream.cpp:34,917-921`). Its effect is to keep I/O pins live during a
reconfiguration driven by an *external* master, enabling partial reconfiguration
over JTAG. Project Trellis states the mechanism explicitly: partial bitstreams
require `BACKGROUND_RECONFIG`, and then "instructions 0x79 ... and 0x74 ... must
be sent **over JTAG**". Lattice FPGA-TN-02039 section 6.2 is blunter still: "When
the ECP5 ... devices are in Background mode, **only read type commands are
supported**."

**Reconfiguration cannot even be *triggered* from fabric.** FPGA-TN-02039
section 5.5 lists exactly three exits from user mode: PROGRAMN asserted, a
REFRESH command on a configuration port, or power cycling. PROGRAMN is a
dedicated **input** (ECP5 datasheet FPGA-DS-02012) with no output driver and no
fabric routing. REFRESH is accepted "only [on] the JTAG port and the Slave SPI
port", both requiring an external master. `BOOTADDR` is CRAM fixed at pack time
(`ecppack.cpp:167-194`), not a runtime register.

So the only route from user logic is: write the image to SPI flash with a soft
SPI master, then have *something external* trigger a reboot. The running design
does not survive it. That is precisely what Apollo's existing
`FlashBridge`/`flash-fast` already does -- and it is why that code writes flash
rather than SRAM. The issue's own table rules that route out on erase/program
time.

## 2. The FPGA-side transfer really is ~6 ms -- and it does not matter

`ecp5-test/loader/bitstream_sink.py` is the receiving half of the proposed
loader: a bulk OUT endpoint on TARGET-C that counts bytes and never
back-pressures. It builds (110 MHz against a 60 MHz constraint), loads, and
enumerates at `1209:000e`. Pushing a 304726-byte bitstream at it:

    wrote 304726 bytes in 7.89 ms (38.6 MB/s, 309 Mbps)

**The issue's ~6 ms estimate was essentially correct.** This is the strongest
single piece of evidence in the investigation, and it cuts against the issue's
own conclusion: the leg that was assumed to be the expensive one is ~350x
cheaper than the path in use, and it was never the problem. Even if the ECP5
*could* configure itself from this data -- it cannot -- adopting this transport
would remove 7.89 ms from a 2746 ms operation.

The same image over the JTAG path takes 2746 ms. The difference is entirely
which chip the bytes pass through.

## 3. Why the JTAG path is actually slow

The issue attributes ~3.0 s to "JTAG clock". The code and the measurements both
disagree.

`ECP5_JTAGProgrammer.configure` sends the whole image in a single
`LSC_BITSTREAM_BURST` data scan (`ecp5.py:457`). But `JTAGChain._scan_data`
splits that scan into `max_bits_per_scan` chunks (`jtag.py:306-321`), and the
SAMD11's staging buffer is 256 bytes (`firmware/src/jtag.c:30-31`). Each chunk
costs **two USB control transfers**: `SET_OUT_BUFFER` carrying the data, then
`SCAN` telling the MCU to clock it out.

Decomposing those two transfers by varying payload size and bit count
independently:

| transfer | 0-8 B / 8 bits | 256 B / 2048 bits | marginal |
|---|---|---|---|
| `SET_OUT_BUFFER` (data in, no JTAG work) | 167 us | 846 us | **2.53 us/byte** |
| `SCAN` (no payload, pure JTAG work) | 138 us | 497 us | **1.40 us/byte** |

Fitting `SET_OUT_BUFFER`: **204 us fixed + 2.53 us/byte** (a second run gave
195 us + 2.51 us/byte; the figures below use the first).

The decisive comparison is the two marginal columns. **Getting a byte into the
SAMD11 costs nearly twice what clocking it onto the JTAG wire costs.** The wire
is not the bottleneck; the pipe feeding it is. The SAMD11 clocks JTAG over
hardware SPI at `baud_divider=1` (`boards/cynthion_d21/jtag.c:25`), which is fast
enough that it spends most of its time waiting for data.

### The ceiling nobody mentioned

The Apollo debug controller enumerates at **12 Mbps full-speed** with a 64-byte
EP0 (`/sys/bus/usb/devices/.../speed` = 12; `CFG_TUD_ENDPOINT0_SIZE 64`).

This is the single most important fact for issue #100, because the 388 Mbps
figure the issue reasons from is the throughput of the **FPGA's** USB PHY, on a
different port and a different chip. The bitstream does not travel that path. It
travels the SAMD11's full-speed link, whose raw ceiling is 1.5 MB/s.

At the measured 2.53 us/byte the control path runs at 0.40 MB/s -- **26% of the
full-speed wire**. That inefficiency is the opportunity.

| bound | time for a 304726 B image |
|---|---|
| measured today (end to end) | **2746 ms** |
| SAMD11 ingest at measured 0.40 MB/s | 771 ms |
| JTAG clocking alone at 1.40 us/byte | 427 ms |
| 100% of full-speed wire | **203 ms** -- floor for *any* SAMD11 path |
| issue #100's target | 6 ms -- **not reachable through this MCU** |

Note the end-to-end 2746 ms exceeds the 771 ms ingest figure, because the
per-transfer fixed cost (204 us x 1191 chunks) and the `SCAN` transfers add on
top.

## 4. What would actually make this fast

> **Outcome, 2026-07-31.** Items 2 and 3 were built and shipped, plus DMA-clocked
> JTAG which this list did not anticipate. Measured **713.9 -> 322.2 ms, 2.22x** on a
> fixed 122880-byte payload -- inside the 3-4x band predicted below, and the ranking
> held: chunk size and overlap were both cheap and both paid.
>
> Item 1 remains the biggest lever and is unbuilt. It is now tracked as **#107 (P0)**,
> and the estimate here is corroborated: dispatching from the USB ISR was measured at
> only 3.2% of the remaining gap, which retires software latency as the explanation
> and leaves the 64-byte control endpoint as the cause -- exactly as stated below.
>
> One correction to the framing: the 204 us fixed cost is measured at **~145 us** per
> transaction on current firmware, against ~47 us of wire time for a 64-byte packet.
> Details in `../apollo_samd11_mcu/apollo-configure-speed-investigation.md`.

Ranked by payoff per unit of risk. None requires new gateware.

1. **Move the bulk data path off control transfers (biggest win).** Control
   transfers on a full-speed device are the worst available mechanism: a 204 us
   fixed cost each, and no host-side pipelining. A bulk OUT endpoint on the
   SAMD11 carrying bitstream data, with `SCAN` retained only for state changes,
   should approach the 203 ms floor. This is an Apollo firmware change.
2. **Enlarge the staging buffer.** Going from 256 B to 1-2 KB divides the
   per-transfer fixed cost by 4-8x, worth roughly 180-210 ms on its own. Cheap,
   but bounded by SAMD11 RAM, and it does not fix the 0.40 MB/s data rate.
3. **Overlap ingest with clocking.** Double-buffering so the SAMD11 clocks chunk
   *n* while receiving chunk *n+1* would hide the smaller of the two costs --
   worth ~30% and independent of the two above.

The realistic target is **600-800 ms**, a 3-4x improvement, bounded by 203 ms.

To go below that the bitstream must not pass through the SAMD11 at all. The only
such path on this board is the FPGA's own USB PHY writing SPI flash, which is
`flash-fast` -- and that pays flash erase/program time, which the issue already
costed at ~1.6 s. **On current Cynthion hardware there is no path to the ~6 ms
figure.** Reaching it would need a high-speed debug MCU.

## 5. Tools

`scripts/fast_loader.py`:

    ./scripts/fast_loader.py --mode measure       # attribute the cost
    ./scripts/fast_loader.py --mode configure --bitstream <file.bit>
    ./scripts/fast_loader.py --mode sink-test --bitstream <file.bit>

`ecp5-test/loader/bitstream_sink.py`:

    ./ecp5-test/loader/bitstream_sink.py --build --program

All are safe. `--mode configure` and `--program` write configuration SRAM only,
via `ISC_ENABLE`/`ISC_ERASE`/`LSC_BITSTREAM_BURST`/`ISC_DISABLE`; no flash opcode
is issued on that path, so a power cycle restores whatever is in flash. `--mode
measure` shifts inert data with no configuration command enabled.

Building needs the OSS CAD Suite on PATH and the real interpreter, since that
environment sets `PYTHONHOME` and hijacks a bare `python3.15t`:

    PATH=$HOME/opt/oss-cad-suite/bin:$HOME/opt/oss-cad-suite/py3bin:$PATH \
      $HOME/opt/cpython-315t/bin/python3.15t ecp5-test/loader/bitstream_sink.py --build

Logs land in `tmp/logs/fast_loader-*.log`.

## 6. Verified vs assumed

**Verified by measurement on hardware:** the 2746 ms baseline; every timing in
the tables in section 3; full-speed enumeration and the 64-byte EP0; the
0.40 MB/s control data rate and the ~200 us fixed cost, from a linear fit over
seven payload sizes; the 7.89 ms / 309 Mbps bulk figure in section 2, from a
design that was built, loaded and enumerated. The two fits were reproduced
across separate runs and agreed to within 5%.

**Verified from primary sources:** the ECP5 primitive list (three independent
codebases); what `--background` sets (Trellis source plus the bit database);
`JTAGG` exposing only ER1/ER2; PROGRAMN being a dedicated input (two Lattice
documents); REFRESH being external-only; `BOOTADDR` being pack-time CRAM; the
256-byte SAMD11 buffer and the two-transfer-per-chunk structure (Apollo source).

**Assumed, and flagged as such:**

- The projected 600-800 ms for the section 4 changes is **extrapolation from the
  measured per-byte and per-transfer costs, not a measurement.** No firmware
  change was built or tested. The 203 ms floor is arithmetic from the
  full-speed wire rate and is a bound rather than an achievable figure.
- The 7.89 ms sink figure is the **host-side** write duration. `libusb` returns
  once the kernel has accepted the buffer, so this may understate the time until
  the last byte is on the wire. The design counts bytes and clocks internally
  (JTAG registers 2/3/4) but those were not read back, because doing so
  contends with the running USB device for the JTAG chain. The figure is
  therefore a good estimate of the transport rate rather than a device-confirmed
  one -- though at ~350x margin over the JTAG path, the conclusion does not
  depend on the precision.
- That no ICAP exists on ECP5 rests partly on absence of evidence across
  prjtrellis, nextpnr and Yosys. The inference is strong -- `PCNTR` shows Lattice
  documents such mechanisms where they exist -- but it is formally an argument
  from silence.
- Lattice documents CR0 bits 27/26/25 as "Reserved"; that they are the
  background-mode enables is inferred from Trellis fuzzing, not documented.
- Timings were taken on one board, on one host, on a hub chain
  (`/sys/bus/usb/devices/1-1.3.3.4`). Host controller and hub depth affect
  control-transfer latency, so absolute numbers may shift; the *ratios* between
  the decomposed costs are the durable result.
