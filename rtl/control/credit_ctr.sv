// -----------------------------------------------------------------------------
// credit_ctr.sv
//
// Admission credit counter -- the whole of this IP's flow control, in one
// counter (engineering log D-006, docs/02_architecture.md section 5).
//
// THE INVARIANT
//
//   in_flight + out_fifo_occupancy  <=  MAX_CREDITS
//
// A credit is taken when a sample is ADMITTED into the arithmetic pipeline, and
// returned only when the corresponding beat has LEFT the IP. Because the
// pipeline produces exactly one output per admitted sample, the number of
// samples that can ever arrive at the output FIFO is bounded by MAX_CREDITS.
// Setting MAX_CREDITS = OUT_FIFO_DEPTH therefore makes output-FIFO overflow
// STRUCTURALLY IMPOSSIBLE -- not unlikely, impossible -- regardless of pipeline
// depth, fold factor, or how long the downstream consumer stalls.
//
// This is why the arithmetic pipeline needs no stall logic at all: it is never
// asked to hold a result it has nowhere to put. No per-stage enables, no global
// stall net, no cross-stage handshaking.
//
// Throughput constraint: to sustain one admission per cycle the counter must
// never reach zero in steady state, which requires
//
//   MAX_CREDITS > (pipeline latency to the output FIFO) + 1
//
// The integrator is responsible for that; the loopback wrapper and the Phase 4
// top both check it at elaboration.
// -----------------------------------------------------------------------------
`ifndef CREDIT_CTR_SV
`define CREDIT_CTR_SV

module credit_ctr #(
  parameter int MAX_CREDITS = 8
) (
  input  logic clk,
  input  logic rst_n,
  input  logic soft_clear,     // synchronous reload from clear_value

  // Credit count to load on soft_clear. A flush discards whatever is in the
  // pipeline, so those credits would otherwise never be returned and the
  // counter would leak downward on every flush. The caller supplies the exact
  // reconciled value: MAX_CREDITS minus whatever is still buffered downstream
  // of the flush point. Tie to MAX_CREDITS if the flush empties everything.
  input  logic [$clog2(MAX_CREDITS+1)-1:0] clear_value,

  input  logic consume,        // a sample was admitted into the pipeline
  input  logic restore,        // an output beat left the IP

  output logic have_credit,    // admission is permitted this cycle
  output logic [$clog2(MAX_CREDITS+1)-1:0] credits
);

  localparam int CNT_W = $clog2(MAX_CREDITS+1);

  initial begin
    if (MAX_CREDITS < 1)
      $fatal(1, "credit_ctr: MAX_CREDITS must be >= 1 (got %0d).", MAX_CREDITS);
  end

  logic [CNT_W-1:0] credits_q;

  assign credits     = credits_q;
  assign have_credit = (credits_q != '0);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      credits_q <= CNT_W'(MAX_CREDITS);
    end else if (soft_clear) begin
      credits_q <= clear_value;
    end else begin
      // consume and restore in the same cycle cancel out exactly -- which is
      // the common case at full throughput, and is why this is a single
      // counter rather than separate issue/retire counters that could drift.
      case ({consume, restore})
        2'b10:   credits_q <= credits_q - 1'b1;
        2'b01:   credits_q <= credits_q + 1'b1;
        default: credits_q <= credits_q;
      endcase
    end
  end

  // The caller must not consume without a credit; doing so would break the
  // invariant silently. Checked here rather than trusted.
  // Plain `always`, not `always_ff`: this block describes no hardware, and
  // declaring it always_ff would (correctly) warn that $error is unsynthesisable.
  // synthesis translate_off
  always @(posedge clk) begin
    if (rst_n && consume && !have_credit) begin
      $error("credit_ctr: consume asserted with zero credits -- invariant broken");
    end
    if (rst_n && !soft_clear && restore && (credits_q == CNT_W'(MAX_CREDITS)) && !consume) begin
      $error("credit_ctr: restore with all credits already returned -- more outputs than admissions");
    end
  end
  // synthesis translate_on

endmodule : credit_ctr

`endif
