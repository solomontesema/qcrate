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

    logic [EVENT_COUNT_WIDTH-1:0] mailbox_event_count_ctrl;
    logic mailbox_external_trigger_enable_ctrl;
    logic mailbox_arm_ctrl;
    logic mailbox_start_ctrl;
    logic mailbox_abort_ctrl;
    logic mailbox_soft_reset_ctrl;
    logic request_toggle_ctrl;
    logic request_toggle_stream;
    logic request_toggle_stream_q;
    logic acknowledge_toggle_stream;
    logic acknowledge_toggle_ctrl;
    logic command_fire_ctrl;

    assign command_busy_o = request_toggle_ctrl ^ acknowledge_toggle_ctrl;
    assign command_fire_ctrl = (arm_cmd_i || start_cmd_i || abort_cmd_i ||
                                soft_reset_cmd_i) && !command_busy_o;

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i) begin
            mailbox_event_count_ctrl <= '0;
            mailbox_external_trigger_enable_ctrl <= 1'b0;
            mailbox_arm_ctrl <= 1'b0;
            mailbox_start_ctrl <= 1'b0;
            mailbox_abort_ctrl <= 1'b0;
            mailbox_soft_reset_ctrl <= 1'b0;
            request_toggle_ctrl <= 1'b0;
        end else if (command_fire_ctrl) begin
            mailbox_event_count_ctrl <= event_count_i;
            mailbox_external_trigger_enable_ctrl <= external_trigger_enable_i;
            mailbox_arm_ctrl <= arm_cmd_i;
            mailbox_start_ctrl <= start_cmd_i;
            mailbox_abort_ctrl <= abort_cmd_i;
            mailbox_soft_reset_ctrl <= soft_reset_cmd_i;
            request_toggle_ctrl <= ~request_toggle_ctrl;
        end
    end

    qcrate_cdc_single u_request_sync (
        .src_clk_i    (ctrl_clk_i),
        .src_signal_i (request_toggle_ctrl),
        .dst_clk_i    (stream_clk_i),
        .dst_rst_n_i  (stream_rst_n_i),
        .dst_signal_o (request_toggle_stream)
    );

    qcrate_cdc_single u_acknowledge_sync (
        .src_clk_i    (stream_clk_i),
        .src_signal_i (acknowledge_toggle_stream),
        .dst_clk_i    (ctrl_clk_i),
        .dst_rst_n_i  (ctrl_rst_n_i),
        .dst_signal_o (acknowledge_toggle_ctrl)
    );

    always_ff @(posedge stream_clk_i) begin
        if (!stream_rst_n_i) begin
            active_event_count_o <= '0;
            active_external_trigger_enable_o <= 1'b0;
            arm_pulse_o <= 1'b0;
            start_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;
            request_toggle_stream_q <= 1'b0;
            acknowledge_toggle_stream <= 1'b0;
        end else begin
            arm_pulse_o <= 1'b0;
            start_pulse_o <= 1'b0;
            abort_pulse_o <= 1'b0;
            soft_reset_pulse_o <= 1'b0;

            if (request_toggle_stream != request_toggle_stream_q) begin
                active_event_count_o <= mailbox_event_count_ctrl;
                active_external_trigger_enable_o <=
                    mailbox_external_trigger_enable_ctrl;
                arm_pulse_o <= mailbox_arm_ctrl;
                start_pulse_o <= mailbox_start_ctrl;
                abort_pulse_o <= mailbox_abort_ctrl;
                soft_reset_pulse_o <= mailbox_soft_reset_ctrl;
                request_toggle_stream_q <= request_toggle_stream;
                acknowledge_toggle_stream <= request_toggle_stream;
            end
        end
    end

endmodule

`default_nettype wire
