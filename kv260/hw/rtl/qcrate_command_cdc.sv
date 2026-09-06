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
    input  wire logic        arm_triggered_cmd_i,
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
    output logic             arm_triggered_pulse_o,
    output logic             abort_pulse_o,
    output logic             soft_reset_pulse_o
);

    localparam int COMMAND_WIDTH = 101;

    logic [COMMAND_WIDTH-1:0] command_data_ctrl;
    logic [COMMAND_WIDTH-1:0] command_data_stream;
    logic command_send_ctrl;
    logic command_valid_stream;

    logic [31:0] command_frame_length_stream;
    logic [31:0] command_frame_count_stream;
    logic [31:0] command_stream_mode_stream;
    logic command_continuous_stream;
    logic command_start_stream;
    logic command_arm_triggered_stream;
    logic command_abort_stream;
    logic command_soft_reset_stream;

    assign command_send_ctrl =
        (start_cmd_i || arm_triggered_cmd_i || abort_cmd_i ||
         soft_reset_cmd_i) && !command_busy_o;

    assign command_data_ctrl = {
        frame_length_i,
        frame_count_i,
        stream_mode_i,
        continuous_i,
        start_cmd_i,
        arm_triggered_cmd_i,
        abort_cmd_i,
        soft_reset_cmd_i
    };

    assign {
        command_frame_length_stream,
        command_frame_count_stream,
        command_stream_mode_stream,
        command_continuous_stream,
        command_start_stream,
        command_arm_triggered_stream,
        command_abort_stream,
        command_soft_reset_stream
    } = command_data_stream;

    qcrate_cdc_handshake #(
        .WIDTH (COMMAND_WIDTH)
    ) u_command_handshake (
        .src_clk_i      (ctrl_clk_i),
        .src_rst_n_i    (ctrl_rst_n_i),
        .src_data_i     (command_data_ctrl),
        .src_send_i     (command_send_ctrl),
        .src_busy_o     (command_busy_o),
        .dest_clk_i     (stream_clk_i),
        .dest_rst_n_i   (stream_rst_n_i),
        .dest_data_o    (command_data_stream),
        .dest_valid_o   (command_valid_stream),
        .dest_accept_i  (1'b1)
    );

    always_ff @(posedge stream_clk_i) begin
        if (!stream_rst_n_i) begin
            active_frame_length_o <= 32'h0000_0000;
            active_frame_count_o <= 32'h0000_0000;
            active_stream_mode_o <= 32'h0000_0000;
            active_continuous_o <= 1'b0;
            start_pulse_o <= 1'b0;
            arm_triggered_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;
        end else begin
            start_pulse_o <= 1'b0;
            arm_triggered_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;

            if (command_valid_stream) begin
                active_frame_length_o <= command_frame_length_stream;
                active_frame_count_o <= command_frame_count_stream;
                active_stream_mode_o <= command_stream_mode_stream;
                active_continuous_o <= command_continuous_stream;
                start_pulse_o <= command_start_stream;
                arm_triggered_pulse_o <= command_arm_triggered_stream;
                abort_pulse_o <= command_abort_stream;
                soft_reset_pulse_o <= command_soft_reset_stream;
            end
        end
    end

endmodule

`default_nettype wire
