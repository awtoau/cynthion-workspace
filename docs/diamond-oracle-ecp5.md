# Diamond as an ECP5 oracle

Lattice Diamond is an independent implementation of every stage the open flow
runs. That makes it useful for something more specific than "should we switch":
it can say **where** the open flow loses, so the gap can be closed in yosys or
nextpnr rather than worked around.

This is the same method that produced the nextpnr-machxo2 IOLOGIC work. See
`/mnt/2tb/git/pluribus/docs/upstream-contributions.md` and
`docs/diamond-re-oracle.md` for the MachXO2 precedent.

The target is **LFE5U-12F-8CABGA256** -- 24288 LUT4, 56 DP16KD, 28 MULT18X18D.
That part is the binding constraint on the whole project, so every measurement
uses it; a bigger part would make the numbers meaningless.

## The noise floor comes first

Placement is stochastic. Before any cross-toolchain difference can be called a
finding, we need to know how large a difference the tool produces *against
itself*.

`./scripts/pnr_noise.py` runs one fixed netlist through nextpnr several times
with different seeds. On the GSG analyzer, four seeds:

| metric | min | max | spread |
|---|---|---|---|
| TRELLIS_COMB | 8191 | 8191 | **0** |
| TRELLIS_FF | 2755 | 2755 | **0** |
| DP16KD | 9 | 9 | **0** |
| MULT18X18D | 0 | 0 | **0** |
| Fmax `$glbnet$clk` | 126.25 | 132.80 | **6.55 MHz (5.2%)** |
| Fmax `aux_phy_0__clk__o` | 82.24 | 87.87 | **5.63 MHz (6.8%)** |

Two consequences, and they set the rules for reading everything below:

- **Utilisation is deterministic.** Packing does not depend on the seed, so
  *any* LUT/FF/BRAM difference against Diamond is real signal, however small.
- **Fmax is not.** A single run's Fmax carries roughly +/-6% of noise, so a
  Diamond-versus-nextpnr frequency gap smaller than about 7% is not a result.
  This is why the open-flow numbers were obtained by binary search on `--freq`
  rather than read off one relaxed run, and why any Diamond Fmax must be
  compared against a constrained run, not an unconstrained one.

## The three configurations

The comparison is only informative if it can attribute a difference to a stage.
Three runs do that:

| configuration | synthesis | place & route | isolates |
|---|---|---|---|
| open | yosys `synth_ecp5` | nextpnr-ecp5 | baseline |
| `--mode lse` | Diamond LSE | Diamond map/par | whole toolchain |
| `--mode yosys` | yosys `synth_ecp5` | Diamond map/par | **place & route only** |

The third is the one that splits the problem in half. If Diamond wins in `lse`
but not in `yosys`, the gap is synthesis and the fix belongs in yosys. If it
wins in `yosys` too, the gap is in place-and-route and belongs in nextpnr.

## Getting the designs into Diamond

The designs are Amaranth, which normally drives the open flow end to end.
`./scripts/emit_verilog.py` stops it one step earlier and reuses the `.il` that
the open-flow build already wrote, so both toolchains start from
byte-identical RTL. Re-elaborating would risk a different result from a
different library version and quietly invalidate the comparison.

Two forms come out, and each hit a real obstacle worth recording:

**Behavioural** (for LSE). `memory_collect` leaves `$mem_v2` cells that
`write_verilog` emits as instantiations of a module that does not exist.
Diamond stops with

    ERROR - synthesis: logical block 'analyzer/clk_I_0' with type
    'ClockedWritePort_16_1_4095_0_15_0' is unexpanded.

`memory_map` instead lowers each memory to a plain reg array, which is the form
a vendor synthesiser is built to infer block RAM from. This is the correct
comparison -- it lets Diamond apply its own inference rules rather than
inheriting yosys's -- but it does mean **BRAM counts in `lse` mode measure
Diamond's inference, and a low BRAM count there means Diamond declined to
infer, not that the design shrank.** Check DP16KD against the open flow before
reading anything else in that mode.

**EDIF** (for `--mode yosys`). `ngdbuild` reads `.ngo`/`.edif`, not Verilog, so
the structural netlist goes out as EDIF via `write_edif` and in through
`edif2ngd`. Feeding structural Verilog back through LSE would let LSE
re-synthesise it and destroy the separation the mode exists to create.

## Cell-level accounting

Totals hide exactly the thing worth finding. nextpnr reports a single
`TRELLIS_COMB` figure, but the yosys netlist that produced it decomposes into:

| primitive | count |
|---|---|
| LUT4 | 5635 |
| PFUMX | 900 |
| CCU2C | 895 |
| L6MUX21 | 244 |
| TRELLIS_FF | 2755 |
| TRELLIS_IO | 119 |
| ODDRX1F | 10 |
| IDDRX1F | 9 |
| DP16KD | 9 |
| EHXPLLL | 1 |

If Diamond reaches for hard blocks the open flow ignored -- DSPs, wide LUT
modes, distributed RAM, IOLOGIC -- that is a nameable missing inference in
yosys, and this breakdown is where it shows up.

## Bitstream options the open flow does not expose

`~/lscc/diamond/3.14/ispfpga/ep5c00/data/bitgen.usg` documents generator
options absent from `ecppack`: `CfgMode` (Disable/Flowthrough/Bypass), `RamCfg`
(Reset/NoReset), the phase controls `DONEPHASE`/`GOEPHASE`/`GSRPHASE`/
`GWDPHASE`, `ES`, and `-m` for mask and readback files. None affect
utilisation or Fmax, so they are not part of the comparison, but `-m` in
particular has no open-flow equivalent at all.

## Reproducing

    ./scripts/pnr_noise.py --json <top.json> --lpf <top.lpf> --runs 4 --freq 120
    ./scripts/emit_verilog.py --il <top.il> --outdir tmp/diamond/<design>
    ./scripts/diamond_flow.py --verilog <behavioural.v> --lpf <top.lpf> \
        --mode lse --outdir tmp/diamond/<design>_lse
    ./scripts/diamond_flow.py --verilog <structural.edf> --lpf <top.lpf> \
        --mode yosys --outdir tmp/diamond/<design>_yospar

Logs land in `./tmp/logs/`. Diamond's environment is built from scratch inside
`diamond_flow.py` rather than inherited, because the oss-cad-suite environment
the open flow needs sets `PYTHONHOME` and prepends its own libstdc++, which
stops Diamond's engines loading their shared objects.
