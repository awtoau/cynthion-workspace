// An open HyperRAM model, behaviour-matched to Winbond's encrypted one.
//
// Why this exists: the vendor model (sources/models/W956X8MBY_verilog_p.zip) is
// AES-encrypted to Siemens' key, so only Questa reads it. This is a plain-Verilog
// twin that Icarus, Verilator and cocotb can run, held honest by sharing one
// testbench with the vendor model -- vendor_model_tb.sv instantiates whichever
// `DUT_MODULE names, so both see identical stimulus and the same PASS/FAIL lines.
//
// It is NOT a timing model. Setup/hold, tRWR, tRP and the power-state machine are
// the vendor's job; this covers protocol and contents, which is what the register
// path and the BIST need. Divergence found so far: none over the shared testbench.
//
// See docs/chips/hyperram/survey.md.
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

module hyperram_model #(
    parameter [15:0] ID0_RESET = 16'h0c86,   // Winbond, 13 row / 9 column bits
    parameter [15:0] ID1_RESET = 16'h0001,
    parameter [15:0] CR0_RESET = 16'h8f2f,   // 7-clock fixed latency, 34 ohm, 32-byte wrap
    parameter [15:0] CR1_RESET = 16'hffc1,   // single-ended CK, 4 us tCSM
    parameter real   T_VCS_NS  = 150_000.0,  // power-on to ready
    parameter real   T_CSM_NS  = 4_000.0     // max CS# low, CR1[1:0] = 01b
) (
    inout  wire [7:0] adq,
    input  wire       clk,
    input  wire       clk_n,
    input  wire       csb,
    inout  wire       rwds,
    input  wire       VCC,
    input  wire       VSS,
    input  wire       resetb
);

  localparam MEM_WORDS = 4 * 1024 * 1024;    // 8 MiB as 16-bit words

  reg [15:0] memory [0:MEM_WORDS-1];
  reg [15:0] id0, id1, cr0, cr1;

  reg        ready = 1'b0;
  realtime   t_cs_fall;

  // Transaction state. `beat` counts clock edges from CS# falling; the CA occupies
  // the first six, one byte per edge.
  reg [47:0] ca;
  reg [15:0] beat;
  reg [21:0] word_addr;
  reg        is_read, is_register;

  reg  [7:0] write_high;
  reg [15:0] rd_word;
  reg  [7:0] dq_out = 8'h00;
  reg        dq_oe  = 1'b0;
  reg        rwds_out = 1'b0;
  reg        rwds_oe  = 1'b0;

  assign adq  = dq_oe   ? dq_out   : 8'hzz;
  assign rwds = rwds_oe ? rwds_out : 1'bz;

  // CR0[7:4] is a sparse, sign-extended encoding: clocks = 5 + sext4(code), so
  // 0..2 give 5..7 and 14..15 give 3..4. CR0[3] = 1 doubles it (fixed latency).
  function [7:0] latency_ck;
    input [15:0] cr0_v;
    reg [3:0] code;
    reg [7:0] base;
    begin
      code = cr0_v[7:4];
      base = (code >= 4'd14) ? (8'd5 + {4'hF, code} - 8'd16) : (8'd5 + code);
      latency_ck = cr0_v[3] ? (base * 2) : base;
    end
  endfunction

  // First edge that carries data: six for the CA, then the latency, except a
  // register write which takes none at all.
  function [15:0] first_data_beat;
    input is_reg_write;
    begin
      first_data_beat = is_reg_write ? 16'd6 : (16'd6 + 2 * latency_ck(cr0));
    end
  endfunction

  function [15:0] read_word;
    input [21:0] a;
    begin
      if (!is_register) read_word = memory[a];
      else case (a)
        22'h00_0000: read_word = id0;
        22'h00_0001: read_word = id1;
        22'h00_0800: read_word = cr0;
        22'h00_0801: read_word = cr1;
        default:     read_word = 16'h0000;
      endcase
    end
  endfunction

  task write_register;
    input [21:0] a;
    input [15:0] d;
    begin
      case (a)
        22'h00_0800: begin
          cr0 = d;
          $display("%m: Write New CR0: 0x%h -- latency code %0d, %0s, drive %0d",
                   d, d[7:4], d[3] ? "fixed" : "variable", d[14:12]);
        end
        22'h00_0801: begin
          cr1 = d;
          $display("%m: Write New CR1: 0x%h -- %0s clock",
                   d, d[6] ? "single-ended" : "differential");
        end
        default: $display("%m: write to read-only register space 0x%h ignored", a);
      endcase
    end
  endtask

  integer i;
  initial begin
    id0 = ID0_RESET; id1 = ID1_RESET; cr0 = CR0_RESET; cr1 = CR1_RESET;
    for (i = 0; i < MEM_WORDS; i = i + 1) memory[i] = 16'h0000;
  end

  always @(posedge VCC) begin
    ready = 1'b0;
    #(T_VCS_NS);
    ready = 1'b1;
    $display("%m: ready at %0t -- ID0=%h ID1=%h CR0=%h CR1=%h", $time, id0, id1, cr0, cr1);
  end

  always @(negedge resetb) begin
    cr0 = CR0_RESET;
    cr1 = CR1_RESET;
    $display("%m: RESET# low at %0t -- config registers back to default", $time);
  end

  always @(negedge csb) begin
    beat      = 16'd0;
    ca        = 48'h0;
    t_cs_fall = $realtime;
  end

  always @(posedge csb) begin
    dq_oe   = 1'b0;
    rwds_oe = 1'b0;
    if (($realtime - t_cs_fall) > T_CSM_NS)
      $display("%m: ERROR tCSM violation. The CE LOW period is %0.3f ns, it should be smaller than %0.3f ns",
               $realtime - t_cs_fall, T_CSM_NS);
  end

  // One block on both edges: HyperBus is DDR and every beat is an edge.
  always @(posedge clk or negedge clk) begin
    if (!csb && ready && resetb) begin
      if (beat < 16'd6) begin
        // Command-Address, most significant byte first.
        ca = {ca[39:0], adq};
        rwds_oe  = 1'b1;
        rwds_out = 1'b1;              // request the extra latency, as the part does
        if (beat == 16'd5) begin
          is_read     = ca[47];         // 1 = read
          is_register = ca[46];         // AS: 1 = register space, 0 = memory array
          word_addr   = {ca[44:16], ca[2:0]} & (MEM_WORDS - 1);
          rwds_out    = 1'b0;
          if (is_register && !is_read) rwds_oe = 1'b0;   // host owns RWDS on a register write
        end
      end else if (beat >= first_data_beat(is_register && !is_read)) begin
        if (is_read) begin
          dq_oe    = 1'b1;
          rwds_oe  = 1'b1;
          rd_word  = read_word(word_addr);   // Icarus will not part-select a call
          // Even data beats carry the high byte and raise the strobe.
          if (!((beat - first_data_beat(1'b0)) & 1)) begin
            dq_out   = rd_word[15:8];
            rwds_out = 1'b1;
          end else begin
            dq_out   = rd_word[7:0];
            rwds_out = 1'b0;
            if (!is_register) word_addr = word_addr + 1'b1;  // registers repeat, memory advances
          end
        end else if (!write_done) begin
          rwds_oe = 1'b0;
          if (!((beat - first_data_beat(is_register)) & 1))
            write_high = adq;
          else begin
            // RWDS low = write this byte. Register space has no mask.
            if (is_register) begin
              write_register(word_addr, {write_high, adq});
              // Register writes are a single word. The host may hold CS# low for
              // another edge or two on the way to raising it, and without this the
              // idle bus lands as a second write of z -- which reads back as a
              // clobbered register, not as a protocol error.
              write_done = 1'b1;
            end else if (rwds !== 1'b1) begin
              memory[word_addr] = {write_high, adq};
              word_addr = word_addr + 1'b1;
            end
          end
        end
      end
      beat = beat + 1'b1;
    end else if (csb) begin
      dq_oe   = 1'b0;
      rwds_oe = 1'b0;
    end
  end


endmodule
