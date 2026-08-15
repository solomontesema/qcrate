`timescale 1ns / 1ps
`default_nettype none

module qcrate_apb_tb;

    localparam logic [31:0] DEVICE_ID        = 32'h5143_5254;
    localparam logic [31:0] VERSION          = 32'h0000_0100;
    localparam logic [31:0] BUILD_ID         = 32'h0000_0000;
    localparam logic [31:0] CAPABILITIES     = 32'h0000_0001;
    localparam logic [31:0] STREAM_CLOCK_HZ  = 32'd200_000_000;
    localparam logic [31:0] CONTROL_CLOCK_HZ = 32'd100_000_000;
    localparam logic [31:0] UNMAPPED_PRDATA  = 32'hDEAD_BEEF;

    localparam logic [31:0] ADDR_DEVICE_ID        = 32'h0000_0000;
    localparam logic [31:0] ADDR_VERSION          = 32'h0000_0004;
    localparam logic [31:0] ADDR_BUILD_ID         = 32'h0000_0008;
    localparam logic [31:0] ADDR_CAPABILITIES     = 32'h0000_000C;
    localparam logic [31:0] ADDR_SCRATCH          = 32'h0000_0010;
    localparam logic [31:0] ADDR_STREAM_CLOCK_HZ  = 32'h0000_0014;
    localparam logic [31:0] ADDR_CONTROL_CLOCK_HZ = 32'h0000_0018;
    localparam logic [31:0] ADDR_STREAM_PAGE      = 32'h0000_1000;
    localparam logic [31:0] ADDR_UNMAPPED_SYS     = 32'h0000_00FC;
    localparam logic [31:0] ADDR_SEQUENCE_PAGE    = 32'h0000_2000;
    localparam logic [31:0] ADDR_UNMAPPED_PAGE    = 32'h0000_3000;

    logic        pclk;
    logic        presetn;

    logic [31:0] paddr;
    logic        psel;
    logic        penable;
    logic        pwrite;
    logic [31:0] pwdata;
    logic [31:0] prdata;
    logic        pready;
    logic        pslverr;

    logic        sys_psel;
    logic        stream_psel;
    logic        sequence_psel;

    logic [31:0] sys_prdata;
    logic        sys_pready;
    logic        sys_pslverr;

    logic [31:0] stream_prdata;
    logic        stream_pready;
    logic        stream_pslverr;

    logic [31:0] sequence_prdata;
    logic        sequence_pready;
    logic        sequence_pslverr;

    int unsigned error_count;

    qcrate_apb_fabric u_fabric (
        .pclk_i             (pclk),
        .presetn_i          (presetn),

        .paddr_i            (paddr),
        .psel_i             (psel),
        .penable_i          (penable),
        .pwrite_i           (pwrite),
        .pwdata_i           (pwdata),

        .sys_psel_o         (sys_psel),
        .stream_psel_o      (stream_psel),
        .sequence_psel_o    (sequence_psel),

        .sys_prdata_i       (sys_prdata),
        .sys_pready_i       (sys_pready),
        .sys_pslverr_i      (sys_pslverr),

        .stream_prdata_i    (stream_prdata),
        .stream_pready_i    (stream_pready),
        .stream_pslverr_i   (stream_pslverr),

        .sequence_prdata_i  (sequence_prdata),
        .sequence_pready_i  (sequence_pready),
        .sequence_pslverr_i (sequence_pslverr),

        .prdata_o           (prdata),
        .pready_o           (pready),
        .pslverr_o          (pslverr)
    );

    qcrate_sys_regs u_sys_regs (
        .pclk_i             (pclk),
        .presetn_i          (presetn),

        .paddr_i            (paddr[11:0]),
        .psel_i             (sys_psel),
        .penable_i          (penable),
        .pwrite_i           (pwrite),
        .pwdata_i           (pwdata),

        .prdata_o           (sys_prdata),
        .pready_o           (sys_pready),
        .pslverr_o          (sys_pslverr)
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
        stream_prdata = 32'hCAFE_BABE;
        stream_pready = 1'b1;
        stream_pslverr = 1'b0;
        sequence_prdata = 32'h5153_4551;
        sequence_pready = 1'b1;
        sequence_pslverr = 1'b0;
        presetn = 1'b0;
        repeat (3) @(posedge pclk);
        presetn = 1'b1;
        @(posedge pclk);
    endtask

    task automatic apb_read(
        input  logic [31:0] addr,
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
        input  logic [31:0] addr,
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
        input logic [31:0] addr,
        input logic [31:0] expected,
        input string       what
    );
        logic [31:0] data;
        logic        err;

        apb_read(addr, data, err);
        expect_word(data, expected, what);
        expect_bit(err, 1'b0, {what, " PSLVERR"});
    endtask

    initial begin
        logic [31:0] data;
        logic        err;

        error_count = 0;
        reset_dut();

        expect_read_ok(ADDR_DEVICE_ID, DEVICE_ID, "DEVICE_ID");
        expect_read_ok(ADDR_VERSION, VERSION, "VERSION");
        expect_read_ok(ADDR_BUILD_ID, BUILD_ID, "BUILD_ID");
        expect_read_ok(ADDR_CAPABILITIES, CAPABILITIES, "CAPABILITIES");
        expect_read_ok(ADDR_STREAM_CLOCK_HZ, STREAM_CLOCK_HZ, "STREAM_CLOCK_HZ");
        expect_read_ok(ADDR_CONTROL_CLOCK_HZ, CONTROL_CLOCK_HZ, "CONTROL_CLOCK_HZ");
        expect_read_ok(ADDR_SCRATCH, 32'h0000_0000, "SCRATCH reset value");

        @(negedge pclk);
        paddr = ADDR_SCRATCH;
        pwrite = 1'b1;
        pwdata = 32'hBAD0_5E7A;
        psel = 1'b1;
        penable = 1'b0;
        @(posedge pclk);
        @(negedge pclk);
        drive_idle();
        expect_read_ok(ADDR_SCRATCH, 32'h0000_0000,
                       "SCRATCH ignores setup-only write");

        apb_write(ADDR_SCRATCH, 32'h1234_5678, err);
        expect_bit(err, 1'b0, "SCRATCH write PSLVERR");
        expect_read_ok(ADDR_SCRATCH, 32'h1234_5678, "SCRATCH readback");

        apb_write(ADDR_SCRATCH, 32'hA5A5_5A5A, err);
        expect_bit(err, 1'b0, "back-to-back SCRATCH write PSLVERR");
        expect_read_ok(ADDR_SCRATCH, 32'hA5A5_5A5A,
                       "back-to-back SCRATCH readback");

        apb_write(ADDR_DEVICE_ID, 32'hFFFF_FFFF, err);
        expect_bit(err, 1'b1, "RO write returns PSLVERR");
        expect_read_ok(ADDR_DEVICE_ID, DEVICE_ID, "RO register unchanged");

        apb_read(ADDR_UNMAPPED_SYS, data, err);
        expect_word(data, UNMAPPED_PRDATA, "unmapped system-page PRDATA");
        expect_bit(err, 1'b1, "unmapped system-page PSLVERR");

        @(negedge pclk);
        paddr = ADDR_UNMAPPED_PAGE;
        pwrite = 1'b0;
        pwdata = '0;
        psel = 1'b1;
        penable = 1'b0;
        #1;
        expect_bit(sys_psel, 1'b0, "unmapped setup sys select");
        expect_bit(stream_psel, 1'b0, "unmapped setup stream select");
        expect_bit(sequence_psel, 1'b0, "unmapped setup sequence select");
        expect_bit(pslverr, 1'b0, "unmapped setup PSLVERR");

        @(negedge pclk);
        penable = 1'b1;
        #1;
        expect_word(prdata, UNMAPPED_PRDATA, "unmapped page PRDATA");
        expect_bit(pslverr, 1'b1, "unmapped page access PSLVERR");
        @(negedge pclk);
        drive_idle();

        stream_prdata = 32'hCAFE_BABE;
        stream_pready = 1'b1;
        stream_pslverr = 1'b1;
        apb_read(ADDR_STREAM_PAGE, data, err);
        expect_word(data, 32'hCAFE_BABE, "stream-page routed PRDATA");
        expect_bit(err, 1'b1, "stream-page routed PSLVERR");

        sequence_prdata = 32'h5153_4551;
        sequence_pready = 1'b1;
        sequence_pslverr = 1'b0;
        apb_read(ADDR_SEQUENCE_PAGE, data, err);
        expect_word(data, 32'h5153_4551, "sequence-page routed PRDATA");
        expect_bit(err, 1'b0, "sequence-page routed PSLVERR");

        if (error_count != 0) begin
            $fatal(1, "FAIL: qcrate_apb_tb had %0d error(s)", error_count);
        end

        $display("PASS: qcrate_apb_tb");
        $finish;
    end

endmodule

`default_nettype wire
