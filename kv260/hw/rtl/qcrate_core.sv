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
    // Interrupt toward PS
    // ============================================================

    output logic                         irq_o
);

    // ============================================================
    // APB slave selections
    // ============================================================

    logic sys_psel;
    logic stream_psel;

    logic [31:0] sys_prdata;
    logic        sys_pready;
    logic        sys_pslverr;

    logic [31:0] stream_prdata;
    logic        stream_pready;
    logic        stream_pslverr;

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

        .sys_prdata_i       (sys_prdata),
        .sys_pready_i       (sys_pready),
        .sys_pslverr_i      (sys_pslverr),

        .stream_prdata_i    (stream_prdata),
        .stream_pready_i    (stream_pready),
        .stream_pslverr_i   (stream_pslverr),

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

    qcrate_stream_engine u_stream_engine (
        .clk_i                      (clk_stream_i),
        .rst_n_i                    (rst_stream_n_i),

        .start_i                    (start_pulse_stream),
        .abort_i                    (abort_pulse_stream),
        .soft_reset_i               (soft_reset_pulse_stream),

        .frame_length_i             (active_frame_length_stream),
        .frame_count_i              (active_frame_count_stream),
        .stream_mode_i              (active_stream_mode_stream),
        .continuous_i               (active_continuous_stream),

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
        30'd0,
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