// Generator : SpinalHDL dev    git head : 1097c1df52f6b4ac532e42ff35a9c610f8e9a07c
// Component : VexiiRiscv
// Git hash  : f8774d4a4cd90c3fd1894f3b25f08fb1ffa4048b

`timescale 1ns/1ps

module VexiiRiscv (
  input  wire [63:0]   PrivilegedPlugin_logic_rdtime,
  input  wire          PrivilegedPlugin_logic_harts_0_int_m_timer /* verilator public */ ,
  input  wire          PrivilegedPlugin_logic_harts_0_int_m_software /* verilator public */ ,
  input  wire          PrivilegedPlugin_logic_harts_0_int_m_external /* verilator public */ ,
  output wire          FetchCachelessPlugin_logic_bus_cmd_valid,
  input  wire          FetchCachelessPlugin_logic_bus_cmd_ready,
  output wire [0:0]    FetchCachelessPlugin_logic_bus_cmd_payload_id,
  output wire [31:0]   FetchCachelessPlugin_logic_bus_cmd_payload_address,
  input  wire          FetchCachelessPlugin_logic_bus_rsp_valid,
  input  wire [0:0]    FetchCachelessPlugin_logic_bus_rsp_payload_id,
  input  wire          FetchCachelessPlugin_logic_bus_rsp_payload_error,
  input  wire [31:0]   FetchCachelessPlugin_logic_bus_rsp_payload_word,
  output wire          LsuCachelessPlugin_logic_bus_cmd_valid,
  input  wire          LsuCachelessPlugin_logic_bus_cmd_ready,
  output wire [0:0]    LsuCachelessPlugin_logic_bus_cmd_payload_id,
  output wire          LsuCachelessPlugin_logic_bus_cmd_payload_write,
  output wire [31:0]   LsuCachelessPlugin_logic_bus_cmd_payload_address,
  output wire [31:0]   LsuCachelessPlugin_logic_bus_cmd_payload_data,
  output wire [1:0]    LsuCachelessPlugin_logic_bus_cmd_payload_size,
  output wire [3:0]    LsuCachelessPlugin_logic_bus_cmd_payload_mask,
  output wire          LsuCachelessPlugin_logic_bus_cmd_payload_amoEnable,
  output wire [4:0]    LsuCachelessPlugin_logic_bus_cmd_payload_amoOp,
  output wire          LsuCachelessPlugin_logic_bus_cmd_payload_io,
  output wire          LsuCachelessPlugin_logic_bus_cmd_payload_fromHart,
  output wire [15:0]   LsuCachelessPlugin_logic_bus_cmd_payload_uopId,
  input  wire          LsuCachelessPlugin_logic_bus_rsp_valid,
  input  wire [0:0]    LsuCachelessPlugin_logic_bus_rsp_payload_id,
  input  wire          LsuCachelessPlugin_logic_bus_rsp_payload_error,
  input  wire [31:0]   LsuCachelessPlugin_logic_bus_rsp_payload_data,
  input  wire          LsuCachelessPlugin_logic_bus_rsp_payload_scMiss,
  input  wire          clk,
  input  wire          reset
);
  localparam EnvPluginOp_ECALL = 3'd0;
  localparam EnvPluginOp_EBREAK = 3'd1;
  localparam EnvPluginOp_PRIV_RET = 3'd2;
  localparam EnvPluginOp_FENCE_I = 3'd3;
  localparam EnvPluginOp_SFENCE_VMA = 3'd4;
  localparam EnvPluginOp_WFI = 3'd5;
  localparam EnvPluginOp_HFENCE_GVMA = 3'd6;
  localparam EnvPluginOp_HFENCE_VVMA = 3'd7;
  localparam BranchPlugin_BranchCtrlEnum_B = 2'd0;
  localparam BranchPlugin_BranchCtrlEnum_JAL = 2'd1;
  localparam BranchPlugin_BranchCtrlEnum_JALR = 2'd2;
  localparam IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 = 2'd0;
  localparam IntAluPlugin_AluBitwiseCtrlEnum_OR_1 = 2'd1;
  localparam IntAluPlugin_AluBitwiseCtrlEnum_AND_1 = 2'd2;
  localparam IntAluPlugin_AluBitwiseCtrlEnum_ZERO = 2'd3;
  localparam TrapPlugin_logic_harts_0_trap_fsm_RESET = 4'd0;
  localparam TrapPlugin_logic_harts_0_trap_fsm_RUNNING = 4'd1;
  localparam TrapPlugin_logic_harts_0_trap_fsm_COMPUTE = 4'd2;
  localparam TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC = 4'd3;
  localparam TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL = 4'd4;
  localparam TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL = 4'd5;
  localparam TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST = 4'd6;
  localparam TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC = 4'd7;
  localparam TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT = 4'd8;
  localparam TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY = 4'd9;
  localparam TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC = 4'd10;
  localparam TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY = 4'd11;
  localparam TrapPlugin_logic_harts_0_trap_fsm_JUMP = 4'd12;
  localparam CsrAccessPlugin_logic_fsm_IDLE = 2'd0;
  localparam CsrAccessPlugin_logic_fsm_READ = 2'd1;
  localparam CsrAccessPlugin_logic_fsm_WRITE = 2'd2;
  localparam CsrAccessPlugin_logic_fsm_COMPLETION = 2'd3;
  localparam PerformanceCounterPlugin_logic_fsm_BOOT = 3'd0;
  localparam PerformanceCounterPlugin_logic_fsm_IDLE = 3'd1;
  localparam PerformanceCounterPlugin_logic_fsm_READ_LOW = 3'd2;
  localparam PerformanceCounterPlugin_logic_fsm_CALC_LOW = 3'd3;
  localparam PerformanceCounterPlugin_logic_fsm_READ_HIGH = 3'd4;
  localparam PerformanceCounterPlugin_logic_fsm_CALC_HIGH = 3'd5;
  localparam PerformanceCounterPlugin_logic_fsm_CSR_WRITE = 3'd6;

  wire                early0_DivPlugin_logic_processing_div_io_cmd_valid;
  reg                 integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_valid;
  reg        [4:0]    integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_address;
  reg        [31:0]   integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_data;
  wire       [32:0]   FetchCachelessPlugin_logic_buffer_words_spinal_port1;
  reg        [31:0]   CsrRamPlugin_logic_mem_spinal_port1;
  wire                early0_DivPlugin_logic_processing_div_io_cmd_ready;
  wire                early0_DivPlugin_logic_processing_div_io_rsp_valid;
  wire       [31:0]   early0_DivPlugin_logic_processing_div_io_rsp_payload_result;
  wire       [31:0]   early0_DivPlugin_logic_processing_div_io_rsp_payload_remain;
  wire                streamArbiter_1_io_inputs_0_ready;
  wire                streamArbiter_1_io_output_valid;
  wire       [31:0]   streamArbiter_1_io_output_payload_pcOnLastSlice;
  wire       [31:0]   streamArbiter_1_io_output_payload_pcTarget;
  wire                streamArbiter_1_io_output_payload_taken;
  wire                streamArbiter_1_io_output_payload_isBranch;
  wire                streamArbiter_1_io_output_payload_isPush;
  wire                streamArbiter_1_io_output_payload_isPop;
  wire                streamArbiter_1_io_output_payload_wasWrong;
  wire                streamArbiter_1_io_output_payload_badPredictedTarget;
  wire       [15:0]   streamArbiter_1_io_output_payload_uopId;
  wire       [0:0]    streamArbiter_1_io_chosenOH;
  wire       [31:0]   integer_RegFilePlugin_logic_regfile_fpga_io_reads_0_data;
  wire       [31:0]   integer_RegFilePlugin_logic_regfile_fpga_io_reads_1_data;
  wire       [31:0]   _zz_early0_IntAluPlugin_logic_alu_result;
  wire       [31:0]   _zz_early0_IntAluPlugin_logic_alu_result_1;
  wire       [31:0]   _zz_early0_IntAluPlugin_logic_alu_result_2;
  wire       [31:0]   _zz_early0_IntAluPlugin_logic_alu_result_3;
  wire       [31:0]   _zz_early0_IntAluPlugin_logic_alu_result_4;
  wire       [0:0]    _zz_early0_IntAluPlugin_logic_alu_result_5;
  wire       [4:0]    _zz_early0_BarrelShifterPlugin_logic_shift_amplitude;
  wire       [31:0]   _zz_early0_BarrelShifterPlugin_logic_shift_reversed;
  wire                _zz_early0_BarrelShifterPlugin_logic_shift_reversed_1;
  wire       [0:0]    _zz_early0_BarrelShifterPlugin_logic_shift_reversed_2;
  wire       [20:0]   _zz_early0_BarrelShifterPlugin_logic_shift_reversed_3;
  wire                _zz_early0_BarrelShifterPlugin_logic_shift_reversed_4;
  wire       [0:0]    _zz_early0_BarrelShifterPlugin_logic_shift_reversed_5;
  wire       [9:0]    _zz_early0_BarrelShifterPlugin_logic_shift_reversed_6;
  wire       [32:0]   _zz_early0_BarrelShifterPlugin_logic_shift_shifted;
  wire       [32:0]   _zz_early0_BarrelShifterPlugin_logic_shift_shifted_1;
  wire       [31:0]   _zz_early0_BarrelShifterPlugin_logic_shift_patched;
  wire                _zz_early0_BarrelShifterPlugin_logic_shift_patched_1;
  wire       [0:0]    _zz_early0_BarrelShifterPlugin_logic_shift_patched_2;
  wire       [20:0]   _zz_early0_BarrelShifterPlugin_logic_shift_patched_3;
  wire                _zz_early0_BarrelShifterPlugin_logic_shift_patched_4;
  wire       [0:0]    _zz_early0_BarrelShifterPlugin_logic_shift_patched_5;
  wire       [9:0]    _zz_early0_BarrelShifterPlugin_logic_shift_patched_6;
  wire       [32:0]   _zz_execute_ctrl2_down_MUL_SRC1_lane0;
  wire       [32:0]   _zz_execute_ctrl2_down_MUL_SRC2_lane0;
  wire       [46:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  wire       [33:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_1;
  wire       [17:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_2;
  wire       [15:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_3;
  wire       [46:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  wire       [33:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_1;
  wire       [15:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_2;
  wire       [17:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_3;
  wire       [29:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  wire       [31:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_1;
  wire       [15:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_2;
  wire       [15:0]   _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_3;
  wire       [62:0]   _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_3;
  wire       [62:0]   _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_4;
  wire       [62:0]   _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_5;
  wire       [62:0]   _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_6;
  wire       [4:0]    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_3;
  wire       [4:0]    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_4;
  wire       [4:0]    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_5;
  wire       [4:0]    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_6;
  wire       [31:0]   _zz_execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0;
  wire       [0:0]    _zz_execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0_1;
  wire       [31:0]   _zz_execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0;
  wire       [0:0]    _zz_execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0_1;
  wire       [31:0]   _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_1;
  wire       [31:0]   _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_2;
  wire       [0:0]    _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_3;
  wire       [32:0]   _zz_FetchCachelessPlugin_logic_buffer_words_port;
  reg                 _zz_FetchCachelessPlugin_logic_buffer_full;
  wire       [63:0]   _zz_WhiteboxerPlugin_logic_decodes_0_pc;
  wire       [1:0]    _zz_PrivilegedPlugin_logic_defaultTrap_adjustPrivilege;
  wire       [11:0]   _zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0;
  wire       [11:0]   _zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0_1;
  wire       [31:0]   _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0;
  wire       [31:0]   _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_1;
  wire       [31:0]   _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_2;
  wire       [0:0]    _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_3;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_commitCount;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_commitCount_1;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_eventInstructions_0;
  wire       [7:0]    _zz_PerformanceCounterPlugin_logic_counters_cycle_value;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_counters_cycle_value_1;
  wire       [7:0]    _zz_PerformanceCounterPlugin_logic_counters_instret_value_1;
  wire       [20:0]   _zz_early0_BranchPlugin_pcCalc_target_b;
  wire       [11:0]   _zz_early0_BranchPlugin_pcCalc_target_b_1;
  wire       [12:0]   _zz_early0_BranchPlugin_pcCalc_target_b_2;
  wire       [31:0]   _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0;
  wire       [31:0]   _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0;
  wire       [2:0]    _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0_1;
  wire       [31:0]   _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0;
  wire       [1:0]    _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0_1;
  reg        [1:0]    _zz_fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK;
  wire       [0:0]    _zz_fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK_1;
  wire       [0:0]    _zz_AlignerPlugin_logic_extractors_0_redo_4;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_23;
  wire       [0:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_24;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_25;
  wire       [31:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_26;
  wire       [11:0]   _zz__zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22;
  wire       [5:0]    _zz__zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22_1;
  reg        [31:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_27;
  wire       [1:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_28;
  reg        [2:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29;
  wire       [2:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_30;
  wire       [6:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_31;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_32;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_33;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_34;
  wire       [11:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_35;
  wire       [11:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_36;
  wire       [1:0]    _zz__zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_5;
  wire       [29:0]   _zz_LsuCachelessPlugin_logic_trapPort_payload_tval2;
  wire       [31:0]   _zz_LsuCachelessPlugin_logic_trapPort_payload_tval2_1;
  wire       [0:0]    _zz_early0_EnvPlugin_logic_exe_xretPriv_1;
  wire       [4:0]    _zz_early0_EnvPlugin_logic_trapPort_payload_code;
  wire       [3:0]    _zz_early0_EnvPlugin_logic_trapPort_payload_code_1;
  wire       [4:0]    _zz_early0_EnvPlugin_logic_trapPort_payload_code_2;
  wire       [0:0]    _zz_decode_ctrls_1_down_RS1_ENABLE_0;
  wire       [4:0]    _zz_decode_ctrls_1_down_RS1_PHYS_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_RS2_ENABLE_0;
  wire       [4:0]    _zz_decode_ctrls_1_down_RS2_PHYS_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_RD_ENABLE_0;
  wire       [31:0]   _zz_decode_ctrls_1_down_RD_ENABLE_0_1;
  wire       [31:0]   _zz_decode_ctrls_1_down_RD_ENABLE_0_2;
  wire       [31:0]   _zz_decode_ctrls_1_down_RD_ENABLE_0_3;
  wire                _zz_decode_ctrls_1_down_RD_ENABLE_0_4;
  wire       [0:0]    _zz_decode_ctrls_1_down_RD_ENABLE_0_5;
  wire       [0:0]    _zz_decode_ctrls_1_down_RD_ENABLE_0_6;
  wire       [4:0]    _zz_decode_ctrls_1_down_RD_PHYS_0;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_1;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_2;
  wire                _zz_decode_ctrls_1_down_Decode_LEGAL_0_3;
  wire       [0:0]    _zz_decode_ctrls_1_down_Decode_LEGAL_0_4;
  wire       [16:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_5;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_6;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_7;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_8;
  wire                _zz_decode_ctrls_1_down_Decode_LEGAL_0_9;
  wire       [0:0]    _zz_decode_ctrls_1_down_Decode_LEGAL_0_10;
  wire       [10:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_11;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_12;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_13;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_14;
  wire                _zz_decode_ctrls_1_down_Decode_LEGAL_0_15;
  wire       [0:0]    _zz_decode_ctrls_1_down_Decode_LEGAL_0_16;
  wire       [4:0]    _zz_decode_ctrls_1_down_Decode_LEGAL_0_17;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_18;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_19;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_20;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_21;
  wire       [31:0]   _zz_decode_ctrls_1_down_Decode_LEGAL_0_22;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_1;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_2;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_3;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_4;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_5;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_1;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_2;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_3;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_4;
  wire                _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_5;
  wire       [2:0]    _zz_LsuCachelessPlugin_logic_trapPort_payload_code;
  reg                 _zz_LsuCachelessPlugin_logic_onJoin_readerValid;
  reg                 _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_error;
  reg        [31:0]   _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_data;
  reg                 _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_scMiss;
  reg        [31:0]   _zz_LsuCachelessPlugin_logic_onJoin_rspAddress;
  reg        [7:0]    _zz_LsuCachelessPlugin_logic_onWb_rspShifted;
  wire       [1:0]    _zz_LsuCachelessPlugin_logic_onWb_rspShifted_1;
  wire       [1:0]    _zz_LsuCachelessPlugin_logic_onWb_rspShifted_2;
  reg        [7:0]    _zz_LsuCachelessPlugin_logic_onWb_rspShifted_3;
  wire       [0:0]    _zz_LsuCachelessPlugin_logic_onWb_rspShifted_4;
  wire       [0:0]    _zz_LsuCachelessPlugin_logic_onWb_rspShifted_5;
  wire       [0:0]    _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0_2;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_2;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_3;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0_1;
  wire       [0:0]    _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0_2;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_0_1;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_0_2;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_1_1;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_1_2;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_2_1;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_2_2;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_3_1;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_3_2;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_4_1;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_4_2;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_5_1;
  wire       [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_5_2;
  wire       [6:0]    _zz_PerformanceCounterPlugin_logic_fsm_calc_a;
  wire       [7:0]    _zz_PerformanceCounterPlugin_logic_fsm_calc_b;
  wire       [32:0]   _zz_PerformanceCounterPlugin_logic_fsm_calc_sum;
  wire       [8:0]    _zz_PerformanceCounterPlugin_logic_fsm_calc_sum_1;
  wire       [1:0]    _zz_PerformanceCounterPlugin_logic_flusher_hits_ohFirst_masked;
  wire       [1:0]    _zz_PerformanceCounterPlugin_logic_csrDecode_hyperOK;
  wire       [0:0]    _zz_LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit_1;
  wire       [0:0]    _zz_LsuCachelessPlugin_pmaBuilder_loadExecute_fault;
  wire       [0:0]    _zz_LsuCachelessPlugin_logic_onPma_port_rsp_io;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_1;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_2;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_3;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_4;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_5;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_6;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_7;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress_8;
  wire                _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception;
  wire                _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1;
  wire                _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_2;
  wire                _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_3;
  wire                _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_4;
  wire                _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_5;
  wire       [32:0]   _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_1;
  wire       [32:0]   _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_2;
  wire       [0:0]    _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_3;
  wire       [32:0]   _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception;
  wire       [32:0]   _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception_1;
  wire       [0:0]    _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception_2;
  wire       [1:0]    _zz_TrapPlugin_logic_harts_0_trap_pending_slices;
  wire       [31:0]   _zz_TrapPlugin_logic_harts_0_trap_fsm_jumpTarget;
  wire       [2:0]    _zz_TrapPlugin_logic_harts_0_trap_fsm_jumpTarget_1;
  wire       [4:0]    _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA;
  wire                _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_1;
  wire       [0:0]    _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_2;
  wire       [3:0]    _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_3;
  wire       [31:0]   _zz_PcPlugin_logic_harts_0_self_pc;
  wire       [2:0]    _zz_PcPlugin_logic_harts_0_self_pc_1;
  wire       [0:0]    _zz_PcPlugin_logic_harts_0_aggregator_fault;
  wire       [11:0]   _zz_CsrAccessPlugin_logic_fsm_inject_csrPriv;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1;
  wire                _zz_COMB_CSR_UNAMED_1_1;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_2;
  wire       [166:0]  _zz_COMB_CSR_UNAMED_1_3;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_4;
  wire                _zz_COMB_CSR_UNAMED_1_5;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_6;
  wire       [158:0]  _zz_COMB_CSR_UNAMED_1_7;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_8;
  wire                _zz_COMB_CSR_UNAMED_1_9;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_10;
  wire       [150:0]  _zz_COMB_CSR_UNAMED_1_11;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_12;
  wire                _zz_COMB_CSR_UNAMED_1_13;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_14;
  wire       [142:0]  _zz_COMB_CSR_UNAMED_1_15;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_16;
  wire                _zz_COMB_CSR_UNAMED_1_17;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_18;
  wire       [134:0]  _zz_COMB_CSR_UNAMED_1_19;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_20;
  wire                _zz_COMB_CSR_UNAMED_1_21;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_22;
  wire       [126:0]  _zz_COMB_CSR_UNAMED_1_23;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_24;
  wire                _zz_COMB_CSR_UNAMED_1_25;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_26;
  wire       [118:0]  _zz_COMB_CSR_UNAMED_1_27;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_28;
  wire                _zz_COMB_CSR_UNAMED_1_29;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_30;
  wire       [110:0]  _zz_COMB_CSR_UNAMED_1_31;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_32;
  wire                _zz_COMB_CSR_UNAMED_1_33;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_34;
  wire       [102:0]  _zz_COMB_CSR_UNAMED_1_35;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_36;
  wire                _zz_COMB_CSR_UNAMED_1_37;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_38;
  wire       [94:0]   _zz_COMB_CSR_UNAMED_1_39;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_40;
  wire                _zz_COMB_CSR_UNAMED_1_41;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_42;
  wire       [86:0]   _zz_COMB_CSR_UNAMED_1_43;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_44;
  wire                _zz_COMB_CSR_UNAMED_1_45;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_46;
  wire       [78:0]   _zz_COMB_CSR_UNAMED_1_47;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_48;
  wire                _zz_COMB_CSR_UNAMED_1_49;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_50;
  wire       [70:0]   _zz_COMB_CSR_UNAMED_1_51;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_52;
  wire                _zz_COMB_CSR_UNAMED_1_53;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_54;
  wire       [62:0]   _zz_COMB_CSR_UNAMED_1_55;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_56;
  wire                _zz_COMB_CSR_UNAMED_1_57;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_58;
  wire       [54:0]   _zz_COMB_CSR_UNAMED_1_59;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_60;
  wire                _zz_COMB_CSR_UNAMED_1_61;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_62;
  wire       [46:0]   _zz_COMB_CSR_UNAMED_1_63;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_64;
  wire                _zz_COMB_CSR_UNAMED_1_65;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_66;
  wire       [38:0]   _zz_COMB_CSR_UNAMED_1_67;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_68;
  wire                _zz_COMB_CSR_UNAMED_1_69;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_70;
  wire       [30:0]   _zz_COMB_CSR_UNAMED_1_71;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_72;
  wire                _zz_COMB_CSR_UNAMED_1_73;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_74;
  wire       [22:0]   _zz_COMB_CSR_UNAMED_1_75;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_76;
  wire                _zz_COMB_CSR_UNAMED_1_77;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_78;
  wire       [14:0]   _zz_COMB_CSR_UNAMED_1_79;
  wire       [11:0]   _zz_COMB_CSR_UNAMED_1_80;
  wire                _zz_COMB_CSR_UNAMED_1_81;
  wire       [0:0]    _zz_COMB_CSR_UNAMED_1_82;
  wire       [6:0]    _zz_COMB_CSR_UNAMED_1_83;
  wire       [11:0]   _zz_COMB_CSR_PerformanceCounterPlugin_logic_csrFilter;
  wire       [11:0]   _zz_COMB_CSR_PerformanceCounterPlugin_logic_csrFilter_1;
  wire       [11:0]   _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter;
  wire                _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_1;
  wire       [0:0]    _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_2;
  wire       [2:0]    _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_3;
  wire       [0:0]    _zz_CsrAccessPlugin_logic_fsm_inject_implemented;
  wire       [13:0]   _zz_CsrAccessPlugin_logic_fsm_inject_implemented_1;
  wire       [0:0]    _zz_CsrAccessPlugin_logic_fsm_inject_implemented_2;
  wire       [2:0]    _zz_CsrAccessPlugin_logic_fsm_inject_implemented_3;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_9;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_10;
  wire       [5:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_11;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_12;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_13;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_14;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_15;
  wire       [7:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_16;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_17;
  wire       [3:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_18;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_19;
  wire       [12:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_20;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_21;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_22;
  wire       [17:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_23;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_24;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_25;
  wire       [4:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_26;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_27;
  wire       [11:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_28;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_29;
  wire       [7:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_30;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_31;
  wire       [3:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_32;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_33;
  wire       [11:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_34;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_35;
  wire       [7:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_36;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_37;
  wire       [3:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_38;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_39;
  wire       [0:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_40;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_41;
  wire       [20:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_42;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_43;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_44;
  wire       [1:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_45;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_46;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_47;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_48;
  wire       [0:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_49;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_50;
  wire       [0:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_51;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_52;
  wire       [2:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_53;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_54;
  wire       [2:0]    _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_55;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_56;
  wire       [13:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_57;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_58;
  wire       [13:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_59;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_60;
  wire       [31:0]   _zz_CsrAccessPlugin_logic_fsm_writeLogic_alu_mask;
  wire       [4:0]    _zz_CsrAccessPlugin_logic_fsm_writeLogic_alu_mask_1;
  reg                 _zz_FetchCachelessPlugin_logic_join_haltIt;
  wire       [29:0]   _zz_FetchCachelessPlugin_logic_trapPort_payload_tval2;
  wire       [31:0]   _zz_FetchCachelessPlugin_logic_trapPort_payload_tval2_1;
  wire       [3:0]    _zz_CsrRamPlugin_logic_writeLogic_hits_ohFirst_masked;
  wire       [2:0]    _zz_CsrRamPlugin_logic_readLogic_hits_ohFirst_masked;
  reg        [2:0]    _zz_CsrRamPlugin_logic_readLogic_port_cmd_payload;
  wire       [3:0]    _zz_CsrRamPlugin_logic_flush_counter;
  wire       [0:0]    _zz_CsrRamPlugin_logic_flush_counter_1;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_BranchPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_BranchPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_MulPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_MulPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_DivPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_DivPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_EnvPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_EnvPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_SEL_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_1;
  wire       [31:0]   _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_2;
  wire       [31:0]   _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_3;
  wire       [31:0]   _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_4;
  wire                _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_5;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_6;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_7;
  wire       [0:0]    _zz_execute_ctrl1_down_COMPLETION_AT_2_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_COMPLETION_AT_2_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_COMPLETION_AT_4_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_COMPLETION_AT_4_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_COMPLETION_AT_3_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_COMPLETION_AT_3_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_6;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_7;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_3;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_4;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_4;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_5;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_SrcStageables_REVERT_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_SrcStageables_REVERT_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_SrcStageables_ZERO_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_SrcStageables_ZERO_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0_1;
  wire       [31:0]   _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  wire       [31:0]   _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0_1;
  wire       [31:0]   _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_1_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_1_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_2_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_2_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_3_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_3_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_4_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_BYPASSED_AT_4_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_SrcStageables_UNSIGNED_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_SrcStageables_UNSIGNED_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_MulPlugin_HIGH_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_MulPlugin_HIGH_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_DivPlugin_REM_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_DivPlugin_REM_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_LOAD_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_LOAD_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_STORE_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_STORE_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_EXECUTE_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_EXECUTE_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0_2;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_FLOAT_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_FLOAT_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_CLEAN_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_CLEAN_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_INVALIDATE_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_INVALIDATE_lane0_1;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_GUEST_lane0;
  wire       [0:0]    _zz_execute_ctrl1_down_AguPlugin_GUEST_lane0_1;
  wire                _zz_when_ExecuteLanePlugin_l306_2;
  wire                _zz_when_ExecuteLanePlugin_l306_2_1;
  wire                _zz_when_ExecuteLanePlugin_l306_2_2;
  wire                _zz_when_ExecuteLanePlugin_l306_2_3;
  wire                _zz_when_ExecuteLanePlugin_l306_2_4;
  wire                _zz_when_ExecuteLanePlugin_l306_2_5;
  wire                _zz_when_ExecuteLanePlugin_l306_2_6;
  wire       [31:0]   _zz_WhiteboxerPlugin_logic_csr_access_payload_address;
  wire       [0:0]    _zz_FetchCachelessPlugin_pmaBuilder_onTransfers_0_addressHit;
  wire       [0:0]    _zz_FetchCachelessPlugin_logic_onPma_port_rsp_io;
  reg        [0:0]    _zz_WhiteboxerPlugin_logic_perf_candidatesCount;
  wire       [0:0]    _zz_WhiteboxerPlugin_logic_perf_candidatesCount_1;
  reg        [0:0]    _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCount;
  wire       [0:0]    _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCount_1;
  wire       [59:0]   _zz__zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1;
  wire       [0:0]    _zz__zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1_1;
  wire       [59:0]   _zz__zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1;
  wire       [0:0]    _zz__zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1_1;
  wire       [59:0]   _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1;
  wire       [0:0]    _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1_1;
  wire       [59:0]   _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1;
  wire       [0:0]    _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1_1;
  wire       [59:0]   _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1;
  wire       [0:0]    _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1_1;
  wire       [59:0]   _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1;
  wire       [0:0]    _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1_1;
  wire       [32:0]   _zz_PerformanceCounterPlugin_logic_writePort_data;
  wire       [32:0]   _zz_PerformanceCounterPlugin_logic_writePort_data_1;
  wire       [31:0]   _zz_PerformanceCounterPlugin_logic_counters_cycle_value_2;
  wire       [31:0]   _zz_PerformanceCounterPlugin_logic_counters_instret_value_2;
  wire                decode_ctrls_0_up_isValid;
  wire                fetch_logic_ctrls_0_up_isReady;
  wire                execute_ctrl4_down_RD_ENABLE_lane0;
  reg        [31:0]   execute_ctrl5_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  reg                 execute_ctrl5_up_COMMIT_lane0;
  reg        [4:0]    execute_ctrl5_up_RD_PHYS_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  wire       [31:0]   execute_ctrl3_down_early0_SrcPlugin_ADD_SUB_lane0;
  wire                execute_ctrl3_down_AguPlugin_FLOAT_lane0;
  wire                execute_ctrl3_down_AguPlugin_ATOMIC_lane0;
  wire                execute_ctrl3_down_AguPlugin_EXECUTE_lane0;
  wire                execute_ctrl3_down_AguPlugin_LOAD_lane0;
  wire                execute_ctrl3_down_MulPlugin_HIGH_lane0;
  wire                execute_ctrl3_down_BYPASSED_AT_4_lane0;
  wire       [1:0]    execute_ctrl3_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  wire                execute_ctrl3_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  wire                execute_ctrl3_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  wire                execute_ctrl3_down_COMPLETION_AT_4_lane0;
  wire                execute_ctrl3_down_lane0_integer_WriteBackPlugin_SEL_lane0;
  wire                execute_ctrl3_down_early0_MulPlugin_SEL_lane0;
  wire       [1:0]    execute_ctrl3_down_AguPlugin_SIZE_lane0;
  wire       [4:0]    execute_ctrl3_down_RD_PHYS_lane0;
  wire                execute_ctrl3_down_RD_ENABLE_lane0;
  wire       [31:0]   execute_ctrl3_down_PC_lane0;
  wire       [0:0]    execute_ctrl3_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  wire       [31:0]   execute_ctrl3_down_Decode_UOP_lane0;
  reg        [31:0]   execute_ctrl4_up_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0;
  reg                 execute_ctrl4_up_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0;
  reg        [4:0]    execute_ctrl4_up_early0_MulPlugin_logic_steps_0_adders_1_lane0;
  reg        [62:0]   execute_ctrl4_up_early0_MulPlugin_logic_steps_0_adders_0_lane0;
  reg                 execute_ctrl4_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
  reg                 execute_ctrl4_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  reg        [31:0]   execute_ctrl4_up_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  reg        [29:0]   execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  reg        [46:0]   execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  reg        [46:0]   execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  reg        [33:0]   execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_0_lane0;
  reg        [31:0]   execute_ctrl4_up_early0_SrcPlugin_ADD_SUB_lane0;
  reg                 execute_ctrl4_up_COMMIT_lane0;
  reg                 execute_ctrl4_up_AguPlugin_FLOAT_lane0;
  reg                 execute_ctrl4_up_AguPlugin_ATOMIC_lane0;
  reg                 execute_ctrl4_up_AguPlugin_EXECUTE_lane0;
  reg                 execute_ctrl4_up_AguPlugin_LOAD_lane0;
  reg                 execute_ctrl4_up_MulPlugin_HIGH_lane0;
  reg                 execute_ctrl4_up_BYPASSED_AT_4_lane0;
  reg        [1:0]    execute_ctrl4_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  reg                 execute_ctrl4_up_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  reg                 execute_ctrl4_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  reg                 execute_ctrl4_up_COMPLETION_AT_4_lane0;
  reg                 execute_ctrl4_up_lane0_integer_WriteBackPlugin_SEL_lane0;
  reg                 execute_ctrl4_up_AguPlugin_SEL_lane0;
  reg                 execute_ctrl4_up_early0_MulPlugin_SEL_lane0;
  reg        [1:0]    execute_ctrl4_up_AguPlugin_SIZE_lane0;
  reg        [15:0]   execute_ctrl4_up_Decode_UOP_ID_lane0;
  reg                 execute_ctrl4_up_TRAP_lane0;
  reg        [31:0]   execute_ctrl4_up_PC_lane0;
  reg        [0:0]    execute_ctrl4_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  reg        [31:0]   execute_ctrl4_up_Decode_UOP_lane0;
  wire                execute_ctrl2_down_AguPlugin_FLOAT_lane0;
  wire                execute_ctrl2_down_MulPlugin_HIGH_lane0;
  wire                execute_ctrl2_down_BYPASSED_AT_4_lane0;
  wire                execute_ctrl2_down_BYPASSED_AT_3_lane0;
  wire       [1:0]    execute_ctrl2_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  wire                execute_ctrl2_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  wire                execute_ctrl2_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  wire                execute_ctrl2_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  wire                execute_ctrl2_down_COMPLETION_AT_3_lane0;
  wire                execute_ctrl2_down_COMPLETION_AT_4_lane0;
  wire                execute_ctrl2_down_lane0_integer_WriteBackPlugin_SEL_lane0;
  wire                execute_ctrl2_down_early0_MulPlugin_SEL_lane0;
  reg                 execute_ctrl3_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
  reg                 execute_ctrl3_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  reg        [31:0]   execute_ctrl3_up_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  reg        [31:0]   execute_ctrl3_up_DivPlugin_DIV_RESULT_lane0;
  reg        [29:0]   execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  reg        [46:0]   execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  reg        [46:0]   execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  reg        [33:0]   execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_0_lane0;
  reg        [31:0]   execute_ctrl3_up_early0_SrcPlugin_ADD_SUB_lane0;
  reg                 execute_ctrl3_up_COMMIT_lane0;
  reg                 execute_ctrl3_up_AguPlugin_FLOAT_lane0;
  reg                 execute_ctrl3_up_AguPlugin_ATOMIC_lane0;
  reg                 execute_ctrl3_up_AguPlugin_EXECUTE_lane0;
  reg                 execute_ctrl3_up_AguPlugin_STORE_lane0;
  reg                 execute_ctrl3_up_AguPlugin_LOAD_lane0;
  reg                 execute_ctrl3_up_MulPlugin_HIGH_lane0;
  reg                 execute_ctrl3_up_BYPASSED_AT_4_lane0;
  reg                 execute_ctrl3_up_BYPASSED_AT_3_lane0;
  reg        [1:0]    execute_ctrl3_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  reg                 execute_ctrl3_up_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  reg                 execute_ctrl3_up_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  reg                 execute_ctrl3_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  reg                 execute_ctrl3_up_COMPLETION_AT_3_lane0;
  reg                 execute_ctrl3_up_COMPLETION_AT_4_lane0;
  reg                 execute_ctrl3_up_lane0_integer_WriteBackPlugin_SEL_lane0;
  reg                 execute_ctrl3_up_AguPlugin_SEL_lane0;
  reg                 execute_ctrl3_up_CsrAccessPlugin_SEL_lane0;
  reg                 execute_ctrl3_up_early0_DivPlugin_SEL_lane0;
  reg                 execute_ctrl3_up_early0_MulPlugin_SEL_lane0;
  reg        [1:0]    execute_ctrl3_up_AguPlugin_SIZE_lane0;
  reg        [15:0]   execute_ctrl3_up_Decode_UOP_ID_lane0;
  reg        [31:0]   execute_ctrl3_up_PC_lane0;
  reg        [0:0]    execute_ctrl3_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  reg        [31:0]   execute_ctrl3_up_Decode_UOP_lane0;
  wire       [1:0]    execute_ctrl1_down_AguPlugin_SIZE_lane0;
  wire                execute_ctrl1_down_COMPLETED_lane0;
  wire       [4:0]    execute_ctrl1_down_RD_PHYS_lane0;
  wire       [15:0]   execute_ctrl1_down_Decode_UOP_ID_lane0;
  wire       [0:0]    execute_ctrl1_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  wire                execute_ctrl1_down_isReady;
  reg        [2:0]    execute_ctrl2_up_early0_EnvPlugin_OP_lane0;
  reg                 execute_ctrl2_up_AguPlugin_GUEST_lane0;
  reg                 execute_ctrl2_up_AguPlugin_FLOAT_lane0;
  reg                 execute_ctrl2_up_AguPlugin_ATOMIC_lane0;
  reg                 execute_ctrl2_up_AguPlugin_EXECUTE_lane0;
  reg                 execute_ctrl2_up_AguPlugin_STORE_lane0;
  reg                 execute_ctrl2_up_AguPlugin_LOAD_lane0;
  reg                 execute_ctrl2_up_CsrAccessPlugin_CSR_CLEAR_lane0;
  reg                 execute_ctrl2_up_CsrAccessPlugin_CSR_MASK_lane0;
  reg                 execute_ctrl2_up_CsrAccessPlugin_CSR_IMM_lane0;
  reg                 execute_ctrl2_up_DivPlugin_REM_lane0;
  reg                 execute_ctrl2_up_RsUnsignedPlugin_RS2_SIGNED_lane0;
  reg                 execute_ctrl2_up_RsUnsignedPlugin_RS1_SIGNED_lane0;
  reg                 execute_ctrl2_up_MulPlugin_HIGH_lane0;
  reg        [1:0]    execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0;
  reg                 execute_ctrl2_up_BarrelShifterPlugin_SIGNED_lane0;
  reg                 execute_ctrl2_up_BarrelShifterPlugin_LEFT_lane0;
  reg                 execute_ctrl2_up_SrcStageables_UNSIGNED_lane0;
  reg                 execute_ctrl2_up_BYPASSED_AT_4_lane0;
  reg                 execute_ctrl2_up_BYPASSED_AT_3_lane0;
  reg                 execute_ctrl2_up_BYPASSED_AT_2_lane0;
  reg        [1:0]    execute_ctrl2_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  reg                 execute_ctrl2_up_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  reg                 execute_ctrl2_up_SrcStageables_ZERO_lane0;
  reg                 execute_ctrl2_up_SrcStageables_REVERT_lane0;
  reg        [1:0]    execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0;
  reg                 execute_ctrl2_up_early0_IntAluPlugin_ALU_SLTX_lane0;
  reg                 execute_ctrl2_up_early0_IntAluPlugin_ALU_ADD_SUB_lane0;
  reg                 execute_ctrl2_up_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  reg                 execute_ctrl2_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  reg                 execute_ctrl2_up_lane0_logic_completions_onCtrl_0_ENABLE_lane0;
  reg                 execute_ctrl2_up_COMPLETION_AT_3_lane0;
  reg                 execute_ctrl2_up_COMPLETION_AT_4_lane0;
  reg                 execute_ctrl2_up_COMPLETION_AT_2_lane0;
  reg                 execute_ctrl2_up_lane0_integer_WriteBackPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_LsuCachelessPlugin_FENCE_lane0;
  reg                 execute_ctrl2_up_AguPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_CsrAccessPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_early0_EnvPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_early0_DivPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_early0_MulPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_early0_BranchPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_early0_BarrelShifterPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_early0_IntAluPlugin_SEL_lane0;
  reg        [31:0]   execute_ctrl2_up_early0_SrcPlugin_SRC2_lane0;
  reg        [31:0]   execute_ctrl2_up_early0_SrcPlugin_SRC1_lane0;
  reg        [15:0]   execute_ctrl2_up_Decode_UOP_ID_lane0;
  reg        [31:0]   execute_ctrl2_up_PC_lane0;
  reg        [0:0]    execute_ctrl2_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  reg        [31:0]   execute_ctrl2_up_Decode_UOP_lane0;
  wire                execute_ctrl0_down_COMPLETED_lane0;
  wire       [4:0]    execute_ctrl0_down_RD_PHYS_lane0;
  wire                execute_ctrl0_down_TRAP_lane0;
  wire       [31:0]   execute_ctrl0_down_PC_lane0;
  wire       [0:0]    execute_ctrl0_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  reg        [1:0]    execute_ctrl1_up_AguPlugin_SIZE_lane0;
  reg                 execute_ctrl1_up_COMPLETED_lane0;
  reg        [4:0]    execute_ctrl1_up_RS2_PHYS_lane0;
  reg        [4:0]    execute_ctrl1_up_RS1_PHYS_lane0;
  reg        [15:0]   execute_ctrl1_up_Decode_UOP_ID_lane0;
  reg                 execute_ctrl1_up_TRAP_lane0;
  reg        [31:0]   execute_ctrl1_up_PC_lane0;
  reg        [0:0]    execute_ctrl1_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  reg        [31:0]   execute_ctrl1_up_Decode_UOP_lane0;
  wire                decode_ctrls_1_down_isReady;
  wire       [0:0]    decode_ctrls_0_down_Decode_INSTRUCTION_SLICE_COUNT_0;
  wire       [31:0]   decode_ctrls_0_down_Decode_INSTRUCTION_RAW_0;
  wire                decode_ctrls_0_down_Decode_DECOMPRESSION_FAULT_0;
  wire       [31:0]   decode_ctrls_0_down_Decode_INSTRUCTION_0;
  wire                decode_ctrls_0_down_isValid;
  wire                decode_ctrls_0_down_isReady;
  reg        [9:0]    decode_ctrls_1_up_Decode_DOP_ID_0;
  reg        [31:0]   decode_ctrls_1_up_PC_0;
  reg        [0:0]    decode_ctrls_1_up_Decode_INSTRUCTION_SLICE_COUNT_0;
  reg        [31:0]   decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0;
  reg                 decode_ctrls_1_up_Decode_DECOMPRESSION_FAULT_0;
  reg        [31:0]   decode_ctrls_1_up_Decode_INSTRUCTION_0;
  wire                fetch_logic_ctrls_0_down_isValid;
  wire                fetch_logic_ctrls_0_down_isReady;
  reg                 fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT;
  reg                 fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT;
  reg                 fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL;
  reg                 fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD;
  reg                 fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_fork_PMA_FAULT;
  reg        [0:0]    fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_BUFFER_ID;
  reg        [31:0]   fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
  reg        [9:0]    fetch_logic_ctrls_1_up_Fetch_ID;
  reg        [31:0]   fetch_logic_ctrls_1_up_Fetch_WORD_PC;
  wire                decode_ctrls_1_down_valid;
  reg                 fetch_logic_ctrls_1_up_valid;
  wire                decode_ctrls_0_down_valid;
  reg                 fetch_logic_ctrls_0_down_valid;
  wire                execute_ctrl0_up_ready;
  wire                execute_ctrl0_down_ready;
  wire                execute_ctrl1_up_ready;
  wire                execute_ctrl1_down_ready;
  wire                execute_ctrl2_up_ready;
  wire                execute_ctrl2_down_ready;
  wire                execute_ctrl3_up_ready;
  wire                execute_ctrl3_down_ready;
  wire                execute_ctrl4_up_ready;
  wire                decode_ctrls_0_up_ready;
  wire                execute_ctrl4_down_ready;
  reg                 decode_ctrls_0_down_ready;
  wire                fetch_logic_ctrls_0_down_ready;
  wire                execute_ctrl5_up_ready;
  reg                 fetch_logic_ctrls_1_up_ready;
  wire                fetch_logic_ctrls_1_up_cancel;
  wire                execute_ctrl5_down_ready;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_pmpPort_logic_NEED_HIT_lane0;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_pmpPort_logic_NEED_HIT;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_BYPASS_TRANSLATION_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_ADDRESS_EXTENSION_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_ACCESS_FAULT_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_PAGE_FAULT_lane0;
  wire       [31:0]   execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_TRANSLATED_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_HAZARD_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_REFILL_lane0;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_BYPASS_TRANSLATION;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_ADDRESS_EXTENSION;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_ACCESS_FAULT;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_PAGE_FAULT;
  wire       [31:0]   fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_TRANSLATED;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_HAZARD;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_REFILL;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_BYPASS_TRANSLATION_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ADDRESS_EXTENSION_lane0;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_BYPASS_TRANSLATION;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ADDRESS_EXTENSION;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT;
  wire       [31:0]   fetch_logic_ctrls_0_down_Fetch_WORD_PC;
  wire       [31:0]   execute_ctrl4_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  wire       [1:0]    execute_ctrl4_down_AguPlugin_SIZE_lane0;
  wire                execute_ctrl4_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
  wire                execute_ctrl4_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  wire       [31:0]   execute_ctrl4_down_Decode_UOP_lane0;
  wire                execute_ctrl2_down_RD_ENABLE_lane0;
  reg                 execute_ctrl2_RD_ENABLE_lane0_bypass;
  reg                 execute_ctrl2_LANE_SEL_lane0_bypass;
  wire                execute_ctrl1_down_RD_ENABLE_lane0;
  reg                 execute_ctrl1_RD_ENABLE_lane0_bypass;
  wire                execute_ctrl1_down_LANE_SEL_lane0;
  reg                 execute_ctrl1_LANE_SEL_lane0_bypass;
  wire                execute_ctrl0_down_RD_ENABLE_lane0;
  reg                 execute_ctrl0_RD_ENABLE_lane0_bypass;
  reg                 execute_ctrl0_LANE_SEL_lane0_bypass;
  wire                execute_ctrl1_down_TRAP_lane0;
  wire       [2:0]    execute_ctrl1_down_early0_EnvPlugin_OP_lane0;
  wire                execute_ctrl1_down_AguPlugin_GUEST_lane0;
  wire                execute_ctrl1_down_AguPlugin_INVALIDATE_lane0;
  wire                execute_ctrl1_down_AguPlugin_CLEAN_lane0;
  wire                execute_ctrl1_down_AguPlugin_FLOAT_lane0;
  wire                execute_ctrl1_down_AguPlugin_ATOMIC_lane0;
  wire                execute_ctrl1_down_AguPlugin_EXECUTE_lane0;
  wire                execute_ctrl1_down_AguPlugin_STORE_lane0;
  wire                execute_ctrl1_down_AguPlugin_LOAD_lane0;
  wire                execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0;
  wire                execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0;
  wire                execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0;
  wire                execute_ctrl1_down_DivPlugin_REM_lane0;
  wire                execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0;
  wire                execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0;
  wire                execute_ctrl1_down_MulPlugin_HIGH_lane0;
  wire       [1:0]    execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0;
  wire                execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0;
  wire                execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0;
  wire                execute_ctrl1_down_SrcStageables_UNSIGNED_lane0;
  wire                execute_ctrl1_down_BYPASSED_AT_4_lane0;
  wire                execute_ctrl1_down_BYPASSED_AT_3_lane0;
  wire                execute_ctrl1_down_BYPASSED_AT_2_lane0;
  wire       [1:0]    execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  wire                execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  wire                execute_ctrl1_down_SrcStageables_ZERO_lane0;
  wire                execute_ctrl1_down_SrcStageables_REVERT_lane0;
  wire       [1:0]    execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0;
  wire                execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0;
  wire                execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0;
  reg                 execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  reg                 execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  reg                 execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0;
  reg                 execute_ctrl1_down_COMPLETION_AT_3_lane0;
  reg                 execute_ctrl1_down_COMPLETION_AT_4_lane0;
  reg                 execute_ctrl1_down_COMPLETION_AT_2_lane0;
  reg                 execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0;
  reg                 execute_ctrl1_down_AguPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_CsrAccessPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_early0_EnvPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_early0_DivPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_early0_MulPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_early0_BranchPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0;
  reg                 execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0;
  wire                execute_ctrl3_down_TRAP_lane0;
  wire                execute_ctrl3_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  wire                execute_ctrl4_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  wire                execute_ctrl2_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0;
  wire       [4:0]    execute_ctrl1_down_RS2_PHYS_lane0;
  wire       [4:0]    execute_ctrl0_down_RS2_PHYS_lane0;
  wire       [31:0]   execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  wire       [4:0]    execute_ctrl1_down_RS1_PHYS_lane0;
  wire       [4:0]    execute_ctrl5_down_RD_PHYS_lane0;
  reg                 execute_ctrl5_up_RD_ENABLE_lane0;
  reg                 execute_ctrl5_up_LANE_SEL_lane0;
  wire       [4:0]    execute_ctrl0_down_RS1_PHYS_lane0;
  reg                 _zz_1;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_pmpPort_ACCESS_FAULT;
  wire                fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_fork_PMA_FAULT;
  wire                fetch_logic_ctrls_1_up_isCancel;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_fork_PMA_FAULT;
  wire       [0:0]    fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_BUFFER_ID;
  wire                fetch_logic_ctrls_0_up_isMoving;
  wire                fetch_logic_ctrls_0_up_isValid;
  wire                execute_ctrl3_down_CsrAccessPlugin_SEL_lane0;
  wire       [4:0]    execute_ctrl2_down_RD_PHYS_lane0;
  wire                execute_ctrl2_down_CsrAccessPlugin_CSR_CLEAR_lane0;
  wire                execute_ctrl2_down_CsrAccessPlugin_CSR_MASK_lane0;
  wire                execute_ctrl2_down_CsrAccessPlugin_CSR_IMM_lane0;
  wire                execute_ctrl2_down_CsrAccessPlugin_SEL_lane0;
  wire                fetch_logic_ctrls_0_up_isFiring;
  reg        [9:0]    fetch_logic_ctrls_0_up_Fetch_ID;
  wire                fetch_logic_ctrls_0_up_Fetch_PC_FAULT;
  wire       [31:0]   fetch_logic_ctrls_0_up_Fetch_WORD_PC;
  reg                 fetch_logic_ctrls_0_up_ready;
  wire                fetch_logic_ctrls_0_up_valid;
  wire       [0:0]    execute_ctrl4_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  wire       [31:0]   execute_ctrl4_down_PC_lane0;
  wire                execute_ctrl4_down_TRAP_lane0;
  wire                execute_ctrl5_down_COMMIT_lane0;
  wire                execute_ctrl5_down_isReady;
  wire                execute_ctrl5_down_LANE_SEL_lane0;
  wire                decode_ctrls_0_down_TRAP_0;
  wire                decode_ctrls_1_down_LANE_SEL_0;
  reg                 decode_ctrls_1_LANE_SEL_0_bypass;
  wire                decode_ctrls_0_down_LANE_SEL_0;
  reg                 decode_ctrls_0_LANE_SEL_0_bypass;
  wire                decode_ctrls_0_up_isMoving;
  wire       [15:0]   execute_ctrl0_down_Decode_UOP_ID_lane0;
  wire                execute_ctrl0_down_isReady;
  wire                execute_ctrl0_down_LANE_SEL_lane0;
  wire       [9:0]    decode_ctrls_1_down_Decode_DOP_ID_0;
  wire                decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0;
  wire                decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0;
  wire       [4:0]    execute_ctrl4_down_RD_PHYS_lane0;
  wire                execute_ctrl4_down_lane0_integer_WriteBackPlugin_SEL_lane0;
  wire       [15:0]   execute_ctrl4_down_Decode_UOP_ID_lane0;
  wire                execute_ctrl4_down_COMMIT_lane0;
  wire                execute_ctrl4_down_isReady;
  wire                execute_ctrl4_down_LANE_SEL_lane0;
  wire       [31:0]   execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  wire       [31:0]   execute_ctrl4_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;
  reg        [31:0]   execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  wire       [15:0]   execute_ctrl3_down_Decode_UOP_ID_lane0;
  wire                execute_ctrl3_down_COMMIT_lane0;
  wire                execute_ctrl3_down_isReady;
  wire                execute_ctrl3_down_LANE_SEL_lane0;
  wire       [31:0]   execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  wire       [31:0]   execute_ctrl3_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;
  reg        [31:0]   execute_ctrl3_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  wire                execute_ctrl2_down_LANE_SEL_lane0;
  wire       [31:0]   execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  wire       [31:0]   execute_ctrl2_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;
  wire                execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0;
  wire                execute_ctrl4_down_AguPlugin_EXECUTE_lane0;
  wire                execute_ctrl4_down_AguPlugin_LOAD_lane0;
  wire                execute_ctrl4_down_AguPlugin_ATOMIC_lane0;
  wire                execute_ctrl4_down_AguPlugin_FLOAT_lane0;
  wire                execute_ctrl4_down_AguPlugin_SEL_lane0;
  wire       [31:0]   execute_ctrl4_down_early0_SrcPlugin_ADD_SUB_lane0;
  wire       [31:0]   execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0;
  wire                execute_ctrl3_up_LsuCachelessPlugin_WITH_ACCESS_lane0;
  reg                 execute_ctrl3_up_LsuCachelessPlugin_WITH_RSP_lane0;
  reg                 execute_ctrl3_up_TRAP_lane0;
  wire                execute_ctrl3_down_AguPlugin_STORE_lane0;
  wire                execute_ctrl3_down_AguPlugin_SEL_lane0;
  wire       [31:0]   execute_ctrl3_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0;
  wire                execute_ctrl3_down_LsuCachelessPlugin_WITH_RSP_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_WITH_RSP_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_pmpPort_ACCESS_FAULT_lane0;
  wire                execute_ctrl0_up_COMPLETED_lane0;
  wire                execute_ctrl0_up_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_lane0;
  wire                execute_ctrl0_up_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_lane0;
  wire       [4:0]    execute_ctrl0_up_RD_PHYS_lane0;
  reg                 execute_ctrl0_up_RD_ENABLE_lane0;
  wire       [4:0]    execute_ctrl0_up_RS2_PHYS_lane0;
  wire                execute_ctrl0_up_RS2_ENABLE_lane0;
  wire       [4:0]    execute_ctrl0_up_RS1_PHYS_lane0;
  wire                execute_ctrl0_up_RS1_ENABLE_lane0;
  wire       [15:0]   execute_ctrl0_up_Decode_UOP_ID_lane0;
  wire                execute_ctrl0_up_TRAP_lane0;
  wire       [31:0]   execute_ctrl0_up_PC_lane0;
  wire       [0:0]    execute_ctrl0_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  wire                execute_ctrl0_up_DispatchPlugin_DONT_FLUSH_FROM_LANES_lane0;
  wire                execute_ctrl0_up_DispatchPlugin_DONT_FLUSH_lane0;
  reg                 execute_ctrl0_up_DispatchPlugin_MAY_FLUSH_lane0;
  wire                execute_ctrl0_up_DispatchPlugin_FENCE_OLDER_lane0;
  wire                execute_ctrl0_up_Decode_UOP_IS_COMPRESSED_lane0;
  wire       [31:0]   execute_ctrl0_up_Decode_UOP_lane0;
  wire                execute_ctrl0_up_LANE_SEL_lane0;
  wire                decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0;
  wire                decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0;
  wire       [31:0]   decode_ctrls_1_down_PC_0;
  wire       [0:0]    decode_ctrls_1_down_Decode_INSTRUCTION_SLICE_COUNT_0;
  wire                decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0;
  wire                decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0;
  wire                decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0;
  wire                decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0;
  wire                decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0;
  wire                decode_ctrls_1_up_isValid;
  reg                 decode_ctrls_1_down_ready;
  wire                execute_ctrl4_down_BYPASSED_AT_4_lane0;
  reg        [4:0]    execute_ctrl4_up_RD_PHYS_lane0;
  reg                 execute_ctrl4_up_RD_ENABLE_lane0;
  wire                execute_ctrl3_down_BYPASSED_AT_3_lane0;
  reg        [4:0]    execute_ctrl3_up_RD_PHYS_lane0;
  reg                 execute_ctrl3_up_RD_ENABLE_lane0;
  wire                execute_ctrl2_down_BYPASSED_AT_2_lane0;
  reg        [4:0]    execute_ctrl2_up_RD_PHYS_lane0;
  reg                 execute_ctrl2_up_RD_ENABLE_lane0;
  wire                execute_ctrl1_down_BYPASSED_AT_1_lane0;
  reg        [4:0]    execute_ctrl1_up_RD_PHYS_lane0;
  reg                 execute_ctrl1_up_RD_ENABLE_lane0;
  wire       [31:0]   decode_ctrls_1_down_Decode_UOP_0;
  reg                 decode_ctrls_1_up_TRAP_0;
  reg                 decode_ctrls_1_TRAP_0_bypass;
  wire       [15:0]   decode_ctrls_1_down_Decode_UOP_ID_0;
  wire                decode_ctrls_1_up_isReady;
  wire                decode_ctrls_1_down_TRAP_0;
  reg                 decode_ctrls_1_up_LANE_SEL_0;
  wire       [31:0]   decode_ctrls_1_down_Decode_INSTRUCTION_RAW_0;
  wire                decode_ctrls_1_down_Decode_VIRTUAL_0;
  wire                decode_ctrls_1_down_Decode_DECOMPRESSION_FAULT_0;
  wire                decode_ctrls_1_down_Decode_LEGAL_0;
  wire       [4:0]    decode_ctrls_1_down_RD_PHYS_0;
  reg                 decode_ctrls_1_down_RD_ENABLE_0;
  wire       [4:0]    decode_ctrls_1_down_RS2_PHYS_0;
  wire                decode_ctrls_1_down_RS2_ENABLE_0;
  wire       [4:0]    decode_ctrls_1_down_RS1_PHYS_0;
  wire       [31:0]   decode_ctrls_1_down_Decode_INSTRUCTION_0;
  wire                decode_ctrls_1_down_RS1_ENABLE_0;
  wire                decode_ctrls_1_up_isCanceling;
  wire                decode_ctrls_1_up_ready;
  reg                 decode_ctrls_1_up_valid;
  wire                decode_ctrls_1_up_isMoving;
  wire                execute_ctrl3_down_COMPLETION_AT_3_lane0;
  reg                 execute_ctrl3_up_COMPLETED_lane0;
  wire                execute_ctrl3_down_COMPLETED_lane0;
  wire                execute_ctrl3_COMPLETED_lane0_bypass;
  wire                execute_ctrl4_down_COMPLETION_AT_4_lane0;
  reg                 execute_ctrl4_up_COMPLETED_lane0;
  wire                execute_ctrl4_down_COMPLETED_lane0;
  wire                execute_ctrl4_COMPLETED_lane0_bypass;
  wire                execute_ctrl2_down_COMPLETION_AT_2_lane0;
  reg                 execute_ctrl2_up_COMPLETED_lane0;
  wire                execute_ctrl2_down_COMPLETED_lane0;
  wire                execute_ctrl2_COMPLETED_lane0_bypass;
  reg                 execute_ctrl1_up_LANE_SEL_lane0;
  wire                execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_IS_JALR_lane0;
  wire                execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_IS_JAL_lane0;
  wire                execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_MISSALIGNED_lane0;
  wire                execute_ctrl2_down_early0_BranchPlugin_SEL_lane0;
  wire                execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0;
  wire                execute_ctrl2_down_early0_BranchPlugin_logic_alu_MSB_FAILED_lane0;
  wire                execute_ctrl2_down_early0_BranchPlugin_logic_alu_EQ_lane0;
  wire                execute_ctrl2_up_COMMIT_lane0;
  wire                execute_ctrl2_down_COMMIT_lane0;
  reg                 execute_ctrl2_COMMIT_lane0_bypass;
  reg                 execute_ctrl2_up_TRAP_lane0;
  wire                execute_ctrl2_down_TRAP_lane0;
  reg                 execute_ctrl2_TRAP_lane0_bypass;
  wire                execute_ctrl2_down_early0_EnvPlugin_SEL_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_FENCE_lane0;
  wire                execute_ctrl2_down_AguPlugin_ATOMIC_lane0;
  wire                execute_ctrl2_down_AguPlugin_SEL_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onTrigger_HIT_lane0;
  wire                execute_ctrl2_down_AguPlugin_LOAD_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_HAZARD_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_REFILL_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ACCESS_FAULT_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_PAGE_FAULT_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_MMU_FAILURE_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  wire                execute_ctrl2_down_AguPlugin_EXECUTE_lane0;
  wire                execute_ctrl2_down_AguPlugin_STORE_lane0;
  wire       [31:0]   execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  wire       [31:0]   execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  reg        [1:0]    execute_ctrl2_up_AguPlugin_SIZE_lane0;
  reg        [1:0]    execute_ctrl2_AguPlugin_SIZE_lane0_bypass;
  wire                execute_ctrl2_down_AguPlugin_GUEST_lane0;
  wire                execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_MISS_ALIGNED_lane0;
  wire       [1:0]    execute_ctrl2_down_AguPlugin_SIZE_lane0;
  wire       [31:0]   execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_RAW_ADDRESS_lane0;
  reg        [31:0]   execute_ctrl2_down_LsuCachelessPlugin_logic_onFirst_WRITE_DATA_lane0;
  wire       [31:0]   execute_ctrl0_down_Decode_UOP_lane0;
  wire       [9:0]    fetch_logic_ctrls_1_down_Fetch_ID;
  reg                 fetch_logic_ctrls_1_down_TRAP;
  wire                fetch_logic_ctrls_1_down_isCancel;
  wire                fetch_logic_ctrls_1_down_isReady;
  wire                fetch_logic_ctrls_1_down_ready;
  wire                decode_ctrls_0_up_isCancel;
  reg                 fetch_logic_ctrls_1_down_valid;
  wire                fetch_logic_ctrls_1_down_isValid;
  wire       [31:0]   fetch_logic_ctrls_1_down_Fetch_WORD;
  wire                decode_ctrls_0_up_valid;
  wire                decode_ctrls_0_up_TRAP_0;
  wire       [9:0]    decode_ctrls_0_up_Fetch_ID_0;
  wire       [9:0]    decode_ctrls_0_up_Decode_DOP_ID_0;
  wire       [31:0]   decode_ctrls_0_up_PC_0;
  wire       [0:0]    decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0;
  reg        [31:0]   decode_ctrls_0_up_Decode_INSTRUCTION_RAW_0;
  reg                 decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_0;
  reg        [31:0]   decode_ctrls_0_up_Decode_INSTRUCTION_0;
  wire                decode_ctrls_0_up_isFiring;
  wire       [1:0]    fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_LAST;
  wire       [1:0]    fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK;
  (* keep , syn_keep *) wire       [31:0]   execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0 /* synthesis syn_keep = 1 */ ;
  (* keep , syn_keep *) wire       [31:0]   execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0 /* synthesis syn_keep = 1 */ ;
  (* keep , syn_keep *) reg        [31:0]   execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0 /* synthesis syn_keep = 1 */ ;
  wire       [0:0]    execute_ctrl2_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  wire       [1:0]    execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0;
  wire       [31:0]   execute_ctrl2_down_Decode_UOP_lane0;
  reg                 execute_ctrl3_up_LANE_SEL_lane0;
  wire       [1:0]    execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  wire                execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  reg                 execute_ctrl4_up_LANE_SEL_lane0;
  wire                execute_ctrl2_down_SrcStageables_UNSIGNED_lane0;
  wire                execute_ctrl2_down_SrcStageables_ZERO_lane0;
  wire                execute_ctrl2_down_SrcStageables_REVERT_lane0;
  wire       [31:0]   execute_ctrl1_down_PC_lane0;
  wire       [31:0]   execute_ctrl1_down_integer_RS2_lane0;
  wire       [1:0]    execute_ctrl1_down_early0_SrcPlugin_logic_SRC2_CTRL_lane0;
  wire       [31:0]   execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0;
  wire       [31:0]   execute_ctrl1_down_integer_RS1_lane0;
  wire       [0:0]    execute_ctrl1_down_early0_SrcPlugin_logic_SRC1_CTRL_lane0;
  wire       [31:0]   execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0;
  wire       [31:0]   execute_ctrl1_down_Decode_UOP_lane0;
  wire       [2:0]    execute_ctrl2_down_early0_EnvPlugin_OP_lane0;
  wire       [31:0]   execute_ctrl2_down_PC_lane0;
  wire       [15:0]   execute_ctrl2_down_Decode_UOP_ID_lane0;
  wire                decode_ctrls_1_lane0_upIsCancel;
  wire                decode_ctrls_1_lane0_downIsCancel;
  wire       [9:0]    decode_ctrls_0_down_Decode_DOP_ID_0;
  wire       [9:0]    decode_ctrls_0_down_Fetch_ID_0;
  wire       [31:0]   decode_ctrls_0_down_PC_0;
  wire                decode_ctrls_0_up_isReady;
  wire                decode_ctrls_0_up_LANE_SEL_0;
  wire                decode_ctrls_0_lane0_upIsCancel;
  wire                decode_ctrls_0_lane0_downIsCancel;
  wire       [9:0]    fetch_logic_ctrls_0_down_Fetch_ID;
  wire       [1:0]    execute_ctrl0_down_AguPlugin_SIZE_lane0;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onPma_RSP_fault;
  wire                fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onPma_RSP_io;
  wire       [31:0]   fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
  wire       [31:0]   fetch_logic_ctrls_1_down_Fetch_WORD_PC;
  wire       [31:0]   fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
  wire       [0:0]    fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_BUFFER_ID;
  wire                fetch_logic_ctrls_1_up_isValid;
  reg                 _zz_2;
  wire                fetch_logic_ctrls_0_down_isFiring;
  wire       [31:0]   execute_ctrl3_down_DivPlugin_DIV_RESULT_lane0;
  wire                execute_ctrl3_down_early0_DivPlugin_SEL_lane0;
  wire       [31:0]   execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0;
  wire                execute_ctrl2_down_early0_DivPlugin_SEL_lane0;
  reg                 execute_ctrl2_up_LANE_SEL_lane0;
  wire                execute_ctrl2_down_isReady;
  wire                execute_ctrl2_down_DivPlugin_REM_lane0;
  wire       [31:0]   execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0;
  wire       [31:0]   execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0;
  wire                execute_ctrl2_down_RsUnsignedPlugin_RS2_REVERT_lane0;
  wire                execute_ctrl2_down_RsUnsignedPlugin_RS1_REVERT_lane0;
  wire       [31:0]   execute_ctrl2_down_RsUnsignedPlugin_RS2_FORMATED_lane0;
  wire       [31:0]   execute_ctrl2_down_RsUnsignedPlugin_RS1_FORMATED_lane0;
  wire                execute_ctrl4_down_MulPlugin_HIGH_lane0;
  wire                execute_ctrl4_down_early0_MulPlugin_SEL_lane0;
  wire       [65:0]   execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0;
  wire       [4:0]    execute_ctrl4_down_early0_MulPlugin_logic_steps_0_adders_1_lane0;
  wire       [62:0]   execute_ctrl4_down_early0_MulPlugin_logic_steps_0_adders_0_lane0;
  wire       [29:0]   execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  wire       [46:0]   execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  wire       [46:0]   execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  wire       [33:0]   execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_0_lane0;
  wire       [4:0]    execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0;
  wire       [62:0]   execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0;
  wire       [29:0]   execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  wire       [46:0]   execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  wire       [46:0]   execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  wire       [33:0]   execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_0_lane0;
  wire       [29:0]   execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  wire       [46:0]   execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  wire       [46:0]   execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  wire       [33:0]   execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_0_lane0;
  wire                execute_ctrl2_down_RsUnsignedPlugin_RS2_SIGNED_lane0;
  wire       [32:0]   execute_ctrl2_down_MUL_SRC2_lane0;
  wire                execute_ctrl2_down_RsUnsignedPlugin_RS1_SIGNED_lane0;
  wire       [32:0]   execute_ctrl2_down_MUL_SRC1_lane0;
  reg        [31:0]   execute_ctrl2_up_integer_RS2_lane0;
  reg        [31:0]   execute_ctrl2_up_integer_RS1_lane0;
  wire                execute_ctrl2_down_early0_BarrelShifterPlugin_SEL_lane0;
  wire       [31:0]   execute_ctrl2_down_early0_BarrelShifterPlugin_SHIFT_RESULT_lane0;
  wire                execute_ctrl2_down_BarrelShifterPlugin_SIGNED_lane0;
  wire                execute_ctrl2_down_BarrelShifterPlugin_LEFT_lane0;
  wire                execute_ctrl2_down_early0_IntAluPlugin_SEL_lane0;
  wire       [31:0]   execute_ctrl2_down_early0_IntAluPlugin_ALU_RESULT_lane0;
  wire                execute_ctrl2_down_early0_IntAluPlugin_ALU_SLTX_lane0;
  wire                execute_ctrl2_down_early0_SrcPlugin_LESS_lane0;
  wire                execute_ctrl2_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0;
  wire       [31:0]   execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0;
  wire       [31:0]   execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0;
  wire       [31:0]   execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0;
  wire       [1:0]    execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0;
  wire                early0_BranchPlugin_logic_events_branchMiss;
  wire                early0_BranchPlugin_logic_events_branchCount;
  wire                AlignerPlugin_api_singleFetch;
  wire                AlignerPlugin_api_downMoving;
  wire                AlignerPlugin_api_haltIt;
  wire                DispatchPlugin_api_haltDispatch;
  wire                execute_freeze_valid;
  wire       [0:0]    execute_lane0_api_hartsInflight;
  wire                execute_lane0_ctrls_2_upIsCancel;
  wire                execute_lane0_ctrls_2_downIsCancel;
  reg                 CsrRamPlugin_api_holdRead;
  reg                 CsrRamPlugin_api_holdWrite;
  reg                 CsrAccessPlugin_bus_decode_exception;
  wire                CsrAccessPlugin_bus_decode_read;
  wire                CsrAccessPlugin_bus_decode_write;
  wire       [11:0]   CsrAccessPlugin_bus_decode_address;
  wire       [1:0]    CsrAccessPlugin_bus_decode_privilege;
  reg                 CsrAccessPlugin_bus_decode_trap;
  wire                PrivilegedPlugin_api_lsuTriggerBus_load;
  wire                PrivilegedPlugin_api_lsuTriggerBus_store;
  wire       [31:0]   PrivilegedPlugin_api_lsuTriggerBus_virtual;
  wire       [1:0]    PrivilegedPlugin_api_lsuTriggerBus_size;
  wire                PrivilegedPlugin_api_harts_0_allowInterrupts;
  wire                PrivilegedPlugin_api_harts_0_allowException;
  wire                PrivilegedPlugin_api_harts_0_allowEbreakException;
  wire                PrivilegedPlugin_api_harts_0_fpuEnable;
  reg                 TrapPlugin_api_harts_0_redo;
  reg                 TrapPlugin_api_harts_0_askWake;
  reg                 TrapPlugin_api_harts_0_rvTrap;
  wire                TrapPlugin_api_harts_0_fsmBusy;
  wire                TrapPlugin_api_harts_0_holdPrivChange;
  wire                FetchCachelessPlugin_logic_trapPort_valid;
  reg                 FetchCachelessPlugin_logic_trapPort_payload_exception;
  wire       [31:0]   FetchCachelessPlugin_logic_trapPort_payload_tval;
  wire       [31:0]   FetchCachelessPlugin_logic_trapPort_payload_tval2;
  wire       [0:0]    decode_logic_trapPending;
  wire       [0:0]    DispatchPlugin_logic_trapPendings;
  wire       [0:0]    execute_lane0_logic_trapPending;
  wire                early0_IntAluPlugin_logic_wb_valid;
  wire       [31:0]   early0_IntAluPlugin_logic_wb_payload;
  (* keep , syn_keep *) reg        [31:0]   early0_IntAluPlugin_logic_alu_bitwise /* synthesis syn_keep = 1 */ ;
  wire       [31:0]   early0_IntAluPlugin_logic_alu_result;
  wire                early0_BarrelShifterPlugin_logic_wb_valid;
  wire       [31:0]   early0_BarrelShifterPlugin_logic_wb_payload;
  wire       [4:0]    early0_BarrelShifterPlugin_logic_shift_amplitude;
  wire       [31:0]   early0_BarrelShifterPlugin_logic_shift_reversed;
  wire       [31:0]   early0_BarrelShifterPlugin_logic_shift_shifted;
  wire       [31:0]   early0_BarrelShifterPlugin_logic_shift_patched;
  wire                early0_BranchPlugin_logic_wb_valid;
  wire       [31:0]   early0_BranchPlugin_logic_wb_payload;
  wire                early0_BranchPlugin_logic_pcPort_valid;
  wire                early0_BranchPlugin_logic_pcPort_payload_fault;
  wire       [31:0]   early0_BranchPlugin_logic_pcPort_payload_pc;
  wire                PcPlugin_logic_harts_0_aggregator_sortedByPriority_1_laneValid;
  wire                early0_BranchPlugin_logic_flushPort_valid;
  reg                 LsuCachelessPlugin_logic_trapPort_valid;
  reg                 LsuCachelessPlugin_logic_trapPort_payload_exception;
  wire       [31:0]   LsuCachelessPlugin_logic_trapPort_payload_tval;
  wire       [31:0]   LsuCachelessPlugin_logic_trapPort_payload_tval2;
  wire                early0_MulPlugin_logic_formatBus_valid;
  wire       [31:0]   early0_MulPlugin_logic_formatBus_payload;
  wire                execute_lane0_ctrls_3_upIsCancel;
  wire                execute_lane0_ctrls_3_downIsCancel;
  reg        [60:0]   _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0;
  reg        [60:0]   _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_1;
  reg        [60:0]   _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_2;
  reg        [2:0]    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0;
  reg        [2:0]    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_1;
  reg        [2:0]    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_2;
  wire                execute_lane0_ctrls_4_upIsCancel;
  wire                execute_lane0_ctrls_4_downIsCancel;
  reg        [65:0]   _zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0;
  reg        [65:0]   _zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0_1;
  wire                early0_DivPlugin_logic_formatBus_valid;
  wire       [31:0]   early0_DivPlugin_logic_formatBus_payload;
  reg                 early0_DivPlugin_logic_processing_divRevertResult;
  reg                 early0_DivPlugin_logic_processing_cmdSent;
  wire                io_cmd_fire;
  wire                early0_DivPlugin_logic_processing_request;
  wire       [31:0]   early0_DivPlugin_logic_processing_a;
  wire       [31:0]   early0_DivPlugin_logic_processing_b;
  reg                 early0_DivPlugin_logic_processing_unscheduleRequest;
  wire                early0_DivPlugin_logic_processing_freeze;
  wire       [31:0]   early0_DivPlugin_logic_processing_selected;
  wire       [31:0]   _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0;
  wire                CsrAccessPlugin_logic_wbWi_valid;
  wire       [31:0]   CsrAccessPlugin_logic_wbWi_payload;
  reg                 CsrAccessPlugin_logic_flushPort_valid;
  reg                 PrivilegedPlugin_logic_harts_0_xretAwayFromMachine;
  wire       [0:0]    PrivilegedPlugin_logic_harts_0_commitMask;
  reg                 PrivilegedPlugin_logic_harts_0_int_pending;
  reg        [2:0]    PrivilegedPlugin_logic_harts_0_privilege;
  wire                PrivilegedPlugin_logic_harts_0_withMachinePrivilege;
  wire                PrivilegedPlugin_logic_harts_0_withSupervisorPrivilege;
  wire                PrivilegedPlugin_logic_harts_0_withVirtualSupervisorPrivilege;
  wire                PrivilegedPlugin_logic_harts_0_withGuestPrivilege;
  wire                PrivilegedPlugin_logic_harts_0_withHostPrivilege;
  wire                PrivilegedPlugin_logic_harts_0_hartRunning;
  wire                PrivilegedPlugin_logic_harts_0_debugMode;
  wire                _zz_when_CsrService_l221;
  reg                 PrivilegedPlugin_logic_harts_0_m_status_mie;
  reg                 PrivilegedPlugin_logic_harts_0_m_status_mpie;
  wire       [1:0]    PrivilegedPlugin_logic_harts_0_m_status_mpp;
  wire                PrivilegedPlugin_logic_harts_0_m_status_mpv;
  wire                PrivilegedPlugin_logic_harts_0_m_status_sd;
  wire                PrivilegedPlugin_logic_harts_0_m_status_tw;
  reg                 PrivilegedPlugin_logic_harts_0_m_status_mprv;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue;
  wire                _zz_when_CsrService_l221_1;
  reg                 PrivilegedPlugin_logic_harts_0_m_cause_interrupt;
  reg        [4:0]    PrivilegedPlugin_logic_harts_0_m_cause_code;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_1;
  reg                 PrivilegedPlugin_logic_harts_0_m_ip_meip;
  reg                 PrivilegedPlugin_logic_harts_0_m_ip_mtip;
  reg                 PrivilegedPlugin_logic_harts_0_m_ip_msip;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_2;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_3;
  reg                 PrivilegedPlugin_logic_harts_0_m_ie_meie;
  reg                 PrivilegedPlugin_logic_harts_0_m_ie_mtie;
  reg                 PrivilegedPlugin_logic_harts_0_m_ie_msie;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_4;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_5;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid;
  reg        [4:0]    PrivilegedPlugin_logic_harts_0_m_topi_interrupt;
  wire       [0:0]    PrivilegedPlugin_logic_harts_0_m_topi_priority;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_6;
  wire                PrivilegedPlugin_logic_harts_0_m_envcfg_stce;
  reg                 PrivilegedPlugin_logic_harts_0_m_counteren_tm;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_7;
  wire                PrivilegedPlugin_logic_harts_0_time_host_allowSupervisor;
  wire                PrivilegedPlugin_logic_harts_0_time_host_allowUser;
  wire                PrivilegedPlugin_logic_harts_0_time_host_accessable;
  reg                 early0_EnvPlugin_logic_trapPort_valid;
  reg                 early0_EnvPlugin_logic_trapPort_payload_exception;
  wire       [31:0]   early0_EnvPlugin_logic_trapPort_payload_tval;
  wire       [31:0]   early0_EnvPlugin_logic_trapPort_payload_tval2;
  reg        [4:0]    early0_EnvPlugin_logic_trapPort_payload_code;
  reg        [2:0]    early0_EnvPlugin_logic_trapPort_payload_arg;
  reg                 early0_EnvPlugin_logic_flushPort_valid;
  wire                WhiteboxerPlugin_logic_fetch_fire;
  reg        [4:0]    CsrAccessPlugin_bus_decode_trapCode;
  wire                CsrAccessPlugin_bus_decode_fence;
  reg                 CsrAccessPlugin_bus_decode_virtual;
  reg                 CsrAccessPlugin_bus_decode_hostDenied;
  wire                CsrAccessPlugin_bus_read_valid;
  wire                CsrAccessPlugin_bus_read_moving;
  wire       [11:0]   CsrAccessPlugin_bus_read_address;
  reg                 CsrAccessPlugin_bus_read_halt;
  wire       [31:0]   CsrAccessPlugin_bus_read_toWriteBits;
  wire       [31:0]   CsrAccessPlugin_bus_read_data;
  wire                CsrAccessPlugin_bus_write_valid;
  wire                CsrAccessPlugin_bus_write_moving;
  reg                 CsrAccessPlugin_bus_write_halt;
  reg        [31:0]   CsrAccessPlugin_bus_write_bits;
  wire       [11:0]   CsrAccessPlugin_bus_write_address;
  reg        [4:0]    FetchCachelessPlugin_logic_trapPort_payload_code;
  reg        [2:0]    FetchCachelessPlugin_logic_trapPort_payload_arg;
  reg                 FetchCachelessPlugin_logic_buffer_reserveId_willIncrement;
  wire                FetchCachelessPlugin_logic_buffer_reserveId_willDecrement;
  wire                FetchCachelessPlugin_logic_buffer_reserveId_willClear;
  wire                FetchCachelessPlugin_logic_buffer_reserveId_willLoad;
  reg        [0:0]    FetchCachelessPlugin_logic_buffer_reserveId_valueNext;
  reg        [0:0]    FetchCachelessPlugin_logic_buffer_reserveId_value;
  wire                FetchCachelessPlugin_logic_buffer_reserveId_willOverflowIfInc;
  wire                FetchCachelessPlugin_logic_buffer_reserveId_willUnderflowIfDec;
  wire                FetchCachelessPlugin_logic_buffer_reserveId_willOverflow;
  wire                FetchCachelessPlugin_logic_buffer_reserveId_willUnderflow;
  reg                 FetchCachelessPlugin_logic_buffer_inflight_0;
  reg                 FetchCachelessPlugin_logic_buffer_inflight_1;
  reg                 FetchCachelessPlugin_logic_buffer_write_valid;
  wire       [0:0]    FetchCachelessPlugin_logic_buffer_write_payload_address;
  wire                FetchCachelessPlugin_logic_buffer_write_payload_data_error;
  wire       [31:0]   FetchCachelessPlugin_logic_buffer_write_payload_data_word;
  wire                FetchCachelessPlugin_logic_buffer_reservedHits_0;
  wire                FetchCachelessPlugin_logic_buffer_full;
  wire                FetchCachelessPlugin_logic_buffer_inflightSpawn;
  wire       [1:0]    _zz_4;
  wire       [1:0]    _zz_5;
  wire       [31:0]   FetchCachelessPlugin_logic_onPma_port_cmd_address;
  wire                FetchCachelessPlugin_logic_onPma_port_rsp_fault;
  wire                FetchCachelessPlugin_logic_onPma_port_rsp_io;
  reg        [4:0]    LsuCachelessPlugin_logic_trapPort_payload_code;
  reg        [2:0]    LsuCachelessPlugin_logic_trapPort_payload_arg;
  reg                 LsuCachelessPlugin_logic_flushPort_valid;
  wire       [15:0]   LsuCachelessPlugin_logic_flushPort_payload_uopId;
  wire                LsuCachelessPlugin_logic_flushPort_payload_self;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_0;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_1;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_2;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_3;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_4;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_5;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_6;
  wire                LsuCachelessPlugin_logic_frontend_defaultsDecodings_7;
  wire                LsuCachelessPlugin_logic_iwb_valid;
  reg        [31:0]   LsuCachelessPlugin_logic_iwb_payload;
  wire                execute_lane0_ctrls_0_upIsCancel;
  wire                execute_lane0_ctrls_0_downIsCancel;
  wire       [9:0]    WhiteboxerPlugin_logic_fetch_fetchId;
  wire                WhiteboxerPlugin_logic_decodes_0_fire;
  reg                 decode_ctrls_0_up_LANE_SEL_0_regNext;
  wire                when_CtrlLaneApi_l50;
  wire                WhiteboxerPlugin_logic_decodes_0_spawn;
  wire       [63:0]   WhiteboxerPlugin_logic_decodes_0_pc;
  wire       [9:0]    WhiteboxerPlugin_logic_decodes_0_fetchId;
  wire       [9:0]    WhiteboxerPlugin_logic_decodes_0_decodeId;
  wire       [15:0]   early0_BranchPlugin_logic_flushPort_payload_uopId;
  wire                early0_BranchPlugin_logic_flushPort_payload_self;
  wire       [15:0]   CsrAccessPlugin_logic_flushPort_payload_uopId;
  wire                CsrAccessPlugin_logic_flushPort_payload_self;
  reg                 CsrAccessPlugin_logic_trapPort_valid;
  reg                 CsrAccessPlugin_logic_trapPort_payload_exception;
  wire       [31:0]   CsrAccessPlugin_logic_trapPort_payload_tval;
  wire       [31:0]   CsrAccessPlugin_logic_trapPort_payload_tval2;
  reg        [4:0]    CsrAccessPlugin_logic_trapPort_payload_code;
  wire       [2:0]    CsrAccessPlugin_logic_trapPort_payload_arg;
  wire       [15:0]   early0_EnvPlugin_logic_flushPort_payload_uopId;
  wire                early0_EnvPlugin_logic_flushPort_payload_self;
  wire       [1:0]    PrivilegedPlugin_logic_defaultTrap_csrPrivilege;
  wire                PrivilegedPlugin_logic_defaultTrap_csrReadOnly;
  wire       [2:0]    PrivilegedPlugin_logic_defaultTrap_hartPrivilege;
  wire       [1:0]    PrivilegedPlugin_logic_defaultTrap_adjustPrivilege;
  wire                when_PrivilegedPlugin_l1186;
  wire                when_PrivilegedPlugin_l1190;
  wire                execute_lane0_ctrls_1_upIsCancel;
  wire                execute_lane0_ctrls_1_downIsCancel;
  reg        [31:0]   _zz_execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0;
  reg        [31:0]   _zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0;
  reg        [31:0]   early0_SrcPlugin_logic_addsub_combined_rs2Patched;
  wire                lane0_IntFormatPlugin_logic_stages_0_wb_valid;
  wire       [31:0]   lane0_IntFormatPlugin_logic_stages_0_wb_payload;
  wire       [1:0]    lane0_IntFormatPlugin_logic_stages_0_hits;
  wire       [31:0]   lane0_IntFormatPlugin_logic_stages_0_raw;
  wire                lane0_IntFormatPlugin_logic_stages_1_wb_valid;
  reg        [31:0]   lane0_IntFormatPlugin_logic_stages_1_wb_payload;
  wire       [1:0]    lane0_IntFormatPlugin_logic_stages_1_hits;
  wire       [31:0]   lane0_IntFormatPlugin_logic_stages_1_raw;
  wire                lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_sels_0;
  reg                 _zz_lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_value;
  wire                lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_value;
  wire                lane0_IntFormatPlugin_logic_stages_1_segments_0_doIt;
  wire                lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_sels_0;
  wire                lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_sels_1;
  reg                 _zz_lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value;
  wire                lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value;
  wire                lane0_IntFormatPlugin_logic_stages_1_segments_1_doIt;
  wire                lane0_IntFormatPlugin_logic_stages_2_wb_valid;
  wire       [31:0]   lane0_IntFormatPlugin_logic_stages_2_wb_payload;
  wire       [1:0]    lane0_IntFormatPlugin_logic_stages_2_hits;
  wire       [31:0]   lane0_IntFormatPlugin_logic_stages_2_raw;
  wire       [0:0]    PerformanceCounterPlugin_logic_commitMask;
  reg                 PerformanceCounterPlugin_logic_ignoreNextCommit;
  wire                when_PerformanceCounterPlugin_l45;
  wire       [0:0]    PerformanceCounterPlugin_logic_commitCount;
  wire                PerformanceCounterPlugin_logic_eventCycles;
  wire                PerformanceCounterPlugin_logic_eventInstructions_0;
  reg        [7:0]    PerformanceCounterPlugin_logic_counters_cycle_value;
  wire                PerformanceCounterPlugin_logic_counters_cycle_needFlush;
  reg                 PerformanceCounterPlugin_logic_counters_cycle_mcounteren;
  reg                 PerformanceCounterPlugin_logic_counters_cycle_mcountinhibit;
  wire                _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_8;
  reg        [7:0]    PerformanceCounterPlugin_logic_counters_instret_value;
  wire                PerformanceCounterPlugin_logic_counters_instret_needFlush;
  reg                 PerformanceCounterPlugin_logic_counters_instret_mcounteren;
  reg                 PerformanceCounterPlugin_logic_counters_instret_mcountinhibit;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_counters_instret_value;
  reg        [31:0]   early0_BranchPlugin_pcCalc_target_a;
  reg        [31:0]   early0_BranchPlugin_pcCalc_target_b;
  wire       [1:0]    early0_BranchPlugin_pcCalc_slices;
  wire       [1:0]    AlignerPlugin_logic_maskGen_frontMasks_0;
  wire       [1:0]    AlignerPlugin_logic_maskGen_frontMasks_1;
  wire       [1:0]    AlignerPlugin_logic_maskGen_backMasks_0;
  wire       [1:0]    AlignerPlugin_logic_maskGen_backMasks_1;
  wire       [15:0]   AlignerPlugin_logic_slices_data_0;
  wire       [15:0]   AlignerPlugin_logic_slices_data_1;
  wire       [15:0]   AlignerPlugin_logic_slices_data_2;
  wire       [15:0]   AlignerPlugin_logic_slices_data_3;
  wire       [3:0]    AlignerPlugin_logic_slices_mask;
  wire       [3:0]    AlignerPlugin_logic_slices_last;
  wire       [31:0]   AlignerPlugin_logic_slicesInstructions_0;
  wire       [31:0]   AlignerPlugin_logic_slicesInstructions_1;
  wire       [31:0]   AlignerPlugin_logic_slicesInstructions_2;
  wire       [31:0]   AlignerPlugin_logic_slicesInstructions_3;
  reg        [3:0]    AlignerPlugin_logic_scanners_0_usageMask;
  wire                AlignerPlugin_logic_scanners_0_checker_0_required;
  wire                AlignerPlugin_logic_scanners_0_checker_0_last;
  wire                AlignerPlugin_logic_scanners_0_checker_0_redo;
  wire                AlignerPlugin_logic_scanners_0_checker_0_present;
  wire                AlignerPlugin_logic_scanners_0_checker_0_valid;
  wire                AlignerPlugin_logic_scanners_0_checker_1_required;
  wire                AlignerPlugin_logic_scanners_0_checker_1_last;
  wire                AlignerPlugin_logic_scanners_0_checker_1_redo;
  wire                AlignerPlugin_logic_scanners_0_checker_1_present;
  wire                AlignerPlugin_logic_scanners_0_checker_1_valid;
  wire                AlignerPlugin_logic_scanners_0_redo;
  wire                AlignerPlugin_logic_scanners_0_valid;
  reg        [3:0]    AlignerPlugin_logic_scanners_1_usageMask;
  wire                AlignerPlugin_logic_scanners_1_checker_0_required;
  wire                AlignerPlugin_logic_scanners_1_checker_0_last;
  wire                AlignerPlugin_logic_scanners_1_checker_0_redo;
  wire                AlignerPlugin_logic_scanners_1_checker_0_present;
  wire                AlignerPlugin_logic_scanners_1_checker_0_valid;
  wire                AlignerPlugin_logic_scanners_1_checker_1_required;
  wire                AlignerPlugin_logic_scanners_1_checker_1_last;
  wire                AlignerPlugin_logic_scanners_1_checker_1_redo;
  wire                AlignerPlugin_logic_scanners_1_checker_1_present;
  wire                AlignerPlugin_logic_scanners_1_checker_1_valid;
  wire                AlignerPlugin_logic_scanners_1_redo;
  wire                AlignerPlugin_logic_scanners_1_valid;
  reg        [3:0]    AlignerPlugin_logic_scanners_2_usageMask;
  wire                AlignerPlugin_logic_scanners_2_checker_0_required;
  wire                AlignerPlugin_logic_scanners_2_checker_0_last;
  wire                AlignerPlugin_logic_scanners_2_checker_0_redo;
  wire                AlignerPlugin_logic_scanners_2_checker_0_present;
  wire                AlignerPlugin_logic_scanners_2_checker_0_valid;
  wire                AlignerPlugin_logic_scanners_2_checker_1_required;
  wire                AlignerPlugin_logic_scanners_2_checker_1_last;
  wire                AlignerPlugin_logic_scanners_2_checker_1_redo;
  wire                AlignerPlugin_logic_scanners_2_checker_1_present;
  wire                AlignerPlugin_logic_scanners_2_checker_1_valid;
  wire                AlignerPlugin_logic_scanners_2_redo;
  wire                AlignerPlugin_logic_scanners_2_valid;
  reg        [3:0]    AlignerPlugin_logic_scanners_3_usageMask;
  wire                AlignerPlugin_logic_scanners_3_checker_0_required;
  wire                AlignerPlugin_logic_scanners_3_checker_0_last;
  wire                AlignerPlugin_logic_scanners_3_checker_0_redo;
  wire                AlignerPlugin_logic_scanners_3_checker_0_present;
  wire                AlignerPlugin_logic_scanners_3_checker_0_valid;
  wire                AlignerPlugin_logic_scanners_3_checker_1_required;
  wire                AlignerPlugin_logic_scanners_3_checker_1_last;
  wire                AlignerPlugin_logic_scanners_3_checker_1_redo;
  wire                AlignerPlugin_logic_scanners_3_checker_1_present;
  wire                AlignerPlugin_logic_scanners_3_checker_1_valid;
  wire                AlignerPlugin_logic_scanners_3_redo;
  wire                AlignerPlugin_logic_scanners_3_valid;
  wire       [3:0]    AlignerPlugin_logic_usedMask_0;
  wire       [3:0]    AlignerPlugin_logic_usedMask_1;
  wire                AlignerPlugin_logic_extractors_0_first;
  wire       [3:0]    AlignerPlugin_logic_extractors_0_usableMask;
  wire       [3:0]    _zz_AlignerPlugin_logic_extractors_0_usableMask_bools_0;
  wire                AlignerPlugin_logic_extractors_0_usableMask_bools_0;
  wire                AlignerPlugin_logic_extractors_0_usableMask_bools_1;
  wire                AlignerPlugin_logic_extractors_0_usableMask_bools_2;
  wire                AlignerPlugin_logic_extractors_0_usableMask_bools_3;
  reg        [3:0]    _zz_AlignerPlugin_logic_extractors_0_slicesOh;
  wire                AlignerPlugin_logic_extractors_0_usableMask_range_0_to_1;
  wire                AlignerPlugin_logic_extractors_0_usableMask_range_0_to_2;
  wire       [3:0]    AlignerPlugin_logic_extractors_0_slicesOh;
  wire                _zz_AlignerPlugin_logic_extractors_0_redo;
  wire                _zz_AlignerPlugin_logic_extractors_0_redo_1;
  wire                _zz_AlignerPlugin_logic_extractors_0_redo_2;
  wire                _zz_AlignerPlugin_logic_extractors_0_redo_3;
  reg                 AlignerPlugin_logic_extractors_0_redo;
  wire       [1:0]    AlignerPlugin_logic_extractors_0_localMask;
  wire       [3:0]    AlignerPlugin_logic_extractors_0_usageMask;
  reg                 AlignerPlugin_logic_extractors_0_valid;
  reg        [31:0]   AlignerPlugin_logic_extractors_0_ctx_pc;
  wire       [31:0]   AlignerPlugin_logic_extractors_0_ctx_instruction;
  wire       [9:0]    AlignerPlugin_logic_extractors_0_ctx_hm_Fetch_ID;
  wire                AlignerPlugin_logic_extractors_0_ctx_trap;
  wire                when_AlignerPlugin_l160;
  reg        [9:0]    AlignerPlugin_logic_feeder_harts_0_dopId;
  wire                when_AlignerPlugin_l171;
  wire                AlignerPlugin_logic_feeder_lanes_0_valid;
  wire                AlignerPlugin_logic_feeder_lanes_0_isRvc;
  reg        [31:0]   AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst;
  reg                 AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_1;
  wire       [11:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_2;
  wire       [11:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_3;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
  reg        [11:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
  reg        [9:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7;
  wire       [20:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_8;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
  reg        [14:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_11;
  reg        [2:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_12;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
  reg        [9:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14;
  wire       [20:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_15;
  wire                _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_16;
  reg        [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_17;
  wire       [12:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_19;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_20;
  wire       [4:0]    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_21;
  wire       [4:0]    switch_Rvc_l55;
  wire                when_Rvc_l59;
  wire                when_Rvc_l80;
  wire       [31:0]   _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22;
  wire                when_Rvc_l101;
  wire                when_Rvc_l114;
  wire       [1:0]    _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0;
  wire       [1:0]    _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_1;
  wire                _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_2;
  reg        [1:0]    _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_3;
  wire       [1:0]    _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_4;
  wire                _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_5;
  reg        [31:0]   AlignerPlugin_logic_buffer_data;
  reg        [1:0]    AlignerPlugin_logic_buffer_mask;
  reg        [1:0]    AlignerPlugin_logic_buffer_last;
  reg        [31:0]   AlignerPlugin_logic_buffer_pc;
  reg                 AlignerPlugin_logic_buffer_trap;
  reg        [9:0]    AlignerPlugin_logic_buffer_hm_Fetch_ID;
  wire       [63:0]   _zz_AlignerPlugin_logic_slices_data_0;
  wire                when_AlignerPlugin_l240;
  wire                when_AlignerPlugin_l241;
  wire                AlignerPlugin_logic_buffer_downFire;
  wire       [3:0]    AlignerPlugin_logic_buffer_usedMask;
  wire                AlignerPlugin_logic_buffer_haltUp;
  wire                when_AlignerPlugin_l256;
  wire       [31:0]   LsuCachelessPlugin_logic_onPma_port_cmd_address;
  wire       [1:0]    LsuCachelessPlugin_logic_onPma_port_cmd_size;
  reg        [1:0]    LsuCachelessPlugin_logic_onPma_port_cmd_op;
  wire                LsuCachelessPlugin_logic_onPma_port_rsp_fault;
  wire                LsuCachelessPlugin_logic_onPma_port_rsp_io;
  wire                LsuCachelessPlugin_logic_cmdInflights;
  reg                 LsuCachelessPlugin_logic_onFork_skip;
  wire                when_LsuCachelessPlugin_l284;
  reg                 LsuCachelessPlugin_logic_onFork_askFenceReg;
  wire                LsuCachelessPlugin_logic_onFork_askFence;
  wire                LsuCachelessPlugin_logic_onFork_doFence;
  wire                LsuCachelessPlugin_logic_bus_cmd_fire;
  reg                 LsuCachelessPlugin_logic_onFork_cmdCounter_willIncrement;
  wire                LsuCachelessPlugin_logic_onFork_cmdCounter_willDecrement;
  wire                LsuCachelessPlugin_logic_onFork_cmdCounter_willClear;
  wire                LsuCachelessPlugin_logic_onFork_cmdCounter_willLoad;
  reg        [0:0]    LsuCachelessPlugin_logic_onFork_cmdCounter_valueNext;
  reg        [0:0]    LsuCachelessPlugin_logic_onFork_cmdCounter_value;
  wire                LsuCachelessPlugin_logic_onFork_cmdCounter_willOverflowIfInc;
  wire                LsuCachelessPlugin_logic_onFork_cmdCounter_willUnderflowIfDec;
  wire                LsuCachelessPlugin_logic_onFork_cmdCounter_willOverflow;
  wire                LsuCachelessPlugin_logic_onFork_cmdCounter_willUnderflow;
  reg                 LsuCachelessPlugin_logic_onFork_cmdSent;
  wire                when_LsuCachelessPlugin_l289;
  reg                 LsuCachelessPlugin_logic_bus_cmd_valid_regNext;
  reg                 LsuCachelessPlugin_logic_bus_cmd_ready_regNext;
  wire                LsuCachelessPlugin_logic_bus_cmd_isStall;
  reg                 LsuCachelessPlugin_logic_bus_cmd_isStall_regNext;
  reg        [0:0]    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_id;
  reg                 LsuCachelessPlugin_logic_bus_cmd_payload_regNext_write;
  reg        [31:0]   LsuCachelessPlugin_logic_bus_cmd_payload_regNext_address;
  reg        [31:0]   LsuCachelessPlugin_logic_bus_cmd_payload_regNext_data;
  reg        [1:0]    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_size;
  reg        [3:0]    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_mask;
  reg                 LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoEnable;
  reg        [4:0]    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoOp;
  reg                 LsuCachelessPlugin_logic_bus_cmd_payload_regNext_io;
  reg                 LsuCachelessPlugin_logic_bus_cmd_payload_regNext_fromHart;
  reg        [15:0]   LsuCachelessPlugin_logic_bus_cmd_payload_regNext_uopId;
  wire       [31:0]   LsuCachelessPlugin_logic_onFork_mapping_0_1;
  wire       [31:0]   LsuCachelessPlugin_logic_onFork_mapping_1_1;
  wire       [31:0]   LsuCachelessPlugin_logic_onFork_mapping_2_1;
  reg        [31:0]   _zz_LsuCachelessPlugin_logic_bus_cmd_payload_data;
  reg        [3:0]    _zz_LsuCachelessPlugin_logic_bus_cmd_payload_mask;
  wire                LsuCachelessPlugin_logic_onFork_freezeIt;
  wire       [2:0]    early0_EnvPlugin_logic_exe_privilege;
  wire                early0_EnvPlugin_logic_exe_isGuest;
  wire       [1:0]    _zz_early0_EnvPlugin_logic_exe_xretPriv;
  reg        [2:0]    early0_EnvPlugin_logic_exe_xretPriv;
  reg                 early0_EnvPlugin_logic_exe_commit;
  wire                early0_EnvPlugin_logic_exe_retKo;
  wire                early0_EnvPlugin_logic_exe_wfiMapping_0;
  wire                early0_EnvPlugin_logic_exe_wfiMapping_1;
  wire                early0_EnvPlugin_logic_exe_vmaKoMapping_0;
  wire                early0_EnvPlugin_logic_exe_vmaKo;
  wire                early0_EnvPlugin_logic_exe_gvmaKo;
  wire                early0_EnvPlugin_logic_exe_vvmaKo;
  wire                when_EnvPlugin_l131;
  wire                when_EnvPlugin_l145;
  wire                when_EnvPlugin_l203;
  wire                when_EnvPlugin_l207;
  reg                 PerformanceCounterPlugin_logic_readPort_valid;
  wire                PerformanceCounterPlugin_logic_readPort_ready;
  wire       [2:0]    PerformanceCounterPlugin_logic_readPort_address;
  wire       [31:0]   PerformanceCounterPlugin_logic_readPort_data;
  reg                 PerformanceCounterPlugin_logic_writePort_valid;
  wire                PerformanceCounterPlugin_logic_writePort_ready;
  wire       [2:0]    PerformanceCounterPlugin_logic_writePort_address;
  reg        [31:0]   PerformanceCounterPlugin_logic_writePort_data;
  wire                early0_BranchPlugin_logic_alu_expectedMsb;
  wire       [2:0]    switch_Misc_l245;
  reg                 _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0;
  reg                 _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0_1;
  wire                early0_BranchPlugin_logic_jumpLogic_needFix;
  wire                early0_BranchPlugin_logic_jumpLogic_doIt;
  wire                early0_BranchPlugin_logic_jumpLogic_rdLink;
  wire                early0_BranchPlugin_logic_jumpLogic_rs1Link;
  wire                early0_BranchPlugin_logic_jumpLogic_rdEquRs1;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_valid;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_ready;
  wire       [31:0]   early0_BranchPlugin_logic_jumpLogic_learn_payload_pcOnLastSlice;
  wire       [31:0]   early0_BranchPlugin_logic_jumpLogic_learn_payload_pcTarget;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_payload_taken;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_payload_isBranch;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_payload_isPush;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_payload_isPop;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_payload_wasWrong;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_payload_badPredictedTarget;
  wire       [15:0]   early0_BranchPlugin_logic_jumpLogic_learn_payload_uopId;
  wire                CsrRamPlugin_setup_initPort_valid;
  wire                CsrRamPlugin_setup_initPort_ready;
  wire       [2:0]    CsrRamPlugin_setup_initPort_address;
  wire       [31:0]   CsrRamPlugin_setup_initPort_data;
  wire                LearnPlugin_logic_learn_valid;
  wire       [31:0]   LearnPlugin_logic_learn_payload_pcOnLastSlice;
  wire       [31:0]   LearnPlugin_logic_learn_payload_pcTarget;
  wire                LearnPlugin_logic_learn_payload_taken;
  wire                LearnPlugin_logic_learn_payload_isBranch;
  wire                LearnPlugin_logic_learn_payload_isPush;
  wire                LearnPlugin_logic_learn_payload_isPop;
  wire                LearnPlugin_logic_learn_payload_wasWrong;
  wire                LearnPlugin_logic_learn_payload_badPredictedTarget;
  wire       [15:0]   LearnPlugin_logic_learn_payload_uopId;
  wire                LearnPlugin_logic_buffered_0_valid;
  wire                LearnPlugin_logic_buffered_0_ready;
  wire       [31:0]   LearnPlugin_logic_buffered_0_payload_pcOnLastSlice;
  wire       [31:0]   LearnPlugin_logic_buffered_0_payload_pcTarget;
  wire                LearnPlugin_logic_buffered_0_payload_taken;
  wire                LearnPlugin_logic_buffered_0_payload_isBranch;
  wire                LearnPlugin_logic_buffered_0_payload_isPush;
  wire                LearnPlugin_logic_buffered_0_payload_isPop;
  wire                LearnPlugin_logic_buffered_0_payload_wasWrong;
  wire                LearnPlugin_logic_buffered_0_payload_badPredictedTarget;
  wire       [15:0]   LearnPlugin_logic_buffered_0_payload_uopId;
  wire                LearnPlugin_logic_arbitrated_valid;
  wire                LearnPlugin_logic_arbitrated_ready;
  wire       [31:0]   LearnPlugin_logic_arbitrated_payload_pcOnLastSlice;
  wire       [31:0]   LearnPlugin_logic_arbitrated_payload_pcTarget;
  wire                LearnPlugin_logic_arbitrated_payload_taken;
  wire                LearnPlugin_logic_arbitrated_payload_isBranch;
  wire                LearnPlugin_logic_arbitrated_payload_isPush;
  wire                LearnPlugin_logic_arbitrated_payload_isPop;
  wire                LearnPlugin_logic_arbitrated_payload_wasWrong;
  wire                LearnPlugin_logic_arbitrated_payload_badPredictedTarget;
  wire       [15:0]   LearnPlugin_logic_arbitrated_payload_uopId;
  wire                LearnPlugin_logic_arbitrated_toFlow_valid;
  wire       [31:0]   LearnPlugin_logic_arbitrated_toFlow_payload_pcOnLastSlice;
  wire       [31:0]   LearnPlugin_logic_arbitrated_toFlow_payload_pcTarget;
  wire                LearnPlugin_logic_arbitrated_toFlow_payload_taken;
  wire                LearnPlugin_logic_arbitrated_toFlow_payload_isBranch;
  wire                LearnPlugin_logic_arbitrated_toFlow_payload_isPush;
  wire                LearnPlugin_logic_arbitrated_toFlow_payload_isPop;
  wire                LearnPlugin_logic_arbitrated_toFlow_payload_wasWrong;
  wire                LearnPlugin_logic_arbitrated_toFlow_payload_badPredictedTarget;
  wire       [15:0]   LearnPlugin_logic_arbitrated_toFlow_payload_uopId;
  reg        [15:0]   DecoderPlugin_logic_harts_0_uopId;
  wire                when_DecoderPlugin_l148;
  wire       [0:0]    DecoderPlugin_logic_interrupt_async;
  wire                when_DecoderPlugin_l156;
  reg        [0:0]    DecoderPlugin_logic_interrupt_buffered;
  wire                _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0;
  wire                DecoderPlugin_logic_laneLogic_0_interruptPending;
  reg                 DecoderPlugin_logic_laneLogic_0_trapPort_valid;
  reg                 DecoderPlugin_logic_laneLogic_0_trapPort_payload_exception;
  wire       [31:0]   DecoderPlugin_logic_laneLogic_0_trapPort_payload_tval;
  wire       [31:0]   DecoderPlugin_logic_laneLogic_0_trapPort_payload_tval2;
  reg        [4:0]    DecoderPlugin_logic_laneLogic_0_trapPort_payload_code;
  wire       [2:0]    DecoderPlugin_logic_laneLogic_0_trapPort_payload_arg;
  wire       [0:0]    DecoderPlugin_logic_laneLogic_0_trapPort_payload_laneAge;
  wire                DecoderPlugin_logic_laneLogic_0_completionPort_valid;
  wire       [15:0]   DecoderPlugin_logic_laneLogic_0_completionPort_payload_uopId;
  wire                DecoderPlugin_logic_laneLogic_0_completionPort_payload_trap;
  wire                DecoderPlugin_logic_laneLogic_0_completionPort_payload_commit;
  reg                 decode_ctrls_1_up_LANE_SEL_0_regNext;
  wire                when_CtrlLaneApi_l50_1;
  wire                when_DecoderPlugin_l245;
  wire                DecoderPlugin_logic_laneLogic_0_flushPort_valid;
  wire       [15:0]   DecoderPlugin_logic_laneLogic_0_flushPort_payload_uopId;
  wire                DecoderPlugin_logic_laneLogic_0_flushPort_payload_self;
  wire                when_DecoderPlugin_l263;
  wire       [15:0]   DecoderPlugin_logic_laneLogic_0_uopIdBase;
  wire                DispatchPlugin_logic_candidates_0_ctx_valid;
  reg        [0:0]    DispatchPlugin_logic_candidates_0_ctx_laneLayerHits;
  wire       [31:0]   DispatchPlugin_logic_candidates_0_ctx_uop;
  wire                DispatchPlugin_logic_candidates_0_ctx_uopIsCompressed;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_FENCE_OLDER;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_MAY_FLUSH;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_DONT_FLUSH;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_DONT_FLUSH_FROM_LANES;
  wire       [0:0]    DispatchPlugin_logic_candidates_0_ctx_hm_Decode_INSTRUCTION_SLICE_COUNT;
  wire       [31:0]   DispatchPlugin_logic_candidates_0_ctx_hm_PC;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_TRAP;
  wire       [15:0]   DispatchPlugin_logic_candidates_0_ctx_hm_Decode_UOP_ID;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_RS1_ENABLE;
  wire       [4:0]    DispatchPlugin_logic_candidates_0_ctx_hm_RS1_PHYS;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_RS2_ENABLE;
  wire       [4:0]    DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_RD_ENABLE;
  wire       [4:0]    DispatchPlugin_logic_candidates_0_ctx_hm_RD_PHYS;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0;
  wire                DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0;
  wire                DispatchPlugin_logic_candidates_0_fire;
  wire                DispatchPlugin_logic_candidates_0_cancel;
  wire       [0:0]    DispatchPlugin_logic_candidates_0_rsHazards;
  wire       [0:0]    DispatchPlugin_logic_candidates_0_reservationHazards;
  wire                DispatchPlugin_logic_candidates_0_flushHazards;
  wire                DispatchPlugin_logic_candidates_0_fenceOlderHazards;
  wire                DispatchPlugin_logic_candidates_0_moving;
  wire                DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard;
  wire                DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard;
  wire                DispatchPlugin_logic_reservationChecker_0_onLl_0_hit;
  wire                DispatchPlugin_logic_flushChecker_0_oldersHazard;
  wire       [0:0]    DispatchPlugin_logic_fenceChecker_olderInflights;
  wire                DispatchPlugin_logic_feeds_0_sending;
  reg                 DispatchPlugin_logic_feeds_0_sent;
  wire                when_DispatchPlugin_l375;
  wire       [0:0]    DispatchPlugin_logic_scheduler_eusFree_0;
  wire       [0:0]    DispatchPlugin_logic_scheduler_eusFree_1;
  wire       [0:0]    DispatchPlugin_logic_scheduler_hartFree_0;
  wire       [0:0]    DispatchPlugin_logic_scheduler_hartFree_1;
  wire                DispatchPlugin_logic_scheduler_arbiters_0_candHazard;
  wire       [0:0]    DispatchPlugin_logic_scheduler_arbiters_0_layersHits;
  wire                DispatchPlugin_logic_scheduler_arbiters_0_layersHits_bools_0;
  wire       [0:0]    _zz_DispatchPlugin_logic_scheduler_arbiters_0_layerOh;
  wire       [0:0]    DispatchPlugin_logic_scheduler_arbiters_0_layerOh;
  wire       [0:0]    DispatchPlugin_logic_scheduler_arbiters_0_eusOh;
  wire                DispatchPlugin_logic_scheduler_arbiters_0_doIt;
  wire       [0:0]    DispatchPlugin_logic_inserter_0_oh;
  wire                DispatchPlugin_logic_inserter_0_trap;
  wire                when_DispatchPlugin_l447;
  wire       [0:0]    DispatchPlugin_logic_inserter_0_layerOhUnfiltred;
  wire                DispatchPlugin_logic_inserter_0_layer_0_1;
  wire                DispatchPlugin_logic_events_frontendStall;
  wire                DispatchPlugin_logic_events_backendStall;
  wire                when_LsuCachelessPlugin_l331;
  wire                when_LsuCachelessPlugin_l337;
  wire       [1:0]    switch_Misc_l245_1;
  reg        [1:0]    _zz_LsuCachelessPlugin_logic_trapPort_payload_arg;
  wire                when_LsuCachelessPlugin_l420;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_0_valid;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_0_inflight;
  reg        [31:0]   LsuCachelessPlugin_logic_onJoin_buffers_0_address;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_0_payload_error;
  reg        [31:0]   LsuCachelessPlugin_logic_onJoin_buffers_0_payload_data;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_0_payload_scMiss;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_1_valid;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_1_inflight;
  reg        [31:0]   LsuCachelessPlugin_logic_onJoin_buffers_1_address;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_1_payload_error;
  reg        [31:0]   LsuCachelessPlugin_logic_onJoin_buffers_1_payload_data;
  reg                 LsuCachelessPlugin_logic_onJoin_buffers_1_payload_scMiss;
  wire                LsuCachelessPlugin_logic_onJoin_busRspWithoutId_error;
  wire       [31:0]   LsuCachelessPlugin_logic_onJoin_busRspWithoutId_data;
  wire                LsuCachelessPlugin_logic_onJoin_busRspWithoutId_scMiss;
  wire                LsuCachelessPlugin_logic_onJoin_pop;
  reg                 LsuCachelessPlugin_logic_onJoin_rspCounter_willIncrement;
  wire                LsuCachelessPlugin_logic_onJoin_rspCounter_willDecrement;
  wire                LsuCachelessPlugin_logic_onJoin_rspCounter_willClear;
  wire                LsuCachelessPlugin_logic_onJoin_rspCounter_willLoad;
  reg        [0:0]    LsuCachelessPlugin_logic_onJoin_rspCounter_valueNext;
  reg        [0:0]    LsuCachelessPlugin_logic_onJoin_rspCounter_value;
  wire                LsuCachelessPlugin_logic_onJoin_rspCounter_willOverflowIfInc;
  wire                LsuCachelessPlugin_logic_onJoin_rspCounter_willUnderflowIfDec;
  wire                LsuCachelessPlugin_logic_onJoin_rspCounter_willOverflow;
  wire                LsuCachelessPlugin_logic_onJoin_rspCounter_willUnderflow;
  wire                LsuCachelessPlugin_logic_onJoin_readerValid;
  wire                LsuCachelessPlugin_logic_onJoin_busRspHit;
  wire                LsuCachelessPlugin_logic_onJoin_rspValid;
  wire                LsuCachelessPlugin_logic_onJoin_rspPayload_error;
  wire       [31:0]   LsuCachelessPlugin_logic_onJoin_rspPayload_data;
  wire                LsuCachelessPlugin_logic_onJoin_rspPayload_scMiss;
  wire       [31:0]   LsuCachelessPlugin_logic_onJoin_rspAddress;
  wire       [7:0]    LsuCachelessPlugin_logic_onWb_rspSplits_0;
  wire       [7:0]    LsuCachelessPlugin_logic_onWb_rspSplits_1;
  wire       [7:0]    LsuCachelessPlugin_logic_onWb_rspSplits_2;
  wire       [7:0]    LsuCachelessPlugin_logic_onWb_rspSplits_3;
  reg        [31:0]   LsuCachelessPlugin_logic_onWb_rspShifted;
  wire                when_LsuCachelessPlugin_l531;
  wire                CsrRamPlugin_csrMapper_read_valid;
  wire                CsrRamPlugin_csrMapper_read_ready;
  wire       [2:0]    CsrRamPlugin_csrMapper_read_address;
  wire       [31:0]   CsrRamPlugin_csrMapper_read_data;
  wire                CsrRamPlugin_csrMapper_write_valid;
  wire                CsrRamPlugin_csrMapper_write_ready;
  wire       [2:0]    CsrRamPlugin_csrMapper_write_address;
  wire       [31:0]   CsrRamPlugin_csrMapper_write_data;
  wire       [1:0]    lane0_integer_WriteBackPlugin_logic_stages_0_hits;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_0_muxed;
  wire                lane0_integer_WriteBackPlugin_logic_stages_0_write_valid;
  wire       [15:0]   lane0_integer_WriteBackPlugin_logic_stages_0_write_payload_uopId;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_0_write_payload_data;
  wire       [0:0]    lane0_integer_WriteBackPlugin_logic_stages_1_hits;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_1_muxed;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_1_merged;
  wire                lane0_integer_WriteBackPlugin_logic_stages_1_write_valid;
  wire       [15:0]   lane0_integer_WriteBackPlugin_logic_stages_1_write_payload_uopId;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_1_write_payload_data;
  wire       [0:0]    lane0_integer_WriteBackPlugin_logic_stages_2_hits;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_2_muxed;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_2_merged;
  wire                lane0_integer_WriteBackPlugin_logic_stages_2_write_valid;
  wire       [15:0]   lane0_integer_WriteBackPlugin_logic_stages_2_write_payload_uopId;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_stages_2_write_payload_data;
  wire                lane0_integer_WriteBackPlugin_logic_write_port_valid;
  wire       [4:0]    lane0_integer_WriteBackPlugin_logic_write_port_address;
  wire       [31:0]   lane0_integer_WriteBackPlugin_logic_write_port_data;
  wire       [15:0]   lane0_integer_WriteBackPlugin_logic_write_port_uopId;
  wire                _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0;
  wire                _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0;
  wire                _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_1;
  reg                 PerformanceCounterPlugin_logic_interrupt_ip;
  reg                 PerformanceCounterPlugin_logic_interrupt_ie;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_0;
  wire       [0:0]    PerformanceCounterPlugin_logic_events_sums_0;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_1;
  wire       [0:0]    PerformanceCounterPlugin_logic_events_sums_1;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_2;
  wire       [0:0]    PerformanceCounterPlugin_logic_events_sums_2;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_3;
  wire       [0:0]    PerformanceCounterPlugin_logic_events_sums_3;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_4;
  wire       [0:0]    PerformanceCounterPlugin_logic_events_sums_4;
  reg        [0:0]    _zz_PerformanceCounterPlugin_logic_events_sums_5;
  wire       [0:0]    PerformanceCounterPlugin_logic_events_sums_5;
  wire                PerformanceCounterPlugin_logic_fsm_wantExit;
  reg                 PerformanceCounterPlugin_logic_fsm_wantStart;
  wire                PerformanceCounterPlugin_logic_fsm_wantKill;
  wire                PerformanceCounterPlugin_logic_fsm_flusherCmd_valid;
  reg                 PerformanceCounterPlugin_logic_fsm_flusherCmd_ready;
  wire       [1:0]    PerformanceCounterPlugin_logic_fsm_flusherCmd_payload_oh;
  reg                 PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid;
  reg                 PerformanceCounterPlugin_logic_fsm_csrWriteCmd_ready;
  wire       [1:0]    PerformanceCounterPlugin_logic_fsm_csrWriteCmd_payload_address;
  wire                PerformanceCounterPlugin_logic_fsm_csrReadCmd_valid;
  reg                 PerformanceCounterPlugin_logic_fsm_csrReadCmd_ready;
  wire       [1:0]    PerformanceCounterPlugin_logic_fsm_csrReadCmd_payload_address;
  reg                 PerformanceCounterPlugin_logic_fsm_cmd_flusher;
  reg        [1:0]    PerformanceCounterPlugin_logic_fsm_cmd_oh;
  wire                _zz_PerformanceCounterPlugin_logic_fsm_cmd_address;
  wire                _zz_PerformanceCounterPlugin_logic_fsm_cmd_address_1;
  wire       [1:0]    PerformanceCounterPlugin_logic_fsm_cmd_address;
  wire                PerformanceCounterPlugin_logic_fsm_done;
  reg        [31:0]   PerformanceCounterPlugin_logic_fsm_ramReaded;
  reg                 PerformanceCounterPlugin_logic_fsm_carry;
  reg        [7:0]    PerformanceCounterPlugin_logic_fsm_counterReaded;
  wire       [31:0]   PerformanceCounterPlugin_logic_fsm_calc_a;
  wire       [7:0]    PerformanceCounterPlugin_logic_fsm_calc_b;
  wire       [32:0]   PerformanceCounterPlugin_logic_fsm_calc_sum;
  wire       [1:0]    PerformanceCounterPlugin_logic_fsm_idleCsrAddress;
  reg                 PerformanceCounterPlugin_logic_fsm_holdCsrWrite;
  wire       [1:0]    PerformanceCounterPlugin_logic_flusher_hits;
  wire                PerformanceCounterPlugin_logic_flusher_hit;
  wire       [1:0]    PerformanceCounterPlugin_logic_flusher_hits_ohFirst_input;
  wire       [1:0]    PerformanceCounterPlugin_logic_flusher_hits_ohFirst_masked;
  wire       [1:0]    PerformanceCounterPlugin_logic_flusher_oh;
  wire       [1:0]    PerformanceCounterPlugin_logic_csrDecode_addr;
  reg                 PerformanceCounterPlugin_logic_csrDecode_mok;
  wire                PerformanceCounterPlugin_logic_csrDecode_sok;
  wire                PerformanceCounterPlugin_logic_csrDecode_vok;
  wire       [2:0]    PerformanceCounterPlugin_logic_csrDecode_privilege;
  wire                PerformanceCounterPlugin_logic_csrDecode_privOk;
  wire                PerformanceCounterPlugin_logic_csrDecode_hyperOK;
  reg                 PerformanceCounterPlugin_logic_csrRead_fired;
  wire                PerformanceCounterPlugin_logic_fsm_csrReadCmd_fire;
  wire                PerformanceCounterPlugin_logic_csrRead_requested;
  wire                when_PerformanceCounterPlugin_l363;
  reg                 PerformanceCounterPlugin_logic_csrWrite_fired;
  wire                PerformanceCounterPlugin_logic_fsm_csrWriteCmd_fire;
  wire       [31:0]   LsuCachelessPlugin_pmaBuilder_addressBits;
  wire       [3:0]    LsuCachelessPlugin_pmaBuilder_argsBits;
  wire                _zz_LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit;
  wire                LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit;
  wire                LsuCachelessPlugin_pmaBuilder_onTransfers_0_argsHit;
  wire                LsuCachelessPlugin_pmaBuilder_onTransfers_0_hit;
  wire                LsuCachelessPlugin_pmaBuilder_loadExecute_needCheck;
  wire                LsuCachelessPlugin_pmaBuilder_loadExecute_fault;
  wire                _zz_LsuCachelessPlugin_logic_onPma_port_rsp_fault;
  wire       [2:0]    CsrRamPlugin_csrMapper_ramAddress;
  wire       [11:0]   _zz_CsrRamPlugin_csrMapper_ramAddress;
  reg                 CsrRamPlugin_csrMapper_withRead;
  wire                when_CsrRamPlugin_l85;
  reg                 CsrRamPlugin_csrMapper_doWrite;
  reg                 CsrRamPlugin_csrMapper_fired;
  wire                when_CsrRamPlugin_l92;
  wire                when_CsrRamPlugin_l96;
  wire                TrapPlugin_logic_initHold;
  reg                 decode_ctrls_1_up_LANE_SEL_0_regNext_1;
  wire                when_CtrlLaneApi_l50_2;
  wire                WhiteboxerPlugin_logic_serializeds_0_fire;
  wire       [9:0]    WhiteboxerPlugin_logic_serializeds_0_decodeId;
  wire       [15:0]   WhiteboxerPlugin_logic_serializeds_0_microOpId;
  wire       [31:0]   WhiteboxerPlugin_logic_serializeds_0_microOp;
  reg                 execute_ctrl0_down_LANE_SEL_lane0_regNext;
  wire                when_CtrlLaneApi_l50_3;
  wire                WhiteboxerPlugin_logic_dispatches_0_fire;
  wire       [15:0]   WhiteboxerPlugin_logic_dispatches_0_microOpId;
  reg                 execute_ctrl2_down_LANE_SEL_lane0_regNext;
  wire                when_CtrlLaneApi_l50_4;
  wire                WhiteboxerPlugin_logic_executes_0_fire;
  wire       [15:0]   WhiteboxerPlugin_logic_executes_0_microOpId;
  wire                WhiteboxerPlugin_logic_csr_access_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_csr_access_payload_uopId;
  wire       [11:0]   WhiteboxerPlugin_logic_csr_access_payload_address;
  wire       [31:0]   WhiteboxerPlugin_logic_csr_access_payload_write;
  wire       [31:0]   WhiteboxerPlugin_logic_csr_access_payload_read;
  wire                WhiteboxerPlugin_logic_csr_access_payload_writeDone;
  wire                WhiteboxerPlugin_logic_csr_access_payload_readDone;
  reg                 TrapPlugin_logic_harts_0_crsPorts_read_valid;
  wire                TrapPlugin_logic_harts_0_crsPorts_read_ready;
  reg        [2:0]    TrapPlugin_logic_harts_0_crsPorts_read_address;
  wire       [31:0]   TrapPlugin_logic_harts_0_crsPorts_read_data;
  wire                AlignerPlugin_logic_buffer_flushIt;
  wire                AlignerPlugin_logic_buffer_readers_0_firstFromBuffer;
  wire                AlignerPlugin_logic_buffer_readers_0_lastFromBuffer;
  wire       [3:0]    _zz_AlignerPlugin_logic_extractors_0_ctx_instruction;
  wire                _zz_AlignerPlugin_logic_extractors_0_ctx_pc;
  wire                decode_logic_flushes_0_onLanes_0_doIt;
  wire                decode_logic_flushes_1_onLanes_0_doIt;
  reg                 TrapPlugin_logic_harts_0_crsPorts_write_valid;
  wire                TrapPlugin_logic_harts_0_crsPorts_write_ready;
  reg        [2:0]    TrapPlugin_logic_harts_0_crsPorts_write_address;
  reg        [31:0]   TrapPlugin_logic_harts_0_crsPorts_write_data;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_id;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_priority;
  wire       [2:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_privilege;
  wire                TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_id;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_priority;
  wire       [2:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_privilege;
  wire                TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_id;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_priority;
  wire       [2:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_privilege;
  wire                TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_id;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_priority;
  wire       [2:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_privilege;
  wire                TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id;
  wire       [4:0]    _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority;
  wire       [2:0]    _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_1;
  wire       [4:0]    _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority_1;
  wire       [2:0]    _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege_1;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid_1;
  wire                _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_2;
  reg        [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id;
  reg        [4:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority;
  reg        [2:0]    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege;
  reg                 TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_xtopi_0_int;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_result_id;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_result_priority;
  wire       [2:0]    TrapPlugin_logic_harts_0_interrupt_result_privilege;
  wire                TrapPlugin_logic_harts_0_interrupt_result_valid;
  wire                TrapPlugin_logic_harts_0_interrupt_valid;
  wire       [4:0]    TrapPlugin_logic_harts_0_interrupt_code;
  wire       [2:0]    TrapPlugin_logic_harts_0_interrupt_targetPrivilege;
  reg                 TrapPlugin_logic_harts_0_interrupt_validBuffer;
  wire                TrapPlugin_logic_harts_0_interrupt_pendingInterrupt;
  wire                when_TrapPlugin_l288;
  wire                _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid;
  wire                _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_valid;
  wire                _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_1;
  wire                _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_2;
  wire                _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_valid;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_tval;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_tval2;
  wire       [4:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_code;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_arg;
  wire       [2:0]    _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception;
  wire       [72:0]   _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_valid;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_exception;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_tval;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_tval2;
  wire       [4:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_code;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_arg;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_valid;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_exception;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_tval;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_tval2;
  wire       [4:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_code;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_arg;
  wire       [2:0]    _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh;
  wire                _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_1;
  wire                _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_2;
  reg        [2:0]    _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_3;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_oh;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_down_valid;
  wire                TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_tval;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_tval2;
  wire       [4:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_code;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_arg;
  wire       [72:0]   _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception;
  reg                 TrapPlugin_logic_harts_0_trap_pending_state_exception;
  reg        [31:0]   TrapPlugin_logic_harts_0_trap_pending_state_tval;
  reg        [31:0]   TrapPlugin_logic_harts_0_trap_pending_state_tval2;
  reg        [4:0]    TrapPlugin_logic_harts_0_trap_pending_state_code;
  reg        [2:0]    TrapPlugin_logic_harts_0_trap_pending_state_arg;
  reg        [31:0]   TrapPlugin_logic_harts_0_trap_pending_pc;
  reg        [1:0]    TrapPlugin_logic_harts_0_trap_pending_slices;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_pending_xret_sourcePrivilege;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilegeMapping_0;
  reg        [2:0]    TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilege;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_exception_exceptionTargetPrivilegeUncapped;
  wire       [4:0]    TrapPlugin_logic_harts_0_trap_exception_code;
  wire       [2:0]    _zz_TrapPlugin_logic_harts_0_trap_exception_targetPrivilege;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_exception_targetPrivilege;
  wire                execute_lane0_ctrls_5_upIsCancel;
  wire                execute_lane0_ctrls_5_downIsCancel;
  wire       [0:0]    TrapPlugin_logic_harts_0_trap_trigger_oh;
  wire                TrapPlugin_logic_harts_0_trap_trigger_valid;
  reg                 TrapPlugin_logic_harts_0_trap_whitebox_trap;
  reg                 TrapPlugin_logic_harts_0_trap_whitebox_interrupt;
  reg        [4:0]    TrapPlugin_logic_harts_0_trap_whitebox_code;
  reg                 TrapPlugin_logic_harts_0_trap_pcPort_valid;
  wire                TrapPlugin_logic_harts_0_trap_pcPort_payload_fault;
  reg        [31:0]   TrapPlugin_logic_harts_0_trap_pcPort_payload_pc;
  wire                PcPlugin_logic_harts_0_aggregator_sortedByPriority_0_laneValid;
  wire                TrapPlugin_logic_harts_0_trap_fsm_wantExit;
  reg                 TrapPlugin_logic_harts_0_trap_fsm_wantStart;
  wire                TrapPlugin_logic_harts_0_trap_fsm_wantKill;
  wire                TrapPlugin_logic_harts_0_trap_fsm_inflightTrap;
  wire                TrapPlugin_logic_harts_0_trap_fsm_holdPort;
  reg                 TrapPlugin_logic_harts_0_trap_fsm_wfi;
  reg                 TrapPlugin_logic_harts_0_trap_fsm_buffer_sampleIt;
  reg                 TrapPlugin_logic_harts_0_trap_fsm_buffer_i_valid;
  reg        [4:0]    TrapPlugin_logic_harts_0_trap_fsm_buffer_i_code;
  reg        [2:0]    TrapPlugin_logic_harts_0_trap_fsm_buffer_i_targetPrivilege;
  wire                TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege;
  wire       [31:0]   TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_tval;
  wire       [4:0]    TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_code;
  wire                TrapPlugin_logic_harts_0_trap_fsm_resetToRunConditions_0;
  reg        [31:0]   TrapPlugin_logic_harts_0_trap_fsm_jumpTarget;
  wire       [1:0]    TrapPlugin_logic_harts_0_trap_fsm_jumpOffset;
  wire                TrapPlugin_logic_harts_0_trap_fsm_writeGVA;
  reg                 TrapPlugin_logic_harts_0_trap_fsm_trapEnterDebug;
  wire                TrapPlugin_logic_harts_0_trap_fsm_triggerEbreak;
  reg                 TrapPlugin_logic_harts_0_trap_fsm_triggerEbreakReg;
  wire                TrapPlugin_logic_harts_0_trap_fsm_isGuestTarget;
  wire                when_TrapPlugin_l841;
  reg        [31:0]   TrapPlugin_logic_harts_0_trap_fsm_readed;
  wire       [2:0]    TrapPlugin_logic_harts_0_trap_fsm_xretPrivilege;
  wire                PcPlugin_logic_forcedSpawn;
  reg        [9:0]    PcPlugin_logic_harts_0_self_id;
  wire                PcPlugin_logic_harts_0_self_flow_valid;
  wire                PcPlugin_logic_harts_0_self_flow_payload_fault;
  wire       [31:0]   PcPlugin_logic_harts_0_self_flow_payload_pc;
  wire                PcPlugin_logic_harts_0_aggregator_sortedByPriority_2_laneValid;
  reg                 PcPlugin_logic_harts_0_self_increment;
  reg                 PcPlugin_logic_harts_0_self_fault;
  reg        [31:0]   PcPlugin_logic_harts_0_self_state;
  wire       [31:0]   PcPlugin_logic_harts_0_self_pc;
  wire                PcPlugin_logic_harts_0_aggregator_valids_0;
  wire                PcPlugin_logic_harts_0_aggregator_valids_1;
  wire                PcPlugin_logic_harts_0_aggregator_valids_2;
  wire       [2:0]    _zz_PcPlugin_logic_harts_0_aggregator_oh;
  wire                _zz_PcPlugin_logic_harts_0_aggregator_oh_1;
  wire                _zz_PcPlugin_logic_harts_0_aggregator_oh_2;
  reg        [2:0]    _zz_PcPlugin_logic_harts_0_aggregator_oh_3;
  wire       [2:0]    PcPlugin_logic_harts_0_aggregator_oh;
  wire       [31:0]   PcPlugin_logic_harts_0_aggregator_target;
  wire                PcPlugin_logic_harts_0_aggregator_fault;
  wire                _zz_PcPlugin_logic_harts_0_aggregator_target;
  wire                _zz_PcPlugin_logic_harts_0_aggregator_target_1;
  wire                _zz_PcPlugin_logic_harts_0_aggregator_target_2;
  wire                PcPlugin_logic_harts_0_holdComb;
  reg                 PcPlugin_logic_harts_0_holdReg;
  wire                PcPlugin_logic_harts_0_output_valid;
  wire                PcPlugin_logic_harts_0_output_ready;
  reg        [31:0]   PcPlugin_logic_harts_0_output_payload_pc;
  wire                PcPlugin_logic_harts_0_output_payload_fault;
  wire                PcPlugin_logic_harts_0_output_fire;
  wire                PcPlugin_logic_holdHalter_doIt;
  wire                fetch_logic_ctrls_0_haltRequest_PcPlugin_l133;
  wire                CsrAccessPlugin_logic_fsm_wantExit;
  reg                 CsrAccessPlugin_logic_fsm_wantStart;
  wire                CsrAccessPlugin_logic_fsm_wantKill;
  reg                 REG_CSR_1952;
  reg                 REG_CSR_1953;
  reg                 REG_CSR_1954;
  reg                 REG_CSR_3857;
  reg                 REG_CSR_3858;
  reg                 REG_CSR_3859;
  reg                 REG_CSR_3860;
  reg                 REG_CSR_769;
  reg                 REG_CSR_768;
  reg                 REG_CSR_834;
  reg                 REG_CSR_836;
  reg                 REG_CSR_772;
  reg                 REG_CSR_4016;
  reg                 REG_CSR_794;
  reg                 REG_CSR_778;
  reg                 REG_CSR_774;
  reg                 REG_CSR_3073;
  reg                 REG_CSR_3201;
  reg                 REG_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_tvecFilter;
  reg                 REG_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_epcFilter;
  reg                 REG_CSR_800;
  reg                 REG_CSR_UNAMED_0;
  reg                 REG_CSR_PerformanceCounterPlugin_logic_csrFilter;
  reg                 REG_CSR_CsrRamPlugin_csrMapper_selFilter;
  reg                 REG_CSR_CsrAccessPlugin_logic_trapNextOnWriteFilter;
  reg                 CsrAccessPlugin_logic_fsm_interface_read;
  reg                 CsrAccessPlugin_logic_fsm_interface_write;
  wire       [31:0]   CsrAccessPlugin_logic_fsm_interface_rs1;
  reg        [31:0]   CsrAccessPlugin_logic_fsm_interface_aluInput;
  reg        [31:0]   CsrAccessPlugin_logic_fsm_interface_csrValue;
  reg        [31:0]   CsrAccessPlugin_logic_fsm_interface_onWriteBits;
  wire       [15:0]   CsrAccessPlugin_logic_fsm_interface_uopId;
  wire       [31:0]   CsrAccessPlugin_logic_fsm_interface_uop;
  wire                CsrAccessPlugin_logic_fsm_interface_doImm;
  wire                CsrAccessPlugin_logic_fsm_interface_doMask;
  wire                CsrAccessPlugin_logic_fsm_interface_doClear;
  wire       [4:0]    CsrAccessPlugin_logic_fsm_interface_rdPhys;
  wire                CsrAccessPlugin_logic_fsm_interface_rdEnable;
  reg                 CsrAccessPlugin_logic_fsm_interface_fire;
  reg        [11:0]   CsrAccessPlugin_logic_fsm_interface_csrId;
  wire       [11:0]   CsrAccessPlugin_logic_fsm_inject_csrAddress;
  wire       [1:0]    CsrAccessPlugin_logic_fsm_inject_csrPriv;
  wire                CsrAccessPlugin_logic_fsm_inject_immZero;
  wire                CsrAccessPlugin_logic_fsm_inject_srcZero;
  wire                CsrAccessPlugin_logic_fsm_inject_csrWrite;
  wire                CsrAccessPlugin_logic_fsm_inject_csrRead;
  wire                COMB_CSR_1952;
  wire                COMB_CSR_1953;
  wire                COMB_CSR_1954;
  wire                COMB_CSR_3857;
  wire                COMB_CSR_3858;
  wire                COMB_CSR_3859;
  wire                COMB_CSR_3860;
  wire                COMB_CSR_769;
  wire                COMB_CSR_768;
  wire                COMB_CSR_834;
  wire                COMB_CSR_836;
  wire                COMB_CSR_772;
  wire                COMB_CSR_4016;
  wire                COMB_CSR_794;
  wire                COMB_CSR_778;
  wire                COMB_CSR_774;
  wire                COMB_CSR_3073;
  wire                COMB_CSR_3201;
  wire                COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_tvecFilter;
  wire                COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_epcFilter;
  wire                COMB_CSR_800;
  wire                COMB_CSR_UNAMED_1;
  wire                COMB_CSR_PerformanceCounterPlugin_logic_csrFilter;
  wire                COMB_CSR_CsrRamPlugin_csrMapper_selFilter;
  wire                COMB_CSR_CsrAccessPlugin_logic_trapNextOnWriteFilter;
  wire                CsrAccessPlugin_logic_fsm_inject_implemented;
  wire                CsrAccessPlugin_logic_fsm_inject_condImplemented;
  wire                CsrAccessPlugin_logic_fsm_inject_onDecodeDo;
  wire                when_CsrAccessPlugin_l174;
  wire                when_CsrService_l135;
  wire                when_CsrAccessPlugin_l174_1;
  wire                when_CsrService_l135_1;
  wire                when_CsrAccessPlugin_l174_2;
  wire                when_CsrService_l135_2;
  wire                when_CsrAccessPlugin_l174_3;
  wire                when_CsrService_l221;
  wire                when_CsrService_l222;
  wire                when_CsrAccessPlugin_l174_4;
  wire                when_CsrService_l221_1;
  wire                when_CsrService_l222_1;
  wire                when_CsrAccessPlugin_l174_5;
  wire                when_CsrService_l221_2;
  wire                when_CsrService_l222_2;
  wire                when_CsrAccessPlugin_l174_6;
  wire                when_CsrService_l221_3;
  wire                when_CsrService_l222_3;
  wire                when_CsrAccessPlugin_l174_7;
  wire                when_CsrService_l135_3;
  wire                when_CsrAccessPlugin_l174_8;
  wire                when_PerformanceCounterPlugin_l342;
  wire                when_PerformanceCounterPlugin_l346;
  wire                when_PerformanceCounterPlugin_l350;
  wire                when_CsrAccessPlugin_l174_9;
  wire                when_CsrService_l135_4;
  wire                when_CsrAccessPlugin_l174_10;
  wire                CsrAccessPlugin_logic_fsm_inject_trap;
  reg                 CsrAccessPlugin_logic_fsm_inject_unfreeze;
  wire                CsrAccessPlugin_logic_fsm_inject_freeze;
  wire                when_CsrAccessPlugin_l218;
  reg                 CsrAccessPlugin_logic_fsm_inject_flushReg;
  wire                when_CsrAccessPlugin_l222;
  reg                 CsrAccessPlugin_logic_fsm_inject_sampled;
  reg                 CsrAccessPlugin_logic_fsm_inject_trapReg;
  reg                 CsrAccessPlugin_logic_fsm_inject_busTrapReg;
  reg        [4:0]    CsrAccessPlugin_logic_fsm_inject_busTrapCodeReg;
  reg                 CsrAccessPlugin_logic_fsm_readLogic_onReadsDo;
  reg                 CsrAccessPlugin_logic_fsm_readLogic_onReadsFireDo;
  wire                when_CsrAccessPlugin_l281;
  wire       [31:0]   CsrAccessPlugin_logic_fsm_readLogic_csrValue;
  wire       [31:0]   CsrAccessPlugin_logic_fsm_writeLogic_alu_mask;
  wire       [31:0]   CsrAccessPlugin_logic_fsm_writeLogic_alu_masked;
  wire       [31:0]   CsrAccessPlugin_logic_fsm_writeLogic_alu_result;
  reg                 CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo;
  reg                 CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo;
  wire                when_CsrAccessPlugin_l375;
  wire                when_CsrAccessPlugin_l375_1;
  wire                when_CsrAccessPlugin_l375_2;
  wire                when_CsrAccessPlugin_l375_3;
  wire                when_CsrAccessPlugin_l375_4;
  wire                when_CsrAccessPlugin_l372;
  wire                when_CsrAccessPlugin_l372_1;
  wire                when_CsrAccessPlugin_l375_5;
  wire                when_CsrAccessPlugin_l372_2;
  wire                when_PerformanceCounterPlugin_l378;
  wire                when_PerformanceCounterPlugin_l380;
  wire                when_CsrAccessPlugin_l372_3;
  wire                FetchCachelessPlugin_logic_fork_forked_valid;
  wire                FetchCachelessPlugin_logic_fork_forked_ready;
  reg                 FetchCachelessPlugin_logic_fork_forked_fired;
  reg                 _zz_FetchCachelessPlugin_logic_fork_forked_valid;
  wire                fetch_logic_ctrls_0_haltRequest_CtrlLink_l112;
  wire                FetchCachelessPlugin_logic_fork_forked_fire;
  wire                _zz_FetchCachelessPlugin_logic_fork_forked_ready;
  reg                 FetchCachelessPlugin_logic_fork_halted_valid;
  wire                FetchCachelessPlugin_logic_fork_halted_ready;
  wire                FetchCachelessPlugin_logic_fork_translated_valid;
  wire                FetchCachelessPlugin_logic_fork_translated_ready;
  wire       [0:0]    FetchCachelessPlugin_logic_fork_translated_payload_id;
  reg        [31:0]   FetchCachelessPlugin_logic_fork_translated_payload_address;
  wire                FetchCachelessPlugin_logic_fork_persistent_valid;
  wire                FetchCachelessPlugin_logic_fork_persistent_ready;
  wire       [0:0]    FetchCachelessPlugin_logic_fork_persistent_payload_id;
  wire       [31:0]   FetchCachelessPlugin_logic_fork_persistent_payload_address;
  reg                 FetchCachelessPlugin_logic_fork_translated_rValidN;
  reg        [0:0]    FetchCachelessPlugin_logic_fork_translated_rData_id;
  reg        [31:0]   FetchCachelessPlugin_logic_fork_translated_rData_address;
  wire                FetchCachelessPlugin_logic_fork_translated_fire;
  reg                 FetchCachelessPlugin_logic_bus_cmd_valid_regNext;
  reg                 FetchCachelessPlugin_logic_bus_cmd_ready_regNext;
  wire                FetchCachelessPlugin_logic_bus_cmd_isStall;
  reg                 FetchCachelessPlugin_logic_bus_cmd_isStall_regNext;
  reg        [0:0]    FetchCachelessPlugin_logic_bus_cmd_payload_regNext_id;
  reg        [31:0]   FetchCachelessPlugin_logic_bus_cmd_payload_regNext_address;
  wire                when_FetchCachelessPlugin_l178;
  reg                 FetchCachelessPlugin_logic_join_haltIt;
  wire       [32:0]   _zz_FetchCachelessPlugin_logic_join_rsp_error;
  reg                 FetchCachelessPlugin_logic_join_rsp_error;
  reg        [31:0]   FetchCachelessPlugin_logic_join_rsp_word;
  wire                when_FetchCachelessPlugin_l193;
  reg                 FetchCachelessPlugin_logic_join_trapSent;
  wire                when_FetchCachelessPlugin_l213;
  wire                when_FetchCachelessPlugin_l266;
  wire                fetch_logic_ctrls_1_haltRequest_FetchCachelessPlugin_l268;
  wire       [3:0]    CsrRamPlugin_logic_writeLogic_hits;
  wire                CsrRamPlugin_logic_writeLogic_hit;
  wire       [3:0]    CsrRamPlugin_logic_writeLogic_hits_ohFirst_input;
  wire       [3:0]    CsrRamPlugin_logic_writeLogic_hits_ohFirst_masked;
  wire       [3:0]    CsrRamPlugin_logic_writeLogic_oh;
  wire                CsrRamPlugin_logic_writeLogic_port_valid;
  wire       [2:0]    CsrRamPlugin_logic_writeLogic_port_payload_address;
  wire       [31:0]   CsrRamPlugin_logic_writeLogic_port_payload_data;
  wire                _zz_PerformanceCounterPlugin_logic_writePort_ready;
  wire                _zz_TrapPlugin_logic_harts_0_crsPorts_write_ready;
  wire                _zz_CsrRamPlugin_csrMapper_write_ready;
  wire                _zz_CsrRamPlugin_setup_initPort_ready;
  wire       [2:0]    CsrRamPlugin_logic_readLogic_hits;
  wire                CsrRamPlugin_logic_readLogic_hit;
  wire       [2:0]    CsrRamPlugin_logic_readLogic_hits_ohFirst_input;
  wire       [2:0]    CsrRamPlugin_logic_readLogic_hits_ohFirst_masked;
  wire       [2:0]    CsrRamPlugin_logic_readLogic_oh;
  wire                _zz_CsrRamPlugin_logic_readLogic_sel;
  wire                _zz_CsrRamPlugin_logic_readLogic_sel_1;
  wire       [1:0]    CsrRamPlugin_logic_readLogic_sel;
  wire                CsrRamPlugin_logic_readLogic_port_cmd_valid;
  wire       [2:0]    CsrRamPlugin_logic_readLogic_port_cmd_payload;
  wire       [31:0]   CsrRamPlugin_logic_readLogic_port_rsp;
  reg        [2:0]    CsrRamPlugin_logic_readLogic_ohReg;
  reg                 CsrRamPlugin_logic_readLogic_busy;
  reg        [3:0]    CsrRamPlugin_logic_flush_counter;
  wire                CsrRamPlugin_logic_flush_done;
  wire                execute_lane0_bypasser_integer_RS1_port_valid;
  wire       [4:0]    execute_lane0_bypasser_integer_RS1_port_address;
  wire       [31:0]   execute_lane0_bypasser_integer_RS1_port_data;
  reg        [1:0]    execute_lane0_bypasser_integer_RS1_bypassEnables;
  wire       [1:0]    _zz_execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0;
  wire                execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0;
  wire                execute_lane0_bypasser_integer_RS1_bypassEnables_bools_1;
  reg        [1:0]    _zz_execute_lane0_bypasser_integer_RS1_sel;
  wire       [1:0]    execute_lane0_bypasser_integer_RS1_sel;
  wire                execute_lane0_bypasser_integer_RS2_port_valid;
  wire       [4:0]    execute_lane0_bypasser_integer_RS2_port_address;
  wire       [31:0]   execute_lane0_bypasser_integer_RS2_port_data;
  reg        [1:0]    execute_lane0_bypasser_integer_RS2_bypassEnables;
  wire       [1:0]    _zz_execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0;
  wire                execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0;
  wire                execute_lane0_bypasser_integer_RS2_bypassEnables_bools_1;
  reg        [1:0]    _zz_execute_lane0_bypasser_integer_RS2_sel;
  wire       [1:0]    execute_lane0_bypasser_integer_RS2_sel;
  wire                execute_lane0_logic_completions_onCtrl_0_port_valid;
  wire       [15:0]   execute_lane0_logic_completions_onCtrl_0_port_payload_uopId;
  wire                execute_lane0_logic_completions_onCtrl_0_port_payload_trap;
  wire                execute_lane0_logic_completions_onCtrl_0_port_payload_commit;
  wire                execute_lane0_logic_completions_onCtrl_1_port_valid;
  wire       [15:0]   execute_lane0_logic_completions_onCtrl_1_port_payload_uopId;
  wire                execute_lane0_logic_completions_onCtrl_1_port_payload_trap;
  wire                execute_lane0_logic_completions_onCtrl_1_port_payload_commit;
  wire                execute_lane0_logic_completions_onCtrl_2_port_valid;
  wire       [15:0]   execute_lane0_logic_completions_onCtrl_2_port_payload_uopId;
  wire                execute_lane0_logic_completions_onCtrl_2_port_payload_trap;
  wire                execute_lane0_logic_completions_onCtrl_2_port_payload_commit;
  wire       [31:0]   execute_lane0_logic_decoding_decodingBits;
  wire                _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_1;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_2;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_1;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_2;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_3;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_4;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_5;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_1;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_2;
  wire                _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_3;
  wire                _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0;
  wire                _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0;
  wire                _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0;
  wire                _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0;
  wire       [1:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0;
  wire       [1:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1;
  wire       [1:0]    _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2;
  wire                _zz_execute_ctrl1_down_AguPlugin_STORE_lane0;
  wire                _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0;
  wire       [1:0]    _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0;
  wire       [1:0]    _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1;
  wire       [1:0]    _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2;
  wire                _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0;
  wire                _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0;
  wire       [2:0]    _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1;
  wire       [2:0]    _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2;
  wire       [2:0]    _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3;
  wire                when_ExecuteLanePlugin_l306;
  wire                when_ExecuteLanePlugin_l306_1;
  wire                when_ExecuteLanePlugin_l306_2;
  wire                WhiteboxerPlugin_logic_csr_port_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_csr_port_payload_uopId;
  wire       [11:0]   WhiteboxerPlugin_logic_csr_port_payload_address;
  wire       [31:0]   WhiteboxerPlugin_logic_csr_port_payload_write;
  wire       [31:0]   WhiteboxerPlugin_logic_csr_port_payload_read;
  wire                WhiteboxerPlugin_logic_csr_port_payload_writeDone;
  wire                WhiteboxerPlugin_logic_csr_port_payload_readDone;
  wire                WhiteboxerPlugin_logic_rfWrites_ports_0_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_rfWrites_ports_0_payload_uopId;
  wire       [31:0]   WhiteboxerPlugin_logic_rfWrites_ports_0_payload_data;
  wire                WhiteboxerPlugin_logic_rfWrites_ports_1_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_rfWrites_ports_1_payload_uopId;
  wire       [31:0]   WhiteboxerPlugin_logic_rfWrites_ports_1_payload_data;
  wire                WhiteboxerPlugin_logic_rfWrites_ports_2_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_rfWrites_ports_2_payload_uopId;
  wire       [31:0]   WhiteboxerPlugin_logic_rfWrites_ports_2_payload_data;
  wire                WhiteboxerPlugin_logic_completions_ports_0_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_completions_ports_0_payload_uopId;
  wire                WhiteboxerPlugin_logic_completions_ports_0_payload_trap;
  wire                WhiteboxerPlugin_logic_completions_ports_0_payload_commit;
  wire                WhiteboxerPlugin_logic_completions_ports_1_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_completions_ports_1_payload_uopId;
  wire                WhiteboxerPlugin_logic_completions_ports_1_payload_trap;
  wire                WhiteboxerPlugin_logic_completions_ports_1_payload_commit;
  wire                WhiteboxerPlugin_logic_completions_ports_2_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_completions_ports_2_payload_uopId;
  wire                WhiteboxerPlugin_logic_completions_ports_2_payload_trap;
  wire                WhiteboxerPlugin_logic_completions_ports_2_payload_commit;
  wire                WhiteboxerPlugin_logic_completions_ports_3_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_completions_ports_3_payload_uopId;
  wire                WhiteboxerPlugin_logic_completions_ports_3_payload_trap;
  wire                WhiteboxerPlugin_logic_completions_ports_3_payload_commit;
  wire                WhiteboxerPlugin_logic_commits_ports_0_oh_0;
  wire                WhiteboxerPlugin_logic_commits_ports_0_valid;
  wire       [31:0]   WhiteboxerPlugin_logic_commits_ports_0_pc;
  wire       [31:0]   WhiteboxerPlugin_logic_commits_ports_0_uop;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_0_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_reschedules_flushes_0_payload_uopId;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_0_payload_self;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_1_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_reschedules_flushes_1_payload_uopId;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_1_payload_self;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_2_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_reschedules_flushes_2_payload_uopId;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_2_payload_self;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_3_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_reschedules_flushes_3_payload_uopId;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_3_payload_self;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_4_valid;
  wire       [15:0]   WhiteboxerPlugin_logic_reschedules_flushes_4_payload_uopId;
  wire                WhiteboxerPlugin_logic_reschedules_flushes_4_payload_self;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_asFlow_valid;
  wire       [31:0]   early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_pcOnLastSlice;
  wire       [31:0]   early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_pcTarget;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_taken;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isBranch;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isPush;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isPop;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_wasWrong;
  wire                early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_badPredictedTarget;
  wire       [15:0]   early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_uopId;
  wire                WhiteboxerPlugin_logic_prediction_learns_0_valid;
  wire       [31:0]   WhiteboxerPlugin_logic_prediction_learns_0_payload_pcOnLastSlice;
  wire       [31:0]   WhiteboxerPlugin_logic_prediction_learns_0_payload_pcTarget;
  wire                WhiteboxerPlugin_logic_prediction_learns_0_payload_taken;
  wire                WhiteboxerPlugin_logic_prediction_learns_0_payload_isBranch;
  wire                WhiteboxerPlugin_logic_prediction_learns_0_payload_isPush;
  wire                WhiteboxerPlugin_logic_prediction_learns_0_payload_isPop;
  wire                WhiteboxerPlugin_logic_prediction_learns_0_payload_wasWrong;
  wire                WhiteboxerPlugin_logic_prediction_learns_0_payload_badPredictedTarget;
  wire       [15:0]   WhiteboxerPlugin_logic_prediction_learns_0_payload_uopId;
  wire                WhiteboxerPlugin_logic_loadExecute_fire;
  wire       [15:0]   WhiteboxerPlugin_logic_loadExecute_uopId;
  wire       [1:0]    WhiteboxerPlugin_logic_loadExecute_size;
  wire       [31:0]   WhiteboxerPlugin_logic_loadExecute_address;
  wire       [31:0]   WhiteboxerPlugin_logic_loadExecute_data;
  wire                WhiteboxerPlugin_logic_storeCommit_fire;
  wire       [15:0]   WhiteboxerPlugin_logic_storeCommit_uopId;
  wire       [11:0]   WhiteboxerPlugin_logic_storeCommit_storeId;
  wire       [1:0]    WhiteboxerPlugin_logic_storeCommit_size;
  wire       [31:0]   WhiteboxerPlugin_logic_storeCommit_address;
  wire       [31:0]   WhiteboxerPlugin_logic_storeCommit_data;
  wire                WhiteboxerPlugin_logic_storeCommit_amo;
  wire                WhiteboxerPlugin_logic_storeConditional_fire;
  wire       [15:0]   WhiteboxerPlugin_logic_storeConditional_uopId;
  wire                WhiteboxerPlugin_logic_storeConditional_miss;
  wire                WhiteboxerPlugin_logic_storeBroadcast_fire;
  wire       [11:0]   WhiteboxerPlugin_logic_storeBroadcast_storeId;
  wire                fetch_logic_flushes_0_doIt;
  wire                fetch_logic_ctrls_1_forgetsSingleRequest_FetchPipelinePlugin_l50;
  wire                PmpPlugin_logic_isMachine;
  wire                PmpPlugin_logic_instructionShouldHit;
  wire                PmpPlugin_logic_dataShouldHit;
  wire                FetchCachelessPlugin_logic_pmpPort_logic_dataShouldHitPort;
  wire       [19:0]   FetchCachelessPlugin_logic_pmpPort_logic_torCmpAddress;
  wire                LsuCachelessPlugin_logic_pmpPort_logic_dataShouldHitPort;
  wire       [19:0]   LsuCachelessPlugin_logic_pmpPort_logic_torCmpAddress;
  wire       [31:0]   FetchCachelessPlugin_pmaBuilder_addressBits;
  wire                FetchCachelessPlugin_pmaBuilder_onTransfers_0_addressHit;
  wire                FetchCachelessPlugin_pmaBuilder_onTransfers_0_argsHit;
  wire                FetchCachelessPlugin_pmaBuilder_onTransfers_0_hit;
  wire                FetchCachelessPlugin_pmaBuilder_executeFault;
  wire                _zz_FetchCachelessPlugin_logic_onPma_port_rsp_fault;
  wire                integer_RegFilePlugin_logic_writeMerges_0_bus_valid;
  wire       [4:0]    integer_RegFilePlugin_logic_writeMerges_0_bus_address;
  wire       [31:0]   integer_RegFilePlugin_logic_writeMerges_0_bus_data;
  wire       [15:0]   integer_RegFilePlugin_logic_writeMerges_0_bus_uopId;
  reg        [5:0]    integer_RegFilePlugin_logic_initalizer_counter;
  wire                integer_RegFilePlugin_logic_initalizer_done;
  wire                when_RegFilePlugin_l132;
  wire                integer_write_0_valid /* verilator public */ ;
  wire       [4:0]    integer_write_0_address /* verilator public */ ;
  wire       [31:0]   integer_write_0_data /* verilator public */ ;
  wire       [15:0]   integer_write_0_uopId /* verilator public */ ;
  wire       [0:0]    WhiteboxerPlugin_logic_wfi;
  wire                WhiteboxerPlugin_logic_perf_executeFreezed;
  wire                WhiteboxerPlugin_logic_perf_dispatchHazards;
  wire       [0:0]    WhiteboxerPlugin_logic_perf_candidatesCount;
  wire       [0:0]    WhiteboxerPlugin_logic_perf_dispatchFeedCount;
  reg                 _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2;
  wire       [59:0]   WhiteboxerPlugin_logic_perf_executeFreezedCounter;
  reg                 _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2;
  wire       [59:0]   WhiteboxerPlugin_logic_perf_dispatchHazardsCounter;
  wire                when_Counter_l254;
  reg                 _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_2;
  wire       [59:0]   WhiteboxerPlugin_logic_perf_candidatesCountCounters_0;
  wire                when_Counter_l254_1;
  reg                 _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_2;
  wire       [59:0]   WhiteboxerPlugin_logic_perf_candidatesCountCounters_1;
  wire                when_Counter_l254_2;
  reg                 _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_2;
  wire       [59:0]   WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0;
  wire                when_Counter_l254_3;
  reg                 _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1;
  reg        [59:0]   _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_2;
  wire       [59:0]   WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1;
  wire                WhiteboxerPlugin_logic_trap_ports_0_valid;
  wire                WhiteboxerPlugin_logic_trap_ports_0_interrupt;
  wire       [4:0]    WhiteboxerPlugin_logic_trap_ports_0_cause;
  wire                fetch_logic_ctrls_1_up_forgetOne;
  wire                when_CtrlLink_l191;
  wire                when_CtrlLink_l191_1;
  wire                when_StageLink_l71;
  wire                when_DecodePipelinePlugin_l70;
  reg        [2:0]    PerformanceCounterPlugin_logic_fsm_stateReg;
  reg        [2:0]    PerformanceCounterPlugin_logic_fsm_stateNext;
  wire                when_PerformanceCounterPlugin_l283;
  wire                when_PerformanceCounterPlugin_l290;
  wire                when_PerformanceCounterPlugin_l261;
  wire                when_PerformanceCounterPlugin_l267;
  reg        [3:0]    TrapPlugin_logic_harts_0_trap_fsm_stateReg;
  reg        [3:0]    TrapPlugin_logic_harts_0_trap_fsm_stateNext;
  wire                when_TrapPlugin_l547;
  wire                when_TrapPlugin_l609;
  reg        [2:0]    _zz_TrapPlugin_logic_harts_0_crsPorts_write_address;
  reg        [2:0]    _zz_TrapPlugin_logic_harts_0_crsPorts_write_address_1;
  reg        [2:0]    _zz_TrapPlugin_logic_harts_0_crsPorts_read_address;
  wire                when_TrapPlugin_l856;
  reg        [2:0]    _zz_TrapPlugin_logic_harts_0_crsPorts_read_address_1;
  wire                when_TrapPlugin_l975;
  wire       [2:0]    switch_TrapPlugin_l976;
  wire                when_TrapPlugin_l478;
  reg        [1:0]    CsrAccessPlugin_logic_fsm_stateReg;
  reg        [1:0]    CsrAccessPlugin_logic_fsm_stateNext;
  wire                when_CsrAccessPlugin_l325;
  wire                when_CsrAccessPlugin_l354;
  wire                when_CsrAccessPlugin_l237;
  wire                when_CsrAccessPlugin_l238;
  wire                when_CsrAccessPlugin_l250;
  `ifndef SYNTHESIS
  reg [87:0] execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string;
  reg [31:0] execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0_string;
  reg [39:0] execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string;
  reg [87:0] execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string;
  reg [31:0] execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string;
  reg [39:0] execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string;
  reg [31:0] execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0_string;
  reg [87:0] execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string;
  reg [39:0] execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string;
  reg [39:0] _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string;
  reg [39:0] _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1_string;
  reg [39:0] _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2_string;
  reg [31:0] _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string;
  reg [31:0] _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1_string;
  reg [31:0] _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2_string;
  reg [87:0] _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string;
  reg [87:0] _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string;
  reg [87:0] _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string;
  reg [71:0] PerformanceCounterPlugin_logic_fsm_stateReg_string;
  reg [71:0] PerformanceCounterPlugin_logic_fsm_stateNext_string;
  reg [87:0] TrapPlugin_logic_harts_0_trap_fsm_stateReg_string;
  reg [87:0] TrapPlugin_logic_harts_0_trap_fsm_stateNext_string;
  reg [79:0] CsrAccessPlugin_logic_fsm_stateReg_string;
  reg [79:0] CsrAccessPlugin_logic_fsm_stateNext_string;
  `endif

  (* ram_style = "distributed" *) reg [32:0] FetchCachelessPlugin_logic_buffer_words [0:1];
  reg [31:0] CsrRamPlugin_logic_mem [0:7];
  function [2:0] zz_FetchCachelessPlugin_logic_trapPort_payload_arg(input dummy);
    begin
      zz_FetchCachelessPlugin_logic_trapPort_payload_arg = 3'b000;
      zz_FetchCachelessPlugin_logic_trapPort_payload_arg[1 : 0] = 2'b10;
      zz_FetchCachelessPlugin_logic_trapPort_payload_arg[2] = 1'b0;
    end
  endfunction
  wire [2:0] _zz_9;

  assign _zz_early0_IntAluPlugin_logic_alu_result = (early0_IntAluPlugin_logic_alu_bitwise | _zz_early0_IntAluPlugin_logic_alu_result_1);
  assign _zz_early0_IntAluPlugin_logic_alu_result_1 = (execute_ctrl2_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0 ? execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0 : 32'h0);
  assign _zz_early0_IntAluPlugin_logic_alu_result_2 = (execute_ctrl2_down_early0_IntAluPlugin_ALU_SLTX_lane0 ? _zz_early0_IntAluPlugin_logic_alu_result_3 : 32'h0);
  assign _zz_early0_IntAluPlugin_logic_alu_result_3 = _zz_early0_IntAluPlugin_logic_alu_result_4;
  assign _zz_early0_IntAluPlugin_logic_alu_result_5 = execute_ctrl2_down_early0_SrcPlugin_LESS_lane0;
  assign _zz_early0_IntAluPlugin_logic_alu_result_4 = {31'd0, _zz_early0_IntAluPlugin_logic_alu_result_5};
  assign _zz_early0_BarrelShifterPlugin_logic_shift_amplitude = execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0[4 : 0];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_reversed = {execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[0],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[1],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[2],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[3],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[4],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[5],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[6],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[7],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[8],{_zz_early0_BarrelShifterPlugin_logic_shift_reversed_1,{_zz_early0_BarrelShifterPlugin_logic_shift_reversed_2,_zz_early0_BarrelShifterPlugin_logic_shift_reversed_3}}}}}}}}}}};
  assign _zz_early0_BarrelShifterPlugin_logic_shift_shifted = ($signed(_zz_early0_BarrelShifterPlugin_logic_shift_shifted_1) >>> early0_BarrelShifterPlugin_logic_shift_amplitude);
  assign _zz_early0_BarrelShifterPlugin_logic_shift_shifted_1 = {(execute_ctrl2_down_BarrelShifterPlugin_SIGNED_lane0 && execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[31]),early0_BarrelShifterPlugin_logic_shift_reversed};
  assign _zz_early0_BarrelShifterPlugin_logic_shift_patched = {early0_BarrelShifterPlugin_logic_shift_shifted[0],{early0_BarrelShifterPlugin_logic_shift_shifted[1],{early0_BarrelShifterPlugin_logic_shift_shifted[2],{early0_BarrelShifterPlugin_logic_shift_shifted[3],{early0_BarrelShifterPlugin_logic_shift_shifted[4],{early0_BarrelShifterPlugin_logic_shift_shifted[5],{early0_BarrelShifterPlugin_logic_shift_shifted[6],{early0_BarrelShifterPlugin_logic_shift_shifted[7],{early0_BarrelShifterPlugin_logic_shift_shifted[8],{_zz_early0_BarrelShifterPlugin_logic_shift_patched_1,{_zz_early0_BarrelShifterPlugin_logic_shift_patched_2,_zz_early0_BarrelShifterPlugin_logic_shift_patched_3}}}}}}}}}}};
  assign _zz_execute_ctrl2_down_MUL_SRC1_lane0 = {(execute_ctrl2_down_RsUnsignedPlugin_RS1_SIGNED_lane0 && execute_ctrl2_up_integer_RS1_lane0[31]),execute_ctrl2_up_integer_RS1_lane0};
  assign _zz_execute_ctrl2_down_MUL_SRC2_lane0 = {(execute_ctrl2_down_RsUnsignedPlugin_RS2_SIGNED_lane0 && execute_ctrl2_up_integer_RS2_lane0[31]),execute_ctrl2_up_integer_RS2_lane0};
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_1 = ($signed(_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_2) * $signed(_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_3));
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0 = {{13{_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_1[33]}}, _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_1};
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_2 = {1'b0,execute_ctrl2_down_MUL_SRC1_lane0[16 : 0]};
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0_3 = execute_ctrl2_down_MUL_SRC2_lane0[32 : 17];
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_1 = ($signed(_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_2) * $signed(_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_3));
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0 = {{13{_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_1[33]}}, _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_1};
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_2 = execute_ctrl2_down_MUL_SRC1_lane0[32 : 17];
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0_3 = {1'b0,execute_ctrl2_down_MUL_SRC2_lane0[16 : 0]};
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_1 = ($signed(_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_2) * $signed(_zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_3));
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0 = _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_1[29:0];
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_2 = execute_ctrl2_down_MUL_SRC1_lane0[32 : 17];
  assign _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0_3 = execute_ctrl2_down_MUL_SRC2_lane0[32 : 17];
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_3 = (_zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_4 + _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_5);
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_4 = {2'd0, _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0};
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_5 = {2'd0, _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_1};
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_6 = {2'd0, _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_2};
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_3 = (_zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_4 + _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_5);
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_4 = {2'd0, _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0};
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_5 = {2'd0, _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_1};
  assign _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_6 = {2'd0, _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_2};
  assign _zz_execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0_1 = execute_ctrl2_down_RsUnsignedPlugin_RS1_REVERT_lane0;
  assign _zz_execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0 = {31'd0, _zz_execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0_1};
  assign _zz_execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0_1 = execute_ctrl2_down_RsUnsignedPlugin_RS2_REVERT_lane0;
  assign _zz_execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0 = {31'd0, _zz_execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0_1};
  assign _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_1 = ((early0_DivPlugin_logic_processing_divRevertResult ? (~ _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0) : _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0) + _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_2);
  assign _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_3 = early0_DivPlugin_logic_processing_divRevertResult;
  assign _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_2 = {31'd0, _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_3};
  assign _zz_WhiteboxerPlugin_logic_decodes_0_pc = {32'd0, decode_ctrls_0_down_PC_0};
  assign _zz_PrivilegedPlugin_logic_defaultTrap_adjustPrivilege = PrivilegedPlugin_logic_defaultTrap_hartPrivilege[1 : 0];
  assign _zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0 = execute_ctrl1_down_Decode_UOP_lane0[31 : 20];
  assign _zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0_1 = {execute_ctrl1_down_Decode_UOP_lane0[31 : 25],execute_ctrl1_down_Decode_UOP_lane0[11 : 7]};
  assign _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0 = ($signed(execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0) + $signed(early0_SrcPlugin_logic_addsub_combined_rs2Patched));
  assign _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_1 = _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_2;
  assign _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_3 = execute_ctrl2_down_SrcStageables_REVERT_lane0;
  assign _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_2 = {31'd0, _zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_3};
  assign _zz_PerformanceCounterPlugin_logic_eventInstructions_0 = ((! PerformanceCounterPlugin_logic_ignoreNextCommit) ? PerformanceCounterPlugin_logic_commitMask : 1'b0);
  assign _zz_PerformanceCounterPlugin_logic_counters_cycle_value_1 = (! PerformanceCounterPlugin_logic_counters_cycle_mcountinhibit);
  assign _zz_PerformanceCounterPlugin_logic_counters_cycle_value = {7'd0, _zz_PerformanceCounterPlugin_logic_counters_cycle_value_1};
  assign _zz_PerformanceCounterPlugin_logic_counters_instret_value_1 = {7'd0, _zz_PerformanceCounterPlugin_logic_counters_instret_value};
  assign _zz_early0_BranchPlugin_pcCalc_target_b = {{{{execute_ctrl2_down_Decode_UOP_lane0[31],execute_ctrl2_down_Decode_UOP_lane0[19 : 12]},execute_ctrl2_down_Decode_UOP_lane0[20]},execute_ctrl2_down_Decode_UOP_lane0[30 : 21]},1'b0};
  assign _zz_early0_BranchPlugin_pcCalc_target_b_1 = execute_ctrl2_down_Decode_UOP_lane0[31 : 20];
  assign _zz_early0_BranchPlugin_pcCalc_target_b_2 = {{{{execute_ctrl2_down_Decode_UOP_lane0[31],execute_ctrl2_down_Decode_UOP_lane0[7]},execute_ctrl2_down_Decode_UOP_lane0[30 : 25]},execute_ctrl2_down_Decode_UOP_lane0[11 : 8]},1'b0};
  assign _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0 = ($signed(early0_BranchPlugin_pcCalc_target_a) + $signed(early0_BranchPlugin_pcCalc_target_b));
  assign _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0_1 = ({1'd0,early0_BranchPlugin_pcCalc_slices} <<< 1'd1);
  assign _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0 = {29'd0, _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0_1};
  assign _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0_1 = ({1'd0,execute_ctrl2_down_Decode_INSTRUCTION_SLICE_COUNT_lane0} <<< 1'd1);
  assign _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0 = {30'd0, _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0_1};
  assign _zz_AlignerPlugin_logic_extractors_0_redo_4 = (((_zz_AlignerPlugin_logic_extractors_0_redo ? AlignerPlugin_logic_scanners_0_redo : 1'b0) | (_zz_AlignerPlugin_logic_extractors_0_redo_1 ? AlignerPlugin_logic_scanners_1_redo : 1'b0)) | ((_zz_AlignerPlugin_logic_extractors_0_redo_2 ? AlignerPlugin_logic_scanners_2_redo : 1'b0) | (_zz_AlignerPlugin_logic_extractors_0_redo_3 ? AlignerPlugin_logic_scanners_3_redo : 1'b0)));
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_26 = {{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10,AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2]},12'h0};
  assign _zz__zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22_1 = {AlignerPlugin_logic_extractors_0_ctx_instruction[12],AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2]};
  assign _zz__zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22 = {6'd0, _zz__zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22_1};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_35 = {{{4'b0000,AlignerPlugin_logic_extractors_0_ctx_instruction[8 : 7]},AlignerPlugin_logic_extractors_0_ctx_instruction[12 : 9]},2'b00};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_36 = {{{4'b0000,AlignerPlugin_logic_extractors_0_ctx_instruction[8 : 7]},AlignerPlugin_logic_extractors_0_ctx_instruction[12 : 9]},2'b00};
  assign _zz__zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_5 = {_zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_4[0],_zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_4[1]};
  assign _zz_LsuCachelessPlugin_logic_trapPort_payload_tval2 = _zz_LsuCachelessPlugin_logic_trapPort_payload_tval2_1[31 : 2];
  assign _zz_LsuCachelessPlugin_logic_trapPort_payload_tval2_1 = execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  assign _zz_early0_EnvPlugin_logic_exe_xretPriv_1 = ((_zz_early0_EnvPlugin_logic_exe_xretPriv == 2'b11) ? 1'b0 : early0_EnvPlugin_logic_exe_privilege[2]);
  assign _zz_early0_EnvPlugin_logic_trapPort_payload_code_1 = ({1'd0,early0_EnvPlugin_logic_exe_privilege} <<< 1'd1);
  assign _zz_early0_EnvPlugin_logic_trapPort_payload_code = {1'd0, _zz_early0_EnvPlugin_logic_trapPort_payload_code_1};
  assign _zz_early0_EnvPlugin_logic_trapPort_payload_code_2 = {{2{early0_EnvPlugin_logic_exe_privilege[2]}}, early0_EnvPlugin_logic_exe_privilege};
  assign _zz_decode_ctrls_1_down_RS1_ENABLE_0 = (|{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000044) == 32'h0),{_zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0,{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00006004) == 32'h00002000),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00005004) == 32'h00001000),((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00002050) == 32'h00002000)}}}});
  assign _zz_decode_ctrls_1_down_RS1_PHYS_0 = decode_ctrls_1_down_Decode_INSTRUCTION_0[19 : 15];
  assign _zz_decode_ctrls_1_down_RS2_ENABLE_0 = (|{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000034) == 32'h00000020),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000064) == 32'h00000020),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h08000070) == 32'h08000020),((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h10000070) == 32'h00000020)}}});
  assign _zz_decode_ctrls_1_down_RS2_PHYS_0 = decode_ctrls_1_down_Decode_INSTRUCTION_0[24 : 20];
  assign _zz_decode_ctrls_1_down_RD_ENABLE_0 = (|{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000048) == 32'h00000048),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00001010) == 32'h00001010),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & _zz_decode_ctrls_1_down_RD_ENABLE_0_1) == 32'h00002010),{(_zz_decode_ctrls_1_down_RD_ENABLE_0_2 == _zz_decode_ctrls_1_down_RD_ENABLE_0_3),{_zz_decode_ctrls_1_down_RD_ENABLE_0_4,{_zz_decode_ctrls_1_down_RD_ENABLE_0_5,_zz_decode_ctrls_1_down_RD_ENABLE_0_6}}}}}});
  assign _zz_decode_ctrls_1_down_RD_PHYS_0 = decode_ctrls_1_down_Decode_INSTRUCTION_0[11 : 7];
  assign _zz_LsuCachelessPlugin_logic_trapPort_payload_code = (execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_MISS_ALIGNED_lane0 ? (execute_ctrl2_down_AguPlugin_STORE_lane0 ? 3'b110 : 3'b100) : 3'b000);
  assign _zz_LsuCachelessPlugin_logic_onWb_rspShifted_2 = execute_ctrl4_down_early0_SrcPlugin_ADD_SUB_lane0[1 : 0];
  assign _zz_LsuCachelessPlugin_logic_onWb_rspShifted_5 = execute_ctrl4_down_early0_SrcPlugin_ADD_SUB_lane0[1 : 1];
  assign _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0 = _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0_1[0];
  assign _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0_1 = 1'b0;
  assign _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0 = _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0_1[0];
  assign _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0_1 = 1'b0;
  assign _zz_decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0 = _zz_decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0_1[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0_1 = (|_zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0);
  assign _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0_1 = _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0_2[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0_2 = (|{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000040) == 32'h00000040),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00002010) == 32'h00002000),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00001010) == 32'h00001000),_zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0}}});
  assign _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0 = _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0_1[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0_1 = 1'b0;
  assign _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_2 = _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_3[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_3 = (|{_zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_1,_zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0});
  assign _zz_decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0 = _zz_decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0_1[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0_1 = (|{_zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_1,_zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0});
  assign _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0 = _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0_1[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0_1 = (|_zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0);
  assign _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0_1 = _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0_2[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0_2 = (|_zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0);
  assign _zz_PerformanceCounterPlugin_logic_fsm_calc_a = PerformanceCounterPlugin_logic_fsm_counterReaded[6:0];
  assign _zz_PerformanceCounterPlugin_logic_fsm_calc_b = ({7'd0,PerformanceCounterPlugin_logic_fsm_counterReaded[7]} <<< 3'd7);
  assign _zz_PerformanceCounterPlugin_logic_fsm_calc_sum_1 = {1'b0,PerformanceCounterPlugin_logic_fsm_calc_b};
  assign _zz_PerformanceCounterPlugin_logic_fsm_calc_sum = {24'd0, _zz_PerformanceCounterPlugin_logic_fsm_calc_sum_1};
  assign _zz_PerformanceCounterPlugin_logic_flusher_hits_ohFirst_masked = (PerformanceCounterPlugin_logic_flusher_hits_ohFirst_input - 2'b01);
  assign _zz_PerformanceCounterPlugin_logic_csrDecode_hyperOK = PerformanceCounterPlugin_logic_csrDecode_privilege[1 : 0];
  assign _zz_LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit_1 = (|_zz_LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit);
  assign _zz_LsuCachelessPlugin_pmaBuilder_loadExecute_fault = (|_zz_LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit);
  assign _zz_LsuCachelessPlugin_logic_onPma_port_rsp_io = (|_zz_LsuCachelessPlugin_logic_onPma_port_rsp_fault);
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_slices = {1'd0, execute_ctrl4_down_Decode_INSTRUCTION_SLICE_COUNT_lane0};
  assign _zz_TrapPlugin_logic_harts_0_trap_fsm_jumpTarget_1 = ({1'd0,TrapPlugin_logic_harts_0_trap_fsm_jumpOffset} <<< 1'd1);
  assign _zz_TrapPlugin_logic_harts_0_trap_fsm_jumpTarget = {29'd0, _zz_TrapPlugin_logic_harts_0_trap_fsm_jumpTarget_1};
  assign _zz_PcPlugin_logic_harts_0_self_pc_1 = (PcPlugin_logic_harts_0_self_increment ? 3'b100 : 3'b000);
  assign _zz_PcPlugin_logic_harts_0_self_pc = {29'd0, _zz_PcPlugin_logic_harts_0_self_pc_1};
  assign _zz_PcPlugin_logic_harts_0_aggregator_fault = (((_zz_PcPlugin_logic_harts_0_aggregator_target ? TrapPlugin_logic_harts_0_trap_pcPort_payload_fault : 1'b0) | (_zz_PcPlugin_logic_harts_0_aggregator_target_1 ? early0_BranchPlugin_logic_pcPort_payload_fault : 1'b0)) | (_zz_PcPlugin_logic_harts_0_aggregator_target_2 ? PcPlugin_logic_harts_0_self_flow_payload_fault : 1'b0));
  assign _zz_CsrAccessPlugin_logic_fsm_inject_csrPriv = execute_ctrl2_down_Decode_UOP_lane0[31 : 20];
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_11 = ((_zz_when_CsrService_l221 && REG_CSR_3858) ? 6'h2e : 6'h0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_10 = {26'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_11};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_16 = ({7'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue ? PrivilegedPlugin_logic_harts_0_m_status_mpie : 1'b0)} <<< 3'd7);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_15 = {24'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_16};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_18 = ({3'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue ? PrivilegedPlugin_logic_harts_0_m_status_mie : 1'b0)} <<< 2'd3);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_17 = {28'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_18};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_20 = ({11'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue ? PrivilegedPlugin_logic_harts_0_m_status_mpp : 2'b00)} <<< 4'd11);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_19 = {19'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_20};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_21 = ({31'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue ? PrivilegedPlugin_logic_harts_0_m_status_sd : 1'b0)} <<< 5'd31);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_23 = ({17'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue ? PrivilegedPlugin_logic_harts_0_m_status_mprv : 1'b0)} <<< 5'd17);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_22 = {14'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_23};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_24 = ({31'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_1 ? PrivilegedPlugin_logic_harts_0_m_cause_interrupt : 1'b0)} <<< 5'd31);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_26 = (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_1 ? PrivilegedPlugin_logic_harts_0_m_cause_code : 5'h0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_25 = {27'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_26};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_28 = ({11'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_3 ? PrivilegedPlugin_logic_harts_0_m_ip_meip : 1'b0)} <<< 4'd11);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_27 = {20'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_28};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_30 = ({7'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_3 ? PrivilegedPlugin_logic_harts_0_m_ip_mtip : 1'b0)} <<< 3'd7);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_29 = {24'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_30};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_32 = ({3'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_3 ? PrivilegedPlugin_logic_harts_0_m_ip_msip : 1'b0)} <<< 2'd3);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_31 = {28'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_32};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_34 = ({11'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_5 ? PrivilegedPlugin_logic_harts_0_m_ie_meie : 1'b0)} <<< 4'd11);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_33 = {20'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_34};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_36 = ({7'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_5 ? PrivilegedPlugin_logic_harts_0_m_ie_mtie : 1'b0)} <<< 3'd7);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_35 = {24'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_36};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_38 = ({3'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_5 ? PrivilegedPlugin_logic_harts_0_m_ie_msie : 1'b0)} <<< 2'd3);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_37 = {28'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_38};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_40 = (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_6 ? PrivilegedPlugin_logic_harts_0_m_topi_priority : 1'b0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_39 = {31'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_40};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_42 = ({16'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_6 ? PrivilegedPlugin_logic_harts_0_m_topi_interrupt : 5'h0)} <<< 5'd16);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_41 = {11'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_42};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_43 = ({31'd0,((_zz_when_CsrService_l221 && REG_CSR_794) ? PrivilegedPlugin_logic_harts_0_m_envcfg_stce : 1'b0)} <<< 5'd31);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_45 = ({1'd0,((_zz_when_CsrService_l221 && _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_7) ? PrivilegedPlugin_logic_harts_0_m_counteren_tm : 1'b0)} <<< 1'd1);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_44 = {30'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_45};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_49 = (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_7 ? PerformanceCounterPlugin_logic_counters_cycle_mcounteren : 1'b0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_48 = {31'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_49};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_51 = (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_8 ? PerformanceCounterPlugin_logic_counters_cycle_mcountinhibit : 1'b0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_50 = {31'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_51};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_53 = ({2'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_7 ? PerformanceCounterPlugin_logic_counters_instret_mcounteren : 1'b0)} <<< 2'd2);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_52 = {29'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_53};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_55 = ({2'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_8 ? PerformanceCounterPlugin_logic_counters_instret_mcountinhibit : 1'b0)} <<< 2'd2);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_54 = {29'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_55};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_57 = ({13'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_2 ? PerformanceCounterPlugin_logic_interrupt_ip : 1'b0)} <<< 4'd13);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_56 = {18'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_57};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_59 = ({13'd0,(_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_4 ? PerformanceCounterPlugin_logic_interrupt_ie : 1'b0)} <<< 4'd13);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_58 = {18'd0, _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_59};
  assign _zz_CsrAccessPlugin_logic_fsm_writeLogic_alu_mask_1 = CsrAccessPlugin_logic_fsm_interface_uop[19 : 15];
  assign _zz_CsrAccessPlugin_logic_fsm_writeLogic_alu_mask = {27'd0, _zz_CsrAccessPlugin_logic_fsm_writeLogic_alu_mask_1};
  assign _zz_FetchCachelessPlugin_logic_trapPort_payload_tval2 = _zz_FetchCachelessPlugin_logic_trapPort_payload_tval2_1[31 : 2];
  assign _zz_FetchCachelessPlugin_logic_trapPort_payload_tval2_1 = fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
  assign _zz_CsrRamPlugin_logic_writeLogic_hits_ohFirst_masked = (CsrRamPlugin_logic_writeLogic_hits_ohFirst_input - 4'b0001);
  assign _zz_CsrRamPlugin_logic_readLogic_hits_ohFirst_masked = (CsrRamPlugin_logic_readLogic_hits_ohFirst_input - 3'b001);
  assign _zz_CsrRamPlugin_logic_flush_counter_1 = (! CsrRamPlugin_logic_flush_done);
  assign _zz_CsrRamPlugin_logic_flush_counter = {3'd0, _zz_CsrRamPlugin_logic_flush_counter_1};
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0_1 = _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0_2[0];
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0_2 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00002030) == 32'h00002010),{_zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0,{((execute_lane0_logic_decoding_decodingBits & 32'h00001030) == 32'h00000010),{((execute_lane0_logic_decoding_decodingBits & 32'h02002050) == 32'h00002010),((execute_lane0_logic_decoding_decodingBits & 32'h02001050) == 32'h00000010)}}}});
  assign _zz_execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00003034) == 32'h00001010),((execute_lane0_logic_decoding_decodingBits & 32'h02003054) == 32'h00001010)});
  assign _zz_execute_ctrl1_down_early0_BranchPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_BranchPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_early0_BranchPlugin_SEL_lane0_1 = (|_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0);
  assign _zz_execute_ctrl1_down_early0_MulPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_MulPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_early0_MulPlugin_SEL_lane0_1 = (|_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0);
  assign _zz_execute_ctrl1_down_early0_DivPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_DivPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_early0_DivPlugin_SEL_lane0_1 = (|_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0);
  assign _zz_execute_ctrl1_down_early0_EnvPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_EnvPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_early0_EnvPlugin_SEL_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00001048) == 32'h00001008),((execute_lane0_logic_decoding_decodingBits & 32'h00003050) == 32'h00000050)});
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_SEL_lane0 = _zz_execute_ctrl1_down_CsrAccessPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_SEL_lane0_1 = (|{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_2,_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_1});
  assign _zz_execute_ctrl1_down_AguPlugin_SEL_lane0 = _zz_execute_ctrl1_down_AguPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_AguPlugin_SEL_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00002050) == 32'h00002000),((execute_lane0_logic_decoding_decodingBits & 32'h00000058) == 32'h0)});
  assign _zz_execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0 = _zz_execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0_1[0];
  assign _zz_execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0_1 = (|((execute_lane0_logic_decoding_decodingBits & 32'h00003048) == 32'h00000008));
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0 = _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_1[0];
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00000048) == 32'h00000048),{((execute_lane0_logic_decoding_decodingBits & 32'h00001010) == 32'h00001010),{((execute_lane0_logic_decoding_decodingBits & _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_2) == 32'h00002010),{(_zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_3 == _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_4),{_zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_5,{_zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_6,_zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_7}}}}}});
  assign _zz_execute_ctrl1_down_COMPLETION_AT_2_lane0 = _zz_execute_ctrl1_down_COMPLETION_AT_2_lane0_1[0];
  assign _zz_execute_ctrl1_down_COMPLETION_AT_2_lane0_1 = (|{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_5,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_4,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_1,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_3,_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_2}}}}});
  assign _zz_execute_ctrl1_down_COMPLETION_AT_4_lane0 = _zz_execute_ctrl1_down_COMPLETION_AT_4_lane0_1[0];
  assign _zz_execute_ctrl1_down_COMPLETION_AT_4_lane0_1 = (|{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_2,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_1,_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0}});
  assign _zz_execute_ctrl1_down_COMPLETION_AT_3_lane0 = _zz_execute_ctrl1_down_COMPLETION_AT_3_lane0_1[0];
  assign _zz_execute_ctrl1_down_COMPLETION_AT_3_lane0_1 = (|{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_2,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_1,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_3,_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0}}});
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_6 = _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_7[0];
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_7 = (|{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_5,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_4,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_1,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_3,_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_2}}}}});
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_3 = _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_4[0];
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_4 = (|{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_2,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_1,_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0}});
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_4 = _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_5[0];
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_5 = (|{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_2,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_1,{_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_3,_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0}}});
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0_1 = _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0_2[0];
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0_2 = (|{_zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0,_zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0});
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0 = _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0_1[0];
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0_1 = (|((execute_lane0_logic_decoding_decodingBits & 32'h00006004) == 32'h00002000));
  assign _zz_execute_ctrl1_down_SrcStageables_REVERT_lane0 = _zz_execute_ctrl1_down_SrcStageables_REVERT_lane0_1[0];
  assign _zz_execute_ctrl1_down_SrcStageables_REVERT_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00000040) == 32'h00000040),{((execute_lane0_logic_decoding_decodingBits & 32'h00002014) == 32'h00002010),((execute_lane0_logic_decoding_decodingBits & 32'h40000034) == 32'h40000030)}});
  assign _zz_execute_ctrl1_down_SrcStageables_ZERO_lane0 = _zz_execute_ctrl1_down_SrcStageables_ZERO_lane0_1[0];
  assign _zz_execute_ctrl1_down_SrcStageables_ZERO_lane0_1 = (|((execute_lane0_logic_decoding_decodingBits & 32'h00000024) == 32'h00000024));
  assign _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 = _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0_1[0];
  assign _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0_1 = (|((execute_lane0_logic_decoding_decodingBits & 32'h00004010) == 32'h0));
  assign _zz_execute_ctrl1_down_BYPASSED_AT_1_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_1_lane0_1[0];
  assign _zz_execute_ctrl1_down_BYPASSED_AT_1_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_BYPASSED_AT_2_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_2_lane0_1[0];
  assign _zz_execute_ctrl1_down_BYPASSED_AT_2_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_BYPASSED_AT_3_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_3_lane0_1[0];
  assign _zz_execute_ctrl1_down_BYPASSED_AT_3_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_BYPASSED_AT_4_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_4_lane0_1[0];
  assign _zz_execute_ctrl1_down_BYPASSED_AT_4_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_SrcStageables_UNSIGNED_lane0 = _zz_execute_ctrl1_down_SrcStageables_UNSIGNED_lane0_1[0];
  assign _zz_execute_ctrl1_down_SrcStageables_UNSIGNED_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00002010) == 32'h00002000),((execute_lane0_logic_decoding_decodingBits & 32'h00005000) == 32'h00001000)});
  assign _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0_1 = _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0_2[0];
  assign _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0_2 = (|_zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0);
  assign _zz_execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0 = _zz_execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0_1[0];
  assign _zz_execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0_1 = (|((execute_lane0_logic_decoding_decodingBits & 32'h40000000) == 32'h40000000));
  assign _zz_execute_ctrl1_down_MulPlugin_HIGH_lane0 = _zz_execute_ctrl1_down_MulPlugin_HIGH_lane0_1[0];
  assign _zz_execute_ctrl1_down_MulPlugin_HIGH_lane0_1 = (|{_zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0,_zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0});
  assign _zz_execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0 = _zz_execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0_1[0];
  assign _zz_execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00001000) == 32'h0),_zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0});
  assign _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0_1 = _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0_2[0];
  assign _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0_2 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00005000) == 32'h00004000),_zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0});
  assign _zz_execute_ctrl1_down_DivPlugin_REM_lane0 = _zz_execute_ctrl1_down_DivPlugin_REM_lane0_1[0];
  assign _zz_execute_ctrl1_down_DivPlugin_REM_lane0_1 = (|_zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0);
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0 = _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0_1[0];
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0_1 = (|((execute_lane0_logic_decoding_decodingBits & 32'h00004000) == 32'h00004000));
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0_1 = _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0_2[0];
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0_2 = (|_zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0);
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0_1 = _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0_2[0];
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0_2 = (|_zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0);
  assign _zz_execute_ctrl1_down_AguPlugin_LOAD_lane0 = _zz_execute_ctrl1_down_AguPlugin_LOAD_lane0_1[0];
  assign _zz_execute_ctrl1_down_AguPlugin_LOAD_lane0_1 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h00000020) == 32'h0),{((execute_lane0_logic_decoding_decodingBits & 32'h10000008) == 32'h00000008),((execute_lane0_logic_decoding_decodingBits & 32'h08000008) == 32'h00000008)}});
  assign _zz_execute_ctrl1_down_AguPlugin_STORE_lane0_1 = _zz_execute_ctrl1_down_AguPlugin_STORE_lane0_2[0];
  assign _zz_execute_ctrl1_down_AguPlugin_STORE_lane0_2 = (|{((execute_lane0_logic_decoding_decodingBits & 32'h08000020) == 32'h08000020),{_zz_execute_ctrl1_down_AguPlugin_STORE_lane0,((execute_lane0_logic_decoding_decodingBits & 32'h00000028) == 32'h00000020)}});
  assign _zz_execute_ctrl1_down_AguPlugin_EXECUTE_lane0 = _zz_execute_ctrl1_down_AguPlugin_EXECUTE_lane0_1[0];
  assign _zz_execute_ctrl1_down_AguPlugin_EXECUTE_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0_1 = _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0_2[0];
  assign _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0_2 = (|_zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0);
  assign _zz_execute_ctrl1_down_AguPlugin_FLOAT_lane0 = _zz_execute_ctrl1_down_AguPlugin_FLOAT_lane0_1[0];
  assign _zz_execute_ctrl1_down_AguPlugin_FLOAT_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_AguPlugin_CLEAN_lane0 = _zz_execute_ctrl1_down_AguPlugin_CLEAN_lane0_1[0];
  assign _zz_execute_ctrl1_down_AguPlugin_CLEAN_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_AguPlugin_INVALIDATE_lane0 = _zz_execute_ctrl1_down_AguPlugin_INVALIDATE_lane0_1[0];
  assign _zz_execute_ctrl1_down_AguPlugin_INVALIDATE_lane0_1 = 1'b0;
  assign _zz_execute_ctrl1_down_AguPlugin_GUEST_lane0 = _zz_execute_ctrl1_down_AguPlugin_GUEST_lane0_1[0];
  assign _zz_execute_ctrl1_down_AguPlugin_GUEST_lane0_1 = 1'b0;
  assign _zz_WhiteboxerPlugin_logic_csr_access_payload_address = CsrAccessPlugin_logic_fsm_interface_uop;
  assign _zz_FetchCachelessPlugin_pmaBuilder_onTransfers_0_addressHit = (|((FetchCachelessPlugin_pmaBuilder_addressBits & 32'h0) == 32'h0));
  assign _zz_FetchCachelessPlugin_logic_onPma_port_rsp_io = (|_zz_FetchCachelessPlugin_logic_onPma_port_rsp_fault);
  assign _zz__zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1_1 = _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter;
  assign _zz__zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1 = {59'd0, _zz__zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1_1};
  assign _zz__zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1_1 = _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter;
  assign _zz__zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1 = {59'd0, _zz__zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1_1};
  assign _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1_1 = _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0;
  assign _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1 = {59'd0, _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1_1};
  assign _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1_1 = _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1;
  assign _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1 = {59'd0, _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1_1};
  assign _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1_1 = _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0;
  assign _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1 = {59'd0, _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1_1};
  assign _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1_1 = _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1;
  assign _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1 = {59'd0, _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1_1};
  assign _zz_PerformanceCounterPlugin_logic_writePort_data = PerformanceCounterPlugin_logic_fsm_calc_sum;
  assign _zz_PerformanceCounterPlugin_logic_writePort_data_1 = PerformanceCounterPlugin_logic_fsm_calc_sum;
  assign _zz_PerformanceCounterPlugin_logic_counters_cycle_value_2 = CsrAccessPlugin_bus_write_bits;
  assign _zz_PerformanceCounterPlugin_logic_counters_instret_value_2 = CsrAccessPlugin_bus_write_bits;
  assign _zz_FetchCachelessPlugin_logic_buffer_words_port = {FetchCachelessPlugin_logic_buffer_write_payload_data_word,FetchCachelessPlugin_logic_buffer_write_payload_data_error};
  assign _zz_PerformanceCounterPlugin_logic_commitCount_1 = PerformanceCounterPlugin_logic_commitMask[0];
  assign _zz_fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK_1 = fetch_logic_ctrls_1_down_Fetch_WORD_PC[1 : 1];
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_28 = AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 10];
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_30 = {AlignerPlugin_logic_extractors_0_ctx_instruction[12],AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 5]};
  assign _zz_LsuCachelessPlugin_logic_onWb_rspShifted_1 = _zz_LsuCachelessPlugin_logic_onWb_rspShifted_2;
  assign _zz_LsuCachelessPlugin_logic_onWb_rspShifted_4 = _zz_LsuCachelessPlugin_logic_onWb_rspShifted_5;
  assign _zz_PerformanceCounterPlugin_logic_events_sums_0_2 = _zz_PerformanceCounterPlugin_logic_events_sums_0[0];
  assign _zz_PerformanceCounterPlugin_logic_events_sums_1_2 = _zz_PerformanceCounterPlugin_logic_events_sums_1[0];
  assign _zz_PerformanceCounterPlugin_logic_events_sums_2_2 = _zz_PerformanceCounterPlugin_logic_events_sums_2[0];
  assign _zz_PerformanceCounterPlugin_logic_events_sums_3_2 = _zz_PerformanceCounterPlugin_logic_events_sums_3[0];
  assign _zz_PerformanceCounterPlugin_logic_events_sums_4_2 = _zz_PerformanceCounterPlugin_logic_events_sums_4[0];
  assign _zz_PerformanceCounterPlugin_logic_events_sums_5_2 = _zz_PerformanceCounterPlugin_logic_events_sums_5[0];
  assign _zz_WhiteboxerPlugin_logic_perf_candidatesCount_1 = DispatchPlugin_logic_candidates_0_ctx_valid;
  assign _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCount_1 = decode_ctrls_1_up_LANE_SEL_0;
  assign _zz_early0_BarrelShifterPlugin_logic_shift_reversed_1 = execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[9];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_reversed_2 = execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[10];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_reversed_3 = {execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[11],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[12],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[13],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[14],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[15],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[16],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[17],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[18],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[19],{_zz_early0_BarrelShifterPlugin_logic_shift_reversed_4,{_zz_early0_BarrelShifterPlugin_logic_shift_reversed_5,_zz_early0_BarrelShifterPlugin_logic_shift_reversed_6}}}}}}}}}}};
  assign _zz_early0_BarrelShifterPlugin_logic_shift_reversed_4 = execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[20];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_reversed_5 = execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[21];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_reversed_6 = {execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[22],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[23],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[24],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[25],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[26],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[27],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[28],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[29],{execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[30],execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[31]}}}}}}}}};
  assign _zz_early0_BarrelShifterPlugin_logic_shift_patched_1 = early0_BarrelShifterPlugin_logic_shift_shifted[9];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_patched_2 = early0_BarrelShifterPlugin_logic_shift_shifted[10];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_patched_3 = {early0_BarrelShifterPlugin_logic_shift_shifted[11],{early0_BarrelShifterPlugin_logic_shift_shifted[12],{early0_BarrelShifterPlugin_logic_shift_shifted[13],{early0_BarrelShifterPlugin_logic_shift_shifted[14],{early0_BarrelShifterPlugin_logic_shift_shifted[15],{early0_BarrelShifterPlugin_logic_shift_shifted[16],{early0_BarrelShifterPlugin_logic_shift_shifted[17],{early0_BarrelShifterPlugin_logic_shift_shifted[18],{early0_BarrelShifterPlugin_logic_shift_shifted[19],{_zz_early0_BarrelShifterPlugin_logic_shift_patched_4,{_zz_early0_BarrelShifterPlugin_logic_shift_patched_5,_zz_early0_BarrelShifterPlugin_logic_shift_patched_6}}}}}}}}}}};
  assign _zz_early0_BarrelShifterPlugin_logic_shift_patched_4 = early0_BarrelShifterPlugin_logic_shift_shifted[20];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_patched_5 = early0_BarrelShifterPlugin_logic_shift_shifted[21];
  assign _zz_early0_BarrelShifterPlugin_logic_shift_patched_6 = {early0_BarrelShifterPlugin_logic_shift_shifted[22],{early0_BarrelShifterPlugin_logic_shift_shifted[23],{early0_BarrelShifterPlugin_logic_shift_shifted[24],{early0_BarrelShifterPlugin_logic_shift_shifted[25],{early0_BarrelShifterPlugin_logic_shift_shifted[26],{early0_BarrelShifterPlugin_logic_shift_shifted[27],{early0_BarrelShifterPlugin_logic_shift_shifted[28],{early0_BarrelShifterPlugin_logic_shift_shifted[29],{early0_BarrelShifterPlugin_logic_shift_shifted[30],early0_BarrelShifterPlugin_logic_shift_shifted[31]}}}}}}}}};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_23 = {_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_12,AlignerPlugin_logic_extractors_0_ctx_instruction[4 : 3]};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_24 = AlignerPlugin_logic_extractors_0_ctx_instruction[5];
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_25 = AlignerPlugin_logic_extractors_0_ctx_instruction[2];
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_31 = 7'h0;
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_32 = AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2];
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_33 = AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_34 = AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7];
  assign _zz_decode_ctrls_1_down_RD_ENABLE_0_1 = 32'h00002010;
  assign _zz_decode_ctrls_1_down_RD_ENABLE_0_2 = (decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00002008);
  assign _zz_decode_ctrls_1_down_RD_ENABLE_0_3 = 32'h00002008;
  assign _zz_decode_ctrls_1_down_RD_ENABLE_0_4 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000050) == 32'h00000010);
  assign _zz_decode_ctrls_1_down_RD_ENABLE_0_5 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000000c) == 32'h00000004);
  assign _zz_decode_ctrls_1_down_RD_ENABLE_0_6 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000028) == 32'h0);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0 = 32'h0000107f;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_1 = (decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000207f);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_2 = 32'h00002073;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_3 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000407f) == 32'h00004063);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_4 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000207f) == 32'h00002013);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_5 = {((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000107f) == 32'h00000013),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000603f) == 32'h00000023),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & _zz_decode_ctrls_1_down_Decode_LEGAL_0_6) == 32'h00000003),{(_zz_decode_ctrls_1_down_Decode_LEGAL_0_7 == _zz_decode_ctrls_1_down_Decode_LEGAL_0_8),{_zz_decode_ctrls_1_down_Decode_LEGAL_0_9,{_zz_decode_ctrls_1_down_Decode_LEGAL_0_10,_zz_decode_ctrls_1_down_Decode_LEGAL_0_11}}}}}};
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_6 = 32'h0000207f;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_7 = (decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000505f);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_8 = 32'h00000003;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_9 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000707b) == 32'h00000063);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_10 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000607f) == 32'h0000000f);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_11 = {((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h1800707f) == 32'h0000202f),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hfc00007f) == 32'h00000033),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & _zz_decode_ctrls_1_down_Decode_LEGAL_0_12) == 32'h0800202f),{(_zz_decode_ctrls_1_down_Decode_LEGAL_0_13 == _zz_decode_ctrls_1_down_Decode_LEGAL_0_14),{_zz_decode_ctrls_1_down_Decode_LEGAL_0_15,{_zz_decode_ctrls_1_down_Decode_LEGAL_0_16,_zz_decode_ctrls_1_down_Decode_LEGAL_0_17}}}}}};
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_12 = 32'he800707f;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_13 = (decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hfc00305f);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_14 = 32'h00001013;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_15 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hbc00707f) == 32'h00005013);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_16 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hbe00707f) == 32'h00005033);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_17 = {((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hbe00707f) == 32'h00000033),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hf9f0707f) == 32'h1000202f),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & _zz_decode_ctrls_1_down_Decode_LEGAL_0_18) == 32'h00000073),{(_zz_decode_ctrls_1_down_Decode_LEGAL_0_19 == _zz_decode_ctrls_1_down_Decode_LEGAL_0_20),(_zz_decode_ctrls_1_down_Decode_LEGAL_0_21 == _zz_decode_ctrls_1_down_Decode_LEGAL_0_22)}}}};
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_18 = 32'hffefffff;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_19 = (decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hffffffff);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_20 = 32'h10500073;
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_21 = (decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'hffffffff);
  assign _zz_decode_ctrls_1_down_Decode_LEGAL_0_22 = 32'h30200073;
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard = (DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0 && execute_ctrl3_up_RD_ENABLE_lane0);
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_1 = (execute_ctrl3_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS1_PHYS);
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_2 = ((DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0 && execute_ctrl2_up_RD_ENABLE_lane0) && (execute_ctrl2_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS1_PHYS));
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_3 = 1'b1;
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_4 = ((DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0 && execute_ctrl1_up_RD_ENABLE_lane0) && (execute_ctrl1_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS1_PHYS));
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_5 = 1'b1;
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard = (DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0 && execute_ctrl3_up_RD_ENABLE_lane0);
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_1 = (execute_ctrl3_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS);
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_2 = ((DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0 && execute_ctrl2_up_RD_ENABLE_lane0) && (execute_ctrl2_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS));
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_3 = 1'b1;
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_4 = ((DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0 && execute_ctrl1_up_RD_ENABLE_lane0) && (execute_ctrl1_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS));
  assign _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_5 = 1'b1;
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_1 = (_zz_CsrRamPlugin_csrMapper_ramAddress & 12'h802);
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_2 = 12'h002;
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_3 = (_zz_CsrRamPlugin_csrMapper_ramAddress & 12'h042);
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_4 = 12'h0;
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_5 = (_zz_CsrRamPlugin_csrMapper_ramAddress & 12'h080);
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_6 = 12'h080;
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_7 = (_zz_CsrRamPlugin_csrMapper_ramAddress & 12'h003);
  assign _zz_CsrRamPlugin_csrMapper_ramAddress_8 = 12'h001;
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception = 1'b0;
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1 = 1'b0;
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_2 = (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_2 && 1'b0);
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_3 = (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid && 1'b0);
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_4 = (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_2 && 1'b0);
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_5 = (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_1 && 1'b0);
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_1 = {early0_EnvPlugin_logic_trapPort_payload_tval,early0_EnvPlugin_logic_trapPort_payload_exception};
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_2 = {LsuCachelessPlugin_logic_trapPort_payload_tval,LsuCachelessPlugin_logic_trapPort_payload_exception};
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_3 = CsrAccessPlugin_logic_trapPort_payload_exception;
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception = {TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_tval,TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception};
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception_1 = {TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_tval,TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_exception};
  assign _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception_2 = TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_exception;
  assign _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA = 5'h07;
  assign _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_1 = (TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h05);
  assign _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_2 = (TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h01);
  assign _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_3 = {(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h06),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h04),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h0),(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h03)}}};
  assign _zz_COMB_CSR_UNAMED_1 = 12'hc1f;
  assign _zz_COMB_CSR_UNAMED_1_1 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb1f);
  assign _zz_COMB_CSR_UNAMED_1_2 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h73e);
  assign _zz_COMB_CSR_UNAMED_1_3 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc9e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb9e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h33e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc1e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb1e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_4),{_zz_COMB_CSR_UNAMED_1_5,{_zz_COMB_CSR_UNAMED_1_6,_zz_COMB_CSR_UNAMED_1_7}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_4 = 12'h73d;
  assign _zz_COMB_CSR_UNAMED_1_5 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc9d);
  assign _zz_COMB_CSR_UNAMED_1_6 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb9d);
  assign _zz_COMB_CSR_UNAMED_1_7 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h33d),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc1d),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb1d),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h73c),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc9c),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_8),{_zz_COMB_CSR_UNAMED_1_9,{_zz_COMB_CSR_UNAMED_1_10,_zz_COMB_CSR_UNAMED_1_11}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_8 = 12'hb9c;
  assign _zz_COMB_CSR_UNAMED_1_9 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h33c);
  assign _zz_COMB_CSR_UNAMED_1_10 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc1c);
  assign _zz_COMB_CSR_UNAMED_1_11 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb1c),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h73b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc9b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb9b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h33b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_12),{_zz_COMB_CSR_UNAMED_1_13,{_zz_COMB_CSR_UNAMED_1_14,_zz_COMB_CSR_UNAMED_1_15}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_12 = 12'hc1b;
  assign _zz_COMB_CSR_UNAMED_1_13 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb1b);
  assign _zz_COMB_CSR_UNAMED_1_14 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h73a);
  assign _zz_COMB_CSR_UNAMED_1_15 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc9a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb9a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h33a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc1a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb1a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_16),{_zz_COMB_CSR_UNAMED_1_17,{_zz_COMB_CSR_UNAMED_1_18,_zz_COMB_CSR_UNAMED_1_19}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_16 = 12'h739;
  assign _zz_COMB_CSR_UNAMED_1_17 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc99);
  assign _zz_COMB_CSR_UNAMED_1_18 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb99);
  assign _zz_COMB_CSR_UNAMED_1_19 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h339),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc19),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb19),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h738),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc98),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_20),{_zz_COMB_CSR_UNAMED_1_21,{_zz_COMB_CSR_UNAMED_1_22,_zz_COMB_CSR_UNAMED_1_23}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_20 = 12'hb98;
  assign _zz_COMB_CSR_UNAMED_1_21 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h338);
  assign _zz_COMB_CSR_UNAMED_1_22 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc18);
  assign _zz_COMB_CSR_UNAMED_1_23 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb18),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h737),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc97),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb97),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h337),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_24),{_zz_COMB_CSR_UNAMED_1_25,{_zz_COMB_CSR_UNAMED_1_26,_zz_COMB_CSR_UNAMED_1_27}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_24 = 12'hc17;
  assign _zz_COMB_CSR_UNAMED_1_25 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb17);
  assign _zz_COMB_CSR_UNAMED_1_26 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h736);
  assign _zz_COMB_CSR_UNAMED_1_27 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc96),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb96),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h336),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc16),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb16),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_28),{_zz_COMB_CSR_UNAMED_1_29,{_zz_COMB_CSR_UNAMED_1_30,_zz_COMB_CSR_UNAMED_1_31}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_28 = 12'h735;
  assign _zz_COMB_CSR_UNAMED_1_29 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc95);
  assign _zz_COMB_CSR_UNAMED_1_30 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb95);
  assign _zz_COMB_CSR_UNAMED_1_31 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h335),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc15),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb15),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h734),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc94),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_32),{_zz_COMB_CSR_UNAMED_1_33,{_zz_COMB_CSR_UNAMED_1_34,_zz_COMB_CSR_UNAMED_1_35}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_32 = 12'hb94;
  assign _zz_COMB_CSR_UNAMED_1_33 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h334);
  assign _zz_COMB_CSR_UNAMED_1_34 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc14);
  assign _zz_COMB_CSR_UNAMED_1_35 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb14),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h733),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc93),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb93),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h333),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_36),{_zz_COMB_CSR_UNAMED_1_37,{_zz_COMB_CSR_UNAMED_1_38,_zz_COMB_CSR_UNAMED_1_39}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_36 = 12'hc13;
  assign _zz_COMB_CSR_UNAMED_1_37 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb13);
  assign _zz_COMB_CSR_UNAMED_1_38 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h732);
  assign _zz_COMB_CSR_UNAMED_1_39 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc92),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb92),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h332),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc12),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb12),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_40),{_zz_COMB_CSR_UNAMED_1_41,{_zz_COMB_CSR_UNAMED_1_42,_zz_COMB_CSR_UNAMED_1_43}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_40 = 12'h731;
  assign _zz_COMB_CSR_UNAMED_1_41 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc91);
  assign _zz_COMB_CSR_UNAMED_1_42 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb91);
  assign _zz_COMB_CSR_UNAMED_1_43 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h331),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc11),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb11),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h730),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc90),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_44),{_zz_COMB_CSR_UNAMED_1_45,{_zz_COMB_CSR_UNAMED_1_46,_zz_COMB_CSR_UNAMED_1_47}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_44 = 12'hb90;
  assign _zz_COMB_CSR_UNAMED_1_45 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h330);
  assign _zz_COMB_CSR_UNAMED_1_46 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc10);
  assign _zz_COMB_CSR_UNAMED_1_47 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb10),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h72f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc8f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb8f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h32f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_48),{_zz_COMB_CSR_UNAMED_1_49,{_zz_COMB_CSR_UNAMED_1_50,_zz_COMB_CSR_UNAMED_1_51}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_48 = 12'hc0f;
  assign _zz_COMB_CSR_UNAMED_1_49 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb0f);
  assign _zz_COMB_CSR_UNAMED_1_50 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h72e);
  assign _zz_COMB_CSR_UNAMED_1_51 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc8e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb8e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h32e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc0e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb0e),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_52),{_zz_COMB_CSR_UNAMED_1_53,{_zz_COMB_CSR_UNAMED_1_54,_zz_COMB_CSR_UNAMED_1_55}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_52 = 12'h72d;
  assign _zz_COMB_CSR_UNAMED_1_53 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc8d);
  assign _zz_COMB_CSR_UNAMED_1_54 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb8d);
  assign _zz_COMB_CSR_UNAMED_1_55 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h32d),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc0d),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb0d),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h72c),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc8c),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_56),{_zz_COMB_CSR_UNAMED_1_57,{_zz_COMB_CSR_UNAMED_1_58,_zz_COMB_CSR_UNAMED_1_59}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_56 = 12'hb8c;
  assign _zz_COMB_CSR_UNAMED_1_57 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h32c);
  assign _zz_COMB_CSR_UNAMED_1_58 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc0c);
  assign _zz_COMB_CSR_UNAMED_1_59 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb0c),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h72b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc8b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb8b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h32b),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_60),{_zz_COMB_CSR_UNAMED_1_61,{_zz_COMB_CSR_UNAMED_1_62,_zz_COMB_CSR_UNAMED_1_63}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_60 = 12'hc0b;
  assign _zz_COMB_CSR_UNAMED_1_61 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb0b);
  assign _zz_COMB_CSR_UNAMED_1_62 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h72a);
  assign _zz_COMB_CSR_UNAMED_1_63 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc8a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb8a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h32a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc0a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb0a),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_64),{_zz_COMB_CSR_UNAMED_1_65,{_zz_COMB_CSR_UNAMED_1_66,_zz_COMB_CSR_UNAMED_1_67}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_64 = 12'h729;
  assign _zz_COMB_CSR_UNAMED_1_65 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc89);
  assign _zz_COMB_CSR_UNAMED_1_66 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb89);
  assign _zz_COMB_CSR_UNAMED_1_67 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h329),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc09),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb09),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h728),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc88),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_68),{_zz_COMB_CSR_UNAMED_1_69,{_zz_COMB_CSR_UNAMED_1_70,_zz_COMB_CSR_UNAMED_1_71}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_68 = 12'hb88;
  assign _zz_COMB_CSR_UNAMED_1_69 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h328);
  assign _zz_COMB_CSR_UNAMED_1_70 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc08);
  assign _zz_COMB_CSR_UNAMED_1_71 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb08),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h727),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc87),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb87),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h327),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_72),{_zz_COMB_CSR_UNAMED_1_73,{_zz_COMB_CSR_UNAMED_1_74,_zz_COMB_CSR_UNAMED_1_75}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_72 = 12'hc07;
  assign _zz_COMB_CSR_UNAMED_1_73 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb07);
  assign _zz_COMB_CSR_UNAMED_1_74 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h726);
  assign _zz_COMB_CSR_UNAMED_1_75 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc86),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb86),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h326),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc06),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb06),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_76),{_zz_COMB_CSR_UNAMED_1_77,{_zz_COMB_CSR_UNAMED_1_78,_zz_COMB_CSR_UNAMED_1_79}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_76 = 12'h725;
  assign _zz_COMB_CSR_UNAMED_1_77 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc85);
  assign _zz_COMB_CSR_UNAMED_1_78 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb85);
  assign _zz_COMB_CSR_UNAMED_1_79 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h325),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc05),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb05),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h724),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc84),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1_80),{_zz_COMB_CSR_UNAMED_1_81,{_zz_COMB_CSR_UNAMED_1_82,_zz_COMB_CSR_UNAMED_1_83}}}}}}}};
  assign _zz_COMB_CSR_UNAMED_1_80 = 12'hb84;
  assign _zz_COMB_CSR_UNAMED_1_81 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h324);
  assign _zz_COMB_CSR_UNAMED_1_82 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc04);
  assign _zz_COMB_CSR_UNAMED_1_83 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb04),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h723),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc83),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb83),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h323),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc03),(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb03)}}}}}};
  assign _zz_COMB_CSR_PerformanceCounterPlugin_logic_csrFilter = 12'hc00;
  assign _zz_COMB_CSR_PerformanceCounterPlugin_logic_csrFilter_1 = 12'hb00;
  assign _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter = 12'hc00;
  assign _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_1 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb00);
  assign _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_2 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h340);
  assign _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_3 = {(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h341),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h343),(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h305)}};
  assign _zz_CsrAccessPlugin_logic_fsm_inject_implemented = COMB_CSR_778;
  assign _zz_CsrAccessPlugin_logic_fsm_inject_implemented_1 = {COMB_CSR_794,{COMB_CSR_4016,{COMB_CSR_772,{COMB_CSR_836,{COMB_CSR_834,{COMB_CSR_768,{COMB_CSR_769,{COMB_CSR_3860,{COMB_CSR_3859,{COMB_CSR_3858,{_zz_CsrAccessPlugin_logic_fsm_inject_implemented_2,_zz_CsrAccessPlugin_logic_fsm_inject_implemented_3}}}}}}}}}}};
  assign _zz_CsrAccessPlugin_logic_fsm_inject_implemented_2 = COMB_CSR_3857;
  assign _zz_CsrAccessPlugin_logic_fsm_inject_implemented_3 = {COMB_CSR_1954,{COMB_CSR_1953,COMB_CSR_1952}};
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_9 = 32'h0;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_12 = 32'h0;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_13 = 32'h0;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_14 = ((_zz_when_CsrService_l221 && REG_CSR_769) ? 32'h40001105 : 32'h0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_46 = ((_zz_when_CsrService_l221 && REG_CSR_3073) ? PrivilegedPlugin_logic_rdtime[31 : 0] : 32'h0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_47 = ((_zz_when_CsrService_l221 && REG_CSR_3201) ? PrivilegedPlugin_logic_rdtime[63 : 32] : 32'h0);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_60 = 32'h0;
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_2 = 32'h00002010;
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_3 = (execute_lane0_logic_decoding_decodingBits & 32'h00002008);
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_4 = 32'h00002008;
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_5 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000050) == 32'h00000010);
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_6 = _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_1;
  assign _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0_7 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000028) == 32'h0);
  assign _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 = 32'h00002020;
  assign _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0_1 = (execute_lane0_logic_decoding_decodingBits & 32'h08002000);
  assign _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0_2 = 32'h00002000;
  assign _zz_when_ExecuteLanePlugin_l306_2 = 1'b1;
  assign _zz_when_ExecuteLanePlugin_l306_2_1 = 1'b1;
  assign _zz_when_ExecuteLanePlugin_l306_2_2 = 1'b0;
  assign _zz_when_ExecuteLanePlugin_l306_2_3 = (1'b1 && early0_BranchPlugin_logic_flushPort_payload_self);
  assign _zz_when_ExecuteLanePlugin_l306_2_4 = 1'b1;
  assign _zz_when_ExecuteLanePlugin_l306_2_5 = 1'b0;
  assign _zz_when_ExecuteLanePlugin_l306_2_6 = (1'b1 && LsuCachelessPlugin_logic_flushPort_payload_self);
  always @(posedge clk) begin
    if(_zz_2) begin
      FetchCachelessPlugin_logic_buffer_words[FetchCachelessPlugin_logic_buffer_write_payload_address] <= _zz_FetchCachelessPlugin_logic_buffer_words_port;
    end
  end

  assign FetchCachelessPlugin_logic_buffer_words_spinal_port1 = FetchCachelessPlugin_logic_buffer_words[fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_BUFFER_ID];
  always @(posedge clk) begin
    if(_zz_1) begin
      CsrRamPlugin_logic_mem[CsrRamPlugin_logic_writeLogic_port_payload_address] <= CsrRamPlugin_logic_writeLogic_port_payload_data;
    end
  end

  always @(posedge clk) begin
    if(CsrRamPlugin_logic_readLogic_port_cmd_valid) begin
      CsrRamPlugin_logic_mem_spinal_port1 <= CsrRamPlugin_logic_mem[CsrRamPlugin_logic_readLogic_port_cmd_payload];
    end
  end

  DivRadix early0_DivPlugin_logic_processing_div (
    .io_flush                  (execute_ctrl2_down_isReady                                       ), //i
    .io_cmd_valid              (early0_DivPlugin_logic_processing_div_io_cmd_valid               ), //i
    .io_cmd_ready              (early0_DivPlugin_logic_processing_div_io_cmd_ready               ), //o
    .io_cmd_payload_a          (early0_DivPlugin_logic_processing_a[31:0]                        ), //i
    .io_cmd_payload_b          (early0_DivPlugin_logic_processing_b[31:0]                        ), //i
    .io_cmd_payload_normalized (1'b0                                                             ), //i
    .io_cmd_payload_iterations (5'bxxxxx                                                         ), //i
    .io_rsp_valid              (early0_DivPlugin_logic_processing_div_io_rsp_valid               ), //o
    .io_rsp_ready              (1'b0                                                             ), //i
    .io_rsp_payload_result     (early0_DivPlugin_logic_processing_div_io_rsp_payload_result[31:0]), //o
    .io_rsp_payload_remain     (early0_DivPlugin_logic_processing_div_io_rsp_payload_remain[31:0]), //o
    .clk                       (clk                                                              ), //i
    .reset                     (reset                                                            )  //i
  );
  StreamArbiter streamArbiter_1 (
    .io_inputs_0_valid                      (LearnPlugin_logic_buffered_0_valid                      ), //i
    .io_inputs_0_ready                      (streamArbiter_1_io_inputs_0_ready                       ), //o
    .io_inputs_0_payload_pcOnLastSlice      (LearnPlugin_logic_buffered_0_payload_pcOnLastSlice[31:0]), //i
    .io_inputs_0_payload_pcTarget           (LearnPlugin_logic_buffered_0_payload_pcTarget[31:0]     ), //i
    .io_inputs_0_payload_taken              (LearnPlugin_logic_buffered_0_payload_taken              ), //i
    .io_inputs_0_payload_isBranch           (LearnPlugin_logic_buffered_0_payload_isBranch           ), //i
    .io_inputs_0_payload_isPush             (LearnPlugin_logic_buffered_0_payload_isPush             ), //i
    .io_inputs_0_payload_isPop              (LearnPlugin_logic_buffered_0_payload_isPop              ), //i
    .io_inputs_0_payload_wasWrong           (LearnPlugin_logic_buffered_0_payload_wasWrong           ), //i
    .io_inputs_0_payload_badPredictedTarget (LearnPlugin_logic_buffered_0_payload_badPredictedTarget ), //i
    .io_inputs_0_payload_uopId              (LearnPlugin_logic_buffered_0_payload_uopId[15:0]        ), //i
    .io_output_valid                        (streamArbiter_1_io_output_valid                         ), //o
    .io_output_ready                        (LearnPlugin_logic_arbitrated_ready                      ), //i
    .io_output_payload_pcOnLastSlice        (streamArbiter_1_io_output_payload_pcOnLastSlice[31:0]   ), //o
    .io_output_payload_pcTarget             (streamArbiter_1_io_output_payload_pcTarget[31:0]        ), //o
    .io_output_payload_taken                (streamArbiter_1_io_output_payload_taken                 ), //o
    .io_output_payload_isBranch             (streamArbiter_1_io_output_payload_isBranch              ), //o
    .io_output_payload_isPush               (streamArbiter_1_io_output_payload_isPush                ), //o
    .io_output_payload_isPop                (streamArbiter_1_io_output_payload_isPop                 ), //o
    .io_output_payload_wasWrong             (streamArbiter_1_io_output_payload_wasWrong              ), //o
    .io_output_payload_badPredictedTarget   (streamArbiter_1_io_output_payload_badPredictedTarget    ), //o
    .io_output_payload_uopId                (streamArbiter_1_io_output_payload_uopId[15:0]           ), //o
    .io_chosenOH                            (streamArbiter_1_io_chosenOH                             )  //o
  );
  RegFileMem integer_RegFilePlugin_logic_regfile_fpga (
    .io_writes_0_valid   (integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_valid       ), //i
    .io_writes_0_address (integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_address[4:0]), //i
    .io_writes_0_data    (integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_data[31:0]  ), //i
    .io_writes_0_uopId   (integer_RegFilePlugin_logic_writeMerges_0_bus_uopId[15:0]        ), //i
    .io_reads_0_valid    (execute_lane0_bypasser_integer_RS1_port_valid                    ), //i
    .io_reads_0_address  (execute_lane0_bypasser_integer_RS1_port_address[4:0]             ), //i
    .io_reads_0_data     (integer_RegFilePlugin_logic_regfile_fpga_io_reads_0_data[31:0]   ), //o
    .io_reads_1_valid    (execute_lane0_bypasser_integer_RS2_port_valid                    ), //i
    .io_reads_1_address  (execute_lane0_bypasser_integer_RS2_port_address[4:0]             ), //i
    .io_reads_1_data     (integer_RegFilePlugin_logic_regfile_fpga_io_reads_1_data[31:0]   ), //o
    .clk                 (clk                                                              ), //i
    .reset               (reset                                                            )  //i
  );
  always @(*) begin
    case(FetchCachelessPlugin_logic_buffer_reserveId_value)
      1'b0 : _zz_FetchCachelessPlugin_logic_buffer_full = FetchCachelessPlugin_logic_buffer_inflight_0;
      default : _zz_FetchCachelessPlugin_logic_buffer_full = FetchCachelessPlugin_logic_buffer_inflight_1;
    endcase
  end

  always @(*) begin
    case(_zz_PerformanceCounterPlugin_logic_commitCount_1)
      1'b0 : _zz_PerformanceCounterPlugin_logic_commitCount = 1'b0;
      default : _zz_PerformanceCounterPlugin_logic_commitCount = 1'b1;
    endcase
  end

  always @(*) begin
    case(_zz_fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK_1)
      1'b0 : _zz_fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK = AlignerPlugin_logic_maskGen_frontMasks_0;
      default : _zz_fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK = AlignerPlugin_logic_maskGen_frontMasks_1;
    endcase
  end

  always @(*) begin
    case(_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_28)
      2'b00 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_27 = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22;
      2'b01 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_27 = (_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22 | 32'h40000000);
      2'b10 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_27 = {{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5,_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},3'b111},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},7'h13};
      default : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_27 = ({{{{{7'h0,_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_1},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},(AlignerPlugin_logic_extractors_0_ctx_instruction[12] ? 7'h3b : 7'h33)} | ((AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 5] == 2'b00) ? 32'h40000000 : 32'h0));
    endcase
  end

  always @(*) begin
    case(_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_30)
      3'b000 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b000;
      3'b001 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b100;
      3'b010 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b110;
      3'b011 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b111;
      3'b100 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b000;
      3'b101 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b000;
      3'b110 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b010;
      default : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_29 = 3'b011;
    endcase
  end

  always @(*) begin
    case(LsuCachelessPlugin_logic_onJoin_rspCounter_value)
      1'b0 : begin
        _zz_LsuCachelessPlugin_logic_onJoin_readerValid = LsuCachelessPlugin_logic_onJoin_buffers_0_valid;
        _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_error = LsuCachelessPlugin_logic_onJoin_buffers_0_payload_error;
        _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_data = LsuCachelessPlugin_logic_onJoin_buffers_0_payload_data;
        _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_scMiss = LsuCachelessPlugin_logic_onJoin_buffers_0_payload_scMiss;
        _zz_LsuCachelessPlugin_logic_onJoin_rspAddress = LsuCachelessPlugin_logic_onJoin_buffers_0_address;
      end
      default : begin
        _zz_LsuCachelessPlugin_logic_onJoin_readerValid = LsuCachelessPlugin_logic_onJoin_buffers_1_valid;
        _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_error = LsuCachelessPlugin_logic_onJoin_buffers_1_payload_error;
        _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_data = LsuCachelessPlugin_logic_onJoin_buffers_1_payload_data;
        _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_scMiss = LsuCachelessPlugin_logic_onJoin_buffers_1_payload_scMiss;
        _zz_LsuCachelessPlugin_logic_onJoin_rspAddress = LsuCachelessPlugin_logic_onJoin_buffers_1_address;
      end
    endcase
  end

  always @(*) begin
    case(_zz_LsuCachelessPlugin_logic_onWb_rspShifted_1)
      2'b00 : _zz_LsuCachelessPlugin_logic_onWb_rspShifted = LsuCachelessPlugin_logic_onWb_rspSplits_0;
      2'b01 : _zz_LsuCachelessPlugin_logic_onWb_rspShifted = LsuCachelessPlugin_logic_onWb_rspSplits_1;
      2'b10 : _zz_LsuCachelessPlugin_logic_onWb_rspShifted = LsuCachelessPlugin_logic_onWb_rspSplits_2;
      default : _zz_LsuCachelessPlugin_logic_onWb_rspShifted = LsuCachelessPlugin_logic_onWb_rspSplits_3;
    endcase
  end

  always @(*) begin
    case(_zz_LsuCachelessPlugin_logic_onWb_rspShifted_4)
      1'b0 : _zz_LsuCachelessPlugin_logic_onWb_rspShifted_3 = LsuCachelessPlugin_logic_onWb_rspSplits_1;
      default : _zz_LsuCachelessPlugin_logic_onWb_rspShifted_3 = LsuCachelessPlugin_logic_onWb_rspSplits_3;
    endcase
  end

  always @(*) begin
    case(_zz_PerformanceCounterPlugin_logic_events_sums_0_2)
      1'b0 : _zz_PerformanceCounterPlugin_logic_events_sums_0_1 = 1'b0;
      default : _zz_PerformanceCounterPlugin_logic_events_sums_0_1 = 1'b1;
    endcase
  end

  always @(*) begin
    case(_zz_PerformanceCounterPlugin_logic_events_sums_1_2)
      1'b0 : _zz_PerformanceCounterPlugin_logic_events_sums_1_1 = 1'b0;
      default : _zz_PerformanceCounterPlugin_logic_events_sums_1_1 = 1'b1;
    endcase
  end

  always @(*) begin
    case(_zz_PerformanceCounterPlugin_logic_events_sums_2_2)
      1'b0 : _zz_PerformanceCounterPlugin_logic_events_sums_2_1 = 1'b0;
      default : _zz_PerformanceCounterPlugin_logic_events_sums_2_1 = 1'b1;
    endcase
  end

  always @(*) begin
    case(_zz_PerformanceCounterPlugin_logic_events_sums_3_2)
      1'b0 : _zz_PerformanceCounterPlugin_logic_events_sums_3_1 = 1'b0;
      default : _zz_PerformanceCounterPlugin_logic_events_sums_3_1 = 1'b1;
    endcase
  end

  always @(*) begin
    case(_zz_PerformanceCounterPlugin_logic_events_sums_4_2)
      1'b0 : _zz_PerformanceCounterPlugin_logic_events_sums_4_1 = 1'b0;
      default : _zz_PerformanceCounterPlugin_logic_events_sums_4_1 = 1'b1;
    endcase
  end

  always @(*) begin
    case(_zz_PerformanceCounterPlugin_logic_events_sums_5_2)
      1'b0 : _zz_PerformanceCounterPlugin_logic_events_sums_5_1 = 1'b0;
      default : _zz_PerformanceCounterPlugin_logic_events_sums_5_1 = 1'b1;
    endcase
  end

  always @(*) begin
    case(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_BUFFER_ID)
      1'b0 : _zz_FetchCachelessPlugin_logic_join_haltIt = FetchCachelessPlugin_logic_buffer_inflight_0;
      default : _zz_FetchCachelessPlugin_logic_join_haltIt = FetchCachelessPlugin_logic_buffer_inflight_1;
    endcase
  end

  always @(*) begin
    case(CsrRamPlugin_logic_readLogic_sel)
      2'b00 : _zz_CsrRamPlugin_logic_readLogic_port_cmd_payload = PerformanceCounterPlugin_logic_readPort_address;
      2'b01 : _zz_CsrRamPlugin_logic_readLogic_port_cmd_payload = TrapPlugin_logic_harts_0_crsPorts_read_address;
      default : _zz_CsrRamPlugin_logic_readLogic_port_cmd_payload = CsrRamPlugin_csrMapper_read_address;
    endcase
  end

  always @(*) begin
    case(_zz_WhiteboxerPlugin_logic_perf_candidatesCount_1)
      1'b0 : _zz_WhiteboxerPlugin_logic_perf_candidatesCount = 1'b0;
      default : _zz_WhiteboxerPlugin_logic_perf_candidatesCount = 1'b1;
    endcase
  end

  always @(*) begin
    case(_zz_WhiteboxerPlugin_logic_perf_dispatchFeedCount_1)
      1'b0 : _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCount = 1'b0;
      default : _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCount = 1'b1;
    endcase
  end

  `ifndef SYNTHESIS
  always @(*) begin
    case(execute_ctrl2_up_early0_EnvPlugin_OP_lane0)
      EnvPluginOp_ECALL : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "ECALL      ";
      EnvPluginOp_EBREAK : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "EBREAK     ";
      EnvPluginOp_PRIV_RET : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "PRIV_RET   ";
      EnvPluginOp_FENCE_I : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "FENCE_I    ";
      EnvPluginOp_SFENCE_VMA : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "SFENCE_VMA ";
      EnvPluginOp_WFI : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "WFI        ";
      EnvPluginOp_HFENCE_GVMA : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "HFENCE_GVMA";
      EnvPluginOp_HFENCE_VVMA : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "HFENCE_VVMA";
      default : execute_ctrl2_up_early0_EnvPlugin_OP_lane0_string = "???????????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0)
      BranchPlugin_BranchCtrlEnum_B : execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0_string = "B   ";
      BranchPlugin_BranchCtrlEnum_JAL : execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0_string = "JAL ";
      BranchPlugin_BranchCtrlEnum_JALR : execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0_string = "JALR";
      default : execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0_string = "????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0)
      IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 : execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "XOR_1";
      IntAluPlugin_AluBitwiseCtrlEnum_OR_1 : execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "OR_1 ";
      IntAluPlugin_AluBitwiseCtrlEnum_AND_1 : execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "AND_1";
      IntAluPlugin_AluBitwiseCtrlEnum_ZERO : execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "ZERO ";
      default : execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "?????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl1_down_early0_EnvPlugin_OP_lane0)
      EnvPluginOp_ECALL : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "ECALL      ";
      EnvPluginOp_EBREAK : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "EBREAK     ";
      EnvPluginOp_PRIV_RET : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "PRIV_RET   ";
      EnvPluginOp_FENCE_I : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "FENCE_I    ";
      EnvPluginOp_SFENCE_VMA : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "SFENCE_VMA ";
      EnvPluginOp_WFI : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "WFI        ";
      EnvPluginOp_HFENCE_GVMA : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "HFENCE_GVMA";
      EnvPluginOp_HFENCE_VVMA : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "HFENCE_VVMA";
      default : execute_ctrl1_down_early0_EnvPlugin_OP_lane0_string = "???????????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0)
      BranchPlugin_BranchCtrlEnum_B : execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "B   ";
      BranchPlugin_BranchCtrlEnum_JAL : execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "JAL ";
      BranchPlugin_BranchCtrlEnum_JALR : execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "JALR";
      default : execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0)
      IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 : execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "XOR_1";
      IntAluPlugin_AluBitwiseCtrlEnum_OR_1 : execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "OR_1 ";
      IntAluPlugin_AluBitwiseCtrlEnum_AND_1 : execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "AND_1";
      IntAluPlugin_AluBitwiseCtrlEnum_ZERO : execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "ZERO ";
      default : execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "?????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0)
      BranchPlugin_BranchCtrlEnum_B : execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0_string = "B   ";
      BranchPlugin_BranchCtrlEnum_JAL : execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0_string = "JAL ";
      BranchPlugin_BranchCtrlEnum_JALR : execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0_string = "JALR";
      default : execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0_string = "????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl2_down_early0_EnvPlugin_OP_lane0)
      EnvPluginOp_ECALL : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "ECALL      ";
      EnvPluginOp_EBREAK : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "EBREAK     ";
      EnvPluginOp_PRIV_RET : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "PRIV_RET   ";
      EnvPluginOp_FENCE_I : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "FENCE_I    ";
      EnvPluginOp_SFENCE_VMA : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "SFENCE_VMA ";
      EnvPluginOp_WFI : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "WFI        ";
      EnvPluginOp_HFENCE_GVMA : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "HFENCE_GVMA";
      EnvPluginOp_HFENCE_VVMA : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "HFENCE_VVMA";
      default : execute_ctrl2_down_early0_EnvPlugin_OP_lane0_string = "???????????";
    endcase
  end
  always @(*) begin
    case(execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0)
      IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 : execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "XOR_1";
      IntAluPlugin_AluBitwiseCtrlEnum_OR_1 : execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "OR_1 ";
      IntAluPlugin_AluBitwiseCtrlEnum_AND_1 : execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "AND_1";
      IntAluPlugin_AluBitwiseCtrlEnum_ZERO : execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "ZERO ";
      default : execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "?????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0)
      IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "XOR_1";
      IntAluPlugin_AluBitwiseCtrlEnum_OR_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "OR_1 ";
      IntAluPlugin_AluBitwiseCtrlEnum_AND_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "AND_1";
      IntAluPlugin_AluBitwiseCtrlEnum_ZERO : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "ZERO ";
      default : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_string = "?????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1)
      IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1_string = "XOR_1";
      IntAluPlugin_AluBitwiseCtrlEnum_OR_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1_string = "OR_1 ";
      IntAluPlugin_AluBitwiseCtrlEnum_AND_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1_string = "AND_1";
      IntAluPlugin_AluBitwiseCtrlEnum_ZERO : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1_string = "ZERO ";
      default : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1_string = "?????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2)
      IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2_string = "XOR_1";
      IntAluPlugin_AluBitwiseCtrlEnum_OR_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2_string = "OR_1 ";
      IntAluPlugin_AluBitwiseCtrlEnum_AND_1 : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2_string = "AND_1";
      IntAluPlugin_AluBitwiseCtrlEnum_ZERO : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2_string = "ZERO ";
      default : _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2_string = "?????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0)
      BranchPlugin_BranchCtrlEnum_B : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "B   ";
      BranchPlugin_BranchCtrlEnum_JAL : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "JAL ";
      BranchPlugin_BranchCtrlEnum_JALR : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "JALR";
      default : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_string = "????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1)
      BranchPlugin_BranchCtrlEnum_B : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1_string = "B   ";
      BranchPlugin_BranchCtrlEnum_JAL : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1_string = "JAL ";
      BranchPlugin_BranchCtrlEnum_JALR : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1_string = "JALR";
      default : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1_string = "????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2)
      BranchPlugin_BranchCtrlEnum_B : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2_string = "B   ";
      BranchPlugin_BranchCtrlEnum_JAL : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2_string = "JAL ";
      BranchPlugin_BranchCtrlEnum_JALR : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2_string = "JALR";
      default : _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2_string = "????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1)
      EnvPluginOp_ECALL : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "ECALL      ";
      EnvPluginOp_EBREAK : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "EBREAK     ";
      EnvPluginOp_PRIV_RET : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "PRIV_RET   ";
      EnvPluginOp_FENCE_I : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "FENCE_I    ";
      EnvPluginOp_SFENCE_VMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "SFENCE_VMA ";
      EnvPluginOp_WFI : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "WFI        ";
      EnvPluginOp_HFENCE_GVMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "HFENCE_GVMA";
      EnvPluginOp_HFENCE_VVMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "HFENCE_VVMA";
      default : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1_string = "???????????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2)
      EnvPluginOp_ECALL : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "ECALL      ";
      EnvPluginOp_EBREAK : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "EBREAK     ";
      EnvPluginOp_PRIV_RET : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "PRIV_RET   ";
      EnvPluginOp_FENCE_I : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "FENCE_I    ";
      EnvPluginOp_SFENCE_VMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "SFENCE_VMA ";
      EnvPluginOp_WFI : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "WFI        ";
      EnvPluginOp_HFENCE_GVMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "HFENCE_GVMA";
      EnvPluginOp_HFENCE_VVMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "HFENCE_VVMA";
      default : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2_string = "???????????";
    endcase
  end
  always @(*) begin
    case(_zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3)
      EnvPluginOp_ECALL : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "ECALL      ";
      EnvPluginOp_EBREAK : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "EBREAK     ";
      EnvPluginOp_PRIV_RET : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "PRIV_RET   ";
      EnvPluginOp_FENCE_I : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "FENCE_I    ";
      EnvPluginOp_SFENCE_VMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "SFENCE_VMA ";
      EnvPluginOp_WFI : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "WFI        ";
      EnvPluginOp_HFENCE_GVMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "HFENCE_GVMA";
      EnvPluginOp_HFENCE_VVMA : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "HFENCE_VVMA";
      default : _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3_string = "???????????";
    endcase
  end
  always @(*) begin
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_BOOT : PerformanceCounterPlugin_logic_fsm_stateReg_string = "BOOT     ";
      PerformanceCounterPlugin_logic_fsm_IDLE : PerformanceCounterPlugin_logic_fsm_stateReg_string = "IDLE     ";
      PerformanceCounterPlugin_logic_fsm_READ_LOW : PerformanceCounterPlugin_logic_fsm_stateReg_string = "READ_LOW ";
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : PerformanceCounterPlugin_logic_fsm_stateReg_string = "CALC_LOW ";
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : PerformanceCounterPlugin_logic_fsm_stateReg_string = "READ_HIGH";
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : PerformanceCounterPlugin_logic_fsm_stateReg_string = "CALC_HIGH";
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : PerformanceCounterPlugin_logic_fsm_stateReg_string = "CSR_WRITE";
      default : PerformanceCounterPlugin_logic_fsm_stateReg_string = "?????????";
    endcase
  end
  always @(*) begin
    case(PerformanceCounterPlugin_logic_fsm_stateNext)
      PerformanceCounterPlugin_logic_fsm_BOOT : PerformanceCounterPlugin_logic_fsm_stateNext_string = "BOOT     ";
      PerformanceCounterPlugin_logic_fsm_IDLE : PerformanceCounterPlugin_logic_fsm_stateNext_string = "IDLE     ";
      PerformanceCounterPlugin_logic_fsm_READ_LOW : PerformanceCounterPlugin_logic_fsm_stateNext_string = "READ_LOW ";
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : PerformanceCounterPlugin_logic_fsm_stateNext_string = "CALC_LOW ";
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : PerformanceCounterPlugin_logic_fsm_stateNext_string = "READ_HIGH";
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : PerformanceCounterPlugin_logic_fsm_stateNext_string = "CALC_HIGH";
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : PerformanceCounterPlugin_logic_fsm_stateNext_string = "CSR_WRITE";
      default : PerformanceCounterPlugin_logic_fsm_stateNext_string = "?????????";
    endcase
  end
  always @(*) begin
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RESET : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "RESET      ";
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "RUNNING    ";
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "COMPUTE    ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "TRAP_EPC   ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "TRAP_TVAL  ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "TRAP_HTVAL ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "TRAP_HTINST";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "TRAP_TVEC  ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "TRAP_WAIT  ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "TRAP_APPLY ";
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "XRET_EPC   ";
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "XRET_APPLY ";
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "JUMP       ";
      default : TrapPlugin_logic_harts_0_trap_fsm_stateReg_string = "???????????";
    endcase
  end
  always @(*) begin
    case(TrapPlugin_logic_harts_0_trap_fsm_stateNext)
      TrapPlugin_logic_harts_0_trap_fsm_RESET : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "RESET      ";
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "RUNNING    ";
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "COMPUTE    ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "TRAP_EPC   ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "TRAP_TVAL  ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "TRAP_HTVAL ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "TRAP_HTINST";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "TRAP_TVEC  ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "TRAP_WAIT  ";
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "TRAP_APPLY ";
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "XRET_EPC   ";
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "XRET_APPLY ";
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "JUMP       ";
      default : TrapPlugin_logic_harts_0_trap_fsm_stateNext_string = "???????????";
    endcase
  end
  always @(*) begin
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_IDLE : CsrAccessPlugin_logic_fsm_stateReg_string = "IDLE      ";
      CsrAccessPlugin_logic_fsm_READ : CsrAccessPlugin_logic_fsm_stateReg_string = "READ      ";
      CsrAccessPlugin_logic_fsm_WRITE : CsrAccessPlugin_logic_fsm_stateReg_string = "WRITE     ";
      CsrAccessPlugin_logic_fsm_COMPLETION : CsrAccessPlugin_logic_fsm_stateReg_string = "COMPLETION";
      default : CsrAccessPlugin_logic_fsm_stateReg_string = "??????????";
    endcase
  end
  always @(*) begin
    case(CsrAccessPlugin_logic_fsm_stateNext)
      CsrAccessPlugin_logic_fsm_IDLE : CsrAccessPlugin_logic_fsm_stateNext_string = "IDLE      ";
      CsrAccessPlugin_logic_fsm_READ : CsrAccessPlugin_logic_fsm_stateNext_string = "READ      ";
      CsrAccessPlugin_logic_fsm_WRITE : CsrAccessPlugin_logic_fsm_stateNext_string = "WRITE     ";
      CsrAccessPlugin_logic_fsm_COMPLETION : CsrAccessPlugin_logic_fsm_stateNext_string = "COMPLETION";
      default : CsrAccessPlugin_logic_fsm_stateNext_string = "??????????";
    endcase
  end
  `endif

  assign execute_ctrl2_down_RD_ENABLE_lane0 = execute_ctrl2_RD_ENABLE_lane0_bypass;
  always @(*) begin
    execute_ctrl2_RD_ENABLE_lane0_bypass = execute_ctrl2_up_RD_ENABLE_lane0;
    if(when_ExecuteLanePlugin_l306_2) begin
      execute_ctrl2_RD_ENABLE_lane0_bypass = 1'b0;
    end
  end

  assign execute_ctrl2_down_LANE_SEL_lane0 = execute_ctrl2_LANE_SEL_lane0_bypass;
  always @(*) begin
    execute_ctrl2_LANE_SEL_lane0_bypass = execute_ctrl2_up_LANE_SEL_lane0;
    if(when_ExecuteLanePlugin_l306_2) begin
      execute_ctrl2_LANE_SEL_lane0_bypass = 1'b0;
    end
  end

  assign execute_ctrl1_down_RD_ENABLE_lane0 = execute_ctrl1_RD_ENABLE_lane0_bypass;
  always @(*) begin
    execute_ctrl1_RD_ENABLE_lane0_bypass = execute_ctrl1_up_RD_ENABLE_lane0;
    if(when_ExecuteLanePlugin_l306_1) begin
      execute_ctrl1_RD_ENABLE_lane0_bypass = 1'b0;
    end
  end

  assign execute_ctrl1_down_LANE_SEL_lane0 = execute_ctrl1_LANE_SEL_lane0_bypass;
  always @(*) begin
    execute_ctrl1_LANE_SEL_lane0_bypass = execute_ctrl1_up_LANE_SEL_lane0;
    if(when_ExecuteLanePlugin_l306_1) begin
      execute_ctrl1_LANE_SEL_lane0_bypass = 1'b0;
    end
  end

  assign execute_ctrl0_down_RD_ENABLE_lane0 = execute_ctrl0_RD_ENABLE_lane0_bypass;
  always @(*) begin
    execute_ctrl0_RD_ENABLE_lane0_bypass = execute_ctrl0_up_RD_ENABLE_lane0;
    if(when_ExecuteLanePlugin_l306) begin
      execute_ctrl0_RD_ENABLE_lane0_bypass = 1'b0;
    end
  end

  assign execute_ctrl0_down_LANE_SEL_lane0 = execute_ctrl0_LANE_SEL_lane0_bypass;
  always @(*) begin
    execute_ctrl0_LANE_SEL_lane0_bypass = execute_ctrl0_up_LANE_SEL_lane0;
    if(when_ExecuteLanePlugin_l306) begin
      execute_ctrl0_LANE_SEL_lane0_bypass = 1'b0;
    end
  end

  always @(*) begin
    _zz_1 = 1'b0;
    if(CsrRamPlugin_logic_writeLogic_port_valid) begin
      _zz_1 = 1'b1;
    end
  end

  assign decode_ctrls_1_down_LANE_SEL_0 = decode_ctrls_1_LANE_SEL_0_bypass;
  always @(*) begin
    decode_ctrls_1_LANE_SEL_0_bypass = decode_ctrls_1_up_LANE_SEL_0;
    if(decode_logic_flushes_1_onLanes_0_doIt) begin
      decode_ctrls_1_LANE_SEL_0_bypass = 1'b0;
    end
  end

  assign decode_ctrls_0_down_LANE_SEL_0 = decode_ctrls_0_LANE_SEL_0_bypass;
  always @(*) begin
    decode_ctrls_0_LANE_SEL_0_bypass = decode_ctrls_0_up_LANE_SEL_0;
    if(decode_logic_flushes_0_onLanes_0_doIt) begin
      decode_ctrls_0_LANE_SEL_0_bypass = 1'b0;
    end
  end

  assign execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 = execute_ctrl4_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;
  assign execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 = execute_ctrl3_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;
  assign execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 = execute_ctrl2_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;
  assign decode_ctrls_1_down_TRAP_0 = decode_ctrls_1_TRAP_0_bypass;
  always @(*) begin
    decode_ctrls_1_TRAP_0_bypass = decode_ctrls_1_up_TRAP_0;
    if(when_DecoderPlugin_l245) begin
      decode_ctrls_1_TRAP_0_bypass = 1'b1;
    end
  end

  assign execute_ctrl3_down_COMPLETED_lane0 = execute_ctrl3_COMPLETED_lane0_bypass;
  assign execute_ctrl4_down_COMPLETED_lane0 = execute_ctrl4_COMPLETED_lane0_bypass;
  assign execute_ctrl2_down_COMPLETED_lane0 = execute_ctrl2_COMPLETED_lane0_bypass;
  assign execute_ctrl2_down_COMMIT_lane0 = execute_ctrl2_COMMIT_lane0_bypass;
  always @(*) begin
    execute_ctrl2_COMMIT_lane0_bypass = execute_ctrl2_up_COMMIT_lane0;
    if(when_EnvPlugin_l203) begin
      if(when_EnvPlugin_l207) begin
        execute_ctrl2_COMMIT_lane0_bypass = 1'b0;
      end
    end
    if(when_LsuCachelessPlugin_l420) begin
      execute_ctrl2_COMMIT_lane0_bypass = 1'b0;
    end
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
          if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
            if(CsrAccessPlugin_logic_fsm_inject_trapReg) begin
              execute_ctrl2_COMMIT_lane0_bypass = 1'b0;
            end
          end
        end
      end
    endcase
  end

  assign execute_ctrl2_down_TRAP_lane0 = execute_ctrl2_TRAP_lane0_bypass;
  always @(*) begin
    execute_ctrl2_TRAP_lane0_bypass = execute_ctrl2_up_TRAP_lane0;
    if(when_EnvPlugin_l203) begin
      execute_ctrl2_TRAP_lane0_bypass = 1'b1;
    end
    if(when_LsuCachelessPlugin_l420) begin
      execute_ctrl2_TRAP_lane0_bypass = 1'b1;
    end
    if(CsrAccessPlugin_logic_fsm_inject_flushReg) begin
      execute_ctrl2_TRAP_lane0_bypass = 1'b1;
    end
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
          if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
            if(CsrAccessPlugin_logic_fsm_inject_trapReg) begin
              execute_ctrl2_TRAP_lane0_bypass = 1'b1;
            end else begin
              if(CsrAccessPlugin_logic_fsm_inject_busTrapReg) begin
                execute_ctrl2_TRAP_lane0_bypass = 1'b1;
              end
            end
          end
        end
      end
    endcase
  end

  assign execute_ctrl2_down_AguPlugin_SIZE_lane0 = execute_ctrl2_AguPlugin_SIZE_lane0_bypass;
  always @(*) begin
    execute_ctrl2_AguPlugin_SIZE_lane0_bypass = execute_ctrl2_up_AguPlugin_SIZE_lane0;
    if(execute_ctrl2_down_AguPlugin_GUEST_lane0) begin
      execute_ctrl2_AguPlugin_SIZE_lane0_bypass = execute_ctrl2_down_Decode_UOP_lane0[27 : 26];
    end
  end

  always @(*) begin
    _zz_2 = 1'b0;
    if(FetchCachelessPlugin_logic_buffer_write_valid) begin
      _zz_2 = 1'b1;
    end
  end

  assign AlignerPlugin_api_singleFetch = 1'b0;
  assign AlignerPlugin_api_haltIt = 1'b0;
  assign DispatchPlugin_api_haltDispatch = 1'b0;
  always @(*) begin
    CsrRamPlugin_api_holdRead = 1'b0;
    if(PerformanceCounterPlugin_logic_csrRead_requested) begin
      if(when_PerformanceCounterPlugin_l363) begin
        CsrRamPlugin_api_holdRead = 1'b1;
      end
    end
  end

  always @(*) begin
    CsrRamPlugin_api_holdWrite = 1'b0;
    if(PerformanceCounterPlugin_logic_fsm_holdCsrWrite) begin
      CsrRamPlugin_api_holdWrite = 1'b1;
    end
  end

  assign PrivilegedPlugin_api_harts_0_allowInterrupts = 1'b1;
  assign PrivilegedPlugin_api_harts_0_allowException = 1'b1;
  assign PrivilegedPlugin_api_harts_0_allowEbreakException = 1'b1;
  assign PrivilegedPlugin_api_harts_0_fpuEnable = 1'b0;
  always @(*) begin
    TrapPlugin_api_harts_0_redo = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
        if(!when_TrapPlugin_l547) begin
          case(TrapPlugin_logic_harts_0_trap_pending_state_code)
            5'h0 : begin
            end
            5'h01 : begin
            end
            5'h02 : begin
            end
            5'h04 : begin
              TrapPlugin_api_harts_0_redo = 1'b1;
            end
            5'h05 : begin
            end
            5'h08 : begin
            end
            5'h06 : begin
            end
            5'h09 : begin
            end
            default : begin
            end
          endcase
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_api_harts_0_askWake = 1'b0;
    if(when_TrapPlugin_l288) begin
      TrapPlugin_api_harts_0_askWake = 1'b1;
    end
  end

  always @(*) begin
    TrapPlugin_api_harts_0_rvTrap = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
        TrapPlugin_api_harts_0_rvTrap = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  assign TrapPlugin_api_harts_0_holdPrivChange = 1'b0;
  always @(*) begin
    case(execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0)
      IntAluPlugin_AluBitwiseCtrlEnum_AND_1 : begin
        early0_IntAluPlugin_logic_alu_bitwise = (execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0 & execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0);
      end
      IntAluPlugin_AluBitwiseCtrlEnum_OR_1 : begin
        early0_IntAluPlugin_logic_alu_bitwise = (execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0 | execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0);
      end
      IntAluPlugin_AluBitwiseCtrlEnum_XOR_1 : begin
        early0_IntAluPlugin_logic_alu_bitwise = (execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0 ^ execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0);
      end
      default : begin
        early0_IntAluPlugin_logic_alu_bitwise = 32'h0;
      end
    endcase
  end

  assign early0_IntAluPlugin_logic_alu_result = (_zz_early0_IntAluPlugin_logic_alu_result | _zz_early0_IntAluPlugin_logic_alu_result_2);
  assign execute_ctrl2_down_early0_IntAluPlugin_ALU_RESULT_lane0 = early0_IntAluPlugin_logic_alu_result;
  assign early0_IntAluPlugin_logic_wb_valid = execute_ctrl2_down_early0_IntAluPlugin_SEL_lane0;
  assign early0_IntAluPlugin_logic_wb_payload = execute_ctrl2_down_early0_IntAluPlugin_ALU_RESULT_lane0;
  assign early0_BarrelShifterPlugin_logic_shift_amplitude = _zz_early0_BarrelShifterPlugin_logic_shift_amplitude;
  assign early0_BarrelShifterPlugin_logic_shift_reversed = (execute_ctrl2_down_BarrelShifterPlugin_LEFT_lane0 ? _zz_early0_BarrelShifterPlugin_logic_shift_reversed : execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0);
  assign early0_BarrelShifterPlugin_logic_shift_shifted = _zz_early0_BarrelShifterPlugin_logic_shift_shifted[31:0];
  assign early0_BarrelShifterPlugin_logic_shift_patched = (execute_ctrl2_down_BarrelShifterPlugin_LEFT_lane0 ? _zz_early0_BarrelShifterPlugin_logic_shift_patched : early0_BarrelShifterPlugin_logic_shift_shifted);
  assign execute_ctrl2_down_early0_BarrelShifterPlugin_SHIFT_RESULT_lane0 = early0_BarrelShifterPlugin_logic_shift_patched;
  assign early0_BarrelShifterPlugin_logic_wb_valid = execute_ctrl2_down_early0_BarrelShifterPlugin_SEL_lane0;
  assign early0_BarrelShifterPlugin_logic_wb_payload = execute_ctrl2_down_early0_BarrelShifterPlugin_SHIFT_RESULT_lane0;
  assign execute_ctrl2_down_MUL_SRC1_lane0 = _zz_execute_ctrl2_down_MUL_SRC1_lane0;
  assign execute_ctrl2_down_MUL_SRC2_lane0 = _zz_execute_ctrl2_down_MUL_SRC2_lane0;
  assign execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_0_lane0 = (execute_ctrl2_down_MUL_SRC1_lane0[16 : 0] * execute_ctrl2_down_MUL_SRC2_lane0[16 : 0]);
  assign execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0 = _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  assign execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0 = _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  assign execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0 = _zz_execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  always @(*) begin
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0 = 61'h0;
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0[33 : 0] = execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_0_lane0[33 : 0];
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0[60 : 34] = execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_3_lane0[26 : 0];
  end

  always @(*) begin
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_1 = 61'h0;
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_1[60 : 17] = execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_1_lane0[43 : 0];
  end

  always @(*) begin
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_2 = 61'h0;
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_2[60 : 17] = execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_2_lane0[43 : 0];
  end

  always @(*) begin
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0 = 3'b000;
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0[2 : 0] = execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_3_lane0[29 : 27];
  end

  always @(*) begin
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_1 = 3'b000;
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_1[2 : 0] = execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_1_lane0[46 : 44];
  end

  always @(*) begin
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_2 = 3'b000;
    _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_2[2 : 0] = execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_2_lane0[46 : 44];
  end

  assign execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0 = (_zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_3 + _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0_6);
  assign execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0 = (_zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_3 + _zz_execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0_6);
  always @(*) begin
    _zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0 = 66'h0;
    _zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0[62 : 0] = execute_ctrl4_down_early0_MulPlugin_logic_steps_0_adders_0_lane0[62 : 0];
  end

  always @(*) begin
    _zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0_1 = 66'h0;
    _zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0_1[65 : 61] = execute_ctrl4_down_early0_MulPlugin_logic_steps_0_adders_1_lane0[4 : 0];
  end

  assign execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0 = (_zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0 + _zz_execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0_1);
  assign early0_MulPlugin_logic_formatBus_valid = execute_ctrl4_down_early0_MulPlugin_SEL_lane0;
  assign early0_MulPlugin_logic_formatBus_payload = (execute_ctrl4_down_MulPlugin_HIGH_lane0 ? execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0[63 : 32] : execute_ctrl4_down_early0_MulPlugin_logic_steps_1_adders_0_lane0[31 : 0]);
  assign execute_ctrl2_down_RsUnsignedPlugin_RS1_FORMATED_lane0 = execute_ctrl2_up_integer_RS1_lane0;
  assign execute_ctrl2_down_RsUnsignedPlugin_RS2_FORMATED_lane0 = execute_ctrl2_up_integer_RS2_lane0;
  assign execute_ctrl2_down_RsUnsignedPlugin_RS1_REVERT_lane0 = (execute_ctrl2_down_RsUnsignedPlugin_RS1_SIGNED_lane0 && execute_ctrl2_down_RsUnsignedPlugin_RS1_FORMATED_lane0[31]);
  assign execute_ctrl2_down_RsUnsignedPlugin_RS2_REVERT_lane0 = (execute_ctrl2_down_RsUnsignedPlugin_RS2_SIGNED_lane0 && execute_ctrl2_down_RsUnsignedPlugin_RS2_FORMATED_lane0[31]);
  assign execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0 = ((execute_ctrl2_down_RsUnsignedPlugin_RS1_REVERT_lane0 ? (~ execute_ctrl2_down_RsUnsignedPlugin_RS1_FORMATED_lane0) : execute_ctrl2_down_RsUnsignedPlugin_RS1_FORMATED_lane0) + _zz_execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0);
  assign execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0 = ((execute_ctrl2_down_RsUnsignedPlugin_RS2_REVERT_lane0 ? (~ execute_ctrl2_down_RsUnsignedPlugin_RS2_FORMATED_lane0) : execute_ctrl2_down_RsUnsignedPlugin_RS2_FORMATED_lane0) + _zz_execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0);
  assign io_cmd_fire = (early0_DivPlugin_logic_processing_div_io_cmd_valid && early0_DivPlugin_logic_processing_div_io_cmd_ready);
  assign early0_DivPlugin_logic_processing_request = (execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_early0_DivPlugin_SEL_lane0);
  assign early0_DivPlugin_logic_processing_a = execute_ctrl2_down_RsUnsignedPlugin_RS1_UNSIGNED_lane0;
  assign early0_DivPlugin_logic_processing_b = execute_ctrl2_down_RsUnsignedPlugin_RS2_UNSIGNED_lane0;
  assign early0_DivPlugin_logic_processing_div_io_cmd_valid = (early0_DivPlugin_logic_processing_request && (! early0_DivPlugin_logic_processing_cmdSent));
  assign early0_DivPlugin_logic_processing_freeze = ((early0_DivPlugin_logic_processing_request && (! early0_DivPlugin_logic_processing_div_io_rsp_valid)) && (! early0_DivPlugin_logic_processing_unscheduleRequest));
  assign early0_DivPlugin_logic_processing_selected = (execute_ctrl2_down_DivPlugin_REM_lane0 ? early0_DivPlugin_logic_processing_div_io_rsp_payload_remain : early0_DivPlugin_logic_processing_div_io_rsp_payload_result);
  assign _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0 = early0_DivPlugin_logic_processing_selected;
  assign execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0 = _zz_execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0_1;
  assign early0_DivPlugin_logic_formatBus_valid = execute_ctrl3_down_early0_DivPlugin_SEL_lane0;
  assign early0_DivPlugin_logic_formatBus_payload = execute_ctrl3_down_DivPlugin_DIV_RESULT_lane0;
  always @(*) begin
    PrivilegedPlugin_logic_harts_0_xretAwayFromMachine = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
        if(when_TrapPlugin_l975) begin
          PrivilegedPlugin_logic_harts_0_xretAwayFromMachine = 1'b1;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    PrivilegedPlugin_logic_harts_0_int_pending = 1'b0;
    if(TrapPlugin_logic_harts_0_interrupt_pendingInterrupt) begin
      PrivilegedPlugin_logic_harts_0_int_pending = 1'b1;
    end
  end

  assign PrivilegedPlugin_logic_harts_0_withMachinePrivilege = ($signed(3'b011) <= $signed(PrivilegedPlugin_logic_harts_0_privilege));
  assign PrivilegedPlugin_logic_harts_0_withSupervisorPrivilege = ($signed(3'b001) <= $signed(PrivilegedPlugin_logic_harts_0_privilege));
  assign PrivilegedPlugin_logic_harts_0_withVirtualSupervisorPrivilege = ($signed(3'b101) <= $signed(PrivilegedPlugin_logic_harts_0_privilege));
  assign PrivilegedPlugin_logic_harts_0_withGuestPrivilege = PrivilegedPlugin_logic_harts_0_privilege[2];
  assign PrivilegedPlugin_logic_harts_0_withHostPrivilege = (! PrivilegedPlugin_logic_harts_0_withGuestPrivilege);
  assign PrivilegedPlugin_logic_harts_0_hartRunning = 1'b1;
  assign PrivilegedPlugin_logic_harts_0_debugMode = (! PrivilegedPlugin_logic_harts_0_hartRunning);
  assign PrivilegedPlugin_logic_harts_0_m_status_mpp = 2'b11;
  assign PrivilegedPlugin_logic_harts_0_m_status_mpv = 1'b0;
  assign PrivilegedPlugin_logic_harts_0_m_status_sd = 1'b0;
  assign PrivilegedPlugin_logic_harts_0_m_status_tw = 1'b0;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue = (_zz_when_CsrService_l221 && REG_CSR_768);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_1 = (_zz_when_CsrService_l221 && REG_CSR_834);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_3 = (_zz_when_CsrService_l221 && _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_2);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_5 = (_zz_when_CsrService_l221 && _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_4);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid = (PrivilegedPlugin_logic_harts_0_m_ip_mtip && PrivilegedPlugin_logic_harts_0_m_ie_mtie);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid = (PrivilegedPlugin_logic_harts_0_m_ip_msip && PrivilegedPlugin_logic_harts_0_m_ie_msie);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid = (PrivilegedPlugin_logic_harts_0_m_ip_meip && PrivilegedPlugin_logic_harts_0_m_ie_meie);
  always @(*) begin
    PrivilegedPlugin_logic_harts_0_m_topi_interrupt = 5'bxxxxx;
    PrivilegedPlugin_logic_harts_0_m_topi_interrupt = TrapPlugin_logic_harts_0_interrupt_xtopi_0_int;
  end

  assign PrivilegedPlugin_logic_harts_0_m_topi_priority = ((PrivilegedPlugin_logic_harts_0_m_topi_interrupt == 5'h0) ? 1'b0 : 1'b1);
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_6 = (_zz_when_CsrService_l221 && REG_CSR_4016);
  assign PrivilegedPlugin_logic_harts_0_m_envcfg_stce = 1'b0;
  assign PrivilegedPlugin_logic_harts_0_time_host_allowSupervisor = (PrivilegedPlugin_logic_harts_0_withMachinePrivilege || PrivilegedPlugin_logic_harts_0_m_counteren_tm);
  assign PrivilegedPlugin_logic_harts_0_time_host_allowUser = 1'b1;
  assign PrivilegedPlugin_logic_harts_0_time_host_accessable = (PrivilegedPlugin_logic_harts_0_time_host_allowSupervisor && PrivilegedPlugin_logic_harts_0_time_host_allowUser);
  assign WhiteboxerPlugin_logic_fetch_fire = fetch_logic_ctrls_0_down_isFiring;
  always @(*) begin
    CsrAccessPlugin_bus_decode_exception = 1'b0;
    if(when_PrivilegedPlugin_l1186) begin
      CsrAccessPlugin_bus_decode_exception = 1'b1;
    end
    if(when_CsrAccessPlugin_l174) begin
      if(when_CsrService_l135) begin
        CsrAccessPlugin_bus_decode_exception = 1'b1;
      end
    end
    if(when_CsrAccessPlugin_l174_1) begin
      if(when_CsrService_l135_1) begin
        CsrAccessPlugin_bus_decode_exception = 1'b1;
      end
    end
    if(when_CsrAccessPlugin_l174_2) begin
      if(when_CsrService_l135_2) begin
        CsrAccessPlugin_bus_decode_exception = 1'b1;
      end
    end
    if(when_CsrAccessPlugin_l174_3) begin
      if(when_CsrService_l221) begin
        if(when_CsrService_l222) begin
          CsrAccessPlugin_bus_decode_exception = 1'b1;
        end
      end
    end
    if(when_CsrAccessPlugin_l174_4) begin
      if(when_CsrService_l221_1) begin
        if(when_CsrService_l222_1) begin
          CsrAccessPlugin_bus_decode_exception = 1'b1;
        end
      end
    end
    if(when_CsrAccessPlugin_l174_5) begin
      if(when_CsrService_l221_2) begin
        if(when_CsrService_l222_2) begin
          CsrAccessPlugin_bus_decode_exception = 1'b1;
        end
      end
    end
    if(when_CsrAccessPlugin_l174_6) begin
      if(when_CsrService_l221_3) begin
        if(when_CsrService_l222_3) begin
          CsrAccessPlugin_bus_decode_exception = 1'b1;
        end
      end
    end
    if(when_CsrAccessPlugin_l174_7) begin
      if(when_CsrService_l135_3) begin
        CsrAccessPlugin_bus_decode_exception = 1'b1;
      end
    end
    if(when_CsrAccessPlugin_l174_8) begin
      if(when_PerformanceCounterPlugin_l342) begin
        if(when_PerformanceCounterPlugin_l346) begin
          CsrAccessPlugin_bus_decode_exception = 1'b1;
        end
      end
    end
    if(when_CsrAccessPlugin_l174_9) begin
      if(when_CsrService_l135_4) begin
        CsrAccessPlugin_bus_decode_exception = 1'b1;
      end
    end
  end

  always @(*) begin
    CsrAccessPlugin_bus_decode_trap = 1'b0;
    if(when_CsrAccessPlugin_l174_10) begin
      if(CsrAccessPlugin_bus_decode_write) begin
        CsrAccessPlugin_bus_decode_trap = 1'b1;
      end
    end
  end

  always @(*) begin
    CsrAccessPlugin_bus_decode_trapCode = 5'bxxxxx;
    if(when_CsrAccessPlugin_l174_10) begin
      if(CsrAccessPlugin_bus_decode_write) begin
        CsrAccessPlugin_bus_decode_trapCode = 5'h05;
      end
    end
  end

  assign CsrAccessPlugin_bus_decode_fence = 1'b0;
  always @(*) begin
    CsrAccessPlugin_bus_decode_virtual = 1'b0;
    if(when_CsrAccessPlugin_l174_8) begin
      if(when_PerformanceCounterPlugin_l350) begin
        CsrAccessPlugin_bus_decode_virtual = 1'b1;
      end
    end
  end

  always @(*) begin
    CsrAccessPlugin_bus_decode_hostDenied = 1'b0;
    if(when_PrivilegedPlugin_l1190) begin
      CsrAccessPlugin_bus_decode_hostDenied = 1'b1;
    end
    if(when_CsrAccessPlugin_l174_8) begin
      if(when_PerformanceCounterPlugin_l342) begin
        if(CsrAccessPlugin_bus_decode_write) begin
          CsrAccessPlugin_bus_decode_hostDenied = 1'b1;
        end
      end
    end
  end

  always @(*) begin
    CsrAccessPlugin_bus_read_halt = 1'b0;
    if(when_CsrRamPlugin_l85) begin
      CsrAccessPlugin_bus_read_halt = 1'b1;
    end
  end

  always @(*) begin
    CsrAccessPlugin_bus_write_halt = 1'b0;
    if(when_CsrRamPlugin_l96) begin
      CsrAccessPlugin_bus_write_halt = 1'b1;
    end
    if(when_CsrAccessPlugin_l372_2) begin
      if(when_PerformanceCounterPlugin_l378) begin
        if(when_PerformanceCounterPlugin_l380) begin
          CsrAccessPlugin_bus_write_halt = 1'b1;
        end
      end
    end
  end

  always @(*) begin
    FetchCachelessPlugin_logic_buffer_reserveId_willIncrement = 1'b0;
    if(!when_FetchCachelessPlugin_l178) begin
      if(fetch_logic_ctrls_0_up_isMoving) begin
        FetchCachelessPlugin_logic_buffer_reserveId_willIncrement = 1'b1;
      end
    end
  end

  assign FetchCachelessPlugin_logic_buffer_reserveId_willDecrement = 1'b0;
  assign FetchCachelessPlugin_logic_buffer_reserveId_willClear = 1'b0;
  assign FetchCachelessPlugin_logic_buffer_reserveId_willLoad = 1'b0;
  assign FetchCachelessPlugin_logic_buffer_reserveId_willOverflowIfInc = (FetchCachelessPlugin_logic_buffer_reserveId_value == 1'b1);
  assign FetchCachelessPlugin_logic_buffer_reserveId_willUnderflowIfDec = (FetchCachelessPlugin_logic_buffer_reserveId_value == 1'b0);
  assign FetchCachelessPlugin_logic_buffer_reserveId_willOverflow = (FetchCachelessPlugin_logic_buffer_reserveId_willOverflowIfInc && FetchCachelessPlugin_logic_buffer_reserveId_willIncrement);
  always @(*) begin
    FetchCachelessPlugin_logic_buffer_reserveId_valueNext = (FetchCachelessPlugin_logic_buffer_reserveId_value + FetchCachelessPlugin_logic_buffer_reserveId_willIncrement);
    if(FetchCachelessPlugin_logic_buffer_reserveId_willClear) begin
      FetchCachelessPlugin_logic_buffer_reserveId_valueNext = 1'b0;
    end
  end

  assign FetchCachelessPlugin_logic_buffer_reserveId_willUnderflow = (FetchCachelessPlugin_logic_buffer_reserveId_willUnderflowIfDec && FetchCachelessPlugin_logic_buffer_reserveId_willDecrement);
  assign FetchCachelessPlugin_logic_buffer_reservedHits_0 = (fetch_logic_ctrls_1_up_isValid && (fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_BUFFER_ID == FetchCachelessPlugin_logic_buffer_reserveId_value));
  assign FetchCachelessPlugin_logic_buffer_full = ((|FetchCachelessPlugin_logic_buffer_reservedHits_0) || _zz_FetchCachelessPlugin_logic_buffer_full);
  assign _zz_4 = ({1'd0,1'b1} <<< FetchCachelessPlugin_logic_buffer_reserveId_value);
  always @(*) begin
    FetchCachelessPlugin_logic_buffer_write_valid = 1'b0;
    if(FetchCachelessPlugin_logic_bus_rsp_valid) begin
      FetchCachelessPlugin_logic_buffer_write_valid = 1'b1;
    end
  end

  assign FetchCachelessPlugin_logic_buffer_write_payload_address = FetchCachelessPlugin_logic_bus_rsp_payload_id;
  assign FetchCachelessPlugin_logic_buffer_write_payload_data_error = FetchCachelessPlugin_logic_bus_rsp_payload_error;
  assign FetchCachelessPlugin_logic_buffer_write_payload_data_word = FetchCachelessPlugin_logic_bus_rsp_payload_word;
  assign _zz_5 = ({1'd0,1'b1} <<< FetchCachelessPlugin_logic_bus_rsp_payload_id);
  assign FetchCachelessPlugin_logic_onPma_port_cmd_address = fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onPma_RSP_fault = FetchCachelessPlugin_logic_onPma_port_rsp_fault;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onPma_RSP_io = FetchCachelessPlugin_logic_onPma_port_rsp_io;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_0 = 1'b0;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_1 = 1'b0;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_2 = 1'b0;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_3 = 1'b0;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_4 = 1'b0;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_5 = 1'b0;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_6 = 1'b0;
  assign LsuCachelessPlugin_logic_frontend_defaultsDecodings_7 = 1'b0;
  assign WhiteboxerPlugin_logic_fetch_fetchId = fetch_logic_ctrls_0_down_Fetch_ID;
  assign WhiteboxerPlugin_logic_decodes_0_fire = ((decode_ctrls_0_up_LANE_SEL_0 && decode_ctrls_0_up_isReady) && (! decode_ctrls_0_lane0_upIsCancel));
  assign when_CtrlLaneApi_l50 = (decode_ctrls_0_up_isReady || decode_ctrls_0_lane0_upIsCancel);
  assign WhiteboxerPlugin_logic_decodes_0_spawn = (decode_ctrls_0_up_LANE_SEL_0 && (! decode_ctrls_0_up_LANE_SEL_0_regNext));
  assign WhiteboxerPlugin_logic_decodes_0_pc = _zz_WhiteboxerPlugin_logic_decodes_0_pc;
  assign WhiteboxerPlugin_logic_decodes_0_fetchId = decode_ctrls_0_down_Fetch_ID_0;
  assign WhiteboxerPlugin_logic_decodes_0_decodeId = decode_ctrls_0_down_Decode_DOP_ID_0;
  always @(*) begin
    early0_EnvPlugin_logic_flushPort_valid = 1'b0;
    if(when_EnvPlugin_l203) begin
      early0_EnvPlugin_logic_flushPort_valid = 1'b1;
    end
  end

  assign early0_EnvPlugin_logic_flushPort_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign early0_EnvPlugin_logic_flushPort_payload_self = 1'b0;
  always @(*) begin
    early0_EnvPlugin_logic_trapPort_valid = 1'b0;
    if(when_EnvPlugin_l203) begin
      early0_EnvPlugin_logic_trapPort_valid = 1'b1;
    end
  end

  always @(*) begin
    early0_EnvPlugin_logic_trapPort_payload_exception = 1'b1;
    case(execute_ctrl2_down_early0_EnvPlugin_OP_lane0)
      EnvPluginOp_PRIV_RET : begin
        if(when_EnvPlugin_l131) begin
          early0_EnvPlugin_logic_trapPort_payload_exception = 1'b0;
        end
      end
      EnvPluginOp_WFI : begin
        if(when_EnvPlugin_l145) begin
          early0_EnvPlugin_logic_trapPort_payload_exception = 1'b0;
        end
      end
      EnvPluginOp_FENCE_I : begin
        early0_EnvPlugin_logic_trapPort_payload_exception = 1'b0;
      end
      default : begin
      end
    endcase
  end

  assign PrivilegedPlugin_logic_defaultTrap_csrPrivilege = CsrAccessPlugin_bus_decode_privilege;
  assign PrivilegedPlugin_logic_defaultTrap_csrReadOnly = (CsrAccessPlugin_bus_decode_address[11 : 10] == 2'b11);
  assign PrivilegedPlugin_logic_defaultTrap_hartPrivilege = PrivilegedPlugin_logic_harts_0_privilege;
  assign PrivilegedPlugin_logic_defaultTrap_adjustPrivilege = _zz_PrivilegedPlugin_logic_defaultTrap_adjustPrivilege;
  assign when_PrivilegedPlugin_l1186 = (PrivilegedPlugin_logic_defaultTrap_adjustPrivilege < PrivilegedPlugin_logic_defaultTrap_csrPrivilege);
  assign when_PrivilegedPlugin_l1190 = (PrivilegedPlugin_logic_defaultTrap_csrReadOnly && CsrAccessPlugin_bus_decode_write);
  always @(*) begin
    _zz_execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0 = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    case(execute_ctrl1_down_early0_SrcPlugin_logic_SRC1_CTRL_lane0)
      1'b0 : begin
        _zz_execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0 = execute_ctrl1_down_integer_RS1_lane0;
      end
      default : begin
        _zz_execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0 = {execute_ctrl1_down_Decode_UOP_lane0[31 : 12],12'h0};
      end
    endcase
  end

  assign execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0 = _zz_execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0;
  always @(*) begin
    _zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0 = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    case(execute_ctrl1_down_early0_SrcPlugin_logic_SRC2_CTRL_lane0)
      2'b00 : begin
        _zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0 = execute_ctrl1_down_integer_RS2_lane0;
      end
      2'b01 : begin
        _zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0 = {{20{_zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0[11]}}, _zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0};
      end
      2'b10 : begin
        _zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0 = execute_ctrl1_down_PC_lane0;
      end
      default : begin
        _zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0 = {{20{_zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0_1[11]}}, _zz__zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0_1};
      end
    endcase
  end

  assign execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0 = _zz_execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0;
  always @(*) begin
    early0_SrcPlugin_logic_addsub_combined_rs2Patched = execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0;
    if(execute_ctrl2_down_SrcStageables_REVERT_lane0) begin
      early0_SrcPlugin_logic_addsub_combined_rs2Patched = (~ execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0);
    end
    if(execute_ctrl2_down_SrcStageables_ZERO_lane0) begin
      early0_SrcPlugin_logic_addsub_combined_rs2Patched = 32'h0;
    end
  end

  assign execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0 = ($signed(_zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0) + $signed(_zz_execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0_1));
  assign execute_ctrl2_down_early0_SrcPlugin_LESS_lane0 = ((execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[31] == execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0[31]) ? execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0[31] : (execute_ctrl2_down_SrcStageables_UNSIGNED_lane0 ? execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0[31] : execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0[31]));
  assign lane0_IntFormatPlugin_logic_stages_0_hits = {early0_BarrelShifterPlugin_logic_wb_valid,early0_IntAluPlugin_logic_wb_valid};
  assign lane0_IntFormatPlugin_logic_stages_0_wb_valid = (execute_ctrl2_up_LANE_SEL_lane0 && (|lane0_IntFormatPlugin_logic_stages_0_hits));
  assign lane0_IntFormatPlugin_logic_stages_0_raw = ((lane0_IntFormatPlugin_logic_stages_0_hits[0] ? early0_IntAluPlugin_logic_wb_payload : 32'h0) | (lane0_IntFormatPlugin_logic_stages_0_hits[1] ? early0_BarrelShifterPlugin_logic_wb_payload : 32'h0));
  assign lane0_IntFormatPlugin_logic_stages_0_wb_payload = lane0_IntFormatPlugin_logic_stages_0_raw;
  assign lane0_IntFormatPlugin_logic_stages_1_hits = {LsuCachelessPlugin_logic_iwb_valid,early0_MulPlugin_logic_formatBus_valid};
  assign lane0_IntFormatPlugin_logic_stages_1_wb_valid = (execute_ctrl4_up_LANE_SEL_lane0 && (|lane0_IntFormatPlugin_logic_stages_1_hits));
  assign lane0_IntFormatPlugin_logic_stages_1_raw = ((lane0_IntFormatPlugin_logic_stages_1_hits[0] ? early0_MulPlugin_logic_formatBus_payload : 32'h0) | (lane0_IntFormatPlugin_logic_stages_1_hits[1] ? LsuCachelessPlugin_logic_iwb_payload : 32'h0));
  always @(*) begin
    lane0_IntFormatPlugin_logic_stages_1_wb_payload = lane0_IntFormatPlugin_logic_stages_1_raw;
    if(lane0_IntFormatPlugin_logic_stages_1_segments_0_doIt) begin
      lane0_IntFormatPlugin_logic_stages_1_wb_payload[15 : 8] = {8{lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_value}};
    end
    if(lane0_IntFormatPlugin_logic_stages_1_segments_1_doIt) begin
      lane0_IntFormatPlugin_logic_stages_1_wb_payload[31 : 16] = {16{lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value}};
    end
  end

  assign lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_sels_0 = lane0_IntFormatPlugin_logic_stages_1_raw[7];
  always @(*) begin
    _zz_lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_value = 1'bx;
    case(execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0)
      2'b00 : begin
        _zz_lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_value = lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_sels_0;
      end
      default : begin
      end
    endcase
  end

  assign lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_value = (execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 && _zz_lane0_IntFormatPlugin_logic_stages_1_segments_0_sign_value);
  assign lane0_IntFormatPlugin_logic_stages_1_segments_0_doIt = (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 < 2'b01);
  assign lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_sels_0 = lane0_IntFormatPlugin_logic_stages_1_raw[7];
  assign lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_sels_1 = lane0_IntFormatPlugin_logic_stages_1_raw[15];
  always @(*) begin
    _zz_lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value = 1'bx;
    case(execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0)
      2'b00 : begin
        _zz_lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value = lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_sels_0;
      end
      2'b01 : begin
        _zz_lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value = lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_sels_1;
      end
      default : begin
      end
    endcase
  end

  assign lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value = (execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 && _zz_lane0_IntFormatPlugin_logic_stages_1_segments_1_sign_value);
  assign lane0_IntFormatPlugin_logic_stages_1_segments_1_doIt = (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 < 2'b10);
  assign lane0_IntFormatPlugin_logic_stages_2_hits = {CsrAccessPlugin_logic_wbWi_valid,early0_DivPlugin_logic_formatBus_valid};
  assign lane0_IntFormatPlugin_logic_stages_2_wb_valid = (execute_ctrl3_up_LANE_SEL_lane0 && (|lane0_IntFormatPlugin_logic_stages_2_hits));
  assign lane0_IntFormatPlugin_logic_stages_2_raw = ((lane0_IntFormatPlugin_logic_stages_2_hits[0] ? early0_DivPlugin_logic_formatBus_payload : 32'h0) | (lane0_IntFormatPlugin_logic_stages_2_hits[1] ? CsrAccessPlugin_logic_wbWi_payload : 32'h0));
  assign lane0_IntFormatPlugin_logic_stages_2_wb_payload = lane0_IntFormatPlugin_logic_stages_2_raw;
  assign PerformanceCounterPlugin_logic_commitMask = PrivilegedPlugin_logic_harts_0_commitMask;
  assign when_PerformanceCounterPlugin_l45 = (|PerformanceCounterPlugin_logic_commitMask);
  assign PerformanceCounterPlugin_logic_commitCount = (_zz_PerformanceCounterPlugin_logic_commitCount - (PerformanceCounterPlugin_logic_ignoreNextCommit && (|PerformanceCounterPlugin_logic_commitMask)));
  assign PerformanceCounterPlugin_logic_counters_cycle_needFlush = PerformanceCounterPlugin_logic_counters_cycle_value[7];
  assign PerformanceCounterPlugin_logic_counters_instret_needFlush = PerformanceCounterPlugin_logic_counters_instret_value[7];
  assign PerformanceCounterPlugin_logic_eventCycles = 1'b1;
  assign PerformanceCounterPlugin_logic_eventInstructions_0 = _zz_PerformanceCounterPlugin_logic_eventInstructions_0[0];
  always @(*) begin
    case(execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0)
      BranchPlugin_BranchCtrlEnum_JALR : begin
        early0_BranchPlugin_pcCalc_target_a = execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0;
      end
      default : begin
        early0_BranchPlugin_pcCalc_target_a = execute_ctrl2_down_PC_lane0;
      end
    endcase
  end

  always @(*) begin
    case(execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0)
      BranchPlugin_BranchCtrlEnum_JAL : begin
        early0_BranchPlugin_pcCalc_target_b = {{11{_zz_early0_BranchPlugin_pcCalc_target_b[20]}}, _zz_early0_BranchPlugin_pcCalc_target_b};
      end
      BranchPlugin_BranchCtrlEnum_JALR : begin
        early0_BranchPlugin_pcCalc_target_b = {{20{_zz_early0_BranchPlugin_pcCalc_target_b_1[11]}}, _zz_early0_BranchPlugin_pcCalc_target_b_1};
      end
      default : begin
        early0_BranchPlugin_pcCalc_target_b = {{19{_zz_early0_BranchPlugin_pcCalc_target_b_2[12]}}, _zz_early0_BranchPlugin_pcCalc_target_b_2};
      end
    endcase
  end

  assign early0_BranchPlugin_pcCalc_slices = ({1'b0,execute_ctrl2_down_Decode_INSTRUCTION_SLICE_COUNT_lane0} + {1'b0,1'b1});
  always @(*) begin
    execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0 = _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0;
    execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[0] = 1'b0;
  end

  assign execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0 = (execute_ctrl2_down_PC_lane0 + _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0);
  assign execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0 = (execute_ctrl2_down_PC_lane0 + _zz_execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0);
  assign AlignerPlugin_logic_maskGen_frontMasks_0 = 2'b11;
  assign AlignerPlugin_logic_maskGen_frontMasks_1 = 2'b10;
  assign AlignerPlugin_logic_maskGen_backMasks_0 = 2'b01;
  assign AlignerPlugin_logic_maskGen_backMasks_1 = 2'b11;
  assign fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK = _zz_fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK;
  assign fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_LAST = 2'b00;
  assign AlignerPlugin_logic_slicesInstructions_0 = {AlignerPlugin_logic_slices_data_1,AlignerPlugin_logic_slices_data_0};
  assign AlignerPlugin_logic_slicesInstructions_1 = {AlignerPlugin_logic_slices_data_2,AlignerPlugin_logic_slices_data_1};
  assign AlignerPlugin_logic_slicesInstructions_2 = {AlignerPlugin_logic_slices_data_3,AlignerPlugin_logic_slices_data_2};
  assign AlignerPlugin_logic_slicesInstructions_3 = {16'd0, AlignerPlugin_logic_slices_data_3};
  always @(*) begin
    AlignerPlugin_logic_scanners_0_usageMask = 4'b0000;
    AlignerPlugin_logic_scanners_0_usageMask[0] = AlignerPlugin_logic_scanners_0_checker_0_required;
    AlignerPlugin_logic_scanners_0_usageMask[1] = AlignerPlugin_logic_scanners_0_checker_1_required;
  end

  assign AlignerPlugin_logic_scanners_0_checker_0_required = 1'b1;
  assign AlignerPlugin_logic_scanners_0_checker_0_last = (AlignerPlugin_logic_slices_data_0[1 : 0] != 2'b11);
  assign AlignerPlugin_logic_scanners_0_checker_0_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_0_checker_0_present = AlignerPlugin_logic_slices_mask[0];
  assign AlignerPlugin_logic_scanners_0_checker_0_valid = AlignerPlugin_logic_scanners_0_checker_0_present;
  assign AlignerPlugin_logic_scanners_0_checker_1_required = (AlignerPlugin_logic_slices_data_0[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_0_checker_1_last = (AlignerPlugin_logic_slices_data_0[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_0_checker_1_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_0_checker_1_present = AlignerPlugin_logic_slices_mask[1];
  assign AlignerPlugin_logic_scanners_0_checker_1_valid = (AlignerPlugin_logic_scanners_0_checker_1_present || (! AlignerPlugin_logic_scanners_0_checker_1_required));
  assign AlignerPlugin_logic_scanners_0_redo = (|{AlignerPlugin_logic_scanners_0_checker_1_redo,AlignerPlugin_logic_scanners_0_checker_0_redo});
  assign AlignerPlugin_logic_scanners_0_valid = (AlignerPlugin_logic_scanners_0_checker_0_valid && ((&AlignerPlugin_logic_scanners_0_checker_1_valid) || (|{AlignerPlugin_logic_scanners_0_checker_1_redo,AlignerPlugin_logic_scanners_0_checker_0_redo})));
  always @(*) begin
    AlignerPlugin_logic_scanners_1_usageMask = 4'b0000;
    AlignerPlugin_logic_scanners_1_usageMask[1] = AlignerPlugin_logic_scanners_1_checker_0_required;
    AlignerPlugin_logic_scanners_1_usageMask[2] = AlignerPlugin_logic_scanners_1_checker_1_required;
  end

  assign AlignerPlugin_logic_scanners_1_checker_0_required = 1'b1;
  assign AlignerPlugin_logic_scanners_1_checker_0_last = (AlignerPlugin_logic_slices_data_1[1 : 0] != 2'b11);
  assign AlignerPlugin_logic_scanners_1_checker_0_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_1_checker_0_present = AlignerPlugin_logic_slices_mask[1];
  assign AlignerPlugin_logic_scanners_1_checker_0_valid = AlignerPlugin_logic_scanners_1_checker_0_present;
  assign AlignerPlugin_logic_scanners_1_checker_1_required = (AlignerPlugin_logic_slices_data_1[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_1_checker_1_last = (AlignerPlugin_logic_slices_data_1[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_1_checker_1_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_1_checker_1_present = AlignerPlugin_logic_slices_mask[2];
  assign AlignerPlugin_logic_scanners_1_checker_1_valid = (AlignerPlugin_logic_scanners_1_checker_1_present || (! AlignerPlugin_logic_scanners_1_checker_1_required));
  assign AlignerPlugin_logic_scanners_1_redo = (|{AlignerPlugin_logic_scanners_1_checker_1_redo,AlignerPlugin_logic_scanners_1_checker_0_redo});
  assign AlignerPlugin_logic_scanners_1_valid = (AlignerPlugin_logic_scanners_1_checker_0_valid && ((&AlignerPlugin_logic_scanners_1_checker_1_valid) || (|{AlignerPlugin_logic_scanners_1_checker_1_redo,AlignerPlugin_logic_scanners_1_checker_0_redo})));
  always @(*) begin
    AlignerPlugin_logic_scanners_2_usageMask = 4'b0000;
    AlignerPlugin_logic_scanners_2_usageMask[2] = AlignerPlugin_logic_scanners_2_checker_0_required;
    AlignerPlugin_logic_scanners_2_usageMask[3] = AlignerPlugin_logic_scanners_2_checker_1_required;
  end

  assign AlignerPlugin_logic_scanners_2_checker_0_required = 1'b1;
  assign AlignerPlugin_logic_scanners_2_checker_0_last = (AlignerPlugin_logic_slices_data_2[1 : 0] != 2'b11);
  assign AlignerPlugin_logic_scanners_2_checker_0_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_2_checker_0_present = AlignerPlugin_logic_slices_mask[2];
  assign AlignerPlugin_logic_scanners_2_checker_0_valid = AlignerPlugin_logic_scanners_2_checker_0_present;
  assign AlignerPlugin_logic_scanners_2_checker_1_required = (AlignerPlugin_logic_slices_data_2[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_2_checker_1_last = (AlignerPlugin_logic_slices_data_2[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_2_checker_1_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_2_checker_1_present = AlignerPlugin_logic_slices_mask[3];
  assign AlignerPlugin_logic_scanners_2_checker_1_valid = (AlignerPlugin_logic_scanners_2_checker_1_present || (! AlignerPlugin_logic_scanners_2_checker_1_required));
  assign AlignerPlugin_logic_scanners_2_redo = (|{AlignerPlugin_logic_scanners_2_checker_1_redo,AlignerPlugin_logic_scanners_2_checker_0_redo});
  assign AlignerPlugin_logic_scanners_2_valid = (AlignerPlugin_logic_scanners_2_checker_0_valid && ((&AlignerPlugin_logic_scanners_2_checker_1_valid) || (|{AlignerPlugin_logic_scanners_2_checker_1_redo,AlignerPlugin_logic_scanners_2_checker_0_redo})));
  always @(*) begin
    AlignerPlugin_logic_scanners_3_usageMask = 4'b0000;
    AlignerPlugin_logic_scanners_3_usageMask[3] = AlignerPlugin_logic_scanners_3_checker_0_required;
  end

  assign AlignerPlugin_logic_scanners_3_checker_0_required = 1'b1;
  assign AlignerPlugin_logic_scanners_3_checker_0_last = (AlignerPlugin_logic_slices_data_3[1 : 0] != 2'b11);
  assign AlignerPlugin_logic_scanners_3_checker_0_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_3_checker_0_present = AlignerPlugin_logic_slices_mask[3];
  assign AlignerPlugin_logic_scanners_3_checker_0_valid = AlignerPlugin_logic_scanners_3_checker_0_present;
  assign AlignerPlugin_logic_scanners_3_checker_1_required = (AlignerPlugin_logic_slices_data_3[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_3_checker_1_last = (AlignerPlugin_logic_slices_data_3[1 : 0] == 2'b11);
  assign AlignerPlugin_logic_scanners_3_checker_1_redo = 1'b0;
  assign AlignerPlugin_logic_scanners_3_checker_1_present = 1'b0;
  assign AlignerPlugin_logic_scanners_3_checker_1_valid = (AlignerPlugin_logic_scanners_3_checker_1_present || (! AlignerPlugin_logic_scanners_3_checker_1_required));
  assign AlignerPlugin_logic_scanners_3_redo = (|{AlignerPlugin_logic_scanners_3_checker_1_redo,AlignerPlugin_logic_scanners_3_checker_0_redo});
  assign AlignerPlugin_logic_scanners_3_valid = (AlignerPlugin_logic_scanners_3_checker_0_valid && ((&AlignerPlugin_logic_scanners_3_checker_1_valid) || (|{AlignerPlugin_logic_scanners_3_checker_1_redo,AlignerPlugin_logic_scanners_3_checker_0_redo})));
  assign AlignerPlugin_logic_usedMask_0 = 4'b0000;
  assign AlignerPlugin_logic_extractors_0_first = 1'b1;
  assign AlignerPlugin_logic_extractors_0_usableMask = {(AlignerPlugin_logic_scanners_3_valid && (! AlignerPlugin_logic_usedMask_0[3])),{(AlignerPlugin_logic_scanners_2_valid && (! AlignerPlugin_logic_usedMask_0[2])),{(AlignerPlugin_logic_scanners_1_valid && (! AlignerPlugin_logic_usedMask_0[1])),(AlignerPlugin_logic_scanners_0_valid && (! AlignerPlugin_logic_usedMask_0[0]))}}};
  assign _zz_AlignerPlugin_logic_extractors_0_usableMask_bools_0 = AlignerPlugin_logic_extractors_0_usableMask;
  assign AlignerPlugin_logic_extractors_0_usableMask_bools_0 = _zz_AlignerPlugin_logic_extractors_0_usableMask_bools_0[0];
  assign AlignerPlugin_logic_extractors_0_usableMask_bools_1 = _zz_AlignerPlugin_logic_extractors_0_usableMask_bools_0[1];
  assign AlignerPlugin_logic_extractors_0_usableMask_bools_2 = _zz_AlignerPlugin_logic_extractors_0_usableMask_bools_0[2];
  assign AlignerPlugin_logic_extractors_0_usableMask_bools_3 = _zz_AlignerPlugin_logic_extractors_0_usableMask_bools_0[3];
  always @(*) begin
    _zz_AlignerPlugin_logic_extractors_0_slicesOh[0] = (AlignerPlugin_logic_extractors_0_usableMask_bools_0 && (! 1'b0));
    _zz_AlignerPlugin_logic_extractors_0_slicesOh[1] = (AlignerPlugin_logic_extractors_0_usableMask_bools_1 && (! AlignerPlugin_logic_extractors_0_usableMask_bools_0));
    _zz_AlignerPlugin_logic_extractors_0_slicesOh[2] = (AlignerPlugin_logic_extractors_0_usableMask_bools_2 && (! AlignerPlugin_logic_extractors_0_usableMask_range_0_to_1));
    _zz_AlignerPlugin_logic_extractors_0_slicesOh[3] = (AlignerPlugin_logic_extractors_0_usableMask_bools_3 && (! AlignerPlugin_logic_extractors_0_usableMask_range_0_to_2));
  end

  assign AlignerPlugin_logic_extractors_0_usableMask_range_0_to_1 = (|{AlignerPlugin_logic_extractors_0_usableMask_bools_1,AlignerPlugin_logic_extractors_0_usableMask_bools_0});
  assign AlignerPlugin_logic_extractors_0_usableMask_range_0_to_2 = (|{AlignerPlugin_logic_extractors_0_usableMask_bools_2,{AlignerPlugin_logic_extractors_0_usableMask_bools_1,AlignerPlugin_logic_extractors_0_usableMask_bools_0}});
  assign AlignerPlugin_logic_extractors_0_slicesOh = _zz_AlignerPlugin_logic_extractors_0_slicesOh;
  assign _zz_AlignerPlugin_logic_extractors_0_redo = AlignerPlugin_logic_extractors_0_slicesOh[0];
  assign _zz_AlignerPlugin_logic_extractors_0_redo_1 = AlignerPlugin_logic_extractors_0_slicesOh[1];
  assign _zz_AlignerPlugin_logic_extractors_0_redo_2 = AlignerPlugin_logic_extractors_0_slicesOh[2];
  assign _zz_AlignerPlugin_logic_extractors_0_redo_3 = AlignerPlugin_logic_extractors_0_slicesOh[3];
  always @(*) begin
    AlignerPlugin_logic_extractors_0_redo = _zz_AlignerPlugin_logic_extractors_0_redo_4[0];
    if(when_AlignerPlugin_l160) begin
      AlignerPlugin_logic_extractors_0_redo = 1'b0;
    end
  end

  assign AlignerPlugin_logic_extractors_0_localMask = (((_zz_AlignerPlugin_logic_extractors_0_redo ? {AlignerPlugin_logic_scanners_0_checker_1_required,AlignerPlugin_logic_scanners_0_checker_0_required} : 2'b00) | (_zz_AlignerPlugin_logic_extractors_0_redo_1 ? {AlignerPlugin_logic_scanners_1_checker_1_required,AlignerPlugin_logic_scanners_1_checker_0_required} : 2'b00)) | ((_zz_AlignerPlugin_logic_extractors_0_redo_2 ? {AlignerPlugin_logic_scanners_2_checker_1_required,AlignerPlugin_logic_scanners_2_checker_0_required} : 2'b00) | (_zz_AlignerPlugin_logic_extractors_0_redo_3 ? {AlignerPlugin_logic_scanners_3_checker_1_required,AlignerPlugin_logic_scanners_3_checker_0_required} : 2'b00)));
  assign AlignerPlugin_logic_extractors_0_usageMask = (((_zz_AlignerPlugin_logic_extractors_0_redo ? AlignerPlugin_logic_scanners_0_usageMask : 4'b0000) | (_zz_AlignerPlugin_logic_extractors_0_redo_1 ? AlignerPlugin_logic_scanners_1_usageMask : 4'b0000)) | ((_zz_AlignerPlugin_logic_extractors_0_redo_2 ? AlignerPlugin_logic_scanners_2_usageMask : 4'b0000) | (_zz_AlignerPlugin_logic_extractors_0_redo_3 ? AlignerPlugin_logic_scanners_3_usageMask : 4'b0000)));
  assign AlignerPlugin_logic_usedMask_1 = (AlignerPlugin_logic_usedMask_0 | AlignerPlugin_logic_extractors_0_usageMask);
  always @(*) begin
    AlignerPlugin_logic_extractors_0_valid = (|AlignerPlugin_logic_extractors_0_slicesOh);
    if(when_AlignerPlugin_l160) begin
      AlignerPlugin_logic_extractors_0_valid = 1'b0;
    end
    if(when_AlignerPlugin_l240) begin
      if(when_AlignerPlugin_l241) begin
        AlignerPlugin_logic_extractors_0_valid = 1'b0;
      end
    end
  end

  assign when_AlignerPlugin_l160 = (AlignerPlugin_api_haltIt || (AlignerPlugin_api_singleFetch && (! AlignerPlugin_logic_extractors_0_first)));
  assign when_AlignerPlugin_l171 = (decode_ctrls_0_up_isFiring && 1'b1);
  assign AlignerPlugin_logic_feeder_lanes_0_valid = AlignerPlugin_logic_extractors_0_valid;
  assign decode_ctrls_0_up_LANE_SEL_0 = AlignerPlugin_logic_feeder_lanes_0_valid;
  always @(*) begin
    decode_ctrls_0_up_Decode_INSTRUCTION_0 = AlignerPlugin_logic_extractors_0_ctx_instruction;
    if(AlignerPlugin_logic_feeder_lanes_0_isRvc) begin
      decode_ctrls_0_up_Decode_INSTRUCTION_0 = AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst;
    end
  end

  always @(*) begin
    decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_0 = 1'b0;
    if(AlignerPlugin_logic_feeder_lanes_0_isRvc) begin
      decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_0 = AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal;
    end
  end

  always @(*) begin
    decode_ctrls_0_up_Decode_INSTRUCTION_RAW_0 = AlignerPlugin_logic_extractors_0_ctx_instruction;
    if(AlignerPlugin_logic_feeder_lanes_0_isRvc) begin
      decode_ctrls_0_up_Decode_INSTRUCTION_RAW_0[31 : 16] = 16'h0;
    end
  end

  assign AlignerPlugin_logic_feeder_lanes_0_isRvc = (AlignerPlugin_logic_extractors_0_ctx_instruction[1 : 0] != 2'b11);
  always @(*) begin
    AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    case(switch_Rvc_l55)
      5'h0 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{{{{{2'b00,AlignerPlugin_logic_extractors_0_ctx_instruction[10 : 7]},AlignerPlugin_logic_extractors_0_ctx_instruction[12 : 11]},AlignerPlugin_logic_extractors_0_ctx_instruction[5]},AlignerPlugin_logic_extractors_0_ctx_instruction[6]},2'b00},5'h02},3'b000},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_1},7'h13};
      end
      5'h02 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_2,_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},3'b010},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_1},7'h03};
      end
      5'h05 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_3[11 : 5],_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_1},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},3'b011},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_3[4 : 0]},7'h27};
      end
      5'h06 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_2[11 : 5],_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_1},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},3'b010},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_2[4 : 0]},7'h23};
      end
      5'h08 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5,AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},3'b000},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},7'h13};
      end
      5'h09 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_8[20],_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_8[10 : 1]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_8[11]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_8[19 : 12]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_20},7'h6f};
      end
      5'h0a : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5,5'h0},3'b000},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},7'h13};
      end
      5'h0b : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = ((AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7] == 5'h02) ? {{{{{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_23,_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_24},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_25},AlignerPlugin_logic_extractors_0_ctx_instruction[6]},4'b0000},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},3'b000},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},7'h13} : {{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_26[31 : 12],AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},7'h37});
      end
      5'h0c : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_27;
      end
      5'h0d : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_15[20],_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_15[10 : 1]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_15[11]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_15[19 : 12]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_19},7'h6f};
      end
      5'h0e : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[12],_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[10 : 5]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_19},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},3'b000},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[4 : 1]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[11]},7'h63};
      end
      5'h0f : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[12],_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[10 : 5]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_19},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},3'b001},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[4 : 1]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18[11]},7'h63};
      end
      5'h10 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{{6'h0,AlignerPlugin_logic_extractors_0_ctx_instruction[12]},AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2]},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},3'b001},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},7'h13};
      end
      5'h12 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{{{{4'b0000,AlignerPlugin_logic_extractors_0_ctx_instruction[3 : 2]},AlignerPlugin_logic_extractors_0_ctx_instruction[12]},AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 4]},2'b00},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_21},3'b010},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},7'h03};
      end
      5'h14 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = ((AlignerPlugin_logic_extractors_0_ctx_instruction[12 : 2] == 11'h400) ? 32'h00100073 : ((AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2] == 5'h0) ? {{{{12'h0,AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},3'b000},(AlignerPlugin_logic_extractors_0_ctx_instruction[12] ? _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_20 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_19)},7'h67} : {{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_31,_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_32},(_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_33 ? _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_34 : _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_19)},3'b000},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7]},7'h33}));
      end
      5'h16 : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_35[11 : 5],AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2]},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_21},3'b010},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_36[4 : 0]},7'h23};
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal = 1'b0;
    case(switch_Rvc_l55)
      5'h0 : begin
        if(when_Rvc_l59) begin
          AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal = 1'b1;
        end
      end
      5'h02 : begin
      end
      5'h05 : begin
      end
      5'h06 : begin
      end
      5'h08 : begin
      end
      5'h09 : begin
      end
      5'h0a : begin
      end
      5'h0b : begin
        if(when_Rvc_l80) begin
          AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal = 1'b1;
        end
      end
      5'h0c : begin
      end
      5'h0d : begin
      end
      5'h0e : begin
      end
      5'h0f : begin
      end
      5'h10 : begin
      end
      5'h12 : begin
        if(when_Rvc_l101) begin
          AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal = 1'b1;
        end
      end
      5'h14 : begin
        if(when_Rvc_l114) begin
          AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal = 1'b1;
        end
      end
      5'h16 : begin
      end
      default : begin
        AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal = 1'b1;
      end
    endcase
  end

  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst = {2'b01,AlignerPlugin_logic_extractors_0_ctx_instruction[9 : 7]};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_1 = {2'b01,AlignerPlugin_logic_extractors_0_ctx_instruction[4 : 2]};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_2 = {{{{5'h0,AlignerPlugin_logic_extractors_0_ctx_instruction[5]},AlignerPlugin_logic_extractors_0_ctx_instruction[12 : 10]},AlignerPlugin_logic_extractors_0_ctx_instruction[6]},2'b00};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_3 = {{{4'b0000,AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 5]},AlignerPlugin_logic_extractors_0_ctx_instruction[12 : 10]},3'b000};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4 = AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  always @(*) begin
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[11] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[10] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[9] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[8] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[7] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[6] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[5] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_4;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_5[4 : 0] = AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2];
  end

  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6 = AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  always @(*) begin
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[9] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[8] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[7] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[6] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[5] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[4] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[3] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[2] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[1] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7[0] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_6;
  end

  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_8 = {{{{{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_7,AlignerPlugin_logic_extractors_0_ctx_instruction[8]},AlignerPlugin_logic_extractors_0_ctx_instruction[10 : 9]},AlignerPlugin_logic_extractors_0_ctx_instruction[6]},AlignerPlugin_logic_extractors_0_ctx_instruction[7]},AlignerPlugin_logic_extractors_0_ctx_instruction[2]},AlignerPlugin_logic_extractors_0_ctx_instruction[11]},AlignerPlugin_logic_extractors_0_ctx_instruction[5 : 3]},1'b0};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9 = AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  always @(*) begin
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[14] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[13] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[12] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[11] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[10] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[9] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[8] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[7] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[6] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[5] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[4] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[3] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[2] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[1] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_10[0] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_9;
  end

  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_11 = AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  always @(*) begin
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_12[2] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_11;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_12[1] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_11;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_12[0] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_11;
  end

  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13 = AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  always @(*) begin
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[9] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[8] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[7] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[6] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[5] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[4] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[3] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[2] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[1] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14[0] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_13;
  end

  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_15 = {{{{{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_14,AlignerPlugin_logic_extractors_0_ctx_instruction[8]},AlignerPlugin_logic_extractors_0_ctx_instruction[10 : 9]},AlignerPlugin_logic_extractors_0_ctx_instruction[6]},AlignerPlugin_logic_extractors_0_ctx_instruction[7]},AlignerPlugin_logic_extractors_0_ctx_instruction[2]},AlignerPlugin_logic_extractors_0_ctx_instruction[11]},AlignerPlugin_logic_extractors_0_ctx_instruction[5 : 3]},1'b0};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_16 = AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  always @(*) begin
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_17[4] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_16;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_17[3] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_16;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_17[2] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_16;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_17[1] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_16;
    _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_17[0] = _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_16;
  end

  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_18 = {{{{{_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_17,AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 5]},AlignerPlugin_logic_extractors_0_ctx_instruction[2]},AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 10]},AlignerPlugin_logic_extractors_0_ctx_instruction[4 : 3]},1'b0};
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_19 = 5'h0;
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_20 = 5'h01;
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_21 = 5'h02;
  assign switch_Rvc_l55 = {AlignerPlugin_logic_extractors_0_ctx_instruction[1 : 0],AlignerPlugin_logic_extractors_0_ctx_instruction[15 : 13]};
  assign when_Rvc_l59 = (AlignerPlugin_logic_extractors_0_ctx_instruction[12 : 5] == 8'h0);
  assign when_Rvc_l80 = ((AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2] == 5'h0) && (AlignerPlugin_logic_extractors_0_ctx_instruction[12] == 1'b0));
  assign _zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22 = {{{{_zz__zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst_22,_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},3'b101},_zz_AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst},7'h13};
  assign when_Rvc_l101 = (AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7] == 5'h0);
  assign when_Rvc_l114 = (((AlignerPlugin_logic_extractors_0_ctx_instruction[11 : 7] == 5'h0) && (AlignerPlugin_logic_extractors_0_ctx_instruction[6 : 2] == 5'h0)) && (AlignerPlugin_logic_extractors_0_ctx_instruction[12] == 1'b0));
  assign _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0 = AlignerPlugin_logic_extractors_0_localMask;
  assign _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_1 = {_zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0[0],_zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0[1]};
  assign _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_2 = _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_1[0];
  always @(*) begin
    _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_3[0] = (_zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_2 && (! 1'b0));
    _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_3[1] = (_zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_1[1] && (! _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_2));
  end

  assign _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_4 = _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_3;
  assign _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_5 = _zz__zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_5[1];
  assign decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0 = _zz_decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0_5;
  assign decode_ctrls_0_up_PC_0 = AlignerPlugin_logic_extractors_0_ctx_pc;
  assign decode_ctrls_0_up_Decode_DOP_ID_0 = AlignerPlugin_logic_feeder_harts_0_dopId;
  assign decode_ctrls_0_up_Fetch_ID_0 = AlignerPlugin_logic_extractors_0_ctx_hm_Fetch_ID;
  assign decode_ctrls_0_up_TRAP_0 = AlignerPlugin_logic_extractors_0_ctx_trap;
  assign decode_ctrls_0_up_valid = (|AlignerPlugin_logic_feeder_lanes_0_valid);
  assign _zz_AlignerPlugin_logic_slices_data_0 = {fetch_logic_ctrls_1_down_Fetch_WORD,AlignerPlugin_logic_buffer_data};
  assign AlignerPlugin_logic_slices_data_0 = _zz_AlignerPlugin_logic_slices_data_0[15 : 0];
  assign AlignerPlugin_logic_slices_data_1 = _zz_AlignerPlugin_logic_slices_data_0[31 : 16];
  assign AlignerPlugin_logic_slices_data_2 = _zz_AlignerPlugin_logic_slices_data_0[47 : 32];
  assign AlignerPlugin_logic_slices_data_3 = _zz_AlignerPlugin_logic_slices_data_0[63 : 48];
  assign AlignerPlugin_logic_slices_mask = {(fetch_logic_ctrls_1_down_isValid ? fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK : 2'b00),AlignerPlugin_logic_buffer_mask};
  assign AlignerPlugin_logic_slices_last = {fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_LAST,AlignerPlugin_logic_buffer_last};
  assign when_AlignerPlugin_l240 = (! fetch_logic_ctrls_1_down_valid);
  assign when_AlignerPlugin_l241 = (AlignerPlugin_logic_extractors_0_usageMask[3 : 2] != 2'b00);
  assign AlignerPlugin_logic_buffer_downFire = (decode_ctrls_0_up_isReady || decode_ctrls_0_up_isCancel);
  assign AlignerPlugin_logic_buffer_usedMask = (AlignerPlugin_logic_extractors_0_valid ? AlignerPlugin_logic_extractors_0_usageMask : 4'b0000);
  assign AlignerPlugin_logic_buffer_haltUp = ((|(AlignerPlugin_logic_buffer_mask & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[1 : 0] : 2'b00)))) || AlignerPlugin_api_haltIt);
  assign fetch_logic_ctrls_1_down_ready = ((! fetch_logic_ctrls_1_down_valid) || (! AlignerPlugin_logic_buffer_haltUp));
  assign when_AlignerPlugin_l256 = ((fetch_logic_ctrls_1_down_isValid && fetch_logic_ctrls_1_down_isReady) && (! fetch_logic_ctrls_1_down_isCancel));
  assign execute_ctrl0_down_AguPlugin_SIZE_lane0 = execute_ctrl0_down_Decode_UOP_lane0[13 : 12];
  always @(*) begin
    execute_ctrl2_down_LsuCachelessPlugin_logic_onFirst_WRITE_DATA_lane0 = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    execute_ctrl2_down_LsuCachelessPlugin_logic_onFirst_WRITE_DATA_lane0[31 : 0] = execute_ctrl2_up_integer_RS2_lane0;
  end

  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_RAW_ADDRESS_lane0 = execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_MISS_ALIGNED_lane0 = (|{((execute_ctrl2_down_AguPlugin_SIZE_lane0 == 2'b10) && (execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_RAW_ADDRESS_lane0[1 : 0] != 2'b00)),((execute_ctrl2_down_AguPlugin_SIZE_lane0 == 2'b01) && (execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_RAW_ADDRESS_lane0[0 : 0] != 1'b0))});
  assign LsuCachelessPlugin_logic_onPma_port_cmd_address = execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  assign LsuCachelessPlugin_logic_onPma_port_cmd_size = execute_ctrl2_down_AguPlugin_SIZE_lane0;
  always @(*) begin
    LsuCachelessPlugin_logic_onPma_port_cmd_op[0] = execute_ctrl2_down_AguPlugin_STORE_lane0;
    LsuCachelessPlugin_logic_onPma_port_cmd_op[1] = execute_ctrl2_down_AguPlugin_EXECUTE_lane0;
  end

  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault = LsuCachelessPlugin_logic_onPma_port_rsp_fault;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io = LsuCachelessPlugin_logic_onPma_port_rsp_io;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_MMU_FAILURE_lane0 = (((execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_PAGE_FAULT_lane0 || execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ACCESS_FAULT_lane0) || execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_REFILL_lane0) || execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_HAZARD_lane0);
  assign PrivilegedPlugin_api_lsuTriggerBus_load = (execute_ctrl2_down_AguPlugin_LOAD_lane0 || execute_ctrl2_down_AguPlugin_EXECUTE_lane0);
  assign PrivilegedPlugin_api_lsuTriggerBus_store = execute_ctrl2_down_AguPlugin_STORE_lane0;
  assign PrivilegedPlugin_api_lsuTriggerBus_virtual = execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_RAW_ADDRESS_lane0;
  assign PrivilegedPlugin_api_lsuTriggerBus_size = execute_ctrl2_down_AguPlugin_SIZE_lane0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onTrigger_HIT_lane0 = 1'b0;
  always @(*) begin
    LsuCachelessPlugin_logic_onFork_skip = 1'b0;
    if(when_LsuCachelessPlugin_l331) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
    if(when_LsuCachelessPlugin_l337) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_PAGE_FAULT_lane0) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ACCESS_FAULT_lane0) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_REFILL_lane0) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_HAZARD_lane0) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_MISS_ALIGNED_lane0) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onTrigger_HIT_lane0) begin
      LsuCachelessPlugin_logic_onFork_skip = 1'b1;
    end
  end

  assign when_LsuCachelessPlugin_l284 = (! execute_freeze_valid);
  assign LsuCachelessPlugin_logic_onFork_askFence = (execute_ctrl2_up_LANE_SEL_lane0 && ((execute_ctrl2_down_LsuCachelessPlugin_FENCE_lane0 || (execute_ctrl2_down_AguPlugin_SEL_lane0 && execute_ctrl2_down_AguPlugin_ATOMIC_lane0)) || LsuCachelessPlugin_logic_onFork_askFenceReg));
  assign LsuCachelessPlugin_logic_onFork_doFence = (LsuCachelessPlugin_logic_onFork_askFence && LsuCachelessPlugin_logic_cmdInflights);
  assign LsuCachelessPlugin_logic_bus_cmd_fire = (LsuCachelessPlugin_logic_bus_cmd_valid && LsuCachelessPlugin_logic_bus_cmd_ready);
  always @(*) begin
    LsuCachelessPlugin_logic_onFork_cmdCounter_willIncrement = 1'b0;
    if(LsuCachelessPlugin_logic_bus_cmd_fire) begin
      LsuCachelessPlugin_logic_onFork_cmdCounter_willIncrement = 1'b1;
    end
  end

  assign LsuCachelessPlugin_logic_onFork_cmdCounter_willDecrement = 1'b0;
  assign LsuCachelessPlugin_logic_onFork_cmdCounter_willClear = 1'b0;
  assign LsuCachelessPlugin_logic_onFork_cmdCounter_willLoad = 1'b0;
  assign LsuCachelessPlugin_logic_onFork_cmdCounter_willOverflowIfInc = (LsuCachelessPlugin_logic_onFork_cmdCounter_value == 1'b1);
  assign LsuCachelessPlugin_logic_onFork_cmdCounter_willUnderflowIfDec = (LsuCachelessPlugin_logic_onFork_cmdCounter_value == 1'b0);
  assign LsuCachelessPlugin_logic_onFork_cmdCounter_willOverflow = (LsuCachelessPlugin_logic_onFork_cmdCounter_willOverflowIfInc && LsuCachelessPlugin_logic_onFork_cmdCounter_willIncrement);
  always @(*) begin
    LsuCachelessPlugin_logic_onFork_cmdCounter_valueNext = (LsuCachelessPlugin_logic_onFork_cmdCounter_value + LsuCachelessPlugin_logic_onFork_cmdCounter_willIncrement);
    if(LsuCachelessPlugin_logic_onFork_cmdCounter_willClear) begin
      LsuCachelessPlugin_logic_onFork_cmdCounter_valueNext = 1'b0;
    end
  end

  assign LsuCachelessPlugin_logic_onFork_cmdCounter_willUnderflow = (LsuCachelessPlugin_logic_onFork_cmdCounter_willUnderflowIfDec && LsuCachelessPlugin_logic_onFork_cmdCounter_willDecrement);
  assign when_LsuCachelessPlugin_l289 = (! execute_freeze_valid);
  assign LsuCachelessPlugin_logic_bus_cmd_isStall = (LsuCachelessPlugin_logic_bus_cmd_valid && (! LsuCachelessPlugin_logic_bus_cmd_ready));
  assign LsuCachelessPlugin_logic_bus_cmd_valid = (((((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_AguPlugin_SEL_lane0) && (! LsuCachelessPlugin_logic_onFork_cmdSent)) && (! execute_lane0_ctrls_2_upIsCancel)) && (! LsuCachelessPlugin_logic_onFork_skip)) && (! LsuCachelessPlugin_logic_onFork_doFence));
  assign LsuCachelessPlugin_logic_bus_cmd_payload_id = LsuCachelessPlugin_logic_onFork_cmdCounter_value;
  assign LsuCachelessPlugin_logic_bus_cmd_payload_write = execute_ctrl2_down_AguPlugin_STORE_lane0;
  assign LsuCachelessPlugin_logic_bus_cmd_payload_address = execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  assign LsuCachelessPlugin_logic_onFork_mapping_0_1 = {4{execute_ctrl2_down_LsuCachelessPlugin_logic_onFirst_WRITE_DATA_lane0[7 : 0]}};
  assign LsuCachelessPlugin_logic_onFork_mapping_1_1 = {2{execute_ctrl2_down_LsuCachelessPlugin_logic_onFirst_WRITE_DATA_lane0[15 : 0]}};
  assign LsuCachelessPlugin_logic_onFork_mapping_2_1 = {1{execute_ctrl2_down_LsuCachelessPlugin_logic_onFirst_WRITE_DATA_lane0[31 : 0]}};
  always @(*) begin
    _zz_LsuCachelessPlugin_logic_bus_cmd_payload_data = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    case(LsuCachelessPlugin_logic_bus_cmd_payload_size)
      2'b00 : begin
        _zz_LsuCachelessPlugin_logic_bus_cmd_payload_data = LsuCachelessPlugin_logic_onFork_mapping_0_1;
      end
      2'b01 : begin
        _zz_LsuCachelessPlugin_logic_bus_cmd_payload_data = LsuCachelessPlugin_logic_onFork_mapping_1_1;
      end
      2'b10 : begin
        _zz_LsuCachelessPlugin_logic_bus_cmd_payload_data = LsuCachelessPlugin_logic_onFork_mapping_2_1;
      end
      default : begin
      end
    endcase
  end

  assign LsuCachelessPlugin_logic_bus_cmd_payload_data = _zz_LsuCachelessPlugin_logic_bus_cmd_payload_data;
  assign LsuCachelessPlugin_logic_bus_cmd_payload_size = execute_ctrl2_down_AguPlugin_SIZE_lane0;
  always @(*) begin
    _zz_LsuCachelessPlugin_logic_bus_cmd_payload_mask = 4'bxxxx;
    case(LsuCachelessPlugin_logic_bus_cmd_payload_size)
      2'b00 : begin
        _zz_LsuCachelessPlugin_logic_bus_cmd_payload_mask = 4'b0001;
      end
      2'b01 : begin
        _zz_LsuCachelessPlugin_logic_bus_cmd_payload_mask = 4'b0011;
      end
      2'b10 : begin
        _zz_LsuCachelessPlugin_logic_bus_cmd_payload_mask = 4'b1111;
      end
      default : begin
      end
    endcase
  end

  assign LsuCachelessPlugin_logic_bus_cmd_payload_mask = (_zz_LsuCachelessPlugin_logic_bus_cmd_payload_mask <<< LsuCachelessPlugin_logic_bus_cmd_payload_address[1 : 0]);
  assign LsuCachelessPlugin_logic_bus_cmd_payload_io = execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  assign LsuCachelessPlugin_logic_bus_cmd_payload_fromHart = 1'b1;
  assign LsuCachelessPlugin_logic_bus_cmd_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign LsuCachelessPlugin_logic_bus_cmd_payload_amoEnable = execute_ctrl2_down_AguPlugin_ATOMIC_lane0;
  assign LsuCachelessPlugin_logic_bus_cmd_payload_amoOp = execute_ctrl2_down_Decode_UOP_lane0[31 : 27];
  assign LsuCachelessPlugin_logic_onFork_freezeIt = (LsuCachelessPlugin_logic_bus_cmd_isStall || LsuCachelessPlugin_logic_onFork_doFence);
  always @(*) begin
    LsuCachelessPlugin_logic_flushPort_valid = 1'b0;
    if(when_LsuCachelessPlugin_l420) begin
      LsuCachelessPlugin_logic_flushPort_valid = 1'b1;
    end
  end

  assign LsuCachelessPlugin_logic_flushPort_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign LsuCachelessPlugin_logic_flushPort_payload_self = 1'b0;
  always @(*) begin
    LsuCachelessPlugin_logic_trapPort_valid = 1'b0;
    if(when_LsuCachelessPlugin_l420) begin
      LsuCachelessPlugin_logic_trapPort_valid = 1'b1;
    end
  end

  assign LsuCachelessPlugin_logic_trapPort_payload_tval = execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_RAW_ADDRESS_lane0;
  assign LsuCachelessPlugin_logic_trapPort_payload_tval2 = {2'd0, _zz_LsuCachelessPlugin_logic_trapPort_payload_tval2};
  always @(*) begin
    LsuCachelessPlugin_logic_trapPort_payload_exception = 1'bx;
    if(when_LsuCachelessPlugin_l331) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b0;
    end
    if(when_LsuCachelessPlugin_l337) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_PAGE_FAULT_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ACCESS_FAULT_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_REFILL_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b0;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_HAZARD_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b0;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_MISS_ALIGNED_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b1;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onTrigger_HIT_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_exception = 1'b0;
    end
  end

  always @(*) begin
    LsuCachelessPlugin_logic_trapPort_payload_code = 5'bxxxxx;
    if(when_LsuCachelessPlugin_l331) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = 5'h04;
    end
    if(when_LsuCachelessPlugin_l337) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = 5'h05;
      if(execute_ctrl2_down_AguPlugin_STORE_lane0) begin
        LsuCachelessPlugin_logic_trapPort_payload_code[1] = 1'b1;
      end
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_PAGE_FAULT_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = 5'h0d;
      if(execute_ctrl2_down_AguPlugin_STORE_lane0) begin
        LsuCachelessPlugin_logic_trapPort_payload_code[1] = 1'b1;
      end
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ACCESS_FAULT_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = 5'h05;
      if(execute_ctrl2_down_AguPlugin_STORE_lane0) begin
        LsuCachelessPlugin_logic_trapPort_payload_code[1] = 1'b1;
      end
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_REFILL_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = 5'h07;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_HAZARD_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = 5'h04;
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_MISS_ALIGNED_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = {2'd0, _zz_LsuCachelessPlugin_logic_trapPort_payload_code};
    end
    if(execute_ctrl2_down_LsuCachelessPlugin_logic_onTrigger_HIT_lane0) begin
      LsuCachelessPlugin_logic_trapPort_payload_code = 5'h03;
    end
  end

  always @(*) begin
    LsuCachelessPlugin_logic_trapPort_payload_arg = 3'b000;
    LsuCachelessPlugin_logic_trapPort_payload_arg[1 : 0] = _zz_LsuCachelessPlugin_logic_trapPort_payload_arg;
    LsuCachelessPlugin_logic_trapPort_payload_arg[2] = execute_ctrl2_down_AguPlugin_GUEST_lane0;
  end

  assign early0_EnvPlugin_logic_trapPort_payload_tval = (((execute_ctrl2_down_early0_EnvPlugin_OP_lane0 == EnvPluginOp_EBREAK) ? execute_ctrl2_down_PC_lane0 : 32'h0) | ((|{(execute_ctrl2_down_early0_EnvPlugin_OP_lane0 == EnvPluginOp_SFENCE_VMA),{(execute_ctrl2_down_early0_EnvPlugin_OP_lane0 == EnvPluginOp_WFI),(execute_ctrl2_down_early0_EnvPlugin_OP_lane0 == EnvPluginOp_PRIV_RET)}}) ? execute_ctrl2_down_Decode_UOP_lane0 : 32'h0));
  assign early0_EnvPlugin_logic_trapPort_payload_tval2 = 32'h0;
  always @(*) begin
    early0_EnvPlugin_logic_trapPort_payload_code = 5'h02;
    case(execute_ctrl2_down_early0_EnvPlugin_OP_lane0)
      EnvPluginOp_EBREAK : begin
        early0_EnvPlugin_logic_trapPort_payload_code = 5'h03;
      end
      EnvPluginOp_ECALL : begin
        early0_EnvPlugin_logic_trapPort_payload_code = ((early0_EnvPlugin_logic_exe_privilege[2] ? _zz_early0_EnvPlugin_logic_trapPort_payload_code : _zz_early0_EnvPlugin_logic_trapPort_payload_code_2) | 5'h08);
      end
      EnvPluginOp_PRIV_RET : begin
        if(when_EnvPlugin_l131) begin
          early0_EnvPlugin_logic_trapPort_payload_code = 5'h01;
        end
      end
      EnvPluginOp_WFI : begin
        if(when_EnvPlugin_l145) begin
          early0_EnvPlugin_logic_trapPort_payload_code = 5'h08;
        end
      end
      EnvPluginOp_FENCE_I : begin
        early0_EnvPlugin_logic_trapPort_payload_code = 5'h02;
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    early0_EnvPlugin_logic_trapPort_payload_arg = 3'b000;
    case(execute_ctrl2_down_early0_EnvPlugin_OP_lane0)
      EnvPluginOp_PRIV_RET : begin
        if(when_EnvPlugin_l131) begin
          early0_EnvPlugin_logic_trapPort_payload_arg[2 : 0] = early0_EnvPlugin_logic_exe_xretPriv;
        end
      end
      default : begin
      end
    endcase
  end

  assign early0_EnvPlugin_logic_exe_privilege = PrivilegedPlugin_logic_harts_0_privilege;
  assign early0_EnvPlugin_logic_exe_isGuest = early0_EnvPlugin_logic_exe_privilege[2];
  assign _zz_early0_EnvPlugin_logic_exe_xretPriv = execute_ctrl2_down_Decode_UOP_lane0[29 : 28];
  always @(*) begin
    early0_EnvPlugin_logic_exe_xretPriv[2] = _zz_early0_EnvPlugin_logic_exe_xretPriv_1[0];
    early0_EnvPlugin_logic_exe_xretPriv[1 : 0] = _zz_early0_EnvPlugin_logic_exe_xretPriv;
  end

  always @(*) begin
    early0_EnvPlugin_logic_exe_commit = 1'b0;
    case(execute_ctrl2_down_early0_EnvPlugin_OP_lane0)
      EnvPluginOp_PRIV_RET : begin
        if(when_EnvPlugin_l131) begin
          early0_EnvPlugin_logic_exe_commit = 1'b1;
        end
      end
      EnvPluginOp_WFI : begin
        if(when_EnvPlugin_l145) begin
          early0_EnvPlugin_logic_exe_commit = 1'b1;
        end
      end
      EnvPluginOp_FENCE_I : begin
        early0_EnvPlugin_logic_exe_commit = 1'b1;
      end
      default : begin
      end
    endcase
  end

  assign early0_EnvPlugin_logic_exe_retKo = 1'b0;
  assign early0_EnvPlugin_logic_exe_wfiMapping_0 = 1'b1;
  assign early0_EnvPlugin_logic_exe_wfiMapping_1 = 1'b1;
  assign early0_EnvPlugin_logic_exe_vmaKoMapping_0 = 1'b1;
  assign early0_EnvPlugin_logic_exe_vmaKo = 1'b0;
  assign early0_EnvPlugin_logic_exe_gvmaKo = 1'b1;
  assign early0_EnvPlugin_logic_exe_vvmaKo = 1'b1;
  assign when_EnvPlugin_l131 = (($signed(early0_EnvPlugin_logic_exe_xretPriv) <= $signed(PrivilegedPlugin_logic_harts_0_privilege)) && (! early0_EnvPlugin_logic_exe_retKo));
  assign when_EnvPlugin_l145 = (($signed(early0_EnvPlugin_logic_exe_privilege) == $signed(3'b011)) || ((! PrivilegedPlugin_logic_harts_0_m_status_tw) && (|{(($signed(early0_EnvPlugin_logic_exe_privilege) == $signed(3'b000)) && early0_EnvPlugin_logic_exe_wfiMapping_1),(($signed(early0_EnvPlugin_logic_exe_privilege) == $signed(3'b001)) && early0_EnvPlugin_logic_exe_wfiMapping_0)})));
  assign when_EnvPlugin_l203 = (execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_early0_EnvPlugin_SEL_lane0);
  assign when_EnvPlugin_l207 = (! early0_EnvPlugin_logic_exe_commit);
  assign execute_ctrl2_down_early0_BranchPlugin_logic_alu_EQ_lane0 = ($signed(execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0) == $signed(execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0));
  assign early0_BranchPlugin_logic_alu_expectedMsb = 1'b0;
  assign execute_ctrl2_down_early0_BranchPlugin_logic_alu_MSB_FAILED_lane0 = ((execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0 == BranchPlugin_BranchCtrlEnum_JALR) && 1'b0);
  assign switch_Misc_l245 = execute_ctrl2_down_Decode_UOP_lane0[14 : 12];
  always @(*) begin
    casez(switch_Misc_l245)
      3'b000 : begin
        _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0 = execute_ctrl2_down_early0_BranchPlugin_logic_alu_EQ_lane0;
      end
      3'b001 : begin
        _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0 = (! execute_ctrl2_down_early0_BranchPlugin_logic_alu_EQ_lane0);
      end
      3'b1?1 : begin
        _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0 = (! execute_ctrl2_down_early0_SrcPlugin_LESS_lane0);
      end
      default : begin
        _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0 = execute_ctrl2_down_early0_SrcPlugin_LESS_lane0;
      end
    endcase
  end

  always @(*) begin
    case(execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0)
      BranchPlugin_BranchCtrlEnum_JALR : begin
        _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0_1 = 1'b1;
      end
      BranchPlugin_BranchCtrlEnum_JAL : begin
        _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0_1 = 1'b1;
      end
      default : begin
        _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0_1 = _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0;
      end
    endcase
  end

  assign execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0 = _zz_execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0_1;
  assign early0_BranchPlugin_logic_jumpLogic_needFix = (execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0 || execute_ctrl2_down_early0_BranchPlugin_logic_alu_MSB_FAILED_lane0);
  assign early0_BranchPlugin_logic_jumpLogic_doIt = ((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_early0_BranchPlugin_SEL_lane0) && early0_BranchPlugin_logic_jumpLogic_needFix);
  assign early0_BranchPlugin_logic_pcPort_valid = early0_BranchPlugin_logic_jumpLogic_doIt;
  assign early0_BranchPlugin_logic_pcPort_payload_fault = execute_ctrl2_down_early0_BranchPlugin_logic_alu_MSB_FAILED_lane0;
  assign early0_BranchPlugin_logic_pcPort_payload_pc = execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0;
  assign early0_BranchPlugin_logic_flushPort_valid = early0_BranchPlugin_logic_jumpLogic_doIt;
  assign early0_BranchPlugin_logic_flushPort_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign early0_BranchPlugin_logic_flushPort_payload_self = 1'b0;
  assign execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_MISSALIGNED_lane0 = ((execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[0 : 0] != 1'b0) && execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0);
  assign execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_IS_JAL_lane0 = (execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0 == BranchPlugin_BranchCtrlEnum_JAL);
  assign execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_IS_JALR_lane0 = (execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0 == BranchPlugin_BranchCtrlEnum_JALR);
  assign early0_BranchPlugin_logic_jumpLogic_rdLink = (|{(execute_ctrl2_down_Decode_UOP_lane0[11 : 7] == 5'h05),(execute_ctrl2_down_Decode_UOP_lane0[11 : 7] == 5'h01)});
  assign early0_BranchPlugin_logic_jumpLogic_rs1Link = (|{(execute_ctrl2_down_Decode_UOP_lane0[19 : 15] == 5'h05),(execute_ctrl2_down_Decode_UOP_lane0[19 : 15] == 5'h01)});
  assign early0_BranchPlugin_logic_jumpLogic_rdEquRs1 = (execute_ctrl2_down_Decode_UOP_lane0[11 : 7] == execute_ctrl2_down_Decode_UOP_lane0[19 : 15]);
  assign early0_BranchPlugin_logic_jumpLogic_learn_valid = (((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_isReady) && (! execute_lane0_ctrls_2_upIsCancel)) && (|execute_ctrl2_down_early0_BranchPlugin_SEL_lane0));
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_taken = execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_COND_lane0;
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_pcTarget = execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0;
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_pcOnLastSlice = execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_LAST_SLICE_lane0;
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_isBranch = (execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0 == BranchPlugin_BranchCtrlEnum_B);
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_isPush = ((execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_IS_JAL_lane0 || execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_IS_JALR_lane0) && early0_BranchPlugin_logic_jumpLogic_rdLink);
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_isPop = (execute_ctrl2_down_early0_BranchPlugin_logic_jumpLogic_IS_JALR_lane0 && (((! early0_BranchPlugin_logic_jumpLogic_rdLink) && early0_BranchPlugin_logic_jumpLogic_rs1Link) || ((early0_BranchPlugin_logic_jumpLogic_rdLink && early0_BranchPlugin_logic_jumpLogic_rs1Link) && (! early0_BranchPlugin_logic_jumpLogic_rdEquRs1))));
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_wasWrong = early0_BranchPlugin_logic_jumpLogic_needFix;
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_badPredictedTarget = 1'b0;
  assign early0_BranchPlugin_logic_jumpLogic_learn_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign early0_BranchPlugin_logic_events_branchMiss = ((early0_BranchPlugin_logic_jumpLogic_learn_valid && early0_BranchPlugin_logic_jumpLogic_learn_payload_isBranch) && early0_BranchPlugin_logic_jumpLogic_learn_payload_wasWrong);
  assign early0_BranchPlugin_logic_events_branchCount = (early0_BranchPlugin_logic_jumpLogic_learn_valid && early0_BranchPlugin_logic_jumpLogic_learn_payload_isBranch);
  assign early0_BranchPlugin_logic_wb_valid = execute_ctrl2_down_early0_BranchPlugin_SEL_lane0;
  assign early0_BranchPlugin_logic_wb_payload = execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_FALSE_lane0;
  assign execute_ctrl2_COMPLETED_lane0_bypass = (execute_ctrl2_up_COMPLETED_lane0 || execute_ctrl2_down_COMPLETION_AT_2_lane0);
  assign execute_ctrl4_COMPLETED_lane0_bypass = (execute_ctrl4_up_COMPLETED_lane0 || execute_ctrl4_down_COMPLETION_AT_4_lane0);
  assign execute_ctrl3_COMPLETED_lane0_bypass = (execute_ctrl3_up_COMPLETED_lane0 || execute_ctrl3_down_COMPLETION_AT_3_lane0);
  assign execute_lane0_api_hartsInflight[0] = (|{(execute_ctrl4_up_LANE_SEL_lane0 && 1'b1),{(execute_ctrl3_up_LANE_SEL_lane0 && 1'b1),{(execute_ctrl2_up_LANE_SEL_lane0 && 1'b1),(execute_ctrl1_up_LANE_SEL_lane0 && 1'b1)}}});
  assign LearnPlugin_logic_buffered_0_valid = early0_BranchPlugin_logic_jumpLogic_learn_valid;
  assign early0_BranchPlugin_logic_jumpLogic_learn_ready = LearnPlugin_logic_buffered_0_ready;
  assign LearnPlugin_logic_buffered_0_payload_pcOnLastSlice = early0_BranchPlugin_logic_jumpLogic_learn_payload_pcOnLastSlice;
  assign LearnPlugin_logic_buffered_0_payload_pcTarget = early0_BranchPlugin_logic_jumpLogic_learn_payload_pcTarget;
  assign LearnPlugin_logic_buffered_0_payload_taken = early0_BranchPlugin_logic_jumpLogic_learn_payload_taken;
  assign LearnPlugin_logic_buffered_0_payload_isBranch = early0_BranchPlugin_logic_jumpLogic_learn_payload_isBranch;
  assign LearnPlugin_logic_buffered_0_payload_isPush = early0_BranchPlugin_logic_jumpLogic_learn_payload_isPush;
  assign LearnPlugin_logic_buffered_0_payload_isPop = early0_BranchPlugin_logic_jumpLogic_learn_payload_isPop;
  assign LearnPlugin_logic_buffered_0_payload_wasWrong = early0_BranchPlugin_logic_jumpLogic_learn_payload_wasWrong;
  assign LearnPlugin_logic_buffered_0_payload_badPredictedTarget = early0_BranchPlugin_logic_jumpLogic_learn_payload_badPredictedTarget;
  assign LearnPlugin_logic_buffered_0_payload_uopId = early0_BranchPlugin_logic_jumpLogic_learn_payload_uopId;
  assign LearnPlugin_logic_buffered_0_ready = streamArbiter_1_io_inputs_0_ready;
  assign LearnPlugin_logic_arbitrated_valid = streamArbiter_1_io_output_valid;
  assign LearnPlugin_logic_arbitrated_payload_pcOnLastSlice = streamArbiter_1_io_output_payload_pcOnLastSlice;
  assign LearnPlugin_logic_arbitrated_payload_pcTarget = streamArbiter_1_io_output_payload_pcTarget;
  assign LearnPlugin_logic_arbitrated_payload_taken = streamArbiter_1_io_output_payload_taken;
  assign LearnPlugin_logic_arbitrated_payload_isBranch = streamArbiter_1_io_output_payload_isBranch;
  assign LearnPlugin_logic_arbitrated_payload_isPush = streamArbiter_1_io_output_payload_isPush;
  assign LearnPlugin_logic_arbitrated_payload_isPop = streamArbiter_1_io_output_payload_isPop;
  assign LearnPlugin_logic_arbitrated_payload_wasWrong = streamArbiter_1_io_output_payload_wasWrong;
  assign LearnPlugin_logic_arbitrated_payload_badPredictedTarget = streamArbiter_1_io_output_payload_badPredictedTarget;
  assign LearnPlugin_logic_arbitrated_payload_uopId = streamArbiter_1_io_output_payload_uopId;
  assign LearnPlugin_logic_arbitrated_ready = 1'b1;
  assign LearnPlugin_logic_arbitrated_toFlow_valid = LearnPlugin_logic_arbitrated_valid;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_pcOnLastSlice = LearnPlugin_logic_arbitrated_payload_pcOnLastSlice;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_pcTarget = LearnPlugin_logic_arbitrated_payload_pcTarget;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_taken = LearnPlugin_logic_arbitrated_payload_taken;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_isBranch = LearnPlugin_logic_arbitrated_payload_isBranch;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_isPush = LearnPlugin_logic_arbitrated_payload_isPush;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_isPop = LearnPlugin_logic_arbitrated_payload_isPop;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_wasWrong = LearnPlugin_logic_arbitrated_payload_wasWrong;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_badPredictedTarget = LearnPlugin_logic_arbitrated_payload_badPredictedTarget;
  assign LearnPlugin_logic_arbitrated_toFlow_payload_uopId = LearnPlugin_logic_arbitrated_payload_uopId;
  assign LearnPlugin_logic_learn_valid = LearnPlugin_logic_arbitrated_toFlow_valid;
  assign LearnPlugin_logic_learn_payload_pcOnLastSlice = LearnPlugin_logic_arbitrated_toFlow_payload_pcOnLastSlice;
  assign LearnPlugin_logic_learn_payload_pcTarget = LearnPlugin_logic_arbitrated_toFlow_payload_pcTarget;
  assign LearnPlugin_logic_learn_payload_taken = LearnPlugin_logic_arbitrated_toFlow_payload_taken;
  assign LearnPlugin_logic_learn_payload_isBranch = LearnPlugin_logic_arbitrated_toFlow_payload_isBranch;
  assign LearnPlugin_logic_learn_payload_isPush = LearnPlugin_logic_arbitrated_toFlow_payload_isPush;
  assign LearnPlugin_logic_learn_payload_isPop = LearnPlugin_logic_arbitrated_toFlow_payload_isPop;
  assign LearnPlugin_logic_learn_payload_wasWrong = LearnPlugin_logic_arbitrated_toFlow_payload_wasWrong;
  assign LearnPlugin_logic_learn_payload_badPredictedTarget = LearnPlugin_logic_arbitrated_toFlow_payload_badPredictedTarget;
  assign LearnPlugin_logic_learn_payload_uopId = LearnPlugin_logic_arbitrated_toFlow_payload_uopId;
  assign when_DecoderPlugin_l148 = (decode_ctrls_1_up_isMoving && 1'b1);
  assign DecoderPlugin_logic_interrupt_async = PrivilegedPlugin_logic_harts_0_int_pending;
  assign when_DecoderPlugin_l156 = (((! decode_ctrls_1_up_valid) || decode_ctrls_1_up_ready) || decode_ctrls_1_up_isCanceling);
  assign _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00000018) == 32'h0);
  assign decode_ctrls_1_down_RS1_ENABLE_0 = _zz_decode_ctrls_1_down_RS1_ENABLE_0[0];
  assign decode_ctrls_1_down_RS1_PHYS_0 = _zz_decode_ctrls_1_down_RS1_PHYS_0[4 : 0];
  assign decode_ctrls_1_down_RS2_ENABLE_0 = _zz_decode_ctrls_1_down_RS2_ENABLE_0[0];
  assign decode_ctrls_1_down_RS2_PHYS_0 = _zz_decode_ctrls_1_down_RS2_PHYS_0[4 : 0];
  always @(*) begin
    decode_ctrls_1_down_RD_ENABLE_0 = _zz_decode_ctrls_1_down_RD_ENABLE_0[0];
    if(when_DecoderPlugin_l263) begin
      decode_ctrls_1_down_RD_ENABLE_0 = 1'b0;
    end
  end

  assign decode_ctrls_1_down_RD_PHYS_0 = _zz_decode_ctrls_1_down_RD_PHYS_0[4 : 0];
  assign decode_ctrls_1_down_Decode_LEGAL_0 = ((|{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000005f) == 32'h00000017),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0000007f) == 32'h0000006f),{((decode_ctrls_1_down_Decode_INSTRUCTION_0 & _zz_decode_ctrls_1_down_Decode_LEGAL_0) == 32'h00001073),{(_zz_decode_ctrls_1_down_Decode_LEGAL_0_1 == _zz_decode_ctrls_1_down_Decode_LEGAL_0_2),{_zz_decode_ctrls_1_down_Decode_LEGAL_0_3,{_zz_decode_ctrls_1_down_Decode_LEGAL_0_4,_zz_decode_ctrls_1_down_Decode_LEGAL_0_5}}}}}}) && (! decode_ctrls_1_down_Decode_DECOMPRESSION_FAULT_0));
  assign decode_ctrls_1_down_Decode_VIRTUAL_0 = 1'b0;
  assign DecoderPlugin_logic_laneLogic_0_interruptPending = DecoderPlugin_logic_interrupt_buffered[0];
  always @(*) begin
    DecoderPlugin_logic_laneLogic_0_trapPort_valid = 1'b0;
    if(when_DecoderPlugin_l245) begin
      DecoderPlugin_logic_laneLogic_0_trapPort_valid = ((! decode_ctrls_1_up_TRAP_0) || DecoderPlugin_logic_laneLogic_0_interruptPending);
    end
  end

  always @(*) begin
    DecoderPlugin_logic_laneLogic_0_trapPort_payload_exception = 1'b1;
    if(DecoderPlugin_logic_laneLogic_0_interruptPending) begin
      DecoderPlugin_logic_laneLogic_0_trapPort_payload_exception = 1'b0;
    end
  end

  assign DecoderPlugin_logic_laneLogic_0_trapPort_payload_tval = decode_ctrls_1_down_Decode_INSTRUCTION_RAW_0;
  assign DecoderPlugin_logic_laneLogic_0_trapPort_payload_tval2 = 32'h0;
  always @(*) begin
    DecoderPlugin_logic_laneLogic_0_trapPort_payload_code = 5'h02;
    if(decode_ctrls_1_down_Decode_VIRTUAL_0) begin
      DecoderPlugin_logic_laneLogic_0_trapPort_payload_code = 5'h16;
    end
    if(DecoderPlugin_logic_laneLogic_0_interruptPending) begin
      DecoderPlugin_logic_laneLogic_0_trapPort_payload_code = 5'h0;
    end
  end

  assign DecoderPlugin_logic_laneLogic_0_trapPort_payload_laneAge = 1'b0;
  assign DecoderPlugin_logic_laneLogic_0_trapPort_payload_arg = 3'b000;
  assign when_CtrlLaneApi_l50_1 = (decode_ctrls_1_up_isReady || decode_ctrls_1_lane0_upIsCancel);
  assign DecoderPlugin_logic_laneLogic_0_completionPort_valid = ((decode_ctrls_1_up_LANE_SEL_0 && decode_ctrls_1_down_TRAP_0) && (decode_ctrls_1_up_LANE_SEL_0 && (! decode_ctrls_1_up_LANE_SEL_0_regNext)));
  assign DecoderPlugin_logic_laneLogic_0_completionPort_payload_uopId = decode_ctrls_1_down_Decode_UOP_ID_0;
  assign DecoderPlugin_logic_laneLogic_0_completionPort_payload_trap = 1'b1;
  assign DecoderPlugin_logic_laneLogic_0_completionPort_payload_commit = 1'b0;
  assign when_DecoderPlugin_l245 = (decode_ctrls_1_up_LANE_SEL_0 && (((! decode_ctrls_1_down_Decode_LEGAL_0) || DecoderPlugin_logic_laneLogic_0_interruptPending) || 1'b0));
  assign DecoderPlugin_logic_laneLogic_0_flushPort_valid = (decode_ctrls_1_up_LANE_SEL_0 && decode_ctrls_1_down_TRAP_0);
  assign DecoderPlugin_logic_laneLogic_0_flushPort_payload_uopId = decode_ctrls_1_down_Decode_UOP_ID_0;
  assign DecoderPlugin_logic_laneLogic_0_flushPort_payload_self = 1'b0;
  assign when_DecoderPlugin_l263 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0[11 : 7] == 5'h0) && (|1'b1));
  assign decode_ctrls_1_down_Decode_UOP_0 = decode_ctrls_1_down_Decode_INSTRUCTION_0;
  assign DecoderPlugin_logic_laneLogic_0_uopIdBase = DecoderPlugin_logic_harts_0_uopId;
  assign decode_ctrls_1_down_Decode_UOP_ID_0 = (DecoderPlugin_logic_laneLogic_0_uopIdBase + 16'h0);
  assign DispatchPlugin_logic_trapPendings[0] = 1'b0;
  assign DispatchPlugin_logic_candidates_0_moving = (((! DispatchPlugin_logic_candidates_0_ctx_valid) || DispatchPlugin_logic_candidates_0_fire) || DispatchPlugin_logic_candidates_0_cancel);
  assign DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard = (DispatchPlugin_logic_candidates_0_ctx_hm_RS1_ENABLE && (|{((((DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0 && execute_ctrl4_up_RD_ENABLE_lane0) && (execute_ctrl4_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS1_PHYS)) && 1'b1) && (! execute_ctrl4_down_BYPASSED_AT_4_lane0)),{(((_zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard && _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_1) && 1'b1) && (! execute_ctrl3_down_BYPASSED_AT_3_lane0)),{((_zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_2 && _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_3) && (! execute_ctrl2_down_BYPASSED_AT_2_lane0)),((_zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_4 && _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard_5) && (! execute_ctrl1_down_BYPASSED_AT_1_lane0))}}}));
  assign DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard = (DispatchPlugin_logic_candidates_0_ctx_hm_RS2_ENABLE && (|{((((DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0 && execute_ctrl4_up_RD_ENABLE_lane0) && (execute_ctrl4_up_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS)) && 1'b1) && (! execute_ctrl4_down_BYPASSED_AT_4_lane0)),{(((_zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard && _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_1) && 1'b1) && (! execute_ctrl3_down_BYPASSED_AT_3_lane0)),{((_zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_2 && _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_3) && (! execute_ctrl2_down_BYPASSED_AT_2_lane0)),((_zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_4 && _zz_DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard_5) && (! execute_ctrl1_down_BYPASSED_AT_1_lane0))}}}));
  assign DispatchPlugin_logic_candidates_0_rsHazards[0] = (|{DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard,DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard});
  assign DispatchPlugin_logic_reservationChecker_0_onLl_0_hit = 1'b0;
  assign DispatchPlugin_logic_candidates_0_reservationHazards[0] = DispatchPlugin_logic_reservationChecker_0_onLl_0_hit;
  assign DispatchPlugin_logic_flushChecker_0_oldersHazard = 1'b0;
  assign DispatchPlugin_logic_candidates_0_flushHazards = (1'b0 || (DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_DONT_FLUSH_FROM_LANES && DispatchPlugin_logic_flushChecker_0_oldersHazard));
  assign DispatchPlugin_logic_fenceChecker_olderInflights = (|execute_lane0_api_hartsInflight[0]);
  assign DispatchPlugin_logic_candidates_0_fenceOlderHazards = (DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_FENCE_OLDER && (DispatchPlugin_logic_fenceChecker_olderInflights[0] || 1'b0));
  always @(*) begin
    decode_ctrls_1_down_ready = 1'b1;
    if(when_DispatchPlugin_l375) begin
      decode_ctrls_1_down_ready = 1'b0;
    end
  end

  assign DispatchPlugin_logic_feeds_0_sending = DispatchPlugin_logic_candidates_0_fire;
  assign DispatchPlugin_logic_candidates_0_cancel = decode_ctrls_1_lane0_upIsCancel;
  assign DispatchPlugin_logic_candidates_0_ctx_valid = ((decode_ctrls_1_up_isValid && decode_ctrls_1_up_LANE_SEL_0) && (! DispatchPlugin_logic_feeds_0_sent));
  always @(*) begin
    DispatchPlugin_logic_candidates_0_ctx_laneLayerHits = decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0;
    if(decode_ctrls_1_down_TRAP_0) begin
      DispatchPlugin_logic_candidates_0_ctx_laneLayerHits = 1'b1;
    end
  end

  assign DispatchPlugin_logic_candidates_0_ctx_uop = decode_ctrls_1_down_Decode_UOP_0;
  assign DispatchPlugin_logic_candidates_0_ctx_uopIsCompressed = (! (&decode_ctrls_1_down_Decode_INSTRUCTION_RAW_0[1 : 0]));
  assign DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_FENCE_OLDER = decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_MAY_FLUSH = decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_DONT_FLUSH = decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_DONT_FLUSH_FROM_LANES = decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_Decode_INSTRUCTION_SLICE_COUNT = decode_ctrls_1_down_Decode_INSTRUCTION_SLICE_COUNT_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_PC = decode_ctrls_1_down_PC_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_TRAP = decode_ctrls_1_down_TRAP_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_Decode_UOP_ID = decode_ctrls_1_down_Decode_UOP_ID_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_RS1_ENABLE = decode_ctrls_1_down_RS1_ENABLE_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_RS1_PHYS = decode_ctrls_1_down_RS1_PHYS_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_RS2_ENABLE = decode_ctrls_1_down_RS2_ENABLE_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS = decode_ctrls_1_down_RS2_PHYS_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_RD_ENABLE = decode_ctrls_1_down_RD_ENABLE_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_RD_PHYS = decode_ctrls_1_down_RD_PHYS_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0 = decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0;
  assign DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0 = decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0;
  assign when_DispatchPlugin_l375 = ((decode_ctrls_1_up_LANE_SEL_0 && (! DispatchPlugin_logic_feeds_0_sent)) && (! DispatchPlugin_logic_candidates_0_fire));
  assign DispatchPlugin_logic_scheduler_eusFree_0 = 1'b1;
  assign DispatchPlugin_logic_scheduler_hartFree_0 = 1'b1;
  assign DispatchPlugin_logic_scheduler_arbiters_0_candHazard = 1'b0;
  assign DispatchPlugin_logic_scheduler_arbiters_0_layersHits = (((DispatchPlugin_logic_candidates_0_ctx_laneLayerHits & (~ DispatchPlugin_logic_candidates_0_rsHazards)) & (~ DispatchPlugin_logic_candidates_0_reservationHazards)) & DispatchPlugin_logic_scheduler_eusFree_0[0]);
  assign DispatchPlugin_logic_scheduler_arbiters_0_layersHits_bools_0 = DispatchPlugin_logic_scheduler_arbiters_0_layersHits[0];
  assign _zz_DispatchPlugin_logic_scheduler_arbiters_0_layerOh[0] = (DispatchPlugin_logic_scheduler_arbiters_0_layersHits_bools_0 && (! 1'b0));
  assign DispatchPlugin_logic_scheduler_arbiters_0_layerOh = _zz_DispatchPlugin_logic_scheduler_arbiters_0_layerOh;
  assign DispatchPlugin_logic_scheduler_arbiters_0_eusOh = (|DispatchPlugin_logic_scheduler_arbiters_0_layerOh[0]);
  assign DispatchPlugin_logic_scheduler_arbiters_0_doIt = (((((DispatchPlugin_logic_candidates_0_ctx_valid && (! DispatchPlugin_logic_candidates_0_flushHazards)) && (! DispatchPlugin_logic_candidates_0_fenceOlderHazards)) && (|DispatchPlugin_logic_scheduler_arbiters_0_layerOh)) && DispatchPlugin_logic_scheduler_hartFree_0[0]) && (! DispatchPlugin_logic_scheduler_arbiters_0_candHazard));
  assign DispatchPlugin_logic_scheduler_eusFree_1 = (DispatchPlugin_logic_scheduler_eusFree_0 & ((! DispatchPlugin_logic_scheduler_arbiters_0_doIt) ? 1'b1 : (~ DispatchPlugin_logic_scheduler_arbiters_0_eusOh)));
  assign DispatchPlugin_logic_scheduler_hartFree_1 = (DispatchPlugin_logic_scheduler_hartFree_0 & (((! DispatchPlugin_logic_candidates_0_ctx_valid) || DispatchPlugin_logic_scheduler_arbiters_0_doIt) ? 1'b1 : (~ 1'b1)));
  assign DispatchPlugin_logic_candidates_0_fire = ((DispatchPlugin_logic_scheduler_arbiters_0_doIt && (! execute_freeze_valid)) && (! DispatchPlugin_api_haltDispatch));
  assign DispatchPlugin_logic_inserter_0_oh = (DispatchPlugin_logic_scheduler_arbiters_0_doIt && DispatchPlugin_logic_scheduler_arbiters_0_eusOh[0]);
  assign DispatchPlugin_logic_inserter_0_trap = DispatchPlugin_logic_candidates_0_ctx_hm_TRAP;
  assign execute_ctrl0_up_LANE_SEL_lane0 = (((|DispatchPlugin_logic_inserter_0_oh) && (! DispatchPlugin_logic_candidates_0_cancel)) && (! DispatchPlugin_api_haltDispatch));
  assign execute_ctrl0_up_Decode_UOP_lane0 = DispatchPlugin_logic_candidates_0_ctx_uop;
  assign execute_ctrl0_up_Decode_UOP_IS_COMPRESSED_lane0 = DispatchPlugin_logic_candidates_0_ctx_uopIsCompressed;
  assign execute_ctrl0_up_DispatchPlugin_FENCE_OLDER_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_FENCE_OLDER;
  always @(*) begin
    execute_ctrl0_up_DispatchPlugin_MAY_FLUSH_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_MAY_FLUSH;
    if(when_DispatchPlugin_l447) begin
      execute_ctrl0_up_DispatchPlugin_MAY_FLUSH_lane0 = 1'b0;
    end
  end

  assign execute_ctrl0_up_DispatchPlugin_DONT_FLUSH_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_DONT_FLUSH;
  assign execute_ctrl0_up_DispatchPlugin_DONT_FLUSH_FROM_LANES_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_DONT_FLUSH_FROM_LANES;
  assign execute_ctrl0_up_Decode_INSTRUCTION_SLICE_COUNT_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_Decode_INSTRUCTION_SLICE_COUNT;
  assign execute_ctrl0_up_PC_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_PC;
  assign execute_ctrl0_up_TRAP_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_TRAP;
  assign execute_ctrl0_up_Decode_UOP_ID_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_Decode_UOP_ID;
  assign execute_ctrl0_up_RS1_ENABLE_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_RS1_ENABLE;
  assign execute_ctrl0_up_RS1_PHYS_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_RS1_PHYS;
  assign execute_ctrl0_up_RS2_ENABLE_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_RS2_ENABLE;
  assign execute_ctrl0_up_RS2_PHYS_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS;
  always @(*) begin
    execute_ctrl0_up_RD_ENABLE_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_RD_ENABLE;
    if(when_DispatchPlugin_l447) begin
      execute_ctrl0_up_RD_ENABLE_lane0 = 1'b0;
    end
  end

  assign execute_ctrl0_up_RD_PHYS_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_RD_PHYS;
  assign execute_ctrl0_up_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0;
  assign execute_ctrl0_up_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_lane0 = DispatchPlugin_logic_candidates_0_ctx_hm_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0;
  assign when_DispatchPlugin_l447 = ((! execute_ctrl0_up_LANE_SEL_lane0) || DispatchPlugin_logic_inserter_0_trap);
  assign execute_ctrl0_up_COMPLETED_lane0 = DispatchPlugin_logic_inserter_0_trap;
  assign DispatchPlugin_logic_inserter_0_layerOhUnfiltred = (DispatchPlugin_logic_inserter_0_oh[0] ? DispatchPlugin_logic_scheduler_arbiters_0_layerOh : 1'b0);
  assign DispatchPlugin_logic_inserter_0_layer_0_1 = DispatchPlugin_logic_inserter_0_layerOhUnfiltred[0];
  assign DispatchPlugin_logic_events_frontendStall = (DispatchPlugin_logic_candidates_0_ctx_valid == 1'b0);
  assign DispatchPlugin_logic_events_backendStall = ((|DispatchPlugin_logic_candidates_0_ctx_valid) && (DispatchPlugin_logic_candidates_0_fire == 1'b0));
  assign when_LsuCachelessPlugin_l331 = (((execute_ctrl2_down_AguPlugin_LOAD_lane0 || execute_ctrl2_down_AguPlugin_EXECUTE_lane0) && execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io) && 1'b0);
  assign when_LsuCachelessPlugin_l337 = ((execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ACCESS_FAULT_lane0 || execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault) || execute_ctrl2_down_LsuCachelessPlugin_logic_pmpPort_ACCESS_FAULT_lane0);
  assign switch_Misc_l245_1 = {execute_ctrl2_down_AguPlugin_EXECUTE_lane0,execute_ctrl2_down_AguPlugin_STORE_lane0};
  always @(*) begin
    case(switch_Misc_l245_1)
      2'b00 : begin
        _zz_LsuCachelessPlugin_logic_trapPort_payload_arg = 2'b00;
      end
      2'b01 : begin
        _zz_LsuCachelessPlugin_logic_trapPort_payload_arg = 2'b01;
      end
      default : begin
        _zz_LsuCachelessPlugin_logic_trapPort_payload_arg = 2'b11;
      end
    endcase
  end

  assign when_LsuCachelessPlugin_l420 = ((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_AguPlugin_SEL_lane0) && LsuCachelessPlugin_logic_onFork_skip);
  assign execute_ctrl2_down_LsuCachelessPlugin_WITH_RSP_lane0 = (LsuCachelessPlugin_logic_bus_cmd_valid || LsuCachelessPlugin_logic_onFork_cmdSent);
  assign LsuCachelessPlugin_logic_cmdInflights = (|{LsuCachelessPlugin_logic_onJoin_buffers_1_inflight,LsuCachelessPlugin_logic_onJoin_buffers_0_inflight});
  assign LsuCachelessPlugin_logic_onJoin_busRspWithoutId_error = LsuCachelessPlugin_logic_bus_rsp_payload_error;
  assign LsuCachelessPlugin_logic_onJoin_busRspWithoutId_data = LsuCachelessPlugin_logic_bus_rsp_payload_data;
  assign LsuCachelessPlugin_logic_onJoin_busRspWithoutId_scMiss = LsuCachelessPlugin_logic_bus_rsp_payload_scMiss;
  assign LsuCachelessPlugin_logic_onJoin_pop = (execute_ctrl3_down_LsuCachelessPlugin_WITH_RSP_lane0 && (! execute_freeze_valid));
  always @(*) begin
    LsuCachelessPlugin_logic_onJoin_rspCounter_willIncrement = 1'b0;
    if(LsuCachelessPlugin_logic_onJoin_pop) begin
      LsuCachelessPlugin_logic_onJoin_rspCounter_willIncrement = 1'b1;
    end
  end

  assign LsuCachelessPlugin_logic_onJoin_rspCounter_willDecrement = 1'b0;
  assign LsuCachelessPlugin_logic_onJoin_rspCounter_willClear = 1'b0;
  assign LsuCachelessPlugin_logic_onJoin_rspCounter_willLoad = 1'b0;
  assign LsuCachelessPlugin_logic_onJoin_rspCounter_willOverflowIfInc = (LsuCachelessPlugin_logic_onJoin_rspCounter_value == 1'b1);
  assign LsuCachelessPlugin_logic_onJoin_rspCounter_willUnderflowIfDec = (LsuCachelessPlugin_logic_onJoin_rspCounter_value == 1'b0);
  assign LsuCachelessPlugin_logic_onJoin_rspCounter_willOverflow = (LsuCachelessPlugin_logic_onJoin_rspCounter_willOverflowIfInc && LsuCachelessPlugin_logic_onJoin_rspCounter_willIncrement);
  always @(*) begin
    LsuCachelessPlugin_logic_onJoin_rspCounter_valueNext = (LsuCachelessPlugin_logic_onJoin_rspCounter_value + LsuCachelessPlugin_logic_onJoin_rspCounter_willIncrement);
    if(LsuCachelessPlugin_logic_onJoin_rspCounter_willClear) begin
      LsuCachelessPlugin_logic_onJoin_rspCounter_valueNext = 1'b0;
    end
  end

  assign LsuCachelessPlugin_logic_onJoin_rspCounter_willUnderflow = (LsuCachelessPlugin_logic_onJoin_rspCounter_willUnderflowIfDec && LsuCachelessPlugin_logic_onJoin_rspCounter_willDecrement);
  assign LsuCachelessPlugin_logic_onJoin_readerValid = _zz_LsuCachelessPlugin_logic_onJoin_readerValid;
  assign LsuCachelessPlugin_logic_onJoin_busRspHit = (LsuCachelessPlugin_logic_bus_rsp_valid && (LsuCachelessPlugin_logic_bus_rsp_payload_id == LsuCachelessPlugin_logic_onJoin_rspCounter_value));
  assign LsuCachelessPlugin_logic_onJoin_rspValid = (LsuCachelessPlugin_logic_onJoin_readerValid || LsuCachelessPlugin_logic_onJoin_busRspHit);
  assign LsuCachelessPlugin_logic_onJoin_rspPayload_error = (LsuCachelessPlugin_logic_onJoin_readerValid ? _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_error : LsuCachelessPlugin_logic_onJoin_busRspWithoutId_error);
  assign LsuCachelessPlugin_logic_onJoin_rspPayload_data = (LsuCachelessPlugin_logic_onJoin_readerValid ? _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_data : LsuCachelessPlugin_logic_onJoin_busRspWithoutId_data);
  assign LsuCachelessPlugin_logic_onJoin_rspPayload_scMiss = (LsuCachelessPlugin_logic_onJoin_readerValid ? _zz_LsuCachelessPlugin_logic_onJoin_rspPayload_scMiss : LsuCachelessPlugin_logic_onJoin_busRspWithoutId_scMiss);
  assign LsuCachelessPlugin_logic_onJoin_rspAddress = _zz_LsuCachelessPlugin_logic_onJoin_rspAddress;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0 = LsuCachelessPlugin_logic_onJoin_rspPayload_scMiss;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0 = LsuCachelessPlugin_logic_onJoin_rspPayload_data;
  assign execute_ctrl3_up_LsuCachelessPlugin_WITH_ACCESS_lane0 = 1'b0;
  assign LsuCachelessPlugin_logic_onWb_rspSplits_0 = execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0[7 : 0];
  assign LsuCachelessPlugin_logic_onWb_rspSplits_1 = execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0[15 : 8];
  assign LsuCachelessPlugin_logic_onWb_rspSplits_2 = execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0[23 : 16];
  assign LsuCachelessPlugin_logic_onWb_rspSplits_3 = execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0[31 : 24];
  always @(*) begin
    LsuCachelessPlugin_logic_onWb_rspShifted[7 : 0] = _zz_LsuCachelessPlugin_logic_onWb_rspShifted;
    LsuCachelessPlugin_logic_onWb_rspShifted[15 : 8] = _zz_LsuCachelessPlugin_logic_onWb_rspShifted_3;
    LsuCachelessPlugin_logic_onWb_rspShifted[23 : 16] = LsuCachelessPlugin_logic_onWb_rspSplits_2;
    LsuCachelessPlugin_logic_onWb_rspShifted[31 : 24] = LsuCachelessPlugin_logic_onWb_rspSplits_3;
  end

  assign LsuCachelessPlugin_logic_iwb_valid = (execute_ctrl4_down_AguPlugin_SEL_lane0 && (! execute_ctrl4_down_AguPlugin_FLOAT_lane0));
  always @(*) begin
    LsuCachelessPlugin_logic_iwb_payload = LsuCachelessPlugin_logic_onWb_rspShifted;
    if(when_LsuCachelessPlugin_l531) begin
      LsuCachelessPlugin_logic_iwb_payload[0] = execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0;
      LsuCachelessPlugin_logic_iwb_payload[7 : 1] = 7'h0;
    end
  end

  assign when_LsuCachelessPlugin_l531 = (execute_ctrl4_down_AguPlugin_ATOMIC_lane0 && (! (execute_ctrl4_down_AguPlugin_LOAD_lane0 || execute_ctrl4_down_AguPlugin_EXECUTE_lane0)));
  assign lane0_integer_WriteBackPlugin_logic_stages_0_hits = {lane0_IntFormatPlugin_logic_stages_0_wb_valid,early0_BranchPlugin_logic_wb_valid};
  assign lane0_integer_WriteBackPlugin_logic_stages_0_muxed = ((lane0_integer_WriteBackPlugin_logic_stages_0_hits[0] ? early0_BranchPlugin_logic_wb_payload : 32'h0) | (lane0_integer_WriteBackPlugin_logic_stages_0_hits[1] ? lane0_IntFormatPlugin_logic_stages_0_wb_payload : 32'h0));
  assign execute_ctrl2_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass = lane0_integer_WriteBackPlugin_logic_stages_0_muxed;
  assign lane0_integer_WriteBackPlugin_logic_stages_0_write_valid = (((((execute_ctrl2_down_LANE_SEL_lane0 && execute_ctrl2_down_isReady) && (! execute_lane0_ctrls_2_downIsCancel)) && (|lane0_integer_WriteBackPlugin_logic_stages_0_hits)) && execute_ctrl2_up_RD_ENABLE_lane0) && execute_ctrl2_down_COMMIT_lane0);
  assign lane0_integer_WriteBackPlugin_logic_stages_0_write_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign lane0_integer_WriteBackPlugin_logic_stages_0_write_payload_data = lane0_integer_WriteBackPlugin_logic_stages_0_muxed;
  assign lane0_integer_WriteBackPlugin_logic_stages_1_hits = lane0_IntFormatPlugin_logic_stages_2_wb_valid;
  assign lane0_integer_WriteBackPlugin_logic_stages_1_muxed = (lane0_integer_WriteBackPlugin_logic_stages_1_hits[0] ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);
  assign lane0_integer_WriteBackPlugin_logic_stages_1_merged = (execute_ctrl3_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 | lane0_integer_WriteBackPlugin_logic_stages_1_muxed);
  assign execute_ctrl3_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass = lane0_integer_WriteBackPlugin_logic_stages_1_merged;
  assign lane0_integer_WriteBackPlugin_logic_stages_1_write_valid = (((((execute_ctrl3_down_LANE_SEL_lane0 && execute_ctrl3_down_isReady) && (! execute_lane0_ctrls_3_downIsCancel)) && (|lane0_integer_WriteBackPlugin_logic_stages_1_hits)) && execute_ctrl3_up_RD_ENABLE_lane0) && execute_ctrl3_down_COMMIT_lane0);
  assign lane0_integer_WriteBackPlugin_logic_stages_1_write_payload_uopId = execute_ctrl3_down_Decode_UOP_ID_lane0;
  assign lane0_integer_WriteBackPlugin_logic_stages_1_write_payload_data = lane0_integer_WriteBackPlugin_logic_stages_1_muxed;
  assign lane0_integer_WriteBackPlugin_logic_stages_2_hits = lane0_IntFormatPlugin_logic_stages_1_wb_valid;
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (lane0_integer_WriteBackPlugin_logic_stages_2_hits[0] ? lane0_IntFormatPlugin_logic_stages_1_wb_payload : 32'h0);
  assign lane0_integer_WriteBackPlugin_logic_stages_2_merged = (execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 | lane0_integer_WriteBackPlugin_logic_stages_2_muxed);
  assign execute_ctrl4_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass = lane0_integer_WriteBackPlugin_logic_stages_2_merged;
  assign lane0_integer_WriteBackPlugin_logic_stages_2_write_valid = (((((execute_ctrl4_down_LANE_SEL_lane0 && execute_ctrl4_down_isReady) && (! execute_lane0_ctrls_4_downIsCancel)) && (|lane0_integer_WriteBackPlugin_logic_stages_2_hits)) && execute_ctrl4_up_RD_ENABLE_lane0) && execute_ctrl4_down_COMMIT_lane0);
  assign lane0_integer_WriteBackPlugin_logic_stages_2_write_payload_uopId = execute_ctrl4_down_Decode_UOP_ID_lane0;
  assign lane0_integer_WriteBackPlugin_logic_stages_2_write_payload_data = lane0_integer_WriteBackPlugin_logic_stages_2_muxed;
  assign lane0_integer_WriteBackPlugin_logic_write_port_valid = (((((execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_isReady) && (! execute_lane0_ctrls_4_upIsCancel)) && execute_ctrl4_up_RD_ENABLE_lane0) && execute_ctrl4_down_lane0_integer_WriteBackPlugin_SEL_lane0) && execute_ctrl4_down_COMMIT_lane0);
  assign lane0_integer_WriteBackPlugin_logic_write_port_address = execute_ctrl4_down_RD_PHYS_lane0[4 : 0];
  assign lane0_integer_WriteBackPlugin_logic_write_port_data = execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  assign lane0_integer_WriteBackPlugin_logic_write_port_uopId = execute_ctrl4_down_Decode_UOP_ID_lane0;
  assign decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0 = _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0[0];
  assign decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0 = _zz_decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h0) == 32'h0);
  assign decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0 = _zz_decode_ctrls_1_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_0[0];
  assign decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0 = _zz_decode_ctrls_1_down_DispatchPlugin_MAY_FLUSH_0_1[0];
  assign decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0 = _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_0[0];
  assign _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00002050) == 32'h00002050);
  assign _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_1 = ((decode_ctrls_1_down_Decode_INSTRUCTION_0 & 32'h00001050) == 32'h00001050);
  assign decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0 = _zz_decode_ctrls_1_down_DispatchPlugin_DONT_FLUSH_FROM_LANES_0_2[0];
  assign decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0 = _zz_decode_ctrls_1_down_DispatchPlugin_FENCE_OLDER_0[0];
  assign decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0 = _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_0_ENABLES_0_0[0];
  assign decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0 = _zz_decode_ctrls_1_down_DispatchPlugin_logic_hcs_0_onRs_1_ENABLES_0_0_1[0];
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid = (PerformanceCounterPlugin_logic_interrupt_ip && PerformanceCounterPlugin_logic_interrupt_ie);
  assign PerformanceCounterPlugin_logic_events_sums_0 = _zz_PerformanceCounterPlugin_logic_events_sums_0_1;
  assign PerformanceCounterPlugin_logic_events_sums_1 = _zz_PerformanceCounterPlugin_logic_events_sums_1_1;
  assign PerformanceCounterPlugin_logic_events_sums_2 = _zz_PerformanceCounterPlugin_logic_events_sums_2_1;
  assign PerformanceCounterPlugin_logic_events_sums_3 = _zz_PerformanceCounterPlugin_logic_events_sums_3_1;
  assign PerformanceCounterPlugin_logic_events_sums_4 = _zz_PerformanceCounterPlugin_logic_events_sums_4_1;
  assign PerformanceCounterPlugin_logic_events_sums_5 = _zz_PerformanceCounterPlugin_logic_events_sums_5_1;
  assign PerformanceCounterPlugin_logic_fsm_wantExit = 1'b0;
  always @(*) begin
    PerformanceCounterPlugin_logic_fsm_wantStart = 1'b0;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
      end
      default : begin
        PerformanceCounterPlugin_logic_fsm_wantStart = 1'b1;
      end
    endcase
  end

  assign PerformanceCounterPlugin_logic_fsm_wantKill = 1'b0;
  always @(*) begin
    PerformanceCounterPlugin_logic_fsm_csrReadCmd_ready = 1'b0;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
        if(!PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid) begin
          if(!PerformanceCounterPlugin_logic_fsm_flusherCmd_valid) begin
            if(PerformanceCounterPlugin_logic_fsm_csrReadCmd_valid) begin
              PerformanceCounterPlugin_logic_fsm_csrReadCmd_ready = 1'b1;
            end
          end
        end
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    PerformanceCounterPlugin_logic_fsm_flusherCmd_ready = 1'b0;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
        if(!PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid) begin
          if(PerformanceCounterPlugin_logic_fsm_flusherCmd_valid) begin
            PerformanceCounterPlugin_logic_fsm_flusherCmd_ready = 1'b1;
          end
        end
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    PerformanceCounterPlugin_logic_fsm_csrWriteCmd_ready = 1'b0;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
        PerformanceCounterPlugin_logic_fsm_csrWriteCmd_ready = 1'b1;
      end
      default : begin
      end
    endcase
  end

  assign _zz_PerformanceCounterPlugin_logic_fsm_cmd_address = PerformanceCounterPlugin_logic_fsm_cmd_oh[0];
  assign _zz_PerformanceCounterPlugin_logic_fsm_cmd_address_1 = PerformanceCounterPlugin_logic_fsm_cmd_oh[1];
  assign PerformanceCounterPlugin_logic_fsm_cmd_address = ((_zz_PerformanceCounterPlugin_logic_fsm_cmd_address ? 2'b01 : 2'b00) | (_zz_PerformanceCounterPlugin_logic_fsm_cmd_address_1 ? 2'b00 : 2'b00));
  always @(*) begin
    PerformanceCounterPlugin_logic_readPort_valid = 1'b0;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
        PerformanceCounterPlugin_logic_readPort_valid = 1'b1;
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
        PerformanceCounterPlugin_logic_readPort_valid = 1'b1;
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
      end
      default : begin
      end
    endcase
  end

  assign PerformanceCounterPlugin_logic_readPort_address = {PerformanceCounterPlugin_logic_fsm_cmd_address,PerformanceCounterPlugin_logic_fsm_carry};
  always @(*) begin
    PerformanceCounterPlugin_logic_writePort_valid = 1'b0;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
        PerformanceCounterPlugin_logic_writePort_valid = 1'b1;
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
        PerformanceCounterPlugin_logic_writePort_valid = 1'b1;
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
      end
      default : begin
      end
    endcase
  end

  assign PerformanceCounterPlugin_logic_writePort_address = PerformanceCounterPlugin_logic_readPort_address;
  always @(*) begin
    PerformanceCounterPlugin_logic_writePort_data = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
        PerformanceCounterPlugin_logic_writePort_data = _zz_PerformanceCounterPlugin_logic_writePort_data[31:0];
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
        PerformanceCounterPlugin_logic_writePort_data = _zz_PerformanceCounterPlugin_logic_writePort_data_1[31:0];
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
      end
      default : begin
      end
    endcase
  end

  assign PerformanceCounterPlugin_logic_fsm_calc_a = (PerformanceCounterPlugin_logic_fsm_carry ? PerformanceCounterPlugin_logic_fsm_ramReaded : {PerformanceCounterPlugin_logic_fsm_ramReaded[31 : 7],_zz_PerformanceCounterPlugin_logic_fsm_calc_a});
  assign PerformanceCounterPlugin_logic_fsm_calc_b = (PerformanceCounterPlugin_logic_fsm_carry ? 8'h01 : _zz_PerformanceCounterPlugin_logic_fsm_calc_b);
  assign PerformanceCounterPlugin_logic_fsm_calc_sum = ({1'b0,PerformanceCounterPlugin_logic_fsm_calc_a} + _zz_PerformanceCounterPlugin_logic_fsm_calc_sum);
  assign PerformanceCounterPlugin_logic_fsm_idleCsrAddress = (PerformanceCounterPlugin_logic_fsm_csrReadCmd_valid ? PerformanceCounterPlugin_logic_fsm_csrReadCmd_payload_address : PerformanceCounterPlugin_logic_fsm_csrWriteCmd_payload_address);
  always @(*) begin
    PerformanceCounterPlugin_logic_fsm_holdCsrWrite = 1'b1;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
        PerformanceCounterPlugin_logic_fsm_holdCsrWrite = 1'b0;
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
        PerformanceCounterPlugin_logic_fsm_holdCsrWrite = 1'b0;
      end
      default : begin
      end
    endcase
  end

  assign PerformanceCounterPlugin_logic_flusher_hits = {PerformanceCounterPlugin_logic_counters_instret_needFlush,PerformanceCounterPlugin_logic_counters_cycle_needFlush};
  assign PerformanceCounterPlugin_logic_flusher_hit = (|PerformanceCounterPlugin_logic_flusher_hits);
  assign PerformanceCounterPlugin_logic_flusher_hits_ohFirst_input = PerformanceCounterPlugin_logic_flusher_hits;
  assign PerformanceCounterPlugin_logic_flusher_hits_ohFirst_masked = (PerformanceCounterPlugin_logic_flusher_hits_ohFirst_input & (~ _zz_PerformanceCounterPlugin_logic_flusher_hits_ohFirst_masked));
  assign PerformanceCounterPlugin_logic_flusher_oh = PerformanceCounterPlugin_logic_flusher_hits_ohFirst_masked;
  assign PerformanceCounterPlugin_logic_fsm_flusherCmd_valid = PerformanceCounterPlugin_logic_flusher_hit;
  assign PerformanceCounterPlugin_logic_fsm_flusherCmd_payload_oh = PerformanceCounterPlugin_logic_flusher_oh;
  assign PerformanceCounterPlugin_logic_csrDecode_addr = CsrAccessPlugin_bus_decode_address[1 : 0];
  always @(*) begin
    PerformanceCounterPlugin_logic_csrDecode_mok = 1'bx;
    case(PerformanceCounterPlugin_logic_csrDecode_addr)
      2'b00 : begin
        PerformanceCounterPlugin_logic_csrDecode_mok = PerformanceCounterPlugin_logic_counters_cycle_mcounteren;
      end
      2'b10 : begin
        PerformanceCounterPlugin_logic_csrDecode_mok = PerformanceCounterPlugin_logic_counters_instret_mcounteren;
      end
      default : begin
      end
    endcase
  end

  assign PerformanceCounterPlugin_logic_csrDecode_sok = 1'b1;
  assign PerformanceCounterPlugin_logic_csrDecode_vok = 1'b1;
  assign PerformanceCounterPlugin_logic_csrDecode_privilege = PrivilegedPlugin_logic_harts_0_privilege;
  assign PerformanceCounterPlugin_logic_csrDecode_privOk = (&((PerformanceCounterPlugin_logic_csrDecode_privilege ^ 3'b100) | {{PerformanceCounterPlugin_logic_csrDecode_vok,PerformanceCounterPlugin_logic_csrDecode_mok},PerformanceCounterPlugin_logic_csrDecode_sok}));
  assign PerformanceCounterPlugin_logic_csrDecode_hyperOK = (PerformanceCounterPlugin_logic_csrDecode_privilege[2] && (&(_zz_PerformanceCounterPlugin_logic_csrDecode_hyperOK | {PerformanceCounterPlugin_logic_csrDecode_mok,(PerformanceCounterPlugin_logic_csrDecode_vok || PerformanceCounterPlugin_logic_csrDecode_sok)})));
  assign PerformanceCounterPlugin_logic_fsm_csrReadCmd_fire = (PerformanceCounterPlugin_logic_fsm_csrReadCmd_valid && PerformanceCounterPlugin_logic_fsm_csrReadCmd_ready);
  assign PerformanceCounterPlugin_logic_csrRead_requested = (CsrAccessPlugin_bus_read_valid && REG_CSR_PerformanceCounterPlugin_logic_csrFilter);
  assign PerformanceCounterPlugin_logic_fsm_csrReadCmd_valid = (PerformanceCounterPlugin_logic_csrRead_requested && (! PerformanceCounterPlugin_logic_csrRead_fired));
  assign PerformanceCounterPlugin_logic_fsm_csrReadCmd_payload_address = CsrAccessPlugin_bus_read_address[1 : 0];
  assign when_PerformanceCounterPlugin_l363 = ((! PerformanceCounterPlugin_logic_csrRead_fired) || (! PerformanceCounterPlugin_logic_fsm_done));
  assign PerformanceCounterPlugin_logic_fsm_csrWriteCmd_fire = (PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid && PerformanceCounterPlugin_logic_fsm_csrWriteCmd_ready);
  always @(*) begin
    PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid = 1'b0;
    if(when_CsrAccessPlugin_l372_2) begin
      if(when_PerformanceCounterPlugin_l378) begin
        PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid = 1'b1;
      end
    end
  end

  assign PerformanceCounterPlugin_logic_fsm_csrWriteCmd_payload_address = CsrAccessPlugin_bus_write_address[1 : 0];
  assign LsuCachelessPlugin_pmaBuilder_addressBits = LsuCachelessPlugin_logic_onPma_port_cmd_address;
  assign LsuCachelessPlugin_pmaBuilder_argsBits = {LsuCachelessPlugin_logic_onPma_port_cmd_size,LsuCachelessPlugin_logic_onPma_port_cmd_op};
  assign _zz_LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit = ((LsuCachelessPlugin_pmaBuilder_addressBits & 32'h0) == 32'h0);
  assign LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit = _zz_LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit_1[0];
  assign LsuCachelessPlugin_pmaBuilder_onTransfers_0_argsHit = (|((LsuCachelessPlugin_pmaBuilder_argsBits & 4'b0000) == 4'b0000));
  assign LsuCachelessPlugin_pmaBuilder_onTransfers_0_hit = (LsuCachelessPlugin_pmaBuilder_onTransfers_0_argsHit && LsuCachelessPlugin_pmaBuilder_onTransfers_0_addressHit);
  assign LsuCachelessPlugin_pmaBuilder_loadExecute_needCheck = (LsuCachelessPlugin_logic_onPma_port_cmd_op == 2'b10);
  assign LsuCachelessPlugin_pmaBuilder_loadExecute_fault = ((! _zz_LsuCachelessPlugin_pmaBuilder_loadExecute_fault[0]) && LsuCachelessPlugin_pmaBuilder_loadExecute_needCheck);
  assign _zz_LsuCachelessPlugin_logic_onPma_port_rsp_fault = ((LsuCachelessPlugin_pmaBuilder_addressBits & 32'h80000000) == 32'h80000000);
  assign LsuCachelessPlugin_logic_onPma_port_rsp_fault = ((! ((|{_zz_LsuCachelessPlugin_logic_onPma_port_rsp_fault,((LsuCachelessPlugin_pmaBuilder_addressBits & 32'hf0000000) == 32'h10000000)}) && (|LsuCachelessPlugin_pmaBuilder_onTransfers_0_hit))) || LsuCachelessPlugin_pmaBuilder_loadExecute_fault);
  assign LsuCachelessPlugin_logic_onPma_port_rsp_io = (! _zz_LsuCachelessPlugin_logic_onPma_port_rsp_io[0]);
  assign _zz_CsrRamPlugin_csrMapper_ramAddress = CsrAccessPlugin_bus_decode_address;
  assign CsrRamPlugin_csrMapper_ramAddress = {(|((_zz_CsrRamPlugin_csrMapper_ramAddress & 12'h800) == 12'h0)),{(|{(_zz_CsrRamPlugin_csrMapper_ramAddress_1 == _zz_CsrRamPlugin_csrMapper_ramAddress_2),(_zz_CsrRamPlugin_csrMapper_ramAddress_3 == _zz_CsrRamPlugin_csrMapper_ramAddress_4)}),(|{(_zz_CsrRamPlugin_csrMapper_ramAddress_5 == _zz_CsrRamPlugin_csrMapper_ramAddress_6),(_zz_CsrRamPlugin_csrMapper_ramAddress_7 == _zz_CsrRamPlugin_csrMapper_ramAddress_8)})}};
  always @(*) begin
    CsrRamPlugin_csrMapper_withRead = 1'b0;
    if(when_CsrAccessPlugin_l281) begin
      CsrRamPlugin_csrMapper_withRead = 1'b1;
    end
  end

  assign CsrRamPlugin_csrMapper_read_valid = (CsrRamPlugin_csrMapper_withRead && (! CsrRamPlugin_api_holdRead));
  assign CsrRamPlugin_csrMapper_read_address = CsrRamPlugin_csrMapper_ramAddress;
  assign when_CsrRamPlugin_l85 = (CsrRamPlugin_csrMapper_withRead && (! CsrRamPlugin_csrMapper_read_ready));
  always @(*) begin
    CsrRamPlugin_csrMapper_doWrite = 1'b0;
    if(when_CsrAccessPlugin_l372_3) begin
      CsrRamPlugin_csrMapper_doWrite = 1'b1;
    end
  end

  assign when_CsrRamPlugin_l92 = (CsrRamPlugin_csrMapper_write_valid && CsrRamPlugin_csrMapper_write_ready);
  assign CsrRamPlugin_csrMapper_write_valid = ((CsrRamPlugin_csrMapper_doWrite && (! CsrRamPlugin_csrMapper_fired)) && (! CsrRamPlugin_api_holdWrite));
  assign CsrRamPlugin_csrMapper_write_address = CsrRamPlugin_csrMapper_ramAddress;
  assign CsrRamPlugin_csrMapper_write_data = CsrAccessPlugin_bus_write_bits;
  assign when_CsrRamPlugin_l96 = ((CsrRamPlugin_csrMapper_doWrite && (! CsrRamPlugin_csrMapper_fired)) && (! CsrRamPlugin_csrMapper_write_ready));
  assign when_CtrlLaneApi_l50_2 = (decode_ctrls_1_up_isReady || decode_ctrls_1_lane0_upIsCancel);
  assign WhiteboxerPlugin_logic_serializeds_0_fire = (decode_ctrls_1_up_LANE_SEL_0 && (! decode_ctrls_1_up_LANE_SEL_0_regNext_1));
  assign WhiteboxerPlugin_logic_serializeds_0_decodeId = decode_ctrls_1_down_Decode_DOP_ID_0;
  assign WhiteboxerPlugin_logic_serializeds_0_microOpId = decode_ctrls_1_down_Decode_UOP_ID_0;
  assign WhiteboxerPlugin_logic_serializeds_0_microOp = decode_ctrls_1_down_Decode_UOP_0;
  assign when_CtrlLaneApi_l50_3 = (execute_ctrl0_down_isReady || execute_lane0_ctrls_0_downIsCancel);
  assign WhiteboxerPlugin_logic_dispatches_0_fire = (execute_ctrl0_down_LANE_SEL_lane0 && (! execute_ctrl0_down_LANE_SEL_lane0_regNext));
  assign WhiteboxerPlugin_logic_dispatches_0_microOpId = execute_ctrl0_down_Decode_UOP_ID_lane0;
  assign when_CtrlLaneApi_l50_4 = (execute_ctrl2_down_isReady || execute_lane0_ctrls_2_downIsCancel);
  assign WhiteboxerPlugin_logic_executes_0_fire = ((execute_ctrl2_down_LANE_SEL_lane0 && (! execute_ctrl2_down_LANE_SEL_lane0_regNext)) && execute_ctrl2_down_COMMIT_lane0);
  assign WhiteboxerPlugin_logic_executes_0_microOpId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign AlignerPlugin_logic_buffer_flushIt = (|{(DecoderPlugin_logic_laneLogic_0_flushPort_valid && 1'b1),{(early0_EnvPlugin_logic_flushPort_valid && 1'b1),{(CsrAccessPlugin_logic_flushPort_valid && 1'b1),{(early0_BranchPlugin_logic_flushPort_valid && 1'b1),(LsuCachelessPlugin_logic_flushPort_valid && 1'b1)}}}});
  assign AlignerPlugin_logic_buffer_readers_0_firstFromBuffer = (|{_zz_AlignerPlugin_logic_extractors_0_redo_1,_zz_AlignerPlugin_logic_extractors_0_redo});
  assign AlignerPlugin_logic_buffer_readers_0_lastFromBuffer = ({AlignerPlugin_logic_extractors_0_usageMask[3],AlignerPlugin_logic_extractors_0_usageMask[2]} == 2'b00);
  assign _zz_AlignerPlugin_logic_extractors_0_ctx_instruction = {_zz_AlignerPlugin_logic_extractors_0_redo_3,{_zz_AlignerPlugin_logic_extractors_0_redo_2,{_zz_AlignerPlugin_logic_extractors_0_redo_1,_zz_AlignerPlugin_logic_extractors_0_redo}}};
  assign AlignerPlugin_logic_extractors_0_ctx_instruction = (((_zz_AlignerPlugin_logic_extractors_0_ctx_instruction[0] ? AlignerPlugin_logic_slicesInstructions_0 : 32'h0) | (_zz_AlignerPlugin_logic_extractors_0_ctx_instruction[1] ? AlignerPlugin_logic_slicesInstructions_1 : 32'h0)) | ((_zz_AlignerPlugin_logic_extractors_0_ctx_instruction[2] ? AlignerPlugin_logic_slicesInstructions_2 : 32'h0) | (_zz_AlignerPlugin_logic_extractors_0_ctx_instruction[3] ? AlignerPlugin_logic_slicesInstructions_3 : 32'h0)));
  always @(*) begin
    AlignerPlugin_logic_extractors_0_ctx_pc = (AlignerPlugin_logic_buffer_readers_0_firstFromBuffer ? AlignerPlugin_logic_buffer_pc : fetch_logic_ctrls_1_down_Fetch_WORD_PC);
    AlignerPlugin_logic_extractors_0_ctx_pc[1 : 1] = _zz_AlignerPlugin_logic_extractors_0_ctx_pc;
  end

  assign _zz_AlignerPlugin_logic_extractors_0_ctx_pc = (|{_zz_AlignerPlugin_logic_extractors_0_redo_3,_zz_AlignerPlugin_logic_extractors_0_redo_1});
  assign AlignerPlugin_logic_extractors_0_ctx_trap = ((AlignerPlugin_logic_buffer_readers_0_firstFromBuffer && AlignerPlugin_logic_buffer_trap) || ((! AlignerPlugin_logic_buffer_readers_0_lastFromBuffer) && fetch_logic_ctrls_1_down_TRAP));
  assign AlignerPlugin_logic_extractors_0_ctx_hm_Fetch_ID = (AlignerPlugin_logic_buffer_readers_0_firstFromBuffer ? AlignerPlugin_logic_buffer_hm_Fetch_ID : fetch_logic_ctrls_1_down_Fetch_ID);
  assign AlignerPlugin_api_downMoving = decode_ctrls_0_up_isMoving;
  always @(*) begin
    TrapPlugin_logic_harts_0_crsPorts_read_valid = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
        TrapPlugin_logic_harts_0_crsPorts_read_valid = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
        TrapPlugin_logic_harts_0_crsPorts_read_valid = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_logic_harts_0_crsPorts_read_address = 3'bxxx;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
        TrapPlugin_logic_harts_0_crsPorts_read_address = _zz_TrapPlugin_logic_harts_0_crsPorts_read_address;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
        TrapPlugin_logic_harts_0_crsPorts_read_address = _zz_TrapPlugin_logic_harts_0_crsPorts_read_address_1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  assign decode_logic_flushes_0_onLanes_0_doIt = (|{(DecoderPlugin_logic_laneLogic_0_flushPort_valid && 1'b1),{(early0_EnvPlugin_logic_flushPort_valid && 1'b1),{(CsrAccessPlugin_logic_flushPort_valid && 1'b1),{(early0_BranchPlugin_logic_flushPort_valid && 1'b1),(LsuCachelessPlugin_logic_flushPort_valid && 1'b1)}}}});
  assign decode_ctrls_0_lane0_downIsCancel = 1'b0;
  assign decode_ctrls_0_lane0_upIsCancel = decode_logic_flushes_0_onLanes_0_doIt;
  assign decode_logic_flushes_1_onLanes_0_doIt = (|{((DecoderPlugin_logic_laneLogic_0_flushPort_valid && 1'b1) && (1'b0 || (1'b1 && DecoderPlugin_logic_laneLogic_0_flushPort_payload_self))),{(early0_EnvPlugin_logic_flushPort_valid && 1'b1),{(CsrAccessPlugin_logic_flushPort_valid && 1'b1),{(early0_BranchPlugin_logic_flushPort_valid && 1'b1),(LsuCachelessPlugin_logic_flushPort_valid && 1'b1)}}}});
  assign decode_ctrls_1_lane0_downIsCancel = 1'b0;
  assign decode_ctrls_1_lane0_upIsCancel = decode_logic_flushes_1_onLanes_0_doIt;
  assign decode_logic_trapPending[0] = (|{((decode_ctrls_1_up_LANE_SEL_0 && 1'b1) && decode_ctrls_1_down_TRAP_0),((decode_ctrls_0_up_LANE_SEL_0 && 1'b1) && decode_ctrls_0_down_TRAP_0)});
  always @(*) begin
    TrapPlugin_logic_harts_0_crsPorts_write_valid = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
        TrapPlugin_logic_harts_0_crsPorts_write_valid = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
        TrapPlugin_logic_harts_0_crsPorts_write_valid = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_logic_harts_0_crsPorts_write_address = 3'bxxx;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
        TrapPlugin_logic_harts_0_crsPorts_write_address = _zz_TrapPlugin_logic_harts_0_crsPorts_write_address;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
        TrapPlugin_logic_harts_0_crsPorts_write_address = _zz_TrapPlugin_logic_harts_0_crsPorts_write_address_1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_logic_harts_0_crsPorts_write_data = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
        TrapPlugin_logic_harts_0_crsPorts_write_data = TrapPlugin_logic_harts_0_trap_pending_pc;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
        TrapPlugin_logic_harts_0_crsPorts_write_data = TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_tval;
        if(TrapPlugin_logic_harts_0_trap_fsm_triggerEbreakReg) begin
          TrapPlugin_logic_harts_0_crsPorts_write_data = TrapPlugin_logic_harts_0_trap_pending_pc;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_id = 5'h07;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_priority = 5'h03;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_privilege = 3'b011;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid = ((_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid && 1'b1) && (! 1'b0));
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_id = 5'h03;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_priority = 5'h02;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_privilege = 3'b011;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid = ((_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid && 1'b1) && (! 1'b0));
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_id = 5'h0b;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_priority = 5'h01;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_privilege = 3'b011;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid = ((_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid && 1'b1) && (! 1'b0));
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_id = 5'h0d;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_priority = 5'h0b;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_privilege = 3'b011;
  assign TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid = ((_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid && 1'b1) && (! 1'b0));
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id = ((! TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid) || (TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid && (($signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_privilege) < $signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_privilege)) || (($signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_privilege) == $signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_privilege)) && (TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_priority < TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_priority)))));
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority = (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_priority : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_priority);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege = (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_privilege : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_privilege);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid = (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_1 = ((! TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid) || (TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid && (($signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_privilege) < $signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_privilege)) || (($signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_privilege) == $signed(TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_privilege)) && (TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_priority < TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_priority)))));
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority_1 = (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_1 ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_priority : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_priority);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege_1 = (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_1 ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_privilege : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_privilege);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid_1 = (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_1 ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid);
  assign _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_2 = ((! _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid_1) || (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid && (($signed(_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege_1) < $signed(_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege)) || (($signed(_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege) == $signed(_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege_1)) && (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority < _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority_1)))));
  assign TrapPlugin_logic_harts_0_interrupt_xtopi_0_int = (TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id : 5'h0);
  assign TrapPlugin_logic_harts_0_interrupt_result_id = TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id;
  assign TrapPlugin_logic_harts_0_interrupt_result_priority = TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority;
  assign TrapPlugin_logic_harts_0_interrupt_result_privilege = TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege;
  assign TrapPlugin_logic_harts_0_interrupt_result_valid = (TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid && (PrivilegedPlugin_logic_harts_0_m_status_mie || (! PrivilegedPlugin_logic_harts_0_withMachinePrivilege)));
  assign TrapPlugin_logic_harts_0_interrupt_valid = TrapPlugin_logic_harts_0_interrupt_result_valid;
  assign TrapPlugin_logic_harts_0_interrupt_code = TrapPlugin_logic_harts_0_interrupt_result_id;
  assign TrapPlugin_logic_harts_0_interrupt_targetPrivilege = TrapPlugin_logic_harts_0_interrupt_result_privilege;
  assign TrapPlugin_logic_harts_0_interrupt_pendingInterrupt = (TrapPlugin_logic_harts_0_interrupt_validBuffer && PrivilegedPlugin_api_harts_0_allowInterrupts);
  assign when_TrapPlugin_l288 = (|{_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_valid,{_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_valid,{_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_valid,_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_valid}}});
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid = (early0_EnvPlugin_logic_trapPort_valid && 1'b1);
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_valid = (FetchCachelessPlugin_logic_trapPort_valid && 1'b1);
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_1 = (LsuCachelessPlugin_logic_trapPort_valid && 1'b1);
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_2 = (CsrAccessPlugin_logic_trapPort_valid && 1'b1);
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_valid = (DecoderPlugin_logic_laneLogic_0_trapPort_valid && 1'b1);
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception = {(_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_2 && (&{(! (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_1 && _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception)),(! (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid && _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1))})),{(_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_1 && (&{(! _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_2),(! _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_3)})),(_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid && (&{(! _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_4),(! _zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_5)}))}};
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid = (|{_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_2,{_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_1,_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid}});
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1 = (((_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception[0] ? {early0_EnvPlugin_logic_trapPort_payload_arg,{early0_EnvPlugin_logic_trapPort_payload_code,{early0_EnvPlugin_logic_trapPort_payload_tval2,_zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_1}}} : 73'h0) | (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception[1] ? {LsuCachelessPlugin_logic_trapPort_payload_arg,{LsuCachelessPlugin_logic_trapPort_payload_code,{LsuCachelessPlugin_logic_trapPort_payload_tval2,_zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_2}}} : 73'h0)) | (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception[2] ? {CsrAccessPlugin_logic_trapPort_payload_arg,{CsrAccessPlugin_logic_trapPort_payload_code,{CsrAccessPlugin_logic_trapPort_payload_tval2,{CsrAccessPlugin_logic_trapPort_payload_tval,_zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1_3}}}} : 73'h0));
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1[0];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_tval = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1[32 : 1];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_tval2 = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1[64 : 33];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_code = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1[69 : 65];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_arg = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_exception_1[72 : 70];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_valid = (|_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_valid);
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_exception = DecoderPlugin_logic_laneLogic_0_trapPort_payload_exception;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_tval = DecoderPlugin_logic_laneLogic_0_trapPort_payload_tval;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_tval2 = DecoderPlugin_logic_laneLogic_0_trapPort_payload_tval2;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_code = DecoderPlugin_logic_laneLogic_0_trapPort_payload_code;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_arg = DecoderPlugin_logic_laneLogic_0_trapPort_payload_arg;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_valid = (|_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_valid);
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_exception = FetchCachelessPlugin_logic_trapPort_payload_exception;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_tval = FetchCachelessPlugin_logic_trapPort_payload_tval;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_tval2 = FetchCachelessPlugin_logic_trapPort_payload_tval2;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_code = FetchCachelessPlugin_logic_trapPort_payload_code;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_arg = FetchCachelessPlugin_logic_trapPort_payload_arg;
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh = {TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_valid,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_valid,TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid}};
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_1 = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh[0];
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_2 = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh[1];
  always @(*) begin
    _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_3[0] = (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_1 && (! 1'b0));
    _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_3[1] = (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_2 && (! _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_1));
    _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_3[2] = (_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh[2] && (! (|{_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_2,_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_1})));
  end

  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_oh = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_oh_3;
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_down_valid = (|{_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_valid,{_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_2,{_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid_1,{_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_valid,_zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_valid}}}});
  assign _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception = (((TrapPlugin_logic_harts_0_trap_pending_arbiter_oh[0] ? {TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_arg,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_code,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_0_payload_tval2,_zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception}}} : 73'h0) | (TrapPlugin_logic_harts_0_trap_pending_arbiter_oh[1] ? {TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_arg,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_code,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_1_payload_tval2,_zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception_1}}} : 73'h0)) | (TrapPlugin_logic_harts_0_trap_pending_arbiter_oh[2] ? {TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_arg,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_code,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_tval2,{TrapPlugin_logic_harts_0_trap_pending_arbiter_ports_2_payload_tval,_zz__zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception_2}}}} : 73'h0));
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception[0];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_tval = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception[32 : 1];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_tval2 = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception[64 : 33];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_code = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception[69 : 65];
  assign TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_arg = _zz_TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception[72 : 70];
  assign TrapPlugin_logic_harts_0_trap_pending_xret_sourcePrivilege = TrapPlugin_logic_harts_0_trap_pending_state_arg[2 : 0];
  assign TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilegeMapping_0 = {((PrivilegedPlugin_logic_harts_0_m_status_mpp == 2'b11) ? 1'b0 : PrivilegedPlugin_logic_harts_0_m_status_mpv),PrivilegedPlugin_logic_harts_0_m_status_mpp};
  always @(*) begin
    TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilege = TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilegeMapping_0;
    case(TrapPlugin_logic_harts_0_trap_pending_xret_sourcePrivilege)
      3'b011 : begin
        TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilege = TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilegeMapping_0;
      end
      default : begin
      end
    endcase
  end

  assign TrapPlugin_logic_harts_0_trap_exception_exceptionTargetPrivilegeUncapped = 3'b011;
  assign TrapPlugin_logic_harts_0_trap_exception_code = TrapPlugin_logic_harts_0_trap_pending_state_code;
  assign _zz_TrapPlugin_logic_harts_0_trap_exception_targetPrivilege = (PrivilegedPlugin_logic_harts_0_privilege | 3'b001);
  assign TrapPlugin_logic_harts_0_trap_exception_targetPrivilege = (($signed(_zz_TrapPlugin_logic_harts_0_trap_exception_targetPrivilege) < $signed(TrapPlugin_logic_harts_0_trap_exception_exceptionTargetPrivilegeUncapped)) ? TrapPlugin_logic_harts_0_trap_exception_exceptionTargetPrivilegeUncapped : _zz_TrapPlugin_logic_harts_0_trap_exception_targetPrivilege);
  assign PrivilegedPlugin_logic_harts_0_commitMask = (((execute_ctrl5_down_LANE_SEL_lane0 && execute_ctrl5_down_isReady) && (! execute_lane0_ctrls_5_downIsCancel)) && execute_ctrl5_down_COMMIT_lane0);
  assign TrapPlugin_logic_harts_0_trap_trigger_oh = (((execute_ctrl4_down_LANE_SEL_lane0 && execute_ctrl4_down_isReady) && (! execute_lane0_ctrls_4_downIsCancel)) && execute_ctrl4_down_TRAP_lane0);
  assign TrapPlugin_logic_harts_0_trap_trigger_valid = (|TrapPlugin_logic_harts_0_trap_trigger_oh);
  always @(*) begin
    TrapPlugin_logic_harts_0_trap_whitebox_trap = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
        TrapPlugin_logic_harts_0_trap_whitebox_trap = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_logic_harts_0_trap_whitebox_interrupt = 1'bx;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
        TrapPlugin_logic_harts_0_trap_whitebox_interrupt = TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_logic_harts_0_trap_whitebox_code = 5'bxxxxx;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
        TrapPlugin_logic_harts_0_trap_whitebox_code = TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_code;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_logic_harts_0_trap_pcPort_valid = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
        if(!when_TrapPlugin_l547) begin
          case(TrapPlugin_logic_harts_0_trap_pending_state_code)
            5'h0 : begin
              TrapPlugin_logic_harts_0_trap_pcPort_valid = 1'b1;
            end
            5'h01 : begin
            end
            5'h02 : begin
            end
            5'h04 : begin
            end
            5'h05 : begin
            end
            5'h08 : begin
            end
            5'h06 : begin
            end
            5'h09 : begin
            end
            default : begin
            end
          endcase
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
        TrapPlugin_logic_harts_0_trap_pcPort_valid = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
        TrapPlugin_logic_harts_0_trap_pcPort_valid = 1'b1;
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
        TrapPlugin_logic_harts_0_trap_pcPort_valid = 1'b1;
      end
      default : begin
      end
    endcase
  end

  assign TrapPlugin_logic_harts_0_trap_pcPort_payload_fault = 1'b0;
  always @(*) begin
    TrapPlugin_logic_harts_0_trap_pcPort_payload_pc = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
        if(!when_TrapPlugin_l547) begin
          case(TrapPlugin_logic_harts_0_trap_pending_state_code)
            5'h0 : begin
              TrapPlugin_logic_harts_0_trap_pcPort_payload_pc = TrapPlugin_logic_harts_0_trap_pending_pc;
            end
            5'h01 : begin
            end
            5'h02 : begin
            end
            5'h04 : begin
            end
            5'h05 : begin
            end
            5'h08 : begin
            end
            5'h06 : begin
            end
            5'h09 : begin
            end
            default : begin
            end
          endcase
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
        TrapPlugin_logic_harts_0_trap_pcPort_payload_pc = TrapPlugin_logic_harts_0_trap_fsm_readed;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
        TrapPlugin_logic_harts_0_trap_pcPort_payload_pc = TrapPlugin_logic_harts_0_trap_fsm_readed;
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
        TrapPlugin_logic_harts_0_trap_pcPort_payload_pc = TrapPlugin_logic_harts_0_trap_fsm_jumpTarget;
      end
      default : begin
      end
    endcase
  end

  assign TrapPlugin_logic_harts_0_trap_fsm_wantExit = 1'b0;
  always @(*) begin
    TrapPlugin_logic_harts_0_trap_fsm_wantStart = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
        TrapPlugin_logic_harts_0_trap_fsm_wantStart = 1'b1;
      end
    endcase
  end

  assign TrapPlugin_logic_harts_0_trap_fsm_wantKill = 1'b0;
  assign TrapPlugin_logic_harts_0_trap_fsm_inflightTrap = (|{execute_lane0_logic_trapPending[0],{DispatchPlugin_logic_trapPendings[0],decode_logic_trapPending[0]}});
  assign TrapPlugin_logic_harts_0_trap_fsm_holdPort = (TrapPlugin_logic_harts_0_trap_fsm_inflightTrap || (! (TrapPlugin_logic_harts_0_trap_fsm_stateReg == TrapPlugin_logic_harts_0_trap_fsm_RUNNING)));
  assign TrapPlugin_api_harts_0_fsmBusy = (! (TrapPlugin_logic_harts_0_trap_fsm_stateReg == TrapPlugin_logic_harts_0_trap_fsm_RUNNING));
  always @(*) begin
    TrapPlugin_logic_harts_0_trap_fsm_wfi = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
        if(!when_TrapPlugin_l547) begin
          case(TrapPlugin_logic_harts_0_trap_pending_state_code)
            5'h0 : begin
            end
            5'h01 : begin
            end
            5'h02 : begin
            end
            5'h04 : begin
            end
            5'h05 : begin
            end
            5'h08 : begin
              TrapPlugin_logic_harts_0_trap_fsm_wfi = 1'b1;
            end
            5'h06 : begin
            end
            5'h09 : begin
            end
            default : begin
            end
          endcase
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    TrapPlugin_logic_harts_0_trap_fsm_buffer_sampleIt = 1'b0;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
        if(TrapPlugin_logic_harts_0_trap_trigger_valid) begin
          TrapPlugin_logic_harts_0_trap_fsm_buffer_sampleIt = 1'b1;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
  end

  assign TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt = ((TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h0) && TrapPlugin_logic_harts_0_trap_fsm_buffer_i_valid);
  assign TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege = (TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt ? TrapPlugin_logic_harts_0_trap_fsm_buffer_i_targetPrivilege : TrapPlugin_logic_harts_0_trap_exception_targetPrivilege);
  assign TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_tval = ((! TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt) ? TrapPlugin_logic_harts_0_trap_pending_state_tval : 32'h0);
  assign TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_code = (TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt ? TrapPlugin_logic_harts_0_trap_fsm_buffer_i_code : TrapPlugin_logic_harts_0_trap_pending_state_code);
  assign TrapPlugin_logic_harts_0_trap_fsm_resetToRunConditions_0 = (! TrapPlugin_logic_initHold);
  assign TrapPlugin_logic_harts_0_trap_fsm_jumpOffset = ((|{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h08),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h09),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h06),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h02),(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h05)}}}}) ? TrapPlugin_logic_harts_0_trap_pending_slices : 2'b00);
  assign TrapPlugin_logic_harts_0_trap_fsm_writeGVA = (|{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h17),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h15),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h14),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h0f),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h0d),{(TrapPlugin_logic_harts_0_trap_pending_state_code == 5'h0c),{(TrapPlugin_logic_harts_0_trap_pending_state_code == _zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA),{_zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_1,{_zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_2,_zz_TrapPlugin_logic_harts_0_trap_fsm_writeGVA_3}}}}}}}}});
  assign TrapPlugin_logic_harts_0_trap_fsm_triggerEbreak = 1'b0;
  assign TrapPlugin_logic_harts_0_trap_fsm_isGuestTarget = TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege[2];
  assign when_TrapPlugin_l841 = (TrapPlugin_logic_harts_0_crsPorts_read_valid && TrapPlugin_logic_harts_0_crsPorts_read_ready);
  assign TrapPlugin_logic_harts_0_trap_fsm_xretPrivilege = TrapPlugin_logic_harts_0_trap_pending_state_arg[2 : 0];
  assign PcPlugin_logic_forcedSpawn = (|{TrapPlugin_logic_harts_0_trap_pcPort_valid,early0_BranchPlugin_logic_pcPort_valid});
  assign PcPlugin_logic_harts_0_self_pc = (PcPlugin_logic_harts_0_self_state + _zz_PcPlugin_logic_harts_0_self_pc);
  assign PcPlugin_logic_harts_0_self_flow_valid = 1'b1;
  assign PcPlugin_logic_harts_0_self_flow_payload_fault = PcPlugin_logic_harts_0_self_fault;
  assign PcPlugin_logic_harts_0_self_flow_payload_pc = PcPlugin_logic_harts_0_self_pc;
  assign PcPlugin_logic_harts_0_aggregator_sortedByPriority_1_laneValid = 1'b1;
  assign PcPlugin_logic_harts_0_aggregator_sortedByPriority_0_laneValid = 1'b1;
  assign PcPlugin_logic_harts_0_aggregator_sortedByPriority_2_laneValid = 1'b1;
  assign PcPlugin_logic_harts_0_aggregator_valids_0 = ((TrapPlugin_logic_harts_0_trap_pcPort_valid && 1'b1) && PcPlugin_logic_harts_0_aggregator_sortedByPriority_0_laneValid);
  assign PcPlugin_logic_harts_0_aggregator_valids_1 = ((early0_BranchPlugin_logic_pcPort_valid && 1'b1) && PcPlugin_logic_harts_0_aggregator_sortedByPriority_1_laneValid);
  assign PcPlugin_logic_harts_0_aggregator_valids_2 = ((PcPlugin_logic_harts_0_self_flow_valid && 1'b1) && PcPlugin_logic_harts_0_aggregator_sortedByPriority_2_laneValid);
  assign _zz_PcPlugin_logic_harts_0_aggregator_oh = {PcPlugin_logic_harts_0_aggregator_valids_2,{PcPlugin_logic_harts_0_aggregator_valids_1,PcPlugin_logic_harts_0_aggregator_valids_0}};
  assign _zz_PcPlugin_logic_harts_0_aggregator_oh_1 = _zz_PcPlugin_logic_harts_0_aggregator_oh[0];
  assign _zz_PcPlugin_logic_harts_0_aggregator_oh_2 = _zz_PcPlugin_logic_harts_0_aggregator_oh[1];
  always @(*) begin
    _zz_PcPlugin_logic_harts_0_aggregator_oh_3[0] = (_zz_PcPlugin_logic_harts_0_aggregator_oh_1 && (! 1'b0));
    _zz_PcPlugin_logic_harts_0_aggregator_oh_3[1] = (_zz_PcPlugin_logic_harts_0_aggregator_oh_2 && (! _zz_PcPlugin_logic_harts_0_aggregator_oh_1));
    _zz_PcPlugin_logic_harts_0_aggregator_oh_3[2] = (_zz_PcPlugin_logic_harts_0_aggregator_oh[2] && (! (|{_zz_PcPlugin_logic_harts_0_aggregator_oh_2,_zz_PcPlugin_logic_harts_0_aggregator_oh_1})));
  end

  assign PcPlugin_logic_harts_0_aggregator_oh = _zz_PcPlugin_logic_harts_0_aggregator_oh_3;
  assign _zz_PcPlugin_logic_harts_0_aggregator_target = PcPlugin_logic_harts_0_aggregator_oh[0];
  assign _zz_PcPlugin_logic_harts_0_aggregator_target_1 = PcPlugin_logic_harts_0_aggregator_oh[1];
  assign _zz_PcPlugin_logic_harts_0_aggregator_target_2 = PcPlugin_logic_harts_0_aggregator_oh[2];
  assign PcPlugin_logic_harts_0_aggregator_target = (((_zz_PcPlugin_logic_harts_0_aggregator_target ? TrapPlugin_logic_harts_0_trap_pcPort_payload_pc : 32'h0) | (_zz_PcPlugin_logic_harts_0_aggregator_target_1 ? early0_BranchPlugin_logic_pcPort_payload_pc : 32'h0)) | (_zz_PcPlugin_logic_harts_0_aggregator_target_2 ? PcPlugin_logic_harts_0_self_flow_payload_pc : 32'h0));
  assign PcPlugin_logic_harts_0_aggregator_fault = _zz_PcPlugin_logic_harts_0_aggregator_fault[0];
  assign PcPlugin_logic_harts_0_holdComb = (|TrapPlugin_logic_harts_0_trap_fsm_holdPort);
  assign PcPlugin_logic_harts_0_output_valid = (! PcPlugin_logic_harts_0_holdReg);
  assign PcPlugin_logic_harts_0_output_payload_fault = PcPlugin_logic_harts_0_aggregator_fault;
  always @(*) begin
    PcPlugin_logic_harts_0_output_payload_pc = PcPlugin_logic_harts_0_aggregator_target;
    PcPlugin_logic_harts_0_output_payload_pc[0 : 0] = 1'b0;
  end

  assign PcPlugin_logic_harts_0_output_fire = (PcPlugin_logic_harts_0_output_valid && PcPlugin_logic_harts_0_output_ready);
  assign fetch_logic_ctrls_0_up_valid = PcPlugin_logic_harts_0_output_valid;
  assign PcPlugin_logic_harts_0_output_ready = fetch_logic_ctrls_0_up_ready;
  assign fetch_logic_ctrls_0_up_Fetch_WORD_PC = PcPlugin_logic_harts_0_output_payload_pc;
  assign fetch_logic_ctrls_0_up_Fetch_PC_FAULT = PcPlugin_logic_harts_0_output_payload_fault;
  always @(*) begin
    fetch_logic_ctrls_0_up_Fetch_ID = 10'bxxxxxxxxxx;
    fetch_logic_ctrls_0_up_Fetch_ID = PcPlugin_logic_harts_0_self_id;
  end

  assign PcPlugin_logic_holdHalter_doIt = PcPlugin_logic_harts_0_holdComb;
  assign fetch_logic_ctrls_0_haltRequest_PcPlugin_l133 = PcPlugin_logic_holdHalter_doIt;
  assign CsrAccessPlugin_logic_fsm_wantExit = 1'b0;
  always @(*) begin
    CsrAccessPlugin_logic_fsm_wantStart = 1'b0;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        CsrAccessPlugin_logic_fsm_wantStart = 1'b1;
      end
    endcase
  end

  assign CsrAccessPlugin_logic_fsm_wantKill = 1'b0;
  always @(*) begin
    CsrAccessPlugin_logic_fsm_interface_fire = 1'b0;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
        if(execute_ctrl2_down_isReady) begin
          CsrAccessPlugin_logic_fsm_interface_fire = 1'b1;
        end
      end
      default : begin
      end
    endcase
  end

  assign CsrAccessPlugin_logic_fsm_inject_csrAddress = execute_ctrl2_down_Decode_UOP_lane0[31 : 20];
  assign CsrAccessPlugin_logic_fsm_inject_csrPriv = _zz_CsrAccessPlugin_logic_fsm_inject_csrPriv[9 : 8];
  assign CsrAccessPlugin_logic_fsm_inject_immZero = (execute_ctrl2_down_Decode_UOP_lane0[19 : 15] == 5'h0);
  assign CsrAccessPlugin_logic_fsm_inject_srcZero = (execute_ctrl2_down_CsrAccessPlugin_CSR_IMM_lane0 ? CsrAccessPlugin_logic_fsm_inject_immZero : (execute_ctrl2_down_Decode_UOP_lane0[19 : 15] == 5'h0));
  assign CsrAccessPlugin_logic_fsm_inject_csrWrite = (! (execute_ctrl2_down_CsrAccessPlugin_CSR_MASK_lane0 && CsrAccessPlugin_logic_fsm_inject_srcZero));
  assign CsrAccessPlugin_logic_fsm_inject_csrRead = (! ((! execute_ctrl2_down_CsrAccessPlugin_CSR_MASK_lane0) && (! execute_ctrl2_up_RD_ENABLE_lane0)));
  assign COMB_CSR_1952 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h7a0);
  assign COMB_CSR_1953 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h7a1);
  assign COMB_CSR_1954 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h7a2);
  assign COMB_CSR_3857 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hf11);
  assign COMB_CSR_3858 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hf12);
  assign COMB_CSR_3859 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hf13);
  assign COMB_CSR_3860 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hf14);
  assign COMB_CSR_769 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h301);
  assign COMB_CSR_768 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h300);
  assign COMB_CSR_834 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h342);
  assign COMB_CSR_836 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h344);
  assign COMB_CSR_772 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h304);
  assign COMB_CSR_4016 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hfb0);
  assign COMB_CSR_794 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h31a);
  assign COMB_CSR_778 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h30a);
  assign COMB_CSR_774 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h306);
  assign COMB_CSR_3073 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc01);
  assign COMB_CSR_3201 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc81);
  assign COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_tvecFilter = (|(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h305));
  assign COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_epcFilter = (|(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h341));
  assign COMB_CSR_800 = (CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h320);
  assign COMB_CSR_UNAMED_1 = (|{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h73f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc9f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb9f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h33f),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_UNAMED_1),{_zz_COMB_CSR_UNAMED_1_1,{_zz_COMB_CSR_UNAMED_1_2,_zz_COMB_CSR_UNAMED_1_3}}}}}}});
  assign COMB_CSR_PerformanceCounterPlugin_logic_csrFilter = (|{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc82),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb82),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc80),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb80),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc02),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb02),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_PerformanceCounterPlugin_logic_csrFilter),(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_PerformanceCounterPlugin_logic_csrFilter_1)}}}}}}});
  assign COMB_CSR_CsrRamPlugin_csrMapper_selFilter = (|{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc82),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb82),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc80),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb80),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hc02),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'hb02),{(CsrAccessPlugin_logic_fsm_inject_csrAddress == _zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter),{_zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_1,{_zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_2,_zz_COMB_CSR_CsrRamPlugin_csrMapper_selFilter_3}}}}}}}}});
  assign COMB_CSR_CsrAccessPlugin_logic_trapNextOnWriteFilter = (|{(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h310),(CsrAccessPlugin_logic_fsm_inject_csrAddress == 12'h300)});
  assign CsrAccessPlugin_logic_fsm_inject_implemented = (|{COMB_CSR_CsrAccessPlugin_logic_trapNextOnWriteFilter,{COMB_CSR_CsrRamPlugin_csrMapper_selFilter,{COMB_CSR_PerformanceCounterPlugin_logic_csrFilter,{COMB_CSR_UNAMED_1,{COMB_CSR_800,{COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_epcFilter,{COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_tvecFilter,{COMB_CSR_3201,{COMB_CSR_3073,{COMB_CSR_774,{_zz_CsrAccessPlugin_logic_fsm_inject_implemented,_zz_CsrAccessPlugin_logic_fsm_inject_implemented_1}}}}}}}}}}});
  assign CsrAccessPlugin_logic_fsm_inject_condImplemented = 1'b0;
  assign CsrAccessPlugin_logic_fsm_inject_onDecodeDo = ((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_CsrAccessPlugin_SEL_lane0) && (CsrAccessPlugin_logic_fsm_stateReg == CsrAccessPlugin_logic_fsm_IDLE));
  assign when_CsrAccessPlugin_l174 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_1952);
  assign when_CsrService_l135 = (! 1'b1);
  assign when_CsrAccessPlugin_l174_1 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_1953);
  assign when_CsrService_l135_1 = (! 1'b1);
  assign when_CsrAccessPlugin_l174_2 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_1954);
  assign when_CsrService_l135_2 = (! 1'b1);
  assign when_CsrAccessPlugin_l174_3 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_794);
  assign when_CsrService_l221 = (_zz_when_CsrService_l221_1 || _zz_when_CsrService_l221);
  assign when_CsrService_l222 = (! 1'b1);
  assign when_CsrAccessPlugin_l174_4 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_778);
  assign when_CsrService_l221_1 = (_zz_when_CsrService_l221_1 || _zz_when_CsrService_l221);
  assign when_CsrService_l222_1 = (! 1'b1);
  assign when_CsrAccessPlugin_l174_5 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_3073);
  assign when_CsrService_l221_2 = (_zz_when_CsrService_l221_1 || _zz_when_CsrService_l221);
  assign when_CsrService_l222_2 = (! PrivilegedPlugin_logic_harts_0_time_host_accessable);
  assign when_CsrAccessPlugin_l174_6 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_3201);
  assign when_CsrService_l221_3 = (_zz_when_CsrService_l221_1 || _zz_when_CsrService_l221);
  assign when_CsrService_l222_3 = (! PrivilegedPlugin_logic_harts_0_time_host_accessable);
  assign when_CsrAccessPlugin_l174_7 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_UNAMED_1);
  assign when_CsrService_l135_3 = (! 1'b1);
  assign when_CsrAccessPlugin_l174_8 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_PerformanceCounterPlugin_logic_csrFilter);
  assign when_PerformanceCounterPlugin_l342 = (CsrAccessPlugin_bus_decode_address[9 : 8] == 2'b00);
  assign when_PerformanceCounterPlugin_l346 = (! PerformanceCounterPlugin_logic_csrDecode_privOk);
  assign when_PerformanceCounterPlugin_l350 = ((CsrAccessPlugin_bus_decode_address[9 : 8] < 2'b11) && PerformanceCounterPlugin_logic_csrDecode_hyperOK);
  assign when_CsrAccessPlugin_l174_9 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_CsrRamPlugin_csrMapper_selFilter);
  assign when_CsrService_l135_4 = (! 1'b1);
  assign when_CsrAccessPlugin_l174_10 = (CsrAccessPlugin_logic_fsm_inject_onDecodeDo && COMB_CSR_CsrAccessPlugin_logic_trapNextOnWriteFilter);
  assign CsrAccessPlugin_logic_fsm_inject_trap = (((! CsrAccessPlugin_logic_fsm_inject_implemented) || CsrAccessPlugin_bus_decode_exception) || CsrAccessPlugin_bus_decode_hostDenied);
  assign CsrAccessPlugin_bus_decode_read = CsrAccessPlugin_logic_fsm_inject_csrRead;
  assign CsrAccessPlugin_bus_decode_write = CsrAccessPlugin_logic_fsm_inject_csrWrite;
  assign CsrAccessPlugin_bus_decode_address = CsrAccessPlugin_logic_fsm_inject_csrAddress;
  assign CsrAccessPlugin_bus_decode_privilege = CsrAccessPlugin_logic_fsm_inject_csrPriv;
  assign CsrAccessPlugin_logic_fsm_interface_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign CsrAccessPlugin_logic_fsm_interface_rs1 = execute_ctrl2_up_integer_RS1_lane0;
  assign CsrAccessPlugin_logic_fsm_interface_uop = execute_ctrl2_down_Decode_UOP_lane0;
  assign CsrAccessPlugin_logic_fsm_interface_doImm = execute_ctrl2_down_CsrAccessPlugin_CSR_IMM_lane0;
  assign CsrAccessPlugin_logic_fsm_interface_doMask = execute_ctrl2_down_CsrAccessPlugin_CSR_MASK_lane0;
  assign CsrAccessPlugin_logic_fsm_interface_doClear = execute_ctrl2_down_CsrAccessPlugin_CSR_CLEAR_lane0;
  assign CsrAccessPlugin_logic_fsm_interface_rdEnable = execute_ctrl2_up_RD_ENABLE_lane0;
  assign CsrAccessPlugin_logic_fsm_interface_rdPhys = execute_ctrl2_down_RD_PHYS_lane0;
  assign CsrAccessPlugin_logic_fsm_inject_freeze = ((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_CsrAccessPlugin_SEL_lane0) && (! CsrAccessPlugin_logic_fsm_inject_unfreeze));
  always @(*) begin
    CsrAccessPlugin_logic_flushPort_valid = 1'b0;
    if(CsrAccessPlugin_logic_fsm_inject_flushReg) begin
      CsrAccessPlugin_logic_flushPort_valid = 1'b1;
    end
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
          if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
            if(CsrAccessPlugin_logic_fsm_inject_trapReg) begin
              CsrAccessPlugin_logic_flushPort_valid = 1'b1;
            end else begin
              if(CsrAccessPlugin_logic_fsm_inject_busTrapReg) begin
                CsrAccessPlugin_logic_flushPort_valid = 1'b1;
              end
            end
          end
        end
      end
    endcase
  end

  assign CsrAccessPlugin_logic_flushPort_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign CsrAccessPlugin_logic_flushPort_payload_self = 1'b0;
  always @(*) begin
    CsrAccessPlugin_logic_trapPort_valid = 1'b0;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
          if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
            if(CsrAccessPlugin_logic_fsm_inject_trapReg) begin
              CsrAccessPlugin_logic_trapPort_valid = 1'b1;
            end else begin
              if(CsrAccessPlugin_logic_fsm_inject_busTrapReg) begin
                CsrAccessPlugin_logic_trapPort_valid = 1'b1;
              end
            end
          end
        end
      end
    endcase
  end

  always @(*) begin
    CsrAccessPlugin_logic_trapPort_payload_exception = 1'b1;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
          if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
            if(!CsrAccessPlugin_logic_fsm_inject_trapReg) begin
              if(CsrAccessPlugin_logic_fsm_inject_busTrapReg) begin
                CsrAccessPlugin_logic_trapPort_payload_exception = 1'b0;
              end
            end
          end
        end
      end
    endcase
  end

  always @(*) begin
    CsrAccessPlugin_logic_trapPort_payload_code = 5'h02;
    if(when_CsrAccessPlugin_l218) begin
      CsrAccessPlugin_logic_trapPort_payload_code = 5'h16;
    end
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
          if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
            if(!CsrAccessPlugin_logic_fsm_inject_trapReg) begin
              if(CsrAccessPlugin_logic_fsm_inject_busTrapReg) begin
                CsrAccessPlugin_logic_trapPort_payload_code = CsrAccessPlugin_logic_fsm_inject_busTrapCodeReg;
              end
            end
          end
        end
      end
    endcase
  end

  assign CsrAccessPlugin_logic_trapPort_payload_tval = execute_ctrl2_down_Decode_UOP_lane0;
  assign CsrAccessPlugin_logic_trapPort_payload_tval2 = 32'h0;
  assign CsrAccessPlugin_logic_trapPort_payload_arg = 3'b000;
  assign when_CsrAccessPlugin_l218 = (((CsrAccessPlugin_logic_fsm_inject_implemented || CsrAccessPlugin_logic_fsm_inject_condImplemented) && (! CsrAccessPlugin_bus_decode_hostDenied)) && CsrAccessPlugin_bus_decode_virtual);
  assign when_CsrAccessPlugin_l222 = (! execute_freeze_valid);
  always @(*) begin
    CsrAccessPlugin_logic_fsm_readLogic_onReadsDo = 1'b0;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
        CsrAccessPlugin_logic_fsm_readLogic_onReadsDo = CsrAccessPlugin_logic_fsm_interface_read;
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    CsrAccessPlugin_logic_fsm_readLogic_onReadsFireDo = 1'b0;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
        if(when_CsrAccessPlugin_l325) begin
          CsrAccessPlugin_logic_fsm_readLogic_onReadsFireDo = CsrAccessPlugin_logic_fsm_interface_read;
        end
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
      end
    endcase
  end

  assign CsrAccessPlugin_bus_read_valid = CsrAccessPlugin_logic_fsm_readLogic_onReadsDo;
  assign CsrAccessPlugin_bus_read_address = CsrAccessPlugin_logic_fsm_interface_csrId;
  assign CsrAccessPlugin_bus_read_moving = (! CsrAccessPlugin_bus_read_halt);
  assign when_CsrAccessPlugin_l281 = (CsrAccessPlugin_logic_fsm_readLogic_onReadsDo && REG_CSR_CsrRamPlugin_csrMapper_selFilter);
  assign CsrAccessPlugin_logic_fsm_readLogic_csrValue = (((((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_9 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_10) | (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_12 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_13)) | ((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_14 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_15) | (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_17 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_19))) | (((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_21 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_22) | (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_24 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_25)) | ((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_27 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_29) | (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_31 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_33)))) | ((((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_35 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_37) | (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_39 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_41)) | ((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_43 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_44) | (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_46 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_47))) | (((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_48 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_50) | (_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_52 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_54)) | ((_zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_56 | _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_58) | (CsrRamPlugin_csrMapper_withRead ? CsrRamPlugin_csrMapper_read_data : _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_60)))));
  assign CsrAccessPlugin_bus_read_data = CsrAccessPlugin_logic_fsm_readLogic_csrValue;
  assign CsrAccessPlugin_bus_read_toWriteBits = CsrAccessPlugin_logic_fsm_readLogic_csrValue;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_2 = REG_CSR_836;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_4 = REG_CSR_772;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_7 = REG_CSR_774;
  assign _zz_CsrAccessPlugin_logic_fsm_readLogic_csrValue_8 = REG_CSR_800;
  assign _zz_when_CsrService_l221 = 1'b1;
  assign CsrAccessPlugin_bus_write_moving = (! CsrAccessPlugin_bus_write_halt);
  assign CsrAccessPlugin_logic_fsm_writeLogic_alu_mask = (CsrAccessPlugin_logic_fsm_interface_doImm ? _zz_CsrAccessPlugin_logic_fsm_writeLogic_alu_mask : CsrAccessPlugin_logic_fsm_interface_rs1);
  assign CsrAccessPlugin_logic_fsm_writeLogic_alu_masked = (CsrAccessPlugin_logic_fsm_interface_doClear ? (CsrAccessPlugin_logic_fsm_interface_aluInput & (~ CsrAccessPlugin_logic_fsm_writeLogic_alu_mask)) : (CsrAccessPlugin_logic_fsm_interface_aluInput | CsrAccessPlugin_logic_fsm_writeLogic_alu_mask));
  assign CsrAccessPlugin_logic_fsm_writeLogic_alu_result = (CsrAccessPlugin_logic_fsm_interface_doMask ? CsrAccessPlugin_logic_fsm_writeLogic_alu_masked : CsrAccessPlugin_logic_fsm_writeLogic_alu_mask);
  always @(*) begin
    CsrAccessPlugin_bus_write_bits = CsrAccessPlugin_logic_fsm_writeLogic_alu_result;
    if(when_CsrAccessPlugin_l372) begin
      CsrAccessPlugin_bus_write_bits[1 : 0] = 2'b00;
    end
    if(when_CsrAccessPlugin_l372_1) begin
      CsrAccessPlugin_bus_write_bits[0 : 0] = 1'b0;
    end
  end

  assign CsrAccessPlugin_bus_write_address = CsrAccessPlugin_logic_fsm_interface_csrId;
  always @(*) begin
    CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo = 1'b0;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
        CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo = CsrAccessPlugin_logic_fsm_interface_write;
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo = 1'b0;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
        if(when_CsrAccessPlugin_l354) begin
          CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo = CsrAccessPlugin_logic_fsm_interface_write;
        end
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
      end
    endcase
  end

  assign CsrAccessPlugin_bus_write_valid = CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo;
  assign _zz_when_CsrService_l221_1 = 1'b1;
  assign when_CsrAccessPlugin_l375 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo && REG_CSR_768);
  assign when_CsrAccessPlugin_l375_1 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo && REG_CSR_834);
  assign when_CsrAccessPlugin_l375_2 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo && REG_CSR_836);
  assign when_CsrAccessPlugin_l375_3 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo && REG_CSR_772);
  assign when_CsrAccessPlugin_l375_4 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo && REG_CSR_774);
  assign when_CsrAccessPlugin_l372 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo && REG_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_tvecFilter);
  assign when_CsrAccessPlugin_l372_1 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo && REG_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_epcFilter);
  assign when_CsrAccessPlugin_l375_5 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesFireDo && REG_CSR_800);
  assign when_CsrAccessPlugin_l372_2 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo && REG_CSR_PerformanceCounterPlugin_logic_csrFilter);
  assign when_PerformanceCounterPlugin_l378 = (! PerformanceCounterPlugin_logic_csrWrite_fired);
  assign when_PerformanceCounterPlugin_l380 = (! PerformanceCounterPlugin_logic_fsm_csrWriteCmd_ready);
  assign when_CsrAccessPlugin_l372_3 = (CsrAccessPlugin_logic_fsm_writeLogic_onWritesDo && REG_CSR_CsrRamPlugin_csrMapper_selFilter);
  assign CsrAccessPlugin_logic_wbWi_valid = execute_ctrl3_down_CsrAccessPlugin_SEL_lane0;
  assign CsrAccessPlugin_logic_wbWi_payload = CsrAccessPlugin_logic_fsm_interface_csrValue;
  always @(*) begin
    _zz_FetchCachelessPlugin_logic_fork_forked_valid = FetchCachelessPlugin_logic_fork_forked_fired;
    if(PcPlugin_logic_forcedSpawn) begin
      _zz_FetchCachelessPlugin_logic_fork_forked_valid = 1'b0;
    end
  end

  assign FetchCachelessPlugin_logic_fork_forked_valid = (fetch_logic_ctrls_0_up_isValid && (! _zz_FetchCachelessPlugin_logic_fork_forked_valid));
  assign fetch_logic_ctrls_0_haltRequest_CtrlLink_l112 = ((! _zz_FetchCachelessPlugin_logic_fork_forked_valid) && (! FetchCachelessPlugin_logic_fork_forked_ready));
  assign FetchCachelessPlugin_logic_fork_forked_fire = (FetchCachelessPlugin_logic_fork_forked_valid && FetchCachelessPlugin_logic_fork_forked_ready);
  assign _zz_FetchCachelessPlugin_logic_fork_forked_ready = (! FetchCachelessPlugin_logic_buffer_full);
  always @(*) begin
    FetchCachelessPlugin_logic_fork_halted_valid = (FetchCachelessPlugin_logic_fork_forked_valid && _zz_FetchCachelessPlugin_logic_fork_forked_ready);
    if(when_FetchCachelessPlugin_l178) begin
      FetchCachelessPlugin_logic_fork_halted_valid = 1'b0;
    end
  end

  assign FetchCachelessPlugin_logic_fork_forked_ready = (FetchCachelessPlugin_logic_fork_halted_ready && _zz_FetchCachelessPlugin_logic_fork_forked_ready);
  assign FetchCachelessPlugin_logic_fork_translated_valid = FetchCachelessPlugin_logic_fork_halted_valid;
  assign FetchCachelessPlugin_logic_fork_halted_ready = FetchCachelessPlugin_logic_fork_translated_ready;
  assign FetchCachelessPlugin_logic_fork_translated_payload_id = FetchCachelessPlugin_logic_buffer_reserveId_value;
  always @(*) begin
    FetchCachelessPlugin_logic_fork_translated_payload_address = fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
    FetchCachelessPlugin_logic_fork_translated_payload_address[1 : 1] = 1'b0;
  end

  assign FetchCachelessPlugin_logic_fork_translated_ready = FetchCachelessPlugin_logic_fork_translated_rValidN;
  assign FetchCachelessPlugin_logic_fork_persistent_valid = (FetchCachelessPlugin_logic_fork_translated_valid || (! FetchCachelessPlugin_logic_fork_translated_rValidN));
  assign FetchCachelessPlugin_logic_fork_persistent_payload_id = (FetchCachelessPlugin_logic_fork_translated_rValidN ? FetchCachelessPlugin_logic_fork_translated_payload_id : FetchCachelessPlugin_logic_fork_translated_rData_id);
  assign FetchCachelessPlugin_logic_fork_persistent_payload_address = (FetchCachelessPlugin_logic_fork_translated_rValidN ? FetchCachelessPlugin_logic_fork_translated_payload_address : FetchCachelessPlugin_logic_fork_translated_rData_address);
  assign FetchCachelessPlugin_logic_bus_cmd_valid = FetchCachelessPlugin_logic_fork_persistent_valid;
  assign FetchCachelessPlugin_logic_fork_persistent_ready = FetchCachelessPlugin_logic_bus_cmd_ready;
  assign FetchCachelessPlugin_logic_bus_cmd_payload_id = FetchCachelessPlugin_logic_fork_persistent_payload_id;
  assign FetchCachelessPlugin_logic_bus_cmd_payload_address = FetchCachelessPlugin_logic_fork_persistent_payload_address;
  assign FetchCachelessPlugin_logic_fork_translated_fire = (FetchCachelessPlugin_logic_fork_translated_valid && FetchCachelessPlugin_logic_fork_translated_ready);
  assign FetchCachelessPlugin_logic_buffer_inflightSpawn = FetchCachelessPlugin_logic_fork_translated_fire;
  assign FetchCachelessPlugin_logic_bus_cmd_isStall = (FetchCachelessPlugin_logic_bus_cmd_valid && (! FetchCachelessPlugin_logic_bus_cmd_ready));
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_BUFFER_ID = FetchCachelessPlugin_logic_buffer_reserveId_value;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_fork_PMA_FAULT = fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onPma_RSP_fault;
  assign when_FetchCachelessPlugin_l178 = ((((fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD || fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL) || fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD) || fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL) || fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_fork_PMA_FAULT);
  always @(*) begin
    FetchCachelessPlugin_logic_join_haltIt = _zz_FetchCachelessPlugin_logic_join_haltIt;
    if(when_FetchCachelessPlugin_l193) begin
      FetchCachelessPlugin_logic_join_haltIt = 1'b0;
    end
  end

  assign _zz_FetchCachelessPlugin_logic_join_rsp_error = FetchCachelessPlugin_logic_buffer_words_spinal_port1;
  always @(*) begin
    FetchCachelessPlugin_logic_join_rsp_error = _zz_FetchCachelessPlugin_logic_join_rsp_error[0];
    if(when_FetchCachelessPlugin_l193) begin
      FetchCachelessPlugin_logic_join_rsp_error = FetchCachelessPlugin_logic_bus_rsp_payload_error;
    end
  end

  always @(*) begin
    FetchCachelessPlugin_logic_join_rsp_word = _zz_FetchCachelessPlugin_logic_join_rsp_error[32 : 1];
    if(when_FetchCachelessPlugin_l193) begin
      FetchCachelessPlugin_logic_join_rsp_word = FetchCachelessPlugin_logic_bus_rsp_payload_word;
    end
  end

  assign when_FetchCachelessPlugin_l193 = (FetchCachelessPlugin_logic_bus_rsp_valid && (FetchCachelessPlugin_logic_bus_rsp_payload_id == fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_BUFFER_ID));
  assign fetch_logic_ctrls_1_down_Fetch_WORD = FetchCachelessPlugin_logic_join_rsp_word;
  always @(*) begin
    fetch_logic_ctrls_1_down_TRAP = 1'b0;
    if(when_FetchCachelessPlugin_l213) begin
      fetch_logic_ctrls_1_down_TRAP = 1'b1;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT) begin
      fetch_logic_ctrls_1_down_TRAP = 1'b1;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT) begin
      fetch_logic_ctrls_1_down_TRAP = 1'b1;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL) begin
      fetch_logic_ctrls_1_down_TRAP = 1'b1;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD) begin
      fetch_logic_ctrls_1_down_TRAP = 1'b1;
    end
    if(when_FetchCachelessPlugin_l266) begin
      fetch_logic_ctrls_1_down_TRAP = 1'b0;
    end
  end

  assign FetchCachelessPlugin_logic_trapPort_valid = (fetch_logic_ctrls_1_down_TRAP && (! FetchCachelessPlugin_logic_join_trapSent));
  assign FetchCachelessPlugin_logic_trapPort_payload_tval = fetch_logic_ctrls_1_down_Fetch_WORD_PC;
  assign FetchCachelessPlugin_logic_trapPort_payload_tval2 = {2'd0, _zz_FetchCachelessPlugin_logic_trapPort_payload_tval2};
  always @(*) begin
    FetchCachelessPlugin_logic_trapPort_payload_exception = 1'bx;
    if(when_FetchCachelessPlugin_l213) begin
      FetchCachelessPlugin_logic_trapPort_payload_exception = 1'b1;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT) begin
      FetchCachelessPlugin_logic_trapPort_payload_exception = 1'b1;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT) begin
      FetchCachelessPlugin_logic_trapPort_payload_exception = 1'b1;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL) begin
      FetchCachelessPlugin_logic_trapPort_payload_exception = 1'b0;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD) begin
      FetchCachelessPlugin_logic_trapPort_payload_exception = 1'b0;
    end
  end

  always @(*) begin
    FetchCachelessPlugin_logic_trapPort_payload_code = 5'bxxxxx;
    if(when_FetchCachelessPlugin_l213) begin
      FetchCachelessPlugin_logic_trapPort_payload_code = 5'h01;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT) begin
      FetchCachelessPlugin_logic_trapPort_payload_code = 5'h0c;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT) begin
      FetchCachelessPlugin_logic_trapPort_payload_code = 5'h01;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL) begin
      FetchCachelessPlugin_logic_trapPort_payload_code = 5'h07;
    end
    if(fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD) begin
      FetchCachelessPlugin_logic_trapPort_payload_code = 5'h04;
    end
  end

  assign _zz_9 = zz_FetchCachelessPlugin_logic_trapPort_payload_arg(1'b0);
  always @(*) FetchCachelessPlugin_logic_trapPort_payload_arg = _zz_9;
  assign when_FetchCachelessPlugin_l213 = ((FetchCachelessPlugin_logic_join_rsp_error || fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_fork_PMA_FAULT) || fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_pmpPort_ACCESS_FAULT);
  assign when_FetchCachelessPlugin_l266 = ((! fetch_logic_ctrls_1_up_isValid) || FetchCachelessPlugin_logic_join_haltIt);
  assign fetch_logic_ctrls_1_haltRequest_FetchCachelessPlugin_l268 = FetchCachelessPlugin_logic_join_haltIt;
  assign CsrRamPlugin_logic_writeLogic_hits = {CsrRamPlugin_setup_initPort_valid,{CsrRamPlugin_csrMapper_write_valid,{TrapPlugin_logic_harts_0_crsPorts_write_valid,PerformanceCounterPlugin_logic_writePort_valid}}};
  assign CsrRamPlugin_logic_writeLogic_hit = (|CsrRamPlugin_logic_writeLogic_hits);
  assign CsrRamPlugin_logic_writeLogic_hits_ohFirst_input = CsrRamPlugin_logic_writeLogic_hits;
  assign CsrRamPlugin_logic_writeLogic_hits_ohFirst_masked = (CsrRamPlugin_logic_writeLogic_hits_ohFirst_input & (~ _zz_CsrRamPlugin_logic_writeLogic_hits_ohFirst_masked));
  assign CsrRamPlugin_logic_writeLogic_oh = CsrRamPlugin_logic_writeLogic_hits_ohFirst_masked;
  assign _zz_PerformanceCounterPlugin_logic_writePort_ready = CsrRamPlugin_logic_writeLogic_oh[0];
  assign _zz_TrapPlugin_logic_harts_0_crsPorts_write_ready = CsrRamPlugin_logic_writeLogic_oh[1];
  assign _zz_CsrRamPlugin_csrMapper_write_ready = CsrRamPlugin_logic_writeLogic_oh[2];
  assign _zz_CsrRamPlugin_setup_initPort_ready = CsrRamPlugin_logic_writeLogic_oh[3];
  assign CsrRamPlugin_logic_writeLogic_port_valid = CsrRamPlugin_logic_writeLogic_hit;
  assign CsrRamPlugin_logic_writeLogic_port_payload_address = (((_zz_PerformanceCounterPlugin_logic_writePort_ready ? PerformanceCounterPlugin_logic_writePort_address : 3'b000) | (_zz_TrapPlugin_logic_harts_0_crsPorts_write_ready ? TrapPlugin_logic_harts_0_crsPorts_write_address : 3'b000)) | ((_zz_CsrRamPlugin_csrMapper_write_ready ? CsrRamPlugin_csrMapper_write_address : 3'b000) | (_zz_CsrRamPlugin_setup_initPort_ready ? CsrRamPlugin_setup_initPort_address : 3'b000)));
  assign CsrRamPlugin_logic_writeLogic_port_payload_data = (((_zz_PerformanceCounterPlugin_logic_writePort_ready ? PerformanceCounterPlugin_logic_writePort_data : 32'h0) | (_zz_TrapPlugin_logic_harts_0_crsPorts_write_ready ? TrapPlugin_logic_harts_0_crsPorts_write_data : 32'h0)) | ((_zz_CsrRamPlugin_csrMapper_write_ready ? CsrRamPlugin_csrMapper_write_data : 32'h0) | (_zz_CsrRamPlugin_setup_initPort_ready ? CsrRamPlugin_setup_initPort_data : 32'h0)));
  assign PerformanceCounterPlugin_logic_writePort_ready = _zz_PerformanceCounterPlugin_logic_writePort_ready;
  assign TrapPlugin_logic_harts_0_crsPorts_write_ready = _zz_TrapPlugin_logic_harts_0_crsPorts_write_ready;
  assign CsrRamPlugin_csrMapper_write_ready = _zz_CsrRamPlugin_csrMapper_write_ready;
  assign CsrRamPlugin_setup_initPort_ready = _zz_CsrRamPlugin_setup_initPort_ready;
  assign CsrRamPlugin_logic_readLogic_hits = {CsrRamPlugin_csrMapper_read_valid,{TrapPlugin_logic_harts_0_crsPorts_read_valid,PerformanceCounterPlugin_logic_readPort_valid}};
  assign CsrRamPlugin_logic_readLogic_hit = (|CsrRamPlugin_logic_readLogic_hits);
  assign CsrRamPlugin_logic_readLogic_hits_ohFirst_input = CsrRamPlugin_logic_readLogic_hits;
  assign CsrRamPlugin_logic_readLogic_hits_ohFirst_masked = (CsrRamPlugin_logic_readLogic_hits_ohFirst_input & (~ _zz_CsrRamPlugin_logic_readLogic_hits_ohFirst_masked));
  assign CsrRamPlugin_logic_readLogic_oh = CsrRamPlugin_logic_readLogic_hits_ohFirst_masked;
  assign _zz_CsrRamPlugin_logic_readLogic_sel = CsrRamPlugin_logic_readLogic_oh[1];
  assign _zz_CsrRamPlugin_logic_readLogic_sel_1 = CsrRamPlugin_logic_readLogic_oh[2];
  assign CsrRamPlugin_logic_readLogic_sel = {_zz_CsrRamPlugin_logic_readLogic_sel_1,_zz_CsrRamPlugin_logic_readLogic_sel};
  assign CsrRamPlugin_logic_readLogic_port_rsp = CsrRamPlugin_logic_mem_spinal_port1;
  assign CsrRamPlugin_logic_readLogic_port_cmd_valid = (((|CsrRamPlugin_logic_readLogic_oh) && (! CsrRamPlugin_logic_writeLogic_port_valid)) && (! CsrRamPlugin_logic_readLogic_busy));
  assign CsrRamPlugin_logic_readLogic_port_cmd_payload = _zz_CsrRamPlugin_logic_readLogic_port_cmd_payload;
  assign PerformanceCounterPlugin_logic_readPort_ready = CsrRamPlugin_logic_readLogic_ohReg[0];
  assign TrapPlugin_logic_harts_0_crsPorts_read_ready = CsrRamPlugin_logic_readLogic_ohReg[1];
  assign CsrRamPlugin_csrMapper_read_ready = CsrRamPlugin_logic_readLogic_ohReg[2];
  assign PerformanceCounterPlugin_logic_readPort_data = CsrRamPlugin_logic_readLogic_port_rsp;
  assign TrapPlugin_logic_harts_0_crsPorts_read_data = CsrRamPlugin_logic_readLogic_port_rsp;
  assign CsrRamPlugin_csrMapper_read_data = CsrRamPlugin_logic_readLogic_port_rsp;
  assign CsrRamPlugin_logic_flush_done = CsrRamPlugin_logic_flush_counter[3];
  assign CsrRamPlugin_setup_initPort_valid = (! CsrRamPlugin_logic_flush_done);
  assign CsrRamPlugin_setup_initPort_address = CsrRamPlugin_logic_flush_counter[2:0];
  assign CsrRamPlugin_setup_initPort_data = 32'h0;
  assign execute_lane0_bypasser_integer_RS1_port_valid = (! execute_freeze_valid);
  assign execute_lane0_bypasser_integer_RS1_port_address = execute_ctrl0_down_RS1_PHYS_lane0[4 : 0];
  always @(*) begin
    execute_lane0_bypasser_integer_RS1_bypassEnables[0] = (((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && (execute_ctrl5_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && 1'b1);
    execute_lane0_bypasser_integer_RS1_bypassEnables[1] = 1'b1;
  end

  assign _zz_execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0 = execute_lane0_bypasser_integer_RS1_bypassEnables;
  assign execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0 = _zz_execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0[0];
  assign execute_lane0_bypasser_integer_RS1_bypassEnables_bools_1 = _zz_execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0[1];
  always @(*) begin
    _zz_execute_lane0_bypasser_integer_RS1_sel[0] = (execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0 && (! 1'b0));
    _zz_execute_lane0_bypasser_integer_RS1_sel[1] = (execute_lane0_bypasser_integer_RS1_bypassEnables_bools_1 && (! execute_lane0_bypasser_integer_RS1_bypassEnables_bools_0));
  end

  assign execute_lane0_bypasser_integer_RS1_sel = _zz_execute_lane0_bypasser_integer_RS1_sel;
  assign execute_ctrl1_down_integer_RS1_lane0 = ((execute_lane0_bypasser_integer_RS1_sel[0] ? execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (execute_lane0_bypasser_integer_RS1_sel[1] ? execute_lane0_bypasser_integer_RS1_port_data : 32'h0));
  assign execute_lane0_bypasser_integer_RS2_port_valid = (! execute_freeze_valid);
  assign execute_lane0_bypasser_integer_RS2_port_address = execute_ctrl0_down_RS2_PHYS_lane0[4 : 0];
  always @(*) begin
    execute_lane0_bypasser_integer_RS2_bypassEnables[0] = (((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && (execute_ctrl5_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && 1'b1);
    execute_lane0_bypasser_integer_RS2_bypassEnables[1] = 1'b1;
  end

  assign _zz_execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0 = execute_lane0_bypasser_integer_RS2_bypassEnables;
  assign execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0 = _zz_execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0[0];
  assign execute_lane0_bypasser_integer_RS2_bypassEnables_bools_1 = _zz_execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0[1];
  always @(*) begin
    _zz_execute_lane0_bypasser_integer_RS2_sel[0] = (execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0 && (! 1'b0));
    _zz_execute_lane0_bypasser_integer_RS2_sel[1] = (execute_lane0_bypasser_integer_RS2_bypassEnables_bools_1 && (! execute_lane0_bypasser_integer_RS2_bypassEnables_bools_0));
  end

  assign execute_lane0_bypasser_integer_RS2_sel = _zz_execute_lane0_bypasser_integer_RS2_sel;
  assign execute_ctrl1_down_integer_RS2_lane0 = ((execute_lane0_bypasser_integer_RS2_sel[0] ? execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (execute_lane0_bypasser_integer_RS2_sel[1] ? execute_lane0_bypasser_integer_RS2_port_data : 32'h0));
  assign execute_lane0_logic_completions_onCtrl_0_port_valid = (((execute_ctrl2_down_LANE_SEL_lane0 && execute_ctrl2_down_isReady) && (! execute_lane0_ctrls_2_downIsCancel)) && execute_ctrl2_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0);
  assign execute_lane0_logic_completions_onCtrl_0_port_payload_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign execute_lane0_logic_completions_onCtrl_0_port_payload_trap = execute_ctrl2_down_TRAP_lane0;
  assign execute_lane0_logic_completions_onCtrl_0_port_payload_commit = execute_ctrl2_down_COMMIT_lane0;
  assign execute_lane0_logic_completions_onCtrl_1_port_valid = (((execute_ctrl4_down_LANE_SEL_lane0 && execute_ctrl4_down_isReady) && (! execute_lane0_ctrls_4_downIsCancel)) && execute_ctrl4_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0);
  assign execute_lane0_logic_completions_onCtrl_1_port_payload_uopId = execute_ctrl4_down_Decode_UOP_ID_lane0;
  assign execute_lane0_logic_completions_onCtrl_1_port_payload_trap = execute_ctrl4_down_TRAP_lane0;
  assign execute_lane0_logic_completions_onCtrl_1_port_payload_commit = execute_ctrl4_down_COMMIT_lane0;
  assign execute_lane0_logic_completions_onCtrl_2_port_valid = (((execute_ctrl3_down_LANE_SEL_lane0 && execute_ctrl3_down_isReady) && (! execute_lane0_ctrls_3_downIsCancel)) && execute_ctrl3_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0);
  assign execute_lane0_logic_completions_onCtrl_2_port_payload_uopId = execute_ctrl3_down_Decode_UOP_ID_lane0;
  assign execute_lane0_logic_completions_onCtrl_2_port_payload_trap = execute_ctrl3_down_TRAP_lane0;
  assign execute_lane0_logic_completions_onCtrl_2_port_payload_commit = execute_ctrl3_down_COMMIT_lane0;
  assign execute_lane0_logic_decoding_decodingBits = execute_ctrl1_down_Decode_UOP_lane0;
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h0000004c) == 32'h00000004);
  always @(*) begin
    execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0_1[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0 = 1'b0;
    end
  end

  always @(*) begin
    execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000050) == 32'h00000040);
  always @(*) begin
    execute_ctrl1_down_early0_BranchPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_BranchPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_early0_BranchPlugin_SEL_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h02004074) == 32'h02000030);
  always @(*) begin
    execute_ctrl1_down_early0_MulPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_MulPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_early0_MulPlugin_SEL_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h02004064) == 32'h02004020);
  always @(*) begin
    execute_ctrl1_down_early0_DivPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_DivPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_early0_DivPlugin_SEL_lane0 = 1'b0;
    end
  end

  always @(*) begin
    execute_ctrl1_down_early0_EnvPlugin_SEL_lane0 = _zz_execute_ctrl1_down_early0_EnvPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_early0_EnvPlugin_SEL_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_1 = ((execute_lane0_logic_decoding_decodingBits & 32'h00002050) == 32'h00002050);
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_2 = ((execute_lane0_logic_decoding_decodingBits & 32'h00001050) == 32'h00001050);
  always @(*) begin
    execute_ctrl1_down_CsrAccessPlugin_SEL_lane0 = _zz_execute_ctrl1_down_CsrAccessPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_CsrAccessPlugin_SEL_lane0 = 1'b0;
    end
  end

  always @(*) begin
    execute_ctrl1_down_AguPlugin_SEL_lane0 = _zz_execute_ctrl1_down_AguPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_AguPlugin_SEL_lane0 = 1'b0;
    end
  end

  always @(*) begin
    execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0 = _zz_execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_1 = ((execute_lane0_logic_decoding_decodingBits & 32'h0000000c) == 32'h00000004);
  always @(*) begin
    execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0 = _zz_execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_2 = ((execute_lane0_logic_decoding_decodingBits & 32'h02000050) == 32'h00000010);
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_3 = ((execute_lane0_logic_decoding_decodingBits & 32'h00003040) == 32'h00000040);
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_4 = ((execute_lane0_logic_decoding_decodingBits & 32'h00002008) == 32'h00000008);
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_5 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000030) == 32'h00000010);
  always @(*) begin
    execute_ctrl1_down_COMPLETION_AT_2_lane0 = _zz_execute_ctrl1_down_COMPLETION_AT_2_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_COMPLETION_AT_2_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_1 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000038) == 32'h0);
  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_2 = ((execute_lane0_logic_decoding_decodingBits & 32'h00002048) == 32'h00002008);
  always @(*) begin
    execute_ctrl1_down_COMPLETION_AT_4_lane0 = _zz_execute_ctrl1_down_COMPLETION_AT_4_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_COMPLETION_AT_4_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_3 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000078) == 32'h00000020);
  always @(*) begin
    execute_ctrl1_down_COMPLETION_AT_3_lane0 = _zz_execute_ctrl1_down_COMPLETION_AT_3_lane0[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_COMPLETION_AT_3_lane0 = 1'b0;
    end
  end

  always @(*) begin
    execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0 = _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_6[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0 = 1'b0;
    end
  end

  always @(*) begin
    execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0 = _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0_3[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0 = 1'b0;
    end
  end

  always @(*) begin
    execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0 = _zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0_4[0];
    if(execute_ctrl1_down_TRAP_lane0) begin
      execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0 = 1'b0;
    end
  end

  assign _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00006000) == 32'h0);
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000004) == 32'h00000004);
  assign execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0 = _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0_1[0];
  assign execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0 = _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0[0];
  assign _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00004000) == 32'h0);
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00001000) == 32'h00001000);
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1 = {(|{_zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0,{_zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0,_zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0}}),(|{_zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0,{_zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0,((execute_lane0_logic_decoding_decodingBits & 32'h00003000) == 32'h00002000)}})};
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0 = _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_1;
  assign _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2 = _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0;
  assign execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0 = _zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0_2;
  assign execute_ctrl1_down_SrcStageables_REVERT_lane0 = _zz_execute_ctrl1_down_SrcStageables_REVERT_lane0[0];
  assign execute_ctrl1_down_SrcStageables_ZERO_lane0 = _zz_execute_ctrl1_down_SrcStageables_ZERO_lane0[0];
  assign execute_ctrl1_down_early0_SrcPlugin_logic_SRC1_CTRL_lane0 = (|_zz_execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0);
  assign execute_ctrl1_down_early0_SrcPlugin_logic_SRC2_CTRL_lane0 = {(|{_zz_execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0,((execute_lane0_logic_decoding_decodingBits & 32'h00000070) == 32'h00000020)}),(|{((execute_lane0_logic_decoding_decodingBits & 32'h00000050) == 32'h0),((execute_lane0_logic_decoding_decodingBits & 32'h00000024) == 32'h0)})};
  assign execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 = _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0[0];
  assign _zz_execute_ctrl1_down_AguPlugin_STORE_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h10000020) == 32'h00000020);
  assign execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 = {(|{((execute_lane0_logic_decoding_decodingBits & 32'h00000010) == 32'h00000010),{((execute_lane0_logic_decoding_decodingBits & _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0) == 32'h00002000),{_zz_execute_ctrl1_down_AguPlugin_STORE_lane0,(_zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0_1 == _zz_execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0_2)}}}),(|((execute_lane0_logic_decoding_decodingBits & 32'h00001010) == 32'h00001000))};
  assign execute_ctrl1_down_BYPASSED_AT_1_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_1_lane0[0];
  assign execute_ctrl1_down_BYPASSED_AT_2_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_2_lane0[0];
  assign execute_ctrl1_down_BYPASSED_AT_3_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_3_lane0[0];
  assign execute_ctrl1_down_BYPASSED_AT_4_lane0 = _zz_execute_ctrl1_down_BYPASSED_AT_4_lane0[0];
  assign execute_ctrl1_down_SrcStageables_UNSIGNED_lane0 = _zz_execute_ctrl1_down_SrcStageables_UNSIGNED_lane0[0];
  assign execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0 = _zz_execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0_1[0];
  assign execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0 = _zz_execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0[0];
  assign _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000008) == 32'h00000008);
  assign _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1 = {(|_zz_execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0_1),(|_zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0)};
  assign _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0 = _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_1;
  assign _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2 = _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0;
  assign execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0 = _zz_execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0_2;
  assign _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00002000) == 32'h00002000);
  assign execute_ctrl1_down_MulPlugin_HIGH_lane0 = _zz_execute_ctrl1_down_MulPlugin_HIGH_lane0[0];
  assign execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0 = _zz_execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0[0];
  assign execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0 = _zz_execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0_1[0];
  assign execute_ctrl1_down_DivPlugin_REM_lane0 = _zz_execute_ctrl1_down_DivPlugin_REM_lane0[0];
  assign execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0 = _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0[0];
  assign execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0 = _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0_1[0];
  assign execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0 = _zz_execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0_1[0];
  assign execute_ctrl1_down_AguPlugin_LOAD_lane0 = _zz_execute_ctrl1_down_AguPlugin_LOAD_lane0[0];
  assign execute_ctrl1_down_AguPlugin_STORE_lane0 = _zz_execute_ctrl1_down_AguPlugin_STORE_lane0_1[0];
  assign execute_ctrl1_down_AguPlugin_EXECUTE_lane0 = _zz_execute_ctrl1_down_AguPlugin_EXECUTE_lane0[0];
  assign execute_ctrl1_down_AguPlugin_ATOMIC_lane0 = _zz_execute_ctrl1_down_AguPlugin_ATOMIC_lane0_1[0];
  assign execute_ctrl1_down_AguPlugin_FLOAT_lane0 = _zz_execute_ctrl1_down_AguPlugin_FLOAT_lane0[0];
  assign execute_ctrl1_down_AguPlugin_CLEAN_lane0 = _zz_execute_ctrl1_down_AguPlugin_CLEAN_lane0[0];
  assign execute_ctrl1_down_AguPlugin_INVALIDATE_lane0 = _zz_execute_ctrl1_down_AguPlugin_INVALIDATE_lane0[0];
  assign execute_ctrl1_down_AguPlugin_GUEST_lane0 = _zz_execute_ctrl1_down_AguPlugin_GUEST_lane0[0];
  assign _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0 = ((execute_lane0_logic_decoding_decodingBits & 32'h00000040) == 32'h0);
  assign _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2 = {(|((execute_lane0_logic_decoding_decodingBits & 32'h30001000) == 32'h10000000)),{(|{((execute_lane0_logic_decoding_decodingBits & 32'h20000000) == 32'h20000000),_zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0}),(|{((execute_lane0_logic_decoding_decodingBits & 32'h00100000) == 32'h00100000),_zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0})}};
  assign _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1 = _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_2;
  assign _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3 = _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_1;
  assign execute_ctrl1_down_early0_EnvPlugin_OP_lane0 = _zz_execute_ctrl1_down_early0_EnvPlugin_OP_lane0_3;
  assign when_ExecuteLanePlugin_l306 = (|{(early0_EnvPlugin_logic_flushPort_valid && 1'b1),{(CsrAccessPlugin_logic_flushPort_valid && 1'b1),{(early0_BranchPlugin_logic_flushPort_valid && 1'b1),(LsuCachelessPlugin_logic_flushPort_valid && 1'b1)}}});
  assign execute_lane0_ctrls_0_downIsCancel = 1'b0;
  assign execute_lane0_ctrls_0_upIsCancel = when_ExecuteLanePlugin_l306;
  assign when_ExecuteLanePlugin_l306_1 = (|{(early0_EnvPlugin_logic_flushPort_valid && 1'b1),{(CsrAccessPlugin_logic_flushPort_valid && 1'b1),{(early0_BranchPlugin_logic_flushPort_valid && 1'b1),(LsuCachelessPlugin_logic_flushPort_valid && 1'b1)}}});
  assign execute_lane0_ctrls_1_downIsCancel = 1'b0;
  assign execute_lane0_ctrls_1_upIsCancel = when_ExecuteLanePlugin_l306_1;
  assign when_ExecuteLanePlugin_l306_2 = (|{((early0_EnvPlugin_logic_flushPort_valid && 1'b1) && (1'b0 || (1'b1 && early0_EnvPlugin_logic_flushPort_payload_self))),{((CsrAccessPlugin_logic_flushPort_valid && 1'b1) && (1'b0 || (_zz_when_ExecuteLanePlugin_l306_2 && CsrAccessPlugin_logic_flushPort_payload_self))),{((early0_BranchPlugin_logic_flushPort_valid && _zz_when_ExecuteLanePlugin_l306_2_1) && (_zz_when_ExecuteLanePlugin_l306_2_2 || _zz_when_ExecuteLanePlugin_l306_2_3)),((LsuCachelessPlugin_logic_flushPort_valid && _zz_when_ExecuteLanePlugin_l306_2_4) && (_zz_when_ExecuteLanePlugin_l306_2_5 || _zz_when_ExecuteLanePlugin_l306_2_6))}}});
  assign execute_lane0_ctrls_2_downIsCancel = 1'b0;
  assign execute_lane0_ctrls_2_upIsCancel = when_ExecuteLanePlugin_l306_2;
  assign execute_lane0_ctrls_3_downIsCancel = 1'b0;
  assign execute_lane0_ctrls_3_upIsCancel = 1'b0;
  assign execute_lane0_ctrls_4_downIsCancel = 1'b0;
  assign execute_lane0_ctrls_4_upIsCancel = 1'b0;
  assign execute_lane0_ctrls_5_downIsCancel = 1'b0;
  assign execute_lane0_ctrls_5_upIsCancel = 1'b0;
  assign execute_lane0_logic_trapPending[0] = (|{((execute_ctrl4_up_LANE_SEL_lane0 && 1'b1) && execute_ctrl4_down_TRAP_lane0),{((execute_ctrl3_up_LANE_SEL_lane0 && 1'b1) && execute_ctrl3_down_TRAP_lane0),{((execute_ctrl2_up_LANE_SEL_lane0 && 1'b1) && execute_ctrl2_down_TRAP_lane0),((execute_ctrl1_up_LANE_SEL_lane0 && 1'b1) && execute_ctrl1_down_TRAP_lane0)}}});
  assign execute_ctrl2_up_COMMIT_lane0 = (! execute_ctrl2_up_TRAP_lane0);
  assign WhiteboxerPlugin_logic_csr_access_valid = CsrAccessPlugin_logic_fsm_interface_fire;
  assign WhiteboxerPlugin_logic_csr_access_payload_uopId = CsrAccessPlugin_logic_fsm_interface_uopId;
  assign WhiteboxerPlugin_logic_csr_access_payload_address = _zz_WhiteboxerPlugin_logic_csr_access_payload_address[31 : 20];
  assign WhiteboxerPlugin_logic_csr_access_payload_write = CsrAccessPlugin_logic_fsm_interface_onWriteBits;
  assign WhiteboxerPlugin_logic_csr_access_payload_read = CsrAccessPlugin_logic_fsm_interface_csrValue;
  assign WhiteboxerPlugin_logic_csr_access_payload_writeDone = CsrAccessPlugin_logic_fsm_interface_write;
  assign WhiteboxerPlugin_logic_csr_access_payload_readDone = CsrAccessPlugin_logic_fsm_interface_read;
  assign WhiteboxerPlugin_logic_csr_port_valid = WhiteboxerPlugin_logic_csr_access_valid;
  assign WhiteboxerPlugin_logic_csr_port_payload_uopId = WhiteboxerPlugin_logic_csr_access_payload_uopId;
  assign WhiteboxerPlugin_logic_csr_port_payload_address = WhiteboxerPlugin_logic_csr_access_payload_address;
  assign WhiteboxerPlugin_logic_csr_port_payload_write = WhiteboxerPlugin_logic_csr_access_payload_write;
  assign WhiteboxerPlugin_logic_csr_port_payload_read = WhiteboxerPlugin_logic_csr_access_payload_read;
  assign WhiteboxerPlugin_logic_csr_port_payload_writeDone = WhiteboxerPlugin_logic_csr_access_payload_writeDone;
  assign WhiteboxerPlugin_logic_csr_port_payload_readDone = WhiteboxerPlugin_logic_csr_access_payload_readDone;
  assign WhiteboxerPlugin_logic_rfWrites_ports_0_valid = lane0_integer_WriteBackPlugin_logic_stages_0_write_valid;
  assign WhiteboxerPlugin_logic_rfWrites_ports_0_payload_uopId = lane0_integer_WriteBackPlugin_logic_stages_0_write_payload_uopId;
  assign WhiteboxerPlugin_logic_rfWrites_ports_0_payload_data = lane0_integer_WriteBackPlugin_logic_stages_0_write_payload_data;
  assign WhiteboxerPlugin_logic_rfWrites_ports_1_valid = lane0_integer_WriteBackPlugin_logic_stages_1_write_valid;
  assign WhiteboxerPlugin_logic_rfWrites_ports_1_payload_uopId = lane0_integer_WriteBackPlugin_logic_stages_1_write_payload_uopId;
  assign WhiteboxerPlugin_logic_rfWrites_ports_1_payload_data = lane0_integer_WriteBackPlugin_logic_stages_1_write_payload_data;
  assign WhiteboxerPlugin_logic_rfWrites_ports_2_valid = lane0_integer_WriteBackPlugin_logic_stages_2_write_valid;
  assign WhiteboxerPlugin_logic_rfWrites_ports_2_payload_uopId = lane0_integer_WriteBackPlugin_logic_stages_2_write_payload_uopId;
  assign WhiteboxerPlugin_logic_rfWrites_ports_2_payload_data = lane0_integer_WriteBackPlugin_logic_stages_2_write_payload_data;
  assign WhiteboxerPlugin_logic_completions_ports_0_valid = DecoderPlugin_logic_laneLogic_0_completionPort_valid;
  assign WhiteboxerPlugin_logic_completions_ports_0_payload_uopId = DecoderPlugin_logic_laneLogic_0_completionPort_payload_uopId;
  assign WhiteboxerPlugin_logic_completions_ports_0_payload_trap = DecoderPlugin_logic_laneLogic_0_completionPort_payload_trap;
  assign WhiteboxerPlugin_logic_completions_ports_0_payload_commit = DecoderPlugin_logic_laneLogic_0_completionPort_payload_commit;
  assign WhiteboxerPlugin_logic_completions_ports_1_valid = execute_lane0_logic_completions_onCtrl_0_port_valid;
  assign WhiteboxerPlugin_logic_completions_ports_1_payload_uopId = execute_lane0_logic_completions_onCtrl_0_port_payload_uopId;
  assign WhiteboxerPlugin_logic_completions_ports_1_payload_trap = execute_lane0_logic_completions_onCtrl_0_port_payload_trap;
  assign WhiteboxerPlugin_logic_completions_ports_1_payload_commit = execute_lane0_logic_completions_onCtrl_0_port_payload_commit;
  assign WhiteboxerPlugin_logic_completions_ports_2_valid = execute_lane0_logic_completions_onCtrl_1_port_valid;
  assign WhiteboxerPlugin_logic_completions_ports_2_payload_uopId = execute_lane0_logic_completions_onCtrl_1_port_payload_uopId;
  assign WhiteboxerPlugin_logic_completions_ports_2_payload_trap = execute_lane0_logic_completions_onCtrl_1_port_payload_trap;
  assign WhiteboxerPlugin_logic_completions_ports_2_payload_commit = execute_lane0_logic_completions_onCtrl_1_port_payload_commit;
  assign WhiteboxerPlugin_logic_completions_ports_3_valid = execute_lane0_logic_completions_onCtrl_2_port_valid;
  assign WhiteboxerPlugin_logic_completions_ports_3_payload_uopId = execute_lane0_logic_completions_onCtrl_2_port_payload_uopId;
  assign WhiteboxerPlugin_logic_completions_ports_3_payload_trap = execute_lane0_logic_completions_onCtrl_2_port_payload_trap;
  assign WhiteboxerPlugin_logic_completions_ports_3_payload_commit = execute_lane0_logic_completions_onCtrl_2_port_payload_commit;
  assign WhiteboxerPlugin_logic_commits_ports_0_oh_0 = ((((execute_ctrl4_down_LANE_SEL_lane0 && execute_ctrl4_down_isReady) && (! execute_lane0_ctrls_4_downIsCancel)) && execute_ctrl4_down_COMMIT_lane0) && 1'b1);
  assign WhiteboxerPlugin_logic_commits_ports_0_valid = (|WhiteboxerPlugin_logic_commits_ports_0_oh_0);
  assign WhiteboxerPlugin_logic_commits_ports_0_pc = (WhiteboxerPlugin_logic_commits_ports_0_oh_0 ? execute_ctrl4_down_PC_lane0 : 32'h0);
  assign WhiteboxerPlugin_logic_commits_ports_0_uop = (WhiteboxerPlugin_logic_commits_ports_0_oh_0 ? execute_ctrl4_down_Decode_UOP_lane0 : 32'h0);
  assign WhiteboxerPlugin_logic_reschedules_flushes_0_valid = LsuCachelessPlugin_logic_flushPort_valid;
  assign WhiteboxerPlugin_logic_reschedules_flushes_0_payload_uopId = LsuCachelessPlugin_logic_flushPort_payload_uopId;
  assign WhiteboxerPlugin_logic_reschedules_flushes_0_payload_self = LsuCachelessPlugin_logic_flushPort_payload_self;
  assign WhiteboxerPlugin_logic_reschedules_flushes_1_valid = early0_BranchPlugin_logic_flushPort_valid;
  assign WhiteboxerPlugin_logic_reschedules_flushes_1_payload_uopId = early0_BranchPlugin_logic_flushPort_payload_uopId;
  assign WhiteboxerPlugin_logic_reschedules_flushes_1_payload_self = early0_BranchPlugin_logic_flushPort_payload_self;
  assign WhiteboxerPlugin_logic_reschedules_flushes_2_valid = CsrAccessPlugin_logic_flushPort_valid;
  assign WhiteboxerPlugin_logic_reschedules_flushes_2_payload_uopId = CsrAccessPlugin_logic_flushPort_payload_uopId;
  assign WhiteboxerPlugin_logic_reschedules_flushes_2_payload_self = CsrAccessPlugin_logic_flushPort_payload_self;
  assign WhiteboxerPlugin_logic_reschedules_flushes_3_valid = early0_EnvPlugin_logic_flushPort_valid;
  assign WhiteboxerPlugin_logic_reschedules_flushes_3_payload_uopId = early0_EnvPlugin_logic_flushPort_payload_uopId;
  assign WhiteboxerPlugin_logic_reschedules_flushes_3_payload_self = early0_EnvPlugin_logic_flushPort_payload_self;
  assign WhiteboxerPlugin_logic_reschedules_flushes_4_valid = DecoderPlugin_logic_laneLogic_0_flushPort_valid;
  assign WhiteboxerPlugin_logic_reschedules_flushes_4_payload_uopId = DecoderPlugin_logic_laneLogic_0_flushPort_payload_uopId;
  assign WhiteboxerPlugin_logic_reschedules_flushes_4_payload_self = DecoderPlugin_logic_laneLogic_0_flushPort_payload_self;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_valid = early0_BranchPlugin_logic_jumpLogic_learn_valid;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_pcOnLastSlice = early0_BranchPlugin_logic_jumpLogic_learn_payload_pcOnLastSlice;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_pcTarget = early0_BranchPlugin_logic_jumpLogic_learn_payload_pcTarget;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_taken = early0_BranchPlugin_logic_jumpLogic_learn_payload_taken;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isBranch = early0_BranchPlugin_logic_jumpLogic_learn_payload_isBranch;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isPush = early0_BranchPlugin_logic_jumpLogic_learn_payload_isPush;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isPop = early0_BranchPlugin_logic_jumpLogic_learn_payload_isPop;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_wasWrong = early0_BranchPlugin_logic_jumpLogic_learn_payload_wasWrong;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_badPredictedTarget = early0_BranchPlugin_logic_jumpLogic_learn_payload_badPredictedTarget;
  assign early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_uopId = early0_BranchPlugin_logic_jumpLogic_learn_payload_uopId;
  assign WhiteboxerPlugin_logic_prediction_learns_0_valid = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_valid;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_pcOnLastSlice = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_pcOnLastSlice;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_pcTarget = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_pcTarget;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_taken = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_taken;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_isBranch = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isBranch;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_isPush = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isPush;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_isPop = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_isPop;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_wasWrong = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_wasWrong;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_badPredictedTarget = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_badPredictedTarget;
  assign WhiteboxerPlugin_logic_prediction_learns_0_payload_uopId = early0_BranchPlugin_logic_jumpLogic_learn_asFlow_payload_uopId;
  assign WhiteboxerPlugin_logic_loadExecute_fire = ((((((execute_ctrl4_down_LANE_SEL_lane0 && execute_ctrl4_down_isReady) && (! execute_lane0_ctrls_4_downIsCancel)) && execute_ctrl4_down_AguPlugin_SEL_lane0) && (execute_ctrl4_down_AguPlugin_LOAD_lane0 || execute_ctrl4_down_AguPlugin_EXECUTE_lane0)) && (! execute_ctrl4_down_TRAP_lane0)) && (! execute_ctrl4_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io));
  assign WhiteboxerPlugin_logic_loadExecute_uopId = execute_ctrl4_down_Decode_UOP_ID_lane0;
  assign WhiteboxerPlugin_logic_loadExecute_size = execute_ctrl4_down_AguPlugin_SIZE_lane0;
  assign WhiteboxerPlugin_logic_loadExecute_address = execute_ctrl4_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  assign WhiteboxerPlugin_logic_loadExecute_data = lane0_IntFormatPlugin_logic_stages_1_wb_payload;
  assign WhiteboxerPlugin_logic_storeCommit_fire = ((LsuCachelessPlugin_logic_bus_cmd_fire && LsuCachelessPlugin_logic_bus_cmd_payload_write) && (! LsuCachelessPlugin_logic_bus_cmd_payload_io));
  assign WhiteboxerPlugin_logic_storeCommit_uopId = execute_ctrl2_down_Decode_UOP_ID_lane0;
  assign WhiteboxerPlugin_logic_storeCommit_size = LsuCachelessPlugin_logic_bus_cmd_payload_size;
  assign WhiteboxerPlugin_logic_storeCommit_address = LsuCachelessPlugin_logic_bus_cmd_payload_address;
  assign WhiteboxerPlugin_logic_storeCommit_data = LsuCachelessPlugin_logic_bus_cmd_payload_data;
  assign WhiteboxerPlugin_logic_storeCommit_storeId = execute_ctrl2_down_Decode_UOP_ID_lane0[11:0];
  assign WhiteboxerPlugin_logic_storeCommit_amo = LsuCachelessPlugin_logic_bus_cmd_payload_amoEnable;
  assign WhiteboxerPlugin_logic_storeConditional_fire = (((((execute_ctrl4_down_LANE_SEL_lane0 && execute_ctrl4_down_isReady) && (! execute_lane0_ctrls_4_downIsCancel)) && execute_ctrl4_down_AguPlugin_SEL_lane0) && (execute_ctrl4_down_AguPlugin_ATOMIC_lane0 && (! (execute_ctrl4_down_AguPlugin_LOAD_lane0 || execute_ctrl4_down_AguPlugin_EXECUTE_lane0)))) && (! execute_ctrl4_down_TRAP_lane0));
  assign WhiteboxerPlugin_logic_storeConditional_uopId = execute_ctrl4_down_Decode_UOP_ID_lane0;
  assign WhiteboxerPlugin_logic_storeConditional_miss = execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0;
  assign WhiteboxerPlugin_logic_storeBroadcast_fire = WhiteboxerPlugin_logic_storeCommit_fire;
  assign WhiteboxerPlugin_logic_storeBroadcast_storeId = WhiteboxerPlugin_logic_storeCommit_storeId;
  assign fetch_logic_flushes_0_doIt = (|{(DecoderPlugin_logic_laneLogic_0_flushPort_valid && 1'b1),{(early0_EnvPlugin_logic_flushPort_valid && 1'b1),{(CsrAccessPlugin_logic_flushPort_valid && 1'b1),{(early0_BranchPlugin_logic_flushPort_valid && 1'b1),(LsuCachelessPlugin_logic_flushPort_valid && 1'b1)}}}});
  assign fetch_logic_ctrls_1_forgetsSingleRequest_FetchPipelinePlugin_l50 = fetch_logic_flushes_0_doIt;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL = 1'b0;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD = 1'b0;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED = fetch_logic_ctrls_0_down_Fetch_WORD_PC;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT = 1'b0;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT = 1'b0;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ADDRESS_EXTENSION = 1'b0;
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_BYPASS_TRANSLATION = 1'b1;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_REFILL_lane0 = 1'b0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_HAZARD_lane0 = 1'b0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0 = execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_RAW_ADDRESS_lane0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_PAGE_FAULT_lane0 = 1'b0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ACCESS_FAULT_lane0 = 1'b0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_ADDRESS_EXTENSION_lane0 = 1'b0;
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_BYPASS_TRANSLATION_lane0 = 1'b1;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_REFILL = 1'b0;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_HAZARD = 1'b0;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_TRANSLATED = fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_PAGE_FAULT = 1'b0;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_ACCESS_FAULT = 1'b0;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_ADDRESS_EXTENSION = 1'b0;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress1_translationPort_keys_BYPASS_TRANSLATION = 1'b1;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_REFILL_lane0 = 1'b0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_HAZARD_lane0 = 1'b0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_TRANSLATED_lane0 = execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_PAGE_FAULT_lane0 = 1'b0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_ACCESS_FAULT_lane0 = 1'b0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_ADDRESS_EXTENSION_lane0 = 1'b0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress1_translationPort_keys_BYPASS_TRANSLATION_lane0 = 1'b1;
  assign PmpPlugin_logic_isMachine = ($signed(PrivilegedPlugin_logic_harts_0_privilege) == $signed(3'b011));
  assign PmpPlugin_logic_instructionShouldHit = (! PmpPlugin_logic_isMachine);
  assign PmpPlugin_logic_dataShouldHit = ((! PmpPlugin_logic_isMachine) || (PrivilegedPlugin_logic_harts_0_m_status_mprv && (PrivilegedPlugin_logic_harts_0_m_status_mpp != 2'b11)));
  assign FetchCachelessPlugin_logic_pmpPort_logic_dataShouldHitPort = (PmpPlugin_logic_dataShouldHit || 1'b0);
  assign FetchCachelessPlugin_logic_pmpPort_logic_torCmpAddress = (fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED >>> 4'd12);
  assign fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_pmpPort_logic_NEED_HIT = ((PmpPlugin_logic_instructionShouldHit && 1'b1) || (FetchCachelessPlugin_logic_pmpPort_logic_dataShouldHitPort && (1'b0 || 1'b0)));
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_pmpPort_ACCESS_FAULT = 1'b0;
  assign LsuCachelessPlugin_logic_pmpPort_logic_dataShouldHitPort = (PmpPlugin_logic_dataShouldHit || 1'b0);
  assign LsuCachelessPlugin_logic_pmpPort_logic_torCmpAddress = (execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0 >>> 4'd12);
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_pmpPort_logic_NEED_HIT_lane0 = ((PmpPlugin_logic_instructionShouldHit && (execute_ctrl2_down_AguPlugin_EXECUTE_lane0 && execute_ctrl2_down_AguPlugin_GUEST_lane0)) || (LsuCachelessPlugin_logic_pmpPort_logic_dataShouldHitPort && ((execute_ctrl2_down_AguPlugin_LOAD_lane0 || (execute_ctrl2_down_AguPlugin_EXECUTE_lane0 && execute_ctrl2_down_AguPlugin_GUEST_lane0)) || execute_ctrl2_down_AguPlugin_STORE_lane0)));
  assign execute_ctrl2_down_LsuCachelessPlugin_logic_pmpPort_ACCESS_FAULT_lane0 = 1'b0;
  assign FetchCachelessPlugin_pmaBuilder_addressBits = FetchCachelessPlugin_logic_onPma_port_cmd_address;
  assign FetchCachelessPlugin_pmaBuilder_onTransfers_0_addressHit = _zz_FetchCachelessPlugin_pmaBuilder_onTransfers_0_addressHit[0];
  assign FetchCachelessPlugin_pmaBuilder_onTransfers_0_argsHit = (|1'b1);
  assign FetchCachelessPlugin_pmaBuilder_onTransfers_0_hit = (FetchCachelessPlugin_pmaBuilder_onTransfers_0_argsHit && FetchCachelessPlugin_pmaBuilder_onTransfers_0_addressHit);
  assign FetchCachelessPlugin_pmaBuilder_executeFault = 1'b0;
  assign _zz_FetchCachelessPlugin_logic_onPma_port_rsp_fault = ((FetchCachelessPlugin_pmaBuilder_addressBits & 32'h80000000) == 32'h80000000);
  assign FetchCachelessPlugin_logic_onPma_port_rsp_fault = ((! ((|{_zz_FetchCachelessPlugin_logic_onPma_port_rsp_fault,((FetchCachelessPlugin_pmaBuilder_addressBits & 32'hf0000000) == 32'h10000000)}) && (|FetchCachelessPlugin_pmaBuilder_onTransfers_0_hit))) || FetchCachelessPlugin_pmaBuilder_executeFault);
  assign FetchCachelessPlugin_logic_onPma_port_rsp_io = (! _zz_FetchCachelessPlugin_logic_onPma_port_rsp_io[0]);
  assign integer_RegFilePlugin_logic_writeMerges_0_bus_valid = (|lane0_integer_WriteBackPlugin_logic_write_port_valid);
  assign integer_RegFilePlugin_logic_writeMerges_0_bus_address = lane0_integer_WriteBackPlugin_logic_write_port_address;
  assign integer_RegFilePlugin_logic_writeMerges_0_bus_data = lane0_integer_WriteBackPlugin_logic_write_port_data;
  assign integer_RegFilePlugin_logic_writeMerges_0_bus_uopId = lane0_integer_WriteBackPlugin_logic_write_port_uopId;
  assign execute_lane0_bypasser_integer_RS1_port_data = integer_RegFilePlugin_logic_regfile_fpga_io_reads_0_data;
  assign execute_lane0_bypasser_integer_RS2_port_data = integer_RegFilePlugin_logic_regfile_fpga_io_reads_1_data;
  always @(*) begin
    integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_valid = integer_RegFilePlugin_logic_writeMerges_0_bus_valid;
    if(when_RegFilePlugin_l132) begin
      integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_valid = 1'b1;
    end
  end

  always @(*) begin
    integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_address = integer_RegFilePlugin_logic_writeMerges_0_bus_address;
    if(when_RegFilePlugin_l132) begin
      integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_address = integer_RegFilePlugin_logic_initalizer_counter[4:0];
    end
  end

  always @(*) begin
    integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_data = integer_RegFilePlugin_logic_writeMerges_0_bus_data;
    if(when_RegFilePlugin_l132) begin
      integer_RegFilePlugin_logic_regfile_fpga_io_writes_0_data = 32'h0;
    end
  end

  assign integer_RegFilePlugin_logic_initalizer_done = integer_RegFilePlugin_logic_initalizer_counter[5];
  assign when_RegFilePlugin_l132 = (! integer_RegFilePlugin_logic_initalizer_done);
  assign integer_write_0_valid = integer_RegFilePlugin_logic_writeMerges_0_bus_valid;
  assign integer_write_0_address = integer_RegFilePlugin_logic_writeMerges_0_bus_address;
  assign integer_write_0_data = integer_RegFilePlugin_logic_writeMerges_0_bus_data;
  assign integer_write_0_uopId = integer_RegFilePlugin_logic_writeMerges_0_bus_uopId;
  assign execute_freeze_valid = (|{CsrAccessPlugin_logic_fsm_inject_freeze,{(execute_ctrl3_down_LsuCachelessPlugin_WITH_RSP_lane0 && (! LsuCachelessPlugin_logic_onJoin_rspValid)),{LsuCachelessPlugin_logic_onFork_freezeIt,early0_DivPlugin_logic_processing_freeze}}});
  assign execute_ctrl5_down_ready = (! execute_freeze_valid);
  assign TrapPlugin_logic_initHold = (|{(! CsrRamPlugin_logic_flush_done),(! integer_RegFilePlugin_logic_initalizer_done)});
  assign WhiteboxerPlugin_logic_wfi = TrapPlugin_logic_harts_0_trap_fsm_wfi;
  assign WhiteboxerPlugin_logic_perf_executeFreezed = execute_freeze_valid;
  assign WhiteboxerPlugin_logic_perf_dispatchHazards = (|(DispatchPlugin_logic_candidates_0_ctx_valid && (! DispatchPlugin_logic_candidates_0_fire)));
  assign WhiteboxerPlugin_logic_perf_candidatesCount = _zz_WhiteboxerPlugin_logic_perf_candidatesCount;
  assign WhiteboxerPlugin_logic_perf_dispatchFeedCount = _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCount;
  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter = 1'b0;
    if(WhiteboxerPlugin_logic_perf_executeFreezed) begin
      _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter = 1'b1;
    end
  end

  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1 = (_zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2 + _zz__zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1);
    if(1'b0) begin
      _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1 = 60'h0;
    end
  end

  assign WhiteboxerPlugin_logic_perf_executeFreezedCounter = _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2;
  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter = 1'b0;
    if(WhiteboxerPlugin_logic_perf_dispatchHazards) begin
      _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter = 1'b1;
    end
  end

  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1 = (_zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2 + _zz__zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1);
    if(1'b0) begin
      _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1 = 60'h0;
    end
  end

  assign WhiteboxerPlugin_logic_perf_dispatchHazardsCounter = _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2;
  assign when_Counter_l254 = (WhiteboxerPlugin_logic_perf_candidatesCount == 1'b0);
  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0 = 1'b0;
    if(when_Counter_l254) begin
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0 = 1'b1;
    end
  end

  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1 = (_zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_2 + _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1);
    if(1'b0) begin
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1 = 60'h0;
    end
  end

  assign WhiteboxerPlugin_logic_perf_candidatesCountCounters_0 = _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_2;
  assign when_Counter_l254_1 = (WhiteboxerPlugin_logic_perf_candidatesCount == 1'b1);
  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1 = 1'b0;
    if(when_Counter_l254_1) begin
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1 = 1'b1;
    end
  end

  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1 = (_zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_2 + _zz__zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1);
    if(1'b0) begin
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1 = 60'h0;
    end
  end

  assign WhiteboxerPlugin_logic_perf_candidatesCountCounters_1 = _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_2;
  assign when_Counter_l254_2 = (WhiteboxerPlugin_logic_perf_dispatchFeedCount == 1'b0);
  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0 = 1'b0;
    if(when_Counter_l254_2) begin
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0 = 1'b1;
    end
  end

  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1 = (_zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_2 + _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1);
    if(1'b0) begin
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1 = 60'h0;
    end
  end

  assign WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0 = _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_2;
  assign when_Counter_l254_3 = (WhiteboxerPlugin_logic_perf_dispatchFeedCount == 1'b1);
  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1 = 1'b0;
    if(when_Counter_l254_3) begin
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1 = 1'b1;
    end
  end

  always @(*) begin
    _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1 = (_zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_2 + _zz__zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1);
    if(1'b0) begin
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1 = 60'h0;
    end
  end

  assign WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1 = _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_2;
  assign WhiteboxerPlugin_logic_trap_ports_0_valid = TrapPlugin_logic_harts_0_trap_whitebox_trap;
  assign WhiteboxerPlugin_logic_trap_ports_0_interrupt = TrapPlugin_logic_harts_0_trap_whitebox_interrupt;
  assign WhiteboxerPlugin_logic_trap_ports_0_cause = TrapPlugin_logic_harts_0_trap_whitebox_code;
  assign fetch_logic_ctrls_1_up_forgetOne = (|fetch_logic_ctrls_1_forgetsSingleRequest_FetchPipelinePlugin_l50);
  assign fetch_logic_ctrls_1_up_cancel = (|fetch_logic_flushes_0_doIt);
  assign fetch_logic_ctrls_0_down_ready = fetch_logic_ctrls_1_up_ready;
  always @(*) begin
    fetch_logic_ctrls_0_down_valid = fetch_logic_ctrls_0_up_valid;
    if(when_CtrlLink_l191) begin
      fetch_logic_ctrls_0_down_valid = 1'b0;
    end
  end

  always @(*) begin
    fetch_logic_ctrls_0_up_ready = fetch_logic_ctrls_0_down_isReady;
    if(when_CtrlLink_l191) begin
      fetch_logic_ctrls_0_up_ready = 1'b0;
    end
  end

  assign when_CtrlLink_l191 = (|{fetch_logic_ctrls_0_haltRequest_CtrlLink_l112,fetch_logic_ctrls_0_haltRequest_PcPlugin_l133});
  assign fetch_logic_ctrls_0_down_Fetch_WORD_PC = fetch_logic_ctrls_0_up_Fetch_WORD_PC;
  assign fetch_logic_ctrls_0_down_Fetch_ID = fetch_logic_ctrls_0_up_Fetch_ID;
  always @(*) begin
    fetch_logic_ctrls_1_down_valid = fetch_logic_ctrls_1_up_valid;
    if(when_CtrlLink_l191_1) begin
      fetch_logic_ctrls_1_down_valid = 1'b0;
    end
  end

  always @(*) begin
    fetch_logic_ctrls_1_up_ready = fetch_logic_ctrls_1_down_isReady;
    if(when_CtrlLink_l191_1) begin
      fetch_logic_ctrls_1_up_ready = 1'b0;
    end
  end

  assign when_CtrlLink_l191_1 = (|fetch_logic_ctrls_1_haltRequest_FetchCachelessPlugin_l268);
  assign fetch_logic_ctrls_1_down_Fetch_WORD_PC = fetch_logic_ctrls_1_up_Fetch_WORD_PC;
  assign fetch_logic_ctrls_1_down_Fetch_ID = fetch_logic_ctrls_1_up_Fetch_ID;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED = fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_BUFFER_ID = fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_BUFFER_ID;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_fork_PMA_FAULT = fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_fork_PMA_FAULT;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD = fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL = fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT = fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT;
  assign fetch_logic_ctrls_1_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT = fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT;
  always @(*) begin
    decode_ctrls_0_down_ready = decode_ctrls_1_up_ready;
    if(when_StageLink_l71) begin
      decode_ctrls_0_down_ready = 1'b1;
    end
  end

  assign when_StageLink_l71 = (! decode_ctrls_1_up_isValid);
  assign when_DecodePipelinePlugin_l70 = ((! decode_ctrls_1_up_isReady) && decode_ctrls_1_lane0_upIsCancel);
  assign decode_ctrls_0_down_valid = decode_ctrls_0_up_valid;
  assign decode_ctrls_0_up_ready = decode_ctrls_0_down_isReady;
  assign decode_ctrls_0_down_Decode_INSTRUCTION_0 = decode_ctrls_0_up_Decode_INSTRUCTION_0;
  assign decode_ctrls_0_down_Decode_DECOMPRESSION_FAULT_0 = decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_0;
  assign decode_ctrls_0_down_Decode_INSTRUCTION_RAW_0 = decode_ctrls_0_up_Decode_INSTRUCTION_RAW_0;
  assign decode_ctrls_0_down_Decode_INSTRUCTION_SLICE_COUNT_0 = decode_ctrls_0_up_Decode_INSTRUCTION_SLICE_COUNT_0;
  assign decode_ctrls_0_down_PC_0 = decode_ctrls_0_up_PC_0;
  assign decode_ctrls_0_down_Decode_DOP_ID_0 = decode_ctrls_0_up_Decode_DOP_ID_0;
  assign decode_ctrls_0_down_Fetch_ID_0 = decode_ctrls_0_up_Fetch_ID_0;
  assign decode_ctrls_0_down_TRAP_0 = decode_ctrls_0_up_TRAP_0;
  assign decode_ctrls_1_down_valid = decode_ctrls_1_up_valid;
  assign decode_ctrls_1_up_ready = decode_ctrls_1_down_isReady;
  assign decode_ctrls_1_down_Decode_INSTRUCTION_0 = decode_ctrls_1_up_Decode_INSTRUCTION_0;
  assign decode_ctrls_1_down_Decode_DECOMPRESSION_FAULT_0 = decode_ctrls_1_up_Decode_DECOMPRESSION_FAULT_0;
  assign decode_ctrls_1_down_Decode_INSTRUCTION_RAW_0 = decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0;
  assign decode_ctrls_1_down_Decode_INSTRUCTION_SLICE_COUNT_0 = decode_ctrls_1_up_Decode_INSTRUCTION_SLICE_COUNT_0;
  assign decode_ctrls_1_down_PC_0 = decode_ctrls_1_up_PC_0;
  assign decode_ctrls_1_down_Decode_DOP_ID_0 = decode_ctrls_1_up_Decode_DOP_ID_0;
  assign execute_ctrl0_down_ready = execute_ctrl1_up_ready;
  assign execute_ctrl1_down_ready = execute_ctrl2_up_ready;
  assign execute_ctrl2_down_ready = execute_ctrl3_up_ready;
  assign execute_ctrl3_down_ready = execute_ctrl4_up_ready;
  assign execute_ctrl4_down_ready = execute_ctrl5_up_ready;
  assign execute_ctrl0_up_ready = execute_ctrl0_down_isReady;
  assign execute_ctrl0_down_Decode_UOP_lane0 = execute_ctrl0_up_Decode_UOP_lane0;
  assign execute_ctrl0_down_Decode_INSTRUCTION_SLICE_COUNT_lane0 = execute_ctrl0_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  assign execute_ctrl0_down_PC_lane0 = execute_ctrl0_up_PC_lane0;
  assign execute_ctrl0_down_TRAP_lane0 = execute_ctrl0_up_TRAP_lane0;
  assign execute_ctrl0_down_Decode_UOP_ID_lane0 = execute_ctrl0_up_Decode_UOP_ID_lane0;
  assign execute_ctrl0_down_RS1_PHYS_lane0 = execute_ctrl0_up_RS1_PHYS_lane0;
  assign execute_ctrl0_down_RS2_PHYS_lane0 = execute_ctrl0_up_RS2_PHYS_lane0;
  assign execute_ctrl0_down_RD_PHYS_lane0 = execute_ctrl0_up_RD_PHYS_lane0;
  assign execute_ctrl0_down_COMPLETED_lane0 = execute_ctrl0_up_COMPLETED_lane0;
  assign execute_ctrl1_up_ready = execute_ctrl1_down_isReady;
  assign execute_ctrl1_down_Decode_UOP_lane0 = execute_ctrl1_up_Decode_UOP_lane0;
  assign execute_ctrl1_down_Decode_INSTRUCTION_SLICE_COUNT_lane0 = execute_ctrl1_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  assign execute_ctrl1_down_PC_lane0 = execute_ctrl1_up_PC_lane0;
  assign execute_ctrl1_down_TRAP_lane0 = execute_ctrl1_up_TRAP_lane0;
  assign execute_ctrl1_down_Decode_UOP_ID_lane0 = execute_ctrl1_up_Decode_UOP_ID_lane0;
  assign execute_ctrl1_down_RS1_PHYS_lane0 = execute_ctrl1_up_RS1_PHYS_lane0;
  assign execute_ctrl1_down_RS2_PHYS_lane0 = execute_ctrl1_up_RS2_PHYS_lane0;
  assign execute_ctrl1_down_RD_PHYS_lane0 = execute_ctrl1_up_RD_PHYS_lane0;
  assign execute_ctrl1_down_COMPLETED_lane0 = execute_ctrl1_up_COMPLETED_lane0;
  assign execute_ctrl1_down_AguPlugin_SIZE_lane0 = execute_ctrl1_up_AguPlugin_SIZE_lane0;
  assign execute_ctrl2_up_ready = execute_ctrl2_down_isReady;
  assign execute_ctrl2_down_Decode_UOP_lane0 = execute_ctrl2_up_Decode_UOP_lane0;
  assign execute_ctrl2_down_Decode_INSTRUCTION_SLICE_COUNT_lane0 = execute_ctrl2_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  assign execute_ctrl2_down_PC_lane0 = execute_ctrl2_up_PC_lane0;
  assign execute_ctrl2_down_Decode_UOP_ID_lane0 = execute_ctrl2_up_Decode_UOP_ID_lane0;
  assign execute_ctrl2_down_RD_PHYS_lane0 = execute_ctrl2_up_RD_PHYS_lane0;
  assign execute_ctrl2_down_early0_SrcPlugin_SRC1_lane0 = execute_ctrl2_up_early0_SrcPlugin_SRC1_lane0;
  assign execute_ctrl2_down_early0_SrcPlugin_SRC2_lane0 = execute_ctrl2_up_early0_SrcPlugin_SRC2_lane0;
  assign execute_ctrl2_down_early0_IntAluPlugin_SEL_lane0 = execute_ctrl2_up_early0_IntAluPlugin_SEL_lane0;
  assign execute_ctrl2_down_early0_BarrelShifterPlugin_SEL_lane0 = execute_ctrl2_up_early0_BarrelShifterPlugin_SEL_lane0;
  assign execute_ctrl2_down_early0_BranchPlugin_SEL_lane0 = execute_ctrl2_up_early0_BranchPlugin_SEL_lane0;
  assign execute_ctrl2_down_early0_MulPlugin_SEL_lane0 = execute_ctrl2_up_early0_MulPlugin_SEL_lane0;
  assign execute_ctrl2_down_early0_DivPlugin_SEL_lane0 = execute_ctrl2_up_early0_DivPlugin_SEL_lane0;
  assign execute_ctrl2_down_early0_EnvPlugin_SEL_lane0 = execute_ctrl2_up_early0_EnvPlugin_SEL_lane0;
  assign execute_ctrl2_down_CsrAccessPlugin_SEL_lane0 = execute_ctrl2_up_CsrAccessPlugin_SEL_lane0;
  assign execute_ctrl2_down_AguPlugin_SEL_lane0 = execute_ctrl2_up_AguPlugin_SEL_lane0;
  assign execute_ctrl2_down_LsuCachelessPlugin_FENCE_lane0 = execute_ctrl2_up_LsuCachelessPlugin_FENCE_lane0;
  assign execute_ctrl2_down_lane0_integer_WriteBackPlugin_SEL_lane0 = execute_ctrl2_up_lane0_integer_WriteBackPlugin_SEL_lane0;
  assign execute_ctrl2_down_COMPLETION_AT_2_lane0 = execute_ctrl2_up_COMPLETION_AT_2_lane0;
  assign execute_ctrl2_down_COMPLETION_AT_4_lane0 = execute_ctrl2_up_COMPLETION_AT_4_lane0;
  assign execute_ctrl2_down_COMPLETION_AT_3_lane0 = execute_ctrl2_up_COMPLETION_AT_3_lane0;
  assign execute_ctrl2_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0 = execute_ctrl2_up_lane0_logic_completions_onCtrl_0_ENABLE_lane0;
  assign execute_ctrl2_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0 = execute_ctrl2_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  assign execute_ctrl2_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0 = execute_ctrl2_up_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  assign execute_ctrl2_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0 = execute_ctrl2_up_early0_IntAluPlugin_ALU_ADD_SUB_lane0;
  assign execute_ctrl2_down_early0_IntAluPlugin_ALU_SLTX_lane0 = execute_ctrl2_up_early0_IntAluPlugin_ALU_SLTX_lane0;
  assign execute_ctrl2_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0 = execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0;
  assign execute_ctrl2_down_SrcStageables_REVERT_lane0 = execute_ctrl2_up_SrcStageables_REVERT_lane0;
  assign execute_ctrl2_down_SrcStageables_ZERO_lane0 = execute_ctrl2_up_SrcStageables_ZERO_lane0;
  assign execute_ctrl2_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 = execute_ctrl2_up_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  assign execute_ctrl2_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 = execute_ctrl2_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  assign execute_ctrl2_down_BYPASSED_AT_2_lane0 = execute_ctrl2_up_BYPASSED_AT_2_lane0;
  assign execute_ctrl2_down_BYPASSED_AT_3_lane0 = execute_ctrl2_up_BYPASSED_AT_3_lane0;
  assign execute_ctrl2_down_BYPASSED_AT_4_lane0 = execute_ctrl2_up_BYPASSED_AT_4_lane0;
  assign execute_ctrl2_down_SrcStageables_UNSIGNED_lane0 = execute_ctrl2_up_SrcStageables_UNSIGNED_lane0;
  assign execute_ctrl2_down_BarrelShifterPlugin_LEFT_lane0 = execute_ctrl2_up_BarrelShifterPlugin_LEFT_lane0;
  assign execute_ctrl2_down_BarrelShifterPlugin_SIGNED_lane0 = execute_ctrl2_up_BarrelShifterPlugin_SIGNED_lane0;
  assign execute_ctrl2_down_BranchPlugin_BRANCH_CTRL_lane0 = execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0;
  assign execute_ctrl2_down_MulPlugin_HIGH_lane0 = execute_ctrl2_up_MulPlugin_HIGH_lane0;
  assign execute_ctrl2_down_RsUnsignedPlugin_RS1_SIGNED_lane0 = execute_ctrl2_up_RsUnsignedPlugin_RS1_SIGNED_lane0;
  assign execute_ctrl2_down_RsUnsignedPlugin_RS2_SIGNED_lane0 = execute_ctrl2_up_RsUnsignedPlugin_RS2_SIGNED_lane0;
  assign execute_ctrl2_down_DivPlugin_REM_lane0 = execute_ctrl2_up_DivPlugin_REM_lane0;
  assign execute_ctrl2_down_CsrAccessPlugin_CSR_IMM_lane0 = execute_ctrl2_up_CsrAccessPlugin_CSR_IMM_lane0;
  assign execute_ctrl2_down_CsrAccessPlugin_CSR_MASK_lane0 = execute_ctrl2_up_CsrAccessPlugin_CSR_MASK_lane0;
  assign execute_ctrl2_down_CsrAccessPlugin_CSR_CLEAR_lane0 = execute_ctrl2_up_CsrAccessPlugin_CSR_CLEAR_lane0;
  assign execute_ctrl2_down_AguPlugin_LOAD_lane0 = execute_ctrl2_up_AguPlugin_LOAD_lane0;
  assign execute_ctrl2_down_AguPlugin_STORE_lane0 = execute_ctrl2_up_AguPlugin_STORE_lane0;
  assign execute_ctrl2_down_AguPlugin_EXECUTE_lane0 = execute_ctrl2_up_AguPlugin_EXECUTE_lane0;
  assign execute_ctrl2_down_AguPlugin_ATOMIC_lane0 = execute_ctrl2_up_AguPlugin_ATOMIC_lane0;
  assign execute_ctrl2_down_AguPlugin_FLOAT_lane0 = execute_ctrl2_up_AguPlugin_FLOAT_lane0;
  assign execute_ctrl2_down_AguPlugin_GUEST_lane0 = execute_ctrl2_up_AguPlugin_GUEST_lane0;
  assign execute_ctrl2_down_early0_EnvPlugin_OP_lane0 = execute_ctrl2_up_early0_EnvPlugin_OP_lane0;
  assign execute_ctrl3_up_ready = execute_ctrl3_down_isReady;
  assign execute_ctrl3_down_LANE_SEL_lane0 = execute_ctrl3_up_LANE_SEL_lane0;
  assign execute_ctrl3_down_Decode_UOP_lane0 = execute_ctrl3_up_Decode_UOP_lane0;
  assign execute_ctrl3_down_Decode_INSTRUCTION_SLICE_COUNT_lane0 = execute_ctrl3_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  assign execute_ctrl3_down_PC_lane0 = execute_ctrl3_up_PC_lane0;
  assign execute_ctrl3_down_TRAP_lane0 = execute_ctrl3_up_TRAP_lane0;
  assign execute_ctrl3_down_Decode_UOP_ID_lane0 = execute_ctrl3_up_Decode_UOP_ID_lane0;
  assign execute_ctrl3_down_RD_ENABLE_lane0 = execute_ctrl3_up_RD_ENABLE_lane0;
  assign execute_ctrl3_down_RD_PHYS_lane0 = execute_ctrl3_up_RD_PHYS_lane0;
  assign execute_ctrl3_down_AguPlugin_SIZE_lane0 = execute_ctrl3_up_AguPlugin_SIZE_lane0;
  assign execute_ctrl3_down_early0_MulPlugin_SEL_lane0 = execute_ctrl3_up_early0_MulPlugin_SEL_lane0;
  assign execute_ctrl3_down_early0_DivPlugin_SEL_lane0 = execute_ctrl3_up_early0_DivPlugin_SEL_lane0;
  assign execute_ctrl3_down_CsrAccessPlugin_SEL_lane0 = execute_ctrl3_up_CsrAccessPlugin_SEL_lane0;
  assign execute_ctrl3_down_AguPlugin_SEL_lane0 = execute_ctrl3_up_AguPlugin_SEL_lane0;
  assign execute_ctrl3_down_lane0_integer_WriteBackPlugin_SEL_lane0 = execute_ctrl3_up_lane0_integer_WriteBackPlugin_SEL_lane0;
  assign execute_ctrl3_down_COMPLETION_AT_4_lane0 = execute_ctrl3_up_COMPLETION_AT_4_lane0;
  assign execute_ctrl3_down_COMPLETION_AT_3_lane0 = execute_ctrl3_up_COMPLETION_AT_3_lane0;
  assign execute_ctrl3_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0 = execute_ctrl3_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  assign execute_ctrl3_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0 = execute_ctrl3_up_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
  assign execute_ctrl3_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 = execute_ctrl3_up_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  assign execute_ctrl3_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 = execute_ctrl3_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  assign execute_ctrl3_down_BYPASSED_AT_3_lane0 = execute_ctrl3_up_BYPASSED_AT_3_lane0;
  assign execute_ctrl3_down_BYPASSED_AT_4_lane0 = execute_ctrl3_up_BYPASSED_AT_4_lane0;
  assign execute_ctrl3_down_MulPlugin_HIGH_lane0 = execute_ctrl3_up_MulPlugin_HIGH_lane0;
  assign execute_ctrl3_down_AguPlugin_LOAD_lane0 = execute_ctrl3_up_AguPlugin_LOAD_lane0;
  assign execute_ctrl3_down_AguPlugin_STORE_lane0 = execute_ctrl3_up_AguPlugin_STORE_lane0;
  assign execute_ctrl3_down_AguPlugin_EXECUTE_lane0 = execute_ctrl3_up_AguPlugin_EXECUTE_lane0;
  assign execute_ctrl3_down_AguPlugin_ATOMIC_lane0 = execute_ctrl3_up_AguPlugin_ATOMIC_lane0;
  assign execute_ctrl3_down_AguPlugin_FLOAT_lane0 = execute_ctrl3_up_AguPlugin_FLOAT_lane0;
  assign execute_ctrl3_down_COMMIT_lane0 = execute_ctrl3_up_COMMIT_lane0;
  assign execute_ctrl3_down_early0_SrcPlugin_ADD_SUB_lane0 = execute_ctrl3_up_early0_SrcPlugin_ADD_SUB_lane0;
  assign execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_0_lane0 = execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_0_lane0;
  assign execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_1_lane0 = execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  assign execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_2_lane0 = execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  assign execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_3_lane0 = execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  assign execute_ctrl3_down_DivPlugin_DIV_RESULT_lane0 = execute_ctrl3_up_DivPlugin_DIV_RESULT_lane0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0 = execute_ctrl3_up_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault = execute_ctrl3_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
  assign execute_ctrl3_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io = execute_ctrl3_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  assign execute_ctrl3_down_LsuCachelessPlugin_WITH_RSP_lane0 = execute_ctrl3_up_LsuCachelessPlugin_WITH_RSP_lane0;
  assign execute_ctrl4_up_ready = execute_ctrl4_down_isReady;
  assign execute_ctrl4_down_LANE_SEL_lane0 = execute_ctrl4_up_LANE_SEL_lane0;
  assign execute_ctrl4_down_Decode_UOP_lane0 = execute_ctrl4_up_Decode_UOP_lane0;
  assign execute_ctrl4_down_Decode_INSTRUCTION_SLICE_COUNT_lane0 = execute_ctrl4_up_Decode_INSTRUCTION_SLICE_COUNT_lane0;
  assign execute_ctrl4_down_PC_lane0 = execute_ctrl4_up_PC_lane0;
  assign execute_ctrl4_down_TRAP_lane0 = execute_ctrl4_up_TRAP_lane0;
  assign execute_ctrl4_down_Decode_UOP_ID_lane0 = execute_ctrl4_up_Decode_UOP_ID_lane0;
  assign execute_ctrl4_down_RD_ENABLE_lane0 = execute_ctrl4_up_RD_ENABLE_lane0;
  assign execute_ctrl4_down_RD_PHYS_lane0 = execute_ctrl4_up_RD_PHYS_lane0;
  assign execute_ctrl4_down_AguPlugin_SIZE_lane0 = execute_ctrl4_up_AguPlugin_SIZE_lane0;
  assign execute_ctrl4_down_early0_MulPlugin_SEL_lane0 = execute_ctrl4_up_early0_MulPlugin_SEL_lane0;
  assign execute_ctrl4_down_AguPlugin_SEL_lane0 = execute_ctrl4_up_AguPlugin_SEL_lane0;
  assign execute_ctrl4_down_lane0_integer_WriteBackPlugin_SEL_lane0 = execute_ctrl4_up_lane0_integer_WriteBackPlugin_SEL_lane0;
  assign execute_ctrl4_down_COMPLETION_AT_4_lane0 = execute_ctrl4_up_COMPLETION_AT_4_lane0;
  assign execute_ctrl4_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0 = execute_ctrl4_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
  assign execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 = execute_ctrl4_up_lane0_IntFormatPlugin_logic_SIGNED_lane0;
  assign execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 = execute_ctrl4_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
  assign execute_ctrl4_down_BYPASSED_AT_4_lane0 = execute_ctrl4_up_BYPASSED_AT_4_lane0;
  assign execute_ctrl4_down_MulPlugin_HIGH_lane0 = execute_ctrl4_up_MulPlugin_HIGH_lane0;
  assign execute_ctrl4_down_AguPlugin_LOAD_lane0 = execute_ctrl4_up_AguPlugin_LOAD_lane0;
  assign execute_ctrl4_down_AguPlugin_EXECUTE_lane0 = execute_ctrl4_up_AguPlugin_EXECUTE_lane0;
  assign execute_ctrl4_down_AguPlugin_ATOMIC_lane0 = execute_ctrl4_up_AguPlugin_ATOMIC_lane0;
  assign execute_ctrl4_down_AguPlugin_FLOAT_lane0 = execute_ctrl4_up_AguPlugin_FLOAT_lane0;
  assign execute_ctrl4_down_COMMIT_lane0 = execute_ctrl4_up_COMMIT_lane0;
  assign execute_ctrl4_down_early0_SrcPlugin_ADD_SUB_lane0 = execute_ctrl4_up_early0_SrcPlugin_ADD_SUB_lane0;
  assign execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_0_lane0 = execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_0_lane0;
  assign execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_1_lane0 = execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_1_lane0;
  assign execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_2_lane0 = execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_2_lane0;
  assign execute_ctrl4_down_early0_MulPlugin_logic_mul_VALUES_3_lane0 = execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_3_lane0;
  assign execute_ctrl4_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0 = execute_ctrl4_up_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
  assign execute_ctrl4_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault = execute_ctrl4_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
  assign execute_ctrl4_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io = execute_ctrl4_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
  assign execute_ctrl4_down_early0_MulPlugin_logic_steps_0_adders_0_lane0 = execute_ctrl4_up_early0_MulPlugin_logic_steps_0_adders_0_lane0;
  assign execute_ctrl4_down_early0_MulPlugin_logic_steps_0_adders_1_lane0 = execute_ctrl4_up_early0_MulPlugin_logic_steps_0_adders_1_lane0;
  assign execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0 = execute_ctrl4_up_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0;
  assign execute_ctrl4_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0 = execute_ctrl4_up_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0;
  assign execute_ctrl5_up_ready = execute_ctrl5_down_isReady;
  assign execute_ctrl5_down_LANE_SEL_lane0 = execute_ctrl5_up_LANE_SEL_lane0;
  assign execute_ctrl5_down_RD_PHYS_lane0 = execute_ctrl5_up_RD_PHYS_lane0;
  assign execute_ctrl5_down_COMMIT_lane0 = execute_ctrl5_up_COMMIT_lane0;
  assign execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 = execute_ctrl5_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
  assign fetch_logic_ctrls_0_down_isFiring = (fetch_logic_ctrls_0_down_isValid && fetch_logic_ctrls_0_down_isReady);
  assign fetch_logic_ctrls_0_down_isValid = fetch_logic_ctrls_0_down_valid;
  assign fetch_logic_ctrls_0_down_isReady = fetch_logic_ctrls_0_down_ready;
  assign fetch_logic_ctrls_1_up_isValid = fetch_logic_ctrls_1_up_valid;
  assign fetch_logic_ctrls_1_up_isCancel = fetch_logic_ctrls_1_up_cancel;
  assign fetch_logic_ctrls_0_up_isFiring = (fetch_logic_ctrls_0_up_isValid && fetch_logic_ctrls_0_up_isReady);
  assign fetch_logic_ctrls_0_up_isMoving = (fetch_logic_ctrls_0_up_isValid && fetch_logic_ctrls_0_up_isReady);
  assign fetch_logic_ctrls_0_up_isValid = fetch_logic_ctrls_0_up_valid;
  assign fetch_logic_ctrls_0_up_isReady = fetch_logic_ctrls_0_up_ready;
  assign fetch_logic_ctrls_1_down_isValid = fetch_logic_ctrls_1_down_valid;
  assign fetch_logic_ctrls_1_down_isReady = fetch_logic_ctrls_1_down_ready;
  assign fetch_logic_ctrls_1_down_isCancel = 1'b0;
  assign decode_ctrls_0_down_isValid = decode_ctrls_0_down_valid;
  assign decode_ctrls_0_down_isReady = decode_ctrls_0_down_ready;
  assign decode_ctrls_1_up_isMoving = (decode_ctrls_1_up_isValid && decode_ctrls_1_up_isReady);
  assign decode_ctrls_1_up_isValid = decode_ctrls_1_up_valid;
  assign decode_ctrls_1_up_isReady = decode_ctrls_1_up_ready;
  assign decode_ctrls_1_up_isCanceling = 1'b0;
  assign decode_ctrls_0_up_isFiring = (decode_ctrls_0_up_isValid && decode_ctrls_0_up_isReady);
  assign decode_ctrls_0_up_isMoving = (decode_ctrls_0_up_isValid && decode_ctrls_0_up_isReady);
  assign decode_ctrls_0_up_isValid = decode_ctrls_0_up_valid;
  assign decode_ctrls_0_up_isReady = decode_ctrls_0_up_ready;
  assign decode_ctrls_0_up_isCancel = 1'b0;
  assign decode_ctrls_1_down_isReady = decode_ctrls_1_down_ready;
  assign execute_ctrl0_down_isReady = execute_ctrl0_down_ready;
  assign execute_ctrl1_down_isReady = execute_ctrl1_down_ready;
  assign execute_ctrl2_down_isReady = execute_ctrl2_down_ready;
  assign execute_ctrl3_down_isReady = execute_ctrl3_down_ready;
  assign execute_ctrl4_down_isReady = execute_ctrl4_down_ready;
  assign execute_ctrl5_down_isReady = execute_ctrl5_down_ready;
  always @(*) begin
    PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_stateReg;
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
        if(PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid) begin
          PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_CSR_WRITE;
        end else begin
          if(PerformanceCounterPlugin_logic_fsm_flusherCmd_valid) begin
            PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_READ_LOW;
          end else begin
            if(PerformanceCounterPlugin_logic_fsm_csrReadCmd_valid) begin
              PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_READ_LOW;
            end
          end
        end
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
        if(PerformanceCounterPlugin_logic_readPort_ready) begin
          PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_CALC_LOW;
        end
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
        if(PerformanceCounterPlugin_logic_writePort_ready) begin
          PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_IDLE;
          if(when_PerformanceCounterPlugin_l290) begin
            PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_READ_HIGH;
          end
        end
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
        if(PerformanceCounterPlugin_logic_readPort_ready) begin
          PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_CALC_HIGH;
        end
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
        if(PerformanceCounterPlugin_logic_writePort_ready) begin
          PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_IDLE;
        end
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
        PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_IDLE;
      end
      default : begin
      end
    endcase
    if(PerformanceCounterPlugin_logic_fsm_wantStart) begin
      PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_IDLE;
    end
    if(PerformanceCounterPlugin_logic_fsm_wantKill) begin
      PerformanceCounterPlugin_logic_fsm_stateNext = PerformanceCounterPlugin_logic_fsm_BOOT;
    end
  end

  assign when_PerformanceCounterPlugin_l283 = PerformanceCounterPlugin_logic_fsm_counterReaded[7];
  assign when_PerformanceCounterPlugin_l290 = PerformanceCounterPlugin_logic_fsm_calc_sum[32];
  assign when_PerformanceCounterPlugin_l261 = (CsrAccessPlugin_bus_write_address[7] == 1'b0);
  assign when_PerformanceCounterPlugin_l267 = PerformanceCounterPlugin_logic_fsm_cmd_oh[1];
  assign PerformanceCounterPlugin_logic_fsm_done = (PerformanceCounterPlugin_logic_fsm_stateReg == PerformanceCounterPlugin_logic_fsm_IDLE);
  always @(*) begin
    TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_stateReg;
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
        if(TrapPlugin_logic_harts_0_trap_trigger_valid) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_COMPUTE;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
        if(when_TrapPlugin_l547) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL;
        end else begin
          case(TrapPlugin_logic_harts_0_trap_pending_state_code)
            5'h0 : begin
              TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_RUNNING;
            end
            5'h01 : begin
              if(when_TrapPlugin_l609) begin
                TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC;
              end
            end
            5'h02 : begin
              TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_JUMP;
            end
            5'h04 : begin
              TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_JUMP;
            end
            5'h05 : begin
              TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_JUMP;
            end
            5'h08 : begin
              if(TrapPlugin_api_harts_0_askWake) begin
                TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_JUMP;
              end
            end
            5'h06 : begin
              TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_JUMP;
            end
            5'h09 : begin
              TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_JUMP;
            end
            default : begin
            end
          endcase
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
        if(TrapPlugin_logic_harts_0_crsPorts_write_ready) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
        if(TrapPlugin_logic_harts_0_crsPorts_write_ready) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
        if(TrapPlugin_logic_harts_0_crsPorts_read_ready) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
        if(when_TrapPlugin_l856) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
        TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_RUNNING;
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
        if(TrapPlugin_logic_harts_0_crsPorts_read_ready) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY;
        end
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
        TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_RUNNING;
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
        TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_RUNNING;
      end
      default : begin
        if(when_TrapPlugin_l478) begin
          TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_RUNNING;
        end
      end
    endcase
    if(TrapPlugin_logic_harts_0_trap_fsm_wantKill) begin
      TrapPlugin_logic_harts_0_trap_fsm_stateNext = TrapPlugin_logic_harts_0_trap_fsm_RESET;
    end
  end

  assign when_TrapPlugin_l547 = ((TrapPlugin_logic_harts_0_trap_pending_state_exception || TrapPlugin_logic_harts_0_trap_fsm_triggerEbreak) || TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt);
  assign when_TrapPlugin_l609 = (! TrapPlugin_api_harts_0_holdPrivChange);
  always @(*) begin
    _zz_TrapPlugin_logic_harts_0_crsPorts_write_address = 3'b101;
    case(TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege)
      3'b011 : begin
        _zz_TrapPlugin_logic_harts_0_crsPorts_write_address = 3'b101;
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    _zz_TrapPlugin_logic_harts_0_crsPorts_write_address_1 = 3'b110;
    case(TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege)
      3'b011 : begin
        _zz_TrapPlugin_logic_harts_0_crsPorts_write_address_1 = 3'b110;
      end
      default : begin
      end
    endcase
  end

  always @(*) begin
    _zz_TrapPlugin_logic_harts_0_crsPorts_read_address = 3'b111;
    case(TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege)
      3'b011 : begin
        _zz_TrapPlugin_logic_harts_0_crsPorts_read_address = 3'b111;
      end
      default : begin
      end
    endcase
  end

  assign when_TrapPlugin_l856 = (! TrapPlugin_api_harts_0_holdPrivChange);
  always @(*) begin
    _zz_TrapPlugin_logic_harts_0_crsPorts_read_address_1 = 3'b101;
    case(TrapPlugin_logic_harts_0_trap_fsm_xretPrivilege)
      3'b011 : begin
        _zz_TrapPlugin_logic_harts_0_crsPorts_read_address_1 = 3'b101;
      end
      default : begin
      end
    endcase
  end

  assign when_TrapPlugin_l975 = ($signed(TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilege) != $signed(3'b011));
  assign switch_TrapPlugin_l976 = TrapPlugin_logic_harts_0_trap_pending_state_arg[2 : 0];
  assign when_TrapPlugin_l478 = (&TrapPlugin_logic_harts_0_trap_fsm_resetToRunConditions_0);
  always @(*) begin
    CsrAccessPlugin_logic_fsm_stateNext = CsrAccessPlugin_logic_fsm_stateReg;
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
        if(when_CsrAccessPlugin_l325) begin
          CsrAccessPlugin_logic_fsm_stateNext = CsrAccessPlugin_logic_fsm_WRITE;
        end
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
        if(when_CsrAccessPlugin_l354) begin
          CsrAccessPlugin_logic_fsm_stateNext = CsrAccessPlugin_logic_fsm_COMPLETION;
        end
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
        if(execute_ctrl2_down_isReady) begin
          CsrAccessPlugin_logic_fsm_stateNext = CsrAccessPlugin_logic_fsm_IDLE;
        end
      end
      default : begin
        if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
          if(when_CsrAccessPlugin_l237) begin
            if(when_CsrAccessPlugin_l238) begin
              CsrAccessPlugin_logic_fsm_stateNext = CsrAccessPlugin_logic_fsm_READ;
            end
          end
          if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
            if(!CsrAccessPlugin_logic_fsm_inject_trapReg) begin
              if(when_CsrAccessPlugin_l250) begin
                CsrAccessPlugin_logic_fsm_stateNext = CsrAccessPlugin_logic_fsm_READ;
              end
            end
          end
        end
      end
    endcase
    if(CsrAccessPlugin_logic_fsm_wantKill) begin
      CsrAccessPlugin_logic_fsm_stateNext = CsrAccessPlugin_logic_fsm_IDLE;
    end
  end

  assign when_CsrAccessPlugin_l325 = (! CsrAccessPlugin_bus_read_halt);
  assign when_CsrAccessPlugin_l354 = (! CsrAccessPlugin_bus_write_halt);
  assign when_CsrAccessPlugin_l237 = ((! CsrAccessPlugin_logic_fsm_inject_trap) && (! CsrAccessPlugin_bus_decode_trap));
  assign when_CsrAccessPlugin_l238 = (! CsrAccessPlugin_bus_decode_fence);
  assign when_CsrAccessPlugin_l250 = (! CsrAccessPlugin_bus_decode_fence);
  always @(posedge clk) begin
    early0_DivPlugin_logic_processing_divRevertResult <= ((execute_ctrl2_down_RsUnsignedPlugin_RS1_REVERT_lane0 ^ (execute_ctrl2_down_RsUnsignedPlugin_RS2_REVERT_lane0 && (! execute_ctrl2_down_DivPlugin_REM_lane0))) && (! (((execute_ctrl2_down_RsUnsignedPlugin_RS2_FORMATED_lane0 == 32'h0) && execute_ctrl2_down_RsUnsignedPlugin_RS2_SIGNED_lane0) && (! execute_ctrl2_down_DivPlugin_REM_lane0))));
    if(when_AlignerPlugin_l256) begin
      AlignerPlugin_logic_buffer_data <= fetch_logic_ctrls_1_down_Fetch_WORD;
      AlignerPlugin_logic_buffer_pc <= fetch_logic_ctrls_1_down_Fetch_WORD_PC;
      AlignerPlugin_logic_buffer_hm_Fetch_ID <= fetch_logic_ctrls_1_down_Fetch_ID;
    end
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_id <= LsuCachelessPlugin_logic_bus_cmd_payload_id;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_write <= LsuCachelessPlugin_logic_bus_cmd_payload_write;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_address <= LsuCachelessPlugin_logic_bus_cmd_payload_address;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_data <= LsuCachelessPlugin_logic_bus_cmd_payload_data;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_size <= LsuCachelessPlugin_logic_bus_cmd_payload_size;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_mask <= LsuCachelessPlugin_logic_bus_cmd_payload_mask;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoEnable <= LsuCachelessPlugin_logic_bus_cmd_payload_amoEnable;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoOp <= LsuCachelessPlugin_logic_bus_cmd_payload_amoOp;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_io <= LsuCachelessPlugin_logic_bus_cmd_payload_io;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_fromHart <= LsuCachelessPlugin_logic_bus_cmd_payload_fromHart;
    LsuCachelessPlugin_logic_bus_cmd_payload_regNext_uopId <= LsuCachelessPlugin_logic_bus_cmd_payload_uopId;
    if(LsuCachelessPlugin_logic_bus_cmd_fire) begin
      case(LsuCachelessPlugin_logic_bus_cmd_payload_id)
        1'b0 : begin
          LsuCachelessPlugin_logic_onJoin_buffers_0_address <= LsuCachelessPlugin_logic_bus_cmd_payload_address;
        end
        default : begin
          LsuCachelessPlugin_logic_onJoin_buffers_1_address <= LsuCachelessPlugin_logic_bus_cmd_payload_address;
        end
      endcase
    end
    if(LsuCachelessPlugin_logic_bus_rsp_valid) begin
      case(LsuCachelessPlugin_logic_bus_rsp_payload_id)
        1'b0 : begin
          LsuCachelessPlugin_logic_onJoin_buffers_0_payload_error <= LsuCachelessPlugin_logic_onJoin_busRspWithoutId_error;
          LsuCachelessPlugin_logic_onJoin_buffers_0_payload_data <= LsuCachelessPlugin_logic_onJoin_busRspWithoutId_data;
          LsuCachelessPlugin_logic_onJoin_buffers_0_payload_scMiss <= LsuCachelessPlugin_logic_onJoin_busRspWithoutId_scMiss;
        end
        default : begin
          LsuCachelessPlugin_logic_onJoin_buffers_1_payload_error <= LsuCachelessPlugin_logic_onJoin_busRspWithoutId_error;
          LsuCachelessPlugin_logic_onJoin_buffers_1_payload_data <= LsuCachelessPlugin_logic_onJoin_busRspWithoutId_data;
          LsuCachelessPlugin_logic_onJoin_buffers_1_payload_scMiss <= LsuCachelessPlugin_logic_onJoin_busRspWithoutId_scMiss;
        end
      endcase
    end
    _zz_PerformanceCounterPlugin_logic_events_sums_0 <= early0_BranchPlugin_logic_events_branchMiss;
    _zz_PerformanceCounterPlugin_logic_events_sums_1 <= early0_BranchPlugin_logic_events_branchCount;
    _zz_PerformanceCounterPlugin_logic_events_sums_2 <= PerformanceCounterPlugin_logic_eventCycles;
    _zz_PerformanceCounterPlugin_logic_events_sums_3 <= PerformanceCounterPlugin_logic_eventInstructions_0;
    _zz_PerformanceCounterPlugin_logic_events_sums_4 <= DispatchPlugin_logic_events_frontendStall;
    _zz_PerformanceCounterPlugin_logic_events_sums_5 <= DispatchPlugin_logic_events_backendStall;
    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id <= (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_2 ? (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_0_id : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_1_id) : (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_1 ? TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_2_id : TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_interrupts_3_id));
    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority <= (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_2 ? _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority : _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_priority_1);
    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege <= (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_2 ? _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege : _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_privilege_1);
    TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid <= (_zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_id_2 ? _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid : _zz_TrapPlugin_logic_harts_0_interrupt_privilegeTriggers_0_triggered_valid_1);
    if(TrapPlugin_logic_harts_0_trap_pending_arbiter_down_valid) begin
      TrapPlugin_logic_harts_0_trap_pending_state_exception <= TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception;
      TrapPlugin_logic_harts_0_trap_pending_state_tval <= TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_tval;
      TrapPlugin_logic_harts_0_trap_pending_state_tval2 <= TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_tval2;
      TrapPlugin_logic_harts_0_trap_pending_state_code <= TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_code;
      TrapPlugin_logic_harts_0_trap_pending_state_arg <= TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_arg;
    end
    if(TrapPlugin_logic_harts_0_trap_trigger_valid) begin
      TrapPlugin_logic_harts_0_trap_pending_pc <= execute_ctrl4_down_PC_lane0;
      TrapPlugin_logic_harts_0_trap_pending_slices <= (_zz_TrapPlugin_logic_harts_0_trap_pending_slices + 2'b01);
    end
    if(TrapPlugin_logic_harts_0_trap_fsm_buffer_sampleIt) begin
      TrapPlugin_logic_harts_0_trap_fsm_buffer_i_valid <= TrapPlugin_logic_harts_0_interrupt_valid;
    end
    if(TrapPlugin_logic_harts_0_trap_fsm_buffer_sampleIt) begin
      TrapPlugin_logic_harts_0_trap_fsm_buffer_i_code <= TrapPlugin_logic_harts_0_interrupt_code;
    end
    if(TrapPlugin_logic_harts_0_trap_fsm_buffer_sampleIt) begin
      TrapPlugin_logic_harts_0_trap_fsm_buffer_i_targetPrivilege <= TrapPlugin_logic_harts_0_interrupt_targetPrivilege;
    end
    TrapPlugin_logic_harts_0_trap_fsm_jumpTarget <= (TrapPlugin_logic_harts_0_trap_pending_pc + _zz_TrapPlugin_logic_harts_0_trap_fsm_jumpTarget);
    if(when_TrapPlugin_l841) begin
      TrapPlugin_logic_harts_0_trap_fsm_readed <= TrapPlugin_logic_harts_0_crsPorts_read_data;
    end
    CsrAccessPlugin_logic_fsm_interface_read <= ((execute_ctrl2_down_CsrAccessPlugin_SEL_lane0 && (! CsrAccessPlugin_logic_fsm_inject_trap)) && CsrAccessPlugin_logic_fsm_inject_csrRead);
    CsrAccessPlugin_logic_fsm_interface_write <= ((execute_ctrl2_down_CsrAccessPlugin_SEL_lane0 && (! CsrAccessPlugin_logic_fsm_inject_trap)) && CsrAccessPlugin_logic_fsm_inject_csrWrite);
    CsrAccessPlugin_logic_fsm_interface_csrId <= CsrAccessPlugin_logic_fsm_inject_csrAddress;
    CsrAccessPlugin_logic_fsm_inject_trapReg <= CsrAccessPlugin_logic_fsm_inject_trap;
    CsrAccessPlugin_logic_fsm_inject_busTrapReg <= CsrAccessPlugin_bus_decode_trap;
    CsrAccessPlugin_logic_fsm_inject_busTrapCodeReg <= CsrAccessPlugin_bus_decode_trapCode;
    CsrAccessPlugin_logic_fsm_interface_onWriteBits <= CsrAccessPlugin_logic_fsm_writeLogic_alu_result;
    if(FetchCachelessPlugin_logic_fork_translated_ready) begin
      FetchCachelessPlugin_logic_fork_translated_rData_id <= FetchCachelessPlugin_logic_fork_translated_payload_id;
      FetchCachelessPlugin_logic_fork_translated_rData_address <= FetchCachelessPlugin_logic_fork_translated_payload_address;
    end
    FetchCachelessPlugin_logic_bus_cmd_payload_regNext_id <= FetchCachelessPlugin_logic_bus_cmd_payload_id;
    FetchCachelessPlugin_logic_bus_cmd_payload_regNext_address <= FetchCachelessPlugin_logic_bus_cmd_payload_address;
    if(fetch_logic_ctrls_0_down_isReady) begin
      fetch_logic_ctrls_1_up_Fetch_WORD_PC <= fetch_logic_ctrls_0_down_Fetch_WORD_PC;
      fetch_logic_ctrls_1_up_Fetch_ID <= fetch_logic_ctrls_0_down_Fetch_ID;
      fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED <= fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_TRANSLATED;
      fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_BUFFER_ID <= fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_BUFFER_ID;
      fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_fork_PMA_FAULT <= fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_fork_PMA_FAULT;
      fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD <= fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_HAZARD;
      fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL <= fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_REFILL;
      fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT <= fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_PAGE_FAULT;
      fetch_logic_ctrls_1_up_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT <= fetch_logic_ctrls_0_down_FetchCachelessPlugin_logic_onAddress0_translationPort_keys_ACCESS_FAULT;
    end
    if(decode_ctrls_0_down_isReady) begin
      decode_ctrls_1_up_Decode_INSTRUCTION_0 <= decode_ctrls_0_down_Decode_INSTRUCTION_0;
      decode_ctrls_1_up_Decode_DECOMPRESSION_FAULT_0 <= decode_ctrls_0_down_Decode_DECOMPRESSION_FAULT_0;
      decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0 <= decode_ctrls_0_down_Decode_INSTRUCTION_RAW_0;
      decode_ctrls_1_up_Decode_INSTRUCTION_SLICE_COUNT_0 <= decode_ctrls_0_down_Decode_INSTRUCTION_SLICE_COUNT_0;
      decode_ctrls_1_up_PC_0 <= decode_ctrls_0_down_PC_0;
      decode_ctrls_1_up_Decode_DOP_ID_0 <= decode_ctrls_0_down_Decode_DOP_ID_0;
      decode_ctrls_1_up_TRAP_0 <= decode_ctrls_0_down_TRAP_0;
    end
    if(execute_ctrl0_down_isReady) begin
      execute_ctrl1_up_Decode_UOP_lane0 <= execute_ctrl0_down_Decode_UOP_lane0;
      execute_ctrl1_up_Decode_INSTRUCTION_SLICE_COUNT_lane0 <= execute_ctrl0_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
      execute_ctrl1_up_PC_lane0 <= execute_ctrl0_down_PC_lane0;
      execute_ctrl1_up_TRAP_lane0 <= execute_ctrl0_down_TRAP_lane0;
      execute_ctrl1_up_Decode_UOP_ID_lane0 <= execute_ctrl0_down_Decode_UOP_ID_lane0;
      execute_ctrl1_up_RS1_PHYS_lane0 <= execute_ctrl0_down_RS1_PHYS_lane0;
      execute_ctrl1_up_RS2_PHYS_lane0 <= execute_ctrl0_down_RS2_PHYS_lane0;
      execute_ctrl1_up_RD_ENABLE_lane0 <= execute_ctrl0_down_RD_ENABLE_lane0;
      execute_ctrl1_up_RD_PHYS_lane0 <= execute_ctrl0_down_RD_PHYS_lane0;
      execute_ctrl1_up_COMPLETED_lane0 <= execute_ctrl0_down_COMPLETED_lane0;
      execute_ctrl1_up_AguPlugin_SIZE_lane0 <= execute_ctrl0_down_AguPlugin_SIZE_lane0;
    end
    if(execute_ctrl1_down_isReady) begin
      execute_ctrl2_up_Decode_UOP_lane0 <= execute_ctrl1_down_Decode_UOP_lane0;
      execute_ctrl2_up_Decode_INSTRUCTION_SLICE_COUNT_lane0 <= execute_ctrl1_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
      execute_ctrl2_up_PC_lane0 <= execute_ctrl1_down_PC_lane0;
      execute_ctrl2_up_TRAP_lane0 <= execute_ctrl1_down_TRAP_lane0;
      execute_ctrl2_up_Decode_UOP_ID_lane0 <= execute_ctrl1_down_Decode_UOP_ID_lane0;
      execute_ctrl2_up_RD_ENABLE_lane0 <= execute_ctrl1_down_RD_ENABLE_lane0;
      execute_ctrl2_up_RD_PHYS_lane0 <= execute_ctrl1_down_RD_PHYS_lane0;
      execute_ctrl2_up_COMPLETED_lane0 <= execute_ctrl1_down_COMPLETED_lane0;
      execute_ctrl2_up_AguPlugin_SIZE_lane0 <= execute_ctrl1_down_AguPlugin_SIZE_lane0;
      execute_ctrl2_up_early0_SrcPlugin_SRC1_lane0 <= execute_ctrl1_down_early0_SrcPlugin_SRC1_lane0;
      execute_ctrl2_up_integer_RS1_lane0 <= execute_ctrl1_down_integer_RS1_lane0;
      execute_ctrl2_up_early0_SrcPlugin_SRC2_lane0 <= execute_ctrl1_down_early0_SrcPlugin_SRC2_lane0;
      execute_ctrl2_up_integer_RS2_lane0 <= execute_ctrl1_down_integer_RS2_lane0;
      execute_ctrl2_up_early0_IntAluPlugin_SEL_lane0 <= execute_ctrl1_down_early0_IntAluPlugin_SEL_lane0;
      execute_ctrl2_up_early0_BarrelShifterPlugin_SEL_lane0 <= execute_ctrl1_down_early0_BarrelShifterPlugin_SEL_lane0;
      execute_ctrl2_up_early0_BranchPlugin_SEL_lane0 <= execute_ctrl1_down_early0_BranchPlugin_SEL_lane0;
      execute_ctrl2_up_early0_MulPlugin_SEL_lane0 <= execute_ctrl1_down_early0_MulPlugin_SEL_lane0;
      execute_ctrl2_up_early0_DivPlugin_SEL_lane0 <= execute_ctrl1_down_early0_DivPlugin_SEL_lane0;
      execute_ctrl2_up_early0_EnvPlugin_SEL_lane0 <= execute_ctrl1_down_early0_EnvPlugin_SEL_lane0;
      execute_ctrl2_up_CsrAccessPlugin_SEL_lane0 <= execute_ctrl1_down_CsrAccessPlugin_SEL_lane0;
      execute_ctrl2_up_AguPlugin_SEL_lane0 <= execute_ctrl1_down_AguPlugin_SEL_lane0;
      execute_ctrl2_up_LsuCachelessPlugin_FENCE_lane0 <= execute_ctrl1_down_LsuCachelessPlugin_FENCE_lane0;
      execute_ctrl2_up_lane0_integer_WriteBackPlugin_SEL_lane0 <= execute_ctrl1_down_lane0_integer_WriteBackPlugin_SEL_lane0;
      execute_ctrl2_up_COMPLETION_AT_2_lane0 <= execute_ctrl1_down_COMPLETION_AT_2_lane0;
      execute_ctrl2_up_COMPLETION_AT_4_lane0 <= execute_ctrl1_down_COMPLETION_AT_4_lane0;
      execute_ctrl2_up_COMPLETION_AT_3_lane0 <= execute_ctrl1_down_COMPLETION_AT_3_lane0;
      execute_ctrl2_up_lane0_logic_completions_onCtrl_0_ENABLE_lane0 <= execute_ctrl1_down_lane0_logic_completions_onCtrl_0_ENABLE_lane0;
      execute_ctrl2_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0 <= execute_ctrl1_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
      execute_ctrl2_up_lane0_logic_completions_onCtrl_2_ENABLE_lane0 <= execute_ctrl1_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
      execute_ctrl2_up_early0_IntAluPlugin_ALU_ADD_SUB_lane0 <= execute_ctrl1_down_early0_IntAluPlugin_ALU_ADD_SUB_lane0;
      execute_ctrl2_up_early0_IntAluPlugin_ALU_SLTX_lane0 <= execute_ctrl1_down_early0_IntAluPlugin_ALU_SLTX_lane0;
      execute_ctrl2_up_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0 <= execute_ctrl1_down_early0_IntAluPlugin_ALU_BITWISE_CTRL_lane0;
      execute_ctrl2_up_SrcStageables_REVERT_lane0 <= execute_ctrl1_down_SrcStageables_REVERT_lane0;
      execute_ctrl2_up_SrcStageables_ZERO_lane0 <= execute_ctrl1_down_SrcStageables_ZERO_lane0;
      execute_ctrl2_up_lane0_IntFormatPlugin_logic_SIGNED_lane0 <= execute_ctrl1_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
      execute_ctrl2_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 <= execute_ctrl1_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
      execute_ctrl2_up_BYPASSED_AT_2_lane0 <= execute_ctrl1_down_BYPASSED_AT_2_lane0;
      execute_ctrl2_up_BYPASSED_AT_3_lane0 <= execute_ctrl1_down_BYPASSED_AT_3_lane0;
      execute_ctrl2_up_BYPASSED_AT_4_lane0 <= execute_ctrl1_down_BYPASSED_AT_4_lane0;
      execute_ctrl2_up_SrcStageables_UNSIGNED_lane0 <= execute_ctrl1_down_SrcStageables_UNSIGNED_lane0;
      execute_ctrl2_up_BarrelShifterPlugin_LEFT_lane0 <= execute_ctrl1_down_BarrelShifterPlugin_LEFT_lane0;
      execute_ctrl2_up_BarrelShifterPlugin_SIGNED_lane0 <= execute_ctrl1_down_BarrelShifterPlugin_SIGNED_lane0;
      execute_ctrl2_up_BranchPlugin_BRANCH_CTRL_lane0 <= execute_ctrl1_down_BranchPlugin_BRANCH_CTRL_lane0;
      execute_ctrl2_up_MulPlugin_HIGH_lane0 <= execute_ctrl1_down_MulPlugin_HIGH_lane0;
      execute_ctrl2_up_RsUnsignedPlugin_RS1_SIGNED_lane0 <= execute_ctrl1_down_RsUnsignedPlugin_RS1_SIGNED_lane0;
      execute_ctrl2_up_RsUnsignedPlugin_RS2_SIGNED_lane0 <= execute_ctrl1_down_RsUnsignedPlugin_RS2_SIGNED_lane0;
      execute_ctrl2_up_DivPlugin_REM_lane0 <= execute_ctrl1_down_DivPlugin_REM_lane0;
      execute_ctrl2_up_CsrAccessPlugin_CSR_IMM_lane0 <= execute_ctrl1_down_CsrAccessPlugin_CSR_IMM_lane0;
      execute_ctrl2_up_CsrAccessPlugin_CSR_MASK_lane0 <= execute_ctrl1_down_CsrAccessPlugin_CSR_MASK_lane0;
      execute_ctrl2_up_CsrAccessPlugin_CSR_CLEAR_lane0 <= execute_ctrl1_down_CsrAccessPlugin_CSR_CLEAR_lane0;
      execute_ctrl2_up_AguPlugin_LOAD_lane0 <= execute_ctrl1_down_AguPlugin_LOAD_lane0;
      execute_ctrl2_up_AguPlugin_STORE_lane0 <= execute_ctrl1_down_AguPlugin_STORE_lane0;
      execute_ctrl2_up_AguPlugin_EXECUTE_lane0 <= execute_ctrl1_down_AguPlugin_EXECUTE_lane0;
      execute_ctrl2_up_AguPlugin_ATOMIC_lane0 <= execute_ctrl1_down_AguPlugin_ATOMIC_lane0;
      execute_ctrl2_up_AguPlugin_FLOAT_lane0 <= execute_ctrl1_down_AguPlugin_FLOAT_lane0;
      execute_ctrl2_up_AguPlugin_GUEST_lane0 <= execute_ctrl1_down_AguPlugin_GUEST_lane0;
      execute_ctrl2_up_early0_EnvPlugin_OP_lane0 <= execute_ctrl1_down_early0_EnvPlugin_OP_lane0;
    end
    if(execute_ctrl2_down_isReady) begin
      execute_ctrl3_up_Decode_UOP_lane0 <= execute_ctrl2_down_Decode_UOP_lane0;
      execute_ctrl3_up_Decode_INSTRUCTION_SLICE_COUNT_lane0 <= execute_ctrl2_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
      execute_ctrl3_up_PC_lane0 <= execute_ctrl2_down_PC_lane0;
      execute_ctrl3_up_TRAP_lane0 <= execute_ctrl2_down_TRAP_lane0;
      execute_ctrl3_up_Decode_UOP_ID_lane0 <= execute_ctrl2_down_Decode_UOP_ID_lane0;
      execute_ctrl3_up_RD_ENABLE_lane0 <= execute_ctrl2_down_RD_ENABLE_lane0;
      execute_ctrl3_up_RD_PHYS_lane0 <= execute_ctrl2_down_RD_PHYS_lane0;
      execute_ctrl3_up_COMPLETED_lane0 <= execute_ctrl2_down_COMPLETED_lane0;
      execute_ctrl3_up_AguPlugin_SIZE_lane0 <= execute_ctrl2_down_AguPlugin_SIZE_lane0;
      execute_ctrl3_up_early0_MulPlugin_SEL_lane0 <= execute_ctrl2_down_early0_MulPlugin_SEL_lane0;
      execute_ctrl3_up_early0_DivPlugin_SEL_lane0 <= execute_ctrl2_down_early0_DivPlugin_SEL_lane0;
      execute_ctrl3_up_CsrAccessPlugin_SEL_lane0 <= execute_ctrl2_down_CsrAccessPlugin_SEL_lane0;
      execute_ctrl3_up_AguPlugin_SEL_lane0 <= execute_ctrl2_down_AguPlugin_SEL_lane0;
      execute_ctrl3_up_lane0_integer_WriteBackPlugin_SEL_lane0 <= execute_ctrl2_down_lane0_integer_WriteBackPlugin_SEL_lane0;
      execute_ctrl3_up_COMPLETION_AT_4_lane0 <= execute_ctrl2_down_COMPLETION_AT_4_lane0;
      execute_ctrl3_up_COMPLETION_AT_3_lane0 <= execute_ctrl2_down_COMPLETION_AT_3_lane0;
      execute_ctrl3_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0 <= execute_ctrl2_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
      execute_ctrl3_up_lane0_logic_completions_onCtrl_2_ENABLE_lane0 <= execute_ctrl2_down_lane0_logic_completions_onCtrl_2_ENABLE_lane0;
      execute_ctrl3_up_lane0_IntFormatPlugin_logic_SIGNED_lane0 <= execute_ctrl2_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
      execute_ctrl3_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 <= execute_ctrl2_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
      execute_ctrl3_up_BYPASSED_AT_3_lane0 <= execute_ctrl2_down_BYPASSED_AT_3_lane0;
      execute_ctrl3_up_BYPASSED_AT_4_lane0 <= execute_ctrl2_down_BYPASSED_AT_4_lane0;
      execute_ctrl3_up_MulPlugin_HIGH_lane0 <= execute_ctrl2_down_MulPlugin_HIGH_lane0;
      execute_ctrl3_up_AguPlugin_LOAD_lane0 <= execute_ctrl2_down_AguPlugin_LOAD_lane0;
      execute_ctrl3_up_AguPlugin_STORE_lane0 <= execute_ctrl2_down_AguPlugin_STORE_lane0;
      execute_ctrl3_up_AguPlugin_EXECUTE_lane0 <= execute_ctrl2_down_AguPlugin_EXECUTE_lane0;
      execute_ctrl3_up_AguPlugin_ATOMIC_lane0 <= execute_ctrl2_down_AguPlugin_ATOMIC_lane0;
      execute_ctrl3_up_AguPlugin_FLOAT_lane0 <= execute_ctrl2_down_AguPlugin_FLOAT_lane0;
      execute_ctrl3_up_COMMIT_lane0 <= execute_ctrl2_down_COMMIT_lane0;
      execute_ctrl3_up_early0_SrcPlugin_ADD_SUB_lane0 <= execute_ctrl2_down_early0_SrcPlugin_ADD_SUB_lane0;
      execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_0_lane0 <= execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_0_lane0;
      execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_1_lane0 <= execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_1_lane0;
      execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_2_lane0 <= execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_2_lane0;
      execute_ctrl3_up_early0_MulPlugin_logic_mul_VALUES_3_lane0 <= execute_ctrl2_down_early0_MulPlugin_logic_mul_VALUES_3_lane0;
      execute_ctrl3_up_DivPlugin_DIV_RESULT_lane0 <= execute_ctrl2_down_DivPlugin_DIV_RESULT_lane0;
      execute_ctrl3_up_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0 <= execute_ctrl2_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
      execute_ctrl3_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault <= execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
      execute_ctrl3_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_io <= execute_ctrl2_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
      execute_ctrl3_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 <= execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
    end
    if(execute_ctrl3_down_isReady) begin
      execute_ctrl4_up_Decode_UOP_lane0 <= execute_ctrl3_down_Decode_UOP_lane0;
      execute_ctrl4_up_Decode_INSTRUCTION_SLICE_COUNT_lane0 <= execute_ctrl3_down_Decode_INSTRUCTION_SLICE_COUNT_lane0;
      execute_ctrl4_up_PC_lane0 <= execute_ctrl3_down_PC_lane0;
      execute_ctrl4_up_TRAP_lane0 <= execute_ctrl3_down_TRAP_lane0;
      execute_ctrl4_up_Decode_UOP_ID_lane0 <= execute_ctrl3_down_Decode_UOP_ID_lane0;
      execute_ctrl4_up_RD_ENABLE_lane0 <= execute_ctrl3_down_RD_ENABLE_lane0;
      execute_ctrl4_up_RD_PHYS_lane0 <= execute_ctrl3_down_RD_PHYS_lane0;
      execute_ctrl4_up_COMPLETED_lane0 <= execute_ctrl3_down_COMPLETED_lane0;
      execute_ctrl4_up_AguPlugin_SIZE_lane0 <= execute_ctrl3_down_AguPlugin_SIZE_lane0;
      execute_ctrl4_up_early0_MulPlugin_SEL_lane0 <= execute_ctrl3_down_early0_MulPlugin_SEL_lane0;
      execute_ctrl4_up_AguPlugin_SEL_lane0 <= execute_ctrl3_down_AguPlugin_SEL_lane0;
      execute_ctrl4_up_lane0_integer_WriteBackPlugin_SEL_lane0 <= execute_ctrl3_down_lane0_integer_WriteBackPlugin_SEL_lane0;
      execute_ctrl4_up_COMPLETION_AT_4_lane0 <= execute_ctrl3_down_COMPLETION_AT_4_lane0;
      execute_ctrl4_up_lane0_logic_completions_onCtrl_1_ENABLE_lane0 <= execute_ctrl3_down_lane0_logic_completions_onCtrl_1_ENABLE_lane0;
      execute_ctrl4_up_lane0_IntFormatPlugin_logic_SIGNED_lane0 <= execute_ctrl3_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
      execute_ctrl4_up_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 <= execute_ctrl3_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0;
      execute_ctrl4_up_BYPASSED_AT_4_lane0 <= execute_ctrl3_down_BYPASSED_AT_4_lane0;
      execute_ctrl4_up_MulPlugin_HIGH_lane0 <= execute_ctrl3_down_MulPlugin_HIGH_lane0;
      execute_ctrl4_up_AguPlugin_LOAD_lane0 <= execute_ctrl3_down_AguPlugin_LOAD_lane0;
      execute_ctrl4_up_AguPlugin_EXECUTE_lane0 <= execute_ctrl3_down_AguPlugin_EXECUTE_lane0;
      execute_ctrl4_up_AguPlugin_ATOMIC_lane0 <= execute_ctrl3_down_AguPlugin_ATOMIC_lane0;
      execute_ctrl4_up_AguPlugin_FLOAT_lane0 <= execute_ctrl3_down_AguPlugin_FLOAT_lane0;
      execute_ctrl4_up_COMMIT_lane0 <= execute_ctrl3_down_COMMIT_lane0;
      execute_ctrl4_up_early0_SrcPlugin_ADD_SUB_lane0 <= execute_ctrl3_down_early0_SrcPlugin_ADD_SUB_lane0;
      execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_0_lane0 <= execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_0_lane0;
      execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_1_lane0 <= execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_1_lane0;
      execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_2_lane0 <= execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_2_lane0;
      execute_ctrl4_up_early0_MulPlugin_logic_mul_VALUES_3_lane0 <= execute_ctrl3_down_early0_MulPlugin_logic_mul_VALUES_3_lane0;
      execute_ctrl4_up_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0 <= execute_ctrl3_down_LsuCachelessPlugin_logic_onAddress_translationPort_keys_TRANSLATED_lane0;
      execute_ctrl4_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault <= execute_ctrl3_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_fault;
      execute_ctrl4_up_LsuCachelessPlugin_logic_onPma_RSP_lane0_io <= execute_ctrl3_down_LsuCachelessPlugin_logic_onPma_RSP_lane0_io;
      execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 <= execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
      execute_ctrl4_up_early0_MulPlugin_logic_steps_0_adders_0_lane0 <= execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_0_lane0;
      execute_ctrl4_up_early0_MulPlugin_logic_steps_0_adders_1_lane0 <= execute_ctrl3_down_early0_MulPlugin_logic_steps_0_adders_1_lane0;
      execute_ctrl4_up_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0 <= execute_ctrl3_down_LsuCachelessPlugin_logic_onJoin_SC_MISS_lane0;
      execute_ctrl4_up_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0 <= execute_ctrl3_down_LsuCachelessPlugin_logic_onJoin_READ_DATA_lane0;
    end
    if(execute_ctrl4_down_isReady) begin
      execute_ctrl5_up_RD_ENABLE_lane0 <= execute_ctrl4_down_RD_ENABLE_lane0;
      execute_ctrl5_up_RD_PHYS_lane0 <= execute_ctrl4_down_RD_PHYS_lane0;
      execute_ctrl5_up_COMMIT_lane0 <= execute_ctrl4_down_COMMIT_lane0;
      execute_ctrl5_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 <= execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
    end
    case(PerformanceCounterPlugin_logic_fsm_stateReg)
      PerformanceCounterPlugin_logic_fsm_IDLE : begin
        PerformanceCounterPlugin_logic_fsm_cmd_oh <= {(PerformanceCounterPlugin_logic_fsm_idleCsrAddress == 2'b10),(PerformanceCounterPlugin_logic_fsm_idleCsrAddress == 2'b00)};
        if(!PerformanceCounterPlugin_logic_fsm_csrWriteCmd_valid) begin
          if(PerformanceCounterPlugin_logic_fsm_flusherCmd_valid) begin
            PerformanceCounterPlugin_logic_fsm_cmd_flusher <= 1'b1;
            PerformanceCounterPlugin_logic_fsm_cmd_oh <= PerformanceCounterPlugin_logic_fsm_flusherCmd_payload_oh;
          end else begin
            if(PerformanceCounterPlugin_logic_fsm_csrReadCmd_valid) begin
              PerformanceCounterPlugin_logic_fsm_cmd_flusher <= 1'b0;
            end
          end
        end
        PerformanceCounterPlugin_logic_fsm_carry <= 1'b0;
      end
      PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
        PerformanceCounterPlugin_logic_fsm_ramReaded <= PerformanceCounterPlugin_logic_readPort_data;
        PerformanceCounterPlugin_logic_fsm_counterReaded <= ((_zz_PerformanceCounterPlugin_logic_fsm_cmd_address ? PerformanceCounterPlugin_logic_counters_cycle_value : 8'h0) | (_zz_PerformanceCounterPlugin_logic_fsm_cmd_address_1 ? PerformanceCounterPlugin_logic_counters_instret_value : 8'h0));
      end
      PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
        if(PerformanceCounterPlugin_logic_writePort_ready) begin
          if(when_PerformanceCounterPlugin_l290) begin
            PerformanceCounterPlugin_logic_fsm_carry <= 1'b1;
          end
        end
      end
      PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
        PerformanceCounterPlugin_logic_fsm_ramReaded <= PerformanceCounterPlugin_logic_readPort_data;
      end
      PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
      end
      PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
      end
      default : begin
      end
    endcase
    case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
      TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
        TrapPlugin_logic_harts_0_trap_fsm_triggerEbreakReg <= TrapPlugin_logic_harts_0_trap_fsm_triggerEbreak;
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
      end
      TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
      end
      default : begin
      end
    endcase
    case(CsrAccessPlugin_logic_fsm_stateReg)
      CsrAccessPlugin_logic_fsm_READ : begin
        CsrAccessPlugin_logic_fsm_interface_aluInput <= CsrAccessPlugin_bus_read_toWriteBits;
        CsrAccessPlugin_logic_fsm_interface_csrValue <= CsrAccessPlugin_logic_fsm_readLogic_csrValue;
      end
      CsrAccessPlugin_logic_fsm_WRITE : begin
      end
      CsrAccessPlugin_logic_fsm_COMPLETION : begin
      end
      default : begin
        REG_CSR_1952 <= COMB_CSR_1952;
        REG_CSR_1953 <= COMB_CSR_1953;
        REG_CSR_1954 <= COMB_CSR_1954;
        REG_CSR_3857 <= COMB_CSR_3857;
        REG_CSR_3858 <= COMB_CSR_3858;
        REG_CSR_3859 <= COMB_CSR_3859;
        REG_CSR_3860 <= COMB_CSR_3860;
        REG_CSR_769 <= COMB_CSR_769;
        REG_CSR_768 <= COMB_CSR_768;
        REG_CSR_834 <= COMB_CSR_834;
        REG_CSR_836 <= COMB_CSR_836;
        REG_CSR_772 <= COMB_CSR_772;
        REG_CSR_4016 <= COMB_CSR_4016;
        REG_CSR_794 <= COMB_CSR_794;
        REG_CSR_778 <= COMB_CSR_778;
        REG_CSR_774 <= COMB_CSR_774;
        REG_CSR_3073 <= COMB_CSR_3073;
        REG_CSR_3201 <= COMB_CSR_3201;
        REG_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_tvecFilter <= COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_tvecFilter;
        REG_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_epcFilter <= COMB_CSR_PrivilegedPlugin_logic_readAnyWriteLegal_epcFilter;
        REG_CSR_800 <= COMB_CSR_800;
        REG_CSR_UNAMED_0 <= COMB_CSR_UNAMED_1;
        REG_CSR_PerformanceCounterPlugin_logic_csrFilter <= COMB_CSR_PerformanceCounterPlugin_logic_csrFilter;
        REG_CSR_CsrRamPlugin_csrMapper_selFilter <= COMB_CSR_CsrRamPlugin_csrMapper_selFilter;
        REG_CSR_CsrAccessPlugin_logic_trapNextOnWriteFilter <= COMB_CSR_CsrAccessPlugin_logic_trapNextOnWriteFilter;
      end
    endcase
  end

  always @(posedge clk or posedge reset) begin
    if(reset) begin
      early0_DivPlugin_logic_processing_cmdSent <= 1'b0;
      early0_DivPlugin_logic_processing_unscheduleRequest <= 1'b0;
      PrivilegedPlugin_logic_harts_0_privilege <= 3'b011;
      PrivilegedPlugin_logic_harts_0_m_status_mie <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_status_mpie <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_status_mprv <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_cause_interrupt <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_cause_code <= 5'h0;
      PrivilegedPlugin_logic_harts_0_m_ip_meip <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_ip_mtip <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_ip_msip <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_ie_meie <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_ie_mtie <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_ie_msie <= 1'b0;
      PrivilegedPlugin_logic_harts_0_m_counteren_tm <= 1'b1;
      FetchCachelessPlugin_logic_buffer_reserveId_value <= 1'b0;
      FetchCachelessPlugin_logic_buffer_inflight_0 <= 1'b0;
      FetchCachelessPlugin_logic_buffer_inflight_1 <= 1'b0;
      decode_ctrls_0_up_LANE_SEL_0_regNext <= 1'b0;
      PerformanceCounterPlugin_logic_ignoreNextCommit <= 1'b0;
      PerformanceCounterPlugin_logic_counters_cycle_value <= 8'h0;
      PerformanceCounterPlugin_logic_counters_cycle_mcounteren <= 1'b0;
      PerformanceCounterPlugin_logic_counters_cycle_mcountinhibit <= 1'b0;
      PerformanceCounterPlugin_logic_counters_instret_value <= 8'h0;
      PerformanceCounterPlugin_logic_counters_instret_mcounteren <= 1'b0;
      PerformanceCounterPlugin_logic_counters_instret_mcountinhibit <= 1'b0;
      _zz_PerformanceCounterPlugin_logic_counters_instret_value <= 1'b0;
      AlignerPlugin_logic_feeder_harts_0_dopId <= 10'h0;
      AlignerPlugin_logic_buffer_mask <= 2'b00;
      AlignerPlugin_logic_buffer_last <= 2'b00;
      AlignerPlugin_logic_buffer_trap <= 1'b0;
      LsuCachelessPlugin_logic_onFork_askFenceReg <= 1'b0;
      LsuCachelessPlugin_logic_onFork_cmdCounter_value <= 1'b0;
      LsuCachelessPlugin_logic_onFork_cmdSent <= 1'b0;
      LsuCachelessPlugin_logic_bus_cmd_valid_regNext <= 1'b0;
      LsuCachelessPlugin_logic_bus_cmd_ready_regNext <= 1'b0;
      LsuCachelessPlugin_logic_bus_cmd_isStall_regNext <= 1'b0;
      DecoderPlugin_logic_harts_0_uopId <= 16'h0;
      DecoderPlugin_logic_interrupt_buffered <= 1'b0;
      decode_ctrls_1_up_LANE_SEL_0_regNext <= 1'b0;
      DispatchPlugin_logic_feeds_0_sent <= 1'b0;
      LsuCachelessPlugin_logic_onJoin_buffers_0_valid <= 1'b0;
      LsuCachelessPlugin_logic_onJoin_buffers_0_inflight <= 1'b0;
      LsuCachelessPlugin_logic_onJoin_buffers_1_valid <= 1'b0;
      LsuCachelessPlugin_logic_onJoin_buffers_1_inflight <= 1'b0;
      LsuCachelessPlugin_logic_onJoin_rspCounter_value <= 1'b0;
      execute_ctrl3_up_LsuCachelessPlugin_WITH_RSP_lane0 <= 1'b0;
      PerformanceCounterPlugin_logic_interrupt_ip <= 1'b0;
      PerformanceCounterPlugin_logic_interrupt_ie <= 1'b0;
      PerformanceCounterPlugin_logic_csrRead_fired <= 1'b0;
      PerformanceCounterPlugin_logic_csrWrite_fired <= 1'b0;
      CsrRamPlugin_csrMapper_fired <= 1'b0;
      decode_ctrls_1_up_LANE_SEL_0_regNext_1 <= 1'b0;
      execute_ctrl0_down_LANE_SEL_lane0_regNext <= 1'b0;
      execute_ctrl2_down_LANE_SEL_lane0_regNext <= 1'b0;
      decode_ctrls_1_up_LANE_SEL_0 <= 1'b0;
      TrapPlugin_logic_harts_0_interrupt_validBuffer <= 1'b0;
      TrapPlugin_logic_harts_0_trap_fsm_trapEnterDebug <= 1'b0;
      PcPlugin_logic_harts_0_self_id <= 10'h0;
      PcPlugin_logic_harts_0_self_increment <= 1'b0;
      PcPlugin_logic_harts_0_self_fault <= 1'b0;
      PcPlugin_logic_harts_0_self_state <= 32'h80000000;
      PcPlugin_logic_harts_0_holdReg <= 1'b1;
      CsrAccessPlugin_logic_fsm_inject_unfreeze <= 1'b0;
      CsrAccessPlugin_logic_fsm_inject_flushReg <= 1'b0;
      CsrAccessPlugin_logic_fsm_inject_sampled <= 1'b0;
      FetchCachelessPlugin_logic_fork_forked_fired <= 1'b0;
      FetchCachelessPlugin_logic_fork_translated_rValidN <= 1'b1;
      FetchCachelessPlugin_logic_bus_cmd_valid_regNext <= 1'b0;
      FetchCachelessPlugin_logic_bus_cmd_ready_regNext <= 1'b0;
      FetchCachelessPlugin_logic_bus_cmd_isStall_regNext <= 1'b0;
      FetchCachelessPlugin_logic_join_trapSent <= 1'b0;
      CsrRamPlugin_logic_readLogic_ohReg <= 3'b000;
      CsrRamPlugin_logic_readLogic_busy <= 1'b0;
      CsrRamPlugin_logic_flush_counter <= 4'b0000;
      execute_ctrl1_up_LANE_SEL_lane0 <= 1'b0;
      execute_ctrl2_up_LANE_SEL_lane0 <= 1'b0;
      execute_ctrl3_up_LANE_SEL_lane0 <= 1'b0;
      execute_ctrl4_up_LANE_SEL_lane0 <= 1'b0;
      execute_ctrl5_up_LANE_SEL_lane0 <= 1'b0;
      integer_RegFilePlugin_logic_initalizer_counter <= 6'h0;
      _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2 <= 60'h0;
      _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2 <= 60'h0;
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_2 <= 60'h0;
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_2 <= 60'h0;
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_2 <= 60'h0;
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_2 <= 60'h0;
      fetch_logic_ctrls_1_up_valid <= 1'b0;
      decode_ctrls_1_up_valid <= 1'b0;
      PerformanceCounterPlugin_logic_fsm_stateReg <= PerformanceCounterPlugin_logic_fsm_BOOT;
      TrapPlugin_logic_harts_0_trap_fsm_stateReg <= TrapPlugin_logic_harts_0_trap_fsm_RESET;
      CsrAccessPlugin_logic_fsm_stateReg <= CsrAccessPlugin_logic_fsm_IDLE;
    end else begin
      if(io_cmd_fire) begin
        early0_DivPlugin_logic_processing_cmdSent <= 1'b1;
      end
      if(execute_ctrl2_down_isReady) begin
        early0_DivPlugin_logic_processing_cmdSent <= 1'b0;
      end
      early0_DivPlugin_logic_processing_unscheduleRequest <= execute_lane0_ctrls_2_upIsCancel;
      if(execute_ctrl2_down_isReady) begin
        early0_DivPlugin_logic_processing_unscheduleRequest <= 1'b0;
      end
      if(PrivilegedPlugin_logic_harts_0_xretAwayFromMachine) begin
        PrivilegedPlugin_logic_harts_0_m_status_mprv <= 1'b0;
      end
      PrivilegedPlugin_logic_harts_0_m_ip_meip <= PrivilegedPlugin_logic_harts_0_int_m_external;
      PrivilegedPlugin_logic_harts_0_m_ip_mtip <= PrivilegedPlugin_logic_harts_0_int_m_timer;
      PrivilegedPlugin_logic_harts_0_m_ip_msip <= PrivilegedPlugin_logic_harts_0_int_m_software;
      FetchCachelessPlugin_logic_buffer_reserveId_value <= FetchCachelessPlugin_logic_buffer_reserveId_valueNext;
      if(FetchCachelessPlugin_logic_buffer_inflightSpawn) begin
        if(_zz_4[0]) begin
          FetchCachelessPlugin_logic_buffer_inflight_0 <= 1'b1;
        end
        if(_zz_4[1]) begin
          FetchCachelessPlugin_logic_buffer_inflight_1 <= 1'b1;
        end
      end
      if(FetchCachelessPlugin_logic_bus_rsp_valid) begin
        if(_zz_5[0]) begin
          FetchCachelessPlugin_logic_buffer_inflight_0 <= 1'b0;
        end
        if(_zz_5[1]) begin
          FetchCachelessPlugin_logic_buffer_inflight_1 <= 1'b0;
        end
      end
      decode_ctrls_0_up_LANE_SEL_0_regNext <= decode_ctrls_0_up_LANE_SEL_0;
      if(when_CtrlLaneApi_l50) begin
        decode_ctrls_0_up_LANE_SEL_0_regNext <= 1'b0;
      end
      if(when_PerformanceCounterPlugin_l45) begin
        PerformanceCounterPlugin_logic_ignoreNextCommit <= 1'b0;
      end
      PerformanceCounterPlugin_logic_counters_cycle_value <= (PerformanceCounterPlugin_logic_counters_cycle_value + _zz_PerformanceCounterPlugin_logic_counters_cycle_value);
      _zz_PerformanceCounterPlugin_logic_counters_instret_value <= ((! PerformanceCounterPlugin_logic_counters_instret_mcountinhibit) ? PerformanceCounterPlugin_logic_commitCount : 1'b0);
      PerformanceCounterPlugin_logic_counters_instret_value <= (PerformanceCounterPlugin_logic_counters_instret_value + _zz_PerformanceCounterPlugin_logic_counters_instret_value_1);
      if(when_AlignerPlugin_l171) begin
        AlignerPlugin_logic_feeder_harts_0_dopId <= (decode_ctrls_0_down_Decode_DOP_ID_0 + 10'h001);
      end
      if(AlignerPlugin_logic_buffer_downFire) begin
        AlignerPlugin_logic_buffer_mask <= (AlignerPlugin_logic_buffer_mask & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
        AlignerPlugin_logic_buffer_last <= (AlignerPlugin_logic_buffer_last & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
      end
      if(when_AlignerPlugin_l256) begin
        AlignerPlugin_logic_buffer_mask <= (fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_MASK & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[3 : 2] : 2'b00)));
        AlignerPlugin_logic_buffer_trap <= fetch_logic_ctrls_1_down_TRAP;
        AlignerPlugin_logic_buffer_last <= fetch_logic_ctrls_1_down_AlignerPlugin_logic_FETCH_LAST;
      end
      if(when_LsuCachelessPlugin_l284) begin
        LsuCachelessPlugin_logic_onFork_askFenceReg <= ((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_AguPlugin_SEL_lane0) && execute_ctrl2_down_AguPlugin_ATOMIC_lane0);
      end
      LsuCachelessPlugin_logic_onFork_cmdCounter_value <= LsuCachelessPlugin_logic_onFork_cmdCounter_valueNext;
      if(LsuCachelessPlugin_logic_bus_cmd_fire) begin
        LsuCachelessPlugin_logic_onFork_cmdSent <= 1'b1;
      end
      if(when_LsuCachelessPlugin_l289) begin
        LsuCachelessPlugin_logic_onFork_cmdSent <= 1'b0;
      end
      LsuCachelessPlugin_logic_bus_cmd_valid_regNext <= LsuCachelessPlugin_logic_bus_cmd_valid;
      LsuCachelessPlugin_logic_bus_cmd_ready_regNext <= LsuCachelessPlugin_logic_bus_cmd_ready;
      `ifndef SYNTHESIS
        `ifdef FORMAL
          assert((! (((! LsuCachelessPlugin_logic_bus_cmd_valid) && LsuCachelessPlugin_logic_bus_cmd_valid_regNext) && (! LsuCachelessPlugin_logic_bus_cmd_ready_regNext)))); // Stream.scala:L658
        `else
          if(!(! (((! LsuCachelessPlugin_logic_bus_cmd_valid) && LsuCachelessPlugin_logic_bus_cmd_valid_regNext) && (! LsuCachelessPlugin_logic_bus_cmd_ready_regNext)))) begin
            $display("FAILURE Stream valid persistence failed"); // Stream.scala:L658
            $finish;
          end
        `endif
      `endif
      LsuCachelessPlugin_logic_bus_cmd_isStall_regNext <= LsuCachelessPlugin_logic_bus_cmd_isStall;
      if(LsuCachelessPlugin_logic_bus_cmd_isStall_regNext) begin
        `ifndef SYNTHESIS
          `ifdef FORMAL
            assert(((((((((((((LsuCachelessPlugin_logic_bus_cmd_payload_id == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_id) && (LsuCachelessPlugin_logic_bus_cmd_payload_write == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_write)) && (LsuCachelessPlugin_logic_bus_cmd_payload_address == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_address)) && (LsuCachelessPlugin_logic_bus_cmd_payload_data == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_data)) && (LsuCachelessPlugin_logic_bus_cmd_payload_size == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_size)) && (LsuCachelessPlugin_logic_bus_cmd_payload_mask == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_mask)) && (LsuCachelessPlugin_logic_bus_cmd_payload_amoEnable == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoEnable)) && (LsuCachelessPlugin_logic_bus_cmd_payload_amoOp == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoOp)) && (LsuCachelessPlugin_logic_bus_cmd_payload_io == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_io)) && (LsuCachelessPlugin_logic_bus_cmd_payload_fromHart == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_fromHart)) && (LsuCachelessPlugin_logic_bus_cmd_payload_uopId == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_uopId)) && 1'b1)); // Stream.scala:L662
          `else
            if(!((((((((((((LsuCachelessPlugin_logic_bus_cmd_payload_id == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_id) && (LsuCachelessPlugin_logic_bus_cmd_payload_write == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_write)) && (LsuCachelessPlugin_logic_bus_cmd_payload_address == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_address)) && (LsuCachelessPlugin_logic_bus_cmd_payload_data == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_data)) && (LsuCachelessPlugin_logic_bus_cmd_payload_size == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_size)) && (LsuCachelessPlugin_logic_bus_cmd_payload_mask == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_mask)) && (LsuCachelessPlugin_logic_bus_cmd_payload_amoEnable == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoEnable)) && (LsuCachelessPlugin_logic_bus_cmd_payload_amoOp == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_amoOp)) && (LsuCachelessPlugin_logic_bus_cmd_payload_io == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_io)) && (LsuCachelessPlugin_logic_bus_cmd_payload_fromHart == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_fromHart)) && (LsuCachelessPlugin_logic_bus_cmd_payload_uopId == LsuCachelessPlugin_logic_bus_cmd_payload_regNext_uopId)) && 1'b1)) begin
              $display("FAILURE Stream payload persistence failed"); // Stream.scala:L662
              $finish;
            end
          `endif
        `endif
      end
      if(when_DecoderPlugin_l148) begin
        DecoderPlugin_logic_harts_0_uopId <= (DecoderPlugin_logic_harts_0_uopId + 16'h0001);
      end
      if(when_DecoderPlugin_l156) begin
        DecoderPlugin_logic_interrupt_buffered <= DecoderPlugin_logic_interrupt_async;
      end
      decode_ctrls_1_up_LANE_SEL_0_regNext <= decode_ctrls_1_up_LANE_SEL_0;
      if(when_CtrlLaneApi_l50_1) begin
        decode_ctrls_1_up_LANE_SEL_0_regNext <= 1'b0;
      end
      if(DispatchPlugin_logic_feeds_0_sending) begin
        DispatchPlugin_logic_feeds_0_sent <= 1'b1;
      end
      if(decode_ctrls_1_up_isMoving) begin
        DispatchPlugin_logic_feeds_0_sent <= 1'b0;
      end
      if(LsuCachelessPlugin_logic_bus_cmd_fire) begin
        case(LsuCachelessPlugin_logic_bus_cmd_payload_id)
          1'b0 : begin
            LsuCachelessPlugin_logic_onJoin_buffers_0_inflight <= 1'b1;
          end
          default : begin
            LsuCachelessPlugin_logic_onJoin_buffers_1_inflight <= 1'b1;
          end
        endcase
      end
      if(LsuCachelessPlugin_logic_bus_rsp_valid) begin
        case(LsuCachelessPlugin_logic_bus_rsp_payload_id)
          1'b0 : begin
            LsuCachelessPlugin_logic_onJoin_buffers_0_valid <= 1'b1;
            LsuCachelessPlugin_logic_onJoin_buffers_0_inflight <= 1'b0;
          end
          default : begin
            LsuCachelessPlugin_logic_onJoin_buffers_1_valid <= 1'b1;
            LsuCachelessPlugin_logic_onJoin_buffers_1_inflight <= 1'b0;
          end
        endcase
      end
      LsuCachelessPlugin_logic_onJoin_rspCounter_value <= LsuCachelessPlugin_logic_onJoin_rspCounter_valueNext;
      if(LsuCachelessPlugin_logic_onJoin_pop) begin
        case(LsuCachelessPlugin_logic_onJoin_rspCounter_value)
          1'b0 : begin
            LsuCachelessPlugin_logic_onJoin_buffers_0_valid <= 1'b0;
          end
          default : begin
            LsuCachelessPlugin_logic_onJoin_buffers_1_valid <= 1'b0;
          end
        endcase
      end
      `ifndef SYNTHESIS
        `ifdef FORMAL
          assert((! ((((execute_ctrl3_up_LANE_SEL_lane0 && execute_lane0_ctrls_3_upIsCancel) && execute_ctrl3_down_AguPlugin_SEL_lane0) && execute_ctrl3_down_AguPlugin_STORE_lane0) && (! execute_ctrl3_up_TRAP_lane0)))); // LsuCachelessPlugin.scala:L488
        `else
          if(!(! ((((execute_ctrl3_up_LANE_SEL_lane0 && execute_lane0_ctrls_3_upIsCancel) && execute_ctrl3_down_AguPlugin_SEL_lane0) && execute_ctrl3_down_AguPlugin_STORE_lane0) && (! execute_ctrl3_up_TRAP_lane0)))) begin
            $display("FAILURE LsuCachelessPlugin saw unexpected select && STORE && cancel request"); // LsuCachelessPlugin.scala:L488
            $finish;
          end
        `endif
      `endif
      if(PerformanceCounterPlugin_logic_fsm_csrReadCmd_fire) begin
        PerformanceCounterPlugin_logic_csrRead_fired <= 1'b1;
      end
      if(CsrAccessPlugin_bus_read_moving) begin
        PerformanceCounterPlugin_logic_csrRead_fired <= 1'b0;
      end
      if(PerformanceCounterPlugin_logic_fsm_csrWriteCmd_fire) begin
        PerformanceCounterPlugin_logic_csrWrite_fired <= 1'b1;
      end
      if(CsrAccessPlugin_bus_write_moving) begin
        PerformanceCounterPlugin_logic_csrWrite_fired <= 1'b0;
      end
      if(when_CsrRamPlugin_l92) begin
        CsrRamPlugin_csrMapper_fired <= 1'b1;
      end
      if(CsrAccessPlugin_bus_write_moving) begin
        CsrRamPlugin_csrMapper_fired <= 1'b0;
      end
      decode_ctrls_1_up_LANE_SEL_0_regNext_1 <= decode_ctrls_1_up_LANE_SEL_0;
      if(when_CtrlLaneApi_l50_2) begin
        decode_ctrls_1_up_LANE_SEL_0_regNext_1 <= 1'b0;
      end
      execute_ctrl0_down_LANE_SEL_lane0_regNext <= execute_ctrl0_down_LANE_SEL_lane0;
      if(when_CtrlLaneApi_l50_3) begin
        execute_ctrl0_down_LANE_SEL_lane0_regNext <= 1'b0;
      end
      execute_ctrl2_down_LANE_SEL_lane0_regNext <= execute_ctrl2_down_LANE_SEL_lane0;
      if(when_CtrlLaneApi_l50_4) begin
        execute_ctrl2_down_LANE_SEL_lane0_regNext <= 1'b0;
      end
      if(AlignerPlugin_logic_buffer_flushIt) begin
        AlignerPlugin_logic_buffer_mask <= 2'b00;
        AlignerPlugin_logic_buffer_last <= 2'b00;
      end
      TrapPlugin_logic_harts_0_interrupt_validBuffer <= TrapPlugin_logic_harts_0_interrupt_valid;
      PcPlugin_logic_harts_0_holdReg <= PcPlugin_logic_harts_0_holdComb;
      PcPlugin_logic_harts_0_self_state <= PcPlugin_logic_harts_0_output_payload_pc;
      PcPlugin_logic_harts_0_self_fault <= PcPlugin_logic_harts_0_output_payload_fault;
      PcPlugin_logic_harts_0_self_increment <= 1'b0;
      if(PcPlugin_logic_harts_0_output_fire) begin
        PcPlugin_logic_harts_0_self_increment <= 1'b1;
        PcPlugin_logic_harts_0_self_state[1 : 1] <= 1'b0;
      end
      if(fetch_logic_ctrls_0_up_isFiring) begin
        PcPlugin_logic_harts_0_self_id <= (PcPlugin_logic_harts_0_self_id + 10'h001);
      end
      `ifndef SYNTHESIS
        `ifdef FORMAL
          assert((! ((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_CsrAccessPlugin_SEL_lane0) && execute_lane0_ctrls_2_upIsCancel))); // CsrAccessPlugin.scala:L151
        `else
          if(!(! ((execute_ctrl2_up_LANE_SEL_lane0 && execute_ctrl2_down_CsrAccessPlugin_SEL_lane0) && execute_lane0_ctrls_2_upIsCancel))) begin
            $display("FAILURE CsrAccessPlugin saw forbidden select && cancel request"); // CsrAccessPlugin.scala:L151
            $finish;
          end
        `endif
      `endif
      CsrAccessPlugin_logic_fsm_inject_unfreeze <= 1'b0;
      if(CsrAccessPlugin_logic_flushPort_valid) begin
        CsrAccessPlugin_logic_fsm_inject_flushReg <= 1'b1;
      end
      if(when_CsrAccessPlugin_l222) begin
        CsrAccessPlugin_logic_fsm_inject_flushReg <= 1'b0;
      end
      CsrAccessPlugin_logic_fsm_inject_sampled <= execute_freeze_valid;
      if(when_CsrAccessPlugin_l375) begin
        PrivilegedPlugin_logic_harts_0_m_status_mpie <= CsrAccessPlugin_bus_write_bits[7];
        PrivilegedPlugin_logic_harts_0_m_status_mie <= CsrAccessPlugin_bus_write_bits[3];
        PrivilegedPlugin_logic_harts_0_m_status_mprv <= CsrAccessPlugin_bus_write_bits[17];
      end
      if(when_CsrAccessPlugin_l375_1) begin
        PrivilegedPlugin_logic_harts_0_m_cause_interrupt <= CsrAccessPlugin_bus_write_bits[31];
        PrivilegedPlugin_logic_harts_0_m_cause_code <= CsrAccessPlugin_bus_write_bits[4 : 0];
      end
      if(when_CsrAccessPlugin_l375_2) begin
        PerformanceCounterPlugin_logic_interrupt_ip <= CsrAccessPlugin_bus_write_bits[13];
      end
      if(when_CsrAccessPlugin_l375_3) begin
        PrivilegedPlugin_logic_harts_0_m_ie_meie <= CsrAccessPlugin_bus_write_bits[11];
        PrivilegedPlugin_logic_harts_0_m_ie_mtie <= CsrAccessPlugin_bus_write_bits[7];
        PrivilegedPlugin_logic_harts_0_m_ie_msie <= CsrAccessPlugin_bus_write_bits[3];
        PerformanceCounterPlugin_logic_interrupt_ie <= CsrAccessPlugin_bus_write_bits[13];
      end
      if(when_CsrAccessPlugin_l375_4) begin
        PrivilegedPlugin_logic_harts_0_m_counteren_tm <= CsrAccessPlugin_bus_write_bits[1];
        PerformanceCounterPlugin_logic_counters_cycle_mcounteren <= CsrAccessPlugin_bus_write_bits[0];
        PerformanceCounterPlugin_logic_counters_instret_mcounteren <= CsrAccessPlugin_bus_write_bits[2];
      end
      if(when_CsrAccessPlugin_l375_5) begin
        PerformanceCounterPlugin_logic_counters_cycle_mcountinhibit <= CsrAccessPlugin_bus_write_bits[0];
        PerformanceCounterPlugin_logic_counters_instret_mcountinhibit <= CsrAccessPlugin_bus_write_bits[2];
      end
      if(PcPlugin_logic_forcedSpawn) begin
        FetchCachelessPlugin_logic_fork_forked_fired <= 1'b0;
      end
      if(FetchCachelessPlugin_logic_fork_forked_fire) begin
        FetchCachelessPlugin_logic_fork_forked_fired <= 1'b1;
      end
      if(fetch_logic_ctrls_0_up_isMoving) begin
        FetchCachelessPlugin_logic_fork_forked_fired <= 1'b0;
      end
      if(FetchCachelessPlugin_logic_fork_translated_valid) begin
        FetchCachelessPlugin_logic_fork_translated_rValidN <= 1'b0;
      end
      if(FetchCachelessPlugin_logic_fork_persistent_ready) begin
        FetchCachelessPlugin_logic_fork_translated_rValidN <= 1'b1;
      end
      FetchCachelessPlugin_logic_bus_cmd_valid_regNext <= FetchCachelessPlugin_logic_bus_cmd_valid;
      FetchCachelessPlugin_logic_bus_cmd_ready_regNext <= FetchCachelessPlugin_logic_bus_cmd_ready;
      `ifndef SYNTHESIS
        `ifdef FORMAL
          assert((! (((! FetchCachelessPlugin_logic_bus_cmd_valid) && FetchCachelessPlugin_logic_bus_cmd_valid_regNext) && (! FetchCachelessPlugin_logic_bus_cmd_ready_regNext)))); // Stream.scala:L658
        `else
          if(!(! (((! FetchCachelessPlugin_logic_bus_cmd_valid) && FetchCachelessPlugin_logic_bus_cmd_valid_regNext) && (! FetchCachelessPlugin_logic_bus_cmd_ready_regNext)))) begin
            $display("FAILURE Stream valid persistence failed"); // Stream.scala:L658
            $finish;
          end
        `endif
      `endif
      FetchCachelessPlugin_logic_bus_cmd_isStall_regNext <= FetchCachelessPlugin_logic_bus_cmd_isStall;
      if(FetchCachelessPlugin_logic_bus_cmd_isStall_regNext) begin
        `ifndef SYNTHESIS
          `ifdef FORMAL
            assert(((FetchCachelessPlugin_logic_bus_cmd_payload_id == FetchCachelessPlugin_logic_bus_cmd_payload_regNext_id) && (FetchCachelessPlugin_logic_bus_cmd_payload_address == FetchCachelessPlugin_logic_bus_cmd_payload_regNext_address))); // Stream.scala:L662
          `else
            if(!((FetchCachelessPlugin_logic_bus_cmd_payload_id == FetchCachelessPlugin_logic_bus_cmd_payload_regNext_id) && (FetchCachelessPlugin_logic_bus_cmd_payload_address == FetchCachelessPlugin_logic_bus_cmd_payload_regNext_address))) begin
              $display("FAILURE Stream payload persistence failed"); // Stream.scala:L662
              $finish;
            end
          `endif
        `endif
      end
      if(FetchCachelessPlugin_logic_trapPort_valid) begin
        FetchCachelessPlugin_logic_join_trapSent <= 1'b1;
      end
      if(fetch_logic_ctrls_1_up_isCancel) begin
        FetchCachelessPlugin_logic_join_trapSent <= 1'b0;
      end
      CsrRamPlugin_logic_readLogic_ohReg <= (CsrRamPlugin_logic_readLogic_port_cmd_valid ? CsrRamPlugin_logic_readLogic_oh : 3'b000);
      CsrRamPlugin_logic_readLogic_busy <= CsrRamPlugin_logic_readLogic_port_cmd_valid;
      CsrRamPlugin_logic_flush_counter <= (CsrRamPlugin_logic_flush_counter + _zz_CsrRamPlugin_logic_flush_counter);
      if(when_RegFilePlugin_l132) begin
        integer_RegFilePlugin_logic_initalizer_counter <= (integer_RegFilePlugin_logic_initalizer_counter + 6'h01);
      end
      _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2 <= _zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1;
      _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2 <= _zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_1;
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_2 <= _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_0_1;
      _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_2 <= _zz_WhiteboxerPlugin_logic_perf_candidatesCountCounters_1_1;
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_2 <= _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_0_1;
      _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_2 <= _zz_WhiteboxerPlugin_logic_perf_dispatchFeedCounters_1_1;
      if(fetch_logic_ctrls_1_up_forgetOne) begin
        fetch_logic_ctrls_1_up_valid <= 1'b0;
      end
      if(fetch_logic_ctrls_0_down_isReady) begin
        fetch_logic_ctrls_1_up_valid <= fetch_logic_ctrls_0_down_isValid;
      end
      if(decode_ctrls_0_down_isReady) begin
        decode_ctrls_1_up_valid <= decode_ctrls_0_down_isValid;
      end
      if(decode_ctrls_0_down_isReady) begin
        decode_ctrls_1_up_LANE_SEL_0 <= decode_ctrls_0_down_LANE_SEL_0;
      end
      if(when_DecodePipelinePlugin_l70) begin
        decode_ctrls_1_up_LANE_SEL_0 <= 1'b0;
      end
      if(execute_ctrl0_down_isReady) begin
        execute_ctrl1_up_LANE_SEL_lane0 <= execute_ctrl0_down_LANE_SEL_lane0;
      end
      if(execute_ctrl1_down_isReady) begin
        execute_ctrl2_up_LANE_SEL_lane0 <= execute_ctrl1_down_LANE_SEL_lane0;
      end
      if(execute_ctrl2_down_isReady) begin
        execute_ctrl3_up_LANE_SEL_lane0 <= execute_ctrl2_down_LANE_SEL_lane0;
        execute_ctrl3_up_LsuCachelessPlugin_WITH_RSP_lane0 <= execute_ctrl2_down_LsuCachelessPlugin_WITH_RSP_lane0;
      end
      if(execute_ctrl3_down_isReady) begin
        execute_ctrl4_up_LANE_SEL_lane0 <= execute_ctrl3_down_LANE_SEL_lane0;
      end
      if(execute_ctrl4_down_isReady) begin
        execute_ctrl5_up_LANE_SEL_lane0 <= execute_ctrl4_down_LANE_SEL_lane0;
      end
      PerformanceCounterPlugin_logic_fsm_stateReg <= PerformanceCounterPlugin_logic_fsm_stateNext;
      case(PerformanceCounterPlugin_logic_fsm_stateReg)
        PerformanceCounterPlugin_logic_fsm_IDLE : begin
        end
        PerformanceCounterPlugin_logic_fsm_READ_LOW : begin
        end
        PerformanceCounterPlugin_logic_fsm_CALC_LOW : begin
          if(when_PerformanceCounterPlugin_l283) begin
            if(_zz_PerformanceCounterPlugin_logic_fsm_cmd_address) begin
              PerformanceCounterPlugin_logic_counters_cycle_value[7] <= 1'b0;
            end
            if(_zz_PerformanceCounterPlugin_logic_fsm_cmd_address_1) begin
              PerformanceCounterPlugin_logic_counters_instret_value[7] <= 1'b0;
            end
          end
        end
        PerformanceCounterPlugin_logic_fsm_READ_HIGH : begin
        end
        PerformanceCounterPlugin_logic_fsm_CALC_HIGH : begin
        end
        PerformanceCounterPlugin_logic_fsm_CSR_WRITE : begin
          if(when_PerformanceCounterPlugin_l261) begin
            if(_zz_PerformanceCounterPlugin_logic_fsm_cmd_address) begin
              PerformanceCounterPlugin_logic_counters_cycle_value <= _zz_PerformanceCounterPlugin_logic_counters_cycle_value_2[7:0];
              PerformanceCounterPlugin_logic_counters_cycle_value[7] <= 1'b0;
            end
            if(_zz_PerformanceCounterPlugin_logic_fsm_cmd_address_1) begin
              PerformanceCounterPlugin_logic_counters_instret_value <= _zz_PerformanceCounterPlugin_logic_counters_instret_value_2[7:0];
              PerformanceCounterPlugin_logic_counters_instret_value[7] <= 1'b0;
            end
          end
          if(when_PerformanceCounterPlugin_l267) begin
            PerformanceCounterPlugin_logic_ignoreNextCommit <= 1'b1;
          end
        end
        default : begin
        end
      endcase
      TrapPlugin_logic_harts_0_trap_fsm_stateReg <= TrapPlugin_logic_harts_0_trap_fsm_stateNext;
      case(TrapPlugin_logic_harts_0_trap_fsm_stateReg)
        TrapPlugin_logic_harts_0_trap_fsm_RUNNING : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_COMPUTE : begin
          TrapPlugin_logic_harts_0_trap_fsm_trapEnterDebug <= 1'b0;
          if(!when_TrapPlugin_l547) begin
            case(TrapPlugin_logic_harts_0_trap_pending_state_code)
              5'h0 : begin
                `ifndef SYNTHESIS
                  `ifdef FORMAL
                    assert((! TrapPlugin_logic_harts_0_trap_fsm_buffer_i_valid)); // TrapPlugin.scala:L603
                  `else
                    if(!(! TrapPlugin_logic_harts_0_trap_fsm_buffer_i_valid)) begin
                      $display("FAILURE "); // TrapPlugin.scala:L603
                      $finish;
                    end
                  `endif
                `endif
              end
              5'h01 : begin
              end
              5'h02 : begin
              end
              5'h04 : begin
              end
              5'h05 : begin
              end
              5'h08 : begin
              end
              5'h06 : begin
              end
              5'h09 : begin
              end
              default : begin
                `ifndef SYNTHESIS
                  `ifdef FORMAL
                    assert(1'b0); // TrapPlugin.scala:L672
                  `else
                    if(!1'b0) begin
                      $display("FAILURE Unexpected trap reason"); // TrapPlugin.scala:L672
                      $finish;
                    end
                  `endif
                `endif
              end
            endcase
          end
        end
        TrapPlugin_logic_harts_0_trap_fsm_TRAP_EPC : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVAL : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTVAL : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_TRAP_HTINST : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_TRAP_TVEC : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_TRAP_WAIT : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_TRAP_APPLY : begin
          PrivilegedPlugin_logic_harts_0_privilege <= TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege;
          case(TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_targetPrivilege)
            3'b011 : begin
              PrivilegedPlugin_logic_harts_0_m_status_mie <= 1'b0;
              PrivilegedPlugin_logic_harts_0_m_status_mpie <= PrivilegedPlugin_logic_harts_0_m_status_mie;
              PrivilegedPlugin_logic_harts_0_m_cause_code <= TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_code;
              PrivilegedPlugin_logic_harts_0_m_cause_interrupt <= TrapPlugin_logic_harts_0_trap_fsm_buffer_trap_interrupt;
            end
            default : begin
            end
          endcase
        end
        TrapPlugin_logic_harts_0_trap_fsm_XRET_EPC : begin
        end
        TrapPlugin_logic_harts_0_trap_fsm_XRET_APPLY : begin
          PrivilegedPlugin_logic_harts_0_privilege <= TrapPlugin_logic_harts_0_trap_pending_xret_targetPrivilege;
          case(switch_TrapPlugin_l976)
            3'b011 : begin
              PrivilegedPlugin_logic_harts_0_m_status_mie <= PrivilegedPlugin_logic_harts_0_m_status_mpie;
              PrivilegedPlugin_logic_harts_0_m_status_mpie <= 1'b1;
            end
            default : begin
            end
          endcase
        end
        TrapPlugin_logic_harts_0_trap_fsm_JUMP : begin
        end
        default : begin
        end
      endcase
      CsrAccessPlugin_logic_fsm_stateReg <= CsrAccessPlugin_logic_fsm_stateNext;
      case(CsrAccessPlugin_logic_fsm_stateReg)
        CsrAccessPlugin_logic_fsm_READ : begin
        end
        CsrAccessPlugin_logic_fsm_WRITE : begin
        end
        CsrAccessPlugin_logic_fsm_COMPLETION : begin
        end
        default : begin
          if(CsrAccessPlugin_logic_fsm_inject_onDecodeDo) begin
            if(CsrAccessPlugin_logic_fsm_inject_sampled) begin
              if(CsrAccessPlugin_logic_fsm_inject_trapReg) begin
                CsrAccessPlugin_logic_fsm_inject_unfreeze <= execute_freeze_valid;
              end
            end
          end
        end
      endcase
      case(CsrAccessPlugin_logic_fsm_stateNext)
        CsrAccessPlugin_logic_fsm_READ : begin
        end
        CsrAccessPlugin_logic_fsm_WRITE : begin
        end
        CsrAccessPlugin_logic_fsm_COMPLETION : begin
          CsrAccessPlugin_logic_fsm_inject_unfreeze <= 1'b1;
        end
        default : begin
        end
      endcase
    end
  end


endmodule

module RegFileMem (
  input  wire          io_writes_0_valid,
  input  wire [4:0]    io_writes_0_address,
  input  wire [31:0]   io_writes_0_data,
  input  wire [15:0]   io_writes_0_uopId,
  input  wire          io_reads_0_valid,
  input  wire [4:0]    io_reads_0_address,
  output wire [31:0]   io_reads_0_data,
  input  wire          io_reads_1_valid,
  input  wire [4:0]    io_reads_1_address,
  output wire [31:0]   io_reads_1_data,
  input  wire          clk,
  input  wire          reset
);

  reg        [31:0]   asMem_ram_spinal_port1;
  reg        [31:0]   asMem_ram_spinal_port2;
  reg                 _zz_1;
  wire                conv_writes_0_valid;
  wire       [4:0]    conv_writes_0_payload_address;
  wire       [31:0]   conv_writes_0_payload_data;
  wire                conv_read_0_cmd_valid;
  wire       [4:0]    conv_read_0_cmd_payload;
  wire       [31:0]   conv_read_0_rsp;
  wire                conv_read_1_cmd_valid;
  wire       [4:0]    conv_read_1_cmd_payload;
  wire       [31:0]   conv_read_1_rsp;
  wire                asMem_writes_0_port_valid;
  wire       [4:0]    asMem_writes_0_port_payload_address;
  wire       [31:0]   asMem_writes_0_port_payload_data;
  wire                asMem_reads_0_sync_port_cmd_valid;
  wire       [4:0]    asMem_reads_0_sync_port_cmd_payload;
  wire       [31:0]   asMem_reads_0_sync_port_rsp;
  wire                asMem_reads_1_sync_port_cmd_valid;
  wire       [4:0]    asMem_reads_1_sync_port_cmd_payload;
  wire       [31:0]   asMem_reads_1_sync_port_rsp;
  reg [31:0] asMem_ram [0:31] /* verilator public */ ;

  always @(posedge clk) begin
    if(_zz_1) begin
      asMem_ram[asMem_writes_0_port_payload_address] <= asMem_writes_0_port_payload_data;
    end
  end

  always @(posedge clk) begin
    if(asMem_reads_0_sync_port_cmd_valid) begin
      asMem_ram_spinal_port1 <= asMem_ram[asMem_reads_0_sync_port_cmd_payload];
    end
  end

  always @(posedge clk) begin
    if(asMem_reads_1_sync_port_cmd_valid) begin
      asMem_ram_spinal_port2 <= asMem_ram[asMem_reads_1_sync_port_cmd_payload];
    end
  end

  always @(*) begin
    _zz_1 = 1'b0;
    if(asMem_writes_0_port_valid) begin
      _zz_1 = 1'b1;
    end
  end

  assign conv_writes_0_valid = io_writes_0_valid;
  assign conv_writes_0_payload_address = io_writes_0_address;
  assign conv_writes_0_payload_data = io_writes_0_data;
  assign conv_read_0_cmd_valid = io_reads_0_valid;
  assign conv_read_0_cmd_payload = io_reads_0_address;
  assign io_reads_0_data = conv_read_0_rsp;
  assign conv_read_1_cmd_valid = io_reads_1_valid;
  assign conv_read_1_cmd_payload = io_reads_1_address;
  assign io_reads_1_data = conv_read_1_rsp;
  assign asMem_writes_0_port_valid = conv_writes_0_valid;
  assign asMem_writes_0_port_payload_address = conv_writes_0_payload_address;
  assign asMem_writes_0_port_payload_data = conv_writes_0_payload_data;
  assign asMem_reads_0_sync_port_rsp = asMem_ram_spinal_port1;
  assign asMem_reads_0_sync_port_cmd_valid = conv_read_0_cmd_valid;
  assign asMem_reads_0_sync_port_cmd_payload = conv_read_0_cmd_payload;
  assign conv_read_0_rsp = asMem_reads_0_sync_port_rsp;
  assign asMem_reads_1_sync_port_rsp = asMem_ram_spinal_port2;
  assign asMem_reads_1_sync_port_cmd_valid = conv_read_1_cmd_valid;
  assign asMem_reads_1_sync_port_cmd_payload = conv_read_1_cmd_payload;
  assign conv_read_1_rsp = asMem_reads_1_sync_port_rsp;

endmodule

module StreamArbiter (
  input  wire          io_inputs_0_valid,
  output wire          io_inputs_0_ready,
  input  wire [31:0]   io_inputs_0_payload_pcOnLastSlice,
  input  wire [31:0]   io_inputs_0_payload_pcTarget,
  input  wire          io_inputs_0_payload_taken,
  input  wire          io_inputs_0_payload_isBranch,
  input  wire          io_inputs_0_payload_isPush,
  input  wire          io_inputs_0_payload_isPop,
  input  wire          io_inputs_0_payload_wasWrong,
  input  wire          io_inputs_0_payload_badPredictedTarget,
  input  wire [15:0]   io_inputs_0_payload_uopId,
  output wire          io_output_valid,
  input  wire          io_output_ready,
  output wire [31:0]   io_output_payload_pcOnLastSlice,
  output wire [31:0]   io_output_payload_pcTarget,
  output wire          io_output_payload_taken,
  output wire          io_output_payload_isBranch,
  output wire          io_output_payload_isPush,
  output wire          io_output_payload_isPop,
  output wire          io_output_payload_wasWrong,
  output wire          io_output_payload_badPredictedTarget,
  output wire [15:0]   io_output_payload_uopId,
  output wire [0:0]    io_chosenOH
);

  wire                maskProposal_0;

  assign io_output_valid = io_inputs_0_valid;
  assign io_inputs_0_ready = io_output_ready;
  assign io_output_payload_pcOnLastSlice = io_inputs_0_payload_pcOnLastSlice;
  assign io_output_payload_pcTarget = io_inputs_0_payload_pcTarget;
  assign io_output_payload_taken = io_inputs_0_payload_taken;
  assign io_output_payload_isBranch = io_inputs_0_payload_isBranch;
  assign io_output_payload_isPush = io_inputs_0_payload_isPush;
  assign io_output_payload_isPop = io_inputs_0_payload_isPop;
  assign io_output_payload_wasWrong = io_inputs_0_payload_wasWrong;
  assign io_output_payload_badPredictedTarget = io_inputs_0_payload_badPredictedTarget;
  assign io_output_payload_uopId = io_inputs_0_payload_uopId;
  assign io_chosenOH = 1'b1;

endmodule

module DivRadix (
  input  wire          io_flush,
  input  wire          io_cmd_valid,
  output wire          io_cmd_ready,
  input  wire [31:0]   io_cmd_payload_a,
  input  wire [31:0]   io_cmd_payload_b,
  input  wire          io_cmd_payload_normalized,
  input  wire [4:0]    io_cmd_payload_iterations,
  output wire          io_rsp_valid,
  input  wire          io_rsp_ready,
  output wire [31:0]   io_rsp_payload_result,
  output wire [31:0]   io_rsp_payload_remain,
  input  wire          clk,
  input  wire          reset
);

  wire       [7:0]    _zz_shifter_1;
  wire       [15:0]   _zz_shifter_2;
  wire       [23:0]   _zz_shifter_3;
  wire       [30:0]   _zz_shifter_4;
  reg        [4:0]    counter;
  reg                 busy;
  wire                io_rsp_fire;
  reg                 done;
  wire                when_DivRadix_l45;
  reg        [31:0]   shifter;
  reg        [31:0]   numerator;
  reg        [31:0]   result;
  reg        [32:0]   div1;
  reg        [32:0]   div3;
  wire       [32:0]   div2;
  wire       [32:0]   shifted;
  wire       [33:0]   sub1;
  wire                when_DivRadix_l64;
  reg        [32:0]   _zz_shifter;
  wire                when_DivRadix_l68;
  wire                slicesZero_0;
  wire                slicesZero_1;
  wire                slicesZero_2;
  wire       [2:0]    shiftSel;
  wire       [3:0]    _zz_sel;
  wire                _zz_sel_1;
  wire                _zz_sel_2;
  wire                _zz_sel_3;
  reg        [3:0]    _zz_sel_4;
  wire       [3:0]    _zz_sel_5;
  wire                _zz_sel_6;
  wire                _zz_sel_7;
  wire                _zz_sel_8;
  wire       [1:0]    sel;
  reg                 wasBusy;
  wire                when_DivRadix_l93;

  assign _zz_shifter_1 = io_cmd_payload_a[31 : 24];
  assign _zz_shifter_2 = io_cmd_payload_a[31 : 16];
  assign _zz_shifter_3 = io_cmd_payload_a[31 : 8];
  assign _zz_shifter_4 = io_cmd_payload_a[31 : 1];
  assign io_rsp_fire = (io_rsp_valid && io_rsp_ready);
  assign when_DivRadix_l45 = (busy && (counter == 5'h1f));
  assign div2 = (div1 <<< 1);
  assign shifted = {shifter,numerator[31 : 31]};
  assign sub1 = ({1'b0,shifted} - {1'b0,div1});
  assign io_rsp_valid = done;
  assign io_rsp_payload_result = result;
  assign io_rsp_payload_remain = shifter;
  assign io_cmd_ready = (! busy);
  assign when_DivRadix_l64 = (! done);
  always @(*) begin
    _zz_shifter = shifted;
    if(when_DivRadix_l68) begin
      _zz_shifter = sub1[32:0];
    end
  end

  assign when_DivRadix_l68 = (! sub1[33]);
  assign slicesZero_0 = (io_cmd_payload_a[15 : 8] == 8'h0);
  assign slicesZero_1 = (io_cmd_payload_a[23 : 16] == 8'h0);
  assign slicesZero_2 = (io_cmd_payload_a[31 : 24] == 8'h0);
  assign shiftSel = {(&slicesZero_2),{(&{slicesZero_2,slicesZero_1}),(&{slicesZero_2,{slicesZero_1,slicesZero_0}})}};
  assign _zz_sel = {1'b1,shiftSel};
  assign _zz_sel_1 = _zz_sel[0];
  assign _zz_sel_2 = _zz_sel[1];
  assign _zz_sel_3 = _zz_sel[2];
  always @(*) begin
    _zz_sel_4[0] = (_zz_sel_1 && (! 1'b0));
    _zz_sel_4[1] = (_zz_sel_2 && (! _zz_sel_1));
    _zz_sel_4[2] = (_zz_sel_3 && (! (|{_zz_sel_2,_zz_sel_1})));
    _zz_sel_4[3] = (_zz_sel[3] && (! (|{_zz_sel_3,{_zz_sel_2,_zz_sel_1}})));
  end

  assign _zz_sel_5 = _zz_sel_4;
  assign _zz_sel_6 = _zz_sel_5[3];
  assign _zz_sel_7 = (_zz_sel_5[1] || _zz_sel_6);
  assign _zz_sel_8 = (_zz_sel_5[2] || _zz_sel_6);
  assign sel = {_zz_sel_8,_zz_sel_7};
  assign when_DivRadix_l93 = (! busy);
  always @(posedge clk or posedge reset) begin
    if(reset) begin
      busy <= 1'b0;
      done <= 1'b0;
      wasBusy <= 1'b0;
    end else begin
      if(io_rsp_fire) begin
        busy <= 1'b0;
      end
      if(when_DivRadix_l45) begin
        done <= 1'b1;
      end
      if(io_rsp_fire) begin
        done <= 1'b0;
      end
      wasBusy <= busy;
      if(when_DivRadix_l93) begin
        busy <= io_cmd_valid;
      end
      if(io_flush) begin
        done <= 1'b0;
        busy <= 1'b0;
      end
    end
  end

  always @(posedge clk) begin
    if(when_DivRadix_l64) begin
      counter <= (counter + 5'h01);
      result <= (result <<< 1);
      if(when_DivRadix_l68) begin
        result[0 : 0] <= 1'b1;
      end
      shifter <= _zz_shifter[31:0];
      numerator <= (numerator <<< 1);
    end
    if(when_DivRadix_l93) begin
      div1 <= {1'd0, io_cmd_payload_b};
      result <= ((io_cmd_payload_b == 32'h0) ? 32'hffffffff : 32'h0);
      case(sel)
        2'b11 : begin
          counter <= 5'h0;
          shifter <= 32'h0;
          numerator <= (io_cmd_payload_a <<< 0);
        end
        2'b10 : begin
          counter <= 5'h08;
          shifter <= {24'd0, _zz_shifter_1};
          numerator <= (io_cmd_payload_a <<< 8);
        end
        2'b01 : begin
          counter <= 5'h10;
          shifter <= {16'd0, _zz_shifter_2};
          numerator <= (io_cmd_payload_a <<< 16);
        end
        default : begin
          counter <= 5'h18;
          shifter <= {8'd0, _zz_shifter_3};
          numerator <= (io_cmd_payload_a <<< 24);
        end
      endcase
      if(io_cmd_payload_normalized) begin
        counter <= (5'h1f - io_cmd_payload_iterations);
        shifter <= {1'd0, _zz_shifter_4};
        numerator <= (io_cmd_payload_a <<< 31);
      end
    end
  end


endmodule
