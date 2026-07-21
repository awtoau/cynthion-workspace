`timescale 1ns/1ps

module tb_vexiiriscv_smoke;
  reg clk = 1'b0;
  reg reset = 1'b1;

  reg [63:0] rdtime = 64'd0;
  reg int_m_timer = 1'b0;
  reg int_m_software = 1'b0;
  reg int_m_external = 1'b0;

  wire fetch_cmd_valid;
  reg  fetch_cmd_ready = 1'b1;
  wire [0:0] fetch_cmd_id;
  wire [31:0] fetch_cmd_address;

  reg  fetch_rsp_valid = 1'b0;
  reg  [0:0] fetch_rsp_id = 1'b0;
  reg  fetch_rsp_error = 1'b0;
  reg  [31:0] fetch_rsp_word = 32'h00000013; // NOP

  wire lsu_cmd_valid;
  reg  lsu_cmd_ready = 1'b1;
  wire [0:0] lsu_cmd_id;
  wire lsu_cmd_write;
  wire [31:0] lsu_cmd_address;
  wire [31:0] lsu_cmd_data;
  wire [1:0] lsu_cmd_size;
  wire [3:0] lsu_cmd_mask;
  wire lsu_cmd_io;
  wire lsu_cmd_fromHart;
  wire [15:0] lsu_cmd_uopId;

  reg  lsu_rsp_valid = 1'b0;
  reg  [0:0] lsu_rsp_id = 1'b0;
  reg  lsu_rsp_error = 1'b0;
  reg  [31:0] lsu_rsp_data = 32'd0;

  reg [31:0] boot_base = 32'd0;
  reg boot_base_set = 1'b0;

  integer cycles = 0;
  integer fetch_count = 0;
  integer lsu_count = 0;
  integer loop_sig_hits = 0;

  function [31:0] rom_word;
    input [31:0] addr;
    reg [31:0] idx;
    begin
      idx = (addr - boot_base) >> 2;
      case (idx)
        // sw x0, 0(x0)     (deterministic signature write)
        0: rom_word = 32'h00002023;
        // jal x0, 0        (loop forever)
        1: rom_word = 32'h0000006f;
        default: rom_word = 32'h00000013; // NOP
      endcase
    end
  endfunction

  always #5 clk = ~clk;

  VexiiRiscv dut (
    .PrivilegedPlugin_logic_rdtime(rdtime),
    .PrivilegedPlugin_logic_harts_0_int_m_timer(int_m_timer),
    .PrivilegedPlugin_logic_harts_0_int_m_software(int_m_software),
    .PrivilegedPlugin_logic_harts_0_int_m_external(int_m_external),
    .FetchCachelessPlugin_logic_bus_cmd_valid(fetch_cmd_valid),
    .FetchCachelessPlugin_logic_bus_cmd_ready(fetch_cmd_ready),
    .FetchCachelessPlugin_logic_bus_cmd_payload_id(fetch_cmd_id),
    .FetchCachelessPlugin_logic_bus_cmd_payload_address(fetch_cmd_address),
    .FetchCachelessPlugin_logic_bus_rsp_valid(fetch_rsp_valid),
    .FetchCachelessPlugin_logic_bus_rsp_payload_id(fetch_rsp_id),
    .FetchCachelessPlugin_logic_bus_rsp_payload_error(fetch_rsp_error),
    .FetchCachelessPlugin_logic_bus_rsp_payload_word(fetch_rsp_word),
    .LsuCachelessPlugin_logic_bus_cmd_valid(lsu_cmd_valid),
    .LsuCachelessPlugin_logic_bus_cmd_ready(lsu_cmd_ready),
    .LsuCachelessPlugin_logic_bus_cmd_payload_id(lsu_cmd_id),
    .LsuCachelessPlugin_logic_bus_cmd_payload_write(lsu_cmd_write),
    .LsuCachelessPlugin_logic_bus_cmd_payload_address(lsu_cmd_address),
    .LsuCachelessPlugin_logic_bus_cmd_payload_data(lsu_cmd_data),
    .LsuCachelessPlugin_logic_bus_cmd_payload_size(lsu_cmd_size),
    .LsuCachelessPlugin_logic_bus_cmd_payload_mask(lsu_cmd_mask),
    .LsuCachelessPlugin_logic_bus_cmd_payload_io(lsu_cmd_io),
    .LsuCachelessPlugin_logic_bus_cmd_payload_fromHart(lsu_cmd_fromHart),
    .LsuCachelessPlugin_logic_bus_cmd_payload_uopId(lsu_cmd_uopId),
    .LsuCachelessPlugin_logic_bus_rsp_valid(lsu_rsp_valid),
    .LsuCachelessPlugin_logic_bus_rsp_payload_id(lsu_rsp_id),
    .LsuCachelessPlugin_logic_bus_rsp_payload_error(lsu_rsp_error),
    .LsuCachelessPlugin_logic_bus_rsp_payload_data(lsu_rsp_data),
    .clk(clk),
    .reset(reset)
  );

  initial begin
    repeat (20) @(posedge clk);
    reset <= 1'b0;

    while (cycles < 600) begin
      @(posedge clk);
      cycles <= cycles + 1;
      rdtime <= rdtime + 1;

      fetch_rsp_valid <= 1'b0;
      lsu_rsp_valid <= 1'b0;

      if (fetch_cmd_valid && fetch_cmd_ready) begin
        if (!boot_base_set) begin
          boot_base <= fetch_cmd_address & 32'hfffffff0;
          boot_base_set <= 1'b1;
        end

        fetch_count <= fetch_count + 1;
        fetch_rsp_valid <= 1'b1;
        fetch_rsp_id <= fetch_cmd_id;
        fetch_rsp_error <= 1'b0;
        fetch_rsp_word <= rom_word(fetch_cmd_address);

        if (boot_base_set && (fetch_cmd_address == (boot_base + 32'h00000004))) begin
          loop_sig_hits <= loop_sig_hits + 1;
          if (loop_sig_hits > 20) begin
            $display("SMOKE_RESULT cycles=%0d fetch_count=%0d lsu_count=%0d loop_sig_hits=%0d", cycles, fetch_count, lsu_count, loop_sig_hits);
            $display("PASS: Fetch-loop signature observed at boot_base+4");
            $finish(0);
          end
        end
      end

      if (lsu_cmd_valid && lsu_cmd_ready) begin
        lsu_count <= lsu_count + 1;
        lsu_rsp_valid <= 1'b1;
        lsu_rsp_id <= lsu_cmd_id;
        lsu_rsp_error <= 1'b0;
        lsu_rsp_data <= 32'h00000000;

        // LSU response remains modeled to keep the interface complete.
      end
    end

    $display("SMOKE_RESULT cycles=%0d fetch_count=%0d lsu_count=%0d loop_sig_hits=%0d", cycles, fetch_count, lsu_count, loop_sig_hits);
    $display("FAIL: Fetch-loop signature not observed before timeout.");
    $finish(1);
  end
endmodule
