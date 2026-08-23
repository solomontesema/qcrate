`timescale 1ns / 1ps
`default_nettype none

module qcrate_synthetic_source_tb;

    localparam int VECTOR_COUNT = 1024;

    logic clk;
    logic rst_n;
    logic enable;
    logic phase_load;
    logic signed [15:0] sample_data;
    logic sample_valid;
    logic sample_ready;
    logic signed [15:0] expected_samples [0:VECTOR_COUNT-1];

    qcrate_synthetic_source u_dut (
        .clk_i                      (clk),
        .rst_n_i                    (rst_n),
        .enable_i                   (enable),
        .phase_load_i               (phase_load),
        .m_sample_data_o            (sample_data),
        .m_sample_valid_o           (sample_valid),
        .m_sample_ready_i           (sample_ready)
    );

    always #2.5 clk = ~clk;

    initial begin
        int output_count;
        int guard;
        logic [31:0] prng;
        logic transfer;
        logic stalled;
        logic signed [15:0] transferred_sample;
        logic signed [15:0] held_sample;

        $readmemh("build/dsp/rtl_vectors/adc_q1_15.hex", expected_samples);

        clk = 1'b0;
        rst_n = 1'b0;
        enable = 1'b0;
        phase_load = 1'b0;
        sample_ready = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        @(negedge clk);
        phase_load = 1'b1;
        @(posedge clk);
        #1;
        phase_load = 1'b0;
        enable = 1'b1;

        output_count = 0;
        guard = 0;
        prng = 32'h9e37_79b9;
        while ((output_count < VECTOR_COUNT) && (guard < 30000)) begin
            @(negedge clk);
            prng = {prng[30:0], prng[31] ^ prng[21] ^ prng[1] ^ prng[0]};
            sample_ready = |prng[2:0];
            #1;

            transfer = sample_valid && sample_ready;
            stalled = sample_valid && !sample_ready;
            transferred_sample = sample_data;
            held_sample = sample_data;

            @(posedge clk);
            #1;
            if (transfer) begin
                if (transferred_sample !== expected_samples[output_count]) begin
                    $fatal(1,
                           "source sample %0d mismatch: actual=%0d expected=%0d",
                           output_count, transferred_sample,
                           expected_samples[output_count]);
                end
                output_count++;
            end
            if (stalled && ((!sample_valid) || (sample_data !== held_sample))) begin
                $fatal(1, "synthetic source changed under backpressure");
            end
            guard++;
        end

        if (output_count != VECTOR_COUNT) begin
            $fatal(1, "source timeout after %0d samples", output_count);
        end
        $display("PASS: qcrate_synthetic_source_tb verified %0d samples",
                 VECTOR_COUNT);
        $finish;
    end

endmodule

`default_nettype wire
