# Capture-path compression in the FPGA: design

**Status:** stage 1 (codec) and stage 2 (per-transaction framer, ring, record-aligned blocks, see [stage 2](#implementation-stage-2-framer-ring-block-feeder)) built and simulated: modules only (`gateware/soc/capture/`), standalone, NOT in `top.py`, HyperRAM or the shell; nothing run on hardware (U). Tap, block packer, index, CSRs: not built. See [Implementation: stage 1](#implementation-stage-1). Requirement (Dan, 2026-11-02): compression is essential; it must be **switchable**; the RISC-V must be able to **sample data before it is compressed**; data is **stored compressed in HyperRAM**; the RISC-V reads **uncompressed** data through a **gateware decompressor** (no software decompression).
**Index:** [`aoa-adaptor.md`](aoa-adaptor.md) · [`upstream-watch.md`](upstream-watch.md) (upstream has no compression anywhere on the capture path) · [`chips/hyperram/soc-peripheral.md`](chips/hyperram/soc-peripheral.md)

## Why
- HyperRAM: register interface on the #581 core at CK 180, see [docs/chips/hyperram/soc-peripheral.md](chips/hyperram/soc-peripheral.md) (#588). A saturated high-speed bus is ~40-50 MB/s of captured bytes. Compression is what raises the effective capture rate and buffer depth.
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

## Implementation: stage 1
Tags: V = verified in simulation, I = inferred, U = unverified (nothing is on hardware).

- Code: [`gateware/soc/capture/`](../gateware/soc/capture/) (`codec.py`, `lz4_compress.py`, `lz4_decompress.py`); oracle [`scripts/soc/capture_codec_ref.py`](../scripts/soc/capture_codec_ref.py); sim [`scripts/soc/capture_codec_sim.py`](../scripts/soc/capture_codec_sim.py) (in `soc_sims.py`); cells [`scripts/soc/capture_codec_area.py`](../scripts/soc/capture_codec_area.py).
- Not built: integration (`top.py`, HyperRAM block writer/reader, index, CSRs, tap). Compressor and decompressor are standalone modules.

### Interface (`codec.py`)
- Streams: `data[8]`, `valid`, `ready`, `last` (`last` = final byte of the block).
- `CompressorSignature`: `sink` raw, `source` coded, `block_limit` in (1..1024, default 1024; reaching it ends the block), `codec_id` / `comp_len` / `raw_len` out, valid on the `source.last` beat.
- `DecompressorSignature`: `sink` coded, `source` raw (`last` on the final raw byte), `codec_id` / `raw_len` in (from the index entry, held for the block), `error` out (valid when `done` pulses; held to the next block's first beat), `done` out (1-cycle strobe).
- `CODECS[id] = Codec(name, compressor, decompressor)`: 0 stored, 1 lz4, 2..15 reserved. `register_codec` adds one; importing `capture` registers all.
- `SwitchableCompressor` (`mode` in, sampled at a block's first byte) and `SwitchableDecompressor` (dispatch on `codec_id`). A block of a different codec waits until the previous block has fully left, so output order is preserved. V
- `StoredCompressor` / `StoredDecompressor`: pass-through, same interface; `comp_len == raw_len`. V

### LZ4 compressor (`lz4_compress.py`)
- Engine runs on a 16-byte lookahead window, one position per clock, three stages: S0 hash + table read, S1 candidate check + history read + table write, S2 byte compare + commit.
- Hash: 4 bytes -> 8-bit index (256 entries) + 6-bit check; entry = 10-bit position + check. Candidates are not pre-verified; a speculative match under 4 bytes is dropped (those positions stay literals). Stale entries from earlier blocks are harmless (compare decides).
- Match extension: the history address (`pos - offset`) is predictable, so extension reads 1 byte/clk with no stall; the table lookup pipeline keeps running through a match, so back-to-back matches have no bubble. V
- Tail rules: match starts only with >= 12 bytes left, extends only with >= 6 left (ends >= 5 before the end); the window gives the remaining count without knowing the block length. V (round trip with the `lz4` library as decoder)
- Literals are re-read from `raw_b` at emit time; sequence records (literal count, match length, offset) sit in a 512 x 32 RAM, 256 per bank.
- Coded size is known before the first coded byte; `coded >= raw` -> stored (id 0, bytes replayed from `raw_b`). Output is never larger than the input. V
- Two raw copies (`raw_a` history, `raw_b` emit), 2 x 1 KiB banks each, so ingest of block k+1, compression and emission of block k overlap and no RAM port is shared.
- `window` (<= 1024) limits the offset only; RAM does not shrink with it.

### LZ4 decompressor (`lz4_decompress.py`)
- WINDOW-byte circular RAM; literals pass through at 1 B/clk; match copy 1 B/clk after a 1-clock prime; offset-1 runs forward the previous byte. Newest byte is held back one stage so `last` lands on the final raw byte.
- Corrupt block (offset 0 / beyond output / beyond WINDOW, literal or match length past `raw_len`, `last` mid-sequence, output != `raw_len`): `error` set, no further byte emitted, rest of block drained to `last`, then `done`.
- Bound (`source.ready` high): ends within `comp_len + raw_len + 2 * sequences + 3` clocks of the first beat; output <= `raw_len`. V (worst measured 0.58 of the bound over the corrupt set)

### Cells (yosys `synth_ecp5`, module alone; no timing)
| config | LUT4 | CCU2C | FF | DP16KD | LUTRAM |
|---|---|---|---|---|---|
| compress, window 1024 | 712 | 196 | 621 | 4 | 0 |
| decompress, window 1024 | 512 | 118 | 124 | 1 | 0 |
| both, window 1024 | 1070 | 311 | 745 | 5 | 0 |
| both, window 256 | 1051 | 309 | 741 | 5 | 0 |

- 5 DP16KD for both exceeds the ~3 estimated earlier. Breakdown: `raw_a` 1, `raw_b` 1, hash table 1, sequence RAM 1, decompressor window 1.
- Window 256 saves nothing in BRAM (compressor RAMs hold whole blocks; the 256 B decoder window still maps to one DP16KD) and ~20 LUT4.
- Levers if the budget bites (I, not tried): hash table (256 x 16) into LUTRAM; drop the `raw_a`/`raw_b` mirror by time-sharing a port (costs throughput); 512 B blocks halve both raw RAMs only if banks are also dropped.

### Throughput (simulation, output always ready, input valid every clock)
- Compressor: 1024 B block = 1036 clk (0.988 B/clk) for zero, ones, alternating, 12 B frames, 74 B pattern, incompressible, far repeats. Blocks of 100-300 B: 0.89-0.96 B/clk. V
- Per-block overhead: 12 clk (ingest pauses while the 16-byte window drains the tail; the next block's first 12 bytes fill the window before the engine starts). Independent of content.
- Emission overhead is about 4 clk per sequence (record load, token, offset bytes); the corpora never make emission the limiter. U for fragmented traffic whose coded size approaches the input.
- Decompressor: 0.89-0.99 B/clk of raw output (about 4 clk overhead per sequence). V

### Verification run (`capture_codec_sim.py`)
- (a) compress -> Python and `lz4` library decode == input: 19-block corpus + 2000 fuzz blocks (seeded, random stalls both sides), window 256 offsets <= 256. V
- (b) Python / `lz4` library (default and HC) encode -> gateware decompress == input, window 1024 and 256, plus generated valid blocks with overlap, runs and length extensions. V
- (c) stored iff the lz4 size is not smaller (checked against the compressor's own size signal); incompressible 1 KiB stored, zero-fill 1 KiB lz4. V
- (d) mode switching at block boundaries, both directions, mixed stream decoded. V
- (e) 14 corrupt-block cases (+ a good block after each) and the stored decoder's `raw_len` bound. V
- The AOAT-frame corpus entries are skipped: no `awto-dji` checkout is reachable from here (U).

### Run
- `python3 scripts/soc/capture_codec_sim.py [--seed N] [--fuzz N]` (default 2000; log `tmp/logs/dev.log`).
- `python3 scripts/soc_sims.py capture_codec`
- `python3 scripts/soc/capture_codec_area.py`
- Needs `lz4` for the library checks (`python3 -m pip install lz4`); without it those checks are skipped.

### Unverified
- U: everything on hardware. Block RAM read-during-write behaviour on the ECP5 for the two same-address cases (table read vs write, history read vs ingest write): the design never relies on the collision result (table write is one stage after the read; ingest and history addresses differ), but the mapped primitives were not run.
- U: ratio on real USB captures (only synthetic corpora here).
- U: a corrupt block whose `last` never arrives hangs by construction (the decoder is input-driven); the bound is from the first beat to `done` given the block's `last`.

## Implementation: stage 2 (framer, ring, block feeder)
Tags as above. Code: [`framer.py`](../gateware/soc/capture/framer.py) (`CaptureRing`), [`path.py`](../gateware/soc/capture/path.py) (`CapturePath` = ring + `SwitchableCompressor`); sim [`capture_framer_sim.py`](../scripts/soc/capture_framer_sim.py) (in `soc_sims.py`, about 36 s). **Not wired into `top.py`** (see [Integration plan](#integration-plan-not-done)).

### Record: one per transaction (Dan, 2026-11-02, #557)
- Byte stream, little endian, 5-byte header then payload:

| bytes | field | content |
|---|---|---|
| 0,1 | `ts16` | counter value when the transaction's first packet byte arrived |
| 2 | flags | `[1:0]` kind (0 TXN, 1 EVENT, 2 DROP, 3 WRAP); `2` err; `3` timeout; `4` trunc; `5` has_tok; `6` has_data; `7` has_hs |
| 3,4 | `len` | payload bytes |
| 5.. | payload | TXN: token (3 B) + data packet + handshake (1 B), those present; EVENT: code; DROP: records dropped (saturating 255); WRAP: none |

- **Transaction** = token (+ data) (+ handshake). SOF is a record of its own (closed at its last byte). Closes on: handshake, next token, a second data/handshake, or `txn_idle` clocks of silence (default 512). Closed without a handshake and not SOF sets `timeout` (also isochronous OUT: no handshake by design).
- `err`: any packet's RxError, or a token-class packet whose length is not 3. `trunc`: payload past 1019 B cut (a record always fits one 1 KiB block).
- Timestamps are per transaction, never per byte or per packet. Time of an event/DROP/WRAP record is when it was raised/written.
- Tap is the framed packet stream (`pkt_data/valid/last/err` + `ev_valid/ev_code`), per the item-5 finding that status never reaches the raw ULPI stream.

### ts16 rollover rule
- Counter: 16 bits, one count per `1 << ts_shift` clocks (default 1 clock = 1.09 ms period at 60 MHz).
- Decoder: `abs += (ts - prev_ts) & 0xFFFF`. Valid iff consecutive records are < 0x10000 counts apart.
- Guarantee: a WRAP record is written whenever 0x8000 counts pass with no record, so consecutive records are at most 0x8000 + latency (a transaction time, a few hundred counts) apart. WRAP is also written at first idle moment, never inside a transaction.
- Order: records are in emission order and `ts16` is non-decreasing modulo 2^16. Events are written before a queued packet only if older than it (4-deep event queue).
- Loss of guarantee: ring full for 0xFFFF counts. The next DROP record carries `trunc` (flag bit 4) = "time may have wrapped unseen".
- Sim: `ts_init` starts near 0xFFFF; 132 k clocks of silence decode to the true distance (4 WRAP records).

### Path and policies
```
packets -> input FIFO (16) -> framer (counters see every packet first) -> packet ring (2 KiB, 1 DP16KD)
        -> record-length queue (16) -> block feeder -> SwitchableCompressor -> coded blocks
```
- **Counters** (`n_pkt n_sof n_nak n_err n_dropped n_lost`, 16-bit wrapping) increment at the input, before the FIFO, so dropped records are still counted. V
- **Record-aligned blocks:** the feeder ends a block only on a record boundary when the next record would exceed 1024 B, on `flush`, or after `flush_idle` clocks with nothing queued (default 1024). The last byte of each record is held until the next record's length is known. Each block parses alone; first record `ts16` is `blk_ts` (strobe `blk_ts_valid`, in block order, for the index entry). V
- **Ring full policy = drop** (stall not built): a transaction that cannot get header space or runs out of space is dropped whole (ring rolled back), counted once in `n_dropped` and `DROP`; the oldest data stays. Events dropped the same way when the 4-deep queue is full. `stall` policy: U, not implemented.
- **Input FIFO overflow** (back-to-back packets with no gap, not seen with USB gaps): FIFO cleared, open transaction aborted, input discards to the next packet end, `n_lost` incremented. V
- **Record write latency:** header backfill is 5 clocks per record; packets arriving meanwhile wait in the 16-deep FIFO (HS gaps ≥ 11 clocks).
- Collapser (NAK/SOF repeat collapse, above) is not built; transaction records already cut a NAK storm to one 10-byte record per NAK.

### Verification run (`capture_framer_sim.py`)
- Traffic: SOF, IN/NAK, IN/DATA/ACK, OUT/DATA/ACK|NAK, SETUP, no-response IN, isochronous OUT, STALL, NYET, RxError, 0..512 B data, 1-24 clock packet gaps, FS byte rate; PIDs/CRC5/CRC16 from the `usb_host` sims. Data content mixes random, zeros, pattern, text.
- V: exact records (ts16, flags, bytes) for 140 mixed transactions; 512 B transaction = one record; counters exact; FS rate; rollover (start at 0xFF00, plus silence over 2 periods); ring full (1 KiB ring, output stalled: 98 of 115 records dropped whole, DROP + kept = fed, `n_dropped` equal); FIFO overflow then clean traffic exact; full path (LZ4 + stored stretch, random output stalls): coded blocks decode (python, `lz4` library, gateware decompressor) to the ring bytes, every record present or counted.
- U: real USB3343 traffic, `rx_error` mid-packet behaviour, bus-level wiring from the SIE (the sim feeds packets, not ULPI/SIE signals).

### Cells (yosys `synth_ecp5`, module alone; no timing)
| config | LUT4 | CCU2C | FF | DP16KD | LUTRAM |
|---|---|---|---|---|---|
| framer + ring (`CaptureRing`, 2 KiB ring) | 533 | 185 | 407 | 1 | 16 |
| path (ring + compressor) | 1236 | 386 | 1058 | 5 | 16 |
| + decompressor | +512 | +118 | +124 | +1 | 0 |

- Path + decompressor ≈ 1750 LUT4 + 504 CCU2C, 6 DP16KD.

### Integration plan (not done)
- **Why not wired:** the design is at about 84% COMB; the path with the decompressor is about 2.7 k logic cells (LUT4 + 2 x CCU2C), which does not fit in the remaining margin without trimming elsewhere (I). Not built into `top.py`, so the build is untouched.
- **Cheapest first step:** `CaptureRing` + `StoredCompressor` only (no LZ4): ~0.7 k + 1 DP16KD, records still per-transaction, blocks still record aligned; LZ4 added when cells allow.
- **Taps:** host role: SIE `rxs` payload/valid/last/error for RX packets plus the engine's own TX packets (the engine knows token/data it sends); `raw` role: RX FIFO entries, tag 0 data, tag 1 RXCMD, packet end = RxActive low. Passive `target_phy`: needs the `UTMITranslator` first. Events: line/VBUS/reset status changes as `ev_valid/ev_code` (upstream codes 1..30).
- **CSR block `capture` (proposed, 8-bit CSR bus like `usb_host`):**

| reg | access | content |
|---|---|---|
| CTRL | rw | `enable`, `mode[3:0]` codec id (0 stored, 1 lz4), `flush` (write-1 pulse), `clear_counters` |
| STATUS | r | `ring_used[10:0]`, block FIFO level, sticky `dropped`, `lost` |
| COUNTERS | r | `n_pkt n_sof n_nak n_err n_dropped n_lost`, 6 x 16 bit, read-latch strobe for a consistent snapshot |
| NOW | r | `now[15:0]` |
| BLK | r | last block: `blk_ts`, `raw_len`, `comp_len`, `codec_id` (feeds the index entry) |

- **Sinks:** coded `source` stream into the HyperRAM block writer (block size 1 KiB, index entry = offset, `comp_len`, `raw_len`, `codec_id`, `blk_ts`); RISC-V reads blocks through `SwitchableDecompressor` and parses the records in software (no codec in software). Pair `blk_ts` with the compressor's output block through a 4-entry FIFO (compressor output trails its input by up to 2 blocks).
- **Sizes:** ring 2048 B (1 DP16KD; the ring-full test shows a 2 KiB ring drops under a stalled writer, so size to HyperRAM write latency), `txn_idle` 512, `flush_idle` 1024 (clocks at the capture clock), `ts_shift` 0.
- Gate before wiring: `scripts/soc/capture_codec_area.py` cells vs the current `top` margin; the framer sim in `scripts/soc_sims.py`.

## Open
- Wire the path into the SoC (integration plan above); `stall` policy; collapser.
- Confirm the tap by simulation (item 5).
- Simultaneous capture from both sources.
