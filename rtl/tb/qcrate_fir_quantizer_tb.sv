`timescale 1ns / 1ps
`default_nettype none

module qcrate_fir_quantizer_tb;

    logic signed [35:0] accumulator_i;
    logic signed [35:0] accumulator_q;
    logic signed [15:0] sample_i;
    logic signed [15:0] sample_q;

    qcrate_fir_quantizer u_dut (
        .accumulator_i_i             (accumulator_i),
        .accumulator_q_i             (accumulator_q),
        .sample_i_o                  (sample_i),
        .sample_q_o                  (sample_q)
    );

    task automatic check_pair(
        input logic signed [35:0] input_i,
        input logic signed [35:0] input_q,
        input logic signed [15:0] expected_i,
        input logic signed [15:0] expected_q,
        input string what
    );
        begin
            accumulator_i = input_i;
            accumulator_q = input_q;
            #1;
            if ((sample_i !== expected_i) || (sample_q !== expected_q)) begin
                $fatal(1, "%s: I=%0d/%0d Q=%0d/%0d",
                       what, sample_i, expected_i, sample_q, expected_q);
            end
        end
    endtask

    initial begin
        check_pair(36'sd0, 36'sd0, 16'sd0, 16'sd0, "zero");
        check_pair(36'sd262143, -36'sd262143,
                   16'sd0, 16'sd0, "below half LSB");
        check_pair(36'sd262144, -36'sd262144,
                   16'sd1, -16'sd1, "half ties away");
        check_pair(36'sd786432, -36'sd786432,
                   16'sd2, -16'sd2, "one and a half ties");
        check_pair(36'sd17179344896, -36'sd17179869184,
                   16'sd32767, -16'sd32768, "exact output limits");
        check_pair(36'sh7ffffffff, 36'sh800000000,
                   16'sd32767, -16'sd32768, "saturation");

        $display("PASS: qcrate_fir_quantizer_tb");
        $finish;
    end

endmodule

`default_nettype wire
