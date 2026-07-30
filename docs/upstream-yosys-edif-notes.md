# yosys -> Lattice Diamond EDIF handoff: three blockers

Found while feeding a yosys-synthesised netlist into Diamond's place-and-route
to separate synthesis from PAR in the ECP5 comparison
(`docs/diamond-oracle-ecp5.md`). Each has a minimal reproducer and a
workaround; two are arguably yosys bugs and one is an Amaranth bug.

The working recipe that results:

    read_verilog <any pre-generated cores>
    read_rtlil top.il
    synth_ecp5 -top top
    flatten
    delete t:$scopeinfo     # blocker 1
    splitnets -ports        # blocker 2
    opt_clean
    write_edif out.edf      # NOT -attrprop

plus rewriting `FREQUENCY PORT ... HZ` to `MHZ` in the .lpf (blocker 3).

## 1. `$scopeinfo` emitted as instances of undeclared cells

    ERROR - edif2ngd: Reference to an unknown cell id00013.

Covered in full in `docs/upstream-yosys-write-edif-hierarchy.md`. Summary:
`flatten` leaves one `$scopeinfo` debug-annotation cell per former module
boundary (246 in the GSG analyzer), and `write_edif` emits an instance for
each while never declaring a corresponding cell. Fix belongs in
`backends/edif/edif.cc` -- skip `$scopeinfo`, and warn on any dangling
`cellRef` rather than emitting it.

## 2. Vector ports become multiple pins with one name

    ERROR - edif2ngd: block '': multiple pins named 'd'

### Minimal reproducer

    module top(inout [7:0] d, input clk, input oe);
      reg [7:0] q;
      always @(posedge clk) q <= d;
      assign d = oe ? q : 8'bzzzzzzzz;
    endmodule

    yosys -p "read_verilog minibus.v; synth_ecp5 -top top; write_edif minibus.edf"
    edif2ngd -l ECP5U -d LFE5U-12F minibus.edf minibus.ngo

yosys writes the port as an EDIF array:

    (port (array d 8) (direction INOUT))

which is valid EDIF, but Diamond's NGD model has no vector pin. `edif2ngd`
expands the array into eight pins that all retain the base name `d`, then its
own design check rejects the duplicates.

### Workaround

`splitnets -ports` before `write_edif` turns it into eight scalar ports:

    (port (rename id00004 "d[7]") (direction INOUT))
    ...
    (port (rename id00011 "d[0]") (direction INOUT))

`edif2ngd` accepts this. The bracketed names also match what Amaranth's `.lpf`
already uses (`LOCATE COMP "d[0]" SITE ...`), so pin constraints keep binding.

### Assessment

Arguably Diamond's bug rather than yosys's -- EDIF arrays are legal and other
consumers accept them. But since Diamond is the dominant consumer of ECP5
EDIF, a note in the `write_edif` documentation recommending `splitnets -ports`
for Lattice targets would save the next person the same afternoon. A
`-splitports` convenience flag would be better still.

## 3. Amaranth writes `FREQUENCY PORT ... HZ`, which Diamond rejects

    WARNING - map: top.lpf(242): Syntax error in "FREQUENCY PORT
    "clk_60MHz_0__io" 60000000.0 HZ;": error on token "HZ".
    ERROR - map: There are syntax errors in the preference file, "top.lpf".

Amaranth's LPF writer emits

    FREQUENCY PORT "clk_60MHz_0__io" 60000000.0 HZ;

nextpnr accepts `HZ` and the float. Diamond's `map` accepts neither, wanting

    FREQUENCY PORT "clk_60MHz_0__io" 60 MHZ;

This makes an Amaranth-generated `.lpf` non-portable to the vendor tool that
defines the format, which is worth fixing in Amaranth
(`amaranth/vendor/_lattice.py`, the ECP5 LPF emission) regardless of this
experiment -- anyone taking an Amaranth design to Diamond hits it immediately.

`scripts/diamond_flow.py` rewrites the line rather than dropping it. Dropping
it would leave the design unconstrained, and an unconstrained Diamond run
reports whatever frequency it happened to reach rather than one it worked
toward -- not comparable to the open flow's binary-searched Fmax.

## Not a blocker, but noted

`edif2ngd` warns `Unsupported property CEMUX/CLKMUX/LSRMUX/SRMODE found -
ignoring...` for every `TRELLIS_FF`. These are the flip-flop's mux
configuration attributes. Diamond ignores them and re-derives the
configuration from connectivity, so the netlist is still correct, but it does
mean a `--mode yosys` run is not a bit-exact transplant of yosys's packing
decisions -- Diamond re-makes some of them. Worth remembering before
attributing a small difference in that mode purely to placement.
