// -----------------------------------------------------------------------------
// mac_array.sv
//
// NMAC parallel signed multipliers, each followed by a pipeline register.
// No accumulation happens here -- reduction is acc_reduce's job. Keeping the
// two apart is what allows the reduction to be re-pipelined during timing
// closure without touching the multipliers.
//
// The multipliers are written as a plain `signed * signed` and left entirely to
// the synthesis tool (requirement SR-06). No Booth encoding, no vendor
// primitives, no manual partial-product tricks: the point of the Phase 10 PPA
// experiment is to measure what the tool does with a clean description, and
// hand-optimising here would destroy that measurement before it is taken.
//
// Operand width is DATA_W+1, not DATA_W, because the symmetric pre-adder
// produces x[n-k] + x[n-(N-1-k)], which needs one extra bit to stay exact.
// -----------------------------------------------------------------------------
`ifndef MAC_ARRAY_SV
`define MAC_ARRAY_SV

module mac_array #(
  parameter int OPND_W = 17,   // DATA_W + 1 (pre-adder headroom)
  parameter int COEF_W = 16,
  parameter int NMAC   = 64
) (
  input  logic                          clk,
  input  logic                          rst_n,
  input  logic [NMAC*OPND_W-1:0]        opnd_flat,   // signed operands
  input  logic [NMAC*COEF_W-1:0]        coef_flat,   // signed coefficients
  output logic [NMAC*(OPND_W+COEF_W)-1:0] prod_flat  // signed exact products
);

  localparam int PROD_W = OPND_W + COEF_W;

  genvar g;
  generate
    for (g = 0; g < NMAC; g++) begin : g_mac
      logic signed [OPND_W-1:0] a;
      logic signed [COEF_W-1:0] b;
      logic signed [PROD_W-1:0] p;

      assign a = opnd_flat[g*OPND_W +: OPND_W];
      assign b = coef_flat[g*COEF_W +: COEF_W];

      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) p <= '0;
        else        p <= a * b;
      end

      assign prod_flat[g*PROD_W +: PROD_W] = p;
    end
  endgenerate

endmodule : mac_array

`endif
