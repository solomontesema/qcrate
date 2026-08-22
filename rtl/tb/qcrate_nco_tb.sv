`timescale 1ns / 1ps
`default_nettype none

module qcrate_nco_tb;

    localparam int VECTOR_COUNT = 1024;
    localparam logic [31:0] LO_PHASE_INCREMENT = 32'h251e_b852;

    logic clk;
    logic rst_n;
    logic ce;
    logic phase_load;
    logic advance;
    logic valid;
    logic signed [15:0] sine;
    logic signed [15:0] cosine;
    logic [31:0] phase;

    logic signed [15:0] expected_sine [0:VECTOR_COUNT-1];
    logic signed [15:0] expected_cosine [0:VECTOR_COUNT-1];

    qcrate_nco u_dut (
        .clk_i                       (clk),
        .rst_n_i                     (rst_n),
        .ce_i                        (ce),
        .phase_load_i                (phase_load),
        .phase_initial_i             (32'h0000_0000),
        .phase_increment_i           (LO_PHASE_INCREMENT),
        .advance_i                   (advance),
        .valid_o                     (valid),
        .sine_o                      (sine),
        .cosine_o                    (cosine),
        .phase_o                     (phase)
    );

    always #2.5 clk = ~clk;

    initial begin
        int accepted_count;
        int output_count;
        int guard;
        logic [31:0] prng;
        logic held_valid;
        logic signed [15:0] held_sine;
        logic signed [15:0] held_cosine;
        logic [31:0] held_phase;
        logic [31:0] expected_phase;

        $readmemh("build/dsp/rtl_vectors/lo_sin_q1_15.hex", expected_sine);
        $readmemh("build/dsp/rtl_vectors/lo_cos_q1_15.hex", expected_cosine);

        clk = 1'b0;
        rst_n = 1'b0;
        ce = 1'b0;
        phase_load = 1'b0;
        advance = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        @(negedge clk);
        phase_load = 1'b1;
        @(posedge clk);
        #1;
        phase_load = 1'b0;

        accepted_count = 0;
        output_count = 0;
        guard = 0;
        prng = 32'h6d2b_79f5;

        while ((output_count < VECTOR_COUNT) && (guard < 20000)) begin
            @(negedge clk);
            prng = {prng[30:0], prng[31] ^ prng[21] ^ prng[1] ^ prng[0]};
            if (accepted_count < VECTOR_COUNT) begin
                ce = |prng[1:0];
                advance = ce && |prng[3:2];
            end else begin
                ce = 1'b1;
                advance = 1'b0;
            end

            held_valid = valid;
            held_sine = sine;
            held_cosine = cosine;
            held_phase = phase;

            @(posedge clk);
            #1;
            if (ce && advance) begin
                accepted_count++;
            end
            if (!ce) begin
                if ((valid !== held_valid) || (sine !== held_sine) ||
                    (cosine !== held_cosine) || (phase !== held_phase)) begin
                    $fatal(1, "NCO changed while CE was low");
                end
            end else if (valid) begin
                if ((sine !== expected_sine[output_count]) ||
                    (cosine !== expected_cosine[output_count])) begin
                    $fatal(1,
                           "NCO sample %0d mismatch: sine=%0d/%0d cosine=%0d/%0d",
                           output_count, sine, expected_sine[output_count],
                           cosine, expected_cosine[output_count]);
                end
                output_count++;
            end
            guard++;
        end

        if (output_count != VECTOR_COUNT) begin
            $fatal(1, "NCO test timed out after %0d outputs", output_count);
        end
        expected_phase = LO_PHASE_INCREMENT * VECTOR_COUNT;
        if (phase !== expected_phase) begin
            $fatal(1, "NCO phase mismatch: got 0x%08h expected 0x%08h",
                   phase, expected_phase);
        end

        $display("PASS: qcrate_nco_tb verified %0d samples", VECTOR_COUNT);
        $finish;
    end

endmodule

`default_nettype wire
