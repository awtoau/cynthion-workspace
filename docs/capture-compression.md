# Capture-path compression in the FPGA: design

**Status:** stage 1 built and simulated: codec modules only (`gateware/soc/capture/`), standalone, NOT in `top.py`, HyperRAM or the shell; nothing run on hardware (U). Tap, block packer, index, CSRs: not built. See [Implementation: stage 1](#implementation-stage-1). Requirement (Dan, 2026-11-02): compression is essential; it must be **switchable**; the RISC-V must be able to **sample data before it is compressed**; data is **stored compressed in HyperRAM**; the RISC-V reads **uncompressed** data through a **gateware decompressor** (no software decompression).
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
   - UNVERIFIED: the SoC's current BRAM count (49 of 56 was the 2026-08-12 figure; run `./dev.py metrics report`), the exact chirp event count.

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

## Open
- Confirm the tap by simulation (item 5).
- Simultaneous capture from both sources.
