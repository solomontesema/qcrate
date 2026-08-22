`timescale 1ns / 1ps
`default_nettype none

module qcrate_dsp_frontend #(
    parameter string SINE_LUT_FILE = "rtl/dsp/tables/sine_quarter_q1_15.hex"
) (
    input  wire logic                      clk_i,
    input  wire logic                      rst_n_i,

    input  wire logic                      phase_load_i,
    input  wire logic [31:0]               lo_phase_initial_i,
    input  wire logic [31:0]               lo_phase_increment_i,

    input  wire logic signed [15:0]        s_sample_data_i,
    input  wire logic                      s_sample_valid_i,
    output logic                           s_sample_ready_o,

    output logic signed [17:0]             m_i_o,
    output logic signed [17:0]             m_q_o,
    output logic                           m_valid_o,
    input  wire logic                      m_ready_i,

    output logic [31:0]                    lo_phase_o
);

    logic pipeline_ce;
    logic input_fire;

    logic signed [15:0] adc_delay_0;
    logic signed [15:0] adc_delay_1;

    logic nco_valid;
    logic signed [15:0] lo_sine;
    logic signed [15:0] lo_cosine;

    assign pipeline_ce = !m_valid_o || m_ready_i;
    assign s_sample_ready_o = pipeline_ce && !phase_load_i;
    assign input_fire = s_sample_valid_i && s_sample_ready_o;

    qcrate_nco #(
        .LUT_FILE                    (SINE_LUT_FILE)
    ) u_lo_nco (
        .clk_i                       (clk_i),
        .rst_n_i                     (rst_n_i),
        .ce_i                        (pipeline_ce),
        .phase_load_i                (phase_load_i),
        .phase_initial_i             (lo_phase_initial_i),
        .phase_increment_i           (lo_phase_increment_i),
        .advance_i                   (input_fire),
        .valid_o                     (nco_valid),
        .sine_o                      (lo_sine),
        .cosine_o                    (lo_cosine),
        .phase_o                     (lo_phase_o)
    );

    always_ff @(posedge clk_i) begin
        if (!rst_n_i || phase_load_i) begin
            adc_delay_0 <= '0;
            adc_delay_1 <= '0;
        end else if (pipeline_ce) begin
            if (input_fire) begin
                adc_delay_0 <= s_sample_data_i;
            end
            adc_delay_1 <= adc_delay_0;
        end
    end

    qcrate_ddc_mixer u_mixer (
        .clk_i                       (clk_i),
        .rst_n_i                     (rst_n_i),
        .ce_i                        (pipeline_ce),
        .clear_i                     (phase_load_i),
        .valid_i                     (nco_valid),
        .adc_i                       (adc_delay_1),
        .lo_sine_i                   (lo_sine),
        .lo_cosine_i                 (lo_cosine),
        .valid_o                     (m_valid_o),
        .i_o                         (m_i_o),
        .q_o                         (m_q_o)
    );

endmodule

`default_nettype wire
