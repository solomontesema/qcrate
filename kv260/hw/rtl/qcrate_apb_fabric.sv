`timescale 1ns / 1ps
`default_nettype none

module qcrate_apb_fabric #(
    parameter int APB_ADDR_WIDTH = 32,
    parameter int APB_DATA_WIDTH = 32
) (
    input  wire logic                    pclk_i,
    input  wire logic                    presetn_i,

    // The fabric decodes only the 4 KiB page bits; lower bits belong to slaves.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire logic [APB_ADDR_WIDTH-1:0]
                                         paddr_i,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire logic                    psel_i,
    input  wire logic                    penable_i,
    input  wire logic                    pwrite_i,
    input  wire logic [APB_DATA_WIDTH-1:0]
                                         pwdata_i,

    output logic                         sys_psel_o,
    output logic                         stream_psel_o,

    input  wire logic [APB_DATA_WIDTH-1:0]
                                         sys_prdata_i,
    input  wire logic                    sys_pready_i,
    input  wire logic                    sys_pslverr_i,

    input  wire logic [APB_DATA_WIDTH-1:0]
                                         stream_prdata_i,
    input  wire logic                    stream_pready_i,
    input  wire logic                    stream_pslverr_i,

    output logic [APB_DATA_WIDTH-1:0]    prdata_o,
    output logic                         pready_o,
    output logic                         pslverr_o
);

    localparam logic [APB_DATA_WIDTH-1:0] UNMAPPED_PRDATA = 32'hDEAD_BEEF;

    typedef enum logic [1:0] {
        SEL_NONE     = 2'b00,
        SEL_SYS      = 2'b01,
        SEL_STREAM   = 2'b10,
        SEL_UNMAPPED = 2'b11
    } apb_sel_t;

    logic [3:0] page;
    apb_sel_t   selected_q;

    assign page = paddr_i[15:12];

    always_ff @(posedge pclk_i) begin
        if (!presetn_i) begin
            selected_q <= SEL_NONE;
        end else if (psel_i && !penable_i) begin
            unique case (page)
                4'h0: selected_q <= SEL_SYS;
                4'h1: selected_q <= SEL_STREAM;
                default: selected_q <= SEL_UNMAPPED;
            endcase
        end else if (!psel_i) begin
            selected_q <= SEL_NONE;
        end
    end

    assign sys_psel_o = psel_i && (selected_q == SEL_SYS);
    assign stream_psel_o = psel_i && (selected_q == SEL_STREAM);

    always_comb begin
        prdata_o = '0;
        pready_o = 1'b1;
        pslverr_o = 1'b0;

        unique case (selected_q)
            SEL_SYS: begin
                prdata_o = sys_prdata_i;
                pready_o = sys_pready_i;
                pslverr_o = sys_pslverr_i;
            end

            SEL_STREAM: begin
                prdata_o = stream_prdata_i;
                pready_o = stream_pready_i;
                pslverr_o = stream_pslverr_i;
            end

            SEL_UNMAPPED: begin
                prdata_o = UNMAPPED_PRDATA;
                pslverr_o = psel_i && penable_i;
            end

            default: begin
            end
        endcase
    end

    logic unused_inputs;
    assign unused_inputs = &{1'b0, pwrite_i, pwdata_i};

endmodule

`default_nettype wire
