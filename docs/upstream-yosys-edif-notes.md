# yosys -> Lattice Diamond EDIF handoff: three blockers

Found feeding a yosys-synthesised netlist into Diamond place-and-route, to separate synthesis from PAR in the ECP5 comparison (`scripts/diamond/`). Two are arguably yosys bugs, one is an Amaranth bug. Not reported upstream ([No upstreaming](upstream-boundary.md#no-upstreaming)).

Working recipe:

    read_verilog <any pre-generated cores>
    read_rtlil top.il
    synth_ecp5 -top top
    flatten
    delete t:$scopeinfo     # blocker 1
    splitnets -ports        # blocker 2
    opt_clean
    write_edif out.edf      # NOT -attrprop

plus rewrite `FREQUENCY PORT ... HZ` to `MHZ` in the `.lpf` (blocker 3). `./scripts/emit_verilog.py` applies blocker 1's workaround.

## 1. `$scopeinfo` emitted as instances of undeclared cells

    ERROR - edif2ngd: Reference to an unknown cell id00013.

- Breaks any yosys -> vendor EDIF handoff on a hierarchical design. Diamond 3.14 `edif2ngd`.
- GSG analyzer: `id00013` referenced 246 times, declared nowhere = the 246 `$scopeinfo` cells reported by `stat` after `synth_ecp5; flatten; opt_clean` (10856 cells total).
- `$scopeinfo` is a debug-only annotation recording where a module boundary was; no ports, no logic. `write_edif` emits an instance per cell, referencing a cell id it never declares. yosys does not warn.
- `flatten` creates the `$scopeinfo` cells; they must be deleted explicitly (`delete t:$scopeinfo`). Electrically a no-op.
- Reproducer (Yosys 0.65+57, git sha1 9d0cdb855):

      module sub(input a, output b); assign b = ~a; endmodule
      module top(input clk, input a, output reg b);
        wire w; sub u(.a(a), .b(w));
        always @(posedge clk) b <= w;
      endmodule

      yosys -p "read_verilog mini.v; synth_ecp5 -top top; write_edif -attrprop mini.edf"
      grep -n id00008 mini.edf

  One hit: `(instance u (viewRef VIEW_NETLIST (cellRef id00008)) ...)` with no declaration; the `(rename idNNNNN ...)` sequence skips `id00008`.
- Fix belongs in `backends/edif/edif.cc`, in the `module->cells()` loop: skip `$scopeinfo`; also error/warn on a cell type resolving to a module not written to the EDIF library instead of emitting a dangling `cellRef`.

## 2. Vector ports become multiple pins with one name

    ERROR - edif2ngd: block '': multiple pins named 'd'

    module top(inout [7:0] d, input clk, input oe);
      reg [7:0] q;
      always @(posedge clk) q <= d;
      assign d = oe ? q : 8'bzzzzzzzz;
    endmodule

    yosys -p "read_verilog minibus.v; synth_ecp5 -top top; write_edif minibus.edf"
    edif2ngd -l ECP5U -d LFE5U-12F minibus.edf minibus.ngo

- yosys writes `(port (array d 8) (direction INOUT))` (valid EDIF); Diamond's NGD has no vector pin, expands it to eight pins all named `d`, then rejects the duplicates.
- Workaround: `splitnets -ports` before `write_edif` gives `(port (rename id00004 "d[7]") ...)` scalar ports. The bracketed names match Amaranth's `.lpf` (`LOCATE COMP "d[0]" SITE ...`), so pin constraints bind.
- Arguably Diamond's bug. Documentation note for `write_edif` recommending `splitnets -ports` for Lattice targets, or a `-splitports` flag, would help.

## 3. Amaranth writes `FREQUENCY PORT ... HZ`, which Diamond rejects

    WARNING - map: top.lpf(242): Syntax error in "FREQUENCY PORT "clk_60MHz_0__io" 60000000.0 HZ;": error on token "HZ".
    ERROR - map: There are syntax errors in the preference file, "top.lpf".

- nextpnr accepts `HZ` and the float; Diamond `map` wants `FREQUENCY PORT "clk_60MHz_0__io" 60 MHZ;`.
- Fix belongs in Amaranth (`amaranth/vendor/_lattice.py`, ECP5 LPF emission).
- `scripts/diamond/flow.py` rewrites the line. Dropping it leaves the design unconstrained, and an unconstrained Diamond run reports whatever it reached, not comparable to the open flow's binary-searched Fmax.

## Not a blocker

- `edif2ngd` warns `Unsupported property CEMUX/CLKMUX/LSRMUX/SRMODE found - ignoring...` for every `TRELLIS_FF`. Diamond re-derives the flip-flop mux configuration from connectivity: netlist correct, but a `--mode yosys` run is not a bit-exact transplant of yosys's packing. Keep in mind before attributing a small difference purely to placement.
