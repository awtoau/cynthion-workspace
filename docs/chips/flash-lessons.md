# Config flash lessons from the old flash/QSPI work

Harvest of the old W25Q32 work for the clean-room flash baseline ([#590](https://github.com/awtoau/cynthion-workspace/issues/590); branch `worktree-agent-a6a5973e02a69bd09`, `gateware/flash_baseline/`, runner `scripts/flash/flash_baseline.py`) and the no-CPU test image ([#591](https://github.com/awtoau/cynthion-workspace/issues/591)). Part facts that still hold live in [w25q32-config-flash.md](w25q32-config-flash.md); this page holds the evidence trail, the bugs and the methods.

- Facts and test methods only; no design is carried over (clean-room rule, Dan, 2026-11-03).
- Old sources: [`flash.py`](../../gateware/soc/peripherals/flash.py), [`flash_cdc.py`](../../gateware/soc/peripherals/flash_cdc.py), [`flash_sck_full.py`](../../gateware/soc/peripherals/flash_sck_full.py), [`qspi_gateware.py`](../../gateware/probes/qspi/qspi_gateware.py), [`qspi_burst.py`](../../scripts/fabric/qspi_burst.py), [`qspi_burst_sim.py`](../../scripts/fabric/qspi_burst_sim.py), the old flash doc (`git show 2b3405b:docs/chips/w25q32-config-flash.md`), retired scripts in [`debris/scripts/`](../../debris/scripts), issues [#100](https://github.com/awtoau/cynthion-workspace/issues/100) [#167](https://github.com/awtoau/cynthion-workspace/issues/167) [#459](https://github.com/awtoau/cynthion-workspace/issues/459) [#460](https://github.com/awtoau/cynthion-workspace/issues/460) [#533](https://github.com/awtoau/cynthion-workspace/issues/533).
- Baseline read at `160182c`. Cited as `spiflash.py`, `top.py` (both `gateware/flash_baseline/`), `harness` (`scripts/flash/flash_baseline.py`), `sim` (`scripts/flash/flash_baseline_sim.py`) on that branch.
- Verdicts: **true**, **superseded**, **disproved**, **open**. Harvested 2026-11-03.

## Board / part facts

| fact | evidence | verdict |
|---|---|---|
| JEDEC `EF 40 16`; -IM/-JM read `70 16`, JW parts `60 16`/`80 16` | `apollo flash-info`, `debris/scripts/flash_capacity_probe.py` (Aug); JEDEC first right in `12cb78d` 2026-07-31; baseline `50d95a9` 2026-11-03 | true |
| SR1 `00`, SR2 `02` (QE = 1, factory-fixed for IQ/JQ), SR3 `60` (DRV 25%, WPS 0; no ADS bit, S23 reserved) | `flash_ceiling.py --status` (Aug); baseline `50d95a9` | true |
| SR3 volatile write (50h then 11h) through Apollo background SPI did not take: read back `60` | old flash doc "Registers that affect read speed" | open; never retried from fabric |
| Unique ID `355027cba3ac60de` (one board) | `apollo flash-info` | true |
| Capacity 4 MiB three ways: SFDP density, ID byte 0x16, aliasing (4/8/12 MiB read offset 0; nothing past 16 MiB). Aliasing compared against live bitstream bytes (`Part: LFE5U-12` legible), never 0xFF | `flash_capacity_probe.py` | true; method: never judge a path by FF/00 |
| Continuous Read (M5-4 = 10, 0xA0) works although Rev G documents it nowhere (it says M = Fxh in four places); survives an FPGA reconfigure; a part left in it reads the first 8 DQ0 bits of address/mode as an opcode (address 0 + mode FF spells 0x03) and returns real data from the wrong address | `f3c0f89` 2026-08-03; [#100](https://github.com/awtoau/cynthion-workspace/issues/100) 2026-08-03 | true; baseline sends M = F0h but never a continuous-read reset or 66h/99h at load |
| QPI and DTR "absent on this die" (Rev G Tables 1/2 have no 38h/FFh/C0h/0Dh/BDh/EDh) | old flash doc | disproved on silicon by baseline `50d95a9`: 38h + 4-wide 9Fh = EF4016; EDh 0 errors / 1048576 vs fresh seed. #100 had flagged it as untested. Negative controls still missing (see below) |
| A part left in QPI stops the FPGA booting from flash (config engine reads SPI); only FFh in QPI or a power cycle clears it | [#100](https://github.com/awtoau/cynthion-workspace/issues/100) 2026-08-03; `hsk/tangnano20k_spi_flash_example` README | true; baseline sends 4-wide FFh at load (`harness` `measure()`) |
| SCK only via USRMCLK on N9: no DDR at CCLK (nextpnr and Diamond 3.14 map both refuse ODDRX1F -> USRMCLK: "DDR component 'sck_oddr' cannot be packed in any I/O logic block"); no DATAMUX_ODDR/IOLDO mux at the site; JA4 has no global-spine source, so a global clock reaches USRMCLKI only through a LUT or FF | `906f0bf` 2026-08-13, [`usrmclk_oddr`](../../scripts/diamond/designs/usrmclk_oddr) | true; so the baseline's CLKOS2 -> USRMCLKI path is fabric-routed and unconstrained against /CS and IO |
| User-mode USRMCLK has no fmax, no setup/hold, no prjtrellis timing in any grade; the "62 MHz MCLK limit" is fCCLK (config engine) | `f3c0f89`, [#100](https://github.com/awtoau/cynthion-workspace/issues/100) 2026-08-03 | true; "62 MHz" disproved. Still repeated in [`flash.py`](../../gateware/soc/peripherals/flash.py) docstring and #100's body |
| TN-02039 §6.1.2: once USRMCLK is used, background programming through the SPI port is gone; Apollo (U6) is on none of the flash nets | [#590](https://github.com/awtoau/cynthion-workspace/issues/590) comment 1 | true |
| `apollo flash-read` of all 4 MiB in one call silently corrupts past ~1.9 MB (returns the READ_PAGE opcode and page address) | `2b2ada5` 2026-07-29 | true; never use it as a full-part reference |
| Pull-ups: /CS 5.1k (R110), CLK 5.1k (R109); IO0-IO3 none; no series parts on any net | [#590](https://github.com/awtoau/cynthion-workspace/issues/590) comment 1 (`flash_board_nets.py`) | true; old "pull-ups on DQ2/DQ3 UNVERIFIED" resolved: none |
| Old SoC pads: DRIVE unset = 8 mA, SLEW SLOW, PULLMODE NONE, HYSTERESIS ON on DQ (from `top.config`); now stated explicitly | [#459](https://github.com/awtoau/cynthion-workspace/issues/459), `eb4f86a` | true for the SoC; baseline runs DRIVE 16 / FAST, a different electrical point from every old measurement. Drive x slew sweep never run |
| Old probe read byte-exact to 144 MHz SCK in 5 modes x 4 divisors x 3 sync rates (60 points); ceiling was the probe's own fmax (149 MHz; Glasgow IOStreamer above); checks at bytes 0-15 and 1008-1023 against `apollo flash-read` | `f3c0f89` 2026-08-03, one build, default seed, 8 mA / SLOW | superseded as a limit by the baseline SCK sweep; a cross-check for it |
| Old SoC fast paths: FLASH_PHY_FAST (120 MHz domain) and FLASH_FULL_SCK (gated clock, SCK = domain) both left the board silent | `6020f96` 2026-08-13 | superseded (SoC removals approved in #591) |
| Program/erase (baseline, 20 MHz, one pass): tPP 0.348-0.364 ms (02h and 32h, 2048 pages each), tSE 50-53 ms, tBE1 172 ms, tBE2 239-245 ms | [#590](https://github.com/awtoau/cynthion-workspace/issues/590) comment 3, `50d95a9` | current. Note: tBE1/tBE2 are 1.4x / 1.6x datasheet typical (120 / 150 ms), not "near typical" |
| tPP 0.7 ms in #100's body and `repos/apollo` `90c8b7b` is from the preliminary 2014 datasheet; Rev G says 0.4 ms | old flash doc §4 | true |

## Bugs the old work found

"Repeat?" = could the baseline at `160182c` hit the same class.

| bug | found by | repeat? |
|---|---|---|
| Opcode pre-shifted twice: JEDEC read 0; a wrong "<< 24" comment hid it | ILA of outgoing DQ0 next to an mmap positive control in the same capture (`12cb78d`) | low: own `desc()`; sim checks JEDEC |
| CS dropped between transfers: CS field was a one-cycle pulse, CS followed `tx_fifo.r_rdy` | ILA, gaps of 81/36/36 samples (`38a2993`, `b999ce6`) | no: one engine transaction holds /CS |
| luna crossbar starved the controller (arbitrated on `cs`, which mmap holds 256 cycles after a burst; no grant in 600 cycles); fair crossbar can still re-grant mid-command | sim (`16d0008`), [#460](https://github.com/awtoau/cynthion-workspace/issues/460) | n/a |
| CS hold crossed clock domains unsynchronised; build and sim clean because the flash samples CS | review (`819f8f1`) | partly: CLK (CLKOS2 +180 deg through fabric to USRMCLKI) vs /CS and IO (CLKOP fabric FFs) skew is placement-dependent and unconstrained |
| Burst sequencer armed with a stale `length` (32768); only with real IOStreamer latency (4 on LatticePlatform vs 2 in bare sim) | `f64c97b`, `qspi_burst_sim.py` | moderate: `sim` models fabric registers only, no pad/PLL-phase latency |
| Power-on read became the comparator's reference (`pending` started set) | identical mismatch counts at 120 and 144 MHz (`f3c0f89`) | no: reference is a seed. **Related bug present**: `first_point` sets `state["seed"]` before erase/program finish; on `Hung` the seed stays set, every later SCK point skips programming and reads against a region never written. `last_seed()` takes the last "program 02h" done even if 32h or the erase failed |
| Host check covered only the first 32 bytes (the ones a marginal clock gets right) | `f3c0f89` | no: every byte checked in fabric |
| In-FPGA pass comparator checked a buffer nothing wrote (`done` level vs `capturing` cleared `first_pass`) | same 3 mismatches at 120 and 144 MHz (`qspi_gateware.py`) | n/a; lesson: identical counts at two rates = instrument fault |
| `done` as a level re-triggered forever (3.9e9 cycles); a busy-bit gap read the previous run's count; the wedge reported plausible numbers until completed-read count was checked | `qspi_gateware.py`, `qspi_burst.py` `run_burst` | fixed class: baseline hit it on its first board run (GO edge detector vs toggled bit: SR1 read the JEDEC value), fixed; SEQ added `074f8c2`. QPI/DTR result `50d95a9` predates SEQ |
| Capture write pointer not reset: a re-measure read stale bytes and passed | `qspi_gateware.py` | no: all results reset at GO |
| Divisor 3 (30 MHz at sync 120) failed 22/64 bytes, repeatable 3x, while 1, 2, 4, 5, 7 passed; at sync 60 a different divisor failed: placement on the sample path, not the part | old flash doc | **yes**: one bitstream per SCK, so each rung is a new placement of the cap -> sck path; a failing rung needs a second seed/build before it is read as an SCK limit |
| Fixed two-cycle sample point probably late at SCK = domain (board silent) | `6020f96` (hypothesis) | superseded by runtime capture calibration |
| Unbuffered AsyncFIFO formed one path with the PHY carry chain: 123.72 -> 143.20 MHz once buffered | `7e25810` | n/a |
| Stale bitstream loaded: a failed build then `configure` loaded an older one; digest ignored the variant | `7e25810`, `189ef20` | **yes**: `gateware_rev()` = commit + `-dirty`; `build_cached` reuses `top.bit` when the stamp matches; two different uncommitted edits share it and the rev is written into every result row |
| Test value equal to the failure value: 0xFFFFFFFF looked like a floating bus, 0x00000000 like nothing driving; probe moved to 0x40 (`2a558800`) | `16d0008` | **yes**: `CHECK_BLANK` compares with 0xFF on lines with no pull-ups; a capture/turnaround fault that reads FF passes and marks sectors blank. Blank result logged, never gated |
| Stopwatch numbers with no positive control: a stalled SCAN looked like a fast one | [#100](https://github.com/awtoau/cynthion-workspace/issues/100) 2026-07-30 | **yes**: MB/s = bytes / `run_cycles`, both engine counts; "20.00 MB/s DTR" restates the clock, only the error count is evidence |
| Stale comments blocked work ("divisor 0 produces no clock", "62 MHz MCLK") | `f3c0f89` | still present in [`flash.py`](../../gateware/soc/peripherals/flash.py) and #100's body table |
| ILA across a clock boundary is invalid for sub-cycle phase questions | `flash_cdc.py` OBSERVED comment, `819f8f1` | analog: `sck`-domain results read from 60 MHz JTAG unsynchronised; `rd_stable` mitigates |
| Instruments nobody reads (FlashPinProbe, nine `o_*` signals) | [#533](https://github.com/awtoau/cynthion-workspace/issues/533) | n/a; removal approved in #591 |
| PLL dynamic phase (old HyperRAM rig): phase ports driven while `DPHASE_SOURCE` stayed DISABLED, so the shifter was never reachable; a negative count printed a fresh state having stepped nothing; the direction bit stored beside the strobe could hand the shaper the previous direction | `5b3ffc0`, `69071df`, `f18aa83` | partly: baseline ties PHASESEL/PHASEDIR constant (no direction race); step count still not proven against a probe (see methods) |

## Test methods worth porting

Ranked for the baseline's gaps; most fit the no-CPU image ([#591](https://github.com/awtoau/cynthion-workspace/issues/591)).

| method | catches | cost | caught before |
|---|---|---|---|
| Negative controls for features: 4-wide 9Fh **without** 38h must not give EF4016; after FFh must not answer; 1-wide 9Fh while in QPI must fail; EDh at a wrong lat/half must fail; stale/xor controls on every mode, not only EBh and DTR. Sim: an IQ model without EDh must fail the DTR checks (`sim` implements EDh) | a feature "passing" on a part without it | ~5 ops | none yet (#590 QPI/DTR claim) |
| SFDP BFPT read (5Ah): JESD216 DWORD1 bit 19 = DTR, 4-4-4 bits, fast-read dummies | settles QPI/DTR from the die, independent of transport | one read-only op | proposed in the old doc, never run |
| Repeat each eye point N >= 3 (clean only if all pass); repeat the post-placement verify N times. "A rate that works nine times in ten is indistinguishable from one that always works if tried once" | intermittent points chosen as centre | N x eye time | `qspi_gateware.py` repeat compare |
| Phase-step retrace: step N forward then N back, the probe must return to the starting count; key a sweep on an accumulated count so a crossing shows as an intermediate value, not a coin flip | lost/doubled PHASESTEP pulses; a step count that does not name a phase | ~2N JTAG writes | `f18aa83` (14 forward + 14 back retraced exactly, old rig), `84f060a` |
| Flag identical error counts / bad_index across SCK rungs or seeds | instrument fault | free, in `table()` | `f3c0f89` |
| Gate every region result on `checked == length`, `txs == expected`, SEQ fresh | short or stale runs | host | `qspi_burst.py` `run_burst`; baseline GO bug |
| Control row that must not move: 20 MHz 0Bh x1 read at the start and end of every SCK point | board/state fault vs SCK fault | 2 ops per point | `7fd5001` (2 KiB D-cache control) |
| Positive control in the same capture / two independent paths agree: Apollo background SPI read of the programmed region (blank image loaded, < 1.9 MB per call) | self-consistent wrongness | one load | `38a2993`, `f3c0f89` |
| Content-hash bitstream stamp (sources + dirty diff + toolchain), refuse a stale `top.bit` | old bitstream measured as new | host | `7e25810`, `189ef20` |
| Seeded page before trusting a blank check (or blank-check only after a seeded read at the same capture passed); never judge by FF/00 | floating bus read as erased | 1 page | `16d0008`, capacity aliasing |
| Exit device state on every path: 4-wide FFh, 1-wide FFh with 16 clocks of IO0 high (mode-bit reset), 66h + 99h, then JEDEC 1-wide | sticky QPI / continuous / C0h / wrap state across runs and boot | 4 ops | old flash doc "Continuous Read is device state", #100 |
| Pin-activity counters per op (CS falls, SCK edges inside CS, SCK edges with OE driven) | missing/extra SCK inside /CS, contention | a few counters | FlashPinProbe ([#533](https://github.com/awtoau/cynthion-workspace/issues/533)) |
| Seed-sweep one netlist / two builds per failing rung | placement read as SCK limit | P&R time | divisor-3 failure |
| Run the old (broken) arrangement next to the fixed one in sim with real pipeline latency and assert the old one fails | regressions (e.g. GO edge detector) | sim | `qspi_burst_sim.py` |
| Cross-check toolchain claims in Diamond (does nextpnr analyse CLKOS2 -> USRMCLKI at all; nextpnr TMG_IGNOREs every EHXPLLL port) | silent unconstrained paths | Diamond run | `906f0bf`, `cc3de56` |

## Items that may explain the #590 symptoms

**Results not repeating between runs**
- One bitstream per SCK and per rebuild: cap -> sck capture path, pad FFs and the CLKOS2 -> USRMCLKI fabric route all move (divisor-3 precedent). `--timing-allow-fail`; timing report not recorded per point.
- CLK vs /CS / IO skew unconstrained; the 180 deg launch margin is T/2 = 2.08 ns at 240 MHz, under tSLCH/tCHSH 3 ns: an extra or missing CLK edge inside /CS on some builds only.
- Seed set before programming completes: a hung first point poisons every later point; `last_seed()` can name a region never fully written.
- BUSY poll stops on the first byte with bit 0 = 0 (`spiflash.py` receiver `stop`): a mis-captured 0x00 ends the poll at once, the next WREN/program goes to a busy part and is ignored: a partly programmed region, failing only that run.
- Sticky device state (Continuous Read survives a reconfigure; QPI, C0h, wrap persist): only 4-wide FFh at load.
- Electrical point DRIVE 16 / FAST with no series R and no pull-ups on IO0-3; old results were at 8 mA / SLOW. Turnaround/dummy cycles read leftover charge.
- `Board.wr` skips a write when the cached value matches: one dropped JTAG write desynchronises gateware and cache for the rest of the load.
- Stale-bitstream stamp (`-dirty`) reuses an old build across different edits.
- One probe per eye point: marginal points move the "widest run" and its centre.

**Eye probe failing right after the scan placed it**
- `pick()` orders points by `lat * n_steps + step` (wrap = next lat), but `seek()` steps `(phase + stride) % n_steps` at a **fixed** lat and concatenates `row + row` (wrap = same lat). A clean run that crosses the turn in `pick` is not one run in `seek`, which then stops after `run // 2` clean probes wherever it is: on an edge.
- `seek()` starts from wherever the phase is after the scan (the last grid point), not from `best`; if it starts inside a clean run it stops near that run's end.
- PHASESTEP drift (~6 steps per 64-step turn at 100 MHz, `160182c`, `255d553`): grid counts are not phases after a turn; nothing retraces to prove a step count.
- XFER chosen per row; near T/4 and 3T/4 both paths race (`160182c`).
- Coarse strides (JEDEC n/4, EBh n/16, DTR n/8) leave the centre one stride from an edge, inside the drift.
- The JEDEC eye judges 3 bytes on IO1 and fixes the EBh lat window to lat0 +- 1; if the EBh round trip is outside it at high SCK (the DTR window moved out by 80 MHz, `84363de`), the EBh "eye" is the edge of a window.
- Single-shot verify plus temperature drift between scan and verify.

**QPI/DTR "passing" on a part documented without them**
- Likely real: DTR gave 0 / 1048576 vs a fresh seed and 1048576 with the stale seed; the pattern folds every address bit and all four seed bytes; floating lines give FF/00, not EF4016 or a seeded pattern; consistent with the undocumented Continuous Read (the IQ datasheet documents a subset of the die).
- Open: no hardware negative control for QPI; QPI ID predates the SEQ check (the op before it is "QPI enter", result 0, so a stale read would show 0, not EF4016; re-run with SEQ); DTR always at address 0 (repeat at a second pair-equal address, e.g. 0x110000 bytes); `sim` model implements EDh despite claiming Rev G only; settle from SFDP.
- Hazard: QPI / C0h state must be cleared (66h + 99h) at the end of every run, before any flash boot.
