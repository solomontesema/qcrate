`timescale 1ns / 1ps
`default_nettype none

module qcrate_sys_regs #(
    parameter logic [31:0] VERSION          = 32'h0000_0100,
    parameter logic [31:0] BUILD_ID         = 32'h0000_0000,
    parameter logic [31:0] CAPABILITIES     = 32'h0000_0001,
    parameter logic [31:0] STREAM_CLOCK_HZ  = 32'd200_000_000,
    parameter logic [31:0] CONTROL_CLOCK_HZ = 32'd100_000_000
) (
    input  logic        pclk_i,
    input  logic        presetn_i,

    input  logic [11:0] paddr_i,
    input  logic        psel_i,
    input  logic        penable_i,
    input  logic        pwrite_i,
    input  logic [31:0] pwdata_i,

    output logic [31:0] prdata_o,
    output logic        pready_o,
    output logic        pslverr_o
);

    localparam logic [31:0] DEVICE_ID        = 32'h5143_5254; // "QCRT"
    localparam logic [31:0] UNMAPPED_PRDATA  = 32'hDEAD_BEEF;

    localparam logic [11:0] ADDR_DEVICE_ID        = 12'h000;
    localparam logic [11:0] ADDR_VERSION          = 12'h004;
    localparam logic [11:0] ADDR_BUILD_ID         = 12'h008;
    localparam logic [11:0] ADDR_CAPABILITIES     = 12'h00C;
    localparam logic [11:0] ADDR_SCRATCH          = 12'h010;
    localparam logic [11:0] ADDR_STREAM_CLOCK_HZ  = 12'h014;
    localparam logic [11:0] ADDR_CONTROL_CLOCK_HZ = 12'h018;

    logic [31:0] scratch_q;
    logic        apb_access;
    logic        addr_mapped;
    logic        write_allowed;

    assign apb_access = psel_i && penable_i;
    assign pready_o = 1'b1;

    always_comb begin
        addr_mapped = 1'b1;
        write_allowed = 1'b0;
        prdata_o = UNMAPPED_PRDATA;

        unique case (paddr_i)
            ADDR_DEVICE_ID: begin
                prdata_o = DEVICE_ID;
            end

            ADDR_VERSION: begin
                prdata_o = VERSION;
            end

            ADDR_BUILD_ID: begin
                prdata_o = BUILD_ID;
            end

            ADDR_CAPABILITIES: begin
                prdata_o = CAPABILITIES;
            end

            ADDR_SCRATCH: begin
                prdata_o = scratch_q;
                write_allowed = 1'b1;
            end

            ADDR_STREAM_CLOCK_HZ: begin
                prdata_o = STREAM_CLOCK_HZ;
            end

            ADDR_CONTROL_CLOCK_HZ: begin
                prdata_o = CONTROL_CLOCK_HZ;
            end

            default: begin
                addr_mapped = 1'b0;
            end
        endcase
    end

    assign pslverr_o = apb_access && (!addr_mapped || (pwrite_i && !write_allowed));

    always_ff @(posedge pclk_i) begin
        if (!presetn_i) begin
            scratch_q <= 32'h0000_0000;
        end else if (apb_access && pwrite_i && write_allowed) begin
            scratch_q <= pwdata_i;
        end
    end

endmodule

`default_nettype wire
