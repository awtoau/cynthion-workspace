// OUR controller WITH upstream's `HyperRAMPHY` in the loop, against the open model.
//
// `controller_model_tb.sv` cuts the DUT at the `HyperBusPHY` record and gears it with
// an IDEAL shim -- zero output latency, one cycle of input latency. Its own header
// says the ECP5 IOLOGIC skew is out of scope and flagged rather than guessed. THAT
// skew is what #338 turns on, so here the PHY is inside the DUT and the primitives
// are Lattice's own simulation models.
//
// What it measures, in order:
//
//   1. `ODDRX1F` output latency and `IDDRX1F` input latency, on spare instances
//      driven with a single-cycle pulse. Measured, not asserted.
//   2. Where the controller's extra-latency sample lands: the pin cycle CS# falls,
//      the pin cycles the CA occupies, the cycle the device first drives RWDS, and
//      the cycles the controller is in SHIFT_COMMAND1/2.
//   3. Elections. Per cell, whether the controller took the LONG or the SHORT
//      latency branch, against the answer the device actually gave.
//
// The float experiment. `float_level` weakly drives RWDS **only while CS# is High** --
// the idle line, and nothing inside a transaction. A controller that samples the
// device's CA answer cannot see it; a controller that samples the idle line follows
// it every time. `float_level = 0` is the control and must be indistinguishable
// from the part.
//
// Driven by scripts/hyperram_phy_rwds_sim.py, which passes the state encoding, the
// latency port width and the sync period it elaborated with.
//
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

`ifndef LAT_W
  `define LAT_W 6
`endif
`ifndef TCK_NS
  `define TCK_NS 12.5
`endif
// The FSM encoding, passed in from the controller's own `STATES` tuple.
`ifndef ST_IDLE
  `define ST_IDLE 0
`endif
`ifndef ST_SHIFT_COMMAND1
  `define ST_SHIFT_COMMAND1 3
`endif
`ifndef ST_SHIFT_COMMAND2
  `define ST_SHIFT_COMMAND2 4
`endif
`ifndef ST_HANDLE_LATENCY
  `define ST_HANDLE_LATENCY 6
`endif
`ifndef ST_READ_DATA
  `define ST_READ_DATA 7
`endif
// Transactions between the model's refresh-forced 2x elections. 0 never elects, so
// the device always takes the SHORT count under variable latency; 1 elects on every
// transaction, so it always takes the LONG one. Both directions are run.
`ifndef REFRESH_EVERY
  `define REFRESH_EVERY 0
`endif
// Smallest `latency_clocks` a READ may take, passed in from the controller's own
// derivation. Below it `READ_DATA` begins while the CA-period RWDS request is still
// in flight and latches its fall as a read strobe. (#353)
`ifndef MIN_READ_LATENCY
  `define MIN_READ_LATENCY 7
