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
    // Minimum CS# HIGH between transactions, at the grade FITTED: a `6I` = T166,
    // where Config-AC.v gives 6 ns. Ten was the T100 column, and #341 took it out
    // of both controllers. The ONLY place this part fact is judged -- the Python
    // models used to judge it against their own copy of it. (#341, #346)
    parameter real   T_CSHI_NS = 6.0,
    // CS# low to RWDS valid. Not a timing check -- it decides WHICH CA edge
    // carries the extra-latency request, and a controller sampling earlier than
    // this samples a float. 12 ns at T166 / 3.0 V, Config-AC.v. (#338, #342)
    parameter real   T_DSV_NS  = 12.0,
    // TRANSACTIONS between refresh-forced 2x elections; 0 disables.
    //
    // The only case that makes a variable-latency transaction take 2L. Counted in
    // transactions and not in time because that is what the VENDOR model does --
    // measured at exactly 100 CS# assertions apart whether the transaction is one
    // word (210 ns) or 128 (1480 ns). A real part refreshes on a wall clock, so
    // this cadence exercises the path, it does not predict a rate. (#338, #342)
    parameter integer REFRESH_EVERY = 100,
    // FAULT INJECTION. 0 is the part; anything else is a device deliberately
    // misbehaving, so the controller's response to it can be checked. See
    // docs/chips/hyperram/sim-audit.md and #346.
    //
    // What RWDS does over the CA period:
    //   0  the part -- float for tDSV, then the CR0[3] | refresh answer
    //   1  stuck High      2  stuck Low      3  never driven at all
    // 1-3 hold from CS# falling with no tDSV, because a fault is held and not
    // timed. Mode 3 is the float a controller sampling before tDSV reads, which
    // is the prime suspect in #338; modes 1 and 2 are a device whose answer
    // contradicts what it then serves.
    parameter integer CA_RWDS_FAULT = 0,
    // Read words this device serves before going silent, counted per transaction.
    // -1 is a part, which always answers. 0 never answers at all, which is the
    // shape of the board's wedge and the case `READ_DATA` had no exit from; N
    // stops mid-burst, and 7-of-8 is how the unbounded state was proven (#316).
    // Silent means DQ and RWDS both released: no strobe, so nothing clocks in.
    parameter integer DELIVER_WORDS = -1,
    // 1 = take the register write off the bus and then ignore it. The CR0/CR1
    // verify path only means anything against a device that can refuse: with a
    // model that always accepts, a controller which never issued the write and
    // one whose write was dropped read back the same value. (#346)
    parameter integer REFUSE_REG_WRITE = 0
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
  // Last CS# rise. Starts far in the past so the first transaction of a run is
  // not judged against a gap that never existed.
  realtime   t_cs_rise = -1.0e9;

  // Transaction state. `beat` counts clock edges from CS# falling; the CA occupies
  // the first six, one byte per edge.
  reg [47:0] ca;
  reg [15:0] beat;
  reg [21:0] word_addr;
  reg        is_read, is_register;

  reg        dpd;               // CR0[15] = 0; only RESET# gets out
  reg        wrapping, hybrid;
  reg [21:0] wrap_mask, wrap_base, wrap_count;

  // Refresh, as the vendor model does it: a transaction counter, not a clock.
  integer    xact_count = 0;
  reg        take_long  = 1'b0;

  integer    served;            // read words served this transaction

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
  // Codes 3..13 are RESERVED -- see `lat_ck_held`.
  function legal_code;
    input [3:0] code;
    begin legal_code = (code <= 4'd2) || (code >= 4'd14); end
  endfunction

  function [7:0] decode_ck;
    input [15:0] cr0_v;
    reg [7:0] base;
    begin
      base = (cr0_v[7:4] >= 4'd14) ? (cr0_v[7:4] - 4'd11) : (cr0_v[7:4] + 4'd5);
      decode_ck = cr0_v[3] ? (base * 2) : base;
    end
  endfunction

  // The latency in CK last decoded from a LEGAL code. A reserved code leaves the
  // whole latency field inert -- CR0[3] included -- and the part serves this
  // instead. OBSERVED from the vendor model, not specified: reserved is undefined
  // silicon and nothing may be designed against it. (#350)
  reg [7:0] lat_ck_held;

  function [7:0] latency_ck;
    input [15:0] cr0_v;
    begin
      latency_ck = legal_code(cr0_v[7:4]) ? decode_ck(cr0_v) : lat_ck_held;
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
    reg [7:0] l;
    begin
      // A refresh election doubles the count, exactly as CR0[3] does -- so with
      // CR0[3] = 1 it changes nothing, which is why fixed latency never shows it.
      l = latency_ck(cr0) * ((take_long && !cr0[3]) ? 2 : 1);
      first_data_beat = is_reg_write ? 16'd6 : (16'd4 + 2 * l);
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
          if (legal_code(d[7:4]))
            lat_ck_held = decode_ck(d);
          else
            $display("%m: CR0[7:4] = %0d is RESERVED -- undefined on the part; holding %0d CK",
                     d[7:4], lat_ck_held);
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

  // `REFRESH_EVERY` as this run uses it. `+refresh_every=N` overrides EVERY
  // instance in the run, which is the point: 1 makes the part ask for the extra
  // latency on every transaction, so both answers can be driven deliberately
  // instead of waiting 100 transactions for one. (#380, #381)
  integer refresh_every = REFRESH_EVERY;

  integer i;
  initial begin
    id0 = ID0_RESET; id1 = ID1_RESET; cr0 = CR0_RESET; cr1 = CR1_RESET; dpd = 1'b0;
    lat_ck_held = decode_ck(CR0_RESET);
    for (i = 0; i < MEM_WORDS; i = i + 1) memory[i] = 16'h0000;
    if ($value$plusargs("refresh_every=%d", refresh_every))
      $display("%m: refresh_every = %0d from +refresh_every", refresh_every);
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
    lat_ck_held = decode_ck(CR0_RESET);
    dpd = 1'b0;
    $display("%m: RESET# low at %0t -- config registers back to default", $time);
  end

  always @(negedge csb) begin
    beat       = 16'd0;
    ca         = 48'h0;
    write_done = 1'b0;
    served     = 0;
    t_cs_fall  = $realtime;
    if (($realtime - t_cs_rise) < T_CSHI_NS)
      $display("%m: ERROR tCSHI violation. The CE HIGH period is %0.3f ns, it should be larger than %0.3f ns",
               $realtime - t_cs_rise, T_CSHI_NS);
    xact_count = xact_count + 1;
    // Decided at CS# falling, before tDSV, and held for the whole transaction --
    // the device cannot change its mind once the CA has been answered.
    take_long  = (refresh_every != 0) && (xact_count % refresh_every == 0);
  end

  // The extra-latency request. RWDS over the CA period is the ONLY thing that
  // says 1x or 2x with CR0[3] = 0 -- and it is not driven until tDSV, so the
  // first CA edge or two carry a float, not an answer. The vendor model shows
  // `zz1111` for fixed and `zz0000` for variable at 100 MHz.
  //
  // Fixed latency always asks; variable latency asks only when a refresh is due.
  always @(negedge csb) begin
    rwds_oe = 1'b0;
    if (CA_RWDS_FAULT == 0) begin
      #(T_DSV_NS);
      if (!csb) begin
        rwds_out = cr0[3] | take_long;
        rwds_oe  = 1'b1;
      end
    end else if (CA_RWDS_FAULT != 3) begin
      rwds_out = (CA_RWDS_FAULT == 1);
      rwds_oe  = 1'b1;
    end
  end

  always @(posedge csb) begin
    dq_oe     = 1'b0;
    rwds_oe   = 1'b0;
    t_cs_rise = $realtime;
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
      end else if (beat < first_data_beat(is_register && !is_read)) begin
        rwds_out = 1'b0;                // latency period: the request is answered
        if (is_read) rwds_oe = 1'b1;    // CA_RWDS_FAULT covers the CA and no more
      end else begin
        // Reads and writes start at the SAME edge. An earlier version delayed
        // reads by one, "measured against the vendor model" -- but that was
        // measured by hunting for the RWDS strobe, and the two models drive RWDS
        // through the latency differently, so the hunt agreed while the DQ edge
        // did not. dqs_latency_probe_tb.sv measures the DQ edge directly on both
        // models and caught it at every latency code.
        if (is_read && DELIVER_WORDS >= 0 && served >= DELIVER_WORDS) begin
          // The device has stopped answering. Both lines released, so there is no
          // strobe and the address does not advance -- what a part in Deep Power
          // Down looks like from here. (#316, #346)
          dq_oe   = 1'b0;
          rwds_oe = 1'b0;
        end else if (is_read) begin
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
            served   = served + 1;
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
              if (REFUSE_REG_WRITE)
                $display("%m: register write to 0x%h of %h REFUSED (fault injection)",
                         word_addr, {write_high, adq});
              else
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
