// The DQS controller and PHY, in ECP5 primitives, driving Winbond's own model.
// Settles #186: reads-late or writes-early.
`timescale 1ns/1ps

module dqs_tb;   // see scripts/hyperram_dqs_model_sim.py

  localparam real T_SYNC = 20.0;   // 50 MHz sync, 100 MHz fast -> CK 100 MHz

  reg sync_clk = 1'b0, fast_clk = 1'b0;
  reg rst = 1'b1, fast_rst = 1'b1;

  always #(T_SYNC/2.0) sync_clk = ~sync_clk;
  always #(T_SYNC/4.0) fast_clk = ~fast_clk;

  reg  [31:0] address = 0;
  reg         register_space = 0, perform_write = 0, single_page = 0;
  reg         start_transfer = 0, final_word = 0;
  reg  [31:0] write_data = 0;
  wire [31:0] read_data;
  wire        idle, read_ready, write_ready, dll_ready;
  wire  [3:0] state;

  wire ram_clk_p, ram_clk_n, ram_cs, ram_reset;
  wire ram_rwds;
  wire [7:0] ram_dq;

  dqs_top dut (
    .clk(sync_clk), .rst(rst), .fast_clk(fast_clk), .fast_rst(fast_rst),
    .address(address), .register_space(register_space),
    .perform_write(perform_write), .single_page(single_page),
    .start_transfer(start_transfer), .final_word(final_word),
    .write_data(write_data), .read_data(read_data),
    .idle(idle), .read_ready(read_ready), .write_ready(write_ready),
    .state(state), .dll_ready(dll_ready),
    .ram_clk_p(ram_clk_p), .ram_clk_n(ram_clk_n), .ram_cs(ram_cs),
    .ram_rwds(ram_rwds), .ram_dq(ram_dq), .ram_reset(ram_reset)
  );

  // VCC/VSS are ports on the vendor model; the board ties them.
  W956A8MBYA ram (
    .adq(ram_dq), .clk(ram_clk_p), .clk_n(ram_clk_n), .csb(ram_cs),
    .rwds(ram_rwds), .VCC(1'b1), .VSS(1'b0), .resetb(ram_reset)
  );

  // Lattice's primitive models reach for two global nets by absolute hierarchical
  // name -- GSR_INST.GSRNET and PUR_INST.PURNET. Diamond's own flow instantiates
  // them; a hand-written testbench has to, and without them vopt fails with
  // "Failed to find 'GSR_INST' in hierarchical name" for every DDR primitive in
  // the PHY. Both held inactive: this design drives its own resets.
  GSR GSR_INST (.GSR(1'b1));
  PUR PUR_INST (.PUR(1'b1));

  integer i;

  // Latch read data when the controller says it is ready, not afterwards: the
  // bus has moved on by the time the task returns.
  reg [31:0] captured = 32'hxxxx_xxxx;
  always @(posedge sync_clk) if (read_ready) captured <= read_data;

  // Track the FSM, not `idle`. `idle` sits at x from reset until the controller
  // has run a transaction, so every wait on it either falls straight through or
  // times out -- which is what made the first read look like it did nothing.
  // `state` is 0 (IDLE) from the first clock edge and is exported for exactly
  // this purpose (#318).
  task wait_idle;
    begin
      i = 0;
      while (state !== 4'd0 && i < 20000) begin @(posedge sync_clk); i = i + 1; end
      if (i >= 20000) $display("[dqs] TIMEOUT waiting for IDLE, state=%0d", state);
    end
  endtask

  // The controller takes a cycle or two to leave IDLE after `start_transfer`.
  // Waiting only for `idle` therefore returns instantly, before the transaction
  // has begun -- which reads as "the read did nothing" when in fact it never
  // started. Wait for busy first, then for idle.
  task wait_done;
    begin
      i = 0;
      while (state === 4'd0 && i < 100) begin @(posedge sync_clk); i = i + 1; end
      if (i >= 100) $display("[dqs] transaction never started, state=%0d", state);
      wait_idle;
      repeat (4) @(posedge sync_clk);
    end
  endtask

  task do_write(input [31:0] addr, input [31:0] data);
    begin
      wait_idle;
      @(posedge sync_clk);
      address        = addr;
      write_data     = data;
      register_space = 1'b0;
      perform_write  = 1'b1;
      single_page    = 1'b0;
      final_word     = 1'b1;
      start_transfer = 1'b1;
      @(posedge sync_clk);
      start_transfer = 1'b0;
      wait_done;
      final_word = 1'b0;
    end
  endtask

  task do_read(input [31:0] addr);
    begin
      wait_idle;
      @(posedge sync_clk);
      address        = addr;
      register_space = 1'b0;
      perform_write  = 1'b0;
      single_page    = 1'b0;
      final_word     = 1'b1;
      start_transfer = 1'b1;
      @(posedge sync_clk);
      start_transfer = 1'b0;
      wait_idle;
      final_word = 1'b0;
    end
  endtask

  // Visibility: this design is being brought up for the first time in simulation,
  // and "it did nothing" and "it hung in state 3" look identical from outside.
  reg [3:0] last_state = 4'hf;
  always @(posedge sync_clk) if (state !== last_state) begin
    $display("[dqs] t=%0t state=%0d idle=%0b dll_ready=%0b cs=%0b",
             $time, state, idle, dll_ready, ram_cs);
    last_state = state;
  end

  initial begin
    $display("[dqs] === reset ===");
    #500;
    rst = 1'b0; fast_rst = 1'b0;

    // The DDRDLL has to lock and the settle sequence has to finish before any
    // transaction is legal. The vendor model also needs tVCS.
    // Wait for tVCS first -- the part ignores everything for 150 us anyway, and
    // it gives the DDRDLL its settle time. `dll_ready === 1'b1` rather than a
    // truthiness test: an x reads as false and would fall straight through.
    #160_000;
    i = 0;
    while (dll_ready !== 1'b1 && i < 20000) begin @(posedge sync_clk); i = i + 1; end
    $display("[dqs] dll_ready=%0b after %0d further sync cycles", dll_ready, i);

    $display("[dqs] === write 0xdeadbeef to word address 0x100 ===");
    do_write(32'h0000_0100, 32'hdead_beef);

    $display("[dqs] === read word address 0x100 ===");
    do_read(32'h0000_0100);
    $display("[dqs] read_data = %h (captured on read_ready: %h)", read_data, captured);

    #2000;
    $display("[dqs] === done ===");
    $finish;
  end

  // Watchdog: this design can hang, and #186 exists partly because it does.
  initial begin
    #2_000_000;
    $display("[dqs] GLOBAL TIMEOUT at %0t, state=%0d idle=%0b", $time, state, idle);
    $finish;
  end

endmodule
