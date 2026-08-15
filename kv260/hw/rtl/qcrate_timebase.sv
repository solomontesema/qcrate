`timescale 1ns / 1ps
`default_nettype none

module qcrate_timebase #(
    parameter int WIDTH = 64
) (
    input  wire logic             clk_i,
    input  wire logic             rst_n_i,
    output logic [WIDTH-1:0]      time_o
);

    always_ff @(posedge clk_i) begin
        if (!rst_n_i)
            time_o <= '0;
        else
            time_o <= time_o + {{(WIDTH-1){1'b0}}, 1'b1};
    end

endmodule

`default_nettype wire
