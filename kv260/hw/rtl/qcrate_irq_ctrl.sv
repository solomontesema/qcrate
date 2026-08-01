`timescale 1ns / 1ps
`default_nettype none

module qcrate_irq_ctrl (
    input  wire logic        clk_i,
    input  wire logic        rst_n_i,

    input  wire logic [31:0] events_i,
    input  wire logic [31:0] enable_i,
    input  wire logic [31:0] clear_i,

    output logic [31:0]      status_o,
    output logic             irq_o
);

    assign irq_o = |(status_o & enable_i);

    always_ff @(posedge clk_i) begin
        if (!rst_n_i) begin
            status_o <= 32'h0000_0000;
        end else begin
            status_o <= (status_o & ~clear_i) | events_i; //keeps sticky status with event-wins-over-clear precedence
        end
    end

endmodule

`default_nettype wire
