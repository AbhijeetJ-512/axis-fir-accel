// -----------------------------------------------------------------------------
// fir_core_wrap.sv   *** TESTBENCH WRAPPER -- NOT PART OF THE DELIVERED IP ***
//
// Phase 3 device under test: fir_core with a reset synchroniser and flat ports.
// The datapath is exercised directly, with no FIFOs and no credit gating, so
// that any mismatch is unambiguously an ARITHMETIC fault. Flow control was
// already proved in Phase 2 and is deliberately not in the way here.
//
// dbg_latency exports the compile-time LATENCY so the test checks the RTL's own
// figure rather than a number duplicated in Python that could drift.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "fir_defs.svh"

module fir_core_wrap #(
  parameter int DATA_W    = 16,
  parameter int COEF_W    = 16,
  parameter int NTAPS_MAX = 64,
  parameter bit SYMMETRIC_EN = 1'b1
) (
  input  logic                            clk,
  input  logic                            arst_n,
  input  logic                            clear,

  input  logic [fir_pkg::CFG_NTAPS_W-1:0] cfg_ntaps,
  input  logic [fir_pkg::CFG_ROUND_W-1:0] cfg_round_mode,
  input  logic                            cfg_sat_mode,
  input  logic                            cfg_symmetric,
  input  logic [NTAPS_MAX*COEF_W-1:0]     coef_flat,

  input  logic                            in_valid,
  input  logic [DATA_W-1:0]               in_data,
  input  logic                            in_last,

  output logic                            out_valid,
  output logic [DATA_W-1:0]               out_data,
  output logic                            out_last,
  output logic                            out_sat,

  output logic [7:0]                      dbg_latency,
  output logic [7:0]                      dbg_acc_w,
  output logic signed [`FIR_ACC_W(DATA_W, COEF_W, NTAPS_MAX)-1:0] dbg_acc
);

  logic rst_n;
  reset_sync #(.STAGES(2)) u_rst (.clk (clk), .arst_n (arst_n), .rst_n (rst_n));

  fir_core #(
    .DATA_W (DATA_W), .COEF_W (COEF_W), .NTAPS_MAX (NTAPS_MAX), .SYMMETRIC_EN (SYMMETRIC_EN)
  ) u_core (
    .clk (clk), .rst_n (rst_n), .clear (clear),
    .cfg_ntaps (cfg_ntaps), .cfg_round_mode (cfg_round_mode),
    .cfg_sat_mode (cfg_sat_mode), .cfg_symmetric (cfg_symmetric),
    .coef_flat (coef_flat),
    .in_valid (in_valid), .in_data (in_data), .in_last (in_last),
    .out_valid (out_valid), .out_data (out_data),
    .out_last (out_last), .out_sat (out_sat)
  );

  assign dbg_latency = 8'(u_core.LATENCY);
  assign dbg_acc_w   = 8'(u_core.ACC_W);

  // ---------------------------------------------------------------------------
  // Accumulator exposed so the test can report how close the stimulus actually
  // came to the ACC_W bound. NR-05 claims overflow is impossible; an empirical
  // peak that approaches the bound without reaching it is the evidence that the
  // derivation is tight rather than merely generous.
  // ---------------------------------------------------------------------------
  assign dbg_acc = u_core.u_acc.acc;

endmodule : fir_core_wrap