`endif

module tb;

  localparam real TCK = `TCK_NS;
  localparam integer XACT_LIMIT   = 4096;
  localparam integer RECOVER_LIMIT = 512;
  localparam [31:0]  ADDR_CR0 = 32'h0000_0800;
  localparam integer BURST     = 8;
  // Sync cycles to let the pins catch up after `idle` rises: the three the output
  // path is behind, plus one. Not a guess at a settling time -- it is the measured
  // depth of `FFSynchronizer(stages=3)` and `ODDRX1F`, both 3.
  localparam integer PIN_DRAIN = 4;

  integer checks = 0;
  integer errors = 0;

  reg clk = 1'b0;
  reg rst = 1'b1;
  always #(TCK/2.0) clk = ~clk;

  integer cyc = 0;
  always @(posedge clk) cyc <= cyc + 1;

  // The ECP5 primitives want these two in the hierarchy by name.
  GSR GSR_INST (.GSR(1'b1));
  PUR PUR_INST (.PUR(1'b1));

  //
  // 1. The primitives' own latency, measured on spare instances.
  //
  // Both are driven with a one-cycle pulse and watched for the cycle it comes out.
  // The numbers matter because they are the whole asymmetry: `HyperRAMPHY` sends
  // CS#/OE through `FFSynchronizer(stages=3)` and CK/DQ through `ODDRX1F`, and
  // brings RWDS/DQ back through `IDDRX1F`.
  //
  localparam integer PROBE_CYC = 8;

  reg  probe_d = 1'b0;
  always @(posedge clk) probe_d <= ((cyc + 1) == PROBE_CYC);

  wire probe_q;
  ODDRX1F probe_oddr (.D0(probe_d), .D1(probe_d), .SCLK(clk), .RST(1'b0), .Q(probe_q));

  wire probe_iq0, probe_iq1;
  IDDRX1F probe_iddr (.D(probe_d), .SCLK(clk), .RST(1'b0), .Q0(probe_iq0), .Q1(probe_iq1));

  integer oddr_out_cyc = -1;
  integer iddr_out_cyc = -1;
  // Sampled off the falling edge: an ODDR output is only whole for one full cycle
  // when D0 and D1 agree, and the IDDR pair is stable across the cycle it lands in.
  always @(negedge clk) begin
    if (probe_q && oddr_out_cyc < 0) oddr_out_cyc = cyc;
    if ((probe_iq0 || probe_iq1) && iddr_out_cyc < 0) iddr_out_cyc = cyc;
  end

  //
  // 2. The DUT: controller AND PHY, cut at the pin.
  //
  reg               start_transfer     = 1'b0;
  reg               register_space     = 1'b0;
  reg               perform_write      = 1'b0;
  reg               single_page        = 1'b0;
  reg  [31:0]       address            = 32'h0;
  reg  [`LAT_W-1:0] latency_clocks     = 14;
  reg  [`LAT_W-1:0] low_latency_clocks = 7;
  reg               fixed_latency      = 1'b1;
  reg               final_word;
  reg  [15:0]       write_data;

  wire        idle, read_ready, write_ready, timed_out;
  wire [15:0] read_data;
  wire [3:0]  state;
  wire [1:0]  rwds_i_seen;

  wire       cs_pin, ck_pin;
  wire [7:0] dq_o_pin;
  wire       dq_oe_pin, rwds_o_pin, rwds_oe_pin;

  wire [7:0] adq;
  wire       rwds;

  hyperram_phy_top dut (
    .clk(clk), .rst(rst),
    .start_transfer(start_transfer), .register_space(register_space),
    .perform_write(perform_write), .single_page(single_page),
    .final_word(final_word), .address(address), .write_data(write_data),
    .latency_clocks(latency_clocks), .low_latency_clocks(low_latency_clocks),
    .fixed_latency(fixed_latency),
    .idle(idle), .read_ready(read_ready), .write_ready(write_ready),
    .timed_out(timed_out), .read_data(read_data), .state(state),
    .rwds_i_seen(rwds_i_seen),
    .cs_o(cs_pin), .clk_o(ck_pin),
    .dq_o(dq_o_pin), .dq_i(adq), .dq_oe(dq_oe_pin),
    .rwds_o(rwds_o_pin), .rwds_i(rwds), .rwds_oe(rwds_oe_pin)
  );

  // Held High while the controller is in reset: without it the model sees CS# fall
  // at time 0 from an uninitialised output and reports a tCSM violation before any
  // transaction has happened. The pad is `PinsN`, so the record's active-High `cs`
  // is the pin's active-Low CS#.
  wire csb = rst ? 1'b1 : ~cs_pin;

  assign adq  = dq_oe_pin   ? dq_o_pin   : 8'hzz;
  assign rwds = rwds_oe_pin ? rwds_o_pin : 1'bz;

  // THE FLOAT EXPERIMENT. Weak, and only while CS# is High -- what an undriven RWDS
  // line reads as between transactions. `z` is a true float, which an IDDR resolves
  // to x; 0 and 1 are the two levels a real board's leakage could sit at.
  reg float_level = 1'bz;
  assign (weak0, weak1) rwds = csb ? float_level : 1'bz;

  reg resetb = 1'b1;
  reg VCC    = 1'b0;
  wire VSS   = 1'b0;

  // T_VCS is 150 us of power-on wait that has nothing to do with this measurement;
  // shortened so the sweep runs in a second. Every other parameter is the part's.
  hyperram_model #(.REFRESH_EVERY(`REFRESH_EVERY), .T_VCS_NS(200.0)) model (
    .adq(adq), .clk(ck_pin), .clk_n(~ck_pin), .csb(csb),
    .rwds(rwds), .VCC(VCC), .VSS(VSS), .resetb(resetb)
  );

  //
  // 3. Where the sample lands. Everything here is read off the pins.
  //
  integer cs_fall_cyc   = -1;   // cycle CS# went Low at the PIN
  integer ck_first_cyc  = -1;   // cycle of the first CK edge inside this transaction
  integer dev_drive_cyc = -1;   // cycle the DEVICE first drove RWDS
  integer sc1_cyc       = -1;   // cycle the controller was in SHIFT_COMMAND1
  integer sc2_cyc       = -1;   // and SHIFT_COMMAND2
  integer ca_end_cyc    = -1;   // cycle carrying the last CA edge
  reg [1:0] sc1_sample  = 2'b00;
  reg [1:0] sc2_sample  = 2'b00;
  reg       window_done = 1'b0;

  always @(negedge csb) begin
    cs_fall_cyc   = cyc;
    ck_first_cyc  = -1;
    dev_drive_cyc = -1;
    ca_end_cyc    = -1;
  end

  always @(posedge ck_pin) if (!csb && ck_first_cyc < 0) ck_first_cyc = cyc;
  // The CA is six edges from the first; the third CK cycle carries the last two.
  always @(posedge ck_pin) if (!csb && ck_first_cyc >= 0 && ca_end_cyc < 0
                               && cyc == ck_first_cyc + 2) ca_end_cyc = cyc;

  // The device driving RWDS, told apart from the host and from the weak float by
  // asking the model itself whether its output enable is on.
  always @(posedge clk or negedge clk)
    if (!csb && dev_drive_cyc < 0 && model.rwds_oe) dev_drive_cyc = cyc;

  always @(posedge clk) begin
    if (state == `ST_SHIFT_COMMAND1) begin sc1_cyc <= cyc; sc1_sample <= rwds_i_seen; end
    if (state == `ST_SHIFT_COMMAND2) begin sc2_cyc <= cyc; sc2_sample <= rwds_i_seen; end
  end

  //
  // Election counter. This is the counter #338 asks for, in simulation, where the
  // answer the device gave is knowable too. HANDLE_LATENCY runs one cycle per
  // remaining count plus the zero cycle, so it is L-1 cycles on the SHORT branch
  // and 2L-1 on the LONG one.
  //
  integer hl_count = 0;
  always @(posedge clk) begin
    if (state == `ST_SHIFT_COMMAND1)        hl_count <= 0;
    else if (state == `ST_HANDLE_LATENCY)   hl_count <= hl_count + 1;
  end

  integer elections_long  = 0;
  integer elections_short = 0;
  integer elections_wrong = 0;
  integer cells_with_data_errors = 0;

  //
  // Burst engine, the same shape `controller_model_tb.sv` uses.
  //
  reg [31:0] burst_base   = 32'h0;
  integer    burst_target = 0;
  reg        burst_active = 1'b0;
  reg        burst_reg    = 1'b0;
  reg [15:0] burst_regval = 16'h0;
  integer    burst_count  = 0;
  reg [15:0] read_words [0:31];

  function [15:0] pattern(input [31:0] a_in);
    pattern = {a_in[7:0] ^ 8'h5a, a_in[15:8] ^ 8'ha5} ^ {6'b0, a_in[21:12]};
  endfunction

  always @* final_word = burst_active && ((burst_count + 1) >= burst_target);
  always @* write_data = burst_reg ? burst_regval : pattern(burst_base + burst_count);

  always @(posedge clk) begin
    if (burst_active) begin
      if (read_ready) begin
        read_words[burst_count[4:0]] <= read_data;
        burst_count <= burst_count + 1;
      end else if (write_ready) begin
        burst_count <= burst_count + 1;
      end
    end
  end

  // A read strobe taken before the DEVICE has driven DQ at all. `READ_DATA` hunts
  // for RWDS going High, and the CA-period extra-latency request IS RWDS High --
  // so entering the data phase before that request has fallen latches it as a
  // strobe and clocks in a tristate bus. #342 named this hazard; the count is what
  // says whether it happens.
  reg     dev_drove_dq  = 1'b0;
  integer early_strobes = 0;
  always @(negedge csb) dev_drove_dq = 1'b0;
  always @(posedge clk or negedge clk) if (!csb && model.dq_oe) dev_drove_dq = 1'b1;
  always @(posedge clk)
    if (burst_active && read_ready && !dev_drove_dq) early_strobes = early_strobes + 1;

  //
  // Per-cycle trace, for arguing a beat count rather than asserting it.
  //
  always @(posedge clk)
    if ($test$plusargs("trace") && !csb)
      $display("[trc] cyc=%0d st=%0d csb=%0d ck=%0d rwds=%b rwds_i=%b adq=%h beat=%0d",
               cyc, state, csb, ck_pin, rwds, rwds_i_seen, adq, model.beat);

  //
  // Transactions.
  //
  task wait_idle;
    integer n;
    begin
      n = 0;
      while (!idle && n < RECOVER_LIMIT) begin @(posedge clk); n = n + 1; end
      if (!idle) begin
        $display("[tb] FAIL not idle after %0d cycles (state=%0d timed_out=%0d)",
                 n, state, timed_out);
        checks = checks + 1;
        errors = errors + 1;
      end
    end
  endtask

  task run_burst(input [31:0] addr, input reg_space, input is_write,
                 input integer nwords, input [15:0] regval);
    integer n;
    begin
      wait_idle;
      @(negedge clk);
      address        = addr;
      register_space = reg_space;
      perform_write  = is_write;
      single_page    = 1'b0;
      burst_base     = addr;
      burst_target   = nwords;
      burst_reg      = reg_space;
      burst_regval   = regval;
      burst_count    = 0;
      burst_active   = 1'b1;
      for (n = 0; n < 32; n = n + 1) read_words[n] = 16'hxxxx;
      start_transfer = 1'b1;
      @(negedge clk);
      start_transfer = 1'b0;

      n = 0;
      while (burst_count < nwords && n < XACT_LIMIT) begin
        @(posedge clk);
        n = n + 1;
      end
      if (burst_count < nwords) begin
        $display("[tb] FAIL %0s of %0d word(s) at %h stopped after %0d (state=%0d timed_out=%0d)",
                 is_write ? "write" : "read", nwords, addr, burst_count, state, timed_out);
        checks = checks + 1;
        errors = errors + 1;
      end
      wait_idle;
      burst_active = 1'b0;
      // `idle` rises while the transaction is still going out: every output is
      // three cycles behind the record. Anything read off the DEVICE has to wait
      // for the pins to catch up or it is reading the previous transaction.
      repeat (PIN_DRAIN) @(posedge clk);
    end
  endtask

  //
  // One cell: program CR0, tell the controller the same thing, write and read back.
  //
  integer    cell_errors;
  integer    cell_shift;
  integer    cell_early;
  integer    early_mark;
  reg [15:0] cr0_value;
  reg [8*8-1:0] mode_label;

  task run_cell(input integer code, input fixed, input [31:0] base);
    integer L, want_hl, long_hl, i, s, matched;
    reg device_long;
    begin
      cr0_value      = 16'h8f2f;
      cr0_value[7:4] = code[3:0];
      cr0_value[3]   = fixed;
      run_burst(ADDR_CR0, 1'b1, 1'b1, 1, cr0_value);
      if (model.cr0 !== cr0_value) begin
        $display("[tb] FAIL CR0 write [%0s code %0d] read %h, wanted %h",
                 mode_label, code, model.cr0, cr0_value);
        checks = checks + 1;
        errors = errors + 1;
      end

      L = (code >= 14) ? (code - 11) : (code + 5);
      @(negedge clk);
      latency_clocks     = 2 * L;
      low_latency_clocks = L;
      fixed_latency      = fixed;

      run_burst(base, 1'b0, 1'b1, BURST, 16'h0);
      early_mark = early_strobes;
      run_burst(base, 1'b0, 1'b0, BURST, 16'h0);
      cell_early = early_strobes - early_mark;

      // What the DEVICE decided for this transaction, from the model's own state.
      // The LONG count is floored: a read that starts its RWDS hunt before the
      // CA-period request has fallen catches the fall as a strobe. (#353)
      device_long = model.cr0[3] | model.take_long;
      long_hl     = ((2 * L) < `MIN_READ_LATENCY) ? `MIN_READ_LATENCY : (2 * L);
      want_hl     = device_long ? (long_hl - 1) : (L - 1);

      if (hl_count == long_hl - 1)    elections_long  = elections_long + 1;
      else if (hl_count == L - 1)     elections_short = elections_short + 1;

      if (hl_count != want_hl) elections_wrong = elections_wrong + 1;

      cell_errors = 0;
      for (i = 0; i < BURST; i = i + 1)
        if (read_words[i] !== pattern(base + i)) cell_errors = cell_errors + 1;
      if (cell_errors != 0) cells_with_data_errors = cells_with_data_errors + 1;

      // The shift, if there is one: how many words in the burst the first word
      // actually came from. `host 2L against device L` shows up as exactly L.
      cell_shift = -1;
      for (s = 0; s < 16; s = s + 1)
        if (cell_shift < 0 && read_words[0] === pattern(base + s)) cell_shift = s;

      checks = checks + 1;
      if (hl_count != want_hl) begin
        errors = errors + 1;
        $display("[tb] FAIL election [%0s code %0d] controller waited %0d cycles, device asked for %0d",
                 mode_label, code, hl_count, want_hl);
      end

      // A strobe taken before the device drove DQ is never right, at any latency
      // and in any mode: the word it clocks in comes off a tristate bus. (#353)
      checks = checks + 1;
      if (cell_early != 0) begin
        errors = errors + 1;
        $display("[tb] FAIL early strobe [%0s code %0d L=%0d] %0d read strobe(s) taken before the device drove DQ",
                 mode_label, code, L, cell_early);
      end

      // Word errors are graded only where the election was RIGHT. Where it was
      // wrong the data loss is a consequence of that, and how much of it the
      // caller's hunt absorbs is a property of the caller, not of the controller.
      checks = checks + 1;
      if (hl_count == want_hl && cell_errors != 0) begin
        errors = errors + 1;
        $display("[tb] FAIL data [%0s code %0d L=%0d] %0d of %0d words wrong on a correct election, shift %0d",
                 mode_label, code, L, cell_errors, BURST, cell_shift);
      end

      $display("[cell] float=%0s %0s code %2d L=%0d | controller waited %0d (%0s), device asked %0s | errors %0d/%0d shift %0d early %0d",
               (float_level === 1'bz) ? "z" : (float_level ? "1" : "0"),
               mode_label, code, L, hl_count,
               (hl_count == long_hl - 1) ? "LONG" : (hl_count == L - 1) ? "SHORT" : "??",
               device_long ? "LONG" : "SHORT",
               cell_errors, BURST, cell_shift, cell_early);
    end
  endtask

  integer ci, fi;
  integer codes [0:5];
  integer base_addr;

  initial begin
    if ($test$plusargs("dump")) begin
      $dumpfile("phy_rwds.vcd");
      $dumpvars(0, tb);
    end

    codes[0] = 0; codes[1] = 1; codes[2] = 2;
    codes[3] = 6; codes[4] = 14; codes[5] = 15;

    VCC = 1'b1;
    repeat (8) @(posedge clk);
    rst = 1'b0;
    // The model's own power-on wait, shortened at the instance above.
    #400;
    @(posedge clk);

    $display("[phy] ODDR output latency = %0d cycles (pulse in cycle %0d, out in %0d)",
             oddr_out_cyc - PROBE_CYC, PROBE_CYC, oddr_out_cyc);
    $display("[phy] IDDR input latency = %0d cycles (pulse in cycle %0d, out in %0d)",
             iddr_out_cyc - PROBE_CYC, PROBE_CYC, iddr_out_cyc);

    //
    // A single fixed-latency read, purely to measure the sample window.
    //
    mode_label = "fixed";
    float_level = 1'b0;
    latency_clocks = 14; low_latency_clocks = 7; fixed_latency = 1'b1;
    run_burst(32'h0000_0100, 1'b0, 1'b1, 1, 16'h0);
    run_burst(32'h0000_0100, 1'b0, 1'b0, 1, 16'h0);

    $display("[win] sample window: CS# falls at pin cycle %0d, first CK edge %0d, CA in %0d..%0d, device drives RWDS from %0d",
             cs_fall_cyc, ck_first_cyc, ck_first_cyc, ca_end_cyc, dev_drive_cyc);
    $display("[win] controller sampled RWDS in cycles %0d (SHIFT_COMMAND1) and %0d (SHIFT_COMMAND2), values %b and %b",
             sc1_cyc, sc2_cyc, sc1_sample, sc2_sample);
    $display("[win] those reflect pin cycles %0d and %0d, %0d cycles of IDDR behind",
             sc1_cyc - (iddr_out_cyc - PROBE_CYC), sc2_cyc - (iddr_out_cyc - PROBE_CYC),
             iddr_out_cyc - PROBE_CYC);
    checks = checks + 1;
    if (sc2_cyc - (iddr_out_cyc - PROBE_CYC) < cs_fall_cyc) begin
      $display("[win] VERDICT the controller's LAST RWDS sample is %0d pin cycle(s) BEFORE CS# falls -- it cannot have seen the device at all",
               cs_fall_cyc - (sc2_cyc - (iddr_out_cyc - PROBE_CYC)));
    end else if (sc2_cyc - (iddr_out_cyc - PROBE_CYC) < dev_drive_cyc) begin
      $display("[win] VERDICT the last sample lands after CS# but before the device drives RWDS -- inside tDSV, a float");
    end else begin
      $display("[win] VERDICT the last sample lands on a driven RWDS, in the CA window");
    end

    //
    // The sweep. Two float levels, two latency modes, six codes.
    //
    base_addr = 32'h0000_1000;
    for (fi = 0; fi < 2; fi = fi + 1) begin
      float_level = (fi == 1);
      for (ci = 0; ci < 6; ci = ci + 1) begin
        mode_label = "fix";
        run_cell(codes[ci], 1'b1, base_addr); base_addr = base_addr + 32;
        mode_label = "var";
        run_cell(codes[ci], 1'b0, base_addr); base_addr = base_addr + 32;
      end
    end

    // Put the part back where it started, so a following run is not reading a
    // register this one left behind.
    float_level = 1'b0;
    latency_clocks = 14; low_latency_clocks = 7; fixed_latency = 1'b1;
    run_burst(ADDR_CR0, 1'b1, 1'b1, 1, 16'h8f2f);

    $display("[tb] elections: %0d LONG, %0d SHORT, %0d disagreed with the device (REFRESH_EVERY=%0d)",
             elections_long, elections_short, elections_wrong, `REFRESH_EVERY);
    $display("[tb] data: %0d of %0d cells came back wrong; %0d read strobes taken before the device drove DQ",
             cells_with_data_errors, 24, early_strobes);
    $display("[tb] %0d checks, %0d errors", checks, errors);
    $finish;
  end

endmodule
