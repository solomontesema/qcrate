`timescale 1ns / 1ps
`default_nettype none

module qcrate_sequence_engine #(
    parameter int MAX_EVENTS = 128,
    parameter int EVENT_ADDR_WIDTH = $clog2(MAX_EVENTS)
) (
    input  wire logic                        clk_i,
    input  wire logic                        rst_n_i,

    input  wire logic                        arm_i,
    input  wire logic                        start_i,
    input  wire logic                        abort_i,
    input  wire logic                        soft_reset_i,
    input  wire logic                        external_trigger_enable_i,
    input  wire logic                        external_trigger_i,
    input  wire logic [EVENT_ADDR_WIDTH:0]   event_count_i,
    input  wire logic [63:0]                 timebase_i,

    /* Ordered, fixed one-cycle response memory interface. */
    output logic                             event_req_o,
    output logic [EVENT_ADDR_WIDTH-1:0]      event_addr_o,
    input  wire logic                        event_valid_i,
    input  wire logic [127:0]                event_data_i,

    output logic                             idle_o,
    output logic                             validating_o,
    output logic                             armed_o,
    output logic                             busy_o,
    output logic                             faulted_o,

    output logic                             done_pulse_o,
    output logic                             aborted_pulse_o,
    output logic                             fault_pulse_o,
    output logic [7:0]                       fault_code_o,
    output logic [EVENT_ADDR_WIDTH-1:0]      fault_event_index_o,

    output logic [EVENT_ADDR_WIDTH-1:0]      active_event_index_o,
    output logic [31:0]                      completed_shots_o,
    output logic [63:0]                      start_time_o,
    output logic [63:0]                      elapsed_tick_o,
    output logic [1:0]                       pulse_o
);

    localparam logic [7:0] FAULT_NONE             = 8'd0;
    localparam logic [7:0] FAULT_BAD_EVENT_COUNT  = 8'd1;
    localparam logic [7:0] FAULT_TIME_ORDER       = 8'd2;
    localparam logic [7:0] FAULT_RESERVED_STATE   = 8'd3;
    localparam logic [7:0] FAULT_RESERVED_FLAGS   = 8'd4;
    localparam logic [7:0] FAULT_NO_STATE_CHANGE  = 8'd5;
    localparam logic [7:0] FAULT_UNSAFE_FINAL     = 8'd6;
    localparam logic [7:0] FAULT_MEMORY_UNDERFLOW = 8'd7;
    localparam logic [7:0] FAULT_MEMORY_PROTOCOL  = 8'd8;
    localparam logic [7:0] FAULT_ILLEGAL_COMMAND  = 8'd9;

    localparam logic [EVENT_ADDR_WIDTH:0] MIN_EVENTS = 2;
    localparam logic [EVENT_ADDR_WIDTH:0] MAX_EVENT_COUNT =
        (EVENT_ADDR_WIDTH + 1)'(MAX_EVENTS);

    typedef enum logic [2:0] {
        STATE_IDLE,
        STATE_VALIDATE,
        STATE_PREFETCH,
        STATE_ARMED,
        STATE_RUN,
        STATE_FAULT
    } state_t;

    state_t state_q;

    logic [EVENT_ADDR_WIDTH:0] event_count_q;
    logic [EVENT_ADDR_WIDTH:0] validate_req_index_q;
    logic [EVENT_ADDR_WIDTH:0] validate_rsp_index_q;
    logic [EVENT_ADDR_WIDTH:0] prefetch_req_index_q;
    logic [EVENT_ADDR_WIDTH:0] prefetch_rsp_index_q;
    logic [EVENT_ADDR_WIDTH:0] fetch_index_q;

    logic [63:0] previous_timestamp_q;
    logic [1:0]  previous_state_q;

    logic [65:0] current_event_q;
    logic [65:0] next_event_q;
    logic         current_event_valid_q;
    logic         next_event_valid_q;

    logic [63:0] current_timestamp;
    logic [1:0]  current_state;
    logic        current_due;
    logic [EVENT_ADDR_WIDTH:0] active_event_number_next;

    logic [63:0] response_timestamp;
    logic [31:0] response_state_word;
    logic [31:0] response_flags;

    assign current_timestamp = current_event_q[63:0];
    assign current_state = current_event_q[65:64];
    assign current_due = current_event_valid_q &&
                         (current_timestamp == elapsed_tick_o);
    assign active_event_number_next =
        {1'b0, active_event_index_o} + 1'b1;

    assign response_timestamp = event_data_i[63:0];
    assign response_state_word = event_data_i[95:64];
    assign response_flags = event_data_i[127:96];

    assign idle_o = state_q == STATE_IDLE;
    assign validating_o = (state_q == STATE_VALIDATE) ||
                          (state_q == STATE_PREFETCH);
    assign armed_o = state_q == STATE_ARMED;
    assign busy_o = state_q == STATE_RUN;
    assign faulted_o = state_q == STATE_FAULT;

    always_comb begin
        event_req_o = 1'b0;
        event_addr_o = '0;

        unique case (state_q)
            STATE_VALIDATE: begin
                if (validate_req_index_q < event_count_q) begin
                    event_req_o = 1'b1;
                    event_addr_o = validate_req_index_q[EVENT_ADDR_WIDTH-1:0];
                end
            end

            STATE_PREFETCH: begin
                if (prefetch_req_index_q < MIN_EVENTS) begin
                    event_req_o = 1'b1;
                    event_addr_o = prefetch_req_index_q[EVENT_ADDR_WIDTH-1:0];
                end
            end

            STATE_RUN: begin
                if (current_due &&
                    (active_event_number_next < event_count_q) &&
                    (fetch_index_q < event_count_q)) begin
                    event_req_o = 1'b1;
                    event_addr_o = fetch_index_q[EVENT_ADDR_WIDTH-1:0];
                end
            end

            default: begin
            end
        endcase
    end

    task automatic clear_sequence;
        begin
            state_q <= STATE_IDLE;
            event_count_q <= '0;
            validate_req_index_q <= '0;
            validate_rsp_index_q <= '0;
            prefetch_req_index_q <= '0;
            prefetch_rsp_index_q <= '0;
            fetch_index_q <= '0;
            previous_timestamp_q <= '0;
            previous_state_q <= '0;
            current_event_q <= '0;
            next_event_q <= '0;
            current_event_valid_q <= 1'b0;
            next_event_valid_q <= 1'b0;
            fault_code_o <= FAULT_NONE;
            fault_event_index_o <= '0;
            active_event_index_o <= '0;
            completed_shots_o <= '0;
            start_time_o <= '0;
            elapsed_tick_o <= '0;
            pulse_o <= 2'b00;
        end
    endtask

    task automatic enter_fault(
        input logic [7:0] code,
        input logic [EVENT_ADDR_WIDTH-1:0] event_index
    );
        begin
            state_q <= STATE_FAULT;
            fault_code_o <= code;
            fault_event_index_o <= event_index;
            current_event_valid_q <= 1'b0;
            next_event_valid_q <= 1'b0;
            pulse_o <= 2'b00;
            fault_pulse_o <= 1'b1;
        end
    endtask

    always_ff @(posedge clk_i) begin
        if (!rst_n_i) begin
            done_pulse_o <= 1'b0;
            aborted_pulse_o <= 1'b0;
            fault_pulse_o <= 1'b0;
            clear_sequence();
        end else begin
            done_pulse_o <= 1'b0;
            aborted_pulse_o <= 1'b0;
            fault_pulse_o <= 1'b0;

            if (soft_reset_i) begin
                clear_sequence();
            end else if ((state_q != STATE_FAULT) &&
                         ((arm_i && (state_q != STATE_IDLE)) ||
                          (start_i && (state_q != STATE_ARMED)))) begin
                enter_fault(FAULT_ILLEGAL_COMMAND, active_event_index_o);
            end else if (abort_i &&
                         (state_q != STATE_IDLE) &&
                         (state_q != STATE_FAULT)) begin
                state_q <= STATE_IDLE;
                current_event_valid_q <= 1'b0;
                next_event_valid_q <= 1'b0;
                pulse_o <= 2'b00;
                aborted_pulse_o <= 1'b1;
            end else begin
                unique case (state_q)
                    STATE_IDLE: begin
                        if (arm_i) begin
                            if ((event_count_i < MIN_EVENTS) ||
                                (event_count_i > MAX_EVENT_COUNT)) begin
                                enter_fault(FAULT_BAD_EVENT_COUNT, '0);
                            end else begin
                                event_count_q <= event_count_i;
                                validate_req_index_q <= '0;
                                validate_rsp_index_q <= '0;
                                previous_timestamp_q <= '0;
                                previous_state_q <= 2'b00;
                                fault_code_o <= FAULT_NONE;
                                fault_event_index_o <= '0;
                                active_event_index_o <= '0;
                                current_event_valid_q <= 1'b0;
                                next_event_valid_q <= 1'b0;
                                pulse_o <= 2'b00;
                                state_q <= STATE_VALIDATE;
                            end
                        end
                    end

                    STATE_VALIDATE: begin
                        if (event_req_o)
                            validate_req_index_q <= validate_req_index_q + 1'b1;

                        if (event_valid_i) begin
                            if (response_state_word[31:2] != '0) begin
                                enter_fault(FAULT_RESERVED_STATE,
                                    validate_rsp_index_q[EVENT_ADDR_WIDTH-1:0]);
                            end else if (response_flags != 32'd0) begin
                                enter_fault(FAULT_RESERVED_FLAGS,
                                    validate_rsp_index_q[EVENT_ADDR_WIDTH-1:0]);
                            end else if (response_state_word[1:0] ==
                                         previous_state_q) begin
                                enter_fault(FAULT_NO_STATE_CHANGE,
                                    validate_rsp_index_q[EVENT_ADDR_WIDTH-1:0]);
                            end else if ((validate_rsp_index_q != 0) &&
                                         (response_timestamp <=
                                          previous_timestamp_q)) begin
                                enter_fault(FAULT_TIME_ORDER,
                                    validate_rsp_index_q[EVENT_ADDR_WIDTH-1:0]);
                            end else if ((validate_rsp_index_q + 1'b1 ==
                                          event_count_q) &&
                                         (response_state_word[1:0] != 2'b00)) begin
                                enter_fault(FAULT_UNSAFE_FINAL,
                                    validate_rsp_index_q[EVENT_ADDR_WIDTH-1:0]);
                            end else begin
                                previous_timestamp_q <= response_timestamp;
                                previous_state_q <= response_state_word[1:0];
                                validate_rsp_index_q <= validate_rsp_index_q + 1'b1;

                                if (validate_rsp_index_q + 1'b1 ==
                                    event_count_q) begin
                                    prefetch_req_index_q <= '0;
                                    prefetch_rsp_index_q <= '0;
                                    state_q <= STATE_PREFETCH;
                                end
                            end
                        end
                    end

                    STATE_PREFETCH: begin
                        if (event_req_o)
                            prefetch_req_index_q <= prefetch_req_index_q + 1'b1;

                        if (event_valid_i) begin
                            if (prefetch_rsp_index_q == 0) begin
                                current_event_q <= event_data_i[65:0];
                                current_event_valid_q <= 1'b1;
                            end else begin
                                next_event_q <= event_data_i[65:0];
                                next_event_valid_q <= 1'b1;
                            end
                            prefetch_rsp_index_q <= prefetch_rsp_index_q + 1'b1;

                            if (prefetch_rsp_index_q + 1'b1 == MIN_EVENTS) begin
                                fetch_index_q <= MIN_EVENTS;
                                active_event_index_o <= '0;
                                state_q <= STATE_ARMED;
                            end
                        end
                    end

                    STATE_ARMED: begin
                        if (start_i ||
                            (external_trigger_enable_i && external_trigger_i)) begin
                            start_time_o <= timebase_i;
                            elapsed_tick_o <= '0;
                            state_q <= STATE_RUN;
                        end
                    end

                    STATE_RUN: begin
                        if (event_req_o)
                            fetch_index_q <= fetch_index_q + 1'b1;

                        if (current_event_valid_q &&
                            (current_timestamp < elapsed_tick_o)) begin
                            enter_fault(FAULT_MEMORY_UNDERFLOW,
                                active_event_index_o);
                        end else if (current_due) begin
                            pulse_o <= current_state;

                            if (active_event_number_next == event_count_q) begin
                                state_q <= STATE_IDLE;
                                current_event_valid_q <= 1'b0;
                                next_event_valid_q <= 1'b0;
                                completed_shots_o <= completed_shots_o + 32'd1;
                                done_pulse_o <= 1'b1;
                            end else if (next_event_valid_q) begin
                                current_event_q <= next_event_q;
                                active_event_index_o <= active_event_index_o + 1'b1;
                                if (event_valid_i) begin
                                    next_event_q <= event_data_i[65:0];
                                    next_event_valid_q <= 1'b1;
                                end else begin
                                    next_event_valid_q <= 1'b0;
                                end
                            end else if (event_valid_i) begin
                                current_event_q <= event_data_i[65:0];
                                current_event_valid_q <= 1'b1;
                                active_event_index_o <= active_event_index_o + 1'b1;
                            end else begin
                                enter_fault(FAULT_MEMORY_UNDERFLOW,
                                    active_event_index_o + 1'b1);
                            end
                        end else if (event_valid_i) begin
                            if (next_event_valid_q) begin
                                enter_fault(FAULT_MEMORY_PROTOCOL,
                                    active_event_index_o);
                            end else begin
                                next_event_q <= event_data_i[65:0];
                                next_event_valid_q <= 1'b1;
                            end
                        end

                        elapsed_tick_o <= elapsed_tick_o + 64'd1;
                    end

                    STATE_FAULT: begin
                        pulse_o <= 2'b00;
                    end

                    default: begin
                        enter_fault(FAULT_ILLEGAL_COMMAND, '0);
                    end
                endcase
            end
        end
    end

endmodule

`default_nettype wire
