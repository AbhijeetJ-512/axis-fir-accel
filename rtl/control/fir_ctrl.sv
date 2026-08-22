// -----------------------------------------------------------------------------
// fir_ctrl.sv
//
// Three concerns, one module (docs/02_architecture.md section 3.4):
//   1. Run/stop FSM       -- gates streaming on ENABLE and configuration legality
//   2. Fold scheduler     -- sequences P passes per output sample
//   3. Admission control  -- consumes a credit per sample admitted
//
// THE FOLD SCHEDULE
//
//     N_eff = cfg_symmetric ? ceil(N/2) : N
//     P     = ceil(N_eff / MAC_UNITS)          (a shift: MAC_UNITS is 2^k)
//
//   cycle c      sample_start: pop the input FIFO, SHIFT THE DELAY LINE once
//   cycle c+1    pass 0   (tap_base = 0)
//   cycle c+2    pass 1   (tap_base = MAC_UNITS)
//   ...
//   cycle c+P    pass P-1 (tap_base = (P-1)*MAC_UNITS), and the NEXT
//                sample_start may occur on this same cycle
//
// so the initiation interval is exactly P cycles and back-to-back samples
// overlap by one cycle at the boundary. At cycle c+P the delay line shifts on
// the clock edge while pass P-1 captures its operands on the same edge -- the
// operands are the pre-shift taps, which is correct, because a register
// captures the value present before the edge.
//
// THE DELAY LINE SHIFTS ONCE PER SAMPLE, NOT ONCE PER PASS. This is risk R3:
// shifting per pass gives a plausible-looking but silently wrong filter, with
// no protocol violation and no assertion to catch it. It is caught instead by
// requiring bit-identity against the unfolded datapath for every MAC_UNITS.
//
// DRAIN exists so that dropping ENABLE mid-stream does not violate the
// losslessness guarantee (FR-15): samples already admitted are carried to
// completion before the FSM returns to IDLE. Only hard reset is abrupt.
// -----------------------------------------------------------------------------
`ifndef FIR_CTRL_SV
`define FIR_CTRL_SV

