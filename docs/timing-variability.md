# Timing variability: why nextpnr's verdict moves, and what makes a build route

No Fmax figures here (house rule: we flash and test). Verdicts are nextpnr's per-clock PASS/FAIL, cells, and router behaviour.

- Toolchain: nextpnr-ecp5 0.10-74 (OSS CAD Suite), target `--12k --speed 8` ([lfe5u-12f.md](chips/ecp5/lfe5u-12f.md), settled).
- Netlist under study: default variant `ck80-dqs0-sync60`, commit `953c228`, source digest `7658b917…`, built 2026-11-02 15:30. yosys: 15254 LUT4, 2289 PFUMX, 276 L6MUX21, 1343 CCU2C, 311 TRELLIS_DPR16X4, 10277 FF. nextpnr: TRELLIS_COMB 20520/24288 (84%), TRELLIS_FF 10283, TRELLIS_RAMW 311, DP16KD 28/56, EHXPLLL 2/2.
- Measurements dated 2026-11-02 unless stated; one netlist (`top.json` copied, never re-synthesised), so every row below is placement/routing only.
- Builds on: [soc-size-review.md](soc-size-review.md) ("Fmax is placement luck"), [soc-clocking.md](soc-clocking.md), [#467](https://github.com/awtoau/cynthion-workspace/issues/467), [#478](https://github.com/awtoau/cynthion-workspace/issues/478), [#481](https://github.com/awtoau/cynthion-workspace/issues/481), [#495](https://github.com/awtoau/cynthion-workspace/issues/495), [#441](https://github.com/awtoau/cynthion-workspace/issues/441), [#361](https://github.com/awtoau/cynthion-workspace/issues/361), [#474](https://github.com/awtoau/cynthion-workspace/issues/474).

## 1. Causes, ranked by how much of the variance they own

1. **The critical path is wire, from the arbiter grant into the BIST peripheral's CSR read mux.** `clk`'s critical path on the reference build is 10 LUT levels, 87% routing, and walks the die: `arbiter.grant` (41,6) → `bus_fault` (56,6) → `decoder` ack gather + `bus__adr[17]` (35,28) → `usb_host_bridge` ack (35,28) → `hyper_bist` CSR bridge mux (25,21) → `results_mem` LUTRAM read mux (14,11) → read-shadow clock enable (7,17). Same start and same end module on 8/8 seeds (§2). [`top.py:900-1370`](../gateware/soc/top.py) hangs 15 windows on one `Decoder`; `BistCsrTransport` ([`bist_csr.py:83`](../gateware/soc/peripherals/bist_csr.py)) is a 128-register flat `csr.Multiplexer` whose read mux its own comment (`:131-136`) already records as the thing that made the BIST variant marginal. Wire length is what placement decides, so the verdict is a placement draw. At 56% occupancy the grant was 43% of 730 critical paths ([#495](https://github.com/awtoau/cynthion-workspace/issues/495)) with 26 distinct path identities over 40 seeds ([#481](https://github.com/awtoau/cynthion-workspace/issues/481)); at 84% it is all of them.
2. **router2 adds the detour that fails the clock.** Same placements (post-place checksum `0x59f0a62d` identical in both seed-1 logs), seeds 1, 2, 3, 5, 6, 7: router2 gives `clk` PASS 2/6 and `hr_clk` PASS 2/6; **router1 gives PASS on every clock on all 6**, and the `clk` path drops from 83-89% wire to 79% (§4). router2 is a congestion-priced PathFinder: it clears overuse by detouring whichever net is cheapest, timing is a weight; router1 routes arc by arc with rip-up on criticality. [`fast_build_env.py:96`](../scripts/fast_build_env.py)'s "`--router router2` recovers the Fmax" was measured 2026-08-10 on a 56%-occupancy netlist; at 84% the opposite holds. `soc_run.py --router router1` already exists.
3. **Placer seed.** HeAP + SA refinement on a fixed netlist: at 56% occupancy the spread is ~13% of the median over 40 seeds ([#467](https://github.com/awtoau/cynthion-workspace/issues/467)); at 84% occupancy see §2. Nothing in the project passed `--seed` before 2026-08-12; `soc_run.py --seed` now does.
3. **`--parallel-refine`** (shipping default, [`fast_build_env.py:96`](../scripts/fast_build_env.py)): threaded SA mutates one placement from 16 threads, so the same netlist and seed give different placements per run ([#361](https://github.com/awtoau/cynthion-workspace/issues/361), [#429](https://github.com/awtoau/cynthion-workspace/issues/429)). Same order of spread as the seed. `soc_run.py --no-parallel-refine` removes it; `--threads 31 --router router2` alone is deterministic ([#441](https://github.com/awtoau/cynthion-workspace/issues/441), [#481](https://github.com/awtoau/cynthion-workspace/issues/481) at 4 and 31 threads).
4. **Netlist re-roll.** Any source change re-maps the flattened design: TRELLIS_COMB moves ±194 for a changed constant ([#454](https://github.com/awtoau/cynthion-workspace/issues/454)), -687 LUT4 when *adding* adders ([#467](https://github.com/awtoau/cynthion-workspace/issues/467)). A different netlist is a fresh placement draw, so a before/after pair cannot be compared on timing at all; 22 cells apart was a 8 MHz-class swing ([#441](https://github.com/awtoau/cynthion-workspace/issues/441)).
5. **The constraint does not steer the placer.** 60 vs 80 MHz on `clk` gives bit-identical results on 12/12 seeds ([#478](https://github.com/awtoau/cynthion-workspace/issues/478)): criticality is relative to the worst path, so the request only decides PASS/FAIL.
6. **The model corner.** Numbers are -8 for a -6-marked part at an undocumented corner ([#474](https://github.com/awtoau/cynthion-workspace/issues/474), settled). A design at 50 °C running where STA says FAIL is ordinary corner conservatism, not evidence that STA is broken; it is also not margin.
7. **Not causes:** yosys threads (pinned to 1, [#306](https://github.com/awtoau/cynthion-workspace/issues/306)); router2 (deterministic per seed); the constraint set (§3).

## 2. Seed study on one netlist (2026-11-02)

`pnr_noise.py --runs 8 --jobs 4 --opts "--threads 6 --router router2 --timing-allow-fail"`, seeds 1-8, no `--parallel-refine`, `Program finished normally.` in every log. Reference build (shipping flags, no seed): `clk` FAIL, `hr_clk` PASS, 63 router iterations, 19-iteration plateau at 3 overused wires.

| seed | `clk` | `hr_clk` | `usb` | `jtag_tck` | router2 iterations | plateau | wall s |
|---:|---|---|---|---|---:|---:|---:|
| 1 | FAIL | FAIL | PASS | PASS | 75 | 12 | 163 |
| 2 | FAIL | PASS | PASS | PASS | 78 | 17 | 154 |
| 3 | PASS | FAIL | PASS | PASS | 61 | 4 | 127 |
| 4 | PASS | PASS | PASS | PASS | 138 | 72 | 206 |
| 5 | FAIL | FAIL | PASS | PASS | 68 | 10 | 148 |
| 6 | PASS | FAIL | PASS | PASS | 54 | 6 | 124 |
| 7 | FAIL | PASS | PASS | PASS | 56 | 7 | 121 |
| 8 | PASS | PASS | PASS | PASS | 72 | 20 | 111 |

- Cells identical on all 8 (COMB 20520, FF 10283, DP16KD 28): packing is the netlist's, placement is the seed's.
- **`clk` PASS 4/8, `hr_clk` PASS 4/8, both 2/8 (seeds 4, 8), routed 8/8.** The two verdicts are independent across seeds.
- **Both marginal paths end in the BIST peripheral.** `clk`: `arbiter.grant` → `decoder` → `hyper_bist.transport.bridge.mux.r_shadow_*_data` (the BIST CSR transport's read-shadow registers, via the `results_mem` LUTRAM read mux) on **8/8 seeds**. `hr_clk`: `hyper_bist.engine.index` → `checked_against` ([`hyperram_ceiling_top.py:620`](../gateware/probes/hyperram/hyperram_ceiling_top.py)) → compare with `psram.read_data` (`:962`) → `bad_index`/`bad_want`/`bad_got` capture on 7/8 seeds (seed 8: engine FSM → `psram.fsm_state`). 73-80% wire.
- The `clk` critical path starts at `arbiter.grant` on **8/8 seeds** (and on the reference build); 83-89% wire, logic 2.2-2.9 ns, 10-11 LUT levels. At 56% occupancy it was 43% of paths ([#495](https://github.com/awtoau/cynthion-workspace/issues/495)); at 84% the grant fan-out owns the verdict outright. Where the path ends varies with the seed (CSR bridge shadow registers, LUTRAM read muxes).
- nextpnr's pre-route (placement) estimate says `clk` FAIL on 8/8 seeds; the post-route verdict is PASS 4/8 with router2 and PASS on every router1 run (§4). The placement is marginal on every seed; which side of the line the final route lands is the router's doing.
- Router effort does not predict the verdict: seed 4 took 138 iterations and a 72-iteration plateau at a handful of overused wires, and passed both clocks; seed 6 took 54 and failed `hr_clk`.
- Wall time 111-206 s per run with 4 in flight at `--threads 6` and another agent's build on the box (loadavg 13); not a clean timing.
- So: with the current netlist, one default build is a coin flip on `clk` AND a coin flip on `hr_clk`. `soc_run.py --build-only` on the default variant has been failing or passing on the draw since `sync` went to 60 ([#525](https://github.com/awtoau/cynthion-workspace/issues/525)).

## 3. Constraint audit: what nextpnr was told

| constraint | set at | nextpnr sees | verdict |
|---|---|---|---|
| `sync` 60 MHz | [`clocks.py:449`](../gateware/soc/clocks.py) → `FREQUENCY NET "car.clk_sync"` | `$glbnet$clk` at 60 | real; the clock that fails |
| `hr` 80 MHz (`hr_mhz_max`) | [`hyperram_clocks.py:344`](../gateware/soc/hyperram_clocks.py) → `FREQUENCY NET "hr_car.clk_hr_rung0"` | `$glbnet$hr_clk` at 80 | real; PASS on the reference, FAIL on the `fdrive8-fslewSLOW` rebuild (re-roll, not the pins) |
| `usb` 60 MHz | platform default clock → `FREQUENCY PORT "clk_60MHz_0__io"` | derived `$glbnet$aux_phy_0__clk__o` at 60 (oscillator net, named after a sink) | real; never binds |
| `jtag_tck` 20 MHz | [`jtag_stage.py:112,157`](../gateware/soc/bus/jtag_stage.py) `JTCK_CONSTRAINT_HZ` | `$glbnet$jtag_tck` at 20 | invented: Apollo drives 405 kHz ([#461](https://github.com/awtoau/cynthion-workspace/issues/461), whose "nothing sets it" is stale). 50x over-constrained, never binds, harmless |
| `fast` (2x sync) | [`clocks.py:451`](../gateware/soc/clocks.py) | absent in `dqs0` variants | n/a here |
| `BLOCK ASYNCPATHS; BLOCK RESETPATHS;` | Amaranth emits | accepted silently (the binary carries exactly those two literals; any other verb logs "ignoring unsupported LPF command") | no effect: nextpnr never grades async or cross-domain paths |
| cross-domain `clk`↔`hr_clk`, `usb`↔`clk`, `jtag`↔`clk`, `<async>`↔* | none | "Max delay" rows only, no PASS/FAIL | not in any verdict; CDC correctness is the design's (`FFSynchronizer`, `AsyncFIFO`), not STA's |
| I/O timing (ULPI, HyperRAM DQ/RWDS, flash) | none; nextpnr-ecp5 honours `FREQUENCY`, `LOCATE`, `IOBUF`, `SYSCONFIG` only | unchecked | hardware-only, as it always was |
| `--freq` | not passed | 12 MHz default applies to nothing (every fabric clock is constrained) | fine |

- Verdict: the constraint set is complete for what nextpnr can check. Nothing in it manufactures the `clk` FAIL; the reported critical path is a genuine `clk`→`clk` synchronous path.
- Only fix worth making: `jtag_tck` to the driven rate or leave it; it cannot change any verdict.

## 4. What makes a build route

Router2 on this netlist: first iteration 24015 overused wires, converges at iteration 63 after 20 iterations stuck at 3. `build_log_audit.router_progress` calls a build DIVERGING once overuse rises for 3 iterations past its best ([`build_log_audit.py:82`](../scripts/tools/build_log_audit.py)); three past builds were waited out to 610-1500 s that way ([#540](https://github.com/awtoau/cynthion-workspace/issues/540) comment, `soc_run.py:185`).

History (`timing_survey.py` over `tmp/awto_soc/build/*/top.tim`; refine = `--parallel-refine`):

| build | COMB | FF | RAMW | refine | router | iterations | plateau | routed |
|---|---:|---:|---:|---|---|---:|---:|---|
| `build` (2026-08-10) | 16939 | 8991 | 211 | yes | router2 | 38 | 1 | yes |
| `bist1-ck120-dqs1-…` | 17661 | 9065 | 211 | yes | router2 | 52 | 9 | yes |
| `bist0-ck100-dqs1-…` | 15406 | 7977 | 219 | yes | router2 | 72 | 17 | yes |
| `ck80-dqs0-sync60` (reference) | 20520 | 10283 | 311 | no | router2 | 63 | 19 | yes |
| `ck80-sync60-fdrive8-fslewSLOW` | 20808 | 10283 | 311 | no | router2 | 79 | 10 | yes |
| `ck100-dqs1-sync60`, `ck160-dqs1-sync60` | 18615 / 19927 | 10024 / 10028 | 219 | yes | router1 | n/a | n/a | yes |

- Every logged build routed. Iterations and plateau length rise with COMB; the 84% builds sit 20 iterations on a 3-wire plateau, which is one bad draw from the divergence detector firing.
- Pressure points on this netlist: 84% COMB (LUT4 + CCU2C + the 311 DPR16X4 LUTRAMs = 1244 slices of VexiiRiscv regfile/LRU/results memories); `bus__adr`/`arbiter.grant`/CSR data fanning out to 15 windows; two PLL tiles both used (places `hr` logic by the PLL corner).

### Levers, same netlist, same seeds (2026-11-02; placement identical per seed unless the placer flag changes: SA starts from the same `timing cost 1797, wirelen 121678` on every seed-1 arm)

| arm (nextpnr flags beyond `--threads N --timing-allow-fail`) | seed | `clk` | `hr_clk` | routed | router iterations | router s |
|---|---:|---|---|---|---:|---:|
| `--router router2` (default, no refine) | 1 | FAIL | FAIL | yes | 75 | 30 |
| | 2 | FAIL | PASS | yes | 78 | 30 |
| **`--router router1`** | 1 | **PASS** | **PASS** | yes | n/a (arc table) | 43 |
| | 2 | **PASS** | **PASS** | yes | n/a | 129 |
| `--router router1`, confirmation | 3, 5, 6, 7 | **PASS ×4** | **PASS ×4** | yes ×4 | n/a | 35, 44, 114, **429** |
| `--router router2 --tmg-ripup` | 1 | **PASS** | **PASS** | yes | 77 | 41 |
| `--router router2 --placer-heap-timingweight 30` (new placement) | 1 | FAIL | PASS | yes | 146 | 48 |
| | 2 | PASS | PASS | yes | 102 | |
| shipping `--parallel-refine --threads 16 --router router2`, seed 1 twice | 1 | PASS | FAIL | yes | 78 | 48 |
| | 1 | PASS | PASS | yes | 67 | 30 |

- **router1 flips every failing clock on the same placement: 6/6 seeds, all four clocks PASS**, where router2 gave `clk` 2/6 and `hr_clk` 2/6 on the identical placements. router2's final route carries ~4 ns more wire on the `clk` critical path than router1's (seed 1: 14.75 vs 10.26 ns routing; logic unchanged at 2.6 ns). router2's `--tmg-ripup` recovers it too while keeping router2's congestion pass (n=1).
- router1's cost is wall time with a long tail: 35-129 s on five seeds, 429 s on seed 6 (router2: 30-48 s). Its arc table has no congestion metric, so `router_progress` cannot call it diverging; the 60 s stall rule on `top.tim` is the guard.
- Placer timing weight: two verdicts out of four changed, within the seed draw at n=2; it costs 30-90% more router iterations. Not a lever.
- `--parallel-refine`: two identical invocations, two placements, two different `hr_clk` verdicts; 2x faster. The reproducibility cost is the whole story ([#361](https://github.com/awtoau/cynthion-workspace/issues/361)).
- Wall time for router1 is 1.4-4x router2's on this netlist (43-129 s vs 30 s); on a 56%-full netlist router1 was 8.7 s ([`fast_build_env.py:40`](../scripts/fast_build_env.py)).

## 5. A go/no-go signal that does not come from nextpnr

The board runs where STA says FAIL and STA is a relative instrument ([#474](https://github.com/awtoau/cynthion-workspace/issues/474), [#469](https://github.com/awtoau/cynthion-workspace/issues/469)). Proposal, for Dan to run (not run here):

1. **Silicon margin ladder.** `riscv_clock_ladder.py --sync 60 70 72 75 80 84 90` with `--allow-timing-fail`, `cpu stress` ≥ 200 passes a rung plus `bench bram`/`bench flash`, die temperature from `DTR` logged per rung ([`riscv_clock_ladder.py`](../scripts/riscv/riscv_clock_ladder.py) already checks `measured sync` and the checksum). Result: the first rung that fails the hard load.
2. **Rule.** Ship clock ≤ first-failing-rung / 1.25 (the project's timeout multiplier applied to silicon). For `sync` 60 that means passing `cpu stress` at 75 on a throwaway build. Re-run the ladder when the fabric changes by more than the mapper noise band (±194 COMB) or when a new window is added to the decoder.
3. **nextpnr's role afterwards.** Regression indicator only: worst PASS/FAIL over N seeds on the *same* netlist (`pnr_noise.py`, `soc_occupancy_timing.py sweep`). Never a single build, never two different netlists.
4. **Also measure once:** a temperature soak at the shipped clock (`DTR` + `cpu stress` under the `#430` idle queue), to turn the assumed derating into a number.

## 6. What to change and what to stop worrying about

- Stop: reading one nextpnr verdict as a property of the design; comparing two builds of two netlists; chasing `clk` FAIL with CPU options ([#495](https://github.com/awtoau/cynthion-workspace/issues/495)).
- Change, in order of leverage:
  0. `--router router1` for the default build (`soc_run.py --router router1` exists; flipping `NEXTPNR_OPTS` in `fast_build_env.py` is Dan's call: it costs 35-430 s of router time per build against router2's 30-48 s). Or router2 with `--tmg-ripup` once it has 8 seeds behind it. Either way, keep the 60 s stall guard.
  1. The BIST peripheral owns both FAIL verdicts on every seed. Register its CSR read path (a one-cycle `r_stb`/`r_data` stage in `BistCsrTransport`, or a registered `WishboneCSRBridge` ack for that window: BIST register reads are not latency-sensitive) and register `checked_against` / the `psram.read_data` compare in the engine (`hyperram_ceiling_top.py:620,962`). Prove it with `pnr_noise.py --runs 8` on the new netlist: the test is PASS 8/8 on both clocks, not a number.
  2. Fabric-wide, if (1) leaves `clk` marginal: a register stage between `arbiter.grant`/`bus__adr` and the 15 bridges, or a two-level decoder.
  3. Drop `--parallel-refine` for any build whose log is read; pass `--seed` and report the worst of N.
- Keep: `--timing-allow-fail` (a FAIL must still produce a bitstream to test), the constraint set as is, `--12k --speed 8`.
- Budget used: 23 re-placements of one netlist (8 seeds + 15 lever runs), no synthesis, no board.

## Reproduce

    cp tmp/awto_soc/build/ck80-dqs0-sync60/top.{json,lpf} tmp/timing/netlist/
    ./scripts/soc/pnr_noise.py --json tmp/timing/netlist/top.json --lpf tmp/timing/netlist/top.lpf \
        --runs 8 --jobs 4 --opts "--threads 6 --router router2 --timing-allow-fail" \
        --outdir tmp/timing/seeds --name seeds8
    ./scripts/soc/timing_survey.py tmp/timing/seeds tmp/awto_soc/build
    ./scripts/tools/watch_pnr_runs.py --log tmp/logs/seeds8.log --runs tmp/timing/seeds   # 60 s stall watch

Logs: `tmp/logs/seeds8.log`, `tmp/logs/timing_survey*.json`, per-run `top.tim` under `tmp/timing/`.
