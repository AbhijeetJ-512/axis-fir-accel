// -----------------------------------------------------------------------------
// delay_line.sv
//
// Sample history: taps_flat[k] == x[n-k] for k in 0..NTAPS_MAX-1.
//
// Implemented as a shift register rather than a circular RAM so that ALL taps
// are readable in the same cycle, which the fully-parallel configuration needs
// and which the symmetric pre-adder needs regardless of fold factor (it reads
// from both ends of the line at once). The area cost is NTAPS_MAX x DATA_W
// flops; open question OQ-1 revisits this in Phase 10 with measured numbers.
//
// SHIFT RATE: the line advances on shift_en, which is asserted once per OUTPUT
// SAMPLE -- not once per clock. In a folded configuration the same history must
// stay stable for all P passes of the MAC array, so shift_en is driven by the
// last pass, not by a free-running enable. Getting that wrong is the single
// most likely functional bug in the folded datapath (risk R3).
//
// Unlike the coefficient banks (D-009) this array IS reset: requirement FR-04
// specifies that the first N-1 outputs after reset or flush correspond to a
// zero-padded input history, which is only true if the line starts at zero.
// -----------------------------------------------------------------------------
`ifndef DELAY_LINE_SV
`define DELAY_LINE_SV

module delay_line #(
  parameter int DATA_W    = 16,
  parameter int NTAPS_MAX = 64
) (
  input  logic                         clk,
  input  logic                         rst_n,
  input  logic                         clear,     // synchronous zero-fill (FLUSH)
  input  logic                         shift_en,  // advance by one sample
  input  logic [DATA_W-1:0]            din,
  output logic [NTAPS_MAX*DATA_W-1:0]  taps_flat
);

  logic [DATA_W-1:0] mem [0:NTAPS_MAX-1];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < NTAPS_MAX; i++) mem[i] <= '0;
    end else if (clear) begin
      for (int i = 0; i < NTAPS_MAX; i++) mem[i] <= '0;
    end else if (shift_en) begin
      mem[0] <= din;
      for (int i = 1; i < NTAPS_MAX; i++) mem[i] <= mem[i-1];
    end
  end

  genvar g;
  generate
    for (g = 0; g < NTAPS_MAX; g++) begin : g_flatten
      assign taps_flat[g*DATA_W +: DATA_W] = mem[g];
    end
  endgenerate

endmodule : delay_line

`endif
