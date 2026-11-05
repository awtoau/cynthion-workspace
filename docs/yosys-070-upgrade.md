# yosys 0.70 / nextpnr 0.11 upgrade: gains, blocker, workaround, size

Status: **works on the board with yosys 0.70+329, the `M` workaround and nextpnr 0.11.1; the default.** Tracked in [#602](https://github.com/awtoau/cynthion-workspace/issues/602). Measured 2026-11-05 on the SoC `top.il` (ck180), host `selwyn`, `YOSYS_MAX_THREADS=1`.

## Board result (r1.4, seed 6, default synth options)
- yosys 0.70+329 + `trellis_ff_without_m.v` + nextpnr 0.10-74: `soc_test --board` 131/131, `hyperram_board_test` 13/13 at 343.5 / 340.1 / 341.8 MB/s. Build 155 s (yosys ~37 s).
- Select with one setting, `CYNTHION_TOOLCHAIN` (read by `soc_run`; an explicitly set variable still wins):
  - `yosys070` (default): yosys 0.70+329 and `ecppack` from `~/opt/oss-cad-suite-20261104`, that suite's own nextpnr 0.11.1, `-nowidelut`, the `M` workaround.
  - `yosys070-widelut`: the same with yosys's default (wide-LUT) mapping.
  - `stable`: the installed suite, no workaround.
- `-nowidelut` on the board (yosys 0.70, seed 6): `soc_test --board` 132/132, `hyperram_board_test` 13/13, COMB 20893 (86.0%).
- The bitstream cache key includes the yosys and nextpnr versions and the synth / placer options ([#603](https://github.com/awtoau/cynthion-workspace/issues/603)), so a toolchain change builds instead of reusing another toolchain's bitstream.
- nextpnr 0.11.1 passes the board suites and is the default (`yosys070-nextpnr010` keeps 0.10-74). The Apollo hang is not its fault: `isolate B` (old yosys netlist placed by 0.11.1) ended its configure with a 692-byte final chunk, and Apollo's JTAG tail stored its TDO byte 179 bytes past the end of its `comms` buffer, onto the USB control struct's callback pointer ([`board-usb-recovery.md`](board-usb-recovery.md)). Fixed in Apollo (`repos/apollo` `a476c69`); `isolate B` now loads and passes. Re-evaluated 2026-11-05 on the fixed Apollo (`CYNTHION_NEXTPNR_ECP5=~/opt/oss-cad-suite-20261104/oss-cad-suite/bin/nextpnr-ecp5`, yosys 0.70+329, `-nowidelut`, default seed): nextpnr-0.11.1-86 builds 20891 COMB (0.10-74: 20893), loads, `soc_test --board` 132/132, `hyperram_board_test` 13/13.
- New-yosys images built with 0.11.1 were silent, but those carried the first version of the techmap (below), so they say nothing about 0.11.1.

- yosys main `8f3cc3d0c` (2026-11-04, 7 commits past `b8d84f823`; built from a worktree, `YOSYS_WITHOUT_SLANG=ON`), 2026-11-05: the M repro still gives `"M": ["0"]` on `TRELLIS_FF`, so the workaround is still needed; with it and nextpnr 0.11.1 the SoC builds (20891 COMB), loads, `soc_test --board` 132/132, `hyperram_board_test` 13/13. Not in the default profile: the suite's yosys is `b8d84f823`.

## Toolchains
- Active: `~/opt/oss-cad-suite` — yosys 0.65+57, nextpnr 0.10-74.
- Candidate: `~/opt/oss-cad-suite-20261104` ([release](https://github.com/YosysHQ/oss-cad-suite-build/releases/tag/2026-11-04)) — yosys 0.70+329 (`b8d84f823`), nextpnr 0.11.1-86. Side by side, nothing replaced.

## Gain
- yosys step with `autoname`: 104.5 s -> 37 s. `autoname` itself 53.3 s -> 0.12 s (rewritten in 0.70), so the `autoname` skip in [`fast_build_env.py`](../scripts/fast_build_env.py) is not needed there.

## Blocker: abc9 ties `M` of async flip-flops to 0
- `common_sim.vh:247`: `TRELLIS_FF` with `SRMODE=="ASYNC"` is an `abc9_box`.
- 0.70's abc9 gives every box input pin an AIG output; an unconnected pin comes back as constant 0. Result: `M` connected to `1'b0` on every async FF (576 here; 0 in 0.65+57).
- nextpnr 0.10-74 and 0.11.1 then fail in the packer: `net '$PACKER_GND_NET' user port '<ff>.M' connected to incorrect net`.
- Bisected to the `abc9` call; four-line repro in the upstream draft below.
- Workaround: [`scripts/yosys/trellis_ff_without_m.v`](../scripts/yosys/trellis_ff_without_m.v), after synthesis. To enable: `AMARANTH_script_after_synth` gets `techmap -max_iter 1 -map <path>` (append to the replay lines in `fast_build_env.py`). It must connect only the ports the original cell had: `CE` is absent when `CEMUX` is `"1"`, and tying it to 0 froze 269 flip-flops (silent CPU on the board). Covered by [`tests/test_trellis_ff_techmap.py`](../tests/test_trellis_ff_techmap.py).
- yosys 0.70 also drops the net names `car.clk_sync` and `hyperram.pll.clk_hr` (aliases merged), so nextpnr ignored their clock constraints; the clock signals carry `keep`, and `soc_run` refuses a build with a dropped constraint.
- Parameters in that file must not carry a `[127:0]` range: it turns `CEMUX="1"` into the integer 49 in the JSON.
- A source fix was tried and dropped: skipping box inputs that nothing drives in `abc_ops_reintegrate.cc`. By reintegration time ABC has already absorbed the logic feeding most pins, so "undriven" cannot be decided there; it strips real connections (all flip-flops vanished). Needs the maintainers.

## Size (nextpnr `--pack-only`, TRELLIS_COMB of 24288)

| option | 0.65+57 | 0.70+329 with the `M` fix |
|---|---|---|
| default | 21876 (90.0%) | 22362 (92.1%) |
| `-cmp2softlogic` | 21040 | 21696 |
| `-nowidelut` | 20901 | 20855 |
| both | 20501 | 20412 |

- 0.70 default is +486 COMB, all in wide-LUT mapping (PFUMX 2478 vs 2038, L6MUX21 469 vs 289). LUT4-only mapping is equal.
- No new size switch in 0.70: `-abc2`, `-noabc9`, `-retime` removed (accepted, ignored); `-latches` added. `abc9 -W 300` is a constant.
- Not measured: timing with `-nowidelut` (deeper logic), and any board run with `-nowidelut` or `-cmp2softlogic`.
- Tool: [`scripts/yosys_synth_sweep.py`](../scripts/yosys_synth_sweep.py).

## Upstream report (draft, not posted: needs Dan's go)

**Title:** `synth_ecp5`: abc9 ties the unused `M` input of async `TRELLIS_FF` to 0; nextpnr-ecp5 then rejects the netlist

**Versions:** Yosys 0.70+329 (`b8d84f823`, OSS CAD Suite 2026-11-04); nextpnr-ecp5 0.11.1-86 and 0.10-74. Yosys 0.65+57 is not affected.

**Repro** (`t.v`):
```verilog
module t(input clk, input rst_n, input d, output reg q);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 1'b0; else q <= d;
endmodule
```
```
yosys -p "read_verilog t.v; synth_ecp5 -top t; write_json t.json"
```
- 0.70+329: the `TRELLIS_FF` has `"M": ["0"]` in `connections`.
- 0.65+57: no `M` connection.
- `synth_ecp5 -run :map_luts` first: `M` absent. After `abc9 -W 300`: present. So it is the `abc9` step.

**Cause (from reading the source):** `techlibs/lattice/common_sim.vh` marks `TRELLIS_FF` with `SRMODE=="ASYNC"` as `abc9_box`. The box path gives every box input an AIG output; `abc_ops_reintegrate` sets every box port from ABC's result, so a pin that was never connected comes back as constant 0.

**Impact:** on a ~32k-cell design with 576 async flip-flops, nextpnr-ecp5 (both versions) stops in packing with `net '$PACKER_GND_NET' user port '<ff>.M' connected to incorrect net '...'`. `M` is unused in `ASYNC` mode without `LSRMODE="PRLD"`.

**Workaround:** a `techmap -max_iter 1` that re-instantiates `TRELLIS_FF` without `M` for `SRMODE=="ASYNC" && LSRMODE!="PRLD"` after `synth_ecp5`; the packer then accepts the netlist.

**Expected:** a pin left unconnected stays unconnected after `abc9`, as in 0.65. Possibly nextpnr should also accept a constant on `M`.
