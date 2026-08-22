`timescale 1ns / 1ps
`default_nettype none

module qcrate_dsp_frontend_tb;

    localparam int VECTOR_COUNT = 1024;
    localparam logic [31:0] LO_PHASE_INCREMENT = 32'h251e_b852;

    logic clk;
    logic rst_n;
    logic phase_load;
    logic signed [15:0] sample_data;
    logic sample_valid;
    logic sample_ready;
    logic signed [17:0] mixed_i;
    logic signed [17:0] mixed_q;
    logic mixed_valid;
    logic mixed_ready;
    logic [31:0] lo_phase;

    logic signed [15:0] adc_vectors [0:VECTOR_COUNT-1];
    logic signed [17:0] expected_i [0:VECTOR_COUNT-1];
    logic signed [17:0] expected_q [0:VECTOR_COUNT-1];

    qcrate_dsp_frontend u_dut (
        .clk_i                       (clk),
        .rst_n_i                     (rst_n),
        .phase_load_i                (phase_load),
        .lo_phase_initial_i          (32'h0000_0000),
        .lo_phase_increment_i        (LO_PHASE_INCREMENT),
        .s_sample_data_i             (sample_data),
        .s_sample_valid_i            (sample_valid),
        .s_sample_ready_o            (sample_ready),
        .m_i_o                       (mixed_i),
        .m_q_o                       (mixed_q),
        .m_valid_o                   (mixed_valid),
        .m_ready_i                   (mixed_ready),
        .lo_phase_o                  (lo_phase)
    );

    always #2.5 clk = ~clk;

    initial begin
        int input_count;
        int output_count;
        int guard;
        logic source_pending;
        logic [31:0] prng;
        logic input_transfer;
        logic output_transfer;
        logic stalled_output;
        logic signed [17:0] transferred_i;
        logic signed [17:0] transferred_q;
        logic signed [17:0] held_i;
        logic signed [17:0] held_q;
        logic held_valid;
        logic [31:0] expected_phase;

        $readmemh("build/dsp/rtl_vectors/adc_q1_15.hex", adc_vectors);
        $readmemh("build/dsp/rtl_vectors/mixed_i_q1_17.hex", expected_i);
        $readmemh("build/dsp/rtl_vectors/mixed_q_q1_17.hex", expected_q);

        clk = 1'b0;
        rst_n = 1'b0;
        phase_load = 1'b0;
        sample_data = '0;
        sample_valid = 1'b0;
        mixed_ready = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        @(negedge clk);
        phase_load = 1'b1;
        @(posedge clk);
        #1;
        phase_load = 1'b0;

        input_count = 0;
        output_count = 0;
        guard = 0;
        source_pending = 1'b0;
        prng = 32'h1bad_f00d;

        while ((output_count < VECTOR_COUNT) && (guard < 30000)) begin
            @(negedge clk);
            prng = {prng[30:0], prng[31] ^ prng[21] ^ prng[1] ^ prng[0]};
            mixed_ready = |prng[2:0];
            if (!source_pending && (input_count < VECTOR_COUNT) && |prng[4:3]) begin
                source_pending = 1'b1;
                sample_data = adc_vectors[input_count];
            end
            sample_valid = source_pending;

            #1;

            input_transfer = sample_valid && sample_ready;
            output_transfer = mixed_valid && mixed_ready;
            stalled_output = mixed_valid && !mixed_ready;
            transferred_i = mixed_i;
            transferred_q = mixed_q;
            held_valid = mixed_valid;
            held_i = mixed_i;
            held_q = mixed_q;

            @(posedge clk);
            #1;
            if (input_transfer) begin
                input_count++;
                source_pending = 1'b0;
            end
            if (output_transfer) begin
                if ((transferred_i !== expected_i[output_count]) ||
                    (transferred_q !== expected_q[output_count])) begin
                    $fatal(1,
                           "frontend sample %0d mismatch: I=%0d/%0d Q=%0d/%0d",
                           output_count, transferred_i, expected_i[output_count],
                           transferred_q, expected_q[output_count]);
                end
                output_count++;
            end
            if (stalled_output &&
                ((mixed_valid !== held_valid) || (mixed_i !== held_i) ||
                 (mixed_q !== held_q))) begin
                $fatal(1, "frontend output changed under backpressure");
            end
            guard++;
        end

        if ((input_count != VECTOR_COUNT) || (output_count != VECTOR_COUNT)) begin
            $fatal(1, "frontend timeout: accepted=%0d produced=%0d",
                   input_count, output_count);
        end
        expected_phase = LO_PHASE_INCREMENT * VECTOR_COUNT;
        if (lo_phase !== expected_phase) begin
            $fatal(1, "frontend phase mismatch: got 0x%08h expected 0x%08h",
                   lo_phase, expected_phase);
        end

        $display("PASS: qcrate_dsp_frontend_tb verified %0d samples",
                 VECTOR_COUNT);
        $finish;
    end

endmodule

`default_nettype wire
