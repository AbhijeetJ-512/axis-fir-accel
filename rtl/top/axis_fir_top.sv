// -----------------------------------------------------------------------------
// axis_fir_top.sv
//
// Top-level integration of axis_fir_accel. STRUCTURAL ONLY: parameter
// propagation, elaboration-time legality checks, and wiring. There is no logic
// here beyond a handful of one-line glue assignments, deliberately -- a top
// level that contains logic is a top level that cannot be reviewed.
//
//                      AXI4-Lite (control plane)
//                               |
//              axil_slave --> csr_regfile --> coeff_bank (shadow | active)
//                               |                  |
//   s_axis --> [in_fifo] --> fir_ctrl --> fir_core --+--> [out_fifo] --> m_axis
//                               ^                          |
//                               +------- credit_ctr <-------+
//
// Flow control is entirely the credit counter's (D-006): the arithmetic
// pipeline has no stall input and never needs one.
// -----------------------------------------------------------------------------
`ifndef AXIS_FIR_TOP_SV
`define AXIS_FIR_TOP_SV

`include "fir_defs.svh"

module axis_fir_top #(
  parameter int DATA_W         = 16,
  parameter int COEF_W         = 16,
  parameter int NTAPS_MAX      = 64,
  parameter int MAC_UNITS      = 8,
  parameter int IN_FIFO_DEPTH  = 8,
  parameter int OUT_FIFO_DEPTH = 16,
  parameter int AXIL_ADDR_W    = 12,
  parameter bit SYMMETRIC_EN   = 1'b1
) (
  input  logic                        aclk,
  input  logic                        aresetn,

  // ---- AXI4-Lite control -------------------------------------------------------
  input  logic [AXIL_ADDR_W-1:0]      s_axil_awaddr,
  input  logic [2:0]                  s_axil_awprot,
  input  logic                        s_axil_awvalid,
  output logic                        s_axil_awready,
  input  logic [31:0]                 s_axil_wdata,
  input  logic [3:0]                  s_axil_wstrb,
  input  logic                        s_axil_wvalid,
  output logic                        s_axil_wready,
  output logic [1:0]                  s_axil_bresp,
  output logic                        s_axil_bvalid,
  input  logic                        s_axil_bready,
  input  logic [AXIL_ADDR_W-1:0]      s_axil_araddr,
  input  logic [2:0]                  s_axil_arprot,
  input  logic                        s_axil_arvalid,
  output logic                        s_axil_arready,
  output logic [31:0]                 s_axil_rdata,
  output logic [1:0]                  s_axil_rresp,
  output logic                        s_axil_rvalid,
  input  logic                        s_axil_rready,

  // ---- AXI4-Stream data --------------------------------------------------------
  input  logic [DATA_W-1:0]           s_axis_tdata,
  input  logic                        s_axis_tlast,
  input  logic                        s_axis_tvalid,
  output logic                        s_axis_tready,

  output logic [DATA_W-1:0]           m_axis_tdata,
  output logic                        m_axis_tlast,
  output logic                        m_axis_tvalid,
  input  logic                        m_axis_tready,

  output logic                        irq
);

  // ---------------------------------------------------------------------------
  // Elaboration-time legality
  //
  // OUT_FIFO_DEPTH must exceed the worst-case number of samples in flight, or
  // the credit counter throttles admission below the achievable rate. That does
  // not corrupt data -- it silently halves throughput, which is exactly the kind
  // of defect that hides until someone measures performance.
  // ---------------------------------------------------------------------------
  localparam int LEVELS  = $clog2(MAC_UNITS);
  localparam int CORE_LAT_MIN = 5 + LEVELS;      // latency at P = 1

  initial begin
    if (OUT_FIFO_DEPTH <= CORE_LAT_MIN)
      $fatal(1, "axis_fir_top: OUT_FIFO_DEPTH (%0d) must exceed the P=1 core latency (%0d) to sustain II=1.",
             OUT_FIFO_DEPTH, CORE_LAT_MIN);
    if (MAC_UNITS > NTAPS_MAX)
      $fatal(1, "axis_fir_top: MAC_UNITS (%0d) must not exceed NTAPS_MAX (%0d).", MAC_UNITS, NTAPS_MAX);
  end

  logic rst_n;
  reset_sync #(.STAGES(2)) u_rst (.clk (aclk), .arst_n (aresetn), .rst_n (rst_n));

  // ---------------------------------------------------------------------------
  // Control plane
  // ---------------------------------------------------------------------------
  logic [AXIL_ADDR_W-1:0] reg_waddr, reg_raddr;
  logic [31:0]            reg_wdata, reg_rdata;
  logic [3:0]             reg_wstrb;
  logic                   reg_wen, reg_werr, reg_ren, reg_rerr;

  axil_slave #(.ADDR_W (AXIL_ADDR_W), .DATA_W (32)) u_axil (
    .clk (aclk), .rst_n (rst_n),
    .s_axil_awaddr (s_axil_awaddr), .s_axil_awprot (s_axil_awprot),
    .s_axil_awvalid(s_axil_awvalid), .s_axil_awready(s_axil_awready),
    .s_axil_wdata (s_axil_wdata), .s_axil_wstrb (s_axil_wstrb),
    .s_axil_wvalid(s_axil_wvalid), .s_axil_wready(s_axil_wready),
    .s_axil_bresp (s_axil_bresp), .s_axil_bvalid(s_axil_bvalid), .s_axil_bready(s_axil_bready),
    .s_axil_araddr (s_axil_araddr), .s_axil_arprot (s_axil_arprot),
    .s_axil_arvalid(s_axil_arvalid), .s_axil_arready(s_axil_arready),
    .s_axil_rdata (s_axil_rdata), .s_axil_rresp (s_axil_rresp),
    .s_axil_rvalid(s_axil_rvalid), .s_axil_rready(s_axil_rready),
    .reg_waddr (reg_waddr), .reg_wdata (reg_wdata), .reg_wstrb (reg_wstrb),
    .reg_wen (reg_wen), .reg_werr (reg_werr),
    .reg_raddr (reg_raddr), .reg_ren (reg_ren),
    .reg_rdata (reg_rdata), .reg_rerr (reg_rerr)
  );

  logic                            coef_we;
  logic [fir_pkg::COEF_IDX_W-1:0]  coef_waddr, coef_raddr;
  logic [COEF_W-1:0]               coef_wdata, coef_rdata;
  logic                            ctrl_enable, cfg_sat_mode, cfg_symmetric, cfg_illegal;
  logic [fir_pkg::CFG_NTAPS_W-1:0] cfg_ntaps;
  logic [fir_pkg::CFG_ROUND_W-1:0] cfg_round_mode;
  logic                            cmd_soft_reset, cmd_coeff_commit, cmd_flush;
  logic                            sts_busy, sts_draining, sts_coeff_pending;
  logic                            evt_sample, evt_frame_done, evt_saturate;
  logic                            in_fifo_empty, out_fifo_full;

  csr_regfile #(
    .ADDR_W (AXIL_ADDR_W), .AXIL_DATA_W (32), .DATA_W (DATA_W), .COEF_W (COEF_W),
    .NTAPS_MAX (NTAPS_MAX), .MAC_UNITS (MAC_UNITS), .SYMMETRIC_EN (SYMMETRIC_EN)
  ) u_csr (
    .clk (aclk), .rst_n (rst_n),
    .reg_waddr (reg_waddr), .reg_wdata (reg_wdata), .reg_wstrb (reg_wstrb),
    .reg_wen (reg_wen), .reg_werr (reg_werr),
    .reg_raddr (reg_raddr), .reg_ren (reg_ren),
    .reg_rdata (reg_rdata), .reg_rerr (reg_rerr),
    .coef_we (coef_we), .coef_waddr (coef_waddr), .coef_wdata (coef_wdata),
    .coef_raddr (coef_raddr), .coef_rdata (coef_rdata),
    .ctrl_enable (ctrl_enable), .cfg_ntaps (cfg_ntaps),
    .cfg_round_mode (cfg_round_mode), .cfg_sat_mode (cfg_sat_mode),
    .cfg_symmetric (cfg_symmetric), .cfg_illegal (cfg_illegal),
    .cmd_soft_reset (cmd_soft_reset), .cmd_coeff_commit (cmd_coeff_commit),
    .cmd_flush (cmd_flush),
    .sts_busy (sts_busy), .sts_draining (sts_draining),
    .sts_coeff_pending (sts_coeff_pending),
    .sts_in_fifo_empty (in_fifo_empty), .sts_out_fifo_full (out_fifo_full),
    .evt_sample (evt_sample), .evt_frame_done (evt_frame_done),
    .evt_saturate (evt_saturate),
    .irq (irq)
  );

  // ---------------------------------------------------------------------------
  // Flush / soft-reset scope
  //
  // These clear the input FIFO, the delay line and the pipeline, but NOT the
  // output FIFO -- dropping m_axis_tvalid on a beat already offered to the
  // consumer would violate AXI4-Stream (D-020). Credits are reconciled to the
  // beats still buffered so the invariant survives the flush exactly.
  // ---------------------------------------------------------------------------
  logic soft_clear;
  assign soft_clear = cmd_soft_reset | cmd_flush;

  // ---------------------------------------------------------------------------
  // Coefficients
  // ---------------------------------------------------------------------------
  logic [NTAPS_MAX*COEF_W-1:0] coef_flat;
  logic                        frame_boundary, core_idle;

  coeff_bank #(.NTAPS_MAX (NTAPS_MAX), .COEF_W (COEF_W)) u_coef (
    .clk (aclk), .rst_n (rst_n),
    .we (coef_we), .waddr (coef_waddr), .wdata (coef_wdata),
    .raddr (coef_raddr), .rdata (coef_rdata),
    .commit_req (cmd_coeff_commit), .frame_boundary (frame_boundary),
    .idle (core_idle), .pending (sts_coeff_pending),
    .coef_flat (coef_flat)
  );

  // ---------------------------------------------------------------------------
  // Input elastic buffer
  // ---------------------------------------------------------------------------
  logic [DATA_W-1:0] in_tdata;
  logic              in_tlast, in_tvalid, in_tready;

  axis_fifo #(.DATA_W (DATA_W), .DEPTH (IN_FIFO_DEPTH)) u_in_fifo (
    .clk (aclk), .rst_n (rst_n), .soft_clear (soft_clear),
    .s_tdata (s_axis_tdata), .s_tlast (s_axis_tlast),
    .s_tvalid(s_axis_tvalid), .s_tready(s_axis_tready),
    .m_tdata (in_tdata), .m_tlast (in_tlast),
    .m_tvalid(in_tvalid), .m_tready(in_tready),
    .count (), .full (), .empty (in_fifo_empty)
  );

  // ---------------------------------------------------------------------------
  // Admission credit
  // ---------------------------------------------------------------------------
  logic have_credit, consume, out_pop, out_push;
  logic [$clog2(OUT_FIFO_DEPTH):0]   out_count;
  logic [$clog2(OUT_FIFO_DEPTH+1)-1:0] credit_reload;
  logic [$clog2(OUT_FIFO_DEPTH):0]   out_count_next;

  localparam int CW = $clog2(OUT_FIFO_DEPTH+1);

  assign out_pop        = m_axis_tvalid && m_axis_tready;
  assign out_count_next = out_count
                        + {{$clog2(OUT_FIFO_DEPTH){1'b0}}, out_push}
                        - {{$clog2(OUT_FIFO_DEPTH){1'b0}}, out_pop};
  assign credit_reload  = CW'(OUT_FIFO_DEPTH) - out_count_next[CW-1:0];

  credit_ctr #(.MAX_CREDITS (OUT_FIFO_DEPTH)) u_credit (
    .clk (aclk), .rst_n (rst_n), .soft_clear (soft_clear),
    .clear_value (credit_reload),
    .consume (consume), .restore (out_pop),
    .have_credit (have_credit), .credits ()
  );

  // ---------------------------------------------------------------------------
  // Control and datapath
  // ---------------------------------------------------------------------------
  logic                            shift_en, pass_vld, pass_first, pass_last, pass_tlast;
  logic [fir_pkg::CFG_NTAPS_W-1:0] tap_base;
  logic                            core_out_valid, core_out_last, core_out_sat;
  logic [DATA_W-1:0]               core_out_data;

  fir_ctrl #(
    .NTAPS_MAX (NTAPS_MAX), .MAC_UNITS (MAC_UNITS), .OUT_FIFO_DEPTH (OUT_FIFO_DEPTH)
  ) u_ctrl (
    .clk (aclk), .rst_n (rst_n), .soft_clear (soft_clear),
    .ctrl_enable (ctrl_enable), .cfg_illegal (cfg_illegal),
    .cfg_ntaps (cfg_ntaps), .cfg_symmetric (cfg_symmetric), .cmd_flush (cmd_flush),
    .in_tvalid (in_tvalid), .in_tlast (in_tlast), .in_tready (in_tready),
    .have_credit (have_credit), .consume (consume),
    .core_out_valid (core_out_valid),
    .shift_en (shift_en), .pass_vld (pass_vld), .pass_first (pass_first),
    .pass_last (pass_last), .pass_tlast (pass_tlast), .tap_base (tap_base),
    .sts_busy (sts_busy), .sts_draining (sts_draining),
    .dbg_state (), .dbg_passes ()
  );

  fir_core #(
    .DATA_W (DATA_W), .COEF_W (COEF_W), .NTAPS_MAX (NTAPS_MAX),
    .MAC_UNITS (MAC_UNITS), .SYMMETRIC_EN (SYMMETRIC_EN)
  ) u_core (
    .clk (aclk), .rst_n (rst_n), .clear (soft_clear),
    .cfg_ntaps (cfg_ntaps), .cfg_round_mode (cfg_round_mode),
    .cfg_sat_mode (cfg_sat_mode), .cfg_symmetric (cfg_symmetric),
    .coef_flat (coef_flat),
    .shift_en (shift_en), .in_data (in_tdata),
    .pass_vld (pass_vld), .pass_first (pass_first), .pass_last (pass_last),
    .pass_tlast (pass_tlast), .tap_base (tap_base),
    .out_valid (core_out_valid), .out_data (core_out_data),
    .out_last (core_out_last), .out_sat (core_out_sat)
  );

  // ---------------------------------------------------------------------------
  // Output elastic buffer
  //
  // s_tready is ignored: the credit counter guarantees a free slot for every
  // admitted sample, which is the whole point of the scheme.
  // ---------------------------------------------------------------------------
  assign out_push = core_out_valid;

  axis_fifo #(.DATA_W (DATA_W), .DEPTH (OUT_FIFO_DEPTH)) u_out_fifo (
    .clk (aclk), .rst_n (rst_n), .soft_clear (1'b0),
    .s_tdata (core_out_data), .s_tlast (core_out_last),
    .s_tvalid(out_push), .s_tready(),
    .m_tdata (m_axis_tdata), .m_tlast (m_axis_tlast),
    .m_tvalid(m_axis_tvalid), .m_tready(m_axis_tready),
    .count (out_count), .full (out_fifo_full), .empty ()
  );

  // ---------------------------------------------------------------------------
  // Events and status glue
  //
  // frame_boundary is taken from the OUTPUT pop of a TLAST beat, not from the
  // input, so a coefficient swap can never land inside a frame still being
  // computed (D-008).
  // ---------------------------------------------------------------------------
  assign evt_sample     = consume;
  assign evt_frame_done = out_pop && m_axis_tlast;
  assign evt_saturate   = core_out_valid && core_out_sat;
  assign frame_boundary = evt_frame_done;
  assign core_idle      = !sts_busy;

endmodule : axis_fir_top

`endif
