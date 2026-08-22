`timescale 1ns / 1ps
`default_nettype none

module qcrate_nco #(
    parameter int PHASE_WIDTH = 32,
    parameter int LUT_ADDR_WIDTH = 12,
    parameter int OUTPUT_WIDTH = 16,
    parameter string LUT_FILE = "rtl/dsp/tables/sine_quarter_q1_15.hex"
) (
    input  wire logic                          clk_i,
    input  wire logic                          rst_n_i,
    input  wire logic                          ce_i,

    input  wire logic                          phase_load_i,
    input  wire logic [PHASE_WIDTH-1:0]        phase_initial_i,
    input  wire logic [PHASE_WIDTH-1:0]        phase_increment_i,
    input  wire logic                          advance_i,

    output logic                               valid_o,
    output logic signed [OUTPUT_WIDTH-1:0]     sine_o,
    output logic signed [OUTPUT_WIDTH-1:0]     cosine_o,
    output logic [PHASE_WIDTH-1:0]             phase_o
);

    localparam int QUARTER_INDEX_WIDTH = LUT_ADDR_WIDTH - 2;
    localparam int QUARTER_ADDR_WIDTH = QUARTER_INDEX_WIDTH + 1;
    localparam int QUARTER_DEPTH = 1 << QUARTER_INDEX_WIDTH;
    localparam logic [QUARTER_ADDR_WIDTH-1:0] QUARTER_DEPTH_VALUE = {
        1'b1, {QUARTER_INDEX_WIDTH{1'b0}}
    };
    localparam logic [PHASE_WIDTH-1:0] QUARTER_TURN = {
        2'b01, {(PHASE_WIDTH-2){1'b0}}
    };

    (* rom_style = "block" *)
    logic signed [OUTPUT_WIDTH-1:0] sine_quarter_rom [0:QUARTER_DEPTH];

    logic [PHASE_WIDTH-1:0] phase_accumulator;
    logic [LUT_ADDR_WIDTH-1:0] sine_index;
    logic [LUT_ADDR_WIDTH-1:0] cosine_index;
    logic [QUARTER_ADDR_WIDTH:0] sine_decode;
    logic [QUARTER_ADDR_WIDTH:0] cosine_decode;

    logic rom_valid;
    logic rom_sine_negative;
    logic rom_cosine_negative;
    logic signed [OUTPUT_WIDTH-1:0] rom_sine_data;
    logic signed [OUTPUT_WIDTH-1:0] rom_cosine_data;

    function automatic logic [QUARTER_ADDR_WIDTH:0] decode_index(
        input logic [LUT_ADDR_WIDTH-1:0] full_index
    );
        logic [1:0] quadrant;
        logic [QUARTER_INDEX_WIDTH-1:0] offset;
        logic [QUARTER_ADDR_WIDTH-1:0] address;
        begin
            quadrant = full_index[LUT_ADDR_WIDTH-1 -: 2];
            offset = full_index[QUARTER_INDEX_WIDTH-1:0];
            if (quadrant[0]) begin
                address = QUARTER_DEPTH_VALUE - {
                    {(QUARTER_ADDR_WIDTH-QUARTER_INDEX_WIDTH){1'b0}}, offset
                };
            end else begin
                address = {{(QUARTER_ADDR_WIDTH-QUARTER_INDEX_WIDTH){1'b0}},
                           offset};
            end
            return {quadrant[1], address};
        end
    endfunction

    initial begin
        if (PHASE_WIDTH < LUT_ADDR_WIDTH) begin
            $fatal(1, "PHASE_WIDTH must be at least LUT_ADDR_WIDTH");
        end
        if ((LUT_ADDR_WIDTH != 12) || (OUTPUT_WIDTH != 16)) begin
            $fatal(1, "DSP-1 table contract requires a 12-bit address and Q1.15 output");
        end
        $readmemh(LUT_FILE, sine_quarter_rom);
    end

    assign sine_index = phase_accumulator[PHASE_WIDTH-1 -: LUT_ADDR_WIDTH];
    assign cosine_index = sine_index + QUARTER_TURN[
        PHASE_WIDTH-1 -: LUT_ADDR_WIDTH
    ];
    assign sine_decode = decode_index(sine_index);
    assign cosine_decode = decode_index(cosine_index);
    assign phase_o = phase_accumulator;

    always_ff @(posedge clk_i) begin
        if (!rst_n_i) begin
            phase_accumulator <= '0;
            rom_valid <= 1'b0;
            rom_sine_negative <= 1'b0;
            rom_cosine_negative <= 1'b0;
            rom_sine_data <= '0;
            rom_cosine_data <= '0;
            valid_o <= 1'b0;
            sine_o <= '0;
            cosine_o <= '0;
        end else if (phase_load_i) begin
            phase_accumulator <= phase_initial_i;
            rom_valid <= 1'b0;
            valid_o <= 1'b0;
        end else if (ce_i) begin
            valid_o <= rom_valid;
            rom_valid <= advance_i;

            if (advance_i) begin
                rom_sine_data <= sine_quarter_rom[
                    sine_decode[QUARTER_ADDR_WIDTH-1:0]
                ];
                rom_cosine_data <= sine_quarter_rom[
                    cosine_decode[QUARTER_ADDR_WIDTH-1:0]
                ];
                rom_sine_negative <= sine_decode[QUARTER_ADDR_WIDTH];
                rom_cosine_negative <= cosine_decode[QUARTER_ADDR_WIDTH];
                phase_accumulator <= phase_accumulator + phase_increment_i;
            end

            if (rom_valid) begin
                sine_o <= rom_sine_negative ? -rom_sine_data : rom_sine_data;
                cosine_o <= rom_cosine_negative ?
                            -rom_cosine_data : rom_cosine_data;
            end
        end
    end

endmodule

`default_nettype wire
