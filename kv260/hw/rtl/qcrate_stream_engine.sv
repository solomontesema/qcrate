`timescale 1ns / 1ps
`default_nettype none

module qcrate_stream_engine #(
    parameter int AXIS_DATA_WIDTH = 32
) (
    input  wire logic                         clk_i,
    input  wire logic                         rst_n_i,

    input  wire logic                         start_i,
    input  wire logic                         abort_i,
    input  wire logic                         soft_reset_i,

    input  wire logic [31:0]                  frame_length_i,
    input  wire logic [31:0]                  frame_count_i,
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire logic [31:0]                  stream_mode_i,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire logic                         continuous_i,

    output logic                              busy_o,
    output logic                              done_pulse_o,
    output logic                              error_pulse_o,

    output logic [31:0]                       completed_frames_o,
    output logic [31:0]                       current_frame_id_o,
    output logic [31:0]                       current_sample_index_o,
    output logic [31:0]                       stall_cycles_o,

    output logic [AXIS_DATA_WIDTH-1:0]        m_axis_tdata_o,
    output logic [(AXIS_DATA_WIDTH/8)-1:0]    m_axis_tkeep_o,
    output logic                              m_axis_tvalid_o,
    input  wire logic                         m_axis_tready_i,
    output logic                              m_axis_tlast_o
);

    logic [31:0] active_frame_length;
    logic [31:0] active_frame_count;
    logic        active_continuous;
    logic        abort_pending;

    logic        axis_fire;
    logic        final_sample;
    logic        final_frame;
    logic [31:0] next_sample_index;
    logic [31:0] next_frame_id;

    assign axis_fire = m_axis_tvalid_o && m_axis_tready_i;
    assign final_sample = current_sample_index_o == (active_frame_length - 32'd1);
    assign final_frame = !active_continuous &&
                         ((completed_frames_o + 32'd1) >= active_frame_count);
    assign next_sample_index = current_sample_index_o + 32'd1;
    assign next_frame_id = current_frame_id_o + 32'd1;

    assign m_axis_tkeep_o = {(AXIS_DATA_WIDTH/8){1'b1}};

    function automatic logic [AXIS_DATA_WIDTH-1:0] pack_stream_word(
        input logic [15:0] frame_id,
        input logic [15:0] sample_index
    );
        logic [AXIS_DATA_WIDTH-1:0] word;
        begin
            word = '0;
            word[31:16] = frame_id;
            word[15:0] = sample_index;
            return word;
        end
    endfunction

    task automatic clear_engine;
        begin
            active_frame_length <= 32'h0000_0000;
            active_frame_count <= 32'h0000_0000;
            active_continuous <= 1'b0;
            abort_pending <= 1'b0;
            busy_o <= 1'b0;
            completed_frames_o <= 32'h0000_0000;
            current_frame_id_o <= 32'h0000_0000;
            current_sample_index_o <= 32'h0000_0000;
            stall_cycles_o <= 32'h0000_0000;
            m_axis_tdata_o <= '0;
            m_axis_tvalid_o <= 1'b0;
            m_axis_tlast_o <= 1'b0;
        end
    endtask

    always_ff @(posedge clk_i) begin
        if (!rst_n_i) begin
            clear_engine();
            done_pulse_o <= 1'b0;
            error_pulse_o <= 1'b0;
        end else begin
            done_pulse_o <= 1'b0;
            error_pulse_o <= 1'b0;

            if (soft_reset_i) begin
                clear_engine();
            end else begin
                if (m_axis_tvalid_o && !m_axis_tready_i) begin
                    stall_cycles_o <= stall_cycles_o + 32'd1;
                end

                if (abort_i && busy_o) begin
                    abort_pending <= 1'b1;
                end

                if (axis_fire) begin
                    if (abort_pending || abort_i) begin
                        busy_o <= 1'b0;
                        abort_pending <= 1'b0;
                        m_axis_tvalid_o <= 1'b0;
                        m_axis_tlast_o <= 1'b0;
                    end else if (final_sample) begin
                        completed_frames_o <= completed_frames_o + 32'd1;

                        if (final_frame) begin
                            busy_o <= 1'b0;
                            done_pulse_o <= 1'b1;
                            m_axis_tvalid_o <= 1'b0;
                            m_axis_tlast_o <= 1'b0;
                        end else begin
                            current_frame_id_o <= next_frame_id;
                            current_sample_index_o <= 32'h0000_0000;
                            m_axis_tdata_o <= pack_stream_word(next_frame_id[15:0], 16'd0);
                            m_axis_tlast_o <= (active_frame_length == 32'd1);
                        end
                    end else begin
                        current_sample_index_o <= next_sample_index;
                        m_axis_tdata_o <= pack_stream_word(current_frame_id_o[15:0],
                                                           next_sample_index[15:0]);
                        m_axis_tlast_o <= (next_sample_index ==
                                           (active_frame_length - 32'd1));
                    end
                end

                if (!busy_o && start_i) begin
                    if ((frame_length_i == 32'd0) ||
                        (!continuous_i && (frame_count_i == 32'd0))) begin
                        error_pulse_o <= 1'b1;
                    end else begin
                        active_frame_length <= frame_length_i;
                        active_frame_count <= frame_count_i;
                        active_continuous <= continuous_i;
                        abort_pending <= 1'b0;
                        busy_o <= 1'b1;
                        completed_frames_o <= 32'h0000_0000;
                        current_frame_id_o <= 32'h0000_0000;
                        current_sample_index_o <= 32'h0000_0000;
                        stall_cycles_o <= 32'h0000_0000;
                        m_axis_tdata_o <= pack_stream_word(16'd0, 16'd0);
                        m_axis_tvalid_o <= 1'b1;
                        m_axis_tlast_o <= (frame_length_i == 32'd1);
                    end
                end
            end
        end
    end

endmodule

`default_nettype wire
