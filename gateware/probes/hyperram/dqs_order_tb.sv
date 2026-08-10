// #206: what byte and word order the 32-bit DQS path actually uses.
//
// Three measurements, none of which compares a path against itself:
//
//   * **ca**    -- the CA leaves through the same `dq.o` and the same ODDRX2DQA
//     mapping the DATA does, and the device decodes an address from it. So the
//     fabric-bit -> wire-byte orientation is not free: get it wrong and the part
//     is handed a different address. `+dq_perm` permutes the four bytes at the
//     fabric boundary and the run reports what the device then resolved -- the
//     negative control that says the measurement can tell the orders apart.
//   * **write** -- the controller writes one 32-bit word; the check reads
//     `u_ram.memory` HIERARCHICALLY. Nothing on the read path takes part, so a
//     write-side permutation has nothing to cancel against.
//   * **read**  -- the memory is PRELOADED (never written through the
//     controller) with a byte ramp where every byte in the window names its own
//     device byte address, and the check compares `read_data` against it.
//
// Both data tests also log the raw byte sequence on `adq`, in arrival order, so
// the observation is separable into "what went on the wire" and "how the fabric
// grouped it". A slip in the grouping is a different fault from a permutation
// and this file does not merge them.
//
// ## Pattern choice, and why these patterns discriminate
//
//   * read: `memory[BASE + k] = {8'(2k), 8'(2k+1)}` -- device byte address j
//     holds the value j, over 128 words. Every byte in the window is distinct,
//     so all 24 permutations of the four fabric byte positions produce 24
//     different `read_data` values and the observed one names exactly one.
//     A ramp in WORDS (`0x1000 + k`) does NOT do this: its high byte is `0x10`
//     for every word, so a byte swap inside a half is invisible in it.
//   * write: `0x12345678` and `0xa5c31234`. Every byte distinct, both 16-bit
//     halves distinct, and no symmetry -- half-swap, byte-swap-inside-half and
//     full reverse each give a value nothing else gives. Two patterns rather
//     than one so a coincidence has to happen twice; `a5c31234` is the value the
//     board experiment in #206 used, so the sim and the board name the same thing.
//
// ## What this CANNOT establish
//
//   * ODDRX2DQA's own D0..D3 -> wire order. The shim IS
//     `hyperram_dqs_phy.py`'s mapping restated; no open model of the primitive
//     exists. The **ca** test is what constrains it: whatever the primitive
//     does, the CA and the data go through it identically, and only one
//     orientation decodes an address the part agrees with.
//   * DQSBUFM, and therefore where the read grouping is anchored. `+rd_slip`
//     moves it; the reported wire sequence is what the slip is measured against.
//   * anything analogue.
//
// Driven by scripts/hyperram_dqs_model_sim.py --stage order. See #206, #186.
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
  // Waits for: `idle` after `start_transfer`.
  // Expected worst case: 14 CK fixed latency is 7 cycles, plus command, data and
  //   recovery -- under 32 cycles.
  // Multiplier: 4x, not 1.25x. A controller that never leaves READ_DATA is one of
  //   the outcomes being measured and the run must still reach its report line.
  // On expiry: the row reports `hung=1` and whatever was last presented.
  localparam integer WAIT_CYCLES = 128;

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
  // The behavioural 4:1 PHY. Same shim as dqs_model_tb.sv; the defaults are the
  // ONE offset combination in which the device decodes the address it was asked
  // for, measured by that file's sweep.
  //
  integer dq_pipe = 0;
  integer ck_pipe = 1;
  integer dq_ph   = 1;
  integer rd_slip = 0;
  integer verbose = 0;
  // Which fabric byte reaches which wire position. 0 is the PHY as wired
  // (`i_D0 = dq.o[31:24]` first out); 1..3 are the three orders #206 lists as
  // candidates, present so the CA test's discrimination is shown and not claimed.
  integer dq_perm = 0;
  // The same four orders applied to the DATA beats only, in both directions --
  // the CA still decodes, so a run under this is a device that answered the
  // right address and a data path wired differently. It is the negative control
  // for tests 2 and 3: under any non-zero value the recorded convention must
  // FAIL, or those tests were never able to see it.
  integer data_perm = 0;
  // Fabric words the controller has driven since CS# fell. The first two are the
  // CA; everything after is data. Counted rather than decoded from `state` so the
  // gate does not depend on the FSM's encoding.
  integer oe_words = 0;

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
  integer data_first_edge = -1;

  // Read capture: four device edges make one fabric word.
  reg [7:0] rd_bytes [0:3];
  reg       rd_valid [0:3];
  reg [31:0] rd_word = 32'h0;
  reg        rd_word_valid = 1'b0;
  reg        ck_was = 1'b0;
  reg [21:0] served = 22'h3f_ffff;

  // The wire, in arrival order, from the first data edge on. `host_*` is what the
  // controller drove after the CA; `dev_*` is what the device drove. Eight of each
  // is two 32-bit fabric words, enough to see a grouping slip of either sign.
  reg [7:0] host_b [0:7];
  reg [7:0] dev_b  [0:7];
  reg [7:0] ca_b   [0:5];
  integer   host_n = 0, dev_n = 0, ca_n = 0;
  integer   host_first = -1, dev_first = -1;

  integer p, k, slot;
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

  // The negative control. 0 leaves the PHY's own wiring alone.
  function [31:0] permute(input [31:0] w, input integer pm);
    begin
      case (pm)
        1: permute = {w[23:16], w[31:24], w[7:0],   w[15:8]};   // bytes swapped in each half
        2: permute = {w[15:8],  w[7:0],   w[31:24], w[23:16]};  // halves swapped
        3: permute = {w[7:0],   w[15:8],  w[23:16], w[31:24]};  // full reverse
        default: permute = w;
      endcase
    end
  endfunction

  task clear_wire;
    begin
      for (k = 0; k < 8; k = k + 1) begin host_b[k] = 8'hxx; dev_b[k] = 8'hxx; end
      for (k = 0; k < 6; k = k + 1) ca_b[k] = 8'hxx;
      host_n = 0; dev_n = 0; ca_n = 0;
      host_first = -1; dev_first = -1;
      data_first_edge = -1;
      served = 22'h3f_ffff;
    end
  endtask

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

    dq_e_e   = dq_e_hist[dq_pipe];
    dq_o_e   = permute(dq_o_hist[dq_pipe], (oe_words >= 2) ? data_perm : dq_perm);
    if (dq_e_e) oe_words = oe_words + 1;
    rwds_o_e = rwds_o_hist[dq_pipe];
    rwds_e_e = rwds_e_hist[dq_pipe];
    clk_en_e = clk_en_hist[ck_pipe];
    cs_e     = cs_hist[ck_pipe];

    if (csb && !cs_e) begin               // a new transaction begins
      edge_idx = -1;
      oe_words = 0;
    end
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
      #0.5;                         // past the device's own delta
      if (ck_was !== clk && !csb) begin
        edge_idx = edge_idx + 1;
        // The CA is decoded on edge 5, so edge 6 is the first moment the address
        // the DEVICE resolved can be read out.
        if (edge_idx < 6) begin
          if (ca_n < 6) begin ca_b[ca_n] = adq; ca_n = ca_n + 1; end
        end else begin
          if (edge_idx == 6) served = u_ram.word_addr;
          if (adq_oe) begin
            if (host_first < 0) host_first = edge_idx;
            if (host_n < 8) begin host_b[host_n] = adq; host_n = host_n + 1; end
          end else if (adq !== 8'hzz && adq !== 8'hxx) begin
            if (dev_first < 0) dev_first = edge_idx;
            if (data_first_edge < 0) data_first_edge = edge_idx;
            if (dev_n < 8) begin dev_b[dev_n] = adq; dev_n = dev_n + 1; end
          end
        end
        slot = (p + 8 - ((dq_ph + rd_slip) % 4)) % 4;
        rd_bytes[slot] = adq;
        rd_valid[slot] = (adq !== 8'hzz) && (adq !== 8'hxx) && !adq_oe;
        if (slot == 3) begin
          rd_word = permute({rd_bytes[0], rd_bytes[1], rd_bytes[2], rd_bytes[3]},
                            data_perm);
          rd_word_valid = rd_valid[0] & rd_valid[1] & rd_valid[2] & rd_valid[3];
        end
        if (verbose)
          $display("[edge] e=%0d ph=%0d adq=%h oe=%0d rwds=%b st=%0d",
                   edge_idx, p, adq, adq_oe, rwds, state);
      end
      if (p < 3) #(TPH/2.0 - 0.5);
    end
  end

  always @(posedge sync_clk) begin
    phy_dq_i      <= rd_word;
    phy_datavalid <= rd_word_valid;
    phy_burstdet  <= rd_word_valid;
    phy_rwds_i    <= {4{rwds === 1'b1}};
  end

  //
  // The experiment.
  //
  integer   cycles;
  reg       hung;
  reg [31:0] got;
  reg       got_valid;
  integer   code, n, i, base, dev_ck, pm, pat_i;
  reg [15:0] cr0_v;
  reg [31:0] pattern;

  // Separate windows, so a write test can never be read back by the read test
  // and a leftover from one cannot be mistaken for the other's result.
  localparam [31:0] READ_BASE  = 32'h0000_0200;   // preloaded byte ramp
  localparam [31:0] WRITE_ADDR = 32'h0000_0300;   // filled with 0xEEEE first
  // Chosen so the four CA bytes of the FIRST 32-bit beat are all distinct:
  // {a0, 05, 9c, 3a}. a0 = read, memory space, linear burst, address[31:27] = 0;
  // then address[26:19], [18:11], [10:3]. Every permutation of four distinct
  // bytes names a different transaction, so only the identity can resolve back
  // to this address.
  localparam [31:0] CA_ADDR    = 32'h002C_E1D6;

  task settle;
    begin
      for (cycles = 0; cycles < 4 * WAIT_CYCLES; cycles = cycles + 1) begin
        @(posedge sync_clk); #(TSET/2.0);
        if (idle) cycles = 4 * WAIT_CYCLES;
      end
    end
  endtask

  task do_read(input [31:0] a);
    begin
      settle;
      clear_wire;
      got = 32'hxxxx_xxxx; got_valid = 1'b0; hung = 1'b1;
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

  task do_write(input [31:0] a, input [31:0] d);
    begin
      settle;
      clear_wire;
      hung = 1'b1;
      @(posedge sync_clk); #(TSET/2.0);
      address = a; write_data = d; perform_write = 1'b1; register_space = 1'b0;
      final_word = 1'b1; start_transfer = 1'b1;
      @(posedge sync_clk); #(TSET/2.0);
      start_transfer = 1'b0;
      for (cycles = 0; cycles < WAIT_CYCLES; cycles = cycles + 1) begin
        @(posedge sync_clk); #(TSET/2.0);
        if (idle && cycles > 2) begin hung = 1'b0; cycles = WAIT_CYCLES; end
      end
      final_word = 1'b0; perform_write = 1'b0;
    end
  endtask

  function integer base_ck(input integer c);
    begin base_ck = (c >= 14) ? (5 + c - 16) : (5 + c); end
  endfunction

  task set_code(input integer c);
    begin
      cr0_v = 16'h8f2f;
      cr0_v[7:4] = c[3:0];
      cr0_v[3]   = 1'b1;                // fixed latency; the part's power-on state
      u_ram.cr0 = cr0_v;                // twin only; the controller has no part in it
    end
  endtask

  reg [7:0] ramp_hi, ramp_lo;
  task preload_ramp;
    begin
      // Device byte address j holds j, over 128 words. Nothing here goes through
      // the controller, so the read test has no write-side error to cancel.
      for (i = 0; i < 128; i = i + 1) begin
        ramp_hi = 2*i;
        ramp_lo = 2*i + 1;
        u_ram.memory[READ_BASE + i] = {ramp_hi, ramp_lo};
      end
    end
  endtask

  initial begin
    if (!$value$plusargs("dq_pipe=%d", dq_pipe)) dq_pipe = 0;
    if (!$value$plusargs("ck_pipe=%d", ck_pipe)) ck_pipe = 1;
    if (!$value$plusargs("dq_ph=%d", dq_ph)) dq_ph = 1;
    if (!$value$plusargs("rd_slip=%d", rd_slip)) rd_slip = 0;
    if (!$value$plusargs("verbose=%d", verbose)) verbose = 0;
    if (!$value$plusargs("data_perm=%d", data_perm)) data_perm = 0;

    VCC = 1'b1; VSS = 1'b0; resetb = 1'b1;
    #200_000;
    preload_ramp;
    rst = 1'b0;
    @(posedge sync_clk);

    $display("[order] shim dq_pipe=%0d ck_pipe=%0d dq_ph=%0d rd_slip=%0d data_perm=%0d",
             dq_pipe, ck_pipe, dq_ph, rd_slip, data_perm);

    //
    // 1. The CA, through the same mapping the data uses.
    //
    set_code(2);
    latency_clocks = 4'd6;
    for (pm = 0; pm <= 3; pm = pm + 1) begin
      dq_perm = pm;
      do_read(CA_ADDR);
      $display("[order] test=ca perm=%0d addr=%0h served=%0h ca=%h%h%h%h%h%h",
               pm, CA_ADDR[21:0], served,
               ca_b[0], ca_b[1], ca_b[2], ca_b[3], ca_b[4], ca_b[5]);
    end
    dq_perm = 0;

    //
    // 2. Writes, checked against the model's own array.
    //
    for (pat_i = 0; pat_i <= 1; pat_i = pat_i + 1) begin
      pattern = (pat_i == 0) ? 32'h1234_5678 : 32'ha5c3_1234;
      for (code = 0; code <= 2; code = code + 2) begin
        set_code(code);
        base = base_ck(code);
        dev_ck = 2 * base;
        for (n = 0; n <= 8; n = n + 1) begin
          for (i = 0; i < 8; i = i + 1) u_ram.memory[WRITE_ADDR - 2 + i] = 16'heeee;
          latency_clocks = n[3:0];
          do_write(WRITE_ADDR, pattern);
          $display("[order] test=write pat=%h code=%0d n=%0d dev_ck=%0d dev_beat=%0d served=%0h host_first=%0d host_n=%0d wire=%h%h%h%h%h%h%h%h mm2=%h mm1=%h m0=%h m1=%h m2=%h m3=%h hung=%0d",
                   pattern, code, n, dev_ck, 4 + 2*dev_ck, served,
                   host_first, host_n,
                   host_b[0], host_b[1], host_b[2], host_b[3],
                   host_b[4], host_b[5], host_b[6], host_b[7],
                   u_ram.memory[WRITE_ADDR-2], u_ram.memory[WRITE_ADDR-1],
                   u_ram.memory[WRITE_ADDR],   u_ram.memory[WRITE_ADDR+1],
                   u_ram.memory[WRITE_ADDR+2], u_ram.memory[WRITE_ADDR+3],
                   hung);
        end
      end
    end

    //
    // 3. Reads, from the preloaded ramp.
    //
    preload_ramp;
    for (code = 0; code <= 2; code = code + 2) begin
      set_code(code);
      base = base_ck(code);
      dev_ck = 2 * base;
      for (n = 0; n <= 8; n = n + 1) begin
        latency_clocks = n[3:0];
        do_read(READ_BASE);
        $display("[order] test=read base=%0h code=%0d n=%0d dev_ck=%0d dev_beat=%0d served=%0h dev_first=%0d dev_n=%0d wire=%h%h%h%h%h%h%h%h got=%h gv=%0d hung=%0d",
                 READ_BASE[21:0], code, n, dev_ck, 4 + 2*dev_ck, served,
                 dev_first, dev_n,
                 dev_b[0], dev_b[1], dev_b[2], dev_b[3],
                 dev_b[4], dev_b[5], dev_b[6], dev_b[7],
                 got, got_valid, hung);
      end
    end

    $display("[order] === done ===");
    $finish;
  end

endmodule
