// -----------------------------------------------------------------------------
// round_sat.sv
//
// The ONLY place in this IP where information is lost, and therefore the only
// place overflow can occur. Everything upstream is exact by construction.
//
// Converts the Q(ACC_W-2*SHIFT).(2*SHIFT) accumulator back to the Q1.(DATA_W-1)
// sample format by discarding SHIFT fractional bits and clamping to DATA_W bits.
//
// Purely combinational: it is a pure function of (acc, round_mode, sat_mode),
// and keeping it that way is what lets the exact rounding and saturation
// boundary values be tested in isolation. fir_core registers the result.
//
// ROUNDING. All three modes are one predicate on the same floored value, which
// is both cheaper in gates and far easier to reason about than three separate
// expressions:
//
//   floored = acc >>> SHIFT                (arithmetic shift = floor, exactly)
//   guard   = acc[SHIFT-1]                 (the half bit)
//   sticky  = |acc[SHIFT-2:0]              (anything below the half bit)
//   lsb     = acc[SHIFT]                   (parity of the floored result)
//
//     TRUNC       inc = 0                    -> floor, biased toward -inf
//     HALF_UP     inc = guard                -> ties go toward +inf
//     CONVERGENT  inc = guard & (sticky|lsb) -> ties go to even, UNBIASED
//
// Convergent is the default worth caring about: truncation and round-half-up
// both inject a DC offset that accumulates through a filter chain, which shows
// up as a spurious tone rather than as noise.
//
// SATURATION. Symmetric mode clamps to -(2^(W-1)-1) rather than -2^(W-1), so
// that negating an output is always representable. Asymmetric mode keeps the
// full two's-complement range.
// -----------------------------------------------------------------------------
`ifndef ROUND_SAT_SV
`define ROUND_SAT_SV

module round_sat #(
  parameter int ACC_W  = 38,
  parameter int DATA_W = 16,
  parameter int SHIFT  = 15    // fractional bits to discard (= COEF_W-1)
) (
  input  logic signed [ACC_W-1:0]  acc,
  input  logic [1:0]               round_mode,   // fir_pkg::round_mode_e
  input  logic                     sat_mode,     // 0 asymmetric, 1 symmetric
  output logic signed [DATA_W-1:0] y,
  output logic                     sat_event
);

  localparam int RND_W = ACC_W - SHIFT;   // width of the floored value

  initial begin
    if (SHIFT < 2)
      $fatal(1, "round_sat: SHIFT must be >= 2 (got %0d); the sticky term needs at least one bit.", SHIFT);
    if (RND_W <= DATA_W)
      $fatal(1, "round_sat: ACC_W-SHIFT (%0d) must exceed DATA_W (%0d), else saturation is unreachable.", RND_W, DATA_W);
  end

  // --- floor, guard, sticky, lsb ---------------------------------------------
  logic signed [RND_W-1:0] floored;
  logic                    guard, sticky, lsb, inc;

  assign floored = acc[ACC_W-1:SHIFT];      // dropping low bits IS floor in two's complement
  assign guard   = acc[SHIFT-1];
  assign sticky  = |acc[SHIFT-2:0];
  assign lsb     = acc[SHIFT];

  always_comb begin
    case (round_mode)
      fir_pkg::RND_TRUNC:      inc = 1'b0;
      fir_pkg::RND_HALF_UP:    inc = guard;
      fir_pkg::RND_CONVERGENT: inc = guard & (sticky | lsb);
      default:                 inc = 1'b0;   // RND_RESERVED: blocked by CFG_ERR
    endcase
  end

  // One extra bit so that incrementing the most positive floored value cannot
  // wrap before the saturator gets a chance to see it.
  logic signed [RND_W:0] rounded;
  assign rounded = $signed({floored[RND_W-1], floored}) + $signed({{RND_W{1'b0}}, inc});

  // --- saturate ---------------------------------------------------------------
  //
  // Written as continuous assignments rather than an always_comb. Icarus 12
  // reports "constant selects in always_* processes are not currently
  // supported (all bits will be included)" for a part-select of a net inside a
  // process -- it SILENTLY IGNORES THE SELECT, which is a correctness hazard,
  // not a cosmetic warning. Continuous assignments have no such limitation, and
  // a pure select-of-three needs no process anyway.
  logic signed [RND_W:0] max_pos, min_neg;
  assign max_pos = $signed({{(RND_W+1-DATA_W){1'b0}}, 1'b0, {(DATA_W-1){1'b1}}});
  assign min_neg = sat_mode ? -max_pos                                        // symmetric  -(2^(W-1)-1)
                            : -max_pos - $signed({{RND_W{1'b0}}, 1'b1});      // asymmetric -2^(W-1)

  logic signed [DATA_W-1:0] max_pos_t, min_neg_t, rounded_t;
  assign max_pos_t = max_pos[DATA_W-1:0];
  assign min_neg_t = min_neg[DATA_W-1:0];
  assign rounded_t = rounded[DATA_W-1:0];

  logic sat_hi, sat_lo;
  assign sat_hi    = (rounded > max_pos);
  assign sat_lo    = (rounded < min_neg);
  assign sat_event = sat_hi | sat_lo;
  assign y         = sat_hi ? max_pos_t : (sat_lo ? min_neg_t : rounded_t);

endmodule : round_sat

`endif
