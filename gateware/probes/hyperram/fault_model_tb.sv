// The open twin told to MISBEHAVE, and what that looks like on the bus.
//
// vendor_model_tb.sv checks that hyperram_model.v behaves like the part, against
// Winbond's own model. This one checks the opposite capability: that it can be
// told not to. A faithful model cannot test a controller's response to an
// unfaithful device, and a correct part never misbehaves -- so the knobs are the
// only way to reach the read watchdog (#316), the stall escape and the tDSV
// float (#338). See docs/chips/hyperram/sim-audit.md and #346.
//
// One knob per run, set by `+define+` from scripts/hyperram_vendor_model_sim.py:
//
//   FAULT_CA_RWDS   RWDS over the CA: 0 the part, 1 stuck High, 2 stuck Low,
//                   3 never driven -- the float a controller may sample early
//   FAULT_DELIVER   read words served before the device goes silent, -1 = all.
//                   0 is a device that never answers, N one that stops mid-burst
//
// ICARUS ONLY, and NOT the shared testbench. The vendor model has no such
// parameters, so there is nothing to hold this one honest against; what holds it
// honest is that the SAME model passes vendor_model_tb.sv with every knob at 0.
//
// It proves the knob does what its name says. Pointing a controller at a faulted
// device is controller_model_tb.sv's job, not this file's.
//
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

`ifndef FAULT_CA_RWDS
  `define FAULT_CA_RWDS 0
`endif
`ifndef FAULT_DELIVER
  `define FAULT_DELIVER -1
`endif

