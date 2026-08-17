// -----------------------------------------------------------------------------
// ctrl_plane_wrap.sv    *** TESTBENCH WRAPPER -- NOT PART OF THE DELIVERED IP ***
//
// Phase 1 device under test: axil_slave + csr_regfile, plus a minimal
// single-bank coefficient array standing in for coeff_bank (Phase 4).
//
// The stand-in array exists only so that the coefficient ADDRESS DECODE and
// read/write routing can be verified now, in isolation, before the real
// dual-bank/atomic-swap logic is built. It deliberately implements no shadow
// bank and no commit semantics -- those are Phase 4 behaviour and testing them
// here would be testing the testbench.
//
// Datapath status and event inputs are driven from primary inputs so that the
// cocotb tests can stimulate BUSY, saturation and frame events without a
// datapath existing yet.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps

module ctrl_plane_wrap
#(
  parameter int ADDR_W       = 12,
  parameter int AXIL_DATA_W  = 32,
  parameter int DATA_W       = 16,
  parameter int COEF_W       = 16,
  parameter int NTAPS_MAX    = 64,
  parameter int MAC_UNITS    = 8,
  parameter bit SYMMETRIC_EN = 1'b1
) (
  input  logic                     clk,
  input  logic                     arst_n,

  // AXI4-Lite
  input  logic [ADDR_W-1:0]        s_axil_awaddr,
  input  logic [2:0]               s_axil_awprot,
  input  logic                     s_axil_awvalid,
  output logic                     s_axil_awready,
  input  logic [AXIL_DATA_W-1:0]   s_axil_wdata,
  input  logic [AXIL_DATA_W/8-1:0] s_axil_wstrb,
  input  logic                     s_axil_wvalid,
  output logic                     s_axil_wready,
  output logic [1:0]               s_axil_bresp,
  output logic                     s_axil_bvalid,
  input  logic                     s_axil_bready,
  input  logic [ADDR_W-1:0]        s_axil_araddr,
  input  logic [2:0]               s_axil_arprot,
  input  logic                     s_axil_arvalid,
  output logic                     s_axil_arready,
  output logic [AXIL_DATA_W-1:0]   s_axil_rdata,
  output logic [1:0]               s_axil_rresp,
  output logic                     s_axil_rvalid,
  input  logic                     s_axil_rready,

  // Datapath status stimulus (TB-driven stand-ins for Phase 4 signals)
  input  logic                     sts_busy,
  input  logic                     sts_draining,
  input  logic                     sts_coeff_pending,
  input  logic                     sts_in_fifo_empty,
  input  logic                     sts_out_fifo_full,
  input  logic                     evt_sample,
  input  logic                     evt_frame_done,
  input  logic                     evt_saturate,

  // Observable configuration outputs
  output logic                     ctrl_enable,
  output logic [fir_pkg::CFG_NTAPS_W-1:0]   cfg_ntaps,
  output logic [fir_pkg::CFG_ROUND_W-1:0]   cfg_round_mode,
  output logic                     cfg_sat_mode,
  output logic                     cfg_symmetric,
  output logic                     cfg_illegal,
  output logic                     cmd_soft_reset,
  output logic                     cmd_coeff_commit,
  output logic                     cmd_flush,
  output logic                     irq
);

  // ---------------------------------------------------------------------------
  // Reset synchroniser (part of the real design, exercised here)
  // ---------------------------------------------------------------------------
  logic rst_n;
  reset_sync #(.STAGES(2)) u_rst_sync (
    .clk    (clk),
    .arst_n (arst_n),
    .rst_n  (rst_n)
  );

  // ---------------------------------------------------------------------------
  // Internal register bus
  // ---------------------------------------------------------------------------
  logic [ADDR_W-1:0]        reg_waddr;
  logic [AXIL_DATA_W-1:0]   reg_wdata;
  logic [AXIL_DATA_W/8-1:0] reg_wstrb;
  logic                     reg_wen;
  logic                     reg_werr;
  logic [ADDR_W-1:0]        reg_raddr;
  logic                     reg_ren;
  logic [AXIL_DATA_W-1:0]   reg_rdata;
  logic                     reg_rerr;

  axil_slave #(
    .ADDR_W (ADDR_W),
    .DATA_W (AXIL_DATA_W)
  ) u_axil (
    .clk            (clk),
    .rst_n          (rst_n),
    .s_axil_awaddr  (s_axil_awaddr),
    .s_axil_awprot  (s_axil_awprot),
    .s_axil_awvalid (s_axil_awvalid),
    .s_axil_awready (s_axil_awready),
    .s_axil_wdata   (s_axil_wdata),
    .s_axil_wstrb   (s_axil_wstrb),
    .s_axil_wvalid  (s_axil_wvalid),
    .s_axil_wready  (s_axil_wready),
    .s_axil_bresp   (s_axil_bresp),
    .s_axil_bvalid  (s_axil_bvalid),
    .s_axil_bready  (s_axil_bready),
    .s_axil_araddr  (s_axil_araddr),
    .s_axil_arprot  (s_axil_arprot),
    .s_axil_arvalid (s_axil_arvalid),
    .s_axil_arready (s_axil_arready),
    .s_axil_rdata   (s_axil_rdata),
    .s_axil_rresp   (s_axil_rresp),
    .s_axil_rvalid  (s_axil_rvalid),
    .s_axil_rready  (s_axil_rready),
    .reg_waddr      (reg_waddr),
    .reg_wdata      (reg_wdata),
    .reg_wstrb      (reg_wstrb),
    .reg_wen        (reg_wen),
    .reg_werr       (reg_werr),
    .reg_raddr      (reg_raddr),
    .reg_ren        (reg_ren),
    .reg_rdata      (reg_rdata),
    .reg_rerr       (reg_rerr)
  );

  // ---------------------------------------------------------------------------
  // Coefficient port
  // ---------------------------------------------------------------------------
  logic                  coef_we;
  logic [fir_pkg::COEF_IDX_W-1:0] coef_waddr;
  logic [COEF_W-1:0]     coef_wdata;
  logic [fir_pkg::COEF_IDX_W-1:0] coef_raddr;
  logic [COEF_W-1:0]     coef_rdata;

  csr_regfile #(
    .ADDR_W       (ADDR_W),
    .AXIL_DATA_W  (AXIL_DATA_W),
    .DATA_W       (DATA_W),
    .COEF_W       (COEF_W),
    .NTAPS_MAX    (NTAPS_MAX),
    .MAC_UNITS    (MAC_UNITS),
    .SYMMETRIC_EN (SYMMETRIC_EN)
  ) u_csr (
    .clk               (clk),
    .rst_n             (rst_n),
    .reg_waddr         (reg_waddr),
    .reg_wdata         (reg_wdata),
    .reg_wstrb         (reg_wstrb),
    .reg_wen           (reg_wen),
    .reg_werr          (reg_werr),
    .reg_raddr         (reg_raddr),
    .reg_ren           (reg_ren),
    .reg_rdata         (reg_rdata),
    .reg_rerr          (reg_rerr),
    .coef_we           (coef_we),
    .coef_waddr        (coef_waddr),
    .coef_wdata        (coef_wdata),
    .coef_raddr        (coef_raddr),
    .coef_rdata        (coef_rdata),
    .ctrl_enable       (ctrl_enable),
    .cfg_ntaps         (cfg_ntaps),
    .cfg_round_mode    (cfg_round_mode),
    .cfg_sat_mode      (cfg_sat_mode),
    .cfg_symmetric     (cfg_symmetric),
    .cfg_illegal       (cfg_illegal),
    .cmd_soft_reset    (cmd_soft_reset),
    .cmd_coeff_commit  (cmd_coeff_commit),
    .cmd_flush         (cmd_flush),
    .sts_busy          (sts_busy),
    .sts_draining      (sts_draining),
    .sts_coeff_pending (sts_coeff_pending),
    .sts_in_fifo_empty (sts_in_fifo_empty),
    .sts_out_fifo_full (sts_out_fifo_full),
    .evt_sample        (evt_sample),
    .evt_frame_done    (evt_frame_done),
    .evt_saturate      (evt_saturate),
    .irq               (irq)
  );

  // ---------------------------------------------------------------------------
  // Phase 1 stand-in coefficient array (single bank, no commit semantics).
  // Replaced by rtl/mem/coeff_bank.sv in Phase 4.
  // ---------------------------------------------------------------------------
  logic [COEF_W-1:0] coef_mem [0:NTAPS_MAX-1];

  always_ff @(posedge clk) begin
    if (coef_we) begin
      coef_mem[coef_waddr] <= coef_wdata;
    end
  end

  assign coef_rdata = coef_mem[coef_raddr];

  // Deterministic start state so that reads of never-written coefficients
  // return a known value rather than X (see engineering log D-009: the real
  // banks are not reset, so the TESTBENCH carries this obligation).
  initial begin
    for (int i = 0; i < NTAPS_MAX; i++) coef_mem[i] = '0;
  end

endmodule : ctrl_plane_wrap
