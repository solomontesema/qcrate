`timescale 1ns / 1ps
`default_nettype none

module qcrate_dsp_chain #(
    parameter string SINE_LUT_FILE = "rtl/dsp/tables/sine_quarter_q1_15.mem"
) (
    input  wire logic                      clk_i,
    input  wire logic                      rst_n_i,
    input  wire logic                      enable_i,
    input  wire logic                      clear_i,
    input  wire logic [31:0]               signal_phase_initial_i,
    input  wire logic [31:0]               signal_phase_increment_i,
    input  wire logic signed [15:0]        signal_amplitude_i,
    input  wire logic signed [15:0]        noise_amplitude_i,
    input  wire logic [15:0]               noise_seed_i,
    input  wire logic [31:0]               lo_phase_initial_i,
    input  wire logic [31:0]               lo_phase_increment_i,

    output logic [31:0]                    m_data_o,
    output logic                           m_valid_o,
    input  wire logic                      m_ready_i
);

    logic signed [15:0] source_sample;
    logic source_valid;
    logic source_ready;

    logic signed [17:0] mixed_i;
    logic signed [17:0] mixed_q;
    logic mixed_valid;
    logic mixed_ready;
    logic [31:0] lo_phase_unused;

    logic signed [15:0] filtered_i;
    logic signed [15:0] filtered_q;
    logic filtered_valid;

    qcrate_synthetic_source #(
        .SINE_LUT_FILE              (SINE_LUT_FILE)
    ) u_source (
        .clk_i                      (clk_i),
        .rst_n_i                    (rst_n_i),
        .enable_i                   (enable_i),
        .phase_load_i               (clear_i),
        .signal_phase_initial_i     (signal_phase_initial_i),
        .signal_phase_increment_i   (signal_phase_increment_i),
        .signal_amplitude_i         (signal_amplitude_i),
        .noise_amplitude_i          (noise_amplitude_i),
        .noise_seed_i               (noise_seed_i),
        .m_sample_data_o            (source_sample),
        .m_sample_valid_o           (source_valid),
        .m_sample_ready_i           (source_ready)
    );

    qcrate_dsp_frontend #(
        .SINE_LUT_FILE              (SINE_LUT_FILE)
    ) u_frontend (
        .clk_i                      (clk_i),
        .rst_n_i                    (rst_n_i),
        .phase_load_i               (clear_i),
        .lo_phase_initial_i         (lo_phase_initial_i),
        .lo_phase_increment_i       (lo_phase_increment_i),
        .s_sample_data_i            (source_sample),
        .s_sample_valid_i           (source_valid),
        .s_sample_ready_o           (source_ready),
        .m_i_o                      (mixed_i),
        .m_q_o                      (mixed_q),
        .m_valid_o                  (mixed_valid),
        .m_ready_i                  (mixed_ready),
        .lo_phase_o                 (lo_phase_unused)
    );

    qcrate_fir_decim16 u_fir (
        .clk_i                      (clk_i),
        .rst_n_i                    (rst_n_i),
        .clear_i                    (clear_i),
        .s_i_i                      (mixed_i),
        .s_q_i                      (mixed_q),
        .s_valid_i                  (mixed_valid),
        .s_ready_o                  (mixed_ready),
        .m_i_o                      (filtered_i),
        .m_q_o                      (filtered_q),
        .m_valid_o                  (filtered_valid),
        .m_ready_i                  (m_ready_i)
    );

    assign m_data_o = {filtered_q, filtered_i};
    // Clear is synchronous inside the pipeline. Mask VALID immediately so a
    // new capture cannot accept an old buffered word on the reset edge.
    assign m_valid_o = filtered_valid && !clear_i;

endmodule

`default_nettype wire
