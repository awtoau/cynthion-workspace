# Can a DQS-group pad be a strobe AND a fabric-readable level?

**Question.** HyperBus variable latency needs the controller to read RWDS as a
LEVEL during the CA phase. On the DQS read path RWDS is also the strobe, wired
to `DQSBUFM.DQSI`. nextpnr-ecp5 refuses both uses at once:

    // nextpnr-ecp5 pack_dqsbuf
    if (pio == nullptr || ci->ports.at(id_DQSI).net->users.entries() > 1)
        log_error("DQSBUFM '%s' DQSI input must be connected only to a top
                   level input\n", ...);

One sink on the DQSI net. Is that the silicon, or the packer? Diamond is the
vendor's own tool and is the oracle, as it was for `usrmclk_oddr/`.

**Designs.** `top.v` and `control.v` are identical but for three lines:

    reg level;
    always @(posedge sclk) level <= rwds_in;   // the second sink
    assign level_out = level;

`control.v` has one sink on DQSI, which nextpnr already allows. If the control
fails, nothing here is about DQSI.

## Result: Diamond's mapper ACCEPTS it

`map` reports **0 errors for both**, run as

    python3 scripts/diamond/flow.py --mode lse \
      --verilog scripts/diamond/designs/dqsi_fanout/top.v \
      --lpf     scripts/diamond/designs/dqsi_fanout/top.lpf \
      --outdir  tmp/diamond/dqsi_top --name dqsi_top

`map` is the stage that enforces connection legality, and it is DISCRIMINATING
-- it rejected four separate illegal structures while this harness was built:

  * `RDPNTR2 ... can only drive the RDPNTR2 pin of an IDDRDQ component`
  * `The ECLK pin of DQSBUFM ... should be driven by ECLKSYNC component`
  * `IDDR component ... SCLK signal must be driven by CLKDIV component output
    CDIVX, otherwise ECLK/SCLK domain crossing cannot be guaranteed`
  * `EHXPLLL ... CLKOP with -270 degree phase shift should not be used as the
    feedback signal`

It did not reject the second sink on DQSI.

## What this does NOT establish

`par` does not complete, for a reason unrelated to the question and IDENTICAL in
both designs: this harness's scratch clock pin.

    ERROR - par: Unable to reach CIB within 1 routing segment for clock 'clk_c'
                 with driver 'clk' at site 'C3/PL2C'.

The DDR interface must sit in one bank (so the pins are the board's real
HyperRAM sites) while the PLL wants a dedicated clock input, and those pull in
opposite directions on a design this small. So: the vendor's legality checker
permits the structure; a routed bitstream has not been produced either way.

**Bottom line.** nextpnr's rule is not corroborated by Diamond's mapper. That
makes it a packer restriction to be challenged rather than a silicon fact to be
designed around. #540.
