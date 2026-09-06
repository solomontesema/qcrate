`timescale 1ns / 1ps
`default_nettype none

module qcrate_dsp_config_cdc #(
    parameter logic [63:0] RESET_CONFIG_ID = 64'h5db4_fb57_8b27_b09f,
    parameter logic [31:0] RESET_SIGNAL_PHASE_INCREMENT = 32'h2666_6666,
    parameter logic [31:0] RESET_SIGNAL_PHASE_INITIAL = 32'h0000_0000,
    parameter logic [15:0] RESET_SIGNAL_AMPLITUDE = 16'h6000,
    parameter logic [15:0] RESET_NOISE_AMPLITUDE = 16'h0148,
    parameter logic [15:0] RESET_NOISE_SEED = 16'hace1,
    parameter logic [31:0] RESET_LO_PHASE_INCREMENT = 32'h251e_b852,
    parameter logic [31:0] RESET_LO_PHASE_INITIAL = 32'h0000_0000
) (
    input  wire logic        ctrl_clk_i,
    input  wire logic        ctrl_rst_n_i,
    input  wire logic [63:0] shadow_config_id_i,
    input  wire logic [31:0] shadow_signal_phase_increment_i,
    input  wire logic [31:0] shadow_signal_phase_initial_i,
    input  wire logic [15:0] shadow_signal_amplitude_i,
    input  wire logic [15:0] shadow_noise_amplitude_i,
    input  wire logic [15:0] shadow_noise_seed_i,
    input  wire logic [31:0] shadow_lo_phase_increment_i,
    input  wire logic [31:0] shadow_lo_phase_initial_i,
    input  wire logic        commit_cmd_i,

    output logic             commit_busy_o,
    output logic             commit_accepted_o,
    output logic             commit_rejected_o,
    output logic [1:0]       commit_reject_reason_o,
    output logic             active_valid_ctrl_o,
    output logic [31:0]      active_generation_ctrl_o,
    output logic [63:0]      active_config_id_ctrl_o,
    output logic [31:0]      active_signal_phase_increment_ctrl_o,
    output logic [31:0]      active_signal_phase_initial_ctrl_o,
    output logic [15:0]      active_signal_amplitude_ctrl_o,
    output logic [15:0]      active_noise_amplitude_ctrl_o,
    output logic [15:0]      active_noise_seed_ctrl_o,
    output logic [31:0]      active_lo_phase_increment_ctrl_o,
    output logic [31:0]      active_lo_phase_initial_ctrl_o,

    input  wire logic        stream_clk_i,
    input  wire logic        stream_rst_n_i,
    input  wire logic        safe_to_commit_i,
    output logic [31:0]      active_generation_stream_o,
    output logic [63:0]      active_config_id_stream_o,
    output logic [31:0]      active_signal_phase_increment_stream_o,
    output logic [31:0]      active_signal_phase_initial_stream_o,
    output logic [15:0]      active_signal_amplitude_stream_o,
    output logic [15:0]      active_noise_amplitude_stream_o,
    output logic [15:0]      active_noise_seed_stream_o,
    output logic [31:0]      active_lo_phase_increment_stream_o,
    output logic [31:0]      active_lo_phase_initial_stream_o
);

    localparam logic [1:0] REJECT_NONE = 2'd0;
    localparam logic [1:0] REJECT_UNSAFE_STATE = 2'd2;
    localparam int CONFIG_WIDTH = 240;
    localparam int RESPONSE_WIDTH = CONFIG_WIDTH + 35;

    typedef enum logic [1:0] {
        STREAM_IDLE,
        STREAM_EVALUATE,
        STREAM_WAIT_REQUEST_CLEAR
    } stream_state_t;

    logic [CONFIG_WIDTH-1:0] request_data_ctrl;
    logic [CONFIG_WIDTH-1:0] request_data_stream;
    logic request_send_ctrl;
    logic request_busy_ctrl;
    logic request_valid_stream;
    logic request_accept_stream;

    logic [63:0] request_config_id_stream;
    logic [31:0] request_signal_increment_stream;
    logic [31:0] request_signal_initial_stream;
    logic [15:0] request_signal_amplitude_stream;
    logic [15:0] request_noise_amplitude_stream;
    logic [15:0] request_noise_seed_stream;
    logic [31:0] request_lo_increment_stream;
    logic [31:0] request_lo_initial_stream;

    logic [RESPONSE_WIDTH-1:0] response_data_stream;
    logic [RESPONSE_WIDTH-1:0] response_data_ctrl;
    logic response_send_stream;
    logic response_busy_stream;
    logic response_valid_ctrl;
    logic response_accept_ctrl;
    logic response_accepted_stream;
    logic [1:0] response_reason_stream;
    logic transaction_busy_ctrl_q;
    logic response_received_ctrl_q;
    logic response_accepted_ctrl_q;
    logic [1:0] response_reason_ctrl_q;
    stream_state_t stream_state_q;

    logic response_accepted_ctrl;
    logic [1:0] response_reason_ctrl;
    logic [31:0] response_generation_ctrl;
    logic [63:0] response_config_id_ctrl;
    logic [31:0] response_signal_increment_ctrl;
    logic [31:0] response_signal_initial_ctrl;
    logic [15:0] response_signal_amplitude_ctrl;
    logic [15:0] response_noise_amplitude_ctrl;
    logic [15:0] response_noise_seed_ctrl;
    logic [31:0] response_lo_increment_ctrl;
    logic [31:0] response_lo_initial_ctrl;

    assign request_data_ctrl = {
        shadow_config_id_i,
        shadow_signal_phase_increment_i,
        shadow_signal_phase_initial_i,
        shadow_signal_amplitude_i,
        shadow_noise_amplitude_i,
        shadow_noise_seed_i,
        shadow_lo_phase_increment_i,
        shadow_lo_phase_initial_i
    };
    assign {
        request_config_id_stream,
        request_signal_increment_stream,
        request_signal_initial_stream,
        request_signal_amplitude_stream,
        request_noise_amplitude_stream,
        request_noise_seed_stream,
        request_lo_increment_stream,
        request_lo_initial_stream
    } = request_data_stream;

    assign response_data_stream = {
        response_accepted_stream,
        response_reason_stream,
        active_generation_stream_o,
        active_config_id_stream_o,
        active_signal_phase_increment_stream_o,
        active_signal_phase_initial_stream_o,
        active_signal_amplitude_stream_o,
        active_noise_amplitude_stream_o,
        active_noise_seed_stream_o,
        active_lo_phase_increment_stream_o,
        active_lo_phase_initial_stream_o
    };
    assign {
        response_accepted_ctrl,
        response_reason_ctrl,
        response_generation_ctrl,
        response_config_id_ctrl,
        response_signal_increment_ctrl,
        response_signal_initial_ctrl,
        response_signal_amplitude_ctrl,
        response_noise_amplitude_ctrl,
        response_noise_seed_ctrl,
        response_lo_increment_ctrl,
        response_lo_initial_ctrl
    } = response_data_ctrl;

    assign commit_busy_o = transaction_busy_ctrl_q || request_busy_ctrl;

    qcrate_cdc_handshake #(
        .WIDTH (CONFIG_WIDTH)
    ) u_request_handshake (
        .src_clk_i      (ctrl_clk_i),
        .src_rst_n_i    (ctrl_rst_n_i),
        .src_data_i     (request_data_ctrl),
        .src_send_i     (request_send_ctrl),
        .src_busy_o     (request_busy_ctrl),
        .dest_clk_i     (stream_clk_i),
        .dest_rst_n_i   (stream_rst_n_i),
        .dest_data_o    (request_data_stream),
        .dest_valid_o   (request_valid_stream),
        .dest_accept_i  (request_accept_stream)
    );

    qcrate_cdc_handshake #(
        .WIDTH (RESPONSE_WIDTH)
    ) u_response_handshake (
        .src_clk_i      (stream_clk_i),
        .src_rst_n_i    (stream_rst_n_i),
        .src_data_i     (response_data_stream),
        .src_send_i     (response_send_stream),
        .src_busy_o     (response_busy_stream),
        .dest_clk_i     (ctrl_clk_i),
        .dest_rst_n_i   (ctrl_rst_n_i),
        .dest_data_o    (response_data_ctrl),
        .dest_valid_o   (response_valid_ctrl),
        .dest_accept_i  (response_accept_ctrl)
    );

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i) begin
            request_send_ctrl <= 1'b0;
            response_accept_ctrl <= 1'b0;
            transaction_busy_ctrl_q <= 1'b0;
            response_received_ctrl_q <= 1'b0;
            response_accepted_ctrl_q <= 1'b0;
            response_reason_ctrl_q <= REJECT_NONE;
            commit_accepted_o <= 1'b0;
            commit_rejected_o <= 1'b0;
            commit_reject_reason_o <= REJECT_NONE;
            active_valid_ctrl_o <= 1'b1;
            active_generation_ctrl_o <= 32'd0;
            active_config_id_ctrl_o <= RESET_CONFIG_ID;
            active_signal_phase_increment_ctrl_o <= RESET_SIGNAL_PHASE_INCREMENT;
            active_signal_phase_initial_ctrl_o <= RESET_SIGNAL_PHASE_INITIAL;
            active_signal_amplitude_ctrl_o <= RESET_SIGNAL_AMPLITUDE;
            active_noise_amplitude_ctrl_o <= RESET_NOISE_AMPLITUDE;
            active_noise_seed_ctrl_o <= RESET_NOISE_SEED;
            active_lo_phase_increment_ctrl_o <= RESET_LO_PHASE_INCREMENT;
            active_lo_phase_initial_ctrl_o <= RESET_LO_PHASE_INITIAL;
        end else begin
            request_send_ctrl <= 1'b0;
            response_accept_ctrl <= 1'b0;
            commit_accepted_o <= 1'b0;
            commit_rejected_o <= 1'b0;

            if (commit_cmd_i && !commit_busy_o) begin
                request_send_ctrl <= 1'b1;
                transaction_busy_ctrl_q <= 1'b1;
            end

            if (response_valid_ctrl && transaction_busy_ctrl_q &&
                !response_received_ctrl_q) begin
                response_accept_ctrl <= 1'b1;
                response_received_ctrl_q <= 1'b1;
                response_accepted_ctrl_q <= response_accepted_ctrl;
                response_reason_ctrl_q <= response_reason_ctrl;
                if (response_accepted_ctrl) begin
                    active_generation_ctrl_o <= response_generation_ctrl;
                    active_config_id_ctrl_o <= response_config_id_ctrl;
                    active_signal_phase_increment_ctrl_o <=
                        response_signal_increment_ctrl;
                    active_signal_phase_initial_ctrl_o <= response_signal_initial_ctrl;
                    active_signal_amplitude_ctrl_o <= response_signal_amplitude_ctrl;
                    active_noise_amplitude_ctrl_o <= response_noise_amplitude_ctrl;
                    active_noise_seed_ctrl_o <= response_noise_seed_ctrl;
                    active_lo_phase_increment_ctrl_o <= response_lo_increment_ctrl;
                    active_lo_phase_initial_ctrl_o <= response_lo_initial_ctrl;
                end
            end else if (response_received_ctrl_q && !request_busy_ctrl) begin
                transaction_busy_ctrl_q <= 1'b0;
                response_received_ctrl_q <= 1'b0;
                commit_reject_reason_o <= response_reason_ctrl_q;
                commit_accepted_o <= response_accepted_ctrl_q;
                commit_rejected_o <= !response_accepted_ctrl_q;
            end
        end
    end

    always_ff @(posedge stream_clk_i) begin
        if (!stream_rst_n_i) begin
            request_accept_stream <= 1'b0;
            response_send_stream <= 1'b0;
            response_accepted_stream <= 1'b1;
            response_reason_stream <= REJECT_NONE;
            stream_state_q <= STREAM_IDLE;
            active_generation_stream_o <= 32'd0;
            active_config_id_stream_o <= RESET_CONFIG_ID;
            active_signal_phase_increment_stream_o <= RESET_SIGNAL_PHASE_INCREMENT;
            active_signal_phase_initial_stream_o <= RESET_SIGNAL_PHASE_INITIAL;
            active_signal_amplitude_stream_o <= RESET_SIGNAL_AMPLITUDE;
            active_noise_amplitude_stream_o <= RESET_NOISE_AMPLITUDE;
            active_noise_seed_stream_o <= RESET_NOISE_SEED;
            active_lo_phase_increment_stream_o <= RESET_LO_PHASE_INCREMENT;
            active_lo_phase_initial_stream_o <= RESET_LO_PHASE_INITIAL;
        end else begin
            request_accept_stream <= 1'b0;
            response_send_stream <= 1'b0;

            unique case (stream_state_q)
                STREAM_IDLE: begin
                    if (request_valid_stream && !response_busy_stream)
                        stream_state_q <= STREAM_EVALUATE;
                end

                STREAM_EVALUATE: begin
                    if (safe_to_commit_i) begin
                        active_generation_stream_o <=
                            active_generation_stream_o + 32'd1;
                        active_config_id_stream_o <= request_config_id_stream;
                        active_signal_phase_increment_stream_o <=
                            request_signal_increment_stream;
                        active_signal_phase_initial_stream_o <=
                            request_signal_initial_stream;
                        active_signal_amplitude_stream_o <=
                            request_signal_amplitude_stream;
                        active_noise_amplitude_stream_o <=
                            request_noise_amplitude_stream;
                        active_noise_seed_stream_o <= request_noise_seed_stream;
                        active_lo_phase_increment_stream_o <=
                            request_lo_increment_stream;
                        active_lo_phase_initial_stream_o <= request_lo_initial_stream;
                        response_accepted_stream <= 1'b1;
                        response_reason_stream <= REJECT_NONE;
                    end else begin
                        response_accepted_stream <= 1'b0;
                        response_reason_stream <= REJECT_UNSAFE_STATE;
                    end
                    response_send_stream <= 1'b1;
                    request_accept_stream <= 1'b1;
                    stream_state_q <= STREAM_WAIT_REQUEST_CLEAR;
                end

                STREAM_WAIT_REQUEST_CLEAR: begin
                    if (!request_valid_stream)
                        stream_state_q <= STREAM_IDLE;
                end

                default: stream_state_q <= STREAM_IDLE;
            endcase
        end
    end

endmodule

`default_nettype wire
