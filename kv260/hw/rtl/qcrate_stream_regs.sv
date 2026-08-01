`timescale 1ns / 1ps
`default_nettype none

module qcrate_stream_regs (
    input  wire logic        pclk_i,
    input  wire logic        presetn_i,

    input  wire logic [11:0] paddr_i,
    input  wire logic        psel_i,
    input  wire logic        penable_i,
    input  wire logic        pwrite_i,
    input  wire logic [31:0] pwdata_i,

    output logic [31:0]      prdata_o,
    output logic             pready_o,
    output logic             pslverr_o,

    output logic [31:0]      frame_length_o,
    output logic [31:0]      frame_count_o,
    output logic [31:0]      stream_mode_o,
    output logic             continuous_o,

    output logic             start_cmd_o,
    output logic             abort_cmd_o,
    output logic             soft_reset_cmd_o,

    input  wire logic        command_busy_i,
    input  wire logic        stream_busy_i,
    input  wire logic [31:0] completed_frames_i,
    input  wire logic [31:0] current_frame_id_i,
    input  wire logic [31:0] current_sample_index_i,
    input  wire logic [31:0] stall_cycles_i,

    input  wire logic [31:0] irq_status_i,
    output logic [31:0]      irq_enable_o,
    output logic [31:0]      irq_clear_o
);

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

    logic        apb_access;
    logic        addr_mapped;
    logic        write_allowed;
    logic [31:0] status_word;
    logic [31:0] control_word;

    assign apb_access = psel_i && penable_i;
    assign pready_o = 1'b1;

    assign control_word = {
        23'd0,
        continuous_o,
        5'd0,
        3'b000
    };

    assign status_word = {
        23'd0,
        command_busy_i,
        4'd0,
        (stall_cycles_i != 32'd0),
        irq_status_i[1],
        irq_status_i[0],
        stream_busy_i
    };

    always_comb begin
        addr_mapped = 1'b1;
        write_allowed = 1'b0;
        prdata_o = UNMAPPED_PRDATA;

        unique case (paddr_i)
            ADDR_CONTROL: begin
                prdata_o = control_word;
                write_allowed = 1'b1;
            end

            ADDR_FRAME_LENGTH: begin
                prdata_o = frame_length_o;
                write_allowed = 1'b1;
            end

            ADDR_FRAME_COUNT: begin
                prdata_o = frame_count_o;
                write_allowed = 1'b1;
            end

            ADDR_STREAM_MODE: begin
                prdata_o = stream_mode_o;
                write_allowed = 1'b1;
            end

            ADDR_STATUS: begin
                prdata_o = status_word;
            end

            ADDR_COMPLETED_FRAMES: begin
                prdata_o = completed_frames_i;
            end

            ADDR_CURRENT_FRAME_ID: begin
                prdata_o = current_frame_id_i;
            end

            ADDR_CURRENT_SAMPLE_INDEX: begin
                prdata_o = current_sample_index_i;
            end

            ADDR_STALL_CYCLES: begin
                prdata_o = stall_cycles_i;
            end

            ADDR_IRQ_STATUS: begin
                prdata_o = irq_status_i;
            end

            ADDR_IRQ_ENABLE: begin
                prdata_o = irq_enable_o;
                write_allowed = 1'b1;
            end

            ADDR_IRQ_CLEAR: begin
                prdata_o = 32'h0000_0000;
                write_allowed = 1'b1;
            end

            default: begin
                addr_mapped = 1'b0;
            end
        endcase
    end

    assign pslverr_o = apb_access && (!addr_mapped || (pwrite_i && !write_allowed));

    always_ff @(posedge pclk_i) begin
        if (!presetn_i) begin
            frame_length_o <= 32'h0000_0000;
            frame_count_o <= 32'h0000_0000;
            stream_mode_o <= 32'h0000_0000;
            continuous_o <= 1'b0;
            start_cmd_o <= 1'b0;
            abort_cmd_o <= 1'b0;
            soft_reset_cmd_o <= 1'b0;
            irq_enable_o <= 32'h0000_0000;
            irq_clear_o <= 32'h0000_0000;
        end else begin
            start_cmd_o <= 1'b0;
            abort_cmd_o <= 1'b0;
            soft_reset_cmd_o <= 1'b0;
            irq_clear_o <= 32'h0000_0000;

            if (apb_access && pwrite_i && write_allowed) begin
                unique case (paddr_i)
                    ADDR_CONTROL: begin
                        start_cmd_o <= pwdata_i[0];
                        abort_cmd_o <= pwdata_i[1];
                        soft_reset_cmd_o <= pwdata_i[2];
                        continuous_o <= pwdata_i[8];
                    end

                    ADDR_FRAME_LENGTH: begin
                        frame_length_o <= pwdata_i;
                    end

                    ADDR_FRAME_COUNT: begin
                        frame_count_o <= pwdata_i;
                    end

                    ADDR_STREAM_MODE: begin
                        stream_mode_o <= pwdata_i;
                    end

                    ADDR_IRQ_ENABLE: begin
                        irq_enable_o <= pwdata_i;
                    end

                    ADDR_IRQ_CLEAR: begin
                        irq_clear_o <= pwdata_i;
                    end

                    default: begin
                    end
                endcase
            end
        end
    end

endmodule

`default_nettype wire
