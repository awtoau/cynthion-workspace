// Can an ODDRX1F drive USRMCLK on an ECP5?
//
// docs/chips/w25q32-config-flash.md says no, on prjtrellis evidence: the CCLK
// site has no DATAMUX_ODDR/IOLDO mux and JA4 carries no global-clock spine
// source. That database is reverse-engineered. Diamond is the vendor's own
// router and knows the real silicon, so it is the oracle for the claim.
module top (
    input  wire clk,
    input  wire en,
    output wire alive
);
    wire sck;

    ODDRX1F sck_oddr (
        .SCLK (clk),
        .RST  (1'b0),
        .D0   (en),
        .D1   (1'b0),
        .Q    (sck)
    );

    USRMCLK mclk (
        .USRMCLKI  (sck),
        .USRMCLKTS (1'b0)
    );

    // A top-level output so the design is not optimised away entirely.
    assign alive = en;
endmodule
