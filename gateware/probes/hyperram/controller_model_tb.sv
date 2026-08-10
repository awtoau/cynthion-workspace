// OUR controller against the open HyperRAM model -- CA, byte order, latency count.
//
// `soc_hyperram_sim.py` drives `HyperRAMController` against `ModelHyperRAM16`, a
// Python model written from the same datasheet reading as the controller, so the
// two can be wrong together. `hyperram_model.v` is the independent one: it is held
// equal to Winbond's encrypted model over `vendor_model_tb.sv`. This testbench puts
// the controller in front of THAT, so a CA-encoding, byte-order or latency-count
// error fails here instead of on the board.
//
// Scope. This is the controller's arithmetic, not the board's electricals:
//
//   * `HyperRAMPHY` is NOT instantiated. The shim below is an IDEAL 2:1 gearbox --
//     same byte order and same CK relationship as the ECP5 `ODDRX1F`/`IDDRX1F` it
//     replaces, but with zero pipeline latency and zero skew between CS#, OE, CK and
//     DQ. Upstream's PHY delays `cs`, `dq.e` and `rwds.e` through a 3-stage
//     `FFSynchronizer` while `clk_en` and `dq.o` go straight into `ODDRX1F`; whether
//     those line up depends on `ODDRX1F`'s output latency, which no open sim model
//     here states. Out of scope, and flagged rather than guessed.
//   * No setup/hold, no tRWR, no power-state machine -- `hyperram_model.v` does not
//     model them either.
//
// Byte order, taken from the primitives upstream's PHY instantiates:
//   ODDRX1F(D0=dq.o[i+8], D1=dq.o[i]) -> high byte on the CK-High half, low byte on
//   the CK-Low half, i.e. high byte captured by the CK rising edge.
//   IDDRX1F(Q0->dq.i[i+8], Q1->dq.i[i]) -> the same mapping coming back.
//
// Five cases: register space, memory, then CR0[7:4] swept in fixed latency, in
// variable latency, and in variable latency with the extra-latency request masked
// away (case 5 -- the short branch, otherwise unreachable against this model).
//
// `+trace` prints one line per CS#-Low cycle, `+dump` writes a VCD, and `+skew=N`
// is the negative control described at `skew` below.
//
// Driven by scripts/hyperram_model_sim.py, which passes LAT_W, TCK_NS and
// RECOVER_LIMIT to match the controller it elaborated.
//
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

// Width of `latency_clocks`/`low_latency_clocks`, which follows the controller's
// `max_latency_clocks`. The driver script passes the value it elaborated with.
`ifndef LAT_W
  `define LAT_W 6
`endif

// The sync period in ns, and the controller's own tCSM watchdog in cycles. Both
// follow `sync_mhz`, so the driver computes them rather than this file assuming a
// clock. Defaults are the 100 MHz case.
`ifndef TCK_NS
  `define TCK_NS 10.0
`endif
`ifndef RECOVER_LIMIT
  `define RECOVER_LIMIT 512
`endif

// Transactions between the model's refresh-forced 2x elections. The driver sweeps
// this rather than leaving it at the shipped 100, because at 100 whether a sweep
// cell collides with a refresh is an accident of how many transactions ran before
// it -- which is the nondeterminism #338 is trying to pin down, not a way to test
// it. 0 never elects (the variable SHORT branch), 1 always does (the LONG one).
`ifndef REFRESH_EVERY
  `define REFRESH_EVERY 100
`endif

