`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_status_cdc #(
    parameter int EVENT_ADDR_WIDTH = 7
) (
    input  wire logic                         stream_clk_i,
    input  wire logic                         stream_rst_n_i,
    input  wire logic                         idle_i,
    input  wire logic                         validating_i,
    input  wire logic                         armed_i,
    input  wire logic                         busy_i,
    input  wire logic                         faulted_i,
    input  wire logic [7:0]                   fault_code_i,
    input  wire logic [EVENT_ADDR_WIDTH-1:0]  fault_event_index_i,
    input  wire logic [EVENT_ADDR_WIDTH-1:0]  active_event_index_i,
    input  wire logic [31:0]                  completed_shots_i,
    input  wire logic [63:0]                  timebase_i,
    input  wire logic [63:0]                  start_time_i,
    input  wire logic [63:0]                  elapsed_tick_i,

    input  wire logic                         ctrl_clk_i,
    input  wire logic                         ctrl_rst_n_i,
    output logic                              idle_o,
    output logic                              validating_o,
    output logic                              armed_o,
    output logic                              busy_o,
    output logic                              faulted_o,
    output logic [7:0]                        fault_code_o,
    output logic [EVENT_ADDR_WIDTH-1:0]       fault_event_index_o,
    output logic [EVENT_ADDR_WIDTH-1:0]       active_event_index_o,
    output logic [31:0]                       completed_shots_o,
    output logic [63:0]                       timebase_o,
    output logic [63:0]                       start_time_o,
    output logic [63:0]                       elapsed_tick_o
);

    logic request_toggle_ctrl;
    logic request_toggle_stream;
    logic request_toggle_stream_q;
    logic acknowledge_toggle_stream;
    logic acknowledge_toggle_ctrl;
    logic acknowledge_toggle_ctrl_q;
    logic request_pending_ctrl;

    logic snapshot_idle_stream;
    logic snapshot_validating_stream;
    logic snapshot_armed_stream;
    logic snapshot_busy_stream;
    logic snapshot_faulted_stream;
    logic [7:0] snapshot_fault_code_stream;
    logic [EVENT_ADDR_WIDTH-1:0] snapshot_fault_event_index_stream;
    logic [EVENT_ADDR_WIDTH-1:0] snapshot_active_event_index_stream;
    logic [31:0] snapshot_completed_shots_stream;
    logic [63:0] snapshot_timebase_stream;
    logic [63:0] snapshot_start_time_stream;
    logic [63:0] snapshot_elapsed_tick_stream;

    assign request_pending_ctrl =
        request_toggle_ctrl ^ acknowledge_toggle_ctrl_q;

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
            snapshot_idle_stream <= 1'b1;
            snapshot_validating_stream <= 1'b0;
            snapshot_armed_stream <= 1'b0;
            snapshot_busy_stream <= 1'b0;
            snapshot_faulted_stream <= 1'b0;
            snapshot_fault_code_stream <= '0;
            snapshot_fault_event_index_stream <= '0;
            snapshot_active_event_index_stream <= '0;
            snapshot_completed_shots_stream <= '0;
            snapshot_timebase_stream <= '0;
            snapshot_start_time_stream <= '0;
            snapshot_elapsed_tick_stream <= '0;
            request_toggle_stream_q <= 1'b0;
            acknowledge_toggle_stream <= 1'b0;
        end else if (request_toggle_stream != request_toggle_stream_q) begin
            snapshot_idle_stream <= idle_i;
            snapshot_validating_stream <= validating_i;
            snapshot_armed_stream <= armed_i;
            snapshot_busy_stream <= busy_i;
            snapshot_faulted_stream <= faulted_i;
            snapshot_fault_code_stream <= fault_code_i;
            snapshot_fault_event_index_stream <= fault_event_index_i;
            snapshot_active_event_index_stream <= active_event_index_i;
            snapshot_completed_shots_stream <= completed_shots_i;
            snapshot_timebase_stream <= timebase_i;
            snapshot_start_time_stream <= start_time_i;
            snapshot_elapsed_tick_stream <= elapsed_tick_i;
            request_toggle_stream_q <= request_toggle_stream;
            acknowledge_toggle_stream <= request_toggle_stream;
        end
    end

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i) begin
            idle_o <= 1'b1;
            validating_o <= 1'b0;
            armed_o <= 1'b0;
            busy_o <= 1'b0;
            faulted_o <= 1'b0;
            fault_code_o <= '0;
            fault_event_index_o <= '0;
            active_event_index_o <= '0;
            completed_shots_o <= '0;
            timebase_o <= '0;
            start_time_o <= '0;
            elapsed_tick_o <= '0;
            request_toggle_ctrl <= 1'b0;
            acknowledge_toggle_ctrl_q <= 1'b0;
        end else begin
            if (acknowledge_toggle_ctrl != acknowledge_toggle_ctrl_q) begin
                idle_o <= snapshot_idle_stream;
                validating_o <= snapshot_validating_stream;
                armed_o <= snapshot_armed_stream;
                busy_o <= snapshot_busy_stream;
                faulted_o <= snapshot_faulted_stream;
                fault_code_o <= snapshot_fault_code_stream;
                fault_event_index_o <= snapshot_fault_event_index_stream;
                active_event_index_o <= snapshot_active_event_index_stream;
                completed_shots_o <= snapshot_completed_shots_stream;
                timebase_o <= snapshot_timebase_stream;
                start_time_o <= snapshot_start_time_stream;
                elapsed_tick_o <= snapshot_elapsed_tick_stream;
                acknowledge_toggle_ctrl_q <= acknowledge_toggle_ctrl;
            end else if (!request_pending_ctrl) begin
                request_toggle_ctrl <= ~request_toggle_ctrl;
            end
        end
    end

endmodule

`default_nettype wire
