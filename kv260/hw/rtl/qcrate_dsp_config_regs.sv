`timescale 1ns / 1ps
`default_nettype none

module qcrate_dsp_config_regs #(
    parameter logic [63:0] RESET_CONFIG_ID = 64'h5db4_fb57_8b27_b09f,
    parameter logic [31:0] RESET_SIGNAL_PHASE_INCREMENT = 32'h2666_6666,
    parameter logic [31:0] RESET_SIGNAL_PHASE_INITIAL = 32'h0000_0000,
    parameter logic [15:0] RESET_SIGNAL_AMPLITUDE = 16'h6000,
    parameter logic [15:0] RESET_NOISE_AMPLITUDE = 16'h0148,
    parameter logic [15:0] RESET_NOISE_SEED = 16'hace1,
    parameter logic [31:0] RESET_LO_PHASE_INCREMENT = 32'h251e_b852,
    parameter logic [31:0] RESET_LO_PHASE_INITIAL = 32'h0000_0000
) (
    input  wire logic        pclk_i,
    input  wire logic        presetn_i,

    input  wire logic [11:0] paddr_i,
    input  wire logic        psel_i,
    input  wire logic        penable_i,
    input  wire logic        pwrite_i,
    input  wire logic [31:0] pwdata_i,

    output logic [31:0]      prdata_o,
    output logic             pready_o,
    output logic             pslverr_o,

    output logic [63:0]      shadow_config_id_o,
    output logic [31:0]      shadow_signal_phase_increment_o,
    output logic [31:0]      shadow_signal_phase_initial_o,
    output logic [15:0]      shadow_signal_amplitude_o,
    output logic [15:0]      shadow_noise_amplitude_o,
    output logic [15:0]      shadow_noise_seed_o,
    output logic [31:0]      shadow_lo_phase_increment_o,
    output logic [31:0]      shadow_lo_phase_initial_o,
    output logic             commit_cmd_o,

    input  wire logic        commit_busy_i,
    input  wire logic        commit_accepted_i,
    input  wire logic        commit_rejected_i,
    input  wire logic [1:0]  commit_reject_reason_i,
    input  wire logic        active_valid_i,
    input  wire logic [31:0] active_generation_i,
    input  wire logic [63:0] active_config_id_i,
    input  wire logic [31:0] active_signal_phase_increment_i,
    input  wire logic [31:0] active_signal_phase_initial_i,
    input  wire logic [15:0] active_signal_amplitude_i,
    input  wire logic [15:0] active_noise_amplitude_i,
    input  wire logic [15:0] active_noise_seed_i,
    input  wire logic [31:0] active_lo_phase_increment_i,
    input  wire logic [31:0] active_lo_phase_initial_i
);

    localparam logic [31:0] UNMAPPED_PRDATA = 32'hdead_beef;

    localparam logic [11:0] ADDR_COMMAND = 12'h000;
    localparam logic [11:0] ADDR_STATUS = 12'h004;
    localparam logic [11:0] ADDR_REJECT_REASON = 12'h008;
    localparam logic [11:0] ADDR_ACTIVE_GENERATION = 12'h00c;
    localparam logic [11:0] ADDR_SHADOW_CONFIG_ID_LOW = 12'h010;
    localparam logic [11:0] ADDR_SHADOW_CONFIG_ID_HIGH = 12'h014;
    localparam logic [11:0] ADDR_SHADOW_SIGNAL_INCREMENT = 12'h018;
    localparam logic [11:0] ADDR_SHADOW_SIGNAL_INITIAL = 12'h01c;
    localparam logic [11:0] ADDR_SHADOW_SIGNAL_AMPLITUDE = 12'h020;
    localparam logic [11:0] ADDR_SHADOW_NOISE_AMPLITUDE = 12'h024;
    localparam logic [11:0] ADDR_SHADOW_NOISE_SEED = 12'h028;
    localparam logic [11:0] ADDR_SHADOW_LO_INCREMENT = 12'h02c;
    localparam logic [11:0] ADDR_SHADOW_LO_INITIAL = 12'h030;
    localparam logic [11:0] ADDR_ACTIVE_CONFIG_ID_LOW = 12'h040;
    localparam logic [11:0] ADDR_ACTIVE_CONFIG_ID_HIGH = 12'h044;
    localparam logic [11:0] ADDR_ACTIVE_SIGNAL_INCREMENT = 12'h048;
    localparam logic [11:0] ADDR_ACTIVE_SIGNAL_INITIAL = 12'h04c;
    localparam logic [11:0] ADDR_ACTIVE_SIGNAL_AMPLITUDE = 12'h050;
    localparam logic [11:0] ADDR_ACTIVE_NOISE_AMPLITUDE = 12'h054;
    localparam logic [11:0] ADDR_ACTIVE_NOISE_SEED = 12'h058;
    localparam logic [11:0] ADDR_ACTIVE_LO_INCREMENT = 12'h05c;
    localparam logic [11:0] ADDR_ACTIVE_LO_INITIAL = 12'h060;

    localparam logic [1:0] REJECT_NONE = 2'd0;
    localparam logic [1:0] REJECT_RESERVED_BITS = 2'd1;
    localparam logic [1:0] REJECT_MAILBOX_BUSY = 2'd3;

    logic apb_access;
    logic addr_mapped;
    logic write_allowed;
    logic write_value_valid;
    logic write_is_shadow;
    logic shadow_dirty_q;
    logic last_commit_rejected_q;
    logic [1:0] reject_reason_q;
    logic [31:0] status_word;
    logic [31:0] active_config_id_high_latch_q;

    assign apb_access = psel_i && penable_i;
    assign pready_o = 1'b1;
    assign status_word = {
        28'd0,
        last_commit_rejected_q,
        active_valid_i,
        commit_busy_i,
        shadow_dirty_q
    };

    always_comb begin
        addr_mapped = 1'b1;
        write_allowed = 1'b0;
        write_value_valid = 1'b1;
        write_is_shadow = 1'b0;
        prdata_o = UNMAPPED_PRDATA;

        unique case (paddr_i)
            ADDR_COMMAND: begin
                prdata_o = 32'd0;
                write_allowed = 1'b1;
                write_value_valid = (pwdata_i[31:2] == 0) &&
                                    (pwdata_i[1:0] != 2'b11) &&
                                    !(|pwdata_i[1:0] && commit_busy_i);
            end
            ADDR_STATUS: prdata_o = status_word;
            ADDR_REJECT_REASON: prdata_o = {30'd0, reject_reason_q};
            ADDR_ACTIVE_GENERATION: prdata_o = active_generation_i;
            ADDR_SHADOW_CONFIG_ID_LOW: begin
                prdata_o = shadow_config_id_o[31:0];
                write_allowed = !commit_busy_i;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_CONFIG_ID_HIGH: begin
                prdata_o = shadow_config_id_o[63:32];
                write_allowed = !commit_busy_i;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_SIGNAL_INCREMENT: begin
                prdata_o = shadow_signal_phase_increment_o;
                write_allowed = !commit_busy_i;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_SIGNAL_INITIAL: begin
                prdata_o = shadow_signal_phase_initial_o;
                write_allowed = !commit_busy_i;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_SIGNAL_AMPLITUDE: begin
                prdata_o = {16'd0, shadow_signal_amplitude_o};
                write_allowed = !commit_busy_i;
                write_value_valid = pwdata_i[31:16] == 0;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_NOISE_AMPLITUDE: begin
                prdata_o = {16'd0, shadow_noise_amplitude_o};
                write_allowed = !commit_busy_i;
                write_value_valid = pwdata_i[31:16] == 0;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_NOISE_SEED: begin
                prdata_o = {16'd0, shadow_noise_seed_o};
                write_allowed = !commit_busy_i;
                write_value_valid = pwdata_i[31:16] == 0;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_LO_INCREMENT: begin
                prdata_o = shadow_lo_phase_increment_o;
                write_allowed = !commit_busy_i;
                write_is_shadow = 1'b1;
            end
            ADDR_SHADOW_LO_INITIAL: begin
                prdata_o = shadow_lo_phase_initial_o;
                write_allowed = !commit_busy_i;
                write_is_shadow = 1'b1;
            end
            ADDR_ACTIVE_CONFIG_ID_LOW: prdata_o = active_config_id_i[31:0];
            ADDR_ACTIVE_CONFIG_ID_HIGH: prdata_o = active_config_id_high_latch_q;
            ADDR_ACTIVE_SIGNAL_INCREMENT:
                prdata_o = active_signal_phase_increment_i;
            ADDR_ACTIVE_SIGNAL_INITIAL: prdata_o = active_signal_phase_initial_i;
            ADDR_ACTIVE_SIGNAL_AMPLITUDE:
                prdata_o = {16'd0, active_signal_amplitude_i};
            ADDR_ACTIVE_NOISE_AMPLITUDE:
                prdata_o = {16'd0, active_noise_amplitude_i};
            ADDR_ACTIVE_NOISE_SEED: prdata_o = {16'd0, active_noise_seed_i};
            ADDR_ACTIVE_LO_INCREMENT: prdata_o = active_lo_phase_increment_i;
            ADDR_ACTIVE_LO_INITIAL: prdata_o = active_lo_phase_initial_i;
            default: addr_mapped = 1'b0;
        endcase
    end

    assign pslverr_o = apb_access &&
        (!addr_mapped || (pwrite_i && (!write_allowed || !write_value_valid)));

    always_ff @(posedge pclk_i) begin
        if (!presetn_i) begin
            shadow_config_id_o <= RESET_CONFIG_ID;
            shadow_signal_phase_increment_o <= RESET_SIGNAL_PHASE_INCREMENT;
            shadow_signal_phase_initial_o <= RESET_SIGNAL_PHASE_INITIAL;
            shadow_signal_amplitude_o <= RESET_SIGNAL_AMPLITUDE;
            shadow_noise_amplitude_o <= RESET_NOISE_AMPLITUDE;
            shadow_noise_seed_o <= RESET_NOISE_SEED;
            shadow_lo_phase_increment_o <= RESET_LO_PHASE_INCREMENT;
            shadow_lo_phase_initial_o <= RESET_LO_PHASE_INITIAL;
            shadow_dirty_q <= 1'b0;
            last_commit_rejected_q <= 1'b0;
            reject_reason_q <= REJECT_NONE;
            commit_cmd_o <= 1'b0;
            active_config_id_high_latch_q <= RESET_CONFIG_ID[63:32];
        end else begin
            commit_cmd_o <= 1'b0;

            if (apb_access && !pwrite_i &&
                (paddr_i == ADDR_ACTIVE_CONFIG_ID_LOW)) begin
                active_config_id_high_latch_q <= active_config_id_i[63:32];
            end

            if (commit_accepted_i) begin
                shadow_dirty_q <= 1'b0;
                last_commit_rejected_q <= 1'b0;
                reject_reason_q <= REJECT_NONE;
            end else if (commit_rejected_i) begin
                last_commit_rejected_q <= 1'b1;
                reject_reason_q <= commit_reject_reason_i;
            end

            if (apb_access && pwrite_i) begin
                if (addr_mapped && write_allowed && write_value_valid) begin
                    if (write_is_shadow)
                        shadow_dirty_q <= 1'b1;
                    unique case (paddr_i)
                        ADDR_COMMAND: begin
                            if (pwdata_i[0]) begin
                                commit_cmd_o <= 1'b1;
                            end else if (pwdata_i[1]) begin
                                shadow_config_id_o <= active_config_id_i;
                                shadow_signal_phase_increment_o <=
                                    active_signal_phase_increment_i;
                                shadow_signal_phase_initial_o <=
                                    active_signal_phase_initial_i;
                                shadow_signal_amplitude_o <=
                                    active_signal_amplitude_i;
                                shadow_noise_amplitude_o <= active_noise_amplitude_i;
                                shadow_noise_seed_o <= active_noise_seed_i;
                                shadow_lo_phase_increment_o <=
                                    active_lo_phase_increment_i;
                                shadow_lo_phase_initial_o <=
                                    active_lo_phase_initial_i;
                                shadow_dirty_q <= 1'b0;
                                last_commit_rejected_q <= 1'b0;
                                reject_reason_q <= REJECT_NONE;
                            end
                        end
                        ADDR_SHADOW_CONFIG_ID_LOW:
                            shadow_config_id_o[31:0] <= pwdata_i;
                        ADDR_SHADOW_CONFIG_ID_HIGH:
                            shadow_config_id_o[63:32] <= pwdata_i;
                        ADDR_SHADOW_SIGNAL_INCREMENT:
                            shadow_signal_phase_increment_o <= pwdata_i;
                        ADDR_SHADOW_SIGNAL_INITIAL:
                            shadow_signal_phase_initial_o <= pwdata_i;
                        ADDR_SHADOW_SIGNAL_AMPLITUDE:
                            shadow_signal_amplitude_o <= pwdata_i[15:0];
                        ADDR_SHADOW_NOISE_AMPLITUDE:
                            shadow_noise_amplitude_o <= pwdata_i[15:0];
                        ADDR_SHADOW_NOISE_SEED:
                            shadow_noise_seed_o <= pwdata_i[15:0];
                        ADDR_SHADOW_LO_INCREMENT:
                            shadow_lo_phase_increment_o <= pwdata_i;
                        ADDR_SHADOW_LO_INITIAL:
                            shadow_lo_phase_initial_o <= pwdata_i;
                        default: begin
                        end
                    endcase
                end else if (commit_busy_i &&
                             (write_is_shadow ||
                              ((paddr_i == ADDR_COMMAND) &&
                               (|pwdata_i[1:0])))) begin
                    last_commit_rejected_q <= 1'b1;
                    reject_reason_q <= REJECT_MAILBOX_BUSY;
                end else if (addr_mapped && write_allowed) begin
                    last_commit_rejected_q <= 1'b1;
                    reject_reason_q <= REJECT_RESERVED_BITS;
                end
            end
        end
    end

endmodule

`default_nettype wire
