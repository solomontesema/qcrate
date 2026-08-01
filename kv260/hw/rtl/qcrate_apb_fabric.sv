`timescale 1ns / 1ps
`default_nettype none

module qcrate_apb_fabric #(
    parameter int APB_ADDR_WIDTH = 32,
    parameter int APB_DATA_WIDTH = 32
) (
    input  logic                         pclk_i,
    input  logic                         presetn_i,

    input  logic [APB_ADDR_WIDTH-1:0]    paddr_i,
    input  logic                         psel_i,
    input  logic                         penable_i,
    input  logic                         pwrite_i,
    input  logic [APB_DATA_WIDTH-1:0]    pwdata_i,

    output logic                         sys_psel_o,
    output logic                         stream_psel_o,

    input  logic [APB_DATA_WIDTH-1:0]    sys_prdata_i,
    input  logic                         sys_pready_i,
    input  logic                         sys_pslverr_i,

    input  logic [APB_DATA_WIDTH-1:0]    stream_prdata_i,
    input  logic                         stream_pready_i,
    input  logic                         stream_pslverr_i,

    output logic [APB_DATA_WIDTH-1:0]    prdata_o,
    output logic                         pready_o,
    output logic                         pslverr_o
);

    localparam logic [APB_DATA_WIDTH-1:0] UNMAPPED_PRDATA = 32'hDEAD_BEEF;

    logic [3:0] page;
    logic       sys_hit;
    logic       stream_hit;

    assign page = paddr_i[15:12];
    assign sys_hit = psel_i && (page == 4'h0);
    assign stream_hit = psel_i && (page == 4'h1);

    assign sys_psel_o = sys_hit;
    assign stream_psel_o = stream_hit;

    always_comb begin
        prdata_o = '0;
        pready_o = 1'b1;
        pslverr_o = 1'b0;

        if (sys_hit) begin
            prdata_o = sys_prdata_i;
            pready_o = sys_pready_i;
            pslverr_o = sys_pslverr_i;
        end else if (stream_hit) begin
            prdata_o = stream_prdata_i;
            pready_o = stream_pready_i;
            pslverr_o = stream_pslverr_i;
        end else if (psel_i) begin
            prdata_o = UNMAPPED_PRDATA;
            pslverr_o = penable_i;
        end
    end

    logic unused_inputs;
    assign unused_inputs = &{1'b0, pclk_i, presetn_i, pwrite_i, pwdata_i};

endmodule

`default_nettype wire
