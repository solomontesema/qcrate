`timescale 1ns / 1ps
`default_nettype none

module qcrate_ddc_mixer_tb;

    logic clk;
    logic rst_n;
    logic ce;
    logic clear;
    logic valid_i;
    logic signed [15:0] adc;
    logic signed [15:0] lo_sine;
    logic signed [15:0] lo_cosine;
    logic valid_o;
    logic signed [17:0] mixed_i;
    logic signed [17:0] mixed_q;

    qcrate_ddc_mixer u_dut (
        .clk_i                       (clk),
        .rst_n_i                     (rst_n),
        .ce_i                        (ce),
        .clear_i                     (clear),
        .valid_i                     (valid_i),
        .adc_i                       (adc),
        .lo_sine_i                   (lo_sine),
        .lo_cosine_i                 (lo_cosine),
        .valid_o                     (valid_o),
        .i_o                         (mixed_i),
        .q_o                         (mixed_q)
    );

    always #2.5 clk = ~clk;

    task automatic check_product(
        input logic signed [15:0] adc_value,
        input logic signed [15:0] sine_value,
        input logic signed [15:0] cosine_value,
        input logic signed [17:0] expected_i,
        input logic signed [17:0] expected_q,
        input string what
    );
        begin
            @(negedge clk);
            valid_i = 1'b1;
            adc = adc_value;
            lo_sine = sine_value;
            lo_cosine = cosine_value;
            @(posedge clk);
            @(posedge clk);
            #1;
            if (!valid_o || (mixed_i !== expected_i) || (mixed_q !== expected_q)) begin
                $fatal(1, "%s mismatch: I=%0d/%0d Q=%0d/%0d valid=%0b",
                       what, mixed_i, expected_i, mixed_q, expected_q, valid_o);
            end
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        ce = 1'b1;
        clear = 1'b0;
        valid_i = 1'b0;
        adc = '0;
        lo_sine = '0;
        lo_cosine = '0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        check_product(16'sd32767, 16'sd0, 16'sd32767,
                      18'sd131064, 18'sd0, "positive full scale");
        check_product(-16'sd32768, -16'sd32768, -16'sd32768,
                      18'sd131071, -18'sd131072, "saturation limits");
        check_product(16'sd1, 16'sd4096, 16'sd4096,
                      18'sd1, -18'sd1, "positive half ties");
        check_product(-16'sd1, 16'sd4096, 16'sd4096,
                      -18'sd1, 18'sd1, "negative half ties");

        @(negedge clk);
        clear = 1'b1;
        valid_i = 1'b0;
        @(posedge clk);
        #1;
        if (valid_o || (mixed_i !== 18'sd0) || (mixed_q !== 18'sd0)) begin
            $fatal(1, "mixer clear did not clear its output state");
        end

        $display("PASS: qcrate_ddc_mixer_tb");
        $finish;
    end

endmodule

`default_nettype wire
