# HyperRAM sequential baseline (non-RV path)

Issue: [#581](https://github.com/awtoau/cynthion-workspace/issues/581).

**Rule (Dan, 2026-11-03):** this baseline is designed from public designs and the datasheet only. From this repo it takes board facts (pinout, 60 MHz oscillator pin, bank voltage) and nothing else: no repo controller, PHY, clocking, constants, timing values, script parameters or doc numbers. The review of the repo's own HyperRAM code is separate ([#582](https://github.com/awtoau/cynthion-workspace/issues/582), [#427 comment](https://github.com/awtoau/cynthion-workspace/issues/427)) and did not feed this design.

## Path measured

- Non-RV: synthetic generator (`sync`) -> 32-bit AsyncFIFO -> HyperRAM engine + controller (`hr`, own PLL) -> 32-bit AsyncFIFO -> checker (`sync`). No CPU, no SoC bus.
- `sync` 100 MHz moves 2 words/cycle (400 MB/s) each way, above the bus at every CK tested; `src_wait`/`sink_wait` count any cycle the engine waited on a FIFO.
- Throughput = 2 bytes x words moved / (engine cycles from first burst to last / CK). Includes command, latency, guard, tail and CS# High per burst; one initial FIFO fill per run (<= 255 cycles of a 4 Mi-word run).

## Sources

| source | revision | used for |
|---|---|---|
| Winbond W956D8MBYA/W956A8MBYA datasheet, `~/vendors/winbond/W956x8MBYA_A01-006.pdf` | A01-006, 2022-07-29 | CA layout (Tables 1/2), data placement (Figs 6/7), AC (Tables 21/22), tCSM (Table 24), reset (tRP/tRH/tRPH), CR0 default 0x8F2F, register addresses (9.1) |
| LiteX `litex/soc/cores/hyperbus.py`, https://github.com/enjoy-digital/litex/blob/f23babce22db4b0803cdb3e78a01653bcba9a337/litex/soc/cores/hyperbus.py | f23babce | latency count starts 1 CK before the CA ends (data at CK 2L+2) |
| LUNA `luna/gateware/interface/psram.py`, https://github.com/greatscottgadgets/luna/blob/af5acbde34bf0267e5be6bde187da1d02a1b8e8b/luna/gateware/interface/psram.py | luna-usb 0.2.3 | idea of realigning a word straddling two IDDR cycles (re-derived, not copied) |
| LUNA `luna/gateware/interface/jtag.py` `JTAGRegisterInterface`, same repo @ 1528a97d | luna-usb 0.2.3 | register readout (used as a library) |
| GSG `cynthion/python/src/gateware/platform/cynthion_r1_4.py`, https://github.com/greatscottgadgets/cynthion/blob/f01a190e88a88bdf8b862601e73e3effe93885f0/cynthion/python/src/gateware/platform/cynthion_r1_4.py | f01a190e | pins: CK C3/D3 (LVCMOS33D), DQ F2 B1 C2 E1 E3 E2 F3 G4, RWDS D1, CS# B2, RESET# C1, 60 MHz on A8; LVCMOS33, SLEWRATE FAST |
| prjtrellis `libtrellis/tools/ecppll.cpp`, https://github.com/YosysHQ/prjtrellis/blob/65fe191a290cd947c125f011e9e78a2ddc90d7a4/libtrellis/tools/ecppll.cpp | 65fe191a | EHXPLLL CPHASE/FPHASE for the 90-degree CK clock |
| Lattice ECP5 family datasheet FPGA-DS-02012 (not in the vendor store) | -- | PLL limits: PFD >= 10 MHz, VCO 400-800 MHz |

## Design

- Files: `gateware/hyperram_baseline/{board_r14,hyperbus,top}.py`; runner `scripts/hyperram/hyperram_baseline.py`; sim `scripts/hyperram/hyperram_baseline_sim.py`.
- One CK per `hr` cycle, 16 bits/cycle: DQ/RWDS through ODDRX1F/IDDRX1F; CK through an ODDRX1F on `hr90` (same PLL, CLKOS +90 degrees) so CK edges sit mid-byte.
- Reads captured by RWDS: a rising RWDS sample marks byte A; either IDDR alignment handled. All 9 inputs through runtime DELAYF (`DELAY` register, 0-127 taps); the runner sweeps taps per CK and runs at the centre of the widest clean run.
- CS#/OE are fabric registers; ODDR output lags them. CS# Low with CK stopped: 3 cycles before, 6 after; DQ OE held to CK 12 on reads; RWDS OE held to CS# High on writes.
- Fixed latency, CR0 power-on default (part reset by RESET# at start: 2 us low, 2 us before first CS#). Latency wait is a runtime register (default 2L-1 = 13 after the CA).
- Burst cap: tCSM 4 us at the longest settable latency (31) and full read tail: `max_burst_words` in `hyperbus.py`.

## Simulation

- Model written from the datasheet in the sim script (not a repo model): CA decode, fixed 2L latency, RWDS High over CA, edge-aligned read bytes, RWDS write mask, CR0.
- Pin path: ODDR 3 cycles, fabric CS#/OE 1, round trip 6 or 7 half-cycles (both IDDR alignments), IDDR 1; undriven DQ reads 0, undriven RWDS masks.
- Passes: word counts, zero missing, device memory == pattern, CR0 = 0x8F2F, CS# Low <= tCSM. Negative controls fail as required: xor (every word), write latency one short (>half the words), and the pre-fix gateware (OE dropped under the ODDR'd CA byte; found on the board first, then modelled).

## Figures

Conditions (all rows):
- Measured 2026-11-03T10:52..11:05+11:00, Cynthion r1.4 (W956A8MBYA6I, 166 MHz grade), bench room temperature, die temperature not read.
- Gateware commit `30fa64f` (rows marked `-dirty`/`60882ef` differ only in the host script). `sync` 100 MHz, CK as listed, CR0 power-on 0x8F2F (read back on every rung), fixed latency 7 (2x), linear bursts.
- Burst = `max_burst_words(CK)` (tCSM-capped); total 4 Mi words = whole 8 MiB array per case; pattern toggles every DQ bit beat to beat and folds in all 22 address bits.
- Input DELAYF tap: centre of the widest clean run of a per-CK sweep (8-tap steps, 64 Ki words write+read per tap).
- Negative control: read checked against pattern xor 1; must fail every word, and did on every rung.
- Regenerate: `./scripts/hyperram/hyperram_baseline.py table` (from `tmp/hyperram-baseline/results.jsonl`).

### Sequential, max burst

| CK MHz | burst | write MB/s | read MB/s | write+read MB/s | read errors / words | write+read errors / words | missing | neg control errors | pin rate MB/s | read % of pin | commit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 188 | 103.5 | 100.7 | 102.1 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 120 | 83.9 | 30fa64f268-dirty |
| 80 | 268 | 143.9 | 141.1 | 142.5 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 160 | 88.2 | 30fa64f268-dirty |
| 100 | 348 | 184.1 | 181.2 | 182.7 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 200 | 90.6 | 30fa64f268-dirty |
| 120 | 428 | 224.3 | 220.9 | 222.6 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 240 | 92.0 | 60882efc82 |
| 140 | 508 | 264.4 | 261.0 | 262.7 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 280 | 93.2 | 60882efc82 |
| 150 | 548 | 284.4 | 281.0 | 282.7 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 300 | 93.7 | 60882efc82 |
| 160 | 588 | 304.4 | 301.1 | 302.7 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 320 | 94.1 | 60882efc82 |
| 165 | 608 | 314.4 | 311.1 | 312.7 | 0 / 4194304 | 0 / 4194304 | 0 | 4194304 / 4194304 | 330 | 94.3 | 60882efc82 |

- `src_wait` (engine waiting on the generator) was only the first FIFO fill per run (<= 255 cycles); `sink_wait` 0 everywhere: the FIFOs and `sync` side never limited.

### Burst-length sweep (1 Mi words each, write / read MB/s, zero errors in every cell)

| CK MHz | burst 16 | burst 32 | burst 64 | burst 128 | burst 256 | burst 512 |
|---|---|---|---|---|---|---|
| 60 | 42 / 37 | 62 / 56 | 82 / 77 | 97 / 94 | - | - |
| 80 | 56 / 49 | 83 / 75 | 109 / 102 | 130 / 125 | 143 / 140 | - |
| 100 | 70 / 62 | 103 / 94 | 136 / 128 | 162 / 156 | 179 / 175 | - |
| 120 | 83 / 72 | 124 / 111 | 163 / 152 | 194 / 186 | 215 / 210 | - |
| 140 | 97 / 85 | 145 / 130 | 191 / 177 | 227 / 217 | 251 / 245 | - |
| 150 | 104 / 91 | 155 / 139 | 204 / 190 | 243 / 233 | 268 / 262 | 283 / 280 |
| 160 | 111 / 97 | 165 / 148 | 218 / 203 | 259 / 248 | 286 / 280 | 302 / 298 |
| 165 | 115 / 100 | 170 / 153 | 225 / 209 | 267 / 256 | 295 / 288 | 312 / 308 |

(`-`: above the tCSM cap at that CK.)

### Read eye (input DELAYF taps 0-127, 8-tap steps; `.` clean, `X` errors)

| CK MHz | 0 8 16 24 32 40 48 56 64 72 80 88 96 104 112 120 | tap used |
|---|---|---|
| 60 | `. . . . . . . . . . . . . . . .` | 64 |
| 80 | `. . . . . . . . . . . . . . . .` | 64 |
| 100 | `. . . . . . . . . . . X X . . .` | 40 |
| 120 | `. . . . X X X . . . . . . . . .` | 88 |
| 140 | `X X X . . . . . . . . . . . . .` | 72 |
| 150 | `X . . . . . . . . . . . . . X X` | 56 |
| 160 | `. . . . . . . . . . . . X X X .` | 48 |
| 165 | `. . . . . . . . . . . X X . . .` | 40 |

- The bad band moves with CK (a fixed round trip against a shrinking period): a fixed input delay passes some CKs and fails others. Calibration per CK is required; with it every rung is clean.
- Tap size and DELAYF direction sense are not measured here; the band width is 2-4 steps.

## Gap vs rated max (CK 165: read 311.1 of 330 MB/s pin rate; rated part 166 MHz = 332 MB/s)

| gap | size at CK 165 | reason |
|---|---|---|
| CK 165, not 166 | 0.6% | 60 MHz reference, PFD >= 10 MHz: 166.67 (60x25/9) needs PFD 6.7 MHz; 165 = 60x11/4 is the highest reachable at or below the rating |
| tCSM 4 us burst cap | ~3% (unavoidable) | CA 3 + latency 13 CK per burst inside 660 CK; CR1 tCSM is fixed at 4 us on this part |
| fixed 2x latency (CR0 default) | ~1% | 7 CK per burst a variable-latency or code-6 setting would save (tACC 36 ns = 6 CK at 165); not changed in the baseline |
| guard cycles | ~1.4% | 3 + 6 CK with CS# Low and CK stopped: CS#/OE are fabric registers and ODDR lags them; an IO-register CS# with matched latency removes most of it |
| conservative burst cap | ~0.5% | cap assumes latency 31 and the full read tail; exact cap at latency 13 is ~18 words longer |
| read vs write | ~1% | read tail (round trip after CK stops) and engine turnaround |
| engine per-burst cycles | ~0.5% | PICK/WAIT/done handshake, 2-3 CK |

- Not binding at any rung tested: the FPGA pads (LVCMOS33 at 165 MHz CK, emulated-differential CK), the fabric (`--timing-allow-fail` build, zero errors), the FIFOs/`sync` side.

## Limit for sequential throughput

- The part's own pin rate x tCSM-bounded burst efficiency. At CK 165 the baseline is at 94-95% of the pin rate; the reachable ceiling with the remaining overheads trimmed is ~97%.
- The read path needs a per-CK input-delay calibration (eye table above); without it, sequential reads fail at some CKs regardless of overhead.

## Notes

- First transaction after one configure read CR0 as 0x8B2F (bit 10 clear); 16 re-reads at taps 0/40/80/120 all 0x8F2F. Not reproduced or explained.
- Board load is volatile (`apollo configure`); the SoC returns on power cycle or `./scripts/soc_run.py --no-build`.
- Not measured: soak beyond one whole-array pass per case, temperature, other boards, CK above the rating.
