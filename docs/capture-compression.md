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

## Decisions (Dan, 2026-11-02)
1. **Block size: 1 KiB raw.** Index entry per 1 KiB block.
2. **Full buffer: stall the source or drop, configurable.** A CSR policy (`stall` | `drop`); `drop` writes an overflow marker and counts dropped bytes/packets.
3. **Source: both** the passive `target_phy` tap and the host engine's RX FIFO, selectable by CSR. (Capturing both at once would need two compressors: not decided.)
4. **PC path: out of scope for now**; whatever works first.
5. **Pre-compression tap: counters + status + event log + packet-header ring (design B), about 1 KiB.** Analysis (2026-11-02, from the analyzer and GUH sources):
   - **Dan's premise is true on the wire and false at every tap point in the repo.** ULPI sends status as RXCMD bytes inside the receive stream (LineState bits 1:0, VbusState 3:2, RxEvent 5:4, id_digital 6; the USB3343 has no interrupt pin, so events are in band). But `UTMITranslator` consumes RXCMD before the analyzer or GUH see anything: the framer gets `rx_data` / `rx_valid` / `rx_active` only, and status is latched into level signals. So the tap goes on the **framed stream**, never on raw ULPI.
   - **Upstream records** (reuse them): packet = 16-bit length + 16-bit timestamp + bytes (4-byte header); event = `0xFF`, code, ts16 (4 bytes, codes 1..30: start/stop, speed, line state, VBUS, attach, reset, chirp, suspend/resume). The 16-bit timestamp wraps every 1.09 ms; a `NONE` event is written on wrap.
   - **Lost before framing today:** `rx_error` (LED only), `host_disconnect`, `id_digital`, `session_end`; speed and direction are not in the packet record.
   - **Tap content:** (a) counters (packets, bytes, per-PID, SOF, NAK, RxError, drops, overflows); (b) a live status CSR snapshot (raw line state, VbusState, host_disconnect, speed, rx_active, last RXCMD); (c) a **64-event log**, 4 B each (the upstream event record); (d) a **32-packet header ring**, 16 B each (pid, len, ts16, error flag, source/direction, first 8 bytes). Counters are read with a sequence counter for a consistent snapshot; the event log drains by interrupt.
   - **No byte ring.** At 60 MB/s a 2 KiB block is 34 us of history: it cannot serve a polling RISC-V (a 1 ms poll needs ~60 KiB). A byte ring only makes sense as a trigger-frozen snapshot.
   - **Why it is small:** a HS IN/NAK storm is ~25 MB/s of records; no tap can hold history, only "latest" plus loss-free counters. Status events are low-rate (tens per second; a reset/chirp burst is ~10-100 events in a few ms), so a 64-event log never overruns.
   - **Compressor input is the framed records**, not bytes. Status events stay inline as the same 4-byte event records so ordering is exact. Block header carries first timestamp, speed and a counter snapshot. Under the `drop` policy packets are dropped and **events are kept**.
   - **Framer gaps to fill:** an RxError flag on the packet record, a direction/source tag, speed in the block header, a wider timestamp (or keep the `NONE` wrap records).
   - **Two sources, two framers feeding one compressor interface:** the passive `target_phy` needs a `UTMITranslator` placed behind it (it has none today: register path only) and infers direction from PID context; the host engine knows its own TX, so its framer synthesises records (token from TX, response from RX) from an 8-bit RX FIFO that has no packet boundaries.
   - **Confirming test:** simulate a worst-case IN/NAK storm plus a reset/chirp burst through translator, framer and tap: the event log never overruns and counters stay consistent with packet totals. Then measure real RxError and mid-packet RXCMD rates on `target_phy` (UNSURE if non-zero).
   - UNVERIFIED: the SoC's current BRAM count (49 of 56 was the 2026-08-12 figure; run `./dev.py metrics report`), the exact chirp event count.

## Open
- Confirm the tap by simulation (item 5).
- Simultaneous capture from both sources.
