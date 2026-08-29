`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_subsystem_tb;

    localparam int MAX_EVENTS = 128;
    localparam int EVENT_ADDR_WIDTH = $clog2(MAX_EVENTS);
    localparam int EVENT_COUNT_WIDTH = EVENT_ADDR_WIDTH + 1;
    localparam int RAM_CTRL_ADDR_WIDTH = EVENT_ADDR_WIDTH + 2;

    localparam logic [11:0] ADDR_CONTROL         = 12'h000;
    localparam logic [11:0] ADDR_STATUS          = 12'h004;
    localparam logic [11:0] ADDR_EVENT_COUNT     = 12'h008;
    localparam logic [11:0] ADDR_COMPLETED_SHOTS = 12'h010;
    localparam logic [11:0] EVENT_WINDOW_BASE    = 12'h800;

    localparam int WAIT_ARMED         = 0;
    localparam int WAIT_BUSY          = 1;
    localparam int WAIT_DONE_PULSE    = 2;
    localparam int WAIT_DONE_EVENT    = 3;
    localparam int WAIT_PULSE_HIGH    = 4;
    localparam int WAIT_ABORT_PULSE   = 5;
    localparam int WAIT_ABORT_EVENT   = 6;

    logic ctrl_clk;
    logic stream_clk;
    logic ctrl_rst_n;
    logic stream_rst_n;

    logic [11:0] paddr;
    logic psel;
    logic penable;
    logic pwrite;
    logic [31:0] pwdata;
    logic [31:0] prdata;
    logic pready;
    logic pslverr;

    logic [EVENT_COUNT_WIDTH-1:0] event_count_ctrl;
    logic external_trigger_enable_ctrl;
    logic arm_cmd_ctrl;
    logic start_cmd_ctrl;
    logic abort_cmd_ctrl;
    logic soft_reset_cmd_ctrl;
    logic command_busy_ctrl;

    logic ram_ctrl_en;
    logic ram_ctrl_write;
    logic [RAM_CTRL_ADDR_WIDTH-1:0] ram_ctrl_addr;
    logic [31:0] ram_ctrl_wdata;
    logic ram_ctrl_rvalid;
    logic [31:0] ram_ctrl_rdata;

    logic [EVENT_COUNT_WIDTH-1:0] event_count_stream;
    logic external_trigger_enable_stream;
    logic arm_pulse_stream;
    logic start_pulse_stream;
    logic abort_pulse_stream;
    logic soft_reset_pulse_stream;

    logic ram_stream_req;
    logic [EVENT_ADDR_WIDTH-1:0] ram_stream_addr;
    logic ram_stream_valid;
    logic [127:0] ram_stream_data;

    logic idle_stream;
    logic validating_stream;
    logic armed_stream;
    logic busy_stream;
    logic faulted_stream;
    logic done_pulse_stream;
    logic aborted_pulse_stream;
    logic fault_pulse_stream;
    logic shot_start_pulse_stream;
    logic [7:0] fault_code_stream;
    logic [EVENT_ADDR_WIDTH-1:0] fault_index_stream;
    logic [EVENT_ADDR_WIDTH-1:0] active_index_stream;
    logic [31:0] completed_shots_stream;
    logic [63:0] timebase_stream;
    logic [63:0] start_time_stream;
    logic [63:0] elapsed_tick_stream;
    logic [1:0] pulse_stream;

    logic idle_ctrl;
    logic validating_ctrl;
    logic armed_ctrl;
    logic busy_ctrl;
    logic faulted_ctrl;
    logic [7:0] fault_code_ctrl;
    logic [EVENT_ADDR_WIDTH-1:0] fault_index_ctrl;
    logic [EVENT_ADDR_WIDTH-1:0] active_index_ctrl;
    logic [31:0] completed_shots_ctrl;
    logic [63:0] timebase_ctrl;
    logic [63:0] start_time_ctrl;
    logic [63:0] elapsed_tick_ctrl;

    logic done_event_ctrl;
    logic aborted_event_ctrl;
    logic fault_event_ctrl;
    logic done_seen;
    logic aborted_seen;
    int unsigned error_count;

    qcrate_sequence_regs #(
        .EVENT_ADDR_WIDTH    (EVENT_ADDR_WIDTH),
        .EVENT_COUNT_WIDTH   (EVENT_COUNT_WIDTH),
        .RAM_CTRL_ADDR_WIDTH (RAM_CTRL_ADDR_WIDTH)
    ) u_regs (
        .pclk_i                     (ctrl_clk),
        .presetn_i                  (ctrl_rst_n),
        .paddr_i                    (paddr),
        .psel_i                     (psel),
        .penable_i                  (penable),
        .pwrite_i                   (pwrite),
        .pwdata_i                   (pwdata),
        .prdata_o                   (prdata),
        .pready_o                   (pready),
        .pslverr_o                  (pslverr),
        .event_count_o              (event_count_ctrl),
        .external_trigger_enable_o  (external_trigger_enable_ctrl),
        .arm_cmd_o                  (arm_cmd_ctrl),
        .start_cmd_o                (start_cmd_ctrl),
        .abort_cmd_o                (abort_cmd_ctrl),
        .soft_reset_cmd_o           (soft_reset_cmd_ctrl),
        .command_busy_i             (command_busy_ctrl),
        .done_event_i               (done_event_ctrl),
        .aborted_event_i            (aborted_event_ctrl),
        .idle_i                     (idle_ctrl),
        .validating_i               (validating_ctrl),
        .armed_i                    (armed_ctrl),
        .busy_i                     (busy_ctrl),
        .faulted_i                  (faulted_ctrl),
        .fault_code_i               (fault_code_ctrl),
        .fault_event_index_i        (fault_index_ctrl),
        .active_event_index_i       (active_index_ctrl),
        .completed_shots_i          (completed_shots_ctrl),
        .timebase_i                 (timebase_ctrl),
        .start_time_i               (start_time_ctrl),
        .elapsed_tick_i             (elapsed_tick_ctrl),
        .ram_en_o                   (ram_ctrl_en),
        .ram_write_o                (ram_ctrl_write),
        .ram_addr_o                 (ram_ctrl_addr),
        .ram_wdata_o                (ram_ctrl_wdata),
        .ram_rvalid_i               (ram_ctrl_rvalid),
        .ram_rdata_i                (ram_ctrl_rdata)
    );

    qcrate_sequence_ram #(
        .MAX_EVENTS       (MAX_EVENTS),
        .EVENT_ADDR_WIDTH (EVENT_ADDR_WIDTH),
        .CTRL_ADDR_WIDTH  (RAM_CTRL_ADDR_WIDTH)
    ) u_ram (
        .ctrl_clk_i      (ctrl_clk),
        .ctrl_rst_n_i    (ctrl_rst_n),
        .ctrl_en_i       (ram_ctrl_en),
        .ctrl_write_i    (ram_ctrl_write),
        .ctrl_addr_i     (ram_ctrl_addr),
        .ctrl_wdata_i    (ram_ctrl_wdata),
        .ctrl_rvalid_o   (ram_ctrl_rvalid),
        .ctrl_rdata_o    (ram_ctrl_rdata),
        .stream_clk_i    (stream_clk),
        .stream_rst_n_i  (stream_rst_n),
        .stream_req_i    (ram_stream_req),
        .stream_addr_i   (ram_stream_addr),
        .stream_valid_o  (ram_stream_valid),
        .stream_data_o   (ram_stream_data)
    );

    qcrate_sequence_command_cdc #(
        .EVENT_COUNT_WIDTH(EVENT_COUNT_WIDTH)
    ) u_command_cdc (
        .ctrl_clk_i                      (ctrl_clk),
        .ctrl_rst_n_i                    (ctrl_rst_n),
        .event_count_i                   (event_count_ctrl),
        .external_trigger_enable_i       (external_trigger_enable_ctrl),
        .arm_cmd_i                       (arm_cmd_ctrl),
        .start_cmd_i                     (start_cmd_ctrl),
        .abort_cmd_i                     (abort_cmd_ctrl),
        .soft_reset_cmd_i                (soft_reset_cmd_ctrl),
        .command_busy_o                  (command_busy_ctrl),
        .stream_clk_i                    (stream_clk),
        .stream_rst_n_i                  (stream_rst_n),
        .active_event_count_o            (event_count_stream),
        .active_external_trigger_enable_o(external_trigger_enable_stream),
        .arm_pulse_o                     (arm_pulse_stream),
        .start_pulse_o                   (start_pulse_stream),
        .abort_pulse_o                   (abort_pulse_stream),
        .soft_reset_pulse_o              (soft_reset_pulse_stream)
    );

    qcrate_timebase u_timebase (
        .clk_i  (stream_clk),
        .rst_n_i(stream_rst_n),
        .time_o (timebase_stream)
    );

    qcrate_sequence_engine #(
        .MAX_EVENTS       (MAX_EVENTS),
        .EVENT_ADDR_WIDTH (EVENT_ADDR_WIDTH)
    ) u_engine (
        .clk_i                     (stream_clk),
        .rst_n_i                   (stream_rst_n),
        .arm_i                     (arm_pulse_stream),
        .start_i                   (start_pulse_stream),
        .abort_i                   (abort_pulse_stream),
        .soft_reset_i              (soft_reset_pulse_stream),
        .external_trigger_enable_i (external_trigger_enable_stream),
        .external_trigger_i        (1'b0),
        .event_count_i             (event_count_stream),
        .timebase_i                (timebase_stream),
        .event_req_o               (ram_stream_req),
        .event_addr_o              (ram_stream_addr),
        .event_valid_i             (ram_stream_valid),
        .event_data_i              (ram_stream_data),
        .idle_o                    (idle_stream),
        .validating_o              (validating_stream),
        .armed_o                   (armed_stream),
        .busy_o                    (busy_stream),
        .faulted_o                 (faulted_stream),
        .done_pulse_o              (done_pulse_stream),
        .aborted_pulse_o           (aborted_pulse_stream),
        .fault_pulse_o             (fault_pulse_stream),
        .shot_start_pulse_o        (shot_start_pulse_stream),
        .fault_code_o              (fault_code_stream),
        .fault_event_index_o       (fault_index_stream),
        .active_event_index_o      (active_index_stream),
        .completed_shots_o         (completed_shots_stream),
        .start_time_o              (start_time_stream),
        .elapsed_tick_o            (elapsed_tick_stream),
        .pulse_o                   (pulse_stream)
    );

    qcrate_sequence_status_cdc #(
        .EVENT_ADDR_WIDTH(EVENT_ADDR_WIDTH)
    ) u_status_cdc (
        .stream_clk_i          (stream_clk),
        .stream_rst_n_i        (stream_rst_n),
        .idle_i                (idle_stream),
        .validating_i          (validating_stream),
        .armed_i               (armed_stream),
        .busy_i                (busy_stream),
        .faulted_i             (faulted_stream),
        .fault_code_i          (fault_code_stream),
        .fault_event_index_i   (fault_index_stream),
        .active_event_index_i  (active_index_stream),
        .completed_shots_i     (completed_shots_stream),
        .timebase_i            (timebase_stream),
        .start_time_i          (start_time_stream),
        .elapsed_tick_i        (elapsed_tick_stream),
        .ctrl_clk_i            (ctrl_clk),
        .ctrl_rst_n_i          (ctrl_rst_n),
        .idle_o                (idle_ctrl),
        .validating_o          (validating_ctrl),
        .armed_o               (armed_ctrl),
        .busy_o                (busy_ctrl),
        .faulted_o             (faulted_ctrl),
        .fault_code_o          (fault_code_ctrl),
        .fault_event_index_o   (fault_index_ctrl),
        .active_event_index_o  (active_index_ctrl),
        .completed_shots_o     (completed_shots_ctrl),
        .timebase_o            (timebase_ctrl),
        .start_time_o          (start_time_ctrl),
        .elapsed_tick_o        (elapsed_tick_ctrl)
    );

    qcrate_sequence_event_cdc u_event_cdc (
        .src_clk_i       (stream_clk),
        .src_rst_n_i     (stream_rst_n),
        .done_pulse_i    (done_pulse_stream),
        .aborted_pulse_i (aborted_pulse_stream),
        .fault_pulse_i   (fault_pulse_stream),
        .dst_clk_i       (ctrl_clk),
        .dst_rst_n_i     (ctrl_rst_n),
        .done_pulse_o    (done_event_ctrl),
        .aborted_pulse_o (aborted_event_ctrl),
        .fault_pulse_o   (fault_event_ctrl)
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
            done_seen <= 1'b0;
            aborted_seen <= 1'b0;
        end else begin
            if (done_event_ctrl)
                done_seen <= 1'b1;
            if (aborted_event_ctrl)
                aborted_seen <= 1'b1;
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

    task automatic expect_word(
        input logic [31:0] actual,
        input logic [31:0] expected,
        input string what
    );
        if (actual !== expected)
            fail($sformatf("%s: expected 0x%08h, got 0x%08h",
                           what, expected, actual));
    endtask

    task automatic drive_idle;
        paddr = '0;
        psel = 1'b0;
        penable = 1'b0;
        pwrite = 1'b0;
        pwdata = '0;
    endtask

    task automatic apb_access(
        input logic [11:0] address,
        input logic write,
        input logic [31:0] write_data,
        output logic [31:0] read_data,
        output logic error
    );
        int guard;
        @(negedge ctrl_clk);
        paddr = address;
        pwrite = write;
        pwdata = write_data;
        psel = 1'b1;
        penable = 1'b0;
        @(negedge ctrl_clk);
        penable = 1'b1;

        guard = 0;
        do begin
            @(posedge ctrl_clk);
            #1;
            guard++;
            if (guard > 20) begin
                fail("APB access timed out");
                break;
            end
        end while (!pready);

        read_data = prdata;
        error = pslverr;
        @(negedge ctrl_clk);
        drive_idle();
    endtask

    task automatic apb_write(
        input logic [11:0] address,
        input logic [31:0] value,
        input logic expected_error,
        input string what
    );
        logic [31:0] unused_data;
        logic error;
        apb_access(address, 1'b1, value, unused_data, error);
        expect_bit(error, expected_error, what);
    endtask

    task automatic apb_write_stretched(
        input logic [11:0] address,
        input logic [31:0] value,
        input int access_cycles,
        input string what
    );
        @(negedge ctrl_clk);
        paddr = address;
        pwrite = 1'b1;
        pwdata = value;
        psel = 1'b1;
        penable = 1'b0;
        @(negedge ctrl_clk);
        penable = 1'b1;
        @(posedge ctrl_clk);
        #1;
        expect_bit(pslverr, 1'b0, what);
        repeat (access_cycles - 1) @(posedge ctrl_clk);
        @(negedge ctrl_clk);
        drive_idle();
    endtask

    task automatic apb_read_expect(
        input logic [11:0] address,
        input logic [31:0] expected,
        input string what
    );
        logic [31:0] value;
        logic error;
        apb_access(address, 1'b0, '0, value, error);
        expect_bit(error, 1'b0, {what, " PSLVERR"});
        expect_word(value, expected, what);
    endtask

    task automatic wait_state(
        input int selector,
        input string what
    );
        int guard;
        logic reached;
        guard = 0;
        reached = 1'b0;
        while (!reached && (guard < 500)) begin
            unique case (selector)
                WAIT_ARMED:       reached = armed_stream;
                WAIT_BUSY:        reached = busy_stream;
                WAIT_DONE_PULSE:  reached = done_pulse_stream;
                WAIT_DONE_EVENT:  reached = done_seen;
                WAIT_PULSE_HIGH:  reached = pulse_stream[0];
                WAIT_ABORT_PULSE: reached = aborted_pulse_stream;
                WAIT_ABORT_EVENT: reached = aborted_seen;
                default:          reached = 1'b0;
            endcase
            if (reached)
                break;
            @(posedge stream_clk);
            #1;
            guard++;
        end
        if (!reached)
            fail({what, ": timeout"});
    endtask

    initial begin
        logic [31:0] data;
        logic error;
        logic [63:0] time_before_soft_reset;

        error_count = 0;
        drive_idle();
        ctrl_rst_n = 1'b0;
        stream_rst_n = 1'b0;
        repeat (5) @(posedge ctrl_clk);
        ctrl_rst_n = 1'b1;
        stream_rst_n = 1'b1;
        repeat (5) @(posedge ctrl_clk);

        /* Event 0: tick 0 -> state 1. Event 1: tick 3 -> safe state 0. */
        apb_write(EVENT_WINDOW_BASE + 12'h000, 32'd0, 1'b0,
                  "event 0 timestamp low write");
        apb_write(EVENT_WINDOW_BASE + 12'h004, 32'd0, 1'b0,
                  "event 0 timestamp high write");
        apb_write(EVENT_WINDOW_BASE + 12'h008, 32'd1, 1'b0,
                  "event 0 state write");
        apb_write(EVENT_WINDOW_BASE + 12'h00C, 32'd0, 1'b0,
                  "event 0 flags write");
        apb_write(EVENT_WINDOW_BASE + 12'h010, 32'd3, 1'b0,
                  "event 1 timestamp low write");
        apb_write(EVENT_WINDOW_BASE + 12'h014, 32'd0, 1'b0,
                  "event 1 timestamp high write");
        apb_write(EVENT_WINDOW_BASE + 12'h018, 32'd0, 1'b0,
                  "event 1 state write");
        apb_write(EVENT_WINDOW_BASE + 12'h01C, 32'd0, 1'b0,
                  "event 1 flags write");
        apb_read_expect(EVENT_WINDOW_BASE + 12'h008, 32'd1,
                        "event RAM readback");
        apb_write(ADDR_EVENT_COUNT, 32'd2, 1'b0, "event count write");
        apb_read_expect(ADDR_EVENT_COUNT, 32'd2, "event count readback");

        apb_write(ADDR_CONTROL, 32'h0000_0003, 1'b1,
                  "multiple commands rejected");
        /* A stretched access must still create exactly one command mailbox request. */
        apb_write_stretched(ADDR_CONTROL, 32'h0000_0001, 12,
                            "stretched arm command");
        wait_state(WAIT_ARMED, "arm and validation");
        expect_bit(faulted_stream, 1'b0, "valid table does not fault");

        apb_access(EVENT_WINDOW_BASE, 1'b0, '0, data, error);
        expect_bit(error, 1'b1, "event RAM read rejected while armed");
        expect_word(data, 32'hDEAD_BEEF,
                    "rejected event RAM read diagnostic data");

        apb_write(ADDR_CONTROL, 32'h0000_0002, 1'b0, "start command");
        wait_state(WAIT_BUSY, "software start");
        wait_state(WAIT_DONE_PULSE, "sequence completion");
        expect_bit(pulse_stream == 2'b00, 1'b1, "completion safe output");
        wait_state(WAIT_DONE_EVENT, "done event CDC");
        repeat (12) @(posedge ctrl_clk);
        apb_read_expect(ADDR_COMPLETED_SHOTS, 32'd1,
                        "completed shot count");

        /* A longer shot verifies abort command crossing and immediate safe state. */
        apb_write(EVENT_WINDOW_BASE + 12'h010, 32'd100, 1'b0,
                  "long event timestamp write");
        apb_write(ADDR_CONTROL, 32'h0000_0001, 1'b0, "second arm command");
        wait_state(WAIT_ARMED, "second arm");
        apb_write(ADDR_CONTROL, 32'h0000_0002, 1'b0, "second start command");
        wait_state(WAIT_BUSY, "second start");
        wait_state(WAIT_PULSE_HIGH, "second shot output high");
        apb_write(ADDR_CONTROL, 32'h0000_0004, 1'b0, "abort command");
        wait_state(WAIT_ABORT_PULSE, "abort reaches engine");
        expect_word({30'd0, pulse_stream}, 32'd0, "abort safe output");
        wait_state(WAIT_ABORT_EVENT, "abort event CDC");

        time_before_soft_reset = timebase_stream;
        apb_write(ADDR_CONTROL, 32'h0000_0008, 1'b0, "soft reset command");
        repeat (12) @(posedge stream_clk);
        if (timebase_stream <= time_before_soft_reset)
            fail("shared timebase stopped or reset on sequencer soft reset");

        apb_read_expect(ADDR_STATUS, 32'h0000_0001, "final idle status");
        expect_bit(fault_event_ctrl, 1'b0, "no fault event");

        if (error_count == 0) begin
            $display("PASS: qcrate_sequence_subsystem_tb");
            $finish;
        end else begin
            $fatal(1, "FAIL: qcrate_sequence_subsystem_tb had %0d errors",
                   error_count);
        end
    end

endmodule

`default_nettype wire
