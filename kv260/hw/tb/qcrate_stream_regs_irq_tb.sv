`timescale 1ns / 1ps
`default_nettype none

module qcrate_stream_regs_irq_tb;

    localparam logic [31:0] UNMAPPED_PRDATA = 32'hDEAD_BEEF;

    localparam logic [11:0] ADDR_CONTROL              = 12'h000;
    localparam logic [11:0] ADDR_FRAME_LENGTH         = 12'h004;
    localparam logic [11:0] ADDR_FRAME_COUNT          = 12'h008;
    localparam logic [11:0] ADDR_STREAM_MODE          = 12'h00C;
    localparam logic [11:0] ADDR_STATUS               = 12'h010;
    localparam logic [11:0] ADDR_COMPLETED_FRAMES     = 12'h014;
    localparam logic [11:0] ADDR_CURRENT_FRAME_ID     = 12'h018;
    localparam logic [11:0] ADDR_CURRENT_SAMPLE_INDEX = 12'h01C;
    localparam logic [11:0] ADDR_STALL_CYCLES         = 12'h020;
    localparam logic [11:0] ADDR_IRQ_STATUS           = 12'h024;
    localparam logic [11:0] ADDR_IRQ_ENABLE           = 12'h028;
    localparam logic [11:0] ADDR_IRQ_CLEAR            = 12'h02C;
    localparam logic [11:0] ADDR_TRIGGER_SHOT_ID      = 12'h030;
    localparam logic [11:0] ADDR_TRIGGER_COUNT        = 12'h034;
    localparam logic [11:0] ADDR_MISSED_TRIGGER_COUNT = 12'h038;
    localparam logic [11:0] ADDR_TRIGGER_TIME_LOW     = 12'h03C;
    localparam logic [11:0] ADDR_TRIGGER_TIME_HIGH    = 12'h040;
    localparam logic [11:0] ADDR_FIRST_SAMPLE_TIME_LOW = 12'h044;
    localparam logic [11:0] ADDR_FIRST_SAMPLE_TIME_HIGH = 12'h048;
    localparam logic [11:0] ADDR_UNMAPPED             = 12'h04C;

    logic        pclk;
    logic        presetn;

    logic [11:0] paddr;
    logic        psel;
    logic        penable;
    logic        pwrite;
    logic [31:0] pwdata;
    logic [31:0] prdata;
    logic        pready;
    logic        pslverr;

    logic [31:0] frame_length;
    logic [31:0] frame_count;
    logic [31:0] stream_mode;
    logic        continuous;
    logic        start_cmd;
    logic        arm_triggered_cmd;
    logic        abort_cmd;
    logic        soft_reset_cmd;

    logic        command_busy;
    logic        stream_busy;
    logic        stream_armed;
    logic        trigger_seen;
    logic        first_sample_time_valid;
    logic [31:0] completed_frames;
    logic [31:0] current_frame_id;
    logic [31:0] current_sample_index;
    logic [31:0] stall_cycles;
    logic [31:0] trigger_shot_id;
    logic [31:0] trigger_count;
    logic [31:0] missed_trigger_count;
    logic [63:0] trigger_time;
    logic [63:0] first_sample_time;

    logic [31:0] irq_events;
    logic [31:0] irq_status;
    logic [31:0] irq_enable;
    logic [31:0] irq_clear;
    logic        irq;

    int unsigned error_count;

    qcrate_stream_regs u_regs (
        .pclk_i                     (pclk),
        .presetn_i                  (presetn),

        .paddr_i                    (paddr),
        .psel_i                     (psel),
        .penable_i                  (penable),
        .pwrite_i                   (pwrite),
        .pwdata_i                   (pwdata),

        .prdata_o                   (prdata),
        .pready_o                   (pready),
        .pslverr_o                  (pslverr),

        .frame_length_o             (frame_length),
        .frame_count_o              (frame_count),
        .stream_mode_o              (stream_mode),
        .continuous_o               (continuous),

        .start_cmd_o                (start_cmd),
        .arm_triggered_cmd_o        (arm_triggered_cmd),
        .abort_cmd_o                (abort_cmd),
        .soft_reset_cmd_o           (soft_reset_cmd),

        .command_busy_i             (command_busy),
        .stream_busy_i              (stream_busy),
        .stream_armed_i             (stream_armed),
        .trigger_seen_i             (trigger_seen),
        .first_sample_time_valid_i  (first_sample_time_valid),
        .completed_frames_i         (completed_frames),
        .current_frame_id_i         (current_frame_id),
        .current_sample_index_i     (current_sample_index),
        .stall_cycles_i             (stall_cycles),
        .trigger_shot_id_i          (trigger_shot_id),
        .trigger_count_i            (trigger_count),
        .missed_trigger_count_i     (missed_trigger_count),
        .trigger_time_i             (trigger_time),
        .first_sample_time_i        (first_sample_time),

        .irq_status_i               (irq_status),
        .irq_enable_o               (irq_enable),
        .irq_clear_o                (irq_clear)
    );

    qcrate_irq_ctrl u_irq (
        .clk_i                      (pclk),
        .rst_n_i                    (presetn),

        .events_i                   (irq_events),
        .enable_i                   (irq_enable),
        .clear_i                    (irq_clear),

        .status_o                   (irq_status),
        .irq_o                      (irq)
    );

    initial begin
        pclk = 1'b0;
        forever #5 pclk = ~pclk;
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

    task automatic drive_idle;
        paddr = '0;
        psel = 1'b0;
        penable = 1'b0;
        pwrite = 1'b0;
        pwdata = '0;
    endtask

    task automatic reset_dut;
        drive_idle();
        command_busy = 1'b0;
        stream_busy = 1'b0;
        stream_armed = 1'b0;
        trigger_seen = 1'b0;
        first_sample_time_valid = 1'b0;
        completed_frames = 32'h0000_0000;
        current_frame_id = 32'h0000_0000;
        current_sample_index = 32'h0000_0000;
        stall_cycles = 32'h0000_0000;
        trigger_shot_id = 32'h0000_0000;
        trigger_count = 32'h0000_0000;
        missed_trigger_count = 32'h0000_0000;
        trigger_time = 64'h0000_0000_0000_0000;
        first_sample_time = 64'h0000_0000_0000_0000;
        irq_events = 32'h0000_0000;
        presetn = 1'b0;
        repeat (3) @(posedge pclk);
        presetn = 1'b1;
        @(posedge pclk);
    endtask

    task automatic apb_read(
        input  logic [11:0] addr,
        output logic [31:0] data,
        output logic        err
    );
        @(negedge pclk);
        paddr = addr;
        pwrite = 1'b0;
        pwdata = '0;
        psel = 1'b1;
        penable = 1'b0;

        @(negedge pclk);
        penable = 1'b1;

        @(posedge pclk);
        #1;
        data = prdata;
        err = pslverr;
        expect_bit(pready, 1'b1, "APB read PREADY");

        @(negedge pclk);
        drive_idle();
    endtask

    task automatic apb_write(
        input  logic [11:0] addr,
        input  logic [31:0] data,
        output logic        err
    );
        @(negedge pclk);
        paddr = addr;
        pwrite = 1'b1;
        pwdata = data;
        psel = 1'b1;
        penable = 1'b0;

        @(negedge pclk);
        penable = 1'b1;

        @(posedge pclk);
        #1;
        err = pslverr;
        expect_bit(pready, 1'b1, "APB write PREADY");

        @(negedge pclk);
        drive_idle();
    endtask

    task automatic expect_read_ok(
        input logic [11:0] addr,
        input logic [31:0] expected,
        input string       what
    );
        logic [31:0] data;
        logic        err;

        apb_read(addr, data, err);
        expect_word(data, expected, what);
        expect_bit(err, 1'b0, {what, " PSLVERR"});
    endtask

    task automatic inject_events(input logic [31:0] events);
        @(negedge pclk);
        irq_events = events;
        @(posedge pclk);
        @(negedge pclk);
        irq_events = 32'h0000_0000;
    endtask

    initial begin
        logic [31:0] data;
        logic        err;

        error_count = 0;
        reset_dut();

        expect_read_ok(ADDR_CONTROL, 32'h0000_0000, "CONTROL reset");
        expect_read_ok(ADDR_FRAME_LENGTH, 32'h0000_0000, "FRAME_LENGTH reset");
        expect_read_ok(ADDR_FRAME_COUNT, 32'h0000_0000, "FRAME_COUNT reset");
        expect_read_ok(ADDR_STREAM_MODE, 32'h0000_0000, "STREAM_MODE reset");
        expect_read_ok(ADDR_IRQ_ENABLE, 32'h0000_0000, "IRQ_ENABLE reset");
        expect_bit(irq, 1'b0, "IRQ reset");

        apb_write(ADDR_FRAME_LENGTH, 32'd4096, err);
        expect_bit(err, 1'b0, "FRAME_LENGTH write PSLVERR");
        apb_write(ADDR_FRAME_COUNT, 32'd7, err);
        expect_bit(err, 1'b0, "FRAME_COUNT write PSLVERR");
        apb_write(ADDR_STREAM_MODE, 32'h0000_0002, err);
        expect_bit(err, 1'b0, "STREAM_MODE write PSLVERR");
        expect_word(frame_length, 32'd4096, "FRAME_LENGTH output");
        expect_word(frame_count, 32'd7, "FRAME_COUNT output");
        expect_word(stream_mode, 32'h0000_0002, "STREAM_MODE output");

        expect_read_ok(ADDR_FRAME_LENGTH, 32'd4096, "FRAME_LENGTH readback");
        expect_read_ok(ADDR_FRAME_COUNT, 32'd7, "FRAME_COUNT readback");
        expect_read_ok(ADDR_STREAM_MODE, 32'h0000_0002, "STREAM_MODE readback");

        apb_write(ADDR_CONTROL, 32'h0000_010F, err);
        expect_bit(err, 1'b0, "CONTROL command write PSLVERR");
        expect_bit(start_cmd, 1'b1, "START pulse asserted");
        expect_bit(abort_cmd, 1'b1, "ABORT pulse asserted");
        expect_bit(soft_reset_cmd, 1'b1, "SOFT_RESET pulse asserted");
        expect_bit(arm_triggered_cmd, 1'b1,
                   "ARM_TRIGGERED pulse asserted");
        expect_bit(continuous, 1'b1, "CONTINUOUS stored");

        @(posedge pclk);
        #1;
        expect_bit(start_cmd, 1'b0, "START pulse clears");
        expect_bit(abort_cmd, 1'b0, "ABORT pulse clears");
        expect_bit(soft_reset_cmd, 1'b0, "SOFT_RESET pulse clears");
        expect_bit(arm_triggered_cmd, 1'b0,
                   "ARM_TRIGGERED pulse clears");
        expect_read_ok(ADDR_CONTROL, 32'h0000_0100, "CONTROL readback");

        apb_write(ADDR_CONTROL, 32'h0000_0000, err);
        expect_bit(err, 1'b0, "CONTROL clear continuous PSLVERR");
        expect_bit(continuous, 1'b0, "CONTINUOUS clears");
        expect_read_ok(ADDR_CONTROL, 32'h0000_0000, "CONTROL cleared readback");

        command_busy = 1'b1;
        stream_busy = 1'b1;
        stream_armed = 1'b1;
        trigger_seen = 1'b1;
        first_sample_time_valid = 1'b1;
        stall_cycles = 32'h0000_0003;
        inject_events(32'h0000_0003);
        expect_read_ok(ADDR_STATUS, 32'h0000_017F,
                       "STATUS command/trigger/timestamp state");
        expect_read_ok(ADDR_IRQ_STATUS, 32'h0000_0003, "IRQ_STATUS sticky events");

        completed_frames = 32'h0000_0011;
        current_frame_id = 32'h0000_0022;
        current_sample_index = 32'h0000_0033;
        stall_cycles = 32'h0000_0044;
        expect_read_ok(ADDR_COMPLETED_FRAMES, 32'h0000_0011, "COMPLETED_FRAMES");
        expect_read_ok(ADDR_CURRENT_FRAME_ID, 32'h0000_0022, "CURRENT_FRAME_ID");
        expect_read_ok(ADDR_CURRENT_SAMPLE_INDEX, 32'h0000_0033,
                       "CURRENT_SAMPLE_INDEX");
        expect_read_ok(ADDR_STALL_CYCLES, 32'h0000_0044, "STALL_CYCLES");

        trigger_shot_id = 32'h1234_5678;
        trigger_count = 32'd9;
        missed_trigger_count = 32'd2;
        trigger_time = 64'h1122_3344_AABB_CCDD;
        first_sample_time = 64'h5566_7788_EEFF_0011;
        expect_read_ok(ADDR_TRIGGER_SHOT_ID, 32'h1234_5678,
                       "TRIGGER_SHOT_ID");
        expect_read_ok(ADDR_TRIGGER_COUNT, 32'd9, "TRIGGER_COUNT");
        expect_read_ok(ADDR_MISSED_TRIGGER_COUNT, 32'd2,
                       "MISSED_TRIGGER_COUNT");
        expect_read_ok(ADDR_TRIGGER_TIME_LOW, 32'hAABB_CCDD,
                       "TRIGGER_TIME low");
        trigger_time[63:32] = 32'hDEAD_BEEF;
        expect_read_ok(ADDR_TRIGGER_TIME_HIGH, 32'h1122_3344,
                       "TRIGGER_TIME coherent high latch");
        expect_read_ok(ADDR_FIRST_SAMPLE_TIME_LOW, 32'hEEFF_0011,
                       "FIRST_SAMPLE_TIME low");
        first_sample_time[63:32] = 32'hCAFE_BABE;
        expect_read_ok(ADDR_FIRST_SAMPLE_TIME_HIGH, 32'h5566_7788,
                       "FIRST_SAMPLE_TIME coherent high latch");

        apb_write(ADDR_IRQ_ENABLE, 32'h0000_0001, err);
        expect_bit(err, 1'b0, "IRQ_ENABLE write PSLVERR");
        expect_read_ok(ADDR_IRQ_ENABLE, 32'h0000_0001, "IRQ_ENABLE readback");
        expect_bit(irq, 1'b1, "IRQ asserted when enabled status is set");

        apb_write(ADDR_IRQ_CLEAR, 32'h0000_0001, err);
        expect_bit(err, 1'b0, "IRQ_CLEAR write PSLVERR");
        expect_word(irq_clear, 32'h0000_0001, "IRQ_CLEAR pulse");
        @(posedge pclk);
        #1;
        expect_word(irq_clear, 32'h0000_0000, "IRQ_CLEAR pulse clears");
        expect_read_ok(ADDR_IRQ_STATUS, 32'h0000_0002, "IRQ_STATUS after clear bit0");
        expect_bit(irq, 1'b0, "IRQ deasserts after enabled bit clears");

        irq_events = 32'h0000_0002;
        apb_write(ADDR_IRQ_CLEAR, 32'h0000_0002, err);
        expect_bit(err, 1'b0, "same-cycle IRQ clear write PSLVERR");
        @(posedge pclk);
        #1;
        irq_events = 32'h0000_0000;
        expect_read_ok(ADDR_IRQ_STATUS, 32'h0000_0002,
                       "IRQ event wins over same-cycle clear");

        apb_write(ADDR_STATUS, 32'hFFFF_FFFF, err);
        expect_bit(err, 1'b1, "RO STATUS write PSLVERR");

        apb_read(ADDR_UNMAPPED, data, err);
        expect_word(data, UNMAPPED_PRDATA, "unmapped stream PRDATA");
        expect_bit(err, 1'b1, "unmapped stream PSLVERR");

        if (error_count != 0) begin
            $fatal(1, "FAIL: qcrate_stream_regs_irq_tb had %0d error(s)",
                   error_count);
        end

        $display("PASS: qcrate_stream_regs_irq_tb");
        $finish;
    end

endmodule

`default_nettype wire
