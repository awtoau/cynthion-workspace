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
// See docs/chips/hyperram/models.md.
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

module hyperram_model #(
    parameter [15:0] ID0_RESET = 16'h0c86,   // Winbond, 13 row / 9 column bits
    parameter [15:0] ID1_RESET = 16'h0001,
    parameter [15:0] CR0_RESET = 16'h8f2f,   // 7-clock fixed latency, 34 ohm, 32-byte wrap
    parameter [15:0] CR1_RESET = 16'hffc1,   // single-ended CK, 4 us tCSM
    parameter real   T_VCS_NS  = 150_000.0,  // power-on to ready
    parameter real   T_CSM_NS  = 4_000.0,    // max CS# low, CR1[1:0] = 01b
    // CS# low to RWDS valid. Not a timing check -- it decides WHICH CA edge
    // carries the extra-latency request, and a controller sampling earlier than
    // this samples a float. 12 ns at T166 / 3.0 V, Config-AC.v. (#338, #342)
    parameter real   T_DSV_NS  = 12.0
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

  reg        dpd;               // CR0[15] = 0; only RESET# gets out
  reg        wrapping, hybrid;
  reg [21:0] wrap_mask, wrap_base, wrap_count;
  reg  [7:0] write_high;
  reg [15:0] rd_word;
  reg        write_done;
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
      base = (code >= 4'd14) ? (code - 4'd11) : (code + 4'd5);
      latency_ck = cr0_v[3] ? (base * 2) : base;
    end
  endfunction

  // First edge that carries data. A register write takes none at all; everything
  // else waits out the initial latency.
  //
  // The -2 is calibrated against the vendor model, not derived: the latency count
  // runs from the last CA *clock*, and the last two CA bytes share the third
  // clock, so counting from the last CA edge lands a beat late. The shared
  // testbench is what caught it -- a read self-aligns on RWDS and hides this, a
  // write does not and captures an idle bus.
  function [15:0] first_data_beat;
    input is_reg_write;
    begin
      first_data_beat = is_reg_write ? 16'd6 : (16'd4 + 2 * latency_ck(cr0));
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
          if (!d[15]) begin
            dpd = 1'b1;
            $display("%m: CR0[15] = 0 -- entering deep power down, RESET# is the only way out");
          end
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
    id0 = ID0_RESET; id1 = ID1_RESET; cr0 = CR0_RESET; cr1 = CR1_RESET; dpd = 1'b0;
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
    dpd = 1'b0;
    $display("%m: RESET# low at %0t -- config registers back to default", $time);
  end

  always @(negedge csb) begin
    beat       = 16'd0;
    ca         = 48'h0;
    write_done = 1'b0;
    t_cs_fall  = $realtime;
  end

  // The extra-latency request. RWDS over the CA period is the ONLY thing that
  // says 1x or 2x with CR0[3] = 0 -- and it is not driven until tDSV, so the
  // first CA edge or two carry a float, not an answer. The vendor model shows
  // `zz1111` for fixed and `zz0000` for variable at 100 MHz.
  //
  // This model has no refresh, so with CR0[3] = 0 it never asks for the extra
  // latency. A refresh collision is the one case that makes a variable-latency
  // transaction take 2L, and it stays the vendor model's alone. (#338, #342)
  always @(negedge csb) begin
    rwds_oe = 1'b0;
    #(T_DSV_NS);
    if (!csb) begin
      rwds_out = cr0[3];
      rwds_oe  = 1'b1;
    end
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
    if (!csb && ready && resetb && !dpd) begin
      if (beat < 16'd6) begin
        // Command-Address, most significant byte first. RWDS is driven by the
        // tDSV block above and stays put for the whole CA -- it is the request.
        ca = {ca[39:0], adq};
        if (beat == 16'd5) begin
          is_read     = ca[47];         // 1 = read
          is_register = ca[46];         // AS: 1 = register space, 0 = memory array
          word_addr   = {ca[44:16], ca[2:0]} & (MEM_WORDS - 1);
          // CA[45] = 0 asks for a wrapped burst. Group size is CR0[1:0]:
          // 128/64/16/32 bytes = 64/32/8/16 words, POR 11b = 32 bytes. Legacy
          // wrap stays inside the group for ever; hybrid (CR0[2] = 0) leaves it
          // after one pass. This models the first pass through the group.
          wrapping    = !ca[45];
          hybrid      = !cr0[2];        // 0 = hybrid, 1 = legacy
          wrap_count  = 22'd0;
          case (cr0[1:0])
            2'b00:   wrap_mask = 22'd63;
            2'b01:   wrap_mask = 22'd31;
            2'b10:   wrap_mask = 22'd7;
            default: wrap_mask = 22'd15;
          endcase
          wrap_base   = {ca[44:16], ca[2:0]} & ~wrap_mask;
          // The request is NOT withdrawn here -- it stands for the whole CA, which
          // is what the vendor model shows (`zz1111` / `zz0000` across the six CA
          // edges). It is dropped in the latency branch below.
          if (!is_read) rwds_oe = 1'b0;   // on any write the HOST owns RWDS from here
        end
      end else if (beat < first_data_beat(is_register && !is_read) +
                          (is_read ? 16'd1 : 16'd0)) begin
        rwds_out = 1'b0;                // latency period: the request is answered
      end else begin
        // A read starts one edge later than a write at the same latency -- the
        // device has to turn the bus around. Measured against the vendor model:
        // 28 edges to the strobe at 14 CK fixed, 14 at 7 CK variable.
        if (is_read) begin
          dq_oe    = 1'b1;
          rwds_oe  = 1'b1;
          rd_word  = read_word(word_addr);   // Icarus will not part-select a call
          // Even data beats carry the high byte and raise the strobe.
          if (!((beat - first_data_beat(1'b0) - 16'd1) & 1)) begin
            dq_out   = rd_word[15:8];
            rwds_out = 1'b1;
          end else begin
            dq_out   = rd_word[7:0];
            rwds_out = 1'b0;
            // registers repeat; memory advances, inside the group while wrapped.
            // Hybrid leaves the group after exactly one pass and continues
            // linearly from the group end -- measured on the vendor model, which
            // returns 0x1010 as word 16 of an 18-word wrapped read at 0x10d.
            // Legacy (CR0[2] = 1) stays in the group instead.
            if (!is_register) begin
              if (wrapping) begin
                wrap_count = wrap_count + 1'b1;
                if (hybrid && wrap_count > wrap_mask) begin
                  wrapping  = 1'b0;
                  word_addr = wrap_base + wrap_mask + 1'b1;
                end else
                  word_addr = wrap_base | ((word_addr + 1'b1) & wrap_mask);
              end else
                word_addr = word_addr + 1'b1;
            end
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
            end else if (rwds === 1'b0) begin
              // Strictly low, not merely "not high". A host that has finished its
              // burst releases RWDS along with the data, and treating that float
              // as an unmasked write stores z into the next address -- which
              // surfaces later as a corrupt word nowhere near the transaction
              // that caused it.
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
