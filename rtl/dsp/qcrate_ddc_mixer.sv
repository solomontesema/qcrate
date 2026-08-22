`timescale 1ns / 1ps
`default_nettype none

module qcrate_ddc_mixer (
    input  wire logic                      clk_i,
    input  wire logic                      rst_n_i,
    input  wire logic                      ce_i,
    input  wire logic                      clear_i,

    input  wire logic                      valid_i,
    input  wire logic signed [15:0]        adc_i,
    input  wire logic signed [15:0]        lo_sine_i,
    input  wire logic signed [15:0]        lo_cosine_i,

    output logic                           valid_o,
    output logic signed [17:0]             i_o,
    output logic signed [17:0]             q_o
);

    localparam int PRODUCT_SHIFT = 13;
    localparam logic signed [33:0] MIXER_MAX = 34'sd131071;
    localparam logic signed [33:0] MIXER_MIN = -34'sd131072;

    logic product_valid;
    logic signed [31:0] i_product;
    logic signed [31:0] q_product_raw;

    function automatic logic signed [17:0] round_saturate_product(
        input logic signed [32:0] product
    );
        logic signed [33:0] adjusted;
        logic signed [33:0] rounded;
        begin
            // Arithmetic right shift rounds down. The asymmetric biases make
            // half-LSB ties round away from zero for both signs.
            adjusted = {{1{product[32]}}, product} +
                       (product[32] ? 34'sd4095 : 34'sd4096);
            rounded = adjusted >>> PRODUCT_SHIFT;

            if (rounded > MIXER_MAX) begin
                return 18'sh1ffff;
            end
            if (rounded < MIXER_MIN) begin
                return 18'sh20000;
            end
            return rounded[17:0];
        end
    endfunction

    always_ff @(posedge clk_i) begin
        if (!rst_n_i) begin
            product_valid <= 1'b0;
            i_product <= '0;
            q_product_raw <= '0;
            valid_o <= 1'b0;
            i_o <= '0;
            q_o <= '0;
        end else if (clear_i) begin
            product_valid <= 1'b0;
            i_product <= '0;
            q_product_raw <= '0;
            valid_o <= 1'b0;
            i_o <= '0;
            q_o <= '0;
        end else if (ce_i) begin
            product_valid <= valid_i;
            valid_o <= product_valid;

            if (valid_i) begin
                i_product <= adc_i * lo_cosine_i;
                q_product_raw <= adc_i * lo_sine_i;
            end

            if (product_valid) begin
                i_o <= round_saturate_product(
                    {i_product[31], i_product}
                );
                q_o <= round_saturate_product(
                    -$signed({q_product_raw[31], q_product_raw})
                );
            end
        end
    end

endmodule

`default_nettype wire
