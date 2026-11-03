# HyperRAM sequential baseline (non-RV path)

Reference measurement for the clean-room core (`gateware/hyperram_baseline/`); not the SoC driver ([soc-peripheral.md](soc-peripheral.md), #588).

Issue: [#581](https://github.com/awtoau/cynthion-workspace/issues/581).

**Rule (Dan, 2026-11-03):** this baseline is designed from public designs and the datasheet only. From this repo it takes board facts (pinout, 60 MHz oscillator pin, bank voltage) and nothing else: no repo controller, PHY, clocking, constants, timing values, script parameters or doc numbers. The review of the repo's own HyperRAM code is separate ([#582](https://github.com/awtoau/cynthion-workspace/issues/582), [#427 comment](https://github.com/awtoau/cynthion-workspace/issues/427)) and did not feed this design.

## Path measured

- Non-RV: synthetic generator (`sync`) -> 32-bit AsyncFIFO -> HyperRAM engine + controller (`hr`, own PLL) -> 32-bit AsyncFIFO -> checker (`sync`). No CPU, no SoC bus.
- `sync` 130 MHz moves 2 words/cycle (520 MB/s) each way, above the bus at every CK tested; `src_wait`/`sink_wait` count any cycle the engine waited on a FIFO.
- Throughput = 2 bytes x words moved / (engine cycles from first burst to last / CK). Includes command, latency, guard, tail and CS# High per burst; the first FIFO fill is counted once per run (`src_wait`, <= 460 CK through 180 MHz, against ~4.4 M CK per run). Measured CK is used.

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
- Passes: word counts, zero missing, device memory == pattern, CR0 = 0x8F2F, CS# Low <= tCSM. Negative controls fail as required: xor (every word), stale seed (every word), write latency one short (>half the words), and the pre-fix gateware (OE dropped under the ODDR'd CA byte; found on the board first, then modelled).


## How to run

- `./scripts/hyperram/hyperram_baseline.py sweep`: one invocation for every CK (60, 80, 100, 120, 140, 150, 160, 165, 180, 210, 240). It builds only the CKs whose cached bitstream is stale against the last gateware commit, loads each CK in turn with `apollo configure`, runs the delay sweep and then one pass of each case, and writes `tmp/hyperram-baseline/sweep-<id>.md`.
- One bitstream per CK. EHXPLLL output dividers are fixed at configuration time, and each rung needs a 0-degree and a 90-degree output. DCSC is a 2:1 mux, so one bitstream can carry at most two rungs. A cached build takes about 20 s.
- Each CK takes about 1 min, mostly JTAG round trips. Each case is a single pass, with no repeats or soak (Dan, 2026-11-03).

## Figures

Conditions:
- Sweep `20261103-121212` (2026-11-03T12:12..12:25+11:00). Cynthion r1.4 (W956A8MBYA6I, 166 MHz grade), bench room temperature. Die temperature not read.
- Gateware `1d272c9`; `sync` 130 MHz. The FIFO side supplies 260 Mwords/s, which is above the bus at every CK. CR0 is the power-on value 0x8F2F, read back after calibration. Fixed latency 7 (2x), linear bursts.
- Burst is the longest tCSM allows at that CK (`max_burst_words(CK)`). Each case covers the whole array (4 Mi words, 8 MiB) in one pass. The pattern toggles every DQ bit on every beat and folds in all 22 address bits.
- Every write uses a fresh random seed, so a read that returns data left by an earlier write fails.
- Two negative controls must fail every word. "xor" checks the read against the pattern xor 1. "stale seed" checks it against the write's seed xor 0x5A5A (a wrong seed, not the previous write's).
- Read delay: input DELAYF swept in 8-tap steps, 64 Ki words written and read per tap. The run uses the centre of the widest clean run.
- **CK measured:** the hr clock is counted over 2^19 cycles of the 60 MHz oscillator pin (8.74 ms window). That count checks the PLL ratio; the absolute frequency is only as good as the oscillator. MB/s is computed from the measured CK.

| CK req MHz | CK meas MHz | burst | clean taps / 16 | widest clean run (taps) | tap | write MB/s | read MB/s | write+read MB/s | read errors | write+read errors | xor ctl | stale-seed ctl | read % of pin |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 60.0000 | 188 | 16 | 128 | 64 | 103.5 | 100.7 | 102.1 | 0 | 0 | 4194304 | 4194304 | 83.9 |
| 80 | 79.9999 | 268 | 16 | 128 | 64 | 143.9 | 141.1 | 142.5 | 0 | 0 | 4194304 | 4194304 | 88.2 |
| 100 | 100.0001 | 348 | 14 | 88 | 40 | 184.1 | 181.2 | 182.7 | 0 | 0 | 4194304 | 4194304 | 90.6 |
| 120 | 120.0000 | 428 | 13 | 72 | 88 | 224.3 | 220.9 | 222.6 | 0 | 0 | 4194304 | 4194304 | 92.0 |
| 140 | 139.9999 | 508 | 13 | 104 | 72 | 264.4 | 261.0 | 262.7 | 0 | 0 | 4194304 | 4194304 | 93.2 |
| 150 | 150.0000 | 548 | 13 | 104 | 56 | 284.4 | 281.0 | 282.7 | 0 | 0 | 4194304 | 4194304 | 93.7 |
| 160 | 160.0001 | 588 | 14 | 96 | 48 | 304.4 | 301.1 | 302.7 | 0 | 0 | 4194304 | 4194304 | 94.1 |
| 165 | 165.0000 | 608 | 13 | 88 | 40 | 314.5 | 311.1 | 312.8 | 0 | 0 | 4194304 | 4194304 | 94.3 |
| 180 | 180.0000 | 668 | 14 | 64 | 32 | 344.5 | 341.1 | 342.8 | 0 | 0 | 4194304 | 4194304 | 94.8 |
| 210 | 210.0000 | 788 | 0 | 0 | -- | hung | hung | hung | hung | hung | hung | hung | -- |
| 240 | 240.0000 | 908 | 0 | 0 | 16 | 464.6 | 461.2 | 462.9 | **4194304** (bits 0xffbb) | **4194304** | 4194304 | 4194304 | -- |

Read eye (8-tap steps, taps 0..120; `.` clean, `X` errors or hung):

| CK | eye |
|---|---|
| 60 | `................` |
| 80 | `................` |
| 100 | `...........XX...` |
| 120 | `....XXX.........` |
| 140 | `XXX.............` |
| 150 | `X.............XX` |
| 160 | `............XX..` |
| 165 | `...........XXX..` |
| 180 | `........XX......` |
| 210 | `XXXXXXXXXXXXXXXX` (every tap hung) |
| 240 | `XXXXXXXXXXXXXXXX` |

### Where it stops, and how

- **60 to 180 MHz: clean.** Zero errors and zero missing words on every case, and both controls fail every word. Throughput tracks the measured CK: read is 94-95% of the pin rate from 160 up.
- **Eye through 180:** one bad band 2-3 steps wide, and its position moves with CK. A fixed round trip lands on a different phase of a shorter period, so any single fixed delay fails some CKs. The widest clean run narrows from 128 taps (60/80) to 64 (180), mostly because the band moves to the middle. Clean taps drop from 16/16 to 13-14/16.
- **210 MHz: the result depends on the build.**
  - The earlier build (`433f21d`, sweep `20261103-115645`) was clean: eye `..XXXX........XX`, bad bands at taps 16-40 and 112-120. The whole array read and wrote back with zero errors at tap 80: 404.5 / 401.2 / 402.8 MB/s, 95.5% of the pin rate.
  - The rebuild (`1d272c9`, which only added the STATE register) hung on every tap. STATE read 0x104: engine in WAIT_W, controller idle, write FIFO empty, so the engine waited for generator data that never crossed into `hr`.
  - So at 210 the FPGA's `hr`-side logic and its FIFO crossing fail before the bus or the part does.
- **240 MHz: transactions complete and every word is wrong.**
  - 11 of 16 taps run at full rate, 462 MB/s, with 100% errors. Bad-bit masks are 0xffbb..0xffff.
  - 5 taps hang in WAIT_W, as at 210.
  - CR0 still reads 0x8F2F at tap 16, so single-word register reads still work.
  - Whether the part, the pads or the fabric fails first at 240 cannot be separated with this harness.
- **The checker detects real failure.** At 240 it reports 4194304 / 4194304 errors on reads that ran at full rate. The fresh seed per write rules out a read returning old data from a lower-CK write (the first, unseeded sweep had that risk; it is superseded and deleted here). The stale-seed control shows only that a wrong seed fails ([lessons.md](lessons.md#corrections-to-the-published-baseline)).
- **First register read before calibration is unreliable.** CR0 read at tap 0 returned 0x87AF (140), 0x8D2F (160), 0x0000/0x27AF (210), 0x5F2F (240). After calibration every CK through 180, and 240 at tap 16, read 0x8F2F.

## Gap vs rated max (CK 165: read 311.1 of 330 MB/s pin rate; rated part 166 MHz = 332 MB/s)

| gap | size at CK 165 | reason |
|---|---|---|
| CK 165, not 166 | 0.6% | 60 MHz reference with PFD >= 10 MHz: 166.67 (60x25/9) would need a 6.7 MHz PFD. 165 = 60x11/4 is the highest reachable at or below the rating |
| tCSM 4 us burst cap | ~3% (unavoidable) | 3 CA + 13 latency CK per burst inside 660 CK; tCSM is fixed at 4 us on this part (CR1) |
| fixed 2x latency (CR0 default) | ~1% | 7 CK per burst that variable latency, or latency code 6, would save (tACC 36 ns = 6 CK at 165); not changed in the baseline |
| guard cycles | ~1.4% | 3 + 6 CK with CS# Low and CK stopped, because CS#/OE are fabric registers and ODDR lags them; an IO-register CS# with matched latency removes most of this |
| conservative burst cap | ~0.5% | the cap assumes latency 31 and the full read tail; the exact cap at latency 13 is ~18 words longer |
| read vs write | ~1% | read tail (round trip after CK stops) and engine turnaround |
| engine per-burst cycles | ~0.5% | PICK/WAIT/done handshake, 2-3 CK |

- Not binding through 180 MHz: the pads (LVCMOS33, emulated-differential CK), the `hr` fabric (`--timing-allow-fail` build), the FIFOs and the `sync` side.

## Limit for sequential throughput

- Within the rating: the part's pin rate times burst efficiency under the tCSM cap, 94-95% at CK 165. Trimming the overheads above reaches about 97%.
- Overclocked: the bus and part still work at 180 MHz (341 MB/s read, 14% over the 166 MHz rating, single pass). The first failure is the FPGA `hr` logic at 210 (build-dependent hang); at 240, data is wrong at every tap.
- Reads need the input delay recalibrated per CK at startup. No single fixed delay passes every CK.

## Notes

- Board load is volatile (`apollo configure`); a power cycle or `./scripts/soc_run.py --no-build` brings the SoC back.
- Not measured: repeats, temperature, other boards, tap size in ps, DELAYF direction sense, burst-length sweep with seeded data (`sweep --bursts`).
