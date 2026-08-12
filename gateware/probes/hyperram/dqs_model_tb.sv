// The DQS controller, elaborated, against a model of the part -- with the ECP5
// primitives replaced by a behavioural 4:1 PHY.
//
// #186 asks whether the DQS read path can align at any latency. The obstacle is
// that HyperRAMDQSPHY is nothing but ODDRX2F/IDDRX2DQA/DQSBUFM/DDRDLLA, which no
// open simulator has models for. scripts/soc_hyperram_sim.py already answers that
// by driving the controller through a behavioural stand-in; this does the same,
// but against `hyperram_model.v` -- a device that decodes the CA itself and says
// which address it served, rather than a Python model built from the same idea of
// the bus as the controller.
//
// ## What the shim reproduces, and from where
//
// Straight out of `gateware/soc/peripherals/hyperram_dqs_phy.py`:
//
//   * CK  from ODDRX2F, i_D0=0, i_D1=clk_en[1], i_D2=0, i_D3=clk_en[0].
//     Four output phases per `sync` cycle -> 2 CK per cycle, edges on phases
//     0,1,2,3 once running, and only 3 edges on the first enabled cycle because
//     phase 0 repeats the level the disabled clock was already at.
//   * DQ  from ODDRX2DQA, i_D0=dq.o[31:24] .. i_D3=dq.o[7:0], so the byte order
//     is MSB first and the four bytes of one fabric word occupy phases 0..3 of
//     ONE cycle. That is the fact the whole parity question turns on.
//   * CS# and RESET# registered, all four phases the same value.
//
// ## What it CANNOT reproduce, and it matters
//
//   * **the SCLK-to-pin latency of each primitive.** ODDRX2F (clock/CS) and
//     ODDRX2DQA via DQSW270 (data) need not have the same pipeline depth, and
//     nothing open says what they are. So the relative offset is a PARAMETER
//     here -- `+ck_pipe` and `+dq_pipe`, in whole `sync` cycles -- and the run
//     reports which settings make the device decode the address it was asked
//     for. A setting that does not is not a result about the design.
//   * **DQSBUFM.** The read grouping below is anchored to the `sync` cycle, which
//     is what the fabric consumes; the real DQSBUFM anchors it to RWDS and can be
//     moved by READCLKSEL. `+rd_slip` shifts the grouping by whole device edges
//     so the size of the correction can be stated.
//   * anything analogue: skew, DELAYG taps, the fabric-routed ECLK of #314.
//
// Driven by scripts/hyperram_dqs_model_sim.py. See #186, #314, #342.
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

`ifndef DUT_MODULE
  `define DUT_MODULE hyperram_model
