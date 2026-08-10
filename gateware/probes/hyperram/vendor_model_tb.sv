// Exercise a HyperRAM model over the bus: registers, memory, and a tCSM violation.
//
// Written against Winbond's own encrypted W956A8MBYA model (which only Questa can
// read -- see scripts/hyperram_vendor_model_sim.py), but it instantiates whatever
// `DUT_MODULE names, so the same stimulus drives an open model for comparison.
//
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

`ifndef DUT_MODULE
  `define DUT_MODULE W956A8MBYA
`endif

module tb;

  localparam real TCK = 10.0;   // 100 MHz -- below the 166 MHz grade, legal everywhere

  // CA[47:45]. The datasheet's four command encodings, as the top byte with a
  // linear burst: read/write x memory/register.
  localparam [7:0] CMD_MEM_READ  = 8'hA0;
  localparam [7:0] CMD_MEM_WRITE = 8'h20;
  localparam [7:0] CMD_REG_READ  = 8'hE0;
  localparam [7:0] CMD_REG_WRITE = 8'h60;

  // Register space word addresses, as the datasheet's own map gives them.
  localparam [31:0] ADDR_ID0 = 32'h0000_0000;
  localparam [31:0] ADDR_CR0 = 32'h0000_0800;
  localparam [31:0] ADDR_CR1 = 32'h0000_0801;

  // CR0[3] = 1 fixed, CR0[7:4] = 7 -> 2 x 7 = 14 CK before data.
  localparam int LATENCY_CK = 14;

  reg        clk = 1'b0;
  wire       clk_n = ~clk;
  reg        csb = 1'b1;
  reg        resetb = 1'b1;
  reg        VCC = 1'b0;
  reg        VSS = 1'b0;

  reg  [7:0] adq_drv = 8'h00;
  reg        adq_oe  = 1'b0;
  wire [7:0] adq = adq_oe ? adq_drv : 8'hzz;

  reg        rwds_drv = 1'b0;
  reg        rwds_oe  = 1'b0;
  wire       rwds = rwds_oe ? rwds_drv : 1'bz;

  `DUT_MODULE dut (
    .adq(adq), .clk(clk), .clk_n(clk_n), .csb(csb),
    .rwds(rwds), .VCC(VCC), .VSS(VSS), .resetb(resetb)
  );

  always #(TCK/2.0) clk = ~clk;

  integer    i;
  reg [15:0] got;
  integer    errors = 0;

  // A HyperBus CA: command in [47:40], A[31:3] in [44:16], A[2:0] in [2:0].
  function [47:0] ca(input [7:0] cmd, input [31:0] word_addr);
    begin
      ca = 48'h0;
      ca[47:40] = cmd;
      ca[44:16] = word_addr[31:3];
      ca[2:0]   = word_addr[2:0];
    end
  endfunction

  // Drive the six CA bytes, one per clock edge, changing 1 ns after each edge so
  // the model sees ~4 ns of setup against a tIS of 0.8 ns.
  task drive_ca(input [47:0] c);
    begin
      @(negedge clk); #1;
      csb     = 1'b0;
      adq_oe  = 1'b1;
      adq_drv = c[47:40];
      @(posedge clk); #1; adq_drv = c[39:32];
      @(negedge clk); #1; adq_drv = c[31:24];
      @(posedge clk); #1; adq_drv = c[23:16];
      @(negedge clk); #1; adq_drv = c[15:8];
      @(posedge clk); #1; adq_drv = c[7:0];
      @(negedge clk); #1; adq_oe = 1'b0;
    end
  endtask

  task bus_idle;
    begin
      @(negedge clk); #1;
      csb      = 1'b1;
      adq_oe   = 1'b0;
      rwds_oe  = 1'b0;
      repeat (10) @(posedge clk);   // well past tCSHI 10 ns and tRWR 36 ns
    end
  endtask

  // Take the first word off the bus. RWDS is the device's own read strobe and it
  // rises with the first byte, so hunt for it rather than counting the latency --
  // self-aligning, and it is what a real controller does. Bounded at 64 edges
  // (~4.5x the 14 CK latency) so a silent device ends the task instead of hanging
  // the simulation; on expiry the word stays z and the caller's check fails.
  task capture_word(output [15:0] word);
    integer edges;
    begin
      word  = 16'hzzzz;
      edges = 0;
      // Every observation below sits 0.5 ns past an edge. Testing RWDS *on* the
      // edge is a delta-cycle race against the device's own driver: one model
      // updated before the check and one after, and the loser samples a beat late,
      // which shows up as a byte-swapped word rather than as a timing bug.
      #0.5;
      // The device also drives RWDS HIGH during the CA period -- that is the
      // extra-latency request, not the read strobe. Let it fall first, or the hunt
      // below latches onto it and samples a tristate bus.
      while (rwds === 1'b1 && edges < 8) begin
        @(clk); #0.5;
        edges = edges + 1;
      end
      while (rwds !== 1'b1 && edges < 64) begin
        @(clk); #0.5;
        edges = edges + 1;
      end
      if (edges >= 64)
        $display("[tb] no read strobe within %0d edges -- device silent", edges);
      else begin
        word[15:8] = adq;
        @(clk); #0.5; word[7:0] = adq;
      end
    end
  endtask

  task read_register(input [31:0] addr, output [15:0] word);
    begin
      drive_ca(ca(CMD_REG_READ, addr));
      capture_word(word);
      bus_idle;
    end
  endtask

  // Register writes take no latency: the data follows the CA immediately, and the
  // host must NOT drive RWDS (the datasheet is explicit, and two open cores get
  // this wrong -- see docs/chips/hyperram/survey.md).
  task write_register(input [31:0] addr, input [15:0] word);
    begin
      drive_ca(ca(CMD_REG_WRITE, addr));
      // drive_ca returns just past the edge that took the last CA byte, so the
      // first data byte goes on the bus NOW to be captured by the very next edge.
      // Presenting it one edge later makes the device latch the idle bus as the
      // high byte -- which lands CR0[15] = 0 and puts the part into deep power
      // down, a failure that looks nothing like a byte-order bug.
      adq_oe  = 1'b1;
      rwds_oe = 1'b0;
      adq_drv = word[15:8];
      @(posedge clk); #1; adq_drv = word[7:0];
      @(negedge clk); #1; adq_oe = 1'b0;
      bus_idle;
    end
  endtask

  // Memory writes take the same latency as reads, and RWDS is the byte mask:
  // low = write this byte.
  task write_memory(input [31:0] word_addr, input [15:0] word);
    begin
      drive_ca(ca(CMD_MEM_WRITE, word_addr));
      repeat (LATENCY_CK - 1) @(posedge clk);
      adq_oe   = 1'b1;
      rwds_oe  = 1'b1;
      rwds_drv = 1'b0;
      @(negedge clk); #1; adq_drv = word[15:8];
      @(posedge clk); #1; adq_drv = word[7:0];
      @(negedge clk); #1; adq_oe = 1'b0; rwds_oe = 1'b0;
      bus_idle;
    end
  endtask

  task read_memory(input [31:0] word_addr, output [15:0] word);
    begin
      drive_ca(ca(CMD_MEM_READ, word_addr));
      capture_word(word);
      bus_idle;
    end
  endtask

  task check(input [127:0] what, input [15:0] observed, input [15:0] expected);
    begin
      if (observed === expected)
        $display("[tb] PASS %0s = %h", what, observed);
      else begin
        $display("[tb] FAIL %0s = %h, expected %h", what, observed, expected);
        errors = errors + 1;
      end
    end
  endtask

  initial begin
    $display("[tb] === power-up ===");
    VCC = 1'b1;
    VSS = 1'b0;
    resetb = 1'b1;
    #200_000;             // tVCS is 150 us
    resetb = 1'b0;
    #1_000;               // tRP min 200 ns
    resetb = 1'b1;
    #2_000;               // tRPH min 400 ns
    bus_idle;

    $display("[tb] === register space ===");
    read_register(ADDR_ID0, got);  check("ID0", got, 16'h0c86);
    read_register(ADDR_CR0, got);  check("CR0 (POR)", got, 16'h8f2f);
    read_register(ADDR_CR1, got);  check("CR1 (POR)", got, 16'hffc1);

    // CR0[14:12] drive strength 000 -> 010 (67 ohm). Everything else held, so a
    // read-back that differs anywhere else means the byte order is wrong.
    $display("[tb] === CR0 write: drive strength 34 ohm -> 67 ohm ===");
    write_register(ADDR_CR0, 16'h AF2F);
    read_register(ADDR_CR0, got);  check("CR0 after write", got, 16'haf2f);

    // CR1[6] = 0 selects the differential clock. The 2025 app note says an
    // unsupported bit is silently discarded, so the read-back is the answer (#334).
    $display("[tb] === CR1 write: single-ended -> differential clock ===");
    write_register(ADDR_CR1, 16'hff81);
    read_register(ADDR_CR1, got);
    $display("[tb] CR1 read back %h -- bit 6 is %0d", got, got[6]);
    if (got[6] === 1'b0) $display("[tb] PASS differential clock accepted");
    else begin $display("[tb] FAIL CR1[6] stayed high"); errors = errors + 1; end
    write_register(ADDR_CR1, 16'hffc1);   // put it back

    $display("[tb] === memory array ===");
    write_memory(32'h00_0000, 16'hdead);
    write_memory(32'h00_0001, 16'hbeef);
    write_memory(32'h3f_ffff, 16'h5aa5);   // top word of the 8 MiB array
    read_memory(32'h00_0000, got);  check("mem[0x000000]", got, 16'hdead);
    read_memory(32'h00_0001, got);  check("mem[0x000001]", got, 16'hbeef);
    read_memory(32'h3f_ffff, got);  check("mem[0x3fffff]", got, 16'h5aa5);

    // tCSM is 4 us. At 100 MHz that is 400 CK, so hold CS# low for 500 and see
    // what the model says. This is the check #317 added to our controller.
    $display("[tb] === deliberate tCSM violation: CS# low for ~5 us ===");
    drive_ca(ca(CMD_MEM_READ, 32'h00_0000));
    repeat (500) @(posedge clk);
    bus_idle;

    $display("[tb] === done, %0d failures ===", errors);
    $finish;
  end

endmodule
