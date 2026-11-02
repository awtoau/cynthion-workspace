# Capture-path compression in the FPGA: design

**Status:** requirement and design only, nothing built. Requirement (Dan, 2026-11-02): compression is essential; it must be **switchable**; the RISC-V must be able to **sample data before it is compressed**; data is **stored compressed in HyperRAM**; the RISC-V reads **uncompressed** data through a **gateware decompressor** (no software decompression).
**Index:** [`aoa-adaptor.md`](aoa-adaptor.md) · [`upstream-watch.md`](upstream-watch.md) (upstream has no compression anywhere on the capture path) · [`chips/hyperram/w956a8.md`](chips/hyperram/w956a8.md)

## Why
- Today's non-DQS HyperRAM path writes at ~5-7 MB/s through the RISC-V window; raw gateware ceilings are unmeasured/void. A saturated high-speed bus is ~40-50 MB/s of captured bytes. Compression is what raises the effective capture rate and buffer depth.
- Upstream analyzer: raw 16 KiB reads, no compression ([`upstream-watch.md`](upstream-watch.md)); it stops when its buffer fills.

## Path
```
 PHY (ULPI RX bytes + RXCMD)
   -> packet framer (len, timestamp, speed/event tags)
   -> TAP --------------------------> RV sample window (before compression)
   -> COMPRESSOR (CSR mode: off | rle | lz) -> block packer
   -> HyperRAM block writer (gateware, bursts)           HyperRAM
                                                           |
   RV / PC <- uncompressed bytes <- DECOMPRESSOR (gateware) <- block reader
```
- **TAP:** a small ring in front of the compressor: the latest N packets (header + first bytes) and counters. 1 DP16KD (2 KiB) is the likely cost; the BRAM budget says ~7 spare ([`chips/ecp5/bram-budget.md`](chips/ecp5/bram-budget.md)), to be re-measured.
- **Switchable:** `comp_mode` CSR (off = framed raw passthrough). Every stored block carries a flag (raw/rle/lz), so the reader never needs to know the mode that wrote it, and mode can change at a block boundary.
- **Blocks, not a stream:** fixed raw block size (1-4 KiB, to decide), each independently decodable. An index entry per block (HyperRAM offset, stored length, flags, first timestamp) gives the RISC-V random access by block number and the decompressor a simple job.
- **No-expansion guarantee:** if a block would not shrink it is stored raw. Worst-case output is input plus a small header, so the writer's worst-case rate is the raw rate (compression improves the average only; backpressure/drop policy still needed: drop with an overflow marker and a counter).
- **Decompressor in gateware:** `block number -> uncompressed bytes` through a read-only CSR window/FIFO to the RISC-V, and (later) as the stream to the PC. The RISC-V never runs a codec.

## Algorithm: what to pick, and how to decide
| option | what it removes | cost | verdict |
|---|---|---|---|
| packet-aware: elide repeated SOF/keepalive, RLE idle/repeated bytes, repeated-packet dedupe (hash of previous), delta timestamps | the dominant redundancy in USB traces (polling NAK storms, SOF, idle, repeated tokens) | LUTs only, 1 B/clk, no BRAM | **stage 1** |
| byte LZ (LZ4-style, 256-1024 B window) | repeated structured payloads (DUML frames, descriptors) | window RAM (BRAM or LUTRAM), match logic | stage 2 only if stage 1's ratio is poor and BRAM allows |
| deflate / zstd | most | far too large | no |
- **Decide with data, not by argument:** an offline harness runs the candidate codecs on real captures (awto-dji `captures/` AOA streams, bench-app AOAT streams, Packetry exports) and synthetic worst cases, and reports ratio per codec. Hardware only after that.
- Note: address-resolvable test data (AOAT `addr` pattern) compresses poorly by design; repeated DUML frames compress well. Report both.

## Verification
- Python reference codec = the spec. Gateware compressor and decompressor are checked bit-exact against it in simulation: round trip on real traces, zero-length packets, all-zero/all-ones/alternating, random, max-length, block-boundary cases, mode switching mid-stream, overflow/drop markers.
- Same sim harness style as `scripts/soc/` (registered in `soc_sims.py`).

## Open questions
1. Block size (1, 2 or 4 KiB raw)?
2. When HyperRAM cannot keep up: drop-with-marker (analyzer behaviour) or stall the source?
3. Source PHY: passive `target_phy` tap (topology C) and/or the host engine's RX FIFO (topology A)?
4. PC path: stream decompressed bytes, or export pcap from the decompressor?
5. How many packets must the pre-compression tap hold for the RISC-V?
