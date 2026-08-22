// -----------------------------------------------------------------------------
// fir_core.sv
//
// The arithmetic datapath: y[n] = sum(k=0..N-1) h[k] * x[n-k], with RESOURCE
// FOLDING.
//
// MAC_UNITS physical multipliers process the taps in P passes per output sample:
//
//     P = ceil(N_eff / MAC_UNITS)      N_eff = ceil(N/2) symmetric, else N
//
// Pass p handles taps [p*MAC_UNITS .. p*MAC_UNITS + MAC_UNITS-1] via `tap_base`.
// One parameter therefore moves the design continuously from area-minimal
// (MAC_UNITS = 1, II = N) to throughput-maximal (MAC_UNITS >= N_eff, II = 1)
// with no source change. This is the knob the Phase 10 PPA sweep turns.
//
// THE DELAY LINE SHIFTS ONCE PER SAMPLE, NOT ONCE PER PASS. `shift_en` is driven
// by the controller when a new sample is admitted; the tap history must stay
// frozen for all P passes of that sample. Shifting per pass would silently
// produce wrong results with no protocol violation to catch it -- risk R3, and
// the reason the gating test is bit-identity against the unfolded path.
//
// WHY out_valid COMES FROM THE CONTROL CHAIN, NOT A FIXED PIPE
//
// Latency is 5 + clog2(MAC_UNITS) + (P-1), and P varies at RUNTIME with
// CFG.NTAPS. A fixed-depth valid shadow register -- which is what the unfolded
// Phase 3 core used -- cannot express that. Instead the pass control record
// {vld, first, last, tlast} is shifted through exactly the datapath's own delay
// (DLY = 2 + LEVELS) so that it emerges aligned with the corresponding adder
// tree output. Latency then tracks the configuration automatically, and any
// number of samples may be in flight without a side FIFO to keep in step.
//
// Latency remains deterministic and data-independent (PF-05); it depends on
// configuration, which is legitimate and documented.
// -----------------------------------------------------------------------------
`ifndef FIR_CORE_SV
`define FIR_CORE_SV

