
`timescale 1 ps / 1 ps
`default_nettype none

module qcrate_top(
    // Future true board-level I/O
    // input  logic interlock_n_i,
    //output logic shot_trigger_o,
    //output logic sync_pulse_o
);
    
  logic [31:0]       APB_M_0_paddr;
  logic              APB_M_0_penable;
  logic [31:0]       APB_M_0_prdata;
  logic              APB_M_0_pready;
  logic              APB_M_0_psel;
  logic              APB_M_0_pslverr;
  logic [31:0]       APB_M_0_pwdata;
  logic              APB_M_0_pwrite;
  
  logic [31:0]       S_AXIS_S2MM_0_tdata;
  logic [3:0]        S_AXIS_S2MM_0_tkeep;
  logic              S_AXIS_S2MM_0_tlast;
  logic              S_AXIS_S2MM_0_tready;
  logic              S_AXIS_S2MM_0_tvalid;
  
  logic              pl_arstn0;
  logic              pl_arstn1;
  logic              pl_clk0;
  logic              pl_clk1;
  logic              qcrate_irq;

    // ------------------------------------------------------------
    // Block design 
    // ------------------------------------------------------------
    design_1 design_1_i (
        .APB_M_0_paddr          (APB_M_0_paddr),
        .APB_M_0_penable        (APB_M_0_penable),
        .APB_M_0_prdata         (APB_M_0_prdata),
        .APB_M_0_pready         (APB_M_0_pready),
        .APB_M_0_psel           (APB_M_0_psel),
        .APB_M_0_pslverr        (APB_M_0_pslverr),
        .APB_M_0_pwdata         (APB_M_0_pwdata),
        .APB_M_0_pwrite         (APB_M_0_pwrite),
        
        .S_AXIS_S2MM_0_tdata    (S_AXIS_S2MM_0_tdata),
        .S_AXIS_S2MM_0_tkeep    (S_AXIS_S2MM_0_tkeep),
        .S_AXIS_S2MM_0_tlast    (S_AXIS_S2MM_0_tlast),
        .S_AXIS_S2MM_0_tready   (S_AXIS_S2MM_0_tready),
        .S_AXIS_S2MM_0_tvalid   (S_AXIS_S2MM_0_tvalid),
        
        .pl_arstn0              (pl_arstn0),
        .pl_arstn1              (pl_arstn1),
        .pl_clk0                (pl_clk0),    //200Mhz
        .pl_clk1                (pl_clk1),    //100Mhz
        .user_irq_in            (qcrate_irq)
    );

`ifdef QCRATE_APB_CANARY

    // Diagnostic APB endpoint: proves the BD-to-top APB path before qcrate_core.
    assign APB_M_0_prdata = 32'h5143_5254;
    assign APB_M_0_pready = 1'b1;
    assign APB_M_0_pslverr = 1'b0;

    assign S_AXIS_S2MM_0_tdata = 32'h0000_0000;
    assign S_AXIS_S2MM_0_tkeep = 4'hF;
    assign S_AXIS_S2MM_0_tvalid = 1'b0;
    assign S_AXIS_S2MM_0_tlast = 1'b0;
    assign qcrate_irq = 1'b0;

`else

    // ------------------------------------------------------------
    // Entire custom Q-Crate application
    // ------------------------------------------------------------
    qcrate_core u_qcrate_core (
        .clk_ctrl_i            (pl_clk1), //100Mhz, APB control clock
        .rst_ctrl_n_i          (pl_arstn1), 

        .clk_stream_i          (pl_clk0),   //200Mhz, streaming and system clock
        .rst_stream_n_i        (pl_arstn0),

        // APB slave
        .s_apb_paddr_i         (APB_M_0_paddr),
        .s_apb_pwdata_i        (APB_M_0_pwdata),
        .s_apb_psel_i          (APB_M_0_psel),
        .s_apb_penable_i       (APB_M_0_penable),
        .s_apb_pwrite_i        (APB_M_0_pwrite),

        .s_apb_prdata_o        (APB_M_0_prdata),
        .s_apb_pready_o        (APB_M_0_pready),
        .s_apb_pslverr_o       (APB_M_0_pslverr),

        // AXI4-Stream master
        .m_axis_tdata_o        (S_AXIS_S2MM_0_tdata),
        .m_axis_tkeep_o        (S_AXIS_S2MM_0_tkeep),
        .m_axis_tvalid_o       (S_AXIS_S2MM_0_tvalid),
        .m_axis_tready_i       (S_AXIS_S2MM_0_tready),
        .m_axis_tlast_o        (S_AXIS_S2MM_0_tlast),

        // Interrupt
        .irq_o                 (qcrate_irq)

        // Board-level safety and timing
        //.interlock_n_i         (interlock_n_i),
        //.shot_trigger_o        (shot_trigger_o),
        //.sync_pulse_o          (sync_pulse_o)
    );

`endif

`ifdef QCRATE_ENABLE_ILA

    qcrate_debug u_debug (
        .clk_ctrl_i            (pl_clk1),
        .clk_stream_i          (pl_clk0),

        .axis_tdata_i          (S_AXIS_S2MM_0_tdata),
        .axis_tkeep_i          (S_AXIS_S2MM_0_tkeep),
        .axis_tvalid_i         (S_AXIS_S2MM_0_tvalid),
        .axis_tready_i         (S_AXIS_S2MM_0_tready),
        .axis_tlast_i          (S_AXIS_S2MM_0_tlast),

        .apb_paddr_i           (APB_M_0_paddr),
        .apb_psel_i            (APB_M_0_psel),
        .apb_penable_i         (APB_M_0_penable),
        .apb_pwrite_i          (APB_M_0_pwrite),

        .irq_i                 (qcrate_irq)
    );

`endif
 
endmodule

`default_nettype wire
