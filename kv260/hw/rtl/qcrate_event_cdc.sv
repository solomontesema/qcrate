`timescale 1ns / 1ps
`default_nettype none

module qcrate_event_cdc (
    input  wire logic src_clk_i,
    input  wire logic src_rst_n_i,
    input  wire logic frame_done_pulse_i,
    input  wire logic stream_error_pulse_i,

    input  wire logic dst_clk_i,
    input  wire logic dst_rst_n_i,
    output logic      frame_done_pulse_o,
    output logic      stream_error_pulse_o
);

    logic frame_done_toggle_src;
    logic stream_error_toggle_src;
    logic frame_done_toggle_dst;
    logic stream_error_toggle_dst;
    logic frame_done_toggle_dst_q;
    logic stream_error_toggle_dst_q;

    always_ff @(posedge src_clk_i) begin
        if (!src_rst_n_i) begin
            frame_done_toggle_src <= 1'b0;
            stream_error_toggle_src <= 1'b0;
        end else begin
            if (frame_done_pulse_i) begin
                frame_done_toggle_src <= ~frame_done_toggle_src;
            end
            if (stream_error_pulse_i) begin
                stream_error_toggle_src <= ~stream_error_toggle_src;
            end
        end
    end

    qcrate_cdc_single u_frame_done_sync (
        .src_clk_i      (src_clk_i),
        .src_signal_i   (frame_done_toggle_src),
        .dst_clk_i      (dst_clk_i),
        .dst_rst_n_i    (dst_rst_n_i),
        .dst_signal_o   (frame_done_toggle_dst)
    );

    qcrate_cdc_single u_stream_error_sync (
        .src_clk_i      (src_clk_i),
        .src_signal_i   (stream_error_toggle_src),
        .dst_clk_i      (dst_clk_i),
        .dst_rst_n_i    (dst_rst_n_i),
        .dst_signal_o   (stream_error_toggle_dst)
    );

    always_ff @(posedge dst_clk_i) begin
        if (!dst_rst_n_i) begin
            frame_done_toggle_dst_q <= 1'b0;
            stream_error_toggle_dst_q <= 1'b0;
            frame_done_pulse_o <= 1'b0;
            stream_error_pulse_o <= 1'b0;
        end else begin
            frame_done_pulse_o <= frame_done_toggle_dst ^ frame_done_toggle_dst_q;
            stream_error_pulse_o <= stream_error_toggle_dst ^ stream_error_toggle_dst_q;
            frame_done_toggle_dst_q <= frame_done_toggle_dst;
            stream_error_toggle_dst_q <= stream_error_toggle_dst;
        end
    end

endmodule

`default_nettype wire
