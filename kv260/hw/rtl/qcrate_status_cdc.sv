`timescale 1ns / 1ps
`default_nettype none

module qcrate_status_cdc (
    input  wire logic        stream_clk_i,
    input  wire logic        stream_rst_n_i,

    input  wire logic        stream_busy_i,
    input  wire logic [31:0] completed_frames_i,
    input  wire logic [31:0] current_frame_id_i,
    input  wire logic [31:0] current_sample_index_i,
    input  wire logic [31:0] stall_cycles_i,

    input  wire logic        ctrl_clk_i,
    input  wire logic        ctrl_rst_n_i,

    output logic             stream_busy_o,
    output logic [31:0]      completed_frames_o,
    output logic [31:0]      current_frame_id_o,
    output logic [31:0]      current_sample_index_o,
    output logic [31:0]      stall_cycles_o
);

    logic        req_toggle_ctrl;
    logic        req_toggle_stream_sync;
    logic        req_toggle_stream_seen;
    logic        ack_toggle_stream;
    logic        ack_toggle_ctrl_sync;
    logic        ack_toggle_ctrl_seen;
    logic        request_pending_ctrl;

    logic        snapshot_busy_stream;
    logic [31:0] snapshot_completed_frames_stream;
    logic [31:0] snapshot_current_frame_id_stream;
    logic [31:0] snapshot_current_sample_index_stream;
    logic [31:0] snapshot_stall_cycles_stream;

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
            snapshot_completed_frames_stream <= 32'h0000_0000;
            snapshot_current_frame_id_stream <= 32'h0000_0000;
            snapshot_current_sample_index_stream <= 32'h0000_0000;
            snapshot_stall_cycles_stream <= 32'h0000_0000;
            req_toggle_stream_seen <= 1'b0;
            ack_toggle_stream <= 1'b0;
        end else if (req_toggle_stream_sync != req_toggle_stream_seen) begin
            snapshot_busy_stream <= stream_busy_i;
            snapshot_completed_frames_stream <= completed_frames_i;
            snapshot_current_frame_id_stream <= current_frame_id_i;
            snapshot_current_sample_index_stream <= current_sample_index_i;
            snapshot_stall_cycles_stream <= stall_cycles_i;
            req_toggle_stream_seen <= req_toggle_stream_sync;
            ack_toggle_stream <= req_toggle_stream_sync;
        end
    end

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i) begin
            stream_busy_o <= 1'b0;
            completed_frames_o <= 32'h0000_0000;
            current_frame_id_o <= 32'h0000_0000;
            current_sample_index_o <= 32'h0000_0000;
            stall_cycles_o <= 32'h0000_0000;
            req_toggle_ctrl <= 1'b0;
            ack_toggle_ctrl_seen <= 1'b0;
        end else begin
            if (ack_toggle_ctrl_sync != ack_toggle_ctrl_seen) begin
                stream_busy_o <= snapshot_busy_stream;
                completed_frames_o <= snapshot_completed_frames_stream;
                current_frame_id_o <= snapshot_current_frame_id_stream;
                current_sample_index_o <= snapshot_current_sample_index_stream;
                stall_cycles_o <= snapshot_stall_cycles_stream;
                ack_toggle_ctrl_seen <= ack_toggle_ctrl_sync;
            end else if (!request_pending_ctrl) begin
                req_toggle_ctrl <= ~req_toggle_ctrl;
            end
        end
    end

endmodule

`default_nettype wire
