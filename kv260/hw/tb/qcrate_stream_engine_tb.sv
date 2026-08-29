`timescale 1ns / 1ps
`default_nettype none

module qcrate_stream_engine_tb;

    logic        clk;
    logic        rst_n;
    logic        start;
    logic        arm_triggered;
    logic        shot_trigger;
    logic        abort;
    logic        soft_reset;
    logic [31:0] trigger_shot_id;
    logic [63:0] timebase;
    logic [31:0] frame_length;
    logic [31:0] frame_count;
    logic [31:0] stream_mode;
    logic        continuous;
    logic [31:0] dsp_tdata;
    logic        dsp_tvalid;
    logic        dsp_enable;
    logic        dsp_tready;
    logic        busy;
    logic        armed;
    logic        capture_start_pulse;
    logic        done_pulse;
    logic        error_pulse;
    logic        trigger_seen;
    logic        first_sample_time_valid;
    logic [31:0] accepted_trigger_shot_id;
    logic [31:0] trigger_count;
    logic [31:0] missed_trigger_count;
    logic [63:0] trigger_time;
    logic [63:0] first_sample_time;
    logic [31:0] completed_frames;
    logic [31:0] current_frame_id;
    logic [31:0] current_sample_index;
    logic [31:0] stall_cycles;
    logic [31:0] tdata;
    logic [3:0]  tkeep;
    logic        tvalid;
    logic        tready;
    logic        tlast;

    int unsigned error_count;

    qcrate_stream_engine u_dut (
        .clk_i                      (clk),
        .rst_n_i                    (rst_n),

        .start_i                    (start),
        .arm_triggered_i            (arm_triggered),
        .shot_trigger_i             (shot_trigger),
        .abort_i                    (abort),
        .soft_reset_i               (soft_reset),
        .trigger_shot_id_i          (trigger_shot_id),
        .timebase_i                 (timebase),

        .frame_length_i             (frame_length),
        .frame_count_i              (frame_count),
        .stream_mode_i              (stream_mode),
        .continuous_i               (continuous),

        .dsp_tdata_i                (dsp_tdata),
        .dsp_tvalid_i               (dsp_tvalid),
        .dsp_enable_o               (dsp_enable),
        .dsp_tready_o               (dsp_tready),

        .busy_o                     (busy),
        .armed_o                    (armed),
        .capture_start_pulse_o      (capture_start_pulse),
        .done_pulse_o               (done_pulse),
        .error_pulse_o              (error_pulse),

        .trigger_seen_o             (trigger_seen),
        .first_sample_time_valid_o  (first_sample_time_valid),
        .trigger_shot_id_o          (accepted_trigger_shot_id),
        .trigger_count_o            (trigger_count),
        .missed_trigger_count_o     (missed_trigger_count),
        .trigger_time_o             (trigger_time),
        .first_sample_time_o        (first_sample_time),

        .completed_frames_o         (completed_frames),
        .current_frame_id_o         (current_frame_id),
        .current_sample_index_o     (current_sample_index),
        .stall_cycles_o             (stall_cycles),

        .m_axis_tdata_o             (tdata),
        .m_axis_tkeep_o             (tkeep),
        .m_axis_tvalid_o            (tvalid),
        .m_axis_tready_i            (tready),
        .m_axis_tlast_o             (tlast)
    );

    initial begin
        clk = 1'b0;
        forever #2.5 clk = ~clk;
    end

    qcrate_timebase u_timebase (
        .clk_i  (clk),
        .rst_n_i(rst_n),
        .time_o (timebase)
    );

    task automatic fail(input string message);
        error_count++;
        $error("%s", message);
    endtask

    task automatic expect_bit(
        input logic actual,
        input logic expected,
        input string what
    );
        if (actual !== expected) begin
            fail($sformatf("%s: expected %0b, got %0b", what, expected, actual));
        end
    endtask

    task automatic expect_word(
        input logic [31:0] actual,
        input logic [31:0] expected,
        input string       what
    );
        if (actual !== expected) begin
            fail($sformatf("%s: expected 0x%08h, got 0x%08h",
                           what, expected, actual));
        end
    endtask

    function automatic logic [31:0] expected_tdata(
        input logic [15:0] frame_id,
        input logic [15:0] sample_index
    );
        return {frame_id, sample_index};
    endfunction

    task automatic reset_dut;
        start = 1'b0;
        arm_triggered = 1'b0;
        shot_trigger = 1'b0;
        abort = 1'b0;
        soft_reset = 1'b0;
        trigger_shot_id = 32'h0000_0000;
        frame_length = 32'h0000_0000;
        frame_count = 32'h0000_0000;
        stream_mode = 32'h0000_0000;
        continuous = 1'b0;
        dsp_tdata = 32'h0000_0000;
        dsp_tvalid = 1'b0;
        tready = 1'b1;
        rst_n = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        #1;
    endtask

    task automatic pulse_start;
        @(negedge clk);
        start = 1'b1;
        @(negedge clk);
        start = 1'b0;
    endtask

    task automatic pulse_arm_triggered;
        @(negedge clk);
        arm_triggered = 1'b1;
        @(negedge clk);
        arm_triggered = 1'b0;
    endtask

    task automatic pulse_shot_trigger;
        @(negedge clk);
        shot_trigger = 1'b1;
        @(negedge clk);
        shot_trigger = 1'b0;
    endtask

    task automatic pulse_soft_reset;
        @(negedge clk);
        soft_reset = 1'b1;
        @(posedge clk);
        #1;
        soft_reset = 1'b0;
    endtask

    task automatic expect_transfer(
        input logic [15:0] frame_id,
        input logic [15:0] sample_index,
        input logic        expected_last,
        input string       what
    );
        int guard;

        guard = 0;
        while (!tvalid && (guard < 20)) begin
            @(negedge clk);
            guard++;
        end
        if (!tvalid) begin
            fail({what, ": timed out waiting for TVALID"});
        end

        expect_word(tdata, expected_tdata(frame_id, sample_index), {what, " TDATA"});
        expect_word({28'd0, tkeep}, 32'h0000_000F, {what, " TKEEP"});
        expect_bit(tlast, expected_last, {what, " TLAST"});

        @(posedge clk);
        #1;
    endtask

    task automatic expect_stalled_word(
        input logic [31:0] expected_data,
        input logic        expected_last,
        input string       what
    );
        expect_bit(tvalid, 1'b1, {what, " TVALID"});
        expect_word(tdata, expected_data, {what, " TDATA stable"});
        expect_bit(tlast, expected_last, {what, " TLAST stable"});
    endtask

    initial begin
        logic [31:0] held_data;
        logic        held_last;
        logic [63:0] expected_trigger_time;
        logic [63:0] expected_first_sample_time;

        error_count = 0;
        reset_dut();

        expect_bit(busy, 1'b0, "reset busy");
        expect_bit(armed, 1'b0, "reset armed");
        expect_bit(tvalid, 1'b0, "reset TVALID");
        expect_bit(dsp_enable, 1'b0, "reset DSP enable");
        expect_bit(dsp_tready, 1'b0, "reset DSP ready");
        expect_word({28'd0, tkeep}, 32'h0000_000F, "reset TKEEP");

        frame_length = 32'd1;
        frame_count = 32'd1;
        continuous = 1'b0;
        pulse_start();
        expect_bit(busy, 1'b1, "one-word start busy");
        expect_bit(capture_start_pulse, 1'b1,
                   "immediate start emits capture-start pulse");
        expect_bit(dsp_enable, 1'b0, "pattern mode isolates DSP enable");
        expect_bit(dsp_tready, 1'b0, "pattern mode isolates DSP source");
        expect_transfer(16'd0, 16'd0, 1'b1, "one-word frame");
        expect_bit(done_pulse, 1'b1, "one-word done pulse");
        expect_bit(busy, 1'b0, "one-word done busy");
        expect_word(completed_frames, 32'd1, "one-word completed frames");
        expect_bit(first_sample_time_valid, 1'b1,
                   "immediate capture first-sample timestamp valid");
        expect_bit(trigger_seen, 1'b0,
                   "immediate capture has no hardware trigger");
        @(posedge clk);
        #1;
        expect_bit(done_pulse, 1'b0, "one-word done pulse clears");

        frame_length = 32'd3;
        frame_count = 32'd2;
        continuous = 1'b0;
        pulse_start();
        expect_transfer(16'd0, 16'd0, 1'b0, "finite frame 0 sample 0");
        expect_transfer(16'd0, 16'd1, 1'b0, "finite frame 0 sample 1");
        expect_transfer(16'd0, 16'd2, 1'b1, "finite frame 0 sample 2");
        expect_transfer(16'd1, 16'd0, 1'b0, "finite frame 1 sample 0");
        expect_transfer(16'd1, 16'd1, 1'b0, "finite frame 1 sample 1");
        expect_transfer(16'd1, 16'd2, 1'b1, "finite frame 1 sample 2");
        expect_bit(done_pulse, 1'b1, "finite done pulse");
        expect_word(completed_frames, 32'd2, "finite completed frames");
        expect_word(current_frame_id, 32'd1, "finite current frame id");
        expect_word(current_sample_index, 32'd2, "finite current sample index");
        @(posedge clk);
        #1;

        frame_length = 32'd2;
        frame_count = 32'd1;
        continuous = 1'b1;
        pulse_start();
        expect_transfer(16'd0, 16'd0, 1'b0, "continuous frame 0 sample 0");
        expect_transfer(16'd0, 16'd1, 1'b1, "continuous frame 0 sample 1");
        expect_transfer(16'd1, 16'd0, 1'b0, "continuous frame 1 sample 0");
        expect_transfer(16'd1, 16'd1, 1'b1, "continuous frame 1 sample 1");
        expect_transfer(16'd2, 16'd0, 1'b0, "continuous frame 2 sample 0");
        expect_bit(done_pulse, 1'b0, "continuous no done pulse");
        expect_bit(busy, 1'b1, "continuous remains busy");
        pulse_soft_reset();
        expect_bit(busy, 1'b0, "soft reset clears busy");
        expect_bit(tvalid, 1'b0, "soft reset clears TVALID");
        expect_word(completed_frames, 32'd0, "soft reset clears completed frames");

        frame_length = 32'd4;
        frame_count = 32'd1;
        continuous = 1'b0;
        pulse_start();
        tready = 1'b0;
        held_data = tdata;
        held_last = tlast;
        repeat (4) begin
            @(posedge clk);
            #1;
            expect_stalled_word(held_data, held_last, "backpressure hold");
        end
        expect_word(stall_cycles, 32'd4, "backpressure stall cycles");
        @(negedge clk);
        tready = 1'b1;
        expect_transfer(16'd0, 16'd0, 1'b0, "backpressure release sample 0");
        expect_transfer(16'd0, 16'd1, 1'b0, "backpressure release sample 1");
        pulse_soft_reset();

        frame_length = 32'd5;
        frame_count = 32'd1;
        continuous = 1'b0;
        pulse_start();
        expect_transfer(16'd0, 16'd0, 1'b0, "abort first sample");
        @(negedge clk);
        tready = 1'b0;
        held_data = tdata;
        held_last = tlast;
        abort = 1'b1;
        @(posedge clk);
        #1;
        abort = 1'b0;
        expect_stalled_word(held_data, held_last, "finite abort while stalled");
        expect_bit(busy, 1'b1, "finite abort preserves issued frame");
        @(negedge clk);
        tready = 1'b1;
        @(posedge clk);
        #1;
        expect_bit(busy, 1'b1, "finite abort drains after accepted word");
        expect_transfer(16'd0, 16'd2, 1'b0, "finite abort drain sample 2");
        expect_transfer(16'd0, 16'd3, 1'b0, "finite abort drain sample 3");
        expect_transfer(16'd0, 16'd4, 1'b1, "finite abort drain sample 4");
        expect_bit(done_pulse, 1'b1, "finite abort drain completes frame");
        expect_bit(busy, 1'b0, "finite abort drain returns idle");

        frame_length = 32'd0;
        frame_count = 32'd1;
        continuous = 1'b0;
        pulse_start();
        expect_bit(error_pulse, 1'b1, "zero frame length error");
        expect_bit(busy, 1'b0, "zero frame length no busy");
        @(posedge clk);
        #1;
        expect_bit(error_pulse, 1'b0, "zero frame length error clears");

        frame_length = 32'd4;
        frame_count = 32'd0;
        continuous = 1'b0;
        pulse_start();
        expect_bit(error_pulse, 1'b1, "zero finite frame count error");
        expect_bit(busy, 1'b0, "zero finite frame count no busy");

        pulse_soft_reset();
        frame_length = 32'd2;
        frame_count = 32'd1;
        continuous = 1'b0;
        trigger_shot_id = 32'd7;

        pulse_shot_trigger();
        expect_word(missed_trigger_count, 32'd1,
                    "trigger before arm is counted and ignored");
        expect_bit(busy, 1'b0, "trigger before arm does not start stream");

        pulse_arm_triggered();
        expect_bit(armed, 1'b1, "triggered capture becomes armed");
        expect_bit(busy, 1'b0, "armed capture is not yet busy");
        expect_bit(tvalid, 1'b0, "armed capture emits no samples");
        expect_bit(first_sample_time_valid, 1'b0,
                   "armed capture has no sample timestamp");

        repeat (4) @(posedge clk);
        @(negedge clk);
        expected_trigger_time = timebase;
        shot_trigger = 1'b1;
        @(negedge clk);
        shot_trigger = 1'b0;
        expect_bit(armed, 1'b0, "accepted trigger clears armed");
        expect_bit(busy, 1'b1, "accepted trigger starts stream");
        expect_bit(capture_start_pulse, 1'b1,
                   "accepted trigger emits capture-start pulse");
        expect_bit(trigger_seen, 1'b1, "accepted trigger recorded");
        expect_word(accepted_trigger_shot_id, 32'd7,
                    "accepted trigger shot identity");
        expect_word(trigger_count, 32'd1, "accepted trigger count");
        if (trigger_time !== expected_trigger_time)
            fail("trigger timestamp does not match acceptance cycle");

        expected_first_sample_time = timebase;
        expect_transfer(16'd0, 16'd0, 1'b0, "triggered sample 0");
        expect_bit(first_sample_time_valid, 1'b1,
                   "triggered first-sample timestamp valid");
        if (first_sample_time !== expected_first_sample_time)
            fail("first-sample timestamp does not match first AXI transfer");
        expect_transfer(16'd0, 16'd1, 1'b1, "triggered sample 1");
        expect_bit(done_pulse, 1'b1, "triggered capture done");

        pulse_soft_reset();
        frame_length = 32'd2;
        frame_count = 32'd1;
        pulse_arm_triggered();
        expect_bit(armed, 1'b1, "abort precondition armed");
        @(negedge clk);
        abort = 1'b1;
        @(posedge clk);
        #1;
        abort = 1'b0;
        expect_bit(armed, 1'b0, "abort clears triggered arm");
        expect_bit(busy, 1'b1, "armed abort starts transport drain");
        expect_bit(trigger_seen, 1'b0, "cancel drain is not a shot trigger");
        expect_transfer(16'd0, 16'd0, 1'b0, "cancel drain sample 0");
        expect_transfer(16'd0, 16'd1, 1'b1, "cancel drain sample 1");
        expect_bit(done_pulse, 1'b1, "cancel drain completes descriptor");
        expect_bit(busy, 1'b0, "cancel drain returns stream idle");

        if (error_count == 0) begin
            $display("PASS: qcrate_stream_engine_tb");
            $finish;
        end else begin
            $fatal(1, "FAIL: qcrate_stream_engine_tb saw %0d error(s)", error_count);
        end
    end

endmodule

`default_nettype wire
