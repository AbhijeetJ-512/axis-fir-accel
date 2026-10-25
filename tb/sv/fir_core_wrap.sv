// -----------------------------------------------------------------------------
// fir_core_wrap.sv   *** TESTBENCH WRAPPER -- NOT PART OF THE DELIVERED IP ***
//
// Arithmetic device under test: fir_core with a reset synchroniser and the flat,
// flow-control-free port list the Phase 3 test bench was written against.
// The datapath is exercised directly, with no FIFOs and no credit gating, so
// that any mismatch is unambiguously an ARITHMETIC fault.
//
// PORTFOLIO CHANGE (2026-10-21). The Phase 3 wrapper (kept in tb/sv/original/)
// instantiated the unfolded fir_core, which had in_valid / in_last ports and a
// LATENCY localparam. Phase 4 replaced those with the folded interface
// (shift_en, pass_*, tap_base) and the wrapper was never updated, so
// `make fir` / `make fir-full` stopped elaborating; `make check` hid that
// because its `fir` step swallows the failure (`grep ... || true`).
// This wrapper restores the suite by emulating, in two registers, what
// fir_ctrl does for one pass per sample (P = 1):
//     cycle c   : shift_en = in_valid   (the delay line takes the sample)
//     cycle c+1 : pass_vld = pass_first = pass_last = 1, tap_base = 0
// and MAC_UNITS = NTAPS_MAX, so every tap has its own multiplier (P = 1 for
// every legal CFG.NTAPS). The arithmetic under test is therefore the real
// folded datapath, run at its unfolded corner.
//
// dbg_latency is the documented latency from sample_start (here: in_valid) to
// out_valid at P = 1: 5 + clog2(MAC_UNITS) (docs/03_microarchitecture.md
// section 3). The test measures the same quantity in simulation and compares.
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

  // Pass record: one cycle behind shift_en, as fir_ctrl produces it (pass_vld = busy).
  logic pv, pl;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)      begin pv <= 1'b0; pl <= 1'b0; end
    else if (clear)  begin pv <= 1'b0; pl <= 1'b0; end
    else             begin pv <= in_valid; pl <= in_last; end
  end

  fir_core #(
    .DATA_W (DATA_W), .COEF_W (COEF_W), .NTAPS_MAX (NTAPS_MAX),
    .MAC_UNITS (NTAPS_MAX), .SYMMETRIC_EN (SYMMETRIC_EN)
  ) u_core (
    .clk (clk), .rst_n (rst_n), .clear (clear),
    .cfg_ntaps (cfg_ntaps), .cfg_round_mode (cfg_round_mode),
    .cfg_sat_mode (cfg_sat_mode), .cfg_symmetric (cfg_symmetric),
    .coef_flat (coef_flat),
    .shift_en (in_valid), .in_data (in_data),
    .pass_vld (pv), .pass_first (pv), .pass_last (pv), .pass_tlast (pl),
    .tap_base ('0),
    .out_valid (out_valid), .out_data (out_data),
    .out_last (out_last), .out_sat (out_sat)
  );

  assign dbg_latency = 8'(5 + $clog2(NTAPS_MAX));
  assign dbg_acc_w   = 8'(`FIR_ACC_W(DATA_W, COEF_W, NTAPS_MAX));

  // ---------------------------------------------------------------------------
  // Accumulator exposed so the test can report how close the stimulus actually
  // came to the ACC_W bound. NR-05 claims overflow is impossible; an empirical
  // peak that approaches the bound without reaching it is the evidence that the
  // derivation is tight rather than merely generous.
  // ---------------------------------------------------------------------------
  assign dbg_acc = u_core.u_acc.acc;

endmodule : fir_core_wrap
