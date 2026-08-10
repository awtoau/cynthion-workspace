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

`ifndef REFRESH_HUNT_N
  `define REFRESH_HUNT_N 256
`endif

module tb;

  localparam real TCK = 10.0;   // 100 MHz -- below the 166 MHz grade, legal everywhere

  // CA[47:45]. The datasheet's four command encodings, as the top byte with a
  // linear burst: read/write x memory/register.
  localparam [7:0] CMD_MEM_READ  = 8'hA0;
  localparam [7:0] CMD_MEM_WRITE = 8'h20;
  localparam [7:0] CMD_REG_READ  = 8'hE0;
  localparam [7:0] CMD_REG_WRITE = 8'h60;
  localparam [7:0] CMD_MEM_READ_WRAP = 8'h80;   // CA[45] = 0 -> wrapped burst

  // Register space word addresses, as the datasheet's own map gives them.
  localparam [31:0] ADDR_ID0 = 32'h0000_0000;
  localparam [31:0] ADDR_CR0 = 32'h0000_0800;
  localparam [31:0] ADDR_CR1 = 32'h0000_0801;

  // CR0[3] = 1 fixed, CR0[7:4] = 7 -> 2 x 7 = 14 CK before data.
  localparam int LATENCY_CK = 14;

  // CR0 with everything at POR except the latency field and CR0[3].
  // POR is 0x8f2f: normal operation, 34 ohm, code 2 (= 7 CK), fixed, 32-byte wrap.
  localparam [15:0] CR0_BASE = 16'h8f07;   // code 0, VARIABLE; OR in code<<4 and bit 3


  // Transactions in the refresh hunt. At ~210 ns each, 256 is ~54 us of model
  // time and catches two 2x elections in the vendor model -- enough to prove the
  // case exists, cheap enough for the default regression. Raise it with
  // `--hunt N` to measure the interval rather than just find it.
  localparam int REFRESH_HUNT_N = `REFRESH_HUNT_N;

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

  // Filled in by drive_ca / capture_word so a test can assert on the *timing* of a
  // transaction, not only its data. Latency is the thing #338 is stuck on and it
  // is invisible in the returned word.
  reg        ca_rwds_high;      // did the device request extra latency during CA?
  integer    strobe_edges;      // edges from the end of CA to the first read strobe
  integer    fixed_edges, var_edges;
  reg [15:0] burst [0:31];
  integer    extra;

  // The same question at finer grain: RWDS at each of the six CA edges, and the
  // latency in CK rather than edges.
  //
  // The count runs from the LAST CA rising edge, not from CS# falling and not from
  // the last CA byte -- the last two CA bytes share the third clock. `rwds_ca` is
  // six samples rather than one because RWDS is not driven until tDSV, so WHICH
  // edge a controller picks decides whether it reads an answer or a float.
  realtime   t_ca_last_rise;
  realtime   t_first_data;
  real       lat_ck;             // measured initial latency, in CK
  reg  [5:0] rwds_ca;            // RWDS at each of the six CA edges, [5] first
  integer    hunt_long, hunt_short;

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
  //
  // RWDS is recorded at every CA edge, 0.5 ns past it. That is the extra-latency
  // request and it is the only thing that says 1x or 2x with CR0[3] = 0; recording
  // all six edges rather than one says whether the *instant* a controller picks
  // can change the answer. (#338, #342)
  task drive_ca(input [47:0] c);
    begin
      ca_rwds_high = 1'b0;
      rwds_ca = 6'bxxxxxx;
      @(negedge clk); #1;
      csb     = 1'b0;
      adq_oe  = 1'b1;
      adq_drv = c[47:40];
      // One sampling instant for both records, 0.5 ns past each edge. `ca_rwds_high`
      // keeps the first four edges, as it always did; `rwds_ca` keeps all six, so a
      // `z` inside tDSV is visible instead of being ORed away.
      @(posedge clk); t_ca_last_rise = $realtime;
      #0.5; rwds_ca[5] = rwds; if (rwds === 1'b1) ca_rwds_high = 1'b1;
      #0.5; adq_drv = c[39:32];
      @(negedge clk);
      #0.5; rwds_ca[4] = rwds; if (rwds === 1'b1) ca_rwds_high = 1'b1;
      #0.5; adq_drv = c[31:24];
      @(posedge clk); t_ca_last_rise = $realtime;
      #0.5; rwds_ca[3] = rwds; if (rwds === 1'b1) ca_rwds_high = 1'b1;
      #0.5; adq_drv = c[23:16];
      @(negedge clk);
      #0.5; rwds_ca[2] = rwds; if (rwds === 1'b1) ca_rwds_high = 1'b1;
      #0.5; adq_drv = c[15:8];
      @(posedge clk); t_ca_last_rise = $realtime;
      #0.5; rwds_ca[1] = rwds; #0.5; adq_drv = c[7:0];
      @(negedge clk); #0.5; rwds_ca[0] = rwds; #0.5; adq_oe = 1'b0;
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
    integer  edges;
    realtime t_edge;
    begin
      word   = 16'hzzzz;
      edges  = 0;
      t_edge = $realtime;
      t_first_data = 0;
      lat_ck = -1.0;
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
        @(clk); t_edge = $realtime; #0.5;
        edges = edges + 1;
      end
      strobe_edges = edges;
      if (edges >= 64)
        $display("[tb] no read strobe within %0d edges -- device silent", edges);
      else begin
        // The strobe edge IS the first data edge, so the initial latency is the
        // gap back to the last CA rising edge, in CK.
        t_first_data = t_edge;
        lat_ck       = (t_first_data - t_ca_last_rise) / TCK;
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
  // this wrong -- see docs/chips/hyperram/models.md).
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
  //
  // `lat` is the count the HOST believes: a write has no strobe to self-align on,
  // so it is the case where getting the latency wrong shows up as data. That is
  // exactly the controller's position with CR0[3] = 0.
  task write_memory_lat(input [31:0] word_addr, input [15:0] word, input int lat);
    begin
      drive_ca(ca(CMD_MEM_WRITE, word_addr));
      repeat (lat - 1) @(posedge clk);
      adq_oe   = 1'b1;
      rwds_oe  = 1'b1;
      rwds_drv = 1'b0;
      @(negedge clk); #1; adq_drv = word[15:8];
      @(posedge clk); #1; adq_drv = word[7:0];
      @(negedge clk); #1; adq_oe = 1'b0; rwds_oe = 1'b0;
      bus_idle;
    end
  endtask

  task write_memory(input [31:0] word_addr, input [15:0] word);
    begin
      write_memory_lat(word_addr, word, LATENCY_CK);
    end
  endtask

  task read_memory(input [31:0] word_addr, output [15:0] word);
    begin
      drive_ca(ca(CMD_MEM_READ, word_addr));
      capture_word(word);
      bus_idle;
    end
  endtask

  // A burst read: hunt the strobe once, then take `n` words back to back. Returns
  // them in `burst`, which is where a wrap shows up as an address sequence rather
  // than as a single wrong word.
  task read_burst(input [7:0] cmd, input [31:0] word_addr, input integer n);
    integer w;
    begin
      drive_ca(ca(cmd, word_addr));
      capture_word(burst[0]);
      for (w = 1; w < n; w = w + 1) begin
        @(clk); #0.5; burst[w][15:8] = adq;
        @(clk); #0.5; burst[w][7:0]  = adq;
      end
      bus_idle;
    end
  endtask

  // Linear burst write of `n` words, RWDS held low so every byte commits.
  task write_burst(input [31:0] word_addr, input [15:0] first, input integer n);
    integer w;
    begin
      drive_ca(ca(CMD_MEM_WRITE, word_addr));
      repeat (LATENCY_CK - 1) @(posedge clk);
      adq_oe   = 1'b1;
      rwds_oe  = 1'b1;
      rwds_drv = 1'b0;
      for (w = 0; w < n; w = w + 1) begin
        @(negedge clk); #1; adq_drv = (first + w) >> 8;
        @(posedge clk); #1; adq_drv = (first + w);
      end
      @(negedge clk); #1; adq_oe = 1'b0; rwds_oe = 1'b0;
      bus_idle;
    end
  endtask

  // CR0[7:4] -> initial latency L in CK. Sparse and sign-extended: L = 5 + sext4,
  // so codes 3..13 are reserved and have no count. Indexed 0..4 so a loop can walk
  // the five that exist. Datasheet Table 8, rev A01-006 p.21.
  function [3:0] lat_code(input integer n);
    case (n) 0: lat_code = 4'd14; 1: lat_code = 4'd15; 2: lat_code = 4'd0;
             3: lat_code = 4'd1;  default: lat_code = 4'd2; endcase
  endfunction

  function integer lat_ck_of(input integer n);
    case (n) 0: lat_ck_of = 3; 1: lat_ck_of = 4; 2: lat_ck_of = 5;
             3: lat_ck_of = 6; default: lat_ck_of = 7; endcase
  endfunction

  // CR0 with the latency field and CR0[3] set, everything else at POR.
  task set_latency(input [3:0] code, input fixed);
    begin
      write_register(ADDR_CR0, CR0_BASE | (code << 4) | (fixed ? 16'h0008 : 16'h0));
    end
  endtask

  // The measured initial latency against the count the datasheet's arithmetic
  // gives. `lat_ck` is the gap from the last CA rising edge to the edge on which
  // the strobe was first SEEN, 0.5 ns past it.
  //
  // The window is [expect, expect+1), not +/- half a beat: both models put the
  // strobe half a clock past the datasheet's count at TCK = 10 ns, so the
  // fractional part is not the thing being checked. A whole beat of error is
  // 1.0 CK and still fails, which is what this exists to catch -- L against 2L
  // differ by at least 3.
  task check_lat(input [63:0] mode, input integer l, input integer expect_ck);
    begin
      if (lat_ck >= expect_ck && lat_ck < expect_ck + 1.0)
        $display("[tb] PASS %0s L=%0d latency %0d CK (measured %0.1f)", mode, l, expect_ck, lat_ck);
      else begin
        $display("[tb] FAIL %0s L=%0d latency %0.1f CK, expected %0d", mode, l, lat_ck, expect_ck);
        errors = errors + 1;
      end
    end
  endtask

  task check(input [255:0] what, input [15:0] observed, input [15:0] expected);
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

    // The axis #338 is stuck on. With CR0[3] = 1 the latency is always 2x and the
    // CA-period RWDS carries no information; with CR0[3] = 0 that RWDS *is* the
    // answer, so a controller that samples it wrongly mis-times every read while
    // passing every fixed-latency test. Measure both, in edges from the end of CA
    // to the first read strobe: 14 CK = 28 edges fixed, 7 CK = 14 edges variable.
    $display("[tb] === latency: fixed vs variable (CR0[3]) ===");
    write_register(ADDR_CR0, 16'h8f2f);            // fixed, latency code 7
    read_register(ADDR_ID0, got);
    $display("[tb] fixed    strobe at %0d edges, RWDS during CA = %0d",
             strobe_edges, ca_rwds_high);
    fixed_edges = strobe_edges;
    check("ID0 with fixed latency", got, 16'h0c86);

    write_register(ADDR_CR0, 16'h8f27);            // CR0[3] = 0 -> variable
    read_register(ADDR_CR0, got);
    check("CR0 reads back variable", got, 16'h8f27);
    read_register(ADDR_ID0, got);
    $display("[tb] variable strobe at %0d edges, RWDS during CA = %0d",
             strobe_edges, ca_rwds_high);
    var_edges = strobe_edges;
    check("ID0 with variable latency", got, 16'h0c86);

    if (var_edges < fixed_edges)
      $display("[tb] PASS variable latency is shorter: %0d edges vs %0d",
               var_edges, fixed_edges);
    else begin
      $display("[tb] FAIL variable latency did not shorten: %0d vs %0d",
               var_edges, fixed_edges);
      errors = errors + 1;
    end

    write_register(ADDR_CR0, 16'h8f2f);            // back to the POR default
    read_register(ADDR_CR0, got);  check("CR0 restored", got, 16'h8f2f);

    // CR0[15] = 0 is Deep Power Down. Everything stops until RESET#, and a
    // register write that lands one edge late writes exactly this by accident --
    // which is why it is worth knowing what it looks like.
    $display("[tb] === deep power down and reset recovery ===");
    write_register(ADDR_CR0, 16'h0f2f);
    #500;                                          // tCSDPD is 200 ns
    read_register(ADDR_ID0, got);
    if (got === 16'hzzzz)
      $display("[tb] PASS device is silent in deep power down");
    else begin
      $display("[tb] FAIL device answered %h in deep power down", got);
      errors = errors + 1;
    end
    resetb = 1'b0; #1_000; resetb = 1'b1; #2_000;  // tRP 200 ns, tRPH 400 ns
    bus_idle;
    read_register(ADDR_CR0, got);
    check("CR0 after reset recovery", got, 16'h8f2f);

    // CR0[2] = 0 enables wrapped bursts; CA[45] = 0 asks for one. With a 32-byte
    // (16-word) group, a wrapped read from the middle of a group runs to the end
    // and comes back to the start -- which is what makes it useful for a cache
    // line, and what option 3 in w956a8.md is about.
    $display("[tb] === wrapped burst ===");
    write_burst(32'h00_0100, 16'h1000, 32);        // 0x1000..0x101f at 0x100..0x11f,
                                                   // two groups, so a burst that leaves
                                                   // the first one has known data to read
    read_burst(CMD_MEM_READ, 32'h00_0100, 4);
    $display("[tb] linear  from 0x100: %h %h %h %h",
             burst[0], burst[1], burst[2], burst[3]);
    if (burst[0] === 16'h1000 && burst[1] === 16'h1001 &&
        burst[2] === 16'h1002 && burst[3] === 16'h1003)
      $display("[tb] PASS linear burst advances");
    else begin
      $display("[tb] FAIL linear burst did not advance"); errors = errors + 1;
    end

    write_register(ADDR_CR0, 16'h8f2b);            // CR0[2] = 0 -> wrapped enabled
    read_register(ADDR_CR0, got);  check("CR0 wrapped enabled", got, 16'h8f2b);
    // 18 words from 13 words into a 16-word group: 3 to the group end, 13 back
    // round, and then word 16 says which kind of wrap this is. Legacy returns to
    // the start address and goes round again; hybrid leaves the group and
    // continues linearly. That is the distinction option 3 in w956a8.md needs.
    read_burst(CMD_MEM_READ_WRAP, 32'h00_010d, 18);
    $display("[tb] wrapped from 0x10d: %h %h %h %h %h %h ... [16]=%h [17]=%h",
             burst[0], burst[1], burst[2], burst[3], burst[4], burst[5],
             burst[16], burst[17]);
    if (burst[0] === 16'h100d && burst[2] === 16'h100f && burst[3] === 16'h1000)
      $display("[tb] PASS wrapped burst wraps to the group start");
    else begin
      $display("[tb] FAIL wrapped burst did not wrap at the group boundary");
      errors = errors + 1;
    end
    if (burst[16] === 16'h1010)
      $display("[tb] PASS hybrid burst: leaves the group after one pass");
    else if (burst[16] === 16'h100d)
      $display("[tb] PASS legacy burst: stays in the group");
    else
      $display("[tb] NOTE word 16 is %h -- neither hybrid nor legacy", burst[16]);
    write_register(ADDR_CR0, 16'h8f2f);            // back to the POR default

`ifdef VENDOR_ONLY
    // Under variable latency the device asks for 2x only when a refresh is due,
    // so the latency changes transaction to transaction. The twin has no refresh
    // and cannot show this; the vendor model can, and it is the case a correct
    // controller has to survive.
    $display("[tb] === refresh collisions under variable latency ===");
    write_register(ADDR_CR0, 16'h8f27);            // variable
    extra = 0;
    for (i = 0; i < 200; i = i + 1) begin
      read_memory(32'h00_0100, got);
      if (ca_rwds_high) extra = extra + 1;
    end
    $display("[tb] %0d of 200 reads were given the extra latency", extra);
    if (extra > 0 && extra < 200)
      $display("[tb] PASS latency varies transaction to transaction");
    else begin
      $display("[tb] FAIL extra latency was %0d of 200 -- not varying", extra);
      errors = errors + 1;
    end
    write_register(ADDR_CR0, 16'h8f2f);
`endif

    // === variable latency, CR0[3] = 0 ===
    //
    // With fixed latency the answer is always 2L, so a controller that never looks
    // at RWDS is right by accident. With CR0[3] = 0 the RWDS the device drives
    // during the CA *is* the answer, and the two counts differ by L. This walks
    // both modes over all five codes and measures what arrives. (#338, #342)
    $display("[tb] === latency: all five codes, fixed and variable ===");
    for (i = 0; i < 5; i = i + 1) begin
      // Reference word, written under FIXED latency where the count is not in
      // question, so a variable-mode read failure is the read's and not the write's.
      set_latency(lat_code(i), 1'b1);
      write_memory_lat(32'h00_0010 + i, 16'h1000 + i, 2 * lat_ck_of(i));
      read_memory(32'h00_0010 + i, got);
      $display("[tb] fix L=%0d code=%0d: RWDS over the CA = %b, latency %0.1f CK, word %h",
               lat_ck_of(i), lat_code(i), rwds_ca, lat_ck, got);
      check_lat("fix", lat_ck_of(i), 2 * lat_ck_of(i));
      check("fix word", got, 16'h1000 + i);

      set_latency(lat_code(i), 1'b0);
      read_memory(32'h00_0010 + i, got);
      $display("[tb] var L=%0d code=%0d: RWDS over the CA = %b, latency %0.1f CK, word %h",
               lat_ck_of(i), lat_code(i), rwds_ca, lat_ck, got);
      check_lat("var", lat_ck_of(i), lat_ck_of(i));
      check("var word", got, 16'h1000 + i);

      // A variable-latency WRITE, at the count the read just measured. No strobe to
      // self-align on, so this is where a wrong count lands as data.
      write_memory_lat(32'h00_0020 + i, 16'h2000 + i, lat_ck_of(i));
      set_latency(lat_code(i), 1'b1);
      read_memory(32'h00_0020 + i, got);
      check("var write", got, 16'h2000 + i);
    end

    // === does the device ever ask for 2x in variable mode? ===
    //
    // Cause 3 for #338: distributed refresh. The part may raise RWDS during the CA
    // to buy time for a refresh, and a controller that then waits L instead of 2L
    // loses the whole burst. tCSM is 4 us, so over ~54 us of back-to-back reads a
    // 4 us refresh has ~13 chances to collide. Zero here is a result too: it says
    // this model does not exercise the case.
    $display("[tb] === variable latency: %0d transactions, hunting a 2x election ===",
             REFRESH_HUNT_N);
    set_latency(4'd2, 1'b0);              // L = 7, the power-on code, variable
    hunt_long  = 0;
    hunt_short = 0;
    for (i = 0; i < REFRESH_HUNT_N; i = i + 1) begin
      read_memory(32'h00_0010 + (i % 5), got);
      if (lat_ck > 1.5 * 7) begin
        hunt_long = hunt_long + 1;
        $display("[tb] 2x election at transaction %0d, t = %0.0f ns: RWDS over the CA = %b, latency %0.1f CK",
                 i, $realtime, rwds_ca, lat_ck);
      end else if (lat_ck > 0.0) hunt_short = hunt_short + 1;
      else begin
        $display("[tb] FAIL transaction %0d returned no strobe at all", i);
        errors = errors + 1;
      end
    end
    $display("[tb] variable-latency elections: %0d short, %0d long, of %0d",
             hunt_short, hunt_long, REFRESH_HUNT_N);
    set_latency(4'd2, 1'b1);              // back to POR before the tCSM section

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
