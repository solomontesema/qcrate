`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_event_cdc (
    input  wire logic src_clk_i,
    input  wire logic src_rst_n_i,
    input  wire logic done_pulse_i,
    input  wire logic aborted_pulse_i,
    input  wire logic fault_pulse_i,

    input  wire logic dst_clk_i,
    input  wire logic dst_rst_n_i,
    output logic      done_pulse_o,
    output logic      aborted_pulse_o,
    output logic      fault_pulse_o
);

    logic [2:0] event_toggle_src;
    logic [2:0] event_toggle_dst;
    logic [2:0] event_toggle_dst_q;

    always_ff @(posedge src_clk_i) begin
        if (!src_rst_n_i)
            event_toggle_src <= '0;
        else begin
            if (done_pulse_i)
                event_toggle_src[0] <= ~event_toggle_src[0];
            if (aborted_pulse_i)
                event_toggle_src[1] <= ~event_toggle_src[1];
            if (fault_pulse_i)
                event_toggle_src[2] <= ~event_toggle_src[2];
        end
    end

    genvar event_index;
    generate
        for (event_index = 0; event_index < 3; event_index++) begin : g_event
            qcrate_cdc_single u_event_sync (
                .src_clk_i    (src_clk_i),
                .src_signal_i (event_toggle_src[event_index]),
                .dst_clk_i    (dst_clk_i),
                .dst_rst_n_i  (dst_rst_n_i),
                .dst_signal_o (event_toggle_dst[event_index])
            );
        end
    endgenerate

    always_ff @(posedge dst_clk_i) begin
        if (!dst_rst_n_i) begin
            event_toggle_dst_q <= '0;
            done_pulse_o <= 1'b0;
            aborted_pulse_o <= 1'b0;
            fault_pulse_o <= 1'b0;
        end else begin
            done_pulse_o <= event_toggle_dst[0] ^ event_toggle_dst_q[0];
            aborted_pulse_o <= event_toggle_dst[1] ^ event_toggle_dst_q[1];
            fault_pulse_o <= event_toggle_dst[2] ^ event_toggle_dst_q[2];
            event_toggle_dst_q <= event_toggle_dst;
        end
    end

endmodule

`default_nettype wire
