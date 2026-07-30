# yosys `write_edif` emits `$scopeinfo` cells as instances of undeclared cells

Found while routing a yosys-synthesised netlist into Lattice Diamond's
place-and-route, to separate synthesis from PAR in the ECP5 comparison
(`docs/diamond-oracle-ecp5.md`). Not specific to that experiment: it breaks any
yosys -> Diamond/vendor EDIF handoff on a hierarchical design.

## Symptom

    ERROR - edif2ngd: Reference to an unknown cell id00013.

`edif2ngd` (Diamond 3.14) refuses the netlist. In the GSG analyzer the
undeclared `id00013` is referenced **246 times** and declared nowhere.

## Minimal reproducer

    module sub(input a, output b); assign b = ~a; endmodule
    module top(input clk, input a, output reg b);
      wire w; sub u(.a(a), .b(w));
      always @(posedge clk) b <= w;
    endmodule

    yosys -p "read_verilog mini.v; synth_ecp5 -top top; write_edif -attrprop mini.edf"
    grep -n id00008 mini.edf

Yields exactly one hit -- a reference, with no matching declaration:

    (instance u
      (viewRef VIEW_NETLIST (cellRef id00008))
      (property TYPE (string "module"))
      (property module (string "sub"))
      (property module_src (string "mini.v:1.1-1.56"))
      (property cell_src (string "mini.v:3.15-3.30"))
      (property cell_module_not_derived (integer 1)))

The `(rename idNNNNN ...)` sequence skips that id: `id00007` and `id00009`
exist, `id00008` does not.

Reproduced on Yosys 0.65+57 (git sha1 9d0cdb855).

## Root cause: `$scopeinfo`

The undeclared cells are **`$scopeinfo`** cells. `yosys ... stat` after
`synth_ecp5; flatten; opt_clean` on the analyzer reports

     10856 cells
       246   $scopeinfo
       895   CCU2C
      5635   LUT4
      ...

-- exactly the 246 undeclared references.

`$scopeinfo` is a debug-only annotation that records where a module boundary
used to be, so that source-level names survive flattening. It has no ports and
no logic. It is not a design element and has no meaning to any EDIF consumer,
but `write_edif` emits an instance for each one anyway, referencing a cell id
that it never declares (because there is no module to declare).

The result is a syntactically well-formed EDIF that is referentially broken.
yosys does not warn; the failure only appears in the consuming tool.

Note that `flatten` alone does **not** fix this -- flattening is what creates
the `$scopeinfo` cells in the first place. They must be deleted explicitly.

## Workaround

    read_rtlil top.il
    synth_ecp5 -top top
    flatten
    delete t:$scopeinfo
    opt_clean
    write_edif -attrprop out.edf

This is what `./scripts/emit_verilog.py` does. Deleting `$scopeinfo` changes
nothing electrically -- the cells carry no connectivity -- it only discards
the source-level scope annotation.

## Where a fix belongs

`backends/edif/edif.cc` in yosys, in the loop that walks `module->cells()`.

The fix is a one-line skip: `$scopeinfo` should never be emitted, alongside
whatever handling already exists for other internal `$`-prefixed cells. It is
metadata, not a netlist element, and no EDIF consumer can do anything with it.

A defensive second change worth making in the same place: if a cell's type
resolves to a module that is not written into the EDIF library, `write_edif`
should error or warn rather than emit a dangling `cellRef`. The current
behaviour produces a file that passes as EDIF and fails only in the vendor
tool, which is a slow way to find a one-line bug.
