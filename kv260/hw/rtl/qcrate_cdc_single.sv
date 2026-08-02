`timescale 1ns / 1ps
`default_nettype none

module qcrate_cdc_single (
    input  wire logic src_clk_i,
    input  wire logic src_signal_i,
    input  wire logic dst_clk_i,
    input  wire logic dst_rst_n_i,
    output logic      dst_signal_o
);

`ifdef VERILATOR
    logic sync_ff1;
    logic sync_ff2;

    always_ff @(posedge dst_clk_i) begin
        if (!dst_rst_n_i) begin
            sync_ff1 <= 1'b0;
            sync_ff2 <= 1'b0;
        end else begin
            sync_ff1 <= src_signal_i;
            sync_ff2 <= sync_ff1;
        end
    end

    assign dst_signal_o = sync_ff2;

    logic unused_src_clk;
    assign unused_src_clk = src_clk_i;
`else
    xpm_cdc_single #(
        .DEST_SYNC_FF   (2),
        .INIT_SYNC_FF   (0),
        .SIM_ASSERT_CHK (0),
        .SRC_INPUT_REG  (0)
    ) u_xpm_cdc_single (
        .src_clk        (src_clk_i),
        .src_in         (src_signal_i),
        .dest_clk       (dst_clk_i),
        .dest_out       (dst_signal_o)
    );

    logic unused_dst_rst_n;
    assign unused_dst_rst_n = dst_rst_n_i;
`endif

endmodule

`default_nettype wire
