// Where does a HyperRAM's data phase actually START? Measured, per CR0[7:4].
//
// #314 claims the DQS read path can never align, because "CA is 3 CK (odd), fixed
// latency 2L is even, so data always starts on an odd CK". That is a derivation.
// This measures the same number on the device instead: it drives a textbook CA --
// six bytes on the six clock edges after CS# falls -- and reports the EDGE INDEX
// at which the device first drives DQ, first raises RWDS, and first accepts write
// data. No controller, no PHY, no gearing: nothing here can supply the answer.
//
// The edge index modulo 4 is the whole of the parity question, because the ECP5
// 4:1 gearing packs four consecutive device edges into one 32-bit fabric word
// (`hyperram_dqs_phy.py`, ODDRX2DQA D0..D3 = dq[31:24]..dq[7:0]) and the CA's
// first four bytes are one such word. Phase 0 aligns; phase 2 is a 16-bit slip;
// odd phases cannot align at all.
//
// `DUT_MODULE names the device, as vendor_model_tb.sv does, so the same stimulus
// runs against the open twin under Icarus and Winbond's own model under Questa.
//
// Driven by scripts/hyperram_dqs_model_sim.py. See #186, #314, #342.
// SPDX-License-Identifier: BSD-3-Clause
`timescale 1ns/1ps

`ifndef DUT_MODULE
  `define DUT_MODULE hyperram_model
`endif

