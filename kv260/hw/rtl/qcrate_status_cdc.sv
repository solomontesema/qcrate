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
    output logic [63:0]      first_sample_time_o
);

    logic        req_toggle_ctrl;
    logic        req_toggle_stream_sync;
    logic        req_toggle_stream_seen;
    logic        ack_toggle_stream;
    logic        ack_toggle_ctrl_sync;
    logic        ack_toggle_ctrl_seen;
    logic        request_pending_ctrl;

    logic        snapshot_busy_stream;
    logic        snapshot_armed_stream;
    logic        snapshot_trigger_seen_stream;
    logic        snapshot_first_sample_time_valid_stream;
    logic [31:0] snapshot_completed_frames_stream;
    logic [31:0] snapshot_current_frame_id_stream;
    logic [31:0] snapshot_current_sample_index_stream;
    logic [31:0] snapshot_stall_cycles_stream;
    logic [31:0] snapshot_trigger_shot_id_stream;
    logic [31:0] snapshot_trigger_count_stream;
    logic [31:0] snapshot_missed_trigger_count_stream;
    logic [63:0] snapshot_trigger_time_stream;
    logic [63:0] snapshot_first_sample_time_stream;

    assign request_pending_ctrl = req_toggle_ctrl ^ ack_toggle_ctrl_seen;

    qcrate_cdc_single u_req_sync (
        .src_clk_i      (ctrl_clk_i),
        .src_signal_i   (req_toggle_ctrl),
        .dst_clk_i      (stream_clk_i),
        .dst_rst_n_i    (stream_rst_n_i),
        .dst_signal_o   (req_toggle_stream_sync)
    );

    qcrate_cdc_single u_ack_sync (
        .src_clk_i      (stream_clk_i),
        .src_signal_i   (ack_toggle_stream),
        .dst_clk_i      (ctrl_clk_i),
        .dst_rst_n_i    (ctrl_rst_n_i),
        .dst_signal_o   (ack_toggle_ctrl_sync)
    );

    always_ff @(posedge stream_clk_i) begin
        if (!stream_rst_n_i) begin
            snapshot_busy_stream <= 1'b0;
            snapshot_armed_stream <= 1'b0;
            snapshot_trigger_seen_stream <= 1'b0;
            snapshot_first_sample_time_valid_stream <= 1'b0;
            snapshot_completed_frames_stream <= 32'h0000_0000;
            snapshot_current_frame_id_stream <= 32'h0000_0000;
            snapshot_current_sample_index_stream <= 32'h0000_0000;
            snapshot_stall_cycles_stream <= 32'h0000_0000;
            snapshot_trigger_shot_id_stream <= 32'h0000_0000;
            snapshot_trigger_count_stream <= 32'h0000_0000;
            snapshot_missed_trigger_count_stream <= 32'h0000_0000;
            snapshot_trigger_time_stream <= 64'h0000_0000_0000_0000;
            snapshot_first_sample_time_stream <= 64'h0000_0000_0000_0000;
            req_toggle_stream_seen <= 1'b0;
            ack_toggle_stream <= 1'b0;
        end else if (req_toggle_stream_sync != req_toggle_stream_seen) begin
            snapshot_busy_stream <= stream_busy_i;
            snapshot_armed_stream <= stream_armed_i;
            snapshot_trigger_seen_stream <= trigger_seen_i;
            snapshot_first_sample_time_valid_stream <=
                first_sample_time_valid_i;
            snapshot_completed_frames_stream <= completed_frames_i;
            snapshot_current_frame_id_stream <= current_frame_id_i;
            snapshot_current_sample_index_stream <= current_sample_index_i;
            snapshot_stall_cycles_stream <= stall_cycles_i;
            snapshot_trigger_shot_id_stream <= trigger_shot_id_i;
            snapshot_trigger_count_stream <= trigger_count_i;
            snapshot_missed_trigger_count_stream <= missed_trigger_count_i;
            snapshot_trigger_time_stream <= trigger_time_i;
            snapshot_first_sample_time_stream <= first_sample_time_i;
            req_toggle_stream_seen <= req_toggle_stream_sync;
            ack_toggle_stream <= req_toggle_stream_sync;
        end
    end

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
            req_toggle_ctrl <= 1'b0;
            ack_toggle_ctrl_seen <= 1'b0;
        end else begin
            if (ack_toggle_ctrl_sync != ack_toggle_ctrl_seen) begin
                stream_busy_o <= snapshot_busy_stream;
                stream_armed_o <= snapshot_armed_stream;
                trigger_seen_o <= snapshot_trigger_seen_stream;
                first_sample_time_valid_o <=
                    snapshot_first_sample_time_valid_stream;
                completed_frames_o <= snapshot_completed_frames_stream;
                current_frame_id_o <= snapshot_current_frame_id_stream;
                current_sample_index_o <= snapshot_current_sample_index_stream;
                stall_cycles_o <= snapshot_stall_cycles_stream;
                trigger_shot_id_o <= snapshot_trigger_shot_id_stream;
                trigger_count_o <= snapshot_trigger_count_stream;
                missed_trigger_count_o <=
                    snapshot_missed_trigger_count_stream;
                trigger_time_o <= snapshot_trigger_time_stream;
                first_sample_time_o <= snapshot_first_sample_time_stream;
                ack_toggle_ctrl_seen <= ack_toggle_ctrl_sync;
            end else if (!request_pending_ctrl) begin
                req_toggle_ctrl <= ~req_toggle_ctrl;
            end
        end
    end

endmodule

`default_nettype wire
