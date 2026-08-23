`timescale 1ns / 1ps
`default_nettype none

module qcrate_dsp_stream_tb #(
    parameter string SINE_LUT_FILE = "rtl/dsp/tables/sine_quarter_q1_15.mem"
);

    localparam int FRAME_LENGTH = 64;
    localparam int FRAME_COUNT = 4;
    localparam int OUTPUT_COUNT = FRAME_LENGTH * FRAME_COUNT;

    logic clk;
    logic rst_n;
    logic start;
    logic abort_cmd;
    logic soft_reset;
    logic busy;
    logic done_pulse;
    logic error_pulse;
    logic [31:0] completed_frames;
    logic [31:0] current_frame_id;
    logic [31:0] current_sample_index;
    logic [31:0] stall_cycles;

    logic dsp_enable;
    logic dsp_clear;
    logic [31:0] dsp_data;
    logic dsp_valid;
    logic dsp_ready;

    logic [31:0] axis_data;
    logic [3:0] axis_keep;
    logic axis_valid;
    logic axis_ready;
    logic axis_last;

    logic [31:0] expected_words [0:OUTPUT_COUNT-1];

    assign dsp_clear = start || soft_reset;

    qcrate_dsp_chain #(
        .SINE_LUT_FILE              (SINE_LUT_FILE)
    ) u_dsp_chain (
        .clk_i                      (clk),
        .rst_n_i                    (rst_n),
        .enable_i                   (dsp_enable),
        .clear_i                    (dsp_clear),
        .m_data_o                   (dsp_data),
        .m_valid_o                  (dsp_valid),
        .m_ready_i                  (dsp_ready)
    );

    qcrate_stream_engine u_stream_engine (
        .clk_i                      (clk),
        .rst_n_i                    (rst_n),
        .start_i                    (start),
        .abort_i                    (abort_cmd),
        .soft_reset_i               (soft_reset),
        .frame_length_i             (FRAME_LENGTH),
        .frame_count_i              (FRAME_COUNT),
        .stream_mode_i              (32'd1),
        .continuous_i               (1'b0),
        .dsp_tdata_i                (dsp_data),
        .dsp_tvalid_i               (dsp_valid),
        .dsp_enable_o               (dsp_enable),
        .dsp_tready_o               (dsp_ready),
        .busy_o                     (busy),
        .done_pulse_o               (done_pulse),
        .error_pulse_o              (error_pulse),
        .completed_frames_o         (completed_frames),
        .current_frame_id_o         (current_frame_id),
        .current_sample_index_o     (current_sample_index),
        .stall_cycles_o             (stall_cycles),
        .m_axis_tdata_o             (axis_data),
        .m_axis_tkeep_o             (axis_keep),
        .m_axis_tvalid_o            (axis_valid),
        .m_axis_tready_i            (axis_ready),
        .m_axis_tlast_o             (axis_last)
    );

    always #2.5 clk = ~clk;

    initial begin
        int output_count;
        int guard;
        int vector_index;
        int pass_file;
        int started_file;
        string vector_dir;
        logic [31:0] prng;
        logic transfer;
        logic stalled;
        logic [31:0] transferred_data;
        logic transferred_last;
        logic [31:0] held_data;
        logic held_last;

        if (!$value$plusargs("VECTOR_DIR=%s", vector_dir)) begin
            $fatal(1, "VECTOR_DIR plusarg is required");
        end
        started_file = $fopen({vector_dir, "/dsp2b.started"}, "w");
        if (started_file == 0) begin
            $fatal(1, "failed to create DSP-2B started sentinel");
        end
        $fdisplay(started_file, "DSP-2B STARTED");
        $fclose(started_file);
        $readmemh({vector_dir, "/fir_output_words.hex"}, expected_words);
        for (vector_index = 0; vector_index < OUTPUT_COUNT; vector_index++) begin
            if ($isunknown(expected_words[vector_index])) begin
                $fatal(1, "missing or unknown DSP output vector %0d", vector_index);
            end
        end

        clk = 1'b0;
        rst_n = 1'b0;
        start = 1'b0;
        abort_cmd = 1'b0;
        soft_reset = 1'b0;
        axis_ready = 1'b0;
        repeat (12) @(posedge clk);
        rst_n = 1'b1;
        repeat (4) @(posedge clk);

        @(negedge clk);
        start = 1'b1;
        @(negedge clk);
        start = 1'b0;
        if (!busy) begin
            $fatal(1, "DSP stream did not enter busy state");
        end

        output_count = 0;
        guard = 0;
        prng = 32'h6d2b_79f5;
        while (busy && (guard < 200000)) begin
            @(negedge clk);
            prng = {prng[30:0], prng[31] ^ prng[21] ^ prng[1] ^ prng[0]};
            axis_ready = |prng[3:0];
            #1;

            transfer = axis_valid && axis_ready;
            stalled = axis_valid && !axis_ready;
            transferred_data = axis_data;
            transferred_last = axis_last;
            held_data = axis_data;
            held_last = axis_last;

            @(posedge clk);
            #1;
            if (transfer) begin
                if (transferred_data !== expected_words[output_count]) begin
                    $fatal(1,
                           "DSP word %0d mismatch: actual=0x%08h expected=0x%08h",
                           output_count, transferred_data,
                           expected_words[output_count]);
                end
                if (transferred_last !==
                    ((output_count % FRAME_LENGTH) == (FRAME_LENGTH - 1))) begin
                    $fatal(1, "DSP TLAST mismatch at output %0d", output_count);
                end
                if (axis_keep !== 4'hf) begin
                    $fatal(1, "DSP TKEEP mismatch at output %0d", output_count);
                end
                output_count++;
            end
            if (stalled &&
                ((!axis_valid) || (axis_data !== held_data) ||
                 (axis_last !== held_last))) begin
                $fatal(1, "DSP AXI output changed under backpressure");
            end
            guard++;
        end

        if (guard >= 200000) begin
            $fatal(1, "DSP stream timeout after %0d outputs", output_count);
        end
        if (output_count != OUTPUT_COUNT) begin
            $fatal(1, "DSP output count %0d, expected %0d",
                   output_count, OUTPUT_COUNT);
        end
        if (!done_pulse || error_pulse) begin
            $fatal(1, "DSP completion event mismatch: done=%0b error=%0b",
                   done_pulse, error_pulse);
        end
        if ((completed_frames != FRAME_COUNT) ||
            (current_frame_id != FRAME_COUNT - 1) ||
            (current_sample_index != FRAME_LENGTH - 1)) begin
            $fatal(1,
                   "DSP final status mismatch: frames=%0d frame=%0d sample=%0d",
                   completed_frames, current_frame_id, current_sample_index);
        end
        if (stall_cycles == 0) begin
            $fatal(1, "DSP test applied no observable backpressure");
        end

        pass_file = $fopen({vector_dir, "/dsp2b.pass"}, "w");
        if (pass_file == 0) begin
            $fatal(1, "failed to create DSP-2B success sentinel");
        end
        $fdisplay(pass_file, "DSP-2B PASS");
        $fclose(pass_file);
        $display(
            "PASS: qcrate_dsp_stream_tb verified %0d exact words in %0d frames",
            OUTPUT_COUNT, FRAME_COUNT
        );
        $finish;
    end

endmodule

`default_nettype wire
