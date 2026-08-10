// The ceiling engine's CONFIG path, then a data burst -- does the data burst
// address REGISTER space, and does it return register contents? #349.
//
// The board reports data reads coming back as `0xff81ff81` (dif) or `0xffc1ffc1`
// (se) -- exactly the two CR1 values `firmware/cynthion-soc/src/bist.rs` writes,
// differing only in bit 6. Two mechanisms produce that and they want opposite
// fixes:
//
//   A. the data transaction ADDRESSES register space -- a leak of `configuring`,
//      `is_register` or the address latch out of the CONFIG_VERIFY states, or a
//      CS# that never rises so the part never sees a new transaction;
//   B. the data transaction addresses MEMORY correctly and the read path returns
//      the LAST CAPTURED WORD, which is CONFIG_VERIFY_CR1's. IDDRX2DQA's outputs
//      hold; `datavalid` comes from the READ window, not from data arriving. A
//      window that misses the burst hands back whatever was there.
//
// The device model decodes the CA itself and exposes `is_register`, so A is
// settled by asking the DEVICE rather than by reading the controller. B is
// produced on demand by mistiming `latency_clocks` against the part's CR0 --
// which is not a hypothetical: `hyperram_ceiling_top.py` drives neither
// `latency_clocks` nor `fixed_latency` on the DQS path (that block sits inside
// the non-DQS `else`), so every cell whose CR0 latency is not the built-in
// constant runs exactly this way.
//
// The sequence below is transcribed from `hyperram_ceiling_top.py`'s FSM --
// CONFIG_CR0, CONFIG_CR0_WAIT, CONFIG_CR1, CONFIG_CR1_WAIT, CONFIG_VERIFY,
// CONFIG_VERIFY_WAIT, CONFIG_VERIFY_CR1, CONFIG_VERIFY_CR1_WAIT, WRITE_START,
// WRITE, WRITE_RECOVER, READ_START, READ -- including the handshake shape
// (`start = idle` in the config states, `start = 1` unconditionally in
// WRITE_START and READ_START).
//
// Driven by scripts/hyperram_dqs_model_sim.py --stage config.
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

`ifndef DUT_MODULE
  `define DUT_MODULE hyperram_model
