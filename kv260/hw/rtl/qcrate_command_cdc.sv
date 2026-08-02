`timescale 1ns / 1ps
`default_nettype none

module qcrate_command_cdc (
    input  wire logic        ctrl_clk_i,
    input  wire logic        ctrl_rst_n_i,

    input  wire logic [31:0] frame_length_i,
    input  wire logic [31:0] frame_count_i,
    input  wire logic [31:0] stream_mode_i,
    input  wire logic        continuous_i,

    input  wire logic        start_cmd_i,
    input  wire logic        abort_cmd_i,
    input  wire logic        soft_reset_cmd_i,
    output logic             command_busy_o,

    input  wire logic        stream_clk_i,
    input  wire logic        stream_rst_n_i,

    output logic [31:0]      active_frame_length_o,
    output logic [31:0]      active_frame_count_o,
    output logic [31:0]      active_stream_mode_o,
    output logic             active_continuous_o,

    output logic             start_pulse_o,
    output logic             abort_pulse_o,
    output logic             soft_reset_pulse_o
);

    logic [31:0] mailbox_frame_length_ctrl;
    logic [31:0] mailbox_frame_count_ctrl;
    logic [31:0] mailbox_stream_mode_ctrl;
    logic        mailbox_continuous_ctrl;
    logic        mailbox_start_ctrl;
    logic        mailbox_abort_ctrl;
    logic        mailbox_soft_reset_ctrl;

    logic        req_toggle_ctrl;
    logic        ack_toggle_stream;
    logic        req_toggle_stream_sync;
    logic        ack_toggle_ctrl_sync;
    logic        req_toggle_stream_seen;
    logic        command_fire_ctrl;

    assign command_busy_o = req_toggle_ctrl ^ ack_toggle_ctrl_sync;
    assign command_fire_ctrl = (start_cmd_i || abort_cmd_i || soft_reset_cmd_i) &&
                               !command_busy_o;

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i) begin
            mailbox_frame_length_ctrl <= 32'h0000_0000;
            mailbox_frame_count_ctrl <= 32'h0000_0000;
            mailbox_stream_mode_ctrl <= 32'h0000_0000;
            mailbox_continuous_ctrl <= 1'b0;
            mailbox_start_ctrl <= 1'b0;
            mailbox_abort_ctrl <= 1'b0;
            mailbox_soft_reset_ctrl <= 1'b0;
            req_toggle_ctrl <= 1'b0;
        end else if (command_fire_ctrl) begin
            mailbox_frame_length_ctrl <= frame_length_i;
            mailbox_frame_count_ctrl <= frame_count_i;
            mailbox_stream_mode_ctrl <= stream_mode_i;
            mailbox_continuous_ctrl <= continuous_i;
            mailbox_start_ctrl <= start_cmd_i;
            mailbox_abort_ctrl <= abort_cmd_i;
            mailbox_soft_reset_ctrl <= soft_reset_cmd_i;
            req_toggle_ctrl <= ~req_toggle_ctrl;
        end
    end

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
            active_frame_length_o <= 32'h0000_0000;
            active_frame_count_o <= 32'h0000_0000;
            active_stream_mode_o <= 32'h0000_0000;
            active_continuous_o <= 1'b0;
            start_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;
            req_toggle_stream_seen <= 1'b0;
            ack_toggle_stream <= 1'b0;
        end else begin
            start_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;

            if (req_toggle_stream_sync != req_toggle_stream_seen) begin
                active_frame_length_o <= mailbox_frame_length_ctrl;
                active_frame_count_o <= mailbox_frame_count_ctrl;
                active_stream_mode_o <= mailbox_stream_mode_ctrl;
                active_continuous_o <= mailbox_continuous_ctrl;
                start_pulse_o <= mailbox_start_ctrl;
                abort_pulse_o <= mailbox_abort_ctrl;
                soft_reset_pulse_o <= mailbox_soft_reset_ctrl;
                req_toggle_stream_seen <= req_toggle_stream_sync;
                ack_toggle_stream <= req_toggle_stream_sync;
            end
        end
    end

endmodule

`default_nettype wire