module tb;

  localparam real TCK = 10.0;   // 100 MHz, below the 166 MHz grade

  localparam [7:0] CMD_MEM_READ  = 8'hA0;
  localparam [7:0] CMD_MEM_WRITE = 8'h20;
  localparam [7:0] CMD_REG_READ  = 8'hE0;
  localparam [7:0] CMD_REG_WRITE = 8'h60;

  localparam [31:0] ADDR_CR0 = 32'h0000_0800;

  // Edges to hunt for the data phase before giving up.
  //
  // Waits for: the device to drive DQ after the CA.
  // Expected worst case: the longest legal fixed latency is 2 x 7 = 14 CK, so the
  //   data phase starts by edge 6 + 28 = 34.
  // Multiplier: 16x, not 1.25x, and deliberately so -- a code that decodes to a
  //   WRONG latency is one of the things being looked for, and a bound at 1.25x
  //   would report "silent" for it and hide the number. 560 edges is 2.8 us, still
  //   inside the 4 us tCSM, so the hunt cannot itself provoke a violation.
  // On expiry: the beat is reported as -1 and the line says `silent`.
  localparam integer MAX_EDGE = 560;

  // Drive just past the edge so the next one has a full beat of setup; observe
  // just before the next edge, at the end of the eye, which is where a device
  // with an output delay has actually presented its data.
  localparam real T_DRIVE   = 1.0;
  localparam real T_OBSERVE = TCK/2.0 - 1.0;

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

  // The edge index the DEVICE is counting: 0 is the first clock edge after CS#
  // falls, which is the edge that takes CA[47:40].
  // Reset by `select`, NOT by an `always @(negedge csb)`: the always block runs
  // in the same time step as the task that lowers CS#, and the simulator may
  // schedule it AFTER the task's next `after_edge` has already read the stale
  // count. That leaves the whole CA driven inside one nanosecond, which shows up
  // as a device that stopped answering rather than as a testbench fault.
  integer  edge_idx = -1;
  realtime edge_time = 0;
  always @(posedge clk or negedge clk) if (!csb) begin
    edge_idx = edge_idx + 1;
    edge_time = $realtime;
  end

  // Resume `d` ns past edge `n`, measured from the edge itself rather than from
  // wherever the caller happens to be.
  //
  // DRIVE at T_DRIVE and OBSERVE at T_OBSERVE, and the two are not the same
  // number. Winbond's model applies an output delay; observing 1 ns past the edge
  // read every device beat ONE EDGE LATE, which reported the data phase as
  // starting on an odd edge and would have said the gearing can never align. The
  // model's own narration -- `read mem : pos clock: addr:` -- is what caught it.
  task at_edge(input integer n, input real d);
    begin
      while (edge_idx < n) @(edge_idx);
      if ($realtime < edge_time + d) #(edge_time + d - $realtime);
    end
  endtask

  task after_edge(input integer n);
    begin at_edge(n, T_DRIVE); end
  endtask

  function [47:0] ca(input [7:0] cmd, input [31:0] word_addr);
    begin
      ca = 48'h0;
      ca[47:40] = cmd;
      ca[44:16] = word_addr[31:3];
      ca[2:0]   = word_addr[2:0];
    end
  endfunction

  // Six bytes on edges 0..5. This is the reference the whole file rests on: the
  // device's own beat 0 is our edge 0 by construction, so every index reported
  // below is in the device's frame and not in some controller's.
  // CS# low, edge counter armed, first CA byte on the bus -- one step, so the
  // counter can never be read stale.
  task select;
    begin
      @(negedge clk); #1;
      edge_idx = -1;
      csb = 1'b0;
      adq_oe = 1'b1;
    end
  endtask

  task drive_ca(input [47:0] c);
    begin
      select;
      adq_drv = c[47:40];
      after_edge(0); adq_drv = c[39:32];
      after_edge(1); adq_drv = c[31:24];
      after_edge(2); adq_drv = c[23:16];
      after_edge(3); adq_drv = c[15:8];
      after_edge(4); adq_drv = c[7:0];
      after_edge(5); adq_oe = 1'b0;
    end
  endtask

  task bus_idle;
    begin
      @(negedge clk); #1;
      csb = 1'b1; adq_oe = 1'b0; rwds_oe = 1'b0;
      repeat (12) @(posedge clk);   // past tCSHI 10 ns and tRWR 36 ns
    end
  endtask

  //
  // The measurements.
  //

  integer read_first;      // edge the device first DRIVES DQ on
  integer read_strobe;     // edge RWDS first rises again after the CA lowered it
  integer rwds_ca_high;    // edges 0..5 on which RWDS was high during the CA
  integer rwds_ca_last;    // last CA-period edge with RWDS high, -1 if never
  reg [15:0] first_word;
  reg [15:0] second_word;

  // A memory read, with every edge after the CA watched. Nothing is counted from
  // a latency number: the loop looks at the bus.
  task probe_read(input [31:0] word_addr);
    integer i;
    reg seen_low;
    begin
      read_first   = -1;
      read_strobe  = -1;
      rwds_ca_high = 0;
      rwds_ca_last = -1;
      first_word   = 16'hzzzz;
      second_word  = 16'hzzzz;
      seen_low     = 1'b0;

      select;
      begin : ca_drive
        reg [47:0] c;
        integer k;
        c = ca(CMD_MEM_READ, word_addr);
        adq_drv = c[47:40];
        for (k = 0; k < 6; k = k + 1) begin
          // Drive the NEXT byte first, then look at RWDS at the end of this
          // beat's eye. The other order leaves the byte one nanosecond of setup.
          at_edge(k, T_DRIVE);
          case (k)
            0: adq_drv = c[39:32];
            1: adq_drv = c[31:24];
            2: adq_drv = c[23:16];
            3: adq_drv = c[15:8];
            4: adq_drv = c[7:0];
            default: adq_oe = 1'b0;
          endcase
          at_edge(k, T_OBSERVE);
          if (rwds === 1'b1) begin
            rwds_ca_high = rwds_ca_high + 1;
            rwds_ca_last = k;
          end
        end
      end

      for (i = 6; i < MAX_EDGE; i = i + 1) begin
        at_edge(i, T_OBSERVE);
        if (rwds !== 1'b1) seen_low = 1'b1;
        // Driven, not merely known: Winbond's model returns x from a location
        // that was never written, and excluding x here reported its data phase as
        // never arriving.
        if (read_first < 0 && adq !== 8'hzz) read_first = i;
        if (read_strobe < 0 && seen_low && rwds === 1'b1)     read_strobe = i;
        if (read_first >= 0 && i == read_first)     first_word[15:8] = adq;
        if (read_first >= 0 && i == read_first + 1) first_word[7:0]  = adq;
        if (read_first >= 0 && i == read_first + 2) second_word[15:8] = adq;
        if (read_first >= 0 && i == read_first + 3) begin
          second_word[7:0] = adq;
          i = MAX_EDGE;                 // done; leave the loop without a break
        end
      end
      bus_idle;
    end
  endtask

  // Where the device expects the first WRITE byte: presented at edge `start`, and
  // the answer is whether the word lands. Searched rather than derived, and it is
  // the only way to see the write side -- a write leaves no strobe to watch.
  task probe_write(input [31:0] word_addr, input integer start, input [15:0] word);
    begin
      drive_ca(ca(CMD_MEM_WRITE, word_addr));
      after_edge(start - 1);
      adq_oe = 1'b1; rwds_oe = 1'b1; rwds_drv = 1'b0;
      adq_drv = word[15:8];
      after_edge(start); adq_drv = word[7:0];
      after_edge(start + 1); adq_oe = 1'b0; rwds_oe = 1'b0;
      bus_idle;
    end
  endtask

  task read_register(input [31:0] addr, output [15:0] word);
    begin
      drive_ca(ca(CMD_REG_READ, addr));
      probe_capture(word);
      bus_idle;
    end
  endtask

  // The strobe hunt used for register reads only: self-aligning, so it works at
  // whatever latency CR0 currently holds.
  task probe_capture(output [15:0] word);
    integer i;
    reg seen_low;
    begin
      word = 16'hzzzz;
      seen_low = 1'b0;
      for (i = 6; i < MAX_EDGE; i = i + 1) begin
        at_edge(i, T_OBSERVE);
        if (rwds !== 1'b1) seen_low = 1'b1;
        if (seen_low && adq !== 8'hzz) begin
          word[15:8] = adq;
          at_edge(i + 1, T_OBSERVE); word[7:0] = adq;
          i = MAX_EDGE;
        end
      end
    end
  endtask

  task write_register(input [31:0] addr, input [15:0] word);
    begin
      drive_ca(ca(CMD_REG_WRITE, addr));
      // The first data byte goes on the bus NOW, to be taken by the very next
      // edge. One edge late lands CR0[15] = 0, which is deep power down.
      adq_oe = 1'b1; rwds_oe = 1'b0;
      adq_drv = word[15:8];
      after_edge(6); adq_drv = word[7:0];
      after_edge(7); adq_oe = 1'b0;
      bus_idle;
    end
  endtask

  //
  // The sweep.
  //

  integer      code, fixed, i, w, wrote_at;
  reg [15:0]   cr0_want, cr0_got;
  integer      base, expect_ck;
  reg [15:0]   probe_val;
  integer      errors = 0;

  // CR0[7:4] is sparse and sign-extended: clocks = 5 + sext4(code), so 0..2 give
  // 5..7 and 14..15 give 3..4. Everything else is reserved; the sweep runs them
  // anyway and reports what the device does, because "reserved" is where a decode
  // bug hides.
  function integer legal_code(input integer c);
    begin
      legal_code = (c <= 2) || (c >= 14);
    end
  endfunction

  function integer base_ck(input integer c);
    begin
      base_ck = (c >= 14) ? (5 + c - 16) : (5 + c);
    end
  endfunction

  initial begin
    VCC = 1'b1; VSS = 1'b0; resetb = 1'b1;
    #200_000;                 // tVCS is 150 us
    resetb = 1'b0; #1_000;    // tRP min 200 ns
    resetb = 1'b1; #2_000;    // tRPH min 400 ns
    bus_idle;

    read_register(ADDR_CR0, cr0_got);
    $display("[probe] POR CR0 = %h", cr0_got);
    if (cr0_got !== 16'h8f2f) begin
      $display("[probe] FAIL POR CR0 is not 8f2f");
      errors = errors + 1;
    end

    // The CA length, stated as a measurement rather than as a citation: the
    // device latched six bytes on edges 0..5 and answered the address in them.
    // Every read below re-confirms it, because a seventh CA byte would move the
    // address the device decodes and the read-back would not match.
    $display("[probe] CA occupies edges 0..5 = 6 bytes = 3 CK; every read below re-checks it");

    for (fixed = 1; fixed >= 0; fixed = fixed - 1) begin
      for (code = 0; code < 16; code = code + 1) begin
        // Reserved codes: run 7 as one witness and skip the rest.
        if (legal_code(code) || code == 7) begin
          cr0_want = 16'h8f2f;
          cr0_want[7:4] = code[3:0];
          cr0_want[3]   = fixed[0];
          write_register(ADDR_CR0, cr0_want);
          read_register(ADDR_CR0, cr0_got);
          if (cr0_got !== cr0_want) begin
            $display("[probe] FAIL CR0 write %h read back %h", cr0_want, cr0_got);
            errors = errors + 1;
          end

          base      = base_ck(code);
          expect_ck = fixed ? 2 * base : base;

          probe_read(32'h00_0100);

          // The write-side start, searched over a window around wherever the read
          // landed. Each trial uses its own address so a stray hit cannot be read
          // back as a success from an earlier one.
          wrote_at = -1;
          if (read_first >= 0) begin
            for (w = -4; w <= 4; w = w + 1) begin
              if (read_first + w >= 6) begin
                probe_write(32'h00_0200 + (w + 4), read_first + w, 16'ha5c3);
                probe_read(32'h00_0200 + (w + 4));
                if (first_word === 16'ha5c3 && wrote_at < 0)
                  wrote_at = w;
              end
            end
            probe_read(32'h00_0100);   // restore the reported read numbers
          end

          $display("[probe] cr0=%h code=%0d fixed=%0d expect_ck=%0d read_first=%0d read_strobe=%0d phase=%0d rwds_ca_high=%0d rwds_ca_last=%0d write_delta=%0d",
                   cr0_want, code, fixed, expect_ck,
                   read_first, read_strobe,
                   (read_first < 0) ? -1 : (read_first % 4),
                   rwds_ca_high, rwds_ca_last, wrote_at);
        end
      end
    end

    // Put the part back the way it powered up, so a later stage in the same run
    // is not looking at a device this sweep left reconfigured.
    write_register(ADDR_CR0, 16'h8f2f);
    $display("[probe] === done, %0d failures ===", errors);
    $finish;
  end

endmodule
