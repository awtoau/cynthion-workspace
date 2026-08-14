// Can the RWDS pad drive DQSBUFM's DQSI *and* be read as a level in fabric?
//
// nextpnr says no, in `pack_dqsbuf`:
//
//     if (pio == nullptr || ci->ports.at(id_DQSI).net->users.entries() > 1)
//         log_error("DQSBUFM '%s' DQSI input must be connected only to a top
//                    level input\n", ...);
//
// That is a PACKER rule -- one sink on the DQSI net -- and it is not by itself
// evidence about the silicon. It decides whether HyperBus variable latency can
// work on the DQS path at all: the device answers "extra latency or not" by
// driving RWDS during CA, and a controller that cannot see that level cannot
// implement the election (#540).
//
// Diamond is the vendor's own mapper and router. Same question, asked of the
// tool that knows the real device. `usrmclk_oddr/` is the precedent.
//
// Pair this with `control.v`, which is this design MINUS the fabric sample. If
// the control also fails, the instantiation is wrong and neither result is
// about the question.
module top (
    inout  wire rwds,
    inout  wire dq,
    input  wire clk,
    output wire level_out,
    output wire dqs_alive
);
    wire rwds_in;

    // The pad exactly as `hyperram_dqs_phy.py` has it: a bidirectional buffer,
    // held in input mode here, whose O is the level.
    BB rwds_buf (.I(1'b0), .T(1'b1), .O(rwds_in), .B(rwds));

    // ECLK must be a real high-speed clock: `par` refuses ECLKI "driven by
    // general routing", so it comes from a PLL here as it does in the SoC's
    // own clock generator, not from a scratch pin.
    wire pll_eclk, pll_lock;
    EHXPLLL #(
        .CLKI_DIV(1), .CLKFB_DIV(4), .CLKOP_DIV(4),
        // CPHASE/FPHASE explicit: the default leaves CLKOP at -270 degrees and
        // Diamond refuses that as the feedback signal.
        .CLKOP_CPHASE(3), .CLKOP_FPHASE(0),
        .CLKOP_ENABLE("ENABLED"), .FEEDBK_PATH("INT_OP"),
        .OUTDIVIDER_MUXA("DIVA")
    ) pll (
        .CLKI(clk), .CLKFB(1'b0), .RST(1'b0), .STDBY(1'b0),
        .PHASESEL0(1'b0), .PHASESEL1(1'b0), .PHASEDIR(1'b0),
        .PHASESTEP(1'b0), .PHASELOADREG(1'b0), .PLLWAKESYNC(1'b0),
        .ENCLKOP(1'b0),
        .CLKOP(pll_eclk), .LOCK(pll_lock)
    );

    // Diamond requires DQSBUFM.ECLK to come from an ECLKSYNCB, not a raw net.
    wire eclk_sync;
    ECLKSYNCB eclksync (.ECLKI(pll_eclk), .STOP(1'b0), .ECLKO(eclk_sync));

    // And SCLK must come from a CLKDIVF's CDIVX, "otherwise ECLK/SCLK domain
    // crossing cannot be guaranteed" -- the same shape the real PHY's clock
    // generator provides.
    wire sclk;
    CLKDIVF clkdiv (.CLKI(eclk_sync), .RST(1'b0), .ALIGNWD(1'b0), .CDIVX(sclk));

    wire dqsr90, dqsw, dqsw270, datavalid, burstdet;
    // RDPNTR/WRPNTR go to the IDDRX2DQA and NOWHERE else: Diamond rejects
    // them reaching fabric -- "can only drive the ... pin of an IDDRDQ
    // component" -- which the control caught before it could be mistaken for
    // an answer about DQSI.
    wire rdpntr0, rdpntr1, rdpntr2, wrpntr0, wrpntr1, wrpntr2;

    DQSBUFM dqs (
        .SCLK        (sclk),
        .ECLK        (eclk_sync),
        .RST         (1'b0),
        .DQSI        (rwds_in),
        .DDRDEL      (1'b0),
        .PAUSE       (1'b0),
        .READ0       (1'b0),
        .READ1       (1'b0),
        .READCLKSEL0 (1'b0),
        .READCLKSEL1 (1'b1),
        .READCLKSEL2 (1'b0),
        .DYNDELAY0   (1'b0), .DYNDELAY1 (1'b0), .DYNDELAY2 (1'b0),
        .DYNDELAY3   (1'b0), .DYNDELAY4 (1'b0), .DYNDELAY5 (1'b0),
        .DQSR90      (dqsr90),
        .DQSW        (dqsw),
        .DQSW270     (dqsw270),
        .RDPNTR0     (rdpntr0), .RDPNTR1 (rdpntr1), .RDPNTR2 (rdpntr2),
        .WRPNTR0     (wrpntr0), .WRPNTR1 (wrpntr1), .WRPNTR2 (wrpntr2),
        .DATAVALID   (datavalid),
        .BURSTDET    (burstdet)
    );

    // THE QUESTION: the same pad input, sampled in fabric as a plain level.
    reg level;
    always @(posedge sclk) level <= rwds_in;

    assign level_out = level;
    // A REAL DQ PATH. `par` refuses a DQSBUFM that serves no DQ pins -- "There
    // is no DQ pins driven by DQSBUF comp" -- so without this the run stops
    // before it reaches the question.
    wire dq_in;
    BB dq_buf (.I(1'b0), .T(1'b1), .O(dq_in), .B(dq));
    wire q0, q1, q2, q3;
    IDDRX2DQA dq_iddr (
        .D       (dq_in),
        .ECLK    (eclk_sync),
        .SCLK    (sclk),
        .DQSR90  (dqsr90),
        .RST     (1'b0),
        .RDPNTR0 (rdpntr0), .RDPNTR1 (rdpntr1), .RDPNTR2 (rdpntr2),
        .WRPNTR0 (wrpntr0), .WRPNTR1 (wrpntr1), .WRPNTR2 (wrpntr2),
        .Q0 (q0), .Q1 (q1), .Q2 (q2), .Q3 (q3)
    );

    // DQSR90/DQSW/DQSW270 are clocks and do not belong in a LUT.
    assign dqs_alive = datavalid ^ burstdet ^ pll_lock ^ q0 ^ q1 ^ q2 ^ q3;
endmodule
