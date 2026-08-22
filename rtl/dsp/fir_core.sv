// -----------------------------------------------------------------------------
// fir_core.sv
//
// The arithmetic datapath: y[n] = sum(k=0..N-1) h[k] * x[n-k].
//
// Phase 3 builds the FULLY PARALLEL form (NMAC = NTAPS_MAX). Phase 4 adds the
// fold scheduler; the module boundaries here were chosen so that folding
// changes only the operand-selection stage and the shift enable, not the
// arithmetic.
//
// FIXED LATENCY, NEVER STALLS. There is no enable, no stall input and no
// handshaking inside this module -- the credit counter upstream (D-006)
// guarantees a landing slot for every sample admitted, so a result is never
// asked to wait. The valid/last pipe simply shadows the datapath.
//
//   t          in_valid; delay_line shifts
//   t+1        taps stable; operand prep (combinational)   -> registered
//   t+2        multiply (combinational)                    -> registered
//   t+3..      adder tree, one level per cycle             -> LEVELS registers
//   t+3+LEVELS round + saturate (combinational)            -> registered
//   t+4+LEVELS out_valid
//
//   LATENCY = 4 + clog2(NMAC), a compile-time constant exported as LATENCY.
//
// SYMMETRIC MODE. For a linear-phase filter h[k] == h[N-1-k], so
//
//     y[n] = sum(k=0..H-1) h[k] * ( x[n-k] + x[n-(N-1-k)] ),   H = ceil(N/2)
//
// which halves the number of products. THE MIDDLE TAP IS THE TRAP: when N is
// odd and k == (N-1)/2, the index N-1-k equals k, so the naive pre-add would
// form 2*x[n-k] and count the centre tap twice. That case is detected and the
// near tap is used alone. This is the most likely silent-wrong-answer bug in
// the whole datapath, so it is called out here and tested directly.
//
// Note that symmetric mode reads only the first H coefficients. If the loaded
// coefficients are NOT actually symmetric the result differs from asymmetric
// mode -- that is the defined meaning of the mode, not a bug.
// -----------------------------------------------------------------------------
`ifndef FIR_CORE_SV
`define FIR_CORE_SV

