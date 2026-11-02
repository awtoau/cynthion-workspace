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

## Repeat collapse before the codec: NAK storms and SOFs (Dan, 2026-11-02)
- **Problem:** a high-speed IN/NAK storm is ~2 M transactions/s, ~25 MB/s of records (tap analysis; wire-gap values are estimates). It would flood HyperRAM and the compressor to record the same transaction millions of times.
- **Design:** a *run collapser* in the framer, in front of the tap and the codec: a transaction identical to the previous one is **counted, not recorded**. Key = (token PID, address, endpoint, response PID, no data). On a run break, a count limit, or a flush tick, one `REPEAT` record is emitted: key, **count**, first and last timestamp. The same stage collapses SOFs as an incrementing run (start frame number + count).
- **The RISC-V still gets exact numbers:** the tap counters (NAK, SOF, per-PID, per-endpoint) increment on **every** packet, collapsed or not. The RV reads "NAKs so far" from a counter, not by decompressing a log.
- **What is lost:** the exact spacing between repeats; only count, first/last timestamp (so average interval) survive. Switchable like the codec (`collapse` CSR bit); off = every transaction recorded. **Optional, low priority (Dan): the counters already give the RISC-V the storm size, and the codec absorbs the repeats; the collapser only keeps the ring/log readable.**
- **Flush tick:** at most every 1 ms. The framer's 16-bit timestamp wraps every 1.09 ms, so a run longer than that could not be timed; 1 ms keeps each record representable and bounds how stale the RV's view can be. On expiry the open run is emitted and a new one starts.
- **Order:** `framer -> COUNTERS (every packet) -> run collapser (switchable) -> packet ring + event log (the tap) -> LZ4 (switchable) -> blocks`. The counters sit *before* the collapser so a collapsed NAK is still counted exactly. The packet ring and event log sit *after* it, so a storm shows as one `REPEAT` entry ("NAK x N since t0") instead of 32 identical NAK headers: otherwise the ring is overwritten every ~16 us and the RISC-V could never see the last real packet. Collapse removes the storm; LZ4 handles real payload. Stages are independent: a collapsed stream still compresses, and an uncollapsed one still works.
- **What the RISC-V sees in a storm:** no per-packet work at all. Counters by register read (snapshot-consistent); the ring/log hold collapsed entries; real packets keep appearing in the ring between runs. Interrupts come from the **event log only** (status events: tens per second, a reset/chirp burst of ~10-100 in a few ms), as a level interrupt while the log is non-empty with the ISR draining it in a loop, so a burst is one entry into the handler, not one per event. NAKs never raise an interrupt.
- **Topology A (RISC-V is the host):** the RISC-V issues the transactions itself, so it sees each NAK to its own IN in software at its own pace; there is no flood. The framer/counters/collapser still run on that source for the stored record.
- **Not in GSG's upstream:** the stock analyzer streams every packet raw (see [`upstream-watch.md`](upstream-watch.md)); this exists only in our fork.
- Verification: simulate an IN/NAK storm and a SOF run: counters equal the uncollapsed packet totals exactly; the expanded `REPEAT` records reproduce the same sequence of (key, count); the flush tick fires inside 1 ms.

## Codec: LZ4 block format, pluggable (Dan, 2026-11-02: "use it, don't analyse")
- **LZ4 block format** (token = 4-bit literal length | 4-bit match length, literals, 16-bit little-endian offset, 255-chain length extensions; min match 4; last 5 bytes literal), with a **history window of 256-1024 B**, a build-time parameter (default 1024 to match the 1 KiB block: offsets never exceed the block). Because it is the standard block format, any software LZ4 decoder reads our blocks and our decompressor reads any LZ4 encoder's output within the window, which gives free test vectors and a PC-side decode path.
- **Pluggable:** a fixed streaming codec interface (bytes in with `last`, bytes out with `last`, a block-size limit) and a registry keyed by a codec id stored in each block's index entry (`0` raw/stored, `1` lz4; ids reserved for later codecs, e.g. a packet-aware RLE). `comp_mode` CSR selects the writer's codec; the reader dispatches on the block's id, so mode changes at a block boundary and old blocks stay readable.
- **No-expansion rule stays:** a block that does not shrink is stored raw (id 0).
- Memory (estimates, to be measured by synthesis of the module): compressor = history window + hash table (1 DP16KD each); decompressor = window (1 DP16KD); the ~7 spare blocks also have to hold the tap. If the budget bites, the window shrinks (256 B) before anything else changes.
- Throughput target: one input byte per clock sustained on typical traffic (60 MB/s at 60 MHz); the module reports its measured worst-case bytes/clock in simulation. Cells only are reported; no timing figures.

## Verification
- The `lz4` block format is the spec; a small Python reference (and the `lz4` library when installed) is the oracle. Gateware compressor and decompressor are checked bit-exact against it in simulation: round trip on real traces, zero-length packets, all-zero/all-ones/alternating, random, max-length, block-boundary cases, mode switching mid-stream, overflow/drop markers.
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
   - UNVERIFIED: the SoC's current BRAM count (49 of 56 was the 2026-08-12 figure; the 2026-11-02 `ck80-dqs0-sync60` build places 34 of 56, `top.tim`), the exact chirp event count.

## Open
- Confirm the tap by simulation (item 5).
- Simultaneous capture from both sources.
