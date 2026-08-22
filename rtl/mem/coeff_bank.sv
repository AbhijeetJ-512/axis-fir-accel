// -----------------------------------------------------------------------------
// coeff_bank.sv
//
// Dual-bank coefficient storage with an atomic frame-boundary swap
// (engineering log D-008).
//
// THE HAZARD THIS EXISTS TO REMOVE. With a single bank, software updating h[3]
// while a frame is in flight produces outputs computed from a MIXTURE of the
// old and new filters. Nothing detects it: no protocol is violated, no status
// bit is set, the stream keeps flowing, and the only symptom is a handful of
// numerically wrong samples. The dual bank makes that structurally impossible.
//
//   software writes  ---->  SHADOW bank
//   datapath reads   <----  ACTIVE bank
//   swap: bank_sel toggles when a commit is pending AND a frame boundary passes
//
// A "frame boundary" is the cycle after a TLAST beat has actually LEFT the IP,
// not when one is accepted at the input -- so the swap can never land in the
// middle of a frame that is still being computed.
//
// SEMANTIC SOFTWARE MUST KNOW: after a swap the two banks exchange roles, so
// the shadow bank now holds the PREVIOUS coefficient set, not the one just
// committed. Software must write every tap it wants changed before each commit.
// The alternative -- copying active into shadow on swap -- would need
// NTAPS_MAX register-to-register moves for no functional gain. This is stated
// in docs/04_interfaces.md rather than left for a driver author to discover.
//
// The arrays themselves are deliberately NOT reset (D-009): software must write
// coefficients before enabling the block, and resetting the largest register
// array in the IP buys nothing. `bank_sel` IS reset -- it is one bit, and an
// undefined value would make the datapath read an undefined bank.
// -----------------------------------------------------------------------------
`ifndef COEFF_BANK_SV
`define COEFF_BANK_SV

module coeff_bank #(
  parameter int NTAPS_MAX = 64,
  parameter int COEF_W    = 16
) (
  input  logic                            clk,
  input  logic                            rst_n,

  // ---- Software write port (targets the SHADOW bank) -------------------------
  input  logic                            we,
  input  logic [fir_pkg::COEF_IDX_W-1:0]  waddr,
  input  logic [COEF_W-1:0]               wdata,

  // ---- Software read-back port (also the SHADOW bank, so software reads what
  //      it just wrote rather than what the datapath is using) -----------------
  input  logic [fir_pkg::COEF_IDX_W-1:0]  raddr,
  output logic [COEF_W-1:0]               rdata,

  // ---- Commit -----------------------------------------------------------------
  input  logic                            commit_req,      // CTRL.COEFF_COMMIT pulse
  input  logic                            frame_boundary,  // TLAST beat left the IP
  input  logic                            idle,            // no frame in flight
  output logic                            pending,         // STATUS.COEFF_PENDING

  // ---- Datapath read port (the ACTIVE bank, all taps in parallel) ------------
  output logic [NTAPS_MAX*COEF_W-1:0]     coef_flat
);

  logic [COEF_W-1:0] bank0 [0:NTAPS_MAX-1];
  logic [COEF_W-1:0] bank1 [0:NTAPS_MAX-1];
  logic              bank_sel;      // 0 => bank0 active, bank1 shadow
  logic              pending_q;

  // ---------------------------------------------------------------------------
  // Software writes land in whichever bank is currently the shadow.
  // ---------------------------------------------------------------------------
  always_ff @(posedge clk) begin
    if (we) begin
      if (bank_sel) bank0[waddr] <= wdata;   // bank1 active -> bank0 is shadow
      else          bank1[waddr] <= wdata;
    end
  end

  assign rdata = bank_sel ? bank0[raddr] : bank1[raddr];

  genvar g;
  generate
    for (g = 0; g < NTAPS_MAX; g++) begin : g_active
      assign coef_flat[g*COEF_W +: COEF_W] = bank_sel ? bank1[g] : bank0[g];
    end
  endgenerate

  // ---------------------------------------------------------------------------
  // Commit tracking
  //
  // `idle` is accepted as a swap opportunity as well as `frame_boundary`, so a
  // commit issued while the IP is stopped takes effect immediately instead of
  // waiting for a frame that may never come.
  // ---------------------------------------------------------------------------
  logic swap_now;
  assign swap_now = pending_q && (frame_boundary || idle);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bank_sel  <= 1'b0;
      pending_q <= 1'b0;
    end else begin
      if (swap_now) begin
        bank_sel  <= ~bank_sel;
        pending_q <= commit_req;      // a commit arriving on the swap cycle survives
      end else if (commit_req) begin
        pending_q <= 1'b1;
      end
    end
  end

  assign pending = pending_q;

endmodule : coeff_bank

`endif
