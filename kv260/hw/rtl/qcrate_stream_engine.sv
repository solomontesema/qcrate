`timescale 1ns / 1ps
`default_nettype none

module qcrate_stream_engine #(
    parameter int AXIS_DATA_WIDTH = 32
) (
    input  wire logic                         clk_i,
    input  wire logic                         rst_n_i,

    input  wire logic                         start_i,
    input  wire logic                         arm_triggered_i,
    input  wire logic                         shot_trigger_i,
    input  wire logic                         abort_i,
    input  wire logic                         soft_reset_i,
    input  wire logic [31:0]                  trigger_shot_id_i,
    input  wire logic [63:0]                  timebase_i,

    input  wire logic [31:0]                  frame_length_i,
    input  wire logic [31:0]                  frame_count_i,
    input  wire logic [31:0]                  stream_mode_i,
    input  wire logic                         continuous_i,

    input  wire logic [AXIS_DATA_WIDTH-1:0]  dsp_tdata_i,
    input  wire logic                         dsp_tvalid_i,
    output logic                              dsp_enable_o,
    output logic                              dsp_tready_o,

    output logic                              busy_o,
    output logic                              armed_o,
    output logic                              capture_start_pulse_o,
    output logic                              done_pulse_o,
    output logic                              error_pulse_o,

    output logic                              trigger_seen_o,
    output logic                              first_sample_time_valid_o,
    output logic [31:0]                       trigger_shot_id_o,
    output logic [31:0]                       trigger_count_o,
    output logic [31:0]                       missed_trigger_count_o,
    output logic [63:0]                       trigger_time_o,
    output logic [63:0]                       first_sample_time_o,

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
    logic [31:0] active_stream_mode;
    logic        active_continuous;
    logic        abort_pending;

    logic        axis_fire;
    logic        final_sample;
    logic        final_frame;
    logic [31:0] next_sample_index;
    logic [31:0] next_frame_id;

    function automatic logic configuration_valid;
        configuration_valid =
            (frame_length_i != 32'd0) &&
            (continuous_i || (frame_count_i != 32'd0)) &&
            (stream_mode_i <= 32'd1);
    endfunction

    assign axis_fire = m_axis_tvalid_o && m_axis_tready_i;
    assign final_sample = current_sample_index_o == (active_frame_length - 32'd1);
    assign final_frame = !active_continuous &&
                         ((completed_frames_o + 32'd1) >= active_frame_count);
    assign next_sample_index = current_sample_index_o + 32'd1;
    assign next_frame_id = current_frame_id_o + 32'd1;

    assign m_axis_tkeep_o = {(AXIS_DATA_WIDTH/8){1'b1}};
    assign m_axis_tdata_o = (active_stream_mode == 32'd1) ?
                            dsp_tdata_i :
                            pack_stream_word(current_frame_id_o[15:0],
                                             current_sample_index_o[15:0]);
    assign m_axis_tvalid_o = busy_o &&
                             ((active_stream_mode == 32'd1) ?
                              dsp_tvalid_i : 1'b1);
    assign m_axis_tlast_o = busy_o && final_sample;
    assign dsp_enable_o = busy_o && (active_stream_mode == 32'd1);
    assign dsp_tready_o = dsp_enable_o && m_axis_tready_i;

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
            active_stream_mode <= 32'h0000_0000;
            active_continuous <= 1'b0;
            abort_pending <= 1'b0;
            busy_o <= 1'b0;
            armed_o <= 1'b0;
            trigger_seen_o <= 1'b0;
            first_sample_time_valid_o <= 1'b0;
            trigger_shot_id_o <= 32'd0;
            trigger_count_o <= 32'd0;
            missed_trigger_count_o <= 32'd0;
            trigger_time_o <= 64'd0;
            first_sample_time_o <= 64'd0;
            completed_frames_o <= 32'h0000_0000;
            current_frame_id_o <= 32'h0000_0000;
            current_sample_index_o <= 32'h0000_0000;
            stall_cycles_o <= 32'h0000_0000;
        end
    endtask

    always_ff @(posedge clk_i) begin
        if (!rst_n_i) begin
            clear_engine();
            capture_start_pulse_o <= 1'b0;
            done_pulse_o <= 1'b0;
            error_pulse_o <= 1'b0;
        end else begin
            capture_start_pulse_o <= 1'b0;
            done_pulse_o <= 1'b0;
            error_pulse_o <= 1'b0;

            if (soft_reset_i) begin
                clear_engine();
            end else begin
                if (shot_trigger_i && !armed_o)
                    missed_trigger_count_o <= missed_trigger_count_o + 32'd1;

                if (m_axis_tvalid_o && !m_axis_tready_i) begin
                    stall_cycles_o <= stall_cycles_o + 32'd1;
                end

                if (abort_i && armed_o) begin
                    armed_o <= 1'b0;
                    busy_o <= 1'b1;
                    active_stream_mode <= 32'd0;
                    active_continuous <= 1'b0;
                    abort_pending <= 1'b0;
                    completed_frames_o <= 32'd0;
                    current_frame_id_o <= 32'd0;
                    current_sample_index_o <= 32'd0;
                    stall_cycles_o <= 32'd0;
                end else if (abort_i && busy_o && active_continuous) begin
                    abort_pending <= 1'b1;
                end

                if (axis_fire) begin
                    if (!first_sample_time_valid_o) begin
                        first_sample_time_o <= timebase_i;
                        first_sample_time_valid_o <= 1'b1;
                    end

                    if (abort_pending || (abort_i && active_continuous)) begin
                        busy_o <= 1'b0;
                        abort_pending <= 1'b0;
                    end else if (final_sample) begin
                        completed_frames_o <= completed_frames_o + 32'd1;

                        if (final_frame) begin
                            busy_o <= 1'b0;
                            done_pulse_o <= 1'b1;
                        end else begin
                            current_frame_id_o <= next_frame_id;
                            current_sample_index_o <= 32'h0000_0000;
                        end
                    end else begin
                        current_sample_index_o <= next_sample_index;
                    end
                end

                if (!busy_o && !armed_o && (start_i || arm_triggered_i)) begin
                    if ((start_i && arm_triggered_i) || !configuration_valid()) begin
                        error_pulse_o <= 1'b1;
                    end else begin
                        active_frame_length <= frame_length_i;
                        active_frame_count <= frame_count_i;
                        active_stream_mode <= stream_mode_i;
                        active_continuous <= continuous_i;
                        abort_pending <= 1'b0;
                        busy_o <= start_i;
                        armed_o <= arm_triggered_i;
                        capture_start_pulse_o <= start_i;
                        trigger_seen_o <= 1'b0;
                        first_sample_time_valid_o <= 1'b0;
                        trigger_shot_id_o <= 32'd0;
                        trigger_time_o <= 64'd0;
                        first_sample_time_o <= 64'd0;
                        completed_frames_o <= 32'h0000_0000;
                        current_frame_id_o <= 32'h0000_0000;
                        current_sample_index_o <= 32'h0000_0000;
                        stall_cycles_o <= 32'h0000_0000;
                    end
                end else if (armed_o && shot_trigger_i && !abort_i) begin
                    armed_o <= 1'b0;
                    busy_o <= 1'b1;
                    capture_start_pulse_o <= 1'b1;
                    trigger_seen_o <= 1'b1;
                    trigger_shot_id_o <= trigger_shot_id_i;
                    trigger_count_o <= trigger_count_o + 32'd1;
                    trigger_time_o <= timebase_i;
                end
            end
        end
    end

endmodule

`default_nettype wire
