# ULPI bridge: 60 MHz byte stream to/from a 120 MHz word stream

Issue: [#581](https://github.com/awtoau/cynthion-workspace/issues/581) (HyperRAM baseline); data path [#580](https://github.com/awtoau/cynthion-workspace/issues/580) (parked).

**Rule (Dan, 2026-11-03):** baseline work, clean-room. Designed from the public sources below. From this repo: board facts only (60 MHz oscillator on A8, ULPI `clk_dir='o'`) and the interface of the HyperRAM baseline (`gateware/hyperram_baseline/` at `4fbd575`: 32-bit `AsyncFIFO` streams). No repo FIFO, CDC helper or USB code reused.

## Recommendation

- **Option (a): Gray-pointer async FIFO**, width change on the 60 MHz side, 512 x 35-bit words (2 KiB) per direction, one EBR each.
- (b) works (built, simulated, safe by construction when its clocking is in place) but costs more cells, saves under one 120 MHz cycle of latency, and needs `usb` moved onto a PLL output plus a runtime phase check. Dan's question: a clean 2:1 does allow a crossing without synchronisers; on this design it is not simpler.
- Decision open: [#586](https://github.com/awtoau/cynthion-workspace/issues/586) (`needs-dan`).

## Sources

| source | revision | used for |
|---|---|---|
| ULPI Specification, `~/vendors/ulpi/ULPI_v1_1.pdf`, https://cdn.sparkfun.com/assets/4/3/e/3/2/ULPI_v1_1.pdf | Rev 1.1, 2004-10-20 | 60 MHz, 8-bit data; only the PHY throttles (`nxt`, 3.1, 3.2); link supplies the next TX byte the cycle after `nxt` (3.8.2.2); RX (3.8.2.4) has no link-side wait |
| C. Cummings, "Simulation and Synthesis Techniques for Asynchronous FIFO Design", SNUG 2002, `~/vendors/sunburst-design/CummingsSNUG2002SJ_FIFO1.pdf` | Rev 1.2 | Gray n+1-bit pointers, 2-FF pointer synchronisers, pessimistic full/empty; holding register + ready/ack handshake (2.3), the pattern (b)'s mailboxes follow without the synchronisers |
| Amaranth `amaranth/lib/fifo.py` (`AsyncFIFO`, `SyncFIFOBuffered`), `amaranth/lib/cdc.py`, https://github.com/amaranth-lang/amaranth/blob/v0.5.10/amaranth/lib/fifo.py | v0.5.10 | (a) crossing used as a library; write-domain reset empties the FIFO, read-domain reset does not |
| Lattice FPGA-TN-02200, ECP5 sysCLOCK PLL/DLL, `~/vendors/lattice/FPGA-TN-02200-ECP5-sysCLOCK-PLL-DLL.pdf`, https://www.latticesemi.com/api/document/download?id=50465 | 1.4, Sep 2025 | 7.3 injection delay removal; 18.1.3 output dividers from one VCO; 18.1.4 static phase, feedback output unshifted; 18.2.1 primary-routing input delay uncompensated; 18.3.3 RST resets the output dividers; 18.3.9 PHASESTEP; 18.3.12 LOCK, ~16 ms, unsticky |
| Lattice FPGA-TN-02204, ECP5 Memory, `~/vendors/lattice/FPGA-TN-02204-1-6-ECP5-Memory-Usage-Guide.pdf` | 1.6, Nov 2023 | Table 4.8: one EBR = 512 x 36 pseudo-dual-port, separate read/write clocks |
| Winbond W956A8MBYA datasheet, `~/vendors/winbond/W956x8MBYA_A01-006.pdf` | A01-006, 2022-07-29 | tCSM 4 us (Table 24, Table 12 / CR1 01b); tCSHI 6 ns, tRWR/tACC 36 ns at 166 MHz (Table 21); fixed latency hides refresh (9.4.3); self-refresh between transactions (9.5.4) |
| GSG `cynthion_r1_4.py`, https://github.com/greatscottgadgets/cynthion/blob/f01a190e88a88bdf8b862601e73e3effe93885f0/cynthion/python/src/gateware/platform/cynthion_r1_4.py | f01a190e | board fact: control/aux/target PHY ULPI clocks `clk_dir='o'` (FPGA drives them) |
| HyperRAM baseline, `docs/chips/hyperram/sequential-baseline.md` @ `4fbd575` | 4fbd575 | CK 165, max burst 608 words, 660 CK per burst, 311-314 MB/s; 32-bit FIFO interface |

## Options

Cells: `./scripts/ulpi_bridge_area.py`, yosys 0.65+57 `synth_ecp5`, module alone, no P&R (2026-11-03, gateware `55439a9`). Latency: `./scripts/ulpi_bridge_sim.py` `*/latency`, empty bridge, one word, input edge to output valid, in 120 MHz cycles (8.34 ns).

| option | LUT4 | CCU2C | FF | EBR | latency RX / TX | risk |
|---|---|---|---|---|---|---|
| (a) AsyncFIFO each way | 212 | 20 | 261 | 2 | 5 / 5-6 | lowest: any clock relation, survives relock, standard structure |
| (b) 2:1 mailboxes + SyncFIFO + PhaseCheck | 264 | 43 | 412 | 2 | 5 / 4-5 | needs one-PLL clocking, static phase, checker; silent corruption if the relation breaks and the check misses it |
| (b) PhaseCheck alone (included above) | 29 | 11 | 31 | 0 | - | - |

- Why (b) is bigger: two 35-bit mailbox registers and two 35-bit FIFO output registers, against (a)'s 2 x 2 x 10 synchroniser FFs and Gray logic.
- A pointer-exchange (b) (dual-clock RAM, binary pointers sampled on the safe edge, no mailbox) would drop the mailboxes; estimate is about equal to (a) once PhaseCheck is added. Not built.
- Latency is irrelevant at this interface: < 50 ns against 4 us HyperRAM pauses and 8.53 us per 512 B packet.

### (c) Width change: where

- Chosen: on the 60 side, 8 to 32 bits before the crossing (`gateware/ulpi_bridge/stream.py`).
  - Word = 35 bits: data 32, byte count - 1 (2), packet last (1); fits 512 x 36 EBR exactly.
  - Crossing carries <= 15 M words/s; 120 side sees the baseline's 32-bit stream, no byte logic.
  - Packer and unpacker sustain 1 byte/cycle with no bubble (sim `stream-ulpi-rate`).
- Alternative: cross 9-bit bytes (2048 x 9 EBR, same 2 KiB), pack on the 120 side. Same EBR, more 120-side logic, no gain.
- 16 bit: no benefit; the baseline stream is 32 bit.

### What (b) requires to be safe

- **One PLL for both clocks.** Example: VCO 480 MHz, CLKOP /4 = 120 MHz (feedback), CLKOS /8 = 60 MHz. Dividers count the same VCO (TN-02200 18.1.3).
- **Today `usb` comes straight from the A8 oscillator.** A PLL compensates only its own feedback path (7.3); a primary-routing input has uncompensated injection delay (18.2.1). The oscillator-direct 60 vs PLL 120 offset is placement-dependent and unknown: (b) is not usable on that clocking. Moving `usb` (and the ULPI clocks to the PHYs) onto CLKOS is a board-wide change.
- **Static phase:** `usb` rising edge T/4 after a `fast` rising edge (T = 8.33 ns): CLKOS +2.08 ns = one VCO period at 480 MHz = 45 deg at 60 MHz. CLKOP must stay unshifted (18.1.4).
- **Margins by construction at nominal phase:**
  - usb-launched registers: captured by `fast` with >= 3T/4 setup, >= T/4 hold.
  - fast-launched registers (`safe` edge only, 3T/4 after a usb edge): captured by `usb` with 1.25T setup, 0.75T hold. Sim launch monitor checks every such change.
- **Reset / lock sequence:**
  1. PLL RST or power-up resets the output dividers together (18.3.3, dividers restart 1 ns after RST).
  2. LOCK (18.3.12, ~16 ms, unsticky) releases the `fast` reset.
  3. PhaseCheck: 2 warm-up cycles + 16 consecutive good usb periods = `ok` (sim: 35 fast cycles).
  4. `usb` side leaves reset 2 usb cycles later (`up`); the ULPI link starts only after `up`.
- **If the PLL relocks out of phase** (dividers restart misaligned, PHASESTEP pulsed, or a different divider set): mailbox toggles and 35-bit words are sampled with no synchroniser near a changing edge: torn or metastable words, duplicated or lost words, silently. The data path cannot detect this; only the check can.
- **Runtime detection (PhaseCheck):** a usb-domain toggle sampled on `fast` rising and falling edges.
  - Good: toggle seen in the first half of every second fast cycle, never in the second half.
  - Catches: ratio not 2:1, stopped `usb`, frequency drift, phase in the second half of the fast cycle.
  - Before `ok`: never qualifies. After `ok`: sticky `fault` within 3 fast cycles of a phase jump (sim); `fault` holds the bridge in reset, in-flight data dropped, `clear` requalifies.
  - Resolution is T/2: accepted window [0, T/2) after a fast edge, nominal T/4 = +-2.08 ns before it trips. Inside the window margins shrink linearly to zero at its edges; finer needs more PLL phases (CLKOS2/3) as extra samplers.
- **Timing analysis** of the usb/fast paths as related clocks by nextpnr: not checked (no build in this work). The by-construction margins above do not rely on it.

## Depth

All figures from the sources table; HyperRAM side at the baseline's CK 165.

- ULPI: 60 MHz x 8 bit = 60 MB/s per direction, half duplex on the wire. HS bulk packet 512 B = 512 cycles = 8.53 us.
- HyperRAM transaction overhead: CA 3 + latency 13 + guards 9 + tail ~3 + CS# High >= tCSHI 6 ns (1 CK) = ~29 CK = 0.18 us; use 0.2 us.
- Longest pause the bridge sees on one direction: one max-length transaction of the other direction (CS# Low <= tCSM 4 us) + 0.2 us = **P = 4.2 us**.
- Refresh adds nothing: fixed 2x latency (CR0 default) covers it (9.4.3); self-refresh runs while CS# is High (9.5.4).

RX (ULPI to HyperRAM): ULPI cannot wait, so the bridge must absorb.
- Engine policy (baseline): a write burst starts only when the FIFO holds all of it. Threshold W = one packet (512 B) or packet end.
- Peak = W + 60 MB/s x P = 512 + 252 = **764 B**. With W = the baseline's max burst (1216 B): 1468 B.
- Drain once the burst runs: >= 311 MB/s against 60 MB/s fill.
- 2 KiB per direction covers both: x2.7 at W = 512, x1.4 at W = 1216.

TX (HyperRAM to ULPI): the link cannot underrun inside a packet.
- Store and forward: a packet starts only when all 512 B are in the bridge (the 120 side or link layer enforces this).
- Back-to-back packets: fetch the next while the current one sends (8.53 us). Worst fetch = P + 0.2 us + 256 words / 165 MHz = 4.2 + 0.2 + 1.55 = 5.95 us < 8.53 us.
- Needs 2 packets = **1 KiB**; 2 KiB holds 4.

Result: 512 words x 35 bits per direction; one EBR each (no extra EBR over 256 words). Measured capacity before `rx_overflow` (sim `overflow`): (a) 2052 B, (b) 2060 B.

## Build

- `gateware/ulpi_bridge/`: `stream.py` (packer/unpacker), `async_bridge.py` (a), `ratio_bridge.py` (b, `PhaseCheck`). Domains `usb` (60) and `fast` (120); renamable.
- Ports (both): 60 side `rx_data/last/valid/ready`, sticky `rx_overflow`, `tx_data/last/valid/ready`; 120 side `rxw_word/valid/ready`, `txw_word/valid/ready`. (b) adds `up` and `check.ok/fault/clear`.
- (a) reset: assert both domain resets; each FIFO empties from its write-domain reset (read-domain reset alone keeps data).
- (b) reset: `fast` domain reset; the bridge core is held while `~ok`, `usb` side follows through a reset synchroniser.

## Simulation

`./scripts/ulpi_bridge_sim.py` (log `tmp/logs/ulpi_bridge_sim.log`, results `tmp/ulpi_bridge/sim.json`). Run 2026-11-03: **24/24 positive pass, 5/5 negative controls fail as required, 2 informational.**

- Clocks: locked mode from one tick grid (T/8 = 1.042 ns), `usb` offset in T/8 steps; free mode with independent periods.
- Both options:
  - `traffic`: ~12 KiB each way, packet sizes 1-1024 incl. 1-5, 7, 8, 511-513, random gaps and back-pressure both sides (p = 0.7); byte-exact with packet boundaries.
  - `traffic-stalls`: as above plus 3000-cycle stalls on each sink, p = 0.5 sinks.
  - `stream-ulpi-rate`: RX valid every cycle ignoring ready, TX sink always ready: no overflow, zero TX bubbles inside packets.
  - `overflow`: RX sink never ready: `rx_overflow` set; capacity reported.
  - `reset-mid-stream`: RX sink stopped, both FIFOs holding data, reset, then traffic: post-reset output equals post-reset input exactly.
  - `latency`: table above.
- (b) only:
  - Phase sweep `phase-offset-{1,2,3}T8` qualify, no fault; `{5,6,7}T8` never qualify. `0T8`, `4T8` (window edges) informational: sim has no metastability, either answer is right.
  - Ratios: fast 100 or 125 MHz with usb 60, 1:1, 3:1 never qualify; fast +0.4% drift qualifies then faults (93 cycles later).
  - `relock-out-of-phase`: offset 2 to 5 after qualifying: fault 3 fast cycles after the jump.
  - Launch monitor in `traffic`: 5534 fast-launched usb-read register changes, 0 within T/2 of a usb edge.
- Negative controls (must fail, did):
  - `a/NEG-byte-swap`, `b/NEG-byte-swap`: packer byte order reversed: 31/31 RX packets differ.
  - `b/NEG-safe-gate-off`: `safe` gate removed: launch monitor flags a change < T/2 before a usb edge.
  - `b/NEG-check-off-offset5`: check removed: bridge enabled at a bad phase.
  - `b/NEG-check-off-relock`: check removed: phase jump never faults.
- Limits: sim has no metastability or skew; a broken (b) relation does not corrupt data in sim, so (b)'s safety rests on PhaseCheck and the launch monitor, not on byte-exact results.