`endif

module tb;

  localparam real TPH   = 5.0;             // 100 MHz CK
  localparam real TSYNC = 4.0 * TPH;       // 50 MHz fabric
  localparam real TSET  = 0.1;

  // Cycles to wait for one transaction. Waits for `idle` after `start_transfer`.
  // Expected worst case: the longest latency reachable here is 24 CK = 12 cycles,
  // plus command, the 128-word burst and recovery -- under 200 cycles. 4x that,
  // not 1.25x, because a controller parked in READ_DATA is one of the outcomes
  // being measured and the run must reach its report line. On expiry the row
  // carries `hung=1`.
  localparam integer WAIT_CYCLES = 800;

  // tCSHI at the grade fitted -- a `6I` = T166, 6 ns in Winbond's `Config-AC.v`.
  // Was 10.0, the T100 column. A minimum recovery, not a timeout. (#341, #364)
  localparam real T_CSHI_NS = 6.0;

  localparam [31:0] DATA_ADDR = 32'h0000_0100;
  localparam [31:0] CR0_ADDR  = 32'h0000_0800;
  localparam [31:0] CR1_ADDR  = 32'h0000_0801;

  // Words per data burst. Short: this measures WHICH SPACE a burst addressed and
  // what it returned, not throughput, and 128 would be 128x the simulation for
  // the same answer.
  localparam integer BURST_WORDS = 4;

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
  reg  [3:0]  latency_clocks = 4'd6;
  // The SHORT count, when the part declines the extra latency. Reset is the old
  // class constant; `run_sequence` drives it per code. (#380)
  reg  [3:0]  low_latency_clocks = 4'd3;
  reg         fixed_latency = 1'b1;
  reg  [31:0] write_data = 32'h0;

  wire        idle, read_ready, write_ready, timed_out, extra_latency;
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
    .extra_latency(extra_latency),
    .phy_cs(phy_cs), .phy_clk_en(phy_clk_en), .phy_dq_o(phy_dq_o),
    .phy_dq_e(phy_dq_e), .phy_rwds_o(phy_rwds_o), .phy_rwds_e(phy_rwds_e),
    .phy_read(phy_read),
    .phy_dq_i(phy_dq_i), .phy_rwds_i(phy_rwds_i),
    .phy_datavalid(phy_datavalid), .phy_burstdet(phy_burstdet)
  );

  //
  // The behavioural 4:1 PHY, from `hyperram_dqs_phy.py`'s own primitive mapping.
  // Same shim as `dqs_model_tb.sv`; the offsets default to the ONE combination
  // that file's sweep found the device decoding a CA in.
  //
  integer dq_pipe = 0;
  integer ck_pipe = 1;
  integer dq_ph   = 1;
  integer rd_slip = 0;
  integer verbose = 0;

  // THE CAPTURE MISSES, on demand. `capture_frozen` holds the read capture
  // registers, which is what a READCLKSEL phase outside the window does to
  // DQSBUFM: no strobe reaches IDDRX2DQA, its outputs keep the last group, and
  // DATAVALID still fires because it comes from the READ window (#358, #366).
  //
  // `+cap_miss=<mask>` selects which reads it happens on, per sequence:
  //   1 rd_cr0   2 rd_cr1   4 rd_cr0b (the re-read)   8 mem_rd
  reg     capture_frozen = 1'b0;
  integer cap_miss = 0;

  // THE CAPTURE LAGS BY ONE TRANSACTION. `+cap_lag=1` hands the controller the
  // word the PREVIOUS transaction captured, which is the board's #366
  // fingerprint: CR1 read back as the previous CR0, and every claim decoded out
  // of it -- latency code, drive, "the part is asleep" -- wrong with it.
  //
  // Distinct from a freeze: every read still returns a plausible replicated
  // register word, so nothing about any one word says it is the wrong one.
  // Seeded with a plausible register word rather than zero: the lag's FIRST
  // read must not be caught for free by the halves rule, or the control is the
  // easy case instead of the board's.
  integer    cap_lag = 0;
  reg [31:0] rd_word_lagged = 32'hff81ff81;

  // Does the engine re-read CR0 after CR1? `0` is the sequence the engine had
  // when #366 was filed, and it is the NEGATIVE CONTROL: with a capture miss on
  // the CR1 read there is nothing in the transcript that can mark the reported
  // CR1 as fabricated, so the checks in the runner must fire on it.
  integer verify_reread = 1;

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

  integer ph = 0;
  integer edge_idx = -1;
  // WHERE THE READ WINDOW LANDS, in device CK edges from CS# falling. `read_edge`
  // is the controller entering READ_DATA, `data_edge` the device first driving
  // DQ. The word alone is a weak instrument for this: with DATAVALID from the
  // device, a controller that entered late can still be handed the first word.
  // (#380, #381)
  localparam integer STATE_READ_DATA = 6;   // `HyperRAMDQSController.STATES`
  integer read_edge = -1;
  integer data_edge = -1;
  // The same two, held past CS# rising so the report line can carry them.
  integer held_read_edge = -1;
  integer held_data_edge = -1;

  reg [7:0] rd_bytes [0:3];
  reg       rd_valid [0:3];
  reg [31:0] rd_word = 32'h0;
  reg        rd_word_valid = 1'b0;
  reg        ck_was = 1'b0;

  // What the DEVICE decoded, latched one edge after the CA completes. These are
  // the absolute reference: they say which space the part thinks it was asked
  // for, which no amount of reading the controller can.
  reg [21:0] served = 22'h3f_ffff;
  reg        served_register = 1'bx;
  reg        served_read = 1'bx;

  // CS# High time before the transaction now running, in ns.
  real cs_high_since = 0.0;
  real cs_high_ns = -1.0;

  integer p, k;
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
  integer    slot;

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

    // CS# is active low at the pad; the record carries it active high. The High
    // time before each falling edge is the tCSHI measurement.
    if (csb && !cs_e) begin
      edge_idx = -1;
      read_edge = -1;
      data_edge = -1;
      cs_high_ns = $realtime - cs_high_since;
      served_register = 1'bx;
      served_read = 1'bx;
    end
    if (!csb && cs_e) cs_high_since = $realtime;
    csb = ~cs_e;

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

    for (p = 0; p < 4; p = p + 1) begin
      ph      = p;
      adq_drv = byte_v[p];
      adq_oe  = oe_v[p];
      rwds_oe = rwoe_v[p];
      rwds_drv= rwv[p];
      #(TPH/2.0);
      ck_was = clk;
      clk = ck_v[p];
      #0.5;
      if (ck_was !== clk && !csb) begin
        edge_idx = edge_idx + 1;
        if (state == STATE_READ_DATA && read_edge < 0) begin
          read_edge = edge_idx; held_read_edge = edge_idx;
        end
        if (u_ram.dq_oe && u_ram.is_read && data_edge < 0) begin
          data_edge = edge_idx; held_data_edge = edge_idx;
        end
        // The CA is decoded on edge 5, so edge 6 is the first moment the space
        // and the address the DEVICE resolved can be read out.
        if (edge_idx == 6) begin
          served = u_ram.word_addr;
          served_register = u_ram.is_register;
          served_read = u_ram.is_read;
        end
        slot = (p + 8 - ((dq_ph + rd_slip) % 4)) % 4;
        // FROZEN means the capture registers do not update, so what the
        // controller is handed is whatever the LAST transaction left there.
        if (!capture_frozen) begin
          rd_bytes[slot] = adq;
          rd_valid[slot] = (adq !== 8'hzz) && (adq !== 8'hxx) && !adq_oe;
          if (slot == 3) begin
            rd_word = {rd_bytes[0], rd_bytes[1], rd_bytes[2], rd_bytes[3]};
            rd_word_valid = rd_valid[0] & rd_valid[1] & rd_valid[2] & rd_valid[3];
          end
        end
        if (verbose)
          $display("[edge] e=%0d ph=%0d adq=%h oe=%0d st=%0d", edge_idx, p, adq,
                   adq_oe, state);
      end
      if (p < 3) #(TPH/2.0 - 0.5);
    end
  end

  // THE HOLD IS THE POINT. `rd_word` is only reassigned when a fourth device edge
  // completes a group, so between groups it keeps the last one -- which is what
  // IDDRX2DQA's output registers do.
  //
  // `+dv_from_read` chooses where DATAVALID comes from, and the two settings are
  // the whole experiment:
  //
  //   0  from the DEVICE driving DQ. The GENEROUS case, and `dqs_model_tb.sv`'s.
  //      A misalignment reported under it cannot be an artefact of a stingy
  //      handshake -- but it also cannot show a read that returns stale data,
  //      because nothing is handed back unless something arrived.
  //   1  from the controller's own READ window, which is what DQSBUFM does:
  //      FPGA-TN-02035-1.3 s6.2.4 -- DATAVALID is generated from the internal
  //      READ pulse, and BURSTDET is the separate signal that says whether that
  //      pulse found a strobe. So a window that misses the burst still asserts
  //      DATAVALID, over data registers holding the last group they captured.
  //
  // Neither is DQSBUFM. 1 is a SKETCH of its handshake and is here to show what
  // that handshake does with a mistimed window, not to model the primitive.
  integer dv_from_read = 0;

  always @(posedge sync_clk) begin
    phy_dq_i      <= cap_lag ? rd_word_lagged : rd_word;
    phy_datavalid <= dv_from_read ? (phy_read != 2'b00) : rd_word_valid;
    phy_burstdet  <= rd_word_valid;
    phy_rwds_i    <= {4{rwds === 1'b1}};
  end

  //
  // The sequence, transcribed from the engine FSM.
  //
  integer   cycles, i, words;
  reg       hung;
  // The controller's own election, latched: `extra_latency` is set at the sample
  // and held to the end of the transaction. (#381)
  reg       elected_long;
  reg [31:0] got_first;
  reg       got_valid;
  integer   errors = 0;
  reg [15:0] cr0_v, cr1_v;
  integer   leak_control;
  integer   dev_latency;         // the part's latency in this run, in CK
  integer   base_latency;        // L, the CR0[7:4] code undoubled, in CK
  integer   ctl_latency;         // what the controller was told, in 2-CK cycles
  integer   refresh_every = 100; // the model's, mirrored so `dev_latency` knows
  integer   latency_mode = 0;    // 0 = the fixed sequences, 1 = the variable ones
  integer   low_const = -1;      // force the short count, or -1 for per-code

  task wait_idle;
    begin
      for (cycles = 0; cycles < WAIT_CYCLES; cycles = cycles + 1) begin
        @(posedge sync_clk); #(TSET/2.0);
        if (idle) cycles = WAIT_CYCLES;
      end
    end
  endtask

  // One register transaction, the engine's handshake: `start` gated on `idle`,
  // held through the WAIT state, `final_word` high because one word IS the
  // transaction.
  task reg_txn(input [31:0] a, input is_write, input [15:0] d, input [63:0] tag,
               input freeze);
    begin
      wait_idle;
      got_valid = 1'b0; got_first = 32'hxxxx_xxxx; hung = 1'b1;
      elected_long = 1'b0;
      held_read_edge = -1; held_data_edge = -1;
      capture_frozen = freeze;
      @(posedge sync_clk); #(TSET/2.0);
      address = a; register_space = 1'b1; perform_write = is_write;
      write_data = {16'h0, d}; final_word = 1'b1; start_transfer = 1'b1;
      @(posedge sync_clk); #(TSET/2.0);
      start_transfer = 1'b0;
      for (cycles = 0; cycles < WAIT_CYCLES; cycles = cycles + 1) begin
        @(posedge sync_clk); #(TSET/2.0);
        if (extra_latency) elected_long = 1'b1;
        if (read_ready && !got_valid) begin
          got_first = read_data; got_valid = 1'b1;
        end
        if (idle && cycles > 2) begin hung = 1'b0; cycles = WAIT_CYCLES; end
      end
      final_word = 1'b0; register_space = 1'b0; capture_frozen = 1'b0;
      if (!is_write) rd_word_lagged = rd_word;
      // `dev_cr0`/`dev_cr1` are what the DEVICE holds, read out of the model
      // rather than inferred from what was written. That is the reference the
      // reported word is judged against -- the part is allowed to discard a
      // write it does not support (#334), so "what we asked for" is not one.
      $display("[cfg] txn=%0s space=%0d dev_register=%0d dev_read=%0d served=%0h got=%h csh_ns=%0.1f hung=%0d dv=%0d frozen=%0d dev_cr0=%h dev_cr1=%h elected=%0d dev_long=%0d lat_ck=%0d read_edge=%0d data_edge=%0d",
               tag, 1, served_register, served_read, served,
               got_valid ? got_first : 32'hxxxx_xxxx, cs_high_ns, hung,
               dv_from_read, freeze, u_ram.cr0, u_ram.cr1, elected_long,
               u_ram.take_long, dev_latency, held_read_edge, held_data_edge);
    end
  endtask

  // One data burst. `leak_control` forces the fault mechanism A is accused of,
  // so the checks below can be shown to detect it.
  task data_txn(input is_write, input [63:0] tag, input freeze);
    begin
      wait_idle;
      got_valid = 1'b0; got_first = 32'hxxxx_xxxx; hung = 1'b1; words = 0;
      elected_long = 1'b0;
      held_read_edge = -1; held_data_edge = -1;
      capture_frozen = freeze;
      @(posedge sync_clk); #(TSET/2.0);
      address = DATA_ADDR;
      register_space = leak_control ? 1'b1 : 1'b0;
      perform_write = is_write;
      write_data = 32'h1000_1001;
      final_word = (BURST_WORDS == 1);
      start_transfer = 1'b1;
      @(posedge sync_clk); #(TSET/2.0);
      start_transfer = 1'b0;
      for (cycles = 0; cycles < WAIT_CYCLES; cycles = cycles + 1) begin
        @(posedge sync_clk); #(TSET/2.0);
        if (extra_latency) elected_long = 1'b1;
        if ((read_ready || write_ready)) begin
          if (read_ready && !got_valid) begin
            got_first = read_data; got_valid = 1'b1;
          end
          words = words + 1;
          if (words >= BURST_WORDS - 1) final_word = 1'b1;
        end
        if (idle && cycles > 2) begin hung = 1'b0; cycles = WAIT_CYCLES; end
      end
      final_word = 1'b0; register_space = 1'b0; capture_frozen = 1'b0;
      if (!is_write) rd_word_lagged = rd_word;
      $display("[cfg] txn=%0s space=%0d dev_register=%0d dev_read=%0d served=%0h got=%h csh_ns=%0.1f hung=%0d dv=%0d frozen=%0d dev_cr0=%h dev_cr1=%h elected=%0d dev_long=%0d lat_ck=%0d read_edge=%0d data_edge=%0d",
               tag, leak_control, served_register, served_read, served,
               got_valid ? got_first : 32'hxxxx_xxxx, cs_high_ns, hung,
               dv_from_read, freeze, u_ram.cr0, u_ram.cr1, elected_long,
               u_ram.take_long, dev_latency, held_read_edge, held_data_edge);
    end
  endtask

  // The whole engine path: configure, verify both registers, write, read.
  //
  // `n` is `latency_clocks`, and `low` is `low_latency_clocks` -- the count for
  // a part that DECLINES the extra latency, which is reachable only with
  // CR0[3] = 0. `+refresh_every=1` makes the model ask on every transaction, so
  // both answers can be driven rather than waited for. (#380, #381)
  task run_sequence_low(input [15:0] cr0v, input [15:0] cr1v, input integer n,
                        input integer low);
    begin
      latency_clocks = n[3:0];
      low_latency_clocks = (low_const >= 0) ? low_const[3:0] : low[3:0];
      ctl_latency = n;
      base_latency = (cr0v[7:4] >= 14) ? (5 + cr0v[7:4] - 16) : (5 + cr0v[7:4]);
      // What the DEVICE will wait: 2L under fixed latency, and under variable
      // latency 2L only when it asks -- which `+refresh_every=1` makes it do.
      dev_latency = (cr0v[3] || refresh_every == 1) ? 2 * base_latency
                                                    : base_latency;
      fixed_latency = cr0v[3];
      $display("[cfg] --- CR0=%h CR1=%h device %0d CK, controller %0d x 2 CK, low %0d x 2 CK, leak_control=%0d cap_miss=%0d cap_lag=%0d reread=%0d",
               cr0v, cr1v, dev_latency, n, low, leak_control, cap_miss, cap_lag,
               verify_reread);
      reg_txn(CR0_ADDR, 1'b1, cr0v, "wr_cr0", 1'b0);
      reg_txn(CR1_ADDR, 1'b1, cr1v, "wr_cr1", 1'b0);
      reg_txn(CR0_ADDR, 1'b0, 16'h0, "rd_cr0", (cap_miss & 1) != 0);
      // CR0 AGAIN, back to back. A read path lagging by one transaction hands
      // this one CR0's own word and the FIRST one whatever preceded it, so the
      // two disagree. Straddling CR1 would not see it: the lag returns the same
      // word to both reads of a CR0/CR1/CR0 sequence. (#366)
      if (verify_reread)
        reg_txn(CR0_ADDR, 1'b0, 16'h0, "rd_cr0b", (cap_miss & 4) != 0);
      reg_txn(CR1_ADDR, 1'b0, 16'h0, "rd_cr1", (cap_miss & 2) != 0);
      data_txn(1'b1, "mem_wr", 1'b0);
      data_txn(1'b0, "mem_rd", (cap_miss & 8) != 0);
    end
  endtask

  // The old signature: the short count left at its reset, which is what every
  // caller had before #380 gave the DQS controller an input for it.
  task run_sequence(input [15:0] cr0v, input [15:0] cr1v, input integer n);
    begin
      run_sequence_low(cr0v, cr1v, n, 3);
    end
  endtask

  initial begin
    if (!$value$plusargs("dq_pipe=%d", dq_pipe)) dq_pipe = 0;
    if (!$value$plusargs("ck_pipe=%d", ck_pipe)) ck_pipe = 1;
    if (!$value$plusargs("dq_ph=%d", dq_ph)) dq_ph = 1;
    if (!$value$plusargs("rd_slip=%d", rd_slip)) rd_slip = 0;
    if (!$value$plusargs("verbose=%d", verbose)) verbose = 0;
    if (!$value$plusargs("dv_from_read=%d", dv_from_read)) dv_from_read = 0;
    if (!$value$plusargs("cap_miss=%d", cap_miss)) cap_miss = 0;
    if (!$value$plusargs("cap_lag=%d", cap_lag)) cap_lag = 0;
    if (!$value$plusargs("verify_reread=%d", verify_reread)) verify_reread = 1;
    if (!$value$plusargs("refresh_every=%d", refresh_every)) refresh_every = 100;
    // Which sequence set runs. `0` is the four fixed-latency sequences #349 and
    // #366 are judged on; `1` is the variable-latency set, the only one in which
    // the election is live at all. (#380, #381)
    if (!$value$plusargs("latency_mode=%d", latency_mode)) latency_mode = 0;
    // Force the SHORT count to one value at every code, which is what
    // `LOW_LATENCY_CLOCKS = 3` did. -1 uses the per-code count. (#380)
    if (!$value$plusargs("low_const=%d", low_const)) low_const = -1;

    VCC = 1'b1; VSS = 1'b0; resetb = 1'b1;
    #200_000;
    for (i = 0; i < 64; i = i + 1)
      u_ram.memory[32'h100 + i] = 16'h1000 + i[15:0];
    rst = 1'b0;
    @(posedge sync_clk);

    $display("[cfg] shim: dq_pipe=%0d ck_pipe=%0d dq_ph=%0d rd_slip=%0d",
             dq_pipe, ck_pipe, dq_ph, rd_slip);
    $display("[cfg] tcshi_ns=%0.1f burst_words=%0d", T_CSHI_NS, BURST_WORDS);

    leak_control = 0;
    if (latency_mode == 0) begin
    // 1. MATCHED. CR0 latency code 2 fixed (14 CK) against the controller's 6
    //    (7 cycles x 2 CK). This is the only combination the DQS ceiling build
    //    can be right in, because it never drives `latency_clocks`.
    run_sequence(16'h8f2f, 16'hff81, 6);

    // 2. MISMATCHED, and this is NOT hypothetical -- it is every `bist latency`
    //    cell and every `var` cell on the DQS build. The part answers at 10 CK,
    //    the controller still waits 14.
    run_sequence(16'h8f0f, 16'hff81, 6);

    // 3. The single-ended CR1, which is the value the board reports leaking.
    run_sequence(16'h8f0f, 16'hffc1, 6);

    // 4. THE CONTROL for mechanism A: register space forced on the data burst.
    //    If the checks cannot see this, they cannot exonerate the CA either.
    leak_control = 1;
    run_sequence(16'h8f2f, 16'hffc1, 6);
    end else begin
    // VARIABLE LATENCY, CR0[3] = 0, one sequence per legal code. The device
    // serves after L CK when it declines and 2L when it asks; the controller has
    // to reach the same number from an RWDS that floats until tDSV. Both defects
    // land here and nowhere else in this file -- every sequence above is fixed
    // latency, where the election is inert. (#380, #381)
    run_sequence_low(16'h8f27, 16'hff81, 6, 2);   // code 2,  L = 7
    run_sequence_low(16'h8f07, 16'hff81, 4, 1);   // code 0,  L = 5
    run_sequence_low(16'h8fe7, 16'hff81, 2, 0);   // code 14, L = 3
    run_sequence_low(16'h8ff7, 16'hff81, 3, 1);   // code 15, L = 4
    end

    $display("[cfg] === done ===");
    $finish;
  end

endmodule
