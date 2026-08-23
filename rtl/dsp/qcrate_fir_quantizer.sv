`timescale 1ns / 1ps
`default_nettype none

module qcrate_fir_quantizer (
    input  wire logic signed [35:0] accumulator_i_i,
    input  wire logic signed [35:0] accumulator_q_i,
    output logic signed [15:0]      sample_i_o,
    output logic signed [15:0]      sample_q_o
);

    localparam int OUTPUT_SHIFT = 19;
    localparam logic signed [36:0] OUTPUT_MAX = 37'sd32767;
    localparam logic signed [36:0] OUTPUT_MIN = -37'sd32768;

    function automatic logic signed [15:0] round_saturate(
        input logic signed [35:0] accumulator
    );
        logic signed [36:0] adjusted;
        logic signed [36:0] rounded;
        begin
            // Arithmetic shift rounds down. These asymmetric half-LSB biases
            // implement round-to-nearest with exact ties away from zero.
            adjusted = {accumulator[35], accumulator} +
                       (accumulator[35] ? 37'sd262143 : 37'sd262144);
            rounded = adjusted >>> OUTPUT_SHIFT;
            if (rounded > OUTPUT_MAX) begin
                return 16'sh7fff;
            end
            if (rounded < OUTPUT_MIN) begin
                return 16'sh8000;
            end
            return rounded[15:0];
        end
    endfunction

    always_comb begin
        sample_i_o = round_saturate(accumulator_i_i);
        sample_q_o = round_saturate(accumulator_q_i);
    end

endmodule

`default_nettype wire
