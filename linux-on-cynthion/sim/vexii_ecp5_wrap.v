`timescale 1ns/1ps

module VexiiRiscvWrap(
  input wire clk,
  input wire reset
);
  wire fetch_cmd_valid;
  wire fetch_cmd_ready;
  wire [0:0] fetch_cmd_id;
  wire [31:0] fetch_cmd_address;
  wire fetch_rsp_valid;
  wire [0:0] fetch_rsp_id;
  wire fetch_rsp_error;
  wire [31:0] fetch_rsp_word;

  wire lsu_cmd_valid;
  wire lsu_cmd_ready;
  wire [0:0] lsu_cmd_id;
  wire lsu_cmd_write;
  wire [31:0] lsu_cmd_address;
  wire [31:0] lsu_cmd_data;
  wire [1:0] lsu_cmd_size;
  wire [3:0] lsu_cmd_mask;
  wire lsu_cmd_io;
  wire lsu_cmd_fromHart;
  wire [15:0] lsu_cmd_uopId;
  wire lsu_rsp_valid;
  wire [0:0] lsu_rsp_id;
  wire lsu_rsp_error;
  wire [31:0] lsu_rsp_data;

  reg [63:0] rdtime = 64'd0;
  reg int_m_timer = 1'b0;
  reg int_m_software = 1'b0;
  reg int_m_external = 1'b0;

  always @(posedge clk) begin
    rdtime <= rdtime + 1'b1;
  end

  assign fetch_cmd_ready = 1'b1;
  assign fetch_rsp_valid = fetch_cmd_valid;
  assign fetch_rsp_id = fetch_cmd_id;
  assign fetch_rsp_error = 1'b0;
  assign fetch_rsp_word = 32'h00000013;

  assign lsu_cmd_ready = 1'b1;
  assign lsu_rsp_valid = lsu_cmd_valid;
  assign lsu_rsp_id = lsu_cmd_id;
  assign lsu_rsp_error = 1'b0;
  assign lsu_rsp_data = 32'h00000000;

  VexiiRiscv core (
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

endmodule
