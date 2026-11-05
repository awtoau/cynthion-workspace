// yosys 0.70 abc9 ties the unused M input of every async-reset TRELLIS_FF to 0; nextpnr rejects that.
// Rebuild those cells without M. FFs that use M (preload mode) are left alone.
//   techmap -max_iter 1 -map scripts/yosys/trellis_ff_without_m.v
//
// Only ports the original cell had are connected again. CE is absent when CEMUX is "1" (enable
// always on); tying it to 0 there freezes the flip-flop on the board.
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

	generate
		if (CEMUX == "CE" || CEMUX == "INV") begin
			TRELLIS_FF #(
				.GSR(GSR), .CEMUX(CEMUX), .CLKMUX(CLKMUX), .LSRMUX(LSRMUX),
				.SRMODE(SRMODE), .REGSET(REGSET), .LSRMODE(LSRMODE)
			) _TECHMAP_REPLACE_ (.CLK(CLK), .LSR(LSR), .CE(CE), .DI(DI), .Q(Q));
		end else begin
			TRELLIS_FF #(
				.GSR(GSR), .CEMUX(CEMUX), .CLKMUX(CLKMUX), .LSRMUX(LSRMUX),
				.SRMODE(SRMODE), .REGSET(REGSET), .LSRMODE(LSRMODE)
			) _TECHMAP_REPLACE_ (.CLK(CLK), .LSR(LSR), .DI(DI), .Q(Q));
		end
	endgenerate
endmodule
