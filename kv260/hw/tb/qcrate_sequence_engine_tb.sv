`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_engine_tb;

    localparam int MAX_EVENTS = 128;
    localparam int ADDR_WIDTH = $clog2(MAX_EVENTS);

    localparam logic [7:0] FAULT_BAD_EVENT_COUNT = 8'd1;
    localparam logic [7:0] FAULT_TIME_ORDER      = 8'd2;
    localparam logic [7:0] FAULT_RESERVED_STATE = 8'd3;
    localparam logic [7:0] FAULT_NO_CHANGE      = 8'd5;
    localparam logic [7:0] FAULT_UNSAFE_FINAL   = 8'd6;

    logic                  clk;
    logic                  rst_n;
    logic                  arm;
    logic                  start;
    logic                  abort;
    logic                  soft_reset;
    logic                  external_trigger_enable;
    logic                  external_trigger;
    logic [ADDR_WIDTH:0]   event_count;

    logic                  event_req;
    logic [ADDR_WIDTH-1:0] event_addr;
    logic                  event_valid;
    logic [127:0]          event_data;

    logic                  idle;
    logic                  validating;
    logic                  armed;
    logic                  busy;
    logic                  faulted;
    logic                  done_pulse;
    logic                  aborted_pulse;
    logic                  fault_pulse;
    logic                  shot_start_pulse;
    logic [7:0]            fault_code;
    logic [ADDR_WIDTH-1:0] fault_event_index;
    logic [ADDR_WIDTH-1:0] active_event_index;
    logic [31:0]           completed_shots;
    logic [63:0]           timebase;
    logic [63:0]           start_time;
    logic [63:0]           elapsed_tick;
    logic [1:0]            pulse;

    logic [127:0] event_memory [0:MAX_EVENTS-1];
    int unsigned error_count;

    qcrate_sequence_engine #(
        .MAX_EVENTS       (MAX_EVENTS),
        .EVENT_ADDR_WIDTH (ADDR_WIDTH)
    ) u_dut (
        .clk_i                     (clk),
        .rst_n_i                   (rst_n),
        .arm_i                     (arm),
        .start_i                   (start),
        .abort_i                   (abort),
        .soft_reset_i              (soft_reset),
        .external_trigger_enable_i (external_trigger_enable),
        .external_trigger_i        (external_trigger),
        .event_count_i             (event_count),
        .timebase_i                 (timebase),
        .event_req_o               (event_req),
        .event_addr_o              (event_addr),
        .event_valid_i             (event_valid),
        .event_data_i              (event_data),
        .idle_o                    (idle),
        .validating_o              (validating),
        .armed_o                   (armed),
        .busy_o                    (busy),
        .faulted_o                 (faulted),
        .done_pulse_o              (done_pulse),
        .aborted_pulse_o           (aborted_pulse),
        .fault_pulse_o             (fault_pulse),
        .shot_start_pulse_o        (shot_start_pulse),
        .fault_code_o              (fault_code),
        .fault_event_index_o       (fault_event_index),
        .active_event_index_o      (active_event_index),
        .completed_shots_o         (completed_shots),
        .start_time_o              (start_time),
        .elapsed_tick_o            (elapsed_tick),
        .pulse_o                   (pulse)
    );

    qcrate_timebase u_timebase (
        .clk_i  (clk),
        .rst_n_i(rst_n),
        .time_o (timebase)
    );

    initial begin
        clk = 1'b0;
        forever #2.5 clk = ~clk;
    end

    /* The final RAM wrapper will provide this ordered one-cycle response. */
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            event_valid <= 1'b0;
            event_data <= '0;
        end else begin
            event_valid <= event_req;
            if (event_req)
                event_data <= event_memory[event_addr];
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
        if (actual !== expected)
            fail($sformatf("%s: expected %0b, got %0b", what, expected, actual));
    endtask

    task automatic expect_u32(
        input logic [31:0] actual,
        input logic [31:0] expected,
        input string what
    );
        if (actual !== expected)
            fail($sformatf("%s: expected 0x%08h, got 0x%08h",
                           what, expected, actual));
    endtask

    task automatic expect_u64(
        input logic [63:0] actual,
        input logic [63:0] expected,
        input string what
    );
        if (actual !== expected)
            fail($sformatf("%s: expected 0x%016h, got 0x%016h",
                           what, expected, actual));
    endtask

    task automatic expect_pulse(
        input logic [1:0] expected,
        input string what
    );
        if (pulse !== expected)
            fail($sformatf("%s: expected pulse %02b, got %02b",
                           what, expected, pulse));
    endtask

    task automatic load_event(
        input logic [ADDR_WIDTH-1:0] index,
        input logic [63:0] timestamp,
        input logic [31:0] state_word,
        input logic [31:0] flags
    );
        event_memory[index] = {flags, state_word, timestamp};
    endtask

    task automatic pulse_signal(ref logic signal);
        @(negedge clk);
        signal = 1'b1;
        @(negedge clk);
        signal = 1'b0;
    endtask

    task automatic reset_dut;
        int index;
        arm = 1'b0;
        start = 1'b0;
        abort = 1'b0;
        soft_reset = 1'b0;
        external_trigger_enable = 1'b0;
        external_trigger = 1'b0;
        event_count = '0;
        for (index = 0; index < MAX_EVENTS; index++)
            event_memory[index] = '0;
        rst_n = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        #1;
    endtask

    task automatic soft_reset_dut;
        pulse_signal(soft_reset);
        #1;
        expect_bit(idle, 1'b1, "soft reset idle");
        expect_pulse(2'b00, "soft reset safe output");
    endtask

    task automatic wait_for_armed(input string what);
        int guard;
        guard = 0;
        while (!armed && !faulted && (guard < 400)) begin
            @(posedge clk);
            #1;
            guard++;
        end
        if (!armed)
            fail({what, ": timed out or faulted before ARMED"});
    endtask

    task automatic wait_for_fault(
        input logic [7:0] expected_code,
        input int unsigned expected_index,
        input string what
    );
        int guard;
        guard = 0;
        while (!faulted && (guard < 400)) begin
            @(posedge clk);
            #1;
            guard++;
        end
        if (!faulted) begin
            fail({what, ": timed out waiting for fault"});
        end else begin
            expect_bit(fault_pulse, 1'b1, {what, " pulse"});
            expect_u32({24'd0, fault_code}, {24'd0, expected_code},
                       {what, " code"});
            expect_u32({{(32-ADDR_WIDTH){1'b0}}, fault_event_index},
                       expected_index, {what, " index"});
            expect_pulse(2'b00, {what, " safe output"});
        end
    endtask

    initial begin
        logic [63:0] time_before_reset;

        error_count = 0;
        reset_dut();

        /* Consecutive events prove one-event-per-cycle prefetch behavior. */
        load_event(0, 64'd0, 32'd1, 32'd0);
        load_event(1, 64'd1, 32'd3, 32'd0);
        load_event(2, 64'd2, 32'd2, 32'd0);
        load_event(3, 64'd3, 32'd0, 32'd0);
        event_count = 4;
        pulse_signal(arm);
        wait_for_armed("consecutive sequence");
        expect_bit(validating, 1'b0, "consecutive validation complete");
        pulse_signal(start);
        expect_bit(busy, 1'b1, "consecutive sequence running");
        expect_bit(shot_start_pulse, 1'b1,
                   "software start emits semantic shot start");
        if (start_time >= timebase)
            fail("start time was not captured before the running timebase advanced");
        @(posedge clk); #1;
        expect_pulse(2'b01, "tick 0");
        expect_u32({{(32-ADDR_WIDTH){1'b0}}, active_event_index},
                   32'd1, "tick 0 next event index");
        expect_u64(elapsed_tick, 64'd1, "tick 0 elapsed count");
        @(posedge clk); #1;
        expect_pulse(2'b11, "tick 1");
        expect_u32({{(32-ADDR_WIDTH){1'b0}}, active_event_index},
                   32'd2, "tick 1 next event index");
        expect_u64(elapsed_tick, 64'd2, "tick 1 elapsed count");
        @(posedge clk); #1;
        expect_pulse(2'b10, "tick 2");
        expect_u32({{(32-ADDR_WIDTH){1'b0}}, active_event_index},
                   32'd3, "tick 2 next event index");
        expect_u64(elapsed_tick, 64'd3, "tick 2 elapsed count");
        @(posedge clk); #1;
        expect_pulse(2'b00, "tick 3 final safe state");
        expect_bit(done_pulse, 1'b1, "consecutive done pulse");
        expect_bit(idle, 1'b1, "consecutive returns idle");
        expect_u32(completed_shots, 32'd1, "consecutive completed shots");
        @(posedge clk); #1;
        expect_bit(done_pulse, 1'b0, "done pulse clears");

        /* External trigger starts the same relative-time scheduler. */
        load_event(0, 64'd2, 32'd1, 32'd0);
        load_event(1, 64'd4, 32'd0, 32'd0);
        event_count = 2;
        external_trigger_enable = 1'b1;
        pulse_signal(arm);
        wait_for_armed("external-trigger sequence");
        pulse_signal(external_trigger);
        expect_bit(busy, 1'b1, "external trigger starts run");
        expect_bit(shot_start_pulse, 1'b1,
                   "external trigger emits semantic shot start");
        @(posedge clk); #1; expect_pulse(2'b00, "external tick 0");
        @(posedge clk); #1; expect_pulse(2'b00, "external tick 1");
        @(posedge clk); #1; expect_pulse(2'b01, "external tick 2");
        @(posedge clk); #1; expect_pulse(2'b01, "external tick 3");
        @(posedge clk); #1;
        expect_pulse(2'b00, "external tick 4");
        expect_bit(done_pulse, 1'b1, "external done");
        expect_u32(completed_shots, 32'd2, "external completed shots");
        external_trigger_enable = 1'b0;

        /* Abort is immediate in the scheduler domain and always drives low. */
        load_event(0, 64'd0, 32'd3, 32'd0);
        load_event(1, 64'd100, 32'd0, 32'd0);
        event_count = 2;
        pulse_signal(arm);
        wait_for_armed("abort sequence");
        pulse_signal(start);
        @(posedge clk); #1; expect_pulse(2'b11, "abort precondition high");
        pulse_signal(abort);
        #1;
        expect_pulse(2'b00, "abort forces safe state");
        expect_bit(aborted_pulse, 1'b1, "abort event pulse");
        expect_bit(idle, 1'b1, "abort returns idle");

        /* Arm-time validation reports the exact malformed event. */
        load_event(0, 64'd2, 32'd1, 32'd0);
        load_event(1, 64'd1, 32'd0, 32'd0);
        event_count = 2;
        pulse_signal(arm);
        wait_for_fault(FAULT_TIME_ORDER, 1, "unordered timestamp");
        soft_reset_dut();

        load_event(0, 64'd0, 32'd5, 32'd0);
        load_event(1, 64'd1, 32'd0, 32'd0);
        event_count = 2;
        pulse_signal(arm);
        wait_for_fault(FAULT_RESERVED_STATE, 0, "reserved output bits");
        soft_reset_dut();

        load_event(0, 64'd0, 32'd1, 32'd0);
        load_event(1, 64'd1, 32'd1, 32'd0);
        event_count = 2;
        pulse_signal(arm);
        wait_for_fault(FAULT_NO_CHANGE, 1, "duplicate output state");
        soft_reset_dut();

        load_event(0, 64'd0, 32'd1, 32'd0);
        load_event(1, 64'd1, 32'd2, 32'd0);
        event_count = 2;
        pulse_signal(arm);
        wait_for_fault(FAULT_UNSAFE_FINAL, 1, "unsafe final state");
        soft_reset_dut();

        event_count = 1;
        pulse_signal(arm);
        wait_for_fault(FAULT_BAD_EVENT_COUNT, 0, "one-event sequence");

        time_before_reset = timebase;
        soft_reset_dut();
        if (timebase <= time_before_reset)
            fail("free-running timebase stopped or reset during soft reset");
        expect_u32(completed_shots, 32'd0, "soft reset clears shot count");

        if (error_count == 0) begin
            $display("PASS: qcrate_sequence_engine_tb");
            $finish;
        end else begin
            $fatal(1, "FAIL: qcrate_sequence_engine_tb had %0d errors",
                   error_count);
        end
    end

endmodule

`default_nettype wire
