`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_ram #(
    parameter int MAX_EVENTS = 128,
    parameter int EVENT_ADDR_WIDTH = $clog2(MAX_EVENTS),
    parameter int CTRL_ADDR_WIDTH = $clog2(MAX_EVENTS * 4)
) (
    input  wire logic                         ctrl_clk_i,
    input  wire logic                         ctrl_rst_n_i,
    input  wire logic                         ctrl_en_i,
    input  wire logic                         ctrl_write_i,
    input  wire logic [CTRL_ADDR_WIDTH-1:0]   ctrl_addr_i,
    input  wire logic [31:0]                  ctrl_wdata_i,
    output logic                              ctrl_rvalid_o,
    output logic [31:0]                       ctrl_rdata_o,

    input  wire logic                         stream_clk_i,
    input  wire logic                         stream_rst_n_i,
    input  wire logic                         stream_req_i,
    input  wire logic [EVENT_ADDR_WIDTH-1:0]  stream_addr_i,
    output logic                              stream_valid_o,
    output logic [127:0]                      stream_data_o
);

`ifdef VERILATOR
    logic [31:0] memory [0:(MAX_EVENTS * 4)-1];

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i) begin
            ctrl_rvalid_o <= 1'b0;
            ctrl_rdata_o <= '0;
        end else begin
            ctrl_rvalid_o <= ctrl_en_i && !ctrl_write_i;
            if (ctrl_en_i) begin
                if (ctrl_write_i)
                    memory[ctrl_addr_i] <= ctrl_wdata_i;
                else
                    ctrl_rdata_o <= memory[ctrl_addr_i];
            end
        end
    end

    always_ff @(posedge stream_clk_i) begin
        if (!stream_rst_n_i) begin
            stream_valid_o <= 1'b0;
            stream_data_o <= '0;
        end else begin
            stream_valid_o <= stream_req_i;
            if (stream_req_i) begin
                stream_data_o <= {
                    memory[{stream_addr_i, 2'b11}],
                    memory[{stream_addr_i, 2'b10}],
                    memory[{stream_addr_i, 2'b01}],
                    memory[{stream_addr_i, 2'b00}]
                };
            end
        end
    end
`else
    logic [31:0]  xpm_ctrl_rdata;
    logic [127:0] xpm_stream_data;

    xpm_memory_tdpram #(
        .ADDR_WIDTH_A          (CTRL_ADDR_WIDTH),
        .ADDR_WIDTH_B          (EVENT_ADDR_WIDTH),
        .AUTO_SLEEP_TIME       (0),
        .BYTE_WRITE_WIDTH_A    (32),
        .BYTE_WRITE_WIDTH_B    (128),
        .CLOCKING_MODE         ("independent_clock"),
        .ECC_MODE              ("no_ecc"),
        .MEMORY_INIT_FILE      ("none"),
        .MEMORY_INIT_PARAM     ("0"),
        .MEMORY_OPTIMIZATION   ("true"),
        .MEMORY_PRIMITIVE      ("block"),
        .MEMORY_SIZE           (MAX_EVENTS * 128),
        .MESSAGE_CONTROL       (0),
        .READ_DATA_WIDTH_A     (32),
        .READ_DATA_WIDTH_B     (128),
        .READ_LATENCY_A        (1),
        .READ_LATENCY_B        (1),
        .READ_RESET_VALUE_A    ("0"),
        .READ_RESET_VALUE_B    ("0"),
        .RST_MODE_A            ("SYNC"),
        .RST_MODE_B            ("SYNC"),
        .SIM_ASSERT_CHK        (0),
        .USE_EMBEDDED_CONSTRAINT(0),
        .USE_MEM_INIT          (1),
        .WAKEUP_TIME           ("disable_sleep"),
        .WRITE_DATA_WIDTH_A    (32),
        .WRITE_DATA_WIDTH_B    (128),
        .WRITE_MODE_A          ("read_first"),
        .WRITE_MODE_B          ("read_first"),
        .WRITE_PROTECT         (1)
    ) u_event_memory (
        .clka           (ctrl_clk_i),
        .rsta           (!ctrl_rst_n_i),
        .ena            (ctrl_en_i),
        .regcea         (1'b1),
        .wea            (ctrl_en_i && ctrl_write_i),
        .addra          (ctrl_addr_i),
        .dina           (ctrl_wdata_i),
        .douta          (xpm_ctrl_rdata),
        .injectsbiterra (1'b0),
        .injectdbiterra (1'b0),
        .sbiterra       (),
        .dbiterra       (),

        .clkb           (stream_clk_i),
        .rstb           (!stream_rst_n_i),
        .enb            (stream_req_i),
        .regceb         (1'b1),
        .web            (1'b0),
        .addrb          (stream_addr_i),
        .dinb           ('0),
        .doutb          (xpm_stream_data),
        .injectsbiterrb (1'b0),
        .injectdbiterrb (1'b0),
        .sbiterrb       (),
        .dbiterrb       (),
        .sleep          (1'b0)
    );

    always_ff @(posedge ctrl_clk_i) begin
        if (!ctrl_rst_n_i)
            ctrl_rvalid_o <= 1'b0;
        else
            ctrl_rvalid_o <= ctrl_en_i && !ctrl_write_i;
    end

    always_ff @(posedge stream_clk_i) begin
        if (!stream_rst_n_i)
            stream_valid_o <= 1'b0;
        else
            stream_valid_o <= stream_req_i;
    end

    assign ctrl_rdata_o = xpm_ctrl_rdata;
    assign stream_data_o = xpm_stream_data;
`endif

endmodule

`default_nettype wire
