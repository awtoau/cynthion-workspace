// Smoke test for Winbond's encrypted W956A8MBYA model under Questa.
// Power up, reset, read ID0 out of register space, print what comes back.
`timescale 1ns/1ps

module tb;

  localparam real TCK = 10.0;   // 100 MHz -- slower than the 166 MHz grade, legal

  reg        clk = 1'b0;
  wire       clk_n = ~clk;
  reg        csb = 1'b1;
  reg        resetb = 1'b0;
  reg        VCC = 1'b0;
  reg        VSS = 1'b0;

  reg  [7:0] adq_drv = 8'h00;
  reg        adq_oe  = 1'b0;
  wire [7:0] adq = adq_oe ? adq_drv : 8'hzz;

  reg        rwds_drv = 1'b0;
  reg        rwds_oe  = 1'b0;
  wire       rwds = rwds_oe ? rwds_drv : 1'bz;

  W956A8MBYA dut (
    .adq(adq), .clk(clk), .clk_n(clk_n), .csb(csb),
    .rwds(rwds), .VCC(VCC), .VSS(VSS), .resetb(resetb)
  );

  always #(TCK/2.0) clk = ~clk;

  // CA[47]=1 read, CA[46]=1 register space, CA[45]=1 linear, address 0 -> ID0
  localparam [47:0] CA_READ_ID0 = 48'hE0_00_00_00_00_00;

  integer  i;
  reg [15:0] captured;

  task send_ca(input [47:0] ca);
    begin
      @(negedge clk); #1;
      csb    = 1'b0;
      adq_oe = 1'b1;
      adq_drv = ca[47:40];          // captured on the next rising edge
      @(posedge clk); #1; adq_drv = ca[39:32];
      @(negedge clk); #1; adq_drv = ca[31:24];
      @(posedge clk); #1; adq_drv = ca[23:16];
      @(negedge clk); #1; adq_drv = ca[15:8];
      @(posedge clk); #1; adq_drv = ca[7:0];
      @(negedge clk); #1; adq_oe = 1'b0;
    end
  endtask

  initial begin
    $display("[tb] power on");
    VCC = 1'b1;
    VSS = 1'b0;
    resetb = 1'b1;                   // RESET# idles high
    #200_000;                        // tVCS is 150 us in Config-AC.v
    resetb = 1'b0;                   // tRP min 200 ns
    #1_000;
    resetb = 1'b1;
    #2_000;                          // tRPH min 400 ns

    $display("[tb] internal state after power-up:");
    $display("[tb]   ID_REG0     = %h", dut.ID_REG0);
    $display("[tb]   ID_REG1     = %h", dut.ID_REG1);
    $display("[tb]   CONFIG_REG0 = %h", dut.CONFIG_REG0);
    $display("[tb]   CONFIG_REG1 = %h", dut.CONFIG_REG1);

    $display("[tb] register read of ID0 at %0t", $time);
    send_ca(CA_READ_ID0);

    // Watch RWDS and the bus through the latency and data phase.
    for (i = 0; i < 40; i = i + 1) begin
      @(posedge clk);
      #0.5;
      if (rwds !== 1'bz || adq !== 8'hzz)
        $display("[tb]   t=%0t  posedge  adq=%h rwds=%b", $time, adq, rwds);
      @(negedge clk);
      #0.5;
      if (rwds !== 1'bz || adq !== 8'hzz)
        $display("[tb]   t=%0t  negedge  adq=%h rwds=%b", $time, adq, rwds);
    end

    @(negedge clk); #1;
    csb = 1'b1;
    #200;
    $display("[tb] done");
    $finish;
  end

endmodule