// Edges between a write's first data beat and a read's at the same latency. The
// device has to turn the bus around, so it cannot drive DQ on the edge it leaves
// Hi-Z. `hyperram_model.v` records this measured against the vendor model: 28
// edges from the last CA edge to the strobe at 14 CK fixed, 14 at 7 CK variable,
// both of which are 2L + 1 beats from CS#. Stated here so that if the model moves
// it again, this fails rather than following.
`define READ_TURNAROUND_EDGES 1

module tb;

  // `HyperRAMPHY` emits one CK per sync cycle, so the sync period is also the CK
  // period. 100 MHz by default, the same clock `vendor_model_tb.sv` uses.
  localparam real TCK = `TCK_NS;

  // The DELAYF upstream's PHY puts on CK alone: 80 taps x 25 ps. It is what centres
  // the CK edge in the DQ eye, and without it every model sample is a delta race
  // against the driver that changes on the same edge.
  localparam real CK_DELAY = 2.0;

  // Sample the device's DQ/RWDS this far past the CK edge that drives them. The
  // model changes its outputs ON the edge with blocking assignments, so sampling on
  // the edge is a race whose loser reads a beat late -- which surfaces as a
  // byte-swapped word, not as a timing bug. Same reason vendor_model_tb.sv uses #0.5.
  localparam real SAMPLE_DELAY = 1.0;

  // Sync cycles one transaction may take before it is called hung. Longest here:
  // CS# setup and CA (5) + 2x11 CK of latency at code 6 + an 8-word burst +
  // recovery ~= 40. 96 is ~2.4x that, and on expiry the task logs the FSM state and
  // `timed_out` rather than stalling the run.
  localparam integer XACT_LIMIT = 96;

  // How long to wait for `idle` after the data phase gives up. The controller's own
  // tCSM watchdog is what ends a transaction a silent device never finishes (#316,
  // #317); the driver passes 1.4x that count for this clock. A watchdog that stopped
  // working is then reported rather than waited out.
  localparam integer RECOVER_LIMIT = `RECOVER_LIMIT;

  localparam [31:0] ADDR_ID0 = 32'h0000_0000;
  localparam [31:0] ADDR_ID1 = 32'h0000_0001;
  localparam [31:0] ADDR_CR0 = 32'h0000_0800;
  localparam [31:0] ADDR_CR1 = 32'h0000_0801;

  integer errors = 0;
  integer checks = 0;

  // Which of `READ_DATA`'s two branches took each burst. The controller's own
  // docstring says nothing has measured this; with the device's read word starting
  // one edge after a write's, it straddles two sync cycles and only the
  // clock-inversion branch can assemble it. Counted rather than asserted, because
  // the answer is a property of the CK-to-sync phase and this gearbox has one phase.
  integer aligned_bursts  = 0;
  integer inverted_bursts = 0;

  //
  // Clock and reset.
  //
  reg clk = 1'b0;
  reg rst = 1'b1;
  always #(TCK/2.0) clk = ~clk;

  //
  // Controller control/status.
  //
  reg               start_transfer     = 1'b0;
  reg               register_space     = 1'b0;
  reg               perform_write      = 1'b0;
  reg               single_page        = 1'b0;
  reg  [31:0]       address            = 32'h0;
  reg  [`LAT_W-1:0] latency_clocks     = 14;
  reg  [`LAT_W-1:0] low_latency_clocks = 7;
  reg               fixed_latency      = 1'b1;

  // Driven combinationally by the burst engine below, because that is how a real
  // caller drives them: `final_word` has to be right DURING the beat it ends, and
  // the beat only becomes visible inside that same cycle.
  reg               final_word;
  reg  [15:0]       write_data;

  wire         idle, read_ready, write_ready, timed_out;
  wire [15:0]  read_data;
  wire [3:0]   state;

  //
  // Device-facing side of the controller's `HyperBusPHY` record.
  //
  wire         cs, clk_en, dq_e, rwds_e;
  wire [15:0]  dq_o;
  wire [1:0]   rwds_o;
  reg  [15:0]  dq_i   = 16'h0;
  reg  [1:0]   rwds_i = 2'b00;

  hyperram_ctrl_top dut (
    .clk(clk), .rst(rst),
    .start_transfer(start_transfer), .register_space(register_space),
    .perform_write(perform_write), .single_page(single_page),
    .final_word(final_word), .address(address), .write_data(write_data),
    .latency_clocks(latency_clocks), .low_latency_clocks(low_latency_clocks),
    .fixed_latency(fixed_latency),
    .idle(idle), .read_ready(read_ready), .write_ready(write_ready),
    .timed_out(timed_out), .read_data(read_data), .state(state),
    .cs(cs), .clk_en(clk_en), .dq_o(dq_o), .dq_e(dq_e), .dq_i(dq_i),
    .rwds_o(rwds_o), .rwds_e(rwds_e), .rwds_i(rwds_i)
  );

  //
  // The ideal 2:1 gearbox. See the header for what it does and does not model.
  //
  // Held High while the controller is in reset. Without it the model sees CS# fall
  // at time 0 from the DUT's uninitialised output and reports a 200 us tCSM
  // violation before any transaction has happened.
  wire csb = rst ? 1'b1 : ~cs;
  reg  resetb = 1'b1;
  reg  VCC = 1'b0;
  wire VSS = 1'b0;

  // ODDRX1F(D0=clk_en, D1=0): CK is High for the first half of every cycle whose
  // `clk_en` is set, and the clock simply stops otherwise -- Active Clock Stop.
  wire ck_raw = clk & clk_en;
  wire ck;
  assign #(CK_DELAY) ck = ck_raw;

  // ODDRX1F(D0=dq.o[15:8], D1=dq.o[7:0]).
  wire [7:0] adq_drv  = clk ? dq_o[15:8] : dq_o[7:0];
  wire       rwds_drv = clk ? rwds_o[1]  : rwds_o[0];

  wire [7:0] adq  = dq_e   ? adq_drv  : 8'hzz;
  wire       rwds = rwds_e ? rwds_drv : 1'bz;

  // IDDRX1F. Sampling is done off a CK delayed by SAMPLE_DELAY rather than on the
  // device's own CK edge; the pair is then handed to the controller on the next sync
  // edge, which is the one cycle of input latency IDDRX1F has.
  wire ck_s;
  assign #(SAMPLE_DELAY) ck_s = ck;

  reg [7:0] s_pos_dq = 8'h0, s_neg_dq = 8'h0;
  reg       s_pos_rwds = 1'b0, s_neg_rwds = 1'b0;

  always @(posedge ck_s) begin s_pos_dq <= adq; s_pos_rwds <= rwds; end
  always @(negedge ck_s) begin s_neg_dq <= adq; s_neg_rwds <= rwds; end
  always @(posedge clk) begin
    dq_i   <= {s_pos_dq, s_neg_dq};
    rwds_i <= {s_pos_rwds, s_neg_rwds};
  end

  hyperram_model #(.REFRESH_EVERY(`REFRESH_EVERY)) model (
    .adq(adq), .clk(ck), .clk_n(~ck), .csb(csb),
    .rwds(rwds), .VCC(VCC), .VSS(VSS), .resetb(resetb)
  );

  //
  // Device beats since CS# fell, so the controller's own latency can be stated in
  // the model's units without asking the model what it expected.
  //
  integer ck_edges = 0;
  always @(negedge csb) ck_edges = 0;
  always @(posedge ck or negedge ck) if (!csb) ck_edges = ck_edges + 1;

  // The DEVICE's first read data beat, taken off the bus rather than inferred from
  // when the controller strobed. The controller's strobe is one or two sync cycles
  // behind depending on which half of the cycle the word landed in, so deriving the
  // device's beat from it bakes an alignment assumption into the measurement -- and
  // the alignment is exactly what the turnaround edge changes.
  //
  // The first RWDS High after the CA is the read strobe: during the CA it is the
  // latency request, and through the latency period the model holds it Low.
  integer dev_first_beat = -1;
  always @(negedge csb) dev_first_beat = -1;
  always @(posedge ck_s or negedge ck_s)
    if (!csb && dev_first_beat < 0 && ck_edges > 6 && rwds === 1'b1)
      dev_first_beat = ck_edges - 1;   // ck_edges already counted the current beat

  //
  // Burst engine. One transaction at a time; `final_word` and `write_data` come out
  // of it combinationally, and everything it observes it registers on the sync edge,
  // which is where `read_ready`/`read_data` are stable for the cycle just ended.
  //
  reg [31:0] burst_base   = 32'h0;
  integer    burst_target = 0;
  reg        burst_active = 1'b0;
  reg        burst_reg    = 1'b0;
  reg [15:0] burst_regval = 16'h0;
  integer    burst_count  = 0;
  reg        saw_beat     = 1'b0;
  integer    first_beat   = -1;
  reg [1:0]  strobe_rwds  = 2'b00;
  reg [15:0] read_words [0:31];

  // Address-derived, and the varying half is the HIGH byte: consecutive words share
  // a[15:8], so a byte-order slip shows as a constant where the pattern should move,
  // and a controller that stops advancing repeats one value.
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
        // Raw, not converted back to a device beat: how far the strobe trails the
        // data depends on which half of the sync cycle the word landed in, and that
        // is the thing being measured. `dev_first_beat` is the device's own answer.
        // `strobe_rwds` says which of READ_DATA's two branches took the word:
        // 0b10 is the aligned one, anything else is the clock-inversion one.
        if (!saw_beat) begin
          first_beat  <= ck_edges;
          strobe_rwds <= rwds_i;
          saw_beat    <= 1'b1;
        end
      end else if (write_ready) begin
        burst_count <= burst_count + 1;
        // `write_ready` is asserted the cycle BEFORE the registered `dq.o` reaches
        // the pins, so ck_edges is already the beat that will carry it.
        if (!saw_beat) begin first_beat <= ck_edges; saw_beat <= 1'b1; end
      end
    end
  end

  // RWDS as the CONTROLLER saw it over the CA -- the device's extra-latency request.
  // Latched in SHIFT_COMMAND2 (state 4), which is where the controller itself decides
  // which count to take. Reported per case, so "the device asked for the long
  // latency" is an observation rather than an assumption.
  reg [1:0] ca_rwds = 2'b00;
  always @(posedge clk) if (state == 4) ca_rwds <= rwds_i;

  //
  // Per-cycle trace, for when a beat count has to be argued rather than asserted.
  //
  integer cyc = 0;
  always @(posedge clk) begin
    cyc <= cyc + 1;
    if ($test$plusargs("trace") && cs)
      $display("[trc] %0d st=%0d cke=%0d dq_e=%0d dq_o=%h rwds_e=%0d rwds_o=%b | beat=%0d wa=%h | dq_i=%h rwds_i=%b rr=%0d wr=%0d fw=%0d",
               cyc, state, clk_en, dq_e, dq_o, rwds_e, rwds_o,
               model.beat, model.word_addr, dq_i, rwds_i, read_ready, write_ready,
               final_word);
  end

  //
  // Checks.
  //
  reg [15:0] got;
  integer    i;
  integer    ci;
  integer    code;
  integer    base_ck;
  integer    lat_ck;
  reg [15:0] cr0_value;
  reg [31:0] a;
  integer    latency_codes [0:5];

  // Which sweep cell a stalled burst belongs to, so a failure caused by a device
  // that never answers can be attributed to the cell that asked for it rather than
  // landing as an unlabelled FAIL. "-" outside the sweep.
  reg [8*8-1:0] case_mode = "-";
  integer       case_code = -1;

  // What the sweep cell is called in every line it prints. Three sweeps run:
  // "fixed", "var" (the device asks for the extra latency), and "varlow" (it does
  // not). They must be distinguishable, because they fail for different reasons.
  reg [8*8-1:0] mode_label = "fixed";

  // NEGATIVE CONTROL. `+skew=N` adds N CK to the count the CONTROLLER is told to
  // wait while leaving the count it is graded against alone -- the one-CK latency
  // error #331 and #338 both were. A harness that still passes with +skew=1 is not
  // measuring the controller's latency, and that is exactly the mistake
  // soc_hyperram_sim.py's section 5b made in its first version.
  integer skew = 0;

  task check16(input [8*24-1:0] what, input [15:0] observed, input [15:0] expected);
    begin
      checks = checks + 1;
      if (observed === expected) $display("[tb] PASS %0s = %h", what, observed);
      else begin
        $display("[tb] FAIL %0s = %h, expected %h", what, observed, expected);
        errors = errors + 1;
      end
    end
  endtask

  //
  // Transactions.
  //
  task wait_idle;
    integer n;
    begin
      n = 0;
      while (!idle && n < RECOVER_LIMIT) begin @(posedge clk); n = n + 1; end
      if (!idle) begin
        $display("[tb] NOT-IDLE [%0s code %0d] after %0d cycles (state=%0d timed_out=%0d)",
                 case_mode, case_code, n, state, timed_out);
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
      saw_beat       = 1'b0;
      first_beat     = -1;
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
        $display("[tb] INCOMPLETE [%0s code %0d] %0s of %0d word(s) at %h stopped after %0d (state=%0d timed_out=%0d)",
                 case_mode, case_code, is_write ? "write" : "read", nwords, addr,
                 burst_count, state, timed_out);
        checks = checks + 1;
        errors = errors + 1;
      end
      wait_idle;
      burst_active = 1'b0;
    end
  endtask

  // One latency configuration: program CR0, then write and read a 4-word burst at an
  // address of its own, so a stale success cannot be mistaken for a fresh one.
  //
  // Two things are graded separately, because they indict different parties:
  //
  //   CTRL-BEAT  the beat the CONTROLLER puts its first write word on, against the
  //              count its own contract says it must take (the long one whenever
  //              CR0[3] is set or the device raised RWDS over the CA). True or false
  //              whatever the device does next, so it survives a silent model.
  //   MODEL-BEAT the beat the DEVICE puts its first read word on, taken off the bus,
  //              against its own documented decode plus the turnaround edge.
  //   DATA       what actually landed in, and came back out of, the model's array.
  //
  // The first two are read off opposite ends of the same bus and graded against the
  // same observed CA request, so neither can excuse the other. A case where
  // CTRL-BEAT passes and DATA fails is the device disagreeing with the controller,
  // not the controller being wrong.
  task latency_case(input integer lcode, input fixed, input [31:0] region);
    integer want_beat, model_beat, data_errors;
    begin
      // The decode `hyperram_model.v` documents: clocks = 5 + sext4(code), doubled
      // when CR0[3] selects fixed latency.
      base_ck    = (lcode >= 14) ? (5 + lcode - 16) : (5 + lcode);
      lat_ck     = base_ck * 2;
      mode_label = fixed ? "fixed" : "var";
      case_mode  = mode_label;
      case_code  = lcode;

      cr0_value      = 16'h8f2f;
      cr0_value[7:4] = lcode[3:0];
      cr0_value[3]   = fixed;

      fixed_latency      = fixed;
      latency_clocks     = lat_ck + skew;      // truncates to the port's own width
      low_latency_clocks = base_ck + skew;

      // Programmed BEFORE the data phase that uses it; a register write takes no
      // latency at all, so the count in force for it cannot matter.
      run_burst(ADDR_CR0, 1'b1, 1'b1, 1, cr0_value);
      checks = checks + 1;
      if (model.cr0 !== cr0_value) begin
        $display("[tb] FAIL %0s code %0d: model CR0 = %h, wrote %h",
                 mode_label, lcode, model.cr0, cr0_value);
        errors = errors + 1;
      end

      data_errors = 0;
      a = region + (lcode << 8);
      run_burst(a, 1'b0, 1'b1, 4, 16'h0);

      // What the controller owed, given what the device asked for over the CA. The
      // request is OBSERVED, not assumed: under variable latency the model elects
      // the long count on a refresh collision, and `ca_rwds` is how it said so.
      want_beat  = 4 + 2 * (((ca_rwds !== 2'b00) || fixed) ? lat_ck : base_ck);

      checks = checks + 1;
      if (first_beat === want_beat)
        $display("[tb] CTRL-BEAT PASS %0s code %2d: first write beat %0d, owed %0d (CA RWDS %b, %0d CK short / %0d CK long)",
                 mode_label, lcode, first_beat, want_beat,
                 ca_rwds, base_ck, lat_ck);
      else begin
        $display("[tb] CTRL-BEAT FAIL %0s code %2d: first write beat %0d, owed %0d (CA RWDS %b, %0d CK short / %0d CK long)",
                 mode_label, lcode, first_beat, want_beat,
                 ca_rwds, base_ck, lat_ck);
        errors = errors + 1;
      end

      for (i = 0; i < 4; i = i + 1) begin
        checks = checks + 1;
        if (model.memory[a[21:0] + i[21:0]] !== pattern(a + i)) begin
          $display("[tb] DATA FAIL %0s code %0d write word %0d: model.memory[%h] = %h, expected %h",
                   mode_label, lcode, i, a + i,
                   model.memory[a[21:0] + i[21:0]], pattern(a + i));
          data_errors = data_errors + 1;
          errors = errors + 1;
        end
      end

      run_burst(a, 1'b0, 1'b0, 4, 16'h0);
      // Recomputed off THIS transaction's request, which is what the read was
      // served against.
      model_beat = 4 + 2 * (((ca_rwds !== 2'b00) || fixed) ? lat_ck : base_ck);
      for (i = 0; i < 4; i = i + 1) begin
        checks = checks + 1;
        if (read_words[i] !== pattern(a + i)) begin
          $display("[tb] DATA FAIL %0s code %0d read word %0d = %h, expected %h",
                   mode_label, lcode, i, read_words[i], pattern(a + i));
          data_errors = data_errors + 1;
          errors = errors + 1;
        end
      end

      // Nothing stored and nothing strobed: the device never entered a data phase at
      // all, which is a different failure from serving the wrong beat.
      if (data_errors == 8 && dev_first_beat == -1)
        $display("[tb] MODEL-SILENT %0s code %2d: no read strobe and nothing stored",
                 mode_label, lcode);

      // The device's OWN first read beat, off the bus, against its own decode plus
      // the turnaround edge. Nothing about the controller enters this comparison.
      if (dev_first_beat != -1
          && dev_first_beat !== model_beat + `READ_TURNAROUND_EDGES)
        $display("[tb] MODEL-BEAT %0s code %2d: device served read beat %0d, its own decode plus turnaround says %0d",
                 mode_label, lcode, dev_first_beat,
                 model_beat + `READ_TURNAROUND_EDGES);

      if (strobe_rwds === 2'b10) aligned_bursts  = aligned_bursts  + 1;
      else                       inverted_bursts = inverted_bursts + 1;

      $display("[tb] %0s code %2d: CR0=%h, device read beat %0d, controller strobed at edge %0d on the %0s branch, 4 words %0s",
               mode_label, lcode, cr0_value, dev_first_beat, first_beat,
               (strobe_rwds === 2'b10) ? "aligned" : "inverted",
               (data_errors == 0) ? "OK" : "WRONG");
      case_mode = "-";
      case_code = -1;
    end
  endtask

  task restore_por;
    begin
      fixed_latency      = 1'b1;
      latency_clocks     = 14;
      low_latency_clocks = 7;
      run_burst(ADDR_CR0, 1'b1, 1'b1, 1, 16'h8f2f);
    end
  endtask

  //
  // Stimulus.
  //
  initial begin
    if ($test$plusargs("dump")) begin
      $dumpfile("controller_model_tb.vcd");
      $dumpvars(0, tb);
    end
    if ($value$plusargs("skew=%d", skew))
      $display("[tb] NEGATIVE CONTROL: the controller is told to wait %0d CK more than it is graded against",
               skew);

    $display("[tb] === power-up ===");
    VCC = 1'b1;
    #200_000;                  // tVCS is 150 us; the model reports `ready` at 150
    resetb = 1'b0;
    #1_000;                    // tRP min 200 ns
    resetb = 1'b1;
    #2_000;                    // tRPH min 400 ns
    repeat (4) @(posedge clk);
    rst = 1'b0;
    repeat (4) @(posedge clk);

    //
    // CASE 1 -- register space, against the model's own CR0/CR1 state.
    //
    $display("[tb] === case 1: register read/write ===");
    run_burst(ADDR_ID0, 1'b1, 1'b0, 1, 16'h0);
    check16("ID0", read_words[0], 16'h0c86);
    run_burst(ADDR_ID1, 1'b1, 1'b0, 1, 16'h0);
    check16("ID1", read_words[0], 16'h0001);
    run_burst(ADDR_CR0, 1'b1, 1'b0, 1, 16'h0);
    check16("CR0 (POR)", read_words[0], 16'h8f2f);
    check16("CR0 vs model state", read_words[0], model.cr0);
    run_burst(ADDR_CR1, 1'b1, 1'b0, 1, 16'h0);
    check16("CR1 (POR)", read_words[0], 16'hffc1);
    check16("CR1 vs model state", read_words[0], model.cr1);

    // Drive strength 34 ohm -> 67 ohm. Everything else held, so a read-back that
    // differs anywhere else is a byte-order fault, not a register fault.
    run_burst(ADDR_CR0, 1'b1, 1'b1, 1, 16'haf2f);
    check16("model CR0 after write", model.cr0, 16'haf2f);
    run_burst(ADDR_CR0, 1'b1, 1'b0, 1, 16'h0);
    check16("CR0 read back", read_words[0], 16'haf2f);
    run_burst(ADDR_CR0, 1'b1, 1'b1, 1, 16'h8f2f);

    // CR1[6] = 0 selects the differential clock (#334).
    run_burst(ADDR_CR1, 1'b1, 1'b1, 1, 16'hff81);
    check16("model CR1 after write", model.cr1, 16'hff81);
    run_burst(ADDR_CR1, 1'b1, 1'b0, 1, 16'h0);
    check16("CR1 read back", read_words[0], 16'hff81);
    run_burst(ADDR_CR1, 1'b1, 1'b1, 1, 16'hffc1);

    //
    // CASE 2 -- memory, address-derived pattern, checked against the model's array
    // and then read back through the controller.
    //
    $display("[tb] === case 2: memory write then read back ===");
    for (i = 0; i < 8; i = i + 1) begin
      a = 32'h0000_1000 + i;
      run_burst(a, 1'b0, 1'b1, 1, 16'h0);
      checks = checks + 1;
      if (model.memory[a[21:0]] !== pattern(a)) begin
        $display("[tb] FAIL model.memory[%h] = %h, controller wrote %h",
                 a, model.memory[a[21:0]], pattern(a));
        errors = errors + 1;
      end
    end
    $display("[tb] eight single-word writes land in the model's array");

    // A burst, so a controller that stops advancing its address is caught.
    run_burst(32'h0000_2000, 1'b0, 1'b1, 8, 16'h0);
    for (i = 0; i < 8; i = i + 1) begin
      a = 32'h0000_2000 + i;
      checks = checks + 1;
      if (model.memory[a[21:0]] !== pattern(a)) begin
        $display("[tb] FAIL burst write model.memory[%h] = %h, expected %h",
                 a, model.memory[a[21:0]], pattern(a));
        errors = errors + 1;
      end
    end

    run_burst(32'h0000_2000, 1'b0, 1'b0, 8, 16'h0);
    for (i = 0; i < 8; i = i + 1) begin
      a = 32'h0000_2000 + i;
      checks = checks + 1;
      if (read_words[i] !== pattern(a)) begin
        $display("[tb] FAIL burst read word %0d = %h, expected %h",
                 i, read_words[i], pattern(a));
        errors = errors + 1;
      end
    end
    $display("[tb] 8-word burst write and read back agree");

    // Top of the 8 MiB array: a CA that drops the high address bits lands at 0.
    run_burst(32'h003f_ffff, 1'b0, 1'b1, 1, 16'h0);
    run_burst(32'h003f_ffff, 1'b0, 1'b0, 1, 16'h0);
    check16("mem[0x3fffff]", read_words[0], pattern(32'h003f_ffff));
    checks = checks + 1;
    if (model.memory[22'h00_0000] === pattern(32'h003f_ffff)) begin
      $display("[tb] FAIL the top-address write also landed at 0 -- CA address truncated");
      errors = errors + 1;
    end

    //
    // CASE 3 -- fixed latency at every code the board sweeps.
    //
    $display("[tb] === case 3: fixed latency, CR0[7:4] sweep ===");
    latency_codes[0] = 0;  latency_codes[1] = 1;  latency_codes[2] = 2;
    latency_codes[3] = 6;  latency_codes[4] = 14; latency_codes[5] = 15;
    for (ci = 0; ci < 6; ci = ci + 1)
      latency_case(latency_codes[ci], 1'b1, 32'h0001_0000);
    restore_por;

    //
    // CASE 4 -- variable latency. CR0[3] = 0, and the controller honours the RWDS
    // sample instead of taking the long count unconditionally (#338). Which branch
    // this exercises is set by REFRESH_EVERY, which the driver sweeps: 0 never
    // elects the 2x count and 1 always does, so both branches are reached on
    // purpose rather than by where a transaction counter happened to land.
    //
    $display("[tb] === case 4: variable latency, CR0[3] = 0, REFRESH_EVERY = %0d ===",
             `REFRESH_EVERY);
    for (ci = 0; ci < 6; ci = ci + 1)
      latency_case(latency_codes[ci], 1'b0, 32'h0002_0000);
    restore_por;

    run_burst(ADDR_CR0, 1'b1, 1'b0, 1, 16'h0);
    check16("CR0 restored", read_words[0], 16'h8f2f);

    $display("[tb] BRANCH %0d bursts on READ_DATA's aligned branch, %0d on the clock-inversion branch",
             aligned_bursts, inverted_bursts);
    $display("[tb] === done, %0d checks, %0d failures ===", checks, errors);
    $finish;
  end

  // Nothing here may run for ever: 200 us of power-up plus a few thousand cycles of
  // stimulus. 2 ms is ~4x the longest expected run and exists so a controller that
  // hangs with CS# Low ends the simulation with a line rather than filling a disk.
  initial begin
    #2_000_000;
    $display("[tb] FAIL simulation ran past its 2 ms limit (state=%0d cs=%0d timed_out=%0d)",
             state, cs, timed_out);
    $display("[tb] === done, %0d checks, %0d failures ===", checks, errors + 1);
    $finish;
  end

endmodule
