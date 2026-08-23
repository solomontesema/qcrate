`timescale 1ns / 1ps
`default_nettype wire

module qcrate_core #(
    parameter int APB_ADDR_WIDTH  = 32,
    parameter int APB_DATA_WIDTH  = 32,
    parameter int AXIS_DATA_WIDTH = 32
) (
    // ============================================================
    // Clock and reset domains
    // ============================================================

    input  logic                         clk_ctrl_i,       // 100 MHz
    input  logic                         rst_ctrl_n_i,

    input  logic                         clk_stream_i,     // 200 MHz
    input  logic                         rst_stream_n_i,

    // ============================================================
    // APB slave interface
    // ============================================================

    input  logic [APB_ADDR_WIDTH-1:0]    s_apb_paddr_i,
    input  logic                         s_apb_psel_i,
    input  logic                         s_apb_penable_i,
    input  logic                         s_apb_pwrite_i,
    input  logic [APB_DATA_WIDTH-1:0]    s_apb_pwdata_i,

    output logic [APB_DATA_WIDTH-1:0]    s_apb_prdata_o,
    output logic                         s_apb_pready_o,
    output logic                         s_apb_pslverr_o,

    // ============================================================
    // AXI4-Stream master toward DMA
    // ============================================================

    output logic [AXIS_DATA_WIDTH-1:0]   m_axis_tdata_o,
    output logic [(AXIS_DATA_WIDTH/8)-1:0]
                                         m_axis_tkeep_o,
    output logic                         m_axis_tvalid_o,
    input  logic                         m_axis_tready_i,
    output logic                         m_axis_tlast_o,

    // ============================================================
    // Deterministic pulse-sequencer interface
    // ============================================================

    input  logic                         sequence_trigger_i,
    output logic [1:0]                   sequence_pulse_o,

    // ============================================================
    // Interrupt toward PS
    // ============================================================

    output logic                         irq_o
);

    // ============================================================
    // APB slave selections
    // ============================================================

    logic sys_psel;
    logic stream_psel;
    logic sequence_psel;

    logic [31:0] sys_prdata;
    logic        sys_pready;
    logic        sys_pslverr;

    logic [31:0] stream_prdata;
    logic        stream_pready;
    logic        stream_pslverr;

    logic [31:0] sequence_prdata;
    logic        sequence_pready;
    logic        sequence_pslverr;

    localparam int SEQUENCE_MAX_EVENTS = 128;
    localparam int SEQUENCE_ADDR_WIDTH = $clog2(SEQUENCE_MAX_EVENTS);
    localparam int SEQUENCE_COUNT_WIDTH = SEQUENCE_ADDR_WIDTH + 1;

    // ============================================================
    // Configuration in 100 MHz control domain
    // ============================================================

    logic [31:0] cfg_frame_length_ctrl;
    logic [31:0] cfg_frame_count_ctrl;
    logic [31:0] cfg_stream_mode_ctrl;
    logic        cfg_continuous_ctrl;

    logic        start_cmd_ctrl;
    logic        abort_cmd_ctrl;
    logic        soft_reset_cmd_ctrl;
    logic        command_busy_ctrl;

    // ============================================================
    // Active configuration in 200 MHz stream domain
    // ============================================================

    logic [31:0] active_frame_length_stream;
    logic [31:0] active_frame_count_stream;
    logic [31:0] active_stream_mode_stream;
    logic        active_continuous_stream;

    logic        start_pulse_stream;
    logic        abort_pulse_stream;
    logic        soft_reset_pulse_stream;

    // ============================================================
    // Stream-domain status
    // ============================================================

    logic        stream_busy;
    logic        stream_done_pulse;
    logic        stream_error_pulse;

    logic [31:0] completed_frames_stream;
    logic [31:0] current_frame_id_stream;
    logic [31:0] current_sample_index_stream;
    logic [31:0] stall_cycles_stream;

    logic [31:0] dsp_stream_data;
    logic        dsp_stream_valid;
    logic        dsp_stream_ready;
    logic        dsp_stream_enable;
    logic        dsp_stream_clear;

    // ============================================================
    // Control-domain status snapshots
    // ============================================================

    logic        stream_busy_ctrl;
    logic [31:0] completed_frames_ctrl;
    logic [31:0] current_frame_id_ctrl;
    logic [31:0] current_sample_index_ctrl;
    logic [31:0] stall_cycles_ctrl;

    // ============================================================
    // Interrupt signals
    // ============================================================

    logic        frame_done_event_ctrl;
    logic        stream_error_event_ctrl;

    logic [31:0] irq_events_ctrl;
    logic [31:0] irq_enable_ctrl;
    logic [31:0] irq_clear_ctrl;
    logic [31:0] irq_status_ctrl;

    // ============================================================
    // Pulse-sequencer control and dual-clock event memory
    // ============================================================

    logic [SEQUENCE_COUNT_WIDTH-1:0] sequence_event_count_ctrl;
    logic sequence_external_trigger_enable_ctrl;
    logic sequence_arm_cmd_ctrl;
    logic sequence_start_cmd_ctrl;
    logic sequence_abort_cmd_ctrl;
    logic sequence_soft_reset_cmd_ctrl;
    logic sequence_command_busy_ctrl;

    logic sequence_ram_ctrl_en;
    logic sequence_ram_ctrl_write;
    logic [SEQUENCE_ADDR_WIDTH+1:0] sequence_ram_ctrl_addr;
    logic [31:0] sequence_ram_ctrl_wdata;
    logic sequence_ram_ctrl_rvalid;
    logic [31:0] sequence_ram_ctrl_rdata;

    logic [SEQUENCE_COUNT_WIDTH-1:0] sequence_event_count_stream;
    logic sequence_external_trigger_enable_stream;
    logic sequence_arm_pulse_stream;
    logic sequence_start_pulse_stream;
    logic sequence_abort_pulse_stream;
    logic sequence_soft_reset_pulse_stream;

    logic sequence_ram_stream_req;
    logic [SEQUENCE_ADDR_WIDTH-1:0] sequence_ram_stream_addr;
    logic sequence_ram_stream_valid;
    logic [127:0] sequence_ram_stream_data;

    logic sequence_idle_stream;
    logic sequence_validating_stream;
    logic sequence_armed_stream;
    logic sequence_busy_stream;
    logic sequence_faulted_stream;
    logic sequence_done_pulse_stream;
    logic sequence_aborted_pulse_stream;
    logic sequence_fault_pulse_stream;
    logic [7:0] sequence_fault_code_stream;
    logic [SEQUENCE_ADDR_WIDTH-1:0] sequence_fault_index_stream;
    logic [SEQUENCE_ADDR_WIDTH-1:0] sequence_active_index_stream;
    logic [31:0] sequence_completed_shots_stream;
    logic [63:0] sequence_timebase_stream;
    logic [63:0] sequence_start_time_stream;
    logic [63:0] sequence_elapsed_tick_stream;

    logic sequence_idle_ctrl;
    logic sequence_validating_ctrl;
    logic sequence_armed_ctrl;
    logic sequence_busy_ctrl;
    logic sequence_faulted_ctrl;
    logic [7:0] sequence_fault_code_ctrl;
    logic [SEQUENCE_ADDR_WIDTH-1:0] sequence_fault_index_ctrl;
    logic [SEQUENCE_ADDR_WIDTH-1:0] sequence_active_index_ctrl;
    logic [31:0] sequence_completed_shots_ctrl;
    logic [63:0] sequence_timebase_ctrl;
    logic [63:0] sequence_start_time_ctrl;
    logic [63:0] sequence_elapsed_tick_ctrl;

    logic sequence_done_event_ctrl;
    logic sequence_aborted_event_ctrl;
    logic sequence_fault_event_ctrl;

    // ============================================================
    // APB address decoder and response multiplexer
    // ============================================================

    qcrate_apb_fabric u_apb_fabric (
        .pclk_i             (clk_ctrl_i),
        .presetn_i          (rst_ctrl_n_i),

        .paddr_i            (s_apb_paddr_i),
        .psel_i             (s_apb_psel_i),
        .penable_i          (s_apb_penable_i),
        .pwrite_i           (s_apb_pwrite_i),
        .pwdata_i           (s_apb_pwdata_i),

        .sys_psel_o         (sys_psel),
        .stream_psel_o      (stream_psel),
        .sequence_psel_o    (sequence_psel),

        .sys_prdata_i       (sys_prdata),
        .sys_pready_i       (sys_pready),
        .sys_pslverr_i      (sys_pslverr),

        .stream_prdata_i    (stream_prdata),
        .stream_pready_i    (stream_pready),
        .stream_pslverr_i   (stream_pslverr),

        .sequence_prdata_i  (sequence_prdata),
        .sequence_pready_i  (sequence_pready),
        .sequence_pslverr_i (sequence_pslverr),

        .prdata_o           (s_apb_prdata_o),
        .pready_o           (s_apb_pready_o),
        .pslverr_o          (s_apb_pslverr_o)
    );

    // ============================================================
    // System identification registers
    // ============================================================

    qcrate_sys_regs u_sys_regs (
        .pclk_i             (clk_ctrl_i),
        .presetn_i          (rst_ctrl_n_i),

        .paddr_i            (s_apb_paddr_i[11:0]),
        .psel_i             (sys_psel),
        .penable_i          (s_apb_penable_i),
        .pwrite_i           (s_apb_pwrite_i),
        .pwdata_i           (s_apb_pwdata_i),

        .prdata_o           (sys_prdata),
        .pready_o           (sys_pready),
        .pslverr_o          (sys_pslverr)
    );

    // ============================================================
    // Stream control and status registers
    // ============================================================

    qcrate_stream_regs u_stream_regs (
        .pclk_i                     (clk_ctrl_i),
        .presetn_i                  (rst_ctrl_n_i),

        .paddr_i                    (s_apb_paddr_i[11:0]),
        .psel_i                     (stream_psel),
        .penable_i                  (s_apb_penable_i),
        .pwrite_i                   (s_apb_pwrite_i),
        .pwdata_i                   (s_apb_pwdata_i),

        .prdata_o                   (stream_prdata),
        .pready_o                   (stream_pready),
        .pslverr_o                  (stream_pslverr),

        .frame_length_o             (cfg_frame_length_ctrl),
        .frame_count_o              (cfg_frame_count_ctrl),
        .stream_mode_o              (cfg_stream_mode_ctrl),
        .continuous_o               (cfg_continuous_ctrl),

        .start_cmd_o                (start_cmd_ctrl),
        .abort_cmd_o                (abort_cmd_ctrl),
        .soft_reset_cmd_o           (soft_reset_cmd_ctrl),

        .command_busy_i             (command_busy_ctrl),
        .stream_busy_i              (stream_busy_ctrl),
        .completed_frames_i         (completed_frames_ctrl),
        .current_frame_id_i         (current_frame_id_ctrl),
        .current_sample_index_i     (current_sample_index_ctrl),
        .stall_cycles_i             (stall_cycles_ctrl),

        .irq_status_i               (irq_status_ctrl),
        .irq_enable_o               (irq_enable_ctrl),
        .irq_clear_o                (irq_clear_ctrl)
    );

    // ============================================================
    // Pulse-sequencer APB page and event memory control port
    // ============================================================

    qcrate_sequence_regs #(
        .EVENT_ADDR_WIDTH           (SEQUENCE_ADDR_WIDTH),
        .EVENT_COUNT_WIDTH          (SEQUENCE_COUNT_WIDTH),
        .RAM_CTRL_ADDR_WIDTH        (SEQUENCE_ADDR_WIDTH + 2)
    ) u_sequence_regs (
        .pclk_i                     (clk_ctrl_i),
        .presetn_i                  (rst_ctrl_n_i),
        .paddr_i                    (s_apb_paddr_i[11:0]),
        .psel_i                     (sequence_psel),
        .penable_i                  (s_apb_penable_i),
        .pwrite_i                   (s_apb_pwrite_i),
        .pwdata_i                   (s_apb_pwdata_i),
        .prdata_o                   (sequence_prdata),
        .pready_o                   (sequence_pready),
        .pslverr_o                  (sequence_pslverr),

        .event_count_o              (sequence_event_count_ctrl),
        .external_trigger_enable_o  (sequence_external_trigger_enable_ctrl),
        .arm_cmd_o                  (sequence_arm_cmd_ctrl),
        .start_cmd_o                (sequence_start_cmd_ctrl),
        .abort_cmd_o                (sequence_abort_cmd_ctrl),
        .soft_reset_cmd_o           (sequence_soft_reset_cmd_ctrl),
        .command_busy_i             (sequence_command_busy_ctrl),
        .done_event_i               (sequence_done_event_ctrl),
        .aborted_event_i            (sequence_aborted_event_ctrl),

        .idle_i                     (sequence_idle_ctrl),
        .validating_i               (sequence_validating_ctrl),
        .armed_i                    (sequence_armed_ctrl),
        .busy_i                     (sequence_busy_ctrl),
        .faulted_i                  (sequence_faulted_ctrl),
        .fault_code_i               (sequence_fault_code_ctrl),
        .fault_event_index_i        (sequence_fault_index_ctrl),
        .active_event_index_i       (sequence_active_index_ctrl),
        .completed_shots_i          (sequence_completed_shots_ctrl),
        .timebase_i                 (sequence_timebase_ctrl),
        .start_time_i               (sequence_start_time_ctrl),
        .elapsed_tick_i             (sequence_elapsed_tick_ctrl),

        .ram_en_o                   (sequence_ram_ctrl_en),
        .ram_write_o                (sequence_ram_ctrl_write),
        .ram_addr_o                 (sequence_ram_ctrl_addr),
        .ram_wdata_o                (sequence_ram_ctrl_wdata),
        .ram_rvalid_i               (sequence_ram_ctrl_rvalid),
        .ram_rdata_i                (sequence_ram_ctrl_rdata)
    );

    qcrate_sequence_ram #(
        .MAX_EVENTS                 (SEQUENCE_MAX_EVENTS),
        .EVENT_ADDR_WIDTH           (SEQUENCE_ADDR_WIDTH),
        .CTRL_ADDR_WIDTH            (SEQUENCE_ADDR_WIDTH + 2)
    ) u_sequence_ram (
        .ctrl_clk_i                 (clk_ctrl_i),
        .ctrl_rst_n_i               (rst_ctrl_n_i),
        .ctrl_en_i                  (sequence_ram_ctrl_en),
        .ctrl_write_i               (sequence_ram_ctrl_write),
        .ctrl_addr_i                (sequence_ram_ctrl_addr),
        .ctrl_wdata_i               (sequence_ram_ctrl_wdata),
        .ctrl_rvalid_o              (sequence_ram_ctrl_rvalid),
        .ctrl_rdata_o               (sequence_ram_ctrl_rdata),

        .stream_clk_i               (clk_stream_i),
        .stream_rst_n_i             (rst_stream_n_i),
        .stream_req_i               (sequence_ram_stream_req),
        .stream_addr_i              (sequence_ram_stream_addr),
        .stream_valid_o             (sequence_ram_stream_valid),
        .stream_data_o              (sequence_ram_stream_data)
    );

    qcrate_sequence_command_cdc #(
        .EVENT_COUNT_WIDTH          (SEQUENCE_COUNT_WIDTH)
    ) u_sequence_command_cdc (
        .ctrl_clk_i                 (clk_ctrl_i),
        .ctrl_rst_n_i               (rst_ctrl_n_i),
        .event_count_i              (sequence_event_count_ctrl),
        .external_trigger_enable_i  (sequence_external_trigger_enable_ctrl),
        .arm_cmd_i                  (sequence_arm_cmd_ctrl),
        .start_cmd_i                (sequence_start_cmd_ctrl),
        .abort_cmd_i                (sequence_abort_cmd_ctrl),
        .soft_reset_cmd_i           (sequence_soft_reset_cmd_ctrl),
        .command_busy_o             (sequence_command_busy_ctrl),

        .stream_clk_i               (clk_stream_i),
        .stream_rst_n_i             (rst_stream_n_i),
        .active_event_count_o       (sequence_event_count_stream),
        .active_external_trigger_enable_o
                                    (sequence_external_trigger_enable_stream),
        .arm_pulse_o                (sequence_arm_pulse_stream),
        .start_pulse_o              (sequence_start_pulse_stream),
        .abort_pulse_o              (sequence_abort_pulse_stream),
        .soft_reset_pulse_o         (sequence_soft_reset_pulse_stream)
    );

    qcrate_timebase u_timebase (
        .clk_i                      (clk_stream_i),
        .rst_n_i                    (rst_stream_n_i),
        .time_o                     (sequence_timebase_stream)
    );

    qcrate_sequence_engine #(
        .MAX_EVENTS                 (SEQUENCE_MAX_EVENTS),
        .EVENT_ADDR_WIDTH           (SEQUENCE_ADDR_WIDTH)
    ) u_sequence_engine (
        .clk_i                      (clk_stream_i),
        .rst_n_i                    (rst_stream_n_i),
        .arm_i                      (sequence_arm_pulse_stream),
        .start_i                    (sequence_start_pulse_stream),
        .abort_i                    (sequence_abort_pulse_stream),
        .soft_reset_i               (sequence_soft_reset_pulse_stream),
        .external_trigger_enable_i  (sequence_external_trigger_enable_stream),
        .external_trigger_i         (sequence_trigger_i),
        .event_count_i              (sequence_event_count_stream),
        .timebase_i                 (sequence_timebase_stream),
        .event_req_o                (sequence_ram_stream_req),
        .event_addr_o               (sequence_ram_stream_addr),
        .event_valid_i              (sequence_ram_stream_valid),
        .event_data_i               (sequence_ram_stream_data),
        .idle_o                     (sequence_idle_stream),
        .validating_o               (sequence_validating_stream),
        .armed_o                    (sequence_armed_stream),
        .busy_o                     (sequence_busy_stream),
        .faulted_o                  (sequence_faulted_stream),
        .done_pulse_o               (sequence_done_pulse_stream),
        .aborted_pulse_o            (sequence_aborted_pulse_stream),
        .fault_pulse_o              (sequence_fault_pulse_stream),
        .fault_code_o               (sequence_fault_code_stream),
        .fault_event_index_o        (sequence_fault_index_stream),
        .active_event_index_o       (sequence_active_index_stream),
        .completed_shots_o          (sequence_completed_shots_stream),
        .start_time_o               (sequence_start_time_stream),
        .elapsed_tick_o             (sequence_elapsed_tick_stream),
        .pulse_o                    (sequence_pulse_o)
    );

    qcrate_sequence_status_cdc #(
        .EVENT_ADDR_WIDTH           (SEQUENCE_ADDR_WIDTH)
    ) u_sequence_status_cdc (
        .stream_clk_i               (clk_stream_i),
        .stream_rst_n_i             (rst_stream_n_i),
        .idle_i                     (sequence_idle_stream),
        .validating_i               (sequence_validating_stream),
        .armed_i                    (sequence_armed_stream),
        .busy_i                     (sequence_busy_stream),
        .faulted_i                  (sequence_faulted_stream),
        .fault_code_i               (sequence_fault_code_stream),
        .fault_event_index_i        (sequence_fault_index_stream),
        .active_event_index_i       (sequence_active_index_stream),
        .completed_shots_i          (sequence_completed_shots_stream),
        .timebase_i                 (sequence_timebase_stream),
        .start_time_i               (sequence_start_time_stream),
        .elapsed_tick_i             (sequence_elapsed_tick_stream),

        .ctrl_clk_i                 (clk_ctrl_i),
        .ctrl_rst_n_i               (rst_ctrl_n_i),
        .idle_o                     (sequence_idle_ctrl),
        .validating_o               (sequence_validating_ctrl),
        .armed_o                    (sequence_armed_ctrl),
        .busy_o                     (sequence_busy_ctrl),
        .faulted_o                  (sequence_faulted_ctrl),
        .fault_code_o               (sequence_fault_code_ctrl),
        .fault_event_index_o        (sequence_fault_index_ctrl),
        .active_event_index_o       (sequence_active_index_ctrl),
        .completed_shots_o          (sequence_completed_shots_ctrl),
        .timebase_o                 (sequence_timebase_ctrl),
        .start_time_o               (sequence_start_time_ctrl),
        .elapsed_tick_o             (sequence_elapsed_tick_ctrl)
    );

    qcrate_sequence_event_cdc u_sequence_event_cdc (
        .src_clk_i                  (clk_stream_i),
        .src_rst_n_i                (rst_stream_n_i),
        .done_pulse_i               (sequence_done_pulse_stream),
        .aborted_pulse_i            (sequence_aborted_pulse_stream),
        .fault_pulse_i              (sequence_fault_pulse_stream),
        .dst_clk_i                  (clk_ctrl_i),
        .dst_rst_n_i                (rst_ctrl_n_i),
        .done_pulse_o               (sequence_done_event_ctrl),
        .aborted_pulse_o            (sequence_aborted_event_ctrl),
        .fault_pulse_o              (sequence_fault_event_ctrl)
    );

    // ============================================================
    // Atomic control-domain to stream-domain command crossing
    // ============================================================

    qcrate_command_cdc u_command_cdc (
        .ctrl_clk_i                 (clk_ctrl_i),
        .ctrl_rst_n_i               (rst_ctrl_n_i),

        .frame_length_i             (cfg_frame_length_ctrl),
        .frame_count_i              (cfg_frame_count_ctrl),
        .stream_mode_i              (cfg_stream_mode_ctrl),
        .continuous_i               (cfg_continuous_ctrl),

        .start_cmd_i                (start_cmd_ctrl),
        .abort_cmd_i                (abort_cmd_ctrl),
        .soft_reset_cmd_i           (soft_reset_cmd_ctrl),
        .command_busy_o             (command_busy_ctrl),

        .stream_clk_i               (clk_stream_i),
        .stream_rst_n_i             (rst_stream_n_i),

        .active_frame_length_o      (active_frame_length_stream),
        .active_frame_count_o       (active_frame_count_stream),
        .active_stream_mode_o       (active_stream_mode_stream),
        .active_continuous_o        (active_continuous_stream),

        .start_pulse_o              (start_pulse_stream),
        .abort_pulse_o              (abort_pulse_stream),
        .soft_reset_pulse_o         (soft_reset_pulse_stream)
    );

    // ============================================================
    // Main stream-producing engine
    // ============================================================

    assign dsp_stream_clear = start_pulse_stream || soft_reset_pulse_stream;

    qcrate_dsp_chain #(
        // Vivado stages tracked .mem sources into each synthesis run.
        .SINE_LUT_FILE             ("sine_quarter_q1_15.mem")
    ) u_dsp_chain (
        .clk_i                      (clk_stream_i),
        .rst_n_i                    (rst_stream_n_i),
        .enable_i                   (dsp_stream_enable),
        .clear_i                    (dsp_stream_clear),
        .m_data_o                   (dsp_stream_data),
        .m_valid_o                  (dsp_stream_valid),
        .m_ready_i                  (dsp_stream_ready)
    );

    qcrate_stream_engine #(
        .AXIS_DATA_WIDTH            (AXIS_DATA_WIDTH)
    ) u_stream_engine (
        .clk_i                      (clk_stream_i),
        .rst_n_i                    (rst_stream_n_i),

        .start_i                    (start_pulse_stream),
        .abort_i                    (abort_pulse_stream),
        .soft_reset_i               (soft_reset_pulse_stream),

        .frame_length_i             (active_frame_length_stream),
        .frame_count_i              (active_frame_count_stream),
        .stream_mode_i              (active_stream_mode_stream),
        .continuous_i               (active_continuous_stream),

        .dsp_tdata_i                (dsp_stream_data),
        .dsp_tvalid_i               (dsp_stream_valid),
        .dsp_enable_o               (dsp_stream_enable),
        .dsp_tready_o               (dsp_stream_ready),

        .busy_o                     (stream_busy),
        .done_pulse_o               (stream_done_pulse),
        .error_pulse_o              (stream_error_pulse),

        .completed_frames_o         (completed_frames_stream),
        .current_frame_id_o         (current_frame_id_stream),
        .current_sample_index_o     (current_sample_index_stream),
        .stall_cycles_o             (stall_cycles_stream),

        .m_axis_tdata_o             (m_axis_tdata_o),
        .m_axis_tkeep_o             (m_axis_tkeep_o),
        .m_axis_tvalid_o            (m_axis_tvalid_o),
        .m_axis_tready_i            (m_axis_tready_i),
        .m_axis_tlast_o             (m_axis_tlast_o)
    );

    // ============================================================
    // Coherent stream-domain status snapshot
    // ============================================================

    qcrate_status_cdc u_status_cdc (
        .stream_clk_i               (clk_stream_i),
        .stream_rst_n_i             (rst_stream_n_i),

        .stream_busy_i              (stream_busy),
        .completed_frames_i         (completed_frames_stream),
        .current_frame_id_i         (current_frame_id_stream),
        .current_sample_index_i     (current_sample_index_stream),
        .stall_cycles_i             (stall_cycles_stream),

        .ctrl_clk_i                 (clk_ctrl_i),
        .ctrl_rst_n_i               (rst_ctrl_n_i),

        .stream_busy_o              (stream_busy_ctrl),
        .completed_frames_o         (completed_frames_ctrl),
        .current_frame_id_o         (current_frame_id_ctrl),
        .current_sample_index_o     (current_sample_index_ctrl),
        .stall_cycles_o             (stall_cycles_ctrl)
    );

    // ============================================================
    // Event synchronization into control domain
    // ============================================================

    qcrate_event_cdc u_event_cdc (
        .src_clk_i                  (clk_stream_i),
        .src_rst_n_i                (rst_stream_n_i),
        .frame_done_pulse_i         (stream_done_pulse),
        .stream_error_pulse_i       (stream_error_pulse),

        .dst_clk_i                  (clk_ctrl_i),
        .dst_rst_n_i                (rst_ctrl_n_i),
        .frame_done_pulse_o         (frame_done_event_ctrl),
        .stream_error_pulse_o       (stream_error_event_ctrl)
    );

    assign irq_events_ctrl = {
        27'd0,
        sequence_fault_event_ctrl,
        sequence_aborted_event_ctrl,
        sequence_done_event_ctrl,
        stream_error_event_ctrl,
        frame_done_event_ctrl
    };

    // ============================================================
    // Sticky interrupt controller
    // ============================================================

    qcrate_irq_ctrl u_irq_ctrl (
        .clk_i                      (clk_ctrl_i),
        .rst_n_i                    (rst_ctrl_n_i),

        .events_i                   (irq_events_ctrl),
        .enable_i                   (irq_enable_ctrl),
        .clear_i                    (irq_clear_ctrl),

        .status_o                   (irq_status_ctrl),
        .irq_o                      (irq_o)
    );

endmodule

`default_nettype wire
