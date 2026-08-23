`timescale 1ns / 1ps
`default_nettype none

module qcrate_fir_decim16_tb;

    localparam int INPUT_COUNT = 4096;
    localparam int OUTPUT_COUNT = 256;

    logic clk;
    logic rst_n;
    logic clear;
    logic signed [17:0] input_i;
    logic signed [17:0] input_q;
    logic input_valid;
    logic input_ready;
    logic signed [15:0] output_i;
    logic signed [15:0] output_q;
    logic output_valid;
    logic output_ready;

    logic signed [35:0] accumulator_i_vectors [0:OUTPUT_COUNT-1];
    logic signed [35:0] accumulator_q_vectors [0:OUTPUT_COUNT-1];
    logic signed [15:0] output_i_vectors [0:OUTPUT_COUNT-1];
    logic signed [15:0] output_q_vectors [0:OUTPUT_COUNT-1];

    qcrate_fir_decim16 u_dut (
        .clk_i                       (clk),
        .rst_n_i                     (rst_n),
        .clear_i                     (clear),
        .s_i_i                       (input_i),
        .s_q_i                       (input_q),
        .s_valid_i                   (input_valid),
        .s_ready_o                   (input_ready),
        .m_i_o                       (output_i),
        .m_q_o                       (output_q),
        .m_valid_o                   (output_valid),
        .m_ready_i                   (output_ready)
    );

    always #2.5 clk = ~clk;

    initial begin
        int input_count;
        int accumulator_count;
        int output_count;
        int guard;
        int input_i_file;
        int input_q_file;
        int pass_file;
        int started_file;
        int scan_result;
        int vector_index;
        string vector_dir;
        logic source_pending;
        logic [31:0] prng;
        logic input_transfer;
        logic accumulator_transfer;
        logic output_transfer;
        logic output_stalled;
        logic signed [35:0] transferred_accumulator_i;
        logic signed [35:0] transferred_accumulator_q;
        logic signed [15:0] transferred_output_i;
        logic signed [15:0] transferred_output_q;
        logic signed [15:0] held_output_i;
        logic signed [15:0] held_output_q;

        if (!$value$plusargs("VECTOR_DIR=%s", vector_dir)) begin
            $fatal(1, "VECTOR_DIR plusarg is required");
        end
        started_file = $fopen({vector_dir, "/xsim.started"}, "w");
        if (started_file == 0) begin
            $fatal(1, "failed to create FIR XSim started sentinel");
        end
        $fdisplay(started_file, "DSP-2A STARTED");
        $fclose(started_file);
        input_i_file = $fopen({vector_dir, "/fir_input_i_q1_17.hex"}, "r");
        input_q_file = $fopen({vector_dir, "/fir_input_q_q1_17.hex"}, "r");
        if ((input_i_file == 0) || (input_q_file == 0)) begin
            $fatal(1, "failed to open FIR input vectors in %s", vector_dir);
        end
        $readmemh({vector_dir, "/fir_accumulator_i.hex"},
                  accumulator_i_vectors);
        $readmemh({vector_dir, "/fir_accumulator_q.hex"},
                  accumulator_q_vectors);
        $readmemh({vector_dir, "/fir_output_i_q1_15.hex"},
                  output_i_vectors);
        $readmemh({vector_dir, "/fir_output_q_q1_15.hex"},
                  output_q_vectors);
        for (vector_index = 0; vector_index < OUTPUT_COUNT; vector_index++) begin
            if ($isunknown(accumulator_i_vectors[vector_index]) ||
                $isunknown(accumulator_q_vectors[vector_index]) ||
                $isunknown(output_i_vectors[vector_index]) ||
                $isunknown(output_q_vectors[vector_index])) begin
                $fatal(1, "missing or unknown expected FIR vector %0d",
                       vector_index);
            end
        end

        clk = 1'b0;
        rst_n = 1'b0;
        clear = 1'b0;
        input_i = '0;
        input_q = '0;
        input_valid = 1'b0;
        output_ready = 1'b0;
        repeat (12) @(posedge clk);
        rst_n = 1'b1;
        repeat (4) @(posedge clk);
        clear = 1'b1;
        repeat (4) @(posedge clk);
        clear = 1'b0;

        input_count = 0;
        accumulator_count = 0;
        output_count = 0;
        guard = 0;
        source_pending = 1'b0;
        prng = 32'h4d59_5df4;

        while ((output_count < OUTPUT_COUNT) && (guard < 100000)) begin
            @(negedge clk);
            prng = {prng[30:0], prng[31] ^ prng[21] ^ prng[1] ^ prng[0]};
            output_ready = |prng[3:0];
            if (!source_pending && (input_count < INPUT_COUNT) && |prng[5:4]) begin
                source_pending = 1'b1;
                scan_result = $fscanf(input_i_file, "%h", input_i);
                if (scan_result != 1) begin
                    $fatal(1, "failed to read FIR I input %0d", input_count);
                end
                scan_result = $fscanf(input_q_file, "%h", input_q);
                if (scan_result != 1) begin
                    $fatal(1, "failed to read FIR Q input %0d", input_count);
                end
            end
            input_valid = source_pending;
            #1;

            input_transfer = input_valid && input_ready;
            accumulator_transfer = u_dut.fir_m_valid && u_dut.fir_m_ready;
            output_transfer = output_valid && output_ready;
            output_stalled = output_valid && !output_ready;
            transferred_accumulator_i = u_dut.fir_accumulator_i;
            transferred_accumulator_q = u_dut.fir_accumulator_q;
            transferred_output_i = output_i;
            transferred_output_q = output_q;
            held_output_i = output_i;
            held_output_q = output_q;

            @(posedge clk);
            #1;
            if (input_transfer) begin
                input_count++;
                source_pending = 1'b0;
            end
            if (accumulator_transfer) begin
                if ((transferred_accumulator_i !==
                     accumulator_i_vectors[accumulator_count]) ||
                    (transferred_accumulator_q !==
                     accumulator_q_vectors[accumulator_count])) begin
                    $display(
                        "FIR accumulator %0d mismatch after %0d inputs:",
                        accumulator_count, input_count);
                    $display("  I actual/expected = %0d / %0d",
                             transferred_accumulator_i,
                             accumulator_i_vectors[accumulator_count]);
                    $display("  Q actual/expected = %0d / %0d",
                             transferred_accumulator_q,
                             accumulator_q_vectors[accumulator_count]);
                    $fatal(1, "FIR accumulator mismatch");
                end
                accumulator_count++;
            end
            if (output_transfer) begin
                if ((transferred_output_i !== output_i_vectors[output_count]) ||
                    (transferred_output_q !== output_q_vectors[output_count])) begin
                    $fatal(1,
                           "FIR output %0d mismatch: I=%0d/%0d Q=%0d/%0d",
                           output_count, transferred_output_i,
                           output_i_vectors[output_count], transferred_output_q,
                           output_q_vectors[output_count]);
                end
                output_count++;
            end
            if (output_stalled &&
                ((output_i !== held_output_i) || (output_q !== held_output_q) ||
                 !output_valid)) begin
                $fatal(1, "FIR output changed under backpressure");
            end
            guard++;
        end

        if ((input_count != INPUT_COUNT) ||
            (accumulator_count != OUTPUT_COUNT) ||
            (output_count != OUTPUT_COUNT)) begin
            $fatal(1, "FIR timeout: inputs=%0d accumulators=%0d outputs=%0d",
                   input_count, accumulator_count, output_count);
        end

        $fclose(input_i_file);
        $fclose(input_q_file);
        pass_file = $fopen({vector_dir, "/xsim.pass"}, "w");
        if (pass_file == 0) begin
            $fatal(1, "failed to create XSim success sentinel in %s", vector_dir);
        end
        $fdisplay(pass_file, "DSP-2A PASS");
        $fclose(pass_file);

        $display("PASS: qcrate_fir_decim16_tb verified %0d exact outputs",
                 OUTPUT_COUNT);
        $finish;
    end

endmodule

`default_nettype wire