`include "fir_defs.svh"

module fir_core #(
  parameter int DATA_W    = 16,
  parameter int COEF_W    = 16,
  parameter int NTAPS_MAX = 64,
  // When 0, the symmetric pre-adder and -- crucially -- the runtime-variable
  // mirror-tap mux are NOT SYNTHESISED AT ALL, and cfg_symmetric is ignored.
  // That mux is an O(NTAPS_MAX^2) structure: NTAPS_MAX instances each selecting
  // one of NTAPS_MAX taps at a runtime index. At NTAPS_MAX=64 that is 4096
  // select paths, and it is the reason simulation time scales superlinearly
  // with tap count (risk R9). Requirement PR-08.
  parameter bit SYMMETRIC_EN = 1'b1
) (
  input  logic                              clk,
  input  logic                              rst_n,
  input  logic                              clear,          // flush the delay line

  // ---- Configuration (static while streaming) -------------------------------
  input  logic [fir_pkg::CFG_NTAPS_W-1:0]   cfg_ntaps,
  input  logic [fir_pkg::CFG_ROUND_W-1:0]   cfg_round_mode,
  input  logic                              cfg_sat_mode,
  input  logic                              cfg_symmetric,
  input  logic [NTAPS_MAX*COEF_W-1:0]       coef_flat,

  // ---- Sample in (no backpressure -- see header) ----------------------------
  input  logic                              in_valid,
  input  logic [DATA_W-1:0]                 in_data,
  input  logic                              in_last,

  // ---- Sample out ------------------------------------------------------------
  output logic                              out_valid,
  output logic [DATA_W-1:0]                 out_data,
  output logic                              out_last,
  output logic                              out_sat         // saturation event
);

  localparam int NMAC   = NTAPS_MAX;                              // fully parallel
  localparam int OPND_W = DATA_W + 1;                             // pre-adder headroom
  localparam int PROD_W = OPND_W + COEF_W;
  localparam int ACC_W  = `FIR_ACC_W(DATA_W, COEF_W, NTAPS_MAX);
  localparam int SHIFT  = `FIR_SHIFT(COEF_W);
  localparam int LEVELS = $clog2(NMAC);
  localparam int IDXW   = $clog2(NTAPS_MAX);

  localparam int LATENCY = 4 + LEVELS;

  // ---------------------------------------------------------------------------
  // Stage 0 -- sample history
  // ---------------------------------------------------------------------------
  logic [NTAPS_MAX*DATA_W-1:0] taps_flat;

  delay_line #(.DATA_W(DATA_W), .NTAPS_MAX(NTAPS_MAX)) u_delay (
    .clk (clk), .rst_n (rst_n), .clear (clear),
    .shift_en (in_valid),          // once per sample; Phase 4 drives this from acc_last
    .din (in_data),
    .taps_flat (taps_flat)
  );

  // ---------------------------------------------------------------------------
  // Stage 1 -- operand preparation (combinational), then registered
  // ---------------------------------------------------------------------------
  logic [fir_pkg::CFG_NTAPS_W-1:0] h_taps;
  assign h_taps = (cfg_ntaps + 1) >> 1;          // ceil(N/2)

  logic [NMAC*OPND_W-1:0] opnd_c, opnd_q;
  logic [NMAC*COEF_W-1:0] coef_c, coef_q;

  genvar g;
  generate
    for (g = 0; g < NMAC; g++) begin : g_prep
      logic signed [DATA_W-1:0] near_v;
      logic signed [OPND_W-1:0] near_x;

      assign near_v = taps_flat[g*DATA_W +: DATA_W];
      assign near_x = {near_v[DATA_W-1], near_v};      // sign-extend to OPND_W

      if (SYMMETRIC_EN) begin : g_sym
        logic [fir_pkg::CFG_NTAPS_W-1:0] far_full;
        logic [IDXW-1:0]                 far_idx;
        logic signed [DATA_W-1:0]        far_v;
        logic signed [OPND_W-1:0]        far_x;
        logic                            active, is_mid;

        // Mirror index N-1-g. Only meaningful while `active`, and inside that
        // range g <= H-1 <= N-1 so the subtraction cannot underflow.
        assign far_full = cfg_ntaps - 1 - g;
        assign far_idx  = far_full[IDXW-1:0];
        assign far_v    = taps_flat[far_idx*DATA_W +: DATA_W];   // the O(N^2) mux
        assign far_x    = {far_v[DATA_W-1], far_v};

        assign active = cfg_symmetric ? (g < h_taps) : (g < cfg_ntaps);
        assign is_mid = (far_idx == IDXW'(g));           // odd N, centre tap

        assign opnd_c[g*OPND_W +: OPND_W] =
              !active        ? {OPND_W{1'b0}}
            : !cfg_symmetric ? near_x
            : is_mid         ? near_x                    // centre tap counted ONCE
            :                  (near_x + far_x);
      end else begin : g_asym
        // No mirror mux, no pre-adder: a strictly cheaper build for
        // applications that never need linear-phase folding.
        logic active;
        assign active = (g < cfg_ntaps);
        assign opnd_c[g*OPND_W +: OPND_W] = active ? near_x : {OPND_W{1'b0}};
      end

      assign coef_c[g*COEF_W +: COEF_W] = coef_flat[g*COEF_W +: COEF_W];
    end
  endgenerate

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      opnd_q <= '0;
      coef_q <= '0;
    end else begin
      opnd_q <= opnd_c;
      coef_q <= coef_c;
    end
  end

  // ---------------------------------------------------------------------------
  // Stage 2 -- multiply
  // ---------------------------------------------------------------------------
  logic [NMAC*PROD_W-1:0] prod_flat;

  mac_array #(.OPND_W(OPND_W), .COEF_W(COEF_W), .NMAC(NMAC)) u_mac (
    .clk (clk), .rst_n (rst_n),
    .opnd_flat (opnd_q), .coef_flat (coef_q),
    .prod_flat (prod_flat)
  );

  // ---------------------------------------------------------------------------
  // Stage 3 -- reduction
  // ---------------------------------------------------------------------------
  logic signed [ACC_W-1:0] acc;

  acc_reduce #(.PROD_W(PROD_W), .ACC_W(ACC_W), .NMAC(NMAC)) u_acc (
    .clk (clk), .rst_n (rst_n),
    .prod_flat (prod_flat), .acc (acc)
  );

  // ---------------------------------------------------------------------------
  // Stage 4 -- round and saturate, then register
  // ---------------------------------------------------------------------------
  logic signed [DATA_W-1:0] y_c;
  logic                     sat_c;

  round_sat #(.ACC_W(ACC_W), .DATA_W(DATA_W), .SHIFT(SHIFT)) u_rs (
    .acc (acc), .round_mode (cfg_round_mode), .sat_mode (cfg_sat_mode),
    .y (y_c), .sat_event (sat_c)
  );

  // ---------------------------------------------------------------------------
  // Valid / last shadow pipe
  //
  // `last` travels beside its sample in a shift register of exactly the
  // datapath depth. It is never recomputed from a counter, so it cannot drift
  // out of alignment with the sample it belongs to.
  // ---------------------------------------------------------------------------
  logic [LATENCY-1:0] vld_pipe, lst_pipe;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      vld_pipe <= '0;
      lst_pipe <= '0;
    end else begin
      vld_pipe[0] <= in_valid;
      lst_pipe[0] <= in_last;
      for (int i = 1; i < LATENCY; i++) begin
        vld_pipe[i] <= vld_pipe[i-1];
        lst_pipe[i] <= lst_pipe[i-1];
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_data <= '0;
      out_sat  <= 1'b0;
    end else begin
      out_data <= y_c;
      // Saturation is only meaningful on a cycle that carries a real result.
      out_sat  <= sat_c & vld_pipe[LATENCY-2];
    end
  end

  assign out_valid = vld_pipe[LATENCY-1];
  assign out_last  = lst_pipe[LATENCY-1];

endmodule : fir_core

`endif
