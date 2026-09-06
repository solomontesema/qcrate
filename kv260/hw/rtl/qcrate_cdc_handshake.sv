`timescale 1ns / 1ps
`default_nettype none

// Pulse-oriented wrapper around Xilinx's acknowledged multi-bit CDC primitive.
// Source data is accepted only while idle and remains owned by the wrapper until
// the destination acknowledges it. Destination data is stable while valid.
module qcrate_cdc_handshake #(
    parameter int WIDTH = 1
) (
    input  wire logic                 src_clk_i,
    input  wire logic                 src_rst_n_i,
    input  wire logic [WIDTH-1:0]     src_data_i,
    input  wire logic                 src_send_i,
    output logic                      src_busy_o,

    input  wire logic                 dest_clk_i,
    input  wire logic                 dest_rst_n_i,
    output logic [WIDTH-1:0]          dest_data_o,
    output logic                      dest_valid_o,
    input  wire logic                 dest_accept_i
);

    logic [WIDTH-1:0] src_data_q;
    logic             src_send_q;
    logic             src_rcv;
    logic [WIDTH-1:0] xpm_dest_data;
    logic             xpm_dest_req;
    logic             dest_ack_q;

    assign dest_data_o = xpm_dest_data;
    assign dest_valid_o = xpm_dest_req && !dest_ack_q;

    always_ff @(posedge src_clk_i) begin
        if (!src_rst_n_i) begin
            src_data_q <= '0;
            src_send_q <= 1'b0;
            src_busy_o <= 1'b0;
        end else begin
            if (!src_busy_o && src_send_i) begin
                src_data_q <= src_data_i;
                src_send_q <= 1'b1;
                src_busy_o <= 1'b1;
            end else if (src_send_q && src_rcv) begin
                src_send_q <= 1'b0;
            end else if (src_busy_o && !src_send_q && !src_rcv) begin
                src_busy_o <= 1'b0;
            end
        end
    end

    always_ff @(posedge dest_clk_i) begin
        if (!dest_rst_n_i) begin
            dest_ack_q <= 1'b0;
        end else if (!xpm_dest_req) begin
            dest_ack_q <= 1'b0;
        end else if (dest_valid_o && dest_accept_i) begin
            dest_ack_q <= 1'b1;
        end
    end

`ifdef VERILATOR
    logic src_send_sync_ff1;
    logic src_send_sync_ff2;
    logic dest_ack_sync_ff1;
    logic dest_ack_sync_ff2;
    logic xpm_dest_req_q;
    logic [WIDTH-1:0] xpm_dest_data_q;

    assign src_rcv = dest_ack_sync_ff2;
    assign xpm_dest_req = xpm_dest_req_q;
    assign xpm_dest_data = xpm_dest_data_q;

    always_ff @(posedge dest_clk_i) begin
        if (!dest_rst_n_i) begin
            src_send_sync_ff1 <= 1'b0;
            src_send_sync_ff2 <= 1'b0;
            xpm_dest_req_q <= 1'b0;
            xpm_dest_data_q <= '0;
        end else begin
            src_send_sync_ff1 <= src_send_q;
            src_send_sync_ff2 <= src_send_sync_ff1;
            xpm_dest_req_q <= src_send_sync_ff2;
            if (src_send_sync_ff2 && !xpm_dest_req_q)
                xpm_dest_data_q <= src_data_q;
        end
    end

    always_ff @(posedge src_clk_i) begin
        if (!src_rst_n_i) begin
            dest_ack_sync_ff1 <= 1'b0;
            dest_ack_sync_ff2 <= 1'b0;
        end else begin
            dest_ack_sync_ff1 <= dest_ack_q;
            dest_ack_sync_ff2 <= dest_ack_sync_ff1;
        end
    end
`else
    xpm_cdc_handshake #(
        .DEST_EXT_HSK   (1),
        .DEST_SYNC_FF   (2),
        .INIT_SYNC_FF   (0),
        .SIM_ASSERT_CHK (0),
        .SRC_SYNC_FF    (2),
        .WIDTH          (WIDTH)
    ) u_xpm_cdc_handshake (
        .src_clk        (src_clk_i),
        .src_in         (src_data_q),
        .src_send       (src_send_q),
        .src_rcv        (src_rcv),
        .dest_clk       (dest_clk_i),
        .dest_out       (xpm_dest_data),
        .dest_req       (xpm_dest_req),
        .dest_ack       (dest_ack_q)
    );
`endif

    initial begin
        if (WIDTH < 1)
            $fatal(1, "WIDTH must be positive");
    end

endmodule

`default_nettype wire