`include "fir_defs.svh"

module fir_core #(
  parameter int DATA_W       = 16,
  parameter int COEF_W       = 16,
  parameter int NTAPS_MAX    = 64,
  parameter int MAC_UNITS    = 8,     // physical multipliers; the PPA knob
  parameter bit SYMMETRIC_EN = 1'b1   // see PR-08 / D-026
) (
  input  logic                              clk,
  input  logic                              rst_n,
  input  logic                              clear,        // flush the delay line

  // ---- Configuration (static while streaming) -------------------------------
  input  logic [fir_pkg::CFG_NTAPS_W-1:0]   cfg_ntaps,
  input  logic [fir_pkg::CFG_ROUND_W-1:0]   cfg_round_mode,
  input  logic                              cfg_sat_mode,
  input  logic                              cfg_symmetric,
  input  logic [NTAPS_MAX*COEF_W-1:0]       coef_flat,

  // ---- Sample admission (from fir_ctrl) --------------------------------------
  input  logic                              shift_en,     // new sample accepted
  input  logic [DATA_W-1:0]                 in_data,

  // ---- Pass control (from fir_ctrl) ------------------------------------------
  input  logic                              pass_vld,
  input  logic                              pass_first,   // first pass of a sample
  input  logic                              pass_last,    // last pass of a sample
  input  logic                              pass_tlast,   // TLAST of this sample
  input  logic [fir_pkg::CFG_NTAPS_W-1:0]   tap_base,     // p * MAC_UNITS

  // ---- Sample out --------------------------------------------------------------
  output logic                              out_valid,
  output logic [DATA_W-1:0]                 out_data,
  output logic                              out_last,
  output logic                              out_sat
);

  localparam int NMAC   = MAC_UNITS;
  localparam int OPND_W = DATA_W + 1;                             // pre-adder headroom
  localparam int PROD_W = OPND_W + COEF_W;
  localparam int ACC_W  = `FIR_ACC_W(DATA_W, COEF_W, NTAPS_MAX);
  localparam int SHIFT  = `FIR_SHIFT(COEF_W);
  localparam int LEVELS = $clog2(NMAC);
  localparam int IDXW   = $clog2(NTAPS_MAX);

  // Cycles from the pass-select cycle to that pass's adder-tree output:
  // one for the operand register, one for the product register, LEVELS for the
  // tree. The control record is delayed by exactly this to stay aligned.
  localparam int DLY = 2 + LEVELS;

  initial begin
    if (MAC_UNITS > NTAPS_MAX)
      $fatal(1, "fir_core: MAC_UNITS (%0d) must not exceed NTAPS_MAX (%0d).", MAC_UNITS, NTAPS_MAX);
    if ((MAC_UNITS & (MAC_UNITS-1)) != 0)
      $fatal(1, "fir_core: MAC_UNITS (%0d) must be a power of two.", MAC_UNITS);
  end

  // ---------------------------------------------------------------------------
  // Stage 0 -- sample history (frozen for all P passes of a sample)
  // ---------------------------------------------------------------------------
  logic [NTAPS_MAX*DATA_W-1:0] taps_flat;

  delay_line #(.DATA_W(DATA_W), .NTAPS_MAX(NTAPS_MAX)) u_delay (
    .clk (clk), .rst_n (rst_n), .clear (clear),
    .shift_en (shift_en), .din (in_data),
    .taps_flat (taps_flat)
  );

  // ---------------------------------------------------------------------------
  // Stage 1 -- operand and coefficient selection for this pass
  // ---------------------------------------------------------------------------
  logic [fir_pkg::CFG_NTAPS_W-1:0] h_taps;
  assign h_taps = (cfg_ntaps + 1) >> 1;          // ceil(N/2)

  logic [NMAC*OPND_W-1:0] opnd_c, opnd_q;
  logic [NMAC*COEF_W-1:0] coef_c, coef_q;

  genvar g;
  generate
    for (g = 0; g < NMAC; g++) begin : g_prep
      // Absolute tap index handled by this MAC unit on this pass.
      logic [fir_pkg::CFG_NTAPS_W-1:0] k_full;
      logic [IDXW-1:0]                 k_idx;
      logic signed [DATA_W-1:0]        near_v;
      logic signed [OPND_W-1:0]        near_x;
      logic                            active;

      assign k_full = tap_base + g;
      assign k_idx  = k_full[IDXW-1:0];

      assign near_v = taps_flat[k_idx*DATA_W +: DATA_W];
      assign near_x = {near_v[DATA_W-1], near_v};

      // The COEFFICIENT is gated by `active`, not just the operand.
      //
      // Coefficient storage is deliberately never reset (D-009) and software is
      // only required to write the NTAPS taps it actually uses -- so entries at
      // and above NTAPS may hold X. Zeroing only the operand is NOT enough:
      // in Verilog 0 * X is X, not 0, so an unwritten coefficient would poison
      // the adder tree and the output, even though its tap is inactive. Gating
      // both operands of the multiply makes the datapath immune to the storage
      // policy the architecture chose.
      assign coef_c[g*COEF_W +: COEF_W] =
          active ? coef_flat[k_idx*COEF_W +: COEF_W] : {COEF_W{1'b0}};

      if (SYMMETRIC_EN) begin : g_sym
        logic [fir_pkg::CFG_NTAPS_W-1:0] far_full;
        logic [IDXW-1:0]                 far_idx;
        logic signed [DATA_W-1:0]        far_v;
        logic signed [OPND_W-1:0]        far_x;
        logic                            is_mid;

        assign far_full = cfg_ntaps - 1 - k_full;
        assign far_idx  = far_full[IDXW-1:0];
        assign far_v    = taps_flat[far_idx*DATA_W +: DATA_W];
        assign far_x    = {far_v[DATA_W-1], far_v};

        assign active = cfg_symmetric ? (k_full < h_taps) : (k_full < cfg_ntaps);
        assign is_mid = (far_idx == k_idx);          // odd N centre tap

        assign opnd_c[g*OPND_W +: OPND_W] =
              !active        ? {OPND_W{1'b0}}
            : !cfg_symmetric ? near_x
            : is_mid         ? near_x                // centre tap counted ONCE
            :                  (near_x + far_x);
      end else begin : g_asym
        assign active = (k_full < cfg_ntaps);
        assign opnd_c[g*OPND_W +: OPND_W] = active ? near_x : {OPND_W{1'b0}};
      end
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
  // Stage 2 -- multiply    Stage 3 -- spatial reduction
  // ---------------------------------------------------------------------------
  logic [NMAC*PROD_W-1:0]  prod_flat;
  logic signed [ACC_W-1:0] tree_acc;

  mac_array #(.OPND_W(OPND_W), .COEF_W(COEF_W), .NMAC(NMAC)) u_mac (
    .clk (clk), .rst_n (rst_n),
    .opnd_flat (opnd_q), .coef_flat (coef_q), .prod_flat (prod_flat)
  );

  acc_reduce #(.PROD_W(PROD_W), .ACC_W(ACC_W), .NMAC(NMAC)) u_acc (
    .clk (clk), .rst_n (rst_n), .prod_flat (prod_flat), .acc (tree_acc)
  );

  // ---------------------------------------------------------------------------
  // Control chain -- delays the pass record to meet its own tree output
  // ---------------------------------------------------------------------------
  logic [DLY-1:0] ch_vld, ch_first, ch_last, ch_tlast;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ch_vld <= '0; ch_first <= '0; ch_last <= '0; ch_tlast <= '0;
    end else begin
      ch_vld[0]   <= pass_vld;
      ch_first[0] <= pass_first;
      ch_last[0]  <= pass_last;
      ch_tlast[0] <= pass_tlast;
      for (int i = 1; i < DLY; i++) begin
        ch_vld[i]   <= ch_vld[i-1];
        ch_first[i] <= ch_first[i-1];
        ch_last[i]  <= ch_last[i-1];
        ch_tlast[i] <= ch_tlast[i-1];
      end
    end
  end

  logic acc_vld, acc_first, acc_last, acc_tlast;
  assign acc_vld   = ch_vld[DLY-1];
  assign acc_first = ch_first[DLY-1];
  assign acc_last  = ch_last[DLY-1];
  assign acc_tlast = ch_tlast[DLY-1];

  // ---------------------------------------------------------------------------
  // Stage 4 -- temporal accumulation across passes
  //
  // Still no saturation and no overflow flag: ACC_W bounds the TOTAL sum over
  // all NTAPS_MAX products, so accumulating the partial sums of a folded
  // configuration cannot reach further than the unfolded case does.
  // ---------------------------------------------------------------------------
  logic signed [ACC_W-1:0] fold_acc, acc_next, result_acc;
  logic                    result_vld, result_tlast;

  assign acc_next = acc_first ? tree_acc : (fold_acc + tree_acc);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      fold_acc     <= '0;
      result_acc   <= '0;
      result_vld   <= 1'b0;
      result_tlast <= 1'b0;
    end else begin
      if (acc_vld) fold_acc <= acc_next;
      result_vld   <= acc_vld && acc_last;
      if (acc_vld && acc_last) begin
        result_acc   <= acc_next;
        result_tlast <= acc_tlast;
      end
    end
  end

  // ---------------------------------------------------------------------------
  // Stage 5 -- round, saturate, register
  // ---------------------------------------------------------------------------
  logic signed [DATA_W-1:0] y_c;
  logic                     sat_c;

  round_sat #(.ACC_W(ACC_W), .DATA_W(DATA_W), .SHIFT(SHIFT)) u_rs (
    .acc (result_acc), .round_mode (cfg_round_mode), .sat_mode (cfg_sat_mode),
    .y (y_c), .sat_event (sat_c)
  );

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_valid <= 1'b0;
      out_data  <= '0;
      out_last  <= 1'b0;
      out_sat   <= 1'b0;
    end else begin
      out_valid <= result_vld;
      if (result_vld) begin
        out_data <= y_c;
        out_last <= result_tlast;
        out_sat  <= sat_c;
      end else begin
        out_sat  <= 1'b0;
      end
    end
  end

endmodule : fir_core

`endif
