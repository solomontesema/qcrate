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

    localparam int STATUS_WIDTH = 237 + (2 * EVENT_ADDR_WIDTH);

    logic [STATUS_WIDTH-1:0] status_data_stream;
    logic [STATUS_WIDTH-1:0] status_data_ctrl;
    logic status_busy_stream;
    logic status_valid_ctrl;

    logic snapshot_idle_ctrl;
    logic snapshot_validating_ctrl;
    logic snapshot_armed_ctrl;
    logic snapshot_busy_ctrl;
    logic snapshot_faulted_ctrl;
    logic [7:0] snapshot_fault_code_ctrl;
    logic [EVENT_ADDR_WIDTH-1:0] snapshot_fault_event_index_ctrl;
    logic [EVENT_ADDR_WIDTH-1:0] snapshot_active_event_index_ctrl;
    logic [31:0] snapshot_completed_shots_ctrl;
    logic [63:0] snapshot_timebase_ctrl;
    logic [63:0] snapshot_start_time_ctrl;
    logic [63:0] snapshot_elapsed_tick_ctrl;

    assign status_data_stream = {
        idle_i,
        validating_i,
        armed_i,
        busy_i,
        faulted_i,
        fault_code_i,
        fault_event_index_i,
        active_event_index_i,
        completed_shots_i,
        timebase_i,
        start_time_i,
        elapsed_tick_i
    };

    assign {
        snapshot_idle_ctrl,
        snapshot_validating_ctrl,
        snapshot_armed_ctrl,
        snapshot_busy_ctrl,
        snapshot_faulted_ctrl,
        snapshot_fault_code_ctrl,
        snapshot_fault_event_index_ctrl,
        snapshot_active_event_index_ctrl,
        snapshot_completed_shots_ctrl,
        snapshot_timebase_ctrl,
        snapshot_start_time_ctrl,
        snapshot_elapsed_tick_ctrl
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
        end else if (status_valid_ctrl) begin
            idle_o <= snapshot_idle_ctrl;
            validating_o <= snapshot_validating_ctrl;
            armed_o <= snapshot_armed_ctrl;
            busy_o <= snapshot_busy_ctrl;
            faulted_o <= snapshot_faulted_ctrl;
            fault_code_o <= snapshot_fault_code_ctrl;
            fault_event_index_o <= snapshot_fault_event_index_ctrl;
            active_event_index_o <= snapshot_active_event_index_ctrl;
            completed_shots_o <= snapshot_completed_shots_ctrl;
            timebase_o <= snapshot_timebase_ctrl;
            start_time_o <= snapshot_start_time_ctrl;
            elapsed_tick_o <= snapshot_elapsed_tick_ctrl;
        end
    end

    initial begin
        if (EVENT_ADDR_WIDTH < 1)
            $fatal(1, "EVENT_ADDR_WIDTH must be positive");
    end

endmodule

`default_nettype wire
