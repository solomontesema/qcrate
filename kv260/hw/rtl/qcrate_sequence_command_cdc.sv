`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_command_cdc #(
    parameter int EVENT_COUNT_WIDTH = 8
) (
    input  wire logic                         ctrl_clk_i,
    input  wire logic                         ctrl_rst_n_i,
    input  wire logic [EVENT_COUNT_WIDTH-1:0] event_count_i,
    input  wire logic                         external_trigger_enable_i,
    input  wire logic                         arm_cmd_i,
    input  wire logic                         start_cmd_i,
    input  wire logic                         abort_cmd_i,
    input  wire logic                         soft_reset_cmd_i,
    output logic                              command_busy_o,

    input  wire logic                         stream_clk_i,
    input  wire logic                         stream_rst_n_i,
    output logic [EVENT_COUNT_WIDTH-1:0]       active_event_count_o,
    output logic                              active_external_trigger_enable_o,
    output logic                              arm_pulse_o,
    output logic                              start_pulse_o,
    output logic                              abort_pulse_o,
    output logic                              soft_reset_pulse_o
);

    localparam int COMMAND_WIDTH = EVENT_COUNT_WIDTH + 5;

    logic [COMMAND_WIDTH-1:0] command_data_ctrl;
    logic [COMMAND_WIDTH-1:0] command_data_stream;
    logic command_send_ctrl;
    logic command_valid_stream;

    logic [EVENT_COUNT_WIDTH-1:0] command_event_count_stream;
    logic command_external_trigger_enable_stream;
    logic command_arm_stream;
    logic command_start_stream;
    logic command_abort_stream;
    logic command_soft_reset_stream;

    assign command_send_ctrl =
        (arm_cmd_i || start_cmd_i || abort_cmd_i || soft_reset_cmd_i) &&
        !command_busy_o;

    assign command_data_ctrl = {
        event_count_i,
        external_trigger_enable_i,
        arm_cmd_i,
        start_cmd_i,
        abort_cmd_i,
        soft_reset_cmd_i
    };

    assign {
        command_event_count_stream,
        command_external_trigger_enable_stream,
        command_arm_stream,
        command_start_stream,
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
            active_event_count_o <= '0;
            active_external_trigger_enable_o <= 1'b0;
            arm_pulse_o <= 1'b0;
            start_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;
        end else begin
            arm_pulse_o <= 1'b0;
            start_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;

            if (command_valid_stream) begin
                active_event_count_o <= command_event_count_stream;
                active_external_trigger_enable_o <=
                    command_external_trigger_enable_stream;
                arm_pulse_o <= command_arm_stream;
                start_pulse_o <= command_start_stream;
                abort_pulse_o <= command_abort_stream;
                soft_reset_pulse_o <= command_soft_reset_stream;
            end
        end
    end

    initial begin
        if (EVENT_COUNT_WIDTH < 1)
            $fatal(1, "EVENT_COUNT_WIDTH must be positive");
    end

endmodule

`default_nettype wire
