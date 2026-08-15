`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_regs #(
    parameter int EVENT_ADDR_WIDTH = 7,
    parameter int EVENT_COUNT_WIDTH = EVENT_ADDR_WIDTH + 1,
    parameter int RAM_CTRL_ADDR_WIDTH = EVENT_ADDR_WIDTH + 2
) (
    input  wire logic                              pclk_i,
    input  wire logic                              presetn_i,
    input  wire logic [11:0]                       paddr_i,
    input  wire logic                              psel_i,
    input  wire logic                              penable_i,
    input  wire logic                              pwrite_i,
    input  wire logic [31:0]                       pwdata_i,
    output logic [31:0]                            prdata_o,
    output logic                                   pready_o,
    output logic                                   pslverr_o,

    output logic [EVENT_COUNT_WIDTH-1:0]           event_count_o,
    output logic                                   external_trigger_enable_o,
    output logic                                   arm_cmd_o,
    output logic                                   start_cmd_o,
    output logic                                   abort_cmd_o,
    output logic                                   soft_reset_cmd_o,
    input  wire logic                              command_busy_i,
    input  wire logic                              done_event_i,
    input  wire logic                              aborted_event_i,

    input  wire logic                              idle_i,
    input  wire logic                              validating_i,
    input  wire logic                              armed_i,
    input  wire logic                              busy_i,
    input  wire logic                              faulted_i,
    input  wire logic [7:0]                        fault_code_i,
    input  wire logic [EVENT_ADDR_WIDTH-1:0]       fault_event_index_i,
    input  wire logic [EVENT_ADDR_WIDTH-1:0]       active_event_index_i,
    input  wire logic [31:0]                       completed_shots_i,
    input  wire logic [63:0]                       timebase_i,
    input  wire logic [63:0]                       start_time_i,
    input  wire logic [63:0]                       elapsed_tick_i,

    output logic                                   ram_en_o,
    output logic                                   ram_write_o,
    output logic [RAM_CTRL_ADDR_WIDTH-1:0]         ram_addr_o,
    output logic [31:0]                            ram_wdata_o,
    input  wire logic                              ram_rvalid_i,
    input  wire logic [31:0]                       ram_rdata_i
);

    localparam logic [31:0] UNMAPPED_PRDATA = 32'hDEAD_BEEF;
    localparam logic [11:0] ADDR_CONTROL         = 12'h000;
    localparam logic [11:0] ADDR_STATUS          = 12'h004;
    localparam logic [11:0] ADDR_EVENT_COUNT     = 12'h008;
    localparam logic [11:0] ADDR_ACTIVE_EVENT    = 12'h00C;
    localparam logic [11:0] ADDR_COMPLETED_SHOTS = 12'h010;
    localparam logic [11:0] ADDR_FAULT_INFO      = 12'h014;
    localparam logic [11:0] ADDR_TIMEBASE_LO     = 12'h018;
    localparam logic [11:0] ADDR_TIMEBASE_HI     = 12'h01C;
    localparam logic [11:0] ADDR_START_TIME_LO   = 12'h020;
    localparam logic [11:0] ADDR_START_TIME_HI   = 12'h024;
    localparam logic [11:0] ADDR_ELAPSED_LO      = 12'h028;
    localparam logic [11:0] ADDR_ELAPSED_HI      = 12'h02C;

    logic apb_access;
    logic apb_transfer;
    logic apb_access_seen_q;
    logic event_window;
    logic addr_mapped;
    logic write_allowed;
    logic command_encoding_valid;
    logic command_requested;
    logic memory_access_allowed;
    logic memory_read_pending_q;
    logic memory_read_ready_q;
    logic memory_locked_q;
    logic unlock_pending_q;
    logic [31:0] memory_read_data_q;
    logic [31:0] timebase_high_latch_q;
    logic [31:0] start_time_high_latch_q;
    logic [31:0] elapsed_high_latch_q;
    logic [31:0] control_word;
    logic [31:0] status_word;
    logic [31:0] fault_info_word;

    assign apb_access = psel_i && penable_i;
    assign apb_transfer = apb_access && !apb_access_seen_q;
    assign event_window = paddr_i[11];
    assign command_requested = |pwdata_i[3:0];
    assign command_encoding_valid =
        (pwdata_i[3:0] == 4'b0000) ||
        (pwdata_i[3:0] == 4'b0001) ||
        (pwdata_i[3:0] == 4'b0010) ||
        (pwdata_i[3:0] == 4'b0100) ||
        (pwdata_i[3:0] == 4'b1000);
    assign memory_access_allowed = !memory_locked_q && !command_busy_i;

    assign control_word = {23'd0, external_trigger_enable_o, 8'd0};
    assign status_word = {
        22'd0,
        memory_locked_q,
        command_busy_i,
        3'd0,
        faulted_i,
        busy_i,
        armed_i,
        validating_i,
        idle_i
    };
    assign fault_info_word = {
        {(16-EVENT_ADDR_WIDTH){1'b0}},
        fault_event_index_i,
        8'd0,
        fault_code_i
    };

    always_comb begin
        addr_mapped = event_window;
        write_allowed = event_window && memory_access_allowed;
        prdata_o = event_window && memory_access_allowed ?
                   memory_read_data_q : UNMAPPED_PRDATA;
        pready_o = event_window && !pwrite_i && memory_access_allowed ?
                   memory_read_ready_q : 1'b1;

        if (!event_window) begin
            unique case (paddr_i)
                ADDR_CONTROL: begin
                    addr_mapped = 1'b1;
                    write_allowed = command_encoding_valid &&
                                    (!command_requested || !command_busy_i);
                    prdata_o = control_word;
                end
                ADDR_STATUS: begin
                    addr_mapped = 1'b1;
                    prdata_o = status_word;
                end
                ADDR_EVENT_COUNT: begin
                    addr_mapped = 1'b1;
                    write_allowed = memory_access_allowed;
                    prdata_o = {{(32-EVENT_COUNT_WIDTH){1'b0}}, event_count_o};
                end
                ADDR_ACTIVE_EVENT: begin
                    addr_mapped = 1'b1;
                    prdata_o = {{(32-EVENT_ADDR_WIDTH){1'b0}},
                                active_event_index_i};
                end
                ADDR_COMPLETED_SHOTS: begin
                    addr_mapped = 1'b1;
                    prdata_o = completed_shots_i;
                end
                ADDR_FAULT_INFO: begin
                    addr_mapped = 1'b1;
                    prdata_o = fault_info_word;
                end
                ADDR_TIMEBASE_LO: begin
                    addr_mapped = 1'b1;
                    prdata_o = timebase_i[31:0];
                end
                ADDR_TIMEBASE_HI: begin
                    addr_mapped = 1'b1;
                    prdata_o = timebase_high_latch_q;
                end
                ADDR_START_TIME_LO: begin
                    addr_mapped = 1'b1;
                    prdata_o = start_time_i[31:0];
                end
                ADDR_START_TIME_HI: begin
                    addr_mapped = 1'b1;
                    prdata_o = start_time_high_latch_q;
                end
                ADDR_ELAPSED_LO: begin
                    addr_mapped = 1'b1;
                    prdata_o = elapsed_tick_i[31:0];
                end
                ADDR_ELAPSED_HI: begin
                    addr_mapped = 1'b1;
                    prdata_o = elapsed_high_latch_q;
                end
                default: begin
                end
            endcase
        end
    end

    assign pslverr_o = apb_access &&
        (!addr_mapped || (pwrite_i && !write_allowed) ||
         (event_window && !memory_access_allowed));

    assign ram_en_o = apb_transfer && event_window && memory_access_allowed &&
                      (pwrite_i ||
                       (!memory_read_pending_q && !memory_read_ready_q));
    assign ram_write_o = pwrite_i;
    assign ram_addr_o = paddr_i[RAM_CTRL_ADDR_WIDTH+1:2];
    assign ram_wdata_o = pwdata_i;

    always_ff @(posedge pclk_i) begin
        if (!presetn_i) begin
            event_count_o <= '0;
            external_trigger_enable_o <= 1'b0;
            arm_cmd_o <= 1'b0;
            start_cmd_o <= 1'b0;
            abort_cmd_o <= 1'b0;
            soft_reset_cmd_o <= 1'b0;
            memory_read_pending_q <= 1'b0;
            memory_read_ready_q <= 1'b0;
            apb_access_seen_q <= 1'b0;
            memory_locked_q <= 1'b0;
            unlock_pending_q <= 1'b0;
            memory_read_data_q <= '0;
            timebase_high_latch_q <= '0;
            start_time_high_latch_q <= '0;
            elapsed_high_latch_q <= '0;
        end else begin
            arm_cmd_o <= 1'b0;
            start_cmd_o <= 1'b0;
            abort_cmd_o <= 1'b0;
            soft_reset_cmd_o <= 1'b0;

            if (!apb_access)
                apb_access_seen_q <= 1'b0;
            else
                apb_access_seen_q <= 1'b1;

            if (done_event_i || aborted_event_i) begin
                memory_locked_q <= 1'b0;
                unlock_pending_q <= 1'b0;
            end else if (unlock_pending_q && !command_busy_i && idle_i) begin
                memory_locked_q <= 1'b0;
                unlock_pending_q <= 1'b0;
            end

            if (!psel_i) begin
                memory_read_pending_q <= 1'b0;
                memory_read_ready_q <= 1'b0;
            end else begin
                if (ram_en_o && !ram_write_o)
                    memory_read_pending_q <= 1'b1;
                if (ram_rvalid_i) begin
                    memory_read_pending_q <= 1'b0;
                    memory_read_ready_q <= 1'b1;
                    memory_read_data_q <= ram_rdata_i;
                end
            end

            if (apb_transfer && !pwrite_i && pready_o) begin
                if (paddr_i == ADDR_TIMEBASE_LO)
                    timebase_high_latch_q <= timebase_i[63:32];
                if (paddr_i == ADDR_START_TIME_LO)
                    start_time_high_latch_q <= start_time_i[63:32];
                if (paddr_i == ADDR_ELAPSED_LO)
                    elapsed_high_latch_q <= elapsed_tick_i[63:32];
            end

            if (apb_transfer && pwrite_i && write_allowed && !event_window) begin
                unique case (paddr_i)
                    ADDR_CONTROL: begin
                        external_trigger_enable_o <= pwdata_i[8];
                        arm_cmd_o <= pwdata_i[0];
                        start_cmd_o <= pwdata_i[1];
                        abort_cmd_o <= pwdata_i[2];
                        soft_reset_cmd_o <= pwdata_i[3];
                        if (pwdata_i[0])
                            memory_locked_q <= 1'b1;
                        if (pwdata_i[3])
                            unlock_pending_q <= 1'b1;
                    end
                    ADDR_EVENT_COUNT: begin
                        event_count_o <= pwdata_i[EVENT_COUNT_WIDTH-1:0];
                    end
                    default: begin
                    end
                endcase
            end
        end
    end

endmodule

`default_nettype wire