module fir_ctrl #(
  parameter int NTAPS_MAX      = 64,
  parameter int MAC_UNITS      = 8,
  parameter int OUT_FIFO_DEPTH = 8
) (
  input  logic                              clk,
  input  logic                              rst_n,
  input  logic                              soft_clear,

  // ---- Configuration ---------------------------------------------------------
  input  logic                              ctrl_enable,
  input  logic                              cfg_illegal,
  input  logic [fir_pkg::CFG_NTAPS_W-1:0]   cfg_ntaps,
  input  logic                              cfg_symmetric,
  input  logic                              cmd_flush,

  // ---- Input stream (from the input FIFO) ------------------------------------
  input  logic                              in_tvalid,
  input  logic                              in_tlast,
  output logic                              in_tready,

  // ---- Admission credit -------------------------------------------------------
  input  logic                              have_credit,
  output logic                              consume,

  // ---- Datapath completion ----------------------------------------------------
  input  logic                              core_out_valid,

  // ---- To the datapath ---------------------------------------------------------
  output logic                              shift_en,
  output logic                              pass_vld,
  output logic                              pass_first,
  output logic                              pass_last,
  output logic                              pass_tlast,
  output logic [fir_pkg::CFG_NTAPS_W-1:0]   tap_base,

  // ---- Status -----------------------------------------------------------------
  output logic                              sts_busy,
  output logic                              sts_draining,
  output logic [1:0]                        dbg_state,
  output logic [fir_pkg::CFG_NTAPS_W-1:0]   dbg_passes
);

  localparam int LOG2_MAC = $clog2(MAC_UNITS);
  localparam int CNT_W    = $clog2(OUT_FIFO_DEPTH + 1);

  initial begin
    if ((MAC_UNITS & (MAC_UNITS-1)) != 0)
      $fatal(1, "fir_ctrl: MAC_UNITS (%0d) must be a power of two -- the pass schedule uses a shift.", MAC_UNITS);
  end

  // ---------------------------------------------------------------------------
  // Pass count for the current configuration
  // ---------------------------------------------------------------------------
  logic [fir_pkg::CFG_NTAPS_W-1:0] h_taps, n_eff, n_pass, p_last;

  assign h_taps = (cfg_ntaps + 1) >> 1;                 // ceil(N/2)
  assign n_eff  = cfg_symmetric ? h_taps : cfg_ntaps;
  // ceil(n_eff / MAC_UNITS); MAC_UNITS is a power of two so this is a shift.
  assign n_pass = (n_eff + fir_pkg::CFG_NTAPS_W'(MAC_UNITS - 1)) >> LOG2_MAC;
  assign p_last = (n_pass == '0) ? '0 : n_pass - 1'b1;  // guard N=0 (illegal anyway)

  assign dbg_passes = n_pass;

  // ---------------------------------------------------------------------------
  // FSM
  // ---------------------------------------------------------------------------
  logic [1:0] state, state_n;
  logic [CNT_W-1:0] dp_count;      // samples inside the datapath
  logic busy;                      // inside the pass phase of a sample
  logic sample_start, last_pass;
  logic [fir_pkg::CFG_NTAPS_W-1:0] pcnt;

  assign last_pass = busy && (pcnt == p_last);

  always_comb begin
    state_n = state;
    case (state)
      fir_pkg::ST_IDLE:
        if (ctrl_enable) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_RUN;
      fir_pkg::ST_RUN:
        if (!ctrl_enable || cmd_flush) state_n = fir_pkg::ST_DRAIN;
      fir_pkg::ST_DRAIN:
        // Every admitted sample has produced its output and no pass is running.
        if (!busy && dp_count == '0) state_n = fir_pkg::ST_IDLE;
      fir_pkg::ST_CFGERR:
        // Held until software drops ENABLE; the sticky CFG_ERR status and the
        // interrupt are csr_regfile's job, not ours.
        if (!ctrl_enable) state_n = fir_pkg::ST_IDLE;
      default: state_n = fir_pkg::ST_IDLE;
    endcase
  end

  // ---------------------------------------------------------------------------
  // Admission
  //
  // A sample may be admitted when the FSM is running, data is available, a
  // credit is free, and either no pass phase is active or it is finishing this
  // cycle. in_tready is therefore a function of registered state and the credit
  // counter -- never of anything downstream (requirement TR-04).
  // ---------------------------------------------------------------------------
  logic can_accept;
  assign can_accept   = !busy || last_pass;
  assign sample_start = (state == fir_pkg::ST_RUN) && in_tvalid && have_credit && can_accept;

  assign in_tready = sample_start;
  assign consume   = sample_start;
  assign shift_en  = sample_start;

  logic tlast_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= fir_pkg::ST_IDLE;
      busy     <= 1'b0;
      pcnt     <= '0;
      tlast_q  <= 1'b0;
      dp_count <= '0;
    end else if (soft_clear) begin
      state    <= fir_pkg::ST_IDLE;
      busy     <= 1'b0;
      pcnt     <= '0;
      tlast_q  <= 1'b0;
      dp_count <= '0;
    end else begin
      state <= state_n;

      // TLAST is captured at admission and held for the whole pass phase. It is
      // read DURING cycle c+P by the last pass while a new sample_start may be
      // overwriting it on that same edge -- correct, because a register reads
      // its pre-edge value.
      if (sample_start) tlast_q <= in_tlast;

      // Pass sequencing. The pass phase runs on the P cycles AFTER admission.
      if (busy) begin
        if (last_pass) begin
          busy <= sample_start;      // back-to-back if a new sample was admitted
          pcnt <= '0;
        end else begin
          pcnt <= pcnt + 1'b1;
        end
      end else begin
        busy <= sample_start;
        pcnt <= '0;
      end

      // Datapath occupancy, used only to know when DRAIN is complete.
      case ({sample_start, core_out_valid})
        2'b10:   dp_count <= dp_count + 1'b1;
        2'b01:   dp_count <= dp_count - 1'b1;
        default: dp_count <= dp_count;
      endcase
    end
  end

  assign pass_vld   = busy;
  assign pass_first = busy && (pcnt == '0);
  assign pass_last  = last_pass;
  assign pass_tlast = tlast_q;
  assign tap_base   = pcnt << LOG2_MAC;

  assign sts_busy     = (state != fir_pkg::ST_IDLE);
  assign sts_draining = (state == fir_pkg::ST_DRAIN);
  assign dbg_state    = state;

endmodule : fir_ctrl

`endif
