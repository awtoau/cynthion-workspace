# yosys 0.70 / nextpnr 0.11 upgrade: gains, blocker, workaround, size

Status: **default is yosys main `8f3cc3d0c` plus our abc9 fix, nextpnr 0.11.1, no `M`/`CE` workaround** (`CYNTHION_TOOLCHAIN=yosys070`). Tracked in [#602](https://github.com/awtoau/cynthion-workspace/issues/602) and [#605](https://github.com/awtoau/cynthion-workspace/issues/605). Measured 2026-11-05 on the SoC `top.il` (ck180), host `selwyn`, `YOSYS_MAX_THREADS=1`.

## Board result (r1.4, seed 6, default synth options)
- yosys 0.70+329 + `trellis_ff_without_m.v` + nextpnr 0.10-74: `soc_test --board` 131/131, `hyperram_board_test` 13/13 at 343.5 / 340.1 / 341.8 MB/s. Build 155 s (yosys ~37 s).
- Select with one setting, `CYNTHION_TOOLCHAIN` (read by `soc_run`; an explicitly set variable still wins):
  - `yosys070` (default): yosys `8f3cc3d0c` + [`patches/yosys-abc9-unlisted-ports-fix.patch`](../patches/yosys-abc9-unlisted-ports-fix.patch), installed to `~/opt/fpga-main` by [`scripts/yosys/build_patched_yosys.py`](../scripts/yosys/build_patched_yosys.py) (soc_run exits with that command if it is missing); `ecppack` and nextpnr 0.11.1 from `~/opt/oss-cad-suite-20261104`; `-nowidelut`; no workaround. Default build, board: `soc_test --board` 132/132, `hyperram_board_test` 13/13 (20781 COMB).
  - `yosys070-widelut`: the same with yosys's default (wide-LUT) mapping.
  - The old suite (2026-05-22: yosys 0.65+57, nextpnr 0.10-74) and its profiles `stable`, `yosys070-nextpnr010`, `yosys070-stock` were removed 2026-11-06; copy in `/mnt/2tb/wastebasket/oss-cad-suite-20260522-*`.
- `-nowidelut` on the board (yosys 0.70, seed 6): `soc_test --board` 132/132, `hyperram_board_test` 13/13, COMB 20893 (86.0%).
- The bitstream cache key includes the yosys and nextpnr versions and the synth / placer options ([#603](https://github.com/awtoau/cynthion-workspace/issues/603)), so a toolchain change builds instead of reusing another toolchain's bitstream.
- nextpnr 0.11.1 passes the board suites and is the default. The Apollo hang is not its fault: `isolate B` (old yosys netlist placed by 0.11.1) ended its configure with a 692-byte final chunk, and Apollo's JTAG tail stored its TDO byte 179 bytes past the end of its `comms` buffer, onto the USB control struct's callback pointer ([`board-usb-recovery.md`](board-usb-recovery.md)). Fixed in Apollo (`repos/apollo` `a476c69`); `isolate B` now loads and passes. Re-evaluated 2026-11-05 on the fixed Apollo (`CYNTHION_NEXTPNR_ECP5=~/opt/oss-cad-suite-20261104/oss-cad-suite/bin/nextpnr-ecp5`, yosys 0.70+329, `-nowidelut`, default seed): nextpnr-0.11.1-86 builds 20891 COMB (0.10-74: 20893), loads, `soc_test --board` 132/132, `hyperram_board_test` 13/13.
- New-yosys images built with 0.11.1 were silent, but those carried the first version of the techmap (below), so they say nothing about 0.11.1.

- yosys main `8f3cc3d0c` (2026-11-04, 7 commits past `b8d84f823`; built from a worktree, `YOSYS_WITHOUT_SLANG=ON`), 2026-11-05: the M repro still gives `"M": ["0"]` on `TRELLIS_FF`, so the workaround is still needed; with it and nextpnr 0.11.1 the SoC builds (20891 COMB), loads, `soc_test --board` 132/132, `hyperram_board_test` 13/13. Not in the default profile: the suite's yosys is `b8d84f823`.

## Toolchains
- Active: `~/opt/oss-cad-suite` — yosys 0.65+57, nextpnr 0.10-74.
- Candidate: `~/opt/oss-cad-suite-20261104` ([release](https://github.com/YosysHQ/oss-cad-suite-build/releases/tag/2026-11-04)) — yosys 0.70+329 (`b8d84f823`), nextpnr 0.11.1-86. Side by side, nothing replaced.

## Gain
- yosys step with `autoname`: 104.5 s -> 37 s. `autoname` itself 53.3 s -> 0.12 s (rewritten in 0.70), so the `autoname` skip in [`fast_build_env.py`](../scripts/fast_build_env.py) is not needed there.

## Blocker: abc9 ties unlisted inputs of async flip-flops to 0 (`M`, `CE`)
- Cause: yosys `ae085b637` "abc_ops: use all ports, including ports the instance doesn't list" (2026-11-02, PR #6437, fixes #6427), in 0.70+329 and main `8f3cc3d0c`. `abc9_ops prep_bypass` now wires every module port of an `abc9_bypass` cell; a port the instance does not list comes back from ABC as constant 0. Reverting that commit makes the test below pass.
- Not specific to `M` or to `TRELLIS_FF`: any input the instance leaves unconnected. On `TRELLIS_FF` that is `M` (unused unless `LSRMODE=="PRLD"`) and `CE` when `CEMUX=="1"` (the flop has no enable).
- Effects: `M` connected to `1'b0` on every async FF (576 here; 0 in 0.65+57), and nextpnr 0.10-74 / 0.11.1 fail in the packer: `net '$PACKER_GND_NET' user port '<ff>.M' connected to incorrect net` (nextpnr `ecp5/pack.cc` drops a floating `M` but not a constant one). `CE` tied to 0 froze flip-flops: the silent CPU on the board.
- The async flop is not an ABC box here: `abc9_ops` strips `abc9_box` from a derived box containing a builtin FF and marks it `abc9_bypass` (`abc9_ops.cc:241-246`); an earlier draft of this report said otherwise.
- Workaround in the flow: [`scripts/yosys/trellis_ff_without_m.v`](../scripts/yosys/trellis_ff_without_m.v), after synthesis. To enable: `AMARANTH_script_after_synth` gets `techmap -max_iter 1 -map <path>` (append to the replay lines in `fast_build_env.py`). It must connect only the ports the original cell had: `CE` is absent when `CEMUX` is `"1"`. Covered by [`tests/test_trellis_ff_techmap.py`](../tests/test_trellis_ff_techmap.py).
- yosys 0.70 also drops the net names `car.clk_sync` and `hyperram.pll.clk_hr` (aliases merged), so nextpnr ignored their clock constraints; the clock signals carry `keep`, and `soc_run` refuses a build with a dropped constraint.
- Parameters in that file must not carry a `[127:0]` range: it turns `CEMUX="1"` into the integer 49 in the JSON.
- Source fix, [`patches/yosys-abc9-unlisted-ports-fix.patch`](../patches/yosys-abc9-unlisted-ports-fix.patch): `techmap_autopurge` on every input port of the `prep_bypass` template module, so a port the instance leaves unconnected is dropped by techmap. Test: [`patches/yosys-abc9-unlisted-ports-test.patch`](../patches/yosys-abc9-unlisted-ports-test.patch) (`tests/arch/ecp5/trellis_ff_unused_ports.ys`).
- An earlier source attempt was dropped: skipping box inputs that nothing drives in `abc_ops_reintegrate.cc`. By reintegration time ABC has already absorbed the logic feeding most pins, so "undriven" cannot be decided there.
- Smaller candidates tried: `techmap_autopurge` on `M` only (in `common_sim.vh`) leaves `CE` tied to 0 and the image is silent on the board.

### Why yosys's tests pass
- `tests/arch/ecp5/dffs.ys` and `adffs.ys` assert `equiv_opt` equivalence against the sim model, cell counts and cell types. A tied-off `M` or `CE` is simulation-identical here (the model ignores `M` unless PRLD and `CE` when `CEMUX=="1"`), and port connectivity is never checked.
- The yosys suite has no place-and-route step, so nextpnr's packer never sees the netlist.
- Run on `8f3cc3d0c` both pass; the new test fails there (`connect \M 1'0` on 3 flops, `connect \CE 1'0` on 2) and passes on 0.65+57.

### Results of the fix (2026-11-05, yosys `8f3cc3d0c` + the patch, nextpnr 0.11.1, no workaround)
- New test passes; 26 ECP5, 18 gowin, 14 nexus, 10 machxo2, 12 intel_alm, 26 ice40, 11 anlogic arch tests and 5 abc9 `various` tests pass. `arch/xilinx` `abc9_dff` and `dsp_abc9` fail identically with and without the patch (an unlisted `ABC: Warning: The network is combinational`), not investigated.
- SoC build (seed 11, 20781 COMB): loads, `soc_test --board` 132/132, `hyperram_board_test` 13/13.
- The cache key includes a SHA-256 of the yosys and nextpnr binaries, so a locally patched yosys builds instead of reusing another image (two earlier runs hit a stale entry); [`tests/test_gateware_digest_toolchain.py`](../tests/test_gateware_digest_toolchain.py).

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

**Title:** `abc9`: input ports an `abc9_bypass` cell instance leaves unconnected come back tied to 0 (regression from #6437; breaks `synth_ecp5` -> nextpnr)

**Versions:** Yosys 0.70+329 (`b8d84f823`) and main `8f3cc3d0c`; regression from `ae085b637` (#6437). Yosys 0.65+57 is not affected. nextpnr-ecp5 0.11.1-86 and 0.10-74.

**Repro** (`t.v`):
```verilog
module t(input clk, input rst_n, input ce, input d, output reg q, output reg r);
  always @(posedge clk or negedge rst_n) if (!rst_n) q <= 1'b0; else q <= d;
  always @(posedge clk or negedge rst_n) if (!rst_n) r <= 1'b0; else if (ce) r <= d;
endmodule
```
```
yosys -p "read_verilog t.v; synth_ecp5 -top t; dump t:TRELLIS_FF"
```
- 0.70+329 / main: every `TRELLIS_FF` has `connect \M 1'0`, and the flop without an enable also `connect \CE 1'0`.
- 0.65+57: neither.
- Reverting `ae085b637` removes both.

**Cause:** `prep_bypass` (`passes/techmap/abc9_ops.cc`) now sets every module port on the replaced cell (`for (auto port_name : inst_module->ports)`), where it used the first instance's `connections()`. The techmap leaves a port the instance does not list undriven, `write_xaiger2` buf-normalisation turns undriven bits into constant 0, and `abc_ops_reintegrate` writes the result back to the port.

**Impact:** nextpnr-ecp5 stops in packing with `user port '<ff>.M' connected to incorrect net '$PACKER_GND_NET'`; a constant-0 `CE` on a flop that has no enable freezes it (found as a silent CPU on hardware).

**Fix proposed:** mark the template module's input ports `techmap_autopurge` (one `set_bool_attribute` in `prep_bypass`), plus the test `tests/arch/ecp5/trellis_ff_unused_ports.ys`. It keeps the behaviour #6437 wanted for ports an instance does connect.

**Also:** nextpnr `ecp5/pack.cc` drops a floating `M` but not one tied to a constant; accepting a constant `M` would be a cheap independent robustness fix.
