`timescale 1ns / 1ps
`default_nettype none

module qcrate_fir_decim16 (
    input  wire logic                      clk_i,
    input  wire logic                      rst_n_i,
    input  wire logic                      clear_i,

    input  wire logic signed [17:0]        s_i_i,
    input  wire logic signed [17:0]        s_q_i,
    input  wire logic                      s_valid_i,
    output logic                           s_ready_o,

    output logic signed [15:0]             m_i_o,
    output logic signed [15:0]             m_q_o,
    output logic                           m_valid_o,
    input  wire logic                      m_ready_i
);

    logic fir_reset_n;
    logic fir_s_ready;
    logic [47:0] fir_s_data;
    logic fir_m_valid;
    logic fir_m_ready;
    logic [79:0] fir_m_data;
    logic signed [35:0] fir_accumulator_i;
    logic signed [35:0] fir_accumulator_q;
    logic signed [15:0] quantized_i;
    logic signed [15:0] quantized_q;

    assign fir_reset_n = rst_n_i && !clear_i;
    assign s_ready_o = fir_s_ready && !clear_i;

    // FIR Compiler byte-aligns each 18-bit input path to 24 bits.
    assign fir_s_data = {
        {6{s_q_i[17]}}, s_q_i,
        {6{s_i_i[17]}}, s_i_i
    };

    // Full-precision 36-bit outputs are byte-aligned to 40-bit path slots.
    assign fir_accumulator_i = fir_m_data[35:0];
    assign fir_accumulator_q = fir_m_data[75:40];
    assign fir_m_ready = !m_valid_o || m_ready_i;

    qcrate_fir_decim16_ip u_fir_compiler (
        .aresetn                     (fir_reset_n),
        .aclk                        (clk_i),
        .s_axis_data_tvalid          (s_valid_i && !clear_i),
        .s_axis_data_tready          (fir_s_ready),
        .s_axis_data_tdata           (fir_s_data),
        .m_axis_data_tvalid          (fir_m_valid),
        .m_axis_data_tready          (fir_m_ready),
        .m_axis_data_tdata           (fir_m_data)
    );

    qcrate_fir_quantizer u_quantizer (
        .accumulator_i_i             (fir_accumulator_i),
        .accumulator_q_i             (fir_accumulator_q),
        .sample_i_o                  (quantized_i),
        .sample_q_o                  (quantized_q)
    );

    // This elastic register keeps the visible output stable under backpressure.
    always_ff @(posedge clk_i) begin
        if (!rst_n_i || clear_i) begin
            m_i_o <= '0;
            m_q_o <= '0;
            m_valid_o <= 1'b0;
        end else if (fir_m_ready) begin
            m_valid_o <= fir_m_valid;
            if (fir_m_valid) begin
                m_i_o <= quantized_i;
                m_q_o <= quantized_q;
            end
        end
    end

endmodule

`default_nettype wire