module tb;

  localparam real TCK = 10.0;   // 100 MHz, the same clock vendor_model_tb.sv uses

  localparam [7:0] CMD_MEM_READ  = 8'hA0;
  localparam [7:0] CMD_MEM_WRITE = 8'h20;
  localparam [7:0] CMD_REG_READ  = 8'hE0;

  localparam [31:0] ADDR_ID0 = 32'h0000_0000;

  // CR0[3] = 1 fixed, CR0[7:4] = 7 -> 2 x 7 = 14 CK before data. POR, untouched
  // here: the knobs are the only thing this file varies.
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

  hyperram_model #(
    .CA_RWDS_FAULT(`FAULT_CA_RWDS),
    .DELIVER_WORDS(`FAULT_DELIVER)
  ) dut (
    .adq(adq), .clk(clk), .clk_n(clk_n), .csb(csb),
    .rwds(rwds), .VCC(VCC), .VSS(VSS), .resetb(resetb)
  );

  always #(TCK/2.0) clk = ~clk;

  reg [15:0] got;
  integer    strobe_edges;
  integer    w, arrived;

  // One character per CA edge: `1`, `0` or `z`. The whole point of the RWDS knob
  // is visible here and nowhere else -- the part prints `zz1111`, stuck-High
  // prints `111111`, and a device that never drives prints `zzzzzz`.
  reg [8*6:1] ca_rwds;

  function [7:0] rwds_char;
    input value;
    begin
      if (value === 1'b1)      rwds_char = "1";
      else if (value === 1'b0) rwds_char = "0";
      else                     rwds_char = "z";
    end
  endfunction

  function [47:0] ca(input [7:0] cmd, input [31:0] word_addr);
    begin
      ca = 48'h0;
      ca[47:40] = cmd;
      ca[44:16] = word_addr[31:3];
      ca[2:0]   = word_addr[2:0];
    end
  endfunction

  // Six CA bytes, one per edge, changing 1 ns after each so the model sees ~4 ns
  // of setup. RWDS is sampled 1 ns before each edge -- the level the device is
  // presenting for that edge, which is what a controller latches.
  task drive_ca(input [47:0] c);
    begin
      ca_rwds = "      ";
      @(negedge clk); #1;
      csb     = 1'b0;
      adq_oe  = 1'b1;
      adq_drv = c[47:40];
      @(posedge clk); #1; adq_drv = c[39:32]; ca_rwds[8*6:8*5+1] = rwds_char(rwds);
      @(negedge clk); #1; adq_drv = c[31:24]; ca_rwds[8*5:8*4+1] = rwds_char(rwds);
      @(posedge clk); #1; adq_drv = c[23:16]; ca_rwds[8*4:8*3+1] = rwds_char(rwds);
      @(negedge clk); #1; adq_drv = c[15:8];  ca_rwds[8*3:8*2+1] = rwds_char(rwds);
      @(posedge clk); #1; adq_drv = c[7:0];   ca_rwds[8*2:8*1+1] = rwds_char(rwds);
      @(negedge clk); #1; adq_oe = 1'b0;      ca_rwds[8*1:1]     = rwds_char(rwds);
    end
  endtask

  task bus_idle;
    begin
      @(negedge clk); #1;
      csb      = 1'b1;
      adq_oe   = 1'b0;
      rwds_oe  = 1'b0;
      repeat (10) @(posedge clk);   // past tCSHI 10 ns and tRWR 36 ns
    end
  endtask

  // Hunt the read strobe rather than counting the latency, which is what a real
  // controller does. The CA-period RWDS has to fall first or the hunt latches
  // onto the extra-latency request; bounded at 64 edges (~4.5x the 14 CK
  // latency) so a silent device ends the task instead of hanging the run.
  task capture_word(output [15:0] word);
    integer edges;
    begin
      word  = 16'hzzzz;
      edges = 0;
      #0.5;
      while (rwds === 1'b1 && edges < 8) begin
        @(clk); #0.5;
        edges = edges + 1;
      end
      while (rwds !== 1'b1 && edges < 64) begin
        @(clk); #0.5;
        edges = edges + 1;
      end
      strobe_edges = edges;
      if (edges < 64) begin
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

  // A memory write takes the same latency as a read and has no strobe to
  // self-align on, so the host counts it. Same body as vendor_model_tb.sv's.
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

  // Read `n` words as one burst and count how many ARRIVED. A device that stops
  // mid-burst releases DQ, so a word that never came reads back `zzzz` -- which
  // is the difference between a short burst and a wrong one.
  task read_burst_served(input [31:0] word_addr, input integer n,
                         output integer arrived);
    integer w;
    reg [15:0] word;
    begin
      arrived = 0;
      drive_ca(ca(CMD_MEM_READ, word_addr));
      capture_word(word);
      if (word !== 16'hzzzz) arrived = 1;
      for (w = 1; w < n; w = w + 1) begin
        @(clk); #0.5; word[15:8] = adq;
        @(clk); #0.5; word[7:0]  = adq;
        if (word !== 16'hzzzz) arrived = arrived + 1;
      end
      bus_idle;
    end
  endtask

  initial begin
    VCC = 1'b1;
    VSS = 1'b0;
    resetb = 1'b1;
    #200_000;             // tVCS 150 us
    resetb = 1'b0; #1_000; resetb = 1'b1; #2_000;
    bus_idle;

    $display("[fault] knobs: CA_RWDS=%0d DELIVER=%0d",
             `FAULT_CA_RWDS, `FAULT_DELIVER);

    // A register read is the shortest transaction that shows the CA period and
    // then a strobe, so one of them answers both questions the knob raises: what
    // the device said over the CA, and whether it still served the word.
    read_register(ADDR_ID0, got);
    $display("[fault] ca rwds = %0s, strobe at %0d edges, id0 = %h",
             ca_rwds, strobe_edges, got);

    write_memory(32'h00_0100, 16'h1234);
    read_memory(32'h00_0100, got);
    $display("[fault] mem[0x000100] = %h", got);

    // Eight words written and read back as one burst. Writes are not gated by
    // DELIVER_WORDS, so the array is filled whatever the knob says and the count
    // below is about the read alone.
    for (w = 0; w < 8; w = w + 1)
      write_memory(32'h00_0200 + w, 16'h2000 + w[15:0]);
    read_burst_served(32'h00_0200, 8, arrived);
    $display("[fault] burst served %0d of 8", arrived);

    $display("[fault] === done ===");
    $finish;
  end

  // The run is bounded: a knob that wedges the model must end the simulation with
  // a report rather than stall the runner. 400 us is ~1.3x the 300 us the stimulus
  // above takes, almost all of it tVCS.
  initial begin
    #400_000;
    $display("[fault] FAIL simulation ran past its 400 us limit");
    $finish;
  end

endmodule