`endif

module tb;

  // One `sync` cycle is 2 CK at 4:1 gearing, and one phase is one device edge.
  localparam real TPH   = 5.0;             // 100 MHz CK
  localparam real TSYNC = 4.0 * TPH;       // 50 MHz fabric
  localparam real TSET  = 0.1;             // sample the controller past its own edge

  // Cycles to wait for a transaction to finish.
  //
  // Waits for: `idle` to come back after `start_transfer`.
  // Expected worst case: the longest fixed latency reachable here is 24 CK (the
  //   reserved code 7), which is 12 cycles, plus command, recovery and the FSM
  //   edges -- under 32 cycles.
  // Multiplier: 4x, not 1.25x. A controller that never leaves READ_DATA is one of
  //   the outcomes being measured, and the run must reach the report line rather
  //   than hang; 128 cycles is 2.56 us, inside the 4 us tCSM.
  // On expiry: the line reports `hung=1` and the read data is whatever was last
  //   presented.
  localparam integer WAIT_CYCLES = 128;

  //
  // Clocking. The testbench owns time: `sync` here, phases and CK below.
  //
  reg sync_clk = 1'b0;
  initial forever begin
    #(TSYNC/2.0) sync_clk = 1'b1;
    #(TSYNC/2.0) sync_clk = 1'b0;
  end

  reg rst = 1'b1;

  //
  // The device.
  //
  reg        clk = 1'b0;
  wire       clk_n = ~clk;
  reg        VCC = 1'b0;
  reg        VSS = 1'b0;
  reg        resetb = 1'b1;

  reg  [7:0] adq_drv = 8'h00;
  reg        adq_oe  = 1'b0;
  wire [7:0] adq = adq_oe ? adq_drv : 8'hzz;

  reg        rwds_drv = 1'b0;
  reg        rwds_oe  = 1'b0;
  wire       rwds = rwds_oe ? rwds_drv : 1'bz;

  reg        csb = 1'b1;

  `DUT_MODULE u_ram (
    .adq(adq), .clk(clk), .clk_n(clk_n), .csb(csb),
    .rwds(rwds), .VCC(VCC), .VSS(VSS), .resetb(resetb)
  );

  //
  // The controller, elaborated from Amaranth.
  //
  reg  [31:0] address = 32'h0;
  reg         register_space = 1'b0;
  reg         perform_write = 1'b0;
  reg         single_page = 1'b0;
  reg         start_transfer = 1'b0;
  reg         final_word = 1'b0;
  reg  [3:0]  latency_clocks = 4'd5;
  // Wired only so it is not `x`: every run here is fixed latency. (#380)
  reg  [3:0]  low_latency_clocks = 4'd3;
  reg         fixed_latency = 1'b1;
  reg  [31:0] write_data = 32'h0;

  wire        idle, read_ready, write_ready, timed_out;
  wire [3:0]  state;
  wire [31:0] read_data;

  wire        phy_cs;
  wire [1:0]  phy_clk_en;
  wire [31:0] phy_dq_o;
  wire        phy_dq_e;
  wire [3:0]  phy_rwds_o;
  wire        phy_rwds_e;
  wire [1:0]  phy_read;

  reg  [31:0] phy_dq_i = 32'h0;
  reg  [3:0]  phy_rwds_i = 4'h0;
  reg         phy_datavalid = 1'b0;
  reg         phy_burstdet = 1'b0;

  dqs_controller u_ctl (
    .clk(sync_clk), .rst(rst),
    .address(address), .register_space(register_space),
    .perform_write(perform_write), .single_page(single_page),
    .start_transfer(start_transfer), .final_word(final_word),
    .latency_clocks(latency_clocks), .fixed_latency(fixed_latency),
    .low_latency_clocks(low_latency_clocks),
    .write_data(write_data),
    .idle(idle), .read_ready(read_ready), .write_ready(write_ready),
    .timed_out(timed_out), .state(state), .read_data(read_data),
    .phy_cs(phy_cs), .phy_clk_en(phy_clk_en), .phy_dq_o(phy_dq_o),
    .phy_dq_e(phy_dq_e), .phy_rwds_o(phy_rwds_o), .phy_rwds_e(phy_rwds_e),
    .phy_read(phy_read),
    .phy_dq_i(phy_dq_i), .phy_rwds_i(phy_rwds_i),
    .phy_datavalid(phy_datavalid), .phy_burstdet(phy_burstdet)
  );

  //
  // The behavioural 4:1 PHY.
  //
  integer dq_pipe = 0;      // whole `sync` cycles of delay on the ODDRX2DQA path
  integer ck_pipe = 0;      // ...and on the ODDRX2F path (clock, CS#)
  // Device edges (quarter cycles) of delay on DQ relative to CK. The ODDRX2F
  // pattern [0, ce1, 0, ce0] puts CK's transitions at the starts of phases 1,2,3
  // and 0-of-the-next -- so with no phase offset the first fabric byte, which sits
  // in phase 0, has no edge to be taken on. DQSW270 is 270 degrees of ECLK and
  // ECLK is two phases, so a phase or two of skew between the paths is exactly
  // what the primitives could be doing; nothing open says which.
  integer dq_ph   = 0;
  integer rd_slip = 0;      // further device edges to shift the read grouping by
  integer verbose = 0;

  // Deep enough for the offsets the sweep uses; index 0 is "this cycle".
  reg [31:0] dq_o_hist  [0:7];
  reg        dq_e_hist  [0:7];
  reg [3:0]  rwds_o_hist[0:7];
  reg        rwds_e_hist[0:7];
  reg [1:0]  clk_en_hist[0:7];
  reg        cs_hist    [0:7];

  integer h;
  initial for (h = 0; h < 8; h = h + 1) begin
    dq_o_hist[h] = 32'h0; dq_e_hist[h] = 1'b0;
    rwds_o_hist[h] = 4'h0; rwds_e_hist[h] = 1'b0;
    clk_en_hist[h] = 2'b00; cs_hist[h] = 1'b0;
  end

  // Where in the `sync` cycle the current device edge sits. 0..3, and phase 0 is
  // the first quarter of a cycle -- so a data phase that starts on phase 0 packs
  // into one 32-bit fabric word and one that starts on phase 2 straddles two.
  integer ph = 0;
  // Device edges since CS# fell, in the DEVICE's own frame.
  integer edge_idx = -1;
  integer ca_first_edge = -1;   // first edge on which the host drove DQ
  integer ca_first_ph   = -1;
  integer data_first_edge = -1; // first edge on which the DEVICE drove DQ
  integer data_first_ph   = -1;

  // Read capture: four device edges make one fabric word.
  reg [7:0] rd_bytes [0:3];
  reg       rd_valid [0:3];
  integer   rd_fill = 0;
  reg [31:0] rd_word = 32'h0;
  reg        rd_word_valid = 1'b0;
  reg        ck_was = 1'b0;
  // The address the DEVICE resolved from the CA it was given -- the absolute
  // reference #186 asks for, and the reason the device model is here rather than
  // a second Python model of the same idea of the bus.
  reg [21:0] served = 22'h3f_ffff;

  task capture_edge(input integer slot);
    begin
      rd_bytes[slot] = adq;
      rd_valid[slot] = (adq !== 8'hzz) && (adq !== 8'hxx) && !adq_oe;
    end
  endtask

  integer p, e;
  reg [7:0]  byte_v [0:3];
  reg        oe_v   [0:3];
  reg        rwv    [0:3];
  reg        rwoe_v [0:3];
  reg        ck_v   [0:3];
  reg [31:0] dq_o_e,  dq_o_prev  = 32'h0;
  reg        dq_e_e,  dq_e_prev  = 1'b0;
  reg [3:0]  rwds_o_e, rwds_o_prev = 4'h0;
  reg        rwds_e_e, rwds_e_prev = 1'b0;
  reg [1:0]  clk_en_e;
  reg        cs_e;
  integer    slot, k;

  function [7:0] word_byte(input [31:0] w, input integer idx);
    begin
      case (idx)
        0: word_byte = w[31:24];
        1: word_byte = w[23:16];
        2: word_byte = w[15:8];
        default: word_byte = w[7:0];
      endcase
    end
  endfunction

  always @(posedge sync_clk) begin : serialise
    #(TSET);
    for (h = 7; h > 0; h = h - 1) begin
      dq_o_hist[h]  = dq_o_hist[h-1];  dq_e_hist[h]   = dq_e_hist[h-1];
      rwds_o_hist[h]= rwds_o_hist[h-1];rwds_e_hist[h] = rwds_e_hist[h-1];
      clk_en_hist[h]= clk_en_hist[h-1];cs_hist[h]     = cs_hist[h-1];
    end
    dq_o_hist[0] = phy_dq_o;   dq_e_hist[0] = phy_dq_e;
    rwds_o_hist[0] = phy_rwds_o; rwds_e_hist[0] = phy_rwds_e;
    clk_en_hist[0] = phy_clk_en; cs_hist[0] = phy_cs;

    dq_o_e   = dq_o_hist[dq_pipe];
    dq_e_e   = dq_e_hist[dq_pipe];
    rwds_o_e = rwds_o_hist[dq_pipe];
    rwds_e_e = rwds_e_hist[dq_pipe];
    clk_en_e = clk_en_hist[ck_pipe];
    cs_e     = cs_hist[ck_pipe];

    // CS# is active low at the pad; the record carries it active high.
    if (csb && !cs_e) edge_idx = -1;      // a new transaction begins
    csb = ~cs_e;

    // The four bytes of one fabric word, shifted by `dq_ph` phases; what falls off
    // the front comes from the word before it.
    for (k = 0; k < 4; k = k + 1) begin
      if (k >= dq_ph) begin
        byte_v[k]  = word_byte(dq_o_e, k - dq_ph);
        oe_v[k]    = dq_e_e;
        rwv[k]     = rwds_o_e[3 - (k - dq_ph)];
        rwoe_v[k]  = rwds_e_e;
      end else begin
        byte_v[k]  = word_byte(dq_o_prev, 4 + k - dq_ph);
        oe_v[k]    = dq_e_prev;
        rwv[k]     = rwds_o_prev[3 - (4 + k - dq_ph)];
        rwoe_v[k]  = rwds_e_prev;
      end
    end
    dq_o_prev = dq_o_e; dq_e_prev = dq_e_e;
    rwds_o_prev = rwds_o_e; rwds_e_prev = rwds_e_e;

    ck_v[0] = 1'b0; ck_v[1] = clk_en_e[1]; ck_v[2] = 1'b0; ck_v[3] = clk_en_e[0];

    // Four phases of exactly TPH, and the block must END before the next fabric
    // edge or it re-triggers late and misses every second cycle -- which presents
    // as a controller that alternates between working and hanging.
    for (p = 0; p < 4; p = p + 1) begin
      ph      = p;
      adq_drv = byte_v[p];
      adq_oe  = oe_v[p];
      rwds_oe = rwoe_v[p];
      rwds_drv= rwv[p];
      #(TPH/2.0);
      // The clock edge sits mid-eye, which is what DELAYG(DQS_CMD_CLK) is for.
      ck_was = clk;
      clk = ck_v[p];
      #0.5;                         // past the device's own delta
      if (ck_was !== clk && !csb) begin
        edge_idx = edge_idx + 1;
        if (ca_first_edge < 0 && adq_oe) begin
          ca_first_edge = edge_idx; ca_first_ph = p;
        end
        if (data_first_edge < 0 && !adq_oe && adq !== 8'hzz && adq !== 8'hxx) begin
          data_first_edge = edge_idx; data_first_ph = p;
        end
        // The CA is decoded on edge 5, so edge 6 is the first moment the address
        // the DEVICE resolved can be read out.
        if (edge_idx == 6) served = u_ram.word_addr;
        // The read grouping is anchored where the WRITE grouping is: a fabric word
        // starts at the phase that carries byte 0. `rd_slip` moves it, which is
        // what a BitSlip in the PHY would do.
        slot = (p + 8 - ((dq_ph + rd_slip) % 4)) % 4;
        capture_edge(slot);
        if (slot == 3) begin
          rd_word = {rd_bytes[0], rd_bytes[1], rd_bytes[2], rd_bytes[3]};
          rd_word_valid = rd_valid[0] & rd_valid[1] & rd_valid[2] & rd_valid[3];
        end
        if (verbose)
          $display("[edge] e=%0d ph=%0d adq=%h oe=%0d rwds=%b st=%0d dv=%0d",
                   edge_idx, p, adq, adq_oe, rwds, state, phy_datavalid);
      end
      if (p < 3) #(TPH/2.0 - 0.5);
    end
  end

  // Hand the assembled word back on the next fabric edge, which is where the
  // IDDRX2DQA output would land. `datavalid` follows the DEVICE driving and NOT
  // the controller's own `read` window -- the generous case, so a misalignment
  // reported here cannot be an artefact of a stingy handshake.
  // RWDS FLOAT, GIVEN A VALUE. `rwds === 1'b1` reads an undriven line as a firm
  // 0, so without this a sample landing in a float is invisible here.
  // `+rwds_float_pct=<0..100>` is the chance a float reads High,
  // `+rwds_float_seed` the stream. Default 0 never floats High. (#400)
  integer rwds_float_pct  = 0;
  integer rwds_float_seed = 1;
  integer rwds_floats     = 0;
  integer rwds_float_hi   = 0;
  reg     rwds_bit;

  always @(posedge sync_clk) begin
    if (rwds === 1'b1)      rwds_bit = 1'b1;
    else if (rwds === 1'b0) rwds_bit = 1'b0;
    else begin
      rwds_floats = rwds_floats + 1;
      rwds_bit    = (({$random(rwds_float_seed)} % 100) < rwds_float_pct);
      if (rwds_bit) rwds_float_hi = rwds_float_hi + 1;
    end
    phy_dq_i      <= rd_word;
    phy_datavalid <= rd_word_valid;
    phy_burstdet  <= rd_word_valid;
    phy_rwds_i    <= {4{rwds_bit}};
  end

  //
  // The experiment.
  //
  integer   cycles;
  reg       hung;
  reg [31:0] got;
  reg       got_valid;
  integer   code, fixed, n, i, base, expect_ck, slip;
  reg [15:0] want_hi, want_lo;
  integer   errors = 0;
  reg [15:0] cr0_v;

  localparam [31:0] READ_ADDR = 32'h0000_0100;

  task do_read(input [31:0] a);
    begin
      ca_first_edge = -1; ca_first_ph = -1;
      data_first_edge = -1; data_first_ph = -1;
      served = 22'h3f_ffff;
      got = 32'hxxxx_xxxx; got_valid = 1'b0; hung = 1'b1;
      // Wait out whatever the previous read left running. A transaction the
      // watchdog is still unwinding swallows the next `start_transfer`, and the
      // run then alternates between measuring and measuring nothing.
      for (cycles = 0; cycles < 4 * WAIT_CYCLES; cycles = cycles + 1) begin
        @(posedge sync_clk); #(TSET/2.0);
        if (idle) cycles = 4 * WAIT_CYCLES;
      end
      @(posedge sync_clk); #(TSET/2.0);
      address = a; perform_write = 1'b0; register_space = 1'b0;
      final_word = 1'b1; start_transfer = 1'b1;
      @(posedge sync_clk); #(TSET/2.0);
      start_transfer = 1'b0;
      for (cycles = 0; cycles < WAIT_CYCLES; cycles = cycles + 1) begin
        @(posedge sync_clk); #(TSET/2.0);
        if (read_ready && !got_valid) begin got = read_data; got_valid = 1'b1; end
        if (idle && cycles > 2) begin hung = 1'b0; cycles = WAIT_CYCLES; end
      end
      final_word = 1'b0;
    end
  endtask

  function integer base_ck(input integer c);
    begin base_ck = (c >= 14) ? (5 + c - 16) : (5 + c); end
  endfunction

  initial begin
    if (!$value$plusargs("dq_pipe=%d", dq_pipe)) dq_pipe = 0;
    if (!$value$plusargs("ck_pipe=%d", ck_pipe)) ck_pipe = 0;
    if (!$value$plusargs("dq_ph=%d", dq_ph)) dq_ph = 0;
    if (!$value$plusargs("rd_slip=%d", rd_slip)) rd_slip = 0;
    if (!$value$plusargs("verbose=%d", verbose)) verbose = 0;
    if (!$value$plusargs("rwds_float_pct=%d", rwds_float_pct)) rwds_float_pct = 0;
    if (!$value$plusargs("rwds_float_seed=%d", rwds_float_seed)) rwds_float_seed = 1;

    VCC = 1'b1; VSS = 1'b0; resetb = 1'b1;
    #200_000;
    // The memory is preloaded rather than written: a write through the same
    // controller would be judged by the same alignment this file is measuring,
    // and a fault that cancelled itself would read as a pass. Hierarchical, so
    // this half of the file is the open twin's only.
    for (i = 0; i < 64; i = i + 1) u_ram.memory[32'h100 + i] = 16'h1000 + i[15:0];
    rst = 1'b0;
    @(posedge sync_clk);

    $display("[dqs] shim: dq_pipe=%0d ck_pipe=%0d dq_ph=%0d rd_slip=%0d",
             dq_pipe, ck_pipe, dq_ph, rd_slip);

    for (fixed = 1; fixed >= 0; fixed = fixed - 1) begin
      for (code = 0; code <= 2; code = code + 1) begin
        cr0_v = 16'h8f2f;
        cr0_v[7:4] = code[3:0];
        cr0_v[3]   = fixed[0];
        u_ram.cr0 = cr0_v;              // twin only; the controller has no part in it
        base = base_ck(code);
        expect_ck = fixed ? 2 * base : base;
        fixed_latency = fixed[0];

        for (n = 0; n <= 12; n = n + 1) begin
          latency_clocks = n[3:0];
          do_read(READ_ADDR);
          // memory[READ_ADDR + k] holds 16'h1000 + k, so an aligned read returns
          // the pair {1000, 1001}. `slip` is how many DEVICE words late the
          // controller's word starts -- 1 is the "reads one word late" of #186,
          // and an odd slip means the 32-bit group straddles a device word.
          want_hi = 16'h1000;
          want_lo = 16'h1001;
          slip = 99;
          if (got_valid) begin
            for (i = -4; i <= 8; i = i + 1) begin
              if (slip == 99 &&
                  got === {16'h1000 + i[15:0], 16'h1000 + i[15:0] + 16'd1})
                slip = i;
            end
          end
          $display("[dqs] code=%0d fixed=%0d dev_ck=%0d n=%0d ca_edge=%0d ca_ph=%0d data_edge=%0d data_ph=%0d served=%0h got=%h want=%h slip=%0d hung=%0d",
                   code, fixed, expect_ck, n,
                   ca_first_edge, ca_first_ph, data_first_edge, data_first_ph,
                   served, got, {want_hi, want_lo}, slip, hung);
        end
      end
    end

    // The injection's own witness: a run asking for floats that met none injected
    // nothing, and a check passing under it proves nothing. (#400)
    $display("[dqs] floats=%0d float_high=%0d float_pct=%0d float_seed=%0d",
             rwds_floats, rwds_float_hi, rwds_float_pct, rwds_float_seed);
    $display("[dqs] === done ===");
    $finish;
  end

endmodule
