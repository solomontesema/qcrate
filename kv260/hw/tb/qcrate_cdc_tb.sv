`timescale 1ns / 1ps
`default_nettype none

module qcrate_cdc_tb;

    logic ctrl_clk;
    logic stream_clk;
    logic ctrl_rst_n;
    logic stream_rst_n;

    logic frame_done_src;
    logic stream_error_src;
    logic frame_done_dst;
    logic stream_error_dst;
    int unsigned frame_done_count;
    int unsigned stream_error_count;

    logic [31:0] cmd_frame_length;
    logic [31:0] cmd_frame_count;
    logic [31:0] cmd_stream_mode;
    logic        cmd_continuous;
    logic        start_cmd;
    logic        arm_triggered_cmd;
    logic        abort_cmd;
    logic        soft_reset_cmd;
    logic        command_busy;
    logic [31:0] active_frame_length;
    logic [31:0] active_frame_count;
    logic [31:0] active_stream_mode;
    logic        active_continuous;
    logic        start_pulse;
    logic        arm_triggered_pulse;
    logic        abort_pulse;
    logic        soft_reset_pulse;
    int unsigned start_count;
    int unsigned arm_triggered_count;
    int unsigned abort_count;
    int unsigned soft_reset_count;

    logic        live_busy;
    logic        live_armed;
    logic        live_trigger_seen;
    logic        live_first_sample_time_valid;
    logic [31:0] live_completed_frames;
    logic [31:0] live_current_frame_id;
    logic [31:0] live_current_sample_index;
    logic [31:0] live_stall_cycles;
    logic [31:0] live_trigger_shot_id;
    logic [31:0] live_trigger_count;
    logic [31:0] live_missed_trigger_count;
    logic [63:0] live_trigger_time;
    logic [63:0] live_first_sample_time;
    logic        snap_busy;
    logic        snap_armed;
    logic        snap_trigger_seen;
    logic        snap_first_sample_time_valid;
    logic [31:0] snap_completed_frames;
    logic [31:0] snap_current_frame_id;
    logic [31:0] snap_current_sample_index;
    logic [31:0] snap_stall_cycles;
    logic [31:0] snap_trigger_shot_id;
    logic [31:0] snap_trigger_count;
    logic [31:0] snap_missed_trigger_count;
    logic [63:0] snap_trigger_time;
    logic [63:0] snap_first_sample_time;

    int unsigned error_count;

    qcrate_event_cdc u_event_cdc (
        .src_clk_i              (stream_clk),
        .src_rst_n_i            (stream_rst_n),
        .frame_done_pulse_i     (frame_done_src),
        .stream_error_pulse_i   (stream_error_src),

        .dst_clk_i              (ctrl_clk),
        .dst_rst_n_i            (ctrl_rst_n),
        .frame_done_pulse_o     (frame_done_dst),
        .stream_error_pulse_o   (stream_error_dst)
    );

    qcrate_command_cdc u_command_cdc (
        .ctrl_clk_i                 (ctrl_clk),
        .ctrl_rst_n_i               (ctrl_rst_n),

        .frame_length_i             (cmd_frame_length),
        .frame_count_i              (cmd_frame_count),
        .stream_mode_i              (cmd_stream_mode),
        .continuous_i               (cmd_continuous),

        .start_cmd_i                (start_cmd),
        .arm_triggered_cmd_i        (arm_triggered_cmd),
        .abort_cmd_i                (abort_cmd),
        .soft_reset_cmd_i           (soft_reset_cmd),
        .command_busy_o             (command_busy),

        .stream_clk_i               (stream_clk),
        .stream_rst_n_i             (stream_rst_n),

        .active_frame_length_o      (active_frame_length),
        .active_frame_count_o       (active_frame_count),
        .active_stream_mode_o       (active_stream_mode),
        .active_continuous_o        (active_continuous),

        .start_pulse_o              (start_pulse),
        .arm_triggered_pulse_o      (arm_triggered_pulse),
        .abort_pulse_o              (abort_pulse),
        .soft_reset_pulse_o         (soft_reset_pulse)
    );

    qcrate_status_cdc u_status_cdc (
        .stream_clk_i               (stream_clk),
        .stream_rst_n_i             (stream_rst_n),

        .stream_busy_i              (live_busy),
        .stream_armed_i             (live_armed),
        .trigger_seen_i             (live_trigger_seen),
        .first_sample_time_valid_i  (live_first_sample_time_valid),
        .completed_frames_i         (live_completed_frames),
        .current_frame_id_i         (live_current_frame_id),
        .current_sample_index_i     (live_current_sample_index),
        .stall_cycles_i             (live_stall_cycles),
        .trigger_shot_id_i          (live_trigger_shot_id),
        .trigger_count_i            (live_trigger_count),
        .missed_trigger_count_i     (live_missed_trigger_count),
        .trigger_time_i             (live_trigger_time),
        .first_sample_time_i        (live_first_sample_time),

        .ctrl_clk_i                 (ctrl_clk),
        .ctrl_rst_n_i               (ctrl_rst_n),

        .stream_busy_o              (snap_busy),
        .stream_armed_o             (snap_armed),
        .trigger_seen_o             (snap_trigger_seen),
        .first_sample_time_valid_o  (snap_first_sample_time_valid),
        .completed_frames_o         (snap_completed_frames),
        .current_frame_id_o         (snap_current_frame_id),
        .current_sample_index_o     (snap_current_sample_index),
        .stall_cycles_o             (snap_stall_cycles),
        .trigger_shot_id_o          (snap_trigger_shot_id),
        .trigger_count_o            (snap_trigger_count),
        .missed_trigger_count_o     (snap_missed_trigger_count),
        .trigger_time_o             (snap_trigger_time),
        .first_sample_time_o        (snap_first_sample_time)
    );

    initial begin
        ctrl_clk = 1'b0;
        forever #5 ctrl_clk = ~ctrl_clk;
    end

    initial begin
        stream_clk = 1'b0;
        forever #2.5 stream_clk = ~stream_clk;
    end

    always_ff @(posedge ctrl_clk) begin
        if (!ctrl_rst_n) begin
            frame_done_count <= 0;
            stream_error_count <= 0;
        end else begin
            if (frame_done_dst) begin
                frame_done_count <= frame_done_count + 1;
            end
            if (stream_error_dst) begin
                stream_error_count <= stream_error_count + 1;
            end
        end
    end

    always_ff @(posedge stream_clk) begin
        if (!stream_rst_n) begin
            start_count <= 0;
            arm_triggered_count <= 0;
            abort_count <= 0;
            soft_reset_count <= 0;
        end else begin
            if (start_pulse) begin
                start_count <= start_count + 1;
            end
            if (arm_triggered_pulse)
                arm_triggered_count <= arm_triggered_count + 1;
            if (abort_pulse) begin
                abort_count <= abort_count + 1;
            end
            if (soft_reset_pulse) begin
                soft_reset_count <= soft_reset_count + 1;
            end
        end
    end

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
        input string what
    );
        if (actual !== expected) begin
            fail($sformatf("%s: expected 0x%08h, got 0x%08h",
                           what, expected, actual));
        end
    endtask

    task automatic reset_dut;
        ctrl_rst_n = 1'b0;
        stream_rst_n = 1'b0;
        frame_done_src = 1'b0;
        stream_error_src = 1'b0;
        cmd_frame_length = 32'h0000_0000;
        cmd_frame_count = 32'h0000_0000;
        cmd_stream_mode = 32'h0000_0000;
        cmd_continuous = 1'b0;
        start_cmd = 1'b0;
        arm_triggered_cmd = 1'b0;
        abort_cmd = 1'b0;
        soft_reset_cmd = 1'b0;
        live_busy = 1'b0;
        live_armed = 1'b0;
        live_trigger_seen = 1'b0;
        live_first_sample_time_valid = 1'b0;
        live_completed_frames = 32'h0000_0000;
        live_current_frame_id = 32'h0000_0000;
        live_current_sample_index = 32'h0000_0000;
        live_stall_cycles = 32'h0000_0000;
        live_trigger_shot_id = 32'h0000_0000;
        live_trigger_count = 32'h0000_0000;
        live_missed_trigger_count = 32'h0000_0000;
        live_trigger_time = 64'h0000_0000_0000_0000;
        live_first_sample_time = 64'h0000_0000_0000_0000;
        repeat (5) @(posedge ctrl_clk);
        repeat (5) @(posedge stream_clk);
        stream_rst_n = 1'b1;
        ctrl_rst_n = 1'b1;
        repeat (5) @(posedge ctrl_clk);
    endtask

    task automatic pulse_stream_event(
        input logic frame_done,
        input logic stream_error
    );
        @(negedge stream_clk);
        frame_done_src = frame_done;
        stream_error_src = stream_error;
        @(negedge stream_clk);
        frame_done_src = 1'b0;
        stream_error_src = 1'b0;
    endtask

    task automatic wait_ctrl_counts(
        input int unsigned expected_done,
        input int unsigned expected_error,
        input string       what
    );
        bit matched;
        matched = 1'b0;

        repeat (80) begin
            @(posedge ctrl_clk);
            #1;
            if ((frame_done_count == expected_done) &&
                (stream_error_count == expected_error)) begin
                matched = 1'b1;
            end
        end

        if (!matched) begin
            fail($sformatf("%s: expected event counts done=%0d error=%0d, got done=%0d error=%0d",
                           what, expected_done, expected_error,
                           frame_done_count, stream_error_count));
        end
    endtask

    task automatic issue_command(
        input logic [31:0] frame_length,
        input logic [31:0] frame_count,
        input logic [31:0] stream_mode,
        input logic        continuous,
        input logic        start,
        input logic        arm_triggered,
        input logic        abort,
        input logic        soft_reset
    );
        @(negedge ctrl_clk);
        cmd_frame_length = frame_length;
        cmd_frame_count = frame_count;
        cmd_stream_mode = stream_mode;
        cmd_continuous = continuous;
        start_cmd = start;
        arm_triggered_cmd = arm_triggered;
        abort_cmd = abort;
        soft_reset_cmd = soft_reset;
        @(negedge ctrl_clk);
        start_cmd = 1'b0;
        arm_triggered_cmd = 1'b0;
        abort_cmd = 1'b0;
        soft_reset_cmd = 1'b0;
    endtask

    task automatic wait_stream_counts(
        input int unsigned expected_start,
        input int unsigned expected_arm_triggered,
        input int unsigned expected_abort,
        input int unsigned expected_soft_reset,
        input string       what
    );
        bit matched;
        matched = 1'b0;

        repeat (120) begin
            @(posedge stream_clk);
            #1;
            if ((start_count == expected_start) &&
                (arm_triggered_count == expected_arm_triggered) &&
                (abort_count == expected_abort) &&
                (soft_reset_count == expected_soft_reset)) begin
                matched = 1'b1;
            end
        end

        if (!matched) begin
            fail($sformatf("%s: command count mismatch", what));
        end
    endtask

    task automatic wait_command_idle;
        bit idle_seen;
        idle_seen = 1'b0;

        repeat (120) begin
            @(posedge ctrl_clk);
            #1;
            if (!command_busy) begin
                idle_seen = 1'b1;
            end
        end

        if (!idle_seen) begin
            fail("command_busy did not deassert");
        end
    endtask

    task automatic wait_snapshot(
        input logic        expected_busy,
        input logic [31:0] expected_completed,
        input logic [31:0] expected_frame_id,
        input logic [31:0] expected_sample_index,
        input logic [31:0] expected_stall_cycles,
        input string       what
    );
        bit matched;
        matched = 1'b0;

        repeat (160) begin
            @(posedge ctrl_clk);
            #1;
            if ((snap_busy == expected_busy) &&
                (snap_completed_frames == expected_completed) &&
                (snap_current_frame_id == expected_frame_id) &&
                (snap_current_sample_index == expected_sample_index) &&
                (snap_stall_cycles == expected_stall_cycles)) begin
                matched = 1'b1;
            end
        end

        if (!matched) begin
            fail($sformatf("%s: snapshot mismatch", what));
            expect_bit(snap_busy, expected_busy, {what, " busy"});
            expect_word(snap_completed_frames, expected_completed,
                        {what, " completed"});
            expect_word(snap_current_frame_id, expected_frame_id,
                        {what, " frame_id"});
            expect_word(snap_current_sample_index, expected_sample_index,
                        {what, " sample_index"});
            expect_word(snap_stall_cycles, expected_stall_cycles,
                        {what, " stall_cycles"});
        end
    endtask

    initial begin
        error_count = 0;
        reset_dut();

        pulse_stream_event(1'b1, 1'b0);
        wait_ctrl_counts(1, 0, "single frame-done event");
        pulse_stream_event(1'b0, 1'b1);
        wait_ctrl_counts(1, 1, "single stream-error event");
        pulse_stream_event(1'b1, 1'b1);
        wait_ctrl_counts(2, 2, "simultaneous events");

        issue_command(32'd4096, 32'd7, 32'h0000_0002, 1'b1,
                      1'b1, 1'b0, 1'b0, 1'b0);
        wait_stream_counts(1, 0, 0, 0, "start command delivery");
        expect_word(active_frame_length, 32'd4096, "active frame length");
        expect_word(active_frame_count, 32'd7, "active frame count");
        expect_word(active_stream_mode, 32'h0000_0002, "active stream mode");
        expect_bit(active_continuous, 1'b1, "active continuous");
        wait_command_idle();

        issue_command(32'd64, 32'd1, 32'h0000_0003, 1'b0,
                      1'b0, 1'b1, 1'b1, 1'b1);
        wait_stream_counts(1, 1, 1, 1,
                           "arm, abort, and soft-reset delivery");
        expect_word(active_frame_length, 32'd64, "second active frame length");
        expect_word(active_frame_count, 32'd1, "second active frame count");
        expect_word(active_stream_mode, 32'h0000_0003, "second active mode");
        expect_bit(active_continuous, 1'b0, "second active continuous");
        wait_command_idle();

        issue_command(32'd128, 32'd2, 32'h0000_0004, 1'b0,
                      1'b1, 1'b0, 1'b0, 1'b0);
        issue_command(32'd999, 32'd999, 32'h0000_0009, 1'b1,
                      1'b0, 1'b0, 1'b1, 1'b0);
        wait_stream_counts(2, 1, 1, 1, "busy command rejection");
        expect_word(active_frame_length, 32'd128, "busy reject frame length");
        expect_word(active_frame_count, 32'd2, "busy reject frame count");
        expect_word(active_stream_mode, 32'h0000_0004, "busy reject mode");
        wait_command_idle();

        @(negedge stream_clk);
        live_busy = 1'b1;
        live_armed = 1'b1;
        live_trigger_seen = 1'b1;
        live_first_sample_time_valid = 1'b1;
        live_completed_frames = 32'h0000_0010;
        live_current_frame_id = 32'h0000_0020;
        live_current_sample_index = 32'h0000_0030;
        live_stall_cycles = 32'h0000_0040;
        live_trigger_shot_id = 32'h0000_0050;
        live_trigger_count = 32'h0000_0060;
        live_missed_trigger_count = 32'h0000_0070;
        live_trigger_time = 64'h1122_3344_5566_7788;
        live_first_sample_time = 64'h99AA_BBCC_DDEE_FF00;
        wait_snapshot(1'b1, 32'h0000_0010, 32'h0000_0020,
                      32'h0000_0030, 32'h0000_0040,
                      "first status snapshot");
        expect_bit(snap_armed, 1'b1, "first snapshot armed");
        expect_bit(snap_trigger_seen, 1'b1, "first snapshot trigger seen");
        expect_bit(snap_first_sample_time_valid, 1'b1,
                   "first snapshot timestamp valid");
        expect_word(snap_trigger_shot_id, 32'h0000_0050,
                    "first snapshot shot id");
        expect_word(snap_trigger_count, 32'h0000_0060,
                    "first snapshot trigger count");
        expect_word(snap_missed_trigger_count, 32'h0000_0070,
                    "first snapshot missed count");
        if (snap_trigger_time !== 64'h1122_3344_5566_7788)
            fail("first snapshot trigger time mismatch");
        if (snap_first_sample_time !== 64'h99AA_BBCC_DDEE_FF00)
            fail("first snapshot sample time mismatch");

        @(negedge stream_clk);
        live_busy = 1'b0;
        live_armed = 1'b0;
        live_trigger_seen = 1'b0;
        live_first_sample_time_valid = 1'b0;
        live_completed_frames = 32'h0000_0100;
        live_current_frame_id = 32'h0000_0200;
        live_current_sample_index = 32'h0000_0300;
        live_stall_cycles = 32'h0000_0400;
        live_trigger_shot_id = 32'h0000_0500;
        live_trigger_count = 32'h0000_0600;
        live_missed_trigger_count = 32'h0000_0700;
        live_trigger_time = 64'h0123_4567_89AB_CDEF;
        live_first_sample_time = 64'hFEDC_BA98_7654_3210;
        wait_snapshot(1'b0, 32'h0000_0100, 32'h0000_0200,
                      32'h0000_0300, 32'h0000_0400,
                      "second status snapshot");
        expect_bit(snap_armed, 1'b0, "second snapshot armed");
        expect_bit(snap_trigger_seen, 1'b0, "second snapshot trigger seen");
        expect_bit(snap_first_sample_time_valid, 1'b0,
                   "second snapshot timestamp valid");
        expect_word(snap_trigger_shot_id, 32'h0000_0500,
                    "second snapshot shot id");
        if (snap_trigger_time !== 64'h0123_4567_89AB_CDEF)
            fail("second snapshot trigger time mismatch");

        if (error_count != 0) begin
            $fatal(1, "FAIL: qcrate_cdc_tb had %0d error(s)", error_count);
        end

        $display("PASS: qcrate_cdc_tb");
        $finish;
    end

endmodule

`default_nettype wire
