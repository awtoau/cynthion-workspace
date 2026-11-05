// yosys 0.70 abc9 ties the unused M input of every async-reset TRELLIS_FF to 0; nextpnr rejects that.
// Rebuild those cells without M. FFs that use M (preload mode) are left alone.
//   techmap -max_iter 1 -map scripts/yosys/trellis_ff_without_m.v
(* techmap_celltype = "TRELLIS_FF" *)
module trellis_ff_without_m #(
	parameter GSR = "ENABLED",
	parameter CEMUX = "1",
	parameter CLKMUX = "CLK",
	parameter LSRMUX = "LSR",
	parameter SRMODE = "LSR_OVER_CE",
	parameter REGSET = "RESET",
	parameter LSRMODE = "LSR"
) (input CLK, LSR, CE, DI, M, output Q);
	// No [127:0] range on the parameters: it turns the strings into integers in the JSON.
	wire _TECHMAP_FAIL_ = !(SRMODE == "ASYNC" && LSRMODE != "PRLD");

	TRELLIS_FF #(
		.GSR(GSR), .CEMUX(CEMUX), .CLKMUX(CLKMUX), .LSRMUX(LSRMUX),
		.SRMODE(SRMODE), .REGSET(REGSET), .LSRMODE(LSRMODE)
	) _TECHMAP_REPLACE_ (.CLK(CLK), .LSR(LSR), .CE(CE), .DI(DI), .Q(Q));
endmodule
