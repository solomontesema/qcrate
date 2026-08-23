`timescale 1ns / 1ps
`default_nettype none

module qcrate_synthetic_source #(
    parameter string SINE_LUT_FILE = "rtl/dsp/tables/sine_quarter_q1_15.mem",
    parameter logic [31:0] SIGNAL_PHASE_INITIAL = 32'h0000_0000,
    parameter logic [31:0] SIGNAL_PHASE_INCREMENT = 32'h2666_6666,
    parameter logic signed [15:0] SIGNAL_AMPLITUDE_Q1_15 = 16'sd24576,
    parameter logic signed [15:0] NOISE_AMPLITUDE_Q1_15 = 16'sd328,
    parameter logic [15:0] NOISE_SEED = 16'hace1
) (
    input  wire logic                      clk_i,
    input  wire logic                      rst_n_i,
    input  wire logic                      enable_i,
    input  wire logic                      phase_load_i,

    output logic signed [15:0]             m_sample_data_o,
    output logic                           m_sample_valid_o,
    input  wire logic                      m_sample_ready_i
);

    localparam logic signed [32:0] SAMPLE_MAX = 33'sd32767;
    localparam logic signed [32:0] SAMPLE_MIN = -33'sd32768;

    logic pipeline_ce;
    logic sample_advance;
    logic nco_valid;
    logic signed [15:0] signal_sine_unused;
    logic signed [15:0] signal_cosine;
    logic [31:0] signal_phase_unused;

    logic [15:0] lfsr_state;
    logic signed [15:0] noise_delay_0;
    logic signed [15:0] noise_delay_1;

    // A globally stalled three-stage arithmetic pipeline preserves source,
    // noise, and valid alignment while sustaining one sample per clock.
    logic product_valid;
    logic component_valid;
    logic signed [31:0] signal_product;
    logic signed [31:0] noise_product;
    logic signed [15:0] signal_component;
    logic signed [15:0] noise_component;

    function automatic logic [15:0] lfsr_step(input logic [15:0] state);
        logic [15:0] shifted;
        begin
            shifted = state >> 1;
            if (state[0]) begin
                shifted = shifted ^ 16'hb400;
            end
            return shifted;
        end
    endfunction

    function automatic logic signed [15:0] round_q1_15_product(
        input logic signed [31:0] product
    );
        logic signed [32:0] adjusted;
        logic signed [32:0] rounded;
        begin
            adjusted = {product[31], product} +
                       (product[31] ? 33'sd16383 : 33'sd16384);
            rounded = adjusted >>> 15;
            if (rounded > SAMPLE_MAX) begin
                return 16'sh7fff;
            end
            if (rounded < SAMPLE_MIN) begin
                return 16'sh8000;
            end
            return rounded[15:0];
        end
    endfunction

    function automatic logic signed [15:0] saturating_add(
        input logic signed [15:0] left,
        input logic signed [15:0] right
    );
        logic signed [16:0] sum;
        begin
            sum = left + right;
            if (sum > 17'sd32767) begin
                return 16'sh7fff;
            end
            if (sum < -17'sd32768) begin
                return 16'sh8000;
            end
            return sum[15:0];
        end
    endfunction

    assign pipeline_ce = !m_sample_valid_o || m_sample_ready_i;
    assign sample_advance = enable_i && pipeline_ce && !phase_load_i;

    qcrate_nco #(
        .LUT_FILE                    (SINE_LUT_FILE)
    ) u_signal_nco (
        .clk_i                       (clk_i),
        .rst_n_i                     (rst_n_i),
        .ce_i                        (pipeline_ce),
        .phase_load_i                (phase_load_i),
        .phase_initial_i             (SIGNAL_PHASE_INITIAL),
        .phase_increment_i           (SIGNAL_PHASE_INCREMENT),
        .advance_i                   (sample_advance),
        .valid_o                     (nco_valid),
        .sine_o                      (signal_sine_unused),
        .cosine_o                    (signal_cosine),
        .phase_o                     (signal_phase_unused)
    );

    always_ff @(posedge clk_i) begin
        logic [15:0] next_lfsr;

        if (!rst_n_i || phase_load_i) begin
            lfsr_state <= NOISE_SEED;
            noise_delay_0 <= '0;
            noise_delay_1 <= '0;
            product_valid <= 1'b0;
            component_valid <= 1'b0;
            signal_product <= '0;
            noise_product <= '0;
            signal_component <= '0;
            noise_component <= '0;
            m_sample_data_o <= '0;
            m_sample_valid_o <= 1'b0;
        end else if (pipeline_ce) begin
            product_valid <= nco_valid;
            component_valid <= product_valid;
            m_sample_valid_o <= component_valid;
            noise_delay_1 <= noise_delay_0;

            if (sample_advance) begin
                next_lfsr = lfsr_step(lfsr_state);
                lfsr_state <= next_lfsr;
                // XORing the sign bit is equivalent to unsigned state - 32768.
                noise_delay_0 <= $signed(next_lfsr ^ 16'h8000);
            end

            if (nco_valid) begin
                signal_product <= signal_cosine * SIGNAL_AMPLITUDE_Q1_15;
                noise_product <= noise_delay_1 * NOISE_AMPLITUDE_Q1_15;
            end

            if (product_valid) begin
                signal_component <= round_q1_15_product(signal_product);
                noise_component <= round_q1_15_product(noise_product);
            end

            if (component_valid) begin
                m_sample_data_o <= saturating_add(signal_component,
                                                  noise_component);
            end
        end
    end

    initial begin
        if (NOISE_SEED == 16'h0000) begin
            $fatal(1, "NOISE_SEED must be nonzero");
        end
    end

endmodule

`default_nettype wire
