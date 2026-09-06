`timescale 1ns / 1ps
`default_nettype none

module qcrate_status_cdc (
    input  wire logic        stream_clk_i,
    input  wire logic        stream_rst_n_i,

    input  wire logic        stream_busy_i,
    input  wire logic        stream_armed_i,
    input  wire logic        trigger_seen_i,
    input  wire logic        first_sample_time_valid_i,
    input  wire logic [31:0] completed_frames_i,
    input  wire logic [31:0] current_frame_id_i,
    input  wire logic [31:0] current_sample_index_i,
    input  wire logic [31:0] stall_cycles_i,
    input  wire logic [31:0] trigger_shot_id_i,
    input  wire logic [31:0] trigger_count_i,
    input  wire logic [31:0] missed_trigger_count_i,
    input  wire logic [63:0] trigger_time_i,
    input  wire logic [63:0] first_sample_time_i,
    input  wire logic [63:0] capture_config_id_i,

    input  wire logic        ctrl_clk_i,
    input  wire logic        ctrl_rst_n_i,

    output logic             stream_busy_o,
    output logic             stream_armed_o,
    output logic             trigger_seen_o,
    output logic             first_sample_time_valid_o,
    output logic [31:0]      completed_frames_o,
    output logic [31:0]      current_frame_id_o,
    output logic [31:0]      current_sample_index_o,
    output logic [31:0]      stall_cycles_o,
    output logic [31:0]      trigger_shot_id_o,
    output logic [31:0]      trigger_count_o,
    output logic [31:0]      missed_trigger_count_o,
    output logic [63:0]      trigger_time_o,
    output logic [63:0]      first_sample_time_o,
    output logic [63:0]      capture_config_id_o
);

    localparam int STATUS_WIDTH = 420;

    logic [STATUS_WIDTH-1:0] status_data_stream;
    logic [STATUS_WIDTH-1:0] status_data_ctrl;
    logic status_busy_stream;
    logic status_valid_ctrl;

    logic snapshot_busy_ctrl;
    logic snapshot_armed_ctrl;
    logic snapshot_trigger_seen_ctrl;
    logic snapshot_first_sample_time_valid_ctrl;
    logic [31:0] snapshot_completed_frames_ctrl;
    logic [31:0] snapshot_current_frame_id_ctrl;
    logic [31:0] snapshot_current_sample_index_ctrl;
    logic [31:0] snapshot_stall_cycles_ctrl;
    logic [31:0] snapshot_trigger_shot_id_ctrl;
    logic [31:0] snapshot_trigger_count_ctrl;
    logic [31:0] snapshot_missed_trigger_count_ctrl;
    logic [63:0] snapshot_trigger_time_ctrl;
    logic [63:0] snapshot_first_sample_time_ctrl;
    logic [63:0] snapshot_capture_config_id_ctrl;

    assign status_data_stream = {
        stream_busy_i,
        stream_armed_i,
        trigger_seen_i,
        first_sample_time_valid_i,
        completed_frames_i,
        current_frame_id_i,
        current_sample_index_i,
        stall_cycles_i,
        trigger_shot_id_i,
        trigger_count_i,
        missed_trigger_count_i,
        trigger_time_i,
        first_sample_time_i,
        capture_config_id_i
    };

    assign {
        snapshot_busy_ctrl,
        snapshot_armed_ctrl,
        snapshot_trigger_seen_ctrl,
        snapshot_first_sample_time_valid_ctrl,
        snapshot_completed_frames_ctrl,
        snapshot_current_frame_id_ctrl,
        snapshot_current_sample_index_ctrl,
        snapshot_stall_cycles_ctrl,
        snapshot_trigger_shot_id_ctrl,
        snapshot_trigger_count_ctrl,
        snapshot_missed_trigger_count_ctrl,
        snapshot_trigger_time_ctrl,
        snapshot_first_sample_time_ctrl,
        snapshot_capture_config_id_ctrl
    } = status_data_ctrl;

    qcrate_cdc_handshake #(
        .WIDTH (STATUS_WIDTH)
    ) u_status_handshake (
        .src_clk_i      (stream_clk_i),
        .src_rst_n_i    (stream_rst_n_i),
        .src_data_i     (status_data_stream),
        .src_send_i     (!status_busy_stream),
        .src_busy_o     (status_busy_stream),
        .dest_clk_i     (ctrl_clk_i),
        .dest_rst_n_i   (ctrl_rst_n_i),
        .dest_data_o    (status_data_ctrl),
        .dest_valid_o   (status_valid_ctrl),
        .dest_accept_i  (1'b1)
    );

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i) begin
            stream_busy_o <= 1'b0;
            stream_armed_o <= 1'b0;
            trigger_seen_o <= 1'b0;
            first_sample_time_valid_o <= 1'b0;
            completed_frames_o <= 32'h0000_0000;
            current_frame_id_o <= 32'h0000_0000;
            current_sample_index_o <= 32'h0000_0000;
            stall_cycles_o <= 32'h0000_0000;
            trigger_shot_id_o <= 32'h0000_0000;
            trigger_count_o <= 32'h0000_0000;
            missed_trigger_count_o <= 32'h0000_0000;
            trigger_time_o <= 64'h0000_0000_0000_0000;
            first_sample_time_o <= 64'h0000_0000_0000_0000;
            capture_config_id_o <= 64'h0000_0000_0000_0000;
        end else if (status_valid_ctrl) begin
            stream_busy_o <= snapshot_busy_ctrl;
            stream_armed_o <= snapshot_armed_ctrl;
            trigger_seen_o <= snapshot_trigger_seen_ctrl;
            first_sample_time_valid_o <=
                snapshot_first_sample_time_valid_ctrl;
            completed_frames_o <= snapshot_completed_frames_ctrl;
            current_frame_id_o <= snapshot_current_frame_id_ctrl;
            current_sample_index_o <= snapshot_current_sample_index_ctrl;
            stall_cycles_o <= snapshot_stall_cycles_ctrl;
            trigger_shot_id_o <= snapshot_trigger_shot_id_ctrl;
            trigger_count_o <= snapshot_trigger_count_ctrl;
            missed_trigger_count_o <= snapshot_missed_trigger_count_ctrl;
            trigger_time_o <= snapshot_trigger_time_ctrl;
            first_sample_time_o <= snapshot_first_sample_time_ctrl;
            capture_config_id_o <= snapshot_capture_config_id_ctrl;
        end
    end

endmodule

`default_nettype wire
