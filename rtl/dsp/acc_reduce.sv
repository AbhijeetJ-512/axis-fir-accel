// -----------------------------------------------------------------------------
// acc_reduce.sv
//
// Balanced binary adder tree over NMAC products, ONE PIPELINE REGISTER PER
// LEVEL, giving a latency of clog2(NMAC) cycles.
//
// Structure. The tree is stored as a heap: node[i] has children node[2i] and
// node[2i+1]. Internal nodes occupy indices 1..NPAD-1; the conceptual leaves at
// NPAD..2*NPAD-1 are the (sign-extended, zero-padded) products, read directly.
// Because every node registers the sum of its children, data advances exactly
// one level per clock and the tree is pipelined by construction -- there is no
// separate "insert registers here" step to get wrong.
//
// NMAC is padded up to a power of two with zeros rather than building a ragged
// tree. Zeros cost nothing after synthesis constant-folds them, and a balanced
// tree keeps the logic depth at exactly clog2(NMAC) for every configuration.
//
// ALL arithmetic is at ACC_W bits, the width proven by
// scripts/verify_acc_width.py to make overflow impossible for both the
// asymmetric and symmetric datapaths. THIS BLOCK THEREFORE HAS NO SATURATION
// LOGIC AND NO OVERFLOW FLAG, AND NEEDS NONE. That is a consequence of deriving
// the width rather than guessing it (engineering log D-007).
//
// Phase 10 will evaluate registering every SECOND level instead: half the
// flops, twice the logic depth per stage. Recorded now so the experiment has a
// documented baseline.
// -----------------------------------------------------------------------------
`ifndef ACC_REDUCE_SV
`define ACC_REDUCE_SV

module acc_reduce #(
  parameter int PROD_W = 33,
  parameter int ACC_W  = 38,
  parameter int NMAC   = 64
) (
  input  logic                     clk,
  input  logic                     rst_n,
  input  logic [NMAC*PROD_W-1:0]   prod_flat,
  output logic signed [ACC_W-1:0]  acc
);

  localparam int LEVELS = $clog2(NMAC);
  localparam int NPAD   = 1 << LEVELS;

  initial begin
    if (ACC_W < PROD_W)
      $fatal(1, "acc_reduce: ACC_W (%0d) must be >= PROD_W (%0d).", ACC_W, PROD_W);
  end

  // Leaves: sign-extend each product to ACC_W; pad the tree with zeros.
  logic signed [ACC_W-1:0] leaf [0:NPAD-1];

  genvar g;
  generate
    for (g = 0; g < NPAD; g++) begin : g_leaf
      if (g < NMAC) begin : g_real
        logic signed [PROD_W-1:0] p;
        assign p       = prod_flat[g*PROD_W +: PROD_W];
        assign leaf[g] = {{(ACC_W-PROD_W){p[PROD_W-1]}}, p};
      end else begin : g_pad
        assign leaf[g] = '0;
      end
    end
  endgenerate

  generate
    if (LEVELS == 0) begin : g_single
      // One MAC: the product is the accumulator, no tree at all.
      assign acc = leaf[0];
    end else begin : g_tree
      logic signed [ACC_W-1:0] node [1:NPAD-1];

      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          for (int i = 1; i < NPAD; i++) node[i] <= '0;
        end else begin
          // Bottom level reads the leaves.
          for (int i = NPAD/2; i < NPAD; i++)
            node[i] <= leaf[2*i - NPAD] + leaf[2*i + 1 - NPAD];
          // Upper levels read the level below, one level per clock.
          for (int i = 1; i < NPAD/2; i++)
            node[i] <= node[2*i] + node[2*i + 1];
        end
      end

      assign acc = node[1];
    end
  endgenerate

endmodule : acc_reduce

`endif
