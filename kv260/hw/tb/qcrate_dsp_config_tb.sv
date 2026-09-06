`timescale 1ns / 1ps
`default_nettype none

module qcrate_dsp_config_tb;

    localparam logic [63:0] DEFAULT_CONFIG_ID = 64'h5db4_fb57_8b27_b09f;
    localparam logic [63:0] ALTERNATE_CONFIG_ID = 64'haf46_287b_b969_ed24;

    localparam logic [11:0] ADDR_COMMAND = 12'h000;
    localparam logic [11:0] ADDR_STATUS = 12'h004;
    localparam logic [11:0] ADDR_REJECT_REASON = 12'h008;
    localparam logic [11:0] ADDR_ACTIVE_GENERATION = 12'h00c;
    localparam logic [11:0] ADDR_SHADOW_CONFIG_ID_LOW = 12'h010;
    localparam logic [11:0] ADDR_SHADOW_CONFIG_ID_HIGH = 12'h014;
    localparam logic [11:0] ADDR_SHADOW_SIGNAL_INCREMENT = 12'h018;
    localparam logic [11:0] ADDR_SHADOW_SIGNAL_INITIAL = 12'h01c;
    localparam logic [11:0] ADDR_SHADOW_SIGNAL_AMPLITUDE = 12'h020;
    localparam logic [11:0] ADDR_SHADOW_NOISE_AMPLITUDE = 12'h024;
    localparam logic [11:0] ADDR_SHADOW_NOISE_SEED = 12'h028;
    localparam logic [11:0] ADDR_SHADOW_LO_INCREMENT = 12'h02c;
    localparam logic [11:0] ADDR_SHADOW_LO_INITIAL = 12'h030;
    localparam logic [11:0] ADDR_ACTIVE_CONFIG_ID_LOW = 12'h040;
    localparam logic [11:0] ADDR_ACTIVE_CONFIG_ID_HIGH = 12'h044;
    localparam logic [11:0] ADDR_ACTIVE_LO_INCREMENT = 12'h05c;

    logic ctrl_clk;
    logic stream_clk;
    logic ctrl_rst_n;
    logic stream_rst_n;
    logic safe_to_commit;

    logic [11:0] paddr;
    logic psel;
    logic penable;
    logic pwrite;
    logic [31:0] pwdata;
    logic [31:0] prdata;
    logic pready;
    logic pslverr;

    logic [63:0] shadow_config_id;
    logic [31:0] shadow_signal_increment;
    logic [31:0] shadow_signal_initial;
    logic [15:0] shadow_signal_amplitude;
    logic [15:0] shadow_noise_amplitude;
    logic [15:0] shadow_noise_seed;
    logic [31:0] shadow_lo_increment;
    logic [31:0] shadow_lo_initial;
    logic commit_cmd;
    logic commit_busy;
    logic commit_accepted;
    logic commit_rejected;
    logic [1:0] commit_reject_reason;

    logic active_valid_ctrl;
    logic [31:0] active_generation_ctrl;
    logic [63:0] active_config_id_ctrl;
    logic [31:0] active_signal_increment_ctrl;
    logic [31:0] active_signal_initial_ctrl;
    logic [15:0] active_signal_amplitude_ctrl;
    logic [15:0] active_noise_amplitude_ctrl;
    logic [15:0] active_noise_seed_ctrl;
    logic [31:0] active_lo_increment_ctrl;
    logic [31:0] active_lo_initial_ctrl;

    logic [31:0] active_generation_stream;
    logic [63:0] active_config_id_stream;
    logic [31:0] active_signal_increment_stream;
    logic [31:0] active_signal_initial_stream;
    logic [15:0] active_signal_amplitude_stream;
    logic [15:0] active_noise_amplitude_stream;
    logic [15:0] active_noise_seed_stream;
    logic [31:0] active_lo_increment_stream;
    logic [31:0] active_lo_initial_stream;

    int unsigned error_count;

    qcrate_dsp_config_regs u_regs (
        .pclk_i                             (ctrl_clk),
        .presetn_i                          (ctrl_rst_n),
        .paddr_i                            (paddr),
        .psel_i                             (psel),
        .penable_i                          (penable),
        .pwrite_i                           (pwrite),
        .pwdata_i                           (pwdata),
        .prdata_o                           (prdata),
        .pready_o                           (pready),
        .pslverr_o                          (pslverr),
        .shadow_config_id_o                 (shadow_config_id),
        .shadow_signal_phase_increment_o    (shadow_signal_increment),
        .shadow_signal_phase_initial_o      (shadow_signal_initial),
        .shadow_signal_amplitude_o          (shadow_signal_amplitude),
        .shadow_noise_amplitude_o           (shadow_noise_amplitude),
        .shadow_noise_seed_o                (shadow_noise_seed),
        .shadow_lo_phase_increment_o        (shadow_lo_increment),
        .shadow_lo_phase_initial_o          (shadow_lo_initial),
        .commit_cmd_o                       (commit_cmd),
        .commit_busy_i                      (commit_busy),
        .commit_accepted_i                  (commit_accepted),
        .commit_rejected_i                  (commit_rejected),
        .commit_reject_reason_i             (commit_reject_reason),
        .active_valid_i                     (active_valid_ctrl),
        .active_generation_i                (active_generation_ctrl),
        .active_config_id_i                 (active_config_id_ctrl),
        .active_signal_phase_increment_i    (active_signal_increment_ctrl),
        .active_signal_phase_initial_i      (active_signal_initial_ctrl),
        .active_signal_amplitude_i          (active_signal_amplitude_ctrl),
        .active_noise_amplitude_i           (active_noise_amplitude_ctrl),
        .active_noise_seed_i                (active_noise_seed_ctrl),
        .active_lo_phase_increment_i        (active_lo_increment_ctrl),
        .active_lo_phase_initial_i          (active_lo_initial_ctrl)
    );

    qcrate_dsp_config_cdc u_cdc (
        .ctrl_clk_i                         (ctrl_clk),
        .ctrl_rst_n_i                       (ctrl_rst_n),
        .shadow_config_id_i                 (shadow_config_id),
        .shadow_signal_phase_increment_i    (shadow_signal_increment),
        .shadow_signal_phase_initial_i      (shadow_signal_initial),
        .shadow_signal_amplitude_i          (shadow_signal_amplitude),
        .shadow_noise_amplitude_i           (shadow_noise_amplitude),
        .shadow_noise_seed_i                (shadow_noise_seed),
        .shadow_lo_phase_increment_i        (shadow_lo_increment),
        .shadow_lo_phase_initial_i          (shadow_lo_initial),
        .commit_cmd_i                       (commit_cmd),
        .commit_busy_o                      (commit_busy),
        .commit_accepted_o                  (commit_accepted),
        .commit_rejected_o                  (commit_rejected),
        .commit_reject_reason_o             (commit_reject_reason),
        .active_valid_ctrl_o                (active_valid_ctrl),
        .active_generation_ctrl_o           (active_generation_ctrl),
        .active_config_id_ctrl_o            (active_config_id_ctrl),
        .active_signal_phase_increment_ctrl_o(active_signal_increment_ctrl),
        .active_signal_phase_initial_ctrl_o (active_signal_initial_ctrl),
        .active_signal_amplitude_ctrl_o     (active_signal_amplitude_ctrl),
        .active_noise_amplitude_ctrl_o      (active_noise_amplitude_ctrl),
        .active_noise_seed_ctrl_o           (active_noise_seed_ctrl),
        .active_lo_phase_increment_ctrl_o   (active_lo_increment_ctrl),
        .active_lo_phase_initial_ctrl_o     (active_lo_initial_ctrl),
        .stream_clk_i                       (stream_clk),
        .stream_rst_n_i                     (stream_rst_n),
        .safe_to_commit_i                   (safe_to_commit),
        .active_generation_stream_o         (active_generation_stream),
        .active_config_id_stream_o          (active_config_id_stream),
        .active_signal_phase_increment_stream_o(active_signal_increment_stream),
        .active_signal_phase_initial_stream_o(active_signal_initial_stream),
        .active_signal_amplitude_stream_o   (active_signal_amplitude_stream),
        .active_noise_amplitude_stream_o    (active_noise_amplitude_stream),
        .active_noise_seed_stream_o         (active_noise_seed_stream),
        .active_lo_phase_increment_stream_o (active_lo_increment_stream),
        .active_lo_phase_initial_stream_o   (active_lo_initial_stream)
    );

    always #5 ctrl_clk = ~ctrl_clk;
    always #6.5 stream_clk = ~stream_clk;

    task automatic fail(input string message);
        error_count++;
        $error("%s", message);
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

    task automatic apb_write(
        input logic [11:0] address,
        input logic [31:0] data,
        output logic error
    );
        @(negedge ctrl_clk);
        paddr = address;
        pwrite = 1'b1;
        pwdata = data;
        psel = 1'b1;
        penable = 1'b0;
        @(negedge ctrl_clk);
        penable = 1'b1;
        @(posedge ctrl_clk);
        #1;
        error = pslverr;
        if (!pready)
            fail("APB write did not complete");
        @(negedge ctrl_clk);
        drive_idle();
    endtask

    task automatic apb_read(
        input logic [11:0] address,
        output logic [31:0] data,
        output logic error
    );
        @(negedge ctrl_clk);
        paddr = address;
        pwrite = 1'b0;
        psel = 1'b1;
        penable = 1'b0;
        @(negedge ctrl_clk);
        penable = 1'b1;
        @(posedge ctrl_clk);
        #1;
        data = prdata;
        error = pslverr;
        if (!pready)
            fail("APB read did not complete");
        @(negedge ctrl_clk);
        drive_idle();
    endtask

    task automatic expect_read(
        input logic [11:0] address,
        input logic [31:0] expected,
        input string what
    );
        logic [31:0] data;
        logic error;
        apb_read(address, data, error);
        if (error)
            fail({what, ": unexpected PSLVERR"});
        expect_word(data, expected, what);
    endtask

    task automatic wait_for_result(input logic expect_accept);
        int guard;
        guard = 0;
        while (!commit_accepted && !commit_rejected && (guard < 100)) begin
            @(posedge ctrl_clk);
            #1;
            guard++;
        end
        if (guard == 100)
            fail("configuration commit result timed out");
        if (commit_accepted !== expect_accept)
            fail("configuration commit result had wrong disposition");
    endtask

    initial begin
        logic error;
        logic [31:0] old_generation;

        error_count = 0;
        ctrl_clk = 1'b0;
        stream_clk = 1'b0;
        ctrl_rst_n = 1'b0;
        stream_rst_n = 1'b0;
        safe_to_commit = 1'b1;
        drive_idle();
        repeat (6) @(posedge ctrl_clk);
        ctrl_rst_n = 1'b1;
        stream_rst_n = 1'b1;
        repeat (5) @(posedge ctrl_clk);

        expect_read(ADDR_STATUS, 32'h0000_0004, "reset STATUS");
        expect_read(ADDR_ACTIVE_GENERATION, 32'd0, "reset generation");
        expect_read(ADDR_ACTIVE_CONFIG_ID_LOW, DEFAULT_CONFIG_ID[31:0],
                    "reset active ID low");
        expect_read(ADDR_ACTIVE_CONFIG_ID_HIGH, DEFAULT_CONFIG_ID[63:32],
                    "reset active ID high");

        apb_write(ADDR_SHADOW_CONFIG_ID_LOW, ALTERNATE_CONFIG_ID[31:0], error);
        apb_write(ADDR_SHADOW_CONFIG_ID_HIGH, ALTERNATE_CONFIG_ID[63:32], error);
        apb_write(ADDR_SHADOW_SIGNAL_INCREMENT, 32'h2666_6666, error);
        apb_write(ADDR_SHADOW_SIGNAL_INITIAL, 32'h0123_4567, error);
        apb_write(ADDR_SHADOW_SIGNAL_AMPLITUDE, 32'h0000_5000, error);
        apb_write(ADDR_SHADOW_NOISE_AMPLITUDE, 32'h0000_0100, error);
        apb_write(ADDR_SHADOW_NOISE_SEED, 32'h0000_1234, error);
        apb_write(ADDR_SHADOW_LO_INCREMENT, 32'h247a_e148, error);
        apb_write(ADDR_SHADOW_LO_INITIAL, 32'h89ab_cdef, error);
        expect_read(ADDR_STATUS, 32'h0000_0005, "dirty STATUS");

        apb_write(ADDR_COMMAND, 32'h0000_0001, error);
        if (error)
            fail("valid COMMIT returned PSLVERR");
        @(posedge ctrl_clk);
        #1;
        if (!commit_busy)
            fail("COMMIT never owned the mailbox");
        if ((active_generation_stream != 0) ||
            (active_config_id_stream != DEFAULT_CONFIG_ID))
            fail("active bundle changed before destination acceptance");
        apb_write(ADDR_SHADOW_LO_INCREMENT, 32'h1111_1111, error);
        if (!error)
            fail("shadow write was accepted while mailbox was busy");
        wait_for_result(1'b1);

        expect_word(active_generation_stream, 32'd1, "stream generation");
        if ((active_config_id_stream != ALTERNATE_CONFIG_ID) ||
            (active_signal_increment_stream != 32'h2666_6666) ||
            (active_signal_initial_stream != 32'h0123_4567) ||
            (active_signal_amplitude_stream != 16'h5000) ||
            (active_noise_amplitude_stream != 16'h0100) ||
            (active_noise_seed_stream != 16'h1234) ||
            (active_lo_increment_stream != 32'h247a_e148) ||
            (active_lo_initial_stream != 32'h89ab_cdef))
            fail("destination did not activate the complete bundle atomically");
        expect_read(ADDR_STATUS, 32'h0000_0004, "accepted STATUS");
        expect_read(ADDR_ACTIVE_GENERATION, 32'd1, "accepted generation");
        expect_read(ADDR_ACTIVE_CONFIG_ID_LOW, ALTERNATE_CONFIG_ID[31:0],
                    "accepted active ID low");
        expect_read(ADDR_ACTIVE_CONFIG_ID_HIGH, ALTERNATE_CONFIG_ID[63:32],
                    "accepted active ID coherent high latch");
        expect_read(ADDR_ACTIVE_LO_INCREMENT, 32'h247a_e148,
                    "accepted active LO");

        apb_write(ADDR_COMMAND, 32'h0000_0002, error);
        expect_read(ADDR_STATUS, 32'h0000_0004,
                    "post-commit discard STATUS");

        apb_write(ADDR_SHADOW_SIGNAL_AMPLITUDE, 32'h0001_0000, error);
        if (!error)
            fail("reserved amplitude bits were accepted");
        expect_read(ADDR_REJECT_REASON, 32'd1, "reserved-bit reason");

        apb_write(ADDR_SHADOW_LO_INCREMENT, 32'h251e_b852, error);
        safe_to_commit = 1'b0;
        old_generation = active_generation_stream;
        apb_write(ADDR_COMMAND, 32'h0000_0001, error);
        wait_for_result(1'b0);
        expect_read(ADDR_REJECT_REASON, 32'd2, "unsafe-state reason");
        expect_word(active_generation_stream, old_generation,
                    "unsafe commit generation");
        expect_word(active_lo_increment_stream, 32'h247a_e148,
                    "unsafe commit active LO");
        expect_read(ADDR_STATUS, 32'h0000_000d, "rejected dirty STATUS");

        safe_to_commit = 1'b1;
        apb_write(ADDR_COMMAND, 32'h0000_0002, error);
        if (error)
            fail("DISCARD returned PSLVERR");
        expect_read(ADDR_STATUS, 32'h0000_0004, "discard STATUS");
        expect_read(ADDR_SHADOW_LO_INCREMENT, 32'h247a_e148,
                    "discard restored active LO");

        if (error_count != 0)
            $fatal(1, "FAIL: qcrate_dsp_config_tb had %0d error(s)",
                   error_count);
        $display("PASS: qcrate_dsp_config_tb");
        $finish;
    end

endmodule

`default_nettype wire
