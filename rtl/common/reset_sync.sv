// -----------------------------------------------------------------------------
// reset_sync.sv
//
// Reset synchroniser: asynchronous assert, synchronous de-assert.
//
// Why this style (docs/02_architecture.md section 7):
//   - Asynchronous assertion guarantees the design goes safe even if the clock
//     is not running or is glitching during power-up.
//   - Synchronous de-assertion prevents reset-recovery/removal timing
//     violations. On a design with a wide register array (the delay line and
//     coefficient banks here) an asynchronously de-asserted reset is effectively
//     impossible to close timing on, and produces metastable state entry that
//     simulation will not show.
//
// The shift register loads 1s from the LSB end after the asynchronous reset is
// released, so the output de-asserts STAGES clock edges later, synchronised to
// the destination clock.
// -----------------------------------------------------------------------------
`ifndef RESET_SYNC_SV
`define RESET_SYNC_SV

module reset_sync #(
  parameter int STAGES = 2
) (
  input  logic clk,
  input  logic arst_n,   // asynchronous, active-low
  output logic rst_n     // synchronised, active-low
);

  // ---------------------------------------------------------------------------
  // Elaboration-time legality
  // ---------------------------------------------------------------------------
  initial begin
    if (STAGES < 2) begin
      $fatal(1, "reset_sync: STAGES must be >= 2 (got %0d). A single stage does not remove metastability.", STAGES);
    end
  end

  logic [STAGES-1:0] sync_q;

  always_ff @(posedge clk or negedge arst_n) begin
    if (!arst_n) begin
      sync_q <= '0;
    end else begin
      sync_q <= {sync_q[STAGES-2:0], 1'b1};
    end
  end

  assign rst_n = sync_q[STAGES-1];

endmodule : reset_sync

`endif
